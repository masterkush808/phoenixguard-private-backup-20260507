from __future__ import annotations
from typing import Any
import pytest

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw


_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main


class _FakePersonal:
    def __init__(self) -> None:
        self.feedback_calls: list[dict[str, str]] = []
        self.context_calls: list[dict[str, str]] = []

    def record_feedback(self, image_hash: str, chosen: str, rejected: str, reason: str, annotationtext: str) -> None:
        self.feedback_calls.append(
            {
                "image_hash": image_hash,
                "chosen": chosen,
                "rejected": rejected,
                "reason": reason,
                "annotationtext": annotationtext,
            }
        )

    def record_context_feedback(
        self,
        context_key: str,
        context_descriptor: str,
        chosen: str,
        reason: str,
        annotationtext: str = "",
    ) -> None:
        self.context_calls.append(
            {
                "context_key": context_key,
                "context_descriptor": context_descriptor,
                "chosen": chosen,
                "reason": reason,
                "annotationtext": annotationtext,
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


class _FailingPersonal(_FakePersonal):
    def record_feedback(self, image_hash: str, chosen: str, rejected: str, reason: str, annotationtext: str) -> None:
        raise RuntimeError("preference store offline")


def _editor_payload(image: Image.Image) -> dict[str, object]:
    return {
        "background": image.copy(),
        "layers": [],
        "composite": image.copy(),
    }


def test_on_feedback_saves_result_image_and_routes_it_into_learning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source_chart.png"
    Image.new("RGB", (40, 24), color=(12, 34, 56)).save(source_path)

    feedback_image = _editor_payload(Image.new("RGB", (48, 32), color=(180, 90, 24)))
    fake_personal = _FakePersonal()
    fake_continual = _FakeContinualLearning()
    fake_rl = _FakeRL()

    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")
    monkeypatch.setattr(main.RUNTIME, "models_dir", tmp_path / "models")
    monkeypatch.setattr(main.RUNTIME, "pause_rl_updates", False)
    monkeypatch.setattr(main.RUNTIME, "enable_feedback_learning_feed", True)
    monkeypatch.setattr(main, "_get_personal", lambda: fake_personal)
    monkeypatch.setattr(main, "_get_continual_learning", lambda: fake_continual)
    monkeypatch.setattr(main, "_get_rl_engine", lambda: fake_rl)
    monkeypatch.setattr(main, "_get_memory_bank", lambda: None)

    status = main.on_feedback(
        str(source_path),
        "BUY",
        "annotated breakout retest",
        feedback_image,
        result_state={"action": "BUY", "inference_id": "inf-1"},
    )

    assert "Outcome review captured and stored successfully." in status
    assert "Result image saved to" in status
    assert fake_personal.feedback_calls
    assert "feedback_result_image" in fake_personal.feedback_calls[0]["annotationtext"]
    assert fake_continual.calls
    assert fake_continual.calls[0]["context_id"] == "inf-1"
    feedback_image_path = str(fake_continual.calls[0]["feedback_image_path"])
    assert feedback_image_path
    assert Path(feedback_image_path).exists()
    assert fake_rl.calls[0]["context_id"] == "inf-1"
    assert fake_rl.calls[0]["feedback_image_path"] == feedback_image_path

    feed_rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "feedback_feed.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert feed_rows[-1]["feedback_image"]["path"] == feedback_image_path
    assert feed_rows[-1]["learning_snapshot_path"] == feedback_image_path
    assert feed_rows[-1]["signal_direction"] == "BUY"
    assert feed_rows[-1]["execution_result"] == "WIN"

    journal_rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "feedback_submissions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(row["event_type"] == "submission_created" for row in journal_rows)
    assert any(row["event_type"] == "stage_applied" and row["stage"] == "rl" for row in journal_rows)


def test_on_feedback_survives_partial_learning_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source_chart.png"
    Image.new("RGB", (40, 24), color=(12, 34, 56)).save(source_path)

    feedback_image = _editor_payload(Image.new("RGB", (48, 32), color=(180, 90, 24)))
    failing_personal = _FailingPersonal()
    fake_continual = _FakeContinualLearning()
    fake_rl = _FakeRL()

    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")
    monkeypatch.setattr(main.RUNTIME, "models_dir", tmp_path / "models")
    monkeypatch.setattr(main.RUNTIME, "enable_feedback_learning_feed", True)
    monkeypatch.setattr(main, "_get_personal", lambda: failing_personal)
    monkeypatch.setattr(main, "_get_continual_learning", lambda: fake_continual)
    monkeypatch.setattr(main, "_get_rl_engine", lambda: fake_rl)
    monkeypatch.setattr(main, "_get_memory_bank", lambda: None)

    status = main.on_feedback(str(source_path), "BUY", "annotated breakout retest", feedback_image)

    assert "Outcome review captured and stored successfully." in status
    assert "Some protected save steps reported issues." in status

    feed_rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "feedback_feed.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "personalization" in feed_rows[-1]["errors"]
    assert feed_rows[-1]["continual_learning_updated"] is True


def test_save_feedback_visual_label_persists_editor_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source_chart.png"
    Image.new("RGB", (40, 24), color=(12, 34, 56)).save(source_path)

    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")
    monkeypatch.setattr(main.RUNTIME, "models_dir", tmp_path / "models")

    saved_asset, status, feed_html = main.save_feedback_visual_label(
        str(source_path),
        "BUY",
        _editor_payload(Image.new("RGB", (48, 32), color=(180, 90, 24))),
    )

    assert "Visual label saved to" in status
    assert Path(str(saved_asset["path"])).exists()
    assert Path(str(saved_asset["annotation_path"])).exists()
    assert "Saved visual label ready" in feed_html


def test_on_feedback_uses_feed_learning_when_rl_updates_are_paused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source_chart.png"
    Image.new("RGB", (40, 24), color=(12, 34, 56)).save(source_path)

    fake_personal = _FakePersonal()
    fake_continual = _FakeContinualLearning()
    fake_rl = _FakeRL()

    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")
    monkeypatch.setattr(main.RUNTIME, "models_dir", tmp_path / "models")
    monkeypatch.setattr(main.RUNTIME, "pause_rl_updates", True)
    monkeypatch.setattr(main.RUNTIME, "enable_feedback_learning_feed", True)
    monkeypatch.setattr(main, "_get_personal", lambda: fake_personal)
    monkeypatch.setattr(main, "_get_continual_learning", lambda: fake_continual)
    monkeypatch.setattr(main, "_get_rl_engine", lambda: fake_rl)
    monkeypatch.setattr(main, "_get_memory_bank", lambda: None)

    status = main.on_feedback(
        str(source_path),
        "BUY",
        "feed-driven learning should still update",
        _editor_payload(Image.new("RGB", (48, 32), color=(180, 90, 24))),
    )

    assert "Outcome review captured and stored successfully." in status
    assert fake_continual.calls
    assert fake_rl.calls


def test_save_feedback_visual_label_extracts_semantic_regions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source_chart.png"
    Image.new("RGB", (40, 24), color=(12, 34, 56)).save(source_path)

    layer = Image.new("RGBA", (40, 24), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rectangle((8, 6, 24, 18), fill=(88, 218, 123, 255))
    payload: dict[str, Any] = {
        "background": Image.new("RGBA", (40, 24), color=(12, 34, 56, 255)),
        "layers": [layer],
        "composite": Image.alpha_composite(
            Image.new("RGBA", (40, 24), color=(12, 34, 56, 255)),
            layer,
        ),
    }

    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")
    monkeypatch.setattr(main.RUNTIME, "models_dir", tmp_path / "models")

    saved_asset, status, _feed_html = main.save_feedback_visual_label(str(source_path), "BUY", payload)

    assert "Visual label saved to" in status
    assert int(saved_asset["visual_region_count"]) == 1
    assert "entry_zone" in saved_asset["visual_labels"]

    annotation_payload = json.loads(Path(str(saved_asset["annotation_path"])).read_text(encoding="utf-8"))
    assert annotation_payload["visual_regions"][0]["semantic_label"] == "entry_zone"


def test_on_feedback_structures_loss_as_hold_for_learning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source_chart.png"
    Image.new("RGB", (40, 24), color=(12, 34, 56)).save(source_path)

    feedback_image = _editor_payload(Image.new("RGB", (48, 32), color=(180, 90, 24)))
    fake_personal = _FakePersonal()
    fake_continual = _FakeContinualLearning()
    fake_rl = _FakeRL()

    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")
    monkeypatch.setattr(main.RUNTIME, "models_dir", tmp_path / "models")
    monkeypatch.setattr(main.RUNTIME, "enable_feedback_learning_feed", True)
    monkeypatch.setattr(main, "_get_personal", lambda: fake_personal)
    monkeypatch.setattr(main, "_get_continual_learning", lambda: fake_continual)
    monkeypatch.setattr(main, "_get_rl_engine", lambda: fake_rl)
    monkeypatch.setattr(main, "_get_memory_bank", lambda: None)

    status = main.on_feedback(
        str(source_path),
        "BUY",
        "trade failed in chop",
        feedback_image,
        result_state={"action": "BUY"},
        execution_result="LOSS",
        market_state="CHOPPY",
        setup_state="FAKEOUT",
        failure_mode="FAKEOUT",
        label_confidence_pct=67,
    )

    assert "Outcome review captured and stored successfully." in status
    assert fake_rl.calls[-1]["actual_outcome"] == "HOLD"
    assert fake_rl.calls[-1]["submission_id"]
    confidence = fake_rl.calls[-1]["operator_confidence"]
    assert isinstance(confidence, (int, float, str))
    assert float(confidence) == 0.67

    feed_rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "feedback_feed.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert feed_rows[-1]["signal_direction"] == "BUY"
    assert feed_rows[-1]["execution_result"] == "LOSS"
    assert feed_rows[-1]["market_state"] == "CHOPPY"
    assert feed_rows[-1]["setup_state"] == "FAKEOUT"
    assert feed_rows[-1]["verdict"] == "HOLD"


def test_feedback_resume_replays_pending_submission(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source_chart.png"
    Image.new("RGB", (40, 24), color=(12, 34, 56)).save(source_path)

    fake_personal = _FakePersonal()
    fake_continual = _FakeContinualLearning()
    fake_rl = _FakeRL()

    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")
    monkeypatch.setattr(main.RUNTIME, "models_dir", tmp_path / "models")
    monkeypatch.setattr(main.RUNTIME, "enable_feedback_learning_feed", True)
    monkeypatch.setattr(main, "_get_personal", lambda: fake_personal)
    monkeypatch.setattr(main, "_get_continual_learning", lambda: fake_continual)
    monkeypatch.setattr(main, "_get_rl_engine", lambda: fake_rl)
    monkeypatch.setattr(main, "_get_memory_bank", lambda: None)

    _img_unused, meta = main.load_any_file_as_image(str(source_path))
    asset = main.save_feedback_result_image(
        str(meta["sha256"]),
        "BUY",
        _editor_payload(Image.new("RGB", (48, 32), color=(180, 90, 24))),
    )
    submission = main.build_feedback_submission_payload(
        file_path=str(source_path),
        meta=meta,
        result_state={"action": "BUY"},
        feedback_target={"source_path": str(source_path), "source_image_hash": str(meta["sha256"]), "inference_id": "resume-1", "inference_action": "BUY"},
        signal_direction_selected="BUY",
        execution_result="WIN",
        market_state="TRENDING",
        setup_state="CONTINUATION",
        failure_mode="NONE",
        label_confidence_pct=88,
        notes="resume me",
        feedback_asset=asset,
    )
    main.append_feedback_submission_event(submission["submission_id"], "submission_created", submission=submission)

    main.resume_pending_feedback_submissions_if_needed()

    assert fake_personal.feedback_calls
    assert fake_continual.calls
    assert fake_rl.calls

    states = main.feedback_submission_states()
    assert states[-1]["status"] == "completed"
