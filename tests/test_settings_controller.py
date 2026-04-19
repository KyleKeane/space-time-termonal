"""Unit tests for SettingsController."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asat.event_bus import EventBus
from asat.events import EventType
from asat.settings_controller import SettingsController, SettingsControllerError
from asat.settings_editor import Level, ResetScope, SettingsEditorError
from asat.sound_bank import EventBinding, SoundBank, SoundRecipe, Voice


def _bank() -> SoundBank:
    """A small bank the controller can walk without depending on the default."""
    return SoundBank(
        voices=(Voice(id="v1", rate=1.0), Voice(id="v2", rate=1.2)),
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


class OpenCloseTests(unittest.TestCase):

    def test_open_creates_editor_and_publishes_opened(self) -> None:
        bus = EventBus()
        opened: list[object] = []
        bus.subscribe(EventType.SETTINGS_OPENED, opened.append)
        controller = SettingsController(bus, _bank())
        self.assertFalse(controller.is_open)
        controller.open()
        self.assertTrue(controller.is_open)
        self.assertEqual(len(opened), 1)

    def test_open_is_idempotent(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        first = controller.open()
        second = controller.open()
        self.assertIs(first, second)

    def test_close_publishes_closed_and_preserves_edits(self) -> None:
        bus = EventBus()
        closed: list[object] = []
        bus.subscribe(EventType.SETTINGS_CLOSED, closed.append)
        controller = SettingsController(bus, _bank())
        controller.open()
        controller.descend()       # into records
        controller.descend()       # into fields
        controller.next()          # to "engine" field
        controller.begin_edit()
        for ch in "sapi":
            controller.extend_edit(ch)
        controller.commit_edit()
        controller.close()
        self.assertFalse(controller.is_open)
        self.assertEqual(len(closed), 1)
        # The cached bank now reflects the edit.
        self.assertEqual(controller.bank.voices[0].engine, "sapi")

    def test_close_without_open_is_safe(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.close()  # must not raise

    def test_editor_property_raises_when_closed(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        with self.assertRaises(SettingsControllerError):
            _ = controller.editor


class NavigationTests(unittest.TestCase):

    def test_prev_and_next_delegate_to_editor(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.next()
        self.assertEqual(controller.editor.state.section.value, "sounds")
        controller.prev()
        self.assertEqual(controller.editor.state.section.value, "voices")

    def test_descend_and_ascend_walk_levels(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.descend()
        self.assertEqual(controller.editor.state.level, Level.RECORD)
        controller.descend()
        self.assertEqual(controller.editor.state.level, Level.FIELD)
        ok = controller.ascend()
        self.assertTrue(ok)
        self.assertEqual(controller.editor.state.level, Level.RECORD)
        controller.ascend()
        self.assertEqual(controller.editor.state.level, Level.SECTION)

    def test_ascend_at_top_returns_false(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        self.assertFalse(controller.ascend())


class EditSubModeTests(unittest.TestCase):

    def _parked_on_field(self) -> SettingsController:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.descend()
        controller.descend()  # now at FIELD level on voices[0].id
        return controller

    def test_begin_edit_requires_field_level(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        with self.assertRaises(SettingsControllerError):
            controller.begin_edit()

    def test_extend_and_commit_applies_via_editor(self) -> None:
        controller = self._parked_on_field()
        controller.next()  # engine
        controller.begin_edit()
        self.assertTrue(controller.editing)
        self.assertEqual(controller.edit_buffer, "")
        for ch in "sapi":
            controller.extend_edit(ch)
        self.assertEqual(controller.edit_buffer, "sapi")
        controller.commit_edit()
        self.assertFalse(controller.editing)
        self.assertEqual(controller.bank.voices[0].engine, "sapi")

    def test_backspace_trims_buffer(self) -> None:
        controller = self._parked_on_field()
        controller.next()
        controller.begin_edit()
        for ch in "sapix":
            controller.extend_edit(ch)
        controller.backspace_edit()
        self.assertEqual(controller.edit_buffer, "sapi")

    def test_cancel_discards_buffer_without_mutating_bank(self) -> None:
        controller = self._parked_on_field()
        controller.next()
        controller.begin_edit()
        for ch in "sapi":
            controller.extend_edit(ch)
        controller.cancel_edit()
        self.assertFalse(controller.editing)
        self.assertEqual(controller.bank.voices[0].engine, "")

    def test_commit_propagates_editor_error_and_keeps_sub_mode(self) -> None:
        controller = self._parked_on_field()
        # Park on the "rate" field (index 2 in VOICE_FIELDS).
        controller.next()  # engine
        controller.next()  # rate
        controller.begin_edit()
        for ch in "fast":
            controller.extend_edit(ch)
        with self.assertRaises(SettingsEditorError):
            controller.commit_edit()
        self.assertTrue(controller.editing)
        # Rate must remain unchanged after a rejected edit.
        self.assertEqual(controller.bank.voices[0].rate, 1.0)

    def test_ascend_while_editing_cancels_the_edit(self) -> None:
        controller = self._parked_on_field()
        controller.next()
        controller.begin_edit()
        controller.extend_edit("x")
        ok = controller.ascend()
        self.assertTrue(ok)
        self.assertFalse(controller.editing)
        # Still at the FIELD level — ascend only aborted the sub-mode.
        self.assertEqual(controller.editor.state.level, Level.FIELD)

    def test_extend_without_open_edit_raises(self) -> None:
        controller = self._parked_on_field()
        with self.assertRaises(SettingsControllerError):
            controller.extend_edit("x")

    def test_extend_rejects_multi_character(self) -> None:
        controller = self._parked_on_field()
        controller.next()
        controller.begin_edit()
        with self.assertRaises(ValueError):
            controller.extend_edit("ab")


class UndoRedoTests(unittest.TestCase):

    def _parked_on_rate(self) -> SettingsController:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.descend()
        controller.descend()
        # Move to "rate" (VOICE_FIELDS: id, engine, rate, ...)
        controller.next()  # engine
        controller.next()  # rate
        controller.begin_edit()
        for ch in "1.3":
            controller.extend_edit(ch)
        controller.commit_edit()
        return controller

    def test_undo_reverts_committed_edit(self) -> None:
        controller = self._parked_on_rate()
        self.assertAlmostEqual(controller.bank.voices[0].rate, 1.3)

        ok = controller.undo()

        self.assertTrue(ok)
        self.assertAlmostEqual(controller.bank.voices[0].rate, 1.0)

    def test_undo_is_noop_when_session_closed(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        self.assertFalse(controller.undo())

    def test_undo_is_noop_while_composing_edit(self) -> None:
        controller = self._parked_on_rate()
        controller.begin_edit()
        controller.extend_edit("2")

        self.assertFalse(controller.undo())
        # The buffer and edit sub-mode survive the ignored request.
        self.assertTrue(controller.editing)
        self.assertEqual(controller.edit_buffer, "2")

    def test_redo_reapplies_undone_edit(self) -> None:
        controller = self._parked_on_rate()
        controller.undo()

        ok = controller.redo()

        self.assertTrue(ok)
        self.assertAlmostEqual(controller.bank.voices[0].rate, 1.3)

    def test_redo_is_noop_when_session_closed(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        self.assertFalse(controller.redo())


class SearchSubModeTests(unittest.TestCase):
    """F21b: the controller exposes begin/extend/backspace/commit/cancel
    that mirror the existing edit sub-mode."""

    def test_begin_search_returns_false_when_closed(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        self.assertFalse(controller.begin_search())

    def test_begin_search_enters_sub_mode(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        self.assertTrue(controller.begin_search())
        self.assertTrue(controller.searching)
        self.assertEqual(controller.search_buffer, "")

    def test_extend_search_appends_to_buffer(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.begin_search()
        for ch in "v1":
            controller.extend_search(ch)
        self.assertEqual(controller.search_buffer, "v1")

    def test_backspace_trims_search_buffer(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.begin_search()
        for ch in "abc":
            controller.extend_search(ch)
        controller.backspace_search()
        self.assertEqual(controller.search_buffer, "ab")

    def test_commit_search_leaves_sub_mode(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.begin_search()
        for ch in "v1":
            controller.extend_search(ch)
        self.assertTrue(controller.commit_search())
        self.assertFalse(controller.searching)

    def test_cancel_search_restores_cursor(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.descend()  # to RECORD in voices
        controller.begin_search()
        for ch in "s1":
            controller.extend_search(ch)
        # Should have jumped to sounds section.
        self.assertEqual(controller.editor.state.section.value, "sounds")
        self.assertTrue(controller.cancel_search())
        self.assertFalse(controller.searching)
        self.assertEqual(controller.editor.state.section.value, "voices")

    def test_ascend_while_searching_cancels_search(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.descend()
        controller.begin_search()
        for ch in "s1":
            controller.extend_search(ch)
        ok = controller.ascend()
        self.assertTrue(ok)
        self.assertFalse(controller.searching)
        # Cursor restored to voices RECORD level, not ascended further.
        self.assertEqual(controller.editor.state.section.value, "voices")

    def test_extend_without_begin_raises(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        with self.assertRaises(SettingsControllerError):
            controller.extend_search("a")

    def test_backspace_without_begin_raises(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        with self.assertRaises(SettingsControllerError):
            controller.backspace_search()

    def test_extend_rejects_multi_character(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.begin_search()
        with self.assertRaises(ValueError):
            controller.extend_search("ab")

    def test_begin_search_while_editing_cancels_edit(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        controller.descend()
        controller.descend()
        controller.next()  # engine field
        controller.begin_edit()
        controller.extend_edit("s")
        self.assertTrue(controller.editing)
        self.assertTrue(controller.begin_search())
        self.assertFalse(controller.editing)
        self.assertTrue(controller.searching)

    def test_undo_is_noop_while_searching(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        # Land a real edit so the undo stack has something to revert.
        controller.descend()
        controller.descend()
        controller.next()  # engine
        controller.begin_edit()
        for ch in "sapi":
            controller.extend_edit(ch)
        controller.commit_edit()
        # Now start a search and confirm undo is refused.
        controller.begin_search()
        self.assertFalse(controller.undo())
        self.assertEqual(controller.bank.voices[0].engine, "sapi")

    def test_commit_without_search_returns_false(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        self.assertFalse(controller.commit_search())

    def test_cancel_without_search_returns_false(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        self.assertFalse(controller.cancel_search())


class SaveTests(unittest.TestCase):

    def test_save_to_configured_path(self) -> None:
        bus = EventBus()
        saved: list[object] = []
        bus.subscribe(EventType.SETTINGS_SAVED, saved.append)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bank.json"
            controller = SettingsController(bus, _bank(), save_path=path)
            controller.open()
            target = controller.save()
            self.assertEqual(target, path)
            self.assertTrue(path.exists())
        self.assertEqual(len(saved), 1)

    def test_save_with_explicit_path_overrides_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            configured = Path(tmp) / "a.json"
            explicit = Path(tmp) / "b.json"
            controller = SettingsController(EventBus(), _bank(), save_path=configured)
            controller.open()
            controller.save(explicit)
            self.assertTrue(explicit.exists())
            self.assertFalse(configured.exists())

    def test_save_without_any_path_raises(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        with self.assertRaises(SettingsControllerError):
            controller.save()


class ResetTests(unittest.TestCase):
    """F21c: reset confirmation sub-mode wiring at the controller layer."""

    def _defaults(self) -> SoundBank:
        return SoundBank(
            voices=(Voice(id="v1", rate=1.0), Voice(id="v2", rate=1.2)),
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

    def test_controller_without_defaults_refuses_begin_reset(self) -> None:
        controller = SettingsController(EventBus(), _bank())
        controller.open()
        self.assertFalse(controller.begin_reset(ResetScope.BANK))
        self.assertFalse(controller.resetting)

    def test_begin_reset_defaults_to_cursor_level_scope(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        self.assertTrue(controller.begin_reset())  # scope=None → SECTION at top
        self.assertEqual(controller.reset_scope, ResetScope.SECTION)

    def test_begin_reset_without_open_session_returns_false(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        self.assertFalse(controller.begin_reset(ResetScope.BANK))

    def test_begin_reset_while_searching_is_refused(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        controller.begin_search()
        self.assertTrue(controller.searching)
        self.assertFalse(controller.begin_reset(ResetScope.BANK))
        self.assertFalse(controller.resetting)

    def test_begin_reset_while_editing_cancels_edit(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        controller.descend()
        controller.descend()
        controller.next()  # engine field
        controller.begin_edit()
        controller.extend_edit("s")
        self.assertTrue(controller.editing)
        self.assertTrue(controller.begin_reset(ResetScope.FIELD))
        self.assertFalse(controller.editing)
        self.assertTrue(controller.resetting)

    def test_confirm_reset_applies_change_and_closes(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        # Stage a difference at voices[0].engine so the reset has something to do.
        controller.descend()
        controller.descend()
        controller.next()  # engine
        controller.begin_edit()
        for ch in "sapi":
            controller.extend_edit(ch)
        controller.commit_edit()
        controller.ascend()  # RECORD
        self.assertTrue(controller.begin_reset(ResetScope.RECORD))
        self.assertTrue(controller.confirm_reset())
        self.assertFalse(controller.resetting)
        self.assertEqual(controller.bank.voices[0].engine, "")

    def test_cancel_reset_leaves_bank_untouched(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        controller.descend()
        controller.descend()
        controller.next()
        controller.begin_edit()
        for ch in "sapi":
            controller.extend_edit(ch)
        controller.commit_edit()
        controller.ascend()
        self.assertTrue(controller.begin_reset(ResetScope.RECORD))
        self.assertTrue(controller.cancel_reset())
        self.assertFalse(controller.resetting)
        self.assertEqual(controller.bank.voices[0].engine, "sapi")

    def test_ascend_cancels_a_pending_reset_and_returns_true(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        controller.begin_reset(ResetScope.BANK)
        self.assertTrue(controller.resetting)
        still_open = controller.ascend()
        self.assertTrue(still_open)
        self.assertFalse(controller.resetting)

    def test_undo_is_refused_while_reset_is_pending(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        # Stage an edit so undo would have something to revert otherwise.
        controller.descend()
        controller.descend()
        controller.next()  # engine
        controller.begin_edit()
        for ch in "sapi":
            controller.extend_edit(ch)
        controller.commit_edit()
        controller.ascend()
        controller.ascend()
        controller.begin_reset(ResetScope.SECTION)
        self.assertFalse(controller.undo())
        self.assertEqual(controller.bank.voices[0].engine, "sapi")

    def test_begin_edit_refused_while_reset_is_pending(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        controller.descend()
        controller.descend()
        controller.next()
        controller.begin_reset(ResetScope.FIELD)
        with self.assertRaises(SettingsControllerError):
            controller.begin_edit()

    def test_begin_search_refused_while_reset_is_pending(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        controller.begin_reset(ResetScope.BANK)
        self.assertFalse(controller.begin_search())
        self.assertFalse(controller.searching)

    def test_confirm_reset_without_pending_returns_false(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        self.assertFalse(controller.confirm_reset())

    def test_cancel_reset_without_pending_returns_false(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        self.assertFalse(controller.cancel_reset())

    def test_default_reset_scope_matches_editor_cursor(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        self.assertEqual(controller.default_reset_scope(), ResetScope.SECTION)
        controller.descend()
        self.assertEqual(controller.default_reset_scope(), ResetScope.RECORD)

    def test_reset_scope_property_is_none_when_not_resetting(self) -> None:
        controller = SettingsController(
            EventBus(), _bank(), defaults_bank=self._defaults()
        )
        controller.open()
        self.assertIsNone(controller.reset_scope)


if __name__ == "__main__":
    unittest.main()
