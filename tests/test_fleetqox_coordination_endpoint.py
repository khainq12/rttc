import unittest

from scripts.fleetqox_coordination_endpoint import wait_until_deadline_while_spinning


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


if __name__ == "__main__":
    unittest.main()
