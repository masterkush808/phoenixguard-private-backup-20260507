[CmdletBinding()]
param(
    [string]$ExtensionPath = '',
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($ExtensionPath)) {
    $ExtensionPath = $PSScriptRoot
}
$issues = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()
$referencedFiles = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)

try {
    $extensionRoot = (Resolve-Path -LiteralPath $ExtensionPath -ErrorAction Stop).Path
} catch {
    $extensionRoot = [System.IO.Path]::GetFullPath($ExtensionPath)
    $issues.Add("Extension directory does not exist: $extensionRoot")
}

$manifestPath = Join-Path -Path $extensionRoot -ChildPath 'manifest.json'
$manifest = $null
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    $issues.Add("MV3 manifest is missing: $manifestPath")
} else {
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
            ConvertFrom-Json -ErrorAction Stop
    } catch {
        $issues.Add("manifest.json is not valid JSON: $($_.Exception.Message)")
    }
}

$extensionName = ''
$extensionVersion = ''
$permissionNames = @()
$hostScopes = @()
if ($null -ne $manifest) {
    $extensionName = [string]$manifest.name
    $extensionVersion = [string]$manifest.version
    if ([int]$manifest.manifest_version -ne 3) {
        $issues.Add('manifest_version must be 3.')
    }
    if ([string]::IsNullOrWhiteSpace($extensionName)) {
        $issues.Add('Extension name is missing.')
    }
    if ($extensionVersion -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?$') {
        $issues.Add('Extension version must use three or four numeric components.')
    }

    $permissionNames = @($manifest.permissions | ForEach-Object { [string]$_ })
    $requiredPermissions = @('activeTab', 'offscreen', 'storage', 'tabCapture')
    $allowedPermissions = @('activeTab', 'offscreen', 'storage', 'tabCapture')
    foreach ($requiredPermission in $requiredPermissions) {
        if ($requiredPermission -notin $permissionNames) {
            $issues.Add("Required MV3 permission is missing: $requiredPermission")
        }
    }
    foreach ($permissionName in $permissionNames) {
        if ($permissionName -notin $allowedPermissions) {
            $issues.Add("Unexpected extension permission: $permissionName")
        }
    }

    $hostScopes = @($manifest.host_permissions | ForEach-Object { [string]$_ })
    $allowedHostScopes = @(
        'https://pocketoption.com/*',
        'https://*.pocketoption.com/*',
        'http://127.0.0.1/*',
        'http://localhost/*'
    )
    foreach ($hostScope in $hostScopes) {
        if ($hostScope -notin $allowedHostScopes) {
            $issues.Add("Unexpected host permission: $hostScope")
        }
    }
    if (-not ($hostScopes | Where-Object { $_ -like 'https://*pocketoption.com/*' })) {
        $issues.Add('An HTTPS Pocket Option host permission is required.')
    }
    if (-not ($hostScopes | Where-Object { $_ -in @('http://127.0.0.1/*', 'http://localhost/*') })) {
        $issues.Add('A loopback-only PhoenixGuard host permission is required.')
    }
    if ($hostScopes -contains '<all_urls>') {
        $issues.Add('<all_urls> is forbidden for this extension.')
    }
    if ($null -ne $manifest.externally_connectable) {
        $issues.Add('externally_connectable is not allowed.')
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$manifest.update_url)) {
        $issues.Add('An unpacked local extension must not declare update_url.')
    }

    if ($null -ne $manifest.background) {
        $worker = [string]$manifest.background.service_worker
        if (-not [string]::IsNullOrWhiteSpace($worker)) {
            [void]$referencedFiles.Add($worker)
        }
    }
    if ($null -ne $manifest.action) {
        $popup = [string]$manifest.action.default_popup
        if (-not [string]::IsNullOrWhiteSpace($popup)) {
            [void]$referencedFiles.Add($popup)
        }
    }
    if ($null -ne $manifest.options_ui) {
        $optionsPage = [string]$manifest.options_ui.page
        if (-not [string]::IsNullOrWhiteSpace($optionsPage)) {
            [void]$referencedFiles.Add($optionsPage)
        }
    }
    $legacyOptionsPage = [string]$manifest.options_page
    if (-not [string]::IsNullOrWhiteSpace($legacyOptionsPage)) {
        [void]$referencedFiles.Add($legacyOptionsPage)
    }
    foreach ($contentScript in @($manifest.content_scripts)) {
        foreach ($scriptPath in @($contentScript.js)) {
            [void]$referencedFiles.Add([string]$scriptPath)
        }
        foreach ($stylePath in @($contentScript.css)) {
            [void]$referencedFiles.Add([string]$stylePath)
        }
    }
}

if (Test-Path -LiteralPath $extensionRoot -PathType Container) {
    foreach ($requiredRuntimeFile in @('common.js', 'service_worker.js', 'offscreen.html', 'offscreen.js', 'options.html', 'options.js')) {
        [void]$referencedFiles.Add($requiredRuntimeFile)
    }

    $rootPrefix = $extensionRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    foreach ($relativePath in $referencedFiles) {
        if ([string]::IsNullOrWhiteSpace($relativePath)) {
            continue
        }
        if ([System.IO.Path]::IsPathRooted($relativePath)) {
            $issues.Add("Extension file reference must be relative: $relativePath")
            continue
        }
        try {
            $candidatePath = [System.IO.Path]::GetFullPath(
                (Join-Path -Path $extensionRoot -ChildPath ($relativePath -replace '/', '\'))
            )
        } catch {
            $issues.Add("Extension file reference is invalid: $relativePath")
            continue
        }
        if (-not $candidatePath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $issues.Add("Extension file escapes its directory: $relativePath")
            continue
        }
        if (-not (Test-Path -LiteralPath $candidatePath -PathType Leaf)) {
            $issues.Add("Referenced extension file is missing: $relativePath")
        }
    }

    $edgeUserData = Join-Path -Path $env:LOCALAPPDATA -ChildPath 'Microsoft\Edge\User Data'
    if (
        -not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA) -and
        $extensionRoot.StartsWith($edgeUserData, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        $issues.Add('Keep unpacked source outside the Edge user-data directory.')
    }

    foreach ($sourceFile in Get-ChildItem -LiteralPath $extensionRoot -File -Recurse -ErrorAction SilentlyContinue) {
        if ($sourceFile.Extension -notin @('.js', '.mjs', '.html')) {
            continue
        }
        $sourceText = Get-Content -LiteralPath $sourceFile.FullName -Raw -Encoding UTF8
        if ($sourceText -match '(?i)\beval\s*\(' -or $sourceText -match '(?i)new\s+Function\s*\(') {
            $issues.Add("Dynamic code execution is forbidden: $($sourceFile.Name)")
        }
        if ($sourceText -match '(?i)<script[^>]+src\s*=\s*["'']https?://') {
            $issues.Add("Remote script loading is forbidden: $($sourceFile.Name)")
        }
    }
}

$fileHashes = @()
if (Test-Path -LiteralPath $extensionRoot -PathType Container) {
    $fileHashes = @(
        Get-ChildItem -LiteralPath $extensionRoot -File -Recurse |
            Sort-Object FullName |
            ForEach-Object {
                [ordered]@{
                    path = $_.FullName.Substring($extensionRoot.Length).TrimStart('\', '/') -replace '\\', '/'
                    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                }
            }
    )
}

$result = [ordered]@{
    schema_version = 'PG_EDGE_EXTENSION_READINESS_V1'
    ready = ($issues.Count -eq 0)
    extension_root = $extensionRoot
    manifest_path = $manifestPath
    manifest_version = if ($null -ne $manifest) { [int]$manifest.manifest_version } else { 0 }
    name = $extensionName
    version = $extensionVersion
    permissions = $permissionNames
    host_permissions = $hostScopes
    issues = @($issues)
    warnings = @($warnings)
    files = $fileHashes
    safety = [ordered]@{
        edge_launched = $false
        profiles_modified = $false
        policies_modified = $false
        registry_modified = $false
        extension_installed = $false
    }
}

if ($Json) {
    $result | ConvertTo-Json -Depth 8
} else {
    $status = if ($result.ready) { 'PASS' } else { 'FAIL' }
    Write-Output "PhoenixGuard Edge extension readiness: $status"
    Write-Output "  Root: $extensionRoot"
    Write-Output "  Manifest: $extensionName $extensionVersion (MV$($result.manifest_version))"
    Write-Output "  Scope: Pocket Option HTTPS + local PhoenixGuard loopback only"
    Write-Output "  Side effects: none; Edge was not launched and no profile or policy was changed"
    foreach ($issue in $issues) {
        Write-Output "  ERROR: $issue"
    }
    foreach ($warning in $warnings) {
        Write-Output "  WARNING: $warning"
    }
}

if (-not $result.ready) {
    exit 1
}
