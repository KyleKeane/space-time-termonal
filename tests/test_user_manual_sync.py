"""Regression guard: every META_COMMAND is mentioned in USER_MANUAL.md.

A meta-command users can type but cannot find in the manual is
worse than a meta-command that does not exist — the user wastes
real time hunting for it. This test asserts that every name in
``input_router.META_COMMANDS`` appears somewhere in
``docs/USER_MANUAL.md`` (anywhere — table cell, prose, cheat
sheet — author's choice; that you document it at all is the
non-negotiable bit).

Pairs with ``test_events_docs_sync.py``: same shape, different
file. Add a sibling test for any new public-name registry that
ships in the repo.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from asat.input_router import META_COMMANDS


_DOCS_PATH = Path(__file__).resolve().parent.parent / "docs" / "USER_MANUAL.md"


class UserManualSyncTests(unittest.TestCase):
    def test_every_meta_command_is_documented(self) -> None:
        text = _DOCS_PATH.read_text(encoding="utf-8")
        missing = [name for name in META_COMMANDS if f":{name}" not in text]
        self.assertEqual(
            missing,
            [],
            "META_COMMANDS missing from docs/USER_MANUAL.md — add a "
            "row in the meta-command table or the cheat sheet "
            f"before merging: {missing}",
        )


if __name__ == "__main__":
    unittest.main()
