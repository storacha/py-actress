import time
from actress.task_methods import Task, SCHEDULER

class Box:
    def __init__(self) -> None:
        self.inbox = []
        self.processed = []

    def handle(self, msg):
        self.processed.append(msg)
        return Task.none()

def run_scheduler_until_idle(timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if SCHEDULER.queue:
            task = SCHEDULER.queue.popleft()
            task._step()
        else:
            time.sleep(0.01)
            if not SCHEDULER.queue:
                break

def run_scheduler_for_a_bit(duration=1.0):
    end = time.time() + duration
    while time.time() < end:
        if SCHEDULER.queue:
            task = SCHEDULER.queue.popleft()
            task._step()
        else:
            time.sleep(0.01)

def reset_scheduler():
    SCHEDULER.queue.clear()

def test_none_effect():
    box = Box()
    def main():
        yield Task.none()
        yield Task.send(box, "ping")
    Task.fork(Task.loop(box))
    Task.fork(Task(main()))
    run_scheduler_until_idle()
    assert box.processed == ["ping"]
    reset_scheduler()

def test_send_and_loop():
    box = Box()
    def main():
        yield Task.send(box, 1)
        yield Task.send(box, 2)
        yield Task.send(box, 3)
        yield Task.none()
    Task.fork(Task.loop(box))
    Task.fork(Task(main()))
    run_scheduler_until_idle()
    assert box.processed == [1, 2, 3]
    reset_scheduler()

def test_wait():
    box = Box()
    def main():
        yield Task.send(box, "before")
        yield Task.wait(100)
        yield Task.send(box, "after")
    Task.fork(Task.loop(box))
    Task.fork(Task(main()))
    run_scheduler_for_a_bit(1.0)
    assert box.processed == ["before", "after"]
    reset_scheduler()

def test_effects():
    box = Box()
    def producer():
        e1 = Task.send(box, "a")
        e2 = Task.send(box, "b")
        yield Task.effects([e1, e2])
    Task.fork(Task.loop(box))
    Task.fork(Task(producer()))
    run_scheduler_until_idle()
    assert set(box.processed) == {"a", "b"}
    reset_scheduler()

def test_listen():
    box = Box()
    def waiter():
        result = yield Task.listen(lambda cb: cb("ping"))
        yield Task.send(box, result)
    Task.fork(Task.loop(box))
    Task.fork(Task(waiter()))
    run_scheduler_until_idle()
    assert box.processed == ["ping"]
    reset_scheduler()

def test_fork_multiple_actors():
    a = Box()
    b = Box()
    def main_a():
        yield Task.send(a, "a1")
        yield Task.send(a, "a2")
    def main_b():
        yield Task.send(b, "b1")
        yield Task.send(b, "b2")
    Task.fork(Task.loop(a))
    Task.fork(Task.loop(b))
    Task.fork(Task(main_a()))
    Task.fork(Task(main_b()))
    run_scheduler_until_idle()
    assert a.processed == ["a1", "a2"]
    assert b.processed == ["b1", "b2"]
    reset_scheduler()

if __name__ == "__main__":
    print("Running py-actress tests...")
    test_none_effect()
    test_send_and_loop()
    test_wait()
    test_effects()
    test_listen()
    test_fork_multiple_actors()
    print("All tests passed ✅")
