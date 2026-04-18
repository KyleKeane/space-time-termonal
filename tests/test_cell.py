"""Unit tests for the Cell data model."""

from __future__ import annotations

import time
import unittest

from asat.cell import Cell, CellStatus


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


if __name__ == "__main__":
    unittest.main()
