from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main


class _FakePersonal:
    def __init__(self) -> None:
        self.feedback_calls: list[dict[str, str]] = []
        self.context_calls: list[dict[str, str]] = []

    def record_feedback(self, image_hash: str, chosen: str, rejected: str, reason: str, annotation_text: str) -> None:
        self.feedback_calls.append(
            {
                "image_hash": image_hash,
                "chosen": chosen,
                "rejected": rejected,
                "reason": reason,
                "annotation_text": annotation_text,
            }
        )

    def record_context_feedback(
        self,
        context_key: str,
        context_descriptor: str,
        chosen: str,
        reason: str,
        annotation_text: str = "",
    ) -> None:
        self.context_calls.append(
            {
                "context_key": context_key,
                "context_descriptor": context_descriptor,
                "chosen": chosen,
                "reason": reason,
                "annotation_text": annotation_text,
            }
        )


class _FakeContinualLearning:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_feedback(self, image_hash: str, verdict: str, reason: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(
            {
                "image_hash": image_hash,
                "verdict": verdict,
                "reason": reason,
                **kwargs,
            }
        )
        return {
            "context_key": "ctx|buy",
            "context_descriptor": "buy continuation context",
            "snapshot_path": str(kwargs.get("feedback_image_path", "")),
            "success": True,
        }


class _FakeRL:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_feedback(self, image_hash: str, actual_outcome: str, reason: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(
            {
                "image_hash": image_hash,
                "actual_outcome": actual_outcome,
                "reason": reason,
                **kwargs,
            }
        )
        return {"updated": False, "feedback_count": 1}


def test_on_feedback_saves_result_image_and_routes_it_into_learning(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source_chart.png"
    Image.new("RGB", (40, 24), color=(12, 34, 56)).save(source_path)

    feedback_image = Image.new("RGB", (48, 32), color=(180, 90, 24))
    fake_personal = _FakePersonal()
    fake_continual = _FakeContinualLearning()
    fake_rl = _FakeRL()

    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")
    monkeypatch.setattr(main, "_get_personal", lambda: fake_personal)
    monkeypatch.setattr(main, "_get_continual_learning", lambda: fake_continual)
    monkeypatch.setattr(main, "_get_rl_engine", lambda: fake_rl)
    monkeypatch.setattr(main, "_get_memory_bank", lambda: None)

    status = main.on_feedback(str(source_path), "BUY", "annotated breakout retest", feedback_image)

    assert "Feedback captured and queued for learning." in status
    assert "Result image saved to" in status
    assert fake_personal.feedback_calls
    assert "feedback_result_image" in fake_personal.feedback_calls[0]["annotation_text"]
    assert fake_continual.calls
    feedback_image_path = str(fake_continual.calls[0]["feedback_image_path"])
    assert feedback_image_path
    assert Path(feedback_image_path).exists()
    assert fake_rl.calls[0]["feedback_image_path"] == feedback_image_path

    feed_rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "feedback_feed.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert feed_rows[-1]["feedback_image"]["path"] == feedback_image_path
    assert feed_rows[-1]["learning_snapshot_path"] == feedback_image_path
