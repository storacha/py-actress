from typing import Any, Callable, Optional, List
from .scheduler import SCHEDULER

class Fork:
    def __init__(self, task: Any) -> None:
        self.task = task
        self.done: bool = False
        self.value: Optional[Any] = None
        self.error: Optional[BaseException] = None
        self._listeners: List[Callable[[], None]] = []

    def on_complete(self, fn: Callable[[], None]) -> None:
        if self.done:
            SCHEDULER.enqueue(_CallbackTask(fn))
        else:
            self._listeners.append(fn)

    def _finish(self, value: Any = None, error: Optional[BaseException] = None) -> None:
        self.done = True
        self.value = value
        self.error = error
        listeners = list(self._listeners)
        self._listeners.clear()
        for fn in listeners:
            SCHEDULER.enqueue(_CallbackTask(fn))

    def get(self) -> Any:
        if not self.done:
            raise RuntimeError("Fork is not done yet")
        if self.error is not None:
            raise self.error
        return self.value


class _CallbackTask:
    def __init__(self, fn: Callable[[], None]) -> None:
        self.fn = fn
        self._done = False

    def _step(self) -> None:
        self.fn()
        self._done = True


class Controller:
    def __init__(self, resume_cb: Callable[[Any], None]) -> None:
        self._resume_cb = resume_cb

    def resume(self, value: Any = None) -> None:
        self._resume_cb(value)
