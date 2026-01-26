from typing import Any, Generator, Generic, Iterable, Iterator, Literal, Protocol, TypeVar, Union, Optional, cast, runtime_checkable

class Symbol:
    """Symbolic global constant"""

    __slots__ = ["_name", "_module"]
    _name: str
    _module: str

    def __init__(self, name: str, moduleName: str) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_module", moduleName)

    def __reduce__(self) -> str:
        return self._name

    def __setattr__(self, attr: str, val: Any) -> None:
        raise TypeError("Symbols are immutable")

    def __repr__(self) -> str:
        return self._name

    __str__ = __repr__


CURRENT = Symbol("current", __name__)
SUSPEND = Symbol("suspend", __name__)

Control = Symbol

Success = TypeVar("Success", covariant=True)
Failure = TypeVar("Failure", covariant=True)
Message = TypeVar("Message", covariant=True)

# Instruction<T> = Message<T> | Control
Instruction = Union[Message, Control]

# TaskState<Success, Message> = IteratorResult<Instruction<Message>, Success>
# In Python, Iterator[YieldType] is used, and the return value is the final StopIteration value.
# Generator[YieldType, SendType, ReturnType]

class Task(Protocol[Success, Failure, Message]):
    def __iter__(self) -> Generator[Instruction[Message], Any, Success]:
        ...

class Controller(
    Generator[Instruction[Message], Any, Success],
    Protocol[Success, Failure, Message]
):
    def throw(
        self,
        typ: Any,
        val: Union[BaseException, object] = None,
        tb: Any = None,
    ) -> Instruction[Message]: ...

    def return_(self, value: Any) -> Instruction[Message]: ...

    def send(self, value: Any) -> Instruction[Message]: ...

Status = Literal["idle", "active", "finished"]

class Stack(Generic[Success, Failure, Message]):
    active: list["Controller[Success, Failure, Message]"]
    idle: set["Controller[Success, Failure, Message]"]

    def __init__(
        self,
        active: Optional[list["Controller[Success, Failure, Message]"]] = None,
        idle: Optional[set["Controller[Success, Failure, Message]"]] = None,
    ) -> None:
        self.active = active if active is not None else []
        self.idle = idle if idle is not None else set()

class Main(Generic[Success, Failure, Message]):
    id: Literal[0] = 0
    parent: None = None
    status: Status = "idle"
    stack: Stack[Success, Failure, Message]

    def __init__(self) -> None:
        self.stack = Stack()

class TaskGroup(Generic[Success, Failure, Message]):
    id: int
    parent: Union["Main[Success, Failure, Message]", "TaskGroup[Success, Failure, Message]"]
    driver: Controller[Success, Failure, Message]
    stack: Stack[Success, Failure, Message]
    result: Optional["Result[Success, Failure]"]

    def __init__(
        self,
        id: int,
        parent: Union["Main[Success, Failure, Message]", "TaskGroup[Success, Failure, Message]"],
        driver: Controller[Success, Failure, Message],
        stack: Stack[Success, Failure, Message],
    ) -> None:
        self.id = id
        self.parent = parent
        self.driver = driver
        self.stack = stack
        self.result = None

class Result(Generic[Success, Failure]):
    ok: bool
    value: Optional[Success]
    error: Optional[Failure]

    def __init__(self, ok: bool, value: Optional[Success] = None, error: Optional[Failure] = None):
        self.ok = ok
        self.value = value
        self.error = error

class Fork(
    Controller[Success, Failure, Message],
    Generic[Success, Failure, Message]
):
    def __init__(self, task: Task[Success, Failure, Message], options: Any = None):
        self.id = -1 # Should be set by scheduler
        self.task = task
        self.controller: Optional[Controller[Success, Failure, Message]] = None
        self.status: Status = "idle"
        self.result: Optional[Result[Success, Failure]] = None
        self.group: Optional[TaskGroup[Success, Failure, Message]] = None

    def __iter__(self) -> "Fork[Success, Failure, Message]":
        if self.controller is None:
            self.controller = cast(Controller[Success, Failure, Message], iter(self.task))
            self.status = "active"
        return self

    def __next__(self) -> Instruction[Message]:
        return self.send(None)

    def send(self, value: Any) -> Instruction[Message]:
        assert self.controller is not None
        try:
            return self.controller.send(value)
        except StopIteration as e:
            self.status = "finished"
            self.result = Result(ok=True, value=e.value)
            raise e

    def throw(self, typ: Any, val: Union[BaseException, object] = None, tb: Any = None) -> Instruction[Message]:
        assert self.controller is not None
        try:
            return self.controller.throw(typ, val, tb)
        except StopIteration as e:
            self.status = "finished"
            self.result = Result(ok=True, value=e.value)
            raise e
        except BaseException as e:
            self.status = "finished"
            self.result = Result(ok=False, error=cast(Any, e))
            raise e

    def return_(self, value: Any) -> Instruction[Message]:
        assert self.controller is not None
        try:
            return self.controller.return_(value)
        except StopIteration as e:
            self.status = "finished"
            self.result = Result(ok=True, value=e.value)
            raise e

    def resume(self) -> Task[None, Any, Any]:
        raise NotImplementedError()

    def join(self) -> Task[Success, Failure, Message]:
        raise NotImplementedError()

    def abort(self, error: Any) -> Task[None, Any, Any]:
        raise NotImplementedError()

    def exit(self, value: Any) -> Task[None, Any, Any]:
        raise NotImplementedError()
