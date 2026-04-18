"""Shared internal helpers used across asat modules.

This module is the single source of truth for tiny utilities that would
otherwise be duplicated in several files. Keeping them here makes it
obvious where to add the next piece of cross-cutting plumbing and keeps
individual modules focused on their real responsibilities.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time with an explicit timezone.

    Always use this instead of ``datetime.utcnow()`` so timestamps carry
    tzinfo and round-trip cleanly through ``isoformat``.
    """
    return datetime.now(timezone.utc)


def new_id() -> str:
    """Return a fresh random identifier as a hex string.

    Used for cell ids, session ids, and anywhere else we need a short
    collision-resistant identifier without exposing uuid details to the
    calling module.
    """
    return uuid.uuid4().hex
