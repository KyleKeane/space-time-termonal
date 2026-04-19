"""Tests for ExecutionWorker (F62): async serial execution of cells.

The worker is a background thread, so every test waits on a real
synchronisation primitive rather than sleeping. `wait_until_drained`
is the test-facing hook; in production code, subscribers react to
`QUEUE_DRAINED` instead.
"""

from __future__ import annotations

import threading
import time
import unittest
from typing import Optional

from asat.cell import Cell
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.execution import ExecutionResult
from asat.execution_worker import ExecutionWorker
from asat.kernel import ExecutionKernel
from asat.session import Session


class _BlockingRunner:
    """Runner whose `run()` waits on a per-call `Event` until released.

    Lets tests queue multiple cells, assert they are serialised, and
    unblock them one at a time.
    """

    def __init__(self) -> None:
        self._events: list[threading.Event] = []
        self._entered = threading.Semaphore(0)
        self._lock = threading.Lock()
        self.entries: list[str] = []

    def run(self, request, stdout_handler=None, stderr_handler=None):
        gate = threading.Event()
        with self._lock:
            self._events.append(gate)
            self.entries.append(request.command)
        self._entered.release()
        gate.wait(timeout=5.0)
        return ExecutionResult(stdout="", stderr="", exit_code=0, timed_out=False)

    def wait_for_entry(self, timeout: float = 2.0) -> bool:
        """Block until the worker has called `run()` once."""
        return self._entered.acquire(timeout=timeout)

    def release_next(self) -> None:
        """Unblock the oldest still-blocked run call."""
        with self._lock:
            for gate in self._events:
                if not gate.is_set():
                    gate.set()
                    return

    def release_all(self) -> None:
        with self._lock:
            for gate in self._events:
                gate.set()


class _Recorder:
    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        self._lock = threading.Lock()
        bus.subscribe("*", self._append)

    def _append(self, event: Event) -> None:
        with self._lock:
            self.events.append(event)

    def snapshot(self) -> list[Event]:
        with self._lock:
            return list(self.events)

    def types(self) -> list[EventType]:
        return [e.event_type for e in self.snapshot()]


class _WorkerTestBase(unittest.TestCase):
    """Shared fixture: runner + kernel + session + worker, all wired."""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.session = Session.new()
        self.runner = _BlockingRunner()
        self.kernel = ExecutionKernel(self.bus, runner=self.runner)
        self.worker = ExecutionWorker(self.bus, self.kernel, self.session)
        self.worker.start()
        self.recorder = _Recorder(self.bus)

    def tearDown(self) -> None:
        # Release any cells still waiting on the gate so the worker
        # thread can finish cleanly before we join it.
        self.runner.release_all()
        self.worker.close(timeout=5.0)

    def _new_cell(self, command: str = "noop") -> Cell:
        cell = Cell.new(command)
        self.session.add_cell(cell)
        return cell


class ExecutionWorkerBasicTests(_WorkerTestBase):
    """A single submission runs through the kernel and drains."""

    def test_enqueue_publishes_command_queued_with_depth(self) -> None:
        cell = self._new_cell()
        depth = self.worker.enqueue(cell.cell_id)
        self.assertEqual(depth, 1)
        # COMMAND_QUEUED fires synchronously on enqueue; we don't need
        # to wait for the thread to pick up the work.
        queued = [
            e for e in self.recorder.snapshot()
            if e.event_type == EventType.COMMAND_QUEUED
        ]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].payload["cell_id"], cell.cell_id)
        self.assertEqual(queued[0].payload["queue_depth"], 1)

    def test_single_submission_drives_kernel_and_drains(self) -> None:
        cell = self._new_cell("echo")
        self.worker.enqueue(cell.cell_id)
        self.assertTrue(self.runner.wait_for_entry())
        # The runner is blocked inside run(); the queue is not drained yet.
        self.assertEqual(self.worker.queue_depth(), 1)
        self.runner.release_next()
        self.assertTrue(self.worker.wait_until_drained(timeout=2.0))
        self.assertEqual(self.worker.queue_depth(), 0)
        drained = [
            e for e in self.recorder.snapshot()
            if e.event_type == EventType.QUEUE_DRAINED
        ]
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0].payload["last_cell_id"], cell.cell_id)


class ExecutionWorkerSerialisationTests(_WorkerTestBase):
    """The worker runs one cell at a time, preserving submission order."""

    def test_second_submission_waits_for_first_to_finish(self) -> None:
        first = self._new_cell("first")
        second = self._new_cell("second")
        self.worker.enqueue(first.cell_id)
        self.worker.enqueue(second.cell_id)
        self.assertEqual(self.worker.queue_depth(), 2)
        # First cell is in flight; second is still waiting.
        self.assertTrue(self.runner.wait_for_entry())
        self.assertEqual(self.runner.entries, ["first"])
        # Releasing first lets the worker pick up second.
        self.runner.release_next()
        self.assertTrue(self.runner.wait_for_entry())
        self.assertEqual(self.runner.entries, ["first", "second"])
        self.runner.release_next()
        self.assertTrue(self.worker.wait_until_drained(timeout=2.0))

    def test_queue_drained_fires_only_once_after_batch(self) -> None:
        cells = [self._new_cell(f"c{i}") for i in range(3)]
        for cell in cells:
            self.worker.enqueue(cell.cell_id)
        # Unblock all three in sequence as the worker enters each.
        for _ in cells:
            self.assertTrue(self.runner.wait_for_entry())
            self.runner.release_next()
        self.assertTrue(self.worker.wait_until_drained(timeout=2.0))
        drained = [
            e for e in self.recorder.snapshot()
            if e.event_type == EventType.QUEUE_DRAINED
        ]
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0].payload["last_cell_id"], cells[-1].cell_id)


class ExecutionWorkerIsolationTests(_WorkerTestBase):
    """One bad cell must not kill the worker thread."""

    def test_handler_exception_does_not_stop_the_worker(self) -> None:
        # A bus subscriber that throws — e.g. a buggy logger — must
        # not poison the worker's processing loop. If the worker
        # swallows the exception cleanly, the second cell still runs.
        def boom(_event: Event) -> None:
            raise RuntimeError("subscriber blew up")

        self.bus.subscribe(EventType.COMMAND_COMPLETED, boom)
        first = self._new_cell("first")
        second = self._new_cell("second")
        self.worker.enqueue(first.cell_id)
        self.runner.wait_for_entry()
        self.runner.release_next()
        # Give the first cell time to publish COMMAND_COMPLETED
        # and hit the subscriber.
        time.sleep(0.05)
        self.worker.enqueue(second.cell_id)
        self.assertTrue(self.runner.wait_for_entry())
        self.runner.release_next()
        self.assertTrue(self.worker.wait_until_drained(timeout=2.0))


class ExecutionWorkerShutdownTests(_WorkerTestBase):
    """`close()` is graceful, idempotent, and waits for in-flight work."""

    def test_close_is_idempotent(self) -> None:
        self.worker.close()
        # Second call is a no-op; must not raise.
        self.worker.close()

    def test_enqueue_after_close_raises(self) -> None:
        self.worker.close()
        cell = self._new_cell()
        with self.assertRaises(RuntimeError):
            self.worker.enqueue(cell.cell_id)


if __name__ == "__main__":
    unittest.main()
