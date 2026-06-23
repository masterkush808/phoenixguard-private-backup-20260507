# Agent 7 Dependency Environment Fixes

CLEAR ANSWER

Dependencies are declared in `requirements.txt`; the repo virtualenv passes `pip check`. The active shell `python` is system Python and lacks ReportLab, so validation was run with `.venv\Scripts\python.exe` where needed.

CONFIDENCE LEVEL

0.86

KEY CAVEATS

`.venv` does not have `pyright` installed even though `requirements.txt` declares it. System Python has Pyright 1.1.410 and was used for static analysis.

FILES STUDIED

`requirements.txt`, `web/package.json`, `pyrightconfig.json`.

ERRORS FOUND

System Python missing ReportLab. `.venv` missing Pyright module. Raw `compileall -q .` enters `.venv` and fails on `aenum/_py2.py`, a Python 2 compatibility file.

FIXES APPLIED

No dependency file changes were needed; required packages were already declared. Verification commands were routed through `.venv` for runtime dependencies.

TESTS RUN

`.venv\Scripts\python.exe -m pip check` passed. `python -m pyright --version` reported 1.1.410.

REMAINING RISKS

Install Pyright into `.venv` for hermetic static analysis, or standardize command docs to use system Pyright with `.venv` configured in `pyrightconfig.json`.
