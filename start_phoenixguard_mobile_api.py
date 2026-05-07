from __future__ import annotations

import os
import logging
from pathlib import Path

import uvicorn


if __name__ == "__main__":
    log_handlers: list[logging.Handler] = [logging.StreamHandler()]
    logs_dir = str(os.getenv("PHOENIXGUARD_LOGS_DIR", "") or "").strip()
    if logs_dir:
        Path(logs_dir).mkdir(parents=True, exist_ok=True)
        log_handlers.append(logging.FileHandler(Path(logs_dir) / "mobile_api.log", encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=log_handlers,
        force=True,
    )
    host = str(os.getenv("PHOENIXGUARD_MOBILE_API_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("PHOENIXGUARD_MOBILE_API_PORT", "8787") or "8787")
    uvicorn.run("phoenixguard.mobile_api.app:create_app", factory=True, host=host, port=port, reload=False)
