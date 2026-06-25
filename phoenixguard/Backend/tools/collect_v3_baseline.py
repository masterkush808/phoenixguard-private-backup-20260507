from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import sys
from pathlib import Path
from typing import Any, Mapping, cast
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phoenixguard.mobile_api.app import create_app
from fastapi.testclient import TestClient
import json
from phoenixguard.execution.v3_language import public_language_scorecard

app = create_app()
client = TestClient(app)

resp = client.get('/v1/mobile/runtime/trace/v3?session_id=debug-tracker')
raw_trace: object = resp.json() if resp.status_code == 200 else {'error': resp.text, 'status_code': resp.status_code}
trace: dict[str, Any] = dict(cast(Mapping[str, Any], raw_trace)) if isinstance(raw_trace, Mapping) else {"value": raw_trace}
score: object = public_language_scorecard()

out: dict[str, object] = {
    'runtime_trace_status': resp.status_code,
    'alignment': trace.get('alignment'),
    'language_scorecard': score,
}
print(json.dumps(out, indent=2))
Path('.codex_runtime/visual_evidence').mkdir(parents=True, exist_ok=True)
with open('.codex_runtime/visual_evidence/trace_alignment.json', 'w', encoding='utf-8') as fh:
    json.dump(out, fh, indent=2)
