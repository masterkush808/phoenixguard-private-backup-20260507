# PhoenixGuard V3 Dependency Governance

## Clear Answer

PhoenixGuard dependencies are grouped by responsibility, but they install into one repo environment:
`.venv`. Backend, frontend tooling, business tooling, training, docs, and live runtime must not create
competing Python environments inside the project.

## Why This Exists

The global Python interpreter on this machine has unrelated package conflicts across TensorFlow,
protobuf, transformers, scikit-learn, plotly, pillow, LangChain, Streamlit, mitmproxy, PyCaret, and
other tooling. PhoenixGuard should not depend on that global environment.

The repo `.venv` currently passes:

```text
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pipdeptree --warn fail
```

The dependency policy is therefore:

```text
Use the repo .venv only.
Do not run PhoenixGuard from global Python.
Do not create .venv-live, .venv-dev, .venv-training, or .venv-business.
Do not auto-create .venv from runtime launchers; if the repo .venv is missing, launch must fail
with a clear setup error.
Use logical requirement groups, but keep the interpreter and installed site-packages rooted in .venv.
Launchers resolve through .\.venv\Scripts\python.exe and use
.\.venv\Scripts\phoenixguard-python.exe as the long-running process host when present. That host is
inside the same .venv; it is not a second environment, and it avoids the Windows venv redirector
child chain.
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
requirements/locks/dev-win-py311.txt
requirements/locks/training-win-py311.txt
requirements/locks/business-win-py311.txt
requirements/locks/docs-pdf-win-py311.txt
```

Locks are compiled with `pip-tools` and the backtracking resolver.

## Installers

Profile installers live under:

```text
Backend/scripts_runtime/env/
```

The scripts all target:

```text
.venv
```

They install the selected lock into `.venv`, then run `pip check` and
`Backend/tools/verify_dependency_profile.py`. They do not create secondary virtual environments.
Runtime launchers do not create `.venv`; the environment must already exist before PhoenixGuard is
started.

## Live Runtime Boundary

The single `.venv` may contain training, docs, business, and dev packages. Live runtime safety is
therefore enforced by launcher paths, lazy imports, optional adapters, and runtime profile checks,
not by creating a second Python environment.

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
transformers
sentence-transformers
faiss-cpu
hnswlib
chronos-forecasting
ultralytics
```

Training and dev may include some of those model-development packages inside `.venv`, but live startup
must stay lean by not importing them on the hot path.

## Optional Adapter Rule

MAPIE and ONNX Runtime stay optional in code through typed dynamic adapters. Missing optional packages
must degrade to the existing fallback path, not break import-time startup.

## Disk Caveat

Do not duplicate Torch/OpenCV/ML wheels into multiple project virtual environments. The supported
PhoenixGuard development layout is one `.venv` plus logical dependency lock files.
