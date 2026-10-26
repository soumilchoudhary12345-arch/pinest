#!/usr/bin/env python3
"""PiHub MCP server: gives coding agents tools to find and move files between home PCs.

Same stdio JSON-RPC shape as the agent-comms bus. Reads the pihub-client config
(device name, hub URL, token), so it needs no settings of its own.
"""
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pihub_client import Hub, load_config  # noqa: E402

SERVER_NAME = "pihub"
SERVER_VERSION = "1.0.0"

TOOLS = [
    {
        "name": "list_devices",
        "description": "List the home PCs PiHub knows: whether each is online now, when it last synced, and how many files the Pi has indexed for it.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_files",
        "description": "Search the file inventory of every home PC by name or path fragment. Results come from the last sync, so very new files may be missing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Part of a file name or path, e.g. report.pdf"},
                "device": {"type": "string", "description": "Only search this PC (optional)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "request_file",
        "description": (
            "Copy a file from another home PC to a folder on this PC. If the other PC is on, its current "
            "file is sent; if it's off, the Pi's last synced copy is used and the note says how old it is. "
            "Paths are relative to that PC's home folder (e.g. Desktop/report.pdf) or start with ~. "
            "By default waits up to 90 seconds for the copy to finish."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "src_device": {"type": "string", "description": "PC that has the file, e.g. win-pc"},
                "src_path": {"type": "string", "description": "Path on that PC, e.g. Desktop/report.pdf"},
                "dst_path": {"type": "string", "description": "Folder or file path on this PC, e.g. ~/Downloads"},
                "dst_device": {"type": "string", "description": "Receiving PC (defaults to this one)"},
                "wait": {"type": "boolean", "default": True},
            },
            "required": ["src_device", "src_path", "dst_path"],
        },
    },
    {
        "name": "task_status",
        "description": "Check a file transfer or sync task by id.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "sync_now",
        "description": "Start a sync to the Pi right away instead of waiting for the next 4-day round. Omit device to sync every PC.",
        "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}}},
    },
]


class RequestError(Exception):
    pass


def call_tool(name, args):
    cfg = load_config()
    hub = Hub(cfg)
    args = args or {}
    try:
        if name == "list_devices":
            o = hub.call("GET", "/api/overview")
            return {"this_device": cfg["device"], "devices": [
                {k: d.get(k) for k in ("name", "online", "last_seen", "last_sync", "files")} for d in o["devices"]],
                "next_sync_round": o.get("next_round")}
        if name == "search_files":
            q = urllib.parse.urlencode({k: v for k, v in (("q", args.get("query")), ("device", args.get("device"))) if v})
            return hub.call("GET", "/api/search?" + q)
        if name == "request_file":
            t = hub.call("POST", "/api/tasks", {
                "src_device": args.get("src_device"), "src_path": args.get("src_path"),
                "dst_device": args.get("dst_device") or cfg["device"], "dst_path": args.get("dst_path")})
            if args.get("wait", True):
                deadline = time.time() + 90
                while t["status"] not in ("done", "failed") and time.time() < deadline:
                    time.sleep(3)
                    t = hub.call("GET", "/api/tasks/" + t["id"])
            return t
        if name == "task_status":
            return hub.call("GET", "/api/tasks/" + str(args.get("id", "")))
        if name == "sync_now":
            return hub.call("POST", "/api/sync-now", {"device": args.get("device")})
    except RuntimeError as e:
        raise RequestError(str(e))
    except OSError as e:
        raise RequestError("Can't reach PiHub at %s: %s" % (cfg["hub_url"], e))
    raise RequestError("unknown tool: %s" % name)


def send(msg):
    sys.stdout.write(json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(req):
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
        return send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid JSON-RPC request"}})
    method, rid = req.get("method"), req.get("id")
    if method == "notifications/initialized":
        return
    if method == "initialize":
        params = req.get("params") or {}
        return send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}})
    if method == "ping":
        return send({"jsonrpc": "2.0", "id": rid, "result": {}})
    if method == "tools/list":
        return send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    if method == "tools/call":
        params = req.get("params") or {}
        try:
            result = call_tool(params.get("name"), params.get("arguments"))
            out = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}
        except RequestError as e:
            out = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        return send({"jsonrpc": "2.0", "id": rid, "result": out})
    if rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found: %s" % method}})


def main():
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}})
            continue
        try:
            handle(req)
        except Exception as e:
            print("%s: %s" % (SERVER_NAME, e), file=sys.stderr, flush=True)
            send({"jsonrpc": "2.0", "id": req.get("id") if isinstance(req, dict) else None,
                  "error": {"code": -32603, "message": "internal error"}})


if __name__ == "__main__":
    main()
