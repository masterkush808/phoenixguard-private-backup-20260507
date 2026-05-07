[CmdletBinding()]
param(
    [string]$ApiToken = $(if ($env:CLOUDFLARE_API_TOKEN) { $env:CLOUDFLARE_API_TOKEN } else { '' }),
    [string]$AccountId = $(if ($env:PHOENIXGUARD_CLOUDFLARE_ACCOUNT_ID) { $env:PHOENIXGUARD_CLOUDFLARE_ACCOUNT_ID } else { '' }),
    [string]$TunnelName = $(if ($env:PHOENIXGUARD_CLOUDFLARE_TUNNEL_NAME) { $env:PHOENIXGUARD_CLOUDFLARE_TUNNEL_NAME } else { '808fx-standard-system-hybrid' }),
    [string]$ZoneName = $(if ($env:PHOENIXGUARD_CLOUDFLARE_ZONE) { $env:PHOENIXGUARD_CLOUDFLARE_ZONE } else { '' }),
    [string]$Hostname = $(if ($env:PHOENIXGUARD_CLOUDFLARE_HOSTNAME) { $env:PHOENIXGUARD_CLOUDFLARE_HOSTNAME } else { '' }),
    [string]$ServiceUrl = $(if ($env:PHOENIXGUARD_CLOUDFLARE_SERVICE_URL) { $env:PHOENIXGUARD_CLOUDFLARE_SERVICE_URL } else { 'http://127.0.0.1:7861' }),
    [string[]]$AccessEmails = $(if ($env:PHOENIXGUARD_CLOUDFLARE_ACCESS_EMAILS) { @($env:PHOENIXGUARD_CLOUDFLARE_ACCESS_EMAILS -split '\s*,\s*' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) } else { @() }),
    [string]$ConfigPath = '',
    [switch]$InstallService,
    [switch]$ReinstallService,
    [switch]$WriteConfig,
    [switch]$ConfigureAccess,
    [switch]$EmitJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ApiBase = 'https://api.cloudflare.com/client/v4'
$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $ConfigPath) {
    $ConfigPath = Join-Path -Path $script:ScriptRoot -ChildPath 'phoenixguard.vm-share.env.ps1'
}

function Invoke-CfApi {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('GET', 'POST', 'PUT', 'PATCH', 'DELETE')]
        [string]$Method,

        [Parameter(Mandatory = $true)]
        [string]$Path,

        [object]$Body = $null
    )

    if (-not $ApiToken) {
        throw 'Set CLOUDFLARE_API_TOKEN or pass -ApiToken before running Cloudflare setup.'
    }

    $headers = @{
        Authorization = "Bearer $ApiToken"
    }

    $invokeParams = @{
        Method      = $Method
        Headers     = $headers
        Uri         = "$script:ApiBase$Path"
        ErrorAction = 'Stop'
    }

    if ($null -ne $Body) {
        $invokeParams['ContentType'] = 'application/json'
        $invokeParams['Body'] = ($Body | ConvertTo-Json -Depth 20 -Compress)
    }

    $response = Invoke-RestMethod @invokeParams
    if (-not $response.success) {
        $message = ($response.errors | ForEach-Object { $_.message }) -join '; '
        if (-not $message) {
            $message = 'Cloudflare API request failed.'
        }
        throw $message
    }
    return $response.result
}

function Get-TunnelSecret {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes)
}

function Get-AccountId {
    if ($AccountId) {
        return $AccountId
    }
    throw 'Pass -AccountId or set PHOENIXGUARD_CLOUDFLARE_ACCOUNT_ID.'
}

function Verify-Token {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedAccountId
    )

    return Invoke-CfApi -Method GET -Path "/accounts/$ResolvedAccountId/tokens/verify"
}

function Get-ZoneList {
    $result = Invoke-CfApi -Method GET -Path '/zones'
    if ($null -eq $result) {
        return @()
    }
    return @($result)
}

function Resolve-Zone {
    param(
        [AllowEmptyCollection()]
        [object[]]$Zones
    )

    if (-not $ZoneName) {
        if ($Zones.Count -eq 1) {
            return $Zones[0]
        }
        return $null
    }

    $match = $Zones | Where-Object { $_.name -eq $ZoneName } | Select-Object -First 1
    if (-not $match) {
        throw "Zone '$ZoneName' was not found in the Cloudflare account."
    }
    return $match
}

function Resolve-Hostname {
    param(
        [object]$Zone
    )

    if ($Hostname) {
        return $Hostname
    }
    if ($null -eq $Zone) {
        return ''
    }
    return "phoenixguard.$($Zone.name)"
}

function Get-ExistingTunnel {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedAccountId
    )

    $tunnels = Invoke-CfApi -Method GET -Path "/accounts/$ResolvedAccountId/cfd_tunnel?is_deleted=false"
    if ($null -eq $tunnels) {
        return $null
    }
    $tunnels = @($tunnels)
    return $tunnels | Where-Object { $_.name -eq $TunnelName } | Select-Object -First 1
}

function Ensure-Tunnel {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedAccountId
    )

    $existing = Get-ExistingTunnel -ResolvedAccountId $ResolvedAccountId
    if ($existing) {
        return $existing
    }

    return Invoke-CfApi -Method POST -Path "/accounts/$ResolvedAccountId/cfd_tunnel" -Body @{
        name       = $TunnelName
        config_src = 'cloudflare'
    }
}

function Get-TunnelToken {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedAccountId,

        [Parameter(Mandatory = $true)]
        [string]$TunnelId
    )

    return Invoke-CfApi -Method GET -Path "/accounts/$ResolvedAccountId/cfd_tunnel/$TunnelId/token"
}

function Set-TunnelConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedAccountId,

        [Parameter(Mandatory = $true)]
        [string]$TunnelId,

        [Parameter(Mandatory = $true)]
        [string]$ResolvedHostname
    )

    $body = @{
        config = @{
            ingress = @(
                @{
                    hostname      = $ResolvedHostname
                    service       = $ServiceUrl
                    originRequest = @{}
                },
                @{
                    service = 'http_status:404'
                }
            )
        }
    }

    return Invoke-CfApi -Method PUT -Path "/accounts/$ResolvedAccountId/cfd_tunnel/$TunnelId/configurations" -Body $body
}

function Ensure-DnsRecord {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ZoneId,

        [Parameter(Mandatory = $true)]
        [string]$ResolvedHostname,

        [Parameter(Mandatory = $true)]
        [string]$TunnelId
    )

    $recordName = $ResolvedHostname
    $target = "$TunnelId.cfargotunnel.com"
    $records = Invoke-CfApi -Method GET -Path "/zones/$ZoneId/dns_records?type=CNAME&name=$recordName"
    if ($null -eq $records) {
        $records = @()
    } else {
        $records = @($records)
    }
    $existing = $records | Select-Object -First 1

    if ($existing) {
        if ($existing.content -eq $target -and $existing.proxied) {
            return $existing
        }
        return Invoke-CfApi -Method PUT -Path "/zones/$ZoneId/dns_records/$($existing.id)" -Body @{
            type    = 'CNAME'
            name    = $recordName
            content = $target
            proxied = $true
        }
    }

    return Invoke-CfApi -Method POST -Path "/zones/$ZoneId/dns_records" -Body @{
        type    = 'CNAME'
        name    = $recordName
        content = $target
        proxied = $true
    }
}

function Get-ExistingAccessApp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedAccountId,

        [Parameter(Mandatory = $true)]
        [string]$ResolvedHostname
    )

    $apps = Invoke-CfApi -Method GET -Path "/accounts/$ResolvedAccountId/access/apps"
    if ($null -eq $apps) {
        return $null
    }
    $apps = @($apps)
    return $apps | Where-Object { $_.domain -eq $ResolvedHostname } | Select-Object -First 1
}

function Ensure-AccessApp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedAccountId,

        [Parameter(Mandatory = $true)]
        [string]$ResolvedHostname
    )

    $existing = Get-ExistingAccessApp -ResolvedAccountId $ResolvedAccountId -ResolvedHostname $ResolvedHostname
    $body = @{
        domain                       = $ResolvedHostname
        type                         = 'self_hosted'
        name                         = 'PhoenixGuard Protected Share'
        session_duration             = '24h'
        app_launcher_visible         = $false
        auto_redirect_to_identity    = $false
        skip_interstitial            = $false
        purpose_justification_required = $true
        purpose_justification_prompt = 'State your reason for accessing 808FxStandardSystemHybrid.'
    }

    if ($existing) {
        return Invoke-CfApi -Method PUT -Path "/accounts/$ResolvedAccountId/access/apps/$($existing.id)" -Body $body
    }

    return Invoke-CfApi -Method POST -Path "/accounts/$ResolvedAccountId/access/apps" -Body $body
}

function Ensure-AccessPolicy {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedAccountId,

        [Parameter(Mandatory = $true)]
        [string]$AppId
    )

    if (-not $AccessEmails -or $AccessEmails.Count -eq 0) {
        return $null
    }

    $policies = Invoke-CfApi -Method GET -Path "/accounts/$ResolvedAccountId/access/apps/$AppId/policies"
    if ($null -eq $policies) {
        $policies = @()
    } else {
        $policies = @($policies)
    }
    $policyName = 'Allow PhoenixGuard Operators'
    $existing = $policies | Where-Object { $_.name -eq $policyName } | Select-Object -First 1
    $includeRules = @()
    foreach ($email in $AccessEmails) {
        if (-not [string]::IsNullOrWhiteSpace($email)) {
            $includeRules += @{
                email = @{
                    email = $email.Trim()
                }
            }
        }
    }
    if ($includeRules.Count -eq 0) {
        return $null
    }

    $body = @{
        name                           = $policyName
        decision                       = 'allow'
        precedence                     = 1
        include                        = $includeRules
        require                        = @()
        exclude                        = @()
        session_duration               = '24h'
        purpose_justification_required = $true
        purpose_justification_prompt   = 'State your reason for accessing 808FxStandardSystemHybrid.'
    }

    if ($existing) {
        return Invoke-CfApi -Method PUT -Path "/accounts/$ResolvedAccountId/access/apps/$AppId/policies/$($existing.id)" -Body $body
    }

    return Invoke-CfApi -Method POST -Path "/accounts/$ResolvedAccountId/access/apps/$AppId/policies" -Body $body
}

function Update-EnvConfig {
    param(
        [AllowEmptyString()]
        [string]$ResolvedHostname,

        [Parameter(Mandatory = $true)]
        [string]$ResolvedAccountId,

        [Parameter(Mandatory = $true)]
        [string]$TunnelId
    )

    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        throw "VM share config not found at '$ConfigPath'."
    }

    $content = Get-Content -LiteralPath $ConfigPath -Raw
    $pairs = @(
        @{ Name = 'PHOENIXGUARD_CLOUDFLARE_ACCOUNT_ID'; Value = $ResolvedAccountId },
        @{ Name = 'PHOENIXGUARD_CLOUDFLARE_TUNNEL_NAME'; Value = $TunnelName },
        @{ Name = 'PHOENIXGUARD_CLOUDFLARE_TUNNEL_ID'; Value = $TunnelId },
        @{ Name = 'PHOENIXGUARD_CLOUDFLARE_HOSTNAME'; Value = $ResolvedHostname },
        @{ Name = 'PHOENIXGUARD_CLOUDFLARE_SERVICE_URL'; Value = $ServiceUrl }
    )

    foreach ($pair in $pairs) {
        $pattern = [regex]::Escape("`$env:$($pair.Name) = '") + ".*?'"
        $replacement = "`$env:$($pair.Name) = '$($pair.Value)'"
        if ($content -match $pattern) {
            $content = [regex]::Replace($content, $pattern, $replacement)
        } else {
            $content = $content.TrimEnd() + [Environment]::NewLine + $replacement + [Environment]::NewLine
        }
    }

    Set-Content -LiteralPath $ConfigPath -Value $content -Encoding UTF8
}

$resolvedAccountId = Get-AccountId
$tokenStatus = Verify-Token -ResolvedAccountId $resolvedAccountId
$zones = @(Get-ZoneList)
$zone = Resolve-Zone -Zones $zones
$resolvedHostname = Resolve-Hostname -Zone $zone
$tunnel = Ensure-Tunnel -ResolvedAccountId $resolvedAccountId
$tunnelId = [string]$tunnel.id
$tunnelToken = [string](Get-TunnelToken -ResolvedAccountId $resolvedAccountId -TunnelId $tunnelId)

$dnsRecord = $null
$accessApp = $null
$accessPolicy = $null

if ($resolvedHostname -and $zone) {
    Set-TunnelConfig -ResolvedAccountId $resolvedAccountId -TunnelId $tunnelId -ResolvedHostname $resolvedHostname | Out-Null
    $dnsRecord = Ensure-DnsRecord -ZoneId ([string]$zone.id) -ResolvedHostname $resolvedHostname -TunnelId $tunnelId

    if ($ConfigureAccess.IsPresent) {
        $accessApp = Ensure-AccessApp -ResolvedAccountId $resolvedAccountId -ResolvedHostname $resolvedHostname
        $accessPolicy = Ensure-AccessPolicy -ResolvedAccountId $resolvedAccountId -AppId ([string]$accessApp.id)
    }
}

if ($WriteConfig.IsPresent) {
    Update-EnvConfig -ResolvedHostname $resolvedHostname -ResolvedAccountId $resolvedAccountId -TunnelId $tunnelId
}

if ($InstallService.IsPresent) {
    $installerPath = Join-Path -Path $PSScriptRoot -ChildPath 'Install-CloudflaredTunnel.ps1'
    if (-not (Test-Path -LiteralPath $installerPath)) {
        throw "Install script not found at '$installerPath'."
    }

    $installArgs = @{
        TunnelToken = $tunnelToken
    }
    if ($ReinstallService.IsPresent) {
        $installArgs['ReinstallService'] = $true
    }
    & $installerPath @installArgs
}

$summary = [ordered]@{
    account_id      = $resolvedAccountId
    token_status    = [string]$tokenStatus.status
    tunnel_name     = $TunnelName
    tunnel_id       = $tunnelId
    hostname        = $resolvedHostname
    zone_name       = if ($zone) { [string]$zone.name } else { '' }
    zone_id         = if ($zone) { [string]$zone.id } else { '' }
    dns_record_id   = if ($dnsRecord) { [string]$dnsRecord.id } else { '' }
    access_app_id   = if ($accessApp) { [string]$accessApp.id } else { '' }
    access_policy_id = if ($accessPolicy) { [string]$accessPolicy.id } else { '' }
    service_url     = $ServiceUrl
    service_installed = $InstallService.IsPresent
}

if ($EmitJson.IsPresent) {
    $summary | ConvertTo-Json -Depth 10
} else {
    $summary.GetEnumerator() | ForEach-Object {
        Write-Output ("{0}: {1}" -f $_.Key, $_.Value)
    }
}
