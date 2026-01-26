from typing import Any, Generator, Generic, Iterable, Iterator, Literal, Protocol, TypeVar, Union, Optional

class Symbol:
    """Symbolic global constant"""

    __slots__ = ["_name", "_module"]

    def __init__(self, name: str, moduleName: str):
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

Control = Union[Literal[CURRENT], Literal[SUSPEND]]

Success = TypeVar("Success", covariant=True)
Failure = TypeVar("Failure", contravariant=True)
Message = TypeVar("Message", covariant=True)

# Instruction<T> = Message<T> | Control
Instruction = Union[Message, Control]

# TaskState<Success, Message> = IteratorResult<Instruction<Message>, Success>
# In Python, Iterator[YieldType] is used, and the return value is the final StopIteration value.
# Generator[YieldType, SendType, ReturnType]

class Task(Generic[Success, Failure, Message], Iterable["Controller[Success, Failure, Message]"]):
    def __iter__(self) -> "Controller[Success, Failure, Message]":
        raise NotImplementedError()

class Controller(
    Generator[Instruction[Message], Any, Success],
    Generic[Success, Failure, Message]
):
    def throw(
        self,
        typ: Any,
        val: Optional[BaseException] = None,
        tb: Any = None,
    ) -> Instruction[Message]:
        raise NotImplementedError()

    def return_(self, value: Success) -> Instruction[Message]:
        raise NotImplementedError()

    def next(self, value: Any) -> Instruction[Message]:
        raise NotImplementedError()

    def send(self, value: Any) -> Instruction[Message]:
        return self.next(value)

Status = Literal["idle", "active", "finished"]

class Stack(Generic[Success, Failure, Message]):
    active: list["Controller[Success, Failure, Message]"]
    idle: set["Controller[Success, Failure, Message]"]

    def __init__(self, active: Optional[list] = None, idle: Optional[set] = None):
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

    def __init__(self, id: int, parent: Union["Main", "TaskGroup"], driver: Controller, stack: Stack):
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
    Task[Success, Failure, Message],
    Generic[Success, Failure, Message]
):
    def __init__(self, task: Task[Success, Failure, Message], options: Any = None):
        self.id = -1 # Should be set by scheduler
        self.task = task
        self.controller: Optional[Controller[Success, Failure, Message]] = None
        self.status: Status = "idle"
        self.result: Optional[Result[Success, Failure]] = None
        self.group: Optional[TaskGroup[Success, Failure, Message]] = None

    def __iter__(self) -> "Controller[Success, Failure, Message]":
        if self.controller is None:
            self.controller = iter(self.task)
            self.status = "active"
        return self

    def next(self, value: Any) -> Instruction[Message]:
        return self.controller.next(value)

    def throw(self, typ: Any, val: Optional[BaseException] = None, tb: Any = None) -> Instruction[Message]:
        return self.controller.throw(typ, val, tb)

    def return_(self, value: Any) -> Instruction[Message]:
        return self.controller.return_(value)
    
    def send(self, value: Any) -> Instruction[Message]:
        return self.controller.send(value)

    def resume(self) -> Task[None, Any, Any]:
        raise NotImplementedError()

    def join(self) -> Task[Success, Failure, Message]:
        raise NotImplementedError()

    def abort(self, error: Failure) -> Task[None, Any, Any]:
        raise NotImplementedError()

    def exit(self, value: Success) -> Task[None, Any, Any]:
        raise NotImplementedError()
