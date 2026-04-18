"""InputRouter: keystrokes to actions, with focus-aware bindings.

The router is where keystrokes meet the notebook. It owns a map from
(FocusMode, Key) pairs to action names, dispatches each keystroke to
the matching action, and publishes KEY_PRESSED and ACTION_INVOKED
events so observers (the audio engine, a recorder, future UI layers)
can react.

The router intentionally does not read from stdin or any OS API.
Keystrokes are delivered by calling handle_key(key). A platform
adapter that reads real terminal input and produces Key values is
left to a later phase; this keeps the router fully testable in
isolation and lets alternative input sources plug in cleanly.

Default bindings cover non-visual navigation between input cells,
in-place command editing, and line-level exploration of a cell's
captured output. They can be overridden or extended by passing a
custom bindings map.

An OutputCursor is optional. When one is provided, the router dispatches
OUTPUT-mode navigation actions to it. When it is absent, those actions
silently no-op so the router is still usable for sessions that do not
care about output navigation.
"""

from __future__ import annotations

from typing import Callable, Optional

from asat import keys as kc
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.keys import Key, Modifier
from asat.notebook import FocusMode, NotebookCursor
from asat.output_cursor import OutputCursor
from asat.settings_controller import SettingsController
from asat.settings_editor import SettingsEditorError


BindingMap = dict[FocusMode, dict[Key, str]]
ActionHandler = Callable[[], Optional[dict[str, object]]]


def _void(fn: Callable[..., object]) -> ActionHandler:
    """Wrap a side-effecting callable so it matches the ActionHandler shape.

    Cursor motion helpers return various convenience values (the Cell
    that moved into focus, the new FocusState, etc.) that the router
    does not care about. _void invokes the underlying method, discards
    its return value, and reports None so the dispatch table's type is
    uniform.
    """

    def wrapper() -> None:
        fn()
        return None

    return wrapper


def default_bindings() -> BindingMap:
    """Return a fresh copy of the default keystroke map.

    NOTEBOOK mode:
        Up/Down move between cells, Home/End jump to ends,
        Enter enters input mode on the focused cell,
        Ctrl+N appends a fresh cell and enters input mode,
        Ctrl+O opens the captured output of the focused cell,
        Ctrl+, (comma) opens the settings editor.

    INPUT mode:
        Backspace deletes the last character,
        Enter submits the current command,
        Escape commits and returns to notebook mode.
        Lines beginning with `:` are treated as meta-commands
        (see META_COMMANDS) instead of being handed to the kernel.

    OUTPUT mode:
        Up/Down walk one line at a time,
        PageUp/PageDown jump a page,
        Home/End jump to the first or last captured line,
        Escape returns to notebook mode.

    SETTINGS mode:
        Up/Down move between records / fields,
        Right / Enter descend one level,
        Left ascends one level,
        `e` begins editing the focused field,
        Ctrl+S saves the bank, Ctrl+Q closes the editor,
        Escape ascends or (at the top level) closes.
    """
    return {
        FocusMode.NOTEBOOK: {
            kc.UP: "move_up",
            kc.DOWN: "move_down",
            kc.HOME: "move_to_top",
            kc.END: "move_to_bottom",
            kc.ENTER: "enter_input",
            Key.combo("n", Modifier.CTRL): "new_cell",
            Key.combo("o", Modifier.CTRL): "view_output",
            Key.combo(",", Modifier.CTRL): "open_settings",
        },
        FocusMode.INPUT: {
            kc.BACKSPACE: "backspace",
            kc.ENTER: "submit",
            kc.ESCAPE: "exit_input",
        },
        FocusMode.OUTPUT: {
            kc.UP: "output_line_up",
            kc.DOWN: "output_line_down",
            kc.PAGE_UP: "output_page_up",
            kc.PAGE_DOWN: "output_page_down",
            kc.HOME: "output_to_start",
            kc.END: "output_to_end",
            kc.ESCAPE: "exit_output",
        },
        FocusMode.SETTINGS: {
            kc.UP: "settings_prev",
            kc.DOWN: "settings_next",
            kc.LEFT: "settings_ascend",
            kc.RIGHT: "settings_descend",
            kc.ENTER: "settings_descend",
            kc.ESCAPE: "settings_ascend",
            Key.printable("e"): "settings_begin_edit",
            Key.combo("s", Modifier.CTRL): "settings_save",
            Key.combo("q", Modifier.CTRL): "settings_close",
        },
    }


# Meta-commands recognised in INPUT mode when the buffer starts with `:`.
# A meta-command short-circuits the normal submit path: the cell is not
# handed to the kernel and the input buffer is discarded (not written
# back into the cell).
META_COMMANDS: tuple[str, ...] = ("settings", "save", "quit", "help")

# "Ambient" meta-commands do their job without taking focus away from
# INPUT mode. `:help` prints a cheat sheet; `:save` persists the
# session. In both cases the natural next action is to keep typing, so
# the router clears the in-progress buffer but leaves the user in INPUT
# mode. Commands NOT in this set (today: `:settings`, `:quit`)
# inherently require a mode change and go through `abandon_input_mode`.
AMBIENT_META_COMMANDS: frozenset[str] = frozenset({"help", "save"})


# The cheat-sheet lines a `:help` meta-command should surface. Kept in
# one place so the renderer, the narration, and the docs can all read
# from the same source of truth.
HELP_LINES: tuple[str, ...] = (
    "ASAT quick reference.",
    "NOTEBOOK:  Up/Down walk cells, Enter type, Ctrl+N new, Ctrl+O output, Ctrl+, settings.",
    "INPUT:     Enter submits, Backspace deletes, Escape leaves without running.",
    "OUTPUT:    Up/Down step lines, PageUp/PageDown page, Escape leaves.",
    "SETTINGS:  Up/Down walk, Right/Enter descend, Left/Escape ascend, e edit, Ctrl+S save, Ctrl+Q close.",
    "Meta:      :help, :settings, :save, :quit (type in INPUT mode then Enter).",
    "Exit:      :quit, or EOF (Ctrl+D on POSIX, Ctrl+Z Enter on Windows).",
    "Docs:      docs/USER_MANUAL.md for the full keystroke reference.",
)


class InputRouter:
    """Dispatches keystrokes to notebook actions."""

    SOURCE = "input_router"

    def __init__(
        self,
        cursor: NotebookCursor,
        bus: EventBus,
        bindings: Optional[BindingMap] = None,
        output_cursor: Optional[OutputCursor] = None,
        settings_controller: Optional[SettingsController] = None,
    ) -> None:
        """Attach the router to a cursor, event bus, and optional cursors.

        When a `settings_controller` is supplied, the router gains the
        `open_settings` action (bound to Ctrl+, by default), the
        SETTINGS focus-mode key map, and recognition of `:settings`
        in INPUT mode. When it is absent, those actions silently no-op
        so a test or embedding that ignores audio still works.
        """
        self._cursor = cursor
        self._bus = bus
        self._bindings = bindings if bindings is not None else default_bindings()
        self._output_cursor = output_cursor
        self._settings_controller = settings_controller

    @property
    def bindings(self) -> BindingMap:
        """Return the active binding map (not a copy; treat as read-only)."""
        return self._bindings

    def handle_key(self, key: Key) -> Optional[str]:
        """Dispatch a single keystroke and return the action name, if any.

        Publishes KEY_PRESSED for every call and ACTION_INVOKED when a
        binding matches. Returns the name of the action that ran, or
        None if the key was inserted as a printable character or had
        no binding.
        """
        self._publish_key(key)
        mode = self._cursor.focus.mode
        if mode == FocusMode.SETTINGS and self._settings_active_editing():
            return self._dispatch_settings_edit_key(key)
        mode_map = self._bindings.get(mode, {})
        action = mode_map.get(key)
        if action is not None:
            self._invoke(action, key)
            return action
        if mode == FocusMode.INPUT and key.is_printable() and key.char is not None:
            self._cursor.insert_character(key.char)
            self._publish_action("insert_character", key, {"char": key.char})
            return "insert_character"
        return None

    def _settings_active_editing(self) -> bool:
        """Return True when the settings controller is composing a value."""
        return (
            self._settings_controller is not None
            and self._settings_controller.is_open
            and self._settings_controller.editing
        )

    def _dispatch_settings_edit_key(self, key: Key) -> Optional[str]:
        """Route keys while the user is typing a replacement field value.

        This overrides the SETTINGS mode binding map so printable
        characters flow into the edit buffer rather than firing actions
        like `settings_begin_edit`.
        """
        assert self._settings_controller is not None
        if key == kc.ENTER:
            self._invoke("settings_edit_commit", key)
            return "settings_edit_commit"
        if key == kc.ESCAPE:
            self._invoke("settings_edit_cancel", key)
            return "settings_edit_cancel"
        if key == kc.BACKSPACE:
            self._invoke("settings_edit_backspace", key)
            return "settings_edit_backspace"
        if key.is_printable() and key.char is not None:
            self._settings_controller.extend_edit(key.char)
            self._publish_action("settings_edit_extend", key, {"char": key.char})
            return "settings_edit_extend"
        return None

    def _invoke(self, action: str, key: Key) -> None:
        """Run a named action and publish ACTION_INVOKED for it.

        Every handler returns an optional dict of extra payload fields
        to merge into the ACTION_INVOKED event. Most simple motions do
        not contribute extras and return None; "submit" uses the hook
        to attach the submitted command's cell_id and text.
        """
        handler = self._action_handler(action)
        extra = handler() or {}
        self._publish_action(action, key, extra)

    def _submit(self) -> Optional[dict[str, object]]:
        """Commit the current input buffer and return submission extras.

        If the buffer begins with `:`, the router treats it as a meta-
        command (`:settings`, `:save`, `:quit`), consumes it without
        writing it back into the cell, and reports the command name via
        the ACTION_INVOKED payload so observers can trace what
        happened.
        """
        buffer = self._cursor.focus.input_buffer
        meta = _parse_meta_command(buffer)
        if meta is not None:
            if meta in AMBIENT_META_COMMANDS:
                self._cursor.reset_input_buffer()
            else:
                self._cursor.abandon_input_mode()
            self._handle_meta_command(meta)
            return {"meta_command": meta}
        cell = self._cursor.submit()
        if cell is None:
            return None
        return {"cell_id": cell.cell_id, "command": cell.command}

    def _handle_meta_command(self, command: str) -> None:
        """Dispatch a parsed meta-command (without its leading `:`).

        `save` and `quit` are handled by the Application via the
        ACTION_INVOKED payload's `meta_command` key, so the router
        itself intentionally does nothing for them here.
        """
        if command == "settings":
            self._open_settings()
        elif command == "help":
            self._publish_help()

    def _publish_help(self) -> None:
        """Emit HELP_REQUESTED so the renderer and audio bank can react."""
        publish_event(
            self._bus,
            EventType.HELP_REQUESTED,
            {"lines": list(HELP_LINES)},
            source=self.SOURCE,
        )

    def _open_settings(self) -> None:
        """Enter SETTINGS mode and open a controller session if available."""
        if self._settings_controller is None:
            return
        self._settings_controller.open()
        self._cursor.enter_settings_mode()

    def _close_settings(self) -> None:
        """Close the controller and return to NOTEBOOK mode."""
        if self._settings_controller is None:
            return
        self._settings_controller.close()
        self._cursor.exit_settings_mode()

    def _save_settings(self) -> None:
        """Persist the in-progress bank; no-op without a save path."""
        if self._settings_controller is None:
            return
        if self._settings_controller.save_path is None:
            return
        self._settings_controller.save()

    def _settings_prev(self) -> None:
        """Move the settings cursor one step backward."""
        if self._settings_controller is not None:
            self._settings_controller.prev()

    def _settings_next(self) -> None:
        """Move the settings cursor one step forward."""
        if self._settings_controller is not None:
            self._settings_controller.next()

    def _settings_descend(self) -> None:
        """Drop one level deeper into the settings hierarchy."""
        if self._settings_controller is not None:
            self._settings_controller.descend()

    def _settings_ascend(self) -> None:
        """Rise one level; at the top, close the editor."""
        if self._settings_controller is None:
            return
        if not self._settings_controller.ascend():
            self._close_settings()

    def _settings_begin_edit(self) -> None:
        """Start composing a replacement value for the focused field."""
        if self._settings_controller is not None:
            self._settings_controller.begin_edit()

    def _settings_edit_commit(self) -> Optional[dict[str, object]]:
        """Apply the in-progress edit buffer; surface errors in the payload."""
        if self._settings_controller is None:
            return None
        try:
            self._settings_controller.commit_edit()
            return {"ok": True}
        except SettingsEditorError as exc:
            return {"ok": False, "error": str(exc)}

    def _settings_edit_cancel(self) -> None:
        """Discard the in-progress edit buffer."""
        if self._settings_controller is not None:
            self._settings_controller.cancel_edit()

    def _settings_edit_backspace(self) -> None:
        """Remove the last character from the edit buffer."""
        if self._settings_controller is not None:
            self._settings_controller.backspace_edit()

    def _action_handler(self, action: str) -> ActionHandler:
        """Map an action name to a zero-argument callable.

        Handlers return an optional dict of extra payload fields. Only
        "submit" contributes extras today; every other handler runs a
        side effect and returns None via _void, which discards whatever
        the underlying cursor method returned.
        """
        handlers: dict[str, ActionHandler] = {
            "move_up": _void(self._cursor.move_up),
            "move_down": _void(self._cursor.move_down),
            "move_to_top": _void(self._cursor.move_to_top),
            "move_to_bottom": _void(self._cursor.move_to_bottom),
            "enter_input": _void(self._cursor.enter_input_mode),
            "exit_input": _void(self._cursor.exit_input_mode),
            "new_cell": _void(self._cursor.new_cell),
            "backspace": _void(self._cursor.backspace),
            "view_output": _void(self._cursor.view_output_mode),
            "exit_output": _void(self._cursor.exit_output_mode),
            "submit": self._submit,
            "output_line_up": lambda: self._with_output_cursor(
                lambda oc: oc.move_line_up()
            ),
            "output_line_down": lambda: self._with_output_cursor(
                lambda oc: oc.move_line_down()
            ),
            "output_page_up": lambda: self._with_output_cursor(
                lambda oc: oc.move_page_up()
            ),
            "output_page_down": lambda: self._with_output_cursor(
                lambda oc: oc.move_page_down()
            ),
            "output_to_start": lambda: self._with_output_cursor(
                lambda oc: oc.move_to_start()
            ),
            "output_to_end": lambda: self._with_output_cursor(
                lambda oc: oc.move_to_end()
            ),
            "open_settings": _void(self._open_settings),
            "settings_prev": _void(self._settings_prev),
            "settings_next": _void(self._settings_next),
            "settings_descend": _void(self._settings_descend),
            "settings_ascend": _void(self._settings_ascend),
            "settings_begin_edit": _void(self._settings_begin_edit),
            "settings_save": _void(self._save_settings),
            "settings_close": _void(self._close_settings),
            "settings_edit_commit": self._settings_edit_commit,
            "settings_edit_cancel": _void(self._settings_edit_cancel),
            "settings_edit_backspace": _void(self._settings_edit_backspace),
        }
        if action not in handlers:
            raise KeyError(f"Unknown action: {action}")
        return handlers[action]

    def _with_output_cursor(
        self,
        operation: Callable[[OutputCursor], object],
    ) -> None:
        """Run an output-cursor operation only if a cursor is attached."""
        if self._output_cursor is None:
            return
        operation(self._output_cursor)

    def _publish_key(self, key: Key) -> None:
        """Publish a KEY_PRESSED event describing the keystroke."""
        publish_event(
            self._bus,
            EventType.KEY_PRESSED,
            {
                "name": key.name,
                "char": key.char,
                "modifiers": sorted(m.value for m in key.modifiers),
            },
            source=self.SOURCE,
        )

    def _publish_action(
        self,
        action: str,
        key: Key,
        extra: dict[str, object],
    ) -> None:
        """Publish an ACTION_INVOKED event with action name and context."""
        payload: dict[str, object] = {
            "action": action,
            "focus_mode": self._cursor.focus.mode.value,
            "cell_id": self._cursor.focus.cell_id,
            "key_name": key.name,
        }
        payload.update(extra)
        publish_event(
            self._bus,
            EventType.ACTION_INVOKED,
            payload,
            source=self.SOURCE,
        )


def _parse_meta_command(buffer: str) -> Optional[str]:
    """Return a meta-command name when buffer is `:<name>` (trimmed), else None."""
    stripped = buffer.strip()
    if not stripped.startswith(":"):
        return None
    name = stripped[1:].strip()
    if name in META_COMMANDS:
        return name
    return None
