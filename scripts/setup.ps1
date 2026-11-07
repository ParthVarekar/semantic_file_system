<#
.SYNOPSIS
  Create the Python environment for Local File Intelligence and (optionally) download the model.

.DESCRIPTION
  Network use (disclosed): Python packages from pypi.org, and - unless -NoModel - the embedding
  models from huggingface.co (document model ~67 MB, image model ~230 MB). After this script
  finishes the application works fully offline.

.PARAMETER Gpu      Replace the CPU ONNX Runtime with onnxruntime-directml (any DirectX 12 GPU).
.PARAMETER Dev      Also install test/packaging tools (pytest, pytest-qt, pyinstaller, pypdf).
.PARAMETER Model    Embedding model to download (default BAAI/bge-small-en-v1.5, ~67 MB).
.PARAMETER NoModel  Skip all model downloads (keyword search works; semantic search needs a model).
.PARAMETER NoImageModel  Skip only the image-search model (nomic-embed-vision/text v1.5 int8, ~230 MB).
.PARAMETER VenvDir  Environment folder (default .venv).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Gpu -Dev
#>
param(
    [switch]$Gpu,
    [switch]$Dev,
    [string]$Model = "BAAI/bge-small-en-v1.5",
    [switch]$NoModel,
    [switch]$NoImageModel,
    [string]$VenvDir = ".venv"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root "$VenvDir\Scripts\python.exe"

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "Creating environment with uv (exact versions from uv.lock)..."
    $env:UV_PROJECT_ENVIRONMENT = $VenvDir
    $uvArgs = @("sync", "--frozen")
    if ($Dev) { $uvArgs += @("--extra", "dev") }
    uv @uvArgs
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
    if ($Gpu) {
        Write-Host "Switching ONNX Runtime to the DirectML (GPU) build..."
        uv pip uninstall --python $py onnxruntime
        uv pip install --python $py "onnxruntime-directml==1.24.4"
        if ($LASTEXITCODE -ne 0) { throw "DirectML install failed" }
    }
} else {
    Write-Host "uv not found; falling back to the Python launcher + pip (versions not locked)."
    Write-Host "For reproducible installs: winget install astral-sh.uv"
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Python 3.12 is required (winget install Python.Python.3.12), or install uv (winget install astral-sh.uv)"
    }
    py -3.12 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 is required (winget install Python.Python.3.12)" }
    & $py -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    if ($Dev) { & $py -m pip install -e ".[dev]" } else { & $py -m pip install -e . }
    if ($LASTEXITCODE -ne 0) { throw "installing the app's packages failed" }
    if ($Gpu) {
        & $py -m pip uninstall -y onnxruntime
        & $py -m pip install "onnxruntime-directml==1.24.4"
        if ($LASTEXITCODE -ne 0) { throw "DirectML install failed" }
    }
}

& $py -c "import onnxruntime as o; print('ONNX Runtime', o.__version__, 'providers:', [p for p in o.get_available_providers() if p != 'AzureExecutionProvider'])"

$problems = @()
if (-not $NoModel) {
    Write-Host "Downloading embedding model '$Model' from huggingface.co (one-time)..."
    & $py -m lfi.cli models download $Model
    if ($LASTEXITCODE -ne 0) { $problems += "the embedding model did not download (keyword search still works; retry in Settings > Embedding model)" }
    if (-not $NoImageModel) {
        Write-Host "Downloading image-search model (~230 MB) from huggingface.co (one-time)..."
        & $py -m lfi.cli models download-vision nomic-q
        if ($LASTEXITCODE -ne 0) { $problems += "the image-search model did not download (retry in Settings > Image search)" }
    }
}
Write-Host ""
if ($problems.Count) {
    Write-Warning ("Setup finished with problems:`n  - " + ($problems -join "`n  - "))
    Write-Host "Start the app anyway with:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1"
    exit 1
}
Write-Host "Done. Start the app with:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1"
