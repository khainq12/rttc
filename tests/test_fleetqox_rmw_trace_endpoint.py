import csv
from pathlib import Path
import tempfile
import unittest

from scripts.fleetqox_rmw_trace_endpoint import (
    _topic_for,
    build_payload,
    compute_receive_capable_deadline_s,
    discovery_converged,
    load_rows,
    run_receive_idle_drain_loop,
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


def _make_fake_spin_with_arrivals(clock, arrival_times, received_counter):
    """spin_once_fn for the idle-drain tests below: advances the fake
    clock by timeout_sec each call (simulating a blocking rmw_wait()),
    and increments received_counter[0] once for each scheduled arrival
    time the clock has now reached (each fires exactly once, in
    chronological order) -- stands in for on_message() appending to
    the real `received` list when a benchmark DATA message shows up."""
    pending = sorted(arrival_times)

    def spin_once_fn(timeout_sec):
        clock[0] += timeout_sec
        while pending and clock[0] >= pending[0]:
            pending.pop(0)
            received_counter[0] += 1

    return spin_once_fn


class OldFixedDeadlineStillShutsDownBeforeLateDataTest(unittest.TestCase):
    """RED (18/09/2026, see docs/AUDIT_ACCEPTANCE_TRACKING.md "RED ->
    MINIMAL FIX -> GREEN: idle-timeout shutdown"): proves the bug that
    SURVIVES the a6dffd1 fix, using a6dffd1's OWN unmodified
    wait_until_deadline_while_spinning + a fixed deadline exactly as
    main() computes it today (nominal peer schedule + drain_s, with no
    awareness of when messages ACTUALLY arrive). Live LAN N=16
    measurement (18/09/2026 "actual send vs shutdown" pass) found
    real /control sends landing ~15.5s after their own nominal
    schedule, and sent_after_shutdown_total == lost_total EXACTLY (100%
    of residual loss, all 3 reps) -- this fixture reproduces that shape
    at unit-test scale: nominal last send at t=3s, but the ACTUAL
    message only arrives at t=18s.
    """

    def test_fixed_deadline_elapses_before_late_actual_message_arrives(self):
        clock = [0.0]
        received_counter = [0]
        # Nominal peer schedule says last send at t=3s; drain_s=10s ->
        # fixed deadline = 13s, exactly how a6dffd1's main() computes it
        # today (compute_receive_capable_deadline_s + drain_s, no idle
        # extension).
        fixed_deadline = 3.0 + 10.0
        spin_once_fn = _make_fake_spin_with_arrivals(
            clock, arrival_times=[18.0], received_counter=received_counter
        )

        wait_until_deadline_while_spinning(
            deadline_monotonic=fixed_deadline,
            spin_once_fn=spin_once_fn,
            poll_interval_s=0.5,
            now_fn=lambda: clock[0],
        )

        self.assertLess(
            clock[0], 18.0,
            "the CURRENT (a6dffd1) fixed-deadline behavior must return "
            "-- i.e. this endpoint decides to shut down -- BEFORE the "
            "late actual message at t=18s ever arrives, reproducing the "
            "exact bug proven live: sent_after_shutdown_total == "
            "lost_total, 100%, all 3 reps",
        )
        self.assertEqual(
            received_counter[0], 0,
            "the late message must NOT have been observed yet when the "
            "current implementation already decided to shut down",
        )


class ReceiveIdleDrainLoopTest(unittest.TestCase):
    """GREEN + edge cases (18/09/2026) for run_receive_idle_drain_loop(),
    the minimal fix for the bug OldFixedDeadlineStillShutsDownBeforeLateDataTest
    proves above: instead of a single a-priori deadline, stay alive as
    long as new benchmark messages keep arriving (extend by drain_s each
    time), never returning before the existing nominal-schedule lower
    bound. All tests use a fake clock/spin_once_fn -- no real sleep.

    Deliberate design boundary, not a gap: an arrival must be OBSERVED
    (i.e. happen before the deadline in force AT THAT TIME) to extend
    anything -- a message that would be the very FIRST ever received,
    arriving strictly AFTER the initial nominal-schedule floor has
    already elapsed with zero prior receive activity, cannot be waited
    for indefinitely (that would violate requirement C: "no data for
    drain_s allows shutdown" -- an endpoint with genuinely no incoming
    traffic must still be able to exit). Real LAN N=16 traffic doesn't
    hit this edge: each robot receives 32-143 /control messages spread
    continuously across the run (not one isolated ping), so an early
    arrival is always available to anchor the first extension -- these
    tests use two/several arrivals for that reason, matching the real
    measured pattern, not a single isolated late message with nothing
    before it.
    """

    def test_a_late_actual_data_extends_lifetime(self):
        clock = [0.0]
        received_counter = [0]
        # Nominal-only floor = 3s + drain_s(10s) = 13s. An actual
        # message arrives at t=8s -- already "late" vs. the nominal
        # t=3s schedule, but still observed BEFORE the floor elapses --
        # and must extend the deadline to 8+10=18, past the floor.
        spin_once_fn = _make_fake_spin_with_arrivals(
            clock, arrival_times=[8.0], received_counter=received_counter
        )

        final_deadline = run_receive_idle_drain_loop(
            initial_deadline_monotonic=13.0,
            drain_s=10.0,
            spin_once_fn=spin_once_fn,
            received_count_fn=lambda: received_counter[0],
            poll_interval_s=0.5,
            now_fn=lambda: clock[0],
        )

        self.assertEqual(received_counter[0], 1, "the late message must have been observed")
        self.assertGreaterEqual(
            final_deadline, 8.0 + 10.0 - 0.5,  # - poll_interval_s slack
            "after receiving the late (t=8s) message, must stay alive "
            "until roughly 8s + drain_s = 18s, not just until the "
            "original nominal-only floor of 13s",
        )

    def test_b_second_later_data_extends_lifetime_again(self):
        clock = [0.0]
        received_counter = [0]
        # First arrival at t=8 pushes the deadline to ~18 -- but a
        # SECOND arrival at t=15 (still before 18, so still observed)
        # must push it further, to ~25, not let the endpoint shut down
        # at the first extension alone.
        spin_once_fn = _make_fake_spin_with_arrivals(
            clock, arrival_times=[8.0, 15.0], received_counter=received_counter
        )

        final_deadline = run_receive_idle_drain_loop(
            initial_deadline_monotonic=13.0,
            drain_s=10.0,
            spin_once_fn=spin_once_fn,
            received_count_fn=lambda: received_counter[0],
            poll_interval_s=0.5,
            now_fn=lambda: clock[0],
        )

        self.assertEqual(received_counter[0], 2, "both messages must have been observed")
        self.assertGreaterEqual(
            clock[0], 15.0,
            "must not have shut down before the SECOND late message arrived",
        )
        self.assertGreaterEqual(
            final_deadline, 15.0 + 10.0 - 0.5,
            "the second arrival (t=15) must extend the deadline again, "
            "to ~25s -- past what the first arrival alone would have "
            "set (~18s)",
        )
        self.assertLess(
            final_deadline, 18.0 + 10.0,
            "sanity: final deadline should track the SECOND arrival's "
            "own extension (~25s), not some unrelated larger value",
        )

    def test_c_no_data_for_drain_s_allows_shutdown(self):
        clock = [0.0]
        received_counter = [0]
        # No arrivals at all -- the loop must not wait forever or
        # needlessly extend past the initial nominal-schedule deadline.
        spin_once_fn = _make_fake_spin_with_arrivals(
            clock, arrival_times=[], received_counter=received_counter
        )

        final_deadline = run_receive_idle_drain_loop(
            initial_deadline_monotonic=13.0,
            drain_s=10.0,
            spin_once_fn=spin_once_fn,
            received_count_fn=lambda: received_counter[0],
            poll_interval_s=0.5,
            now_fn=lambda: clock[0],
        )

        self.assertAlmostEqual(final_deadline, 13.0, delta=0.5)
        self.assertAlmostEqual(clock[0], 13.0, delta=0.5)

    def test_d_internal_non_application_activity_does_not_extend(self):
        """received_count_fn is deliberately wired to len(received) in
        main() -- on_message()'s OWN list, which only grows for this
        endpoint's subscribed benchmark trace topics. FleetRMW's
        internal ACK/NACK/graph/discovery/repair traffic never touches
        that list. Simulate that here: spin_once_fn is called just as
        often (representing internal protocol activity happening under
        the hood), but received_count_fn NEVER increases -- the
        deadline must be unaffected by that internal churn."""
        clock = [0.0]
        internal_traffic_processed = [0]

        def spin_once_fn(timeout_sec):
            clock[0] += timeout_sec
            internal_traffic_processed[0] += 1  # e.g. an ACK/NACK/graph packet

        final_deadline = run_receive_idle_drain_loop(
            initial_deadline_monotonic=13.0,
            drain_s=10.0,
            spin_once_fn=spin_once_fn,
            received_count_fn=lambda: 0,  # no APPLICATION message ever arrives
            poll_interval_s=0.5,
            now_fn=lambda: clock[0],
        )

        self.assertGreater(
            internal_traffic_processed[0], 0,
            "sanity: spin_once_fn really was called many times (internal traffic happened)",
        )
        self.assertAlmostEqual(
            final_deadline, 13.0, delta=0.5,
            msg="internal/non-application traffic must NOT extend the deadline",
        )

    def test_e_does_not_shut_down_merely_because_own_schedule_finished(self):
        """Integration with compute_receive_capable_deadline_s (the
        a6dffd1 fix, unchanged): using the SAME asymmetric-workload
        fixture as ReceiveCapableDeadlineTest (robot_0000's own outgoing
        schedule finishes at t=0s, but peer fleet_controller is still
        scheduled to send until t=15s), confirm the idle-drain loop's
        INITIAL floor alone (with zero actual messages arriving) already
        keeps the endpoint alive through the peer's nominal schedule --
        it does not exit early just because robot_0000's own send loop
        is done."""
        rows = _asymmetric_workload_rows()
        start_offset_ms = 1000.0
        drain_s = 10.0
        initial_deadline = compute_receive_capable_deadline_s(rows, start_offset_ms, drain_s)
        last_incoming_send_s = (15000.0 + start_offset_ms) / 1000.0

        clock = [0.0]
        spin_once_fn = _make_fake_spin_with_arrivals(clock, arrival_times=[], received_counter=[0])

        final_deadline = run_receive_idle_drain_loop(
            initial_deadline_monotonic=initial_deadline,
            drain_s=drain_s,
            spin_once_fn=spin_once_fn,
            received_count_fn=lambda: 0,
            poll_interval_s=0.5,
            now_fn=lambda: clock[0],
        )

        self.assertGreaterEqual(final_deadline, last_incoming_send_s + drain_s)

    def test_f_nominal_schedule_provides_sensible_initial_lower_bound(self):
        """The initial nominal-schedule deadline must be respected
        EXACTLY as a floor when no application data ever arrives -- not
        overshot by some unrelated margin, and never undershot (the
        endpoint must not exit before expected traffic could plausibly
        still show up)."""
        clock = [0.0]
        spin_once_fn = _make_fake_spin_with_arrivals(clock, arrival_times=[], received_counter=[0])

        final_deadline = run_receive_idle_drain_loop(
            initial_deadline_monotonic=42.0,
            drain_s=10.0,
            spin_once_fn=spin_once_fn,
            received_count_fn=lambda: 0,
            poll_interval_s=0.5,
            now_fn=lambda: clock[0],
        )

        self.assertGreaterEqual(final_deadline, 42.0, "must never undershoot the nominal floor")
        self.assertAlmostEqual(final_deadline, 42.0, delta=0.5, msg="must not overshoot it either, absent any data")

    def test_race_message_arrives_exactly_at_deadline_still_extends(self):
        """Requirement 7 (race condition check): a message that becomes
        ready in the SAME instant the deadline is reached must still be
        observed and still extend the deadline, via the loop's existing
        drain-burst-then-check pattern (no new busy loop)."""
        clock = [0.0]
        received_counter = [0]
        # Arrival exactly AT the initial deadline.
        spin_once_fn = _make_fake_spin_with_arrivals(
            clock, arrival_times=[13.0], received_counter=received_counter
        )

        final_deadline = run_receive_idle_drain_loop(
            initial_deadline_monotonic=13.0,
            drain_s=10.0,
            spin_once_fn=spin_once_fn,
            received_count_fn=lambda: received_counter[0],
            poll_interval_s=0.5,
            now_fn=lambda: clock[0],
        )

        self.assertEqual(received_counter[0], 1, "the boundary message must have been observed")
        self.assertGreaterEqual(
            final_deadline, 13.0 + 10.0 - 0.5,
            "a message arriving exactly at the deadline must still extend it",
        )


class DiscoveryConvergedReadinessContractTest(unittest.TestCase):
    """RED/GREEN for the false-ready benchmark-correctness bug (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md, "ZENOH FALSE-READY HARNESS FIX
    AND VALIDATION"): a --discovery-timeout-s timeout must never by
    itself be treated as READY. discovery_converged() is the pure,
    directly-testable readiness decision extracted out of main()'s
    discovery loop -- these tests exercise it without any wall clock,
    rclpy, or subprocess dependency."""

    def test_case_a_one_of_sixteen_peers_at_timeout_is_not_converged(self):
        # The exact seed=41 Zenoh scenario from the root-cause
        # investigation: control_station saw only 1/16 peers when its
        # discovery loop hit the timeout. OLD behavior touched
        # --ready-file unconditionally here (false ready). CORRECT
        # behavior: NOT converged.
        self.assertFalse(
            discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                peers_seen=1,
                expected_peer_count=16,
                subscription_fallback_ok=False,
            )
        )

    def test_case_b_sixteen_of_sixteen_peers_is_converged(self):
        self.assertTrue(
            discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                peers_seen=16,
                expected_peer_count=16,
                subscription_fallback_ok=False,
            )
        )

    def test_case_c_fifteen_of_sixteen_at_timeout_is_not_converged(self):
        self.assertFalse(
            discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                peers_seen=15,
                expected_peer_count=16,
                subscription_fallback_ok=False,
            )
        )

    def test_case_d_zero_of_sixteen_at_timeout_is_not_converged(self):
        self.assertFalse(
            discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                peers_seen=0,
                expected_peer_count=16,
                subscription_fallback_ok=False,
            )
        )

    def test_case_e_convergence_reached_before_timeout_is_converged(self):
        # Modeled as peers_seen already having reached expected_peer_count
        # (the loop's own break condition) -- discovery_converged() does
        # not need to know timing, only the final peer count, which is
        # exactly what makes it a pure function safe to unit test without
        # a real clock.
        self.assertTrue(
            discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                peers_seen=16,
                expected_peer_count=16,
                subscription_fallback_ok=False,
            )
        )

    def test_skip_discovery_wait_is_always_converged(self):
        # FleetRMW's static-mode contract (--skip-discovery-wait): this
        # endpoint never entered the discovery loop by design. Unaffected
        # by this fix -- must remain immediately ready.
        self.assertTrue(
            discovery_converged(
                skip_discovery_wait=True,
                beacon_active=False,
                peers_seen=0,
                expected_peer_count=0,
                subscription_fallback_ok=False,
            )
        )

    def test_non_beacon_fallback_path_respects_its_own_flag(self):
        # expected_peer_count==0 without skip_discovery_wait (not
        # exercised by any current caller, kept for completeness): must
        # defer to the subscription-count fallback's own outcome, not
        # silently succeed.
        self.assertTrue(
            discovery_converged(
                skip_discovery_wait=False,
                beacon_active=False,
                peers_seen=0,
                expected_peer_count=0,
                subscription_fallback_ok=True,
            )
        )
        self.assertFalse(
            discovery_converged(
                skip_discovery_wait=False,
                beacon_active=False,
                peers_seen=0,
                expected_peer_count=0,
                subscription_fallback_ok=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
