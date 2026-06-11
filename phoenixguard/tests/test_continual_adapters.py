from __future__ import annotations

from pathlib import Path
import sys

import torch
import torch.nn as nn


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.runtime.continual_adapters import (
    AdapterConfig,
    apply_lora_adapters,
    available_adapters,
    collect_lora_summary,
    set_active_adapter,
    set_adapter_trainable,
)


class _ToyModel(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.block = nn.Sequential(
            nn.Linear(4, 4),
            nn.ReLU(),
            nn.Linear(4, 2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


def test_lora_adapters_wrap_and_switch_active_context() -> None:
    model = _ToyModel()
    config = AdapterConfig(rank=2, alpha=4.0, dropout=0.0)
    applied = apply_lora_adapters(
        model,
        adapter_name="dark|M5",
        target_paths=["block.0", "block.2"],
        config=config,
    )
    assert applied["wrapped_linear"] == 2
    assert "dark_M5" in available_adapters(model)

    apply_lora_adapters(
        model,
        adapter_name="light|M1",
        target_paths=["block.0", "block.2"],
        config=config,
    )
    assert "light_M1" in available_adapters(model)

    summary = collect_lora_summary(model)
    assert bool(summary["enabled"]) is True
    assert sorted(summary["target_paths"]) == ["block.0", "block.2"]

    for module in model.modules():
        adapters = getattr(module, "adapters", None)
        if adapters is None:
            continue
        for name, adapter in adapters.items():
            for param in adapter.parameters():
                torch.nn.init.constant_(param, 0.2 if name == "dark_M5" else 0.05)

    x = torch.ones((1, 4), dtype=torch.float32)
    set_active_adapter(model, "dark|M5")
    out_dark = model(x)
    set_active_adapter(model, "light|M1")
    out_light = model(x)

    assert not torch.allclose(out_dark, out_light)

    trainable_count = set_adapter_trainable(model, adapter_name="light|M1", trainable=True)
    assert trainable_count > 0
    base_trainable = [
        param.requires_grad
        for name, param in model.named_parameters()
        if ".adapters." not in name
    ]
    assert not any(base_trainable)
