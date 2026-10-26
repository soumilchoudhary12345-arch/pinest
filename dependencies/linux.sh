#!/usr/bin/env bash
# Join this Linux PC to PiHub on the Raspberry Pi, in one run.
# PiHub serves this file with its address and token filled in:
#   curl -fsSL http://192.168.1.13:8899/join/linux.sh -o join-pi.sh && bash join-pi.sh
# The Pi names this PC <user>_linux (soumil_linux, then soumil_linux_2 for a duplicate); pass a name to pick your own.
# Safe to run again: it keeps an existing config and only adds what's missing.
set -euo pipefail

HUB="__PIHUB_URL__"
TOKEN="__PIHUB_TOKEN__"
DEVICE="${1:-}"
APP="$HOME/.local/share/pihub"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/pihub"
UNITS="$HOME/.config/systemd/user"

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run this as your normal user, not root. It asks for sudo when it needs it."
[ -z "$DEVICE" ] || [[ "$DEVICE" =~ ^[a-z0-9][a-z0-9_-]{0,39}$ ]] \
  || die "Bad device name '$DEVICE'. Use lowercase letters, digits, dashes and underscores, like: bash join-pi.sh my_laptop"

say "1/7 Checking the Pi at $HUB"
command -v curl >/dev/null || die "curl is missing. Install it, then run this again."
curl -fsS -m 5 -H "X-Token: $TOKEN" "$HUB/api/overview" >/dev/null \
  || die "Can't reach PiHub. Is this PC on the same Wi-Fi as the Pi?"

say "2/7 Installing python3 and syncthing if they're missing"
need=()
command -v python3 >/dev/null || need+=(python3)
command -v syncthing >/dev/null || need+=(syncthing)
if [ ${#need[@]} -gt 0 ]; then
  if command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm "${need[@]/python3/python}"
  elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y "${need[@]}"
  elif command -v dnf >/dev/null; then sudo dnf install -y "${need[@]}"
  elif command -v zypper >/dev/null; then sudo zypper install -y "${need[@]}"
  else die "Install ${need[*]} with your package manager, then run this again."
  fi
fi

say "3/7 Downloading the PiHub client"
mkdir -p "$APP" "$CFG" "$UNITS"
for f in pihub_client.py pihub_mcp.py report_event.py connect_agents.py pair_syncthing.py favicon.svg; do
  curl -fsS "$HUB/join/$f" -o "$APP/$f"
done
for f in pihub-client.service pihub-sync-fallback.service pihub-sync-fallback.timer; do
  curl -fsS "$HUB/join/$f" -o "$UNITS/$f"
done

say "4/7 Config"
if [ -f "$CFG/client.json" ]; then
  DEVICE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["device"])' "$CFG/client.json")"
  echo "Keeping the existing $CFG/client.json (device: $DEVICE)"
else
  if [ -z "$DEVICE" ]; then
    MACHINE="$(cat /etc/machine-id 2>/dev/null || cat /var/lib/dbus/machine-id)"
    DEVICE="$(curl -fsS -H "X-Token: $TOKEN" -H "Content-Type: application/json" \
      -d "{\"user\": \"${USER:-$(id -un)}\", \"os\": \"linux\", \"machine\": \"$MACHINE\"}" "$HUB/api/register" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["device"])')" || die "The Pi couldn't name this PC."
  fi
  ( umask 077
    PIHUB_TOKEN="$TOKEN" python3 - "$CFG/client.json" "$DEVICE" "$HUB" <<'PY'
import json, os, sys
path, device, hub = sys.argv[1:]
json.dump({
    "device": device, "hub_url": hub, "token": os.environ["PIHUB_TOKEN"],
    "allowed_roots": ["~/Documents", "~/Desktop", "~/Downloads", "~/Pictures", "~/Music", "~/Videos"],
    "deny": [".ssh", ".gnupg", ".env", "API Keys.md", "id_rsa", "id_ed25519", ".password-store", "Cookies"],
    "poll_seconds": 10,
    "syncthing": {"url": "http://127.0.0.1:8384", "api_key": "", "pi_device_id": ""},
}, open(path, "w"), indent=2)
PY
  )
  echo "Wrote $CFG/client.json (device: $DEVICE)"
fi

say "5/7 Starting Syncthing and pihub-client (and at every login)"
systemctl --user daemon-reload
systemctl --user enable --now syncthing.service pihub-client.service pihub-sync-fallback.timer
systemctl --user restart pihub-client.service
printf 'Waiting for Syncthing'
for _ in $(seq 60); do
  if curl -fs -m 2 http://127.0.0.1:8384/rest/noauth/health >/dev/null 2>&1; then break; fi
  printf '.'; sleep 1
done
echo

say "6/7 Pairing with the Pi"
python3 "$APP/pair_syncthing.py"

say "7/7 Agent monitor: connect every coding agent, agent tools and the 'Pi Agents' app"
python3 "$APP/connect_agents.py"
if command -v claude >/dev/null; then
  if claude mcp get pihub >/dev/null 2>&1; then echo "Claude Code PiHub tools: already there"
  else claude mcp add pihub -s user -- python3 "$APP/pihub_mcp.py" >/dev/null && echo "Claude Code PiHub tools: added"; fi
fi

BROWSER=""
for b in chromium google-chrome-stable google-chrome brave brave-browser microsoft-edge-stable; do
  if command -v "$b" >/dev/null; then BROWSER="$b"; break; fi
done
URL="$HUB/agents#token=$TOKEN"
if [ -n "$BROWSER" ]; then EXEC="$BROWSER \"--app=$URL\""; else EXEC="xdg-open \"$URL\""; fi
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons"
cp "$APP/favicon.svg" "$HOME/.local/share/icons/pi-agents.svg"
( umask 077
  cat > "$HOME/.local/share/applications/pi-agents.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pi Agents
GenericName=Agent monitor
Comment=See what your coding agents are doing, live, from the Pi
Exec=$EXEC
Icon=$HOME/.local/share/icons/pi-agents.svg
Terminal=false
Categories=Development;Monitor;
StartupWMClass=pi-agents
EOF
)
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Added 'Pi Agents' to your app launcher"

say "Done. This PC is '$DEVICE' on PiHub."
echo "  Agents:    open 'Pi Agents' from your launcher, or $HUB/agents"
echo "  Dashboard: $HUB  (this PC shows as online within a minute)"
