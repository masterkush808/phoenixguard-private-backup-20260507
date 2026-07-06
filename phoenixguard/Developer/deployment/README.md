# PhoenixGuard Deployment Tools

These files are for moving PhoenixGuard to an always-on Windows worker.

```text
windows_worker_bootstrap.ps1
  Clones/updates the repo, installs .venv-live, writes production env defaults,
  and optionally registers scheduled tasks.

windows_worker_watchdog.ps1
  Checks /health and compact live state, then restarts the live stack through
  the canonical kill switch if needed.

cloudflare_tunnel_config.example.yml
  Example tunnel mapping from a public HTTPS hostname to the worker API.
```

Run these on the rented Windows VPS, not on the local developer machine unless
you are testing the deployment flow.
