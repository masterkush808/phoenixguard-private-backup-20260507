[CmdletBinding()]
param(
    [string]$LocalUrl = 'http://127.0.0.1:7861',
    [int]$WaitTimeoutSec = 45,
    [string]$LogDirectory = '',
    [switch]$StopExisting
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $LogDirectory) {
    $LogDirectory = Join-Path -Path $script:ScriptRoot -ChildPath 'logs'
}

function Get-CloudflaredExe {
    $command = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $fallback = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
    if (Test-Path -LiteralPath $fallback) {
        return $fallback
    }

    throw 'cloudflared.exe is not installed. Install Cloudflare cloudflared first.'
}

function Get-QuickTunnelProcess {
    $needle = 'tunnel --url'
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq 'cloudflared.exe' -and $_.CommandLine -like "*$needle*"
    })
}

function Stop-QuickTunnelProcess {
    $existing = @(Get-QuickTunnelProcess)
    foreach ($process in $existing) {
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        }
        catch {
            Write-Warning "Failed to stop existing quick tunnel process $($process.ProcessId): $($_.Exception.Message)"
        }
    }
}

try {
    Invoke-WebRequest -Uri $LocalUrl -UseBasicParsing -TimeoutSec 5 | Out-Null
}
catch {
    throw "PhoenixGuard is not reachable at '$LocalUrl'. Start the local app before opening a Quick Tunnel."
}

if ($StopExisting.IsPresent) {
    Stop-QuickTunnelProcess
}

if (-not (Test-Path -LiteralPath $LogDirectory)) {
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
}

$cloudflaredExe = Get-CloudflaredExe
$stdoutPath = Join-Path -Path $LogDirectory -ChildPath 'quick-tunnel.stdout.log'
$stderrPath = Join-Path -Path $LogDirectory -ChildPath 'quick-tunnel.stderr.log'
$pidPath = Join-Path -Path $LogDirectory -ChildPath 'quick-tunnel.pid'
$urlPath = Join-Path -Path $LogDirectory -ChildPath 'quick-tunnel.url.txt'

Remove-Item -LiteralPath $stdoutPath -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $stderrPath -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $urlPath -ErrorAction SilentlyContinue

$process = Start-Process `
    -FilePath $cloudflaredExe `
    -ArgumentList @('tunnel', '--url', $LocalUrl, '--no-autoupdate') `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII

$deadline = (Get-Date).AddSeconds($WaitTimeoutSec)
$regex = [regex]'https://[-a-z0-9]+\.trycloudflare\.com'
$publicUrl = $null

while ((Get-Date) -lt $deadline) {
    if ($process.HasExited) {
        break
    }

    foreach ($path in @($stdoutPath, $stderrPath)) {
        if (-not (Test-Path -LiteralPath $path)) {
            continue
        }

        $content = Get-Content -LiteralPath $path -Raw
        if ($null -eq $content) {
            continue
        }
        $match = $regex.Match($content)
        if ($match.Success) {
            $publicUrl = $match.Value
            break
        }
    }

    if ($publicUrl) {
        break
    }

    Start-Sleep -Milliseconds 500
    $process.Refresh()
}

if (-not $publicUrl) {
    $stderrTail = if (Test-Path -LiteralPath $stderrPath) { (Get-Content -LiteralPath $stderrPath -Tail 20) -join [Environment]::NewLine } else { '' }
    $stdoutTail = if (Test-Path -LiteralPath $stdoutPath) { (Get-Content -LiteralPath $stdoutPath -Tail 20) -join [Environment]::NewLine } else { '' }
    throw ("Quick Tunnel did not publish a public URL within {0} seconds.`nSTDOUT:`n{1}`nSTDERR:`n{2}" -f $WaitTimeoutSec, $stdoutTail, $stderrTail)
}

Set-Content -LiteralPath $urlPath -Value $publicUrl -Encoding ASCII

[ordered]@{
    local_url      = $LocalUrl
    public_url     = $publicUrl
    process_id     = $process.Id
    stdout_log     = $stdoutPath
    stderr_log     = $stderrPath
    pid_file       = $pidPath
    url_file       = $urlPath
    note           = 'Cloudflare Quick Tunnel URLs are temporary and intended for development/testing.'
} | ConvertTo-Json -Depth 5
