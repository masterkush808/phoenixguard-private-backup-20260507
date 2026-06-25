[CmdletBinding()]
param(
    [string]$LogDirectory = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $LogDirectory) {
    $LogDirectory = Join-Path -Path $script:ScriptRoot -ChildPath 'logs'
}

$pidPath = Join-Path -Path $LogDirectory -ChildPath 'quick-tunnel.pid'

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output 'No quick tunnel PID file was found.'
    return
}

$pidValue = (Get-Content -LiteralPath $pidPath -Raw).Trim()
if (-not $pidValue) {
    Write-Output 'Quick tunnel PID file is empty.'
    return
}

$process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $process.Id -Force -ErrorAction Stop
    Write-Output "Stopped quick tunnel process $($process.Id)."
} else {
    Write-Output "Quick tunnel process $pidValue is not running."
}
