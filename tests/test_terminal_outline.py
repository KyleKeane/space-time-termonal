"""Tests for the on-screen outline view (PR 2 of the MVP stabilization).

Split into two sections:

* Pure ``render_outline`` behaviour — indentation, focus arrow,
  heading / command / text rendering, collapsed scopes, width
  truncation.
* ``TerminalRenderer`` integration — the optional outline pane opts
  in via ``show_outline=True``, subscribes to structural events, and
  writes an ``OUTLINE_HEADER`` / ``OUTLINE_FOOTER`` framed block to
  the stream on every repaint.
"""

from __future__ import annotations

import io
import unittest

from asat.cell import Cell
from asat.event_bus import EventBus, publish_event
from asat.events import EventType
from asat.outline import render_outline
from asat.terminal import OUTLINE_FOOTER, OUTLINE_HEADER, TerminalRenderer


class RenderOutlineTests(unittest.TestCase):
    def _sample(self) -> list[Cell]:
        return [
            Cell.new_heading(1, "Intro"),
            Cell.new("ls"),
            Cell.new_heading(2, "Setup"),
            Cell.new_text("install the deps"),
            Cell.new("pip install ."),
        ]

    def test_empty_list_returns_empty_list(self) -> None:
        self.assertEqual(render_outline([], focus_cell_id=None), [])

    def test_headings_indent_by_two_spaces_per_level(self) -> None:
        cells = self._sample()
        lines = render_outline(cells, focus_cell_id=None)
        # "  " focus-gap + "" indent + "H1 Intro"
        self.assertEqual(lines[0], "  H1 Intro")
        # "  " focus-gap + "  " indent + "H2 Setup"
        self.assertEqual(lines[2], "    H2 Setup")

    def test_commands_indent_under_enclosing_heading(self) -> None:
        cells = self._sample()
        lines = render_outline(cells, focus_cell_id=None)
        # ls sits under H1 (level 1) -> indent of 2 spaces after gap
        self.assertEqual(lines[1], "    $ ls")
        # pip install . sits under H2 -> indent of 4 spaces after gap
        self.assertEqual(lines[4], "      $ pip install .")

    def test_text_cells_render_with_quotes(self) -> None:
        cells = self._sample()
        lines = render_outline(cells, focus_cell_id=None)
        self.assertEqual(lines[3], '      "install the deps"')

    def test_focus_arrow_marks_current_cell(self) -> None:
        cells = self._sample()
        focused_id = cells[1].cell_id  # ls
        lines = render_outline(cells, focus_cell_id=focused_id)
        self.assertTrue(lines[1].startswith("> "))
        # All others keep the two-space gap so columns line up.
        for other in (0, 2, 3, 4):
            self.assertTrue(lines[other].startswith("  "))
            self.assertFalse(lines[other].startswith("> "))

    def test_cells_before_any_heading_have_no_indent(self) -> None:
        cells = [Cell.new("pwd"), Cell.new_heading(1, "Later")]
        lines = render_outline(cells, focus_cell_id=None)
        self.assertEqual(lines[0], "  $ pwd")
        self.assertEqual(lines[1], "  H1 Later")

    def test_empty_command_renders_placeholder(self) -> None:
        cells = [Cell.new("")]
        lines = render_outline(cells, focus_cell_id=None)
        self.assertEqual(lines, ["  $ (empty)"])

    def test_collapsed_heading_is_labelled_and_hides_children(self) -> None:
        cells = self._sample()
        cells[0].collapsed = True
        lines = render_outline(cells, focus_cell_id=None)
        # Only the collapsed H1 remains; the rest is hidden.
        self.assertEqual(len(lines), 1)
        self.assertIn("[collapsed]", lines[0])
        self.assertIn("H1 Intro", lines[0])

    def test_max_width_truncates_long_lines_with_ellipsis(self) -> None:
        cells = [Cell.new("x" * 200)]
        lines = render_outline(cells, focus_cell_id=None, max_width=12)
        self.assertEqual(len(lines[0]), 12)
        self.assertTrue(lines[0].endswith("\u2026"))

    def test_multiline_command_keeps_only_first_line(self) -> None:
        cells = [Cell.new("echo hi\necho bye")]
        lines = render_outline(cells, focus_cell_id=None)
        self.assertEqual(lines, ["  $ echo hi"])

    def test_multiline_text_keeps_only_first_line(self) -> None:
        cells = [Cell.new_text("first line\nsecond line")]
        lines = render_outline(cells, focus_cell_id=None)
        self.assertEqual(lines, ['  "first line"'])


class TerminalRendererOutlineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.stream = io.StringIO()
        self.cells: list[Cell] = []
        self.renderer = TerminalRenderer(
            self.bus,
            stream=self.stream,
            show_trace=False,
            show_outline=True,
            cells_provider=lambda: self.cells,
        )

    def _emit(self, event_type: EventType, payload: dict) -> None:
        publish_event(self.bus, event_type, payload, source="test")

    def _latest_outline_block(self) -> list[str]:
        """Return the most recent ``OUTLINE_HEADER`` ... ``OUTLINE_FOOTER`` span."""
        text = self.stream.getvalue()
        # Slice from the last header so earlier repaints do not confuse
        # assertions that target the current state.
        last_header_at = text.rfind(OUTLINE_HEADER)
        if last_header_at == -1:
            return []
        tail = text[last_header_at:]
        footer_at = tail.find(OUTLINE_FOOTER)
        body = tail[: footer_at if footer_at != -1 else len(tail)]
        lines = body.splitlines()
        return lines[1:]  # drop the header line itself

    def test_requires_cells_provider_when_outline_is_on(self) -> None:
        with self.assertRaises(ValueError):
            TerminalRenderer(
                EventBus(),
                stream=io.StringIO(),
                show_trace=False,
                show_outline=True,
                cells_provider=None,
            )

    def test_focus_changed_paints_a_framed_outline_block(self) -> None:
        self.cells = [Cell.new("ls")]
        cell_id = self.cells[0].cell_id
        self._emit(
            EventType.FOCUS_CHANGED,
            {"new_mode": "input", "new_cell_id": cell_id},
        )
        text = self.stream.getvalue()
        self.assertIn(OUTLINE_HEADER, text)
        self.assertIn(OUTLINE_FOOTER, text)
        self.assertIn("$ ls", text)

    def test_focus_arrow_follows_the_focused_cell(self) -> None:
        a = Cell.new("a")
        b = Cell.new("b")
        self.cells = [a, b]
        self._emit(
            EventType.FOCUS_CHANGED,
            {"new_mode": "notebook", "new_cell_id": a.cell_id},
        )
        block = self._latest_outline_block()
        self.assertEqual(block[0], "> $ a")
        self.assertEqual(block[1], "  $ b")
        self._emit(
            EventType.FOCUS_CHANGED,
            {"new_mode": "notebook", "new_cell_id": b.cell_id},
        )
        block = self._latest_outline_block()
        self.assertEqual(block[0], "  $ a")
        self.assertEqual(block[1], "> $ b")

    def test_cell_created_triggers_a_repaint(self) -> None:
        # Start empty, then "add" a cell and fire CELL_CREATED. The
        # renderer must pull the new cell list via the provider.
        self._emit(
            EventType.FOCUS_CHANGED,
            {"new_mode": "notebook", "new_cell_id": None},
        )
        block = self._latest_outline_block()
        self.assertIn("(no cells yet)", block[0])

        cell = Cell.new("echo hi")
        self.cells = [cell]
        self._emit(
            EventType.CELL_CREATED,
            {"cell_id": cell.cell_id, "command": cell.command, "index": 0},
        )
        block = self._latest_outline_block()
        self.assertEqual(block, ["  $ echo hi"])

    def test_cell_removed_triggers_a_repaint(self) -> None:
        cell = Cell.new("pwd")
        self.cells = [cell]
        self._emit(
            EventType.CELL_CREATED,
            {"cell_id": cell.cell_id, "command": "pwd", "index": 0},
        )
        self.cells = []
        self._emit(
            EventType.CELL_REMOVED,
            {"cell_id": cell.cell_id, "command": "pwd", "index": 0},
        )
        block = self._latest_outline_block()
        self.assertIn("(no cells yet)", block[0])

    def test_show_trace_false_suppresses_session_banner(self) -> None:
        self._emit(
            EventType.SESSION_CREATED, {"session_id": "abc"},
        )
        self.assertNotIn("session abc ready", self.stream.getvalue())

    def test_focus_changed_does_not_write_status_line_when_trace_off(self) -> None:
        cell = Cell.new("ls")
        self.cells = [cell]
        self._emit(
            EventType.FOCUS_CHANGED,
            {"new_mode": "input", "new_cell_id": cell.cell_id},
        )
        self.assertNotIn("[input", self.stream.getvalue())


class TerminalRendererBothPanesTests(unittest.TestCase):
    """Trace + outline coexist without clobbering each other."""

    def test_session_banner_and_outline_both_render(self) -> None:
        bus = EventBus()
        stream = io.StringIO()
        cells: list[Cell] = []
        TerminalRenderer(
            bus,
            stream=stream,
            show_trace=True,
            show_outline=True,
            cells_provider=lambda: cells,
        )
        publish_event(
            bus, EventType.SESSION_CREATED, {"session_id": "xyz"}, source="t"
        )
        cell = Cell.new("ls")
        cells.append(cell)
        publish_event(
            bus,
            EventType.FOCUS_CHANGED,
            {"new_mode": "input", "new_cell_id": cell.cell_id},
            source="t",
        )
        text = stream.getvalue()
        self.assertIn("session xyz ready", text)
        self.assertIn("[input", text)
        self.assertIn(OUTLINE_HEADER, text)
        self.assertIn("> $ ls", text)


if __name__ == "__main__":
    unittest.main()
