import time
from collections import deque
from typing import Deque, TYPE_CHECKING
if TYPE_CHECKING:
    from .task_methods import Task

class Scheduler:
    def __init__(self) -> None:
        self.queue: Deque["Task"] = deque()

    def enqueue(self, task: "Task") -> None:
        self.queue.append(task)

    def run(self, timeout: float = 5.0) -> None:
        """Run the scheduler until idle or timeout expires."""
        idle_start = None
        while True:
            if self.queue:
                task = self.queue.popleft()
                task._step()
                # RE-ENQUEUE INCOMPLETE TASKS
                if not task.done:
                    self.queue.append(task)
                idle_start = None
            else:
                if idle_start is None:
                    idle_start = time.time()
                elif time.time() - idle_start > timeout:
                    break
                time.sleep(0.01)

    def run_forever(self) -> None:
        while True:
            if self.queue:
                task = self.queue.popleft()
                task._step()
                # RE-ENQUEUE INCOMPLETE TASKS
                if not task.done:
                    self.queue.append(task)
            else:
                time.sleep(0.01)

SCHEDULER = Scheduler()