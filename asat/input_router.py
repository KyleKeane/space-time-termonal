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
import functools
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

from asat import keys as kc
from asat.actions import ActionContext, ActionMenu
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.help_topics import HELP_TOPICS, lookup as lookup_help_topic, topic_names
from asat.keys import Key, Modifier
from asat.notebook import FocusMode, NotebookCursor
from asat.output_buffer import OutputRecorder
from asat.output_cursor import OutputCursor
from asat.session import SessionError
from asat.settings_controller import SettingsController
from asat.settings_editor import ResetScope, SettingsEditorError


BindingMap = dict[FocusMode, dict[Key, str]]
ActionHandler = Callable[[], Optional[dict[str, object]]]


def _requires_settings_controller(method: Callable[..., object]) -> Callable[..., object]:
    """Skip the wrapped method when no SettingsController is configured.

    The router is constructed without a controller when a session has
    no settings UI wired up. Most `_settings_*` helpers exist only to
    delegate one or two calls to that controller, so each one used to
    repeat `if self._settings_controller is None: return`. This
    decorator removes the noise: it short-circuits to None when the
    controller is missing, otherwise calls through.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self._settings_controller is None:
            return None
        return method(self, *args, **kwargs)

    return wrapper


def _requires_output_cursor(method: Callable[..., object]) -> Callable[..., object]:
    """Skip the wrapped method when no OutputCursor is attached.

    Same shape as ``_requires_settings_controller``: OUTPUT-mode
    composer helpers (search, goto, commit, cancel) all began with the
    same ``if self._output_cursor is None: return None`` early-return.
    Decorating them collapses that boilerplate so every method body
    starts with the actual work.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self._output_cursor is None:
            return None
        return method(self, *args, **kwargs)

    return wrapper


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
        NVDA-style heading navigation (F61):
          `]` / `[` jump forward / backward to the next heading
            of any level,
          `1`..`6` jump forward to the next heading of that level.

    INPUT mode:
        Backspace deletes the character before the caret,
        Delete removes the character under the caret,
        Left / Right / Home / End move the caret within the buffer
        (Ctrl+A / Ctrl+E also jump to start / end),
        Up / Down walk command history (most recent first; Down past
        the most recent restores the in-progress draft),
        Ctrl+W kills the word before the caret,
        Ctrl+U kills from the start of the buffer to the caret,
        Ctrl+K kills from the caret to the end of the buffer,
        Ctrl+C cancels the running command (F1; needs the F62
          async-execution worker so the keystroke can reach the router
          while a command is in flight),
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
            Key.printable("]"): "next_heading",
            Key.printable("["): "prev_heading",
            Key.printable("}"): "next_parent_heading",
            Key.printable("{"): "prev_parent_heading",
            Key.printable("z"): "toggle_fold_heading",
            Key.printable("1"): "next_heading_1",
            Key.printable("2"): "next_heading_2",
            Key.printable("3"): "next_heading_3",
            Key.printable("4"): "next_heading_4",
            Key.printable("5"): "next_heading_5",
            Key.printable("6"): "next_heading_6",
            Key.printable("i"): "begin_text_input",
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
            kc.UP: "history_previous",
            kc.DOWN: "history_next",
            Key.combo("a", Modifier.CTRL): "cursor_home",
            Key.combo("e", Modifier.CTRL): "cursor_end",
            Key.combo("w", Modifier.CTRL): "delete_word_left",
            Key.combo("u", Modifier.CTRL): "delete_to_start",
            Key.combo("k", Modifier.CTRL): "delete_to_end",
            Key.combo("c", Modifier.CTRL): "cancel_command",
            kc.ENTER: "submit",
            kc.ESCAPE: "exit_input",
            kc.F2: "open_action_menu",
            menu_open: "open_action_menu",
            repeat_narration: "repeat_last_narration",
        },
        FocusMode.TEXT_INPUT: {
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
            kc.ENTER: "submit_text_input",
            kc.ESCAPE: "abandon_text_input",
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
            Key.printable("p"): "output_playback_toggle",
            Key.printable(" "): "output_playback_toggle",
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
    "state",
    "heading",
    "text",
    "toc",
    "workspace",
    "list-notebooks",
    "new-notebook",
    "bindings",
    "bookmark",
    "unbookmark",
    "bookmarks",
    "jump",
    "verbosity",
    "reload-bank",
    "tts",
)

# "Ambient" meta-commands do their job without taking focus away from
# INPUT mode. `:help` prints a cheat sheet; `:save` persists the
# session; `:pwd` and `:commands` emit an informational HELP_REQUESTED
# event. In each case the natural next action is to keep typing, so
# the router clears the in-progress buffer but leaves the user in INPUT
# mode. Commands NOT in this set (today: `:settings`, `:quit`)
# inherently require a mode change and go through `abandon_input_mode`.
AMBIENT_META_COMMANDS: frozenset[str] = frozenset(
    {
        "help",
        "save",
        "pwd",
        "commands",
        "welcome",
        "repeat",
        "state",
        "toc",
        "workspace",
        "list-notebooks",
        "new-notebook",
        "bindings",
        "bookmark",
        "unbookmark",
        "bookmarks",
        "verbosity",
        "reload-bank",
        "tts",
    }
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
    "           ] / [ next / prev heading; 1..6 next heading of that level.",
    "           } / { next / prev heading shallower than current scope (parent).",
    "           i begins an in-place text cell (Enter creates, Escape abandons).",
    "           z on a heading toggles collapse (hides its cells from Up/Down).",
    "INPUT:     Enter submits, Escape leaves without running.",
    "           Backspace/Delete cut, Left/Right walk, Home/End jump (or Ctrl+A/E).",
    "           Up/Down walk command history (Down past newest restores your draft).",
    "           Ctrl+W kills word, Ctrl+U kills to start, Ctrl+K kills to end.",
    "           Ctrl+C cancels the running command (publishes COMMAND_CANCELLED).",
    "TEXT:      Enter creates a text cell from the buffer, Escape abandons.",
    "           Buffer editing keys match INPUT (Backspace/Delete, Left/Right, Home/End, Ctrl+A/E/W/U/K).",
    "OUTPUT:    Up/Down step lines, PageUp/PageDown page, Escape leaves.",
    "           / search (type query, Enter commits), n / N next / prev hit, g jump-to-line.",
    "SETTINGS:  Up/Down walk, Right/Enter descend, Left/Escape ascend, e edit, Ctrl+S save, Ctrl+Q close.",
    "           / search (cross-section; Enter commits, Escape restores), n / N cycle matches.",
    "           Ctrl+Z undo, Ctrl+Y redo edits in the order you made them.",
    "           Ctrl+R resets to defaults at cursor scope (Enter confirms, Escape cancels).",
    "Menu:      F2 (or Ctrl+.) opens contextual actions; Up/Down walk, Enter invokes, Escape closes.",
    "Meta:      :help, :settings, :save, :quit, :delete, :duplicate, :pwd, :state, :commands, :reset, :welcome, :repeat, :heading, :text, :toc, :workspace, :list-notebooks, :new-notebook, :bindings, :bookmark, :unbookmark, :bookmarks, :jump, :verbosity, :reload-bank, :tts.",
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
        output_recorder: Optional[OutputRecorder] = None,
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

        When an `output_recorder` is supplied alongside `output_cursor`,
        the `view_output` action (Ctrl+O) attaches the cursor to the
        focused cell's buffer on transition, so Up/Down/`/`/`g` work
        immediately without the user having to route through the F2
        action menu first. Without it, Ctrl+O still flips the focus
        mode but navigation keys no-op until something else attaches.
        """
        self._cursor = cursor
        self._bus = bus
        self._bindings = bindings if bindings is not None else default_bindings()
        self._output_cursor = output_cursor
        self._settings_controller = settings_controller
        self._action_menu = action_menu
        self._output_recorder = output_recorder
        # F49: build the action -> handler table once at construction so
        # _invoke is a flat dict lookup. Splitting by subsystem keeps the
        # table searchable: every notebook key lives next to other
        # notebook keys, every settings key next to other settings keys.
        # The optional cursors (output / settings / menu / recorder) are
        # not reassigned after __init__, so caching bound-method handlers
        # is safe; handlers that need a missing cursor route through
        # router methods that re-check the attribute at call time.
        self._handlers: dict[str, ActionHandler] = {
            **self._notebook_handlers(),
            **self._input_handlers(),
            **self._output_handlers(),
            **self._settings_handlers(),
            **self._menu_handlers(),
            **self._global_handlers(),
        }

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
        if (
            mode in (FocusMode.INPUT, FocusMode.TEXT_INPUT)
            and key.is_printable()
            and key.char is not None
        ):
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

    def _submit_text_input(self) -> Optional[dict[str, object]]:
        """Commit the TEXT_INPUT buffer as a new text cell (F27 `i` flow).

        Reports whether a cell was created (an empty / whitespace-only
        buffer is treated as abandonment) and, when one was, its id and
        body so observers can narrate the insertion.
        """
        cell = self._cursor.submit_text_input()
        if cell is None:
            return {"created": False}
        return {
            "created": True,
            "cell_id": cell.cell_id,
            "text": cell.text,
        }

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

        Looks the command up in `_META_HANDLERS` (declared below the
        class). `save`, `quit`, `repeat`, and `welcome` deliberately
        have no router-side handler — they are handled by the
        Application via the ACTION_INVOKED payload's `meta_command`
        key, and we let the table miss for them. The trailing
        `argument` is always passed; handlers that do not read it
        simply ignore it.
        """
        handler = _META_HANDLERS.get(command)
        if handler is not None:
            handler(self, argument)

    def _handle_meta_heading(self, argument: str) -> None:
        """Append a heading cell from `:heading <level> <title>`.

        Expects `<level>` as a single digit 1..6 followed by a
        non-empty title. Malformed arguments surface a HELP_REQUESTED
        hint instead of silently creating a wrong-level heading.
        """
        level, title = _parse_heading_argument(argument)
        if level is None or title is None:
            publish_event(
                self._bus,
                EventType.HELP_REQUESTED,
                {
                    "lines": [
                        "`:heading <level> <title>` — level is 1..6, "
                        "title is the remainder of the line.",
                        "Example: `:heading 2 Setup`.",
                    ],
                },
                source=self.SOURCE,
            )
            return
        self._cursor.new_heading_cell(level, title)

    def _handle_meta_text(self, argument: str) -> None:
        """Append a text cell from `:text <prose>` (F27).

        The entire argument string becomes the prose body. Empty
        arguments surface a HELP_REQUESTED hint rather than creating
        an invisible blank cell.
        """
        body = argument.strip()
        if not body:
            publish_event(
                self._bus,
                EventType.HELP_REQUESTED,
                {
                    "lines": [
                        "`:text <prose>` — body is the remainder of the line.",
                        "Example: `:text Training overfits past 20 epochs.`",
                    ],
                },
                source=self.SOURCE,
            )
            return
        self._cursor.new_text_cell(body)

    def _publish_bindings(self, argument: str) -> None:
        """Surface the keybinding report via HELP_REQUESTED (F64).

        `:bindings` lists every binding in the active map, grouped
        by mode. `:bindings <mode>` filters to one mode (NOTEBOOK,
        INPUT, OUTPUT, SETTINGS — case-insensitive).
        `:bindings <key>` filters to bindings whose primary key
        matches (case-insensitive `Key.name`, e.g. `up`, `enter`,
        `]`). The two filters can combine: `:bindings notebook up`
        narrates only the NOTEBOOK-mode `Up` row. An empty result
        is reported explicitly so the user is not left in silence.
        """
        mode_filter, key_filter = _parse_bindings_filter(argument, self._bindings)
        entries = binding_report(self._bindings)
        if mode_filter is not None:
            entries = tuple(e for e in entries if e.mode is mode_filter)
        if key_filter is not None:
            entries = tuple(e for e in entries if e.key_name == key_filter)
        if not entries:
            scope = []
            if mode_filter is not None:
                scope.append(mode_filter.value)
            if key_filter is not None:
                scope.append(repr(key_filter))
            scope_label = " ".join(scope) if scope else ""
            lines = [
                f"No bindings match {scope_label}." if scope_label
                else "No bindings configured.",
                "Type `:bindings` (no argument) to list every binding.",
            ]
        else:
            label_parts = []
            if mode_filter is not None:
                label_parts.append(mode_filter.value.upper())
            if key_filter is not None:
                label_parts.append(repr(key_filter))
            heading = (
                f"Bindings ({' '.join(label_parts)}):"
                if label_parts
                else "Bindings:"
            )
            lines = [heading]
            current_mode: Optional[FocusMode] = None
            for entry in entries:
                if entry.mode is not current_mode:
                    lines.append(f"  {entry.mode.value.upper()}:")
                    current_mode = entry.mode
                lines.append(f"    {entry.key_spec:<14} → {entry.action}")
        publish_event(
            self._bus,
            EventType.HELP_REQUESTED,
            {"lines": lines},
            source=self.SOURCE,
        )

    def _publish_toc(self) -> None:
        """Announce the notebook's heading outline via HELP_REQUESTED."""
        toc = self._cursor.list_headings()
        if not toc:
            lines = [
                "No headings in this notebook yet.",
                "Add one with `:heading <level> <title>` (level 1..6).",
            ]
        else:
            lines = ["Table of contents:"]
            for index, level, title, _ in toc:
                lines.append(f"  cell {index + 1}: H{level} — {title}")
        publish_event(
            self._bus,
            EventType.HELP_REQUESTED,
            {"lines": lines},
            source=self.SOURCE,
        )

    def _handle_meta_bookmark(self, argument: str) -> None:
        """Capture the focused cell under ``:bookmark <name>`` (F35).

        The argument must be a single non-empty token (no whitespace) so
        ``:jump`` can later disambiguate. The currently focused cell is
        the bookmark target; without one (e.g. an empty notebook) the
        router emits a HELP_REQUESTED hint and bails.
        """
        name = argument.strip()
        if not name or any(ch.isspace() for ch in name):
            self._publish_help_lines(
                [
                    "`:bookmark <name>` — name is one token (no spaces).",
                    "Example: `:bookmark setup`.",
                ]
            )
            return
        focused_id = self._cursor.focus.cell_id
        if focused_id is None:
            self._publish_help_lines(
                ["No cell focused; cannot bookmark — open or create a cell first."]
            )
            return
        try:
            self._cursor.session.add_bookmark(name, focused_id)
        except SessionError as exc:
            self._publish_help_lines([str(exc)])
            return
        publish_event(
            self._bus,
            EventType.BOOKMARK_CREATED,
            {"name": name, "cell_id": focused_id},
            source=self.SOURCE,
        )

    def _handle_meta_unbookmark(self, argument: str) -> None:
        """Remove ``:unbookmark <name>`` from the registry (F35)."""
        name = argument.strip()
        if not name:
            self._publish_help_lines(
                ["`:unbookmark <name>` — name is required."]
            )
            return
        try:
            cell_id = self._cursor.session.remove_bookmark(name)
        except SessionError:
            self._publish_help_lines(
                [f"No bookmark named `{name}`. Type `:bookmarks` to list them."]
            )
            return
        publish_event(
            self._bus,
            EventType.BOOKMARK_REMOVED,
            {"name": name, "cell_id": cell_id},
            source=self.SOURCE,
        )

    def _publish_bookmarks(self) -> None:
        """Narrate every registered bookmark via HELP_REQUESTED (F35)."""
        entries = self._cursor.session.list_bookmarks()
        if not entries:
            lines = [
                "No bookmarks yet.",
                "Capture one with `:bookmark <name>` while a cell is focused.",
            ]
        else:
            session = self._cursor.session
            lines = ["Bookmarks:"]
            for name, cell_id in entries:
                try:
                    index = session.index_of(cell_id)
                    lines.append(f"  {name} → cell {index + 1}")
                except SessionError:
                    lines.append(f"  {name} → (cell missing)")
        publish_event(
            self._bus,
            EventType.HELP_REQUESTED,
            {"lines": lines},
            source=self.SOURCE,
        )

    def _handle_meta_jump(self, argument: str) -> None:
        """Move focus to the cell registered under ``:jump <name>`` (F35)."""
        name = argument.strip()
        if not name:
            self._publish_help_lines(
                ["`:jump <name>` — name is required. List with `:bookmarks`."]
            )
            return
        cell_id = self._cursor.session.get_bookmark(name)
        if cell_id is None:
            self._publish_help_lines(
                [f"No bookmark named `{name}`. Type `:bookmarks` to list them."]
            )
            return
        try:
            self._cursor.focus_cell(cell_id)
        except SessionError:
            # Should not happen — remove_cell prunes dangling entries —
            # but guard so a stale registry never crashes the router.
            self._cursor.session.remove_bookmark(name)
            self._publish_help_lines(
                [f"Bookmark `{name}` pointed at a missing cell; removed."]
            )
            return
        publish_event(
            self._bus,
            EventType.BOOKMARK_JUMPED,
            {"name": name, "cell_id": cell_id},
            source=self.SOURCE,
        )

    def _publish_help_lines(self, lines: list[str]) -> None:
        """Convenience wrapper to publish HELP_REQUESTED with given lines."""
        publish_event(
            self._bus,
            EventType.HELP_REQUESTED,
            {"lines": list(lines)},
            source=self.SOURCE,
        )

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

    def _publish_state(self) -> None:
        """Narrate "where am I?" — focus mode, cell index, cwd, session id.

        Designed for the hands-on test loop: when the user has lost
        track of which cell or mode they're in, `:state` answers in
        one HELP_REQUESTED block. Always reports from the cursor's
        perspective so the answer matches what the next keystroke
        will act on.
        """
        focus = self._cursor.focus
        session = self._cursor.session
        cells = session.cells
        cell_count = len(cells)
        cell_id = focus.cell_id
        if cell_id is not None:
            try:
                index = session.index_of(cell_id)
                position = f"cell {index + 1} of {cell_count}"
            except ValueError:
                position = f"cell {cell_id} (not in session)"
        else:
            position = "no cell focused"
        lines = [
            f"Focus mode: {focus.mode.value}",
            f"Position: {position}",
            f"Session id: {session.session_id}",
            f"Working directory: {os.getcwd()}",
        ]
        publish_event(
            self._bus,
            EventType.HELP_REQUESTED,
            {"lines": lines},
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

    def _view_output(self) -> None:
        """Enter OUTPUT mode and attach the output cursor to the cell's buffer.

        Captures the focused cell id BEFORE the mode transition (which
        may clear it) so the buffer attach lands on the right cell.
        With no output_cursor / output_recorder wired, falls back to
        the old behaviour of simply transitioning focus.
        """
        cell_id = self._cursor.focus.cell_id
        self._cursor.view_output_mode()
        if (
            self._output_cursor is not None
            and self._output_recorder is not None
            and cell_id is not None
        ):
            self._output_cursor.attach(self._output_recorder.buffer_for(cell_id))

    @_requires_settings_controller
    def _open_settings(self) -> None:
        """Enter SETTINGS mode and open a controller session if available."""
        self._settings_controller.open()
        self._cursor.enter_settings_mode()

    @_requires_settings_controller
    def _close_settings(self) -> None:
        """Close the controller and return to NOTEBOOK mode."""
        self._settings_controller.close()
        self._cursor.exit_settings_mode()

    @_requires_settings_controller
    def _save_settings(self) -> None:
        """Persist the in-progress bank; no-op without a save path."""
        if self._settings_controller.save_path is None:
            return
        self._settings_controller.save()

    @_requires_settings_controller
    def _settings_prev(self) -> None:
        """Move the settings cursor one step backward."""
        self._settings_controller.prev()

    @_requires_settings_controller
    def _settings_next(self) -> None:
        """Move the settings cursor one step forward."""
        self._settings_controller.next()

    @_requires_settings_controller
    def _settings_descend(self) -> None:
        """Drop one level deeper into the settings hierarchy."""
        self._settings_controller.descend()

    @_requires_settings_controller
    def _settings_ascend(self) -> None:
        """Rise one level; at the top, close the editor."""
        if not self._settings_controller.ascend():
            self._close_settings()

    @_requires_settings_controller
    def _settings_begin_edit(self) -> None:
        """Start composing a replacement value for the focused field."""
        self._settings_controller.begin_edit()

    @_requires_settings_controller
    def _settings_edit_commit(self) -> Optional[dict[str, object]]:
        """Apply the in-progress edit buffer; surface errors in the payload."""
        try:
            self._settings_controller.commit_edit()
            return {"ok": True}
        except SettingsEditorError as exc:
            return {"ok": False, "error": str(exc)}

    @_requires_settings_controller
    def _settings_edit_cancel(self) -> None:
        """Discard the in-progress edit buffer."""
        self._settings_controller.cancel_edit()

    @_requires_settings_controller
    def _settings_edit_backspace(self) -> None:
        """Remove the last character from the edit buffer."""
        self._settings_controller.backspace_edit()

    @_requires_settings_controller
    def _settings_undo(self) -> None:
        """Revert the most recent edit via the settings controller."""
        self._settings_controller.undo()

    @_requires_settings_controller
    def _settings_redo(self) -> None:
        """Re-apply the most recently undone edit via the settings controller."""
        self._settings_controller.redo()

    @_requires_settings_controller
    def _settings_search_begin(self) -> Optional[dict[str, object]]:
        """Open the `/` search overlay; report whether it started."""
        started = self._settings_controller.begin_search()
        return {"opened": started}

    @_requires_settings_controller
    def _settings_search_commit(self) -> Optional[dict[str, object]]:
        """Apply the in-progress search; surface the query + match count."""
        editor = self._settings_controller.editor
        query = editor.search_buffer
        match_count = editor.search_match_count
        self._settings_controller.commit_search()
        return {"query": query, "match_count": match_count}

    @_requires_settings_controller
    def _settings_search_cancel(self) -> None:
        """Discard the in-progress search and restore the cursor."""
        self._settings_controller.cancel_search()

    @_requires_settings_controller
    def _settings_search_backspace(self) -> None:
        """Trim the last character from the search buffer."""
        self._settings_controller.backspace_search()

    @_requires_settings_controller
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
        started = self._settings_controller.begin_reset(scope)
        payload: dict[str, object] = {"opened": started}
        active_scope = self._settings_controller.reset_scope
        if active_scope is not None:
            payload["scope"] = active_scope.value
        elif scope is not None:
            payload["scope"] = scope.value
        return payload

    @_requires_settings_controller
    def _settings_reset_confirm(self) -> Optional[dict[str, object]]:
        """Confirm the pending reset; report whether the bank changed."""
        scope = self._settings_controller.reset_scope
        applied = self._settings_controller.confirm_reset()
        payload: dict[str, object] = {"applied": applied}
        if scope is not None:
            payload["scope"] = scope.value
        return payload

    @_requires_settings_controller
    def _settings_reset_cancel(self) -> None:
        """Cancel the pending reset; leave the bank untouched."""
        self._settings_controller.cancel_reset()

    @_requires_settings_controller
    def _settings_search_next(self) -> Optional[dict[str, object]]:
        """Cycle to the next match; no-op without prior results."""
        editor = self._settings_controller.editor
        matched = editor.next_search_match()
        return {"matched": matched}

    @_requires_settings_controller
    def _settings_search_prev(self) -> Optional[dict[str, object]]:
        """Cycle to the previous match; no-op without prior results."""
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
        """Look up a zero-argument callable by action name.

        Handlers return an optional dict of extra payload fields. Only
        "submit" contributes extras today; every other handler runs a
        side effect and returns None via _void, which discards whatever
        the underlying cursor method returned. The handler table is
        built once at __init__ from per-subsystem helpers — see
        _notebook_handlers, _input_handlers, _output_handlers,
        _settings_handlers, _menu_handlers, _global_handlers.
        """
        try:
            return self._handlers[action]
        except KeyError:
            raise KeyError(f"Unknown action: {action}") from None

    def _notebook_handlers(self) -> dict[str, ActionHandler]:
        """NOTEBOOK-mode motions and cell mutations."""
        return {
            "move_up": _void(self._cursor.move_up),
            "move_down": _void(self._cursor.move_down),
            "move_to_top": _void(self._cursor.move_to_top),
            "move_to_bottom": _void(self._cursor.move_to_bottom),
            "enter_input": _void(self._cursor.enter_input_mode),
            "exit_input": _void(self._cursor.exit_input_mode),
            "new_cell": _void(self._cursor.new_cell),
            "view_output": _void(self._view_output),
            "exit_output": _void(self._cursor.exit_output_mode),
            "delete_cell": _void(self._cursor.delete_focused_cell),
            "duplicate_cell": _void(self._cursor.duplicate_focused_cell),
            "move_cell_up": _void(lambda: self._cursor.move_focused_cell(-1)),
            "move_cell_down": _void(lambda: self._cursor.move_focused_cell(+1)),
            "next_heading": self._next_heading_action(None),
            "prev_heading": self._prev_heading_action(None),
            "next_heading_1": self._next_heading_action(1),
            "next_heading_2": self._next_heading_action(2),
            "next_heading_3": self._next_heading_action(3),
            "next_heading_4": self._next_heading_action(4),
            "next_heading_5": self._next_heading_action(5),
            "next_heading_6": self._next_heading_action(6),
            "next_parent_heading": self._parent_heading_action(direction=+1),
            "prev_parent_heading": self._parent_heading_action(direction=-1),
            "toggle_fold_heading": self._toggle_fold_action(),
            "begin_text_input": _void(self._cursor.begin_text_input),
            "submit_text_input": self._submit_text_input,
            "abandon_text_input": _void(self._cursor.abandon_text_input),
        }

    def _input_handlers(self) -> dict[str, ActionHandler]:
        """INPUT-mode editing, history navigation, and submission."""
        return {
            "backspace": _void(self._cursor.backspace),
            "cursor_left": _void(self._cursor.cursor_left),
            "cursor_right": _void(self._cursor.cursor_right),
            "cursor_home": _void(self._cursor.cursor_home),
            "cursor_end": _void(self._cursor.cursor_end),
            "delete_forward": _void(self._cursor.delete_forward),
            "delete_word_left": _void(self._cursor.delete_word_left),
            "delete_to_start": _void(self._cursor.delete_to_start),
            "delete_to_end": _void(self._cursor.delete_to_end),
            "history_previous": self._history_previous,
            "history_next": self._history_next,
            "submit": self._submit,
        }

    def _output_handlers(self) -> dict[str, ActionHandler]:
        """OUTPUT-mode scrolling, search, and composer keys."""
        return {
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
            "output_search_begin": self._output_search_begin,
            "output_goto_begin": self._output_goto_begin,
            "output_search_next": self._output_search_next,
            "output_search_prev": self._output_search_prev,
            "output_playback_toggle": lambda: None,
            "output_composer_extend": lambda: None,
            "output_composer_backspace": self._output_composer_backspace,
            "output_composer_commit": self._output_composer_commit,
            "output_composer_cancel": self._output_composer_cancel,
        }

    def _settings_handlers(self) -> dict[str, ActionHandler]:
        """SETTINGS-mode navigation, edit composer, search, reset."""
        return {
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
        }

    def _menu_handlers(self) -> dict[str, ActionHandler]:
        """Action-menu (F2) navigation and activation."""
        return {
            "open_action_menu": self._open_action_menu,
            "menu_prev": _void(self._menu_prev),
            "menu_next": _void(self._menu_next),
            "menu_activate": _void(self._menu_activate),
            "menu_close": _void(self._menu_close),
        }

    def _global_handlers(self) -> dict[str, ActionHandler]:
        """Mode-independent actions handled outside the router itself.

        repeat_last_narration is observed by the SoundEngine to replay
        the last spoken phrase; cancel_command is observed by the
        kernel scheduler. The router only needs to publish
        ACTION_INVOKED for them, so the handlers are no-ops here.
        """
        return {
            "repeat_last_narration": lambda: None,
            "cancel_command": lambda: None,
        }

    def _with_output_cursor(
        self,
        operation: Callable[[OutputCursor], object],
    ) -> None:
        """Run an output-cursor operation only if a cursor is attached."""
        if self._output_cursor is None:
            return
        operation(self._output_cursor)

    def _next_heading_action(self, level: Optional[int]) -> ActionHandler:
        """Handler that reports whether a forward heading jump landed."""
        def handler() -> Optional[dict[str, object]]:
            cell = self._cursor.move_to_next_heading(level=level)
            payload: dict[str, object] = {"matched": cell is not None}
            if level is not None:
                payload["level"] = level
            if cell is not None:
                payload["cell_id"] = cell.cell_id
            return payload
        return handler

    def _prev_heading_action(self, level: Optional[int]) -> ActionHandler:
        """Handler that reports whether a backward heading jump landed."""
        def handler() -> Optional[dict[str, object]]:
            cell = self._cursor.move_to_previous_heading(level=level)
            payload: dict[str, object] = {"matched": cell is not None}
            if level is not None:
                payload["level"] = level
            if cell is not None:
                payload["cell_id"] = cell.cell_id
            return payload
        return handler

    def _toggle_fold_action(self) -> ActionHandler:
        """F27: `z` on a heading toggles its collapsed flag."""
        def handler() -> Optional[dict[str, object]]:
            result = self._cursor.toggle_fold_focused_heading()
            return {"toggled": result is not None, "collapsed": bool(result)}
        return handler

    def _parent_heading_action(self, direction: int) -> ActionHandler:
        """F27: `{` / `}` jump to the prev/next heading shallower than scope."""
        def handler() -> Optional[dict[str, object]]:
            if direction > 0:
                cell = self._cursor.move_to_next_parent_heading()
            else:
                cell = self._cursor.move_to_previous_parent_heading()
            payload: dict[str, object] = {"matched": cell is not None}
            if cell is not None:
                payload["cell_id"] = cell.cell_id
                if cell.heading_level is not None:
                    payload["level"] = cell.heading_level
            return payload
        return handler

    def _history_previous(self) -> Optional[dict[str, object]]:
        """Recall an older command from the session's history."""
        recalled = self._cursor.history_previous()
        return {"recalled": recalled}

    def _history_next(self) -> Optional[dict[str, object]]:
        """Step forward through history (or restore the in-progress draft)."""
        recalled = self._cursor.history_next()
        return {"recalled": recalled}

    @_requires_output_cursor
    def _output_search_begin(self) -> Optional[dict[str, object]]:
        """Open the `/` search composer on the attached output cursor."""
        started = self._output_cursor.begin_search()
        return {"opened": started}

    @_requires_output_cursor
    def _output_goto_begin(self) -> Optional[dict[str, object]]:
        """Open the `g` line-jump composer on the attached output cursor."""
        started = self._output_cursor.begin_goto()
        return {"opened": started}

    @_requires_output_cursor
    def _output_search_next(self) -> Optional[dict[str, object]]:
        """Cycle to the next search hit (no-op without prior matches)."""
        line = self._output_cursor.next_match()
        if line is None:
            return {"matched": False}
        return {"matched": True, "line_number": line.line_number}

    @_requires_output_cursor
    def _output_search_prev(self) -> Optional[dict[str, object]]:
        """Cycle to the previous search hit (no-op without prior matches)."""
        line = self._output_cursor.prev_match()
        if line is None:
            return {"matched": False}
        return {"matched": True, "line_number": line.line_number}

    @_requires_output_cursor
    def _output_composer_backspace(self) -> None:
        """Trim the in-progress query or line-number buffer by one char."""
        self._output_cursor.backspace_composer()

    @_requires_output_cursor
    def _output_composer_commit(self) -> Optional[dict[str, object]]:
        """Apply the in-progress composer; report where we landed."""
        mode = self._output_cursor.composer_mode
        query = self._output_cursor.composer_buffer
        line = self._output_cursor.commit_composer()
        payload: dict[str, object] = {"mode": mode, "query": query}
        if line is not None:
            payload["line_number"] = line.line_number
        return payload

    @_requires_output_cursor
    def _output_composer_cancel(self) -> Optional[dict[str, object]]:
        """Discard the composer and restore the line the user started on."""
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


# F49: table-drive `:meta` dispatch. Each entry is a small
# `(router, argument) -> None` adapter so handlers with mixed
# signatures (no-arg vs argument-taking) share one shape. Adding a
# new meta-command becomes one row instead of a new branch in
# `_handle_meta_command`. `save`, `quit`, `repeat`, and `welcome`
# are intentionally absent — the Application handles them off the
# ACTION_INVOKED payload, and a table miss is the right behaviour
# for them on the router side.
_META_HANDLERS: dict[str, Callable[["InputRouter", str], None]] = {
    "settings": lambda router, _arg: router._open_settings(),
    "help": lambda router, arg: router._publish_help(arg),
    "delete": lambda router, _arg: router._cursor.delete_focused_cell(),
    "duplicate": lambda router, _arg: router._cursor.duplicate_focused_cell(),
    "pwd": lambda router, _arg: router._publish_pwd(),
    "state": lambda router, _arg: router._publish_state(),
    "commands": lambda router, _arg: router._publish_commands(),
    "reset": lambda router, arg: router._handle_meta_reset(arg),
    "heading": lambda router, arg: router._handle_meta_heading(arg),
    "text": lambda router, arg: router._handle_meta_text(arg),
    "toc": lambda router, _arg: router._publish_toc(),
    "bindings": lambda router, arg: router._publish_bindings(arg),
    "bookmark": lambda router, arg: router._handle_meta_bookmark(arg),
    "unbookmark": lambda router, arg: router._handle_meta_unbookmark(arg),
    "bookmarks": lambda router, _arg: router._publish_bookmarks(),
    "jump": lambda router, arg: router._handle_meta_jump(arg),
}


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


def _parse_heading_argument(argument: str) -> tuple[Optional[int], Optional[str]]:
    """Parse `<level> <title>` from a `:heading` meta-command argument.

    Returns `(level, title)` on success or `(None, None)` for any
    malformed input. `level` must be a single digit 1..6; `title`
    must be non-empty after stripping whitespace.
    """
    stripped = argument.strip()
    if not stripped:
        return None, None
    parts = stripped.split(maxsplit=1)
    if len(parts) != 2:
        return None, None
    level_text, title = parts
    if not level_text.isdigit():
        return None, None
    level = int(level_text)
    if not (1 <= level <= 6):
        return None, None
    title = title.strip()
    if not title:
        return None, None
    return level, title


from dataclasses import dataclass


@dataclass(frozen=True)
class BindingEntry:
    """One row of the keybinding introspection report (F64).

    `mode` is the FocusMode the binding fires in. `key_spec` is the
    human-readable key as the user would type it (`"Ctrl+N"`,
    `"Up"`, `"]"`). `key_name` is the canonical lowercase primary
    key name (`"n"`, `"up"`, `"]"`) so the `:bindings <key>` filter
    can match without re-parsing `key_spec`. `modifiers` is the
    immutable set of held modifiers; `action` is the handler name
    `_action_handler` resolves.
    """

    mode: FocusMode
    key_spec: str
    key_name: str
    modifiers: frozenset[Modifier]
    action: str


# Render order matches the user's mental model — Ctrl on the outside,
# then Alt, then Shift, then Meta — and keeps the textual form stable
# across hash randomisation of the underlying frozenset.
_MODIFIER_RENDER_ORDER: tuple[Modifier, ...] = (
    Modifier.CTRL,
    Modifier.ALT,
    Modifier.SHIFT,
    Modifier.META,
)

# Canonical capitalised renderings for the special-key vocabulary in
# `asat.keys`. Anything not listed falls back to title-cased
# underscore-split (`"page_up"` → `"Page Up"`).
_SPECIAL_KEY_LABELS: dict[str, str] = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "page_up": "Page Up",
    "page_down": "Page Down",
    "enter": "Enter",
    "escape": "Escape",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
}


def format_key(key: Key) -> str:
    """Return the human-readable label for a Key (`"Ctrl+N"`, `"Up"`, `"]"`).

    Single-character names render as the character itself so a binding
    on `]` or `,` is reproducible by typing it. Special keys use a
    fixed lookup table for the common arrow / motion keys, with a
    title-cased fallback (`"f7"` → `"F7"`, `"page_up"` → `"Page Up"`).
    Modifiers prepend in the canonical order regardless of the
    underlying frozenset's iteration order, so the output is stable
    for tests and for the generated `docs/BINDINGS.md`.
    """
    name = key.name
    is_letter = len(name) == 1 and name.isalpha()
    has_modifier_prefix = bool(key.modifiers)
    if len(name) == 1 and name not in _SPECIAL_KEY_LABELS:
        # Letters with a modifier render uppercase to match the
        # `Ctrl+N` / `Ctrl+S` convention used in HELP_LINES; bare
        # printables (`d`, `]`, `1`) keep the user's typed form.
        primary = name.upper() if is_letter and has_modifier_prefix else name
    elif name in _SPECIAL_KEY_LABELS:
        primary = _SPECIAL_KEY_LABELS[name]
    elif name.startswith("f") and name[1:].isdigit():
        primary = name.upper()
    else:
        primary = " ".join(part.capitalize() for part in name.split("_"))
    parts = [
        modifier.value.capitalize()
        for modifier in _MODIFIER_RENDER_ORDER
        if modifier in key.modifiers
    ]
    parts.append(primary)
    return "+".join(parts)


def binding_report(bindings: BindingMap) -> tuple[BindingEntry, ...]:
    """Flatten a BindingMap into a sorted list of BindingEntry rows.

    Sort order: mode (NOTEBOOK → INPUT → OUTPUT → SETTINGS, matching
    the FocusMode enum's declaration order), then by formatted
    key_spec. Stable sorting means the generated `docs/BINDINGS.md`
    diff stays minimal when a single binding is added or removed.
    """
    mode_order = {mode: index for index, mode in enumerate(FocusMode)}
    entries: list[BindingEntry] = []
    for mode, key_map in bindings.items():
        for key, action in key_map.items():
            entries.append(
                BindingEntry(
                    mode=mode,
                    key_spec=format_key(key),
                    key_name=key.name.lower(),
                    modifiers=key.modifiers,
                    action=action,
                )
            )
    entries.sort(key=lambda entry: (mode_order.get(entry.mode, 99), entry.key_spec))
    return tuple(entries)


def format_bindings_markdown(bindings: BindingMap) -> str:
    """Render a Markdown reference table for the supplied BindingMap.

    Used by `asat/tools/dump_bindings.py` and by the
    `test_bindings_doc_in_sync` gate test, so a binding added in
    `default_bindings()` but not regenerated into `docs/BINDINGS.md`
    fails CI the same way `test_every_meta_command_is_documented`
    catches missing meta-command rows.
    """
    lines: list[str] = [
        "# Keybindings reference",
        "",
        "Generated by `python -m asat.tools.dump_bindings`. Do NOT edit "
        "by hand — regenerate after changing `default_bindings()` in "
        "`asat/input_router.py`. The `test_bindings_doc_in_sync` gate "
        "fails CI when this file drifts from the in-memory map.",
        "",
        "Each row is one binding. The `Action` column is the handler "
        "name `InputRouter._action_handler` resolves; cross-reference "
        "with `asat/input_router.py` for the underlying call chain.",
        "",
    ]
    entries = binding_report(bindings)
    by_mode: dict[FocusMode, list[BindingEntry]] = {}
    for entry in entries:
        by_mode.setdefault(entry.mode, []).append(entry)
    for mode in FocusMode:
        rows = by_mode.get(mode)
        if not rows:
            continue
        lines.append(f"## {mode.value.upper()} mode")
        lines.append("")
        lines.append("| Keystroke | Action |")
        lines.append("|-----------|--------|")
        for entry in rows:
            lines.append(f"| `{entry.key_spec}` | `{entry.action}` |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_bindings_filter(
    argument: str, bindings: BindingMap
) -> tuple[Optional[FocusMode], Optional[str]]:
    """Parse `:bindings [mode] [key]` into a `(mode, key_name)` filter.

    The argument is split on whitespace. Each token is matched first
    against the FocusMode names, then against any binding's primary
    key name (case-insensitive). Tokens that match neither are
    silently passed through as a key-name filter so a typo just
    yields no rows rather than a router-level error. Returns
    `(None, None)` when ``argument`` is empty.
    """
    tokens = argument.strip().split()
    if not tokens:
        return None, None
    mode: Optional[FocusMode] = None
    key_name: Optional[str] = None
    known_keys = {
        key.name.lower()
        for key_map in bindings.values()
        for key in key_map.keys()
    }
    for token in tokens:
        normalized = token.lower()
        try:
            mode = FocusMode(normalized)
            continue
        except ValueError:
            pass
        if normalized in known_keys or key_name is None:
            key_name = normalized
    return mode, key_name


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
