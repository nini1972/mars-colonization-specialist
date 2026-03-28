$ErrorActionPreference = "Stop"

if (-not (Test-Path .\.venv\Scripts\Activate.ps1)) {
  throw "Virtual environment not found. Run ./scripts/setup.ps1 first."
}

. .\.venv\Scripts\Activate.ps1

Write-Host "[check] Ruff lint"
python -m ruff check .

Write-Host "[check] Mypy type checking"
python -m mypy src tests

Write-Host "[check] Pytest"
python -m pytest

Write-Host "[check] Completed"
