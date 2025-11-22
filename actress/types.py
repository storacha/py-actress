from __future__ import annotations

from typing import (
    Any,
    Callable,
    Generic,
    Iterable,
    Iterator,
    Optional,
    List,
    Protocol,
    Sequence,
    TypeAlias,
    TypeVar,
    Union,
)

from .scheduler import SCHEDULER

T = TypeVar("T")
"""Success type of a Task."""

X = TypeVar("X", bound=BaseException)
"""Failure type of a Task."""

M = TypeVar("M")
"""Message type produced by a Task."""

Control: TypeAlias = Any
Instruction: TypeAlias = Union[M, Control]
"""Python equivalent of TS Instruction<T>."""

class Success(Generic[T]):
    """Represents a successful Task completion: { ok: true, value }"""

    ok: bool = True

    def __init__(self, value: T) -> None:
        self.value = value


class Failure(Generic[X]):
    """Represents a failed Task completion: { ok: false, error }"""

    ok: bool = False

    def __init__(self, error: X) -> None:
        self.error = error


Result: TypeAlias = Union[Success[T], Failure[X]]

Message: TypeAlias = M

class ControllerI(Protocol[T, X, M]):
    """Typing mirror of TS Controller<T, X, M>."""

    def throw(self, error: X) -> "TaskState[T, M]":
        ...

    def return_(self, value: T) -> "TaskState[T, M]":
        ...

    def next(self, value: Any) -> "TaskState[T, M]":
        ...
class Task(Protocol[T, X, M]):
    """Typing mirror of TS Task<Success, Failure, Message>."""

    def __iter__(self) -> ControllerI[T, X, M]:
        ...

class TaskState(Protocol[T, M]):
    """Typing-only representation of Task execution state."""

    @property
    def done(self) -> bool:
        ...

    @property
    def value(self) -> Optional[T]:
        ...

    @property
    def instruction(self) -> Optional[Instruction[M]]:
        ...

class Effect(Task[None, None, M], Protocol[M]):
    """Effect<Event> = Task<void, never, Event> in TS."""

    pass

Status = Union["idle", "active", "finished"]

class Stack(Protocol[T, X, M]):
    """Mirror of TS Stack<T,X,M>."""

    active: Sequence[ControllerI[T, X, M]]
    idle: Iterable[ControllerI[T, X, M]]


class Main(Protocol[T, X, M]):
    id: int
    parent: None
    status: Status
    stack: Stack[T, X, M]


class TaskGroup(Protocol[T, X, M]):
    id: int
    parent: Union[Main[T, X, M], "TaskGroup[T, X, M]"]
    driver: ControllerI[T, X, M]
    stack: Stack[T, X, M]
    result: Optional[Result[T, X]]


Group: TypeAlias = Union[Main[T, X, M], TaskGroup[T, X, M]]

class Future(Protocol[T, X]):
    """Mirror of TS lazy Future<T,X>."""

    def then(
        self,
        handle: Optional[Callable[[T], Any]] = None,
        onrejected: Optional[Callable[[X], Any]] = None,
    ) -> "Future[Any, Any]":
        ...

    def catch(self, handle: Callable[[X], Any]) -> "Future[Any, None]":
        ...

    def finally_(self, handle: Callable[[], None]) -> "Future[T, X]":
        ...

class ForkI(
    ControllerI[T, X, M],
    Task["ForkI[T, X, M]", None, None],
    Future[T, X],
    Protocol[T, X, M],
):
    """Typing mirror of TS Fork<T, X, M>."""

    id: int
    group: Optional[TaskGroup[T, X, M]]
    result: Optional[Result[T, X]]
    status: Status

    def resume(self) -> Task[None, None, None]:
        ...
    def join(self) -> Task[T, X, M]:
        ...
    def abort(self, error: X) -> Task[None, None, None]:
        ...
    def exit(self, value: T) -> Task[None, None, None]:
        ...

class ForkOptions(Protocol):
    name: Optional[str]

class StateHandler(Protocol[T, X]):
    onsuccess: Optional[Callable[[T], None]]
    onfailure: Optional[Callable[[X], None]]

class Fork:
    """
    Runtime Fork implementation. Now type-compatible with ForkI[T,X,M].
    """

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
    """Internal callback task used by the scheduler."""

    def __init__(self, fn: Callable[[], None]) -> None:
        self.fn = fn
        self._done = False

    def _step(self) -> None:
        self.fn()
        self._done = True


class Controller:
    """Runtime controller."""

    def __init__(self, resume_cb: Callable[[Any], None]) -> None:
        self._resume_cb = resume_cb

    def resume(self, value: Any = None) -> None:
        self._resume_cb(value)
