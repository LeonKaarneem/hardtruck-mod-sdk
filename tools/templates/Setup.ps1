[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$GamePath,
    [ValidateSet("Menu", "InstallAll", "Custom", "Uninstall", "CrashReport", "Quit")]
    [string]$Action = "Menu",
    [string[]]$Components = @(),
    [switch]$NonInteractive
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$script:InstallerRoot = $PSScriptRoot
$script:PackageRoot = Split-Path -Parent $PSScriptRoot
$script:PayloadRoot = Join-Path $script:InstallerRoot "payload"
$script:BackupRoot = Join-Path $script:PackageRoot "installer/backups"
$script:SelectedAction = $Action

function Test-Hash {
    param(
        [string]$Path,
        [string]$ExpectedHash
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    return $actual -ieq $ExpectedHash
}

function Read-YesNo {
    param(
        [string]$Prompt,
        [bool]$DefaultYes = $true,
        [switch]$NonInteractive
    )
    if ($NonInteractive) {
        return $DefaultYes
    }
    $suffix = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
    while ($true) {
        $answer = Read-Host "$Prompt $suffix"
        if ($null -eq $answer) {
            throw "Input ended; setup cancelled."
        }
        $answer = $answer.Trim()
        if ($answer.Length -eq 0) {
            return $DefaultYes
        }
        if ($answer -match "^(y|yes)$") {
            return $true
        }
        if ($answer -match "^(n|no)$") {
            return $false
        }
        Write-Host "Please answer Y or N."
    }
}

function Get-ComponentRecords {
    param($Manifest)
    return @($Manifest.components.PSObject.Properties)
}

function New-Selection {
    param($Manifest)
    $selection = @{}
    foreach ($property in @(Get-ComponentRecords $Manifest)) {
        $selection[$property.Name] = $false
    }
    return $selection
}

function Resolve-SetupAction {
    param(
        [string]$Action,
        [switch]$NonInteractive
    )
    if ($Action -ne "Menu") {
        return $Action
    }
    if ($NonInteractive) {
        throw "Noninteractive setup requires -Action InstallAll, Custom, Uninstall, CrashReport, or Quit."
    }
    Write-Host ""
    Write-Host "Hard Truck Modern setup"
    Write-Host "1. Install all"
    Write-Host "2. Choose components"
    Write-Host "3. Uninstall"
    Write-Host "4. Create crash report"
    Write-Host "5. Quit"
    $choice = Read-Host "Select 1-5"
    switch ($choice) {
        "1" { return "InstallAll" }
        "2" { return "Custom" }
        "3" { return "Uninstall" }
        "4" { return "CrashReport" }
        "5" { return "Quit" }
        default { throw "Invalid menu selection; setup cancelled." }
    }
}

function Get-Selection {
    param(
        [string]$Action,
        [string[]]$Components,
        [switch]$NonInteractive,
        $Manifest
    )
    $effectiveAction = $Action
    $script:SelectedAction = $effectiveAction
    if ($effectiveAction -in @("Quit", "Uninstall", "CrashReport")) {
        return $null
    }

    $selection = New-Selection $Manifest
    if ($effectiveAction -eq "InstallAll") {
        foreach ($name in @($selection.Keys)) {
            $selection[$name] = $true
        }
        return $selection
    }

    $allowed = @(Get-ComponentRecords $Manifest | ForEach-Object { $_.Name })
    foreach ($component in @($Components)) {
        foreach ($name in ($component -split ",")) {
            $name = $name.Trim()
            if ($name.Length -eq 0) {
                continue
            }
            if ($allowed -notcontains $name) {
                throw "Unknown component '$name'. Allowed: $($allowed -join ', ')."
            }
            $selection[$name] = $true
        }
    }
    if (-not $NonInteractive) {
        foreach ($property in @(Get-ComponentRecords $Manifest)) {
            $metadata = $property.Value
            Write-Host $metadata.description
            $selection[$property.Name] = Read-YesNo $metadata.label -DefaultYes ([bool]$metadata.default)
        }
    }
    return $selection
}

function Get-SteamInstallPaths {
    $injected = [Environment]::GetEnvironmentVariable("HARDTRUCK_DISCOVERY_REGISTRY_JSON")
    if ($null -ne $injected) {
        try {
            $values = $injected | ConvertFrom-Json
            foreach ($property in $values.PSObject.Properties) {
                if (-not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
                    [string]$property.Value
                }
            }
        }
        catch {
            return
        }
        return
    }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        return
    }
    foreach ($view in @("Registry64", "Registry32")) {
        $baseKey = $null
        $steamKey = $null
        try {
            $registryView = [Microsoft.Win32.RegistryView]::$view
            $baseKey = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
                [Microsoft.Win32.RegistryHive]::LocalMachine, $registryView)
            $steamKey = $baseKey.OpenSubKey("SOFTWARE\Valve\Steam")
            if ($null -ne $steamKey) {
                $installPath = $steamKey.GetValue("InstallPath")
                if (-not [string]::IsNullOrWhiteSpace([string]$installPath)) {
                    [string]$installPath
                }
            }
        }
        catch {
            continue
        }
        finally {
            if ($null -ne $steamKey) { $steamKey.Dispose() }
            if ($null -ne $baseKey) { $baseKey.Dispose() }
        }
    }
}

function Get-SteamLibraryPaths {
    foreach ($steamPath in @(Get-SteamInstallPaths)) {
        try {
            $steamPath
            $vdfPath = Join-Path $steamPath "config/libraryfolders.vdf"
            if (-not (Test-Path -LiteralPath $vdfPath -PathType Leaf)) {
                continue
            }
            $vdf = Get-Content -LiteralPath $vdfPath -Raw -Encoding UTF8
            $matches = [regex]::Matches($vdf, '"path"\s*"((?:\\.|[^"\\])*)"', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
            foreach ($match in $matches) {
                $libraryPath = $match.Groups[1].Value.Replace('\\', '\').Replace('\"', '"')
                if (-not [string]::IsNullOrWhiteSpace($libraryPath)) {
                    $libraryPath
                }
            }
        }
        catch {
            continue
        }
    }
}

function Get-ProgramRoots {
    $injected = [Environment]::GetEnvironmentVariable("HARDTRUCK_DISCOVERY_PROGRAM_ROOTS")
    if ($null -ne $injected) {
        foreach ($root in ($injected -split "\r?\n")) {
            if (-not [string]::IsNullOrWhiteSpace($root)) {
                $root.Trim()
            }
        }
        return
    }
    try {
        $drives = [System.IO.DriveInfo]::GetDrives()
    }
    catch {
        return
    }
    foreach ($drive in $drives) {
        try {
            if ($drive.DriveType -ne [System.IO.DriveType]::Fixed -or -not $drive.IsReady) {
                continue
            }
            $driveRoot = $drive.RootDirectory.FullName
        }
        catch {
            continue
        }
        foreach ($relative in @("Program Files", "Program Files (x86)", "GOG Games", "Games")) {
            try {
                Join-Path $driveRoot $relative
            }
            catch {
                continue
            }
        }
    }
}

function Get-PreviousManifestPaths {
    try {
        if (-not (Test-Path -LiteralPath $script:BackupRoot -PathType Container)) {
            return
        }
        $files = Get-ChildItem -LiteralPath $script:BackupRoot -Filter installation.json -Recurse -File
        foreach ($file in $files) {
            try {
                $installation = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                if (-not [string]::IsNullOrWhiteSpace([string]$installation.game_path)) {
                    [string]$installation.game_path
                }
            }
            catch {
                continue
            }
        }
    }
    catch {
        return
    }
}

function Get-GamePathCandidates {
    $folderNames = @(
        "18 Wheels of Steel Hard Truck",
        "Hard Truck 18 Wheels of Steel",
        "Hard Truck",
        "18 Wheels of Steel - Hard Truck"
    )
    foreach ($library in @(Get-SteamLibraryPaths)) {
        try {
            $common = Join-Path $library "steamapps/common"
        }
        catch {
            continue
        }
        foreach ($name in $folderNames) {
            try {
                Join-Path $common $name
            }
            catch {
                continue
            }
        }
    }
    foreach ($root in @(Get-ProgramRoots)) {
        foreach ($name in $folderNames) {
            try {
                Join-Path $root $name
            }
            catch {
                continue
            }
        }
    }
    Get-PreviousManifestPaths
}

function Get-ValidGameCandidates {
    $seen = @{}
    foreach ($candidate in @(Get-GamePathCandidates)) {
        if ([string]::IsNullOrWhiteSpace([string]$candidate)) {
            continue
        }
        try {
            $resolved = Get-GamePath -RequestedPath ([string]$candidate) -NonInteractive
            $key = $resolved.ToLowerInvariant()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $resolved
            }
        }
        catch {
            continue
        }
    }
}

function Get-GamePath {
    param(
        [string]$RequestedPath,
        [switch]$NonInteractive
    )
    if ([string]::IsNullOrWhiteSpace($RequestedPath)) {
        if ($NonInteractive) {
            throw "Noninteractive setup requires a game folder."
        }
        $candidates = @(Get-ValidGameCandidates)
        if ($candidates.Count -eq 1) {
            Write-Host "Detected Hard Truck installation: $($candidates[0])"
            if (Read-YesNo "Use this folder?" -DefaultYes $true) {
                $RequestedPath = $candidates[0]
            }
        }
        elseif ($candidates.Count -gt 1) {
            Write-Host "Detected Hard Truck installations:"
            for ($index = 0; $index -lt $candidates.Count; $index++) {
                Write-Host "$($index + 1). $($candidates[$index])"
            }
            Write-Host "M. Enter a folder manually"
            Write-Host "C. Cancel"
            while ([string]::IsNullOrWhiteSpace($RequestedPath)) {
                $choice = Read-Host "Select an installation"
                if ($null -eq $choice) {
                    throw "Input ended; setup cancelled."
                }
                $choice = $choice.Trim()
                if ($choice -match "^(c|cancel)$") {
                    throw "Setup cancelled."
                }
                if ($choice -match "^(m|manual)$") {
                    break
                }
                $number = 0
                if ($choice -match "^\d+$" -and [int]::TryParse($choice, [ref]$number)) {
                    if ($number -ge 1 -and $number -le $candidates.Count) {
                        $RequestedPath = $candidates[$number - 1]
                        break
                    }
                }
                Write-Host "Select a listed number, M, or C."
            }
        }
        if ([string]::IsNullOrWhiteSpace($RequestedPath)) {
            $RequestedPath = Read-Host "Hard Truck game folder"
            if ($null -eq $RequestedPath) {
                throw "Input ended; setup cancelled."
            }
        }
    }
    $RequestedPath = $RequestedPath.Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $RequestedPath -PathType Container)) {
        throw "Game folder not found: $RequestedPath"
    }
    $resolved = (Get-Item -LiteralPath $RequestedPath).FullName
    if (-not (Test-Path -LiteralPath (Join-Path $resolved "prism3d.exe") -PathType Leaf)) {
        throw "Not a Hard Truck game folder; prism3d.exe is missing: $resolved"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $resolved "game/sys/game.dll") -PathType Leaf)) {
        throw "Not a Hard Truck game folder; game/sys/game.dll is missing: $resolved"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $resolved "sys/dx8.dll") -PathType Leaf)) {
        throw "Not a Hard Truck game folder; sys/dx8.dll is missing: $resolved"
    }
    return $resolved
}

function Read-PayloadManifest {
    $path = Join-Path $script:PayloadRoot "manifest.json"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Installer payload manifest is missing: $path"
    }
    $manifest = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schema_version -ne 3) {
        throw "Unsupported installer payload manifest version."
    }
    if ($null -eq $manifest.components -or @(Get-ComponentRecords $manifest).Count -eq 0) {
        throw "Installer payload manifest has no component metadata."
    }
    return $manifest
}

function Get-SafeRelativePath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or [System.IO.Path]::IsPathRooted($Path)) {
        throw "Unsafe empty or absolute path in installer manifest."
    }
    $normalized = $Path.Replace("\", "/")
    $parts = $normalized.Split("/")
    if ($parts -contains "" -or $parts -contains "." -or $parts -contains ".." -or $normalized.Contains(":")) {
        throw "Unsafe relative path in installer manifest: $Path"
    }
    return $normalized
}

function Test-SafeDestination {
    param(
        [string]$RelativePath,
        $Manifest
    )
    $relative = Get-SafeRelativePath $RelativePath
    if ($relative -eq "game/sys/game.dll" -or $relative -eq "sys/dx8.dll" -or $relative -eq "prism3d.exe") {
        return $relative
    }
    if ($relative.StartsWith("game/usr/")) {
        $payloadRelative = "data/" + $relative.Substring("game/usr/".Length)
        $dataFiles = @($Manifest.data_components.PSObject.Properties | ForEach-Object { $_.Value })
        if ($dataFiles -contains $payloadRelative) {
            return $relative
        }
    }
    throw "Manifest destination is not an installer-owned file: $RelativePath"
}

function Copy-Atomic {
    param(
        [string]$Source,
        [string]$Destination,
        [string]$ExpectedHash
    )
    if (-not (Test-Hash $Source $ExpectedHash)) {
        throw "Source file is missing or has the wrong SHA-256: $Source"
    }
    $parent = Split-Path -Parent $Destination
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    $temporary = Join-Path $parent ("." + [System.IO.Path]::GetFileName($Destination) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        Copy-Item -LiteralPath $Source -Destination $temporary -Force
        if (-not (Test-Hash $temporary $ExpectedHash)) {
            throw "Temporary copy has the wrong SHA-256: $temporary"
        }
        Move-Item -Force -LiteralPath $temporary -Destination $Destination
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Write-Json {
    param(
        [string]$Path,
        $Value
    )
    $json = $Value | ConvertTo-Json -Depth 10
    $parent = Split-Path -Parent $Path
    $temporary = Join-Path $parent ("." + [System.IO.Path]::GetFileName($Path) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [System.IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))
        Move-Item -Force -LiteralPath $temporary -Destination $Path
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function New-BackupDirectory {
    if (-not (Test-Path -LiteralPath $script:BackupRoot -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $script:BackupRoot -Force)
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $candidate = Join-Path $script:BackupRoot $stamp
    $number = 1
    while (Test-Path -LiteralPath $candidate) {
        $candidate = Join-Path $script:BackupRoot ($stamp + "-" + $number)
        $number += 1
    }
    [void](New-Item -ItemType Directory -Path $candidate)
    return $candidate
}

function Rollback-Install {
    param([array]$Changes)
    $errors = @()
    [array]::Reverse($Changes)
    foreach ($entry in $Changes) {
        $destination = Join-Path $script:GamePath $entry.destination
        try {
            if ($entry.existed) {
                if (Test-Hash $destination $entry.original_sha256) {
                    continue
                }
                if ((Test-Path -LiteralPath $destination -PathType Leaf) -and
                    -not (Test-Hash $destination $entry.installed_sha256)) {
                    $errors += "Rollback skipped a file modified during installation: $($entry.destination)"
                    continue
                }
                $backup = Join-Path $script:CurrentBackup $entry.backup_file
                Copy-Atomic $backup $destination $entry.original_sha256
            }
            elseif (Test-Path -LiteralPath $destination -PathType Leaf) {
                if (-not (Test-Hash $destination $entry.installed_sha256)) {
                    $errors += "Rollback skipped a new file modified during installation: $($entry.destination)"
                    continue
                }
                Remove-Item -LiteralPath $destination -Force
            }
        }
        catch {
            $errors += "Rollback failed for $($entry.destination): $($_.Exception.Message)"
        }
    }
    return $errors
}

function Convert-FrozenPath {
    param([string]$Path)
    if ($env:OS -eq "Windows_NT") {
        return $Path
    }
    $converted = & winepath -w $Path
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($converted)) {
        throw "Could not convert staging path with winepath: $Path"
    }
    return $converted.Trim()
}

function Invoke-LifecyclePlan {
    param(
        [hashtable]$Selection,
        $Manifest
    )
    $patcher = Join-Path $script:PayloadRoot "htmod.exe"
    $catalog = Join-Path $script:PayloadRoot "migrations.json"
    if (-not (Test-Hash $patcher $Manifest.payloads."htmod.exe")) {
        throw "Frozen patcher is missing or corrupt: $patcher"
    }
    if (-not (Test-Hash $catalog $Manifest.payloads."migrations.json")) {
        throw "Migration catalog is missing or corrupt: $catalog"
    }
    $arguments = @(
        "plan",
        "--exact",
        "--game-dir", (Convert-FrozenPath $script:GamePath),
        "--migrations", (Convert-FrozenPath $catalog)
    )
    foreach ($property in @(Get-ComponentRecords $Manifest)) {
        if ($Selection[$property.Name]) {
            $arguments += @("--mod", $property.Name)
        }
    }
    if ($env:OS -eq "Windows_NT") {
        $output = @(& $patcher @arguments)
    }
    else {
        $output = @(& wine $patcher @arguments)
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Frozen lifecycle planning failed; no files were changed. $($output -join ' ')"
    }
    try {
        $parsed = @(($output -join [Environment]::NewLine) | ConvertFrom-Json)
    }
    catch {
        throw "Frozen patcher returned an invalid lifecycle plan: $($_.Exception.Message)"
    }
    if ($parsed.Count -ne 1) {
        throw "Frozen patcher did not return exactly one lifecycle plan."
    }
    $plan = $parsed[0]
    if ($plan -isnot [PSCustomObject]) {
        throw "Frozen patcher returned an invalid lifecycle plan: expected a structured object."
    }
    $properties = @($plan.PSObject.Properties.Name)
    foreach ($name in @("schema_version", "mode", "requested", "selected", "no_op", "data_changes", "binary_changes")) {
        if ($properties -notcontains $name) {
            throw "Frozen patcher returned an invalid lifecycle plan: missing '$name'."
        }
    }
    if (-not ($plan.schema_version -is [int] -or $plan.schema_version -is [long]) -or
        $plan.schema_version -ne 1 -or $plan.mode -ne "exact") {
        throw "Frozen patcher returned an unsupported lifecycle plan."
    }
    foreach ($name in @("requested", "selected", "data_changes", "binary_changes")) {
        if ($null -eq $plan.$name -or $plan.$name -isnot [System.Collections.IList]) {
            throw "Frozen patcher returned an invalid lifecycle plan: '$name' must be an array."
        }
    }
    if ($plan.no_op -isnot [bool]) {
        throw "Frozen patcher returned an invalid lifecycle plan: 'no_op' must be Boolean."
    }
    $selectedIds = @()
    if ($null -ne $plan.selected) {
        foreach ($id in @($plan.selected)) {
            if ($id -isnot [string] -or [string]::IsNullOrWhiteSpace($id) -or
                $selectedIds -contains $id -or $null -eq $Manifest.components.PSObject.Properties[$id]) {
                throw "Frozen patcher returned an invalid selected component ID."
            }
            $selectedIds += $id
        }
    }
    $binaryTargets = @{}
    if ($null -ne $plan.binary_changes) {
        foreach ($change in @($plan.binary_changes)) {
            if ($null -eq $change) {
                throw "Frozen patcher returned an invalid binary change."
            }
            $target = [string]$change.target
            $expected = [string]$change.expected_sha256
            if (($target -ne "game/sys/game.dll" -and $target -ne "sys/dx8.dll" -and $target -ne "prism3d.exe") -or
                $binaryTargets.ContainsKey($target) -or $expected -notmatch "^[0-9a-fA-F]{64}$") {
                throw "Frozen patcher returned an invalid binary change."
            }
            $binaryTargets[$target] = $true
        }
    }
    return $plan
}

function Invoke-StagedMods {
    param(
        $Plan,
        $Manifest,
        [array]$BinaryJobs
    )
    $patcher = Join-Path $script:PayloadRoot "htmod.exe"
    $catalog = Join-Path $script:PayloadRoot "migrations.json"
    if (-not (Test-Hash $patcher $Manifest.payloads."htmod.exe")) {
        throw "Frozen patcher is missing or corrupt: $patcher"
    }
    if (-not (Test-Hash $catalog $Manifest.payloads."migrations.json")) {
        throw "Migration catalog is missing or corrupt: $catalog"
    }
    $stageRoot = Join-Path $script:InstallerRoot ("staging-" + [Guid]::NewGuid().ToString("N"))
    try {
        $arguments = @(
            "stage",
            "--game-dir", (Convert-FrozenPath $script:GamePath),
            "--output-dir", (Convert-FrozenPath $stageRoot),
            "--migrations", (Convert-FrozenPath $catalog)
        )
        if ($null -ne $Plan.selected) {
            foreach ($id in @($Plan.selected)) {
                $arguments += @("--mod", $id)
            }
        }
        foreach ($job in $BinaryJobs) {
            $arguments += @("--expect", "$($job.relative)=$($job.hash)")
        }
        Write-Host "Staging selected binary components locally; no original game binary payloads are shipped."
        if ($env:OS -eq "Windows_NT") {
            & $patcher @arguments | Write-Host
        }
        else {
            & wine $patcher @arguments | Write-Host
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Frozen patcher staging failed; no files were changed."
        }
        foreach ($job in $BinaryJobs) {
            $staged = Join-Path $stageRoot $job.relative
            if (-not (Test-Hash $staged $job.hash)) {
                throw "Staged binary hash verification failed; no files were changed."
            }
        }
        if ($null -ne $Plan.selected -and @($Plan.selected) -contains "config") {
            $renderArguments = @(
                "render-quick-setup",
                "--output-dir", (Convert-FrozenPath $stageRoot)
            )
            foreach ($id in @($Plan.selected)) {
                $renderArguments += @("--mod", $id)
            }
            if ($env:OS -eq "Windows_NT") {
                & $patcher @renderArguments | Write-Host
            }
            else {
                & wine $patcher @renderArguments | Write-Host
            }
            if ($LASTEXITCODE -ne 0) {
                throw "Frozen Quick Setup rendering failed; no files were changed."
            }
            if (-not (Test-Path -LiteralPath (Join-Path $stageRoot "game/usr/ui/cosetup.sc") -PathType Leaf)) {
                throw "Frozen Quick Setup rendering did not produce ui/cosetup.sc."
            }
        }
        return $stageRoot
    }
    catch {
        if (Test-Path -LiteralPath $stageRoot) {
            Remove-Item -LiteralPath $stageRoot -Recurse -Force
        }
        throw
    }
}

function Install-Mods {
    param(
        $Manifest,
        $Plan
    )
    $binaryChanges = @{}
    if ($null -ne $plan.binary_changes) {
        foreach ($change in @($plan.binary_changes)) {
            $binaryChanges[[string]$change.target] = $change
        }
    }
    $jobs = @()
    foreach ($relative in @("game/sys/game.dll", "sys/dx8.dll", "prism3d.exe")) {
        $destination = Join-Path $script:GamePath $relative
        $hash = if ($binaryChanges.ContainsKey($relative)) {
            [string]$binaryChanges[$relative].expected_sha256
        }
        else {
            (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        $jobs += @{
            source = $null
            destination = $destination
            relative = $relative
            hash = $hash
        }
    }
    $binaryJobs = @($jobs)
    $selectedIds = @($plan.selected)
    foreach ($property in @(Get-ComponentRecords $Manifest)) {
        $component = $property.Name
        if ($property.Value.kind -ne "data" -or $selectedIds -notcontains $component) {
            continue
        }
        foreach ($payloadRelative in $Manifest.data_components.$component) {
            $relative = "game/usr/" + $payloadRelative.Substring("data/".Length)
            $jobs += @{
                source = Join-Path $script:PayloadRoot $payloadRelative
                destination = Join-Path $script:GamePath $relative
                relative = $relative
                hash = $Manifest.payloads.$payloadRelative
            }
        }
    }
    foreach ($job in $jobs) {
        if ($null -ne $job.source -and -not (Test-Hash $job.source $job.hash)) {
            throw "Installer payload is missing or corrupt: $($job.source)"
        }
    }

    $stageRoot = $null
    $script:CurrentBackup = $null
    $changes = @()
    $entries = @()
    try {
        $stageRoot = Invoke-StagedMods $plan $Manifest $binaryJobs
        $jobs[0].source = Join-Path $stageRoot "game/sys/game.dll"
        $jobs[1].source = Join-Path $stageRoot "sys/dx8.dll"
        $jobs[2].source = Join-Path $stageRoot "prism3d.exe"
        if ($selectedIds -contains "config") {
            foreach ($job in $jobs) {
                if ($job.relative -eq "game/usr/ui/cosetup.sc") {
                    $job.source = Join-Path $stageRoot "game/usr/ui/cosetup.sc"
                    $job.hash = (Get-FileHash -LiteralPath $job.source -Algorithm SHA256).Hash.ToLowerInvariant()
                }
            }
        }
        $script:CurrentBackup = New-BackupDirectory
        foreach ($job in $jobs) {
            if ((Test-Path -LiteralPath $job.destination) -and
                -not (Test-Path -LiteralPath $job.destination -PathType Leaf)) {
                throw "Destination is not a regular file: $($job.destination)"
            }
            if (Test-Hash $job.destination $job.hash) {
                Write-Host "Already correct: $($job.relative)"
                continue
            }
            $entry = [ordered]@{
                destination = $job.relative
                installed_sha256 = $job.hash
                existed = $false
                backup_file = $null
                original_sha256 = $null
            }
            if (Test-Path -LiteralPath $job.destination -PathType Leaf) {
                $entry.existed = $true
                $entry.original_sha256 = (Get-FileHash -LiteralPath $job.destination -Algorithm SHA256).Hash.ToLowerInvariant()
                $entry.backup_file = "files/" + $job.relative
                $backup = Join-Path $script:CurrentBackup $entry.backup_file
                [void](New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force)
                Copy-Item -LiteralPath $job.destination -Destination $backup
                if (-not (Test-Hash $backup $entry.original_sha256)) {
                    throw "Backup verification failed: $backup"
                }
            }
            $changes += $entry
            $entries += $entry
            Copy-Atomic $job.source $job.destination $job.hash
            Write-Host "Installed: $($job.relative)"
        }
        $installation = [ordered]@{
            schema_version = 1
            status = "Installed"
            created = (Get-Date).ToString("o")
            game_path = $script:GamePath
            entries = $entries
        }
        Write-Json (Join-Path $script:CurrentBackup "installation.json") $installation
        Write-Host "Backup and installation manifest: $script:CurrentBackup"
    }
    catch {
        $originalError = $_.Exception.Message
        $rollbackErrors = @(Rollback-Install $changes)
        $status = if ($rollbackErrors.Count -eq 0) { "RolledBack" } else { "RollbackFailed" }
        $failure = [ordered]@{
            schema_version = 1
            status = $status
            created = (Get-Date).ToString("o")
            game_path = $script:GamePath
            error = $originalError
            rollback_errors = $rollbackErrors
            entries = $entries
        }
        try {
            Write-Json (Join-Path $script:CurrentBackup "installation.json") $failure
        }
        catch {
            Write-Warning "Could not write the failure manifest: $($_.Exception.Message)"
        }
        if ($rollbackErrors.Count -gt 0) {
            throw "Installation failed and rollback was incomplete: $originalError; $($rollbackErrors -join '; ')"
        }
        throw "Installation failed; all files changed by this run were restored. $originalError"
    }
    finally {
        if ($null -ne $stageRoot -and (Test-Path -LiteralPath $stageRoot)) {
            Remove-Item -LiteralPath $stageRoot -Recurse -Force
        }
    }
}

function Uninstall-Mods {
    param($Manifest)
    if (-not (Test-Path -LiteralPath $script:BackupRoot -PathType Container)) {
        throw "No installer backups were found; nothing to uninstall."
    }
    $history = @()
    foreach ($file in (Get-ChildItem -LiteralPath $script:BackupRoot -Filter installation.json -Recurse -File | Sort-Object FullName -Descending)) {
        $candidate = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        if (($candidate.status -eq "Installed" -or $candidate.status -eq "UninstallPartial" -or $candidate.status -eq "RollbackFailed") -and $candidate.entries.Count -gt 0 -and $candidate.game_path -ieq $script:GamePath) {
            $items = @()
            foreach ($entry in $candidate.entries) {
                $relative = Test-SafeDestination $entry.destination $Manifest
                if ($entry.installed_sha256 -notmatch "^[0-9a-fA-F]{64}$") {
                    throw "Invalid installed hash in backup manifest: $($file.FullName)"
                }
                $destination = Join-Path $script:GamePath $relative
                $backup = $null
                if ($entry.existed) {
                    if ($entry.original_sha256 -notmatch "^[0-9a-fA-F]{64}$") {
                        throw "Invalid original hash in backup manifest: $($file.FullName)"
                    }
                    $backupRelative = Get-SafeRelativePath $entry.backup_file
                    $backup = Join-Path (Split-Path -Parent $file.FullName) $backupRelative
                    if (-not (Test-Hash $backup $entry.original_sha256)) {
                        throw "Backup is missing or corrupt for $relative"
                    }
                }
                $items += @{
                    entry = $entry
                    destination = $destination
                    relative = $relative
                    backup = $backup
                }
            }
            $history += @{
                path = $file.FullName
                manifest = $candidate
                items = $items
            }
        }
    }
    if ($history.Count -eq 0) {
        throw "No installed backup manifest was found for this game folder."
    }

    $blockedDestinations = @{}
    foreach ($run in $history) {
        $skipped = @()
        foreach ($item in $run.items) {
            if ($blockedDestinations.ContainsKey($item.relative)) {
                $skipped += $item.relative
                continue
            }
            $entry = $item.entry
            $destination = $item.destination
            if ($entry.existed -and (Test-Hash $destination $entry.original_sha256)) {
                Write-Host "Already restored: $($item.relative)"
                continue
            }
            if (-not $entry.existed -and -not (Test-Path -LiteralPath $destination)) {
                Write-Host "Already removed: $($item.relative)"
                continue
            }
            if (-not (Test-Hash $destination $entry.installed_sha256)) {
                Write-Warning "Skipping user-modified or missing file: $($item.relative)"
                $blockedDestinations[$item.relative] = $true
                $skipped += $item.relative
                continue
            }
            if ($entry.existed) {
                Copy-Atomic $item.backup $destination $entry.original_sha256
                Write-Host "Restored: $($item.relative)"
            }
            else {
                Remove-Item -LiteralPath $destination -Force
                Write-Host "Removed installer-created file: $($item.relative)"
            }
        }
        $run.manifest.status = if ($skipped.Count -eq 0) { "Uninstalled" } else { "UninstallPartial" }
        $run.manifest | Add-Member -Force -NotePropertyName uninstalled -NotePropertyValue (Get-Date).ToString("o")
        $run.manifest | Add-Member -Force -NotePropertyName skipped -NotePropertyValue $skipped
        Write-Json $run.path $run.manifest
        Write-Host "Uninstall manifest updated: $($run.path)"
    }
}

function Protect-ReportAddresses {
    param([string]$Text)
    $ipv6Candidate = '(?<![0-9A-Fa-f:.])[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*(?![0-9A-Fa-f:])'
    $result = [regex]::Replace($Text, $ipv6Candidate, {
        param($match)
        $value = $match.Value
        $suffix = ""
        while ($value.EndsWith(".")) {
            $value = $value.Substring(0, $value.Length - 1)
            $suffix += "."
        }
        $address = $null
        if ([System.Net.IPAddress]::TryParse($value, [ref]$address) -and
            $address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetworkV6) {
            return "<IP>" + $suffix
        }
        return $match.Value
    })
    $octet = '(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)'
    $ipv4Pattern = '(?<![0-9.])' + $octet + '(?:\.' + $octet + '){3}(?![0-9.])'
    return [regex]::Replace($result, $ipv4Pattern, "<IP>")
}

function Protect-ReportText {
    param(
        [AllowNull()]
        [string]$Text,
        [string]$GamePath
    )
    if ($null -eq $Text) {
        return ""
    }
    $pairs = @(
        @{ Value = $GamePath; Replacement = "<GAME>" },
        @{ Value = $script:PackageRoot; Replacement = "<PACKAGE>" },
        @{ Value = [Environment]::GetFolderPath("UserProfile"); Replacement = "<HOME>" },
        @{ Value = [Environment]::GetEnvironmentVariable("USERPROFILE"); Replacement = "<HOME>" },
        @{ Value = [Environment]::GetEnvironmentVariable("HOME"); Replacement = "<HOME>" },
        @{ Value = [Environment]::UserName; Replacement = "<USER>" },
        @{ Value = [Environment]::GetEnvironmentVariable("USERNAME"); Replacement = "<USER>" },
        @{ Value = [Environment]::GetEnvironmentVariable("USER"); Replacement = "<USER>" },
        @{ Value = [Environment]::MachineName; Replacement = "<COMPUTER>" },
        @{ Value = [Environment]::GetEnvironmentVariable("COMPUTERNAME"); Replacement = "<COMPUTER>" }
    )
    $result = $Text
    foreach ($pair in ($pairs | Sort-Object { ([string]$_.Value).Length } -Descending)) {
        $value = [string]$pair.Value
        if ($value.Length -eq 0) {
            continue
        }
        foreach ($variant in @($value, $value.Replace("\", "/"), $value.Replace("/", "\"))) {
            $result = [regex]::Replace(
                $result,
                [regex]::Escape($variant),
                [string]$pair.Replacement,
                [Text.RegularExpressions.RegexOptions]::IgnoreCase)
        }
    }
    return Protect-ReportAddresses $result
}

function Get-ReportComponents {
    param(
        $Manifest,
        [string]$GamePath
    )
    $ids = @{}
    $quickSetupHashes = @{}
    if (-not (Test-Path -LiteralPath $script:BackupRoot -PathType Container)) {
        return @()
    }
    $files = @(Get-ChildItem -LiteralPath $script:BackupRoot -Filter installation.json -Recurse -File | Sort-Object FullName -Descending)
    foreach ($file in $files) {
        try {
            $installation = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            if (@("Installed", "UninstallPartial", "RollbackFailed") -notcontains $installation.status -or
                $installation.game_path -ine $GamePath) {
                continue
            }
            foreach ($entry in @($installation.entries)) {
                if ($installation.status -eq "UninstallPartial" -and
                    @($installation.skipped) -notcontains [string]$entry.destination) {
                    continue
                }
                $relative = Test-SafeDestination ([string]$entry.destination) $Manifest
                if ($installation.status -eq "RollbackFailed") {
                    $destination = Join-Path $GamePath $relative
                    if (-not (Test-Hash $destination ([string]$entry.installed_sha256))) {
                        $restored = if ($entry.existed) {
                            Test-Hash $destination ([string]$entry.original_sha256)
                        }
                        else {
                            -not (Test-Path -LiteralPath $destination)
                        }
                        if ($restored) {
                            continue
                        }
                    }
                }
                if ($relative -eq "game/sys/game.dll") {
                    foreach ($variant in @($Manifest.game_variants.PSObject.Properties)) {
                        if ($variant.Value.sha256 -ieq $entry.installed_sha256) {
                            foreach ($id in @($variant.Value.components)) {
                                $ids[$id] = $true
                            }
                        }
                    }
                }
                elseif ($relative -eq "sys/dx8.dll") {
                    if ($Manifest.dx8.vsync.sha256 -ieq $entry.installed_sha256) {
                        $ids["dx8_vsync"] = $true
                    }
                }
                elseif ($relative -eq "prism3d.exe") {
                    if ($Manifest.prism3d.cap.sha256 -ieq $entry.installed_sha256) {
                        $ids["frame_cap"] = $true
                    }
                }
                else {
                    if ($relative -eq "game/usr/ui/cosetup.sc") {
                        $quickSetupHashes[[string]$entry.installed_sha256] = $true
                    }
                    foreach ($component in @($Manifest.data_components.PSObject.Properties)) {
                        foreach ($payload in @($component.Value)) {
                            if ($relative -eq ("game/usr/" + $payload.Substring("data/".Length))) {
                                $ids[$component.Name] = $true
                            }
                        }
                    }
                }
            }
        }
        catch {
            continue
        }
    }

    $currentGameHash = (Get-FileHash -LiteralPath (Join-Path $GamePath "game/sys/game.dll") -Algorithm SHA256).Hash
    $currentGameVariant = $null
    foreach ($variant in @($Manifest.game_variants.PSObject.Properties)) {
        if ($variant.Value.sha256 -ieq $currentGameHash) {
            $currentGameVariant = $variant.Value
            break
        }
    }
    $components = @()
    foreach ($id in @($ids.Keys | Sort-Object)) {
        if ($null -eq $Manifest.components.PSObject.Properties[$id]) {
            continue
        }
        $metadata = $Manifest.components.$id
        $installed = $false
        if ($metadata.kind -eq "data") {
            $payloads = @($Manifest.data_components.$id)
            $installed = $payloads.Count -gt 0
            foreach ($payload in $payloads) {
                $relative = "game/usr/" + $payload.Substring("data/".Length)
                $destination = Join-Path $GamePath $relative
                $matching = Test-Hash $destination ([string]$Manifest.payloads.$payload)
                if (-not $matching -and $payload -eq "data/ui/cosetup.sc") {
                    foreach ($expected in @($quickSetupHashes.Keys)) {
                        if (Test-Hash $destination ([string]$expected)) {
                            $matching = $true
                            break
                        }
                    }
                }
                if (-not $matching) {
                    $installed = $false
                    break
                }
            }
        }
        elseif ($metadata.target -eq "game/sys/game.dll") {
            $installed = $null -ne $currentGameVariant -and @($currentGameVariant.components) -contains $id
        }
        elseif ($id -eq "dx8_vsync") {
            $installed = Test-Hash (Join-Path $GamePath "sys/dx8.dll") ([string]$Manifest.dx8.vsync.sha256)
        }
        elseif ($id -eq "frame_cap") {
            $installed = Test-Hash (Join-Path $GamePath "prism3d.exe") ([string]$Manifest.prism3d.cap.sha256)
        }
        $components += [ordered]@{
            id = $id
            status = if ($installed) { "installed" } else { "partial" }
            version = if ($installed) { [string]$metadata.version } else { $null }
        }
    }
    return $components
}

function Get-ReportGpu {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        return @()
    }
    try {
        $items = @()
        foreach ($controller in @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop)) {
            $items += [ordered]@{
                name = [string]$controller.Name
                driver_version = [string]$controller.DriverVersion
            }
        }
        return $items
    }
    catch {
        return @()
    }
}

function Add-ReportTextEntry {
    param(
        $Archive,
        [string]$Name,
        [string]$Text,
        [string]$GamePath
    )
    $entry = $Archive.CreateEntry($Name)
    $stream = $entry.Open()
    $writer = New-Object System.IO.StreamWriter($stream, (New-Object System.Text.UTF8Encoding($false)))
    try {
        $writer.Write((Protect-ReportText $Text $GamePath))
    }
    finally {
        $writer.Dispose()
    }
}

function Get-UniqueReportPath {
    param([string]$Directory)
    $base = "HardTruckCrashReport-" + [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss")
    $candidate = Join-Path $Directory ($base + ".zip")
    $number = 1
    while (Test-Path -LiteralPath $candidate) {
        $candidate = Join-Path $Directory ($base + "-" + $number + ".zip")
        $number += 1
    }
    return $candidate
}

function New-CrashReport {
    param(
        $Manifest,
        [string]$GamePath,
        $ExitCode = $null,
        $Launch = $null
    )
    $crashPath = Join-Path $GamePath "prism3d.crash"
    $logPath = Join-Path $GamePath "prism3d.log"
    $crashPresent = Test-Path -LiteralPath $crashPath -PathType Leaf
    $logPresent = Test-Path -LiteralPath $logPath -PathType Leaf
    $crashText = $null
    $logText = $null
    if ($crashPresent) {
        $crashText = [System.IO.File]::ReadAllText($crashPath)
    }
    if ($logPresent) {
        $logText = (@(Get-Content -LiteralPath $logPath -Tail 4000 -Encoding UTF8) -join [Environment]::NewLine)
    }

    $report = [ordered]@{
        schema_version = 1
        created_utc = [DateTime]::UtcNow.ToString("o")
        exit_code = $ExitCode
        game_hashes = [ordered]@{
            "prism3d.exe" = (Get-FileHash -LiteralPath (Join-Path $GamePath "prism3d.exe") -Algorithm SHA256).Hash.ToLowerInvariant()
            "game/sys/game.dll" = (Get-FileHash -LiteralPath (Join-Path $GamePath "game/sys/game.dll") -Algorithm SHA256).Hash.ToLowerInvariant()
            "sys/dx8.dll" = (Get-FileHash -LiteralPath (Join-Path $GamePath "sys/dx8.dll") -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        payload_manifest_schema = $Manifest.schema_version
        components = @(Get-ReportComponents $Manifest $GamePath)
        os = [ordered]@{
            platform = [string][Environment]::OSVersion.Platform
            version = [string][Environment]::OSVersion.VersionString
        }
        gpu = @(Get-ReportGpu)
        logs = [ordered]@{
            "prism3d.crash" = $crashPresent
            "prism3d.log" = $logPresent
        }
    }
    if ($null -ne $Launch) {
        foreach ($key in @($Launch.Keys)) {
            $report[$key] = $Launch[$key]
        }
    }
    $reportJson = $report | ConvertTo-Json -Depth 10
    $readme = @"
Hard Truck Modern crash report

This archive contains sanitized Prism3D crash/log text, installation and system metadata, and this README.
The game log is limited to its last 4000 lines. Paths, user names, and computer names are replaced with placeholders.
It does not contain saves, profiles, backups, memory dumps, registry exports, dxdiag, MSInfo, or arbitrary documents.
Nothing was uploaded. Review the archive and share it only with someone you trust when requesting support.
"@

    Add-Type -AssemblyName System.IO.Compression
    $memory = New-Object System.IO.MemoryStream
    try {
        $archive = New-Object System.IO.Compression.ZipArchive($memory, [System.IO.Compression.ZipArchiveMode]::Create, $true)
        try {
            if ($null -ne $crashText) {
                Add-ReportTextEntry $archive "prism3d.crash" $crashText $GamePath
            }
            if ($null -ne $logText) {
                Add-ReportTextEntry $archive "prism3d.log" $logText $GamePath
            }
            Add-ReportTextEntry $archive "report.json" $reportJson $GamePath
            Add-ReportTextEntry $archive "README.txt" $readme $GamePath
        }
        finally {
            $archive.Dispose()
        }
        $bytes = $memory.ToArray()
    }
    finally {
        $memory.Dispose()
    }

    $desktop = [Environment]::GetEnvironmentVariable("HARDTRUCK_DESKTOP_PATH")
    if ([string]::IsNullOrWhiteSpace($desktop)) {
        $desktop = [Environment]::GetFolderPath("Desktop")
    }
    $directories = @()
    if (-not [string]::IsNullOrWhiteSpace($desktop)) {
        $directories += $desktop
    }
    $directories += $script:PackageRoot
    $lastError = $null
    foreach ($directory in $directories) {
        try {
            if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
                [void](New-Item -ItemType Directory -Path $directory -Force)
            }
            $destination = Get-UniqueReportPath $directory
            [System.IO.File]::WriteAllBytes($destination, $bytes)
            Write-Host "Crash report created: $destination"
            return $destination
        }
        catch {
            $lastError = $_
        }
    }
    throw "Could not create the crash report: $($lastError.Exception.Message)"
}

try {
    $effectiveAction = Resolve-SetupAction $Action -NonInteractive:$NonInteractive
    $script:SelectedAction = $effectiveAction
    if ($effectiveAction -eq "Quit") {
        if ($Action -eq "Menu") { exit 2 } else { exit 0 }
    }
    $manifest = Read-PayloadManifest
    $selection = Get-Selection $effectiveAction $Components -NonInteractive:$NonInteractive -Manifest $manifest
    $script:GamePath = Get-GamePath $GamePath -NonInteractive:$NonInteractive
    if ($effectiveAction -eq "CrashReport") {
        if ((Read-YesNo "Create crash report from engine logs?" -DefaultYes $true -NonInteractive:$NonInteractive)) {
            New-CrashReport $manifest $script:GamePath
        }
        if ($Action -eq "Menu") { exit 2 } else { exit 0 }
    }
    if (-not $NonInteractive) {
        $confirmed = Read-YesNo "Confirm that the game is closed before setup continues" -DefaultYes $false
        if (-not $confirmed) {
            throw "Setup cancelled; close the game before retrying."
        }
    }
    if ($script:SelectedAction -eq "Uninstall") {
        Uninstall-Mods $manifest
    }
    else {
        $plan = Invoke-LifecyclePlan $selection $manifest
        $newProfile = $false
        foreach ($property in @(Get-ComponentRecords $manifest)) {
            if (@($plan.selected) -contains $property.Name -and $property.Value.new_profile) {
                $newProfile = $true
                break
            }
        }
        if ($newProfile) {
            Write-Warning "Selected components make existing saves incompatible; create a new profile after installation."
        }
        Install-Mods $manifest $plan
    }
    if ($Action -eq "Menu") { exit 2 } else { exit 0 }
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
