from __future__ import annotations

from _pg_bootstrap import ensure_project_paths
ensure_project_paths()

from phoenixguard.vision import grounded_backends as _impl

globals().update({name: getattr(_impl, name) for name in dir(_impl) if not name.startswith("__")})
