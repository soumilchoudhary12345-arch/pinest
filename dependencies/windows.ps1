# Join this Windows PC to PiHub on the Raspberry Pi, in one run.
# PiHub serves this file with its address and token filled in. In PowerShell:
#   irm http://192.168.1.13:8899/join/windows.ps1 | iex
# or save it and run:  powershell -ExecutionPolicy Bypass -File windows.ps1
# The Pi names this PC <user>_win (soumil_win, then soumil_win_2 for a duplicate).
# To pick your own name, first run:  $env:PIHUB_DEVICE = "my_pc"
# Safe to run again: it keeps an existing config and only adds what's missing.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Hub = "__PIHUB_URL__"                    # the Pi by name, so a new IP or network doesn't matter
$HubFallback = "__PIHUB_FALLBACK_URL__"   # the address this PC fetched the script from
$Token = "__PIHUB_TOKEN__"
$Headers = @{ "X-Token" = $Token }
$App = Join-Path $env:LOCALAPPDATA "pihub"
$CfgDir = Join-Path $env:APPDATA "pihub"
$CfgFile = Join-Path $CfgDir "client.json"

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Yellow }
function Die($msg) { Write-Host "Error: $msg" -ForegroundColor Red; throw $msg }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
              (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links")
}
function Find-Python {
  # "py" is the real launcher; a bare "python" may be the Microsoft Store stub.
  foreach ($cmd in @("py", "python")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
      $args3 = @(); if ($cmd -eq "py") { $args3 = @("-3") }
      $exe = & $cmd @args3 -c "import sys; print(sys.executable)" 2>$null
      if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { return $exe.Trim() }
    }
  }
  return $null
}

Say "1/7 Checking the Pi at $Hub"
try { Invoke-RestMethod "$Hub/api/overview" -Headers $Headers -TimeoutSec 5 | Out-Null }
catch {
  Write-Host "Can't resolve $Hub; using $HubFallback"
  $Hub = $HubFallback
  try { Invoke-RestMethod "$Hub/api/overview" -Headers $Headers -TimeoutSec 5 | Out-Null }
  catch { Die "Can't reach PiHub. Is this PC on the same network as the Pi?" }
}

Say "2/7 Installing Python and Syncthing if they're missing"
$hasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
$py = Find-Python
if (-not $py) {
  if (-not $hasWinget) { Die "Install Python 3 from python.org (tick 'Add python.exe to PATH'), then run this again." }
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
  Refresh-Path
  $py = Find-Python
  if (-not $py) { Die "Python installed, but this window can't see it yet. Open a new PowerShell and run this again." }
}
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
Write-Host "Python: $py"

Refresh-Path
$st = (Get-Command syncthing -ErrorAction SilentlyContinue).Source
if (-not $st) {
  if (-not $hasWinget) { Die "Install Syncthing from syncthing.net/downloads, then run this again." }
  winget install -e --id Syncthing.Syncthing --silent --accept-package-agreements --accept-source-agreements
  Refresh-Path
  $st = (Get-Command syncthing -ErrorAction SilentlyContinue).Source
  if (-not $st) {
    $st = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages") -Recurse -Filter syncthing.exe -ErrorAction SilentlyContinue |
          Select-Object -First 1 -ExpandProperty FullName
  }
  if (-not $st) { Die "Syncthing installed, but I can't find syncthing.exe. Open a new PowerShell and run this again." }
}
Write-Host "Syncthing: $st"

Say "3/7 Downloading the PiHub client"
New-Item -ItemType Directory -Force -Path $App, $CfgDir | Out-Null
foreach ($f in @("pihub_client.py", "pihub_mcp.py", "report_event.py", "connect_agents.py", "pair_syncthing.py")) {
  Invoke-WebRequest "$Hub/join/$f" -OutFile (Join-Path $App $f) -UseBasicParsing
}

Say "4/7 Config"
if (Test-Path $CfgFile) {
  $Device = (Get-Content $CfgFile -Raw | ConvertFrom-Json).device
  Write-Host "Keeping the existing $CfgFile (device: $Device)"
} else {
  $Device = $env:PIHUB_DEVICE
  if ($Device) {
    if ($Device -cnotmatch '^[a-z0-9][a-z0-9_-]{0,39}$') { Die "Bad device name '$Device'. Use lowercase letters, digits, dashes and underscores." }
  } else {
    $machine = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Cryptography").MachineGuid
    $body = @{ user = $env:USERNAME; os = "win"; machine = $machine } | ConvertTo-Json
    $Device = (Invoke-RestMethod "$Hub/api/register" -Method Post -Headers $Headers -ContentType "application/json" -Body $body).device
  }
  $cfg = [ordered]@{
    device = $Device; hub_url = $Hub; token = $Token
    allowed_roots = @("~/Documents", "~/Desktop", "~/Downloads", "~/Pictures", "~/Music", "~/Videos")
    deny = @(".ssh", ".gnupg", ".env", "API Keys.md", "id_rsa", "id_ed25519", ".password-store", "Cookies")
    poll_seconds = 10
    syncthing = [ordered]@{ url = "http://127.0.0.1:8384"; api_key = ""; pi_device_id = "" }
  }
  # UTF-8 without a BOM, so Python's json module can read it.
  [IO.File]::WriteAllText($CfgFile, ($cfg | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
  Write-Host "Wrote $CfgFile (device: $Device)"
}

Say "5/7 Starting Syncthing and pihub-client (and at every logon)"
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$keep = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$stAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -Command `"& '$st' serve --no-browser`""
Register-ScheduledTask -Force -TaskName "pihub-syncthing" -Trigger $logon -Settings $keep -Action $stAction | Out-Null
$client = Join-Path $App "pihub_client.py"
Register-ScheduledTask -Force -TaskName "pihub-client" -Trigger $logon -Settings $keep `
  -Action (New-ScheduledTaskAction -Execute $pyw -Argument "`"$client`"") | Out-Null
$every4 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(3) -RepetitionInterval (New-TimeSpan -Days 4)
$fallback = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries
Register-ScheduledTask -Force -TaskName "pihub-sync-fallback" -Trigger $every4 -Settings $fallback `
  -Action (New-ScheduledTaskAction -Execute $pyw -Argument "`"$client`" --sync-now") | Out-Null

if (-not (Get-Process syncthing -ErrorAction SilentlyContinue)) { Start-ScheduledTask -TaskName "pihub-syncthing" }
Stop-ScheduledTask -TaskName "pihub-client" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "pihub-client"
Write-Host -NoNewline "Waiting for Syncthing"
$up = $false
for ($i = 0; $i -lt 60 -and -not $up; $i++) {
  try { Invoke-RestMethod "http://127.0.0.1:8384/rest/noauth/health" -TimeoutSec 2 | Out-Null; $up = $true }
  catch { Write-Host -NoNewline "."; Start-Sleep -Seconds 1 }
}
Write-Host ""
if (-not $up) { Die "Syncthing didn't start. Run '$st serve' once to see why, then run this again." }

Say "6/7 Pairing with the Pi"
& $py (Join-Path $App "pair_syncthing.py")
if ($LASTEXITCODE -ne 0) { Die "Pairing failed (see above)." }

Say "7/7 Agent monitor: connect every coding agent, agent tools and the 'Pi Agents' app"
& $py (Join-Path $App "connect_agents.py")

if (Get-Command claude -ErrorAction SilentlyContinue) {
  claude mcp get pihub *> $null
  if ($LASTEXITCODE -eq 0) { Write-Host "Claude Code PiHub tools: already there" }
  else { claude mcp add pihub -s user -- $py (Join-Path $App "pihub_mcp.py") | Out-Null; Write-Host "Claude Code PiHub tools: added" }
}

$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
$url = "$Hub/agents#token=$Token"
$shell = New-Object -ComObject WScript.Shell
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), $startMenu)) {
  $lnk = $shell.CreateShortcut((Join-Path $dir "Pi Agents.lnk"))
  if ($edge) { $lnk.TargetPath = $edge; $lnk.Arguments = "--app=$url" }
  else { $lnk.TargetPath = $url }
  $lnk.Description = "See what your coding agents are doing, live, from the Pi"
  $lnk.Save()
}
Write-Host "Added 'Pi Agents' to the desktop and Start menu"

Say "Done. This PC is '$Device' on PiHub."
Write-Host "  Agents:    open 'Pi Agents', or $Hub/agents"
Write-Host "  Dashboard: $Hub  (this PC shows as online within a minute)"
