<#
.SYNOPSIS  Build a Windows executable folder with PyInstaller (dist\LocalFileIntelligence\).
.PARAMETER BundleModel  Copy the cached embedding model next to the .exe for offline installs.
#>
param([switch]$BundleModel, [string]$VenvDir = ".venv")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root "$VenvDir\Scripts\python.exe"
# Build with a minimal PATH so no DLL is picked up from unrelated installed software.
$savedPath = $env:PATH
$env:PATH = "$(Join-Path $root "$VenvDir\Scripts");$env:SystemRoot\System32;$env:SystemRoot"
try { & $py -m PyInstaller --noconfirm --clean packaging\lfi.spec } finally { $env:PATH = $savedPath }
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
& $py packaging\audit_binaries.py build\lfi\COLLECT-00.toc
if ($LASTEXITCODE -ne 0) { throw "Build bundled DLLs from outside the venv/Python/Windows - refusing to ship it" }
$dist = Join-Path $root "dist\LocalFileIntelligence"
if ($BundleModel) {
    $models = if ($env:LFI_MODELS) { $env:LFI_MODELS } else { Join-Path $env:LOCALAPPDATA "LocalFileIntelligence\models" }
    if (-not (Test-Path $models)) { throw "No model cache at $models - download the models first (scripts\setup.ps1)" }
    Write-Host "Bundling cached models from $models"
    Copy-Item -Recurse -Force $models (Join-Path $dist "models")
}
Write-Host "Built: $dist\LocalFileIntelligence.exe"
