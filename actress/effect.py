from typing import Any, Callable, List, Optional

class Effect:
    def run(self, callback: Callable[[Any], None]) -> None:
        raise NotImplementedError("Effect subclasses must implement run()")

class NoneEffect(Effect):
    def run(self, callback: Callable[[Any], None]) -> None:
        callback(None)

class AggregateEffect(Effect):
    def __init__(self, effects: List[Effect]) -> None:
        self.effects = effects

    def run(self, callback: Callable[[Any], None]) -> None:
        if not self.effects:
            callback([])
            return
        results: List[Optional[Any]] = [None] * len(self.effects)
        remaining = {"n": len(self.effects)}
        def make_cb(i: int):
            def _cb(value: Any) -> None:
                if results[i] is None:
                    results[i] = value
                remaining["n"] -= 1
                if remaining["n"] == 0:
                    callback([r for r in results])
            return _cb
        for i, eff in enumerate(self.effects):
            eff.run(make_cb(i))
