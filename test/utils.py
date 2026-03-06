from typing import Callable, Generic, Optional, cast
from typing_extensions import TypedDict  # for backwards compatibility with python3.10
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


class InspectorResult(TypedDict, Generic[M, T]):
    ok: bool
    value: Optional[T]
    mail: list[M]  # Messages sent by task
    error: Optional[Exception]


def inspector(task: Task[M, T]) -> Task[Control, InspectorResult[M, T]]:
    mail: list[M] = []
    controller = iter(task)
    input = None
    try:
        while True:
            try:
                step = controller.send(input)
            except StopIteration as e:
                return InspectorResult(ok=True, value=e.value, mail=mail, error=None)
            else:
                instruction = step
                if is_instruction(instruction):
                    input = yield cast(Control, instruction)
                else:
                    print(f"Message yielded: {instruction}")
                    mail.append(cast(M, instruction))
    except Exception as e:
        return InspectorResult(ok=False, value=None, error=e, mail=mail)

def inspect(task: Task[M, T]) -> Fork[InspectorResult[M, T], Exception, Control]:
    return fork(inspector(task))
