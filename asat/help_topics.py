"""Self-voicing help topics (F38).

`:help` with no argument narrates the full cheat sheet (HELP_LINES in
`asat/input_router.py`). `:help <topic>` narrates a focused micro-tour
instead — short enough to absorb in one sitting, long enough to cover
one conceptual area. `:help topics` lists every topic name.

Topics live here as plain tuples so editing or translating one is a
one-file change. Each topic's first line is its heading — the renderer
and the sound bank can style it separately from the body if they wish.

The router treats topics as case-insensitive. Add a new topic by
dropping it into `HELP_TOPICS`; `:help topics` will pick it up
automatically, and the typo-suggestion path will include it for free.
"""

from __future__ import annotations


HELP_TOPICS: dict[str, tuple[str, ...]] = {
    "navigation": (
        "Navigation topic.",
        "NOTEBOOK mode is the home base. Up/Down walk between cells, Home/End jump to the ends of the session, Enter enters INPUT mode on the focused cell, Ctrl+N appends a fresh cell.",
        "INPUT mode is where you type a command. Enter submits, Escape leaves without running. Left/Right walk the caret; Home/End jump to the line's start/end. Up/Down walk command history; Down past the most recent restores the in-progress draft.",
        "OUTPUT mode steps through a cell's captured output line-by-line. Ctrl+O enters it, Up/Down step, PageUp/PageDown page, Escape leaves.",
        "Escape is always safe — from any mode it returns you one level up toward NOTEBOOK.",
    ),
    "cells": (
        "Cells topic.",
        "Each cell is one command plus the output it produced. New cells are appended with Ctrl+N; `d` in NOTEBOOK deletes the focused cell, `y` duplicates it.",
        "Alt+Up and Alt+Down reorder the focused cell within the session.",
        "From INPUT mode the meta-commands `:delete` and `:duplicate` do the same things for hands-that-prefer-typing.",
    ),
    "settings": (
        "Settings topic.",
        "Ctrl+, (or the `:settings` meta-command) opens the live settings editor. Up/Down walk, Right/Enter descend, Left/Escape ascend, `e` edits the focused field.",
        "Ctrl+S saves the bank to the `--bank` path if one was given. Ctrl+Q closes without saving.",
        "Ctrl+Z undoes your last settings edit, Ctrl+Y redoes it. Ctrl+R resets the cursor's current scope to the built-in defaults; Enter confirms, Escape cancels.",
        "`/` opens a cross-section search; Enter commits, Escape restores, `n` and `N` cycle matches.",
    ),
    "audio": (
        "Audio topic.",
        "Every event fires a cue or a short narration. --live plays through the speaker on Windows; --wav-dir DIR captures each buffer as a numbered WAV on any platform.",
        "Run `python -m asat --check` for a diagnostic summary. When no audio flag is given, ASAT narrates into an in-memory sink — you will not hear cues.",
        "Open settings (Ctrl+,) to retune voices, speeds, and sound recipes live; the change takes effect on the next event without restarting.",
    ),
    "search": (
        "Search topic.",
        "In OUTPUT mode, press `/` to search the captured output of the focused cell. Type a query and press Enter to commit; `n` jumps to the next hit, `N` the previous. `g` jumps to a specific line number.",
        "In SETTINGS mode, `/` searches across every section of the bank. Enter commits the jump; Escape restores the cursor to where it was before the search.",
    ),
    "meta": (
        "Meta-commands topic.",
        "Any INPUT line that starts with `:` is a meta-command. Names are case-insensitive; a single trailing argument is passed through to commands that use it.",
        "Handy ones: `:help`, `:help topics`, `:settings`, `:save`, `:quit`, `:delete`, `:duplicate`, `:pwd`, `:commands`, `:reset bank`, `:welcome`.",
        "`:commands` lists every meta-command. `:welcome` replays the first-run tour. `:help <topic>` narrates one of the topics you are listening to now.",
    ),
}


def topic_names() -> tuple[str, ...]:
    """Return topic names sorted so `:help topics` output is stable."""
    return tuple(sorted(HELP_TOPICS))


def lookup(topic: str) -> tuple[str, ...] | None:
    """Return the lines for `topic` (case-insensitive), or None if unknown."""
    return HELP_TOPICS.get(topic.lower())
