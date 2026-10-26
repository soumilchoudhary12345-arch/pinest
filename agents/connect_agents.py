#!/usr/bin/env python3
"""Connect every coding agent on this PC to PiHub's agent monitor.

Finds Claude Code, Codex, Copilot CLI, Crush, OpenCode, Gemini CLI and
Antigravity CLI, and adds a hook (a plugin for OpenCode) that runs
report_event.py on their events. Merges into existing configs, never removes
anything, and is safe to run again. Run it after installing a new agent:

    python3 connect_agents.py            (Linux)
    py connect_agents.py                 (Windows)
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "report_event.py")
HOME = os.path.expanduser("~")
WIN = os.name == "nt"
MARK = "report_event.py"   # how an existing PiHub hook is recognised


def q(path):
    return '"%s"' % path if (" " in path or WIN) else path


# Windows uses the full interpreter path; Linux uses python3 from PATH like the existing hooks.
CMD = "%s %s" % (q(sys.executable) if WIN else "python3", q(REPORT))


def has(binary, *dirs):
    return bool(shutil.which(binary)) or any(os.path.isdir(os.path.join(HOME, d)) for d in dirs)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError:
        raise SystemExit("  skipped: %s isn't valid JSON, so I won't touch it" % path)


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".pihub-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def wired(groups):
    """True if a hook list already runs report_event.py (any of the agents' shapes)."""
    for g in groups or []:
        hooks = g.get("hooks") if isinstance(g.get("hooks"), list) else [g]
        for h in hooks:
            if any(MARK in str(h.get(k, "")) for k in ("command", "bash", "powershell")):
                return True
    return False


def add_nested(data, events, cmd, timeout, matcher_events=(), name=None):
    """Claude Code / Codex / Gemini shape: {Event: [{matcher?, hooks: [{type, command, timeout}]}]}."""
    hooks, added = data.setdefault("hooks", {}), 0
    for ev in events:
        groups = hooks.setdefault(ev, [])
        if wired(groups):
            continue
        h = {"type": "command", "command": cmd, "timeout": timeout}
        if name:
            h["name"] = name
        g = {"hooks": [h]}
        if ev in matcher_events:
            g = {"matcher": matcher_events[ev], **g}
        groups.append(g)
        added += 1
    return added


# ---------- one function per agent: returns a short status line ----------

def claude():
    p = os.path.join(HOME, ".claude", "settings.json")
    d = load(p)
    n = add_nested(d, ("SessionStart", "PreToolUse", "Notification", "Stop"), CMD + " --agent claude-code", 5,
                   {"PreToolUse": "*"})
    save(p, d)
    return "connected" if n else "already connected"


def codex():
    p = os.path.join(HOME, ".codex", "hooks.json")
    d = load(p)
    n = add_nested(d, ("SessionStart", "PreToolUse", "PermissionRequest", "Stop"), CMD + " --agent codex", 5)
    save(p, d)
    return ("connected" if n else "already connected") + " (approve it once inside Codex with /hooks)"


def copilot():
    # Copilot CLI loads every ~/.copilot/hooks/*.json; this file is ours alone.
    p = os.path.join(HOME, ".copilot", "hooks", "pihub.json")
    events = ("sessionStart", "preToolUse", "permissionRequest", "notification", "agentStop", "sessionEnd")
    hooks = {}
    for ev in events:
        c = "%s --agent copilot --event %s" % (CMD, ev)
        hooks[ev] = [{"type": "command", "bash": c, "powershell": ("& " + c) if WIN else c, "timeoutSec": 5}]
    existed = os.path.exists(p)
    save(p, {"version": 1, "hooks": hooks})
    return "updated" if existed else "connected"


def crush_config_path():
    try:
        out = subprocess.run(["crush", "dirs"], capture_output=True, text=True, timeout=15).stdout.split()
        if out:
            return os.path.join(out[0], "crush.json")
    except (OSError, subprocess.SubprocessError):
        pass
    base = os.environ.get("LOCALAPPDATA") if WIN else os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config"))
    return os.path.join(base, "crush", "crush.json")


def crush():
    # Crush 0.97 has one hook event, PreToolUse; PiHub marks it quiet after a short pause instead of a Stop.
    p = crush_config_path()
    d = load(p)
    groups = d.setdefault("hooks", {}).setdefault("PreToolUse", [])
    if wired(groups):
        return "already connected"
    groups.append({"command": CMD + " --agent crush", "timeout": 5})
    save(p, d)
    return "connected"


def gemini_family():
    # Gemini CLI and Antigravity CLI share ~/.gemini/settings.json (timeouts in ms);
    # report_event tells them apart from the process tree.
    p = os.path.join(HOME, ".gemini", "settings.json")
    d = load(p)
    n = add_nested(d, ("SessionStart", "BeforeTool", "Notification", "AfterAgent", "SessionEnd"),
                   CMD + " --agent gemini-family", 5000, {"BeforeTool": ".*"}, name="pihub-monitor")
    save(p, d)
    return "connected" if n else "already connected"


OPENCODE_PLUGIN = r'''// PiHub agent monitor for OpenCode, written by connect_agents.py.
// Forwards tool calls and session events to report_event.py, like the other agents' hooks.
import { spawn } from "node:child_process"

const REPORT = __ARGV__

function report(payload) {
  try {
    const p = spawn(REPORT[0], [...REPORT.slice(1), "--agent", "opencode"], { stdio: ["pipe", "ignore", "ignore"] })
    p.on("error", () => {})
    p.stdin.on("error", () => {})
    p.stdin.end(JSON.stringify(payload))
  } catch {}
}

const EVENTS = {
  "session.created": "SessionStart",
  "session.idle": "Stop",
  "permission.asked": "Notification",
  "permission.updated": "Notification",
}

export const PiHubMonitor = async ({ directory }) => ({
  "tool.execute.before": async (input, output) => {
    report({ hook_event_name: "PreToolUse", session_id: input.sessionID, cwd: directory,
             tool_name: input.tool, tool_input: output.args })
  },
  event: async ({ event }) => {
    const ev = EVENTS[event.type]
    if (!ev) return
    const p = event.properties || {}
    report({ hook_event_name: ev, session_id: p.sessionID || (p.info && p.info.id) || "", cwd: directory,
             message: ev === "Notification" ? "Asking permission: " + (p.title || p.type || "a tool") : undefined })
  },
})
'''


def opencode():
    p = os.path.join(HOME, ".config", "opencode", "plugins", "pihub-monitor.js")
    argv = [sys.executable if WIN else "python3", REPORT]
    text = OPENCODE_PLUGIN.replace("__ARGV__", json.dumps(argv))
    try:
        with open(p, encoding="utf-8") as f:
            if f.read() == text:
                return "already connected"
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return "connected"


AGENTS = [
    ("Claude Code", lambda: has("claude", ".claude"), claude),
    ("Codex", lambda: has("codex", ".codex"), codex),
    ("Copilot CLI", lambda: has("copilot", ".copilot"), copilot),
    ("Crush", lambda: has("crush"), crush),
    ("OpenCode", lambda: has("opencode", ".config/opencode"), opencode),
    ("Gemini CLI + Antigravity", lambda: has("gemini", ".gemini") or has("agy") or has("antigravity"), gemini_family),
]


def main():
    if not os.path.isfile(REPORT):
        raise SystemExit("report_event.py must sit next to this script (%s)" % HERE)
    print("Connecting coding agents to PiHub's monitor:")
    for name, present, connect in AGENTS:
        if not present():
            print("  %-26s not installed" % name)
            continue
        try:
            print("  %-26s %s" % (name, connect()))
        except SystemExit as e:
            print("  %-26s %s" % (name, str(e).strip()))
        except OSError as e:
            print("  %-26s failed: %s" % (name, e))


if __name__ == "__main__":
    main()
