"""Regression guard: every EventType is mentioned in docs/EVENTS.md.

Adding a new ``EventType`` without documenting its payload is a silent
contract leak: subscribers outside this repo will not know the event
exists, and no error will ever surface until someone searches the docs
and comes up empty. This test fails the build until every new category
lands in the reference, so the docs cannot drift behind the code.

The check is deliberately loose — it only asserts the enum member
name (e.g. ``SESSION_CREATED``) appears somewhere in EVENTS.md
(inside a table cell, a prose line, or a code fence). Where and how
you document the event is up to you; that you document it at all is
non-negotiable.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from asat.events import EventType


_DOCS_PATH = Path(__file__).resolve().parent.parent / "docs" / "EVENTS.md"


class EventsDocsSyncTests(unittest.TestCase):
    def test_every_event_type_is_documented(self) -> None:
        text = _DOCS_PATH.read_text(encoding="utf-8")
        missing = [event.name for event in EventType if event.name not in text]
        self.assertEqual(
            missing,
            [],
            "EventType names missing from docs/EVENTS.md — add rows "
            "describing payload and producer before merging: "
            f"{missing}",
        )


if __name__ == "__main__":
    unittest.main()
