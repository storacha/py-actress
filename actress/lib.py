from typing import Any, TypeVar, Generator, Optional, Union, Iterable, cast
from .task import Task, Controller, Instruction, CURRENT, SUSPEND, Main, Stack, TaskGroup, Result, Fork, Status

T = TypeVar("T")
X = TypeVar("X")
M = TypeVar("M")

def send(message: T) -> Task[None, Any, T]:
    """
    Task that sends given message.
    """
    yield message
    return None

def listen(source: dict[str, Task[Any, Any, Any]]) -> Task[None, Any, Any]:
    """
    Takes several effects and merges them into a single effect of tagged
    variants so that their source could be identified via `type` field.
    """
    forks = []
    for name, effect_obj in source.items():
        if effect_obj is not NONE:
            f = yield from fork(tag(effect_obj, name))
            forks.append(f)
    yield from group(forks)
    return None

def effects(tasks_list: list[Task[Any, Any, Any]]) -> Task[None, Any, Any]:
    """
    Takes several tasks and creates an effect of them all.
    """
    return batch([effect(t) for t in tasks_list]) if tasks_list else NONE

def effect(task_obj: Task[T, Any, Any]) -> Task[None, Any, T]:
    """
    Turns a task into an effect of its result.
    """
    message = yield from task_obj
    yield from send(message)
    return None

def batch(effects_list: list[Task[None, Any, Any]]) -> Task[None, Any, Any]:
    """
    Takes several effects and combines them into one.
    """
    forks = []
    for ef in effects_list:
        f = yield from fork(ef)
        forks.append(f)
    yield from group(forks)
    return None

def tag(effect_obj: Task[Any, Any, Any], tag_name: str) -> Task[Any, Any, Any]:
    """
    Tags an effect by boxing each event with an object that has `type` field.
    """
    if effect_obj is NONE:
        return NONE
    if isinstance(effect_obj, Tagger):
        return Tagger([*effect_obj.tags, tag_name], effect_obj.source)
    return Tagger([tag_name], effect_obj)

class Tagger(Task[Any, Any, Any], Controller[Any, Any, Any]):
    def __init__(self, tags: list[str], source: Task[Any, Any, Any]):
        self.tags = tags
        self.source = source
        self.controller: Optional[Controller[Any, Any, Any]] = None

    def __iter__(self) -> Controller[Any, Any, Any]:
        if self.controller is None:
            self.controller = iter(self.source)
        return self

    def box(self, state: Instruction[Any]) -> Instruction[Any]:
        if state in (SUSPEND, CURRENT):
            return state
        value = state
        for t in self.tags:
            value = {"type": t, t: value}
        return value

    def next(self, value: Any) -> Instruction[Any]:
        assert self.controller is not None
        return self.box(self.controller.next(value))

    def throw(self, typ: Any, val: Optional[BaseException] = None, tb: Any = None) -> Instruction[Any]:
        assert self.controller is not None
        return self.box(self.controller.throw(typ, val, tb))

    def return_(self, value: Any) -> Instruction[Any]:
        assert self.controller is not None
        return self.box(self.controller.return_(value))

def none() -> Task[None, Any, Any]:
    """
    Returns empty `Effect`, that is produces no messages.
    """
    return NONE

def all(tasks: Iterable[Task[T, Any, Any]]) -> Task[list[T], Any, Any]:
    """
    Takes iterable of tasks and runs them concurrently.
    """
    self_ctrl = yield CURRENT
    
    results: list[Optional[T]] = []
    forks: list[Optional[Fork[Any, Any, Any]]] = []
    count = 0
    
    def succeed(idx: int):
        def _succeed(value: T):
            nonlocal count
            forks[idx] = None
            results[idx] = value
            count -= 1
            if count == 0:
                enqueue(self_ctrl)
        return _succeed

    def fail(error: Any):
        for f in forks:
            if f:
                enqueue(f.abort(error))
        enqueue(self_ctrl.throw(type(error), error, None))

    for task_obj in tasks:
        idx = len(forks)
        f = fork(then(task_obj, succeed(idx), fail))
        forks.append(f)
        yield from f
        count += 1
    
    results = [None] * count
    if count > 0:
        yield SUSPEND
    
    return cast(list[T], results)

def then(task_obj: Task[T, Any, Any], resolve: Any, reject: Any) -> Task[Any, Any, Any]:
    """
    Kind of like promise.then.
    """
    try:
        result = yield from task_obj
        return resolve(result)
    except Exception as e:
        return reject(e)

def isMessage(value: Any) -> bool:
    return value not in (SUSPEND, CURRENT)

def isInstruction(value: Any) -> bool:
    return not isMessage(value)

class Group:
    @staticmethod
    def of(member: Any) -> Union[Main, TaskGroup]:
        return getattr(member, "group", MAIN)

    @staticmethod
    def enqueue(member: Any, group_obj: Union[Main, TaskGroup]):
        member.group = group_obj
        group_obj.stack.active.append(member)

def main(task_obj: Task[None, Any, Any]):
    """
    Starts a main task.
    """
    enqueue(iter(task_obj))

def enqueue(task_ctrl: Controller[Any, Any, Any]):
    group_obj = Group.of(task_ctrl)
    group_obj.stack.active.append(task_ctrl)
    if task_ctrl in group_obj.stack.idle:
        group_obj.stack.idle.remove(task_ctrl)

    parent = getattr(group_obj, "parent", None)
    while parent:
        if group_obj.driver in parent.stack.idle:
            parent.stack.idle.remove(group_obj.driver)
            parent.stack.active.append(group_obj.driver)
        else:
            break
        group_obj = parent
        parent = getattr(group_obj, "parent", None)

    if MAIN.status == "idle":
        MAIN.status = "active"
        while True:
            try:
                for _ in step(MAIN):
                    pass
                MAIN.status = "idle"
                break
            except Exception:
                if MAIN.stack.active:
                    MAIN.stack.active.pop(0)

def resume(task_ctrl: Controller[Any, Any, Any]):
    enqueue(task_ctrl)

def step(group_obj: Union[Main, TaskGroup]) -> Generator[Any, Any, None]:
    active = group_obj.stack.active
    while active:
        task_ctrl = active[0]
        if task_ctrl in group_obj.stack.idle:
            group_obj.stack.idle.remove(task_ctrl)
        
        instruction = CURRENT
        try:
            while task_ctrl == active[0]:
                if instruction == SUSPEND:
                    group_obj.stack.idle.add(task_ctrl)
                    break 
                elif instruction == CURRENT:
                    instruction = task_ctrl.next(task_ctrl)
                else:
                    instruction = task_ctrl.next((yield instruction))
            
            if task_ctrl == active[0]:
                active.pop(0)
        except StopIteration:
            if task_ctrl == active[0]:
                active.pop(0)
        except Exception as e:
            if task_ctrl == active[0]:
                active.pop(0)
            raise e

def spawn(task_obj: Task[None, Any, Any]):
    main(task_obj)

def fork(task_obj: Task[T, X, M], options: Any = None) -> Fork[T, X, M]:
    return Fork(task_obj, options)

def exit(handle: Controller[T, X, M], value: T) -> Task[None, Any, Any]:
    return conclude(handle, Result(ok=True, value=value, error=None))

def terminate(handle: Controller[None, X, M]) -> Task[None, Any, Any]:
    return conclude(handle, Result(ok=True, value=None, error=None))

def abort(handle: Controller[T, X, M], error: X) -> Task[None, Any, Any]:
    return conclude(handle, Result(ok=False, value=None, error=error))

def conclude(handle: Controller[Any, Any, Any], result: Result) -> Task[None, Any, Any]:
    try:
        if result.ok:
            state = handle.return_(result.value)
        else:
            state = handle.throw(type(result.error), result.error, None)
        
        if state == SUSPEND:
            Group.of(handle).stack.idle.add(handle)
        else:
            enqueue(handle)
    except Exception:
        pass
    yield from []

def group(forks_list: list[Fork[Any, Any, Any]]) -> Task[None, Any, Any]:
    if not forks_list:
        return
    
    self_ctrl = yield CURRENT
    group_obj = TaskGroup(id=next(ID_GEN), parent=Group.of(self_ctrl), driver=self_ctrl, stack=Stack(), result=None)
    
    failure = None
    for f in forks_list:
        if f.result:
            if not f.result.ok and not failure:
                failure = f.result
            continue
        move(f, group_obj)
    
    try:
        if failure:
            raise failure.error
        
        while True:
            yield from step(group_obj)
            if len(group_obj.stack.active) + len(group_obj.stack.idle) > 0:
                yield SUSPEND
            else:
                break
    except Exception as e:
        for t in group_obj.stack.active:
            yield from abort(t, e)
        for t in group_obj.stack.idle:
            yield from abort(t, e)
            enqueue(t)
        raise e

def move(f: Fork[Any, Any, Any], target_group: TaskGroup):
    source_group = Group.of(f)
    if source_group != target_group:
        if f in source_group.stack.idle:
            source_group.stack.idle.remove(f)
            target_group.stack.idle.add(f)
        elif f in source_group.stack.active:
            source_group.stack.active.remove(f)
            target_group.stack.active.append(f)
        f.group = target_group

def join(f: Fork[T, X, M]) -> Task[T, X, M]:
    if f.status == "idle":
        yield from f
    
    if not f.result:
        yield from group([f])
    
    assert f.result is not None
    if f.result.ok:
        return f.result.value
    else:
        raise f.result.error

def loop(init: Task[None, Any, M], next_fn: Any) -> Task[None, Any, Any]:
    self_ctrl = yield CURRENT
    group_obj = Group.of(self_ctrl)
    Group.enqueue(iter(init), group_obj)

    while True:
        for message in step(group_obj):
            Group.enqueue(iter(next_fn(message)), group_obj)

        if len(group_obj.stack.active) + len(group_obj.stack.idle) > 0:
            yield SUSPEND
        else:
            break

ID_COUNTER = 0
def id_generator():
    global ID_COUNTER
    while True:
        ID_COUNTER += 1
        yield ID_COUNTER

ID_GEN = id_generator()
MAIN = Main()
NONE = (lambda: (yield from []))()
