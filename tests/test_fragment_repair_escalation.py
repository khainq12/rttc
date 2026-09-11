import unittest

from scripts.run_rmw_docker_fragment_repair_escalation import (
    DEFAULT_LADDER,
    diagnose_run,
)


def _metrics(
    *,
    nacks_sent: int = 0,
    nacks_received: int = 0,
    retransmitted: int = 0,
    ttl_expirations: int = 0,
    nack_exhausted: int = 0,
    oversize_drops: int = 0,
    metadata_mismatch_drops: int = 0,
) -> dict:
    return {
        "fragment_nacks_sent": nacks_sent,
        "fragment_nacks_received": nacks_received,
        "fragments_selectively_retransmitted": retransmitted,
        "fragment_assembly_ttl_expirations": ttl_expirations,
        "fragment_nack_exhausted_assemblies": nack_exhausted,
        "fragment_assembly_oversize_drops": oversize_drops,
        "fragment_assembly_metadata_mismatch_drops": metadata_mismatch_drops,
    }


def result(
    *,
    passed: bool,
    publisher: dict | None = None,
    relay: dict | None = None,
    subscriber: dict | None = None,
) -> dict:
    return {
        "status": "ok",
        "control_delivery_ratio": 1.0 if passed else 0.5,
        "state_delivery_ratio": 1.0 if passed else 0.5,
        "publisher_fragment_repair_metrics": publisher or _metrics(),
        "relay_fragment_repair_metrics": relay or _metrics(),
        "subscriber_fragment_repair_metrics": subscriber or _metrics(),
    }


class DiagnoseRunTest(unittest.TestCase):
    def test_clean_pass(self):
        diagnosis = diagnose_run(result(passed=True))
        self.assertTrue(diagnosis["passed"])
        self.assertEqual(diagnosis["reason"], "no_miss")

    def test_miss_with_no_detected_fragment_loss_is_suspicious(self):
        diagnosis = diagnose_run(result(passed=False))
        self.assertFalse(diagnosis["passed"])
        self.assertEqual(diagnosis["reason"], "miss_without_detected_fragment_loss")

    def test_leg1_nack_never_reached_publisher(self):
        # relay (leg1 receiver) detected loss and its assembly TTL expired,
        # but the publisher (leg1 source) never logged receiving a NACK.
        diagnosis = diagnose_run(
            result(
                passed=False,
                relay=_metrics(nacks_sent=3, ttl_expirations=1),
                publisher=_metrics(),
            )
        )
        self.assertEqual(
            diagnosis["reason"], "leg1_publisher_to_relay:nack_sent_but_never_reached_source"
        )

    def test_leg1_publisher_received_nack_but_never_repaired(self):
        diagnosis = diagnose_run(
            result(
                passed=False,
                relay=_metrics(nacks_sent=3, ttl_expirations=1),
                publisher=_metrics(nacks_received=3, retransmitted=0),
            )
        )
        self.assertEqual(
            diagnosis["reason"],
            "leg1_publisher_to_relay:source_received_nack_but_never_sent_repair",
        )

    def test_leg1_repair_sent_but_still_too_late(self):
        diagnosis = diagnose_run(
            result(
                passed=False,
                relay=_metrics(nacks_sent=3, ttl_expirations=1),
                publisher=_metrics(nacks_received=3, retransmitted=3),
            )
        )
        self.assertEqual(
            diagnosis["reason"],
            "leg1_publisher_to_relay:repair_sent_but_ttl_expired_before_arrival",
        )

    def test_leg2_used_when_leg1_is_clean(self):
        # relay->subscriber leg has its own TTL expiration while leg1 shows
        # nothing wrong at all.
        diagnosis = diagnose_run(
            result(
                passed=False,
                subscriber=_metrics(nacks_sent=2, ttl_expirations=1),
                relay=_metrics(nacks_received=0),
            )
        )
        self.assertEqual(
            diagnosis["reason"], "leg2_relay_to_subscriber:nack_sent_but_never_reached_source"
        )

    def test_nack_budget_exhausted_takes_priority_over_ttl(self):
        diagnosis = diagnose_run(
            result(
                passed=False,
                relay=_metrics(nacks_sent=3, ttl_expirations=1, nack_exhausted=1),
            )
        )
        self.assertEqual(
            diagnosis["reason"], "leg1_publisher_to_relay:nack_budget_exhausted"
        )

    def test_reassembly_failure_takes_priority_over_leg_chain(self):
        diagnosis = diagnose_run(
            result(
                passed=False,
                relay=_metrics(nacks_sent=3, metadata_mismatch_drops=1),
            )
        )
        self.assertEqual(diagnosis["reason"], "reassembly_failure")

    def test_ladder_starts_at_established_good_baseline(self):
        label, robot_count, loss_scale = DEFAULT_LADDER[0]
        self.assertEqual(robot_count, 1)
        self.assertEqual(loss_scale, 0.0)
        self.assertIn("0% loss", label)

    def test_ladder_escalates_robot_count_at_fixed_loss(self):
        robot_counts = [step[1] for step in DEFAULT_LADDER[1:]]
        self.assertEqual(robot_counts, [1, 4, 8, 16, 32])
        loss_scales = [step[2] for step in DEFAULT_LADDER[1:]]
        self.assertTrue(all(scale == loss_scales[0] for scale in loss_scales))


if __name__ == "__main__":
    unittest.main()
