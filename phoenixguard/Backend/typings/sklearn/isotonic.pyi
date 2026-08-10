from typing import Any


class IsotonicRegression:
    def __init__(
        self,
        *,
        out_of_bounds: str = ...,
        y_min: float | None = ...,
        y_max: float | None = ...,
        **kwargs: Any,
    ) -> None: ...
    def fit(self, X: Any, y: Any) -> IsotonicRegression: ...
    def predict(self, X: Any) -> Any: ...
