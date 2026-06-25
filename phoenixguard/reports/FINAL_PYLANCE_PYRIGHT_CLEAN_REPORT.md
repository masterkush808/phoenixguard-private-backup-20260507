# Final Pyright/Pylance Clean Report

Generated: 2026-06-25 17:10:12 +05:30

## Result

python -m pyright completed successfully for the restructured tree.

`	ext
0 errors, 0 warnings, 0 informations
`

## Scope

The Pyright config covers:

- Backend/src/phoenixguard
- Backend/tools
- Backend/tests
- Backend/scripts_runtime
- Backend/scripts_data
- Business/api
- Developer script areas

Moved tool entrypoints now explicitly bootstrap Backend/src through Backend/tools/_bootstrap.py so direct script execution does not depend on an external PYTHONPATH.
