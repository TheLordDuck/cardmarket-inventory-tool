# Builds the Windows release: dist\CardmarketInventoryTool-<version>.zip
# Usage (from the project root, with the .venv set up):
#   .\build.ps1            # version "dev"
#   .\build.ps1 v1.0.0
param([string]$Version = "dev")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$name = "CardmarketInventoryTool"

& $python -m pip install --quiet -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --collect-all playwright bundles Playwright's driver (node + its JS
# package). No browser is bundled: the tool drives the user's own installed
# Google Chrome over CDP.
& $python -m PyInstaller app.py `
    --name $name `
    --onedir `
    --windowed `
    --noconfirm `
    --clean `
    --collect-all playwright `
    --distpath dist `
    --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$out = "dist\$name"
Copy-Item .env.example "$out\.env.example"
Copy-Item RELEASE_README.txt "$out\README.txt"

$zip = "dist\$name-$Version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $out -DestinationPath $zip
Write-Host "Built $zip"
