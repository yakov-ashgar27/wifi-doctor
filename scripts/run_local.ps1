# WiFi-Doctor local runner for Windows (PowerShell 5+).
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1
# Run from an *Administrator* PowerShell with Npcap installed to enable the
# scapy raw-ICMP backend. Otherwise WiFi-Doctor transparently falls back to
# the built-in ping.exe and keeps measuring real latency.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Write-Host "==> WiFi-Doctor | root: $Root" -ForegroundColor Cyan

# ---------- backend ----------
Set-Location "$Root\backend"
$py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
if (-not (Test-Path ".venv")) {
    Write-Host "==> creating Python virtualenv" -ForegroundColor Cyan
    if ($py -eq "py") { & py -3 -m venv .venv } else { & python -m venv .venv }
}
$venvPython = "$Root\backend\.venv\Scripts\python.exe"
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -r requirements.txt

Write-Host "==> starting backend on http://127.0.0.1:8000" -ForegroundColor Cyan
$backend = Start-Process -FilePath $venvPython `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory "$Root\backend" -PassThru -NoNewWindow

try {
    # ---------- frontend ----------
    Set-Location "$Root\frontend"
    if (-not (Test-Path "node_modules")) {
        Write-Host "==> installing frontend dependencies" -ForegroundColor Cyan
        npm install --no-fund --no-audit
    }
    Write-Host "==> dashboard: http://localhost:5173" -ForegroundColor Green
    npm run dev
}
finally {
    Write-Host "==> shutting down backend" -ForegroundColor Cyan
    if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
}
