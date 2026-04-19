"""Unit tests for the Session container."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asat.cell import Cell
from asat.session import Session, SessionError


def _cells(commands: list[str]) -> list[Cell]:
    """Return a list of fresh pending cells for each command."""
    return [Cell.new(command) for command in commands]


class SessionConstructionTests(unittest.TestCase):

    def test_new_session_is_empty(self) -> None:
        session = Session.new()
        self.assertEqual(len(session), 0)
        self.assertIsNone(session.active_cell_id)
        self.assertEqual(session.created_at, session.updated_at)

    def test_iteration_yields_cells_in_order(self) -> None:
        session = Session.new()
        cells = _cells(["a", "b", "c"])
        for cell in cells:
            session.add_cell(cell)
        self.assertEqual([c.command for c in session], ["a", "b", "c"])


class SessionAddRemoveTests(unittest.TestCase):

    def test_add_appends_by_default(self) -> None:
        session = Session.new()
        first, second = _cells(["first", "second"])
        session.add_cell(first)
        session.add_cell(second)
        self.assertEqual(session.cells, [first, second])

    def test_add_at_position_inserts(self) -> None:
        session = Session.new()
        a, b, c = _cells(["a", "b", "c"])
        session.add_cell(a)
        session.add_cell(b)
        session.add_cell(c, position=1)
        self.assertEqual([cell.command for cell in session], ["a", "c", "b"])

    def test_add_duplicate_id_raises(self) -> None:
        session = Session.new()
        cell = Cell.new("x")
        session.add_cell(cell)
        with self.assertRaises(SessionError):
            session.add_cell(cell)

    def test_add_out_of_range_raises(self) -> None:
        session = Session.new()
        with self.assertRaises(SessionError):
            session.add_cell(Cell.new("x"), position=5)

    def test_remove_returns_cell_and_clears_active(self) -> None:
        session = Session.new()
        a, b = _cells(["a", "b"])
        session.add_cell(a)
        session.add_cell(b)
        session.set_active(a.cell_id)
        removed = session.remove_cell(a.cell_id)
        self.assertIs(removed, a)
        self.assertIsNone(session.active_cell_id)
        self.assertEqual(session.cells, [b])

    def test_remove_unknown_id_raises(self) -> None:
        session = Session.new()
        with self.assertRaises(SessionError):
            session.remove_cell("missing")


class SessionMoveTests(unittest.TestCase):

    def test_move_cell_changes_order(self) -> None:
        session = Session.new()
        a, b, c = _cells(["a", "b", "c"])
        for cell in (a, b, c):
            session.add_cell(cell)
        session.move_cell(c.cell_id, 0)
        self.assertEqual([cell.command for cell in session], ["c", "a", "b"])

    def test_move_cell_to_same_position_is_noop(self) -> None:
        session = Session.new()
        a, b = _cells(["a", "b"])
        session.add_cell(a)
        session.add_cell(b)
        session.move_cell(a.cell_id, 0)
        self.assertEqual([cell.command for cell in session], ["a", "b"])

    def test_move_cell_out_of_range_raises(self) -> None:
        session = Session.new()
        a = Cell.new("a")
        session.add_cell(a)
        with self.assertRaises(SessionError):
            session.move_cell(a.cell_id, 5)


class SessionFocusAndNavigationTests(unittest.TestCase):

    def test_set_active_requires_membership(self) -> None:
        session = Session.new()
        with self.assertRaises(SessionError):
            session.set_active("nope")

    def test_active_cell_returns_focused_cell(self) -> None:
        session = Session.new()
        cell = Cell.new("a")
        session.add_cell(cell)
        session.set_active(cell.cell_id)
        self.assertIs(session.active_cell(), cell)

    def test_active_cell_is_none_when_unset(self) -> None:
        session = Session.new()
        self.assertIsNone(session.active_cell())

    def test_next_and_previous_at_boundaries(self) -> None:
        session = Session.new()
        a, b, c = _cells(["a", "b", "c"])
        for cell in (a, b, c):
            session.add_cell(cell)
        self.assertIs(session.next_cell(a.cell_id), b)
        self.assertIs(session.previous_cell(b.cell_id), a)
        self.assertIsNone(session.previous_cell(a.cell_id))
        self.assertIsNone(session.next_cell(c.cell_id))


class SessionCommandHistoryTests(unittest.TestCase):
    """F4: Session tracks the commands the user has submitted."""

    def test_record_appends_non_empty(self) -> None:
        session = Session.new()
        appended = session.record_command("echo hi")
        self.assertTrue(appended)
        self.assertEqual(session.command_history, ["echo hi"])

    def test_record_drops_whitespace_only(self) -> None:
        session = Session.new()
        self.assertFalse(session.record_command("   "))
        self.assertFalse(session.record_command(""))
        self.assertEqual(session.command_history, [])

    def test_record_collapses_consecutive_duplicates(self) -> None:
        session = Session.new()
        session.record_command("pytest")
        appended = session.record_command("pytest")
        self.assertFalse(appended)
        self.assertEqual(session.command_history, ["pytest"])

    def test_record_keeps_non_consecutive_duplicates(self) -> None:
        session = Session.new()
        session.record_command("ls")
        session.record_command("pwd")
        session.record_command("ls")
        self.assertEqual(session.command_history, ["ls", "pwd", "ls"])


class SessionBookmarkTests(unittest.TestCase):
    """F35: Session tracks user-named cell bookmarks."""

    def test_add_bookmark_stores_name(self) -> None:
        session = Session.new()
        cell = Cell.new("echo hi")
        session.add_cell(cell)
        normalised = session.add_bookmark("setup", cell.cell_id)
        self.assertEqual(normalised, "setup")
        self.assertEqual(session.get_bookmark("setup"), cell.cell_id)

    def test_add_bookmark_strips_surrounding_whitespace(self) -> None:
        session = Session.new()
        cell = Cell.new("a")
        session.add_cell(cell)
        session.add_bookmark("  setup  ", cell.cell_id)
        self.assertEqual(session.get_bookmark("setup"), cell.cell_id)

    def test_add_bookmark_rejects_empty_name(self) -> None:
        session = Session.new()
        cell = Cell.new("a")
        session.add_cell(cell)
        with self.assertRaises(SessionError):
            session.add_bookmark("   ", cell.cell_id)

    def test_add_bookmark_requires_existing_cell(self) -> None:
        session = Session.new()
        with self.assertRaises(SessionError):
            session.add_bookmark("setup", "missing-id")

    def test_add_bookmark_rebinds_existing_name(self) -> None:
        session = Session.new()
        a, b = _cells(["a", "b"])
        session.add_cell(a)
        session.add_cell(b)
        session.add_bookmark("here", a.cell_id)
        session.add_bookmark("here", b.cell_id)
        self.assertEqual(session.get_bookmark("here"), b.cell_id)

    def test_remove_bookmark_returns_cell_id(self) -> None:
        session = Session.new()
        cell = Cell.new("a")
        session.add_cell(cell)
        session.add_bookmark("setup", cell.cell_id)
        cleared = session.remove_bookmark("setup")
        self.assertEqual(cleared, cell.cell_id)
        self.assertIsNone(session.get_bookmark("setup"))

    def test_remove_unknown_bookmark_raises(self) -> None:
        session = Session.new()
        with self.assertRaises(SessionError):
            session.remove_bookmark("nope")

    def test_list_bookmarks_returns_sorted_pairs(self) -> None:
        session = Session.new()
        a, b = _cells(["a", "b"])
        session.add_cell(a)
        session.add_cell(b)
        session.add_bookmark("zeta", a.cell_id)
        session.add_bookmark("alpha", b.cell_id)
        self.assertEqual(
            session.list_bookmarks(),
            [("alpha", b.cell_id), ("zeta", a.cell_id)],
        )

    def test_remove_cell_prunes_dangling_bookmarks(self) -> None:
        session = Session.new()
        a, b = _cells(["a", "b"])
        session.add_cell(a)
        session.add_cell(b)
        session.add_bookmark("first", a.cell_id)
        session.add_bookmark("also_first", a.cell_id)
        session.add_bookmark("second", b.cell_id)
        session.remove_cell(a.cell_id)
        self.assertEqual(
            session.list_bookmarks(), [("second", b.cell_id)]
        )


class SessionSerializationTests(unittest.TestCase):

    def test_round_trip_preserves_state(self) -> None:
        session = Session.new()
        a, b = _cells(["a", "b"])
        session.add_cell(a)
        session.add_cell(b)
        session.set_active(b.cell_id)
        session.metadata["project"] = "asat"
        session.record_command("ls")
        session.record_command("pwd")
        session.add_bookmark("start", a.cell_id)
        restored = Session.from_dict(session.to_dict())
        self.assertEqual(restored.session_id, session.session_id)
        self.assertEqual([c.command for c in restored], ["a", "b"])
        self.assertEqual(restored.active_cell_id, b.cell_id)
        self.assertEqual(restored.metadata, {"project": "asat"})
        self.assertEqual(restored.command_history, ["ls", "pwd"])
        self.assertEqual(restored.bookmarks, {"start": a.cell_id})

    def test_save_and_load_roundtrip_on_disk(self) -> None:
        session = Session.new()
        session.add_cell(Cell.new("echo hi"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            session.save(path)
            loaded = Session.load(path)
        self.assertEqual(loaded.session_id, session.session_id)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded.cells[0].command, "echo hi")


if __name__ == "__main__":
    unittest.main()
