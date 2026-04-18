"""Launch the Accessible Spatial Audio Terminal from the command line.

`python -m asat` assembles an Application with sensible defaults and
drives it from real keystrokes. By default the session narrates to an
in-memory sink (so every platform starts cleanly) and prints a small
text trace to stdout (so sighted viewers and anyone debugging can
follow along). The flags below switch the two knobs:

    --live          play audio through the platform live-speaker sink
                    (Windows today; POSIX falls back to MemorySink
                    with an explanation).
    --wav-dir DIR   additionally write every rendered buffer to a
                    numbered WAV file under DIR.
    --quiet         suppress the text trace on stdout (audio only).

Exit with the `:quit` meta-command (type `:quit` in INPUT mode then
press Enter) or by sending EOF (Ctrl+D on POSIX, Ctrl+Z then Enter on
Windows). The full keystroke cheat sheet lives in
docs/USER_MANUAL.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from asat.app import Application
from asat.audio_sink import (
    AudioSink,
    LiveAudioUnavailable,
    MemorySink,
    WavFileSink,
    pick_live_sink,
)
from asat.keyboard import KeyboardReader, pick_default
from asat.session import Session
from asat.sound_bank import SoundBank
from asat.terminal import TerminalRenderer


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse args, build the Application, and drive the read-dispatch loop."""
    args = _parse_args(argv)
    sink = _make_sink(args.wav_dir, args.live)
    bank = SoundBank.load(args.bank) if args.bank is not None else None
    session = Session.load(args.session) if args.session is not None else None
    app = Application.build(
        sink=sink,
        bank=bank,
        bank_path=args.bank,
        session=session,
        session_path=args.session,
    )
    if not args.quiet:
        # Attach AFTER Application.build so the renderer does not
        # double-print the startup banner into its own buffer.
        TerminalRenderer(app.bus)
    keyboard: KeyboardReader = pick_default()
    try:
        _run(app, keyboard)
    except KeyboardInterrupt:
        # Clean exit on Ctrl+C during a blocking read. A real cancel
        # keystroke is tracked as F10 in docs/FEATURE_REQUESTS.md.
        pass
    finally:
        keyboard.close()
        app.close()
    return 0


def _run(app: Application, keyboard: KeyboardReader) -> None:
    """Read keys until the Application flags itself as done."""
    while app.running:
        key = keyboard.read_key()
        if key is None:
            break
        app.handle_key(key)
        for cell_id in app.drain_pending():
            app.execute(cell_id)


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    """Define the CLI surface; kept tiny on purpose."""
    parser = argparse.ArgumentParser(
        prog="asat",
        description="Launch the Accessible Spatial Audio Terminal.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Play audio on the platform live-speaker sink. Today this "
            "means winsound on Windows; POSIX hosts fall back to the "
            "in-memory sink with an explanation."
        ),
    )
    parser.add_argument(
        "--wav-dir",
        type=Path,
        default=None,
        help=(
            "Also write each rendered buffer to a numbered WAV file in "
            "this directory. Useful for off-device review or when "
            "--live is not available."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the text trace on stdout. Audio output is unaffected.",
    )
    parser.add_argument(
        "--bank",
        type=Path,
        default=None,
        help="Load the SoundBank from this JSON file instead of the built-in default.",
    )
    parser.add_argument(
        "--session",
        type=Path,
        default=None,
        help="Load an existing Session from this JSON file; saved on exit.",
    )
    return parser.parse_args(argv)


def _make_sink(wav_dir: Optional[Path], live: bool) -> AudioSink:
    """Compose the sink chain based on the requested flags.

    Priority: `--live` drives the primary sink when available.
    `--wav-dir DIR` can be layered on either the live sink or the
    default MemorySink. When `--live` is requested on a platform
    without a live backend, we print a short explanation and continue
    with MemorySink so the session still starts.
    """
    primary: AudioSink
    if live:
        try:
            primary = pick_live_sink()
        except LiveAudioUnavailable as exc:
            print(f"[asat] --live unavailable: {exc}", file=sys.stderr)
            primary = MemorySink()
    else:
        primary = MemorySink()
    if wav_dir is None:
        return primary
    wav_dir.mkdir(parents=True, exist_ok=True)
    wav_sink = WavFileSink(wav_dir)
    return _TeeSink(primary, wav_sink)


class _TeeSink:
    """Dispatch each buffer to two underlying sinks.

    Private to the CLI because no other layer needs it today. If
    multi-sink composition grows into a real feature, promote this to
    `asat/audio_sink.py`.
    """

    def __init__(self, first: AudioSink, second: AudioSink) -> None:
        self._first = first
        self._second = second

    def play(self, buffer) -> None:
        self._first.play(buffer)
        self._second.play(buffer)

    def close(self) -> None:
        try:
            self._first.close()
        finally:
            self._second.close()


if __name__ == "__main__":
    sys.exit(main())
