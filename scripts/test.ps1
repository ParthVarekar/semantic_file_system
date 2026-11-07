<#
.SYNOPSIS  Run the test suite on synthetic data.
.PARAMETER All   Also run tests that need the downloaded model (-m model) and the 20k-file test (-m slow).
#>
param([switch]$All, [string]$VenvDir = ".venv")
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root "$VenvDir\Scripts\python.exe"
& $py -m pytest -q
$code = $LASTEXITCODE
if ($All) {
    & $py -m pytest -q -m "model or slow"
    if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }
}
exit $code
