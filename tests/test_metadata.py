"""Regression guard for F47 — package metadata hygiene.

Two sources of truth can drift silently:
  * `asat/__init__.py` defines `__version__`.
  * `pyproject.toml` defines `[project].version`.

A release cut from either one needs both to agree, and PyPI should show
the real README, not a Phase-1-era placeholder. These tests fail fast if
someone bumps one and forgets the other, or reverts the readme pointer.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# tomllib moved into the stdlib in Python 3.11. On 3.10 we fall back
# to the `tomli` backport — same API, different module name.
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

import asat


_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _read_project_table() -> dict:
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]


class MetadataTests(unittest.TestCase):
    def test_pyproject_version_matches_package_dunder_version(self) -> None:
        project = _read_project_table()
        self.assertEqual(
            project["version"],
            asat.__version__,
            "pyproject.toml version and asat.__version__ must stay aligned",
        )

    def test_readme_points_at_real_readme_file(self) -> None:
        project = _read_project_table()
        self.assertEqual(
            project["readme"],
            "README.md",
            "pyproject.toml readme must reference README.md, not an inline placeholder",
        )

    def test_description_is_not_the_phase_one_placeholder(self) -> None:
        project = _read_project_table()
        self.assertNotIn("Phase 1", project["description"])


if __name__ == "__main__":
    unittest.main()
