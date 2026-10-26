#!/usr/bin/env python3
"""picloud - a tiny, dependency-free web file cloud.

Serves a single-page UI over the files in CLOUD_ROOT so you can upload from
one device and download from another on the same network. Stdlib only.
"""
import json
import mimetypes
import os
import shutil
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.realpath(os.environ.get("CLOUD_ROOT", "/home/soumil/cloud"))
PORT = int(os.environ.get("CLOUD_PORT", "80"))
BIND = os.environ.get("CLOUD_BIND", "0.0.0.0")
HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK = 1 << 16
PART = ".part"
os.makedirs(ROOT, exist_ok=True)

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/fonts.css": ("fonts.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


class ClientError(Exception):
    """A request problem the user can fix; becomes a 4xx with this message."""

    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def safe_path(rel):
    """Resolve rel inside ROOT, following symlinks, or raise ClientError."""
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    p = os.path.realpath(os.path.join(ROOT, rel))
    if p != ROOT and not p.startswith(ROOT + os.sep):
        raise ClientError("That path is outside the cloud folder.")
    return p


def check_name(name):
    name = (name or "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ClientError("Names can't be empty or contain slashes.")
    if name.endswith(PART):
        raise ClientError("Names can't end in %s." % PART)
    return name


def list_dir(rel):
    d = safe_path(rel)
    if not os.path.isdir(d):
        raise ClientError("That folder doesn't exist.", 404)
    entries = []
    with os.scandir(d) as it:
        for e in it:
            if e.name.endswith(PART):  # unfinished upload
                continue
            try:
                st = e.stat()
                is_dir = e.is_dir()
            except OSError:
                continue
            entries.append({
                "name": e.name,
                "dir": is_dir,
                "size": 0 if is_dir else st.st_size,
                "mtime": int(st.st_mtime),
            })
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return entries


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
    server_version = "picloud/2.0"

    # ---------- responses ----------

    def _send(self, code, ctype, body, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, code, obj):
        self._send(code, "application/json; charset=utf-8", json.dumps(obj))

    def _err(self, code, msg):
        self._json(code, {"error": msg})

    def _run(self, fn, *args):
        """Call a route; turn exceptions into clean JSON errors."""
        try:
            fn(*args)
        except ClientError as e:
            self._err(e.code, str(e))
        except FileNotFoundError:
            self._err(404, "That file or folder no longer exists.")
        except PermissionError:
            self._err(403, "The Pi isn't allowed to touch that file.")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._err(500, "Something went wrong on the Pi. Check `journalctl -u picloud`.")

    # ---------- GET ----------

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        arg = lambda k: q.get(k, [""])[0]
        if u.path in STATIC:
            name, ctype = STATIC[u.path]
            self._run(self._static, os.path.join(HERE, name), ctype)
        elif u.path.startswith("/fonts/") and u.path.endswith(".woff2"):
            name = os.path.basename(urllib.parse.unquote(u.path))
            self._run(self._static, os.path.join(HERE, "fonts", name), "font/woff2")
        elif u.path == "/api/list":
            self._run(lambda: self._json(200, {"path": arg("path"), "entries": list_dir(arg("path"))}))
        elif u.path == "/api/usage":
            self._run(self._usage)
        elif u.path == "/download":
            self._run(self._download, arg("path"), arg("inline") == "1")
        else:
            self._err(404, "not found")

    def _static(self, path, ctype):
        if not os.path.isfile(path):
            raise ClientError("not found", 404)
        with open(path, "rb") as f:
            self._send(200, ctype, f.read(), {"Cache-Control": "no-cache"})

    def _usage(self):
        du = shutil.disk_usage(ROOT)
        self._json(200, {"total": du.total, "used": du.used, "free": du.free})

    def _download(self, rel, inline):
        fp = safe_path(rel)
        if not os.path.isfile(fp):
            raise ClientError("That file doesn't exist.", 404)
        size = os.path.getsize(fp)
        name = os.path.basename(fp)
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        if inline and ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        rng = parse_range(self.headers.get("Range"), size)
        if rng == "bad":
            self.send_response(416)
            self.send_header("Content-Range", "bytes */%d" % size)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = rng or (0, size - 1)
        length = max(0, end - start + 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        disp = "inline" if inline else "attachment"
        self.send_header("Content-Disposition", "%s; filename*=UTF-8''%s" % (disp, urllib.parse.quote(name)))
        self.end_headers()
        with open(fp, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ---------- POST ----------

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        arg = lambda k: q.get(k, [""])[0]
        routes = {
            "/upload": lambda: self._upload(arg("path")),
            "/mkdir": lambda: self._mkdir(arg("path")),
            "/delete": lambda: self._delete(arg("path")),
            "/rename": lambda: self._rename(arg("path"), arg("to")),
        }
        if u.path in routes:
            self._run(routes[u.path])
        else:
            self._err(404, "not found")

    def _upload(self, rel):
        if self.headers.get("Content-Length") is None:
            raise ClientError("Upload is missing its size (Content-Length).", 411)
        length = int(self.headers["Content-Length"])
        fp = safe_path(rel)
        if fp == ROOT or os.path.isdir(fp):
            raise ClientError("A folder with that name already exists.", 409)
        check_name(os.path.basename(fp))
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        tmp = fp + PART
        try:
            remaining = length
            with open(tmp, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                raise ClientError("The upload stopped before it finished. Try again.")
            os.replace(tmp, fp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self._json(200, {"ok": True, "path": rel, "bytes": length})

    def _mkdir(self, rel):
        fp = safe_path(rel)
        check_name(os.path.basename(fp))
        if os.path.exists(fp):
            raise ClientError("Something with that name is already here.", 409)
        os.makedirs(fp)
        self._json(200, {"ok": True})

    def _delete(self, rel):
        fp = safe_path(rel)
        if fp == ROOT:
            raise ClientError("The cloud folder itself can't be deleted.")
        if os.path.isdir(fp) and not os.path.islink(fp):
            shutil.rmtree(fp)
        else:
            os.remove(fp)
        self._json(200, {"ok": True})

    def _rename(self, rel, new_name):
        fp = safe_path(rel)
        if fp == ROOT:
            raise ClientError("The cloud folder itself can't be renamed.")
        dst = os.path.join(os.path.dirname(fp), check_name(new_name))
        safe_path(os.path.relpath(dst, ROOT))
        if os.path.exists(dst):
            raise ClientError("Something with that name is already here.", 409)
        os.rename(fp, dst)
        self._json(200, {"ok": True})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print("picloud serving %s on %s:%d" % (ROOT, BIND, PORT), flush=True)
    srv.serve_forever()
