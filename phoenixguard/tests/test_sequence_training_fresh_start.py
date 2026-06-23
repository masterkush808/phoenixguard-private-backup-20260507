from __future__ import annotations
from pathlib import Path
from typing import NoReturn
import pytest

import torch
from torch import nn

from phoenixguard.training.ensemble_cv_models import EnsembleCVModels


def test_prepare_continual_state_skips_existing_bundle_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle_path = tmp_path / "mobilenetv3_finetuned.pkl"
    bundle_path.write_bytes(b"unused bundle payload")

    ensemble = EnsembleCVModels(
        image_dirs=[],
        device=torch.device("cpu"),
        target_models=["mobilenetv3"],
        enable_continual_learning=False,
    )

    def _unexpected_torch_load(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("torch.load should not run when continual learning is disabled")

    monkeypatch.setattr(torch, "load", _unexpected_torch_load)

    state = ensemble._prepare_continual_state(
        name="mobilenetv3",
        model=nn.Linear(4, 4),
        head=nn.Linear(4, 2),
        aux_head=None,
        model_dir=tmp_path,
    )

    assert state.enabled is False
    assert state.previous_bundle_path == ""
    assert state.replay_samples == []
    assert state.used_lora is False
    assert ensemble.continual_states["mobilenetv3"].enabled is False
