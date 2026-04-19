"""Unit tests for PromptContext (F19)."""

from __future__ import annotations

import unittest

from asat.event_bus import EventBus, publish_event
from asat.events import Event, EventType
from asat.prompt_context import PromptContext


class _Recorder:
    """Collect every event so tests can filter by type."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self.events.append)

    def of(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type == event_type]


def _completed(bus: EventBus, *, cell_id: str = "c1", exit_code: int = 0) -> None:
    publish_event(
        bus,
        EventType.COMMAND_COMPLETED,
        {"cell_id": cell_id, "exit_code": exit_code, "timed_out": False},
        source="test",
    )


def _failed(
    bus: EventBus,
    *,
    cell_id: str = "c1",
    exit_code: int = 1,
    timed_out: bool = False,
) -> None:
    publish_event(
        bus,
        EventType.COMMAND_FAILED,
        {"cell_id": cell_id, "exit_code": exit_code, "timed_out": timed_out},
        source="test",
    )


def _focus_input(bus: EventBus, *, cell_id: str = "c1") -> None:
    publish_event(
        bus,
        EventType.FOCUS_CHANGED,
        {
            "old_mode": "notebook",
            "new_mode": "input",
            "old_cell_id": None,
            "new_cell_id": cell_id,
            "input_buffer": "",
            "transition": "mode",
            "command": "",
        },
        source="test",
    )


def _focus_notebook(bus: EventBus) -> None:
    publish_event(
        bus,
        EventType.FOCUS_CHANGED,
        {
            "old_mode": "input",
            "new_mode": "notebook",
            "old_cell_id": "c1",
            "new_cell_id": "c1",
            "input_buffer": "",
            "transition": "mode",
            "command": "",
        },
        source="test",
    )


class PromptContextTests(unittest.TestCase):

    def test_no_refresh_before_any_command_completes(self) -> None:
        """Entering INPUT mode before any command has finished must
        NOT publish PROMPT_REFRESH — there is nothing trailing to
        report and we don't want to spam `[prompt exit=None]` noise."""
        bus = EventBus()
        recorder = _Recorder(bus)
        PromptContext(bus, cwd_provider=lambda: "/work")
        _focus_input(bus)
        self.assertEqual(recorder.of(EventType.PROMPT_REFRESH), [])

    def test_refresh_fires_on_input_transition_after_success(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        PromptContext(bus, cwd_provider=lambda: "/work")
        _completed(bus, cell_id="c1", exit_code=0)
        _focus_input(bus, cell_id="c2")
        refresh = recorder.of(EventType.PROMPT_REFRESH)
        self.assertEqual(len(refresh), 1)
        self.assertEqual(refresh[0].payload["last_exit_code"], 0)
        self.assertEqual(refresh[0].payload["last_cell_id"], "c1")
        self.assertEqual(refresh[0].payload["cwd"], "/work")
        self.assertFalse(refresh[0].payload["last_timed_out"])

    def test_refresh_fires_after_failure_with_nonzero_exit(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        PromptContext(bus, cwd_provider=lambda: "/sandbox")
        _failed(bus, cell_id="c1", exit_code=127)
        _focus_input(bus)
        refresh = recorder.of(EventType.PROMPT_REFRESH)
        self.assertEqual(len(refresh), 1)
        self.assertEqual(refresh[0].payload["last_exit_code"], 127)

    def test_timed_out_flag_is_forwarded(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        PromptContext(bus, cwd_provider=lambda: "/")
        _failed(bus, cell_id="c1", exit_code=-9, timed_out=True)
        _focus_input(bus)
        refresh = recorder.of(EventType.PROMPT_REFRESH)
        self.assertTrue(refresh[0].payload["last_timed_out"])

    def test_only_fires_on_input_transitions(self) -> None:
        """Transitions into NOTEBOOK or OUTPUT must not emit a refresh."""
        bus = EventBus()
        recorder = _Recorder(bus)
        PromptContext(bus, cwd_provider=lambda: "/")
        _completed(bus, exit_code=0)
        _focus_notebook(bus)
        self.assertEqual(recorder.of(EventType.PROMPT_REFRESH), [])

    def test_later_run_overwrites_earlier_exit_code(self) -> None:
        bus = EventBus()
        recorder = _Recorder(bus)
        PromptContext(bus, cwd_provider=lambda: "/")
        _failed(bus, exit_code=2)
        _completed(bus, exit_code=0)
        _focus_input(bus)
        refresh = recorder.of(EventType.PROMPT_REFRESH)
        self.assertEqual(refresh[0].payload["last_exit_code"], 0)

    def test_properties_track_latest_completion(self) -> None:
        bus = EventBus()
        context = PromptContext(bus, cwd_provider=lambda: "/")
        self.assertIsNone(context.last_exit_code)
        _failed(bus, cell_id="cA", exit_code=42)
        self.assertEqual(context.last_exit_code, 42)
        self.assertEqual(context.last_cell_id, "cA")
        _completed(bus, cell_id="cB", exit_code=0)
        self.assertEqual(context.last_exit_code, 0)
        self.assertEqual(context.last_cell_id, "cB")

    def test_refresh_is_idempotent_per_focus_event(self) -> None:
        """Two INPUT transitions back-to-back produce two refreshes (one each)."""
        bus = EventBus()
        recorder = _Recorder(bus)
        PromptContext(bus, cwd_provider=lambda: "/")
        _completed(bus, exit_code=0)
        _focus_input(bus, cell_id="c2")
        _focus_input(bus, cell_id="c3")
        self.assertEqual(len(recorder.of(EventType.PROMPT_REFRESH)), 2)


if __name__ == "__main__":
    unittest.main()
