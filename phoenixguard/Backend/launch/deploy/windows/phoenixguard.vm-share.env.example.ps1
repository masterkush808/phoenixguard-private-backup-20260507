# Copy this file to `phoenixguard.vm-share.env.ps1` on the VM and replace
# the placeholder secrets before you register the startup task.

$env:PHOENIXGUARD_PROFILE = 'FINAL_LIVE'
$env:PHOENIXGUARD_SHARE_HOST = '127.0.0.1'
$env:PHOENIXGUARD_SHARE_PORT = '7861'
$env:PHOENIXGUARD_SHARE_CREDENTIALS = 'operator:ChangeThisNow2026!,brother:UseAnotherStrongPass2026!'
$env:PHOENIXGUARD_SHARE_PASSWORD = 'ReplaceWithALongRandomPassphrase'
$env:PHOENIXGUARD_SHARE_QUEUE_MAX_SIZE = '40'
$env:PHOENIXGUARD_SHARE_DEFAULT_CONCURRENCY = '2'
$env:PHOENIXGUARD_SHARE_HEAVY_CONCURRENCY = '2'
$env:PHOENIXGUARD_SHARE_SIGNAL_RATE_LIMIT = '6'
$env:PHOENIXGUARD_SHARE_SIGNAL_RATE_WINDOW_SEC = '60'
$env:PHOENIXGUARD_SHARE_MODEL_COUNCIL_RATE_LIMIT = '2'
$env:PHOENIXGUARD_SHARE_MODEL_COUNCIL_RATE_WINDOW_SEC = '300'
$env:PHOENIXGUARD_SHARE_MAX_UPLOAD_BYTES = '12582912'
$env:PHOENIXGUARD_SHARE_MAX_IMAGE_PIXELS = '16000000'

# Optional Cloudflare automation settings used by Setup-PhoenixGuardCloudflare.ps1.
# $env:PHOENIXGUARD_CLOUDFLARE_ACCOUNT_ID = 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
# $env:PHOENIXGUARD_CLOUDFLARE_ZONE = 'example.com'
# $env:PHOENIXGUARD_CLOUDFLARE_HOSTNAME = 'phoenixguard.example.com'
# $env:PHOENIXGUARD_CLOUDFLARE_TUNNEL_NAME = '808fx-standard-system-hybrid'
# $env:PHOENIXGUARD_CLOUDFLARE_SERVICE_URL = 'http://127.0.0.1:7861'
# $env:PHOENIXGUARD_CLOUDFLARE_ACCESS_EMAILS = 'you@example.com,partner@example.com'

# Optional if the VM needs to download private or gated model assets.
# $env:HF_TOKEN = 'hf_xxx'
