# Final Import Rewrite Report

Generated: 2026-06-25 17:10:12 +05:30

## Result

Imports and entrypoints were rewritten for the staged layout.

## Key Changes

- Package import root moved to Backend/src/phoenixguard.
- pyproject.toml, pytest.ini, and pyrightconfig.json now know the Backend/src layout.
- Root compatibility launchers/scripts call _pg_bootstrap.ensure_project_paths() or set PYTHONPATH explicitly.
- Backend tools use Backend/tools/_bootstrap.py for direct CLI execution.
- Business API imports now use Business.api where needed.
- Developer scripts and moved runtime/data scripts have bootstrap support and package __init__.py files.

## Verification

- Direct V3 integrity tool execution passes from the moved path.
- Full pytest passes on the current tree.
- Pyright reports zero errors/warnings.
