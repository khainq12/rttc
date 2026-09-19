import unittest

from scripts.fleetqox_coordination_endpoint import (
    coordination_discovery_converged,
    wait_until_deadline_while_spinning,
)


class WaitUntilDeadlineWhileSpinningTest(unittest.TestCase):
    """RED/GREEN for the 17/09/2026 'làm sạch harness Bảng VI' pass (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md): three call sites in
    fleetqox_coordination_endpoint.py's crossing loop (shared start-offset
    delay, per-crossing request stagger, per-retry jitter) used a blind
    time.sleep() while the endpoint was idle -- not requesting, not
    holding the critical section -- so a peer's incoming REQUEST arriving
    during the wait sat undelivered to on_request() until the sleep
    ended, delaying this endpoint's reply and potentially inflating
    coordination_message_age / triggering spurious retries. Same bug
    CLASS as the Bảng V trace-replay harness's dispatch-gap bug, but this
    helper differs: replies are queued (pending_immediate_replies), not
    sent synchronously from the callback, so the fix must ALSO drain
    (drain_pending_replies + release_stale_deferrals) each iteration, not
    just spin_once() -- hence a separate, adapted helper rather than
    importing the trace-endpoint one.
    """

    def _make_fake_spin(self, ready_at, serviced_log, clock):
        state = {"serviced": False}

        def spin_once_fn(timeout_sec):
            clock[0] += timeout_sec
            if not state["serviced"] and clock[0] >= ready_at:
                state["serviced"] = True
                serviced_log.append(clock[0])

        return spin_once_fn

    def test_incoming_request_ready_mid_wait_is_serviced_before_deadline(self):
        clock = [0.0]
        serviced_log = []
        spin_once_fn = self._make_fake_spin(ready_at=0.5, serviced_log=serviced_log, clock=clock)
        drain_calls = []

        wait_until_deadline_while_spinning(
            deadline_monotonic=1.0,
            spin_once_fn=spin_once_fn,
            drain_fn=lambda: drain_calls.append(clock[0]),
            poll_interval_s=0.05,
            now_fn=lambda: clock[0],
        )

        self.assertEqual(len(serviced_log), 1, "incoming request never got serviced during the wait")
        self.assertLess(
            serviced_log[0], 1.0,
            "request only serviced at/after the deadline -- this is exactly the old blind "
            "time.sleep() behavior (peer's REQUEST held up until this endpoint's next "
            "scheduled action), not fixed",
        )
        self.assertGreater(len(drain_calls), 0, "drain_fn (drain_pending_replies/release_stale_deferrals) never called during the wait")

    def test_never_returns_before_deadline(self):
        clock = [0.0]
        spin_once_fn = self._make_fake_spin(ready_at=999.0, serviced_log=[], clock=clock)

        wait_until_deadline_while_spinning(
            deadline_monotonic=1.0,
            spin_once_fn=spin_once_fn,
            drain_fn=lambda: None,
            poll_interval_s=0.05,
            now_fn=lambda: clock[0],
        )

        self.assertGreaterEqual(clock[0], 1.0)

    def test_preserves_original_stagger_duration_not_early_not_late_by_more_than_poll_interval(self):
        # The fix must not shift workload timing: with instantaneous fake
        # spin_once calls (timeout consumed exactly), the loop should stop
        # within one poll_interval_s of the deadline, not overshoot by a
        # full extra burst/poll cycle.
        clock = [0.0]

        def spin_once_fn(timeout_sec):
            clock[0] += timeout_sec

        wait_until_deadline_while_spinning(
            deadline_monotonic=1.0,
            spin_once_fn=spin_once_fn,
            drain_fn=lambda: None,
            poll_interval_s=0.05,
            now_fn=lambda: clock[0],
        )
        self.assertGreaterEqual(clock[0], 1.0)
        self.assertLess(clock[0], 1.0 + 0.05 + 1e-9)

    def test_already_past_deadline_returns_without_spinning_or_draining(self):
        clock = [2.0]
        spin_calls = []
        drain_calls = []
        wait_until_deadline_while_spinning(
            deadline_monotonic=1.0,
            spin_once_fn=lambda timeout_sec: spin_calls.append(timeout_sec),
            drain_fn=lambda: drain_calls.append(True),
            poll_interval_s=0.05,
            now_fn=lambda: clock[0],
        )
        self.assertEqual(spin_calls, [])
        self.assertEqual(drain_calls, [])


class CoordinationDiscoveryConvergedReadinessContractTest(unittest.TestCase):
    """RED/GREEN for Table VI's own independent false-ready bug (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI READINESS CORRECTNESS
    FIX"): fleetqox_coordination_endpoint.py had a structurally
    IDENTICAL bug to the one already fixed in
    fleetqox_rmw_trace_endpoint.py, in its own separate copy of the
    discovery loop -- args.ready_file.touch() ran unconditionally after
    the loop regardless of whether it exited via genuine convergence or
    via --discovery-timeout-s expiring.

    Unlike the Table IV/V fix, this one can and should check PEER
    IDENTITY, not just a count: discovery_peers_seen already collects
    NAMES (each endpoint publishes its own args.endpoint as the beacon
    payload), and the required peer set (--peers, already excludes self
    by construction in the orchestrator's peers_env) is available too --
    so "correct count, wrong identity" (CASE G) can be told apart from
    genuine convergence, which a pure count check could not do.
    """

    def test_case_a_all_required_peers_seen_is_converged(self):
        self.assertTrue(
            coordination_discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                required_peers=frozenset({"R2", "R3", "R4"}),
                peers_seen=frozenset({"R2", "R3", "R4"}),
            )
        )

    def test_case_b_missing_one_required_peer_is_not_converged(self):
        self.assertFalse(
            coordination_discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                required_peers=frozenset({"R2", "R3", "R4"}),
                peers_seen=frozenset({"R2", "R3"}),
            )
        )

    def test_case_c_zero_peers_is_not_converged(self):
        self.assertFalse(
            coordination_discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                required_peers=frozenset({"R2", "R3", "R4"}),
                peers_seen=frozenset(),
            )
        )

    def test_case_d_progressive_convergence_reaches_ready(self):
        required = frozenset({"R2", "R3", "R4"})
        # Modeled as 3 successive evaluations of the same pure function
        # against a growing peers_seen set -- discovery_converged() has
        # no memory of "when", only "what was seen so far", which is
        # exactly why it's safe to unit test without a real clock.
        self.assertFalse(
            coordination_discovery_converged(
                skip_discovery_wait=False, beacon_active=True,
                required_peers=required, peers_seen=frozenset({"R2"}),
            )
        )
        self.assertFalse(
            coordination_discovery_converged(
                skip_discovery_wait=False, beacon_active=True,
                required_peers=required, peers_seen=frozenset({"R2", "R3"}),
            )
        )
        self.assertTrue(
            coordination_discovery_converged(
                skip_discovery_wait=False, beacon_active=True,
                required_peers=required, peers_seen=frozenset({"R2", "R3", "R4"}),
            )
        )

    def test_case_e_convergence_evaluated_at_timeout_missing_one_stays_invalid(self):
        # The exact "convergence after timeout" scenario: if the decision
        # is evaluated (as the real loop does, once, at the moment the
        # deadline fires) while R4 is still missing, it must be
        # INVALID -- a peer that shows up a moment later must not
        # retroactively rewrite an already-made decision. This function
        # being pure/stateless is exactly what guarantees that: it only
        # ever sees the snapshot it's given.
        self.assertFalse(
            coordination_discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                required_peers=frozenset({"R2", "R3", "R4"}),
                peers_seen=frozenset({"R2", "R3"}),
            )
        )

    def test_case_f_required_peers_never_include_self_by_construction(self):
        # For N=4, R1's required set is {R2,R3,R4} -- R1 is simply never
        # a member of either set passed in (guaranteed upstream by the
        # orchestrator's peers_env = "every OTHER endpoint", unchanged by
        # this fix). The function itself needs no special-casing for
        # self-exclusion; this test documents that contract.
        self.assertTrue(
            coordination_discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                required_peers=frozenset({"R2", "R3", "R4"}),
                peers_seen=frozenset({"R2", "R3", "R4"}),
            )
        )

    def test_case_g_correct_count_wrong_identity_is_not_converged(self):
        # 3 required, 3 seen -- a COUNT-only check would wrongly pass
        # this. Identity-based checking correctly rejects it because R4
        # itself was never actually seen.
        self.assertFalse(
            coordination_discovery_converged(
                skip_discovery_wait=False,
                beacon_active=True,
                required_peers=frozenset({"R2", "R3", "R4"}),
                peers_seen=frozenset({"R2", "R3", "unrelated_peer"}),
            )
        )

    def test_skip_discovery_wait_is_always_converged(self):
        # FleetRMW's static-mode contract, unaffected by this fix.
        self.assertTrue(
            coordination_discovery_converged(
                skip_discovery_wait=True,
                beacon_active=False,
                required_peers=frozenset(),
                peers_seen=frozenset(),
            )
        )


if __name__ == "__main__":
    unittest.main()
