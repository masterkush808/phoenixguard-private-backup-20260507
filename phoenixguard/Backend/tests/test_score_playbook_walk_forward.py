from __future__ import annotations

import json
from pathlib import Path

from Backend.tools import score_playbook_walk_forward as scorer


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def test_walk_forward_scores_buy_and_aligns_replay_truth(tmp_path: Path) -> None:
    samples = tmp_path / "samples.jsonl"
    entries = tmp_path / "entry_events.jsonl"
    truth = tmp_path / "truth.json"
    _write_jsonl(
        samples,
        [
            {
                "seq": 1,
                "captured_epoch": 1000.0,
                "frames": {"display_frame_id": 10},
                "freshness": {"fresh": True},
                "price_proxy": {"current_y": 100.0},
                "entry": {"side": "BUY"},
            },
            {
                "seq": 2,
                "captured_epoch": 1061.0,
                "frames": {"display_frame_id": 11},
                "freshness": {"fresh": True},
                "price_proxy": {"current_y": 90.0},
                "entry": {"side": "BUY"},
            },
        ],
    )
    _write_jsonl(
        entries,
        [
            {
                "seq": 1,
                "frame": 10,
                "captured_epoch": 1000.0,
                "entry": {
                    "allowed": True,
                    "execution_authorized": True,
                    "side": "BUY",
                    "packet_id": "pgpkt_buy_1",
                    "lane_name": "WAVE_RIDING_CONTINUATION",
                },
            }
        ],
    )
    truth.write_text(
        json.dumps(
            {
                "markers": [
                    {
                        "kind": "WOULD_HAVE_ENTERED",
                        "side": "BUY",
                        "label": "WOULD HAVE ENTERED",
                        "seq": 1,
                        "frame": 10,
                        "epoch": 1000.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = scorer.score_burn(
        burn_dir=tmp_path,
        samples_path=samples,
        entries_path=entries,
        truth_paths=[truth],
        raw_dir=None,
        out_dir=tmp_path / "score",
        horizons_sec=(60,),
        include_stale=False,
        include_blocked_trend_study=False,
        min_move_px=3.0,
        truth_window_sec=120.0,
        truth_window_seq=3,
    )

    summary = result["summary"]
    score = result["scores"][0]
    assert summary["candidate_count"] == 1
    assert summary["horizon_counts"]["60"]["correct"] == 1
    assert score["replay_truth"]["alignment_status"] == "ALIGNED_TO_REPLAY_ENTRY"
    assert score["path"]["mfe_px"] == 10.0


def test_walk_forward_scores_sell_direction_with_y_increase(tmp_path: Path) -> None:
    samples = tmp_path / "samples.jsonl"
    entries = tmp_path / "entry_events.jsonl"
    _write_jsonl(
        samples,
        [
            {"seq": 1, "captured_epoch": 1000.0, "freshness": {"fresh": True}, "price_proxy": {"current_y": 100.0}},
            {"seq": 2, "captured_epoch": 1061.0, "freshness": {"fresh": True}, "price_proxy": {"current_y": 117.0}},
        ],
    )
    _write_jsonl(
        entries,
        [{"seq": 1, "captured_epoch": 1000.0, "entry": {"allowed": True, "side": "SELL", "lane_name": "SNIPER_ZONE_ENTRY"}}],
    )

    result = scorer.score_burn(
        burn_dir=tmp_path,
        samples_path=samples,
        entries_path=entries,
        truth_paths=[],
        raw_dir=None,
        out_dir=tmp_path / "score",
        horizons_sec=(60,),
        include_stale=False,
        include_blocked_trend_study=False,
        min_move_px=3.0,
        truth_window_sec=120.0,
        truth_window_seq=3,
    )

    assert result["summary"]["horizon_counts"]["60"]["correct"] == 1
    assert result["scores"][0]["path"]["mfe_px"] == 17.0


def test_stale_samples_are_excluded_by_default(tmp_path: Path) -> None:
    samples = tmp_path / "samples.jsonl"
    entries = tmp_path / "entry_events.jsonl"
    _write_jsonl(
        samples,
        [
            {"seq": 1, "captured_epoch": 1000.0, "freshness": {"fresh": True}, "price_proxy": {"current_y": 100.0}},
            {"seq": 2, "captured_epoch": 1061.0, "freshness": {"fresh": False}, "price_proxy": {"current_y": 80.0}},
        ],
    )
    _write_jsonl(entries, [{"seq": 1, "captured_epoch": 1000.0, "entry": {"allowed": True, "side": "BUY"}}])

    result = scorer.score_burn(
        burn_dir=tmp_path,
        samples_path=samples,
        entries_path=entries,
        truth_paths=[],
        raw_dir=None,
        out_dir=tmp_path / "score",
        horizons_sec=(60,),
        include_stale=False,
        include_blocked_trend_study=False,
        min_move_px=3.0,
        truth_window_sec=120.0,
        truth_window_seq=3,
    )

    assert result["summary"]["stale_excluded_sample_count"] == 1
    assert result["summary"]["horizon_counts"]["60"]["insufficient_future"] == 1


def test_extracts_would_have_truth_markers_from_raw_payload(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_file = raw_dir / "00001_live.json"
    raw_file.write_text(
        json.dumps(
            {
                "response": {
                    "json": {
                        "overlay_objects": [
                            {"display_label": "WOULD HAVE ENTERED", "side": "SELL", "bbox": [10, 20, 30, 40]},
                            {"display_label": "WOULD HAVE EXITED", "side": "SELL", "bbox": [50, 60, 70, 80]},
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    markers = scorer.load_truth_markers([], raw_dir=raw_dir)

    assert [marker["kind"] for marker in markers] == ["WOULD_HAVE_ENTERED", "WOULD_HAVE_EXITED"]
    assert markers[0]["side"] == "SELL"
    assert markers[0]["y"] == 30.0


def test_missed_truth_marker_is_reported(tmp_path: Path) -> None:
    samples = tmp_path / "samples.jsonl"
    entries = tmp_path / "entry_events.jsonl"
    truth = tmp_path / "truth.json"
    _write_jsonl(samples, [{"seq": 1, "captured_epoch": 1000.0, "freshness": {"fresh": True}, "price_proxy": {"current_y": 100.0}}])
    _write_jsonl(entries, [])
    truth.write_text(
        json.dumps({"markers": [{"kind": "WOULD_HAVE_ENTERED", "side": "BUY", "seq": 1, "epoch": 1000.0}]}),
        encoding="utf-8",
    )

    result = scorer.score_burn(
        burn_dir=tmp_path,
        samples_path=samples,
        entries_path=entries,
        truth_paths=[truth],
        raw_dir=None,
        out_dir=tmp_path / "score",
        horizons_sec=(60,),
        include_stale=False,
        include_blocked_trend_study=False,
        min_move_px=3.0,
        truth_window_sec=120.0,
        truth_window_seq=3,
    )

    assert result["summary"]["missed_truth_entry_count"] == 1
    assert result["missed_truth_entries"][0]["side"] == "BUY"
