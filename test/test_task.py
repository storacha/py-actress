from actress.task import *


def test_stack():
    """Test Stack class."""
    print("\n" + "="*70)
    print("TEST 1: Stack Class")
    print("="*70)

    def task1():
        yield

    def task2():
        yield

    t1 = task1()
    t2 = task2()

    stack = Stack()
    print(f"Empty stack size: {Stack.size(stack)}")
    assert Stack.size(stack) == 0

    stack.active.append(t1)
    print(f"After adding to active: {Stack.size(stack)}")
    assert Stack.size(stack) == 1

    stack.idle.add(t2)
    print(f"After adding to idle: {Stack.size(stack)}")
    assert Stack.size(stack) == 2

    print("✓ Stack tests passed!")


def test_primitives():
    """Test current() and suspend()."""
    print("\n" + "="*70)
    print("TEST 2: Primitives")
    print("="*70)

    def task():
        ref = yield from current()
        print(f"Got reference: {ref}")
        yield from suspend()
        print("This should not print (task is suspended)")

    stack = Stack()
    t = task()
    enqueue(t, stack)

    # Process the stack
    for _ in step(stack):
        pass

    print(f"Active tasks: {len(stack.active)}")
    print(f"Idle tasks: {len(stack.idle)}")

    # Task should be in idle (suspended)
    assert len(stack.idle) == 1
    assert len(stack.active) == 0

    print("✓ Primitives tests passed!")


def test_multiple_tasks():
    """Test multiple tasks cooperating."""
    print("\n" + "="*70)
    print("TEST 3: Multiple Tasks")
    print("="*70)

    events = []

    def task1():
        events.append("task1: start")
        yield
        events.append("task1: middle")
        yield
        events.append("task1: end")

    def task2():
        events.append("task2: start")
        yield
        events.append("task2: end")

    stack = Stack()
    enqueue(task1(), stack)
    enqueue(task2(), stack)

    # Process all
    for _ in step(stack):
        pass

    print("Execution order:")
    for event in events:
        print(f"  {event}")

    # Tasks should interleave
    assert events[0] == "task1: start"
    assert events[1] == "task2: start"
    assert events[2] == "task1: middle"
    assert events[3] == "task2: end"

    print("✓ Multiple tasks test passed!")


def test_suspend_resume():
    """Test suspend and resume."""
    print("\n" + "="*70)
    print("TEST 4: Suspend and Resume")
    print("="*70)

    events = []
    suspended_tasks = []

    def task1():
        events.append("task1: before suspend")
        ref = yield from current()
        suspended_tasks.append(ref)
        yield from suspend()
        events.append("task1: after resume")

    def task2():
        events.append("task2: running")
        yield
        # Resume task1
        if suspended_tasks:
            events.append("task2: resuming task1")
            resume(suspended_tasks[0], stack)

    stack = Stack()
    enqueue(task1(), stack)
    enqueue(task2(), stack)

    # Process all
    for _ in step(stack):
        pass

    print("Execution order:")
    for event in events:
        print(f"  {event}")

    assert "task1: after resume" in events
    assert events.index("task2: resuming task1") < events.index("task1: after resume")

    print("Suspend/resume test passed!")


def test_messages():
    """Test that step() yields messages."""
    print("\n" + "="*70)
    print("TEST 5: Messages")
    print("="*70)

    def producer():
        yield "message-1"
        yield "message-2"
        yield "message-3"

    stack = Stack()
    enqueue(producer(), stack)

    messages = []
    for msg in step(stack):
        messages.append(msg)

    print(f"Collected messages: {messages}")

    assert messages == ["message-1", "message-2", "message-3"]

    print("Messages test passed!")


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    print("\n" + "="*70)
    print("MILESTONE 3: COOPERATIVE SCHEDULER")
    print("="*70)
    print("\nImplement the TODO sections, then run this file to test!")
    print("\nHints:")
    print("- Start with TODO 1 (Stack class)")
    print("- Then TODO 2 (primitives)")
    print("- Then TODO 3 (enqueue)")
    print("- Then TODO 4 (step) - this is the hardest!")
    print("- Finally TODO 5 (main)")
    print("\nRun tests to verify your implementation:")

    try:
        test_stack()
        test_primitives()
        test_multiple_tasks()
        test_suspend_resume()
        test_messages()

        print("\n" + "="*70)
        print("ALL TESTS PASSED! Great work on Milestone 3!")
        print("="*70)
    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
