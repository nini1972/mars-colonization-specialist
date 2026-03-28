$ErrorActionPreference = "Stop"

Write-Host "[setup] Creating virtual environment (.venv)"
python -m venv .venv

Write-Host "[setup] Activating virtual environment"
. .\.venv\Scripts\Activate.ps1

Write-Host "[setup] Installing project with development dependencies"
python -m pip install --upgrade pip
python -m pip install -e .[dev]

Write-Host "[setup] Completed"
