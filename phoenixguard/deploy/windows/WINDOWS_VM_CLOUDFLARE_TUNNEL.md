# PhoenixGuard on a Windows Cloud VM

This is the setup for running PhoenixGuard on an always-on Windows VM so you can switch off your own PC.

## Architecture

- PhoenixGuard runs on the Windows VM
- PhoenixGuard listens only on `127.0.0.1:7861`
- `cloudflared` runs on the same VM as a Windows service
- Cloudflare Tunnel publishes the app to a hostname such as `phoenixguard.example.com`
- Cloudflare Access sits in front of that hostname
- PhoenixGuard keeps its own username/password login as a second layer

## Token Types You Will See

Cloudflare uses multiple token types in this setup. Keep them separate:

- `Account API token`: used by you or automation to call the Cloudflare API for tunnels, Access policies, and DNS changes
- `Tunnel token`: generated for one specific remotely-managed tunnel and installed on the VM with `cloudflared service install <TOKEN>`
- `Access service token`: optional machine-to-machine credential for trusted backend callers; not needed for normal browser logins

For PhoenixGuard on a Windows VM:

- create the `Account API token` in the dashboard only if you want scripted Cloudflare management
- install the VM with the per-tunnel `Tunnel token`
- keep `Access service tokens` only for future API-to-API use cases

Do not paste a broad `Account API token` into the public app or into the VM unless you have a very specific automation need. The VM usually only needs the tunnel token.

## Recommended Layout On The VM

- Put the project at `C:\PhoenixGuard\phoenixguard`
- Keep secrets in `deploy\windows\phoenixguard.vm-share.env.ps1`
- Do not run the project from OneDrive or a synced desktop folder

## 1. Copy PhoenixGuard To The VM

Move the full `phoenixguard` folder to the VM, including:

- `models`
- `data`
- `memory_bank`
- `deploy`
- your custom chart assets and any local model weights

If your current local install already downloaded the heavy models, copy those directories too so the VM does not need to rebuild everything from scratch.

## 2. Create The VM Share Config

1. Copy `deploy\windows\phoenixguard.vm-share.env.example.ps1` to `deploy\windows\phoenixguard.vm-share.env.ps1`.
2. Replace the placeholders with real secrets.

Minimum settings:

```powershell
$env:PHOENIXGUARD_PROFILE = 'FINAL_LIVE'
$env:PHOENIXGUARD_SHARE_PORT = '7861'
$env:PHOENIXGUARD_SHARE_CREDENTIALS = 'operator:StrongPass2026!,brother:AnotherStrongPass2026!'
$env:PHOENIXGUARD_PASSPHRASE = 'LongRandomPassphraseHere'
$env:PHOENIXGUARD_SHARE_SIDE_EFFECT_FREE = '1'
$env:PHOENIXGUARD_SHARE_ENABLE_FEEDBACK = '0'
$env:PHOENIXGUARD_SHARE_ENABLE_LEARNING_MUTATIONS = '0'
$env:PHOENIXGUARD_SHARE_QUEUE_MAX_SIZE = '40'
$env:PHOENIXGUARD_SHARE_HEAVY_CONCURRENCY = '2'
$env:PHOENIXGUARD_SHARE_SIGNAL_RATE_LIMIT = '6'
$env:PHOENIXGUARD_SHARE_MODEL_COUNCIL_RATE_LIMIT = '2'
```

These defaults keep share-mode inference mutation-guarded, disable remote learning mutations, and add queue and rate-limit pressure controls for multi-user access.

Optional Cloudflare automation settings:

```powershell
$env:PHOENIXGUARD_CLOUDFLARE_ACCOUNT_ID = 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
$env:PHOENIXGUARD_CLOUDFLARE_ZONE = 'example.com'
$env:PHOENIXGUARD_CLOUDFLARE_HOSTNAME = 'phoenixguard.example.com'
$env:PHOENIXGUARD_CLOUDFLARE_TUNNEL_NAME = '808fx-standard-system-hybrid'
$env:PHOENIXGUARD_CLOUDFLARE_SERVICE_URL = 'http://127.0.0.1:7861'
$env:PHOENIXGUARD_CLOUDFLARE_ACCESS_EMAILS = 'you@example.com,partner@example.com'
```

These are only needed if you want `Setup-PhoenixGuardCloudflare.ps1` to create the tunnel, publish DNS, and write the Cloudflare values back into the VM env file.

## 3. Bootstrap PhoenixGuard On The VM

Open PowerShell in the project root:

```powershell
cd C:\PhoenixGuard\phoenixguard
.\deploy\windows\Start-PhoenixGuardVmShare.ps1 -Bootstrap
```

This will:

- load your VM env script
- create or reuse `.venv`
- install `requirements.txt`
- start the protected share desk locally on `127.0.0.1:7861`

Confirm locally on the VM:

```powershell
Invoke-WebRequest http://127.0.0.1:7861
```

## 4. Make PhoenixGuard Start On Boot

From an elevated PowerShell window:

```powershell
cd C:\PhoenixGuard\phoenixguard
.\deploy\windows\Register-PhoenixGuardShareTask.ps1 -BootstrapOnFirstRun -StartNow
```

This registers a startup task named `PhoenixGuard Share` that runs as `SYSTEM`.

## 5. Create A Remotely-Managed Cloudflare Tunnel

In Cloudflare Zero Trust:

1. Go to `Networks` -> `Tunnels`
2. Create a tunnel
3. Choose `cloudflared`
4. Add a public hostname such as `phoenixguard.example.com`
5. Point it to `http://127.0.0.1:7861`
6. Copy the generated tunnel token from the Windows install command

Cloudflare recommends remotely-managed tunnels so the configuration stays in Cloudflare instead of only on the VM.

If you are also creating an `Account API token`, scope it as tightly as possible:

- keep `Cloudflare Tunnel` and `Access: Apps and Policies` only if you will automate those resources
- keep `DNS` scoped to the one zone that will host PhoenixGuard
- prefer a short expiry
- use client IP filtering only if your admin IP is stable; if you travel or your ISP changes addresses, the API token may stop working until you update the allowlist

### Optional: Automate the Cloudflare Side

If you already have a tightly-scoped `Account API token`, you can automate the tunnel, DNS, and Access configuration:

```powershell
cd C:\PhoenixGuard\phoenixguard
$env:CLOUDFLARE_API_TOKEN = 'cfat_xxx'
.\deploy\windows\Setup-PhoenixGuardCloudflare.ps1 -WriteConfig -ConfigureAccess -InstallService
```

This script:

- verifies the API token against your Cloudflare account
- creates or reuses a remotely-managed tunnel named `808fx-standard-system-hybrid`
- configures the public hostname to point at `http://127.0.0.1:7861`
- writes the resulting Cloudflare IDs and hostname into `phoenixguard.vm-share.env.ps1`
- optionally creates the Cloudflare Access application and email allow policy
- optionally installs the Windows `cloudflared` service using the returned tunnel token

If the account has no Cloudflare zone onboarded yet, the script can still create the tunnel but it cannot publish the final hostname until the zone exists.

## 6. Install cloudflared On The VM

From an elevated PowerShell window:

```powershell
cd C:\PhoenixGuard\phoenixguard
.\deploy\windows\Install-CloudflaredTunnel.ps1 -TunnelToken 'eyJ...'
```

This script:

- installs `cloudflared` with `winget` if needed
- runs `cloudflared service install <TOKEN>` using the tunnel token from step 5
- leaves the tunnel running as a Windows service

## 7. Lock The Hostname With Cloudflare Access

In Cloudflare Zero Trust:

1. Go to `Access` -> `Applications`
2. Add an application
3. Choose `Self-hosted`
4. Use the same hostname you published through the tunnel
5. Add an `Allow` policy for only the identities you trust

Example:

- your own email
- your brother's email

Cloudflare notes that Access applications are deny-by-default, so users must match an `Allow` policy before they can reach the app.

## 7b. Add Cloudflare WAF And Rate Limiting

For a public hostname, also enable:

1. Cloudflare Managed Rules on the zone
2. A rate limiting rule on the PhoenixGuard hostname
3. Bot protection if your plan includes it

Suggested starting rule:

- protect `phoenixguard.example.com`
- challenge or throttle bursts well above your expected user traffic
- keep Cloudflare as the first internet-facing layer and PhoenixGuard on `127.0.0.1`

## 8. Validate The Full Flow

1. Browse to your Cloudflare hostname
2. Pass the Cloudflare Access login
3. Pass the PhoenixGuard login
4. Upload the two chart images
5. Confirm inference works
6. Reboot the VM and confirm the site returns without manual login to the VM

## Quick Tunnel Option

If you do not want to buy or attach a custom domain yet, you can expose the running PhoenixGuard VM with a temporary Cloudflare Quick Tunnel:

```powershell
cd C:\PhoenixGuard\phoenixguard
.\deploy\windows\Start-PhoenixGuardQuickTunnel.ps1
```

This script:

- verifies that PhoenixGuard is already serving on `http://127.0.0.1:7861`
- starts `cloudflared tunnel --url http://127.0.0.1:7861`
- captures the temporary `https://...trycloudflare.com` URL
- writes logs and the discovered URL under `deploy\windows\logs`

To stop the temporary Quick Tunnel:

```powershell
cd C:\PhoenixGuard\phoenixguard
.\deploy\windows\Stop-PhoenixGuardQuickTunnel.ps1
```

Quick Tunnel URLs are temporary and intended for testing or lightweight sharing. For a stable production hostname, use a real domain zone with the remotely-managed tunnel path above.

## Operations Notes

- You can now shut down your own PC because the VM is the host
- Do not expose port `7861` directly to the public internet
- Keep PhoenixGuard on `LAN` mode on the VM and let Cloudflare handle internet exposure
- For stronger Windows isolation, run PhoenixGuard under a dedicated low-privilege local account instead of a broad admin context
- Remote feedback and online learning are disabled by default in the VM env template so outside users cannot retrain the system or churn disk state
- Rotate the tunnel token if it is ever copied into the wrong place
- Update `cloudflared` periodically: `cloudflared update`
- After updating `cloudflared`, restart the service

## If You Need GPU

If your current workflow depends on CUDA, choose a Windows VM with an NVIDIA GPU and the correct drivers. Without that, PhoenixGuard can still run, but inference may be much slower. This GPU guidance is an engineering inference from your codebase, not a Cloudflare requirement.
