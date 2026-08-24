from typing import Any


class YOLO:
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def __getattr__(self, name: str) -> Any: ...


__all__: tuple[str, ...] = ("YOLO",)
