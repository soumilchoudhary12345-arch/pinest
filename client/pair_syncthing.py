#!/usr/bin/env python3
"""Pair this PC's Syncthing with the Pi and set up the folders PiHub syncs.

Run once on each PC after Syncthing is installed and running (the join scripts
do this for you):
    python3 pair_syncthing.py            (Linux)
    py pair_syncthing.py                 (Windows)

What it does:
  * the Pi (through PiHub's /api/pair, using the token in the client config):
    adds this PC and its folders as Receive Only into /srv/nas/devices/<device>/,
    with Staggered versioning so a deletion on the PC never destroys the Pi's copy
  * this PC: adds the Pi as a device (paused; PiHub wakes it every 4 days) and
    shares each folder as Send Only
  * writes this PC's Syncthing API key into the pihub-client config
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "client")]  # installed next to pihub_client.py, or in the repo
from pihub_client import config_path, load_config  # noqa: E402

# By name, so the pairing survives the Pi changing network or IP; "dynamic" adds LAN discovery on top.
PI_ADDR = os.environ.get("PI_ADDR", "tcp://piscreen.local:22000")
ST = "http://127.0.0.1:8384"


def folders():
    """(name the Pi knows, local path) for each standard folder this PC has."""
    home = os.path.expanduser("~")
    out = [(n, os.path.join(home, n)) for n in ("Documents", "Desktop", "Downloads", "Pictures", "Videos")]
    out.append(("OmaOpus", os.path.join(home, "Music", "OmaOpus")))
    return [(n, p) for n, p in out if os.path.isdir(p)]


def st_config_file():
    cands = [os.path.expanduser("~/.local/state/syncthing/config.xml"), os.path.expanduser("~/.config/syncthing/config.xml")]
    if os.name == "nt":
        cands.insert(0, os.path.join(os.environ.get("LOCALAPPDATA", ""), "Syncthing", "config.xml"))
    for c in cands:
        if os.path.isfile(c):
            return c
    raise SystemExit("Couldn't find Syncthing's config.xml. Start Syncthing once, then run this again.")


def st(method, path, key, body=None):
    req = urllib.request.Request(ST + path, method=method, headers={"X-API-Key": key, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else {}


def hub(cfg, path, body):
    req = urllib.request.Request(cfg["hub_url"] + path, method="POST", data=json.dumps(body).encode(),
                                 headers={"X-Token": cfg["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        raise SystemExit("PiHub said %s: %s" % (e.code, msg or e.reason))
    except urllib.error.URLError as e:
        raise SystemExit("Can't reach PiHub at %s (%s). Same Wi-Fi as the Pi?" % (cfg["hub_url"], e.reason))


def main():
    cfg = load_config()
    device = cfg["device"]
    key = re.search(r"<apikey>([^<]+)</apikey>", open(st_config_file(), encoding="utf-8").read()).group(1)
    my_id = st("GET", "/rest/system/status", key)["myID"]
    fl = folders()
    print("This PC (%s) is %s" % (device, my_id))
    print("Folders to sync:", ", ".join(p for _, p in fl) or "none found")

    r = hub(cfg, "/api/pair", {"pc_id": my_id, "device": device, "folders": [n for n, _ in fl]})
    pi_id = r["pi_id"]
    print("Pi is", pi_id)

    st("PUT", "/rest/config/devices/" + pi_id, key,
       {"deviceID": pi_id, "name": "pi (picloud)", "addresses": [PI_ADDR, "dynamic"], "paused": True})
    for (fid, label), (_, path) in zip(r["folders"], fl):
        st("PUT", "/rest/config/folders/" + fid, key, {
            "id": fid, "label": label, "path": path, "type": "sendonly",
            "devices": [{"deviceID": my_id}, {"deviceID": pi_id}],
            "rescanIntervalS": 3600, "fsWatcherEnabled": True})

    cfg_file = config_path()
    raw = json.load(open(cfg_file, encoding="utf-8"))
    raw.setdefault("syncthing", {}).update({"url": ST, "api_key": key, "pi_device_id": pi_id})
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)
    # First backup right away instead of waiting for the next 4-day round. Asked for last, so the
    # folders above already exist when pihub-client picks the task up (within ~10 s).
    hub(cfg, "/api/sync-now", {"device": device})
    print("Done. First backup started: watch it on the Pi's Agents screen or the PiHub dashboard.")


if __name__ == "__main__":
    main()
