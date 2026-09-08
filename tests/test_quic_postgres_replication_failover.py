import copy
import json
from pathlib import Path
import unittest

from scripts.run_rmw_docker_quic_postgres_replication_failover_probe import (
    PRIMARY_ALIAS,
    STANDBY_ALIAS,
    active_failure_service_ok,
    case_ok,
    standby_service_ok,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = (
    ROOT
    / "results_rmw_socket"
    / "docker_quic_postgresql_replication_failover_probe_summary.json"
)


class QuicPostgresReplicationFailoverTest(unittest.TestCase):
    @unittest.skipUnless(ARTIFACT.is_file(), "external Docker evidence artifact absent")
    def test_validators_reject_replication_promotion_or_recovery_regressions(
        self,
    ) -> None:
        summary = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        run = summary["runs"][0]
        self.assertTrue(case_ok(run))
        self.assertTrue(active_failure_service_ok(run["active"]["service"]))
        self.assertTrue(standby_service_ok(run["standby"]["service"]))

        mutations = []
        mutated = copy.deepcopy(run)
        mutated["replication_before_failure"]["sync_state"] = "async"
        mutated["replication_before_failure"]["status"] = "failed"
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["promotion"]["promoted_read_write"] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["active"]["service"]["writer_lease_lost"] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["standby"]["service"]["metrics"]["durable_state"]["endpoint"] = (
            f"postgresql://{PRIMARY_ALIAS}:5432/fleetqox"
        )
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["standby"]["service"]["metrics"]["durable_state"][
            "writer_lease"
        ]["fence_token"] = 1
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["standby"]["service"]["metrics"]["recovered_frames"] = 1
        mutated["seeded_frames_recovered_after_database_promotion"] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["standby"]["qlog_file_count"] = 0
        mutations.append(mutated)
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                self.assertFalse(case_ok(mutation))

    @unittest.skipUnless(ARTIFACT.is_file(), "external Docker evidence artifact absent")
    def test_canonical_synchronous_replication_failover_passes_five_runs(
        self,
    ) -> None:
        summary = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["run_count"], 5)
        self.assertEqual(summary["successful_runs"], 5)
        self.assertEqual(summary["failed_run_count"], 0)
        self.assertEqual(summary["container_count_per_run"], 6)
        self.assertEqual(summary["database_instance_count_per_run"], 2)
        self.assertEqual(summary["gateway_instance_count_per_run"], 2)
        self.assertTrue(summary["real_quic_v1_h3"])
        self.assertTrue(summary["postgresql_streaming_replication_claim"])
        self.assertTrue(summary["postgresql_synchronous_replication_claim"])
        self.assertTrue(summary["database_process_failure_injected_claim"])
        self.assertTrue(summary["manual_database_standby_promotion_claim"])
        self.assertTrue(summary["gateway_reconnect_to_promoted_database_claim"])
        self.assertTrue(summary["seeded_frame_admission_zero_loss_claim"])
        self.assertTrue(summary["post_promotion_monotonic_fence_token_claim"])
        self.assertLess(
            summary["max_database_failure_to_gateway_ready_ms"], 15000
        )
        self.assertFalse(summary["automatic_database_leader_election_claim"])
        self.assertFalse(summary["consensus_backend_claim"])
        self.assertFalse(summary["network_partition_split_brain_tolerance_claim"])
        self.assertFalse(summary["active_active_gateway_claim"])
        self.assertFalse(summary["regional_disaster_recovery_claim"])
        self.assertFalse(summary["production_readiness"])
        for run in summary["runs"]:
            self.assertTrue(case_ok(run))
            checkpoint = run["replication_before_failure"]
            self.assertEqual(checkpoint["state"], "streaming")
            self.assertEqual(checkpoint["sync_state"], "sync")
            self.assertGreater(checkpoint["flush_lsn_bytes"], 0)
            self.assertGreater(checkpoint["replay_lsn_bytes"], 0)
            active_endpoint = run["active"]["service"]["metrics"][
                "durable_state"
            ]["endpoint"]
            standby_endpoint = run["standby"]["service"]["metrics"][
                "durable_state"
            ]["endpoint"]
            self.assertIn(PRIMARY_ALIAS, active_endpoint)
            self.assertIn(STANDBY_ALIAS, standby_endpoint)
            self.assertNotIn("@", active_endpoint)
            self.assertNotIn("@", standby_endpoint)

    def test_runner_wires_sync_replication_failure_and_promotion(self) -> None:
        runner = (
            ROOT
            / "scripts"
            / "run_rmw_docker_quic_postgres_replication_failover_probe.py"
        ).read_text()
        lease = (ROOT / "fleetqox" / "quic_gateway_lease.py").read_text()
        state = (ROOT / "fleetqox" / "quic_gateway_state.py").read_text()
        self.assertIn("pg_basebackup", runner)
        self.assertIn("synchronous_standby_names", runner)
        self.assertIn('"docker", "kill", primary', runner)
        self.assertIn('"promote", "-w"', runner)
        self.assertIn("target_session_attrs=read-write", runner)
        self.assertIn("FramePersistenceUnavailableError", lease)
        self.assertIn("FramePersistenceUnavailableError", state)

    def test_capability_manifest_preserves_ha_boundary(self) -> None:
        manifest = json.loads(
            (
                ROOT
                / "ros2_ws"
                / "src"
                / "rmw_fleetqox_cpp"
                / "capabilities.json"
            ).read_text(encoding="utf-8")
        )["claim_boundaries"]
        self.assertTrue(
            manifest["docker_quic_postgresql_replication_failover_5run_probe"]
        )
        self.assertTrue(
            manifest["quic_gateway_postgresql_synchronous_replication_claim"]
        )
        self.assertTrue(manifest["quic_gateway_database_process_failover_claim"])
        self.assertTrue(manifest["quic_gateway_replicated_database_claim"])
        self.assertTrue(
            manifest["quic_gateway_automatic_database_leader_election_claim"]
        )
        self.assertTrue(manifest["quic_gateway_consensus_backend_claim"])
        self.assertTrue(
            manifest["quic_gateway_partition_split_brain_tolerance_claim"]
        )
        self.assertTrue(
            manifest["quic_gateway_regional_disaster_recovery_claim"]
        )


if __name__ == "__main__":
    unittest.main()
