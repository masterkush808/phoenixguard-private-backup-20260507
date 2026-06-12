from __future__ import annotations

import json
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request


class VoiceRemoteClientError(RuntimeError):
    pass


class WindowTrackerRemoteClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: int = 8,
        bearer_token: str = "",
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout_sec = max(1, int(timeout_sec))
        self.bearer_token = str(bearer_token or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise VoiceRemoteClientError("Tracker API base URL is not configured.")
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(dict(payload), ensure_ascii=True).encode("utf-8")
        req = urllib_request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=self._headers(),
            method=method.upper(),
        )
        try:
            with urllib_request.urlopen(req, timeout=self.timeout_sec) as response:
                raw = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise VoiceRemoteClientError(f"Tracker API request failed: {exc.code} {detail}") from exc
        except urllib_error.URLError as exc:
            raise VoiceRemoteClientError(f"Tracker API is unreachable: {exc.reason}") from exc
        try:
            parsed = json.loads(raw)
        except Exception as exc:
            raise VoiceRemoteClientError("Tracker API returned invalid JSON.") from exc
        return dict(parsed) if isinstance(parsed, dict) else {"payload": parsed}

    def health(self) -> dict[str, Any]:
        return self._request("/v1/mobile/health")

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request(f"/v1/mobile/window-tracker/sessions/{session_id}")

    def start_session(self, session_id: str) -> dict[str, Any]:
        return self._request(f"/v1/mobile/window-tracker/sessions/{session_id}/start", method="POST")

    def stop_session(self, session_id: str) -> dict[str, Any]:
        return self._request(f"/v1/mobile/window-tracker/sessions/{session_id}/stop", method="POST")

    def capture_once(self, session_id: str) -> dict[str, Any]:
        return self._request(f"/v1/mobile/window-tracker/sessions/{session_id}/capture-once", method="POST")

    def update_interval(self, session_id: str, *, capture_interval_sec: float) -> dict[str, Any]:
        return self._request(
            f"/v1/mobile/window-tracker/sessions/{session_id}/controls",
            method="PATCH",
            payload={"capture_interval_sec": float(capture_interval_sec)},
        )
