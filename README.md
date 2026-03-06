# actress

Python implementation of [`actor`](https://github.com/Gozala/actor), built around cooperative scheduling with generator-based tasks.

## Install

```bash
pip install actress
```

## Quick Start

```python
from actress import task


def worker(name: str, delay_ms: int):
    yield from task.sleep(delay_ms)
    yield from task.send(f"done:{name}")
    return name


def main():
    a = yield from task.fork(worker("a", 10))
    b = yield from task.fork(worker("b", 5))
    result_a = yield from task.join(a)
    result_b = yield from task.join(b)
    return [result_a, result_b]


result = await task.fork(main())
# result == ["a", "b"] (order depends on join order; messages are concurrent)
```

## Core Concepts

- `Task[T, X, M]`: a generator representing concurrent work.
  - returns `T` on success
  - can fail with `X` (runtime-enforced by exceptions)
  - can emit messages `M`
- `Effect[M]`: a generator stream of messages/events.
- `Fork[T, X, M]`: handle returned by `task.fork(...)`; can be `await`-ed or `yield from`-ed.

Tasks run on a cooperative scheduler. They yield control explicitly via library operations like `sleep`, `wait`, `suspend`, and message-producing effects.

## API Reference

### `Task[T, X, M]`

Task represents a unit of computation that runs concurrently, a light-weight process. You can spawn many tasks and the cooperative scheduler interleaves execution.

Tasks have three type variables:

- Variable `T` describes return type of successful computation.
- Variable `X` describes error type of failed computation (raised exceptions).
- Variable `M` describes type of messages this task may produce.

> Python does not enforce exception type checking at runtime, so `X` is descriptive rather than guaranteed.

##### `fork(task_gen: Task[M, T], options: ForkOptions | None = None) -> Fork[T, Exception, M]`

Creates a new concurrent task. It is the primary way to activate a task from outside task context, and usually how you start main work. It returns `Fork[T, Exception, M]`, which is awaitable.

```python
async def entry():
    result = await task.fork(main())
    print(result)


def main():
    return 0
    yield
```

You can also start concurrent tasks from other tasks. Forked tasks are detached from the parent task unless joined.

```python
def main():
    worker = yield from task.fork(work())
    print("prints first")


def work():
    print("prints second")
    yield
```

##### `join(fork: Fork[T, X, M]) -> Task[Optional[Instruction[M]], T]`

When a task forks, it gets a `Fork` reference that can be used to join that task back in. The joining task is suspended until fork completes, then resumes with fork return value. If fork fails, `join` raises the same error.

Messages from the fork propagate through the task it is joined with.

```python
def main():
    worker = yield from task.fork(work())
    yield from do_some_other_work()
    try:
        value = yield from task.join(worker)
    except Exception:
        pass
```

##### `abort(handle: ControllerFork[T, X, M], error: Exception) -> Task[None, None]`

Forked task may be aborted by another task if it has a reference to it.

```python
def main():
    worker = yield from task.fork(work())
    yield from task.sleep(10)
    yield from task.abort(worker, Exception("die"))
```

##### `exit_(handle: ControllerFork[T, X, M], value: Any) -> Task[None, None]`

Forked task may be exited successfully by another task if it has a reference to it.

```python
def main():
    worker = yield from task.fork(work())
    result = yield from do_something_concurrently()
    yield from task.exit_(worker, result)
```

##### `spawn(task_gen: Task[None, None]) -> Task[None, None]`

Starts concurrent detached task. This is a lightweight alternative to `fork`, however detached tasks cannot be joined, aborted, or exited via handle.

> `task.spawn(work())` creates task work that spawns provided task when executed. Unlike `fork`, it is not awaitable as a `Fork`.

```python
def main():
    yield from task.spawn(work())
    response = yield from task.wait(fetch())


def work():
    try:
        pass
    except Exception:
        pass
    yield
```

##### `main(task_gen: Task[None, None]) -> None`

Executes top-level task work directly (without returning a `Fork`).

```python
def app_main():
    try:
        while True:
            break
            yield
    except Exception:
        return


task.main(app_main())
```

### `Task[T, X]`

More commonly tasks describe asynchronous operations that may fail (HTTP requests, database operations, etc.) and do not produce messages.

These tasks are similar to futures/promises, but they describe asynchronous operations rather than in-flight result objects.

##### `current() -> Generator[CurrentInstruction, Controller[M, T], Controller[M, T]]`

Gets controller of the currently running task. Controller is usually obtained when a task needs to suspend until an outside event occurs.

##### `suspend() -> Generator[SuspendInstruction, Any, None]`

Suspends current task, which can later be resumed from another task or external callback by calling `task.resume(controller_or_fork)`.

> This task never fails, although it may never resume. `finally` blocks still run if execution is aborted.

```python
def sleep_ms(duration):
    controller = yield from task.current()
    loop = asyncio.get_running_loop()
    handle = loop.call_later(duration / 1000, lambda: task.resume(controller))
    try:
        yield from task.suspend()
    finally:
        handle.cancel()
```

##### `sleep(duration: float = 0) -> Task[Control, None]`

Suspends execution for the given duration (milliseconds), after which execution resumes (unless task is terminated/aborted in the meantime).

```python
def work():
    print("I'm going to take a small nap")
    yield from task.sleep(200)
    print("I am back to work")
```

##### `wait(value: Awaitable[T] | T) -> Task[Control, T]`

Provides equivalent of `await` in task functions. It takes a value you can wait on (`Awaitable[T] | T`) and suspends execution until result is available.

Useful when dealing with **sometimes async** operations.

```python
def fetch_json(url):
    response = yield from task.wait(fetch(url))
    json_data = yield from task.wait(response.json())
    return json_data
```

> Execution is suspended even if input value is not awaitable. Scheduler resumes in the same event-loop turn after processing other queued tasks, avoiding race conditions in mixed sync/async flows.

##### `all_(tasks: Iterable[Task[M, T]]) -> Task[Any, list[T]]`

Takes iterable of tasks and runs them concurrently, returning results in the same order as input tasks (not completion order). If any task fails, all other tasks are aborted and error is thrown into caller.

### `Effect[M]`

Effect is another task variant: instead of describing asynchronous operations that may return/fail, it describes asynchronous operations that may produce a cascade of messages.

Effects represent finite streams and complete.

##### `send(message: M) -> Effect[M]`

Creates an effect that sends the given message.

```python
def work(url):
    try:
        response = yield from task.wait(fetch(url))
        value = yield from task.wait(response.json())
        yield from task.send({"ok": True, "value": value})
    except Exception as error:
        yield from task.send({"ok": False, "error": error})
```

##### `effect(task_gen: Task[None, T]) -> Effect[T]`

Turns a task (that never fails or sends messages) into an effect of its result.

##### `listen(sources: dict[str, Effect[M]]) -> Effect[Control | Tagged[M]]`

Takes several effects and merges them into a single tagged effect so source can be identified via `type` field.

```python
task.listen({
    "read": task.effect(db_read()),
    "write": task.effect(db_write()),
})
```

##### `none_() -> Effect[None]`

Returns empty `Effect`, that is produces no messages. Kind of like `[]` or `""`, useful when you need to interact with an API that takes `Effect`, but in your case you produce none.

##### `batch(effects: list[Effect[T]]) -> Effect[T]`

Takes several effects and combines them into one.

##### `effects(tasks: list[Task[None, T]]) -> Effect[Optional[T]]`

Takes several tasks and creates a combined effect from their results.

##### `tag(effect, tag_name)` and `with_tag(tag, value)`

Helpers for tagged effect streams.

##### `loop(init: Effect[M], next_: Callable[[M], Effect[M]]) -> Task[None, None]`

Runs feedback loops where each emitted message schedules another effect.

##### `then_(task_gen, resolve, reject)`

Transforms task result:

- success -> `resolve(value)`
- failure -> `reject(error)`

## Differences from the JS Reference

This library tracks the same model, but there are Python-specific differences:

- API names adapted for Python keywords:
  - `all_` instead of `all`
  - `none_` instead of `none`
  - `exit_` instead of `exit`
  - `then_` instead of `then`
- Tasks/effects are Python generators, not iterators/promises.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

## Contributing

All welcome! storacha.network is open-source.

## License

Dual-licensed under [Apache-2.0 OR MIT](LICENSE.md)
