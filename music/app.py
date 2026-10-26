#!/usr/bin/env python3
"""Musik - a small self-hosted music library streamer.

Scans MUSIC_ROOT into SQLite, serves a library API, and streams any track with
real HTTP Range support (206 Partial Content), so the browser streams and seeks
instead of downloading whole files. Stdlib only.
"""
import contextlib
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import struct
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MUSIC_ROOT = os.path.realpath(os.path.expanduser(
    os.environ.get("MUSIC_ROOT", "/srv/nas/music")))
DB_PATH = os.path.expanduser(
    os.environ.get("MUSIK_DB", "~/.local/state/musik/library.db"))
PORT = int(os.environ.get("PLAYER_PORT") or os.environ.get("PORT") or "8080")
BIND = os.environ.get("PLAYER_BIND", "0.0.0.0")
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "600"))
CHUNK = 1 << 16
MAX_BODY = 4096

AUDIO_TYPES = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac",
    ".wav": "audio/wav",
}
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/fonts.css": ("fonts.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}


# ---------- tags ----------

def _text(enc, raw):
    if enc == 0:
        s = raw.decode("latin-1", "replace")
    elif enc == 1:
        s = raw.decode("utf-16", "replace")
    elif enc == 2:
        s = raw.decode("utf-16-be", "replace")
    else:
        s = raw.decode("utf-8", "replace")
    return s.replace("\x00", " ").strip()


def _split_term(enc, data):
    """Split encoded data at its null terminator -> (head, rest)."""
    if enc in (1, 2):
        i = 0
        while i + 1 < len(data):
            if data[i] == 0 and data[i + 1] == 0:
                return data[:i], data[i + 2:]
            i += 2
        return data, b""
    head, _, rest = data.partition(b"\x00")
    return head, rest


def read_id3(path, want_cover=False):
    """Minimal ID3v2.2/2.3/2.4 reader -> (tags dict, end offset of tag)."""
    tags = {}
    with open(path, "rb") as f:
        head = f.read(10)
        if len(head) < 10 or head[:3] != b"ID3":
            return tags, 0
        major, flags = head[3], head[5]
        size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
        body = f.read(size)
    end = 10 + size + (10 if flags & 0x10 else 0)
    pos = 0
    if flags & 0x40 and major >= 3:  # extended header
        ext = body[:4]
        pos = ((ext[0] << 21) | (ext[1] << 14) | (ext[2] << 7) | ext[3]) if major == 4 \
            else struct.unpack(">I", ext)[0] + 4
    idlen, hdrlen = (3, 6) if major == 2 else (4, 10)
    names = {"TT2": "title", "TP1": "artist", "TAL": "album", "PIC": "cover",
             "TIT2": "title", "TPE1": "artist", "TALB": "album", "APIC": "cover"}
    while pos + hdrlen <= len(body):
        fid = body[pos:pos + idlen]
        if not fid.strip(b"\x00"):
            break
        if major == 2:
            fsize = int.from_bytes(body[pos + 3:pos + 6], "big")
        elif major == 4:
            b = body[pos + 4:pos + 8]
            fsize = (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]
        else:
            fsize = struct.unpack(">I", body[pos + 4:pos + 8])[0]
        data = body[pos + hdrlen:pos + hdrlen + fsize]
        if major == 4 and len(body) > pos + 9 and body[pos + 9] & 0x01:
            data = data[4:]  # data length indicator
        pos += hdrlen + fsize
        key = names.get(fid.decode("latin-1", "replace"))
        if not key or not data or key in tags:
            continue
        enc = data[0]
        if key == "cover":
            if not want_cover:
                tags["cover"] = True
                continue
            if major == 2:
                mime = {"PNG": "image/png"}.get(data[1:4].decode("latin-1").upper(), "image/jpeg")
                rest = data[5:]
            else:
                mime_raw, rest = data[1:].split(b"\x00", 1)
                mime = mime_raw.decode("latin-1") or "image/jpeg"
                if "/" not in mime:
                    mime = "image/" + mime.lower().replace("jpg", "jpeg")
                rest = rest[1:]
            _, img = _split_term(enc, rest)
            tags["cover"] = (mime, img)
        else:
            tags[key] = _text(enc, data[1:])
    return tags, end


_MP3_RATES = {
    3: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_MP3_SR = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def mp3_duration(path, start):
    """Estimate MP3 (Layer III) duration from the Xing/VBRI header or bitrate."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(start)
        buf = f.read(65536)
    for i in range(len(buf) - 4):
        if buf[i] != 0xFF or (buf[i + 1] & 0xE0) != 0xE0:
            continue
        b1, b2, b3 = buf[i + 1], buf[i + 2], buf[i + 3]
        ver, layer = (b1 >> 3) & 3, (b1 >> 1) & 3
        bri, sri = b2 >> 4, (b2 >> 2) & 3
        if ver == 1 or layer != 1 or bri in (0, 15) or sri == 3:
            continue
        sr = _MP3_SR[ver][sri]
        kbps = _MP3_RATES[3 if ver == 3 else 2][bri]
        spf = 1152 if ver == 3 else 576
        mono = (b3 >> 6) == 3
        xo = i + 4 + ((17 if mono else 32) if ver == 3 else (9 if mono else 17))
        if buf[xo:xo + 4] in (b"Xing", b"Info") and buf[xo + 7] & 1:
            frames = struct.unpack(">I", buf[xo + 8:xo + 12])[0]
            return frames * spf / sr
        if buf[i + 36:i + 40] == b"VBRI":
            frames = struct.unpack(">I", buf[i + 50:i + 54])[0]
            return frames * spf / sr
        return (size - start - i) * 8 / (kbps * 1000)
    return None


_NOISE = re.compile(
    r"\s*[\(\[](?:official\s*(?:music\s*|lyrics?\s*|hd\s*)?(?:video|audio|visuali[sz]er)"
    r"|lyrics?(?:\s*video)?|audio|visuali[sz]er|hd|4k|[A-Za-z0-9_-]{11})[\)\]]",
    re.I)
_SEP = re.compile(r"\s+[-–—]\s+|\s*[：:]\s+")


def clean_title(title, artist):
    """'Artist - Song (Official Video) [id]' -> ('Song', 'Artist').

    YouTube downloads tag the uploader as artist, so the name before the
    separator wins. A colon only splits when the left side is a single
    word ('C418: Aria Math'), so titles like 'Act One: The Return' stay.
    """
    t = _NOISE.sub("", title).strip()
    parts = _SEP.split(t, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        is_colon = not re.search(r"\s[-–—]\s", t)
        if right and (not is_colon or len(left.split()) == 1):
            return right, left
    return t, artist


def describe(path):
    """Return metadata for one file using tags, then the filename."""
    stem = os.path.splitext(os.path.basename(path))[0]
    tags, end = {}, 0
    if path.lower().endswith(".mp3"):
        try:
            tags, end = read_id3(path)
        except (OSError, ValueError, IndexError, struct.error):
            tags, end = {}, 0
    title, artist = clean_title(tags.get("title") or stem, tags.get("artist", ""))
    duration = None
    if path.lower().endswith(".mp3"):
        try:
            duration = mp3_duration(path, end)
        except (OSError, struct.error):
            pass
    return {
        "title": title or stem,
        "artist": artist or "Unknown artist",
        "album": tags.get("album", ""),
        "duration": round(duration, 1) if duration else None,
        "cover": 1 if tags.get("cover") else 0,
    }


# ---------- library ----------

@contextlib.contextmanager
def db():
    """One short-lived connection: commits on success, rolls back on error, always closes.

    (sqlite3's own `with conn:` only commits; it never closes the connection.)
    """
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as c:
        # WAL lets the page read the library while a scan is writing to it.
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
                id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                title TEXT, artist TEXT, album TEXT, duration REAL,
                cover INTEGER, mtime REAL, size INTEGER, added REAL);
            CREATE TABLE IF NOT EXISTS favorites (
                id TEXT PRIMARY KEY, added REAL);
        """)


scan_lock = threading.Lock()


def scan():
    """Upsert every audio file under MUSIC_ROOT; drop rows for removed files."""
    with scan_lock, db() as c:
        known = {r["path"]: (r["mtime"], r["size"]) for r in c.execute("SELECT path, mtime, size FROM tracks")}
        seen = set()
        for root, dirs, files in os.walk(MUSIC_ROOT):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if os.path.splitext(name)[1].lower() not in AUDIO_TYPES or name.startswith("."):
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, MUSIC_ROOT)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                seen.add(rel)
                if known.get(rel) == (st.st_mtime, st.st_size):
                    continue
                m = describe(full)
                tid = hashlib.sha1(rel.encode()).hexdigest()[:12]
                c.execute("""INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(path) DO UPDATE SET title=excluded.title, artist=excluded.artist,
                    album=excluded.album, duration=excluded.duration, cover=excluded.cover,
                    mtime=excluded.mtime, size=excluded.size""",
                          (tid, rel, m["title"], m["artist"], m["album"], m["duration"],
                           m["cover"], st.st_mtime, st.st_size, time.time()))
        for rel in set(known) - seen:
            c.execute("DELETE FROM tracks WHERE path = ?", (rel,))
        return len(seen)


def scan_loop():
    while True:
        time.sleep(SCAN_INTERVAL)
        try:
            scan()
        except Exception as e:  # keep serving even if one scan fails
            print("scan failed:", e, flush=True)


def library():
    with db() as c:
        favs = {r["id"] for r in c.execute("SELECT id FROM favorites")}
        rows = c.execute("SELECT id, title, artist, album, duration, cover, added FROM tracks "
                         "ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, title COLLATE NOCASE")
        return [dict(r, fav=r["id"] in favs) for r in rows]


def track_path(tid):
    with db() as c:
        r = c.execute("SELECT path FROM tracks WHERE id = ?", (tid,)).fetchone()
    if not r:
        return None
    p = os.path.realpath(os.path.join(MUSIC_ROOT, r["path"]))
    if not p.startswith(MUSIC_ROOT + os.sep) or not os.path.isfile(p):
        return None
    return p


# ---------- http ----------

def parse_range(header, size):
    """'bytes=a-b' -> (start, end) inclusive, None for no/invalid range, or 'bad'."""
    if not header or not header.startswith("bytes="):
        return None
    a, _, b = header[6:].split(",")[0].strip().partition("-")
    try:
        if a == "":
            start, end = max(0, size - int(b)), size - 1
        else:
            start, end = int(a), (int(b) if b else size - 1)
    except ValueError:
        return None
    if start >= size or start > end:
        return "bad"
    return start, min(end, size - 1)


class Handler(BaseHTTPRequestHandler):
    server_version = "musik/2.0"
    protocol_version = "HTTP/1.1"

    def _small(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, code, obj):
        self._small(code, "application/json", json.dumps(obj).encode(), {"Cache-Control": "no-store"})

    def _not_found(self):
        self._small(404, "text/plain; charset=utf-8", b"not found")

    def _send_file(self, path, ctype, cache="no-cache"):
        if not os.path.isfile(path):
            return self._not_found()
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as f:
                while True:
                    c = f.read(CHUNK)
                    if not c:
                        break
                    self.wfile.write(c)
        except BrokenPipeError:
            pass

    def _stream(self, path):
        size = os.path.getsize(path)
        ctype = AUDIO_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        rng = parse_range(self.headers.get("Range"), size)
        if rng == "bad":
            self.send_response(416)
            self.send_header("Content-Range", "bytes */%d" % size)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = rng or (0, size - 1)
        length = end - start + 1
        if rng:
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    c = f.read(min(CHUNK, remaining))
                    if not c:
                        break
                    self.wfile.write(c)
                    remaining -= len(c)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _cover(self, tid):
        path = track_path(tid)
        if not path or not path.lower().endswith(".mp3"):
            return self._not_found()
        try:
            cover = read_id3(path, want_cover=True)[0].get("cover")
        except (OSError, ValueError, IndexError, struct.error):
            cover = None
        if not isinstance(cover, tuple) or not cover[1]:
            return self._not_found()
        mime, img = cover
        self._small(200, mime, img, {"Cache-Control": "public, max-age=86400"})

    def _guard(self, route):
        """Run a route; any unexpected error becomes a 500 instead of a dropped connection."""
        try:
            route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self.close_connection = True
            try:
                self._json(500, {"error": "Musik hit an error. Check `journalctl -u songpage`."})
            except OSError:
                pass

    def do_GET(self):
        self._guard(self._get)

    def do_HEAD(self):
        self._guard(self._get)

    def do_POST(self):
        self._guard(self._post)

    def _get(self):
        p = urllib.parse.urlparse(self.path).path
        if p in STATIC:
            name, ctype = STATIC[p]
            self._send_file(os.path.join(HERE, name), ctype)
        elif p.startswith("/fonts/"):
            name = os.path.basename(urllib.parse.unquote(p))
            if not name.endswith(".woff2"):
                return self._not_found()
            self._send_file(os.path.join(HERE, "fonts", name), "font/woff2", "public, max-age=31536000")
        elif p == "/api/library":
            self._json(200, {"root": MUSIC_ROOT, "tracks": library()})
        elif p.startswith("/stream/"):
            path = track_path(p[8:])
            self._stream(path) if path else self._not_found()
        elif p.startswith("/cover/"):
            self._cover(p[7:])
        else:
            self._not_found()

    def _post(self):
        p = urllib.parse.urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            # The body stays unread, so this keep-alive connection can't be reused.
            self.close_connection = True
            return self._json(413, {"error": "Request body must be under %d bytes." % MAX_BODY})
        raw = self.rfile.read(length) if length else b""
        if p.startswith("/api/fav/"):
            tid = p[9:]
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return self._json(400, {"error": "Body must be JSON like {\"fav\": true}."})
            fav = bool(body.get("fav"))
            with db() as c:
                if not c.execute("SELECT 1 FROM tracks WHERE id = ?", (tid,)).fetchone():
                    return self._json(404, {"error": "no such track"})
                if fav:
                    c.execute("INSERT OR IGNORE INTO favorites VALUES (?, ?)", (tid, time.time()))
                else:
                    c.execute("DELETE FROM favorites WHERE id = ?", (tid,))
            self._json(200, {"id": tid, "fav": fav})
        elif p == "/api/rescan":
            self._json(200, {"count": scan()})
        else:
            self._not_found()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    mimetypes.init()
    init_db()
    n = scan()
    threading.Thread(target=scan_loop, daemon=True).start()
    print("musik: %d tracks from %s on %s:%d" % (n, MUSIC_ROOT, BIND, PORT), flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
