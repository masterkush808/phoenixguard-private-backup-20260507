# PhoenixGuard Requirements Profiles

PhoenixGuard uses profile-specific virtual environments and locked requirement files. Do not install
random packages into global Python, Conda, runtime folders, or the wrong profile environment.

## Production Profiles

| Profile | Source | Locked file(s) | Intended use |
| --- | --- | --- | --- |
| Live | `requirements/live.in` | `requirements/locks/live-win-py311.txt`, `requirements/locks/live-linux-py311.txt` | `FINAL_LIVE` tracker, mobile API, overlay dashboard, package reporter, and Ubuntu cloud brain. |
| Business | `requirements/business.in` | `requirements/locks/business-win-py311.txt` | Protected share desk and business API surfaces. |
| Dev | `requirements/dev.in` | `requirements/locks/dev-win-py311.txt` | Full repo tests, Pyright, and developer tooling. |
| Training | `requirements/training.in` | `requirements/locks/training-win-py311.txt` | Model training and export workflows. |
| Docs/PDF | `requirements/docs-pdf.in` | `requirements/locks/docs-pdf-win-py311.txt` | Report and architecture PDF generation. |

Launchers should prefer the locked profile for their surface. The top-level `requirements.txt` is not
the live production authority.

## Runtime Boundary

Runtime state belongs under `runtime/live`. It is not a dependency source and must never be treated as
a Python environment. Use:

```powershell
.\.venv-live\Scripts\python.exe .\Backend\tools\verify_single_venv_runtime.py
.\.venv-live\Scripts\python.exe -m pip check
.\.venv-dev\Scripts\python.exe -m pip check
```

before trusting a production launch.

## Environment Targets

```text
.venv-live      requirements/locks/live-win-py311.txt
.venv-live      requirements/locks/live-linux-py311.txt on Ubuntu VPS hosts
.venv-dev       requirements/locks/dev-win-py311.txt
.venv-training  requirements/locks/training-win-py311.txt
.venv-business  requirements/locks/business-win-py311.txt
.venv-docs      requirements/locks/docs-pdf-win-py311.txt
```
