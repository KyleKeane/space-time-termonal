"""Interactive TUI menu detection.

Many CLI programs present choices as a list of lines where one line is
highlighted (with reverse video or a leading marker like '>'). A blind
user cannot see that highlight, so ASAT extracts the list structure
from a VirtualScreen snapshot and exposes it as a data model the
audio engine can voice natively.

Two detection strategies are tried in order:

Reverse-video strategy
    The snapshot likely shows a menu when exactly one contiguous block
    of rows contains at least one cell with the ATTR_REVERSE
    attribute. That row is treated as the selected item, and the
    contiguous non-empty rows immediately above and below it form the
    remaining items.

Prefix-marker strategy
    If no reverse-video row is found, scan for two or more consecutive
    non-empty rows whose first non-blank character is a common marker.
    One row having a distinguished marker (">", "*", "->" or "=>")
    while its neighbours use something neutral (whitespace, "-", or
    the unselected form "[ ]") is treated as the selected item.

Both strategies produce the same `InteractiveMenu` value so callers
do not need to know which heuristic fired.

Detection is intentionally conservative: when nothing looks menu-like,
detect() returns None so downstream consumers can stay in plain-text
mode. False positives would be worse than false negatives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from asat.screen import ATTR_REVERSE, ScreenSnapshot


SELECTED_MARKERS = (">", "*", "→", "▶", "●", "=>")
UNSELECTED_MARKERS = (" ", "-", "○", "·", "[", "·")


@dataclass(frozen=True)
class MenuItemView:
    """One item in a detected interactive menu."""

    row: int
    text: str
    selected: bool


@dataclass(frozen=True)
class InteractiveMenu:
    """A detected interactive menu shown on a VirtualScreen snapshot."""

    items: tuple[MenuItemView, ...]
    selected_index: int
    detection: str

    @property
    def selected_text(self) -> str:
        """Return the text of the currently selected item."""
        if not self.items:
            return ""
        return self.items[self.selected_index].text


def detect(snapshot: ScreenSnapshot) -> Optional[InteractiveMenu]:
    """Return an InteractiveMenu if one can be inferred from snapshot."""
    menu = _detect_reverse_video(snapshot)
    if menu is not None:
        return menu
    return _detect_prefix_marker(snapshot)


def _detect_reverse_video(snapshot: ScreenSnapshot) -> Optional[InteractiveMenu]:
    """Find the row whose cells carry ATTR_REVERSE; grow items around it."""
    reverse_rows = [
        index
        for index in range(len(snapshot.rows))
        if _row_has_reverse(snapshot, index)
    ]
    if len(reverse_rows) != 1:
        return None
    selected_row = reverse_rows[0]
    text_rows = snapshot.text_rows()
    if not text_rows[selected_row].strip():
        return None
    items = _expand_contiguous_items(text_rows, selected_row)
    if len(items) < 2:
        return None
    selected_index = _find_index(items, selected_row)
    return InteractiveMenu(
        items=items,
        selected_index=selected_index,
        detection="reverse_video",
    )


def _detect_prefix_marker(snapshot: ScreenSnapshot) -> Optional[InteractiveMenu]:
    """Look for a pointer-style marker row among contiguous lines."""
    text_rows = snapshot.text_rows()
    groups = _contiguous_non_empty_groups(text_rows)
    for group_start, group_end in groups:
        selected_row = _find_marker_row(text_rows, group_start, group_end)
        if selected_row is None:
            continue
        items = tuple(
            MenuItemView(
                row=row,
                text=text_rows[row],
                selected=row == selected_row,
            )
            for row in range(group_start, group_end + 1)
        )
        selected_index = _find_index(items, selected_row)
        if len(items) < 2:
            continue
        return InteractiveMenu(
            items=items,
            selected_index=selected_index,
            detection="prefix_marker",
        )
    return None


def _row_has_reverse(snapshot: ScreenSnapshot, row_index: int) -> bool:
    """Return True if any non-blank cell on the row has ATTR_REVERSE."""
    for cell in snapshot.rows[row_index]:
        if ATTR_REVERSE in cell.attrs and cell.char != " ":
            return True
    return False


def _expand_contiguous_items(
    text_rows: tuple[str, ...],
    selected_row: int,
) -> tuple[MenuItemView, ...]:
    """Collect contiguous non-empty rows around selected_row as items."""
    start = selected_row
    while start - 1 >= 0 and text_rows[start - 1].strip():
        start -= 1
    end = selected_row
    while end + 1 < len(text_rows) and text_rows[end + 1].strip():
        end += 1
    return tuple(
        MenuItemView(
            row=row,
            text=text_rows[row],
            selected=row == selected_row,
        )
        for row in range(start, end + 1)
    )


def _contiguous_non_empty_groups(
    text_rows: tuple[str, ...],
) -> list[tuple[int, int]]:
    """Return (start, end) index pairs for runs of non-empty rows."""
    groups: list[tuple[int, int]] = []
    start: Optional[int] = None
    for index, row in enumerate(text_rows):
        if row.strip():
            if start is None:
                start = index
        else:
            if start is not None:
                groups.append((start, index - 1))
                start = None
    if start is not None:
        groups.append((start, len(text_rows) - 1))
    return groups


def _find_marker_row(
    text_rows: tuple[str, ...],
    group_start: int,
    group_end: int,
) -> Optional[int]:
    """Pick the row in [start, end] that starts with a selection marker.

    Only one row in the group may carry a selection marker; otherwise
    we consider the group ambiguous and return None.
    """
    candidates = [
        row
        for row in range(group_start, group_end + 1)
        if _starts_with_selected_marker(text_rows[row])
    ]
    if len(candidates) != 1:
        return None
    return candidates[0]


def _starts_with_selected_marker(row: str) -> bool:
    """Return True if the row's first non-blank token looks selected."""
    trimmed = row.lstrip()
    if not trimmed:
        return False
    for marker in SELECTED_MARKERS:
        if trimmed.startswith(marker):
            return True
    return False


def _find_index(
    items: tuple[MenuItemView, ...],
    row: int,
) -> int:
    """Return the index of the item whose source row matches."""
    for index, item in enumerate(items):
        if item.row == row:
            return index
    return 0
