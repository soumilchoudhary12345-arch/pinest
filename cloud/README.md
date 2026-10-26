# picloud: the web file cloud

Single-file stdlib Python server (`app.py`, port 80 on the Pi) serving `CLOUD_ROOT` (on the Pi: `/srv/nas`).
Browse, upload files or whole folders with progress, create folders, rename, delete, and download with
HTTP Range support. Every path is resolved and checked against the root, so `../` can't escape it.
`?pi=1` switches to a browse-and-view-only layout for the Pi's touchscreen (photo viewer, no deleting).

Run: `CLOUD_ROOT=~/cloud python3 app.py` (add `CLOUD_PORT=8000` to avoid needing root for port 80) · Deploy: `picloud.service`, `picloud.desktop`.
