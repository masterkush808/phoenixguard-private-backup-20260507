# PhoenixGuard V3 Dependency Governance

## Clear Answer

PhoenixGuard dependencies are grouped by responsibility and install into separate profile
environments. Live runtime uses `.venv-live`; full repo diagnostics use `.venv-dev`; training,
business, and docs/PDF use `.venv-training`, `.venv-business`, and `.venv-docs`.

## Why This Exists

The global Python interpreter on this machine has unrelated package conflicts across TensorFlow,
protobuf, transformers, scikit-learn, plotly, pillow, LangChain, Streamlit, mitmproxy, PyCaret, and
other tooling. PhoenixGuard should not depend on that global environment.

The live profile currently passes:

```text
.\.venv-live\Scripts\python.exe -m pip check
.\.venv-live\Scripts\python.exe .\Backend\tools\verify_single_venv_runtime.py
```

The dependency policy is therefore:

```text
Use .venv-live for live runtime only.
Do not run PhoenixGuard from global Python.
Do not install training, business, docs, or dev-only packages into .venv-live.
Do not auto-create environments from runtime launchers; if .venv-live is missing, live launch must
fail with a clear setup error.
Use logical requirement groups and keep each profile rooted in its own locked environment.
Live launchers, background workers, certification monitors, and package reporters resolve through
.\.venv-live\Scripts\python.exe unless PHOENIXGUARD_PYTHON_ENV_NAME explicitly selects another profile.
PhoenixGuard must not create or prefer a copied process-host executable.
.codex_runtime is runtime state only. It stores locks, logs, tracker state, screenshots, and
certification evidence; it is not an environment and must not be used as a dependency source.
```

## Profiles

```text
base          shared Python/runtime basics
live          FINAL_LIVE tracker/API/package reporter runtime
decision      MAPIE/scikit/scipy/xgboost decision support
vision        CV/ONNX/YOLO image intelligence
training      model training and sequence/model export work
simulation    replay/backtest/data plotting
business      commercial API, licensing, onboarding, payments
frontend-dev  Playwright/browser testing
docs-pdf      ReportLab/Markdown PDF generation
voice         local voice bundle validation support
dev           full developer/test/Pyright environment
```

## Lock Files

```text
requirements/locks/live-win-py311.txt
requirements/locks/live-linux-py311.txt
requirements/locks/dev-win-py311.txt
requirements/locks/training-win-py311.txt
requirements/locks/business-win-py311.txt
requirements/locks/docs-pdf-win-py311.txt
```

Locks are compiled with `pip-tools` and the backtracking resolver under Python 3.11 on the target
platform. Linux locks must be resolved under Linux rather than copied from a Windows resolution.

## Installers

Profile installers live under:

```text
Backend/scripts_runtime/env/
```

The scripts target:

```text
install_live.ps1     -> .venv-live
install_dev.ps1      -> .venv-dev
install_training.ps1 -> .venv-training
install_business.ps1 -> .venv-business
install_docs.ps1     -> .venv-docs
```

They install the selected lock into its matching profile environment, then run `pip check` and, where
applicable, `Backend/tools/verify_dependency_profile.py`. Run
`Backend/tools/verify_single_venv_runtime.py` after launch to prove all PhoenixGuard Python processes
are using the configured profile environment. Runtime launchers do not create `.venv-live`; the live
environment must already exist before PhoenixGuard is started.

## Live Runtime Boundary

The live `.venv-live` must not contain training, docs, business, and dev-only package stacks. The
live profile does include Chronos Forecasting and Transformers because the scene forecaster loads the
approved local Chronos-2 weights. Runtime safety is enforced by launcher paths, lazy imports, local
artifact checks, runtime profile checks, and the separate locked environment boundary.

The following packages must not become required imports for live startup unless the live runtime
actually needs them:

```text
bitsandbytes
peft
trl
unsloth
tensorflow / tensorflow-cpu / tensorflow-intel / tf-nightly-intel
streamlit
pycaret
langchain
mitmproxy
onnxruntime-gpu
opencv-python
gradio
reportlab
playwright
pytest
sentence-transformers
faiss-cpu
hnswlib
ultralytics
```

`chronos-forecasting` and `transformers` are intentional live requirements. They must remain behind
the lazy Chronos scene-forecaster boundary, load only `models/foundation/chronos-2-small`, and keep
hub/network fallback disabled. Training and dev may include the other model-development packages
inside their own profile environments, but live startup must stay lean by not importing them on the
hot path.

## Optional Adapter Rule

MAPIE and ONNX Runtime stay optional in code through typed dynamic adapters. Missing optional packages
must degrade to the existing fallback path, not break import-time startup.

## Disk Caveat

Only install a profile environment when that role is needed on the machine. The supported
PhoenixGuard development layout is split locked environments with `.venv-live` as the only live
runtime interpreter.
