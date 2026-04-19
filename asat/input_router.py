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

import difflib
import os
import re
from typing import Callable, Optional

from asat import keys as kc
from asat.actions import ActionContext, ActionMenu
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.help_topics import HELP_TOPICS, lookup as lookup_help_topic, topic_names
from asat.keys import Key, Modifier
from asat.notebook import FocusMode, NotebookCursor
from asat.output_cursor import OutputCursor
from asat.settings_controller import SettingsController
from asat.settings_editor import ResetScope, SettingsEditorError


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
        Ctrl+, (comma) opens the settings editor,
        `d` deletes the focused cell, `y` duplicates it,
        Alt+Up / Alt+Down move the focused cell within the session,
        F2 (or Ctrl+.) opens the contextual actions menu.

    INPUT mode:
        Backspace deletes the character before the caret,
        Delete removes the character under the caret,
        Left / Right / Home / End move the caret within the buffer
        (Ctrl+A / Ctrl+E also jump to start / end),
        Ctrl+W kills the word before the caret,
        Ctrl+U kills from the start of the buffer to the caret,
        Ctrl+K kills from the caret to the end of the buffer,
        Enter submits the current command,
        Escape commits and returns to notebook mode.
        Lines beginning with `:` are treated as meta-commands
        (see META_COMMANDS) instead of being handed to the kernel.

    OUTPUT mode:
        Up/Down walk one line at a time,
        PageUp/PageDown jump a page,
        Home/End jump to the first or last captured line,
        `/` opens the search composer (typed chars narrow matches
        live, Enter commits, Escape cancels), `n` / `N` cycle to
        next / previous match,
        `g` opens the goto-line composer (type a line number then
        Enter),
        F2 (or Ctrl+.) opens the contextual actions menu,
        Escape returns to notebook mode.

    SETTINGS mode:
        Up/Down move between records / fields,
        Right / Enter descend one level,
        Left ascends one level,
        `e` begins editing the focused field,
        `/` opens the cross-section search composer (typed chars
        narrow matches live, Enter commits, Escape restores the
        pre-search cursor), `n` / `N` cycle through matches,
        Ctrl+S saves the bank, Ctrl+Q closes the editor,
        Ctrl+R opens a reset-to-defaults confirmation for the
        cursor's current scope (Enter confirms, Escape cancels),
        Escape ascends or (at the top level) closes.
    """
    menu_open = Key.combo(".", Modifier.CTRL)
    repeat_narration = Key.combo("r", Modifier.CTRL)
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
            Key.printable("d"): "delete_cell",
            Key.printable("y"): "duplicate_cell",
            Key.special("up", Modifier.ALT): "move_cell_up",
            Key.special("down", Modifier.ALT): "move_cell_down",
            kc.F2: "open_action_menu",
            menu_open: "open_action_menu",
            repeat_narration: "repeat_last_narration",
        },
        FocusMode.INPUT: {
            kc.BACKSPACE: "backspace",
            kc.DELETE: "delete_forward",
            kc.LEFT: "cursor_left",
            kc.RIGHT: "cursor_right",
            kc.HOME: "cursor_home",
            kc.END: "cursor_end",
            Key.combo("a", Modifier.CTRL): "cursor_home",
            Key.combo("e", Modifier.CTRL): "cursor_end",
            Key.combo("w", Modifier.CTRL): "delete_word_left",
            Key.combo("u", Modifier.CTRL): "delete_to_start",
            Key.combo("k", Modifier.CTRL): "delete_to_end",
            kc.ENTER: "submit",
            kc.ESCAPE: "exit_input",
            kc.F2: "open_action_menu",
            menu_open: "open_action_menu",
            repeat_narration: "repeat_last_narration",
        },
        FocusMode.OUTPUT: {
            kc.UP: "output_line_up",
            kc.DOWN: "output_line_down",
            kc.PAGE_UP: "output_page_up",
            kc.PAGE_DOWN: "output_page_down",
            kc.HOME: "output_to_start",
            kc.END: "output_to_end",
            kc.ESCAPE: "exit_output",
            Key.printable("/"): "output_search_begin",
            Key.printable("g"): "output_goto_begin",
            Key.printable("n"): "output_search_next",
            Key.printable("N"): "output_search_prev",
            kc.F2: "open_action_menu",
            menu_open: "open_action_menu",
        },
        FocusMode.SETTINGS: {
            kc.UP: "settings_prev",
            kc.DOWN: "settings_next",
            kc.LEFT: "settings_ascend",
            kc.RIGHT: "settings_descend",
            kc.ENTER: "settings_descend",
            kc.ESCAPE: "settings_ascend",
            Key.printable("e"): "settings_begin_edit",
            Key.printable("/"): "settings_search_begin",
            Key.printable("n"): "settings_search_next",
            Key.printable("N"): "settings_search_prev",
            Key.combo("s", Modifier.CTRL): "settings_save",
            Key.combo("q", Modifier.CTRL): "settings_close",
            Key.combo("z", Modifier.CTRL): "settings_undo",
            Key.combo("y", Modifier.CTRL): "settings_redo",
            Key.combo("r", Modifier.CTRL): "settings_reset_begin",
        },
    }


# Meta-commands recognised in INPUT mode when the buffer starts with `:`.
# A meta-command short-circuits the normal submit path: the cell is not
# handed to the kernel and the input buffer is discarded (not written
# back into the cell).
META_COMMANDS: tuple[str, ...] = (
    "settings",
    "save",
    "quit",
    "help",
    "delete",
    "duplicate",
    "pwd",
    "commands",
    "reset",
    "welcome",
    "repeat",
)

# "Ambient" meta-commands do their job without taking focus away from
# INPUT mode. `:help` prints a cheat sheet; `:save` persists the
# session; `:pwd` and `:commands` emit an informational HELP_REQUESTED
# event. In each case the natural next action is to keep typing, so
# the router clears the in-progress buffer but leaves the user in INPUT
# mode. Commands NOT in this set (today: `:settings`, `:quit`)
# inherently require a mode change and go through `abandon_input_mode`.
AMBIENT_META_COMMANDS: frozenset[str] = frozenset(
    {"help", "save", "pwd", "commands", "welcome", "repeat"}
)

# `:name optional-argument` — case-insensitive in the name, everything
# after the first whitespace run is the trailing arg (already stripped).
_META_NAME_RE = re.compile(r"^:([A-Za-z][A-Za-z0-9_-]*)\s*(.*)$")


# The cheat-sheet lines a `:help` meta-command should surface. Kept in
# one place so the renderer, the narration, and the docs can all read
# from the same source of truth.
HELP_LINES: tuple[str, ...] = (
    "ASAT quick reference.",
    "NOTEBOOK:  Up/Down walk cells, Enter type, Ctrl+N new, Ctrl+O output, Ctrl+, settings.",
    "           d delete, y duplicate, Alt+Up/Down reorder.",
    "INPUT:     Enter submits, Escape leaves without running.",
    "           Backspace/Delete cut, Left/Right walk, Home/End jump (or Ctrl+A/E).",
    "           Ctrl+W kills word, Ctrl+U kills to start, Ctrl+K kills to end.",
    "OUTPUT:    Up/Down step lines, PageUp/PageDown page, Escape leaves.",
    "           / search (type query, Enter commits), n / N next / prev hit, g jump-to-line.",
    "SETTINGS:  Up/Down walk, Right/Enter descend, Left/Escape ascend, e edit, Ctrl+S save, Ctrl+Q close.",
    "           / search (cross-section; Enter commits, Escape restores), n / N cycle matches.",
    "           Ctrl+Z undo, Ctrl+Y redo edits in the order you made them.",
    "           Ctrl+R resets to defaults at cursor scope (Enter confirms, Escape cancels).",
    "Menu:      F2 (or Ctrl+.) opens contextual actions; Up/Down walk, Enter invokes, Escape closes.",
    "Meta:      :help, :settings, :save, :quit, :delete, :duplicate, :pwd, :commands, :reset, :welcome, :repeat.",
    "           `:help topics` lists focused tours; `:help <topic>` narrates one (navigation, cells, settings, audio, search, meta).",
    "           `:welcome` replays the first-run tour; `:repeat` (or Ctrl+R in notebook/input) re-speaks the last narration.",
    "           Meta-commands are case-insensitive and accept a trailing argument.",
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
        action_menu: Optional[ActionMenu] = None,
    ) -> None:
        """Attach the router to a cursor, event bus, and optional cursors.

        When a `settings_controller` is supplied, the router gains the
        `open_settings` action (bound to Ctrl+, by default), the
        SETTINGS focus-mode key map, and recognition of `:settings`
        in INPUT mode. When it is absent, those actions silently no-op
        so a test or embedding that ignores audio still works.

        When an `action_menu` is supplied, F2 (and Ctrl+.) open the
        contextual actions menu from NOTEBOOK / INPUT / OUTPUT mode.
        While the menu is open, Up/Down cycle items, Enter invokes the
        focused item, and Escape closes the menu without activating.
        Without an `action_menu` the `open_action_menu` action is a
        silent no-op.
        """
        self._cursor = cursor
        self._bus = bus
        self._bindings = bindings if bindings is not None else default_bindings()
        self._output_cursor = output_cursor
        self._settings_controller = settings_controller
        self._action_menu = action_menu

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
        if self._action_menu is not None and self._action_menu.is_open:
            return self._dispatch_menu_key(key)
        mode = self._cursor.focus.mode
        if mode == FocusMode.SETTINGS and self._settings_active_editing():
            return self._dispatch_settings_edit_key(key)
        if mode == FocusMode.SETTINGS and self._settings_active_searching():
            return self._dispatch_settings_search_key(key)
        if mode == FocusMode.SETTINGS and self._settings_active_resetting():
            return self._dispatch_settings_reset_key(key)
        if mode == FocusMode.OUTPUT and self._output_composing():
            return self._dispatch_output_composer_key(key)
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

    def _dispatch_menu_key(self, key: Key) -> Optional[str]:
        """Route keys while the actions menu is open.

        Up / Down cycle the focused item, Enter invokes it (which then
        closes the menu), and Escape closes without activating. Every
        other key is swallowed so they do not leak into the underlying
        focus mode — opening the menu is modal by design.
        """
        assert self._action_menu is not None
        if key == kc.UP:
            self._invoke("menu_prev", key)
            return "menu_prev"
        if key == kc.DOWN:
            self._invoke("menu_next", key)
            return "menu_next"
        if key == kc.ENTER:
            self._invoke("menu_activate", key)
            return "menu_activate"
        if key == kc.ESCAPE:
            self._invoke("menu_close", key)
            return "menu_close"
        return None

    def _output_composing(self) -> bool:
        """Return True when the output cursor is in a search/goto composer."""
        return (
            self._output_cursor is not None
            and self._output_cursor.composer_mode is not None
        )

    def _dispatch_output_composer_key(self, key: Key) -> Optional[str]:
        """Route keys while the user is typing a `/` search or `g` line jump.

        Printable characters extend the query / line number, Backspace
        trims, Enter commits, Escape cancels. Every other key is
        swallowed so arrow motions don't leak through and silently
        cancel the composer.
        """
        assert self._output_cursor is not None
        if key == kc.ENTER:
            self._invoke("output_composer_commit", key)
            return "output_composer_commit"
        if key == kc.ESCAPE:
            self._invoke("output_composer_cancel", key)
            return "output_composer_cancel"
        if key == kc.BACKSPACE:
            self._invoke("output_composer_backspace", key)
            return "output_composer_backspace"
        if key.is_printable() and key.char is not None:
            self._output_cursor.extend_composer(key.char)
            self._publish_action(
                "output_composer_extend",
                key,
                {
                    "char": key.char,
                    "mode": self._output_cursor.composer_mode,
                    "query": self._output_cursor.composer_buffer,
                },
            )
            return "output_composer_extend"
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

    def _settings_active_searching(self) -> bool:
        """Return True when the settings controller is composing a search."""
        return (
            self._settings_controller is not None
            and self._settings_controller.is_open
            and self._settings_controller.searching
        )

    def _dispatch_settings_search_key(self, key: Key) -> Optional[str]:
        """Route keys while the user is typing a `/` search query.

        Printable characters (including `/`) extend the query so a
        user can search for a literal slash; Backspace trims; Enter
        commits and leaves the cursor on the current match; Escape
        cancels and restores the pre-search cursor. Every other key
        is swallowed so arrow motions don't silently dismiss the
        overlay.
        """
        assert self._settings_controller is not None
        if key == kc.ENTER:
            self._invoke("settings_search_commit", key)
            return "settings_search_commit"
        if key == kc.ESCAPE:
            self._invoke("settings_search_cancel", key)
            return "settings_search_cancel"
        if key == kc.BACKSPACE:
            self._invoke("settings_search_backspace", key)
            return "settings_search_backspace"
        if key.is_printable() and key.char is not None:
            self._settings_controller.extend_search(key.char)
            self._publish_action(
                "settings_search_extend",
                key,
                {"char": key.char, "query": self._settings_controller.search_buffer},
            )
            return "settings_search_extend"
        return None

    def _settings_active_resetting(self) -> bool:
        """Return True while the reset confirmation sub-mode is active."""
        return (
            self._settings_controller is not None
            and self._settings_controller.is_open
            and self._settings_controller.resetting
        )

    def _dispatch_settings_reset_key(self, key: Key) -> Optional[str]:
        """Route keys while the user is being asked to confirm a reset.

        Enter confirms and applies the reset; Escape cancels and
        leaves the bank untouched. Every other key is swallowed so
        stray input can neither silently confirm nor dismiss the
        prompt — a user has to acknowledge the confirmation
        deliberately.
        """
        assert self._settings_controller is not None
        if key == kc.ENTER:
            self._invoke("settings_reset_confirm", key)
            return "settings_reset_confirm"
        if key == kc.ESCAPE:
            self._invoke("settings_reset_cancel", key)
            return "settings_reset_cancel"
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
        command, consumes it without writing it back into the cell,
        and reports the command name via the ACTION_INVOKED payload so
        observers can trace what happened. Unknown `:xxx` lines are
        also intercepted: the router emits a HELP_REQUESTED hint (with
        a difflib-powered typo suggestion when one is plausible) and
        leaves the user in INPUT mode so they can correct and retry.
        """
        buffer = self._cursor.focus.input_buffer
        parsed = _parse_meta_command(buffer)
        if parsed is not None:
            command, arg, raw_name = parsed
            if command is None:
                self._cursor.reset_input_buffer()
                self._publish_unknown_meta(raw_name)
                extras: dict[str, object] = {
                    "meta_command": None,
                    "meta_unknown": raw_name,
                }
                suggestion = _suggest_meta_command(raw_name)
                if suggestion is not None:
                    extras["meta_suggestion"] = suggestion
                return extras
            if command in AMBIENT_META_COMMANDS:
                self._cursor.reset_input_buffer()
            else:
                self._cursor.abandon_input_mode()
            self._handle_meta_command(command, arg)
            extras = {"meta_command": command}
            if arg:
                extras["meta_argument"] = arg
            return extras
        cell = self._cursor.submit()
        if cell is None:
            return None
        return {"cell_id": cell.cell_id, "command": cell.command}

    def _handle_meta_command(self, command: str, argument: str) -> None:
        """Dispatch a parsed meta-command (without its leading `:`).

        `save` and `quit` are handled by the Application via the
        ACTION_INVOKED payload's `meta_command` key, so the router
        itself intentionally does nothing for them here. The trailing
        `argument` is forwarded only where it has a defined meaning;
        commands that do not read it simply ignore it.
        """
        if command == "settings":
            self._open_settings()
        elif command == "help":
            self._publish_help(argument)
        elif command == "delete":
            self._cursor.delete_focused_cell()
        elif command == "duplicate":
            self._cursor.duplicate_focused_cell()
        elif command == "pwd":
            self._publish_pwd()
        elif command == "commands":
            self._publish_commands()
        elif command == "reset":
            self._handle_meta_reset(argument)
        # `repeat`, `save`, `quit`, `welcome` are handled by the
        # Application via the ACTION_INVOKED payload's `meta_command`
        # key — no router-side dispatch needed here.

    def _handle_meta_reset(self, argument: str) -> None:
        """Open settings mode and begin a reset confirmation.

        `:reset bank` / `:reset all` start a bank-level confirmation.
        Any other argument (or none) surfaces a HELP_REQUESTED hint
        instead of silently picking a scope — from INPUT mode there
        is no cursor-level context, so asking the user to be explicit
        avoids the "oops, I reset the wrong thing" failure mode. For
        finer-grained scopes, the user is directed to press Ctrl+R
        inside SETTINGS mode.
        """
        scope = _parse_reset_scope(argument)
        if scope is not ResetScope.BANK:
            publish_event(
                self._bus,
                EventType.HELP_REQUESTED,
                {
                    "lines": [
                        "`:reset` from INPUT mode only supports `:reset bank` "
                        "(also `:reset all`) for a whole-bank reset.",
                        "For finer-grained resets, press Ctrl+, to open "
                        "settings then Ctrl+R at the record, field, or "
                        "section you want to restore.",
                    ],
                },
                source=self.SOURCE,
            )
            return
        if self._settings_controller is None:
            return
        self._open_settings()
        self._settings_controller.begin_reset(ResetScope.BANK)

    def _publish_pwd(self) -> None:
        """Announce the current working directory via HELP_REQUESTED."""
        publish_event(
            self._bus,
            EventType.HELP_REQUESTED,
            {"lines": [f"Working directory: {os.getcwd()}"]},
            source=self.SOURCE,
        )

    def _publish_commands(self) -> None:
        """List every recognised meta-command via HELP_REQUESTED."""
        lines = ["Meta-commands (type `:name` in INPUT mode, then Enter):"]
        lines.extend(f"  :{name}" for name in META_COMMANDS)
        publish_event(
            self._bus,
            EventType.HELP_REQUESTED,
            {"lines": lines},
            source=self.SOURCE,
        )

    def _publish_unknown_meta(self, raw_name: str) -> None:
        """Surface a typo-suggest hint for an unrecognised `:xxx` line."""
        suggestion = _suggest_meta_command(raw_name)
        if suggestion is not None:
            lines = [
                f"Unknown meta-command `:{raw_name}` — "
                f"did you mean `:{suggestion}`?",
                "Line ignored. Type `:commands` to list every meta-command.",
            ]
        else:
            lines = [
                f"Unknown meta-command `:{raw_name}`. Line ignored.",
                "Type `:commands` to list every meta-command.",
            ]
        publish_event(
            self._bus,
            EventType.HELP_REQUESTED,
            {"lines": lines},
            source=self.SOURCE,
        )

    def _publish_help(self, argument: str = "") -> None:
        """Emit HELP_REQUESTED with either the cheat sheet or a topic tour.

        No argument → the full cheat sheet (HELP_LINES), matching the
        pre-F38 behaviour. `:help topics` → a listing of every
        available topic. `:help <topic>` → that topic's micro-tour.
        Unknown topic → a typo-suggestion hint built from
        `difflib.get_close_matches` over the topic names, so the user
        stays in INPUT mode and can correct-and-retry.
        """
        topic = argument.strip().lower()
        if not topic:
            publish_event(
                self._bus,
                EventType.HELP_REQUESTED,
                {"lines": list(HELP_LINES)},
                source=self.SOURCE,
            )
            return
        if topic == "topics":
            lines = ["Available `:help <topic>` tours:"]
            lines.extend(f"  :help {name}" for name in topic_names())
            publish_event(
                self._bus,
                EventType.HELP_REQUESTED,
                {"lines": lines, "help_topic": "topics"},
                source=self.SOURCE,
            )
            return
        body = lookup_help_topic(topic)
        if body is not None:
            publish_event(
                self._bus,
                EventType.HELP_REQUESTED,
                {"lines": list(body), "help_topic": topic},
                source=self.SOURCE,
            )
            return
        lines = [f"Unknown `:help` topic `{argument.strip()}`."]
        suggestion = difflib.get_close_matches(topic, topic_names(), n=1)
        if suggestion:
            lines.append(f"Did you mean `:help {suggestion[0]}`?")
        lines.append("Type `:help topics` to list every available topic.")
        publish_event(
            self._bus,
            EventType.HELP_REQUESTED,
            {"lines": lines, "help_topic_unknown": argument.strip()},
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

    def _settings_undo(self) -> None:
        """Revert the most recent edit via the settings controller."""
        if self._settings_controller is not None:
            self._settings_controller.undo()

    def _settings_redo(self) -> None:
        """Re-apply the most recently undone edit via the settings controller."""
        if self._settings_controller is not None:
            self._settings_controller.redo()

    def _settings_search_begin(self) -> Optional[dict[str, object]]:
        """Open the `/` search overlay; report whether it started."""
        if self._settings_controller is None:
            return None
        started = self._settings_controller.begin_search()
        return {"opened": started}

    def _settings_search_commit(self) -> Optional[dict[str, object]]:
        """Apply the in-progress search; surface the query + match count."""
        if self._settings_controller is None:
            return None
        editor = self._settings_controller.editor
        query = editor.search_buffer
        match_count = editor.search_match_count
        self._settings_controller.commit_search()
        return {"query": query, "match_count": match_count}

    def _settings_search_cancel(self) -> None:
        """Discard the in-progress search and restore the cursor."""
        if self._settings_controller is not None:
            self._settings_controller.cancel_search()

    def _settings_search_backspace(self) -> None:
        """Trim the last character from the search buffer."""
        if self._settings_controller is not None:
            self._settings_controller.backspace_search()

    def _settings_reset_begin(
        self, scope: Optional[ResetScope] = None
    ) -> Optional[dict[str, object]]:
        """Open the reset confirmation; report the scope the editor chose.

        When `scope` is None (Ctrl+R with no argument, or `:reset`
        with no scope), the controller picks the cursor-level default
        — FIELD at FIELD, RECORD at RECORD, SECTION at SECTION. For
        explicit scope arguments (`:reset record`, `:reset bank`, …)
        the caller passes the parsed enum value through.
        """
        if self._settings_controller is None:
            return None
        started = self._settings_controller.begin_reset(scope)
        payload: dict[str, object] = {"opened": started}
        active_scope = self._settings_controller.reset_scope
        if active_scope is not None:
            payload["scope"] = active_scope.value
        elif scope is not None:
            payload["scope"] = scope.value
        return payload

    def _settings_reset_confirm(self) -> Optional[dict[str, object]]:
        """Confirm the pending reset; report whether the bank changed."""
        if self._settings_controller is None:
            return None
        scope = self._settings_controller.reset_scope
        applied = self._settings_controller.confirm_reset()
        payload: dict[str, object] = {"applied": applied}
        if scope is not None:
            payload["scope"] = scope.value
        return payload

    def _settings_reset_cancel(self) -> None:
        """Cancel the pending reset; leave the bank untouched."""
        if self._settings_controller is not None:
            self._settings_controller.cancel_reset()

    def _settings_search_next(self) -> Optional[dict[str, object]]:
        """Cycle to the next match; no-op without prior results."""
        if self._settings_controller is None:
            return None
        editor = self._settings_controller.editor
        matched = editor.next_search_match()
        return {"matched": matched}

    def _settings_search_prev(self) -> Optional[dict[str, object]]:
        """Cycle to the previous match; no-op without prior results."""
        if self._settings_controller is None:
            return None
        editor = self._settings_controller.editor
        matched = editor.prev_search_match()
        return {"matched": matched}

    def _open_action_menu(self) -> Optional[dict[str, object]]:
        """Open the contextual actions menu against the current focus.

        Snapshots the current focus (mode + cell) plus, in OUTPUT mode,
        the focused line number / stream / text so OUTPUT providers can
        offer `Copy focused line`. A no-op when no `action_menu` was
        supplied or when the SETTINGS editor is open (the settings UI
        is modal and its keys would collide with the menu's).
        """
        if self._action_menu is None:
            return None
        mode = self._cursor.focus.mode
        if mode == FocusMode.SETTINGS:
            return None
        context = self._build_action_context(mode)
        self._action_menu.open(context)
        return {"item_count": len(self._action_menu.items)}

    def _build_action_context(self, mode: FocusMode) -> ActionContext:
        """Assemble the ActionContext consumed by ActionCatalog providers."""
        cell_id = self._cursor.focus.cell_id
        line_number: Optional[int] = None
        line_stream: Optional[str] = None
        line_text: Optional[str] = None
        if mode == FocusMode.OUTPUT and self._output_cursor is not None:
            current = self._output_cursor.current_line()
            if current is not None:
                line_number = current.line_number
                line_stream = current.stream
                line_text = current.text
        return ActionContext(
            focus_mode=mode,
            cell_id=cell_id,
            line_number=line_number,
            line_stream=line_stream,
            line_text=line_text,
        )

    def _menu_prev(self) -> None:
        """Move the menu focus to the previous item (no-op when closed)."""
        if self._action_menu is not None:
            self._action_menu.focus_prev()

    def _menu_next(self) -> None:
        """Move the menu focus to the next item (no-op when closed)."""
        if self._action_menu is not None:
            self._action_menu.focus_next()

    def _menu_activate(self) -> None:
        """Invoke the focused menu item; the menu closes itself."""
        if self._action_menu is not None:
            self._action_menu.activate()

    def _menu_close(self) -> None:
        """Close the menu without invoking anything."""
        if self._action_menu is not None:
            self._action_menu.close()

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
            "cursor_left": _void(self._cursor.cursor_left),
            "cursor_right": _void(self._cursor.cursor_right),
            "cursor_home": _void(self._cursor.cursor_home),
            "cursor_end": _void(self._cursor.cursor_end),
            "delete_forward": _void(self._cursor.delete_forward),
            "delete_word_left": _void(self._cursor.delete_word_left),
            "delete_to_start": _void(self._cursor.delete_to_start),
            "delete_to_end": _void(self._cursor.delete_to_end),
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
            "settings_undo": _void(self._settings_undo),
            "settings_redo": _void(self._settings_redo),
            "settings_search_begin": self._settings_search_begin,
            "settings_search_commit": self._settings_search_commit,
            "settings_search_cancel": _void(self._settings_search_cancel),
            "settings_search_backspace": _void(self._settings_search_backspace),
            "settings_search_extend": lambda: None,
            "settings_search_next": self._settings_search_next,
            "settings_search_prev": self._settings_search_prev,
            "settings_reset_begin": self._settings_reset_begin,
            "settings_reset_confirm": self._settings_reset_confirm,
            "settings_reset_cancel": _void(self._settings_reset_cancel),
            "open_action_menu": self._open_action_menu,
            "menu_prev": _void(self._menu_prev),
            "menu_next": _void(self._menu_next),
            "menu_activate": _void(self._menu_activate),
            "menu_close": _void(self._menu_close),
            "delete_cell": _void(self._cursor.delete_focused_cell),
            "duplicate_cell": _void(self._cursor.duplicate_focused_cell),
            "move_cell_up": _void(lambda: self._cursor.move_focused_cell(-1)),
            "move_cell_down": _void(lambda: self._cursor.move_focused_cell(+1)),
            "output_search_begin": self._output_search_begin,
            "output_goto_begin": self._output_goto_begin,
            "output_search_next": self._output_search_next,
            "output_search_prev": self._output_search_prev,
            "output_composer_extend": lambda: None,
            "output_composer_backspace": self._output_composer_backspace,
            "output_composer_commit": self._output_composer_commit,
            "output_composer_cancel": self._output_composer_cancel,
            "repeat_last_narration": lambda: None,
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

    def _output_search_begin(self) -> Optional[dict[str, object]]:
        """Open the `/` search composer on the attached output cursor."""
        if self._output_cursor is None:
            return None
        started = self._output_cursor.begin_search()
        return {"opened": started}

    def _output_goto_begin(self) -> Optional[dict[str, object]]:
        """Open the `g` line-jump composer on the attached output cursor."""
        if self._output_cursor is None:
            return None
        started = self._output_cursor.begin_goto()
        return {"opened": started}

    def _output_search_next(self) -> Optional[dict[str, object]]:
        """Cycle to the next search hit (no-op without prior matches)."""
        if self._output_cursor is None:
            return None
        line = self._output_cursor.next_match()
        if line is None:
            return {"matched": False}
        return {"matched": True, "line_number": line.line_number}

    def _output_search_prev(self) -> Optional[dict[str, object]]:
        """Cycle to the previous search hit (no-op without prior matches)."""
        if self._output_cursor is None:
            return None
        line = self._output_cursor.prev_match()
        if line is None:
            return {"matched": False}
        return {"matched": True, "line_number": line.line_number}

    def _output_composer_backspace(self) -> None:
        """Trim the in-progress query or line-number buffer by one char."""
        if self._output_cursor is not None:
            self._output_cursor.backspace_composer()

    def _output_composer_commit(self) -> Optional[dict[str, object]]:
        """Apply the in-progress composer; report where we landed."""
        if self._output_cursor is None:
            return None
        mode = self._output_cursor.composer_mode
        query = self._output_cursor.composer_buffer
        line = self._output_cursor.commit_composer()
        payload: dict[str, object] = {"mode": mode, "query": query}
        if line is not None:
            payload["line_number"] = line.line_number
        return payload

    def _output_composer_cancel(self) -> Optional[dict[str, object]]:
        """Discard the composer and restore the line the user started on."""
        if self._output_cursor is None:
            return None
        self._output_cursor.cancel_composer()
        return None

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


def _parse_meta_command(
    buffer: str,
) -> Optional[tuple[Optional[str], str, str]]:
    """Parse `:<name> [argument]` into `(canonical, argument, raw_name)`.

    Returns `None` when the buffer is not a meta-command line at all
    (empty, not starting with `:`, or syntactically unparseable). When
    a `:` prefix is present but the name is not in `META_COMMANDS`,
    returns `(None, argument, raw_name)` so the caller can emit a
    typo-suggest hint instead of falling through to the kernel.

    Matching is case-insensitive; `:Help`, `:HELP`, and `:help` all
    resolve to the canonical `"help"`.
    """
    stripped = buffer.strip()
    if not stripped.startswith(":"):
        return None
    match = _META_NAME_RE.match(stripped)
    if match is None:
        return None
    raw_name = match.group(1)
    argument = match.group(2).strip()
    canonical = raw_name.lower()
    if canonical in META_COMMANDS:
        return canonical, argument, raw_name
    return None, argument, raw_name


def _suggest_meta_command(name: str) -> Optional[str]:
    """Return the closest known meta-command to `name`, or None."""
    matches = difflib.get_close_matches(
        name.lower(), META_COMMANDS, n=1, cutoff=0.6
    )
    return matches[0] if matches else None


def _parse_reset_scope(argument: str) -> Optional[ResetScope]:
    """Map a `:reset <arg>` argument to a ResetScope, or None for "unknown".

    `bank` and `all` both resolve to ResetScope.BANK; `section` /
    `record` / `field` resolve to their matching enums. Empty or
    unrecognised arguments return None so the caller can surface a
    help hint rather than a silent wrong scope.
    """
    normalized = argument.strip().lower()
    if normalized in ("bank", "all"):
        return ResetScope.BANK
    if normalized == "section":
        return ResetScope.SECTION
    if normalized == "record":
        return ResetScope.RECORD
    if normalized == "field":
        return ResetScope.FIELD
    return None
