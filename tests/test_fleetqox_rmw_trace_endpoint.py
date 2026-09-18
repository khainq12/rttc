import csv
from pathlib import Path
import tempfile
import unittest

from scripts.fleetqox_rmw_trace_endpoint import (
    _topic_for,
    build_payload,
    compute_receive_capable_deadline_s,
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


def _asymmetric_workload_rows():
    """Peer A ("fleet_controller", stands in for control_station) sends
    4 messages to peer B ("robot_0000") spread out to t=15000ms -- a
    long downlink schedule, matching control_station sending /control
    to all 16 robots in the real benchmark. Peer B sends only 1 message
    of its own, at t=0ms -- a MUCH shorter own-schedule, matching a
    single robot's own 5-topic uplink versus control_station's combined
    downlink to every robot."""
    rows = []
    for i, ts in enumerate([0.0, 5000.0, 10000.0, 15000.0]):
        rows.append({
            "event_id": f"a{i}", "policy": "fifo", "flow_class": "control",
            "src": "fleet_controller", "dst": "robot_0000",
            "bytes": "96", "deadline_ms": "45.0", "timestamp_ms": str(ts),
        })
    rows.append({
        "event_id": "b0", "policy": "fifo", "flow_class": "state",
        "src": "robot_0000", "dst": "fleet_controller",
        "bytes": "320", "deadline_ms": "120.0", "timestamp_ms": "0.0",
    })
    return rows


class ReceiveCapableDeadlineTest(unittest.TestCase):
    """RED/GREEN for the receiver-closes-early harness bug found via the
    18/09/2026 kernel-checkpoint investigation (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md "MECHANISM PROVEN" and "ROOT
    CAUSE TÌM RA"):

        Peer A vẫn còn gửi
                v
        Peer B đã gửi xong phần của B
                v
        B KHÔNG ĐƯỢC shutdown  [X]
                v
        B phải tiếp tục nhận cho đến
        global experiment end / receive deadline

    Direct kernel observation (48/48 robot x rep combinations) showed
    each robot's own port-9100 socket being unhashed -- once,
    permanently -- within ~500ms of that robot's own delivered->missing
    transition, out of a ~19-second run. Reading fleetqox_rmw_trace_endpoint.py
    found why: the OLD drain_deadline was `time.monotonic() + args.drain_s`,
    computed the instant THIS endpoint's own outgoing loop finished --
    with no awareness that a peer (e.g. control_station, whose combined
    downlink schedule to all 16 robots is far longer than any single
    robot's own uplink schedule) might still be mid-stream sending TO it.
    """

    def test_old_own_schedule_only_formula_cuts_off_before_peer_finishes(self):
        """RED: proves the bug still present in main() at the time this
        test was written. Replicates EXACTLY the old inline formula
        (drain_deadline computed from only this endpoint's own outgoing
        rows, ignoring incoming ones) and shows it yields a deadline
        BEFORE peer A's last scheduled send -- i.e. robot_0000 would
        destroy_node()/rclpy.shutdown() (closing its receiving socket,
        confirmed via kprobe to be permanent -- no rebind ever follows)
        while fleet_controller is still actively sending to it."""
        rows = _asymmetric_workload_rows()
        own_outgoing = [r for r in rows if r["src"] == "robot_0000"]
        start_offset_ms = 1000.0
        drain_s = 10.0

        old_buggy_deadline_s = (
            max(float(r["timestamp_ms"]) for r in own_outgoing) + start_offset_ms
        ) / 1000.0 + drain_s
        last_incoming_send_s = (15000.0 + start_offset_ms) / 1000.0

        self.assertLess(
            old_buggy_deadline_s, last_incoming_send_s,
            "old own-schedule-only formula must finish BEFORE peer A's "
            "last send for this fixture to actually reproduce the bug",
        )

    def test_new_formula_stays_alive_through_peers_last_send(self):
        """GREEN: the fixed formula must cover every row where this
        endpoint is dst (every peer's send TO it), not just its own
        outgoing rows -- so it must stay alive at least drain_s past
        peer A's LAST scheduled send, regardless of how short B's own
        outgoing schedule is."""
        rows = _asymmetric_workload_rows()
        start_offset_ms = 1000.0
        drain_s = 10.0

        deadline_s = compute_receive_capable_deadline_s(rows, start_offset_ms, drain_s)
        last_incoming_send_s = (15000.0 + start_offset_ms) / 1000.0

        self.assertGreaterEqual(
            deadline_s, last_incoming_send_s + drain_s,
            "receiver must stay alive at least drain_s after the LAST "
            "message ANY peer is scheduled to send it, not just after "
            "its own (possibly much shorter) outgoing schedule",
        )

    def test_symmetric_workload_matches_old_behavior(self):
        """Sanity check: when a peer's own outgoing schedule already IS
        the longest one it's involved in (the common case this bug
        does NOT affect, e.g. two peers with equal traffic), the new
        formula should not needlessly extend the deadline beyond what
        the schedule actually requires."""
        rows = [
            {
                "event_id": "x0", "policy": "fifo", "flow_class": "state",
                "src": "robot_0000", "dst": "fleet_controller",
                "bytes": "320", "deadline_ms": "120.0", "timestamp_ms": "3000.0",
            },
        ]
        deadline_s = compute_receive_capable_deadline_s(rows, 1000.0, 10.0)
        self.assertAlmostEqual(deadline_s, (3000.0 + 1000.0) / 1000.0 + 10.0)

    def test_empty_rows_falls_back_to_drain_s(self):
        self.assertEqual(compute_receive_capable_deadline_s([], 1000.0, 10.0), 10.0)


if __name__ == "__main__":
    unittest.main()
