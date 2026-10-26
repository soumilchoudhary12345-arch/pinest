# agents: what every coding agent is doing, live

| File | Runs on | What it does |
|---|---|---|
| `connect_agents.py` | each PC | Finds installed agents and adds a hook (OpenCode: a plugin) that calls `report_event.py`. Merges into existing configs; safe to rerun after installing a new agent. |
| `report_event.py` | each PC | Called by the hooks. Normalises each agent's event, field and tool names, masks secrets, and posts a short event to PiHub with a 1-second timeout. Prints nothing and always exits 0, so it can't slow an agent down. |
| `pi.html`, `pi.js`, `pi.css` | Pi (served by PiHub at `/pi`) | The **Agents** touchscreen app (480×320, tap-only): pick a PC, see each agent's state and a plain-language line of what it's doing. Refreshes every 2 s. |
| `agents.desktop`, `agents-icon.png` | Pi | Desktop launcher (Chromium kiosk). Fill in `__PIHUB_TOKEN__` from `/etc/pihub.env` when installing; never commit the token. |

| Agent | Where the hook goes | Events |
|---|---|---|
| Claude Code | `~/.claude/settings.json` | SessionStart, PreToolUse, Notification, Stop |
| Codex | `~/.codex/hooks.json` (approve once with `/hooks`) | SessionStart, PreToolUse, PermissionRequest, Stop |
| Copilot CLI | `~/.copilot/hooks/pihub.json` | sessionStart, preToolUse, permissionRequest, notification, agentStop, sessionEnd |
| Crush | `crush.json` | PreToolUse (marked quiet after 2 minutes, since 0.97 has no stop event) |
| OpenCode | `~/.config/opencode/plugins/pihub-monitor.js` | tool.execute.before, session.created, session.idle, permission.asked |
| Gemini CLI + Antigravity | `~/.gemini/settings.json` (shared) | SessionStart, BeforeTool, Notification, AfterAgent, SessionEnd; the parent process tells them apart |

The Pi turns each event into a sentence in `hub/app.py` (`describe()`), for example "Editing app.py",
"Asking permission to use Bash", or "Done. Last step: writing pi.js".
