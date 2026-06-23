param(
    [Parameter(Mandatory = $true)]
    [string]$StatusPath,

    [Parameter(Mandatory = $true)]
    [string]$RefreshLog,

    [string]$BaseUrl = "http://127.0.0.1:8793",

    [string]$SessionId = "pocket-live-8788",

    [int]$PollSeconds = 10,

    [int]$MinRefreshGapSeconds = 90
)

$ErrorActionPreference = "Continue"
$lastRefresh = Get-Date "2000-01-01"

try {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class PgBrokerFreshnessRefreshWinApi {
  [DllImport("user32.dll")]
  public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@
} catch {
}

try {
    Add-Type -AssemblyName System.Windows.Forms
} catch {
}

function Write-RefreshLog {
    param($Record)

    try {
        $dir = Split-Path -Parent $RefreshLog
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        ($Record | ConvertTo-Json -Depth 8 -Compress) | Add-Content -LiteralPath $RefreshLog -Encoding UTF8
    } catch {
    }
}

while ($true) {
    try {
        if (-not (Test-Path -LiteralPath $StatusPath)) {
            Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
            continue
        }

        $status = Get-Content -LiteralPath $StatusPath -Raw | ConvertFrom-Json
        if (-not [bool]$status.running) {
            break
        }

        $fresh = $status.last_freshness
        $reasons = @($fresh.reasons)
        $pixel = $fresh.pixel_freeze
        $pixelReason = [string]$pixel.reason
        $refreshRecommended = [bool]$pixel.refresh_recommended -or ($pixel.status -in @("STATIC_REFRESH", "FROZEN")) -or (($reasons -join ",") -match "BROKER_PIXELS_(STATIC|FROZEN)") -or ($pixelReason -match "BROKER_PIXELS_(STATIC|FROZEN)")
        $gapReady = ((Get-Date) - $lastRefresh).TotalSeconds -ge $MinRefreshGapSeconds

        if ($refreshRecommended -and $gapReady) {
            $session = Invoke-RestMethod -Uri "$BaseUrl/v1/mobile/window-tracker/sessions/$SessionId" -TimeoutSec 10
            $hwnd = [int]$session.locked_window.hwnd
            if ($hwnd -gt 0) {
                [PgBrokerFreshnessRefreshWinApi]::SetForegroundWindow([IntPtr]$hwnd) | Out-Null
                Start-Sleep -Milliseconds 500
                [System.Windows.Forms.SendKeys]::SendWait("{F5}")
                $lastRefresh = Get-Date
                Write-RefreshLog ([ordered]@{
                    at = $lastRefresh.ToString("o")
                    action = "F5_REFRESH"
                    hwnd = $hwnd
                    title = $session.locked_window.title
                    reason = if ($reasons.Count -gt 0) { ($reasons -join ",") } else { $pixelReason }
                    pixel_status = $pixel.status
                    same_hash_sec = $pixel.same_hash_sec
                    frame = $status.last_frame
                })
            } else {
                Write-RefreshLog ([ordered]@{
                    at = (Get-Date).ToString("o")
                    action = "SKIP_REFRESH"
                    reason = "missing locked_window hwnd"
                    pixel_status = $pixel.status
                    frame = $status.last_frame
                })
            }
        }
    } catch {
        Write-RefreshLog ([ordered]@{
            at = (Get-Date).ToString("o")
            error = $_.Exception.Message
        })
    }

    Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
}
