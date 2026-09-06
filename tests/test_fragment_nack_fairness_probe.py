from __future__ import annotations

import unittest

from scripts.run_rmw_docker_fragment_nack_fairness_probe import (
    INJECTOR_SCHEMA_VERSION,
    RECEIVER_SCHEMA_VERSION,
    summarize_probe,
)


class FragmentNackFairnessProbeTests(unittest.TestCase):
    def test_summary_requires_exact_fleet_fair_share(self) -> None:
        receiver = {
            "schema_version": RECEIVER_SCHEMA_VERSION,
            "status": "ok",
            "metrics": {
                "fragment_active_assemblies": 513,
                "fragment_active_missing_indexes": 513 * 15,
                "fragment_nack_exhausted_assemblies": 513,
                "fragment_nacks_sent": 513,
                "fragment_nack_indexes_requested": 520,
                "fragment_nack_index_budget_reductions": 512,
                "fragment_nack_max_sweep_indexes_requested": 512,
                "fragment_nack_sweep_budget_exhaustions": 1,
            },
        }
        injector = {
            "schema_version": INJECTOR_SCHEMA_VERSION,
            "status": "ok",
            "request_count": 513,
            "unique_fragment_id_count": 513,
            "index_ranges": ["0", "0-7"],
        }
        summary = summarize_probe(
            receiver,
            injector,
            receiver_returncode=0,
            injector_returncode=0,
        )
        self.assertEqual(summary["status"], "ok")
        self.assertTrue(
            summary["fleet_aware_fragment_nack_fairness_claim"]
        )
        self.assertTrue(summary["bounded_fragment_repair_burst_claim"])
        self.assertFalse(
            summary["production_large_sample_reliability_claim"]
        )

        # "0-8" exceeds the configured per-assembly index limit of 7 and is
        # rejected by _valid_index_range, unlike "0-1" which is a legitimate
        # (if smaller) fair-share grant.
        injector["index_ranges"] = ["0-8", "0-7"]
        failed = summarize_probe(
            receiver,
            injector,
            receiver_returncode=0,
            injector_returncode=0,
        )
        self.assertEqual(failed["status"], "failed")


if __name__ == "__main__":
    unittest.main()
