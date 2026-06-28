# PhoenixGuard Requirements Profiles

PhoenixGuard uses one repo virtual environment and profile-specific requirement files. Do not install
random packages into global Python, Conda, nested venvs, or runtime folders.

## Production Profiles

| Profile | Source | Locked Windows file | Intended use |
| --- | --- | --- | --- |
| Live | `requirements/live.in` | `requirements/locks/live-win-py311.txt` | `FINAL_LIVE` tracker, mobile API, overlay dashboard, and package reporter. |
| Business | `requirements/business.in` | `requirements/locks/business-win-py311.txt` | Protected share desk and business API surfaces. |
| Dev | `requirements/dev.in` | `requirements/locks/dev-win-py311.txt` | Full repo tests, Pyright, and developer tooling. |
| Training | `requirements/training.in` | `requirements/locks/training-win-py311.txt` | Model training and export workflows. |
| Docs/PDF | `requirements/docs-pdf.in` | `requirements/locks/docs-pdf-win-py311.txt` | Report and architecture PDF generation. |

Launchers should prefer the locked profile for their surface and fall back to the matching `.in` file
only when the lock is not present. The top-level `requirements.txt` is not the live production
authority.

## Runtime Boundary

Runtime state belongs under `runtime/live`. It is not a dependency source and must never be treated as
a Python environment. Use:

```powershell
.\.venv\Scripts\python.exe .\Backend\tools\verify_single_venv_runtime.py
.\.venv\Scripts\python.exe -m pip check
```

before trusting a production launch.
