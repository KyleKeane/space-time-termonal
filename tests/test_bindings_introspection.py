"""Tests for the F64 keybinding introspection surface.

Covers three things:

  * `format_key` renders Key values in the same form a user would
    type or read in `docs/BINDINGS.md`.
  * `binding_report` flattens a BindingMap into a stable, sorted
    list of BindingEntry rows.
  * `format_bindings_markdown` produces the exact text that
    `docs/BINDINGS.md` should contain — paired with the
    on-disk-vs-regenerated sync check below.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from asat import keys as kc
from asat.input_router import (
    BindingEntry,
    binding_report,
    default_bindings,
    format_bindings_markdown,
    format_key,
)
from asat.keys import Key, Modifier
from asat.notebook import FocusMode


class FormatKeyTests(unittest.TestCase):

    def test_printable_letter_no_modifier_is_lowercase(self) -> None:
        self.assertEqual(format_key(Key.printable("d")), "d")

    def test_printable_letter_with_ctrl_uppercases(self) -> None:
        self.assertEqual(format_key(Key.combo("n", Modifier.CTRL)), "Ctrl+N")

    def test_printable_punctuation_unchanged(self) -> None:
        self.assertEqual(format_key(Key.printable("]")), "]")
        self.assertEqual(format_key(Key.combo(",", Modifier.CTRL)), "Ctrl+,")

    def test_special_keys_use_lookup_table(self) -> None:
        self.assertEqual(format_key(kc.UP), "Up")
        self.assertEqual(format_key(kc.PAGE_DOWN), "Page Down")
        self.assertEqual(format_key(kc.ESCAPE), "Escape")

    def test_function_keys_uppercase(self) -> None:
        self.assertEqual(format_key(Key.special("f7")), "F7")

    def test_modifier_order_is_canonical(self) -> None:
        # Even with the modifiers fed in reverse order, render is
        # Ctrl+Alt+Shift+Meta — stable across hash randomisation.
        key = Key.special(
            "up",
            Modifier.META,
            Modifier.SHIFT,
            Modifier.ALT,
            Modifier.CTRL,
        )
        self.assertEqual(format_key(key), "Ctrl+Alt+Shift+Meta+Up")


class BindingReportTests(unittest.TestCase):

    def test_default_bindings_produce_a_non_empty_report(self) -> None:
        report = binding_report(default_bindings())
        self.assertTrue(len(report) > 30, "default map should have many bindings")
        self.assertTrue(all(isinstance(e, BindingEntry) for e in report))

    def test_report_is_sorted_by_mode_then_keyspec(self) -> None:
        report = binding_report(default_bindings())
        mode_index = {mode: i for i, mode in enumerate(FocusMode)}
        prev = (-1, "")
        for entry in report:
            current = (mode_index[entry.mode], entry.key_spec)
            self.assertGreaterEqual(current, prev)
            prev = current

    def test_report_covers_known_notebook_binding(self) -> None:
        report = binding_report(default_bindings())
        rows = [e for e in report if e.mode is FocusMode.NOTEBOOK and e.action == "new_cell"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].key_spec, "Ctrl+N")
        self.assertEqual(rows[0].key_name, "n")
        self.assertEqual(rows[0].modifiers, frozenset({Modifier.CTRL}))


class BindingsDocInSyncTests(unittest.TestCase):
    """`docs/BINDINGS.md` must equal `format_bindings_markdown(default_bindings())`.

    Re-run `python -m asat.tools.dump_bindings --write` to regenerate
    the file when this gate fails.
    """

    def test_bindings_doc_matches_in_memory_render(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        doc_path = repo_root / "docs" / "BINDINGS.md"
        on_disk = doc_path.read_text(encoding="utf-8")
        rendered = format_bindings_markdown(default_bindings())
        self.assertEqual(
            on_disk,
            rendered,
            "docs/BINDINGS.md is out of sync — run "
            "`python -m asat.tools.dump_bindings --write` to regenerate.",
        )


if __name__ == "__main__":
    unittest.main()
