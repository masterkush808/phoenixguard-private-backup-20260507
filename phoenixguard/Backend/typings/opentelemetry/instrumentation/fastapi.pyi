from typing import Any


class FastAPIInstrumentor:
    @classmethod
    def instrument_app(cls, app: Any, **kwargs: Any) -> None: ...
