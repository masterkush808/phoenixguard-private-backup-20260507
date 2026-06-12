from __future__ import annotations

from .cases import build_adversarial_case


def build_fake_breakout_trap_case(**kwargs):
    return build_adversarial_case("fake_breakout_trap", **kwargs)


def build_fake_breakdown_trap_case(**kwargs):
    return build_adversarial_case("fake_breakdown_trap", **kwargs)


__all__ = ["build_fake_breakout_trap_case", "build_fake_breakdown_trap_case"]
