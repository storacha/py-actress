from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any, Generic, Literal, NoReturn, Optional, TypeVar, TypedDict, Union, cast, overload
from collections.abc import Awaitable, Generator, Callable
from enum import Enum
from typing_extensions import TypeAlias

T = TypeVar("T")  # value of task returned (on success)
X = TypeVar("X", bound= Exception)  # exception raised by task (failure)
M = TypeVar("M")  # message yielded by task


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
        output: Result[T, Exception] = None  # type: ignore

        async def handle_async():
            # closure variable
            nonlocal output
            try:
                value = await (cast(Awaitable[T], input))  # cast to help type checker
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

        # To ensure behavioural consistency in sync/async values of `input`, suspension
        # of task happens immediately while `enqueue(task)` runs async after `wake`
        # task is itself `enqueueed` into the `Main` group within the `main()` function.
        # similar to how when `input` is `Awaitable` and current task (or context) is
        # suspended immediately and other tasks run, while `input` is `awaited`.
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


class Fork(Generic[T, X, M]):
    global ID

    def __init__(
        self,
        task: Task[M, T],
        handler: StateHandler[T, X] = StateHandler(),
        options: ForkOptions = ForkOptions(name=None),
    ) -> None:
        self.handler= handler
        self.id += ID
        self.name = options.get('name', None) or ""
        self.task = task
        self.state: Optional[Union[Instruction[M], StopIteration]] = None
        self.status = Status.IDLE
        self.result: Optional[Result[T, X]] = None
        self.controller: Optional[Controller[M, T]] = None
        self.group: Optional[Group[T, X, M]] = None


    def resume(self) -> Task[None, None]:
        resume(self)
        yield

    def join(self) -> Task[Optional[M], T]:
        return join(self)

    def abort(self, error: X):
        return abort(self, error)

    def activate(self) -> Fork:
        self.controller = iter(self.task)
        self.status = Status.ACTIVE
        enqueue(self)
        return self

    def __iter__(self):
        return self.activate()

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
        # `state` isn't returned like it is in the reference JS impl because `_step` is
        # only needed here to run success handlers and set `result` and `status` when
        # the fork ends. And at that point the `state` or success return value of the
        # task is already extracted from `StopIteration.value` and set in the result as
        # `Success.value`.
        # returning it is even more pointless when you realize `StopIteration` is caught
        # and then `re-raised` just after calling `_step`.

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

    def close(self) -> None:
        try:
            # self.controller.close() == self.controller.throw(GeneratorExit)
            # also cast the type for `self.controller` to eliminate the type-checker
            # from inferring that `self.controller` is still `None` after fork
            # activation
            state = cast(Controller, self.controller).throw(GeneratorExit)
            self._step(state)
        # `GeneratorExit` is outside the scope of `Exception`. it subclasses
        # `BaseException` instead - `Exception`'s parent class. Catch both exc.
        except (Exception, GeneratorExit) as e:
            self._panic(e)  # type: ignore[arg-type]
        else:
            # if no exception was raise, then the generator must have yielded
            raise RuntimeError("Generator ignored `GeneratorExit`")

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


class TaskGroup(Generic[T, X, M]):
    """Task group for managing concurrent tasks."""
    global ID

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

        self.id += ID

    @staticmethod
    def of(member: ControllerFork[T, X, M]) -> Group[T, X, M]:
        # since `Generator` objects dont have the `group` attribute if `member`
        # is not a `Fork` then it's group is the default `MAIN` group
        group = getattr(member, 'group', None)
        return group if group is not None else MAIN


    @staticmethod
    def enqueue(member: TaskFork[T, X, M], group: TaskGroup[T, X, M]) -> None:
        member.group = group  # type: ignore[attr-defined]
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
                for _ in step(MAIN):
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
        # will change the task driver, that is when the conditional statement: 
        # `task == active[0]` will become false and the task would need to be dropped
        # immediately otherwise race dondition will occur due to task been driven by
        # multiple concurrent schedulers.
        while task == active[0]:
            try:
                instruction = task.send(None)
                # if task is suspended we add it to the idle list and break the loop to move
                # to a next task
                if instruction is SUSPEND:
                    group.stack.idle.add(task)
                    break
                # if task requested a context (which is usually to suspend itself) pass back
                # a task reference and continue.
                elif instruction is CURRENT:
                    instruction = task.send(task)
                    continue
                else:
                    # otherwise task sent a message which we yield to the driver and
                    # continue
                    instruction = task.send((yield instruction))  # type: ignore[arg-type]
                    break
            except StopIteration:
                # task finished
                break

        # if task is complete or got suspended we move to a next task
        active.pop(0)
        task = active[0] if active else None
        if task:
            group.stack.idle.discard(task)


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
            # force the generator to end and return with `result.value`
            state = task.throw(StopIteration(result.value))
        elif isinstance(result, Failure):
            state = task.throw(result.error)

        # incase `task` has a `finally` block that still yields values into `state`
        if state is SUSPEND:
            idle = TaskGroup.of(task).stack.idle
            idle.add(task)
        elif state is not None:
            enqueue(task)
    except Exception:
        pass
    yield


def abort(handle: ControllerFork[T, X, M], error: Exception) -> Task[None, None]:
    """
    Aborts given task with an error. Task error type should match provided error.

    Args:
        handle: Task controller to abort
        error: Error to throw into the task
    """
    yield from conclude(handle, Failure(error))


def exit_(handle: Controller[M, T], value: Any) -> Task[None, None]:
    """
    Exits a task successfully with a return value.

    Args:
        handle: Task controller to exit
        value: Return value on exit
    """
    yield from conclude(handle, Success(value))


def terminate(handle: Controller[M, T]) -> Task[None, None]:
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

    self = yield from current()
    group = TaskGroup(self)
    failure: Optional[Failure[X]] = None

    for fork in forks:
        result = fork.result
        if result is not None:
            # only the first error should be recorded, so `failure` has to be `None`
            if not result.ok and failure is None:
                failure = result  # type: ignore[assignment]
            continue
        move(fork, group)

    # keep work looping until there is no more work to be done
    try:
        # raise the exception that caused the first recorded failure result
        if failure:
            raise failure.error
        while True:
            # run all tasks in the active queue to completion
            yield from step(group)
            # but there might be suspended tasks in `group.stack.idle`
            if Stack.size(group.stack) > 0:
                # if there are grouped forked tasks that are suspended, then suspend
                # driver too.
                # NOTE: that `enqueue()` resumes this driver when suspended forked task
                # resumes. since `self` is the driver task of the group containing the
                # forked tasks
                yield from suspend()
            else:
                break
    except Exception as error:
        for task in group.stack.active:
            yield from abort(task, error)

        for task in group.stack.idle:
            yield from abort(task, error)
            enqueue(task)

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
    # if fork is still idle activate it.
    if (fork.status == Status.IDLE):
        yield from fork

    if fork.result is None:
        yield from group([fork])

    result: Result[T, X] = fork.result  # type: ignore[union-attr]
    if isinstance(result, Success):
        return result.value
    else:
        raise result.error

