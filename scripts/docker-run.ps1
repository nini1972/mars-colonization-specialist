#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Run the Mars Agent dashboard container.
.DESCRIPTION
    Starts the mars-agent:latest image, mounts a named volume for SQLite
    persistence, and binds the dashboard port.  Stops any existing container
    with the same name before starting.
.PARAMETER Port
    Host port to bind (default: 8000).
.PARAMETER DataDir
    Host path to use instead of a named Docker volume.  Useful for local
    development where you want to inspect the SQLite file directly.
.PARAMETER Detach
    Run the container in detached mode (background).
.EXAMPLE
    ./scripts/docker-run.ps1
    ./scripts/docker-run.ps1 -Port 8080 -Detach
    ./scripts/docker-run.ps1 -DataDir C:\mars-data
#>
param(
    [int]$Port     = 8000,
    [string]$DataDir = "",
    [switch]$Detach
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ContainerName = "mars-agent-dashboard"
$ImageName     = "mars-agent:latest"

# Stop + remove any previous container with the same name
$existing = docker ps -aq --filter "name=^/${ContainerName}$" 2>$null
if ($existing) {
    Write-Host "Removing existing container $ContainerName ..." -ForegroundColor Yellow
    docker rm -f $ContainerName | Out-Null
}

# Build volume / bind-mount argument
if ($DataDir -ne "") {
    $VolumeArg = @("--volume", "${DataDir}:/data")
} else {
    $VolumeArg = @("--volume", "mars_data:/data")
}

$DetachFlag = if ($Detach) { @("--detach") } else { @() }

Write-Host "Starting $ContainerName on port $Port ..." -ForegroundColor Cyan
docker run `
    --name $ContainerName `
    --publish "${Port}:8000" `
    --env MARS_MCP_PERSISTENCE_BACKEND=sqlite `
    --env MARS_MCP_PERSISTENCE_SQLITE_PATH=/data/mars_mcp_runtime.sqlite3 `
    @VolumeArg `
    @DetachFlag `
    $ImageName

if ($Detach) {
    Write-Host "Dashboard running at http://localhost:${Port}/dashboard" -ForegroundColor Green
}
