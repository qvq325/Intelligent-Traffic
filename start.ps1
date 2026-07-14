#Requires -Version 5.1

[CmdletBinding()]
param(
    [string]$ListenHost = "127.0.0.1",

    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [switch]$NoBrowser,
    [switch]$SkipSync,
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

function Stop-PortListeners {
    param(
        [object[]]$Listeners,
        [int]$LocalPort
    )

    foreach ($listener in $Listeners) {
        $processId = $listener.OwningProcess
        Write-Host "Stopping the existing VideoTest process on port $LocalPort (PID $processId)..."
        Stop-Process -Id $processId -Force -ErrorAction Stop
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (@(Get-PortListeners -LocalPort $LocalPort).Count -gt 0) {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "Port $LocalPort did not become available after stopping the old process."
        }
        Start-Sleep -Milliseconds 250
    }
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    throw "uv was not found. Install it from https://docs.astral.sh/uv/ and run this script again."
}

Set-Location -LiteralPath $ProjectRoot

if (-not $SkipSync) {
    Write-Host "Checking Python 3.11 environment and dependencies..."
    & $uv.Source sync --frozen
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed with exit code $LASTEXITCODE."
    }
}

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
if ($listeners.Count -gt 0) {
    $existingApp = Get-VideoTestHealth -BaseUrl $baseUrl
    if (-not $existingApp -and -not $ForcePort) {
        $processIds = ($listeners | ForEach-Object { $_.OwningProcess }) -join ", "
        throw "Port $Port is used by another application (PID: $processIds). Use -ForcePort only if it is safe to stop."
    }
    Stop-PortListeners -Listeners $listeners -LocalPort $Port
}

$logDirectory = Join-Path $ProjectRoot "runtime\logs"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdoutLog = Join-Path $logDirectory "server-$timestamp.out.log"
$stderrLog = Join-Path $logDirectory "server-$timestamp.err.log"

Write-Host "Starting VideoTest at $baseUrl..."
$processEnvironment = [EnvironmentVariableTarget]::Process
$originalHost = [Environment]::GetEnvironmentVariable("VIDEOTEST_HOST", $processEnvironment)
$originalPort = [Environment]::GetEnvironmentVariable("VIDEOTEST_PORT", $processEnvironment)
try {
    [Environment]::SetEnvironmentVariable("VIDEOTEST_HOST", $ListenHost, $processEnvironment)
    [Environment]::SetEnvironmentVariable("VIDEOTEST_PORT", [string]$Port, $processEnvironment)
    $launcher = Start-Process `
        -FilePath $uv.Source `
        -ArgumentList @("run", "python", "main.py") `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru
}
finally {
    [Environment]::SetEnvironmentVariable("VIDEOTEST_HOST", $originalHost, $processEnvironment)
    [Environment]::SetEnvironmentVariable("VIDEOTEST_PORT", $originalPort, $processEnvironment)
}

$ready = $false
$deadline = [DateTime]::UtcNow.AddSeconds(60)
while ([DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 500
    if ($launcher.HasExited) {
        break
    }
    $activeListeners = @(Get-PortListeners -LocalPort $Port)
    if ($activeListeners.Count -gt 0 -and (Get-VideoTestHealth -BaseUrl $baseUrl)) {
        $ready = $true
        break
    }
}

if (-not $ready) {
    Write-Host ""
    Write-Host "Server startup failed. Recent stderr output:" -ForegroundColor Red
    Get-Content -LiteralPath $stderrLog -Tail 30 -ErrorAction SilentlyContinue
    throw "VideoTest did not become healthy within 60 seconds."
}

$serverListeners = @(Get-PortListeners -LocalPort $Port)
$serverProcessId = if ($serverListeners.Count -gt 0) {
    $serverListeners[0].OwningProcess
} else {
    $launcher.Id
}

Write-Host ""
Write-Host "VideoTest is running." -ForegroundColor Green
Write-Host "URL:    $baseUrl"
Write-Host "PID:    $serverProcessId"
Write-Host "stdout: $stdoutLog"
Write-Host "stderr: $stderrLog"
Write-Host "Run this script again to restart the service with the latest code."
