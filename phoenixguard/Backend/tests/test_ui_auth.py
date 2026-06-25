from __future__ import annotations

import sys
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main


def testui_auth_credentials_noop_when_auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIXGUARD_SHARE_CREDENTIALS", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_SHARE_USERNAME", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_SHARE_PASSWORD", raising=False)

    credentials = main.ui_auth_credentials(
        require_auth=False,
        strict_passwords=False,
    )

    assert credentials == []


def testui_auth_credentials_reads_multiple_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "PHOENIXGUARD_SHARE_CREDENTIALS",
        "operator:StrongPass2026!,brother:BrotherPass2026!",
    )
    monkeypatch.delenv("PHOENIXGUARD_SHARE_USERNAME", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_SHARE_PASSWORD", raising=False)

    credentials = main.ui_auth_credentials(
        require_auth=True,
        strict_passwords=True,
    )

    assert credentials == [
        ("operator", "StrongPass2026!"),
        ("brother", "BrotherPass2026!"),
    ]


def testui_auth_credentials_rejects_weak_passwords_when_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_SHARE_CREDENTIALS", "operator:weakpass")
    monkeypatch.delenv("PHOENIXGUARD_SHARE_USERNAME", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_SHARE_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="too weak"):
        main.ui_auth_credentials(
            require_auth=True,
            strict_passwords=True,
        )


def testresolve_ui_launch_auth_uses_share_credentials_for_tunnel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_UI_REQUIRE_AUTH", "1")
    monkeypatch.setenv("PHOENIXGUARD_SHARE_CREDENTIALS", "operator:StrongPass2026!")
    monkeypatch.delenv("PHOENIXGUARD_SHARE_USERNAME", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_SHARE_PASSWORD", raising=False)

    credentials, auth_message = main.resolve_ui_launch_auth(
        "127.0.0.1",
        share_enabled=True,
    )

    assert credentials == [("operator", "StrongPass2026!")]
    assert auth_message is not None
    assert "Protected PhoenixGuard access" in auth_message
