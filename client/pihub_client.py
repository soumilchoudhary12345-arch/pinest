#!/usr/bin/env python3
"""pihub-client - runs on each PC (Linux or Windows), stdlib only.

Polls PiHub for work and does it:
  sync   -> wake Syncthing, wait until the Pi has everything, report, pause again
  upload -> another PC asked for one of our files: send it (allowed folders only)
  fetch  -> a file we asked for is ready on the Pi: save it where requested

Usage:
  pihub_client.py              run forever (what the service does)
  pihub_client.py --once       one poll, then exit (testing)
  pihub_client.py --sync-now   run a sync locally without the Pi (fallback timer)
  pihub_client.py --check PATH say whether PATH may be shared
"""
import http.client
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

CHUNK = 1 << 16
DEFAULT_DENY = [".ssh", ".gnupg", ".env", "API Keys.md", "id_rsa", "id_ed25519", ".password-store", "Cookies"]


# Try IPv4 first. The Pi only accepts LAN connections, so its public IPv6 address (which mDNS
# or a cache can still hand out) is always dropped, and trying it first stalls every request.
_getaddrinfo = socket.getaddrinfo


def _ipv4_first(*args, **kwargs):
    return sorted(_getaddrinfo(*args, **kwargs), key=lambda r: r[0] != socket.AF_INET)


socket.getaddrinfo = _ipv4_first


def config_path():
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "pihub", "client.json")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "pihub", "client.json")


def load_config():
    with open(config_path(), encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("device", "hub_url", "token", "allowed_roots"):
        if not cfg.get(k):
            raise SystemExit("pihub-client: %s is missing from %s" % (k, config_path()))
    cfg.setdefault("deny", DEFAULT_DENY)
    cfg.setdefault("poll_seconds", 10)
    cfg["hub_url"] = cfg["hub_url"].rstrip("/")
    return cfg


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------- paths ----------

class Refused(Exception):
    pass


def resolve(cfg, path):
    """Absolute real path for a requested path, or Refused if outside allowed folders."""
    p = os.path.expanduser(str(path).strip())
    if not os.path.isabs(p):
        p = os.path.join(os.path.expanduser("~"), p)
    p = os.path.realpath(p)
    roots = [os.path.realpath(os.path.expanduser(r)) for r in cfg["allowed_roots"]]
    if not any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
        raise Refused("%s is outside the folders this PC shares (%s)." % (path, ", ".join(cfg["allowed_roots"])))
    parts = p.replace("\\", "/").split("/")
    for d in cfg["deny"]:
        if d in parts or os.path.basename(p).startswith(d):
            raise Refused("%s is on this PC's never-share list." % path)
    return p


def free_name(p):
    if not os.path.exists(p):
        return p
    stem, ext = os.path.splitext(p)
    n = 1
    while os.path.exists("%s (%d)%s" % (stem, n, ext)):
        n += 1
    return "%s (%d)%s" % (stem, n, ext)


# ---------- hub ----------

class Hub:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg["hub_url"]
        u = urllib.parse.urlparse(self.base)
        self.host, self.port = u.hostname, u.port or 80

    def call(self, method, path, body=None, timeout=20):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-Token": self.cfg["token"], "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error")
            except Exception:
                msg = None
            raise RuntimeError(msg or "PiHub answered %s" % e.code)

    def report(self, tid, status, **extra):
        body = dict(extra, status=status, device=self.cfg["device"])
        try:
            return self.call("POST", "/api/tasks/" + tid, body)
        except Exception as e:
            log("could not report", tid, status, e)

    def upload(self, tid, fp):
        size = os.path.getsize(fp)
        conn = http.client.HTTPConnection(self.host, self.port, timeout=60)
        try:
            conn.putrequest("PUT", "/api/inbox/" + tid)
            conn.putheader("X-Token", self.cfg["token"])
            conn.putheader("Content-Length", str(size))
            conn.endheaders()
            with open(fp, "rb") as f:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    conn.send(chunk)
            r = conn.getresponse()
            body = r.read()
            if r.status != 200:
                raise RuntimeError(json.loads(body or b"{}").get("error") or "upload failed (%s)" % r.status)
        finally:
            conn.close()

    def download(self, tid, dst):
        req = urllib.request.Request(self.base + "/api/inbox/" + tid, headers={"X-Token": self.cfg["token"]})
        tmp = dst + ".pihub-part"
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dst)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# ---------- syncthing ----------

class Syncthing:
    def __init__(self, cfg):
        st = cfg.get("syncthing") or {}
        self.url = st.get("url", "http://127.0.0.1:8384").rstrip("/")
        self.key = st.get("api_key")
        self.pi = st.get("pi_device_id")
        if not (self.key and self.pi):
            raise RuntimeError("syncthing.api_key and syncthing.pi_device_id must be set in client.json")

    def call(self, method, path, timeout=15):
        req = urllib.request.Request(self.url + path, method=method, headers={"X-API-Key": self.key},
                                     data=b"" if method == "POST" else None)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw.strip() else {}

    def patch_folder(self, fid, body):
        req = urllib.request.Request(self.url + "/rest/config/folders/" + urllib.parse.quote(fid), method="PATCH",
                                     data=json.dumps(body).encode(),
                                     headers={"X-API-Key": self.key, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()

    def pi_folders(self):
        return [f["id"] for f in self.call("GET", "/rest/config/folders")
                if any(d["deviceID"] == self.pi for d in f.get("devices", []))]

    def apply_live(self, live, rescan_s):
        """Live mode (client.json "live_folders"): those folders sync all the time, every `rescan_s`
        seconds plus on file changes; the rest stay paused until a sync round. The link to the Pi
        stays up, so pausing happens per folder instead of per device."""
        for fid in self.pi_folders():
            if fid in live:
                self.patch_folder(fid, {"paused": False, "rescanIntervalS": rescan_s, "fsWatcherEnabled": True,
                                        "fsWatcherDelayS": 2})
            else:
                self.patch_folder(fid, {"paused": True})
        self.call("POST", "/rest/system/resume?device=" + self.pi)

    def run_sync(self, deadline_s=7200, live=()):
        """Resume, rescan, wait until the Pi needs nothing from us, pause. Returns (items, bytes).

        With live folders, the round un-pauses the other folders instead of the whole device."""
        others = [f for f in self.pi_folders() if f not in live] if live else []
        for fid in others:
            self.patch_folder(fid, {"paused": False})
        self.call("POST", "/rest/system/resume?device=" + self.pi)
        try:
            self.call("POST", "/rest/db/scan")
            start = time.time()
            while not self.call("GET", "/rest/system/connections")["connections"].get(self.pi, {}).get("connected"):
                if time.time() - start > 300:
                    raise RuntimeError("Couldn't connect to the Pi's Syncthing within 5 minutes.")
                time.sleep(5)
            first, settled = None, 0
            while True:
                c = self.call("GET", "/rest/db/completion?device=" + self.pi)
                if first is None:
                    first = (c.get("needItems", 0), c.get("needBytes", 0))
                if c.get("completion", 0) >= 100 and not c.get("needBytes"):
                    settled += 1
                    if settled >= 2:
                        return first
                else:
                    settled = 0
                if time.time() - start > deadline_s:
                    raise RuntimeError("Sync didn't finish within %d minutes." % (deadline_s // 60))
                time.sleep(10)
        finally:
            try:
                if live:
                    for fid in others:
                        self.patch_folder(fid, {"paused": True})
                else:
                    self.call("POST", "/rest/system/pause?device=" + self.pi)
            except Exception as e:
                log("could not pause syncthing:", e)


# ---------- task handlers ----------

def do_sync(cfg, hub, t):
    hub.report(t["id"], "running")
    try:
        cfg = load_config()  # re-read: pairing writes the Syncthing keys after this client started
        items, nbytes = Syncthing(cfg).run_sync(live=cfg.get("live_folders") or ())
        hub.report(t["id"], "done", files=items, bytes=nbytes, note="Synced %d items (%d bytes)." % (items, nbytes))
        log("sync done:", items, "items")
    except Exception as e:
        hub.report(t["id"], "failed", error=str(e))
        log("sync failed:", e)


def do_upload(cfg, hub, t):
    try:
        fp = resolve(cfg, t["src_path"])
        if not os.path.isfile(fp):
            raise Refused("%s doesn't exist on %s." % (t["src_path"], cfg["device"]))
        hub.upload(t["id"], fp)
        log("sent", fp, "for task", t["id"])
    except Exception as e:
        hub.report(t["id"], "failed", error=str(e))
        log("upload refused/failed:", e)


def do_fetch(cfg, hub, t):
    hub.report(t["id"], "running")
    try:
        dst = resolve(cfg, t["dst_path"])
        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(t["src_path"].replace("\\", "/")))
            dst = resolve(cfg, dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        dst = free_name(dst)
        hub.download(t["id"], dst)
        hub.report(t["id"], "done", note="Saved to %s on %s." % (dst, cfg["device"]))
        log("saved", dst)
    except Exception as e:
        hub.report(t["id"], "failed", error=str(e))
        log("fetch failed:", e)


busy = set()
busy_lock = threading.Lock()


def handle(cfg, hub, t, wait=False):
    with busy_lock:
        if t["id"] in busy:
            return
        busy.add(t["id"])
    if t["action"] == "sync":
        fn = do_sync
    elif t["action"] == "fetch" and t["status"] == "upload_wait" and t["src_device"] == cfg["device"]:
        fn = do_upload
    elif t["action"] == "fetch" and t["status"] == "ready" and t["dst_device"] == cfg["device"]:
        fn = do_fetch
    else:
        with busy_lock:
            busy.discard(t["id"])
        return

    def run():
        try:
            fn(cfg, hub, t)
        except Exception:
            traceback.print_exc()
        finally:
            with busy_lock:
                busy.discard(t["id"])
    th = threading.Thread(target=run, daemon=True)
    th.start()
    if wait:
        th.join()


def poll(cfg, hub, wait=False):
    tasks = hub.call("GET", "/api/tasks?device=" + urllib.parse.quote(cfg["device"]))["tasks"]
    for t in tasks:
        handle(cfg, hub, t, wait)
    return len(tasks)


def main(argv):
    cfg = load_config()
    hub = Hub(cfg)
    if "--check" in argv:
        p = argv[argv.index("--check") + 1]
        try:
            print("allowed:", resolve(cfg, p))
        except Refused as e:
            print("refused:", e)
        return
    if "--sync-now" in argv:
        items, nbytes = Syncthing(cfg).run_sync(live=cfg.get("live_folders") or ())
        log("local sync done:", items, "items,", nbytes, "bytes")
        return
    if "--once" in argv:
        log("tasks:", poll(cfg, hub, wait=True))
        return
    if cfg.get("live_folders"):
        try:
            Syncthing(cfg).apply_live(cfg["live_folders"], int(cfg.get("live_rescan_seconds", 20)))
            log("live folders (every %ss):" % cfg.get("live_rescan_seconds", 20), ", ".join(cfg["live_folders"]))
        except Exception as e:
            log("could not set up live folders:", e)
    log("pihub-client for %s polling %s every %ss" % (cfg["device"], cfg["hub_url"], cfg["poll_seconds"]))
    backoff = cfg["poll_seconds"]
    while True:
        try:
            poll(cfg, hub)
            backoff = cfg["poll_seconds"]
        except Exception as e:
            log("hub unreachable:", e)
            backoff = min(backoff * 2, 300)
        time.sleep(backoff)


if __name__ == "__main__":
    main(sys.argv[1:])
