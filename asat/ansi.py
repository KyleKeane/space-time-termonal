"""ANSI escape sequence tokenizer.

Real terminal programs speak a mix of plain text and escape sequences
controlling cursor position, colours, and screen clears. Before ASAT
can make a TUI accessible it has to understand that stream. This
module turns a raw string into a list of Tokens that later stages
(the virtual screen, the interactive menu detector) can consume
without re-parsing bytes.

The parser only tokenizes. It does not interpret. All semantics (what
a cursor-up command does, which SGR parameter toggles reverse video)
live in screen.py so this module can be exercised in isolation.

Supported sequences:

    CSI (Control Sequence Introducer): ESC [ params final
        where params is digits separated by ';', optionally preceded
        by an intermediate private marker byte like '?'. The final
        byte is a single letter. SGR sequences (final 'm') are the
        most common. Cursor moves use letters A-H, J, K, etc.

    OSC (Operating System Command): ESC ] body ST
        The terminator ST can be BEL (0x07) or ESC \\. The body is
        captured as opaque text. OSC is mostly used for window titles
        and hyperlinks which we tokenize but ignore downstream.

    ESC <char>: a bare escape followed by one byte that is not '[' or
        ']'. Produces an EscapeToken. Covers things like ESC 7 (save
        cursor) that we may want to match on later.

    Text: any run of bytes that is not ESC, LF, CR, TAB, or BS.

    Control: LF (\\n), CR (\\r), TAB (\\t), BS (\\b), BEL (\\a).

Incomplete sequences at the end of a feed are held until the next
feed call, so chunk boundaries never corrupt tokenization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


ESC = "\x1b"
BEL = "\x07"
BACKSPACE_CHAR = "\x08"
CR = "\r"
LF = "\n"
TAB = "\t"

CONTROL_CHARS = {LF, CR, TAB, BACKSPACE_CHAR, BEL}


@dataclass(frozen=True)
class TextToken:
    """A run of printable characters with no control bytes."""

    text: str


@dataclass(frozen=True)
class ControlToken:
    """A single control character such as CR, LF, TAB, BS, or BEL."""

    char: str


@dataclass(frozen=True)
class CSIToken:
    """A parsed CSI escape sequence.

    params: the decoded parameter list (integers). A missing parameter
        is represented as -1 so callers can apply per-command defaults.
    private: the private marker byte if present (e.g. '?'), else None.
    final: the single final byte that identifies the command.
    raw: the exact source substring, useful for logging.
    """

    params: tuple[int, ...]
    private: Optional[str]
    final: str
    raw: str


@dataclass(frozen=True)
class OSCToken:
    """An Operating System Command payload."""

    body: str
    raw: str


@dataclass(frozen=True)
class EscapeToken:
    """A bare ESC followed by a single command byte."""

    char: str
    raw: str


Token = TextToken | ControlToken | CSIToken | OSCToken | EscapeToken


@dataclass
class AnsiParser:
    """Stateful ANSI tokenizer that tolerates split sequences across feeds.

    Holds any partial escape sequence left at the end of a feed and
    prepends it to the next chunk. feed() returns the list of tokens
    recognized so far. Calling finish() flushes any buffered plain
    text as a TextToken and returns residual ESC state as-is.
    """

    _carry: str = field(default="")

    def feed(self, data: str) -> list[Token]:
        """Consume more input and return newly produced tokens."""
        text = self._carry + data
        self._carry = ""
        tokens: list[Token] = []
        index = 0
        length = len(text)
        while index < length:
            char = text[index]
            if char == ESC:
                consumed, token = self._try_parse_escape(text, index)
                if consumed == 0:
                    self._carry = text[index:]
                    return tokens
                if token is not None:
                    tokens.append(token)
                index += consumed
                continue
            if char in CONTROL_CHARS:
                tokens.append(ControlToken(char=char))
                index += 1
                continue
            end = index
            while end < length and text[end] != ESC and text[end] not in CONTROL_CHARS:
                end += 1
            tokens.append(TextToken(text=text[index:end]))
            index = end
        return tokens

    def finish(self) -> list[Token]:
        """Flush buffered plain text as a token; drop residual partial ESC."""
        if not self._carry:
            return []
        leftover = self._carry
        self._carry = ""
        if leftover.startswith(ESC):
            return []
        return [TextToken(text=leftover)]

    def _try_parse_escape(
        self,
        text: str,
        start: int,
    ) -> tuple[int, Optional[Token]]:
        """Attempt to parse an escape sequence beginning at start.

        Returns (consumed_chars, token). If consumed_chars == 0 the
        sequence is incomplete and the caller should buffer it.
        """
        length = len(text)
        if start + 1 >= length:
            return 0, None
        second = text[start + 1]
        if second == "[":
            return self._parse_csi(text, start)
        if second == "]":
            return self._parse_osc(text, start)
        return 2, EscapeToken(char=second, raw=text[start:start + 2])

    def _parse_csi(
        self,
        text: str,
        start: int,
    ) -> tuple[int, Optional[Token]]:
        """Parse ESC [ params final, returning the CSI token or incomplete."""
        index = start + 2
        length = len(text)
        private: Optional[str] = None
        if index < length and text[index] in "?><!":
            private = text[index]
            index += 1
        param_start = index
        while index < length and (text[index].isdigit() or text[index] == ";"):
            index += 1
        if index >= length:
            return 0, None
        final = text[index]
        if not ("A" <= final <= "Z" or "a" <= final <= "z"):
            return 0, None
        raw = text[start:index + 1]
        params = _parse_params(text[param_start:index])
        return index + 1 - start, CSIToken(
            params=params,
            private=private,
            final=final,
            raw=raw,
        )

    def _parse_osc(
        self,
        text: str,
        start: int,
    ) -> tuple[int, Optional[Token]]:
        """Parse ESC ] body ST. ST may be BEL or ESC \\."""
        body_start = start + 2
        length = len(text)
        index = body_start
        while index < length:
            if text[index] == BEL:
                raw = text[start:index + 1]
                body = text[body_start:index]
                return index + 1 - start, OSCToken(body=body, raw=raw)
            if text[index] == ESC and index + 1 < length and text[index + 1] == "\\":
                raw = text[start:index + 2]
                body = text[body_start:index]
                return index + 2 - start, OSCToken(body=body, raw=raw)
            index += 1
        return 0, None


def _parse_params(segment: str) -> tuple[int, ...]:
    """Split a CSI parameter segment into integers; empty fields become -1."""
    if not segment:
        return ()
    out: list[int] = []
    for piece in segment.split(";"):
        if piece == "":
            out.append(-1)
        else:
            try:
                out.append(int(piece))
            except ValueError:
                out.append(-1)
    return tuple(out)
