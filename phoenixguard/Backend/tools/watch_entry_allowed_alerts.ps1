param(
    [Parameter(Mandatory = $true)]
    [string]$StatusPath,

    [Parameter(Mandatory = $true)]
    [string]$EntryEventsPath,

    [Parameter(Mandatory = $true)]
    [string]$AlertLog,

    [int]$PollSeconds = 2,

    [int]$AlertDisplaySeconds = 300,

    [switch]$IgnoreExisting,

    [switch]$SummarizeExisting
)

$ErrorActionPreference = "Continue"
$seen = @{}
$firstScan = $true

function Get-EntryKey {
    param($EventRow)

    if ($EventRow.manual_alert_key) {
        return [string]$EventRow.manual_alert_key
    }
    if ($EventRow.manual_alert -and $EventRow.manual_alert.key) {
        return [string]$EventRow.manual_alert.key
    }
    $entry = $EventRow.entry
    $packet = [string]$entry.packet_id
    if ($packet.Length -gt 0) {
        return "$packet|$($EventRow.seq)|$($EventRow.frame)|$($EventRow.captured_at_utc)"
    }
    return "seq_$($EventRow.seq)|frame_$($EventRow.frame)|$($entry.side)|$($entry.lane_name)|$($EventRow.captured_at_utc)"
}

function Write-Alert {
    param(
        [string]$Message,
        $EventRow
    )

    $record = [ordered]@{
        at = (Get-Date).ToString("o")
        message = $Message
        alert_display_seconds = $AlertDisplaySeconds
        event = $EventRow
    }
    try {
        $dir = Split-Path -Parent $AlertLog
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        ($record | ConvertTo-Json -Depth 12 -Compress) | Add-Content -LiteralPath $AlertLog -Encoding UTF8
    } catch {
    }
    try {
        msg $env:USERNAME "/time:$AlertDisplaySeconds" $Message
    } catch {
    }
    try {
        [console]::beep(1000, 700)
    } catch {
    }
}

while ($true) {
    try {
        $status = $null
        if (Test-Path -LiteralPath $StatusPath) {
            $status = Get-Content -LiteralPath $StatusPath -Raw | ConvertFrom-Json
        }

        if (Test-Path -LiteralPath $EntryEventsPath) {
            $allowedRows = @()
            foreach ($line in Get-Content -LiteralPath $EntryEventsPath) {
                if (-not $line.Trim()) {
                    continue
                }
                try {
                    $row = $line | ConvertFrom-Json
                } catch {
                    continue
                }
                $entry = $row.entry
                if (-not $entry) {
                    continue
                }
                $allowed = [bool]$entry.allowed -or [bool]$entry.execution_authorized
                if (-not $allowed) {
                    continue
                }
                if ($row.PSObject.Properties.Name -contains "manual_alert_allowed" -and -not [bool]$row.manual_alert_allowed) {
                    continue
                }
                if ($entry.PSObject.Properties.Name -contains "manual_alert_allowed" -and -not [bool]$entry.manual_alert_allowed) {
                    continue
                }
                $key = Get-EntryKey $row
                if ($seen.ContainsKey($key)) {
                    continue
                }
                $allowedRows += $row
            }

            if ($firstScan -and $IgnoreExisting) {
                foreach ($row in $allowedRows) {
                    $seen[(Get-EntryKey $row)] = $true
                }
            } elseif ($firstScan -and $SummarizeExisting -and $allowedRows.Count -gt 1) {
                foreach ($row in $allowedRows) {
                    $seen[(Get-EntryKey $row)] = $true
                }
                $latest = $allowedRows[-1]
                $entry = $latest.entry
                $message = "PhoenixGuard EXECUTION ALIGNED: $($allowedRows.Count) allowed events already captured. Latest $($entry.side) lane=$($entry.lane_name) frame=$($latest.frame) score=$($entry.final_score) packet=$($entry.packet_id). Alert uptime: $AlertDisplaySeconds sec."
                Write-Alert -Message $message -EventRow $latest
            } else {
                foreach ($row in $allowedRows) {
                    $seen[(Get-EntryKey $row)] = $true
                    $entry = $row.entry
                    $message = "PhoenixGuard EXECUTION ALIGNED: $($entry.side) lane=$($entry.lane_name) frame=$($row.frame) score=$($entry.final_score) packet=$($entry.packet_id). Alert uptime: $AlertDisplaySeconds sec."
                    Write-Alert -Message $message -EventRow $row
                }
            }
        }

        $firstScan = $false
        if ($status -and -not [bool]$status.running) {
            break
        }
    } catch {
        $record = [ordered]@{
            at = (Get-Date).ToString("o")
            error = $_.Exception.Message
        }
        try {
            ($record | ConvertTo-Json -Depth 4 -Compress) | Add-Content -LiteralPath $AlertLog -Encoding UTF8
        } catch {
        }
    }
    Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
}
