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

linux_cloud_brain_bootstrap.sh
  Ubuntu VPS bootstrap for the cheapest cloud-brain deployment: clone repo,
  create .venv-live with Python 3.11, install the live stack, write systemd,
  optionally install Cloudflare Tunnel, and start the API.

phoenixguard-cloud-brain.service / phoenixguard-cloud-brain.env.example
  systemd and env-file references for the Linux cloud-brain service.

package_cloud_assets.ps1
  Creates a zip containing local ignored model/memory assets that are required
  by the cloud brain but intentionally not committed to Git.

restore_cloud_assets.sh
  Restores that asset package on the VPS and restarts the cloud-brain service.
```

Run the worker bootstrap/watchdog on the rented Windows VPS. Run
edge_frame_agent.py on whichever machine owns the chart pixels when the source
is not the managed VPS browser.

For the lowest-cost off-machine deployment, use linux_cloud_brain_bootstrap.sh
on an Ubuntu VPS and expose it with Cloudflare Tunnel.
