from actress.task_methods import Task, SCHEDULER

class EchoActor:
    def __init__(self) -> None:
        self.inbox = []

    def handle(self, msg):
        print("Echo:", msg)
        return Task.none()

def main():
    actor = EchoActor()

    def producer():
        yield Task.send(actor, "hello")
        yield Task.send(actor, "from")
        yield Task.send(actor, "py-actress")
        yield Task.send(actor, "done")

    Task.fork(Task.loop(actor))
    Task.fork(Task(producer()))
    SCHEDULER.run_forever()

if __name__ == "__main__":
    main()
