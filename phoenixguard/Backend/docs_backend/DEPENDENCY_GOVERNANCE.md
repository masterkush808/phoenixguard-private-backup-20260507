# PhoenixGuard V3 Dependency Governance

## Clear Answer

PhoenixGuard dependencies are now split by responsibility instead of being installed as one overloaded
environment. The live runtime uses a lean lock, while development, training, business, frontend testing,
and docs/PDF tooling have their own profiles.

## Why This Exists

The global Python interpreter on this machine has unrelated package conflicts across TensorFlow,
protobuf, transformers, scikit-learn, plotly, pillow, LangChain, Streamlit, mitmproxy, PyCaret, and
other tooling. PhoenixGuard should not depend on that global environment.

The repo `.venv` currently passes:

```text
python -m pip check
python -m pipdeptree --warn fail
```

The dependency policy is therefore:

```text
Use the repo venv or profile-specific venvs.
Do not run PhoenixGuard from global Python.
Do not install training/docs/business/frontend packages into live unless the lock requires them.
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

The scripts create:

```text
.venv-live
.venv-dev
.venv-training
.venv-business
```

They use `pip-sync`, then run `pip check` and `Backend/tools/verify_dependency_profile.py`.

## Live Runtime Boundary

The live lock intentionally excludes unrelated heavy or fragile packages such as:

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

Training and dev may include some of those model-development packages, but live must stay lean.

## Optional Adapter Rule

MAPIE and ONNX Runtime stay optional in code through typed dynamic adapters. Missing optional packages
must degrade to the existing fallback path, not break import-time startup.

## Disk Caveat

At governance time, the C: drive had about 10.47 GB free. Creating `.venv-live`, `.venv-dev`, and
`.venv-training` simultaneously would duplicate Torch/OpenCV/ML wheels and can consume that space
quickly. The locks and installers are ready, but profile venv creation should be done intentionally
when enough disk is available.
