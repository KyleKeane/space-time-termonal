"""Regression guard: every documented EventType has a producer.

The sister test ``test_events_docs_sync.py`` asserts that every
``EventType`` enum member is *mentioned* somewhere in
``docs/EVENTS.md``. This test goes the other direction: every
``EventType`` either has a real publish site somewhere in the
``asat/`` package, or its EVENTS.md row carries the literal
phrase ``reserved; no producer ships today.``.

Why two checks? An undocumented event type is a contract leak
(callers can't know it exists). A documented event type with no
producer is the opposite leak: subscribers wire bindings against
something that will never fire, then waste hours debugging
"why does my cue never play?". Both directions need to be
loud at CI time.

The "reserved" escape hatch lets us keep the vocabulary entry
for events whose payload contract is intentionally published
ahead of the engine work that will produce them.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from asat.events import EventType


_REPO_ROOT = Path(__file__).resolve().parent.parent
_ASAT_DIR = _REPO_ROOT / "asat"
_DOCS_PATH = _REPO_ROOT / "docs" / "EVENTS.md"
_RESERVED_MARKER = "reserved; no producer ships today."


def _producer_files() -> list[Path]:
    return [p for p in _ASAT_DIR.rglob("*.py") if p.name != "events.py"]


class EventsProducerAuditTests(unittest.TestCase):
    def test_every_event_has_producer_or_is_marked_reserved(self) -> None:
        producer_text = "\n".join(
            p.read_text(encoding="utf-8") for p in _producer_files()
        )
        docs_text = _DOCS_PATH.read_text(encoding="utf-8")

        leaks: list[str] = []
        for event in EventType:
            token = f"EventType.{event.name}"
            if token in producer_text:
                continue
            row_marker = f"`{event.name}`"
            if row_marker not in docs_text:
                continue
            row_index = docs_text.find(row_marker)
            row_end = docs_text.find("\n", row_index)
            row = docs_text[row_index:row_end]
            if _RESERVED_MARKER in row:
                continue
            leaks.append(event.name)

        self.assertEqual(
            leaks,
            [],
            "EventType members documented in docs/EVENTS.md but with "
            "no publish site in the asat package. Either wire a "
            f"producer or mark the row '{_RESERVED_MARKER}': {leaks}",
        )


if __name__ == "__main__":
    unittest.main()
