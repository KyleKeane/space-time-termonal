"""EventBus: synchronous publish/subscribe message router.

The bus is intentionally synchronous for Phase 1. A single publish call
invokes each subscribed handler in registration order before returning.
This keeps the event flow deterministic and easy to reason about when
reading the source with a screen reader.

Handlers must be plain callables that accept one Event argument and
return None. Exceptions raised by one handler do not prevent other
handlers for the same event from running; they are collected and
re-raised together after all handlers have had a chance to react.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from asat.events import Event, EventType


Handler = Callable[[Event], None]

WILDCARD = "*"


def publish_event(
    bus: "EventBus",
    event_type: EventType,
    payload: dict[str, Any],
    *,
    source: str,
) -> None:
    """Build an Event and publish it on the bus in one call.

    Every producer in the codebase goes through this helper so there is
    one place to change if Event construction ever grows a new required
    field (for example a correlation id or a priority). Keyword-only
    source avoids accidental swaps with payload.
    """
    bus.publish(Event(event_type=event_type, payload=payload, source=source))


class EventBusError(Exception):
    """Aggregated error raised when one or more handlers failed."""

    def __init__(self, event: Event, errors: list[BaseException]):
        """Record the offending event and the list of handler errors."""
        self.event = event
        self.errors = errors
        message = f"{len(errors)} handler(s) raised while processing {event.event_type.value}"
        super().__init__(message)


class EventBus:
    """Synchronous in-process publish/subscribe router.

    Create one instance per application. Pass it into modules that need
    to publish or subscribe. Keeping a single bus per process removes
    any ambiguity about which handlers will receive which events.
    """

    def __init__(self) -> None:
        """Initialize an empty subscription registry."""
        self._subscribers: dict[object, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: EventType | str, handler: Handler) -> None:
        """Register handler to receive events of the given type.

        Pass the WILDCARD constant ("*") instead of an EventType to
        receive every event published to the bus. Wildcard handlers are
        typically used for logging or for recording sessions.
        """
        key = self._key(event_type)
        self._subscribers[key].append(handler)

    def unsubscribe(self, event_type: EventType | str, handler: Handler) -> None:
        """Remove a previously registered handler.

        Silently succeeds if the handler was not registered. This keeps
        teardown code simple and idempotent.
        """
        key = self._key(event_type)
        handlers = self._subscribers.get(key)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            return

    def publish(self, event: Event) -> None:
        """Deliver the event to every subscribed handler.

        Handlers registered for the exact event type run first, then
        wildcard handlers. All handlers are attempted even if one
        raises. If any raised, an EventBusError is raised at the end
        aggregating all captured exceptions.
        """
        errors: list[BaseException] = []
        for handler in list(self._subscribers.get(event.event_type, ())):
            self._safe_call(handler, event, errors)
        for handler in list(self._subscribers.get(WILDCARD, ())):
            self._safe_call(handler, event, errors)
        if errors:
            raise EventBusError(event, errors)

    def clear(self) -> None:
        """Remove every subscription. Primarily useful in tests."""
        self._subscribers.clear()

    def subscriber_count(self, event_type: EventType | str) -> int:
        """Return how many handlers are registered for the given type."""
        key = self._key(event_type)
        return len(self._subscribers.get(key, ()))

    @staticmethod
    def _key(event_type: EventType | str) -> object:
        """Translate the public subscription key into the internal key."""
        if event_type == WILDCARD:
            return WILDCARD
        if isinstance(event_type, EventType):
            return event_type
        raise TypeError("event_type must be an EventType or the wildcard string")

    @staticmethod
    def _safe_call(handler: Handler, event: Event, errors: list[BaseException]) -> None:
        """Invoke handler, capturing any exception into errors."""
        try:
            handler(event)
        except BaseException as exc:
            errors.append(exc)
