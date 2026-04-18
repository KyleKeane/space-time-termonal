"""Unit tests for the EventBus and Event value object."""

from __future__ import annotations

import unittest

from asat.event_bus import EventBus, EventBusError, WILDCARD
from asat.events import Event, EventType


class EventConstructionTests(unittest.TestCase):

    def test_event_records_timestamp_and_defaults(self) -> None:
        event = Event(event_type=EventType.CELL_CREATED)
        self.assertEqual(event.event_type, EventType.CELL_CREATED)
        self.assertEqual(event.payload, {})
        self.assertEqual(event.source, "")
        self.assertIsNotNone(event.timestamp.tzinfo)

    def test_event_is_immutable(self) -> None:
        event = Event(event_type=EventType.CELL_CREATED)
        with self.assertRaises(Exception):
            event.source = "hacker"  # type: ignore[misc]


class EventBusSubscriptionTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bus = EventBus()
        self.received: list[Event] = []

    def _record(self, event: Event) -> None:
        self.received.append(event)

    def test_subscribe_and_publish_calls_handler(self) -> None:
        self.bus.subscribe(EventType.CELL_CREATED, self._record)
        event = Event(event_type=EventType.CELL_CREATED, payload={"id": "1"})
        self.bus.publish(event)
        self.assertEqual(self.received, [event])

    def test_handler_only_called_for_subscribed_type(self) -> None:
        self.bus.subscribe(EventType.CELL_CREATED, self._record)
        self.bus.publish(Event(event_type=EventType.CELL_REMOVED))
        self.assertEqual(self.received, [])

    def test_wildcard_receives_every_event(self) -> None:
        self.bus.subscribe(WILDCARD, self._record)
        self.bus.publish(Event(event_type=EventType.CELL_CREATED))
        self.bus.publish(Event(event_type=EventType.COMMAND_COMPLETED))
        self.assertEqual(len(self.received), 2)

    def test_unsubscribe_stops_delivery(self) -> None:
        self.bus.subscribe(EventType.CELL_CREATED, self._record)
        self.bus.unsubscribe(EventType.CELL_CREATED, self._record)
        self.bus.publish(Event(event_type=EventType.CELL_CREATED))
        self.assertEqual(self.received, [])

    def test_unsubscribe_unknown_handler_is_silent(self) -> None:
        self.bus.unsubscribe(EventType.CELL_CREATED, self._record)

    def test_subscriber_count_tracks_registrations(self) -> None:
        self.assertEqual(self.bus.subscriber_count(EventType.CELL_CREATED), 0)
        self.bus.subscribe(EventType.CELL_CREATED, self._record)
        self.assertEqual(self.bus.subscriber_count(EventType.CELL_CREATED), 1)
        self.bus.subscribe(EventType.CELL_CREATED, lambda e: None)
        self.assertEqual(self.bus.subscriber_count(EventType.CELL_CREATED), 2)

    def test_clear_removes_all_subscriptions(self) -> None:
        self.bus.subscribe(EventType.CELL_CREATED, self._record)
        self.bus.subscribe(WILDCARD, self._record)
        self.bus.clear()
        self.assertEqual(self.bus.subscriber_count(EventType.CELL_CREATED), 0)
        self.assertEqual(self.bus.subscriber_count(WILDCARD), 0)

    def test_invalid_subscription_key_raises(self) -> None:
        with self.assertRaises(TypeError):
            self.bus.subscribe("not-an-event", self._record)  # type: ignore[arg-type]


class EventBusErrorIsolationTests(unittest.TestCase):

    def test_failing_handler_does_not_block_others(self) -> None:
        bus = EventBus()
        calls: list[str] = []

        def good(_event: Event) -> None:
            calls.append("good")

        def bad(_event: Event) -> None:
            calls.append("bad")
            raise RuntimeError("boom")

        bus.subscribe(EventType.CELL_CREATED, bad)
        bus.subscribe(EventType.CELL_CREATED, good)
        with self.assertRaises(EventBusError) as ctx:
            bus.publish(Event(event_type=EventType.CELL_CREATED))
        self.assertEqual(calls, ["bad", "good"])
        self.assertEqual(len(ctx.exception.errors), 1)

    def test_multiple_failures_aggregate(self) -> None:
        bus = EventBus()

        def bad(_event: Event) -> None:
            raise ValueError("x")

        bus.subscribe(EventType.CELL_CREATED, bad)
        bus.subscribe(EventType.CELL_CREATED, bad)
        with self.assertRaises(EventBusError) as ctx:
            bus.publish(Event(event_type=EventType.CELL_CREATED))
        self.assertEqual(len(ctx.exception.errors), 2)


if __name__ == "__main__":
    unittest.main()
