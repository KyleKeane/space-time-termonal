"""Unit tests for the Cell data model."""

from __future__ import annotations

import time
import unittest

from asat.cell import Cell, CellKind, CellStatus


class CellFactoryTests(unittest.TestCase):
    """Behaviour of Cell.new and initial state."""

    def test_new_cell_starts_pending(self) -> None:
        cell = Cell.new("echo hi")
        self.assertEqual(cell.command, "echo hi")
        self.assertEqual(cell.status, CellStatus.PENDING)
        self.assertEqual(cell.stdout, "")
        self.assertEqual(cell.stderr, "")
        self.assertIsNone(cell.exit_code)
        self.assertIsNone(cell.parent_id)

    def test_new_cell_has_unique_id(self) -> None:
        first = Cell.new("ls")
        second = Cell.new("ls")
        self.assertNotEqual(first.cell_id, second.cell_id)

    def test_new_cell_records_timestamps(self) -> None:
        cell = Cell.new("pwd")
        self.assertEqual(cell.created_at, cell.updated_at)
        self.assertIsNotNone(cell.created_at.tzinfo)

    def test_new_cell_accepts_parent_id(self) -> None:
        cell = Cell.new("retry", parent_id="abc123")
        self.assertEqual(cell.parent_id, "abc123")


class CellLifecycleTests(unittest.TestCase):
    """Status transitions and output recording."""

    def test_mark_running_updates_status_and_timestamp(self) -> None:
        cell = Cell.new("sleep 1")
        original_updated = cell.updated_at
        time.sleep(0.001)
        cell.mark_running()
        self.assertEqual(cell.status, CellStatus.RUNNING)
        self.assertGreater(cell.updated_at, original_updated)

    def test_mark_completed_zero_exit_becomes_completed(self) -> None:
        cell = Cell.new("true")
        cell.mark_completed(stdout="ok\n", stderr="", exit_code=0)
        self.assertEqual(cell.status, CellStatus.COMPLETED)
        self.assertEqual(cell.stdout, "ok\n")
        self.assertEqual(cell.exit_code, 0)

    def test_mark_completed_nonzero_exit_becomes_failed(self) -> None:
        cell = Cell.new("false")
        cell.mark_completed(stdout="", stderr="boom\n", exit_code=1)
        self.assertEqual(cell.status, CellStatus.FAILED)
        self.assertEqual(cell.stderr, "boom\n")
        self.assertEqual(cell.exit_code, 1)

    def test_mark_cancelled(self) -> None:
        cell = Cell.new("sleep 60")
        cell.mark_running()
        cell.mark_cancelled()
        self.assertEqual(cell.status, CellStatus.CANCELLED)

    def test_update_command_clears_previous_output(self) -> None:
        cell = Cell.new("echo one")
        cell.mark_completed(stdout="one\n", stderr="", exit_code=0)
        cell.update_command("echo two")
        self.assertEqual(cell.command, "echo two")
        self.assertEqual(cell.stdout, "")
        self.assertEqual(cell.stderr, "")
        self.assertIsNone(cell.exit_code)
        self.assertEqual(cell.status, CellStatus.PENDING)


class CellSerializationTests(unittest.TestCase):
    """Round-trip a Cell through to_dict and from_dict."""

    def test_round_trip_preserves_all_fields(self) -> None:
        cell = Cell.new("echo hi", parent_id="parent123")
        cell.mark_completed(stdout="hi\n", stderr="warn\n", exit_code=0)
        cell.metadata["tag"] = "demo"
        restored = Cell.from_dict(cell.to_dict())
        self.assertEqual(restored.cell_id, cell.cell_id)
        self.assertEqual(restored.command, cell.command)
        self.assertEqual(restored.stdout, cell.stdout)
        self.assertEqual(restored.stderr, cell.stderr)
        self.assertEqual(restored.exit_code, cell.exit_code)
        self.assertEqual(restored.status, cell.status)
        self.assertEqual(restored.parent_id, cell.parent_id)
        self.assertEqual(restored.metadata, {"tag": "demo"})
        self.assertEqual(restored.created_at, cell.created_at)
        self.assertEqual(restored.updated_at, cell.updated_at)

    def test_to_dict_status_is_string(self) -> None:
        cell = Cell.new("echo")
        data = cell.to_dict()
        self.assertEqual(data["status"], "pending")
        self.assertIsInstance(data["created_at"], str)


class CellSnapshotTests(unittest.TestCase):
    """snapshot() returns a detached copy safe to hand to subscribers."""

    def test_snapshot_preserves_all_fields(self) -> None:
        cell = Cell.new("echo hi", parent_id="p1")
        cell.mark_completed(stdout="hi\n", stderr="", exit_code=0)
        cell.metadata["label"] = "one"
        snap = cell.snapshot()
        self.assertEqual(snap.cell_id, cell.cell_id)
        self.assertEqual(snap.command, cell.command)
        self.assertEqual(snap.stdout, cell.stdout)
        self.assertEqual(snap.exit_code, cell.exit_code)
        self.assertEqual(snap.status, cell.status)
        self.assertEqual(snap.parent_id, cell.parent_id)
        self.assertEqual(snap.metadata, {"label": "one"})

    def test_snapshot_is_detached_from_source(self) -> None:
        cell = Cell.new("echo hi")
        snap = cell.snapshot()
        cell.mark_completed(stdout="done\n", stderr="", exit_code=0)
        self.assertEqual(snap.stdout, "")
        self.assertEqual(snap.status, CellStatus.PENDING)

    def test_snapshot_metadata_is_independent(self) -> None:
        cell = Cell.new("echo hi")
        cell.metadata["k"] = "v"
        snap = cell.snapshot()
        cell.metadata["k"] = "mutated"
        self.assertEqual(snap.metadata, {"k": "v"})


class CellKindDefaultsTests(unittest.TestCase):
    """A Cell.new() result is a plain COMMAND cell."""

    def test_default_kind_is_command(self) -> None:
        cell = Cell.new("echo hi")
        self.assertEqual(cell.kind, CellKind.COMMAND)
        self.assertTrue(cell.is_executable)
        self.assertFalse(cell.is_heading)
        self.assertIsNone(cell.heading_level)
        self.assertIsNone(cell.heading_title)


class CellHeadingFactoryTests(unittest.TestCase):
    """Cell.new_heading produces structurally valid landmarks."""

    def test_new_heading_sets_kind_and_fields(self) -> None:
        cell = Cell.new_heading(2, "Setup")
        self.assertEqual(cell.kind, CellKind.HEADING)
        self.assertTrue(cell.is_heading)
        self.assertFalse(cell.is_executable)
        self.assertEqual(cell.heading_level, 2)
        self.assertEqual(cell.heading_title, "Setup")
        self.assertEqual(cell.command, "")
        # Headings are "always complete"; they carry no pending work.
        self.assertEqual(cell.status, CellStatus.COMPLETED)

    def test_new_heading_rejects_out_of_range_level(self) -> None:
        with self.assertRaises(ValueError):
            Cell.new_heading(0, "x")
        with self.assertRaises(ValueError):
            Cell.new_heading(7, "x")

    def test_new_heading_rejects_blank_title(self) -> None:
        with self.assertRaises(ValueError):
            Cell.new_heading(1, "")
        with self.assertRaises(ValueError):
            Cell.new_heading(1, "   ")

    def test_heading_cells_are_not_executable_and_refuse_exec_mutations(self) -> None:
        cell = Cell.new_heading(1, "Intro")
        with self.assertRaises(ValueError):
            cell.mark_running()
        with self.assertRaises(ValueError):
            cell.mark_completed("", "", 0)
        with self.assertRaises(ValueError):
            cell.mark_cancelled()
        with self.assertRaises(ValueError):
            cell.update_command("echo")

    def test_update_heading_edits_level_and_title(self) -> None:
        cell = Cell.new_heading(1, "Old")
        cell.update_heading(3, "New Title")
        self.assertEqual(cell.heading_level, 3)
        self.assertEqual(cell.heading_title, "New Title")

    def test_update_heading_refuses_on_command_cell(self) -> None:
        cell = Cell.new("echo")
        with self.assertRaises(ValueError):
            cell.update_heading(1, "nope")


class CellKindSerializationTests(unittest.TestCase):
    """Heading cells round-trip through to_dict/from_dict."""

    def test_heading_round_trip(self) -> None:
        cell = Cell.new_heading(4, "Runs")
        restored = Cell.from_dict(cell.to_dict())
        self.assertEqual(restored.kind, CellKind.HEADING)
        self.assertEqual(restored.heading_level, 4)
        self.assertEqual(restored.heading_title, "Runs")
        self.assertEqual(restored.cell_id, cell.cell_id)

    def test_command_cell_round_trip_preserves_kind(self) -> None:
        cell = Cell.new("ls")
        data = cell.to_dict()
        self.assertEqual(data["kind"], "command")
        restored = Cell.from_dict(data)
        self.assertEqual(restored.kind, CellKind.COMMAND)

    def test_legacy_dict_without_kind_defaults_to_command(self) -> None:
        # Older session JSON (pre-F61) has no `kind` field. It must
        # still load cleanly as a COMMAND cell.
        cell = Cell.new("ls")
        data = cell.to_dict()
        data.pop("kind", None)
        data.pop("heading_level", None)
        data.pop("heading_title", None)
        restored = Cell.from_dict(data)
        self.assertEqual(restored.kind, CellKind.COMMAND)
        self.assertIsNone(restored.heading_level)
        self.assertIsNone(restored.heading_title)


class CellSnapshotKindTests(unittest.TestCase):

    def test_heading_snapshot_preserves_kind(self) -> None:
        cell = Cell.new_heading(2, "Setup")
        snap = cell.snapshot()
        self.assertEqual(snap.kind, CellKind.HEADING)
        self.assertEqual(snap.heading_level, 2)
        self.assertEqual(snap.heading_title, "Setup")

    def test_text_snapshot_preserves_body(self) -> None:
        cell = Cell.new_text("We train for ten epochs.")
        snap = cell.snapshot()
        self.assertEqual(snap.kind, CellKind.TEXT)
        self.assertEqual(snap.text, "We train for ten epochs.")


class CellTextFactoryTests(unittest.TestCase):
    """Cell.new_text produces structurally valid prose cells (F27)."""

    def test_new_text_sets_kind_and_body(self) -> None:
        cell = Cell.new_text("Some prose.")
        self.assertEqual(cell.kind, CellKind.TEXT)
        self.assertTrue(cell.is_text)
        self.assertFalse(cell.is_executable)
        self.assertFalse(cell.is_heading)
        self.assertEqual(cell.text, "Some prose.")
        self.assertEqual(cell.command, "")
        self.assertEqual(cell.status, CellStatus.COMPLETED)

    def test_new_text_rejects_blank_body(self) -> None:
        with self.assertRaises(ValueError):
            Cell.new_text("")
        with self.assertRaises(ValueError):
            Cell.new_text("   ")

    def test_text_cells_are_not_executable_and_refuse_exec_mutations(self) -> None:
        cell = Cell.new_text("prose")
        with self.assertRaises(ValueError):
            cell.mark_running()
        with self.assertRaises(ValueError):
            cell.mark_completed("", "", 0)
        with self.assertRaises(ValueError):
            cell.mark_cancelled()
        with self.assertRaises(ValueError):
            cell.update_command("echo")

    def test_update_text_edits_body(self) -> None:
        cell = Cell.new_text("first draft")
        cell.update_text("revised")
        self.assertEqual(cell.text, "revised")

    def test_update_text_rejects_blank(self) -> None:
        cell = Cell.new_text("prose")
        with self.assertRaises(ValueError):
            cell.update_text("")
        with self.assertRaises(ValueError):
            cell.update_text("   ")

    def test_update_text_refuses_on_command_cell(self) -> None:
        cell = Cell.new("echo")
        with self.assertRaises(ValueError):
            cell.update_text("nope")

    def test_heading_fields_rejected_on_text_cell(self) -> None:
        # Construction-level guard: a direct TEXT cell with a heading
        # level should fail fast.
        from datetime import datetime
        with self.assertRaises(ValueError):
            Cell(
                cell_id="c1",
                command="",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                kind=CellKind.TEXT,
                text="prose",
                heading_level=1,
                heading_title="x",
            )

    def test_text_round_trips_through_to_dict(self) -> None:
        cell = Cell.new_text("note: verify fixtures")
        restored = Cell.from_dict(cell.to_dict())
        self.assertEqual(restored.kind, CellKind.TEXT)
        self.assertEqual(restored.text, "note: verify fixtures")
        self.assertEqual(restored.cell_id, cell.cell_id)


if __name__ == "__main__":
    unittest.main()
