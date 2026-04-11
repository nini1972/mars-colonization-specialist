#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build the Mars Agent Docker image.
.DESCRIPTION
    Builds the Docker image tagged as mars-agent:latest (and optionally a
    version tag).  Run from the repository root.
.PARAMETER Tag
    Optional additional tag (e.g. "1.2.3").  The image is always tagged
    mars-agent:latest.
.EXAMPLE
    ./scripts/docker-build.ps1
    ./scripts/docker-build.ps1 -Tag "1.0.0"
#>
param(
    [string]$Tag = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ImageName = "mars-agent"
$LatestTag = "${ImageName}:latest"

Write-Host "Building $LatestTag ..." -ForegroundColor Cyan
docker build --tag $LatestTag .

if ($Tag -ne "") {
    $VersionTag = "${ImageName}:${Tag}"
    Write-Host "Tagging as $VersionTag ..." -ForegroundColor Cyan
    docker tag $LatestTag $VersionTag
    Write-Host "Built: $VersionTag and $LatestTag" -ForegroundColor Green
} else {
    Write-Host "Built: $LatestTag" -ForegroundColor Green
}
