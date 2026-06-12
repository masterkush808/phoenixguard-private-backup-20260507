from __future__ import annotations

from .cases import build_adversarial_case


def build_steep_impulse_trap_case(**kwargs):
    return build_adversarial_case("steep_impulse_trap", **kwargs)


__all__ = ["build_steep_impulse_trap_case"]
