#Requires -Version 5.1

[CmdletBinding()]
param(
    [string]$ListenHost = "127.0.0.1",

    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [switch]$ForcePort
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

function Get-VideoTestHealth {
    param([string]$BaseUrl)

    try {
        $health = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 2
        if ($health.status -eq "ok" -and $health.service -eq "video-test") {
            return $health
        }
    }
    catch {
        return $null
    }
    return $null
}

function Get-PortListeners {
    param([int]$LocalPort)

    return @(
        Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
            Sort-Object OwningProcess -Unique
    )
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    throw "uv was not found. Install it from https://docs.astral.sh/uv/ and run this script again."
}

Set-Location -LiteralPath $ProjectRoot

$serverConfigScript = @'
import json
import os
from pathlib import Path

from dotenv import dotenv_values

dotenv = dotenv_values(Path.cwd() / ".env")
print(json.dumps({
    "host": dotenv.get("VIDEOTEST_HOST") or os.getenv("VIDEOTEST_HOST") or "127.0.0.1",
    "port": dotenv.get("VIDEOTEST_PORT") or os.getenv("VIDEOTEST_PORT") or "8000",
}))
'@
$serverConfigJson = & $uv.Source run --no-sync python -c $serverConfigScript
if ($LASTEXITCODE -ne 0) {
    throw "Failed to load server configuration from .env."
}

try {
    $serverConfig = $serverConfigJson | ConvertFrom-Json -ErrorAction Stop
}
catch {
    throw "Failed to parse server configuration: $($_.Exception.Message)"
}

if (-not $PSBoundParameters.ContainsKey("ListenHost")) {
    $ListenHost = [string]$serverConfig.host
}
if (-not $PSBoundParameters.ContainsKey("Port")) {
    $parsedPort = 0
    $configuredPort = [string]$serverConfig.port
    if (-not [int]::TryParse($configuredPort, [ref]$parsedPort)) {
        throw "VIDEOTEST_PORT must be an integer."
    }
    if ($parsedPort -lt 1 -or $parsedPort -gt 65535) {
        throw "VIDEOTEST_PORT must be between 1 and 65535."
    }
    $Port = $parsedPort
}

$browserHost = if ($ListenHost -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $ListenHost }
$baseUrl = "http://${browserHost}:$Port"
$listeners = @(Get-PortListeners -LocalPort $Port)

if ($listeners.Count -eq 0) {
    Write-Host "VideoTest is not running on port $Port."
    exit 0
}

if (-not (Get-VideoTestHealth -BaseUrl $baseUrl) -and -not $ForcePort) {
    $processIds = ($listeners | ForEach-Object { $_.OwningProcess }) -join ", "
    throw "Port $Port is not a healthy VideoTest service (PID: $processIds). Use -ForcePort only if it is safe to stop."
}

foreach ($listener in $listeners) {
    $processId = $listener.OwningProcess
    Write-Host "Stopping VideoTest on port $Port (PID $processId)..."
    Stop-Process -Id $processId -Force -ErrorAction Stop
}

$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (@(Get-PortListeners -LocalPort $Port).Count -gt 0) {
    if ([DateTime]::UtcNow -ge $deadline) {
        throw "VideoTest did not stop within 10 seconds."
    }
    Start-Sleep -Milliseconds 250
}

Write-Host "VideoTest has stopped." -ForegroundColor Green
