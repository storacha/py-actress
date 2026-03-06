import asyncio
from collections.abc import Generator
import json

import pytest

from actress import task
from .utils import create_log, inspect, InspectorResult


@pytest.mark.asyncio
class TestWait:
    async def test_wait_on_non_promise(self):
        is_sync = True
        def worker():
            message = yield from task.wait(5)
            assert is_sync == True, "expect to be sync"
            return message

        fork = inspect(worker()).activate()
        is_sync = False
        result = await fork

        assert result == InspectorResult(ok=True, value=5, mail=[], error=None)

    async def test_wait_on_promise(self):
        is_sync = True

        loop = asyncio.get_running_loop()
        promise = loop.create_future()
        promise.set_result(5)

        def main():
            message = yield from task.wait(promise)
            assert is_sync == False, "expect to be async"
            return message

        fork = inspect(main()).activate()
        is_sync = False
        result = await fork

        assert result == InspectorResult(ok=True, value=5, mail=[], error=None)

    async def test_lets_you_yield(self):
        def main():
            value = yield 5
            assert value == None, "return None on normal yield"

        result = await inspect(main())
        assert result == InspectorResult(value=None, ok=True, mail=[5], error=None)

    async def test_throw_on_failed_promises(self):
        boom = Exception("boom!")

        loop = asyncio.get_running_loop()
        promise = loop.create_future()
        promise.set_exception(boom)

        def main():
            message = yield from task.wait(promise)
            return message

        result = await inspect(main())
        assert result == InspectorResult(ok=False, value=None, mail=[], error=boom)

    async def test_can_catch_promise_errors(self):
        boom = Exception("boom!")

        loop = asyncio.get_running_loop()
        promise = loop.create_future()
        promise.set_exception(boom)

        def main():
            try:
                message = yield from task.wait(promise)
                return message
            except Exception as e:
                return e

        result = await inspect(main())
        assert result == InspectorResult(ok=True, value=boom, mail=[], error=None)

    async def test_can_intercept_thrown_errors(self):
        boom = Exception("boom!")
        def fail():
            raise boom

        def main():
            if False: yield
            return fail()

        result = await inspect(main())
        assert result == InspectorResult(ok=False, mail=[], error=boom, value=None)

    async def test_can_catch_thrown_errors(self):
        boom = Exception("boom!")
        def fail():
            raise boom

        def main():
            try:
                if False: yield
                return fail()
            except Exception as e:
                return e

        result = await inspect(main())
        assert result == InspectorResult(ok=True, mail=[], value=boom, error=None)

    async def test_use_finally(self):
        boom = Exception("boom!")
        finalized = False

        loop = asyncio.get_running_loop()
        promise = loop.create_future()
        promise.set_exception(boom)

        def main():
            nonlocal finalized
            try:
                message = yield from task.wait(promise)
                return message
            finally:
                finalized = True

        result = await inspect(main())
        assert result == InspectorResult(ok=False, mail=[], error=boom, value=None)
        assert finalized == True


@pytest.mark.asyncio
class TestMessaging:
    async def test_can_send_message(self):
        def main():
            yield from task.send("one")
            yield from task.send("two")

        result = await inspect(main())
        assert result == InspectorResult(value=None, ok=True, mail=["one", "two"], error=None)

    async def test_can_send_message_in_finally(self):
        def main():
            try:
                yield from task.send("one")
                yield from task.send("two")
            finally:
                yield from task.send("three")

        result = await inspect(main())
        assert result == InspectorResult(value=None, ok=True, mail=["one", "two", "three"], error=None)

    async def test_can_send_message_after_exception(self):
        boom = Exception("boom!")
        def main():
            try:
                yield from task.send("one")
                yield from task.send("two")
                raise boom
                yield from task.send("three")
            finally:
                yield from task.send("four")

        result = await inspect(main())
        assert result == InspectorResult(value=None, ok=False, mail=["one", "two", "four"], error=boom)

    async def test_can_send_message_after_rejected_promise(self):
        boom = Exception("boom!")
        def main():
            try:
                yield from task.send("one")
                yield from task.send("two")

                loop = asyncio.get_running_loop()
                promise = loop.create_future()
                promise.set_exception(boom)

                yield from task.wait(promise)
                yield from task.send("three")
            finally:
                yield from task.send("four")

        result = await(inspect(main()))
        assert result == InspectorResult(ok=False, error=boom, value=None, mail=["one", "two", "four"])

    async def test_can_send_message_before_rejected_promise_in_finally(self):
        boom = Exception("boom!")
        oops = Exception("oops!")
        def main():
            try:
                yield from task.send("one")
                yield from task.send("two")

                loop = asyncio.get_running_loop()
                promise = loop.create_future()
                promise.set_exception(boom)

                yield from task.wait(promise)
                yield from task.send("three")
            finally:
                loop = asyncio.get_running_loop()
                promise = loop.create_future()
                promise.set_exception(oops)

                yield from task.wait(promise)
                yield from task.send("four")

        result = await(inspect(main()))
        assert result == InspectorResult(ok=False, error=oops, value=None, mail=["one", "two"])

    async def test_subtasks_can_send_messages(self):
        oops = Exception("oops")
        def worker():
            yield from task.send("c1")

        def main():
            try:
                yield from task.send("one")
                yield from task.send("two")
                yield from worker()
                yield from task.send("three")
            finally:
                yield from task.send("four")

                loop = asyncio.get_running_loop()
                promise = loop.create_future()
                promise.set_exception(oops)

                yield from task.wait(promise)
                yield from task.send("five")

        result = await inspect(main())
        assert result == InspectorResult(
            ok=False, mail=["one", "two", "c1", "three", "four"], value = None, error=oops
        )

@pytest.mark.asyncio
class TestSubtasks:
    async def test_subtask_crashes_parent(self):
        err = Exception(5)
        def worker(x, y):
            return (yield from task.wait(x)) + (yield from task.wait(y))

        def main():
            loop = asyncio.get_running_loop()
            promise = loop.create_future()
            promise.set_exception(err)

            one = yield from worker(1, 2)
            two = yield from worker(promise, one)
            return two

        result = await inspect(main())
        assert result == InspectorResult(ok=False, value=None, mail=[], error=err)

    async def test_fork_does_not_crash_parent(self):
        boom = Exception("boom")

        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        loop = asyncio.get_running_loop()

        def work(id: str):
            log(f"start {id}")
            yield from task.send(f"{id}#1")

            promise = loop.create_future()
            promise.set_exception(boom)

            yield from task.wait(promise)
            return 0

        def main():
            yield from task.fork(work("A"))

            promise_1 = loop.create_future()
            promise_1.set_result("one")
            yield from task.wait(promise_1)

            yield from task.fork(work("B"))

            promise_2 = loop.create_future()
            promise_2.set_result("two")
            return (yield from task.wait(promise_2))

        result = await inspect(main())
        assert result == InspectorResult(ok=True, value="two", mail=[], error=None)
        assert output == ["start A", "start B"]


    async def test_waiting_on_forks_result_crashes_parent(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def worker(id: str):
            log(f"Start {id}")
            yield from task.send(f"{id}#1")

            loop = asyncio.get_running_loop()
            promise = loop.create_future()

            boom = Exception(f"{id}!boom")
            promise.set_exception(boom)
            yield from task.wait(promise)

        def main():
            a = yield from task.fork(worker("A"))

            loop = asyncio.get_running_loop()
            promise = loop.create_future()
            promise.set_result("one")
            yield from task.wait(promise)

            b = yield from task.fork(worker("B"))

            yield from task.send("hi")

            yield from task.group([a, b])

            loop = asyncio.get_running_loop()
            promise = loop.create_future()
            promise.set_result("two")
            yield from task.wait(promise)

            return 0

        result = await inspect(main())
        expected = InspectorResult(
            ok=False, value=None, error=Exception("A!boom"), mail=["hi", "B#1"]
        )
        assert result["ok"] == expected["ok"]
        assert result["mail"] == expected["mail"]
        assert result["value"] == expected["value"]
        assert str(result["error"]) == str(expected["error"])

    async def test_joining_failed_forks_crashes_parent(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def work(id):
            log(f"Start {id}")
            yield from task.send(f"{id}#1")
            return id

        def main():
            a = yield from task.fork(work("A"))

            loop = asyncio.get_running_loop()
            promise = loop.create_future()
            promise.set_result("one")
            yield from task.wait(promise)

            b = yield from task.fork(work("B"))

            yield from task.send("hi")

            result = yield from task.join(b)
            assert result == "B"

            result2  = yield from task.join(a)
            assert result2 == "A"

        result = await inspect(main())

        assert result == InspectorResult(ok=True, value=None, mail=["hi", "B#1"], error=None)
        assert output == ["Start A", "Start B"]

    async def test_failing_group_member_terminates_group(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)
        boom = Exception("boom")

        def work(ms = 0, name = "", crash = False):
            log(f"{name} on duty")
            if crash:
                yield from task.sleep(ms)
                raise boom

            try:
                yield from task.sleep(ms)
                log(f"{name} is done")
            finally:
                log(f"{name} cancelled")

        def main():
            a = yield from task.fork(work(1, "A"))

            yield from task.sleep(2)

            b = yield from task.fork(work(8, "B"))
            c = yield from task.fork(work(14, "C"))
            d = yield from task.fork(work(4, "D", True))
            e = yield from task.fork(work(10, "E"))

            try:
                yield from task.group([a, b, c, d, e])
            except Exception as e:
                yield from task.sleep(30)
                return e

        assert (
            ((await inspect(main()))) ==
            InspectorResult(ok=True, value=boom, mail=[], error=None)
        )
        assert sorted(output) == sorted([
            "A on duty",
            "B on duty",
            "C on duty",
            "D on duty",
            "E on duty",
            "A is done",
            "E cancelled",
            "A cancelled",
            "B cancelled",
            "C cancelled",
        ])

    async def test_failed_task_fails_the_group(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)
        boom = Exception("boom")

        def fail(error=boom) -> Generator[None, None, None]:
            raise error
            yield

        def work(ms = 0, name = "", crash = False):
            log(f"{name} on duty")

            try:
                yield from task.sleep(ms)
                log(f"{name} is done")
            finally:
                log(f"{name} cancelled")

        def main():
            f = yield from task.fork(fail(boom))
            a = yield from task.fork(work(2, "a"))
            yield from task.sleep()

            yield from task.group([a, (yield from task.fork(work(4, "b"))), f, (yield from task.fork(work(2, "c")))])

        assert (
            ((await inspect(main()))) ==
            InspectorResult(ok=False, value=None, mail=[], error=boom)
        )
        await task.fork(task.sleep(10))
        assert output == ["a on duty", "a cancelled"]

    async def test_can_make_empty_group(self):
        def main():
            return (yield from task.group([]))
        assert (
            (await inspect(main())) ==
            InspectorResult(ok=True, value=None, error=None, mail=[])
        )


class TestConcurrency:
    async def test_can_run_tasks_concurrently(self):
        def worker(name: str, duration: float, count: int):
            for n in range(1, count + 1):
                yield from task.sleep(duration)
                yield from task.send(f"{name}#{n}")

        def main():
            a = yield from task.fork(worker("a", 5, 6))
            yield from task.sleep(5)
            b = yield from task.fork(worker("b", 7, 7))

            yield from task.group([a, b])

        result = await inspect(main())
        mail: list[str] = result["mail"]  # type: ignore[assignemnt]
        assert sorted(mail) == [
            "a#1",
            "a#2",
            "a#3",
            "a#4",
            "a#5",
            "a#6",
            "b#1",
            "b#2",
            "b#3",
            "b#4",
            "b#5",
            "b#6",
            "b#7",
        ], "has all the items"

    async def test_can_fork_and_join(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def work(name):
            log(f"> {name} sleep")
            yield from task.sleep(2)
            log(f"< {name} wake")

        def main():
            log("Spawn A")
            a = yield from task.fork(work("A"))

            log("Sleep")
            yield from task.sleep(20)

            log("Spawn B")
            b = yield from task.fork(work("B"))

            log("Join")
            merge = task.group([a, b])
            yield from merge

            log("Nap")
            yield from task.sleep(2)

            log("Exit")

        await task.fork(main(), task.ForkOptions(name="🤖"))

        assert output == [
            "Spawn A",
            "Sleep",
            "> A sleep",
            "< A wake",
            "Spawn B",
            "Join",
            "> B sleep",
            "< B wake",
            "Nap",
            "Exit",
        ]

    async def test_joining_failed_task_throws(self):
        boom = Exception("boom")

        def work():
            raise boom

        def main():
            worker = yield from task.fork(work())
            yield from task.sleep(10)

            yield from task.join(worker)

        result = await inspect(main())
        assert result == InspectorResult(ok=False, error=boom, value=None, mail=[])

    async def test_spawn_can_outlive_parent(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def worker():
            log("start fork")
            yield from task.sleep(2)
            log("exit fork")

        def main():
            log("start main")
            yield from task.spawn(worker())
            log("exit main")

        await task.fork(main())
        await task.fork(task.sleep(20))

        assert output == [
            "start main",
            "exit main",
            "start fork",
            "exit fork",
        ]

    async def test_throws_on_exit(self):
        boom = Exception("boom")
        def work():
            try:
                yield from task.sleep(5)
            finally:
                raise boom

        def main():
            worker = yield from task.fork(work())
            yield from task.sleep()
            yield from task.exit_(worker, None)

        assert (await inspect(main())) == InspectorResult(ok=True, value=None, mail=[], error=None)


class TestCanAbort:
    async def test_can_terminate_sleeping_task(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def worker():
            log("start worker")
            yield from task.sleep(20)
            log("wake worker")

        def main():
            log("fork worker")
            fork_task = yield from task.fork(worker())
            log("nap")
            yield from task.sleep(1)
            log("terminate worker")
            yield from task.terminate(fork_task)
            log("exit main")

        expect = [
            "fork worker",
            "nap",
            "start worker",
            "terminate worker",
            "exit main",
        ]
        await task.fork(main())
        assert output == expect
        await task.fork(task.sleep(30))

    async def test_sleeping_task_can_still_cleanup(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def worker():
            log("start worker")
            loop = asyncio.get_running_loop()
            id = loop.call_later(10/1000, (lambda: log("timeout fired")))

            try:
                yield from task.suspend()
            finally:
                id.cancel()
                log("can clean up even though aborted")

        def main():
            log("fork worker")
            fork_task = yield from task.fork(worker())
            log("nap")
            yield from task.sleep(1)
            log("abort worker")
            yield from task.terminate(fork_task)
            log("exit main")

        expect = [
            "fork worker",
            "nap",
            "start worker",
            "abort worker",
            "can clean up even though aborted",
            "exit main",
        ]
        await task.fork(main())
        await task.fork(task.sleep(30))


    async def test_can_abort_with_an_error(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def worker():
            try:
                log("start worker")
                yield from task.sleep(20)
                log("wake worker")
            except Exception as e:
                log(f"aborted {e}")

        def main():
            log("fork worker")
            fork = yield from task.fork(worker())
            log("nap")
            yield from task.sleep(1)
            log("abort worker")
            yield from task.abort(fork, error=Exception("kill"))
            log("exit main")

        expect = [
            "fork worker",
            "nap",
            "start worker",
            "abort worker",
            "aborted kill",
            "exit main",
        ]
        await task.fork(main())
        assert output == expect
        await task.fork(task.sleep(30))

        assert output == expect

    async def test_can_still_do_things_when_aborted(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def worker():
            try:
                log("start worker")
                yield from task.sleep(20)
                log("wake worker")
            except Exception as e:
                log(f"aborted {e}")
                yield from task.sleep(2)
                log("ok bye")

        def main():
            log("fork worker")
            fork = yield from task.fork(worker())
            log("nap")
            yield from task.sleep(1)
            log("abort worker")
            yield from task.abort(fork, Exception("kill"))
            log("exit main")

        expect = [
            "fork worker",
            "nap",
            "start worker",
            "abort worker",
            "aborted kill",
            "exit main",
        ]
        await task.fork(main())
        assert output == expect
        await task.fork(task.sleep(10))
        assert output == [*expect] + ["ok bye"]

    async def test_can_still_suspend_after_aborting(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def worker():
            current_task = yield from task.current()
            try:
                log("start worker")
                yield from task.sleep(20)
                log("wake worker")
            except Exception as e:
                log(f"aborted {e}")
                loop = asyncio.get_running_loop()
                loop.call_later(2/1000, lambda: task.resume(current_task))
                log("suspend after abort")
                yield from task.suspend()
                log("ok bye now")

        def main():
            log("fork worker")
            fork = yield from task.fork(worker())
            log("nap")
            yield from task.sleep(1)
            log("abort worker")
            yield from task.abort(fork, Exception("kill"))
            log("exit main")

        expect = [
            "fork worker",
            "nap",
            "start worker",
            "abort worker",
            "aborted kill",
            "suspend after abort",
            "exit main",
        ]
        await task.fork(main())
        assert output == expect
        await task.fork(task.sleep(10))
        assert output == [*expect] + ["ok bye now"]

    async def test_can_exit_the_task(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def main():
            log("fork worker")
            fork = yield from task.fork(worker())
            log("nap")
            yield from task.sleep(1)
            log("exit worker")
            yield from task.exit_(fork, 0)
            log("exit main")

        def worker():
            try:
                log("start worker")
                yield from task.sleep(20)
                log("wake worker")
                return 0
            except Exception as e:
                # generator exits with `StopIteration` in Python - a feature not a bug
                log(f"aborted {e}")
                return 1

        expect = [
            "fork worker",
            "nap",
            "start worker",
            "exit worker",
            "aborted generator raised StopIteration",
            "exit main",
        ]
        await task.fork(main())
        print(output)
        assert output == expect
        await task.fork(task.sleep(30))
        assert output == expect


class TestPromise:
    async def test_fails_promise_if_task_fails(self):
        class boom(Exception):
            def __init__(self, message: str) -> None:
                super().__init__(message)

        def main():
            raise boom("boom")
            yield

        with pytest.raises(boom) as exc_info:
            result = await task.fork(main())
            assert str(exc_info.value) == "boom"

    async def test_can_use_await(self):
        # alternative for js test: "can use then"
        def work():
            yield from task.sleep(1)
            return 0

        result = await task.fork(work())
        assert result == 0

    async def test_can_bubble_error(self):
        # alternative for js test: "can use catch"
        boom = Exception("boom")

        def work():
            yield from task.sleep(1)
            raise boom
            yield

        try:
            await task.fork(work())
        except Exception as e:
            assert e == boom

    async def test_can_use_finally(self):
        def work():
            yield from task.sleep(1)
            return 0

        invoked = False
        try:
            result = await task.fork(work())
        finally:
            invoked = True

        assert result == 0
        assert invoked == True

    async def test_has_to_string_tag(self):
        fork = task.fork(task.sleep(2))
        assert str(fork) == f"Fork(id={fork.id}, status='{fork.status}')"


class TestTag:
    async def test_tags_effect(self):
        def fx():
            yield from task.send(1)
            yield from task.sleep(2)
            yield from task.send(2)

        result = await inspect(task.tag(fx(), "fx"))
        assert result == InspectorResult(
            ok=True, value=None, error=None, mail=[
                {"type": "fx", "fx": 1},
                {"type": "fx", "fx": 2},
            ]
        )

    async def test_tags_with_errors(self):
        error = Exception("boom")
        def fx():
            yield from task.send(1)
            raise error

        def main():
            yield from task.tag(fx(), "fx")

        result = await inspect(main())
        assert result == InspectorResult(
            ok=False, error=error, value=None, mail=[{"type": "fx", "fx": 1}]
        )

    async def test_can_terminate_tagged(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def fx():
            yield from task.send(1)
            log("send 1")
            yield from task.sleep(2)
            yield from task.send(2)
            log("send 2")

        def main():
            fork = yield from task.fork(task.tag(fx(), "fx"))
            yield from task.sleep(1)
            yield from task.terminate(fork)

        result = await inspect(main())
        assert result == InspectorResult(ok=True, value=None, error=None, mail=[])
        assert output == ["send 1"]
        await task.fork(task.sleep(5))
        assert output == ["send 1"]

    async def test_can_abort_tagged(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def fx():
            yield from task.send(1)
            log("send 1")
            yield from task.sleep(1)
            yield from task.send(2)
            log("send 2")

        def main():
            tagged = task.tag(fx(), "fx")
            assert str(tagged) == "TaggedEffect"
            fork = yield from task.fork(tagged)
            yield from task.sleep(1)
            yield from task.abort(fork, Exception("kill"))

        result = await inspect(main())
        assert result == InspectorResult(ok=True, value=None, error=None, mail=[])
        assert output == ["send 1"]
        await task.fork(task.sleep(5))
        assert output == ["send 1"]

    async def test_can_double_tag(self):
        def fx():
            yield from task.send(1)
            yield from task.sleep(1)
            yield from task.send(2)

        tagged = task.tag(task.tag(fx(), "foo"), "bar")
        assert (
            (await inspect(tagged)) ==
            InspectorResult(
                ok=True,
                value=None,
                mail=[
                    {"type": "bar", "bar": {"type": "foo", "foo": 1}},
                    {"type": "bar", "bar": {"type": "foo", "foo": 2}}
                ],
                error=None
            )
        )

    async def test_tagging_none_is_no_op(self):
        def fx():
            yield from task.send(1)
            yield from task.sleep(1)
            yield from task.send(2)

        tagged = task.tag(task.tag(task.none_(), "foo"), "bar")
        assert (
            (await inspect(tagged)) ==
            InspectorResult(ok=True, value=None, mail=[], error=None)
        )


class TestEffect:
    async def test_can_listen_to_several_fx(self):
        def source(delay, count):
            for n in range(count):
                yield from task.sleep(delay)
                yield from task.send(n)

        fx = task.listen({
            "beep": source(3, 5),
            "bop": source(5, 3),
            "buz": source(2, 2),
            })

        result = await inspect(fx)
        mail = result.pop("mail")
        assert result == {"ok": True, "value": None, "error": None}
        inbox = list(map(lambda m: json.dumps(m), mail))  # type: ignore[arg-type]

        expect = [
            {"type": "beep", "beep": 0},
            {"type": "beep", "beep": 1},
            {"type": "beep", "beep": 2},
            {"type": "beep", "beep": 3},
            {"type": "beep", "beep": 4},
            {"type": "bop", "bop": 0},
            {"type": "bop", "bop": 1},
            {"type": "bop", "bop": 2},
            {"type": "buz", "buz": 0},
            {"type": "buz", "buz": 1},
        ]
        print(f"DEBUG: \n{sorted(inbox)}")
        assert sorted(inbox) != inbox, "messages arent supposed ordered by actors"
        assert sorted(inbox) == list(map(lambda v: json.dumps(v), expect)), "all messages has to be recieved"

    async def test_can_listen_to_none(self):
        assert (
            (await inspect(task.listen({}))) ==
            InspectorResult(ok=True, value=None, mail=[], error=None)
        )

    async def test_can_produce_no_messages_on_empty_tasks(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def work():
            log("start work")
            yield from task.sleep(2)
            log("end work")

        main = task.listen({"none": work()})

        assert (
            (await inspect(main)) ==
            InspectorResult(ok=True, value=None, mail=[], error=None)
        )

    async def test_can_turn_task_into_effect(self):
        def work():
            task.sleep(1)
            return "hi"
            yield

        fx = task.effect(work())

        assert (
            (await inspect(fx)) ==
            InspectorResult(ok=True, value=None, mail=["hi"], error=None)
        )

    async def test_can_turn_multiple_tasks_into_effect(self):
        def fx(msg="", delay=1):
            yield from task.sleep(delay)
            return msg

        effect = task.effects([fx("foo", 5), fx("bar", 1), fx("baz", 2)])
        assert (
            (await inspect(effect)) == InspectorResult(
                ok=True, value=None, mail=["bar", "baz", "foo"], error=None
            )
        )

    async def test_can_turn_zero_tasks_into_effect(self):
        effect = task.effects([])
        assert (
            (await inspect(effect)) == InspectorResult(
                ok=True, value=None, mail=[], error=None
            )
        )

    async def test_can_batch_multiple_effects(self):
        def fx(msg="", delay=1):
            yield from task.sleep(delay)
            yield from task.send(msg)

        effect = task.batch([fx("foo", 5), fx("bar", 1), fx("baz", 2)])
        assert (
            (await inspect(effect)) == InspectorResult(
                ok=True, value=None, mail=["bar", "baz", "foo"], error=None
            )
        )

    async def test_can_loop(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def step(*, n: int = 0):
            log(f"<< {n}")
            while (n := n - 1) > 0:
                log(f">> {n}")
                yield from task.sleep(n)
                yield from task.send({"n": n})

        main = await task.fork(task.loop(step(n=4), step))  # type: ignore[arg-type]
        assert sorted(output) != output
        assert sorted(output) == sorted([
            "<< 4",
            ">> 3",
            ">> 2",
            ">> 1",
            "<< 3",
            ">> 2",
            ">> 1",
            "<< 2",
            ">> 1",
            "<< 1",
            "<< 2",
            ">> 1",
            "<< 1",
            "<< 1",
            "<< 1",
        ])

    async def test_can_wait_in_a_loop(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def msg_func(message):
            log(f"<< {message}")
            result = yield from task.wait(0)
            log(f">> {result}")
            return
            yield

        main = task.loop(task.send("start"), msg_func)

        assert (await task.fork(main)) == None
        assert output == ["<< start", ">> 0"]


class TestAllOperator:
    async def test_can_get_all_results(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def work(duration: int, result: str):
            yield from task.sleep(duration)
            log(result)
            return result

        def main():
            result = yield from task.all_([
                work(2, "a"),
                work(9, "b"),
                work(5, "c"),
                work(0, "d"),
            ])
            return result

        result = await task.fork(main())
        assert result == ["a", "b", "c", "d"]
        assert result != output
        assert sorted(result) == sorted(output)

    async def test_on_failure_all_other_tasks_are_aborted(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def work(duration: int, name: str, crash: bool = False):
            yield from task.sleep(duration)
            log(name)
            if crash:
                raise Exception(f"{name}")
            else:
                return name

        def main():
            result = yield from task.all_([
                work(2, "a"),
                work(9, "b"),
                work(5, "c", True),
                work(0, "d"),
                work(8, "e"),
            ])
            return result

        result = await inspect(main())
        assert result["ok"] == False
        assert str(result["error"]) == str(Exception("c"))
        assert result["mail"] == []

        await task.fork(task.sleep(20))
        assert sorted(output) == sorted(["d", "a", "c"])

    async def test_can_make_all_of_none(self):
        assert (await task.fork(task.all_([]))) == []


class TestForkAPI:
    async def test_can_use_abort_method(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        kill = Exception("kill")

        def work():
            log("start work")
            yield from task.sleep(2)
            log("end work")

        def main():
            worker = yield from task.fork(work())
            yield from task.sleep(0)
            log("kill")
            yield from worker.abort(kill)
            log("nap")
            yield from task.sleep(5)
            log("exit")

        await task.fork(main())
        assert output == ["start work", "kill", "nap", "exit"]

    async def test_can_use_exit_method(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def work():
            try:
                log("start work")
                yield from task.sleep(2)
                log("end work")
            finally:
                log("cancel work")

        def main():
            worker = yield from task.fork(work())
            yield from task.sleep(0)
            log("kill")
            yield from worker.exit_(None)
            log("nap")
            yield from task.sleep(5)
            log("exit")

        await task.fork(main())
        assert output == [
            "start work",
            "kill",
            "cancel work",
            "nap",
            "exit",
        ]

    async def test_can_use_resume_method(self):
        _logger = create_log()
        output, log = (_logger["output"], _logger["log"],)

        def work():
            log("suspend work")
            yield from task.suspend()
            log("resume work")

        def main():
            worker = yield from task.fork(work())
            yield from task.sleep(2)
            yield from worker.resume()
            log("exit")

        await task.fork(main())
        assert output == ["suspend work", "exit", "resume work"]

    async def test_can_use_join_method(self):
        def work():
            yield from task.send("a")
            yield from task.sleep(2)
            yield from task.send("b")
            return 0

        def main():
            worker = yield from task.fork(work())
            yield from task.sleep(0)
            result = yield from worker.join()
            return result

        result = await inspect(main())
        assert result == InspectorResult(ok=True, value=0, mail=["b"], error=None)

    async def test_has_to_string_tag(self):
        fork_data_dict = {}
        def main():
            fork = yield from task.fork(task.sleep(2))
            fork_data_dict["id"] = fork.id
            fork_data_dict["status"] = fork.status
            return str(fork)

        assert (await task.fork(main())) == f"Fork(id={fork_data_dict['id']}, status='{fork_data_dict['status']}')"

    async def test_is_iterator(self):
        def work():
            yield from task.send("a")
            yield from task.send("b")
            yield from task.send("c")

        def main():
            fork = yield from task.fork(work())
            return list(fork)

        assert (await task.fork(main())) == []

    async def test_can_join_non_active_fork(self):
        def work():
            yield from task.send("hi")

        worker = task.fork(work())

        def main():
            yield from task.join(worker)

        assert (await inspect(main())) == InspectorResult(
            mail=["hi"], ok=True, value=None, error=None
        )
