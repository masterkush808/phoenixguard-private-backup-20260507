from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterator, Sequence

import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    rank: int = 8
    alpha: float = 16.0
    dropout: float = 0.05


def sanitize_adapter_name(value: str, default: str = "continual_default") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or default


class _LoRAAdapterBase(nn.Module):
    def __init__(self, rank: int, alpha: float, dropout: float) -> None:
        super().__init__()
        self.rank = int(max(rank, 1))
        self.alpha = float(max(alpha, 1.0))
        self.dropout_p = float(max(dropout, 0.0))

    @property
    def scaling(self) -> float:
        return float(self.alpha / max(self.rank, 1))


class LowRankLinearAdapter(_LoRAAdapterBase):
    def __init__(self, base_layer: nn.Linear, rank: int, alpha: float, dropout: float) -> None:
        effective_rank = int(max(1, min(rank, base_layer.in_features, base_layer.out_features)))
        super().__init__(effective_rank, alpha, dropout)
        self.dropout = nn.Dropout(p=self.dropout_p) if self.dropout_p > 0.0 else nn.Identity()
        self.lora_a = nn.Parameter(torch.empty((self.rank, base_layer.in_features), dtype=base_layer.weight.dtype))
        self.lora_b = nn.Parameter(torch.zeros((base_layer.out_features, self.rank), dtype=base_layer.weight.dtype))
        nn.init.kaiming_uniform_(self.lora_a, a=5 ** 0.5)
        nn.init.zeros_(self.lora_b)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        hidden = F.linear(self.dropout(x), self.lora_a)
        return F.linear(hidden, self.lora_b) * self.scaling


class LowRankConv2dAdapter(_LoRAAdapterBase):
    def __init__(self, base_layer: nn.Conv2d, rank: int, alpha: float, dropout: float) -> None:
        effective_rank = int(max(1, min(rank, base_layer.in_channels, base_layer.out_channels)))
        super().__init__(effective_rank, alpha, dropout)
        self.dropout = nn.Dropout2d(p=self.dropout_p) if self.dropout_p > 0.0 else nn.Identity()
        self.down = nn.Conv2d(
            in_channels=base_layer.in_channels,
            out_channels=self.rank,
            kernel_size=base_layer.kernel_size,
            stride=base_layer.stride,
            padding=base_layer.padding,
            dilation=base_layer.dilation,
            groups=base_layer.groups,
            bias=False,
        )
        self.up = nn.Conv2d(
            in_channels=self.rank,
            out_channels=base_layer.out_channels,
            kernel_size=1,
            stride=1,
            padding=0,
            bias=False,
        )
        nn.init.kaiming_uniform_(self.down.weight, a=5 ** 0.5)
        nn.init.zeros_(self.up.weight)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.up(self.dropout(self.down(x))) * self.scaling


class LoRALinear(nn.Module):
    def __init__(self, base_layer: nn.Linear) -> None:
        super().__init__()
        self.base_layer = base_layer
        self.adapters = nn.ModuleDict()
        self.active_adapter_name = ""

    def add_adapter(self, name: str, config: AdapterConfig) -> None:
        adapter_name = sanitize_adapter_name(name)
        if adapter_name not in self.adapters:
            self.adapters[adapter_name] = LowRankLinearAdapter(
                self.base_layer,
                rank=config.rank,
                alpha=config.alpha,
                dropout=config.dropout,
            )
        self.active_adapter_name = adapter_name

    def has_adapter(self, name: str) -> bool:
        return sanitize_adapter_name(name) in self.adapters

    def set_active_adapter(self, name: str | None) -> bool:
        if not name:
            self.active_adapter_name = ""
            return True
        adapter_name = sanitize_adapter_name(name)
        if adapter_name not in self.adapters:
            return False
        self.active_adapter_name = adapter_name
        return True

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.base_layer(x)
        if self.active_adapter_name and self.active_adapter_name in self.adapters:
            out = out + self.adapters[self.active_adapter_name](x)
        return out


class LoRAConv2d(nn.Module):
    def __init__(self, base_layer: nn.Conv2d) -> None:
        super().__init__()
        self.base_layer = base_layer
        self.adapters = nn.ModuleDict()
        self.active_adapter_name = ""

    def add_adapter(self, name: str, config: AdapterConfig) -> None:
        adapter_name = sanitize_adapter_name(name)
        if adapter_name not in self.adapters:
            self.adapters[adapter_name] = LowRankConv2dAdapter(
                self.base_layer,
                rank=config.rank,
                alpha=config.alpha,
                dropout=config.dropout,
            )
        self.active_adapter_name = adapter_name

    def has_adapter(self, name: str) -> bool:
        return sanitize_adapter_name(name) in self.adapters

    def set_active_adapter(self, name: str | None) -> bool:
        if not name:
            self.active_adapter_name = ""
            return True
        adapter_name = sanitize_adapter_name(name)
        if adapter_name not in self.adapters:
            return False
        self.active_adapter_name = adapter_name
        return True

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.base_layer(x)
        if self.active_adapter_name and self.active_adapter_name in self.adapters:
            out = out + self.adapters[self.active_adapter_name](x)
        return out


def _iter_wrapped_modules(module: nn.Module, prefix: str = "") -> Iterator[tuple[str, LoRALinear | LoRAConv2d]]:
    for name, child in module.named_children():
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(child, (LoRALinear, LoRAConv2d)):
            yield path, child
        yield from _iter_wrapped_modules(child, path)


def _module_at_path(module: nn.Module, path: str) -> nn.Module:
    return module.get_submodule(path) if path else module


def collect_adaptable_module_paths(
    module: nn.Module,
    *,
    root_paths: Sequence[str] | None = None,
    include_conv2d: bool = True,
) -> list[str]:
    roots = tuple(str(item).strip() for item in (root_paths or ()) if str(item).strip())
    selected: list[str] = []
    for path, child in module.named_modules():
        if not path:
            continue
        if roots and not any(path == root or path.startswith(f"{root}.") for root in roots):
            continue
        if isinstance(child, nn.Linear):
            selected.append(path)
        elif (
            include_conv2d
            and isinstance(child, nn.Conv2d)
            and int(child.groups) == 1
        ):
            selected.append(path)
    return sorted(set(selected))


def apply_lora_adapters(
    module: nn.Module,
    *,
    adapter_name: str,
    target_paths: Sequence[str],
    config: AdapterConfig,
) -> dict[str, Any]:
    normalized_name = sanitize_adapter_name(adapter_name)
    wrapped_paths: list[str] = []
    linear_count = 0
    conv_count = 0

    for path in sorted({str(item).strip() for item in target_paths if str(item).strip()}):
        if "." in path:
            parent_path, child_name = path.rsplit(".", 1)
            parent = _module_at_path(module, parent_path)
        else:
            parent = module
            child_name = path
        child = getattr(parent, child_name, None)
        if isinstance(child, LoRALinear):
            child.add_adapter(normalized_name, config)
            wrapped_paths.append(path)
            linear_count += 1
            continue
        if isinstance(child, LoRAConv2d):
            child.add_adapter(normalized_name, config)
            wrapped_paths.append(path)
            conv_count += 1
            continue
        if isinstance(child, nn.Linear):
            wrapped = LoRALinear(child)
            wrapped.add_adapter(normalized_name, config)
            setattr(parent, child_name, wrapped)
            wrapped_paths.append(path)
            linear_count += 1
            continue
        if isinstance(child, nn.Conv2d) and int(child.groups) == 1:
            wrapped = LoRAConv2d(child)
            wrapped.add_adapter(normalized_name, config)
            setattr(parent, child_name, wrapped)
            wrapped_paths.append(path)
            conv_count += 1

    return {
        "adapter_name": normalized_name,
        "target_paths": sorted(set(wrapped_paths)),
        "wrapped_linear": int(linear_count),
        "wrapped_conv2d": int(conv_count),
    }


def available_adapters(module: nn.Module) -> list[str]:
    names: set[str] = set()
    for _, wrapped in _iter_wrapped_modules(module):
        names.update(str(name) for name in wrapped.adapters.keys())
    return sorted(names)


def set_active_adapter(module: nn.Module, adapter_name: str | None) -> bool:
    success = False
    for _, wrapped in _iter_wrapped_modules(module):
        result = wrapped.set_active_adapter(adapter_name)
        success = bool(success or result)
    return success


def set_adapter_trainable(
    module: nn.Module,
    *,
    adapter_name: str | None,
    trainable: bool,
    freeze_others: bool = True,
) -> int:
    normalized_name = sanitize_adapter_name(adapter_name or "")
    count = 0
    for _, wrapped in _iter_wrapped_modules(module):
        for param in wrapped.base_layer.parameters():
            param.requires_grad = False
        for name, adapter in wrapped.adapters.items():
            should_train = bool(trainable and name == normalized_name)
            if not freeze_others and trainable:
                should_train = True
            for param in adapter.parameters():
                param.requires_grad = should_train
                if should_train:
                    count += 1
    return count


def collect_lora_summary(module: nn.Module) -> dict[str, Any]:
    target_paths: list[str] = []
    adapter_names: set[str] = set()
    adapter_specs: dict[str, dict[str, float | int]] = {}
    active_adapter = ""

    for path, wrapped in _iter_wrapped_modules(module):
        target_paths.append(path)
        if not active_adapter and wrapped.active_adapter_name:
            active_adapter = str(wrapped.active_adapter_name)
        for name, adapter in wrapped.adapters.items():
            adapter_names.add(str(name))
            adapter_specs.setdefault(
                str(name),
                {
                    "rank": int(getattr(adapter, "rank", 0)),
                    "alpha": float(getattr(adapter, "alpha", 1.0)),
                    "dropout": float(getattr(adapter, "dropout_p", 0.0)),
                },
            )

    return {
        "enabled": bool(target_paths),
        "active_adapter": active_adapter,
        "available_adapters": sorted(adapter_names),
        "target_paths": sorted(set(target_paths)),
        "adapter_specs": adapter_specs,
    }


def has_lora_adapters(module: nn.Module) -> bool:
    return any(True for _path, _wrapped in _iter_wrapped_modules(module))

