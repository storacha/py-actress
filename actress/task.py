from typing import Any, Generator, Literal, TypeVar, Union, Deque, Optional, Set, List
from collections import deque
import asyncio


class CurrentInstruction:
    def __repr__(self) -> str:
        return f'<CURRENT>'


class SuspendInstruction:
    def __repr__(self) -> str:
        return f'<SUSPEND>'


T = TypeVar('T')
X = TypeVar('X')
M = TypeVar('M')


# Special control instructions recognized by the scheduler.
CURRENT = CurrentInstruction()
SUSPEND = SuspendInstruction()

Control = Union[CurrentInstruction, SuspendInstruction]
Instruction = Union[Control, M]
Task = Generator[Instruction[M], Any, T]


class Stack:
    """
    Manages two collection of tasks:
    - active: FIFO Queue of tasks ready to run 
    - idle: Set of suspended tasks (order not necessary)
    """

    def __init__(self, active: Optional[Deque[Task]] = None, idle: Optional[Set[Task]] = None):
        self.active = active or deque([])
        self.idle = idle or set()

    @staticmethod
    def size(stack: 'Stack') -> int:
        """
        Get total number of tasks (active + idle).
        """
        return len(stack.active) + len(stack.idle)



def current() -> Generator[CurrentInstruction, Generator, Generator]:  # does the `Generator` object return a `Generator` object, I thought it returns nothing since it's just `yield CURRENT`
    """
    Get reference to current task.
    """
    return (yield CURRENT)


def suspend() -> Generator[SuspendInstruction, None, None]:
    yield SUSPEND


def enqueue(task: Task, stack: Stack) -> None:
    """
    Add a task to the active queue.
    """
    if task in stack.idle:
        print(stack.idle)
        stack.idle.remove(task)
    stack.active.append(task)
    return


def resume(task: Task, stack: Stack) -> None:
    """
    Resume a suspended task.
    """
    enqueue(task, stack)
    return


def step(stack: Stack) -> Generator[Any, Any, None]:
    """
    Execute one step of the scheduler.

    This is a GENERATOR that:
    - Processes tasks from stack.active
    - Handles SUSPEND and CURRENT control instructions internally
    - YIELDS regular messages to the caller
    """
    active = stack.active
    current_task = active[0] if active else None

    if current_task:
        stack.idle.discard(current_task)

    while current_task:
        # get initial instruction for this task
        try:
            instruction = next(current_task)
        except StopIteration:
            # task completed before any other instruction, add other tasks
            active.popleft()
            current_task = active[0] if active else None
            if current_task:
                stack.idle.discard(current_task)
            continue

        # Track if task is done (for re-enqueueing logic)
        task_done = False

        # Inner loop to process instructions from this task
        while current_task is active[0]:
            if instruction is SUSPEND:
                stack.idle.add(current_task)
                break

            elif instruction is CURRENT:
                try:
                    instruction = current_task.send(current_task)
                    continue
                except StopIteration:
                    # task completed
                    task_done = True
                    break

            else:
                yield instruction
                break  # Exit inner loop, give other tasks a turn

        removed_task = active.popleft()

        # If task yielded a message (not suspended, not done), re-add to back
        if removed_task not in stack.idle and not task_done:
            # Task yielded a message - re-add to back of queue -- cooperative multitasking
            active.append(removed_task)

        current_task = active[0] if active else None

        if current_task:
            stack.idle.discard(current_task)


def sleep(duration: float, stack: Stack) -> Generator[Any, Any, None]:
    """
    /**
     * Suspends execution for the given duration in milliseconds, after which
     * execution is resumed (unless it was aborted in the meantime).
     *
     * Args:
     *  duration: Time to sleep in seconds
     *  stack: The stack this task belongs to
     *
     * Example:
     *  def my_task():
     *      print("Starting")
     *      yield from sleep(0.1, stack)
     *      print("After 100ms")
     */
    """
    task_ref = yield from current()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    handle = loop.call_later(duration, lambda: resume(task_ref, stack))

    try:
        yield from suspend()
    finally:
        handle.cancel()



def wait(input_value: Union[T, Any], stack: Stack) -> Generator[Any, Any, Any]:
    """
    /**
     * Provides equivalent of `await` in async functions. Specifically it takes
     * a value that you can `await` on (that is `Promise<T>|T`) and suspends
     * execution until promise is settled. If promise succeeds execution is resumed
     * with `T` otherwise an error of type `X` is thrown (which is by default
     * `unknown` since promises do not encode error type).
     *
     * It is useful when you need to deal with potentially async set of operations
     * without having to check if thing is a promise at every step.
     *
     * Please note: This that execution is suspended even if given value is not a
     * promise, however scheduler will still resume it in the same tick of the event
     * loop after, just processing other scheduled tasks. This avoids problematic
     * race condititions that can otherwise occur when values are sometimes promises
     * and other times are not.
     *
     *
     *  Args:
     *      input_value: A value or awaitable to wait on
     *      stack: The stack this task belongs to
     *
     *  Returns:
     *      The resolved value
     *
     *  Raises:
     *      Exception if the future fails
     *
     *
     *  Example:
     *      async def fetch_data():
     *          await asyncio.sleep(0.1)
     *          return "data"
     *
     *      def my_task():
     *          future = fetch_data()
     *          result = yield from wait(future, stack)
     *          print(f"Got: {result}")
     */
    """
    task_ref = yield from current()

    if asyncio.isfuture(input_value) or asyncio.iscoroutine(input_value):
        failed = [False]
        output: List[Optional[Exception]] = [None]
        async def handle_async():
            try:
                result = await input_value
                failed[0] = False
                output[0] = result
                enqueue(task_ref, stack)  # Resume task
            except Exception as error:
                failed[0] = True
                output[0] = error
                enqueue(task_ref, stack)  # Resume even on error
        # Get or create event loop, then schedule task
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Use ensure_future to schedule the coroutine to run when the event loop starts,
        # which works even when loop is not running unlike `asyncio.create_task()`
        asyncio.ensure_future(handle_async(), loop=loop)

        yield from suspend()
        if failed[0] and isinstance(output[0], Exception):
            raise output[0]
        else:
            return output[0]
    else:
        def wake():
            resume(task_ref, stack)
            yield
        enqueue(wake(), stack)
        yield from suspend()
        return input_value


def main(task: Task) -> Any:
    """
    Run a single task to completion.
    """
    stack = Stack()
    enqueue(task, stack)

    for _ in step(stack): pass

    return None


# export type Instruction<T> = Message<T> | Control

# export type Await<T> = T | PromiseLike<T>

# export type Result<T extends unknown = unknown, X extends unknown = Error> =
#   | Success<T>
#   | Failure<X>

# export interface Success<T extends unknown> {
#   readonly ok: true
#   readonly value: T
# }

# export interface Failure<X extends unknown = Error> {
#   readonly ok: false
#   readonly error: X
# }

# type CompileError<Reason extends string> = `🚨 ${Reason}`

# /**
#  * Helper type to guard users against easy to make mistakes.
#  */
# export type Message<T> = T extends Task<any, any, any>
#   ? CompileError<`You must 'yield * fn()' to delegate task instead of 'yield fn()' which yields generator instead`>
#   : T extends (...args: any) => Generator
#   ? CompileError<`You must yield invoked generator as in 'yield * fn()' instead of yielding generator function`>
#   : T

# /**
#  * Task is a unit of computation that runs concurrently, a light-weight
#  * process (in Erlang terms). You can spawn bunch of them and provided
#  * cooperative scheduler will interleave their execution.
#  *
#  * Tasks have three type variables first two describing result of the
#  * computation `Success` that corresponds to return type and `Failure`
#  * describing an error type (caused by thrown exceptions). Third type
#  * varibale `Message` describes type of messages this task may produce.
#  *
#  * Please note that that TS does not really check exceptions so `Failure`
#  * type can not be guaranteed. Yet, we find them more practical that omitting
#  * them as TS does for `Promise` types.
#  *
#  * Our tasks are generators (not the generator functions, but what you get
#  * invoking them) that are executed by (library provided) provided scheduler.
#  * Scheduler recognizes two special `Control` instructions yield by generator.
#  * When scheduler gets `context` instruction it will resume generator with
#  * a handle that can be used to resume running generator after it is suspended.
#  * When `suspend` instruction is received scheduler will suspend execution until
#  * it is resumed by queueing it from the outside event.
#  */
# export interface Task<
#   Success extends unknown = unknown,
#   Failure = Error,
#   Message extends unknown = never
# > {
#   [Symbol.iterator](): Controller<Success, Failure, Message>
# }

# Success = TypeVar("Success")
# Failure = TypeVar("Failure")
# Message = TypeVar("Message")
#
# TaskState = Union[Success, Message]

# Generator[yield_type, send_type, return_type]
# Generator<T = unknown, TReturn = any, TNext = any>


# class Task[Success, Message, Failure]:
#     # def __iter__(): Controller[Success, Message, Failure]
#     def __iter__(self):
#         Generator[
#             Union[Success, Message],
#             Task[Success, Message, Failure],
#             Union[Success, Message],
#         ]


# class Controller[Success, Message, Failure](Generator[Union[Success, Message], Task[Success, Message, Failure], Union[Success, Message]]):

# export interface Controller<
#   Success extends unknown = unknown,
#   Failure extends unknown = Error,
#   Message extends unknown = never
# > {
#   throw(error: Failure): TaskState<Success, Message>
#   return(value: Success): TaskState<Success, Message>
#   next(
#     value: Task<Success, Failure, Message> | unknown
#   ): TaskState<Success, Message>
# }

# export type TaskState<
#   Success extends unknown = unknown,
#   Message = unknown
# > = IteratorResult<Instruction<Message>, Success>

# /**
#  * Effect represents potentially asynchronous operation that results in a set
#  * of events. It is often comprised of multiple `Task` and represents either
#  * chain of events or a concurrent set of events (stretched over time).
#  * `Effect` campares to a `Stream` the same way as `Task` compares to `Promise`.
#  * It is not representation of an eventual result, but rather representation of
#  * an operation which if execute will produce certain result. `Effect` can also
#  * be compared to an `EventEmitter`, because very often their `Event` type
#  * variable is a union of various event types, unlike `EventEmitter`s however
#  * `Effect`s have inherent finality to them an in that regard they are more like
#  * `Stream`s.
#  *
#  * You may notice that `Effect`, is just a `Task` which never fails, nor has a
#  * (meaningful) result. Instead it can produce events (send messages).
#  */
# export interface Effect<Event> extends Task<void, never, Event> {}

Status = Literal["idle", "active", "finished"]

# export type Group<T, X, M> = Main<T, X, M> | TaskGroup<T, X, M>

# export interface TaskGroup<T, X, M> {
#   id: number
#   parent: Group<T, X, M>
#   driver: Controller<T, X, M>
#   stack: Stack<T, X, M>

#   result?: Result<T, X>
# }

# export interface Main<T, X, M> {
#   id: 0
#   parent?: null
#   status: Status
#   stack: Stack<T, X, M>
# }

# export interface Stack<T = unknown, X = unknown, M = unknown> {
#   active: Controller<T, X, M>[]
#   idle: Set<Controller<T, X, M>>
# }

# /**
#  * Like promise but lazy. It corresponds to a task that is activated when
#  * then method is called.
#  */
# export interface Future<Success, Failure> extends PromiseLike<Success> {
#   then<U = Success, G = never>(
#     handle?: (value: Success) => U | PromiseLike<U>,
#     onrejected?: (error: Failure) => G | PromiseLike<G>
#   ): Promise<U | G>

#   catch<U = never>(handle: (error: Failure) => U): Future<Success | U, never>

#   finally(handle: () => void): Future<Success, Failure>
# }

# export interface Fork<
#   Success extends unknown = unknown,
#   Failure extends unknown = Error,
#   Message extends unknown = never
# > extends Controller<Success, Failure, Message>,
#     Task<Fork<Success, Failure, Message>, never>,
#     Future<Success, Failure> {
#   readonly id: number

#   group?: void | TaskGroup<Success, Failure, Message>

#   result?: Result<Success, Failure>
#   status: Status
#   resume(): Task<void, never>
#   join(): Task<Success, Failure, Message>

#   abort(error: Failure): Task<void, never>
#   exit(value: Success): Task<void, never>
# }

# export interface ForkOptions {
#   name?: string
# }

# export interface StateHandler<T, X> {
#   onsuccess?: (value: T) => void
#   onfailure?: (error: X) => void
# }
