# dependencies: join a PC to the Pi

| File | What it is |
|---|---|
| `join-pi.cmd` | **Run this.** One file for both systems: Windows runs it as a batch file, Linux runs it with `bash`. It downloads the right installer from the Pi and runs it. |
| `linux.sh` | The Linux installer. The Pi serves it at `/join/linux.sh` with its address and token filled in. |
| `windows.ps1` | The Windows installer, served at `/join/windows.ps1` the same way. |

## Use

* **Windows:** double-click `join-pi.cmd`.
* **Linux:** `bash join-pi.cmd`

Optional device name: `join-pi.cmd my_pc` / `bash join-pi.cmd my_laptop`. Without one, the Pi
names the PC `<user>_<os>` (for example `soumil_linux`, `soumil_win`), adding a number only for a
duplicate (`soumil_win_2`). The name is tied to the machine ID, so running it again keeps it.

**Any network:** the Pi is found by name, `piscreen.local` (mDNS), so it keeps working when the Pi
moves to another router or gets a new IP. `join-pi.cmd` tries `PIHUB_URL` (if you set one), then
`piscreen.local`, then `192.168.1.13`. Override example: `PIHUB_URL=http://10.0.0.5:8899 bash join-pi.cmd`.

## What the installer does

1. Checks the Pi answers on `:8899`.
2. Installs Python 3 and Syncthing if missing (pacman / apt / dnf / zypper, or winget).
3. Downloads the client files from the Pi and writes `client.json` (device name, hub URL, token).
4. Starts Syncthing and pihub-client now and at every login (systemd user units / Task Scheduler), plus a 4-day fallback sync.
5. Pairs Syncthing with the Pi through PiHub's `/api/pair` (no SSH login) and **starts the first backup at once** (Documents, Desktop, Downloads, Pictures, Videos); watch it live on the Pi's Agents screen.
6. Connects every installed coding agent to the monitor (`agents/connect_agents.py`).
7. Adds a **Pi Agents** app that opens the live agent monitor.

Safe to run again: it keeps the existing config and only adds what's missing.

**How `join-pi.cmd` works on both systems:** lines starting with `:;` are labels to Windows, so cmd skips
them; bash treats `:` as a no-op and runs the rest of the line, then exits before the Windows part.
Keep the file's line endings LF.
