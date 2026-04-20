"""Unit tests for asat.outline (F27 scope helpers)."""

from __future__ import annotations

import unittest

from asat.cell import Cell
from asat.outline import enclosing_heading_index, scope_range, visible_indices


def _outline() -> list[Cell]:
    """A mixed outline used by most tests:
      [0] H1 Intro
      [1] cmd ls
      [2] H2 Setup
      [3] cmd install
      [4] H3 Fixtures
      [5] cmd make
      [6] H2 Training
      [7] cmd train
      [8] H1 Runs
      [9] cmd run
    """
    return [
        Cell.new_heading(1, "Intro"),
        Cell.new("ls"),
        Cell.new_heading(2, "Setup"),
        Cell.new("install"),
        Cell.new_heading(3, "Fixtures"),
        Cell.new("make"),
        Cell.new_heading(2, "Training"),
        Cell.new("train"),
        Cell.new_heading(1, "Runs"),
        Cell.new("run"),
    ]


class ScopeRangeTests(unittest.TestCase):
    """scope_range returns a [start, end) span that includes children."""

    def test_h1_spans_through_nested_children_until_next_h1(self) -> None:
        cells = _outline()
        start, end = scope_range(cells, 0)  # H1 Intro
        self.assertEqual((start, end), (0, 8))

    def test_h2_spans_its_h3_child_and_stops_at_sibling_h2(self) -> None:
        cells = _outline()
        start, end = scope_range(cells, 2)  # H2 Setup
        self.assertEqual((start, end), (2, 6))

    def test_h3_spans_only_its_own_tail(self) -> None:
        cells = _outline()
        start, end = scope_range(cells, 4)  # H3 Fixtures
        self.assertEqual((start, end), (4, 6))

    def test_trailing_h1_spans_to_end_of_list(self) -> None:
        cells = _outline()
        start, end = scope_range(cells, 8)  # H1 Runs
        self.assertEqual((start, end), (8, 10))

    def test_heading_with_no_following_cells_has_unit_span(self) -> None:
        cells = [Cell.new_heading(1, "Alone")]
        self.assertEqual(scope_range(cells, 0), (0, 1))

    def test_same_level_sibling_terminates_scope(self) -> None:
        cells = [
            Cell.new_heading(2, "A"),
            Cell.new("x"),
            Cell.new_heading(2, "B"),
        ]
        self.assertEqual(scope_range(cells, 0), (0, 2))

    def test_shallower_sibling_terminates_scope(self) -> None:
        cells = [
            Cell.new_heading(3, "Deep"),
            Cell.new("x"),
            Cell.new_heading(1, "Shallow"),
        ]
        self.assertEqual(scope_range(cells, 0), (0, 2))

    def test_non_heading_index_raises_value_error(self) -> None:
        cells = _outline()
        with self.assertRaises(ValueError):
            scope_range(cells, 1)  # cmd cell

    def test_out_of_range_index_raises_index_error(self) -> None:
        cells = _outline()
        with self.assertRaises(IndexError):
            scope_range(cells, 99)
        with self.assertRaises(IndexError):
            scope_range(cells, -1)

    def test_empty_cells_list_raises_index_error(self) -> None:
        with self.assertRaises(IndexError):
            scope_range([], 0)


class EnclosingHeadingIndexTests(unittest.TestCase):
    """enclosing_heading_index walks backward from the target index."""

    def test_heading_cell_returns_itself(self) -> None:
        cells = _outline()
        self.assertEqual(enclosing_heading_index(cells, 0), 0)
        self.assertEqual(enclosing_heading_index(cells, 4), 4)

    def test_non_heading_finds_nearest_preceding_heading(self) -> None:
        cells = _outline()
        self.assertEqual(enclosing_heading_index(cells, 3), 2)  # under H2 Setup
        self.assertEqual(enclosing_heading_index(cells, 5), 4)  # under H3
        self.assertEqual(enclosing_heading_index(cells, 7), 6)  # under H2 Training

    def test_cell_before_any_heading_has_no_scope(self) -> None:
        cells = [Cell.new("preamble"), Cell.new_heading(1, "First")]
        self.assertIsNone(enclosing_heading_index(cells, 0))

    def test_out_of_range_returns_none(self) -> None:
        cells = _outline()
        self.assertIsNone(enclosing_heading_index(cells, 99))
        self.assertIsNone(enclosing_heading_index(cells, -1))

    def test_empty_cells_returns_none(self) -> None:
        self.assertIsNone(enclosing_heading_index([], 0))


class VisibleIndicesTests(unittest.TestCase):
    """visible_indices skips cells inside collapsed heading scopes."""

    def test_uncollapsed_session_returns_every_index(self) -> None:
        cells = _outline()
        self.assertEqual(visible_indices(cells), list(range(len(cells))))

    def test_collapsed_heading_hides_its_children(self) -> None:
        cells = _outline()
        cells[2].collapsed = True  # H2 Setup [2, 6)
        # Indices 3, 4, 5 are hidden; the H2 itself stays visible.
        self.assertEqual(visible_indices(cells), [0, 1, 2, 6, 7, 8, 9])

    def test_collapsed_h1_absorbs_nested_headings(self) -> None:
        cells = _outline()
        cells[0].collapsed = True  # H1 Intro [0, 8)
        # Only the collapsed H1, the trailing H1, and its body survive.
        self.assertEqual(visible_indices(cells), [0, 8, 9])

    def test_nested_collapses_yield_each_index_once(self) -> None:
        cells = _outline()
        cells[0].collapsed = True  # H1 Intro
        cells[2].collapsed = True  # H2 Setup (nested inside H1)
        # H2 is hidden by H1; only the H1 heading, the trailing H1,
        # and its body are visible.
        self.assertEqual(visible_indices(cells), [0, 8, 9])

    def test_heading_without_body_does_not_hide_anything(self) -> None:
        cells = [
            Cell.new_heading(1, "A"),
            Cell.new_heading(1, "B"),
            Cell.new("x"),
        ]
        cells[0].collapsed = True  # span is [0, 1) — nothing to hide
        self.assertEqual(visible_indices(cells), [0, 1, 2])

    def test_empty_cells_returns_empty_list(self) -> None:
        self.assertEqual(visible_indices([]), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
