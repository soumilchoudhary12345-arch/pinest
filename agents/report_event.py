#!/usr/bin/env python3
"""Coding-agent hook -> PiHub agent monitor.

Reads the hook's JSON from stdin, keeps only the event name, tool name and a
short summary (never file contents or prompts), and posts it to PiHub with a
1-second timeout. Always exits 0 and prints nothing, so it can't slow down or
block the agent.

  report_event.py --agent claude-code                    (Claude Code, Codex, Crush)
  report_event.py --agent copilot --event preToolUse     (Copilot CLI: its payload has no event name)
  report_event.py --agent gemini-family                  (Gemini CLI and Antigravity share one config;
                                                          the parent process tells them apart)

Every agent names events, fields and tools differently; they're normalised here
to Claude Code's names, which PiHub's monitor understands.
"""
import json
import os
import re
import sys
import urllib.request

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "client")]  # installed next to pihub_client.py, or in the pinest repo

# Commands can carry secrets (tokens in headers, passwords piped to sudo...).
# Mask them before anything leaves the laptop.
_SECRET_PATTERNS = [
    re.compile(r"(?i)((?:x-token|x-api-key|authorization|api[_-]?key|token|password|passwd|secret|bearer)[\"']?\s*[:=]?\s*[\"']?"
               r"(?:(?:bearer|basic)\s+)?)([^\s\"']+)"),
    re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"),   # long random-looking strings (keys, tokens)
]


def redact(text):
    text = _SECRET_PATTERNS[0].sub(lambda m: m.group(1) + "•••", text)
    return _SECRET_PATTERNS[1].sub("•••", text)


# Event names -> PiHub's (Claude Code's) names.
EVENTS = {
    "pretooluse": "PreToolUse", "beforetool": "PreToolUse", "pre_tool_use": "PreToolUse",
    "notification": "Notification", "permissionrequest": "Notification",
    "stop": "Stop", "agentstop": "Stop", "afteragent": "Stop", "session.idle": "Stop",
    "sessionstart": "SessionStart", "session.created": "SessionStart",
    "sessionend": "SessionEnd",
}
# Tool names across Claude Code, Codex, Copilot CLI, Crush, OpenCode, Gemini and Antigravity.
TOOLS = {
    "bash": "Bash", "shell": "Bash", "run_shell_command": "Bash", "powershell": "Bash", "exec_command": "Bash",
    "read": "Read", "view": "Read", "read_file": "Read", "read_many_files": "Read",
    "edit": "Edit", "multiedit": "Edit", "replace": "Edit", "str_replace": "Edit", "apply_patch": "Edit",
    "write": "Write", "create": "Write", "write_file": "Write",
    "grep": "Grep", "search_file_content": "Grep", "grep_search": "Grep", "rg": "Grep",
    "glob": "Glob", "ls": "Glob", "list_directory": "Glob",
    "webfetch": "WebFetch", "web_fetch": "WebFetch", "fetch": "WebFetch", "agentic_fetch": "WebFetch",
    "websearch": "WebSearch", "web_search": "WebSearch", "google_web_search": "WebSearch",
    "task": "Agent", "agent": "Agent", "todowrite": "TodoWrite", "todo": "TodoWrite", "write_todos": "TodoWrite",
}
_PATCH_FILE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: (\S+)")


def norm_tool(name):
    if not name:
        return None
    return TOOLS.get(name.lower(), name)


def tool_input(data):
    """tool_input / toolArgs / toolInput; Copilot sends its args as a JSON string."""
    ti = data.get("tool_input", data.get("toolArgs", data.get("toolInput")))
    if isinstance(ti, str):
        try:
            ti = json.loads(ti)
        except ValueError:
            ti = {"command": ti}
    if isinstance(ti, dict):
        ti = dict(ti)
        for alias in ("filePath", "absolute_path", "file"):  # OpenCode, Gemini, others
            if alias in ti and "file_path" not in ti:
                ti["file_path"] = ti[alias]
        patch = ti.get("patch") or ti.get("input") or ""
        if isinstance(patch, str) and _PATCH_FILE.search(patch):  # Codex apply_patch
            ti = {"file_path": _PATCH_FILE.search(patch).group(1)}
    return ti


def gemini_or_antigravity():
    """Both read ~/.gemini/settings.json, so look up the process tree for the program that ran us.

    Only program names count (agy / antigravity, or a gemini script run by node),
    never the rest of a command line, which can contain any text.
    """
    pid = os.getppid()
    for _ in range(10):
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
            with open("/proc/%d/stat" % pid) as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
        names = [os.path.basename(a).lower() for a in argv[:2]]
        if any(n in ("agy", "antigravity") for n in names):  # the binary, or a script/wrapper running it
            return "antigravity"
        if "gemini" in names or (names and names[0] in ("node", "bun") and any("gemini" in n for n in names[1:])):
            return "gemini"
        if ppid <= 1:
            break
        pid = ppid
    return "gemini"


def summarize(tool, ti):
    if not isinstance(ti, dict):
        return None
    # Bash and helper-agent calls carry a plain-English "description"; prefer it over the raw command.
    keys = ("description", "command") if tool in ("Bash", "Agent", "Task") else \
        ("command", "file_path", "path", "pattern", "url", "query", "description", "skill")
    for key in keys:
        v = ti.get(key)
        if isinstance(v, str) and v:
            v = " ".join(v.split())
            if key == "file_path" or key == "path":
                v = os.path.basename(v.rstrip("/")) or v
            return redact(v)[:120]
    return None


def main():
    try:
        arg = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
        agent = arg("--agent", "agent")
        if agent == "gemini-family":
            agent = gemini_or_antigravity()
        data = json.loads(sys.stdin.read() or "{}")
        from pihub_client import load_config
        cfg = load_config()
        raw_event = arg("--event") or data.get("hook_event_name") or data.get("event") or ""
        event = EVENTS.get(raw_event.lower(), raw_event)
        tool = norm_tool(data.get("tool_name") or data.get("toolName"))
        summary = summarize(tool, tool_input(data))
        if event == "Notification":
            if raw_event.lower() == "permissionrequest":
                msg = "Asking permission to use " + (tool or "a tool")
            else:
                msg = data.get("message") or "Needs your attention"
            summary = redact(msg)[:120]
        session = data.get("session_id") or data.get("sessionId") or data.get("sessionID") or ""
        body = {"device": cfg["device"], "agent": agent, "session": str(session)[:80],
                "event": event, "tool": tool, "summary": summary,
                "cwd": os.path.basename((data.get("cwd") or "").rstrip("/"))}
        req = urllib.request.Request(cfg["hub_url"] + "/api/agent-events", data=json.dumps(body).encode(),
                                     method="POST", headers={"X-Token": cfg["token"], "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=1).read()
    except Exception:
        pass  # monitoring must never get in the agent's way


if __name__ == "__main__":
    main()
    sys.exit(0)
