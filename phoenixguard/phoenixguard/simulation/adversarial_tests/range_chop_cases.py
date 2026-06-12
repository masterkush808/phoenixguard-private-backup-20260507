from __future__ import annotations

from .cases import build_adversarial_case


def build_range_chop_trap_case(**kwargs):
    return build_adversarial_case("range_chop_trap", **kwargs)


def build_mid_range_no_edge_case(**kwargs):
    return build_adversarial_case("mid_range_no_edge", **kwargs)


__all__ = ["build_mid_range_no_edge_case", "build_range_chop_trap_case"]
