# client: what each PC runs

| File | What it does |
|---|---|
| `pihub_client.py` | Daemon that polls PiHub's `/api/tasks` every 10 s: runs sync rounds (resume Syncthing, wait for 100 %, pause), uploads requested files, saves fetched files. Only reads and writes inside `allowed_roots`; `.ssh`, `.gnupg`, `.env` and key files are always refused. |
| `pihub_mcp.py` | MCP server for coding agents: `list_devices`, `search_files`, `request_file`, `task_status`, `sync_now`. |
| `pair_syncthing.py` | Pairs this PC's Syncthing with the Pi through PiHub's `/api/pair`: Send Only here, Receive Only + versioning on the Pi. |
| `linux/*.service`, `linux/*.timer` | systemd user units: the client at login, and a 4-day fallback sync with `Persistent=true`. |

Config: `~/.config/pihub/client.json` (Windows: `%APPDATA%\pihub\client.json`). The join script in
`dependencies/` installs all of this; you don't need to copy files by hand.
