"""Launch the Accessible Spatial Audio Terminal from the command line.

`python -m asat` assembles an Application with sensible defaults and
drives it from real keystrokes. Press Enter to submit a command,
Ctrl+N to open a fresh cell, Ctrl+, to edit settings, and type
`:quit` (then Enter) or send EOF (Ctrl+D) to exit. The default
keystroke cheat sheet lives in docs/USER_MANUAL.md.

Audio sinks today: `MemorySink` by default (accumulates in RAM),
`WavFileSink` when `--wav-dir` is given. A live-speaker sink is
still on the feature list (see docs/FEATURE_REQUESTS.md, F6);
until it lands, `--wav-dir DIR` is how a user actually hears the
narration — open the WAVs in a screen-reader-friendly player.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from asat.app import Application
from asat.audio_sink import AudioSink, MemorySink, WavFileSink
from asat.keyboard import KeyboardReader, pick_default
from asat.session import Session
from asat.sound_bank import SoundBank


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse args, build the Application, and drive the read-dispatch loop."""
    args = _parse_args(argv)
    sink = _make_sink(args.wav_dir)
    bank = SoundBank.load(args.bank) if args.bank is not None else None
    session = Session.load(args.session) if args.session is not None else None
    app = Application.build(
        sink=sink,
        bank=bank,
        bank_path=args.bank,
        session=session,
        session_path=args.session,
    )
    keyboard: KeyboardReader = pick_default()
    try:
        _run(app, keyboard)
    except KeyboardInterrupt:
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
        "--wav-dir",
        type=Path,
        default=None,
        help="Write each rendered buffer to a numbered WAV file in this directory.",
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


def _make_sink(wav_dir: Optional[Path]) -> AudioSink:
    """Return a WAV-writing sink when a directory is given, else a MemorySink."""
    if wav_dir is None:
        return MemorySink()
    wav_dir.mkdir(parents=True, exist_ok=True)
    return WavFileSink(wav_dir)


if __name__ == "__main__":
    sys.exit(main())
