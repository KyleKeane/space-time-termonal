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


def visible_indices(cells: Sequence[Cell]) -> list[int]:
    """Return the indices of cells that are not hidden by a collapsed heading.

    A collapsed heading (Cell with `kind is HEADING and collapsed is True`)
    is itself visible, but every cell inside its scope is hidden until
    the scope ends. Nested collapsed headings are absorbed by the outer
    scope — `visible_indices` yields each visible index exactly once.
    """
    hide_until: int = 0
    out: list[int] = []
    for idx, cell in enumerate(cells):
        if idx < hide_until:
            continue
        out.append(idx)
        if (
            cell.kind is CellKind.HEADING
            and cell.collapsed
            and cell.heading_level is not None
        ):
            _, end = scope_range(cells, idx)
            if end > hide_until:
                hide_until = end
    return out


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


_FOCUS_ARROW = "> "
_FOCUS_GAP = "  "


def render_outline(
    cells: Sequence[Cell],
    focus_cell_id: str | None,
    max_width: int = 80,
) -> list[str]:
    """Return one line per visible cell describing the outline.

    Heading cells are indented by ``(level - 1) * 2`` spaces and show
    as ``H{level} {title}``; a collapsed heading is suffixed with
    ``[collapsed]``. Non-heading cells live under their enclosing
    heading and indent one level deeper; commands render as
    ``$ {command}`` (or ``$ (empty)`` while the user has not typed
    anything), text cells render as ``"{first-line}"``.

    The cell whose id matches ``focus_cell_id`` is prefixed with
    ``"> "``; every other line is prefixed with ``"  "`` so columns
    line up. Cells hidden by a collapsed ancestor (see
    ``visible_indices``) are omitted. ``max_width`` truncates long
    lines with a trailing ``…`` so a narrow terminal never wraps.
    ``max_width`` values smaller than a handful of columns simply
    round up to a minimum of 8 to keep the arrow + ellipsis
    legible.
    """
    effective_width = max(max_width, 8)
    visible = visible_indices(cells)
    out: list[str] = []
    for index in visible:
        cell = cells[index]
        body = _format_outline_body(cells, index, cell)
        prefix = _FOCUS_ARROW if cell.cell_id == focus_cell_id else _FOCUS_GAP
        line = prefix + body
        if len(line) > effective_width:
            keep = max(effective_width - 1, 1)
            line = line[:keep] + "\u2026"
        out.append(line)
    return out


def _format_outline_body(
    cells: Sequence[Cell], index: int, cell: Cell
) -> str:
    """Return the indent + label portion of one outline line."""
    if cell.kind is CellKind.HEADING:
        assert cell.heading_level is not None
        assert cell.heading_title is not None
        indent = "  " * (cell.heading_level - 1)
        suffix = " [collapsed]" if cell.collapsed else ""
        return f"{indent}H{cell.heading_level} {cell.heading_title}{suffix}"
    parent = enclosing_heading_index(cells, index)
    if parent is None:
        indent = ""
    else:
        parent_level = cells[parent].heading_level or 1
        indent = "  " * parent_level
    if cell.kind is CellKind.TEXT:
        first_line = (cell.text or "").splitlines()[0] if cell.text else ""
        return f'{indent}"{first_line}"'
    command = cell.command if cell.command else "(empty)"
    first_line = command.splitlines()[0] if command else "(empty)"
    return f"{indent}$ {first_line}"
