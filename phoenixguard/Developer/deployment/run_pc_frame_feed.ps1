param(
    [string]$ConfigPath = "$PSScriptRoot\frame_feed_profiles.example.json",
    [string]$Profile = "desktop-pocket-m5",
    [string]$Token = "",
    [string]$SigningSecret = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$agent = Join-Path $PSScriptRoot "edge_frame_agent.py"
if (-not (Test-Path $agent)) {
    throw "Frame agent not found: $agent"
}

if ($Token) {
    $env:PHOENIXGUARD_FRAME_INGEST_TOKEN = $Token
}
if ($SigningSecret) {
    $env:PHOENIXGUARD_FRAME_INGEST_SIGNING_SECRET = $SigningSecret
}

$venvPython = Join-Path $repoRoot ".venv-live\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython $agent --config $ConfigPath --profile $Profile
} else {
    & python $agent --config $ConfigPath --profile $Profile
}
