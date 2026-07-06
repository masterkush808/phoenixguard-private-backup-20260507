[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Deployment packaging prints operator status.")]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

if (-not $OutputPath.Trim()) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path -Path $ProjectRoot -ChildPath "phoenixguard_cloud_assets_$stamp.zip"
}

$include = @(
    "models",
    "memory_bank",
    "adapters",
    "808 Memory",
    "data\adapter_bank.json",
    "data\skill_gate_smoke.json",
    "data\skill_gate_isolated_smoke.json",
    "yolov8n.pt"
)

$paths = New-Object System.Collections.Generic.List[string]
foreach ($relative in $include) {
    $path = Join-Path -Path $ProjectRoot -ChildPath $relative
    if (Test-Path -LiteralPath $path) {
        $paths.Add($path)
    }
}

if ($paths.Count -eq 0) {
    throw "No PhoenixGuard model/memory assets found to package under $ProjectRoot."
}

if (Test-Path -LiteralPath $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}

Write-Host "[PhoenixGuard Deploy] Packaging assets:"
foreach ($path in $paths) {
    Write-Host "  $path"
}

Compress-Archive -LiteralPath $paths.ToArray() -DestinationPath $OutputPath -CompressionLevel Optimal

$file = Get-Item -LiteralPath $OutputPath
Write-Host "[PhoenixGuard Deploy] Asset package created:"
Write-Host "  $($file.FullName)"
Write-Host "  $([Math]::Round($file.Length / 1MB, 2)) MB"
