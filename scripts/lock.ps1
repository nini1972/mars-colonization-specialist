$ErrorActionPreference = "Stop"

if (-not (Test-Path .\.venv\Scripts\Activate.ps1)) {
  throw "Virtual environment not found. Run ./scripts/setup.ps1 first."
}

. .\.venv\Scripts\Activate.ps1

Write-Host "[lock] Exporting dependency snapshot"
python -m pip freeze | Sort-Object > requirements-dev.lock.txt
Write-Host "[lock] Wrote requirements-dev.lock.txt"
