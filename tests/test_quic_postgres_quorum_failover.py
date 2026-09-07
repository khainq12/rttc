import copy
import json
from pathlib import Path
import unittest

from scripts.run_rmw_docker_quic_postgres_quorum_failover_probe import (
    case_ok,
    controllers_ok,
    failback_controllers_ok,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = (
    ROOT
    / "results_rmw_socket"
    / "docker_quic_postgresql_quorum_failover_probe_summary.json"
)


class QuicPostgresQuorumFailoverTest(unittest.TestCase):
    @unittest.skipUnless(ARTIFACT.is_file(), "external Docker evidence artifact absent")
    def test_validators_reject_quorum_single_winner_or_recovery_regressions(
        self,
    ) -> None:
        summary = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        run = summary["runs"][0]
        self.assertTrue(case_ok(run))
        self.assertTrue(controllers_ok(run["controllers"]))
        self.assertTrue(failback_controllers_ok(run["failback_controllers"]))

        mutations = []
        mutated = copy.deepcopy(run)
        mutated["etcd_cluster"]["member_count"] = 2
        mutated["etcd_cluster"]["status"] = "failed"
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["quorum_loss_control"][
            "standby_remained_in_recovery_without_quorum"
        ] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        observed = next(
            row for row in mutated["controllers"]
            if row["telemetry"]["status"] == "promotion_observed"
        )
        observed["telemetry"]["status"] = "promoted"
        observed["telemetry"]["dcs_lock_acquired"] = True
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        winner = next(
            row for row in mutated["controllers"]
            if row["telemetry"]["status"] == "promoted"
        )
        winner["telemetry"]["dcs_lock_acquired"] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        winner = next(
            row for row in mutated["controllers"]
            if row["telemetry"]["status"] == "promoted"
        )
        winner["telemetry"]["quorum_acquisition_failures"] = 0
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["fence_agent"]["telemetry"]["dcs_lease_authorized"] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["fence_agent"]["telemetry"]["running_before"] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["fence_agent"]["telemetry"]["fence_confirmed_unix_ns"] = (
            next(
                row["telemetry"]["promotion_confirmed_unix_ns"]
                for row in mutated["controllers"]
                if row["telemetry"]["status"] == "promoted"
            )
            + 1
        )
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["quorum_loss_control"][
            "primary_remained_running_without_quorum"
        ] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["fence_security_negative_controls"][
            "unauthenticated_client_rejected"
        ] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["post_failover_rejoin"]["target_in_recovery"] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["failback_quorum_loss_control"][
            "database_roles_unchanged_without_quorum"
        ] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["failback_quorum_loss_control"][
            "all_controllers_rejected_unsafe_replication"
        ] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        failback_winner = next(
            row for row in mutated["failback_controllers"]
            if row["telemetry"]["status"] == "failed_back"
        )
        failback_winner["telemetry"]["dcs_lock_acquired"] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["switchover_agent"]["telemetry"][
            "dcs_lease_authorized"
        ] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["switchover_security_negative_controls"][
            "authenticated_forged_lease_rejected"
        ] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["planned_failback"][
            "current_primary_stopped_before_promotion"
        ] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["failback"]["service"]["metrics"]["durable_state"][
            "writer_lease"
        ]["fence_token"] = 2
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["post_failback_redundancy"]["target_in_recovery"] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["automatic_promotion"]["promoted_read_write"] = False
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["standby"]["service"]["metrics"]["durable_state"][
            "writer_lease"
        ]["fence_token"] = 1
        mutations.append(mutated)
        mutated = copy.deepcopy(run)
        mutated["seeded_admission_state_recovered"] = False
        mutations.append(mutated)
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                self.assertFalse(case_ok(mutation))

    @unittest.skipUnless(ARTIFACT.is_file(), "external Docker evidence artifact absent")
    def test_canonical_quorum_gated_automatic_failover_passes_five_runs(
        self,
    ) -> None:
        summary = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["run_count"], 5)
        self.assertEqual(summary["successful_runs"], 5)
        self.assertEqual(summary["failed_run_count"], 0)
        self.assertEqual(summary["container_count_per_run"], 19)
        self.assertEqual(summary["etcd_member_count_per_run"], 3)
        self.assertEqual(summary["postgresql_instance_count_per_run"], 3)
        self.assertEqual(summary["failover_controller_count_per_run"], 2)
        self.assertEqual(summary["failback_controller_count_per_run"], 2)
        self.assertEqual(summary["gateway_instance_count_per_run"], 3)
        self.assertTrue(summary["real_quic_v1_h3"])
        self.assertTrue(summary["etcd_raft_quorum_claim"])
        self.assertTrue(summary["quorum_loss_promotion_fail_closed_claim"])
        self.assertTrue(summary["n_minus_one_quorum_recovery_claim"])
        self.assertTrue(summary["single_dcs_promotion_winner_claim"])
        self.assertTrue(summary["automatic_database_promotion_claim"])
        self.assertTrue(
            summary["synchronous_replication_seeded_state_continuity_claim"]
        )
        self.assertTrue(summary["gateway_takeover_after_quorum_promotion_claim"])
        self.assertFalse(
            summary["primary_hard_fenced_by_orchestrator_before_promotion"]
        )
        self.assertLess(
            summary["max_database_failure_to_gateway_ready_ms"], 30000
        )
        self.assertTrue(summary["dcs_authorized_docker_stonith_claim"])
        self.assertTrue(summary["controller_stonith_claim"])
        self.assertTrue(summary["fence_agent_mutual_tls_claim"])
        self.assertTrue(summary["fence_client_identity_binding_claim"])
        self.assertTrue(
            summary["fenced_primary_rejoined_as_synchronous_standby_claim"]
        )
        self.assertTrue(summary["post_failover_redundancy_restored_claim"])
        self.assertTrue(summary["docker_automated_rejoin_claim"])
        self.assertFalse(summary["production_automatic_rejoin_claim"])
        self.assertTrue(summary["controlled_planned_failback_claim"])
        self.assertTrue(summary["original_primary_role_restored_claim"])
        self.assertTrue(
            summary["post_failback_gateway_token3_state_continuity_claim"]
        )
        self.assertTrue(summary["post_failback_synchronous_redundancy_claim"])
        self.assertTrue(summary["automatic_failback_policy_claim"])
        self.assertTrue(
            summary["unsafe_failback_preconditions_fail_closed_claim"]
        )
        self.assertTrue(summary["failback_quorum_loss_fail_closed_claim"])
        self.assertTrue(summary["single_dcs_failback_winner_claim"])
        self.assertTrue(summary["dcs_authorized_graceful_switchover_claim"])
        self.assertLess(summary["max_planned_failback_to_gateway_ready_ms"], 20000)
        self.assertTrue(
            summary[
                "docker_live_primary_partition_fenced_before_promotion_claim"
            ]
        )
        self.assertFalse(summary["network_partition_split_brain_tolerance_claim"])
        self.assertTrue(summary["automatic_failback_claim"])
        self.assertFalse(summary["production_automatic_failback_claim"])
        self.assertTrue(summary["etcd_mutual_tls_claim"])
        self.assertFalse(summary["regional_disaster_recovery_claim"])
        self.assertFalse(summary["production_readiness"])
        for run in summary["runs"]:
            self.assertTrue(case_ok(run))
            self.assertEqual(run["etcd_cluster"]["member_count"], 3)
            self.assertTrue(run["etcd_cluster"]["raft_consensus"])
            self.assertTrue(run["etcd_cluster"]["mutual_tls"])
            self.assertTrue(
                run["etcd_cluster"]["unauthenticated_client_rejected"]
            )
            self.assertEqual(
                run["quorum_loss_control"]["dcs_kill_returncodes"], [0, 0]
            )
            self.assertTrue(
                run["quorum_loss_control"][
                    "all_controllers_observed_quorum_unavailable"
                ]
            )
            self.assertTrue(
                run["unauthorized_fence_rejected_while_primary_running"]
            )
            self.assertTrue(
                run["fence_security_negative_controls"][
                    "unauthenticated_client_rejected"
                ]
            )
            self.assertTrue(
                run["fence_security_negative_controls"][
                    "authenticated_forged_lease_rejected"
                ]
            )
            self.assertTrue(run["fence_agent"]["telemetry"]["dcs_lease_authorized"])
            self.assertTrue(
                run["fence_agent"]["telemetry"]["mtls_client_authenticated"]
            )
            self.assertEqual(run["post_failover_rejoin"]["status"], "ok")
            self.assertTrue(run["post_failover_rejoin"]["target_in_recovery"])
            self.assertGreater(
                run["post_failover_rejoin"]["replay_lsn_bytes"], 0
            )
            self.assertEqual(run["planned_failback"]["status"], "ok")
            self.assertEqual(
                run["planned_failback"]["mode"],
                "automatic_policy_dcs_switchover",
            )
            self.assertEqual(
                run["planned_failback"][
                    "synchronous_replay_gap_bytes_before_stop"
                ],
                0,
            )
            self.assertEqual(
                run["failback"]["service"]["metrics"]["durable_state"][
                    "writer_lease"
                ]["fence_token"],
                3,
            )
            self.assertEqual(run["post_failback_redundancy"]["status"], "ok")
            self.assertTrue(
                run["failback_quorum_loss_control"][
                    "all_controllers_observed_quorum_unavailable"
                ]
            )
            self.assertTrue(
                run["failback_quorum_loss_control"][
                    "all_controllers_rejected_unsafe_replication"
                ]
            )
            self.assertTrue(
                run["failback_quorum_loss_control"][
                    "database_roles_unchanged_without_quorum"
                ]
            )
            self.assertTrue(
                run["switchover_agent"]["telemetry"][
                    "graceful_stop_confirmed"
                ]
            )
            self.assertTrue(failback_controllers_ok(
                run["failback_controllers"]
            ))
            winners = [
                row["telemetry"] for row in run["controllers"]
                if row["telemetry"]["status"] == "promoted"
            ]
            self.assertEqual(len(winners), 1)
            self.assertTrue(all(
                row["telemetry"]["quorum_acquisition_failures"] >= 1
                for row in run["controllers"]
            ))

    def test_dcs_and_runner_use_ttl_compare_quorum_and_automatic_promote(self) -> None:
        dcs = (ROOT / "fleetqox" / "postgres_failover_dcs.py").read_text()
        controller = (
            ROOT / "scripts" / "fleetqox_postgres_failover_controller.py"
        ).read_text()
        fence_agent = (
            ROOT / "scripts" / "fleetqox_postgres_fence_agent.py"
        ).read_text()
        failback_controller = (
            ROOT / "scripts" / "fleetqox_postgres_failback_controller.py"
        ).read_text()
        switchover_agent = (
            ROOT / "scripts" / "fleetqox_postgres_switchover_agent.py"
        ).read_text()
        runner = (
            ROOT
            / "scripts"
            / "run_rmw_docker_quic_postgres_quorum_failover_probe.py"
        ).read_text()
        self.assertIn('"target": "CREATE"', dcs)
        self.assertIn('"createRevision": "0"', dcs)
        self.assertIn('"/v3/lease/grant"', dcs)
        self.assertIn('"/v3/kv/txn"', dcs)
        self.assertIn('"/v3/kv/range"', dcs)
        self.assertIn("SELECT pg_promote(true, 10)", controller)
        self.assertIn("request_fence", controller)
        self.assertIn("hard_fence_confirmed", controller)
        self.assertIn("self.server.dcs.get", fence_agent)
        self.assertIn("/var/run/docker.sock", fence_agent)
        self.assertIn("dcs_lease_authorized", fence_agent)
        self.assertIn("ssl.CERT_REQUIRED", fence_agent)
        self.assertIn("peer_common_name != controller_id", fence_agent)
        self.assertIn("synchronous_replay_gap", failback_controller)
        self.assertIn('"status": "unsafe_preconditions"', failback_controller)
        self.assertIn("prefer-original-when-synchronous", failback_controller)
        self.assertIn("request_switchover", failback_controller)
        self.assertIn("SELECT pg_promote(true, 10)", failback_controller)
        self.assertIn("self.server.dcs.get", switchover_agent)
        self.assertIn("/var/run/docker.sock", switchover_agent)
        self.assertIn("graceful_stop_confirmed", switchover_agent)
        self.assertIn("ssl.CERT_REQUIRED", switchover_agent)
        self.assertIn("quay.io/coreos/etcd:v3.5.17", runner)
        self.assertIn('"--client-cert-auth=true"', runner)
        self.assertIn('"--peer-client-cert-auth=true"', runner)
        self.assertIn("unauthenticated_client_rejected", runner)
        self.assertIn('run(["docker", "kill", etcd_names[1]])', runner)
        self.assertIn("tc qdisc replace dev eth0 root netem loss 100%", runner)
        self.assertIn("unauthorized_fence_rejected", runner)
        self.assertIn("pg_basebackup", runner)
        self.assertIn("REJOIN_APPLICATION", runner)
        self.assertIn("start_failback_controller", runner)
        self.assertIn("start_switchover_agent", runner)
        self.assertIn("POST_FAILBACK_APPLICATION", runner)
        self.assertIn("standby_remained_in_recovery_without_quorum", runner)

    def test_capability_manifest_scopes_consensus_and_stonith(self) -> None:
        claims = json.loads(
            (
                ROOT
                / "ros2_ws"
                / "src"
                / "rmw_fleetqox_cpp"
                / "capabilities.json"
            ).read_text(encoding="utf-8")
        )["claim_boundaries"]
        self.assertTrue(
            claims["docker_quic_postgresql_quorum_failover_5run_probe"]
        )
        self.assertTrue(claims["quic_gateway_etcd_raft_failover_dcs_claim"])
        self.assertTrue(
            claims["quic_gateway_automatic_database_leader_election_claim"]
        )
        self.assertTrue(
            claims["quic_gateway_quorum_loss_promotion_fail_closed_claim"]
        )
        self.assertTrue(claims["quic_gateway_consensus_backend_claim"])
        self.assertTrue(claims["quic_gateway_controller_stonith_claim"])
        self.assertTrue(
            claims["quic_gateway_dcs_authorized_docker_stonith_claim"]
        )
        self.assertTrue(
            claims[
                "quic_gateway_live_primary_partition_fenced_before_promotion_claim"
            ]
        )
        self.assertFalse(claims["quic_gateway_hardware_stonith_claim"])
        self.assertTrue(
            claims["quic_gateway_stonith_transport_mutual_tls_claim"]
        )
        self.assertTrue(
            claims["quic_gateway_fence_client_identity_binding_claim"]
        )
        self.assertTrue(claims["quic_gateway_docker_automated_rejoin_claim"])
        self.assertTrue(
            claims[
                "quic_gateway_fenced_primary_rejoined_as_synchronous_standby_claim"
            ]
        )
        self.assertTrue(
            claims["quic_gateway_post_failover_redundancy_restored_claim"]
        )
        self.assertFalse(
            claims["quic_gateway_production_automatic_rejoin_claim"]
        )
        self.assertTrue(
            claims["quic_gateway_controlled_planned_failback_claim"]
        )
        self.assertTrue(
            claims["quic_gateway_original_primary_role_restored_claim"]
        )
        self.assertTrue(
            claims[
                "quic_gateway_post_failback_gateway_token3_state_continuity_claim"
            ]
        )
        self.assertTrue(
            claims["quic_gateway_post_failback_synchronous_redundancy_claim"]
        )
        self.assertTrue(
            claims["quic_gateway_automatic_failback_policy_claim"]
        )
        self.assertTrue(
            claims[
                "quic_gateway_unsafe_failback_preconditions_fail_closed_claim"
            ]
        )
        self.assertTrue(
            claims["quic_gateway_failback_quorum_loss_fail_closed_claim"]
        )
        self.assertTrue(
            claims["quic_gateway_single_dcs_failback_winner_claim"]
        )
        self.assertTrue(
            claims["quic_gateway_dcs_authorized_graceful_switchover_claim"]
        )
        self.assertTrue(claims["quic_gateway_etcd_mutual_tls_claim"])
        self.assertTrue(
            claims["quic_gateway_partition_split_brain_tolerance_claim"]
        )
        self.assertTrue(claims["quic_gateway_automatic_failback_claim"])
        self.assertFalse(
            claims["quic_gateway_production_automatic_failback_claim"]
        )


if __name__ == "__main__":
    unittest.main()
