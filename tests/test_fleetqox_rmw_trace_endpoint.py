import csv
from pathlib import Path
import tempfile
import unittest

from scripts.fleetqox_rmw_trace_endpoint import (
    _topic_for,
    build_payload,
    load_rows,
    wait_until_deadline_while_spinning,
)


class TopicForTest(unittest.TestCase):
    def test_point_to_point_topic_isolates_destination_and_flow_class(self):
        self.assertEqual(
            _topic_for("robot_0003", "control"), "/fleetqox_trace/robot_0003/control"
        )
        self.assertNotEqual(
            _topic_for("robot_0003", "control"), _topic_for("robot_0004", "control")
        )
        self.assertNotEqual(
            _topic_for("robot_0003", "control"), _topic_for("robot_0003", "state")
        )

    def test_sanitizes_non_ros2_topic_characters(self):
        topic = _topic_for("fleet-router!", "human/qoe")
        self.assertTrue(all(ch.isalnum() or ch in "/_" for ch in topic))


class LoadRowsTest(unittest.TestCase):
    def test_filters_by_policy_and_endpoint(self):
        rows = [
            {
                "event_id": "1",
                "policy": "fifo",
                "flow_class": "control",
                "src": "fleet_controller",
                "dst": "robot_0000",
                "bytes": "96",
                "deadline_ms": "45.0",
                "timestamp_ms": "0.0",
            },
            {
                "event_id": "2",
                "policy": "static_priority",
                "flow_class": "control",
                "src": "fleet_controller",
                "dst": "robot_0000",
                "bytes": "96",
                "deadline_ms": "45.0",
                "timestamp_ms": "20.0",
            },
            {
                "event_id": "3",
                "policy": "fifo",
                "flow_class": "state",
                "src": "robot_0001",
                "dst": "fleet_router",
                "bytes": "320",
                "deadline_ms": "120.0",
                "timestamp_ms": "0.0",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.csv"
            with trace_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            robot0_rows = load_rows(trace_path, "fifo", "robot_0000")
            self.assertEqual([r["event_id"] for r in robot0_rows], ["1"])

            router_rows = load_rows(trace_path, "fifo", "fleet_router")
            self.assertEqual([r["event_id"] for r in router_rows], ["3"])

            unrelated_rows = load_rows(trace_path, "fifo", "operator_ui")
            self.assertEqual(unrelated_rows, [])


class BuildPayloadTest(unittest.TestCase):
    ROW = {
        "event_id": "42",
        "policy": "fifo",
        "flow_class": "control",
        "src": "fleet_controller",
        "dst": "robot_0000",
        "deadline_ms": "45.0",
    }

    def test_payload_matches_target_byte_size_exactly(self):
        for target_bytes in (96, 320, 2200, 9000):
            payload = build_payload(self.ROW, target_bytes)
            self.assertEqual(len(payload.encode("utf-8")), target_bytes)

    def test_payload_roundtrips_the_row_fields(self):
        import json

        payload = json.loads(build_payload(self.ROW, 200))
        self.assertEqual(payload["e"], "42")
        self.assertEqual(payload["d"], 45.0)
        self.assertIn("s", payload)

    def test_target_smaller_than_metadata_floor_raises(self):
        with self.assertRaises(ValueError):
            build_payload(self.ROW, 5)


class WaitUntilDeadlineWhileSpinningTest(unittest.TestCase):
    """RED/GREEN for the 17/09/2026 receiver-side dispatch investigation
    (see docs/AUDIT_ACCEPTANCE_TRACKING.md 'SỬA BENCHMARK HARNESS'):
    the send loop's inter-send wait was a blind time.sleep() that called
    rclpy.spin_once() zero times, so a message that became ready to
    dispatch (T_RMW_READY, per the new rmw_pubsub.cpp instrumentation)
    during that wait sat undelivered until the NEXT scheduled send woke
    the process up -- live A/B measured this at up to ~370ms for control-
    class messages. This reproduces the scenario with a fake clock/fake
    spin_once (no real rclpy/DDS needed): a message becomes "ready"
    partway through the wait, and the fix must dispatch it before the
    deadline instead of only once the deadline arrives, WITHOUT ever
    returning early (which would move the publish schedule) or spinning
    unboundedly often (which would busy-loop the CPU).
    """

    def _make_fake_spin(self, ready_at, serviced_log, clock):
        """Returns (spin_once_fn, is_serviced_checker). Each call advances
        the fake clock by `timeout_sec` (simulating a blocking rmw_wait())
        and, once the clock has reached `ready_at`, marks the fake message
        serviced (once) and records the clock time at service."""
        state = {"serviced": False}

        def spin_once_fn(timeout_sec):
            clock[0] += timeout_sec
            if not state["serviced"] and clock[0] >= ready_at:
                state["serviced"] = True
                serviced_log.append(clock[0])

        return spin_once_fn

    def test_message_ready_mid_wait_is_serviced_before_deadline(self):
        clock = [0.0]
        serviced_log = []
        spin_once_fn = self._make_fake_spin(ready_at=0.5, serviced_log=serviced_log, clock=clock)

        wait_until_deadline_while_spinning(
            deadline_monotonic=1.0,
            spin_once_fn=spin_once_fn,
            poll_interval_s=0.05,
            now_fn=lambda: clock[0],
        )

        self.assertEqual(
            len(serviced_log), 1,
            "message never got serviced during the wait at all",
        )
        self.assertLess(
            serviced_log[0], 1.0,
            "message was only serviced at/after the deadline -- this is exactly "
            "the old blind time.sleep() behavior (callback held until the next "
            "scheduled send), not fixed",
        )

    def test_never_returns_before_deadline(self):
        # Nothing ever becomes "ready" (ready_at far in the future) --
        # the helper must still not return before the deadline, i.e. it
        # must never cause an early publish relative to the original
        # schedule.
        clock = [0.0]
        serviced_log = []
        spin_once_fn = self._make_fake_spin(ready_at=999.0, serviced_log=serviced_log, clock=clock)

        wait_until_deadline_while_spinning(
            deadline_monotonic=1.0,
            spin_once_fn=spin_once_fn,
            poll_interval_s=0.05,
            now_fn=lambda: clock[0],
        )

        self.assertGreaterEqual(clock[0], 1.0)

    def test_already_past_deadline_returns_without_spinning(self):
        clock = [2.0]
        calls = []
        wait_until_deadline_while_spinning(
            deadline_monotonic=1.0,
            spin_once_fn=lambda timeout_sec: calls.append(timeout_sec),
            poll_interval_s=0.05,
            now_fn=lambda: clock[0],
        )
        self.assertEqual(calls, [])

    def test_does_not_spin_unboundedly_often(self):
        # A 1-second wait with a 50ms poll interval should take on the
        # order of ~20 bounded spin_once calls, not thousands -- guards
        # against a busy-loop regression (e.g. always passing timeout=0).
        clock = [0.0]
        calls = []

        def spin_once_fn(timeout_sec):
            calls.append(timeout_sec)
            clock[0] += timeout_sec if timeout_sec > 0 else 0.0

        wait_until_deadline_while_spinning(
            deadline_monotonic=1.0,
            spin_once_fn=spin_once_fn,
            poll_interval_s=0.05,
            now_fn=lambda: clock[0],
        )
        bounded_calls = [c for c in calls if c > 0]
        self.assertLess(len(bounded_calls), 100)


if __name__ == "__main__":
    unittest.main()
