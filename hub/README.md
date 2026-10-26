# PiHub

The homelab's coordinator on the Raspberry Pi (`piscreen`, 192.168.1.13, port **8899**).

| Piece | Where it runs | What it does |
|---|---|---|
| `app.py` + `index.html`/`app.js`/`style.css` | Pi, `pihub.service` | File inventory, 4-day sync rounds, file fetch relay, agent monitor, dashboard |
| `deploy/pihub-sync.timer` | Pi | Daily 02:00 check; queues a sync for every PC once 4 days have passed |
| `setup/move-nas-to-ssd.sh` | Pi, once | Formats the USB SSD, mounts it at `/srv/nas` by UUID with `nofail` |
| `../client/`, `../agents/`, `../dependencies/` | PCs | Served by PiHub at `/join/…` so a new PC can install them (see the main README) |

Storage: `/srv/nas` (`devices/<pc>/…` synced copies, `music/`, `inbox/` in-transit files, `pihub.db`).
Token: `PIHUB_TOKEN` in `/etc/pihub.env` (root:som 640). Every `/api/*` call needs `X-Token`.

## Deploy to the Pi

Copy the repo to the Pi and point `deploy/pihub.service` at `hub/app.py` (it finds `agents/`, `client/`
and `dependencies/` next to `hub/`). It also runs from a flat copy with everything in one folder.

```sh
rsync -a --exclude __pycache__ ./ som@piscreen.local:/home/som/pinest/
ssh som@piscreen.local 'sudo systemctl restart pihub'
```

## Agents app on the Pi's screen

`../agents/agents.desktop` opens `/pi#token=…` in a Chromium kiosk. Install it on the Pi with the token
filled in from `/etc/pihub.env` (never commit the token), plus `../agents/agents-icon.png` as
`~/.local/share/icons/agents.png`. Storage numbers come live from the Pi's Syncthing, so a new PC
and its upload show within seconds.

## Add a PC

Run `dependencies/join-pi.cmd` on the PC (see `../dependencies/README.md`).

## Safety rules

* Each PC only shares folders in its `allowed_roots`; `.ssh`, `.gnupg`, `.env`, `API Keys.md` and private keys are always refused.
* The Pi keeps copies (Receive Only + Staggered versioning); deleting on a PC never destroys the Pi's copy.
* The Pi's firewall (ufw) only accepts connections from 192.168.1.0/24 and IPv6 link-local.

## Tests

`python3 -m unittest test_app.py` (from this folder)
