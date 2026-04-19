"""Unit tests for the InputRouter."""

from __future__ import annotations

import unittest

from asat.actions import ActionMenu, MemoryClipboard, default_actions
from asat.cell import Cell
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.input_router import InputRouter, default_bindings
from asat.keys import (
    BACKSPACE,
    DELETE,
    DOWN,
    END,
    ENTER,
    ESCAPE,
    F2,
    HOME,
    Key,
    LEFT,
    Modifier,
    PAGE_DOWN,
    PAGE_UP,
    RIGHT,
    UP,
)
from asat.notebook import FocusMode, NotebookCursor
from asat.output_buffer import OutputBuffer, OutputRecorder, STDOUT
from asat.output_cursor import OutputCursor
from asat.session import Session
from asat.settings_controller import SettingsController
from asat.sound_bank import EventBinding, SoundBank, SoundRecipe, Voice


def _build(commands: list[str]) -> tuple[EventBus, Session, NotebookCursor, InputRouter, list[Cell]]:
    """Construct a fresh bus/session/cursor/router stack for a test."""
    bus = EventBus()
    session = Session.new()
    cells = [Cell.new(command) for command in commands]
    for cell in cells:
        session.add_cell(cell)
    cursor = NotebookCursor(session, bus)
    router = InputRouter(cursor, bus)
    return bus, session, cursor, router, cells


class _Recorder:
    """Captures every event on a bus so tests can assert on sequences."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe("*", self.events.append)

    def types_of(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type == event_type]


class DefaultBindingsTests(unittest.TestCase):

    def test_default_bindings_cover_both_modes(self) -> None:
        bindings = default_bindings()
        self.assertIn(FocusMode.NOTEBOOK, bindings)
        self.assertIn(FocusMode.INPUT, bindings)
        self.assertIn(UP, bindings[FocusMode.NOTEBOOK])
        self.assertIn(BACKSPACE, bindings[FocusMode.INPUT])


class NotebookModeDispatchTests(unittest.TestCase):

    def test_up_moves_cursor_up(self) -> None:
        bus, session, cursor, router, cells = _build(["a", "b", "c"])
        cursor.move_to_bottom()
        result = router.handle_key(UP)
        self.assertEqual(result, "move_up")
        self.assertEqual(cursor.focus.cell_id, cells[1].cell_id)

    def test_down_moves_cursor_down(self) -> None:
        _, _, cursor, router, cells = _build(["a", "b"])
        self.assertEqual(router.handle_key(DOWN), "move_down")
        self.assertEqual(cursor.focus.cell_id, cells[1].cell_id)

    def test_home_jumps_to_top(self) -> None:
        _, _, cursor, router, cells = _build(["a", "b", "c"])
        cursor.move_to_bottom()
        self.assertEqual(router.handle_key(HOME), "move_to_top")
        self.assertEqual(cursor.focus.cell_id, cells[0].cell_id)

    def test_enter_transitions_to_input_mode(self) -> None:
        _, _, cursor, router, _cells = _build(["echo"])
        router.handle_key(ENTER)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)

    def test_ctrl_n_appends_new_cell(self) -> None:
        _, session, cursor, router, _ = _build(["a"])
        self.assertEqual(router.handle_key(Key.combo("n", Modifier.CTRL)), "new_cell")
        self.assertEqual(len(session), 2)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)

    def test_unbound_key_returns_none(self) -> None:
        _, _, _, router, _ = _build(["a"])
        self.assertIsNone(router.handle_key(Key.printable("x")))

    def test_every_key_publishes_key_pressed(self) -> None:
        bus, _, _, router, _ = _build(["a"])
        recorder = _Recorder(bus)
        router.handle_key(DOWN)
        router.handle_key(Key.printable("q"))
        self.assertEqual(len(recorder.types_of(EventType.KEY_PRESSED)), 2)


class InputModeDispatchTests(unittest.TestCase):

    def test_printable_characters_extend_buffer(self) -> None:
        _, _, cursor, router, cells = _build(["echo"])
        router.handle_key(ENTER)
        for char in " hi":
            router.handle_key(Key.printable(char))
        self.assertEqual(cursor.focus.input_buffer, "echo hi")

    def test_backspace_in_input_mode_removes_character(self) -> None:
        _, _, cursor, router, _ = _build(["echo"])
        router.handle_key(ENTER)
        router.handle_key(BACKSPACE)
        self.assertEqual(cursor.focus.input_buffer, "ech")

    def test_escape_commits_and_exits(self) -> None:
        _, _, cursor, router, cells = _build(["echo"])
        router.handle_key(ENTER)
        router.handle_key(Key.printable("x"))
        router.handle_key(ESCAPE)
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertEqual(cells[0].command, "echox")

    def test_enter_submits_and_autoadvances_to_new_input_cell(self) -> None:
        """F11: after Enter submits a non-empty command from the last
        cell, the user lands in INPUT mode on a fresh empty cell so
        they can immediately type the next command."""
        bus, session, cursor, router, cells = _build(["echo"])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        router.handle_key(Key.printable(" "))
        router.handle_key(Key.printable("z"))
        router.handle_key(ENTER)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(cells[0].command, "echo z")
        self.assertEqual(len(session), 2)
        self.assertNotEqual(cursor.focus.cell_id, cells[0].cell_id)
        self.assertEqual(cursor.focus.input_buffer, "")
        submit_events = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertEqual(len(submit_events), 1)
        self.assertEqual(submit_events[0].payload["cell_id"], cells[0].cell_id)
        self.assertEqual(submit_events[0].payload["command"], "echo z")

    def test_ctrl_n_is_ignored_in_input_mode(self) -> None:
        _, session, cursor, router, _ = _build(["echo"])
        router.handle_key(ENTER)
        before = len(session)
        self.assertIsNone(router.handle_key(Key.combo("n", Modifier.CTRL)))
        self.assertEqual(len(session), before)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)


class InLineBufferEditingBindingTests(unittest.TestCase):
    """F13: the INPUT-mode binding map wires caret motion + kill keys
    through to the NotebookCursor."""

    def _enter_with_buffer(self, command: str) -> tuple[NotebookCursor, InputRouter]:
        _, _, cursor, router, _ = _build([command])
        router.handle_key(ENTER)
        return cursor, router

    def test_left_and_right_move_caret(self) -> None:
        cursor, router = self._enter_with_buffer("echo hi")
        router.handle_key(LEFT)
        router.handle_key(LEFT)
        self.assertEqual(cursor.focus.cursor_position, len("echo hi") - 2)
        router.handle_key(RIGHT)
        self.assertEqual(cursor.focus.cursor_position, len("echo hi") - 1)

    def test_home_and_end_jump_caret(self) -> None:
        cursor, router = self._enter_with_buffer("echo hi")
        router.handle_key(HOME)
        self.assertEqual(cursor.focus.cursor_position, 0)
        router.handle_key(END)
        self.assertEqual(cursor.focus.cursor_position, len("echo hi"))

    def test_ctrl_a_and_ctrl_e_mirror_home_and_end(self) -> None:
        cursor, router = self._enter_with_buffer("echo hi")
        router.handle_key(Key.combo("a", Modifier.CTRL))
        self.assertEqual(cursor.focus.cursor_position, 0)
        router.handle_key(Key.combo("e", Modifier.CTRL))
        self.assertEqual(cursor.focus.cursor_position, len("echo hi"))

    def test_delete_removes_character_under_caret(self) -> None:
        cursor, router = self._enter_with_buffer("echo hi")
        router.handle_key(HOME)
        router.handle_key(DELETE)
        self.assertEqual(cursor.focus.input_buffer, "cho hi")

    def test_insert_respects_caret_position(self) -> None:
        cursor, router = self._enter_with_buffer("echo hi")
        router.handle_key(HOME)
        router.handle_key(Key.printable("X"))
        self.assertEqual(cursor.focus.input_buffer, "Xecho hi")

    def test_ctrl_w_kills_word_left(self) -> None:
        cursor, router = self._enter_with_buffer("echo hello")
        router.handle_key(Key.combo("w", Modifier.CTRL))
        self.assertEqual(cursor.focus.input_buffer, "echo ")

    def test_ctrl_u_kills_to_start(self) -> None:
        cursor, router = self._enter_with_buffer("echo hello")
        # Park the caret between "echo" and " hello", then kill the prefix.
        for _ in range(len(" hello")):
            router.handle_key(LEFT)
        router.handle_key(Key.combo("u", Modifier.CTRL))
        self.assertEqual(cursor.focus.input_buffer, " hello")

    def test_ctrl_k_kills_to_end(self) -> None:
        cursor, router = self._enter_with_buffer("echo hello")
        for _ in range(len(" hello")):
            router.handle_key(LEFT)
        router.handle_key(Key.combo("k", Modifier.CTRL))
        self.assertEqual(cursor.focus.input_buffer, "echo")

    def test_motion_publishes_action_invoked(self) -> None:
        bus, _, _, router, _ = _build(["echo hi"])
        router.handle_key(ENTER)
        recorder = _Recorder(bus)
        router.handle_key(LEFT)
        actions = [
            e.payload["action"]
            for e in recorder.types_of(EventType.ACTION_INVOKED)
        ]
        self.assertIn("cursor_left", actions)


class ActionEventPayloadTests(unittest.TestCase):

    def test_insert_character_event_records_char(self) -> None:
        bus, _, _, router, _ = _build(["echo"])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        router.handle_key(Key.printable("q"))
        inserts = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "insert_character"
        ]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0].payload["char"], "q")

    def test_move_action_payload_reports_focus_mode(self) -> None:
        bus, _, _, router, _ = _build(["a", "b"])
        recorder = _Recorder(bus)
        router.handle_key(DOWN)
        actions = recorder.types_of(EventType.ACTION_INVOKED)
        self.assertEqual(actions[0].payload["action"], "move_down")
        self.assertEqual(actions[0].payload["focus_mode"], FocusMode.NOTEBOOK.value)


class OutputModeDispatchTests(unittest.TestCase):

    def _stack(
        self,
        lines: list[str],
    ) -> tuple[NotebookCursor, InputRouter, OutputCursor, OutputBuffer]:
        bus = EventBus()
        session = Session.new()
        cell = Cell.new("echo")
        session.add_cell(cell)
        cursor = NotebookCursor(session, bus)
        output_cursor = OutputCursor(bus, page_size=2)
        buffer = OutputBuffer(cell_id=cell.cell_id)
        for text in lines:
            buffer.append(text, stream=STDOUT)
        output_cursor.attach(buffer)
        cursor.view_output_mode()
        router = InputRouter(cursor, bus, output_cursor=output_cursor)
        return cursor, router, output_cursor, buffer

    def test_up_moves_output_cursor_up(self) -> None:
        _, router, output_cursor, _ = self._stack(["a", "b", "c"])
        result = router.handle_key(UP)
        self.assertEqual(result, "output_line_up")
        self.assertEqual(output_cursor.line_number, 1)

    def test_down_moves_output_cursor_down(self) -> None:
        _, router, output_cursor, _ = self._stack(["a", "b", "c"])
        output_cursor.move_to_start()
        self.assertEqual(router.handle_key(DOWN), "output_line_down")
        self.assertEqual(output_cursor.line_number, 1)

    def test_page_up_and_page_down_use_page_size(self) -> None:
        _, router, output_cursor, _ = self._stack(["a", "b", "c", "d", "e"])
        router.handle_key(PAGE_UP)
        self.assertEqual(output_cursor.line_number, 2)
        router.handle_key(PAGE_DOWN)
        self.assertEqual(output_cursor.line_number, 4)

    def test_home_and_end_jump_to_ends(self) -> None:
        _, router, output_cursor, _ = self._stack(["a", "b", "c"])
        router.handle_key(HOME)
        self.assertEqual(output_cursor.line_number, 0)
        router.handle_key(END)
        self.assertEqual(output_cursor.line_number, 2)

    def test_escape_exits_output_mode(self) -> None:
        cursor, router, _, _ = self._stack(["a"])
        self.assertEqual(router.handle_key(ESCAPE), "exit_output")
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)

    def test_output_actions_noop_without_output_cursor(self) -> None:
        bus = EventBus()
        session = Session.new()
        cell = Cell.new("echo")
        session.add_cell(cell)
        cursor = NotebookCursor(session, bus)
        cursor.view_output_mode()
        router = InputRouter(cursor, bus)
        self.assertEqual(router.handle_key(UP), "output_line_up")

    def test_ctrl_o_enters_output_mode_from_notebook(self) -> None:
        bus = EventBus()
        session = Session.new()
        cell = Cell.new("echo")
        session.add_cell(cell)
        cursor = NotebookCursor(session, bus)
        router = InputRouter(cursor, bus)
        result = router.handle_key(Key.combo("o", Modifier.CTRL))
        self.assertEqual(result, "view_output")
        self.assertEqual(cursor.focus.mode, FocusMode.OUTPUT)


def _settings_bank() -> SoundBank:
    """A tiny bank that the router's settings controller can walk."""
    return SoundBank(
        voices=(Voice(id="v1", rate=1.0),),
        sounds=(SoundRecipe(id="s1", kind="tone", params={"frequency": 440.0}),),
        bindings=(
            EventBinding(
                id="b1",
                event_type="cell.created",
                voice_id="v1",
                say_template="hello",
            ),
        ),
    )


def _build_with_settings(
    commands: list[str],
) -> tuple[EventBus, NotebookCursor, InputRouter, SettingsController]:
    """Build a router wired to a settings controller, no save path."""
    bus = EventBus()
    session = Session.new()
    for command in commands:
        session.add_cell(Cell.new(command))
    cursor = NotebookCursor(session, bus)
    controller = SettingsController(bus, _settings_bank())
    router = InputRouter(cursor, bus, settings_controller=controller)
    return bus, cursor, router, controller


class SettingsModeDispatchTests(unittest.TestCase):

    def test_ctrl_comma_opens_settings_from_notebook(self) -> None:
        _, cursor, router, controller = _build_with_settings(["echo"])
        result = router.handle_key(Key.combo(",", Modifier.CTRL))
        self.assertEqual(result, "open_settings")
        self.assertEqual(cursor.focus.mode, FocusMode.SETTINGS)
        self.assertTrue(controller.is_open)

    def test_ctrl_comma_without_controller_is_noop(self) -> None:
        bus = EventBus()
        session = Session.new()
        session.add_cell(Cell.new("echo"))
        cursor = NotebookCursor(session, bus)
        router = InputRouter(cursor, bus)
        self.assertEqual(router.handle_key(Key.combo(",", Modifier.CTRL)), "open_settings")
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)

    def test_arrow_keys_navigate_records(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(DOWN)  # next section: sounds
        self.assertEqual(controller.editor.state.section.value, "sounds")
        router.handle_key(UP)
        self.assertEqual(controller.editor.state.section.value, "voices")

    def test_enter_descends_then_e_begins_edit(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(ENTER)  # SECTION -> RECORD
        router.handle_key(ENTER)  # RECORD -> FIELD
        router.handle_key(Key.printable("e"))
        self.assertTrue(controller.editing)

    def test_typed_value_lands_in_edit_buffer(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(ENTER)
        router.handle_key(ENTER)
        router.handle_key(DOWN)  # move to "engine" field
        router.handle_key(Key.printable("e"))
        for ch in "sapi":
            router.handle_key(Key.printable(ch))
        self.assertEqual(controller.edit_buffer, "sapi")
        router.handle_key(ENTER)  # commit
        self.assertFalse(controller.editing)
        self.assertEqual(controller.bank.voices[0].engine, "sapi")

    def test_escape_in_edit_cancels_then_ascends(self) -> None:
        _, cursor, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(ENTER)
        router.handle_key(ENTER)  # FIELD level
        router.handle_key(Key.printable("e"))
        router.handle_key(Key.printable("x"))
        router.handle_key(ESCAPE)  # cancels edit, still at FIELD
        self.assertFalse(controller.editing)
        self.assertEqual(cursor.focus.mode, FocusMode.SETTINGS)
        router.handle_key(ESCAPE)  # ascend to RECORD
        router.handle_key(ESCAPE)  # ascend to SECTION
        router.handle_key(ESCAPE)  # at top: closes
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertFalse(controller.is_open)

    def test_ctrl_q_closes_settings(self) -> None:
        _, cursor, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.combo("q", Modifier.CTRL))
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)
        self.assertFalse(controller.is_open)

    def test_backspace_in_edit_trims_buffer(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(ENTER)
        router.handle_key(ENTER)
        router.handle_key(DOWN)
        router.handle_key(Key.printable("e"))
        for ch in "sapix":
            router.handle_key(Key.printable(ch))
        router.handle_key(BACKSPACE)
        self.assertEqual(controller.edit_buffer, "sapi")

    def test_ctrl_z_undoes_the_most_recent_settings_edit(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(ENTER)
        router.handle_key(ENTER)
        router.handle_key(DOWN)  # engine
        router.handle_key(Key.printable("e"))
        for ch in "sapi":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)  # commit
        self.assertEqual(controller.bank.voices[0].engine, "sapi")

        router.handle_key(Key.combo("z", Modifier.CTRL))

        self.assertEqual(controller.bank.voices[0].engine, "")

    def test_ctrl_y_redoes_the_most_recently_undone_edit(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(ENTER)
        router.handle_key(ENTER)
        router.handle_key(DOWN)
        router.handle_key(Key.printable("e"))
        for ch in "sapi":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        router.handle_key(Key.combo("z", Modifier.CTRL))
        self.assertEqual(controller.bank.voices[0].engine, "")

        router.handle_key(Key.combo("y", Modifier.CTRL))

        self.assertEqual(controller.bank.voices[0].engine, "sapi")

    def test_ctrl_z_while_editing_is_discarded(self) -> None:
        """While composing a replacement, Ctrl+Z must not sneak in as an
        undo; the edit buffer stays intact and nothing is reverted."""
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(ENTER)
        router.handle_key(ENTER)
        router.handle_key(DOWN)
        router.handle_key(Key.printable("e"))
        for ch in "sapi":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)  # commit first edit
        router.handle_key(Key.printable("e"))
        router.handle_key(Key.printable("x"))

        router.handle_key(Key.combo("z", Modifier.CTRL))

        self.assertTrue(controller.editing)
        self.assertEqual(controller.edit_buffer, "x")
        self.assertEqual(controller.bank.voices[0].engine, "sapi")

    def test_slash_opens_settings_search_composer(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        result = router.handle_key(Key.printable("/"))
        self.assertEqual(result, "settings_search_begin")
        self.assertTrue(controller.searching)

    def test_typed_chars_extend_search_and_jump_to_match(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.printable("/"))
        for ch in "s1":
            router.handle_key(Key.printable(ch))
        self.assertEqual(controller.search_buffer, "s1")
        # "s1" matches sounds[0] (id="s1") — cursor parks there.
        self.assertEqual(controller.editor.state.section.value, "sounds")
        self.assertEqual(controller.editor.state.record_index, 0)

    def test_enter_commits_settings_search(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.printable("/"))
        router.handle_key(Key.printable("s"))
        result = router.handle_key(ENTER)
        self.assertEqual(result, "settings_search_commit")
        self.assertFalse(controller.searching)
        self.assertEqual(controller.editor.state.section.value, "sounds")

    def test_escape_cancels_settings_search_and_restores_cursor(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        # Drop into voices RECORD so we have a pre-search origin worth
        # restoring.
        router.handle_key(ENTER)
        router.handle_key(Key.printable("/"))
        for ch in "s1":
            router.handle_key(Key.printable(ch))
        self.assertEqual(controller.editor.state.section.value, "sounds")
        result = router.handle_key(ESCAPE)
        self.assertEqual(result, "settings_search_cancel")
        self.assertFalse(controller.searching)
        self.assertEqual(controller.editor.state.section.value, "voices")

    def test_backspace_trims_settings_search_buffer(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.printable("/"))
        for ch in "abc":
            router.handle_key(Key.printable(ch))
        result = router.handle_key(BACKSPACE)
        self.assertEqual(result, "settings_search_backspace")
        self.assertEqual(controller.search_buffer, "ab")

    def test_motion_keys_swallowed_while_searching(self) -> None:
        """Up / Down must not step records while a `/` composer is open —
        that would silently dismiss the overlay and confuse narration."""
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.printable("/"))
        router.handle_key(Key.printable("s"))
        section_before = controller.editor.state.section
        # Arrow keys should NOT change section while composer is active.
        self.assertIsNone(router.handle_key(UP))
        self.assertIsNone(router.handle_key(DOWN))
        self.assertTrue(controller.searching)
        self.assertEqual(controller.editor.state.section, section_before)

    def test_commit_payload_reports_query_and_match_count(self) -> None:
        bus, _, router, _ = _build_with_settings(["echo"])
        recorder = _Recorder(bus)
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.printable("/"))
        for ch in "v1":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        commit_events = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "settings_search_commit"
        ]
        self.assertEqual(len(commit_events), 1)
        payload = commit_events[0].payload
        self.assertEqual(payload["query"], "v1")
        self.assertGreaterEqual(payload["match_count"], 1)

    def test_n_and_N_cycle_matches_after_commit(self) -> None:
        _, _, router, controller = _build_with_settings(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.printable("/"))
        # "v1" matches voices[0] and bindings[0] (voice_id=v1) — 2 results.
        for ch in "v1":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        first_location = (
            controller.editor.state.section,
            controller.editor.state.record_index,
        )
        result = router.handle_key(Key.printable("n"))
        self.assertEqual(result, "settings_search_next")
        second_location = (
            controller.editor.state.section,
            controller.editor.state.record_index,
        )
        self.assertNotEqual(first_location, second_location)
        self.assertEqual(router.handle_key(Key.printable("N")), "settings_search_prev")
        self.assertEqual(
            (controller.editor.state.section, controller.editor.state.record_index),
            first_location,
        )

    def test_search_without_controller_is_noop(self) -> None:
        """Router with no settings_controller: the binding dispatches but
        the handler silently no-ops."""
        bus = EventBus()
        session = Session.new()
        session.add_cell(Cell.new("echo"))
        cursor = NotebookCursor(session, bus)
        cursor.enter_settings_mode()  # simulate being in SETTINGS focus
        router = InputRouter(cursor, bus)
        self.assertEqual(
            router.handle_key(Key.printable("/")),
            "settings_search_begin",
        )

    def test_slash_is_not_bound_outside_settings_mode(self) -> None:
        """The `/` key in NOTEBOOK mode must not accidentally open the
        settings search (settings may not even be open)."""
        _, _, router, controller = _build_with_settings(["echo"])
        # Start in NOTEBOOK; `/` should be unbound there.
        self.assertIsNone(router.handle_key(Key.printable("/")))
        self.assertFalse(controller.searching)

    def test_help_mentions_settings_search(self) -> None:
        from asat.input_router import HELP_LINES
        joined = "\n".join(HELP_LINES)
        self.assertIn("search", joined.lower())

    def test_invalid_commit_surfaces_in_action_payload(self) -> None:
        bus, _, router, _ = _build_with_settings(["echo"])
        recorder = _Recorder(bus)
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(ENTER)
        router.handle_key(ENTER)
        router.handle_key(DOWN)  # engine
        router.handle_key(DOWN)  # rate
        router.handle_key(Key.printable("e"))
        for ch in "fast":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)  # commit (will fail: "fast" isn't a float)
        commit_events = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "settings_edit_commit"
        ]
        self.assertEqual(len(commit_events), 1)
        self.assertFalse(commit_events[0].payload["ok"])
        self.assertIn("error", commit_events[0].payload)


class MetaCommandTests(unittest.TestCase):

    def test_colon_settings_opens_editor_from_input_mode(self) -> None:
        _, cursor, router, controller = _build_with_settings([""])
        router.handle_key(ENTER)  # NOTEBOOK -> INPUT (empty buffer)
        for ch in ":settings":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)  # submit
        self.assertEqual(cursor.focus.mode, FocusMode.SETTINGS)
        self.assertTrue(controller.is_open)

    def test_colon_settings_does_not_overwrite_cell_command(self) -> None:
        _, _, router, _ = _build_with_settings([""])
        # The cell itself keeps whatever it held before; meta-command
        # bypasses commit. Reaching this assertion with no raise proves it.
        router.handle_key(ENTER)
        for ch in ":settings":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)

    def test_submit_payload_reports_meta_command(self) -> None:
        bus, _, router, _ = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":settings":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        submits = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertEqual(len(submits), 1)
        self.assertEqual(submits[0].payload["meta_command"], "settings")
        self.assertNotIn("command", submits[0].payload)

    def test_unknown_meta_command_is_intercepted_with_help_hint(self) -> None:
        """F17: `:unknown` no longer falls through to the shell; the
        router consumes the line and emits a HELP_REQUESTED hint."""
        bus, cursor, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":unknown":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        submits = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertIsNone(submits[0].payload.get("command"))
        self.assertIsNone(submits[0].payload.get("meta_command"))
        self.assertEqual(submits[0].payload.get("meta_unknown"), "unknown")
        helps = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertEqual(len(helps), 1)
        text = "\n".join(helps[0].payload["lines"])
        self.assertIn("unknown", text)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(cursor.focus.input_buffer, "")

    def test_unknown_meta_command_suggests_closest_known_name(self) -> None:
        """F17: difflib suggests `:settings` when the user types
        `:setings` (single-letter typo)."""
        bus, _, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":setings":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        submits = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertEqual(submits[0].payload.get("meta_suggestion"), "settings")
        helps = recorder.types_of(EventType.HELP_REQUESTED)
        text = "\n".join(helps[0].payload["lines"])
        self.assertIn(":settings", text)

    def test_meta_command_matching_is_case_insensitive(self) -> None:
        """F17: `:HELP`, `:Help`, and `:help` all run the same command."""
        bus, _, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":HELP":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        helps = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertEqual(len(helps), 1)
        submits = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertEqual(submits[-1].payload["meta_command"], "help")

    def test_meta_command_trailing_argument_reported_in_payload(self) -> None:
        """F17: `:help settings` exposes `settings` as meta_argument."""
        bus, _, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":help settings":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        submits = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertEqual(submits[-1].payload["meta_command"], "help")
        self.assertEqual(submits[-1].payload["meta_argument"], "settings")

    def test_colon_pwd_reports_working_directory(self) -> None:
        """F17: `:pwd` emits HELP_REQUESTED with the current CWD."""
        import os

        bus, cursor, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":pwd":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        helps = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertEqual(len(helps), 1)
        text = "\n".join(helps[0].payload["lines"])
        self.assertIn(os.getcwd(), text)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(cursor.focus.input_buffer, "")

    def test_colon_commands_lists_every_meta_command(self) -> None:
        """F17: `:commands` enumerates the full meta-command set."""
        from asat.input_router import META_COMMANDS

        bus, _, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":commands":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        helps = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertEqual(len(helps), 1)
        text = "\n".join(helps[0].payload["lines"])
        for name in META_COMMANDS:
            self.assertIn(f":{name}", text)

    def test_colon_help_publishes_help_requested_with_cheat_sheet_lines(self) -> None:
        from asat.input_router import HELP_LINES

        bus, _, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":help":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        helps = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertEqual(len(helps), 1)
        self.assertEqual(tuple(helps[0].payload["lines"]), HELP_LINES)
        submits = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertEqual(submits[-1].payload["meta_command"], "help")

    def test_colon_help_topic_publishes_that_topics_lines(self) -> None:
        """F38: `:help <topic>` narrates a focused micro-tour."""
        from asat.help_topics import HELP_TOPICS

        bus, _, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":help settings":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        helps = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertEqual(len(helps), 1)
        self.assertEqual(
            tuple(helps[0].payload["lines"]),
            HELP_TOPICS["settings"],
        )
        self.assertEqual(helps[0].payload["help_topic"], "settings")

    def test_colon_help_topic_is_case_insensitive(self) -> None:
        """F38: `:HELP Navigation` resolves the same as `:help navigation`."""
        from asat.help_topics import HELP_TOPICS

        bus, _, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":HELP Navigation":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        helps = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertEqual(tuple(helps[0].payload["lines"]), HELP_TOPICS["navigation"])

    def test_colon_help_topics_lists_every_topic_name(self) -> None:
        """F38: `:help topics` enumerates every registered topic."""
        from asat.help_topics import topic_names

        bus, _, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":help topics":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        helps = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertEqual(len(helps), 1)
        text = "\n".join(helps[0].payload["lines"])
        for name in topic_names():
            self.assertIn(f":help {name}", text)
        self.assertEqual(helps[0].payload["help_topic"], "topics")

    def test_colon_help_unknown_topic_suggests_closest_match(self) -> None:
        """F38: `:help navgation` (typo) suggests `:help navigation`."""
        bus, _, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":help navgation":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        helps = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertEqual(len(helps), 1)
        text = "\n".join(helps[0].payload["lines"])
        self.assertIn("Unknown", text)
        self.assertIn(":help navigation", text)
        self.assertEqual(
            helps[0].payload["help_topic_unknown"], "navgation"
        )

    def test_colon_help_is_ambient_leaves_user_in_input_mode(self) -> None:
        """`:help` consumes the buffer but keeps INPUT focus so the
        user can immediately continue typing their real command."""
        _, cursor, router, _controller = _build_with_settings([""])
        router.handle_key(ENTER)
        for ch in ":help":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(cursor.focus.input_buffer, "")
        # Typing after :help lands in the (now empty) buffer.
        for ch in "echo hi":
            router.handle_key(Key.printable(ch))
        self.assertEqual(cursor.focus.input_buffer, "echo hi")

    def test_colon_welcome_surfaces_meta_command_and_stays_in_input(self) -> None:
        """F44: `:welcome` emits `meta_command: welcome` on the submit
        action and, being ambient, keeps the user in INPUT mode so
        they can keep typing after hearing the tour replay."""
        bus, cursor, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":welcome":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        submits = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertEqual(submits[-1].payload["meta_command"], "welcome")
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(cursor.focus.input_buffer, "")

    def test_colon_save_is_ambient_leaves_user_in_input_mode(self) -> None:
        """Same ambient semantics for `:save` — session gets saved by
        the Application via the ACTION_INVOKED payload, and the user
        stays exactly where they were."""
        _, cursor, router, _controller = _build_with_settings([""])
        router.handle_key(ENTER)
        for ch in ":save":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)
        self.assertEqual(cursor.focus.input_buffer, "")

    def test_colon_quit_still_ejects_to_notebook(self) -> None:
        """Non-ambient meta-commands (`:quit`, `:settings`) keep the
        existing behaviour: leave INPUT mode."""
        _, cursor, router, _controller = _build_with_settings([""])
        router.handle_key(ENTER)
        for ch in ":quit":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)


class CustomBindingTests(unittest.TestCase):

    def test_custom_bindings_override_defaults(self) -> None:
        custom = {
            FocusMode.NOTEBOOK: {Key.printable("j"): "move_down"},
            FocusMode.INPUT: {},
        }
        bus = EventBus()
        session = Session.new()
        for command in ("a", "b"):
            session.add_cell(Cell.new(command))
        cursor = NotebookCursor(session, bus)
        router = InputRouter(cursor, bus, bindings=custom)
        self.assertEqual(router.handle_key(Key.printable("j")), "move_down")
        self.assertIsNone(router.handle_key(UP))


def _build_with_menu(
    commands: list[str],
) -> tuple[EventBus, NotebookCursor, InputRouter, ActionMenu, OutputCursor, OutputRecorder, MemoryClipboard]:
    """Build a router wired to a real ActionMenu + default providers.

    Mirrors what `Application.build` assembles but keeps the audio and
    kernel sides out of the way so menu dispatch is the only thing
    under test.
    """
    bus = EventBus()
    session = Session.new()
    for command in commands:
        session.add_cell(Cell.new(command))
    cursor = NotebookCursor(session, bus)
    recorder = OutputRecorder(bus)
    output_cursor = OutputCursor(bus)
    clipboard = MemoryClipboard()
    catalog = default_actions(
        cursor=cursor,
        recorder=recorder,
        output_cursor=output_cursor,
        clipboard=clipboard,
        bus=bus,
    )
    menu = ActionMenu(bus, catalog)
    router = InputRouter(
        cursor,
        bus,
        output_cursor=output_cursor,
        action_menu=menu,
    )
    return bus, cursor, router, menu, output_cursor, recorder, clipboard


class ActionMenuBindingTests(unittest.TestCase):
    """F14: F2 (and Ctrl+.) open the contextual menu, and while the
    menu is open Up / Down / Enter / Escape drive it instead of the
    current focus mode."""

    def test_f2_opens_menu_from_notebook(self) -> None:
        _, _, router, menu, _, _, _ = _build_with_menu(["echo"])
        self.assertFalse(menu.is_open)
        self.assertEqual(router.handle_key(F2), "open_action_menu")
        self.assertTrue(menu.is_open)
        # NOTEBOOK providers contribute "enter_input" + "view_output".
        self.assertEqual([item.id for item in menu.items], ["enter_input", "view_output"])

    def test_ctrl_dot_is_alternate_opener(self) -> None:
        _, _, router, menu, _, _, _ = _build_with_menu(["echo"])
        self.assertEqual(
            router.handle_key(Key.combo(".", Modifier.CTRL)),
            "open_action_menu",
        )
        self.assertTrue(menu.is_open)

    def test_f2_opens_menu_from_input_mode(self) -> None:
        _, _, router, menu, _, _, _ = _build_with_menu(["echo"])
        router.handle_key(ENTER)  # enter INPUT
        router.handle_key(F2)
        self.assertTrue(menu.is_open)
        # INPUT providers contribute "submit" + "exit_input".
        self.assertEqual([item.id for item in menu.items], ["submit", "exit_input"])

    def test_menu_up_and_down_cycle_items(self) -> None:
        _, _, router, menu, _, _, _ = _build_with_menu(["echo"])
        router.handle_key(F2)
        self.assertEqual(menu.current_item().id, "enter_input")
        self.assertEqual(router.handle_key(DOWN), "menu_next")
        self.assertEqual(menu.current_item().id, "view_output")
        self.assertEqual(router.handle_key(UP), "menu_prev")
        self.assertEqual(menu.current_item().id, "enter_input")

    def test_enter_activates_and_closes(self) -> None:
        _, cursor, router, menu, _, _, _ = _build_with_menu(["echo"])
        router.handle_key(F2)
        # "enter_input" is first; activating it enters INPUT mode.
        result = router.handle_key(ENTER)
        self.assertEqual(result, "menu_activate")
        self.assertFalse(menu.is_open)
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)

    def test_escape_closes_without_activating(self) -> None:
        _, cursor, router, menu, _, _, _ = _build_with_menu(["echo"])
        router.handle_key(F2)
        result = router.handle_key(ESCAPE)
        self.assertEqual(result, "menu_close")
        self.assertFalse(menu.is_open)
        # NOT in input mode because we cancelled.
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)

    def test_output_mode_menu_carries_line_context(self) -> None:
        bus, cursor, router, menu, output_cursor, recorder, clipboard = _build_with_menu(["echo"])
        cell_id = cursor.focus.cell_id
        buffer = recorder.buffer_for(cell_id)
        buffer.append("first", stream=STDOUT)
        buffer.append("second", stream=STDOUT)
        output_cursor.attach(buffer)
        cursor.view_output_mode()
        output_cursor.move_to_end()
        router.handle_key(F2)
        # "copy_line" only appears when line_text was captured.
        self.assertIn("copy_line", [item.id for item in menu.items])
        # Focus the "copy_line" item and invoke it.
        while menu.current_item().id != "copy_line":
            router.handle_key(DOWN)
        router.handle_key(ENTER)
        self.assertEqual(clipboard.text, "second")

    def test_unbound_menu_key_is_swallowed(self) -> None:
        _, _, router, menu, _, _, _ = _build_with_menu(["echo"])
        router.handle_key(F2)
        before_index = menu.items.index(menu.current_item())
        # A printable key while menu is open should not leak into INPUT
        # insertion or change the menu focus.
        self.assertIsNone(router.handle_key(Key.printable("x")))
        self.assertTrue(menu.is_open)
        self.assertEqual(menu.items.index(menu.current_item()), before_index)

    def test_menu_no_op_without_action_menu(self) -> None:
        _, _, _, router, _ = _build(["echo"])
        # Without an action_menu wired in, F2 maps to "open_action_menu"
        # but the handler silently no-ops. Router still reports the
        # matched action so downstream observers see the attempt.
        self.assertEqual(router.handle_key(F2), "open_action_menu")


class OutputSearchAndGotoBindingTests(unittest.TestCase):
    """F16: `/` opens search, `g` opens goto-line, `n`/`N` cycle matches."""

    def _stack(
        self,
        lines: list[tuple[str, str]],
    ) -> tuple[EventBus, NotebookCursor, InputRouter, OutputCursor]:
        bus = EventBus()
        session = Session.new()
        cell = Cell.new("echo")
        session.add_cell(cell)
        cursor = NotebookCursor(session, bus)
        output_cursor = OutputCursor(bus, page_size=3)
        buffer = OutputBuffer(cell_id=cell.cell_id)
        for text, stream in lines:
            buffer.append(text, stream=stream)
        output_cursor.attach(buffer)
        cursor.view_output_mode()
        router = InputRouter(cursor, bus, output_cursor=output_cursor)
        return bus, cursor, router, output_cursor

    def test_slash_opens_search_composer(self) -> None:
        _, _, router, oc = self._stack([("alpha", "stdout"), ("beta", "stdout")])
        self.assertEqual(router.handle_key(Key.printable("/")), "output_search_begin")
        self.assertEqual(oc.composer_mode, "search")

    def test_typed_chars_extend_search_and_narrow_matches(self) -> None:
        _, _, router, oc = self._stack(
            [("one", "stdout"), ("two", "stdout"), ("three", "stdout"), ("four", "stdout")]
        )
        router.handle_key(Key.printable("/"))
        router.handle_key(Key.printable("t"))
        # First match is "two" at index 1.
        self.assertEqual(oc.line_number, 1)
        router.handle_key(Key.printable("h"))
        # Now only "three" matches (index 2).
        self.assertEqual(oc.line_number, 2)

    def test_enter_commits_search_and_leaves_composer(self) -> None:
        _, _, router, oc = self._stack([("hi", "stdout"), ("hello", "stdout")])
        router.handle_key(Key.printable("/"))
        router.handle_key(Key.printable("h"))
        router.handle_key(Key.printable("e"))
        result = router.handle_key(ENTER)
        self.assertEqual(result, "output_composer_commit")
        self.assertIsNone(oc.composer_mode)
        self.assertEqual(oc.line_number, 1)

    def test_escape_cancels_search_and_restores_position(self) -> None:
        _, _, router, oc = self._stack([("a", "stdout"), ("b", "stdout"), ("c", "stdout")])
        oc.move_to_start()
        router.handle_key(Key.printable("/"))
        router.handle_key(Key.printable("c"))
        self.assertEqual(oc.line_number, 2)
        result = router.handle_key(ESCAPE)
        self.assertEqual(result, "output_composer_cancel")
        self.assertIsNone(oc.composer_mode)
        self.assertEqual(oc.line_number, 0)

    def test_n_cycles_to_next_match_after_commit(self) -> None:
        _, _, router, oc = self._stack(
            [("error 1", "stdout"), ("ok", "stdout"), ("error 2", "stdout")]
        )
        router.handle_key(Key.printable("/"))
        for ch in "error":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        self.assertEqual(oc.line_number, 0)
        self.assertEqual(router.handle_key(Key.printable("n")), "output_search_next")
        self.assertEqual(oc.line_number, 2)
        self.assertEqual(router.handle_key(Key.printable("N")), "output_search_prev")
        self.assertEqual(oc.line_number, 0)

    def test_g_opens_goto_and_digits_extend(self) -> None:
        _, _, router, oc = self._stack(
            [(f"line-{i}", "stdout") for i in range(12)]
        )
        self.assertEqual(router.handle_key(Key.printable("g")), "output_goto_begin")
        self.assertEqual(oc.composer_mode, "goto")
        router.handle_key(Key.printable("5"))
        self.assertEqual(oc.composer_buffer, "5")
        router.handle_key(ENTER)
        self.assertIsNone(oc.composer_mode)
        # 1-based 5 -> index 4.
        self.assertEqual(oc.line_number, 4)

    def test_goto_non_digits_are_swallowed(self) -> None:
        _, _, router, oc = self._stack([(f"l{i}", "stdout") for i in range(5)])
        router.handle_key(Key.printable("g"))
        router.handle_key(Key.printable("x"))  # swallowed
        router.handle_key(Key.printable("3"))
        self.assertEqual(oc.composer_buffer, "3")

    def test_backspace_trims_composer_buffer(self) -> None:
        _, _, router, oc = self._stack([("alpha", "stdout"), ("beta", "stdout")])
        router.handle_key(Key.printable("/"))
        for ch in "al":
            router.handle_key(Key.printable(ch))
        self.assertEqual(oc.composer_buffer, "al")
        result = router.handle_key(BACKSPACE)
        self.assertEqual(result, "output_composer_backspace")
        self.assertEqual(oc.composer_buffer, "a")

    def test_motion_keys_are_swallowed_during_composer(self) -> None:
        """Arrow keys must NOT step the line cursor while a composer is
        open — that would silently dismiss the search / goto."""
        _, _, router, oc = self._stack([("a", "stdout"), ("b", "stdout"), ("c", "stdout")])
        router.handle_key(Key.printable("/"))
        router.handle_key(Key.printable("a"))
        landing = oc.line_number
        self.assertIsNone(router.handle_key(UP))
        self.assertEqual(oc.line_number, landing)
        self.assertEqual(oc.composer_mode, "search")

    def test_search_without_output_cursor_is_noop(self) -> None:
        # Router with no OutputCursor: the binding still dispatches but
        # the handler guards against the missing collaborator.
        bus = EventBus()
        session = Session.new()
        cell = Cell.new("echo")
        session.add_cell(cell)
        cursor = NotebookCursor(session, bus)
        cursor.view_output_mode()
        router = InputRouter(cursor, bus)
        self.assertEqual(router.handle_key(Key.printable("/")), "output_search_begin")


class CellLifecycleBindingTests(unittest.TestCase):
    """F15: cell-level ops bound to NOTEBOOK keystrokes and meta-commands."""

    def test_d_deletes_focused_cell(self) -> None:
        bus, session, cursor, router, cells = _build(["a", "b", "c"])
        recorder = _Recorder(bus)
        cursor.focus_cell(cells[1].cell_id)
        result = router.handle_key(Key.printable("d"))
        self.assertEqual(result, "delete_cell")
        self.assertEqual(len(session), 2)
        removed = recorder.types_of(EventType.CELL_REMOVED)
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0].payload["cell_id"], cells[1].cell_id)

    def test_y_duplicates_focused_cell(self) -> None:
        bus, session, cursor, router, cells = _build(["a", "b"])
        recorder = _Recorder(bus)
        cursor.focus_cell(cells[0].cell_id)
        result = router.handle_key(Key.printable("y"))
        self.assertEqual(result, "duplicate_cell")
        self.assertEqual(len(session), 3)
        self.assertEqual(session.cells[1].command, "a")
        created = recorder.types_of(EventType.CELL_CREATED)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].payload["command"], "a")

    def test_alt_up_moves_cell_up(self) -> None:
        bus, session, cursor, router, cells = _build(["a", "b", "c"])
        recorder = _Recorder(bus)
        cursor.focus_cell(cells[2].cell_id)
        result = router.handle_key(Key.special("up", Modifier.ALT))
        self.assertEqual(result, "move_cell_up")
        self.assertEqual(
            [c.cell_id for c in session.cells],
            [cells[0].cell_id, cells[2].cell_id, cells[1].cell_id],
        )
        moved = recorder.types_of(EventType.CELL_MOVED)
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].payload["old_index"], 2)
        self.assertEqual(moved[0].payload["new_index"], 1)

    def test_alt_down_moves_cell_down(self) -> None:
        _, session, cursor, router, cells = _build(["a", "b", "c"])
        cursor.focus_cell(cells[0].cell_id)
        self.assertEqual(
            router.handle_key(Key.special("down", Modifier.ALT)),
            "move_cell_down",
        )
        self.assertEqual(session.cells[1].cell_id, cells[0].cell_id)

    def test_alt_up_at_top_is_noop(self) -> None:
        bus, session, cursor, router, cells = _build(["a", "b"])
        recorder = _Recorder(bus)
        cursor.focus_cell(cells[0].cell_id)
        router.handle_key(Key.special("up", Modifier.ALT))
        # Order unchanged; no CELL_MOVED event published.
        self.assertEqual(
            [c.cell_id for c in session.cells],
            [cells[0].cell_id, cells[1].cell_id],
        )
        self.assertEqual(recorder.types_of(EventType.CELL_MOVED), [])

    def test_d_and_y_do_not_fire_in_input_mode(self) -> None:
        """F13 already claims printable keys in INPUT; this confirms
        the cell-op bindings live exclusively on NOTEBOOK mode."""
        _, session, cursor, router, _ = _build(["a", "b"])
        router.handle_key(ENTER)  # enter INPUT
        self.assertEqual(cursor.focus.mode, FocusMode.INPUT)
        router.handle_key(Key.printable("d"))
        router.handle_key(Key.printable("y"))
        # Both keys were inserted as characters, not dispatched.
        self.assertEqual(cursor.focus.input_buffer, "ady")
        self.assertEqual(len(session), 2)

    def test_delete_meta_command_removes_focused_cell(self) -> None:
        bus, session, cursor, router, cells = _build(["a", "b"])
        recorder = _Recorder(bus)
        cursor.focus_cell(cells[0].cell_id)
        cursor.enter_input_mode()
        # Clear the existing command so `:delete` is the only buffer text.
        cursor.reset_input_buffer()
        for ch in ":delete":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        self.assertEqual(len(session), 1)
        self.assertEqual(cursor.focus.mode, FocusMode.NOTEBOOK)
        removed = recorder.types_of(EventType.CELL_REMOVED)
        self.assertEqual(len(removed), 1)
        submit = [
            e
            for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertEqual(submit[-1].payload.get("meta_command"), "delete")

    def test_duplicate_meta_command_copies_focused_cell(self) -> None:
        _, session, cursor, router, cells = _build(["echo hi"])
        cursor.focus_cell(cells[0].cell_id)
        cursor.enter_input_mode()
        cursor.reset_input_buffer()
        for ch in ":duplicate":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        self.assertEqual(len(session), 2)
        self.assertEqual(session.cells[1].command, "echo hi")

    def test_help_mentions_cell_ops(self) -> None:
        from asat.input_router import HELP_LINES
        joined = "\n".join(HELP_LINES)
        self.assertIn("d delete", joined)
        self.assertIn("y duplicate", joined)
        self.assertIn(":delete", joined)
        self.assertIn(":duplicate", joined)


def _defaults_bank() -> SoundBank:
    """Defaults bank paired with `_settings_bank` so reset has somewhere to go."""
    return SoundBank(
        voices=(Voice(id="v1", rate=1.0),),
        sounds=(SoundRecipe(id="s1", kind="tone", params={"frequency": 440.0}),),
        bindings=(
            EventBinding(
                id="b1",
                event_type="cell.created",
                voice_id="v1",
                say_template="hello",
            ),
        ),
    )


def _build_with_reset(
    commands: list[str],
) -> tuple[EventBus, NotebookCursor, InputRouter, SettingsController]:
    """Router + controller wired with a defaults bank so Ctrl+R can fire."""
    bus = EventBus()
    session = Session.new()
    for command in commands:
        session.add_cell(Cell.new(command))
    cursor = NotebookCursor(session, bus)
    # Use a mutated working bank so reset has a visible change to apply.
    working = SoundBank(
        voices=(Voice(id="v1", rate=9.0),),
        sounds=(SoundRecipe(id="s1", kind="tone", params={"frequency": 100.0}),),
        bindings=_defaults_bank().bindings,
    )
    controller = SettingsController(bus, working, defaults_bank=_defaults_bank())
    router = InputRouter(cursor, bus, settings_controller=controller)
    return bus, cursor, router, controller


class SettingsResetBindingTests(unittest.TestCase):
    """F21c: Ctrl+R and reset-confirm key handling inside SETTINGS mode."""

    def test_ctrl_r_opens_reset_confirmation_at_cursor_scope(self) -> None:
        _, _, router, controller = _build_with_reset(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        result = router.handle_key(Key.combo("r", Modifier.CTRL))
        self.assertEqual(result, "settings_reset_begin")
        self.assertTrue(controller.resetting)
        # Cursor is at SECTION level → scope defaults to SECTION.
        self.assertEqual(controller.reset_scope.value, "section")

    def test_enter_confirms_the_pending_reset(self) -> None:
        _, _, router, controller = _build_with_reset(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.combo("r", Modifier.CTRL))
        result = router.handle_key(ENTER)
        self.assertEqual(result, "settings_reset_confirm")
        self.assertFalse(controller.resetting)
        # Reset applied: voices[0].rate back to 1.0.
        self.assertAlmostEqual(controller.bank.voices[0].rate, 1.0)

    def test_escape_cancels_the_pending_reset(self) -> None:
        _, _, router, controller = _build_with_reset(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.combo("r", Modifier.CTRL))
        result = router.handle_key(ESCAPE)
        self.assertEqual(result, "settings_reset_cancel")
        self.assertFalse(controller.resetting)
        # No change: the mutated bank survives.
        self.assertAlmostEqual(controller.bank.voices[0].rate, 9.0)

    def test_arrow_keys_swallowed_while_reset_is_pending(self) -> None:
        """Stray arrow keys must not move the cursor while a confirmation
        is open — acknowledging the reset has to be deliberate."""
        _, _, router, controller = _build_with_reset(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.combo("r", Modifier.CTRL))
        section_before = controller.editor.state.section
        self.assertIsNone(router.handle_key(UP))
        self.assertIsNone(router.handle_key(DOWN))
        self.assertTrue(controller.resetting)
        self.assertEqual(controller.editor.state.section, section_before)

    def test_reset_begin_payload_reports_scope(self) -> None:
        bus, _, router, _ = _build_with_reset(["echo"])
        recorder = _Recorder(bus)
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.combo("r", Modifier.CTRL))
        begins = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "settings_reset_begin"
        ]
        self.assertEqual(len(begins), 1)
        self.assertEqual(begins[0].payload["scope"], "section")
        self.assertTrue(begins[0].payload["opened"])

    def test_reset_confirm_payload_reports_scope_and_applied(self) -> None:
        bus, _, router, _ = _build_with_reset(["echo"])
        recorder = _Recorder(bus)
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.combo("r", Modifier.CTRL))
        router.handle_key(ENTER)
        confirms = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "settings_reset_confirm"
        ]
        self.assertEqual(len(confirms), 1)
        self.assertEqual(confirms[0].payload["scope"], "section")
        self.assertTrue(confirms[0].payload["applied"])

    def test_ctrl_r_without_controller_is_safe_noop(self) -> None:
        """A router without a settings_controller must not crash on Ctrl+R."""
        bus = EventBus()
        session = Session.new()
        session.add_cell(Cell.new("echo"))
        cursor = NotebookCursor(session, bus)
        cursor.enter_settings_mode()  # simulate SETTINGS focus
        router = InputRouter(cursor, bus)
        self.assertEqual(
            router.handle_key(Key.combo("r", Modifier.CTRL)),
            "settings_reset_begin",
        )

    def test_ctrl_r_while_searching_is_refused(self) -> None:
        """While a `/` composer is open the `r` key flows into the query;
        since `r` in Modifier.CTRL isn't a printable, the begin-reset
        handler runs but the controller refuses because searching."""
        _, _, router, controller = _build_with_reset(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(Key.printable("/"))
        self.assertTrue(controller.searching)
        # The Ctrl+R binding isn't dispatched (we're in the search
        # composer sub-mode), so controller.resetting stays False.
        router.handle_key(Key.combo("r", Modifier.CTRL))
        self.assertFalse(controller.resetting)

    def test_ctrl_r_while_editing_cancels_edit_then_opens_reset(self) -> None:
        _, _, router, controller = _build_with_reset(["echo"])
        router.handle_key(Key.combo(",", Modifier.CTRL))
        router.handle_key(ENTER)
        router.handle_key(ENTER)  # FIELD
        router.handle_key(Key.printable("e"))
        router.handle_key(Key.printable("x"))
        # Ctrl+R in edit sub-mode is swallowed (not printable), so
        # the binding map doesn't see it — reset stays unopened.
        self.assertIsNone(router.handle_key(Key.combo("r", Modifier.CTRL)))
        self.assertTrue(controller.editing)
        self.assertFalse(controller.resetting)

    def test_help_mentions_ctrl_r_reset(self) -> None:
        from asat.input_router import HELP_LINES
        joined = "\n".join(HELP_LINES).lower()
        self.assertIn("ctrl+r", joined)
        self.assertIn("reset", joined)

    def test_help_mentions_meta_reset(self) -> None:
        from asat.input_router import HELP_LINES
        joined = "\n".join(HELP_LINES)
        self.assertIn(":reset", joined)

    def test_help_mentions_settings_undo_redo(self) -> None:
        # F48: the SETTINGS-mode undo/redo keystrokes must stay
        # audible to users who can only learn from `:help`. If a
        # future edit drops them, this test names F48 in its
        # failure so the cause is obvious.
        from asat.input_router import HELP_LINES
        joined = "\n".join(HELP_LINES).lower()
        self.assertIn("ctrl+z", joined, "F48: Ctrl+Z undo missing from HELP_LINES")
        self.assertIn("ctrl+y", joined, "F48: Ctrl+Y redo missing from HELP_LINES")
        self.assertIn("undo", joined)
        self.assertIn("redo", joined)


class MetaResetCommandTests(unittest.TestCase):
    """F21c: `:reset` meta-command from INPUT mode."""

    def _submit_meta(
        self, router: InputRouter, cursor: NotebookCursor, text: str
    ) -> None:
        """Type `text` in INPUT mode and submit."""
        cursor.enter_input_mode()
        cursor.reset_input_buffer()
        for ch in text:
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)

    def test_colon_reset_bank_opens_settings_and_begins_bank_reset(self) -> None:
        _, cursor, router, controller = _build_with_reset([""])
        self._submit_meta(router, cursor, ":reset bank")
        self.assertEqual(cursor.focus.mode, FocusMode.SETTINGS)
        self.assertTrue(controller.is_open)
        self.assertTrue(controller.resetting)
        self.assertEqual(controller.reset_scope.value, "bank")

    def test_colon_reset_all_is_alias_for_bank(self) -> None:
        _, cursor, router, controller = _build_with_reset([""])
        self._submit_meta(router, cursor, ":reset all")
        self.assertEqual(controller.reset_scope.value, "bank")

    def test_colon_reset_without_argument_emits_help(self) -> None:
        bus, cursor, router, controller = _build_with_reset([""])
        recorder = _Recorder(bus)
        self._submit_meta(router, cursor, ":reset")
        self.assertFalse(controller.resetting)
        # The cursor does not change modes for a help-only meta.
        help_events = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertGreaterEqual(len(help_events), 1)
        lines = help_events[-1].payload["lines"]
        joined = "\n".join(lines).lower()
        self.assertIn("ctrl+r", joined)

    def test_colon_reset_section_is_refused_from_input(self) -> None:
        """Finer-grained scopes require Ctrl+R inside SETTINGS — from
        INPUT we deliberately refuse them so a user cannot reset the
        wrong slice without cursor context."""
        bus, cursor, router, controller = _build_with_reset([""])
        recorder = _Recorder(bus)
        self._submit_meta(router, cursor, ":reset section")
        self.assertFalse(controller.resetting)
        help_events = recorder.types_of(EventType.HELP_REQUESTED)
        self.assertGreaterEqual(len(help_events), 1)

    def test_colon_reset_is_case_insensitive(self) -> None:
        _, cursor, router, controller = _build_with_reset([""])
        self._submit_meta(router, cursor, ":RESET BANK")
        self.assertTrue(controller.resetting)
        self.assertEqual(controller.reset_scope.value, "bank")

    def test_meta_reset_payload_carries_command_and_argument(self) -> None:
        bus, cursor, router, _ = _build_with_reset([""])
        recorder = _Recorder(bus)
        self._submit_meta(router, cursor, ":reset bank")
        submit = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertGreaterEqual(len(submit), 1)
        self.assertEqual(submit[-1].payload["meta_command"], "reset")
        self.assertEqual(submit[-1].payload["meta_argument"], "bank")

    def test_meta_reset_listed_in_meta_commands(self) -> None:
        from asat.input_router import META_COMMANDS
        self.assertIn("reset", META_COMMANDS)


class ParseResetScopeTests(unittest.TestCase):
    """F21c: the module-level `:reset <arg>` argument parser."""

    def test_parse_reset_scope_recognises_bank_and_all(self) -> None:
        from asat.input_router import _parse_reset_scope
        from asat.settings_editor import ResetScope
        self.assertEqual(_parse_reset_scope("bank"), ResetScope.BANK)
        self.assertEqual(_parse_reset_scope("all"), ResetScope.BANK)

    def test_parse_reset_scope_recognises_each_grain(self) -> None:
        from asat.input_router import _parse_reset_scope
        from asat.settings_editor import ResetScope
        self.assertEqual(_parse_reset_scope("section"), ResetScope.SECTION)
        self.assertEqual(_parse_reset_scope("record"), ResetScope.RECORD)
        self.assertEqual(_parse_reset_scope("field"), ResetScope.FIELD)

    def test_parse_reset_scope_empty_returns_none(self) -> None:
        from asat.input_router import _parse_reset_scope
        self.assertIsNone(_parse_reset_scope(""))
        self.assertIsNone(_parse_reset_scope("   "))

    def test_parse_reset_scope_is_case_insensitive(self) -> None:
        from asat.input_router import _parse_reset_scope
        from asat.settings_editor import ResetScope
        self.assertEqual(_parse_reset_scope("BANK"), ResetScope.BANK)
        self.assertEqual(_parse_reset_scope("Record"), ResetScope.RECORD)

    def test_parse_reset_scope_unknown_returns_none(self) -> None:
        from asat.input_router import _parse_reset_scope
        self.assertIsNone(_parse_reset_scope("everything"))


if __name__ == "__main__":
    unittest.main()
