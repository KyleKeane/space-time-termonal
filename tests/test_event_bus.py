"""Unit tests for the EventBus and Event value object."""

from __future__ import annotations

import unittest

from asat.event_bus import EventBus, EventBusError, WILDCARD, publish_event
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


class EventBusThreadSafetyTests(unittest.TestCase):
    """F62: publishes from multiple threads must not interleave handlers."""

    def test_concurrent_publishes_run_handlers_serially(self) -> None:
        import threading

        bus = EventBus()
        # A handler that holds a local flag while "working". If
        # another thread publishes concurrently and the bus lets them
        # interleave, the overlapping flag will trip the assertion.
        state = {"in_flight": False, "overlap": False}
        state_lock = threading.Lock()

        def handler(_event: Event) -> None:
            with state_lock:
                if state["in_flight"]:
                    state["overlap"] = True
                state["in_flight"] = True
            # Brief work window to widen the interleave window.
            for _ in range(1000):
                pass
            with state_lock:
                state["in_flight"] = False

        bus.subscribe(EventType.CELL_CREATED, handler)

        def pump() -> None:
            for _ in range(200):
                bus.publish(Event(event_type=EventType.CELL_CREATED))

        threads = [threading.Thread(target=pump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(state["overlap"])

    def test_handler_can_publish_nested_event(self) -> None:
        # RLock (not Lock) because subscribers frequently publish
        # follow-on events from inside their callback.
        bus = EventBus()
        received: list[EventType] = []

        def on_outer(_event: Event) -> None:
            bus.publish(Event(event_type=EventType.CELL_UPDATED))

        bus.subscribe(EventType.CELL_CREATED, on_outer)
        bus.subscribe(EventType.CELL_UPDATED, lambda e: received.append(e.event_type))
        bus.publish(Event(event_type=EventType.CELL_CREATED))
        self.assertEqual(received, [EventType.CELL_UPDATED])


class PublishEventHelperTests(unittest.TestCase):

    def test_publish_event_builds_and_dispatches_event(self) -> None:
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe(WILDCARD, received.append)
        publish_event(
            bus,
            EventType.CELL_CREATED,
            {"cell_id": "abc"},
            source="test",
        )
        self.assertEqual(len(received), 1)
        event = received[0]
        self.assertEqual(event.event_type, EventType.CELL_CREATED)
        self.assertEqual(event.payload, {"cell_id": "abc"})
        self.assertEqual(event.source, "test")

    def test_source_is_keyword_only(self) -> None:
        bus = EventBus()
        with self.assertRaises(TypeError):
            publish_event(bus, EventType.CELL_CREATED, {}, "positional")  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
