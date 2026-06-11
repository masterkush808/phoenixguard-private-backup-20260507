from __future__ import annotations

import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main


def test_poll_capture_updates_returns_27_outputs_when_idle(monkeypatch) -> None:
    monkeypatch.setattr(main, "_build_render_config", lambda **_: {})
    monkeypatch.setattr(main, "_get_capture_runtime_snapshot", lambda: {"status_token": 1})
    monkeypatch.setattr(
        main,
        "_get_latest_capture_payload",
        lambda: (0, None, None, "", None, None, None),
    )

    result = main.poll_capture_updates(
        capture_token_state=0,
        capture_status_token_state=1,
        current_result_state={},
        overlay_mode="history-plus-projection",
        min_conf_global=0.42,
        min_conf_latest=0.5,
        history_depth=8,
        label_density=10,
        projection_focus=0.35,
        debug_depth=0.5,
        audit_tab_loaded=False,
        heatmap_tab_loaded=False,
        compare_tab_loaded=False,
    )

    assert len(result) == 27
    assert result[-2:] == (0, 1)


def test_poll_capture_updates_returns_27_outputs_when_only_status_changes(monkeypatch) -> None:
    monkeypatch.setattr(main, "_build_render_config", lambda **_: {})
    monkeypatch.setattr(main, "_get_capture_runtime_snapshot", lambda: {"status_token": 2})
    monkeypatch.setattr(
        main,
        "_get_latest_capture_payload",
        lambda: (0, None, None, "", None, None, None),
    )
    monkeypatch.setattr(main, "build_control_status_html", lambda *_, **__: "status-html")

    result = main.poll_capture_updates(
        capture_token_state=0,
        capture_status_token_state=1,
        current_result_state={},
        overlay_mode="history-plus-projection",
        min_conf_global=0.42,
        min_conf_latest=0.5,
        history_depth=8,
        label_density=10,
        projection_focus=0.35,
        debug_depth=0.5,
        audit_tab_loaded=False,
        heatmap_tab_loaded=False,
        compare_tab_loaded=False,
    )

    assert len(result) == 27
    assert result[12] == "status-html"
    assert result[-2:] == (0, 2)
