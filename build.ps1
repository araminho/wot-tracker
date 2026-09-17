$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name 'WoT Battle Tracker' `
  launcher.py
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}
Write-Host "Built: $root\dist\WoT Battle Tracker.exe"
