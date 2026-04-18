param(
  [ValidateSet("sync", "async", "both")]
  [string]$Mode = "both",
  [int]$Requests = 240,
  [int]$Concurrency = 24,
  [double]$TimeoutSeconds = 20,
  [ValidateSet("memory", "sqlite")]
  [string]$PersistenceBackend = "memory",
  [string]$SqlitePath = ".\.mars_mcp_runtime.soak.sqlite3",
  [string]$OutputPath = "",
  [double]$MaxErrorRate = -1,
  [switch]$ResetRuntimeState
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path .\.venv\Scripts\Activate.ps1)) {
  throw "Virtual environment not found. Run ./scripts/setup.ps1 first."
}

. .\.venv\Scripts\Activate.ps1

$args = @(
  ".\scripts\soak_planner.py",
  "--mode", $Mode,
  "--requests", $Requests,
  "--concurrency", $Concurrency,
  "--timeout-seconds", $TimeoutSeconds,
  "--persistence-backend", $PersistenceBackend,
  "--sqlite-path", $SqlitePath,
  "--max-error-rate", $MaxErrorRate
)

if ($OutputPath -ne "") {
  $args += @("--output", $OutputPath)
}

if ($ResetRuntimeState.IsPresent) {
  $args += "--reset-runtime-state"
}

Write-Host "[soak] mode=$Mode requests=$Requests concurrency=$Concurrency backend=$PersistenceBackend"
python @args
