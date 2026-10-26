# PiNest

A Raspberry Pi 5 homelab that every PC in the house plugs into: a NAS that mirrors each
laptop, a music streamer, a web file cloud, and a live monitor for every AI coding agent,
all on the home LAN with no cloud service and only Python's standard library on the server.

| Folder | What's in it | Runs on |
|---|---|---|
| [`dependencies/`](dependencies) | **Start here.** `join-pi.cmd` joins a PC to the Pi in one run (Linux and Windows) | each PC |
| [`hub/`](hub) | **PiHub**: coordinator API (:8899), device inventory, 4-day sync rounds, file fetch, dashboard | Pi |
| [`agents/`](agents) | Agent monitor: hooks for Claude Code, Codex, Copilot CLI, Crush, OpenCode, Gemini, Antigravity, plus the Pi's **Agents** touchscreen app | PCs + Pi |
| [`client/`](client) | pihub-client (sync + file transfer daemon), MCP tools for agents, Syncthing pairing | each PC |
| [`music/`](music) | **Musik**: music library with HTTP Range streaming (:8080) | Pi |
| [`cloud/`](cloud) | **picloud**: web file cloud (:80) | Pi |
| [`docs/`](docs) | Overview deck (`PiNest-overview.pptx`) and the script that builds it | — |

## How it fits together

```
 Laptops (Linux / Windows)                         Raspberry Pi 5  (Debian 13)
 ┌──────────────────────────┐  Syncthing :22000   ┌────────────────────────────────┐
 │ Syncthing (Send Only)    │ ──────────────────▶ │ Syncthing (Receive Only,       │
 │ pihub-client (polls)     │  REST + X-Token     │   Staggered versioning)        │
 │ agent hooks / plugin     │ ──────────────────▶ │ PiHub :8899  ·  SQLite (WAL)   │
 └──────────────────────────┘        :8899        │ Musik :8080  ·  picloud :80    │
                                                  │ /srv/nas on a USB SSD (ext4)   │
                                                  │ 3.5" touchscreen: Chromium     │
                                                  │   kiosk apps (Musik, picloud,  │
                                                  │   Agents)                      │
                                                  └────────────────────────────────┘
```

* **Pull-based:** the Pi never pushes to a PC. PCs poll `/api/tasks`, so firewalls and sleeping laptops don't matter.
* **One-way mirrors:** PC folders are Send Only, the Pi is Receive Only with 30-day versioning.
* **LAN-only:** the Pi's firewall (ufw) accepts private LAN ranges only; every API call needs the PiHub token.
* **Any network:** the Pi takes its address from the router (DHCP) and PCs find it as `piscreen.local`. To move it,
  plug in Ethernet (Wi-Fi: connect it once with `nmcli dev wifi connect <name> password <pw>`). Run
  `hub/setup/portable-network.sh` once with sudo to open the firewall to every private range.

## Join a PC

```sh
git clone https://github.com/soumilchoudhary12345-arch/pinest
# Linux
bash pinest/dependencies/join-pi.cmd
# Windows: double-click pinest\dependencies\join-pi.cmd
```

See [`dependencies/README.md`](dependencies/README.md) for what it does and how to point it at a Pi with another address.

## Tests

Standard-library `unittest`, no packages needed:

```sh
(cd hub && python3 -m unittest test_app.py)
(cd agents && python3 -m unittest test_report_event.py)
(cd music && python3 -m unittest test_app.py)
(cd cloud && python3 -m unittest test_app.py)
```
