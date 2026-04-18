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
        Ctrl+O opens the captured output of the focused cell.

    INPUT mode:
        Backspace deletes the last character,
        Enter submits the current command,
        Escape commits and returns to notebook mode.

    OUTPUT mode:
        Up/Down walk one line at a time,
        PageUp/PageDown jump a page,
        Home/End jump to the first or last captured line,
        Escape returns to notebook mode.
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
    }


class InputRouter:
    """Dispatches keystrokes to notebook actions."""

    SOURCE = "input_router"

    def __init__(
        self,
        cursor: NotebookCursor,
        bus: EventBus,
        bindings: Optional[BindingMap] = None,
        output_cursor: Optional[OutputCursor] = None,
    ) -> None:
        """Attach the router to a cursor, event bus, and optional output cursor."""
        self._cursor = cursor
        self._bus = bus
        self._bindings = bindings if bindings is not None else default_bindings()
        self._output_cursor = output_cursor

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
        """Commit the current input buffer and return submission extras."""
        cell = self._cursor.submit()
        if cell is None:
            return None
        return {"cell_id": cell.cell_id, "command": cell.command}

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
