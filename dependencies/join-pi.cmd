:; # Join this PC to PiHub on the Raspberry Pi. One file for both kinds of PC:
:; #   Windows: double-click join-pi.cmd (or run it from cmd / PowerShell)
:; #   Linux:   bash join-pi.cmd
:; # Optional device name: join-pi.cmd my-pc  /  bash join-pi.cmd my-laptop
:; # How it works: Windows reads lines starting with ":" as labels and skips them;
:; # bash runs them (":" does nothing, then the rest runs) and exits before the Windows part.
:; # The Pi is found by name (piscreen.local) on any network; the IP is a fallback. Set PIHUB_URL to override.
:; case "$(uname -s)" in Linux) ;; *) echo "This PC runs $(uname -s); PiHub's join script supports Linux and Windows." >&2; exit 1 ;; esac
:; f="$(mktemp)"; trap 'rm -f "$f"' EXIT
:; get() { if command -v curl >/dev/null; then curl -fsSL -m 8 "$1" -o "$f"; else wget -qO "$f" -T 8 "$1"; fi; }
:; HUB=""; for h in ${PIHUB_URL:-} http://piscreen.local:8899 http://192.168.1.13:8899; do if get "$h/join/linux.sh" 2>/dev/null; then HUB="$h"; break; fi; done
:; [ -n "$HUB" ] || { echo "Can't find the Pi (tried piscreen.local and 192.168.1.13). Is it on the same Wi-Fi as this PC? If it just moved to this network, run once on the Pi: sudo bash ~/pihub/setup/portable-network.sh" >&2; exit 1; }
:; bash "$f" "$@"; exit $?
@echo off
if not "%~1"=="" set "PIHUB_DEVICE=%~1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u = @($env:PIHUB_URL, 'http://piscreen.local:8899', 'http://192.168.1.13:8899') | Where-Object { $_ }; foreach ($h in $u) { try { $s = irm ($h + '/join/windows.ps1') -TimeoutSec 8; break } catch {} }; if (-not $s) { Write-Host 'Cannot find the Pi (tried piscreen.local and 192.168.1.13). Is it on the same Wi-Fi as this PC? If it just moved to this network, run once on the Pi: sudo bash ~/pihub/setup/portable-network.sh' -ForegroundColor Red } else { iex $s }"
echo.
pause
