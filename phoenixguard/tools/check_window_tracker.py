from pathlib import Path
import sys

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phoenixguard.mobile_api import app as mobile_app
from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService
from phoenixguard.core.config import RUNTIME

service = mobile_app._window_tracker_service()
print("default service.root_dir:", service.root_dir)
print("default service.sessions_dir:", service.sessions_dir)

alt_root = RUNTIME.project_root / "data" / "window_tracker"
print("alt_root:", alt_root)
service2 = ContinuousWindowTrackerService(root_dir=alt_root)
print("service2.root_dir:", service2.root_dir)
print("service2.sessions_dir:", service2.sessions_dir)
print("sessions in service2:")
for p in sorted(service2.sessions_dir.glob("*/session.json")):
    print(" -", p)

try:
    p = service2.latest_artifact_path("debug-tracker", "chart")
    print("ARTIFACT_PATH:", p)
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))
else:
    try:
        b = p.read_bytes()
        print("bytes_len:", len(b))
        print("png_header:", b[:8].hex())
        expected = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        print("is_png:", b.startswith(expected))
    except Exception as ex:
        print("READ_ERROR:", type(ex).__name__, str(ex))
