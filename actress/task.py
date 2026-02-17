from __future__ import annotations
import asyncio
import weakref
from dataclasses import dataclass
from typing import Any, Generic, Literal, NoReturn, Optional, TypeVar, TypedDict, Union, cast, overload
from collections.abc import Awaitable, Generator, Callable, Iterable
from enum import Enum
from typing_extensions import TypeAlias

T = TypeVar("T")  # value of task returned (on success)
X = TypeVar("X", bound= Exception)  # exception raised by task (failure)
M = TypeVar("M")  # message yielded by task
U = TypeVar("U")


class CurrentInstruction:
    def __repr__(self) -> str:
        return '<CURRENT>'


class SuspendInstruction:
    def __repr__(self) -> str:
        return '<SUSPEND>'


# Special control instructions recognized by the scheduler.
CURRENT = CurrentInstruction()
SUSPEND = SuspendInstruction()

Control: TypeAlias = Union[CurrentInstruction, SuspendInstruction]
Instruction: TypeAlias = Union[Control, M]
Controller: TypeAlias = Generator[Union[Control, M], Any, T]
Task: TypeAlias = Generator[Union[Control, M], Any, T]  # In python the generator object is also the iterator
"""
Task is a unit of computation that runs concurrently, a light-weight
process (in Erlang terms). You can spawn bunch of them and provided
cooperative scheduler will interleave their execution.

Tasks have three type variables first two describing result of the
computation `Success` that corresponds to return type and `Failure`
describing an error type (caused by thrown exceptions). Third type
varibale `Message` describes type of messages this task may produce.

Please note that that Python does not really check exceptions so `Failure`
type can not be guaranteed. Yet, we find them more practical than omitting
them as Python does for `Future` types and its derivatives.

Our tasks are generators (not the generator functions, but what you get
invoking them) that are executed by (library provided) provided scheduler.
Scheduler recognizes two special `Control` instructions yield by generator.
When scheduler gets `current` instruction it will resume generator with
a handle that can be used to resume running generator after it is suspended.
When `suspend` instruction is received scheduler will suspend execution until
it is resumed by queueing it from the outside event.
"""

# `M` == `Event`
Effect: TypeAlias = Generator[M, Any, None]
"""
Effect represents potentially asynchronous operations that results in a set of events.
It is often comprised of multiple `Task` and represents either chain of events or a
concurrent set of events (stretched over time).
Effect compares to a `Stream` in Javascript in the same way as `Task` compares to
`Future` in Python. It is not a representation of an eventual result but rather a
representation of an operation which if executed will produce certain result. `Effect`
can also be compared to an `EventEmitter` in Javascript, but very often their `Event`
type variable (`M` type variable in Python imlementation) is a union of various event
types, unlike `EventEmitter`s however `Effect`s have inherent finality to them and in
that regard are more like Javascript `Streams`
"""

@dataclass
class Success(Generic[T]):
    "Result of a successful task."
    value: T
    ok: bool = True


@dataclass
class Failure(Generic[X]):
    "Result of a failed task."
    error: X
    ok: bool = False


Result: TypeAlias = Union[Success[T], Failure[X]]


@dataclass
class StateHandler(Generic[T, X]):
    onsuccess: Optional[Callable[[T], None]] = None
    onfailure: Optional[Callable[[X], None]] = None

ID = 0
"""Unique IDs for entities (`Fork`, `Group`, ...)"""


class ForkOptions(TypedDict):
    name: Optional[str]


class Status(str, Enum):
    """Task execution status."""
    IDLE = "idle"
    ACTIVE = "active"
    FINISHED = "finished"


class Stack(Generic[T, X, M]):
    """Stack of active and idle tasks"""
    def __init__(
        self,
        active: Optional[list[ControllerFork[T, X, M]]] = None,
        idle: Optional[set[ControllerFork[T, X, M]]] = None
    ) -> None:
        # gymnastics to align with reference JS implementation without sacrificing
        # safety. Using mutable types as default args in Python can lead to weird errors
        # that arise from a shared state created at definition time as opposed to each
        # time the function is called.
        active_eval = active if active is not None else []
        idle_eval = idle if idle is not None else set()
        self.active: list[ControllerFork[T, X, M]] = active_eval
        self.idle: set[ControllerFork[T, X, M]] = idle_eval

    @staticmethod
    def size(stack: Stack[T, X, M]) -> int:
        """Get total number of tasks in stack."""
        return len(stack.active) + len(stack.idle)


def is_async(node: Any) -> bool:
    """
    Checks if a value is awaitable (or its lookalike).
    """
    if (
        asyncio.isfuture(node) or
        asyncio.iscoroutine(node) or
        isinstance(node, Awaitable)
    ):
        return True
    return False

def sleep(duration: float = 0) -> Task[Control, None]:
    """
    Suspends execution for the given duration in milliseconds, after which execution
    is resumed (unless it was aborted in the meantime).

    Args:
        duration: Time to sleep in milliseconds

    Example:
    ```
    def demo():
        print("I'm going to take a small nap")
        yield from sleep(200)
        print("I am back to work")
    ```
    """
    task = yield from current()
    loop = asyncio.get_running_loop()

    # convert duration to millisecs
    handle = loop.call_later(duration/1000, lambda: enqueue(task))

    try:
        yield from suspend()
    finally:
        handle.cancel()

@overload
def wait(input: Awaitable[T]) -> Task[Control, T]: ...

@overload
def wait(input: T) -> Task[Control, T]: ...

def wait(input: Union[Awaitable[T], T]) -> Task[Control, T]:
    """
    Provides equivalent of `await` in async functions. Specifically it takes a value
    that you can `await` on (that is `[T]`, i.e futures, coroutines) and suspends
    execution until future is settled. If future succeeds execution is resumed with `T`
    otherwise an error of type `X` is thrown (which is by default `unknown` since
    futures do not encode error type).

    It is useful when you need to deal with potentially async set of operations
    without having to check if thing is an `await`-able at every step.

    Please note that execution is suspended even if given value is not a
    promise, however scheduler will still resume it in the same tick of the event
    loop after, just processing other scheduled tasks. This avoids problematic
    race condititions that can otherwise occur when values are sometimes promises
    and other times are not.

    Args:
        input_value: A value or awaitable to wait on

    Returns:
        The resolved value

    Raises:
        Exception if the future fails

    Example:
    ```
    def fetch_json(url, options):
       const response = yield from wait(fetch(url, options))
       const json = yield from wait(response.json())
       return json
    ```
    """
    task = yield from current()
    if is_async(input):
        # no need to track `failed = False` like in reference JS impl because failure
        # can be tracked by only the `Result` object in the `output` variable below
        output: Result[T, Exception] = None  # type: ignore[assignment]

        async def handle_async():
            nonlocal output
            try:
                value = await (cast(Awaitable[T], input))
                output = Success(value)
            except Exception as error:
                output = Failure(error)
            enqueue(task)

        loop = asyncio.get_running_loop()
        # schedule the async handler on the loop
        loop.create_task(handle_async())

        yield from suspend()

        # check for failure with `output` instead of `failure` variable like in the
        # reference implementation
        if isinstance(output, Failure):
            raise output.error
        else:
            return output.value
    else:
        # this may seem redundant but it is not, by enqueuing this task we allow
        # scheduler to perform other queued tasks first. This way many race conditions
        # can be avoided when values are sometimes promises and other times aren't.
        # unlike `await` however this will resume in the same tick.

        # when the `wake` task is enqueued in func `main()`, it is enqueued in the `Main`
        # group. and when executed, it enqueues `task`. thereby maintaining consistency
        # in suspending immediately while `enqueue(task)` runs async after other tasks
        # as it similarly happens if `input` was `Awaitable`
        main(wake(task))
        yield from suspend()  # suspension happens immediately
        return cast(T, input)

def wake(task: Task[M, T]) -> Task[None, None]:
    enqueue(task)
    yield

def main(task: Task[None, None]) -> None:
    """
    Starts a main task.

    Args:
        task: A task that produces no output (returns None, never fails, sends no messages)
    """
    controller = iter(task)
    enqueue(controller)  # controller == iterator

def is_message(value: Instruction[M]) -> bool:
    if value is SUSPEND or value is CURRENT:
        return False
    return True

def is_instruction(value: Instruction[M]) -> bool:
    return not is_message(value)


class Future_(Generic[T, X]):
    """
    Base class for awaitable task handles.

    Provides Promise-like interface for tasks, allowing them to be awaited
    from async contexts.
    """

    def __init__(self, handler: Optional[StateHandler[T, X]] = None):
        self.handler = handler or StateHandler()
        self.result: Optional[Result[T, X]] = None
        self._promise: Optional[asyncio.Future[T]] = None

    def _get_promise(self) -> asyncio.Future[T]:
        """
        Lazily create and cache an asyncio.Future for this task.
        """
        if self._promise is not None:
            return self._promise

        # If we already have a result, create a pre-resolved future
        if self.result is not None:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[T] = loop.create_future()

            if isinstance(self.result, Success):
                future.set_result(self.result.value)
            else:
                future.set_exception(self.result.error)

            self._promise = future
            return future

        # Otherwise, create a future and wire up handlers
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        # Store original handlers
        original_onsuccess = self.handler.onsuccess
        original_onfailure = self.handler.onfailure

        def onsuccess(value: T) -> None:
            if not future.done():
                future.set_result(value)
            if original_onsuccess:
                original_onsuccess(value)

        def onfailure(error: X) -> None:
            if not future.done():
                future.set_exception(error)
            if original_onfailure:
                original_onfailure(error)

        self.handler.onsuccess = onsuccess
        self.handler.onfailure = onfailure

        self._promise = future
        return future

    def __await__(self) -> Generator[Any, Any, T]:
        # Get the promise and await it
        promise = self._get_promise()

        # Activate the task (runs synchronously in MAIN scheduler)
        self.activate()

        return promise.__await__()

    def activate(self) -> Future_[T, X]:
        """
        Activate the task. Overriden in subclasses.
        """
        return self


class Fork(Future_[T, X], Generic[T, X, M]):
    """
    A handle to a running task that can be awaited.

    Implements both the generator protocol (for use within tasks) and
    the awaitable protocol (for use in async functions).
    """
    def __init__(
        self,
        task: Task[M, T],
        handler: Optional[StateHandler[T, X]] = None,
        options: Optional[ForkOptions] = None,
    ) -> None:
        super().__init__(handler or StateHandler())

        global ID
        ID += 1
        self.id = ID
        self.name: str = "" if options is None else (options.get('name', None) or "")
        self.task = task
        self.state: Union[Instruction[M], StopIteration] = CURRENT
        self.status = Status.IDLE
        self.controller: Optional[Controller[M, T]] = None
        self.group: Optional[Group[T, X, M]] = None


    def resume(self) -> Task[None, None]:
        resume(self)
        yield

    def join(self) -> Task[Optional[M], T]:
        return join(self)

    def abort(self, error: X) -> Task[None, None]:
        return abort(self, error)

    def exit_(self, value: T) -> Task[None, None]:
        return exit_(self, value)

    def activate(self) -> Fork[T, X, M]:
        """
        Activate the task and enqueue it for execution.
        """
        # Only activate if not already active or finished
        if self.controller is None:
            self.controller = iter(self.task)
            self.status = Status.ACTIVE
            enqueue(self)
        return self

    def __iter__(self) -> Generator[Any, Any, Fork[T, X, M]]:
        """
        Make Fork iterable for use with yield from.
        """
        # making __iter__ a generator ensures that `yield from fork(work())` schedules
        # the fork for concurrent execution without enabling synchronous driving of the
        # forked task after activation and then returns immediately (since MAIN group
        # is likely already actively being executed by the scheduler and `work` already
        # queued in `MAIN`s active queue).
        self.activate()
        return self
        yield

    def _panic(self, error: X) -> NoReturn:
        self.result = Failure(ok=False, error=error)
        self.status = Status.FINISHED
        if self.handler.onfailure is not None:
            self.handler.onfailure(error)
        raise error

    def _step(self, state: Union[Instruction[M], StopIteration]) -> None:
        self.state = state
        #`StopIteration` signifies end of a generator in Python and holds its return
        # value on success
        if isinstance(state, StopIteration):
            self.result = Success(ok=True, value=state.value)
            self.status = Status.FINISHED
            if self.handler.onsuccess is not None:
                self.handler.onsuccess(state.value)
        # `state` is not returned here like in the reference js implementation because -
        # `state` here can be set to `StopIteration(value: T)` which signals competion
        # in Python, but in order to keep `Fork` behaviour predictable and similar to
        # normal generators (e.g `controller` generators) `StopIteration` isn't returned
        # but raised. So no point in returning `state` (especially on completion),
        # within the class or - via the generator protocol methods - outside the class.
        # just run success handlers, update state and set success value in result.

    def __next__(self) -> Instruction[M]:
        try:
            # note that: `task.send(None) == next(task)`
            # also cast the type for `self.controller` to eliminate the type-checker
            # from inferring that `self.controller` is still `None` after fork
            # activation
            state = cast(Controller, self.controller).send(None)
            self._step(state)
            return state
        except StopIteration as e:
            # `StopIteration` means task is finished, therefore task `result`, `status`
            # can be set and success handler function ran.
            self._step(e)
            raise
        except Exception as e:
            return self._panic(e)  # type: ignore[arg-type]

    def send(self, value: Any) -> Instruction[M]:
        try:
            # cast the type for `self.controller` to eliminate the type-checker
            # from inferring that `self.controller` is still `None` after fork
            # activation
            state = cast(Controller, self.controller).send(value)
            self._step(state)
            return state
        except StopIteration as e:
            # `StopIteration` means task is finished, therefore task `result`, `status`
            # can be set and success handler function ran.
            self._step(e)
            raise
        except Exception as e:
            return self._panic(e)  # type: ignore[arg-type]

    def throw(self, error: Union[Exception, GeneratorExit]) -> Instruction[M]:
        try:
            # also cast the type for `self.controller` to eliminate the type-checker
            # from inferring that `self.controller` is still `None` after fork
            # activation
            state = cast(Controller, self.controller).throw(error)
            self._step(state)
            return state
        except StopIteration as e:
            # `StopIteration` means task is finished, therefore task `result`, `status`
            # can be set and success handler function ran.
            self._step(e)
            raise
        # `GeneratorExit` is outside the scope of `Exception`. it subclasses
        # `BaseException` instead - `Exception`'s parent class. Catch both exc.
        except (Exception, GeneratorExit) as e:
            return self._panic(e)  # type: ignore[arg-type]

    def return_(self, value: T) -> T:
        try:
            # also cast the type for `self.controller` to eliminate the type-checker
            # from inferring that `self.controller` is still `None` after fork
            # activation
            cast(Controller, self.controller).throw(StopIteration(value))
        except StopIteration as e:
            self._step(e)
        except Exception as e:
            self._panic(e)  # type: ignore[arg-type]
        else:
            # the controller yielded instead of terminating. therefore enforce close
            cast(Controller, self.controller).close()
        return value

    def close(self) -> None:
        try:
            # self.controller.close() == self.controller.throw(GeneratorExit)
            # also cast the type for `self.controller` to eliminate the type-checker
            # from inferring that `self.controller` is still `None` after fork
            # activation
            cast(Controller, self.controller).close()
        except (Exception) as e:
            self._panic(e)  # type: ignore[arg-type]

    def __repr__(self) -> str:
        return f"Fork(id={self.id}, status='{self.status}')"

# type alias added for convenience
TaskFork: TypeAlias = Union[Task, Fork[T, X, M]]
ControllerFork: TypeAlias = Union[Controller, Fork[T, X, M]]

class Main(Generic[T, X, M]):
    """Default or Fallback Task Group."""
    def __init__(self) -> None:
        self.status = Status.IDLE
        self.stack = Stack()
        self.id: Literal[0] = 0
        self.parent: Optional[TaskGroup[T, X, M]] = None


MAIN = Main()
"""Singleton main group"""

# Python generator objects cannot carry arbitrary attributes like in the reference JS
# implementation, so loop/group helpers store membership here to route resumed generators
# back to their group.
_GROUP_MEMBERSHIP: weakref.WeakKeyDictionary[object, Group[Any, Any, Any]] = weakref.WeakKeyDictionary()


class TaskGroup(Generic[T, X, M]):
    """Task group for managing concurrent tasks."""
    def __init__(
        self,
        driver: ControllerFork[T, X, M],
        active: Optional[list[ControllerFork[T, X, M]]] = None,
        idle: Optional[set[ControllerFork[T, X, M]]] = None,
        stack: Optional[Stack[T, X, M]] = None
    ) -> None:
        self.driver = driver
        self.parent = TaskGroup.of(driver)

        # gymnastics to align with reference JS implementation without sacrificing
        # safety. Using mutable types as default args in Python can lead to weird errors
        # that arise from a shared state created at definition time as opposed to each
        # time the function is called.
        active_eval = active if active is not None else []
        idle_eval = idle if idle is not None else set()
        if active is not None or idle is not None:
            self.stack = Stack(active_eval, idle_eval)
        elif stack is not None:
            self.stack = stack
        else:
            self.stack = Stack()

        global ID
        ID += 1
        self.id = ID

    @staticmethod
    def of(member: ControllerFork[T, X, M]) -> Group[T, X, M]:
        # since `Generator` objects don't have the `group` attribute if `member`
        # is not a `Fork` then it's group is the default `MAIN` group
        group = getattr(member, 'group', None)
        if group is not None:
            return group
        mapped = _GROUP_MEMBERSHIP.get(member)
        return cast(Group[T, X, M], mapped if mapped is not None else MAIN)


    @staticmethod
    def enqueue(member: TaskFork[T, X, M], group: TaskGroup[T, X, M]) -> None:
        try:
            member.group = group  # type: ignore[union-attr]
        except AttributeError:
            _GROUP_MEMBERSHIP[member] = group
        finally:
            group.stack.active.append(member)


Group = Union[TaskGroup[T, X, M], Main[T, X, M]]

def enqueue(task: ControllerFork[T, X, M]) -> None:
    group = TaskGroup.of(task)

    group.stack.active.append(task)
    group.stack.idle.discard(task)

    # then walk up the group chain and unblock their driver tasks
    while group.parent is not None:
        idle = group.parent.stack.idle
        active = group.parent.stack.active

        # only to appease type checkers, since `MAIN` doesn't have a driver and `MAIN`
        # has a parent of `None` so this loop won't run in that case
        driver = getattr(group, "driver", None)
        if driver is not None and driver in idle:
            idle.remove(driver)
            active.append(driver)
        else:
            # if driver was not blocked it must have been unblocked by other task so
            # stop there
            break

        # crawl up to the parent group
        group = group.parent

    if MAIN.status == Status.IDLE:
        MAIN.status = Status.ACTIVE
        while True:
            try:
                for m in step(MAIN):
                    pass
                MAIN.status = Status.IDLE
                break
            except:
                # Top level task may crash and throw an error, but given this is a main
                # group we do not want to interrupt other unrelated tasks, which is why
                # we discard the error and the task that caused it.
                MAIN.stack.active.pop(0)

def step(group: Group[T, X, M]) -> Generator[M, Any, None]:
    active = group.stack.active
    task = active[0] if active else None
    if task:
        group.stack.idle.discard(task)
    while task:
        # Keep processing instructions until task is done, sends a `SUSPEND` request or
        # it has been removed from the active queue.
        # ⚠️ Group changes require extra care so please make sure to understand the
        # detail here. It occurs when a spawned task(s) are joined into a group which
        # will change the driver and the group the task belongs to, that is when the
        # conditional statement: `task == active[0]` will become false and the task
        # would need to be dropped immediately otherwise race dondition will occur due
        # to task been driven by multiple concurrent schedulers.
        try:
            input_value = None
            while task == active[0]:
                instruction = task.send(input_value)
                if instruction is SUSPEND:
                    group.stack.idle.add(task)
                    break
                # if task requested a context (which is usually to suspend itself) pass back
                # a task reference and continue.
                elif instruction is CURRENT:
                    input_value = task
                    continue
                else:
                    # otherwise task sent a message which we yield to the driver and
                    # continue
                    input_value = yield instruction  # type: ignore[misc]
                    continue
        except StopIteration:
            # task finished
            pass

        # if task is complete or got suspended we move to a next task
        if active and active[0] == task:
            active.pop(0)

        task = active[0] if active else None
        if task:
            group.stack.idle.discard(task)

def spawn(task: Task[None, None]) -> Task[None, None]:
    """
    Executes a given task concurrently with a current task (task that spawned it).
    Spawned task is detached from the task that spawned it and it can outlive it and/or
    fail without affecting a task that spawned it. If you need to wait on a concurrent
    task completion consider using `fork` instead which can later be `join`ed. If you
    just want a task to block on another task's execution you can just use:
    `yield from work()` directly instead.
    """
    main(task)
    return
    yield

def fork(task: Task[M, T], options: ForkOptions | None = None) -> Fork[T, Exception, M]:
    """
    Executes a given task concurrently with current task (the task that initiated fork)
    Forked task is detached from the task that created it and it can outlive it and /
    or fail without affecting it. You do however get a handle for the fork which could
    used to `join` the task, in which case `join`ing task would block until fork
    finishes execution.

    This is also a primary interface for executing tasks from the outside of the task
    context. Function returns `Fork` which implements `Future` interface so it can be
    awaited. Please note that calling `fork` does not really do anything, it lazily
    starts execution when you either `await fork(work())` from arbitrary context or
    `yield from fork(work())` in another task context.
    """
    return Fork(task, options=options)

def current() -> Generator[CurrentInstruction, Controller[M, T], Controller[M, T]]:
    return (yield CURRENT)

def suspend() -> Generator[SuspendInstruction, Any, None]:
    yield SUSPEND

def resume(task: Controller[M, T] | Fork[T, X, M]) -> None:
    enqueue(task)

def conclude(
    handle: ControllerFork[T, X, M],
    result: Result[T, Exception]
) -> Task[None, None]:
    """
    Concludes a given task with a result (either success `value` `T` or `error` `X`)

    Args:
        handle: Task controller
        result: Success or failure result
    """
    try:
        task = handle
        if isinstance(result, Success):
            try:
                # force the generator to end and return with `result.value`
                state = task.throw(StopIteration(result.value))
            except StopIteration:
                return
        elif isinstance(result, Failure):
            state = task.throw(result.error)
            while state is CURRENT:
                state = task.send(task)
    except Exception:
        pass
    else:
        # incase `task` has a `finally` block that still yields values into `state`
        if state is SUSPEND:
            idle = TaskGroup.of(task).stack.idle
            idle.add(task)
        elif state is not None:
            enqueue(task)
    return
    yield


def abort(handle: ControllerFork[T, X, M], error: Exception) -> Task[None, None]:
    """
    Aborts given task with an error. Task error type should match provided error.

    Args:
        handle: Task controller to abort
        error: Error to throw into the task
    """
    yield from conclude(handle, Failure(error))


def exit_(handle: ControllerFork[T, X, M], value: Any) -> Task[None, None]:
    """
    Exits a task successfully with a return value.

    Args:
        handle: Task controller to exit
        value: Return value on exit
    """
    yield from conclude(handle, Success(value))


def terminate(handle: ControllerFork[None, X, M]) -> Task[None, None]:
    """
    Terminates a task (only for tasks with void return type). If your task has a
    non-`void` return type you should use `exit` instead.

    Args:
        handle: Task controller to terminate
    """
    yield from conclude(handle, Success(value=None))


def group(forks: list[Fork[T, X, M]]) -> Task[Optional[Instruction[M]], None]:
    """
    Groups multiple forks together and joins them with current task.
    """
    # abort early if there's no work to do
    if len(forks) == 0: return

    self_ = yield from current()
    group = TaskGroup(self_)
    failure: Optional[Failure[X]] = None

    for fork in forks:
        result = fork.result
        if result is not None:
            # only the first error should be recorded, so `failure` has to be `None`
            if not result.ok and failure is None:
                failure = cast(Failure, result)
            continue
        move(fork, group)

    # keep work looping until there is no more work to be done
    try:
        # raise the exception that caused the first recorded failure result
        if failure:
            raise failure.error
        while True:
            # blocks the calling task: `self_`, to run all tasks in the active queue of
            # the group to completion
            yield from step(group)
            # but there might be suspended tasks in `group.stack.idle`
            if Stack.size(group.stack) > 0:
                # if there are grouped forked tasks that are suspended, then suspend
                # driver too.
                # NOTE: that `enqueue()` resumes the driver when suspended forked task
                # resumes. since `enqueue()` unblocks the drivers of a task's group
                # before starting the scheduler and processing the resumed task in it.
                yield from suspend()
            else:
                break
    except Exception as error:
        # only iterate over a copy of active/idle queue to be safe
        for task in list(group.stack.active):
            yield from abort(task, error)

        for task in list(group.stack.idle):
            yield from abort(task, error)
            enqueue(task)  # `conclude` might add idle tasks back into `idle` queue

        raise error


def move(fork: Fork[T, X, M], group: TaskGroup[T, X, M]) -> None:
    """Move a fork from one group to another."""
    from_ = TaskGroup.of(fork)
    if from_ is not group:
        active, idle = (from_.stack.active, from_.stack.idle)
        target = group.stack
        fork.group = group
        # if it is idle just move from one group to the other and update the group task
        # thinks it belongs to.
        if fork in idle:
            idle.remove(fork)
            target.idle.add(fork)
        elif fork in active:
            index = active.index(fork)
            # if task is in the job queue, we move it to a target job queue. Moving top
            # task in the queue requires extra care so it does not end up processed by
            # two groups which would lead to race. For that reason `step` loop checks
            # checks for group changes on each turn
            if index >= 0:
                active.pop(index)
                target.active.append(fork)
        # otherwise task is complete

def join(fork: Fork[T, X, M]) -> Task[Optional[Instruction[M]], T]:
    """
    Joins a forked task back into the current task.

    Suspends the current task until the fork completes, then resumes with its result.
    If the fork fails, the error is thrown.

    Args:
        fork_obj: The fork to join

    Returns:
        The fork's result

    Raises:
        The fork's error if it failed
    """
    if (fork.status == Status.IDLE):
        yield from fork

    # if fork didn't complete, process `fork` in the scheduler and block until
    # completion
    if fork.result is None:
        yield from group([fork])

    result: Result[T, X] = fork.result  # type: ignore[assignment]
    if isinstance(result, Success):
        return result.value
    else:
        raise result.error

def send(message: M) -> Effect[M]:
    """
    Task that sends a given message (or rather an effect producing this message).
    Please note, that while you could use `yield mesage` instead, the reference
    implementation for this library written in TS had risks of breaking changes in the
    TS generator inference which could enable a replacement for `yield *`.
    For uniformity purposes, we decided to stick with the same approach for the Python
    implementation as well.
    """
    yield message

def effect(task: Task[None, T]) -> Effect[T]:
    """
    Turns a task (that never fails or sends messages) into an effect of its result.
    """
    message = yield from task  # type: ignore[misc]
    yield from send(message)

def loop(init: Effect[M], next_: Callable[[M], Effect[M]]) -> Task[None, None]:
    controller = yield from current()
    group = TaskGroup(controller)
    TaskGroup.enqueue(iter(init), group)

    while True:
        for msg in step(group):
            try:
                # incase `next` only accepts keyword args
                effect = next_(**msg)  # type: ignore[arg-type]
            except TypeError:
                effect = next_(cast(M, msg))
            TaskGroup.enqueue(iter(effect), group)
        if Stack.size(group.stack) > 0:
            yield from suspend()
        else:
            break

Tag: TypeAlias = str


class Tagger(Generic[T, X, M]):
    def __init__(self, tags: list[str], source: Fork[T, X, M]) -> None:
        self.tags = tags
        self.source = source
        self.controller: Optional[Controller] = None

    def __iter__(self):
        if not self.controller:
            self.controller = iter(self.source)
        return self

    def box(
        self, state: Union[Instruction[M], StopIteration]
    ) -> Union[Control, Tagged[M]]:
        if isinstance(state, StopIteration):
            return state.value
        else:
            if state is CURRENT or state is SUSPEND:
                return state  # type: ignore[return-value]
            else:  # tag non-control instructions
                # Instead of boxing result at each transform step we perform in-place
                # mutation as we know nothing else is accessing this value.
                tagged = state
                for tag in self.tags:
                    tagged = with_tag(tag, tagged)
                return cast(Tagged, tagged)

    def __next__(self) -> Union[Control, Tagged[M]]:
        return self.box(next(cast(Fork, self.controller)))

    def close(self) -> None:
        cast(Fork, self.controller).close()

    def send(self, instruction: Instruction[M]) -> Union[Control, Tagged[M]]:
        return self.box(cast(Fork, self.controller).send(instruction))

    def throw(self, error: Exception) -> Union[Control, Tagged[M]]:
        return self.box(cast(Fork, self.controller).throw(error))

    def return_(self, value: T) -> Union[Control, Tagged[T]]:
        return self.box(cast(Fork, self.controller).return_(value))  # type: ignore[return-value]

    def __str__(self) -> str:
        return "TaggedEffect"


def _none_effect() -> Effect[None]:
    return
    yield  # necessary for this func to be recognized as a generator that yields nothing

NONE_: Effect[None] = _none_effect()

def none_() -> Effect[None]:
    """
    Returns empty `Effect`, that is produces no messages. Kind of like `[]` or `""` but
    for effects.
    """
    return NONE_

def then_(
    task: Task[M, T],
    resolve: Callable[[T], U],
    reject: Callable[[X], U],
) -> Task[M, U]:
    try:
        return resolve((yield from task))
    except Exception as e:
        return reject(e)  # type: ignore[arg-type]

def all_(tasks: Iterable[Task[M, T]]) -> Task[Any, list[T]]:
    """
    Takes iterable of tasks and runs them concurrently, returning an array of results in
    an order of the tasks (not the order of completion). If any of the tasks fail all
    the rest are aborted and error is thrown into the calling task.
    """
    self_ = yield from current()
    forks: list[Optional[Fork[T, Exception, None]]] = []
    results: list[Optional[T]] = []
    count_ = 0

    def succeed(idx: int) -> Callable[[T], None]:
        def handler(value: T) -> None:
            nonlocal count_
            forks[idx] = None
            results[idx] = value
            count_ -= 1
            if count_ == 0:
                enqueue(self_)
        return handler

    def fail(error: Exception) -> None:
        for handle in forks:
            if handle is not None:
                enqueue(abort(handle, error))
        enqueue(abort(self_, error))

    for i, task in enumerate(tasks):
        results.append(None) # keeps the results list at a size of len(tasks)
        fk = (yield from fork(then_(task, succeed(i), fail)))
        forks.append(fk)  # type: ignore[arg-type]
        count_ += 1

    if count_ > 0:
        yield from suspend()

    return cast(list[T], results)


Tagged: TypeAlias = dict[str, Union[Tag, M]]
"""
The dictionary is shaped like: {"type": tag, tag: value}. There is no way to express
that dictionary shape in Python's current type system at the time of writing this
implementation.
"""

def with_tag(tag: Tag, value: M) -> Tagged[M]:
    return {"type": tag, tag: value}

def tag(effect: Union[ControllerFork[T, X, M], Tagger[T, X, M]], tag: str) -> Effect[Union[Control, Tagged[M]]]:
    """
    Tags an effect by boxing each event with an object that has `type` field
    corresponding to the given tag and same named field holding original message e.g
    given `nums` effect that produces numbers, `tag(nums, "inc")` would create an effect
    that produces events like `{"type": "inc", "inc": 1}`
    """
    if effect is NONE_:
        return NONE_  # type: ignore[return-value]
    elif isinstance(effect, Tagger):
        return Tagger(tags=(effect.tags + [tag]), source=effect.source)  # type: ignore[return-value]
    else:
        return Tagger([tag], effect)  # type:ignore[return-value]

def listen(sources: dict[Tag, Effect[M]]) -> Effect[Union[Control, Tagged[M]]]:
    """
    Takes several effects and merges them into a single effect of tagged variants so
    that their source could be identified via `type` field.
    """
    forks: list[Fork] = []
    for entry in sources.items():
        name, eff = entry
        if eff is not NONE_:
            forks.append(
                (yield from fork(tag(eff, name)))  # type: ignore[arg-type]
            )
    yield from group(forks) # type: ignore[misc]

def batch(effects: list[Effect[T]]) -> Effect[T]:
    """
    Takes several effects and combines them into one effect
    """
    forks: list[Fork] = []
    for eff in effects:
        forks.append((yield from fork(eff)))  # type: ignore[arg-type]
    yield from group(forks)  # type: ignore[misc]

def effects(tasks: list[Task[None, T]]) -> Effect[Optional[T]]:
    """
    Takes several tasks and creates an effect of them all.
    """
    if tasks:
        return batch([effect(task) for task in tasks])
    else:
        return NONE_
