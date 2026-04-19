"""Print `docs/BINDINGS.md` from the in-memory default binding map.

Run as ``python -m asat.tools.dump_bindings`` to print the Markdown
to stdout, or ``python -m asat.tools.dump_bindings --write`` to
overwrite ``docs/BINDINGS.md`` in place. The doc-sync gate
(``tests/test_bindings_doc_sync.py``) regenerates the same Markdown
in-memory and diffs against the committed file, so any new binding
added in ``default_bindings()`` without re-running this tool fails
CI.

Kept tiny and stdlib-only — no argparse subcommands, no plugin
discovery. The whole point of the tool is to be obvious from the
first read.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

from asat.input_router import default_bindings, format_bindings_markdown


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOC_PATH = REPO_ROOT / "docs" / "BINDINGS.md"


def render() -> str:
    """Return the canonical Markdown for the current default bindings."""
    return format_bindings_markdown(default_bindings())


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print to stdout, or with ``--write`` overwrite ``docs/BINDINGS.md``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--write":
        DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOC_PATH.write_text(render(), encoding="utf-8")
        return 0
    sys.stdout.write(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
