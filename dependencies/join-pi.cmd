:; # Join this PC to PiHub on the Raspberry Pi. One file for both kinds of PC:
:; #   Windows: double-click join-pi.cmd (or run it from cmd / PowerShell)
:; #   Linux:   bash join-pi.cmd
:; # Optional device name: join-pi.cmd my-pc  /  bash join-pi.cmd my-laptop
:; # How it works: Windows reads lines starting with ":" as labels and skips them;
:; # bash runs them (":" does nothing, then the rest runs) and exits before the Windows part.
:; HUB="${PIHUB_URL:-http://192.168.1.13:8899}"   # the Pi; set PIHUB_URL if it has another address
:; case "$(uname -s)" in Linux) ;; *) echo "This PC runs $(uname -s); PiHub's join script supports Linux and Windows." >&2; exit 1 ;; esac
:; f="$(mktemp)"; trap 'rm -f "$f"' EXIT
:; if command -v curl >/dev/null; then curl -fsSL "$HUB/join/linux.sh" -o "$f"; else wget -qO "$f" "$HUB/join/linux.sh"; fi || { echo "Can't reach the Pi at $HUB. Same Wi-Fi?" >&2; exit 1; }
:; bash "$f" "$@"; exit $?
@echo off
if not "%~1"=="" set "PIHUB_DEVICE=%~1"
if "%PIHUB_URL%"=="" set "PIHUB_URL=http://192.168.1.13:8899"
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm '%PIHUB_URL%/join/windows.ps1' | iex"
echo.
pause
