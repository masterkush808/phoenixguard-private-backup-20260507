# PhoenixGuard Worldwide Share

Use the share launcher when you want someone outside your LAN to access
PhoenixGuard. It now opens the same advanced workstation used locally, with
auth enabled for shared access.

## Best Quick Start

This is the fastest way to let your brother in Lesotho access the desk while
you keep inference running on your machine in India.

1. Set strong credentials in PowerShell:

```powershell

$env:PHOENIXGUARD_SHARE_CREDENTIALS='you:StrongPass2026!,brother:BrotherPass2026!'

```

1. Launch the advanced workstation with a temporary worldwide HTTPS tunnel:

```powershell

.\Business\api\start_phoenixguard_share.ps1 -LaunchMode HEAVY_LAZY -AccessMode TUNNEL

```

1. Wait for Gradio to print the public share URL.
2. Send your brother:

   - the Gradio URL
   - only his username and password

What this mode does:

- Keeps PhoenixGuard bound to `127.0.0.1` on your PC
- Creates a temporary public HTTPS link
- Still requires PhoenixGuard login before anyone can use the workstation
- Forces strong passwords automatically

## Long-Term Recommended Setup

For a cleaner permanent setup, keep PhoenixGuard local and put Cloudflare
Tunnel in front of it.

1. Start PhoenixGuard in local-only mode:

```powershell

.\Business\api\start_phoenixguard_share.ps1 -LaunchMode HEAVY_LAZY -AccessMode LAN

```

1. Create a Cloudflare Tunnel that points your domain or subdomain to
`http://127.0.0.1:7861`.
2. Add a Cloudflare Access policy so only your brother's email can reach the
app.
3. Use the sample config in `Backend/launch/deploy/cloudflare/phoenixguard-share.example.yml`.

Why this is better:

- No router port forwarding
- Stable custom URL such as `phoenixguard.yourdomain.com`
- Cloudflare can add an identity gate before the PhoenixGuard login page
- Your app still stays on your machine

## Important Clarification

Binding to `0.0.0.0` is not the same as being worldwide.

`-AccessMode PUBLIC` means:

- PhoenixGuard listens on all interfaces on your machine
- People on your LAN can reach it
- The internet still cannot reach it unless you also add router port

  forwarding, a reverse proxy, or a tunnel

Use `PUBLIC` only if you already know you want to manage that networking
yourself.
