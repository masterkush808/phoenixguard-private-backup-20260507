# Root Layout Cleanup Report

## Clear Answer

The PhoenixGuard project root now contains only root-owned repository files:

```text
_pg_bootstrap.py
.gitignore
pyproject.toml
pyrightconfig.json
pytest.ini
README.md
requirements.txt
sitecustomize.py
```

All previously floating launchers, compatibility modules, docs, dashboard entrypoints, business launcher, model asset, and shooter gate state were moved into responsible folders.

## Files Moved

- `Backend/launch`: production launcher, tracker/API launchers, voice launcher, watchdog wrappers, package reporter `shooter.py`.
- `Backend/compat`: legacy top-level Python import compatibility modules used by older tests and tools.
- `Frontend/dashboard`: Gradio dashboard `main.py` and protected share surface `share_phoenixguard.py`.
- `Business/api`: share launcher `start_phoenixguard_share.ps1`.
- `Backend/docs_backend`: root reports and project history docs.
- `Developer/developer_tools`: `.agent.md`.
- `Backend/config/models`: local `yolov8n.pt` runtime model asset.
- `Backend/config`: local `shooter_3_gate_state.json`.

## Deleted Root Duplicates

- `replay_trace.log`: empty generated replay trace at project root.
- `yolov8n.pt`: root duplicate removed after matching SHA-256 and size with `Backend/config/models/yolov8n.pt`.

## Source-Control Boundary

The existing `.gitignore` intentionally keeps model/runtime artifacts out of Git:

- `Backend/config/models/yolov8n.pt`
- `Backend/config/shooter_3_gate_state.json`

Those files are preserved locally in responsible folders, but they remain ignored under the current artifact policy.

## Validation

- `python -m compileall -q .`: PASS
- `python -m pyright`: PASS, 0 errors, 0 warnings, 0 informations
- `python Backend\tools\verify_v3_integrity.py`: PASS
- PowerShell launcher parser check: PASS for all moved launchers
- Focused migration tests: PASS, 229 passed
- Full pytest suite: PASS, 1258 passed, 3 skipped

## Remaining Root Policy

The files left at project root are not loose application files. They are repository metadata, package/test configuration, and bootstrap files that must stay at root so Python, pytest, Pyright, packaging, and launch scripts can discover the project consistently.
