from __future__ import annotations

import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.training import ensemble_cv_models as training_mod


def test_timm_backbone_respects_pretrained_flag(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class _FakeModel:
        def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return None

    def _fake_create_model(model_name: str, *, pretrained: bool, num_classes: int):  # type: ignore[no-untyped-def]
        calls.append(
            {
                "model_name": model_name,
                "pretrained": pretrained,
                "num_classes": num_classes,
            }
        )
        return _FakeModel()

    monkeypatch.setattr(training_mod.timm, "create_model", _fake_create_model)

    training_mod.TimmBackbone("resnet50", pretrained=False)
    training_mod.TimmBackbone("resnet50", pretrained=True)

    assert calls[0]["pretrained"] is False
    assert calls[1]["pretrained"] is True
