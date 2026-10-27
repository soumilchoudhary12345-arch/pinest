#!/usr/bin/env python3
"""PiHub - the homelab's coordinator on the Raspberry Pi.

* File inventory: indexes /srv/nas/devices/<device>/... into SQLite for search.
* Sync rounds: every SYNC_INTERVAL_DAYS, queues a "sync" task per PC; each PC's
  pihub-client wakes Syncthing, waits for 100%, reports back, pauses again.
* File fetch: an agent on one PC asks for a file from another PC. The live file
  is relayed through /srv/nas/inbox when the source PC is online; otherwise the
  last synced copy is served.
* Agent monitor: coding-agent hooks post small events; /agents shows sessions.
* Join: /join/linux.sh and /join/windows.ps1 set up a new PC in one run; it
  pairs Syncthing through /api/pair, so no ssh login to the Pi is needed.

PCs never get pushed to: they poll /api/tasks. Stdlib only.
"""
import contextlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAS = os.path.realpath(os.environ.get("PIHUB_NAS", "/srv/nas"))
DEVICES_DIR = os.path.join(NAS, "devices")
INBOX = os.path.join(NAS, "inbox")
DB_PATH = os.environ.get("PIHUB_DB", os.path.join(NAS, "pihub.db"))
TOKEN = os.environ.get("PIHUB_TOKEN", "")
PORT = int(os.environ.get("PIHUB_PORT", "8899"))
BIND = os.environ.get("PIHUB_BIND", "0.0.0.0")
SYNC_INTERVAL_DAYS = float(os.environ.get("SYNC_INTERVAL_DAYS", "4"))
INDEX_INTERVAL = int(os.environ.get("INDEX_INTERVAL", "300"))
ONLINE_SECONDS = int(os.environ.get("ONLINE_SECONDS", "90"))   # a PC that polled this recently is "on"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # repo root in the pinest layout (hub/app.py)


def find(*names):
    """A file next to app.py (the Pi's flat layout), or under the repo root (pinest: hub/, agents/, ...)."""
    for base in (HERE, ROOT):
        for n in names:
            p = os.path.join(base, n)
            if os.path.isfile(p):
                return p
    return os.path.join(HERE, names[0])
# The Pi's name (mDNS), so PCs keep finding it when it moves to another network or gets a new IP.
PUBLIC_URL = os.environ.get("PIHUB_PUBLIC_URL", "http://piscreen.local:8899")
ST_URL = os.environ.get("PIHUB_ST_URL", "http://127.0.0.1:8384")
ST_CONFIG = os.environ.get("PIHUB_ST_CONFIG", os.path.expanduser("~/.local/state/syncthing/config.xml"))
CHUNK = 1 << 16
MAX_JSON = 64 * 1024

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/agents": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/fonts.css": ("fonts.css", "text/css; charset=utf-8"),
    "/pi": (("pi.html", "agents/pi.html"), "text/html; charset=utf-8"),   # Agents app for the Pi's touchscreen
    "/pi.js": (("pi.js", "agents/pi.js"), "text/javascript; charset=utf-8"),
    "/pi.css": (("pi.css", "agents/pi.css"), "text/css; charset=utf-8"),
}

# Files a new PC downloads while joining. Served without the token (the Pi only
# accepts LAN connections); join scripts get the token baked in when served.
# Each entry: (Pi's flat layout, pinest repo layout).
JOIN_FILES = {
    "pihub_client.py": ("client/pihub_client.py", "client/pihub_client.py"),
    "pihub_mcp.py": ("client/pihub_mcp.py", "client/pihub_mcp.py"),
    "report_event.py": ("client/report_event.py", "agents/report_event.py"),
    "connect_agents.py": ("client/connect_agents.py", "agents/connect_agents.py"),
    "pair_syncthing.py": ("setup/pair_syncthing.py", "client/pair_syncthing.py"),
    "pihub-client.service": ("client/linux/pihub-client.service", "client/linux/pihub-client.service"),
    "pihub-sync-fallback.service": ("client/linux/pihub-sync-fallback.service", "client/linux/pihub-sync-fallback.service"),
    "pihub-sync-fallback.timer": ("client/linux/pihub-sync-fallback.timer", "client/linux/pihub-sync-fallback.timer"),
    "favicon.svg": ("favicon.svg", "hub/favicon.svg"),
}
JOIN_SCRIPTS = {"linux.sh": ("join/linux.sh", "dependencies/linux.sh"),
                "windows.ps1": ("join/windows.ps1", "dependencies/windows.ps1"),
                "join-pi.cmd": ("join/join-pi.cmd", "dependencies/join-pi.cmd")}

# Folders a PC may share, and where the Pi keeps each one.
PAIR_FOLDERS = ("Documents", "Desktop", "Pictures", "Videos", "Downloads", "Music")
DEVICE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ST_ID_RE = re.compile(r"^[A-Z0-9]{7}(-[A-Z0-9]{7}){7}$")


class ClientError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def now():
    return time.time()


# ---------- database ----------

@contextlib.contextmanager
def db():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def init_db():
    for d in (DEVICES_DIR, INBOX):
        os.makedirs(d, exist_ok=True)
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                name TEXT PRIMARY KEY, last_seen REAL, last_sync REAL,
                last_sync_files INTEGER, last_sync_bytes INTEGER, note TEXT);
            CREATE TABLE IF NOT EXISTS files (
                device TEXT, path TEXT, name TEXT, size INTEGER, mtime REAL, seen REAL,
                PRIMARY KEY (device, path));
            CREATE INDEX IF NOT EXISTS files_name ON files(name COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS rounds (id INTEGER PRIMARY KEY, created REAL, forced INTEGER);
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, action TEXT, device TEXT,
                src_device TEXT, src_path TEXT, dst_device TEXT, dst_path TEXT,
                status TEXT, source TEXT, note TEXT, error TEXT,
                created REAL, updated REAL);
            CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, device TEXT, agent TEXT, session TEXT,
                event TEXT, tool TEXT, summary TEXT, cwd TEXT, ts REAL);
            CREATE INDEX IF NOT EXISTS events_session ON events(device, agent, session, ts);
        """)
        if "machine" not in [r[1] for r in c.execute("PRAGMA table_info(devices)")]:
            c.execute("ALTER TABLE devices ADD COLUMN machine TEXT")  # set by /api/register


def register_device(body):
    """Name a new PC <user>_<os>: soumil_win, soumil_linux. A number is added only
    when that exact name is taken (soumil_win_2, soumil_win_3); the first PC keeps
    its name, since its synced folders on the Pi are stored under it.

    The machine ID (Linux /etc/machine-id, Windows MachineGuid) makes it stable:
    running the join script again on the same PC gets the same name back.
    """
    machine = str(body.get("machine") or "").strip().lower()
    if not re.match(r"^[a-z0-9-]{8,64}$", machine):
        raise ClientError("machine must be the PC's machine ID.")
    osname = body.get("os")
    if osname not in ("win", "linux"):
        raise ClientError("os must be win or linux.")
    user = re.sub(r"[^a-z0-9]+", "-", str(body.get("user") or "").lower()).strip("-")[:28] or "pc"
    base = user + "_" + osname
    with db() as c:
        row = c.execute("SELECT name FROM devices WHERE machine = ?", (machine,)).fetchone()
        if row:
            return {"device": row[0], "new": False}
        taken = {r[0] for r in c.execute("SELECT name FROM devices")}
        taken |= set(os.listdir(DEVICES_DIR)) if os.path.isdir(DEVICES_DIR) else set()
        name, n = base, 1
        while name in taken:
            n += 1
            name = "%s_%d" % (base, n)
        c.execute("INSERT INTO devices(name, last_seen, machine) VALUES (?, ?, ?)", (name, now(), machine))
    return {"device": name, "new": True}


def touch_device(name):
    with db() as c:
        c.execute("INSERT INTO devices(name, last_seen) VALUES (?, ?) "
                  "ON CONFLICT(name) DO UPDATE SET last_seen = excluded.last_seen", (name, now()))


def is_online(name):
    with db() as c:
        r = c.execute("SELECT last_seen FROM devices WHERE name = ?", (name,)).fetchone()
    return bool(r and r["last_seen"] and now() - r["last_seen"] < ONLINE_SECONDS)


# ---------- inventory ----------

index_lock = threading.Lock()


def index_once():
    """Walk devices/<device>/..., upsert changed files, drop vanished ones."""
    with index_lock, db() as c:
        known = {(r["device"], r["path"]): (r["size"], r["mtime"])
                 for r in c.execute("SELECT device, path, size, mtime FROM files")}
        seen = set()
        stamp = now()
        for device in sorted(os.listdir(DEVICES_DIR)):
            top = os.path.join(DEVICES_DIR, device)
            if device.startswith(".") or not os.path.isdir(top):
                continue
            for root, dirs, files in os.walk(top):
                dirs[:] = [d for d in dirs if not d.startswith(".st") and d != ".stversions"]
                for name in files:
                    if name.startswith(".syncthing.") or name.endswith(".tmp"):
                        continue
                    full = os.path.join(root, name)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    rel = os.path.relpath(full, top)
                    key = (device, rel)
                    seen.add(key)
                    if known.get(key) == (st.st_size, st.st_mtime):
                        continue
                    c.execute("INSERT INTO files VALUES (?,?,?,?,?,?) ON CONFLICT(device, path) DO UPDATE "
                              "SET size=excluded.size, mtime=excluded.mtime, seen=excluded.seen",
                              (device, rel, name, st.st_size, st.st_mtime, stamp))
        gone = set(known) - seen
        c.executemany("DELETE FROM files WHERE device = ? AND path = ?", list(gone))
        return len(seen)


def index_loop():
    while True:
        try:
            index_once()
        except Exception:
            traceback.print_exc()
        time.sleep(INDEX_INTERVAL)


def search(q, device=None, limit=50):
    q = (q or "").strip()
    if not q:
        raise ClientError("Give a search term, e.g. ?q=report")
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sql = "SELECT device, path, name, size, mtime FROM files WHERE (name LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\')"
    args = [like, like]
    if device:
        sql += " AND device = ?"
        args.append(device)
    sql += " ORDER BY (name LIKE ? ESCAPE '\\') DESC, mtime DESC LIMIT ?"
    args += [q.replace("%", "") + "%", int(limit)]
    with db() as c:
        return [dict(r) for r in c.execute(sql, args)]


def stored_path(device, rel):
    """Synced copy of device:rel on the NAS, or ClientError."""
    if not device or "/" in device or device.startswith("."):
        raise ClientError("Unknown device.")
    base = os.path.realpath(os.path.join(DEVICES_DIR, device))
    p = os.path.realpath(os.path.join(base, (rel or "").lstrip("/")))
    if not p.startswith(base + os.sep):
        raise ClientError("That path is outside the device's folder.")
    return p


# ---------- sync rounds ----------

def sync_round(force=False, only=None):
    """Queue a sync task for each known device if the interval has passed (or forced)."""
    with db() as c:
        last = c.execute("SELECT MAX(created) AS t FROM rounds").fetchone()["t"]
        due = last is None or now() - last >= SYNC_INTERVAL_DAYS * 86400 - 3600  # 1 h slack for timer jitter
        if not (due or force):
            return {"created": [], "due": False, "next": last + SYNC_INTERVAL_DAYS * 86400}
        names = [only] if only else [r["name"] for r in c.execute("SELECT name FROM devices")]
        created = []
        for name in names:
            busy = c.execute("SELECT 1 FROM tasks WHERE action='sync' AND device=? AND status IN ('queued','running')",
                             (name,)).fetchone()
            if busy:
                continue
            tid = uuid.uuid4().hex[:12]
            c.execute("INSERT INTO tasks(id, action, device, status, created, updated) VALUES (?,?,?,?,?,?)",
                      (tid, "sync", name, "queued", now(), now()))
            created.append({"id": tid, "device": name})
        if not only:
            c.execute("INSERT INTO rounds(created, forced) VALUES (?, ?)", (now(), int(force)))
        return {"created": created, "due": due}


# ---------- file fetch ----------

def request_file(src_device, src_path, dst_device, dst_path):
    for k, v in (("src_device", src_device), ("src_path", src_path), ("dst_device", dst_device), ("dst_path", dst_path)):
        if not v or not str(v).strip():
            raise ClientError("Missing %s." % k)
    tid = uuid.uuid4().hex[:12]
    row = {"id": tid, "action": "fetch", "device": dst_device, "src_device": src_device, "src_path": src_path,
           "dst_device": dst_device, "dst_path": dst_path, "created": now(), "updated": now()}
    if is_online(src_device):
        row.update(status="upload_wait", source="live", note="Waiting for %s to send the current file." % src_device)
    else:
        copy = stored_path(src_device, src_path)
        if not os.path.isfile(copy):
            raise ClientError("%s is offline and the Pi has no synced copy of %s." % (src_device, src_path), 404)
        age = (now() - os.path.getmtime(copy)) / 86400
        row.update(status="ready", source="stored",
                   note="%s is offline; using the copy from the last sync (file changed %.1f days ago)." % (src_device, age))
    with db() as c:
        c.execute("INSERT INTO tasks(id, action, device, src_device, src_path, dst_device, dst_path, status, source, note,"
                  " created, updated) VALUES (:id,:action,:device,:src_device,:src_path,:dst_device,:dst_path,:status,"
                  ":source,:note,:created,:updated)", row)
    return task(tid)


def task(tid):
    with db() as c:
        r = c.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    if not r:
        raise ClientError("No such task.", 404)
    return dict(r)


def tasks_for(device):
    """What this PC should do now: its sync tasks, uploads it must send, downloads ready for it."""
    with db() as c:
        rows = c.execute("""
            SELECT * FROM tasks WHERE
              (action='sync'  AND device=? AND status='queued') OR
              (action='fetch' AND src_device=? AND status='upload_wait') OR
              (action='fetch' AND dst_device=? AND status='ready')
            ORDER BY created""", (device, device, device)).fetchall()
    return [dict(r) for r in rows]


def update_task(tid, body):
    t = task(tid)
    status = body.get("status")
    if status not in ("running", "done", "failed"):
        raise ClientError("status must be running, done or failed.")
    with db() as c:
        note = body.get("note") or (None if status != "failed" else "")
        c.execute("UPDATE tasks SET status=?, error=?, note=COALESCE(?, note), updated=? WHERE id=?",
                  (status, body.get("error"), note, now(), tid))
        if t["action"] == "sync" and status == "done":
            c.execute("UPDATE devices SET last_sync=?, last_sync_files=?, last_sync_bytes=? WHERE name=?",
                      (now(), body.get("files"), body.get("bytes"), t["device"]))
    if t["action"] == "fetch" and status in ("done", "failed"):
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(INBOX, tid))
    return task(tid)


def expire_tasks():
    """Fail transfers nobody completed within a day, and drop their inbox files."""
    cutoff = now() - 86400
    with db() as c:
        old = [r["id"] for r in c.execute(
            "SELECT id FROM tasks WHERE status IN ('queued','upload_wait','ready','running') AND updated < ?", (cutoff,))]
        c.executemany("UPDATE tasks SET status='failed', error='Timed out after 24 hours.' WHERE id=?",
                      [(i,) for i in old])
        c.execute("DELETE FROM events WHERE ts < ?", (now() - 7 * 86400,))
    for i in old:
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(INBOX, i))


def housekeeping_loop():
    while True:
        time.sleep(3600)
        try:
            expire_tasks()
        except Exception:
            traceback.print_exc()


# ---------- agent monitor ----------

def add_event(body):
    def s(k, n=200):
        v = body.get(k)
        return str(v)[:n] if v is not None else None
    with db() as c:
        c.execute("INSERT INTO events(device, agent, session, event, tool, summary, cwd, ts) VALUES (?,?,?,?,?,?,?,?)",
                  (s("device", 60), s("agent", 40), s("session", 80), s("event", 40), s("tool", 60),
                   s("summary", 200), s("cwd", 200), now()))


AGENT_NAMES = {"claude-code": "Claude Code", "opencode": "OpenCode", "copilot": "Copilot CLI",
               "antigravity": "Antigravity", "codex": "Codex", "crush": "Crush", "gemini": "Gemini CLI"}
# Agents whose hooks can't say "finished" (Crush 0.97 only has PreToolUse): call them quiet sooner.
QUIET_AFTER = {"crush": 120}
TOOL_PHRASES = {
    "Read": "Reading {s}", "Edit": "Editing {s}", "MultiEdit": "Editing {s}", "Write": "Writing {s}",
    "NotebookEdit": "Editing the notebook {s}", "Grep": "Searching the code for \u201c{s}\u201d",
    "Glob": "Looking for files matching {s}", "WebSearch": "Searching the web for \u201c{s}\u201d",
    "Agent": "Helper agent: {s}", "Task": "Helper agent: {s}", "Skill": "Using the {s} skill",
}
TOOL_ALONE = {"TodoWrite": "Updating its to-do list", "AskUserQuestion": "Asking you a question",
              "ExitPlanMode": "Proposing a plan for you to approve", "EnterPlanMode": "Planning before it changes anything"}


def describe(event, tool, summary, state, last=None):
    """One plain sentence for what an agent is doing, built from its latest hook event.

    `last` is the session's latest (tool, summary), so a finished or quiet agent
    can still say what it did last instead of a generic "done".
    """
    s = (summary or "").strip()
    if event == "Notification" and state != "ended":
        return s or "Waiting for your answer."
    if event in ("Stop", "SubagentStop", "SessionEnd") or state == "ended":
        head = "Session ended." if state == "ended" else "Done."
        if last and last[0]:
            step = describe("PreToolUse", last[0], last[1], "running")
            return "%s Last step: %s" % (head, step[:1].lower() + step[1:])
        return head + " Waiting for your next message." if head == "Done." else head
    if event == "SessionStart":
        return "Just started a session."
    tool = tool or ""
    if tool in TOOL_ALONE:
        doing = TOOL_ALONE[tool]
    elif tool in TOOL_PHRASES and s:
        doing = TOOL_PHRASES[tool].format(s=s)
    elif tool == "WebFetch" and s:
        doing = "Reading " + (urllib.parse.urlparse(s).hostname or s)
    elif tool == "Bash":
        doing = s or "Running a command"   # the hook sends the command's own description when it has one
    elif tool.startswith("mcp__"):
        parts = tool.split("__")
        doing = "Using %s: %s" % (parts[1], parts[-1].replace("_", " ")) if len(parts) >= 3 else "Using " + tool
    elif tool:
        doing = "Using %s" % tool + (": " + s if s else "")
    else:
        doing = "Working"
    if state == "idle":
        return "Quiet for a while. Last step: " + doing[:1].lower() + doing[1:]
    return doing


def agent_sessions():
    """Latest event per session in the last 12 hours, with a plain-language state."""
    with db() as c:
        rows = c.execute("""
            SELECT e.* FROM events e JOIN (
              SELECT device, agent, session, MAX(id) AS id, COUNT(*) AS n FROM events
              WHERE ts > ? GROUP BY device, agent, session) last ON e.id = last.id
            ORDER BY e.ts DESC""", (now() - 12 * 3600,)).fetchall()
        lasts = {(r["device"], r["agent"], r["session"]): (r["tool"], r["summary"]) for r in c.execute("""
            SELECT e.device, e.agent, e.session, e.tool, e.summary FROM events e JOIN (
              SELECT MAX(id) AS id FROM events WHERE ts > ? AND tool IS NOT NULL
              GROUP BY device, agent, session) last ON e.id = last.id""", (now() - 12 * 3600,))}
        counts = {(r["device"], r["agent"], r["session"]): r["n"] for r in c.execute(
            "SELECT device, agent, session, COUNT(*) AS n FROM events WHERE ts > ? GROUP BY device, agent, session",
            (now() - 12 * 3600,))}
    out = []
    for r in rows:
        ev = r["event"] or ""
        age = now() - r["ts"]
        if ev in ("SessionEnd",):
            state = "ended"
        elif ev in ("Stop", "SubagentStop") or age > QUIET_AFTER.get(r["agent"], 600):
            state = "idle"
        elif ev == "Notification":
            state = "waiting"
        else:
            state = "running"
        d = dict(r)
        d.update(state=state, age=age, events=counts.get((r["device"], r["agent"], r["session"]), 0),
                 agent_name=AGENT_NAMES.get(r["agent"], r["agent"]),
                 doing=describe(ev, r["tool"], r["summary"], state, lasts.get((r["device"], r["agent"], r["session"]))))
        out.append(d)
    return out


def syncthing(method, path, payload=None, timeout=15):
    """Call the Pi's own Syncthing REST API."""
    try:
        with open(ST_CONFIG, encoding="utf-8") as f:
            key = re.search(r"<apikey>([^<]+)</apikey>", f.read()).group(1)
    except (OSError, AttributeError):
        raise ClientError("Syncthing on the Pi isn't set up (no API key in %s)." % ST_CONFIG, 503)
    req = urllib.request.Request(ST_URL + path, method=method,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"X-API-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else {}


_storage = {"t": 0.0, "data": {}}
_storage_lock = threading.Lock()


def live_storage():
    """Per device: bytes already on the Pi, bytes the PC has, and whether it's uploading.

    Read from the Pi's Syncthing, which keeps these numbers current while a sync
    runs, so a new PC and its upload show up within seconds (the file index only
    catches up every INDEX_INTERVAL). Cached for 2 s; {} if Syncthing is down.
    """
    with _storage_lock:
        if now() - _storage["t"] < 2:
            return _storage["data"]
        out = {}
        try:
            prefix = DEVICES_DIR + os.sep
            for f in syncthing("GET", "/rest/config/folders", timeout=3):
                path = os.path.realpath(f.get("path") or "")
                if not path.startswith(prefix):
                    continue  # shared folders like music aren't one device's storage
                device = path[len(prefix):].split(os.sep, 1)[0]
                st = syncthing("GET", "/rest/db/status?folder=" + urllib.parse.quote(f["id"]), timeout=3)
                d = out.setdefault(device, {"on_pi": 0, "total": 0, "syncing": False})
                d["on_pi"] += st.get("localBytes", 0)
                d["total"] += max(st.get("globalBytes", 0), st.get("localBytes", 0))
                d["syncing"] = d["syncing"] or st.get("state") in ("syncing", "sync-preparing")
        except Exception:
            out = {}
        _storage.update(t=now(), data=out)
        return out


def pair_device(body):
    """Add a PC and its folders to the Pi's Syncthing (Receive Only, versioned).

    The PC sends its Syncthing ID, its device name and which standard folders it
    has; the Pi decides every path itself, so a PC can't point a share elsewhere.
    """
    device, pc_id = body.get("device") or "", body.get("pc_id") or ""
    if not DEVICE_RE.match(device):
        raise ClientError("Device name must be lowercase letters, digits, dashes and underscores (max 40).")
    if not ST_ID_RE.match(pc_id):
        raise ClientError("That isn't a Syncthing device ID.")
    wanted = body.get("folders") or []
    if not isinstance(wanted, list):
        raise ClientError("folders must be a list.")
    folders = []
    for name in wanted:
        if name in PAIR_FOLDERS:
            folders.append((device + "-" + name.lower(), "%s %s" % (device, name), os.path.join(DEVICES_DIR, device, name)))
        elif name == "OmaOpus":
            folders.append(("music-omaopus", "OmaOpus music", os.path.join(NAS, "music", "omaopus")))
        else:
            raise ClientError("Unknown folder %r." % name)
    st = syncthing
    try:
        me = st("GET", "/rest/system/status")["myID"]
        st("PUT", "/rest/config/devices/" + pc_id, {"deviceID": pc_id, "name": device, "addresses": ["dynamic"]})
        for fid, label, path in folders:
            os.makedirs(path, exist_ok=True)
            # Keep devices already on a shared folder (OmaOpus can come from several PCs), and keep
            # its type if it was switched to two-way on purpose (music: Send & Receive).
            try:
                old = st("GET", "/rest/config/folders/" + fid)
            except urllib.error.HTTPError:
                old = {}
            ids = {d["deviceID"] for d in old.get("devices", [])} | {me, pc_id}
            st("PUT", "/rest/config/folders/" + fid, {
                "id": fid, "label": label, "path": path, "type": old.get("type", "receiveonly"),
                "devices": [{"deviceID": i} for i in sorted(ids)],
                "versioning": {"type": "staggered", "params": {"maxAge": "2592000", "cleanInterval": "3600"}},
                "rescanIntervalS": 3600, "fsWatcherEnabled": False})
    except (urllib.error.URLError, OSError) as e:
        raise ClientError("Couldn't talk to Syncthing on the Pi: %s" % e, 502)
    touch_device(device)
    return {"pi_id": me, "folders": [[fid, label] for fid, label, _ in folders]}


def overview():
    with db() as c:
        devs = [dict(r) for r in c.execute("SELECT * FROM devices ORDER BY name")]
        counts = {r["device"]: (r["n"], r["b"]) for r in c.execute(
            "SELECT device, COUNT(*) AS n, COALESCE(SUM(size),0) AS b FROM files GROUP BY device")}
        last_round = c.execute("SELECT MAX(created) AS t FROM rounds").fetchone()["t"]
        recent = [dict(r) for r in c.execute("SELECT * FROM tasks ORDER BY created DESC LIMIT 30")]
    storage = live_storage()
    for d in devs:
        d["online"] = bool(d["last_seen"] and now() - d["last_seen"] < ONLINE_SECONDS)
        d["files"], d["bytes"] = counts.get(d["name"], (0, 0))
        live = storage.get(d["name"])
        if live:
            d.update(on_pi=live["on_pi"], total=live["total"], syncing=live["syncing"])
        else:
            d.update(on_pi=d["bytes"], total=d["bytes"], syncing=False)
    du = shutil.disk_usage(NAS)
    return {"devices": devs, "tasks": recent, "last_round": last_round,
            "next_round": (last_round + SYNC_INTERVAL_DAYS * 86400) if last_round else None,
            "interval_days": SYNC_INTERVAL_DAYS, "disk": {"total": du.total, "free": du.free}}


# ---------- http ----------

def parse_range(header, size):
    if not header or not header.startswith("bytes="):
        return None
    a, _, b = header[6:].split(",")[0].strip().partition("-")
    try:
        start, end = (max(0, size - int(b)), size - 1) if a == "" else (int(a), int(b) if b else size - 1)
    except ValueError:
        return None
    if start >= size or start > end:
        return "bad"
    return start, min(end, size - 1)


class Handler(BaseHTTPRequestHandler):
    server_version = "pihub/1.0"
    protocol_version = "HTTP/1.1"

    def _send(self, code, ctype, body, extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, "application/json; charset=utf-8", json.dumps(obj))

    def _body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_JSON:
            self.close_connection = True
            raise ClientError("Body too large.", 413)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise ClientError("Body must be JSON.")
        if not isinstance(body, dict):
            raise ClientError("Body must be a JSON object.")
        return body

    def _authed(self, q):
        """X-Token header, or ?token= on GETs so plain download links work."""
        if not TOKEN:
            return True
        got = self.headers.get("X-Token") or (q.get("token", "") if self.command in ("GET", "HEAD") else "")
        return hmac.compare_digest(got.encode(), TOKEN.encode())

    def _run(self, fn):
        try:
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if u.path.startswith("/api/") and not self._authed(q):
                self.close_connection = True
                return self._json(401, {"error": "Missing or wrong X-Token. It's in /etc/pihub.env on the Pi."})
            fn(u.path, q)
        except ClientError as e:
            self._json(e.code, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self.close_connection = True
            with contextlib.suppress(OSError):
                self._json(500, {"error": "PiHub hit an error. Check `journalctl -u pihub`."})

    def do_GET(self):
        self._run(self._get)

    do_HEAD = do_GET

    def do_POST(self):
        self._run(self._post)

    def do_PUT(self):
        self._run(self._put)

    def _get(self, path, q):
        if path in STATIC:
            name, ctype = STATIC[path]
            with open(find(*name) if isinstance(name, tuple) else os.path.join(HERE, name), "rb") as f:
                return self._send(200, ctype, f.read(), {"Cache-Control": "no-cache"})
        if path.startswith("/fonts/") and path.endswith(".woff2"):
            fp = os.path.join(HERE, "fonts", os.path.basename(path))
            if not os.path.isfile(fp):
                raise ClientError("not found", 404)
            with open(fp, "rb") as f:
                return self._send(200, "font/woff2", f.read(), {"Cache-Control": "public, max-age=31536000"})
        if path.startswith("/join/"):
            return self._join(path[len("/join/"):])
        if path == "/api/overview":
            return self._json(200, overview())
        if path == "/api/devices":
            return self._json(200, overview()["devices"])
        if path == "/api/search":
            return self._json(200, {"results": search(q.get("q"), q.get("device"), q.get("limit", 50))})
        if path == "/api/tasks":
            device = q.get("device")
            if not device:
                raise ClientError("Pass ?device=<name>.")
            touch_device(device)
            return self._json(200, {"tasks": tasks_for(device)})
        if path.startswith("/api/tasks/"):
            return self._json(200, task(path.rsplit("/", 1)[1]))
        if path == "/api/agents":
            return self._json(200, {"sessions": agent_sessions()})
        if path == "/api/file":
            return self._file(stored_path(q.get("device"), q.get("path")))
        if path.startswith("/api/inbox/"):
            t = task(path.rsplit("/", 1)[1])
            if t["status"] not in ("ready", "running"):  # the receiver marks it running while it downloads
                raise ClientError("That file isn't ready yet.", 409)
            src = os.path.join(INBOX, t["id"]) if t["source"] == "live" else stored_path(t["src_device"], t["src_path"])
            return self._file(src)
        raise ClientError("not found", 404)

    def _post(self, path, q):
        if path == "/api/sync-round":
            b = self._body_json()
            return self._json(200, sync_round(force=bool(b.get("force"))))
        if path == "/api/sync-now":
            b = self._body_json()
            return self._json(200, sync_round(force=True, only=b.get("device") or None))
        if path == "/api/reindex":
            return self._json(200, {"files": index_once()})
        if path == "/api/tasks":
            b = self._body_json()
            return self._json(200, request_file(b.get("src_device"), b.get("src_path"),
                                                b.get("dst_device"), b.get("dst_path")))
        if path.startswith("/api/tasks/"):
            return self._json(200, update_task(path.rsplit("/", 1)[1], self._body_json()))
        if path == "/api/register":
            return self._json(200, register_device(self._body_json()))
        if path == "/api/pair":
            return self._json(200, pair_device(self._body_json()))
        if path == "/api/agent-events":
            add_event(self._body_json())
            return self._json(200, {"ok": True})
        raise ClientError("not found", 404)

    def _put(self, path, q):
        if not path.startswith("/api/inbox/"):
            raise ClientError("not found", 404)
        t = task(path.rsplit("/", 1)[1])
        if t["status"] != "upload_wait":
            raise ClientError("This task isn't waiting for an upload.", 409)
        if self.headers.get("Content-Length") is None:
            raise ClientError("Upload needs Content-Length.", 411)
        length = int(self.headers["Content-Length"])
        if length > shutil.disk_usage(NAS).free - (512 << 20):
            self.close_connection = True
            raise ClientError("Not enough free space on the Pi for this file.", 507)
        dst = os.path.join(INBOX, t["id"])
        tmp = dst + ".part"
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
                raise ClientError("Upload stopped early.")
            os.replace(tmp, dst)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(tmp)
        with db() as c:
            c.execute("UPDATE tasks SET status='ready', note=?, updated=? WHERE id=?",
                      ("Received the current file from %s." % t["src_device"], now(), t["id"]))
        self._json(200, task(t["id"]))

    def _join(self, name):
        if name in JOIN_SCRIPTS:
            # Fallback: the address this PC just used to reach the Pi, for PCs that can't resolve .local names.
            host = self.headers.get("Host", "")
            fallback = "http://" + host if re.match(r"^[A-Za-z0-9.\-]+:\d+$", host) else PUBLIC_URL
            with open(find(*JOIN_SCRIPTS[name]), encoding="utf-8") as f:
                text = (f.read().replace("__PIHUB_URL__", PUBLIC_URL).replace("__PIHUB_FALLBACK_URL__", fallback)
                        .replace("__PIHUB_TOKEN__", TOKEN))
            if name.endswith(".ps1"):
                text = text.replace("\r\n", "\n").replace("\n", "\r\n")
            return self._send(200, "text/plain; charset=utf-8", text.encode("utf-8"))
        if name in JOIN_FILES:
            with open(find(*JOIN_FILES[name]), "rb") as f:
                return self._send(200, "text/plain; charset=utf-8", f.read())
        raise ClientError("not found", 404)

    def _file(self, fp):
        if not os.path.isfile(fp):
            raise ClientError("File not found on the Pi.", 404)
        size = os.path.getsize(fp)
        rng = parse_range(self.headers.get("Range"), size)
        if rng == "bad":
            return self._send(416, "text/plain", b"", {"Content-Range": "bytes */%d" % size})
        start, end = rng or (0, size - 1)
        length = max(0, end - start + 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(fp, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def log_message(self, *a):
        pass


def main():
    init_db()
    expire_tasks()
    threading.Thread(target=index_loop, daemon=True).start()
    threading.Thread(target=housekeeping_loop, daemon=True).start()
    if not TOKEN:
        print("pihub: WARNING no PIHUB_TOKEN set; API is open to the LAN", flush=True)
    print("pihub: %s on %s:%d (sync every %g days)" % (NAS, BIND, PORT, SYNC_INTERVAL_DAYS), flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
