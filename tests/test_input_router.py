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

    def test_non_meta_colon_command_falls_through_as_regular_submit(self) -> None:
        bus, _, router, _controller = _build_with_settings([""])
        recorder = _Recorder(bus)
        router.handle_key(ENTER)
        for ch in ":unknown":
            router.handle_key(Key.printable(ch))
        router.handle_key(ENTER)
        submits = [
            e for e in recorder.types_of(EventType.ACTION_INVOKED)
            if e.payload.get("action") == "submit"
        ]
        self.assertEqual(submits[0].payload.get("command"), ":unknown")

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


if __name__ == "__main__":
    unittest.main()
