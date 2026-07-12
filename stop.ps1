#Requires -Version 5.1

[CmdletBinding()]
param(
    [string]$ListenHost = "127.0.0.1",

    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [switch]$ForcePort
)

$ErrorActionPreference = "Stop"

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
