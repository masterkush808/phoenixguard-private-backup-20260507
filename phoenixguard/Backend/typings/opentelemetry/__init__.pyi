from typing import Any

class _TraceApi:
    def set_tracer_provider(self, tracer_provider: Any) -> None: ...
    def get_tracer_provider(self) -> Any: ...

trace: _TraceApi

