#Requires -Version 5.1

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [string]$ProjectRoot,
    [string]$RollbackRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
if (-not $RollbackRoot) {
    $RollbackRoot = Join-Path $PSScriptRoot "restore-rollback"
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $stream = [IO.File]::OpenRead($LiteralPath)
    try {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            return [BitConverter]::ToString($sha256.ComputeHash($stream)).Replace("-", "").ToLowerInvariant()
        }
        finally {
            $sha256.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

$manifestPath = Join-Path $PSScriptRoot "MANIFEST.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Backup manifest not found: $manifestPath"
}

try {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
        ConvertFrom-Json -ErrorAction Stop
}
catch {
    throw "Backup manifest is not valid JSON: $($_.Exception.Message)"
}

if ([int]$manifest.manifest_version -ne 1) {
    throw "Unsupported manifest version: $($manifest.manifest_version)"
}

$bundleRootPath = [IO.Path]::GetFullPath($PSScriptRoot)
$backupRootPath = [IO.Path]::GetFullPath((Join-Path $bundleRootPath "backup"))
$projectRootPath = [IO.Path]::GetFullPath($ProjectRoot)
$rollbackBasePath = [IO.Path]::GetFullPath($RollbackRoot)

if (-not (Test-Path -LiteralPath $projectRootPath -PathType Container)) {
    throw "Project root does not exist: $projectRootPath"
}
if (-not (Test-Path -LiteralPath $backupRootPath -PathType Container)) {
    throw "Backup data directory does not exist: $backupRootPath"
}

$directorySeparator = [string][IO.Path]::DirectorySeparatorChar
$backupPrefix = $backupRootPath.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + $directorySeparator
$projectPrefix = $projectRootPath.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + $directorySeparator

$manifestEntries = @($manifest.files)
if ($manifestEntries.Count -eq 0) {
    throw "Backup manifest does not contain any files."
}

$verifiedEntries = @()
foreach ($entry in $manifestEntries) {
    $backupRelative = [string]$entry.backup_path
    $projectRelative = [string]$entry.project_path
    if (-not $backupRelative -or -not $projectRelative) {
        throw "A manifest file entry has an empty path."
    }
    if ([IO.Path]::IsPathRooted($backupRelative) -or [IO.Path]::IsPathRooted($projectRelative)) {
        throw "Manifest paths must be relative: $backupRelative, $projectRelative"
    }

    $backupNative = $backupRelative.Replace("/", $directorySeparator)
    $projectNative = $projectRelative.Replace("/", $directorySeparator)
    $sourcePath = [IO.Path]::GetFullPath((Join-Path $bundleRootPath $backupNative))
    $destinationPath = [IO.Path]::GetFullPath((Join-Path $projectRootPath $projectNative))

    if (-not $sourcePath.StartsWith($backupPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Backup path escapes the backup directory: $backupRelative"
    }
    if (-not $destinationPath.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Project path escapes the project root: $projectRelative"
    }
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Backup file is missing: $sourcePath"
    }

    $actualSize = (Get-Item -LiteralPath $sourcePath).Length
    $expectedSize = [long]$entry.size
    if ($actualSize -ne $expectedSize) {
        throw "Backup file size mismatch for ${backupRelative}: expected $expectedSize, got $actualSize"
    }

    $actualHash = Get-Sha256 -LiteralPath $sourcePath
    $expectedHash = ([string]$entry.sha256).ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "Backup SHA-256 mismatch for $backupRelative"
    }

    $verifiedEntries += [pscustomobject]@{
        project_path = $projectRelative
        source = $sourcePath
        destination = $destinationPath
        sha256 = $expectedHash
    }
}

$topologyEntries = @($verifiedEntries | Where-Object { $_.project_path -eq "traffic_map.json" })
if ($topologyEntries.Count -ne 1) {
    throw "Manifest must contain exactly one traffic_map.json entry."
}

try {
    $topology = Get-Content -LiteralPath $topologyEntries[0].source -Raw -Encoding UTF8 |
        ConvertFrom-Json -ErrorAction Stop
}
catch {
    throw "Backed-up traffic_map.json is not valid JSON: $($_.Exception.Message)"
}

$segmentCount = @($topology.segments).Count
$cameraCount = @($topology.cameras).Count
$mapImage = ([string]$topology.map_image).Replace("\", "/")
if ([int]$topology.version -ne [int]$manifest.topology.version) {
    throw "Topology version does not match the manifest."
}
if ($segmentCount -ne [int]$manifest.topology.segment_count) {
    throw "Topology segment count does not match the manifest."
}
if ($cameraCount -ne [int]$manifest.topology.camera_count) {
    throw "Topology camera count does not match the manifest."
}
if ($mapImage -ne [string]$manifest.topology.map_image) {
    throw "Topology map image path does not match the manifest."
}
if (@($verifiedEntries | Where-Object { $_.project_path -eq $mapImage }).Count -ne 1) {
    throw "The topology map image is not included in the backup manifest."
}

Write-Host (
    "Backup verification passed: topology v{0}, {1} segments, {2} cameras." -f
    $topology.version,
    $segmentCount,
    $cameraCount
) -ForegroundColor Green

$action = "create a rollback snapshot and restore $($verifiedEntries.Count) topology files"
if (-not $PSCmdlet.ShouldProcess($projectRootPath, $action)) {
    Write-Host "No project files were changed."
    return
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$rollbackSnapshotPath = Join-Path $rollbackBasePath $timestamp
$rollbackState = @{}

try {
    New-Item -ItemType Directory -Path $rollbackSnapshotPath -Force | Out-Null

    foreach ($entry in $verifiedEntries) {
        $state = [pscustomobject]@{
            existed = Test-Path -LiteralPath $entry.destination -PathType Leaf
            rollback = $null
        }
        if ($state.existed) {
            $relativeNative = $entry.project_path.Replace("/", $directorySeparator)
            $rollbackPath = [IO.Path]::GetFullPath(
                (Join-Path $rollbackSnapshotPath $relativeNative)
            )
            New-Item -ItemType Directory -Path (Split-Path -Parent $rollbackPath) -Force |
                Out-Null
            Copy-Item -LiteralPath $entry.destination -Destination $rollbackPath -Force
            $state.rollback = $rollbackPath
        }
        $rollbackState[$entry.destination] = $state
    }

    $orderedEntries = @(
        $verifiedEntries | Sort-Object -Property @{
            Expression = { if ($_.project_path -eq "traffic_map.json") { 1 } else { 0 } }
        }
    )
    foreach ($entry in $orderedEntries) {
        $destinationDirectory = Split-Path -Parent $entry.destination
        New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
        $temporaryPath = Join-Path $destinationDirectory (
            ".{0}.{1}.restore" -f [IO.Path]::GetFileName($entry.destination), [guid]::NewGuid().ToString("N")
        )
        try {
            Copy-Item -LiteralPath $entry.source -Destination $temporaryPath -Force
            $temporaryHash = Get-Sha256 -LiteralPath $temporaryPath
            if ($temporaryHash -ne $entry.sha256) {
                throw "Staged file SHA-256 mismatch for $($entry.project_path)"
            }
            Move-Item -LiteralPath $temporaryPath -Destination $entry.destination -Force
        }
        finally {
            if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
                Remove-Item -LiteralPath $temporaryPath -Force
            }
        }
    }

    foreach ($entry in $verifiedEntries) {
        $destinationHash = Get-Sha256 -LiteralPath $entry.destination
        if ($destinationHash -ne $entry.sha256) {
            throw "Restored file SHA-256 mismatch for $($entry.project_path)"
        }
    }
}
catch {
    $restoreError = $_
    $rollbackErrors = @()
    foreach ($entry in $verifiedEntries) {
        try {
            $state = $rollbackState[$entry.destination]
            if ($null -ne $state -and $state.existed) {
                Copy-Item -LiteralPath $state.rollback -Destination $entry.destination -Force
            }
            elseif ($null -ne $state -and (Test-Path -LiteralPath $entry.destination -PathType Leaf)) {
                Remove-Item -LiteralPath $entry.destination -Force
            }
        }
        catch {
            $rollbackErrors += $_.Exception.Message
        }
    }

    if ($rollbackErrors.Count -gt 0) {
        throw (
            "Restore failed: {0}. Automatic rollback also had errors: {1}. Rollback files: {2}" -f
            $restoreError.Exception.Message,
            ($rollbackErrors -join "; "),
            $rollbackSnapshotPath
        )
    }
    throw "Restore failed and project files were rolled back: $($restoreError.Exception.Message)"
}

$restoredTopology = Get-Content -LiteralPath $topologyEntries[0].destination -Raw -Encoding UTF8 |
    ConvertFrom-Json -ErrorAction Stop
if (
    [int]$restoredTopology.version -ne [int]$manifest.topology.version -or
    @($restoredTopology.segments).Count -ne [int]$manifest.topology.segment_count -or
    @($restoredTopology.cameras).Count -ne [int]$manifest.topology.camera_count
) {
    throw "Post-restore topology validation failed. Rollback files: $rollbackSnapshotPath"
}

Write-Host "Topology backup restored successfully." -ForegroundColor Green
Write-Host "Rollback snapshot: $rollbackSnapshotPath"
Write-Host "Start VideoTest and verify /api/health, /api/map, and /api/map/image."
