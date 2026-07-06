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

edge_frame_agent.py
  Lightweight feed agent that sends chart screenshots or image-folder frames to
  the PhoenixGuard cloud brain through /v1/mobile/frame-ingest.
```

Run the worker bootstrap/watchdog on the rented Windows VPS. Run
edge_frame_agent.py on whichever machine owns the chart pixels when the source
is not the managed VPS browser.
