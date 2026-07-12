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

if (-not $PSBoundParameters.ContainsKey("ListenHost") -and $env:VIDEOTEST_HOST) {
    $ListenHost = $env:VIDEOTEST_HOST
}
if (-not $PSBoundParameters.ContainsKey("Port") -and $env:VIDEOTEST_PORT) {
    $parsedPort = 0
    if (-not [int]::TryParse($env:VIDEOTEST_PORT, [ref]$parsedPort)) {
        throw "VIDEOTEST_PORT must be an integer."
    }
    if ($parsedPort -lt 1 -or $parsedPort -gt 65535) {
        throw "VIDEOTEST_PORT must be between 1 and 65535."
    }
    $Port = $parsedPort
}

$browserHost = if ($ListenHost -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $ListenHost }
$baseUrl = "http://${browserHost}:$Port"
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    throw "uv was not found. Install it from https://docs.astral.sh/uv/ and run this script again."
}

Set-Location -LiteralPath $ProjectRoot

$listeners = @(Get-PortListeners -LocalPort $Port)
if ($listeners.Count -gt 0) {
    $existingApp = Get-VideoTestHealth -BaseUrl $baseUrl
    if (-not $existingApp -and -not $ForcePort) {
        $processIds = ($listeners | ForEach-Object { $_.OwningProcess }) -join ", "
        throw "Port $Port is used by another application (PID: $processIds). Use -ForcePort only if it is safe to stop."
    }
    Stop-PortListeners -Listeners $listeners -LocalPort $Port
}

if (-not $SkipSync) {
    Write-Host "Checking Python 3.11 environment and dependencies..."
    & $uv.Source sync --frozen
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed with exit code $LASTEXITCODE."
    }
}

$logDirectory = Join-Path $ProjectRoot "runtime\logs"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdoutLog = Join-Path $logDirectory "server-$timestamp.out.log"
$stderrLog = Join-Path $logDirectory "server-$timestamp.err.log"

$env:VIDEOTEST_HOST = $ListenHost
$env:VIDEOTEST_PORT = [string]$Port

Write-Host "Starting VideoTest at $baseUrl..."
$launcher = Start-Process `
    -FilePath $uv.Source `
    -ArgumentList @("run", "python", "main.py") `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

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

if (-not $NoBrowser) {
    Start-Process $baseUrl
}
