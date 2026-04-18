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

Default bindings cover the Phase 4 goal: non-visual navigation
between input cells and basic in-place command editing. They can be
overridden or extended by passing a custom bindings map.
"""

from __future__ import annotations

from typing import Callable, Optional

from asat import keys as kc
from asat.event_bus import EventBus
from asat.events import Event, EventType
from asat.keys import Key, Modifier
from asat.notebook import FocusMode, NotebookCursor


BindingMap = dict[FocusMode, dict[Key, str]]


def default_bindings() -> BindingMap:
    """Return a fresh copy of the default keystroke map.

    NOTEBOOK mode:
        Up/Down move between cells, Home/End jump to ends,
        Enter enters input mode on the focused cell,
        Ctrl+N appends a fresh cell and enters input mode.

    INPUT mode:
        Backspace deletes the last character,
        Enter submits the current command,
        Escape commits and returns to notebook mode.
    """
    return {
        FocusMode.NOTEBOOK: {
            kc.UP: "move_up",
            kc.DOWN: "move_down",
            kc.HOME: "move_to_top",
            kc.END: "move_to_bottom",
            kc.ENTER: "enter_input",
            Key.combo("n", Modifier.CTRL): "new_cell",
        },
        FocusMode.INPUT: {
            kc.BACKSPACE: "backspace",
            kc.ENTER: "submit",
            kc.ESCAPE: "exit_input",
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
    ) -> None:
        """Attach the router to a cursor and an event bus."""
        self._cursor = cursor
        self._bus = bus
        self._bindings = bindings if bindings is not None else default_bindings()

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
        """Run a named action and publish ACTION_INVOKED for it."""
        payload_extra: dict[str, object] = {}
        if action == "submit":
            cell = self._cursor.submit()
            if cell is not None:
                payload_extra = {
                    "cell_id": cell.cell_id,
                    "command": cell.command,
                }
        else:
            self._action_handler(action)()
        self._publish_action(action, key, payload_extra)

    def _action_handler(self, action: str) -> Callable[[], None]:
        """Map an action name to a zero-argument callable on the cursor."""
        handlers: dict[str, Callable[[], None]] = {
            "move_up": lambda: self._cursor.move_up(),
            "move_down": lambda: self._cursor.move_down(),
            "move_to_top": lambda: self._cursor.move_to_top(),
            "move_to_bottom": lambda: self._cursor.move_to_bottom(),
            "enter_input": lambda: self._cursor.enter_input_mode(),
            "exit_input": lambda: self._cursor.exit_input_mode(),
            "new_cell": lambda: self._cursor.new_cell(),
            "backspace": lambda: self._cursor.backspace(),
        }
        if action not in handlers:
            raise KeyError(f"Unknown action: {action}")
        return handlers[action]

    def _publish_key(self, key: Key) -> None:
        """Publish a KEY_PRESSED event describing the keystroke."""
        self._bus.publish(
            Event(
                event_type=EventType.KEY_PRESSED,
                payload={
                    "name": key.name,
                    "char": key.char,
                    "modifiers": sorted(m.value for m in key.modifiers),
                },
                source=self.SOURCE,
            )
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
        self._bus.publish(
            Event(
                event_type=EventType.ACTION_INVOKED,
                payload=payload,
                source=self.SOURCE,
            )
        )
