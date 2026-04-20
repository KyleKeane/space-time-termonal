"""SettingsController: bridge between keystrokes and the SettingsEditor.

`SettingsEditor` is headless — it exposes `next / prev / enter / back /
edit / save / close` and nothing knows how those map to keys. This
controller owns the editor's lifecycle (open, close, re-open with the
most recent bank) and adds an "editing sub-mode" in which the user
types a replacement value character-by-character before committing.

The controller publishes nothing of its own: navigation and edit
events come out of the underlying `SettingsEditor`. One exception is
the edit sub-mode's own buffer, which the controller exposes via
`edit_buffer` so the TUI layer can echo it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from asat.event_bus import EventBus
from asat.settings_editor import (
    Level,
    ResetScope,
    SettingsEditor,
    SettingsEditorError,
)
from asat.sound_bank import SoundBank


class SettingsControllerError(RuntimeError):
    """Raised when an operation is attempted in the wrong controller state."""


class SettingsController:
    """Own the SettingsEditor lifecycle and map high-level actions to it.

    An instance represents one "settings session" that can be opened
    and closed repeatedly. Each `open()` constructs a fresh
    `SettingsEditor` seeded with the controller's current bank so the
    user always sees the most recent edits.
    """

    def __init__(
        self,
        bus: EventBus,
        bank: SoundBank,
        save_path: Optional[Path | str] = None,
        defaults_bank: Optional[SoundBank] = None,
    ) -> None:
        """Create a controller bound to a bus with an initial bank.

        `defaults_bank` is the factory-fresh bank that F21c reset
        operations restore from. Pass `None` to disable the reset
        sub-mode; `begin_reset` will then return False.
        """
        self._bus = bus
        self._bank = bank
        self._save_path = Path(save_path) if save_path is not None else None
        self._defaults_bank = defaults_bank
        self._editor: Optional[SettingsEditor] = None
        self._editing = False
        self._edit_buffer = ""

    @property
    def bank(self) -> SoundBank:
        """Return the live bank — from the editor if open, else cached."""
        return self._editor.bank if self._editor is not None else self._bank

    @property
    def editor(self) -> SettingsEditor:
        """Return the underlying editor; raises if no session is open."""
        if self._editor is None:
            raise SettingsControllerError("settings session is not open")
        return self._editor

    @property
    def is_open(self) -> bool:
        """Return True when a session is currently open."""
        return self._editor is not None

    @property
    def editing(self) -> bool:
        """Return True when the user is composing a replacement value."""
        return self._editing

    @property
    def edit_buffer(self) -> str:
        """Return the current in-progress replacement value."""
        return self._edit_buffer

    @property
    def save_path(self) -> Optional[Path]:
        """Return the default save path, if one was configured."""
        return self._save_path

    def open(self) -> SettingsEditor:
        """Start a new editor session. Safe to call repeatedly."""
        if self._editor is not None:
            # Already open; leave state alone so we don't lose in-flight edits.
            return self._editor
        self._editor = SettingsEditor(
            self._bus, self._bank, defaults_bank=self._defaults_bank
        )
        self._editing = False
        self._edit_buffer = ""
        return self._editor

    def close(self) -> None:
        """Close the session, preserving any edits in the cached bank."""
        if self._editor is None:
            return
        self._bank = self._editor.bank
        self._editor.close()
        self._editor = None
        self._editing = False
        self._edit_buffer = ""

    def next(self) -> None:
        """Advance the cursor at the current level."""
        self.editor.next()

    def prev(self) -> None:
        """Retreat the cursor at the current level."""
        self.editor.prev()

    def descend(self) -> None:
        """Drop one level deeper (SECTION → RECORD → FIELD)."""
        self.editor.enter()

    def ascend(self) -> bool:
        """Rise one level; return False at the top so the caller can close.

        While editing, ascend cancels the in-progress edit instead.
        While searching, ascend cancels the in-progress search.
        While resetting, ascend cancels the reset confirmation.
        """
        if self._editing:
            self.cancel_edit()
            return True
        if self.searching:
            self.cancel_search()
            return True
        if self.resetting:
            self.cancel_reset()
            return True
        if self.editor.state.level == Level.SECTION:
            return False
        self.editor.back()
        return True

    def begin_edit(self) -> None:
        """Start composing a new value for the focused field."""
        if self.editor.state.level != Level.FIELD:
            raise SettingsControllerError("can only edit at the FIELD level")
        if self.resetting:
            raise SettingsControllerError(
                "cannot begin edit while a reset confirmation is open"
            )
        self._editing = True
        self._edit_buffer = ""

    def extend_edit(self, character: str) -> None:
        """Append a character to the edit buffer."""
        if not self._editing:
            raise SettingsControllerError("not in edit sub-mode")
        if len(character) != 1:
            raise ValueError("extend_edit expects exactly one character")
        self._edit_buffer += character

    def backspace_edit(self) -> None:
        """Remove the last character from the edit buffer."""
        if not self._editing:
            raise SettingsControllerError("not in edit sub-mode")
        self._edit_buffer = self._edit_buffer[:-1]

    def commit_edit(self) -> None:
        """Apply the edit buffer via SettingsEditor.edit and leave sub-mode.

        On parse or validation failure the sub-mode stays active so the
        user can correct the value; the exception propagates so the TUI
        can surface the error message.
        """
        if not self._editing:
            raise SettingsControllerError("not in edit sub-mode")
        try:
            self.editor.edit(self._edit_buffer)
        except SettingsEditorError:
            raise
        self._editing = False
        self._edit_buffer = ""

    def cancel_edit(self) -> None:
        """Discard the edit buffer without committing."""
        if not self._editing:
            return
        self._editing = False
        self._edit_buffer = ""

    def undo(self) -> bool:
        """Revert the most recent edit on the underlying editor.

        Returns False when the session is closed, the user is
        composing a replacement value (the edit sub-mode owns the
        buffer; undo at that point would be surprising), the user is
        composing a `/` search query (same rationale), the user is
        confirming a reset, or when there is nothing on the undo
        stack. Otherwise delegates to `SettingsEditor.undo()` which
        restores the prior bank, refocuses the cursor on the field it
        mutated, and publishes SETTINGS_VALUE_EDITED so narration
        reacts.
        """
        if (
            self._editor is None
            or self._editing
            or self.searching
            or self.resetting
        ):
            return False
        return self._editor.undo()

    def redo(self) -> bool:
        """Re-apply the most recently undone edit. Mirror of `undo()`."""
        if (
            self._editor is None
            or self._editing
            or self.searching
            or self.resetting
        ):
            return False
        return self._editor.redo()

    @property
    def searching(self) -> bool:
        """Return True when a `/` search composer is active."""
        return self._editor is not None and self._editor.searching

    @property
    def search_buffer(self) -> str:
        """Return the in-progress search query (empty when not searching)."""
        return self._editor.search_buffer if self._editor is not None else ""

    def begin_search(self) -> bool:
        """Open the `/` search overlay. Cancels any in-progress edit first.

        Returns False when no session is open, the user is inside a
        reset confirmation, or the bank has no records to search
        across. Opening a second search over an active one is a no-op
        (the buffer is preserved so an accidental retap can't wipe
        the query).
        """
        if self._editor is None:
            return False
        if self.resetting:
            return False
        if self._editing:
            self.cancel_edit()
        return self._editor.begin_search()

    def extend_search(self, character: str) -> None:
        """Append a character to the search buffer."""
        if self._editor is None or not self.searching:
            raise SettingsControllerError("not in search sub-mode")
        if len(character) != 1:
            raise ValueError("extend_search expects exactly one character")
        self._editor.extend_search(character)

    def backspace_search(self) -> None:
        """Remove the last character from the search buffer."""
        if self._editor is None or not self.searching:
            raise SettingsControllerError("not in search sub-mode")
        self._editor.backspace_search()

    def commit_search(self) -> bool:
        """Close the overlay, keeping the cursor on the matched record."""
        if self._editor is None or not self.searching:
            return False
        return self._editor.commit_search()

    def cancel_search(self) -> bool:
        """Close the overlay and restore the cursor to its pre-search spot."""
        if self._editor is None or not self.searching:
            return False
        return self._editor.cancel_search()

    @property
    def resetting(self) -> bool:
        """Return True while a reset confirmation sub-mode is active."""
        return self._editor is not None and self._editor.resetting

    @property
    def reset_scope(self) -> Optional[ResetScope]:
        """Return the scope of the in-progress reset confirmation, if any."""
        return self._editor.reset_scope if self._editor is not None else None

    def default_reset_scope(self) -> ResetScope:
        """Scope `:reset` / Ctrl+R picks when the user doesn't name one."""
        return self.editor.default_reset_scope()

    def begin_reset(self, scope: Optional[ResetScope] = None) -> bool:
        """Open the reset confirmation for `scope` (default: cursor-level).

        Cancels any in-progress edit first — the user tapping Ctrl+R
        while mid-edit clearly wants to drop the half-typed value.
        Refuses while a search composer is active so the two overlays
        don't race. Returns False when no session is open, the reset
        cannot be applied (no defaults bank, or unsupported scope for
        the current cursor, or the focused record has no default).
        """
        if self._editor is None:
            return False
        if self.searching:
            return False
        if self._editing:
            self.cancel_edit()
        if scope is None:
            scope = self.default_reset_scope()
        return self._editor.begin_reset(scope)

    def confirm_reset(self) -> bool:
        """Apply the pending reset and close the confirmation sub-mode."""
        if self._editor is None or not self.resetting:
            return False
        return self._editor.confirm_reset()

    def cancel_reset(self) -> bool:
        """Leave the reset confirmation without mutating the bank."""
        if self._editor is None or not self.resetting:
            return False
        return self._editor.cancel_reset()

    def save(self, path: Optional[Path | str] = None) -> Path:
        """Persist the bank. Uses the configured save_path if none is given."""
        target = Path(path) if path is not None else self._save_path
        if target is None:
            raise SettingsControllerError("no save path configured")
        self.editor.save(target)
        return target

    def reload_from_disk(self, bank: SoundBank) -> None:
        """F3: replace the cached bank after an external disk reload.

        The Application is the authoritative owner of the ``bank_path``
        → ``SoundBank.load()`` flow; this method only updates the
        controller's cached bank so the next ``open()`` picks up the
        fresh copy. Refuses while a session is open so uncommitted
        edits are not silently overwritten; the caller should check
        ``is_open`` before calling.
        """
        if self._editor is not None:
            raise SettingsControllerError(
                "cannot reload bank while a settings session is open"
            )
        self._bank = bank
