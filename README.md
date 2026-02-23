# actress

An implementation of [actor](https://github.com/Gozala/actor) in Python.

## Install

```sh
pip install actress
```

## Usage

```py
import actress
```
## Design

Library uses cooperative scheduler to run concurrent **tasks** (a.k.a light-weight processes) represented via **synchronous** generators. Tasks describe asynchronous operations that may fail, using (synchronous looking) delimited continuations. That is, instead of `await` on a result of async operation, you delegate via `yield from`, which allows scheduler to suspend execution until (async) result is ready and resume task with a value or a thrown exception.

## API Reference

### `Task<T, X, M>`

Task represents a unit of computation that runs concurrently, a light-weight process (in Erlang terms). You can spawn bunch of them and provided cooperative scheduler will interleave their execution.

Tasks have three type variables:

- Variable `T` describes return type of successful computation.
- Variable `X` describes error type of failed computation (type of thrown exceptions)
- Variable `M` describes type of messages this task may produce.

### Scheduler Infrastructure

#### Stack
Manages the execution queue of tasks within a group, separating active tasks (ready to run) from idle tasks (suspended).
- Tasks in active are processed in FIFO order by the scheduler
- Tasks move to idle when they yield SUSPEND
- Tasks move from idle to active when enqueue() is called
- The scheduler's step() function processes the active queue until empty

#### TaskGroup
Groups related tasks together under a driver task while ``` MAIN() ``` is the singleton root group for all top-level tasks. Unlike TaskGroup, it has no driver or parent.

### Task Control Functions

#### abort

```python
def abort(handle: ControllerFork[T, X, M], error: Exception) -> Task
```

Aborts a given task by throwing an error.

This function terminates the task with an error by calling conclude. If the result is a failure, an exception is injected into the generator and the task behaves as if it raised the error itself.

The task may still remain active temporarily because:
- it has a finally block
- cleanup logic yielded control during termination

#### all

```python
def all_(tasks: Iterable[Task[M, T]]) -> Task[Any, list[T]]
```

Runs multiple tasks concurrently and returns their results in order. Equivalent to Promise.all but that does not have cancellation.

#### batch

```python
def batch(effects: list[Effect[T]]) -> Effect[T]
```

Takes several effects and combines them into one effect.

#### effects

```python
def effect(task: Task[None, T]) -> Effect[T]
```

Converts a task (that never fails or sends messages) into an effect that produces its result as a message. Useful for converting task results into the message stream.

#### enqueue

```python
def enqueue(task: ControllerFork[T, X, M]) -> None:
```

Every task belongs to exactly one task group, then it marks the task as runnable and removed from idle if the task was previously blocked, then the code inspect parent group scheduler queues and locate the driver (a child group may have a driver but MAIN will have no driver as it will be root of the tree and then we unblock the driver if it is idle and stop if already unblocked and move upwards in the tree and scheduler loop executes tasks until no idle tasks remain, if some task crashes crashing task is removed and unrelated tasks take place.

#### exit

```python
def exit_(handle: ControllerFork[T, X, M], value: Any) -> Task[None, None]
```

Concludes the task with a Success result. Executes any finally blocks in the task (calls conclude with return value). Task handlers are called with the success value.
Use ``` terminate ``` for like void return 

#### fork

```python
def fork(task: Task[M, T], options: ForkOptions | None = None) -> Fork[T, X, M]
```

Creates a Fork wrapper around the task but does not start execution immediately (lazy).

**Usage Patterns:**
- Concurrent execution: `fk = yield from fork(work())`
- Deferred joining: `fk = fork(work()); ...; result = yield from fk.join()`
- Async integration: `result = await fork(work())`

#### group

```python
def group(forks: list[Fork[T, X, M]]) -> Task[Optional[Instruction[M]], None]
```

It groups multiple forked tasks together and joins them with the current task so that all group tasks either complete successfully or the first failure should abort the entire group.

**Working:**
- A new task group is created and current task acts as a driver of the group
- Each fork is checked if they have already finished, it is not moved into the group and failure is recorded and continues checking the next fork
- Unfinished forks are moved into the group
- If grouped tasks are blocked (idle):
  - The driver suspends itself
  - It will be resumed automatically when a child task wakes up (because enqueue() unblocks the driver)

#### join

```python
def join(fork: Fork[T, X, M]) -> Task[Optional[Instruction[M]], T]
```

- If fork is idle, activates it
- If fork hasn't completed, groups it and waits for completion
- Returns the fork's success value
- Throws the fork's error if it failed

#### listen 

```python
def listen(sources: dict[Tag, Effect[M]]) -> Effect[Union[Control, Tagged[M]]]
```

- Each effect has its key
- All non empty effectes are forked and grouped together

**Example:**
```python
def reads():
    yield "read1"
    yield "read2"

def writes():
    yield "write1"

tagged = listen({"read": reads(), "write": writes()})
# Yields: {"type": "read", "read": "read1"}
#         {"type": "write", "write": "write1"}
#         {"type": "read", "read": "read2"}
```

#### loop 

```python
def loop(init: Effect[M], next_: Callable[[M], Effect[M]]) -> Task[None, None]
```

Creates a feedback loop here each message will produce a new effect 
- Enqueues the initial effect
- For each message from step(), enqueues next_(message)
- Suspends when all effects are idle
- Completes when there are no more active or idle tasks

#### move 

```python
def move(fork: Fork[T, X, M], group: TaskGroup[T, X, M]) -> None
```

Move a fork from its current do a different group .Moving the top task in the active queue is done with attention .
The ``` step() ``` checks for group changes ( to prevent race conditions ) 

#### resume 

```python
def resume(task: Controller[M, T] | Fork[T, X, M]) -> None
```

Resumes a suspended task by enqueuing it for execution.
- Calls ``` enqueue() ``` to add the task again to the active queue ( unblocks the driver if blocked)
- execution is resumed in the next scheduler tick

#### send 

```python
def send(message: M) -> Effect[M]
```

Creates an effect that sends a single message.

#### sleep

```python
def sleep(duration: float = 0) -> Task[Control, None]
```

Suspends execution for a specified duration in milliseconds, then resumes.
if the task is aborted uses finally block cancels the timer 

#### spawn 

```python 
def spawn(task: Task[None, None]) -> None
```

Executes a detached task that cannot be joined 

#### suspend 

```python
def suspend() -> Generator[SuspendInstruction, Any, None]
```

Suspends the current task until it is resumed by calling resume() with its handle. finally block is there if the task is aborted 

#### tag 

```python
def tag(effect: Union[Fork[T, X, M], Tagger[T, X, M]], tag: str) -> Effect[Union[Control, Tagged[M]]]
```

Tags an effect by boxing each message with a type identifier.

**Example:**
```python
def numbers():
    yield 1
    yield 2

tagged = tag(fork(numbers()), "num")
# Yields: {"type": "num", "num": 1}
#         {"type": "num", "num": 2}
```

#### then_

```python
def then_(
    task: Task[M, T],
    resolve: Callable[[T], U],
    reject: Callable[[X], U]
) -> Task[M, U]
```

Executes the task and if successful, calls resolve() or reject() basically promise like then interface for tasks 

`U` is used specifically in the `then_()` function to represent the **transformed return type** after applying success or failure handlers.

```python
def get_number() -> Task[None, int]:
    return 42
    yield

# Transform int to string (U = str)
def process():
    result = yield from then_(
        get_number(),
        resolve=lambda n: f"Success: {n}",  # int -> str
        reject=lambda e: f"Error: {e}"      # Exception -> str
    )
    print(result) 
    yield

# Run the task
main(process())
```

#### wait 

```python
def wait(input: Union[Awaitable[T], T]) -> Task[Control, T]
```

Provides equivalent of `await` in async functions. Specifically it takes a value that you can `await` on (that is `[T]`, i.e futures, coroutines) and suspends execution until future is settled. If future succeeds execution is resumed with `T` otherwise an error of type `X` is thrown (which is by default `unknown` since futures do not encode error type). It is useful when you need to deal with potentially async set of operations without having to check if thing is an `await`-able at every step.

**Please note** that execution is suspended even if given value is not a promise, however scheduler will still resume it in the same tick of the event loop after, just processing other scheduled tasks. This avoids problematic race condititions that can otherwise occur when values are sometimes promises and other times are not.

**Example:**
```python 
def fetch_json(url, options):
    response = yield from wait(fetch(url, options))
    json = yield from wait(response.json())
    return json
```

## Examples 

### Hello World Task

```python
from actress.task import main

def hello_world():
    print("Hello from a task!")
    yield
    print("Task complete")

main(hello_world())
```

### Producing Messages with `send()`

```python
from actress.task import main, send

def counter():
    for i in range(3):
        print(f"Sending message: {i}")
        yield from send(i)
    print("Counter complete")

main(counter())
```

### Concurrent Tasks with `fork()` and `join()`

```python
from actress.task import main, fork, join, sleep

def worker(id, duration):
    print(f"Worker {id} starting")
    yield from sleep(duration)
    print(f"Worker {id} done")
    return f"Result from worker {id}"

def coordinator():
    worker1 = fork(worker(1, 1000))
    worker2 = fork(worker(2, 500))

    result1 = yield from join(worker1)
    print(result1)

    result2 = yield from join(worker2)
    print(result2)

main(coordinator())
```

### Running `all_()`

```python
from actress.task import main, all_, sleep

def task_a():
    yield from sleep(300)
    return "A"

def task_b():
    yield from sleep(100)
    return "B"

def task_c():
    yield from sleep(200)
    return "C"

def run_all():
    results = yield from all_([task_a(), task_b(), task_c()])
    print(results)

main(run_all())
```

## Contributing

All welcome! storacha.network is open-source.

To contribute to this project:

1. Install development dependencies:
   ```sh
   pip install -r requirements.txt
   ```

2. Run tests:
   ```sh
   pytest
   ```

## License

Dual-licensed under [Apache-2.0 OR MIT](LICENSE.md)
