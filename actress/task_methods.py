from __future__ import annotations
from typing import Any, Callable, Generator, Optional, Union, Iterable
import threading

from .scheduler import SCHEDULER
from .effect import Effect, NoneEffect, AggregateEffect
from .types import Fork


class Task:
    def __init__(self, gen: Generator[Any, Any, Any]) -> None:
        self._gen = gen
        self._started = False
        self._next_value: Any = None
        self._done = False
        self._fork = Fork(self)
        self._last_exception: Optional[BaseException] = None

    @property
    def done(self) -> bool:
        return self._done

    @property
    def fork(self) -> Fork:
        return self._fork

    def _step(self) -> None:
        if self._done:
            return
        try:
            if not self._started:
                self._started = True
                yielded = next(self._gen)
            else:
                yielded = self._gen.send(self._next_value)
            self._next_value = None

            if isinstance(yielded, Task):
                SCHEDULER.enqueue(yielded)
                eff = _WaitForForkEffect(yielded.fork)
            elif isinstance(yielded, Effect):
                eff = yielded
            else:
                eff = _ImmediateEffect(yielded)

            def _resume_cb(value: Any) -> None:
                if self._done:
                    return
                self._next_value = value
                SCHEDULER.enqueue(self)

            eff.run(_resume_cb)

        except StopIteration as stop:
            self._done = True
            self._fork._finish(stop.value)
        except BaseException as e:
            self._done = True
            self._last_exception = e
            self._fork._finish(error=e)

    @staticmethod
    def fork(task_like: Union["Task", Generator[Any, Any, Any]]) -> Fork:
        task = task_like if isinstance(task_like, Task) else Task(task_like)
        SCHEDULER.enqueue(task)
        return task.fork

    @staticmethod
    def send(actor: Any, msg: Any) -> Effect:
        class SendEffect(Effect):
            def run(self, cb: Callable[[Any], None]) -> None:
                actor.inbox.append(msg)
                cb(None)
        return SendEffect()

    @staticmethod
    def loop(actor: Any) -> "Task":
        def _loop() -> Generator[Any, Any, None]:
            inbox = getattr(actor, "inbox", None)
            while True:
                if inbox:
                    msg = inbox.pop(0)
                    handle = getattr(actor, "handle")
                    result = handle(msg)
                    if isinstance(result, Task):
                        yield result
                    elif isinstance(result, Effect):
                        yield result
                    else:
                        yield Task.none()
                else:
                    yield Task.none()
        return Task(_loop())

    @staticmethod
    def listen(register: Callable[[Callable[[Any], None]], None]) -> Effect:
        class ListenEffect(Effect):
            def run(self, cb: Callable[[Any], None]) -> None:
                register(cb)
        return ListenEffect()

    @staticmethod
    def effects(effects: Iterable[Effect]) -> Effect:
        return AggregateEffect(list(effects))

    @staticmethod
    def none() -> Effect:
        return NoneEffect()

    @staticmethod
    def wait(ms: int) -> Effect:
        class WaitEffect(Effect):
            def __init__(self, delay_ms: int) -> None:
                self.delay_ms = delay_ms
            def run(self, cb: Callable[[Any], None]) -> None:
                threading.Timer(self.delay_ms / 1000.0, lambda: cb(None)).start()
        return WaitEffect(ms)

    @staticmethod
    def suspend() -> "Task":
        def _suspend() -> Generator[Any, Any, Any]:
            resume_holder = {}
            def register(cb: Callable[[Any], None]):
                def resume(value: Any = None):
                    cb(value)
                resume_holder["resume"] = resume
            yield Task.listen(register)
            return resume_holder["resume"]
        return Task(_suspend())

    @staticmethod
    def join(fork: Fork) -> "Task":
        def _join() -> Generator[Any, Any, Any]:
            if fork.done:
                return fork.get()
            done_box = {}
            def register(cb: Callable[[Any], None]):
                def _on_done():
                    cb(fork.get())
                fork.on_complete(_on_done)
            value = yield Task.listen(register)
            return value
        return Task(_join())


class _ImmediateEffect(Effect):
    def __init__(self, value: Any) -> None:
        self.value = value
    def run(self, cb: Callable[[Any], None]) -> None:
        cb(self.value)


class _WaitForForkEffect(Effect):
    def __init__(self, fork: Fork) -> None:
        self.fork = fork
    def run(self, cb: Callable[[Any], None]) -> None:
        if self.fork.done:
            cb(self.fork.get())
        else:
            self.fork.on_complete(lambda: cb(self.fork.get()))
