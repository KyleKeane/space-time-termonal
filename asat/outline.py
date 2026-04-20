"""Pure helpers for reasoning about heading scopes in a cell list (F27).

A "scope" is the contiguous run of cells that belong to a heading: the
heading itself, followed by every cell up to (but not including) the
next heading whose level is the same as or shallower than the
heading's. These helpers are intentionally framework-free — they take
plain lists of `Cell` and return index ranges — so the notebook, the
clipboard, and any future outline view can share one canonical
definition of "what does this section contain".

Typical use:

    from asat.outline import scope_range

    start, end = scope_range(session.cells, heading_index)
    section = session.cells[start:end]

All functions are safe on empty inputs and raise only when given an
index that is out of range or not a heading.
"""

from __future__ import annotations

from typing import Sequence

from asat.cell import Cell, CellKind


def scope_range(cells: Sequence[Cell], heading_index: int) -> tuple[int, int]:
    """Return [start, end) for the section headed by `cells[heading_index]`.

    `start` is always `heading_index`. `end` is the index of the next
    heading whose level is <= the heading's level, or `len(cells)` if
    no such heading exists. Children (headings with a *strictly
    greater* level) remain inside the returned span.

    Raises `IndexError` if `heading_index` is out of range and
    `ValueError` if the target cell is not a heading.
    """
    if not 0 <= heading_index < len(cells):
        raise IndexError(
            f"heading_index {heading_index} out of range for {len(cells)} cells"
        )
    head = cells[heading_index]
    if head.kind is not CellKind.HEADING or head.heading_level is None:
        raise ValueError(
            f"cells[{heading_index}] is not a heading "
            f"(kind={head.kind.value})"
        )
    level = head.heading_level
    end = len(cells)
    for j in range(heading_index + 1, len(cells)):
        cand = cells[j]
        if (
            cand.kind is CellKind.HEADING
            and cand.heading_level is not None
            and cand.heading_level <= level
        ):
            end = j
            break
    return heading_index, end


def enclosing_heading_index(cells: Sequence[Cell], index: int) -> int | None:
    """Walk backward from `index` to find the scope's heading.

    If `cells[index]` is itself a heading, returns `index`. If `index`
    sits under a heading, returns that heading's index. Returns None
    when no preceding heading exists (i.e. the cell lives before any
    outline entry) or when `index` is out of range.
    """
    if not 0 <= index < len(cells):
        return None
    if cells[index].kind is CellKind.HEADING:
        return index
    j = index - 1
    while j >= 0:
        if cells[j].kind is CellKind.HEADING:
            return j
        j -= 1
    return None
