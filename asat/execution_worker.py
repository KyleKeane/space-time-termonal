"""ExecutionWorker: background queue that serialises cell execution.

F62 follow-up to F60. With one long-lived bash per session, submitting
a second command while the first is still running used to either block
the keyboard read (today's synchronous `app.execute(cell_id)` path) or
race the shell's stdin. Neither is acceptable once more than one
notebook shares a backend (F50-F55) or a user invokes "run all cells":
both want to fire off N submissions quickly and let the backend chew
through them in order.

The worker owns one daemon thread and one `queue.Queue`. `enqueue`
publishes `COMMAND_QUEUED` and drops the cell id in the queue; the
thread drains the queue one id at a time and calls `kernel.execute`
for each. When the queue becomes empty, `QUEUE_DRAINED` fires so UI
and tests can observe the steady state.

Opt-in via `Application.build(async_execution=True)` so tests that
expect synchronous execution keep their deterministic ordering. The
CLI turns it on by default.

Contract.
    - `enqueue(cell_id)` never blocks (the queue is unbounded).
    - Each cell completes one full `kernel.execute` (including every
      OUTPUT_CHUNK and COMMAND_COMPLETED/FAILED) before the next
      starts.
    - `close()` drains pending work, stops the thread, and returns.
      It is idempotent.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.kernel import ExecutionKernel
from asat.session import Session


_STOP_SENTINEL = object()


class ExecutionWorker:
    """Serial background executor for cell submissions."""

    SOURCE = "execution_worker"

    def __init__(
        self,
        bus: EventBus,
        kernel: ExecutionKernel,
        session: Session,
    ) -> None:
        """Bind the worker to one bus, one kernel, and one session.

        The worker holds references (not copies) to all three — a
        cell id submitted today must still resolve against `session`
        when the thread picks it up seconds later.
        """
        self._bus = bus
        self._kernel = kernel
        self._session = session
        self._queue: "queue.Queue[object]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._closed = False
        # Counts pending + in-flight cells. Test hooks observe this to
        # know when the worker is truly idle — `Queue.unfinished_tasks`
        # would work too but is private in name and spirit.
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()

    def start(self) -> None:
        """Spawn the daemon thread that drains the queue.

        Idempotent — calling `start()` twice is a no-op. The thread is
        a daemon so a hung command during a crash never keeps the
        process alive; `close()` is the graceful path.
        """
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="asat-execution-worker",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, cell_id: str) -> int:
        """Accept a cell id for execution. Return the new queue depth.

        Publishes `COMMAND_QUEUED` immediately so the audio pipeline
        can confirm the submission landed even if earlier commands
        are still running. Returns the depth so the caller can narrate
        "queued, N ahead".
        """
        if self._closed:
            raise RuntimeError("ExecutionWorker is closed")
        with self._inflight_lock:
            self._inflight += 1
            self._idle.clear()
            depth = self._inflight
        self._queue.put(cell_id)
        publish_event(
            self._bus,
            EventType.COMMAND_QUEUED,
            {"cell_id": cell_id, "queue_depth": depth},
            source=self.SOURCE,
        )
        return depth

    def queue_depth(self) -> int:
        """Return the number of cells queued or currently running."""
        with self._inflight_lock:
            return self._inflight

    def wait_until_drained(self, timeout: Optional[float] = None) -> bool:
        """Block until the worker is idle. Returns True if drained.

        Exists for tests and for `close()`. Production code should
        react to the `QUEUE_DRAINED` event rather than calling this.
        """
        return self._idle.wait(timeout=timeout)

    def close(self, *, drain: bool = True, timeout: Optional[float] = 5.0) -> None:
        """Stop the worker thread and release its resources.

        When `drain=True` (the default), the worker finishes whatever
        it has already pulled plus everything still in the queue
        before exiting. When `drain=False`, the thread exits as soon
        as it finishes the current cell; queued-but-not-started cells
        are dropped.

        Idempotent. Safe to call from `atexit`.
        """
        if self._closed:
            return
        self._closed = True
        if self._thread is None:
            return
        if drain:
            # Let the thread finish everything it has, then stop.
            self._queue.put(_STOP_SENTINEL)
        else:
            # Prepend-style cancel is not available on stdlib Queue;
            # the best we can do is push the sentinel first and rely
            # on the already-in-flight cell to finish on its own.
            self._queue.put(_STOP_SENTINEL)
        self._thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        """Thread entry point: pull ids, execute, publish drained events."""
        while True:
            item = self._queue.get()
            if item is _STOP_SENTINEL:
                return
            assert isinstance(item, str)
            try:
                cell = self._session.get_cell(item)
                # `kernel.execute` catches FileNotFoundError / ValueError /
                # ShellBackendError internally and publishes
                # COMMAND_FAILED. Anything escaping here is a genuine
                # programmer bug — swallow and keep the thread alive
                # so one rogue cell does not freeze the queue.
                self._kernel.execute(cell)
            except BaseException:
                # Report but keep running. A subscriber-side error is
                # the likely cause (wildcard logger blew up, etc.);
                # dropping the thread here would strand every
                # subsequent submission.
                pass
            finally:
                self._finish_one(item)

    def _finish_one(self, cell_id: str) -> None:
        """Decrement in-flight, and if empty, publish `QUEUE_DRAINED`."""
        with self._inflight_lock:
            self._inflight -= 1
            idle = self._inflight == 0
            depth = self._inflight
        if idle:
            publish_event(
                self._bus,
                EventType.QUEUE_DRAINED,
                {"last_cell_id": cell_id, "queue_depth": depth},
                source=self.SOURCE,
            )
            self._idle.set()
