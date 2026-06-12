from __future__ import annotations

from .cases import build_adversarial_case


def build_buy_into_supply_case(**kwargs):
    return build_adversarial_case("buy_into_supply", **kwargs)


def build_sell_into_demand_case(**kwargs):
    return build_adversarial_case("sell_into_demand", **kwargs)


__all__ = ["build_buy_into_supply_case", "build_sell_into_demand_case"]
