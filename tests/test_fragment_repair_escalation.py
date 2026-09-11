import unittest

from scripts.run_rmw_docker_fragment_repair_escalation import (
    DEFAULT_LADDER,
    diagnose_run,
)


def result(
    *,
    passed: bool,
    nacks_sent: int = 0,
    nacks_received: int = 0,
    retransmitted: int = 0,
    ttl_expirations: int = 0,
    nack_exhausted: int = 0,
    oversize_drops: int = 0,
    metadata_mismatch_drops: int = 0,
    active_missing: int = 0,
) -> dict:
    return {
        "status": "ok",
        "control_delivery_ratio": 1.0 if passed else 0.5,
        "state_delivery_ratio": 1.0 if passed else 0.5,
        "relay_fragment_repair_metrics": {
            "fragment_nacks_sent": nacks_sent,
            "fragment_nacks_received": nacks_received,
            "fragments_selectively_retransmitted": retransmitted,
            "fragment_assembly_ttl_expirations": ttl_expirations,
            "fragment_nack_exhausted_assemblies": nack_exhausted,
            "fragment_assembly_oversize_drops": oversize_drops,
            "fragment_assembly_metadata_mismatch_drops": metadata_mismatch_drops,
            "fragment_active_missing_indexes": active_missing,
        },
    }


class DiagnoseRunTest(unittest.TestCase):
    def test_clean_pass(self):
        diagnosis = diagnose_run(result(passed=True))
        self.assertTrue(diagnosis["passed"])
        self.assertEqual(diagnosis["reason"], "no_miss")

    def test_miss_with_no_detected_fragment_loss_is_suspicious(self):
        diagnosis = diagnose_run(result(passed=False))
        self.assertFalse(diagnosis["passed"])
        self.assertFalse(diagnosis["fragment_loss_observed"])
        self.assertEqual(diagnosis["reason"], "miss_without_detected_fragment_loss")

    def test_nack_sent_but_sender_never_saw_it(self):
        diagnosis = diagnose_run(result(passed=False, nacks_sent=3))
        self.assertEqual(diagnosis["reason"], "nack_sent_but_not_received_by_sender")

    def test_nack_received_but_no_repair_sent(self):
        diagnosis = diagnose_run(
            result(passed=False, nacks_sent=3, nacks_received=3)
        )
        self.assertEqual(diagnosis["reason"], "nack_received_but_no_repair_sent")

    def test_repair_sent_but_ttl_expired(self):
        diagnosis = diagnose_run(
            result(
                passed=False,
                nacks_sent=3,
                nacks_received=3,
                retransmitted=3,
                ttl_expirations=1,
            )
        )
        self.assertEqual(diagnosis["reason"], "repair_sent_but_ttl_expired_before_arrival")

    def test_reassembly_failure_takes_priority(self):
        diagnosis = diagnose_run(
            result(passed=False, nacks_sent=3, metadata_mismatch_drops=1)
        )
        self.assertEqual(diagnosis["reason"], "reassembly_failure")

    def test_ladder_starts_at_established_good_baseline(self):
        label, robot_count, loss_scale = DEFAULT_LADDER[0]
        self.assertEqual(robot_count, 1)
        self.assertEqual(loss_scale, 0.0)
        self.assertIn("0% loss", label)

    def test_ladder_escalates_robot_count_at_fixed_loss(self):
        robot_counts = [step[1] for step in DEFAULT_LADDER[1:]]
        self.assertEqual(robot_counts, [1, 4, 8, 16])
        loss_scales = [step[2] for step in DEFAULT_LADDER[1:]]
        self.assertTrue(all(scale == loss_scales[0] for scale in loss_scales))


if __name__ == "__main__":
    unittest.main()
