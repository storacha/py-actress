from typing import Callable, Generic, Optional, TypedDict, cast
from actress.task import T, M, Control, Fork, Task, fork, is_instruction


class Logger(TypedDict):
    output: list[str]
    log: Callable[[str], list[str]]


def create_log() -> Logger:
    output: list[str] = []
    def log(message):
        output.append(message)
        return output

    return Logger(output=output, log=log)


class InspectResult(TypedDict, Generic[M, T]):
    ok: bool
    value: Optional[T]
    mail: list[M]  # Messages sent by task
    error: Optional[Exception]


def inspector(task: Task[M, T]) -> Task[Control, InspectResult[M, T]]:
    mail: list[M] = []
    controller = iter(task)
    input = None
    try:
        while True:
            try:
                step = controller.send(input)
            except StopIteration as e:
                return InspectResult(ok=True, value=e.value, mail=mail, error=None)
            else:
                instruction = step
                if is_instruction(instruction):
                    input = yield cast(Control, instruction)
                else:
                    print(f"Message yielded: {instruction}")
                    mail.append(cast(M, instruction))
    except Exception as e:
        return InspectResult(ok=False, value=None, error=e, mail=mail)

def inspect(task: Task[M, T]) -> Fork[InspectResult[M, T], Exception, Control]:
    return fork(inspector(task))
