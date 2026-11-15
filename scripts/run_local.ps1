# WiFi-Doctor - one-click Windows launcher (PowerShell 5+)
#
#   Double-click scripts\run_local.bat, or:
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1
#
# Field-hardened on a real Windows 11 laptop:
#   * repairs a broken PATH for this session (System32 / PowerShell missing)
#   * installs missing Python / Node.js via winget
#   * stops only its OWN previous instance (never Docker or other apps)
#   * moves to free ports when 8000 / 5173 are taken by another program
#   * keeps the backend attached to this window: closing it stops everything
# Run from an Administrator shell with Npcap installed to enable raw-socket
# (scapy) probing; otherwise the built-in ping.exe fallback is used.
$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Base = $Repo
$Log  = Join-Path $Base "wifi-doctor-launch.log"
try { Start-Transcript -Path $Log -Force -ErrorAction Stop | Out-Null }
catch { $Log = Join-Path $Base ("wifi-doctor-setup-" + (Get-Date -Format "HHmmss") + ".log"); Start-Transcript -Path $Log -Force | Out-Null }

# A previous run may still be alive (window closed with X leaves child processes).
# Free the ports so the new version really starts instead of silently reusing the old one.
function Stop-PortOwners([int[]]$Ports, [string]$RepoPath) {
    # SAFETY: only ever stop OUR OWN processes (python/node started from this repo).
    # Anything else on these ports (Docker Desktop, another app) is left alone and reported.
    $blocked = @()
    foreach ($port in $Ports) {
        $pids = @()
        try { $pids = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique }
        catch {
            $pids = netstat -ano | Select-String (":$port\s+.*LISTENING\s+(\d+)") | ForEach-Object { [int]$_.Matches[0].Groups[1].Value } | Select-Object -Unique
        }
        foreach ($procId in $pids) {
            if (-not $procId -or $procId -eq $PID) { continue }
            $info = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
            $name = if ($info) { $info.Name } else { "unknown" }
            $cmd = if ($info -and $info.CommandLine) { $info.CommandLine } else { "" }
            $ours = ($name -match '^(python|pythonw|node)\.exe$') -and ($cmd -like "*$RepoPath*" -or $cmd -like "*uvicorn*app.main:app*" -or $cmd -like "*vite*")
            if ($ours) {
                Write-Host "Stopping previous WiFi-Doctor instance on port $port (PID $procId, $name)" -ForegroundColor Yellow
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            } else {
                $blocked += "port $port is used by $name (PID $procId)"
            }
        }
    }
    Start-Sleep -Milliseconds 800
    return $blocked
}
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Refresh-Path {
    $sys = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    $extra = "$env:LOCALAPPDATA\Microsoft\WindowsApps;$env:ProgramFiles\nodejs;$env:ProgramFiles\Git\cmd;$env:LOCALAPPDATA\Programs\Python\Python313;$env:LOCALAPPDATA\Programs\Python\Python313\Scripts;$env:LOCALAPPDATA\Programs\Python\Launcher"
    $env:Path = $sys + ";" + [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User") + ";" + $extra
}
function Fail($why) { Write-Host "STATUS: FAILED $why" -ForegroundColor Red; Stop-Transcript | Out-Null; Read-Host "Press Enter to close"; exit 1 }

function Get-PythonCmd {
    foreach ($c in @(@("py","-3"), @("python"))) {
        try {
            $args2 = @($c | Select-Object -Skip 1) + "--version"
            $v = & $c[0] @args2 2>&1 | Out-String
            if ($v -match "Python 3\.(\d+)") { if ([int]$Matches[1] -ge 10) { return ,$c } }
        } catch {}
    }
    return $null
}
function Run-Py($c, [string[]]$rest) { $a = @($c | Select-Object -Skip 1) + $rest; & $c[0] @a }

Step "Checking tools"
Refresh-Path
$py = Get-PythonCmd
$hasNode = [bool](Get-Command node -ErrorAction SilentlyContinue)
Write-Host "python: $($py -join ' ')  node: $hasNode"

$need = @()
if (-not $py)      { $need += "Python.Python.3.13" }
if (-not $hasNode) { $need += "OpenJS.NodeJS.LTS" }
foreach ($id in $need) {
    Step "Installing $id via winget"
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements --scope user
    if ($LASTEXITCODE -ne 0) { winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements }
}
Refresh-Path
$py = Get-PythonCmd
if (-not $py) { Fail "python-missing" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail "node-missing" }
Write-Host "python: $(Run-Py $py @('--version'))  node: $(node --version)"

Step "Backend: virtualenv + dependencies"
Set-Location (Join-Path $Repo "backend")
if (-not (Test-Path ".venv")) { Run-Py $py @('-m','venv','.venv') }
$vpy = Join-Path $Repo "backend\.venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "pip" }

Step "Frontend: npm install"
Set-Location (Join-Path $Repo "frontend")
npm install --no-fund --no-audit
if ($LASTEXITCODE -ne 0) { Fail "npm" }

Step "Stopping any previous WiFi-Doctor instance"
function Get-FreePort([int]$Preferred) {
    for ($p = $Preferred; $p -lt $Preferred + 50; $p++) {
        $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
        if (-not $busy) { return $p }
    }
    return $null
}

$blocked = Stop-PortOwners @(8000, 5173) $Repo
if ($blocked) {
    # Another app (e.g. a Docker project) owns the default port: never touch it, just move over.
    Write-Host "Note: $($blocked -join '; '). WiFi-Doctor will use free ports instead." -ForegroundColor Yellow
}
$ApiPort = Get-FreePort 8000
$UiPort = Get-FreePort 5173
if (-not $ApiPort -or -not $UiPort) { Fail "no-free-port" }
$env:VITE_BACKEND_URL = "http://127.0.0.1:$ApiPort"
Write-Host "Ports: API $ApiPort, dashboard $UiPort"

Step "Starting backend (port $ApiPort)"
$backendLog = Join-Path $Base "wifi-doctor-backend.log"
# -NoNewWindow keeps the backend attached to this console: closing the window stops it too.
$backend = Start-Process -FilePath $vpy -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","$ApiPort" `
    -WorkingDirectory (Join-Path $Repo "backend") -RedirectStandardError $backendLog -PassThru -NoNewWindow
Start-Sleep -Seconds 8
try { $s = Invoke-RestMethod "http://127.0.0.1:$ApiPort/api/status" -TimeoutSec 5; Write-Host "backend OK | probe=$($s.network.probe_backend) gateway=$($s.network.gateway_ip) dns=$($s.network.dns_server) iface=$($s.network.interface) | wifi=$($s.wifi | ConvertTo-Json -Compress)" }
catch { Write-Host "backend not answering yet: $_" -ForegroundColor Yellow }

Write-Host "STATUS: RUNNING  dashboard=http://localhost:$UiPort  api=$ApiPort" -ForegroundColor Green
Write-Host "Keep this window open. Close it (or Ctrl+C) to stop WiFi-Doctor." -ForegroundColor Green
Stop-Transcript | Out-Null
Start-Job -ArgumentList $UiPort { param($port) Start-Sleep 8; Start-Process "http://localhost:$port" } | Out-Null
try { npm run dev -- --strictPort --port $UiPort } finally { if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -Force } }
