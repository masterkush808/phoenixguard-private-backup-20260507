from typing import Any

COLOR_BGR2RGB: int
COLOR_RGB2BGR: int
COLOR_BGR2GRAY: int
COLOR_RGB2GRAY: int
THRESH_BINARY: int
THRESH_OTSU: int
INTER_AREA: int
INTER_LINEAR: int
RETR_EXTERNAL: int
CHAIN_APPROX_SIMPLE: int
FONT_HERSHEY_SIMPLEX: int


def __getattr__(name: str) -> Any: ...
