"""Unit tests for asat/help_topics.py (F38)."""

from __future__ import annotations

import unittest

from asat.help_topics import HELP_TOPICS, lookup, topic_names


class HelpTopicsTests(unittest.TestCase):
    def test_every_topic_has_at_least_two_lines(self) -> None:
        # A topic with one line is just a heading — not a tour. This
        # keeps the module honest when someone adds a new topic.
        for name, lines in HELP_TOPICS.items():
            self.assertGreaterEqual(
                len(lines),
                2,
                f"help topic `{name}` must have a heading plus at least one body line",
            )

    def test_topic_names_are_sorted_for_stable_listing(self) -> None:
        names = topic_names()
        self.assertEqual(names, tuple(sorted(HELP_TOPICS)))

    def test_lookup_is_case_insensitive(self) -> None:
        for name in HELP_TOPICS:
            self.assertEqual(lookup(name), HELP_TOPICS[name])
            self.assertEqual(lookup(name.upper()), HELP_TOPICS[name])

    def test_lookup_returns_none_for_unknown_topic(self) -> None:
        self.assertIsNone(lookup("no-such-topic"))

    def test_meta_topic_mentions_welcome_and_help_topics(self) -> None:
        """Discoverability regression guard: the `meta` tour names
        both `:welcome` and `:help topics` so a user who finds their
        way to `:help meta` learns about the two self-service
        commands the rest of the app relies on."""
        meta_text = "\n".join(HELP_TOPICS["meta"]).lower()
        self.assertIn(":welcome", meta_text)
        self.assertIn(":help topics", meta_text)


if __name__ == "__main__":
    unittest.main()
