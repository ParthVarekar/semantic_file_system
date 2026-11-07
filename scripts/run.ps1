<#
.SYNOPSIS  Launch the desktop app (no console window). Use -Console to see log output.
           -Quick starts the quick search window instead (Alt+Shift+Space; lives in the tray).
#>
param([switch]$Console, [switch]$Quick, [string]$VenvDir = "")
$root = Split-Path -Parent $PSScriptRoot
if (-not $VenvDir) {  # prefer the GPU (DirectML) environment when it exists
    $VenvDir = if (Test-Path (Join-Path $root ".venv-gpu\Scripts\python.exe")) { ".venv-gpu" } else { ".venv" }
}
$exe = if ($Console) { "python.exe" } else { "pythonw.exe" }
$py = Join-Path $root "$VenvDir\Scripts\$exe"
if (-not (Test-Path $py)) { Write-Error "Environment not found. Run scripts\setup.ps1 first."; exit 1 }
$module = if ($Quick) { "lfi.ui.quick" } else { "lfi.ui.app" }
# Run the venv interpreter directly (not 'uv run') so a -Gpu ONNX Runtime swap is not reverted.
& $py -m $module
