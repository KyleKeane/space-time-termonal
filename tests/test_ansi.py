"""Unit tests for the AnsiParser tokenizer."""

from __future__ import annotations

import unittest

from asat.ansi import (
    AnsiParser,
    CSIToken,
    ControlToken,
    EscapeToken,
    OSCToken,
    TextToken,
)


class PlainTextTests(unittest.TestCase):

    def test_feeding_plain_text_emits_single_token(self) -> None:
        parser = AnsiParser()
        tokens = parser.feed("hello world")
        self.assertEqual(len(tokens), 1)
        self.assertIsInstance(tokens[0], TextToken)
        self.assertEqual(tokens[0].text, "hello world")

    def test_control_characters_are_isolated(self) -> None:
        parser = AnsiParser()
        tokens = parser.feed("a\r\nb\t\bc")
        kinds = [type(t).__name__ for t in tokens]
        self.assertEqual(
            kinds,
            [
                "TextToken",
                "ControlToken",
                "ControlToken",
                "TextToken",
                "ControlToken",
                "ControlToken",
                "TextToken",
            ],
        )


class CSIParsingTests(unittest.TestCase):

    def test_clear_screen_has_final_J_and_params(self) -> None:
        parser = AnsiParser()
        tokens = parser.feed("\x1b[2J")
        self.assertEqual(len(tokens), 1)
        assert isinstance(tokens[0], CSIToken)
        self.assertEqual(tokens[0].final, "J")
        self.assertEqual(tokens[0].params, (2,))
        self.assertIsNone(tokens[0].private)
        self.assertEqual(tokens[0].raw, "\x1b[2J")

    def test_sgr_with_multiple_params(self) -> None:
        parser = AnsiParser()
        tokens = parser.feed("\x1b[1;31;4m")
        assert isinstance(tokens[0], CSIToken)
        self.assertEqual(tokens[0].final, "m")
        self.assertEqual(tokens[0].params, (1, 31, 4))

    def test_empty_param_becomes_sentinel(self) -> None:
        parser = AnsiParser()
        tokens = parser.feed("\x1b[;5H")
        assert isinstance(tokens[0], CSIToken)
        self.assertEqual(tokens[0].params, (-1, 5))

    def test_private_marker_captured(self) -> None:
        parser = AnsiParser()
        tokens = parser.feed("\x1b[?25l")
        assert isinstance(tokens[0], CSIToken)
        self.assertEqual(tokens[0].private, "?")
        self.assertEqual(tokens[0].final, "l")
        self.assertEqual(tokens[0].params, (25,))


class OSCAndEscTests(unittest.TestCase):

    def test_osc_with_bel_terminator(self) -> None:
        parser = AnsiParser()
        tokens = parser.feed("\x1b]0;title\x07")
        self.assertEqual(len(tokens), 1)
        assert isinstance(tokens[0], OSCToken)
        self.assertEqual(tokens[0].body, "0;title")

    def test_osc_with_st_terminator(self) -> None:
        parser = AnsiParser()
        tokens = parser.feed("\x1b]8;;https://x\x1b\\")
        self.assertEqual(len(tokens), 1)
        assert isinstance(tokens[0], OSCToken)
        self.assertEqual(tokens[0].body, "8;;https://x")

    def test_bare_escape_command(self) -> None:
        parser = AnsiParser()
        tokens = parser.feed("\x1b7")
        self.assertEqual(len(tokens), 1)
        assert isinstance(tokens[0], EscapeToken)
        self.assertEqual(tokens[0].char, "7")


class IncompleteSequenceTests(unittest.TestCase):

    def test_split_csi_is_buffered_across_feeds(self) -> None:
        parser = AnsiParser()
        first = parser.feed("\x1b[1;3")
        self.assertEqual(first, [])
        second = parser.feed("1m")
        self.assertEqual(len(second), 1)
        assert isinstance(second[0], CSIToken)
        self.assertEqual(second[0].params, (1, 31))
        self.assertEqual(second[0].final, "m")

    def test_split_osc_is_buffered_until_terminator(self) -> None:
        parser = AnsiParser()
        first = parser.feed("\x1b]0;hel")
        self.assertEqual(first, [])
        second = parser.feed("lo\x07")
        self.assertEqual(len(second), 1)
        assert isinstance(second[0], OSCToken)
        self.assertEqual(second[0].body, "0;hello")

    def test_finish_returns_buffered_plain_text(self) -> None:
        parser = AnsiParser()
        parser.feed("x")
        parser.feed("\x1b[")
        residual = parser.finish()
        self.assertEqual(residual, [])


class MixedStreamTests(unittest.TestCase):

    def test_real_world_mix_of_tokens(self) -> None:
        parser = AnsiParser()
        stream = "hello\x1b[1;31m world\x1b[0m\r\n"
        tokens = parser.feed(stream)
        text_runs = [t for t in tokens if isinstance(t, TextToken)]
        csis = [t for t in tokens if isinstance(t, CSIToken)]
        controls = [t for t in tokens if isinstance(t, ControlToken)]
        self.assertEqual([t.text for t in text_runs], ["hello", " world"])
        self.assertEqual([t.params for t in csis], [(1, 31), (0,)])
        self.assertEqual([c.char for c in controls], ["\r", "\n"])


if __name__ == "__main__":
    unittest.main()
