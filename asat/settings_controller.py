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
from asat.settings_editor import Level, SettingsEditor, SettingsEditorError
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
    ) -> None:
        """Create a controller bound to a bus with an initial bank."""
        self._bus = bus
        self._bank = bank
        self._save_path = Path(save_path) if save_path is not None else None
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
        self._editor = SettingsEditor(self._bus, self._bank)
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
        """
        if self._editing:
            self.cancel_edit()
            return True
        if self.editor.state.level == Level.SECTION:
            return False
        self.editor.back()
        return True

    def begin_edit(self) -> None:
        """Start composing a new value for the focused field."""
        if self.editor.state.level != Level.FIELD:
            raise SettingsControllerError("can only edit at the FIELD level")
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

    def save(self, path: Optional[Path | str] = None) -> Path:
        """Persist the bank. Uses the configured save_path if none is given."""
        target = Path(path) if path is not None else self._save_path
        if target is None:
            raise SettingsControllerError("no save path configured")
        self.editor.save(target)
        return target
