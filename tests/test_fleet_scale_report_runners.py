import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import scripts.run_rmw_docker_stress_security_campaign as stress_campaign_module

from scripts.generate_unified_benchmark_report import (
    build_report as build_unified_benchmark_report,
    render_markdown as render_unified_benchmark_markdown,
)
from scripts.run_large_scale_rmw_comparison import (
    aggregate as aggregate_comparison,
    metric_summary,
    normalize_row,
    render_markdown as render_comparison_markdown,
    row_needs_infrastructure_rerun,
)
from scripts.run_rmw_docker_fleet_repair_capacity_frontier import (
    aggregate_rows as aggregate_frontier_rows,
    frontier_row,
    render_markdown as render_frontier_markdown,
    reusable_prior_row,
    RUNNER_SEMANTICS_VERSION,
)
from scripts.run_rmw_docker_router_matched_multi_topic_probe import (
    reliable_timing_for_netem,
)
from scripts.run_rmw_docker_quic_netem_frame_probe import (
    parse_netem_qdisc_counters,
    parse_ngtcp2_path_telemetry,
)
from scripts.run_rmw_docker_quic_gateway_publish_probe import (
    parse_quic_session_reuse_telemetry,
    parse_server_body_bytes,
    parse_server_body_sizes,
    parse_server_content_length,
    parse_server_content_lengths,
)
from scripts.run_rmw_docker_quic_gateway_async_burst_soak import (
    aggregate as aggregate_quic_gateway_soak,
)
from scripts.run_rmw_docker_stress_security_campaign import (
    aggregate_campaign as aggregate_stress_security_campaign,
    component_row as stress_security_component_row,
)
from scripts.run_rmw_docker_nav2_navigate_to_pose_long_moving_probe import (
    aggregate as aggregate_nav2_long_moving,
)
from scripts.run_ns3_docker_fleet_matrix import parse_csv_summary


class FleetScaleReportRunnersTest(unittest.TestCase):
    def test_ns3_summary_parser_preserves_policy_metrics(self) -> None:
        rows = parse_csv_summary(
            "noise\n"
            "policy,tx,rx,bytes,deadline_miss_ratio,p50_ms,p99_ms,utility\n"
            "fifo,10,9,900,0.1,2.0,7.0,4.5\n"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["policy"], "fifo")
        self.assertEqual(rows[0]["tx"], 10)
        self.assertEqual(rows[0]["rx"], 9)
        self.assertEqual(rows[0]["p99_ms"], 7.0)

    def test_reliable_timing_uses_profile_rtt_and_retry_horizon(self) -> None:
        timeout_ms, linger_s = reliable_timing_for_netem(
            {"delay_ms": 58.0, "jitter_ms": 22.0},
            configured_ack_timeout_ms=None,
            max_retransmissions=3,
        )

        self.assertEqual(timeout_ms, 254)
        self.assertEqual(linger_s, 6.0)

        disabled_timeout, disabled_linger = reliable_timing_for_netem(
            {"delay_ms": 58.0, "jitter_ms": 22.0},
            configured_ack_timeout_ms=0,
            max_retransmissions=3,
        )
        self.assertEqual(disabled_timeout, 0)
        self.assertEqual(disabled_linger, 0.5)

    def test_quic_netem_telemetry_parser_tracks_ngtcp2_path_state(self) -> None:
        telemetry = parse_ngtcp2_path_telemetry(
            "Sent packet: local=[172.18.0.3]:35048 remote=[172.18.0.2]:4445 "
            "ecn=0x2 1200 bytes\n"
            "Received packet: local=[172.18.0.3]:35048 remote=[172.18.0.2]:4445 "
            "ecn=0x2 1200 bytes\n"
            "I00000049 conn pkt tx pkn=1 type=1RTT len=64\n"
            "I00000049 conn pkt rx pkn=0 type=Initial len=119\n"
            "I00000049 conn rcv latest_rtt=49 min_rtt=49 smoothed_rtt=49 "
            "rttvar=24 ack_delay=0\n"
            "I00000049 conn rcv pkn=0 acked, slow start cwnd=15720\n"
            "I00000049 conn con path is ECN capable\n"
            "I00000049 conn con the negotiated version is 0x00000001\n",
            "I00000072 conn rcv latest_rtt=53 min_rtt=20 smoothed_rtt=50 "
            "rttvar=12 target_cwnd=226120 max_delivery_rate_sec=1048576\n",
        )

        self.assertTrue(telemetry["quic_v1_negotiated_observed"])
        self.assertTrue(telemetry["ecn_capable_observed"])
        self.assertEqual(telemetry["sent_packet_log_count"], 1)
        self.assertEqual(telemetry["received_packet_log_count"], 1)
        self.assertEqual(telemetry["sent_packet_bytes_logged"], 1200)
        self.assertEqual(telemetry["packet_tx_log_count"], 1)
        self.assertEqual(telemetry["packet_rx_log_count"], 1)
        self.assertEqual(telemetry["rtt_raw"]["latest"]["sample_count"], 2)
        self.assertEqual(telemetry["rtt_raw"]["latest"]["max"], 53)
        self.assertEqual(telemetry["rtt_raw"]["min"]["min"], 20)
        self.assertEqual(telemetry["congestion_raw"]["cwnd_bytes"]["last"], 15720)
        self.assertEqual(
            telemetry["congestion_raw"]["target_cwnd_bytes"]["last"],
            226120,
        )
        self.assertEqual(
            telemetry["congestion_raw"]["max_delivery_rate_per_s"]["last"],
            1048576,
        )

    def test_netem_qdisc_counter_parser_reads_before_after_snapshots(self) -> None:
        counters = parse_netem_qdisc_counters(
            "qdisc netem 8002: root refcnt 13 limit 1000 delay 20ms  5ms\n"
            " Sent 8400 bytes 7 pkt (dropped 1, overlimits 2 requeues 3)\n"
            " backlog 120b 1p requeues 4\n"
        )

        self.assertEqual(counters["sent_bytes"], 8400)
        self.assertEqual(counters["sent_packets"], 7)
        self.assertEqual(counters["dropped_packets"], 1)
        self.assertEqual(counters["overlimits"], 2)
        self.assertEqual(counters["requeues"], 3)
        self.assertEqual(counters["backlog_bytes"], 120)
        self.assertEqual(counters["backlog_packets"], 1)
        self.assertEqual(counters["backlog_requeues"], 4)

    def test_quic_gateway_server_log_parser_tracks_uploaded_body(self) -> None:
        server_log = (
            "http: stream 0x0 [content-length: 536]\n"
            "http: stream 0x0 body 536 bytes\n"
            "http: stream 0x4 [content-length: 17]\n"
            "http: stream 0x4 body 17 bytes\n"
        )

        self.assertEqual(parse_server_content_length(server_log), 536)
        self.assertEqual(parse_server_body_bytes(server_log), 536)
        self.assertEqual(parse_server_content_lengths(server_log), [536, 17])
        self.assertEqual(parse_server_body_sizes(server_log), [536, 17])

    def test_quic_gateway_session_reuse_parser_keeps_0rtt_claim_conservative(self) -> None:
        telemetry = parse_quic_session_reuse_telemetry(
            "Reading token file /tmp/token.bin\n"
            "Could not read token file /tmp/token.bin\n"
            "Reading session file /tmp/session.bin\n"
            "I00000001 conn pkt tx pkn=0 type=0RTT len=0\n"
            "I00000001 conn frm tx 0 0RTT STREAM(0x0a) id=0x2\n"
        )

        self.assertEqual(telemetry["token_file_read_count"], 1)
        self.assertEqual(telemetry["token_file_missing_count"], 1)
        self.assertEqual(telemetry["session_file_read_count"], 1)
        self.assertTrue(telemetry["session_resumption_attempted_observed"])
        self.assertTrue(telemetry["zero_rtt_packet_observed"])
        self.assertEqual(telemetry["zero_rtt_tx_packet_count"], 1)
        self.assertEqual(telemetry["zero_rtt_tx_frame_count"], 1)
        self.assertFalse(telemetry["zero_rtt_accepted_observed"])
        self.assertFalse(telemetry["session_resumption_observed"])

        accepted = parse_quic_session_reuse_telemetry(
            "TLS session resumed\n"
            "early data accepted\n"
        )
        self.assertTrue(accepted["session_resumption_observed"])
        self.assertTrue(accepted["zero_rtt_accepted_observed"])

    def test_quic_gateway_async_burst_soak_aggregate_tracks_totals(self) -> None:
        rows = [
            {
                "status": "ok",
                "probe": {
                    "quic_gateway_frames_sent": "2",
                    "quic_gateway_frames_enqueued": "2",
                    "quic_gateway_bytes_sent": "100",
                    "quic_gateway_frames_failed": "0",
                    "quic_gateway_frames_dropped": "0",
                },
                "server_body_total_bytes": 100,
                "qlog_total_bytes": 40,
                "netem": {"counters_after": {"sent_packets": 5}},
                "path_telemetry": {
                    "rtt_raw": {"latest": {"sample_count": 1}},
                },
            },
            {
                "status": "ok",
                "probe": {
                    "quic_gateway_frames_sent": 3,
                    "quic_gateway_frames_enqueued": 3,
                    "quic_gateway_bytes_sent": 150,
                    "quic_gateway_frames_failed": 0,
                    "quic_gateway_frames_dropped": 0,
                },
                "server_body_total_bytes": 150,
                "qlog_total_bytes": 70,
                "netem": {"counters_after": {"sent_packets": 7}},
                "path_telemetry": {
                    "rtt_raw": {"latest": {"sample_count": 2}},
                },
            },
        ]

        summary = aggregate_quic_gateway_soak(rows, netem=True)

        self.assertEqual(
            summary["schema_version"],
            "fleetrmw.docker_quic_gateway_async_burst_soak.v1",
        )
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["ok_run_count"], 2)
        self.assertEqual(summary["total_quic_gateway_frames_sent"], 5)
        self.assertEqual(summary["total_quic_gateway_frames_enqueued"], 5)
        self.assertEqual(summary["total_quic_gateway_bytes_sent"], 250)
        self.assertEqual(summary["total_server_body_bytes"], 250)
        self.assertEqual(summary["total_quic_gateway_frames_dropped"], 0)
        self.assertEqual(summary["qlog_total_bytes"], 110)
        self.assertEqual(summary["netem_sent_packets"], 12)
        self.assertEqual(summary["rtt_sample_count"], 3)
        self.assertTrue(summary["server_payload_bytes_match"])
        self.assertFalse(summary["production_quic_backend"])
        self.assertFalse(summary["full_bidirectional_quic_backend"])

    def test_stress_security_campaign_aggregate_keeps_long_claim_gated(self) -> None:
        components = [
            stress_security_component_row(
                "security_options",
                {
                    "schema_version": "fleetrmw.docker_security_options_probe.v1",
                    "status": "ok",
                    "run_count": 5,
                    "ok_run_count": 5,
                    "security_policy_enforcement_executed": False,
                    "security_hardening_blocker": (
                        "full_sros2_policy_enforcement_not_implemented"
                    ),
                },
            ),
            stress_security_component_row(
                "security_policy",
                {
                    "schema_version": "fleetrmw.docker_security_policy_probe.v1",
                    "status": "ok",
                    "run_count": 5,
                    "ok_run_count": 5,
                    "fleetqox_security_policy_enforcement_claim": True,
                    "security_policy_repeated_enforcement_claim": True,
                    "sros2_policy_enforcement_claim": False,
                },
            ),
            stress_security_component_row(
                "sros2_permissions",
                {
                    "schema_version": "fleetrmw.docker_sros2_permissions_probe.v1",
                    "status": "ok",
                    "run_count": 5,
                    "ok_run_count": 5,
                    "security_policy_enforcement_executed": True,
                    "security_policy_enforcement_gap_reason": (
                        "peer_auth_transport_revocation_hardening_not_implemented"
                    ),
                    "sros2_cli_generated_artifacts": True,
                    "signed_permissions_verified_preflight": True,
                    "permissions_xsd_validated": True,
                    "sros2_permissions_xml_publish_enforcement_claim": True,
                    "sros2_permissions_xml_subscribe_enforcement_claim": True,
                    "sros2_permissions_xml_pubsub_enforcement_claim": True,
                    "sros2_permissions_xml_repeated_enforcement_claim": True,
                    "sros2_permissions_xml_subscribe_repeated_enforcement_claim": True,
                    "malformed_permissions_fail_closed_claim": True,
                    "runtime_sros2_permissions_signature_validation_claim": True,
                    "tampered_signed_permissions_fail_closed_claim": True,
                    "sros2_service_request_reply_authorization_claim": True,
                    "sros2_service_repeated_authorization_claim": True,
                    "sros2_action_authorization_claim": True,
                    "sros2_action_repeated_authorization_claim": True,
                    "sros2_action_allowed_end_to_end_claim": True,
                    "sros2_action_call_denied_fail_closed_claim": True,
                    "sros2_action_execute_denied_fail_closed_claim": True,
                    "sros2_action_call_execute_decision_matrix_claim": True,
                    "governance_xml_enforcement_claim": True,
                    "sros2_governance_access_control_claim": True,
                    "sros2_governance_repeated_access_control_claim": True,
                    "sros2_governance_runtime_signature_validation_claim": True,
                    "sros2_governance_transport_protection_fail_closed_claim": True,
                    "sros2_tampered_signed_governance_fail_closed_claim": True,
                    "sros2_local_identity_credentials_validation_claim": True,
                    "sros2_local_identity_credentials_repeated_validation_claim": True,
                    "sros2_tampered_identity_certificate_fail_closed_claim": True,
                    "sros2_identity_private_key_mismatch_fail_closed_claim": True,
                    "sros2_identity_enclave_mismatch_fail_closed_claim": True,
                    "sros2_policy_enforcement_claim": False,
                },
            ),
            stress_security_component_row(
                "udp_aead",
                {
                    "schema_version": "fleetrmw.docker_udp_aead_probe.v1",
                    "status": "ok",
                    "run_count": 5,
                    "ok_run_count": 5,
                    "udp_aead_authenticated_encryption_claim": True,
                    "udp_aead_repeated_authenticated_encryption_claim": True,
                    "udp_aead_tamper_fail_closed_claim": True,
                    "udp_aead_strict_missing_key_fail_closed_claim": True,
                    "udp_authenticated_psk_session_key_derivation_claim": True,
                    "udp_session_key_rotation_claim": True,
                    "session_key_establishment_claim": True,
                    "dds_security_interoperability_claim": False,
                },
            ),
            stress_security_component_row(
                "udp_peer_auth",
                {
                    "schema_version": "fleetrmw.docker_udp_peer_auth_probe.v1",
                    "status": "ok",
                    "run_count": 5,
                    "ok_run_count": 5,
                    "sros2_peer_identity_authentication_claim": True,
                    "sros2_peer_identity_repeated_authentication_claim": True,
                    "udp_peer_identity_allowlist_fail_closed_claim": True,
                    "udp_peer_signature_tamper_fail_closed_claim": True,
                    "udp_peer_untrusted_certificate_fail_closed_claim": True,
                    "udp_certificate_authenticated_aead_claim": True,
                    "session_key_establishment_claim": False,
                    "certificate_revocation_claim": True,
                },
            ),
            stress_security_component_row(
                "dynamic_message",
                {
                    "schema_version": "fleetrmw.docker_dynamic_message_probe.v1",
                    "status": "ok",
                    "run_count": 5,
                    "ok_run_count": 5,
                    "dynamic_serialization_support_claim": True,
                    "dynamic_serialization_support_repeated_claim": True,
                    "dynamic_message_take_claim": True,
                    "dynamic_message_take_with_info_claim": True,
                    "message_info_sequence_features_claim": True,
                },
            ),
            stress_security_component_row(
                "allocation",
                {
                    "schema_version": "fleetrmw.docker_allocation_probe.v1",
                    "status": "ok",
                    "run_count": 5,
                    "ok_run_count": 5,
                },
            ),
            stress_security_component_row(
                "qos_event",
                {
                    "schema_version": "fleetrmw.docker_qos_event_probe.v1",
                    "status": "ok",
                    "run_count": 5,
                    "ok_run_count": 5,
                },
            ),
            stress_security_component_row(
                "content_filter",
                {
                    "schema_version": "fleetrmw.docker_content_filter_probe.v1",
                    "status": "ok",
                    "run_count": 5,
                    "ok_run_count": 5,
                },
            ),
            stress_security_component_row(
                "quic_gateway_async_burst_soak",
                {
                    "schema_version": (
                        "fleetrmw.docker_quic_gateway_async_burst_soak.v1"
                    ),
                    "status": "ok",
                    "run_count": 3,
                    "ok_run_count": 3,
                    "total_quic_gateway_frames_sent": 12,
                    "total_quic_gateway_frames_failed": 0,
                    "total_quic_gateway_frames_dropped": 0,
                    "total_quic_gateway_bytes_sent": 1200,
                    "qlog_total_bytes": 800,
                },
            ),
        ]

        summary = aggregate_stress_security_campaign(
            components,
            profile="repeated",
            elapsed_s=120.0,
            long_min_runtime_s=3600.0,
            quic_netem=True,
        )

        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["component_count"], 10)
        self.assertEqual(summary["total_component_runs"], 48)
        self.assertTrue(summary["stress_security_smoke_claim"])
        self.assertTrue(summary["stress_security_repeated_claim"])
        self.assertFalse(summary["long_stress_security_campaign_claim"])
        self.assertTrue(summary["fleetqox_security_policy_enforcement_claim"])
        self.assertTrue(summary["security_policy_repeated_enforcement_claim"])
        self.assertTrue(summary["security_policy_enforcement_executed"])
        self.assertTrue(summary["sros2_cli_generated_artifacts"])
        self.assertTrue(summary["signed_permissions_verified_preflight"])
        self.assertTrue(summary["permissions_xsd_validated"])
        self.assertTrue(summary["sros2_permissions_xml_publish_enforcement_claim"])
        self.assertTrue(summary["sros2_permissions_xml_subscribe_enforcement_claim"])
        self.assertTrue(summary["sros2_permissions_xml_pubsub_enforcement_claim"])
        self.assertTrue(summary["sros2_permissions_xml_repeated_enforcement_claim"])
        self.assertTrue(
            summary["sros2_permissions_xml_subscribe_repeated_enforcement_claim"]
        )
        self.assertTrue(summary["malformed_permissions_fail_closed_claim"])
        self.assertTrue(summary["runtime_sros2_permissions_signature_validation_claim"])
        self.assertTrue(summary["tampered_signed_permissions_fail_closed_claim"])
        self.assertTrue(summary["sros2_service_request_reply_authorization_claim"])
        self.assertTrue(summary["sros2_service_repeated_authorization_claim"])
        self.assertTrue(summary["sros2_action_authorization_claim"])
        self.assertTrue(summary["sros2_action_repeated_authorization_claim"])
        self.assertTrue(summary["sros2_action_allowed_end_to_end_claim"])
        self.assertTrue(summary["sros2_action_call_denied_fail_closed_claim"])
        self.assertTrue(summary["sros2_action_execute_denied_fail_closed_claim"])
        self.assertTrue(summary["sros2_action_call_execute_decision_matrix_claim"])
        self.assertTrue(summary["governance_xml_enforcement_claim"])
        self.assertTrue(summary["sros2_governance_access_control_claim"])
        self.assertTrue(summary["sros2_governance_repeated_access_control_claim"])
        self.assertTrue(
            summary["sros2_governance_transport_protection_fail_closed_claim"]
        )
        self.assertFalse(summary["governance_transport_security_claim"])
        self.assertTrue(summary["sros2_local_identity_credentials_validation_claim"])
        self.assertTrue(
            summary["sros2_local_identity_credentials_repeated_validation_claim"]
        )
        self.assertTrue(
            summary["sros2_identity_enclave_mismatch_fail_closed_claim"]
        )
        self.assertTrue(summary["sros2_peer_identity_authentication_claim"])
        self.assertTrue(summary["sros2_peer_identity_repeated_authentication_claim"])
        self.assertTrue(summary["udp_peer_identity_allowlist_fail_closed_claim"])
        self.assertTrue(summary["udp_peer_signature_tamper_fail_closed_claim"])
        self.assertTrue(summary["udp_peer_untrusted_certificate_fail_closed_claim"])
        self.assertTrue(summary["udp_certificate_authenticated_aead_claim"])
        self.assertTrue(summary["udp_authenticated_psk_session_key_derivation_claim"])
        self.assertTrue(summary["udp_session_key_rotation_claim"])
        self.assertTrue(summary["session_key_establishment_claim"])
        self.assertFalse(summary["forward_secrecy_claim"])
        self.assertFalse(summary["asymmetric_session_key_exchange_claim"])
        self.assertTrue(summary["dynamic_serialization_support_claim"])
        self.assertTrue(summary["dynamic_serialization_support_repeated_claim"])
        self.assertTrue(summary["dynamic_message_take_claim"])
        self.assertTrue(summary["dynamic_message_take_with_info_claim"])
        self.assertTrue(summary["message_info_sequence_features_claim"])
        self.assertTrue(summary["certificate_revocation_claim"])
        self.assertTrue(summary["udp_aead_authenticated_encryption_claim"])
        self.assertTrue(summary["udp_aead_repeated_authenticated_encryption_claim"])
        self.assertTrue(summary["udp_aead_tamper_fail_closed_claim"])
        self.assertTrue(summary["udp_aead_strict_missing_key_fail_closed_claim"])
        self.assertFalse(summary["dds_security_interoperability_claim"])
        self.assertFalse(summary["sros2_policy_enforcement_claim"])
        self.assertEqual(summary["quic_soak_total_frames_sent"], 12)
        self.assertEqual(
            summary["security_hardening_blocker"],
            "forward_secret_key_exchange_and_transport_hardening_not_implemented",
        )

    def test_long_stress_campaign_repeats_active_rounds_until_threshold(self) -> None:
        component_summary = {
            "schema_version": "fleetrmw.docker_allocation_probe.v1",
            "status": "ok",
            "run_count": 40,
            "ok_run_count": 40,
        }
        with mock.patch.object(
            stress_campaign_module,
            "run_allocation_probe",
            return_value=component_summary,
        ) as allocation_probe, mock.patch.object(
            stress_campaign_module.time,
            "monotonic",
            side_effect=[0.0, 10.0, 20.0, 20.0],
        ):
            summary = stress_campaign_module.run_campaign(
                root=Path("."),
                image="unused",
                profile="long",
                components=["allocation"],
                abi_iterations=40,
                security_iterations=1,
                quic_iterations=1,
                base_port=4900,
                quic_netem=False,
                delay_ms=0,
                jitter_ms=0,
                loss_percent=0.0,
                long_min_runtime_s=15.0,
            )
        self.assertEqual(allocation_probe.call_count, 2)
        self.assertEqual(summary["campaign_round_count"], 2)
        self.assertTrue(summary["active_soak_until_runtime_threshold"])
        self.assertEqual(summary["total_component_runs"], 80)
        self.assertTrue(summary["long_stress_security_campaign_claim"])

    def test_nav2_long_moving_aggregate_requires_repeated_distance(self) -> None:
        rows = [
            {
                "status": "ok",
                "navigate_to_pose_goal_succeeded": True,
                "cmd_vel_topic_forwarded": True,
                "extended_moving_navigation_claim": True,
                "fake_base_cmd_vel_count": 6,
                "fake_base_moved_distance": 0.95,
                "navigation_goal_x": 1.2,
                "fleetqox_router_service_frames": 54,
                "fleetqox_router_forwarded_frames": 100,
                "fleetqox_router_received_frames": 100,
            },
            {
                "status": "ok",
                "navigate_to_pose_goal_succeeded": True,
                "cmd_vel_topic_forwarded": True,
                "extended_moving_navigation_claim": True,
                "fake_base_cmd_vel_count": 7,
                "fake_base_moved_distance": 0.98,
                "navigation_goal_x": 1.2,
                "fleetqox_router_service_frames": 55,
                "fleetqox_router_forwarded_frames": 101,
                "fleetqox_router_received_frames": 101,
            },
            {
                "status": "ok",
                "navigate_to_pose_goal_succeeded": True,
                "cmd_vel_topic_forwarded": True,
                "extended_moving_navigation_claim": True,
                "fake_base_cmd_vel_count": 8,
                "fake_base_moved_distance": 1.01,
                "navigation_goal_x": 1.2,
                "fleetqox_router_service_frames": 56,
                "fleetqox_router_forwarded_frames": 102,
                "fleetqox_router_received_frames": 102,
            },
        ]

        summary = aggregate_nav2_long_moving(
            rows,
            min_iterations=3,
            min_total_moved_distance=2.4,
            min_total_cmd_vel_count=18,
        )

        self.assertEqual(
            summary["schema_version"],
            "fleetrmw.docker_nav2_navigate_to_pose_long_moving_probe.v1",
        )
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["ok_run_count"], 3)
        self.assertEqual(summary["navigate_to_pose_goal_succeeded_run_count"], 3)
        self.assertEqual(summary["extended_moving_navigation_run_count"], 3)
        self.assertTrue(summary["long_navigation_workload_claim"])
        self.assertFalse(summary["obstacle_field_recovery_claim"])
        self.assertEqual(summary["total_fake_base_cmd_vel_count"], 21)
        self.assertAlmostEqual(summary["total_fake_base_moved_distance"], 2.94)
        self.assertEqual(summary["total_fleetqox_router_service_frames"], 165)

        failed = aggregate_nav2_long_moving(
            rows[:2],
            min_iterations=3,
            min_total_moved_distance=2.4,
            min_total_cmd_vel_count=18,
        )
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["long_navigation_workload_claim"])

    def test_unified_benchmark_report_preserves_claim_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capabilities = root / "capabilities.json"
            capabilities.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_capabilities.v1",
                        "implementation": "rmw_fleetqox_cpp",
                        "production_ready": False,
                        "serialization_format": "fleetrmw.introspection_c.v1",
                        "supported": {
                            "docker_quic_gateway_async_burst_soak_runner": True,
                            "docker_quic_gateway_async_burst_soak_3run_netem": True,
                            "docker_quic_gateway_async_burst_soak_10run_netem": True,
                            "docker_stress_security_campaign_runner": True,
                            "fleetqox_security_policy_enforcement": True,
                            "fleetqox_security_policy_repeated_enforcement_5run": True,
                            "sros2_generated_permissions_xml_publish_enforcement": True,
                            "sros2_generated_permissions_xml_publish_enforcement_5run": True,
                            "sros2_generated_permissions_xml_subscribe_enforcement": True,
                            "sros2_generated_permissions_xml_subscribe_enforcement_5run": True,
                            "sros2_generated_permissions_xml_pubsub_enforcement": True,
                            "sros2_signed_permissions_preflight": True,
                            "sros2_runtime_permissions_signature_ca_validation": True,
                            "sros2_permissions_malformed_fail_closed": True,
                            "sros2_tampered_signed_permissions_fail_closed": True,
                            "sros2_generated_permissions_service_request_reply_authorization": True,
                            "sros2_generated_permissions_service_request_reply_authorization_5run": True,
                            "sros2_generated_permissions_action_call_execute_authorization": True,
                            "sros2_generated_permissions_action_call_execute_authorization_5run": True,
                            "sros2_signed_governance_access_control": True,
                            "sros2_signed_governance_access_control_5run": True,
                            "sros2_governance_transport_protection_fail_closed": True,
                            "sros2_tampered_signed_governance_fail_closed": True,
                            "sros2_local_identity_certificate_key_ca_validation": True,
                            "sros2_local_identity_certificate_key_ca_validation_5run": True,
                            "sros2_identity_negative_controls_fail_closed": True,
                            "udp_payload_aes_256_gcm_psk": True,
                            "udp_payload_aes_256_gcm_psk_5run": True,
                            "udp_payload_aead_tamper_fail_closed": True,
                            "dynamic_serialization_support_plugin_loader": True,
                            "dynamic_message_take": True,
                            "dynamic_message_take_with_info": True,
                            "message_info_publication_reception_sequence_features": True,
                            "omnetpp_template_input_generation": True,
                        },
                        "partial": {"transport": "publish-side QUIC gateway"},
                        "unsupported": [],
                        "claim_boundaries": {
                            "production_quic_backend_claim": False,
                            "full_bidirectional_quic_backend_claim": False,
                            "docker_quic_gateway_async_burst_soak_smoke": True,
                            "docker_quic_gateway_async_burst_soak_3run_netem": True,
                            "docker_quic_gateway_async_burst_soak_10run_netem": True,
                            "docker_stress_security_campaign_smoke": True,
                            "stress_security_repeated_claim": True,
                            "long_stress_security_campaign_claim": True,
                            "docker_ngtcp2_quic_gateway_take_path_probe": True,
                            "docker_ngtcp2_quic_gateway_rmw_take_path_probe": True,
                            "docker_ngtcp2_quic_gateway_rmw_take_session_reuse_file_probe": True,
                            "docker_ngtcp2_quic_gateway_rmw_take_session_reuse_5download_probe": True,
                            "docker_ngtcp2_quic_gateway_bidirectional_publish_take_probe": True,
                            "docker_ngtcp2_quic_gateway_bidirectional_publish_take_5run_probe": True,
                            "docker_quic_gateway_disable_early_data_control": True,
                            "docker_nav2_planner_controller_lifecycle_configure": True,
                            "docker_nav2_planner_controller_lifecycle_activate_dynamic_tf": True,
                            "docker_nav2_planner_compute_path_action_map_tf": True,
                            "docker_nav2_controller_follow_path_action_map_tf_odom": True,
                            "docker_nav2_navigate_to_pose_same_pose_bt_pipeline": True,
                            "docker_nav2_navigate_to_pose_repeated_same_pose_bt_pipeline": True,
                            "docker_nav2_navigate_to_pose_moving_base_bt_pipeline": True,
                            "docker_nav2_navigate_to_pose_extended_moving_base_bt_pipeline": True,
                            "docker_nav2_behavior_server_spin_action": True,
                            "docker_nav2_navigate_to_pose_recovery_tree_fallback": True,
                            "docker_nav2_navigate_to_pose_recovered_success_after_spin": True,
                            "docker_nav2_navigate_to_pose_recovered_success_repeated_smoke": True,
                            "docker_nav2_navigate_to_pose_long_moving_workload": True,
                            "docker_nav2_rmf_upstream_concurrency8": True,
                            "docker_nav2_rmf_upstream_concurrency16": True,
                            "docker_nav2_rmf_upstream_concurrency32": True,
                            "docker_nav2_rmf_upstream_concurrency64": True,
                            "docker_nav2_rmf_upstream_concurrency128": True,
                            "docker_nav2_rmf_upstream_concurrency256": True,
                            "docker_nav2_rmf_upstream_concurrency512": True,
                            "docker_nav2_rmf_upstream_concurrency1024": True,
                            "docker_nav2_rmf_upstream_concurrency2048": True,
                            "docker_nav2_rmf_upstream_concurrency4096": True,
                            "docker_nav2_rmf_upstream_total4096_admission_window8": True,
                            "nav2_rmf_unwindowed_4096_claim": True,
                            "full_nav2_navigation_stack_claim": True,
                            "nav2_rmf_larger_upstream_client_count_claim": True,
                            "nav2_rmf_total4096_admission_window_claim": True,
                            "moving_robot_navigation_claim": True,
                            "extended_moving_navigation_claim": True,
                            "nav2_recovery_behavior_claim": True,
                            "navigate_to_pose_recovery_tree_claim": True,
                            "successful_recovered_navigation_claim": True,
                            "repeated_recovered_navigation_claim": True,
                            "docker_nav2_planner_static_obstacle_repair": True,
                            "docker_nav2_navigate_to_pose_obstacle_retry_after_clear": True,
                            "docker_nav2_navigate_to_pose_autonomous_same_goal_obstacle_recovery": True,
                            "planner_static_obstacle_repair_claim": True,
                            "nav2_obstacle_retry_after_clear_claim": True,
                            "obstacle_field_recovery_claim": True,
                            "full_nav2_obstacle_recovery_claim": True,
                            "autonomous_same_goal_nav2_obstacle_recovery_claim": True,
                            "long_navigation_workload_claim": True,
                            "docker_qos_matched_event_production": True,
                            "docker_qos_matched_event_5run_probe": True,
                            "qos_matched_event_production_claim": True,
                            "docker_remote_graph_matched_qos_type_liveliness_event_5run_probe": True,
                            "remote_graph_matched_event_claim": True,
                            "remote_graph_incompatible_qos_event_claim": True,
                            "remote_graph_incompatible_type_event_claim": True,
                            "remote_graph_liveliness_lifecycle_event_claim": True,
                            "remote_graph_renewal_deduplication_claim": True,
                            "full_remote_graph_event_production_claim": False,
                            "docker_qos_reliability_incompatible_event_production": True,
                            "qos_reliability_incompatible_event_claim": True,
                            "docker_qos_type_incompatible_event_production": True,
                            "docker_qos_type_incompatible_event_5run_probe": True,
                            "qos_type_incompatible_event_claim": True,
                            "docker_qos_durability_incompatible_event_production": True,
                            "qos_durability_incompatible_event_claim": True,
                            "docker_qos_reliability_durability_incompatible_event_5run_probe": True,
                            "docker_qos_deadline_incompatible_event_production": True,
                            "docker_qos_deadline_incompatible_event_5run_probe": True,
                            "qos_deadline_incompatible_event_claim": True,
                            "qos_missing_offered_deadline_incompatible_event_claim": True,
                            "qos_missing_offered_deadline_incompatible_repeated_claim": True,
                            "missing_offered_deadline_offered_event_claim": True,
                            "missing_offered_deadline_requested_event_claim": True,
                            "remote_offered_deadline_missed_event_claim": True,
                            "remote_requested_deadline_missed_event_claim": True,
                            "remote_deadline_missed_event_repeated_claim": True,
                            "docker_qos_message_lost_event_production": True,
                            "docker_qos_message_lost_event_5run_probe": True,
                            "qos_message_lost_event_claim": True,
                            "qos_message_lost_best_effort_sequence_gap_claim": True,
                            "qos_message_lost_repair_reorder_suppression_claim": True,
                            "qos_message_lost_reliable_history_exhaustion_claim": True,
                            "unrecoverable_loss_notice_subscriber_identity_claim": True,
                            "remote_unrecoverable_loss_notice_claim": True,
                            "remote_message_lost_waitable_claim": True,
                            "duplicate_unrecoverable_loss_notice_deduplication_claim": True,
                            "repeated_remote_message_lost_claim": True,
                            "repair_budget_terminal_loss_notice_claim": True,
                            "repair_attempt_limit_terminal_loss_notice_claim": True,
                            "repair_admission_terminal_loss_notice_claim": True,
                            "terminal_repair_duplicate_notice_deduplication_claim": True,
                            "terminal_repair_clean_teardown_claim": True,
                            "terminal_repair_controls_repeated_claim": True,
                            "qos_best_effort_no_repair_request_claim": True,
                            "docker_qos_liveliness_event_production": True,
                            "docker_qos_liveliness_event_5run_probe": True,
                            "qos_liveliness_event_claim": True,
                            "automatic_liveliness_idle_renewal_claim": True,
                            "automatic_liveliness_false_loss_suppression_claim": True,
                            "automatic_liveliness_repeated_claim": True,
                            "remote_manual_liveliness_idle_timeout_claim": True,
                            "remote_manual_liveliness_explicit_assert_claim": True,
                            "remote_manual_liveliness_publish_assert_claim": True,
                            "remote_publisher_liveliness_lost_event_claim": True,
                            "remote_manual_liveliness_graph_lease_independence_claim": True,
                            "remote_manual_liveliness_repeated_claim": True,
                            "remote_liveliness_multi_endpoint_independence_claim": True,
                            "remote_liveliness_alive_not_alive_remove_claim": True,
                            "remote_liveliness_endpoint_churn_recreate_claim": True,
                            "remote_liveliness_expiry_preserves_matching_claim": True,
                            "remote_liveliness_multi_endpoint_repeated_claim": True,
                            "liveliness_manual_multi_endpoint_scale_claim": True,
                            "liveliness_system_default_automatic_renewal_claim": True,
                            "liveliness_scale_repeated_claim": True,
                            "remote_liveliness_64_endpoint_scale_claim": True,
                            "remote_liveliness_exact_aggregate_transition_claim": True,
                            "remote_liveliness_scale_repeated_claim": True,
                            "liveliness_default_lease_lifecycle_claim": True,
                            "liveliness_unresolved_policy_fail_closed_claim": True,
                            "liveliness_default_lease_repeated_claim": True,
                            "system_default_infinite_lease_lifecycle_claim": True,
                            "automatic_infinite_lease_lifecycle_claim": True,
                            "manual_infinite_lease_lifecycle_claim": True,
                            "best_available_infinite_lease_lifecycle_claim": True,
                            "unknown_liveliness_fail_closed_claim": True,
                            "manual_by_node_infinite_lease_lifecycle_claim": True,
                            "qos_liveliness_incompatible_event_production_claim": True,
                            "qos_liveliness_incompatible_event_repeated_claim": True,
                            "liveliness_kind_offered_event_claim": True,
                            "liveliness_kind_requested_event_claim": True,
                            "liveliness_slow_lease_offered_event_claim": True,
                            "liveliness_slow_lease_requested_event_claim": True,
                            "liveliness_missing_lease_offered_event_claim": True,
                            "liveliness_missing_lease_requested_event_claim": True,
                            "liveliness_compatible_control_claim": True,
                            "qos_best_available_endpoint_adaptation_claim": True,
                            "qos_best_available_endpoint_adaptation_repeated_claim": True,
                            "best_publisher_manual_selection_claim": True,
                            "best_subscription_automatic_selection_claim": True,
                            "zero_endpoint_best_available_defaults_claim": True,
                            "mixed_publishers_automatic_max_lease_claim": True,
                            "best_available_policy_frozen_after_create_claim": True,
                            "docker_publisher_subscription_payload_scratch_allocation": True,
                            "docker_publisher_subscription_payload_scratch_allocation_5run_probe": True,
                            "publisher_subscription_payload_scratch_reuse_claim": True,
                            "docker_qos_event_deadline_waitable_5run_probe": True,
                            "docker_qos_event_waitability_matrix_5run_probe": True,
                            "qos_event_waitability_matrix_claim": True,
                            "qos_event_waitability_repeated_claim": True,
                            "full_qos_event_waitable_readiness_claim": True,
                            "docker_content_filter_std_msgs_string_text_enforcement": True,
                            "docker_content_filter_dynamic_reconfigure_disable": True,
                            "docker_content_filter_repeated_enforcement_5run_probe": True,
                            "docker_security_options_lifecycle_probe": True,
                            "docker_security_options_lifecycle_5run_probe": True,
                            "security_options_lifecycle_claim": True,
                            "docker_fleetqox_security_policy_enforcement_probe": True,
                            "fleetqox_security_policy_enforcement_claim": True,
                            "security_policy_repeated_enforcement_claim": True,
                            "docker_sros2_permissions_xml_publish_enforcement_probe": True,
                            "sros2_permissions_xml_publish_enforcement_claim": True,
                            "sros2_permissions_xml_subscribe_enforcement_claim": True,
                            "sros2_permissions_xml_pubsub_enforcement_claim": True,
                            "sros2_permissions_xml_repeated_enforcement_claim": True,
                            "sros2_permissions_xml_subscribe_repeated_enforcement_claim": True,
                            "malformed_permissions_fail_closed_claim": True,
                            "runtime_sros2_permissions_signature_validation_claim": True,
                            "tampered_signed_permissions_fail_closed_claim": True,
                            "sros2_service_request_reply_authorization_claim": True,
                            "sros2_service_repeated_authorization_claim": True,
                            "sros2_action_authorization_claim": True,
                            "sros2_action_repeated_authorization_claim": True,
                            "sros2_action_allowed_end_to_end_claim": True,
                            "sros2_action_call_denied_fail_closed_claim": True,
                            "sros2_action_execute_denied_fail_closed_claim": True,
                            "sros2_action_call_execute_decision_matrix_claim": True,
                            "sros2_action_authorization_metrics_claim": True,
                            "governance_xml_enforcement_claim": True,
                            "sros2_governance_access_control_claim": True,
                            "sros2_governance_repeated_access_control_claim": True,
                            "sros2_governance_runtime_signature_validation_claim": True,
                            "sros2_governance_transport_protection_fail_closed_claim": True,
                            "sros2_tampered_signed_governance_fail_closed_claim": True,
                            "governance_transport_security_claim": False,
                            "sros2_local_identity_credentials_validation_claim": True,
                            "sros2_local_identity_credentials_repeated_validation_claim": True,
                            "sros2_tampered_identity_certificate_fail_closed_claim": True,
                            "sros2_identity_private_key_mismatch_fail_closed_claim": True,
                            "sros2_identity_enclave_mismatch_fail_closed_claim": True,
                            "sros2_peer_identity_authentication_claim": True,
                            "sros2_peer_identity_repeated_authentication_claim": True,
                            "udp_peer_identity_allowlist_fail_closed_claim": True,
                            "udp_peer_signature_tamper_fail_closed_claim": True,
                            "udp_peer_untrusted_certificate_fail_closed_claim": True,
                            "udp_certificate_authenticated_aead_claim": True,
                            "udp_authenticated_psk_session_key_derivation_claim": True,
                            "udp_session_key_rotation_claim": True,
                            "session_key_establishment_claim": True,
                            "forward_secrecy_claim": False,
                            "asymmetric_session_key_exchange_claim": False,
                            "dynamic_serialization_support_claim": True,
                            "dynamic_serialization_support_repeated_claim": True,
                            "dynamic_message_take_claim": True,
                            "dynamic_message_take_with_info_claim": True,
                            "message_info_sequence_features_claim": True,
                            "certificate_revocation_claim": True,
                            "udp_aead_authenticated_encryption_claim": True,
                            "udp_aead_repeated_authenticated_encryption_claim": True,
                            "udp_aead_tamper_fail_closed_claim": True,
                            "udp_aead_strict_missing_key_fail_closed_claim": True,
                            "dds_security_interoperability_claim": False,
                            "sros2_policy_enforcement_claim": False,
                            "production_security_hardening_claim": False,
                            "omnetpp_template_integrity_claim": True,
                            "omnetpp_input_trace_claim": True,
                            "omnetpp_inet_runtime_claim": False,
                            "omnetpp_parity_claim": False,
                            "ns3_omnetpp_parity_claim": False,
                        },
                    }
                ),
                encoding="utf-8",
            )
            quic = root / "docker_quic_gateway_async_burst_soak_summary.json"
            quic.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_quic_gateway_async_burst_soak.v1",
                        "status": "ok",
                        "mode": "netem_async_burst",
                        "run_count": 10,
                        "ok_run_count": 10,
                        "total_quic_gateway_frames_sent": 40,
                        "total_quic_gateway_frames_enqueued": 40,
                        "total_quic_gateway_frames_dropped": 0,
                        "total_quic_gateway_frames_failed": 0,
                        "netem_sent_packets": 208,
                        "rtt_sample_count": 160,
                        "server_payload_bytes_match": True,
                        "production_quic_backend": False,
                        "full_bidirectional_quic_backend": False,
                    }
                ),
                encoding="utf-8",
            )
            stress_security = root / "docker_stress_security_campaign_summary.json"
            stress_security.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_stress_security_campaign.v1",
                        "status": "ok",
                        "campaign_profile": "repeated",
                        "component_count": 10,
                        "ok_component_count": 10,
                        "failed_component_count": 0,
                        "total_component_runs": 48,
                        "total_component_ok_runs": 48,
                        "total_component_failed_runs": 0,
                        "elapsed_s": 120.0,
                        "long_min_runtime_s": 3600.0,
                        "quic_netem": False,
                        "stress_security_smoke_claim": True,
                        "stress_security_repeated_claim": True,
                        "long_stress_security_campaign_claim": False,
                        "long_stress_security_campaign_blocker": (
                            "profile_or_runtime_threshold_not_met"
                        ),
                        "security_policy_enforcement_executed": True,
                        "security_hardening_blocker": (
                            "forward_secret_key_exchange_and_transport_hardening_not_implemented"
                        ),
                        "fleetqox_security_policy_enforcement_claim": True,
                        "security_policy_repeated_enforcement_claim": True,
                        "sros2_cli_generated_artifacts": True,
                        "signed_permissions_verified_preflight": True,
                        "permissions_xsd_validated": True,
                        "sros2_permissions_xml_publish_enforcement_claim": True,
                        "sros2_permissions_xml_subscribe_enforcement_claim": True,
                        "sros2_permissions_xml_pubsub_enforcement_claim": True,
                        "sros2_permissions_xml_repeated_enforcement_claim": True,
                        "sros2_permissions_xml_subscribe_repeated_enforcement_claim": True,
                        "malformed_permissions_fail_closed_claim": True,
                        "runtime_sros2_permissions_signature_validation_claim": True,
                        "tampered_signed_permissions_fail_closed_claim": True,
                        "sros2_service_request_reply_authorization_claim": True,
                        "sros2_service_repeated_authorization_claim": True,
                        "sros2_action_authorization_claim": True,
                        "sros2_action_repeated_authorization_claim": True,
                        "sros2_action_allowed_end_to_end_claim": True,
                        "sros2_action_call_denied_fail_closed_claim": True,
                        "sros2_action_execute_denied_fail_closed_claim": True,
                        "sros2_action_call_execute_decision_matrix_claim": True,
                        "governance_xml_enforcement_claim": True,
                        "sros2_governance_access_control_claim": True,
                        "sros2_governance_repeated_access_control_claim": True,
                        "sros2_governance_runtime_signature_validation_claim": True,
                        "sros2_governance_transport_protection_fail_closed_claim": True,
                        "sros2_tampered_signed_governance_fail_closed_claim": True,
                        "governance_transport_security_claim": False,
                        "sros2_local_identity_credentials_validation_claim": True,
                        "sros2_local_identity_credentials_repeated_validation_claim": True,
                        "sros2_tampered_identity_certificate_fail_closed_claim": True,
                        "sros2_identity_private_key_mismatch_fail_closed_claim": True,
                        "sros2_identity_enclave_mismatch_fail_closed_claim": True,
                        "sros2_peer_identity_authentication_claim": True,
                        "sros2_peer_identity_repeated_authentication_claim": True,
                        "udp_peer_identity_allowlist_fail_closed_claim": True,
                        "udp_peer_signature_tamper_fail_closed_claim": True,
                        "udp_peer_untrusted_certificate_fail_closed_claim": True,
                        "udp_certificate_authenticated_aead_claim": True,
                        "udp_authenticated_psk_session_key_derivation_claim": True,
                        "udp_session_key_rotation_claim": True,
                        "session_key_establishment_claim": True,
                        "forward_secrecy_claim": False,
                        "asymmetric_session_key_exchange_claim": False,
                        "dynamic_serialization_support_claim": True,
                        "dynamic_serialization_support_repeated_claim": True,
                        "dynamic_message_take_claim": True,
                        "dynamic_message_take_with_info_claim": True,
                        "message_info_sequence_features_claim": True,
                        "certificate_revocation_claim": True,
                        "udp_aead_authenticated_encryption_claim": True,
                        "udp_aead_repeated_authenticated_encryption_claim": True,
                        "udp_aead_tamper_fail_closed_claim": True,
                        "udp_aead_strict_missing_key_fail_closed_claim": True,
                        "dds_security_interoperability_claim": False,
                        "sros2_policy_enforcement_claim": False,
                        "production_security_hardening_claim": False,
                        "quic_soak_run_count": 3,
                        "quic_soak_ok_run_count": 3,
                        "quic_soak_total_frames_sent": 12,
                        "quic_soak_total_frames_failed": 0,
                        "quic_soak_total_frames_dropped": 0,
                        "quic_soak_total_bytes_sent": 2140,
                        "quic_soak_qlog_total_bytes": 4096,
                        "production_quic_backend": False,
                        "full_bidirectional_quic_backend": False,
                        "rows": [
                            {
                                "component": "security_options",
                                "status": "ok",
                                "run_count": 5,
                                "ok_run_count": 5,
                                "failed_run_count": 0,
                                "stress_component_ok": True,
                            },
                            {
                                "component": "security_policy",
                                "status": "ok",
                                "run_count": 5,
                                "ok_run_count": 5,
                                "failed_run_count": 0,
                                "stress_component_ok": True,
                            },
                            {
                                "component": "sros2_permissions",
                                "status": "ok",
                                "run_count": 5,
                                "ok_run_count": 5,
                                "failed_run_count": 0,
                                "stress_component_ok": True,
                            },
                            {
                                "component": "udp_aead",
                                "status": "ok",
                                "run_count": 5,
                                "ok_run_count": 5,
                                "failed_run_count": 0,
                                "stress_component_ok": True,
                            },
                            {
                                "component": "udp_peer_auth",
                                "status": "ok",
                                "run_count": 5,
                                "ok_run_count": 5,
                                "failed_run_count": 0,
                                "stress_component_ok": True,
                            },
                            {
                                "component": "dynamic_message",
                                "status": "ok",
                                "run_count": 5,
                                "ok_run_count": 5,
                                "failed_run_count": 0,
                                "stress_component_ok": True,
                            },
                            {
                                "component": "allocation",
                                "status": "ok",
                                "run_count": 5,
                                "ok_run_count": 5,
                                "failed_run_count": 0,
                                "stress_component_ok": True,
                            },
                            {
                                "component": "qos_event",
                                "status": "ok",
                                "run_count": 5,
                                "ok_run_count": 5,
                                "failed_run_count": 0,
                                "stress_component_ok": True,
                            },
                            {
                                "component": "content_filter",
                                "status": "ok",
                                "run_count": 5,
                                "ok_run_count": 5,
                                "failed_run_count": 0,
                                "stress_component_ok": True,
                            },
                            {
                                "component": "quic_gateway_async_burst_soak",
                                "status": "ok",
                                "run_count": 3,
                                "ok_run_count": 3,
                                "failed_run_count": 0,
                                "stress_component_ok": True,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            quic_rmw_take = root / "docker_quic_gateway_rmw_take_probe_summary.json"
            quic_rmw_take.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_quic_gateway_rmw_take_probe.v1"
                        ),
                        "status": "ok",
                        "quic_gateway_take_path_download": True,
                        "rmw_take_path_integrated": True,
                        "take_path_scope": (
                            "rmw_take_serialized_message_on_demand_quic_gateway_get"
                        ),
                        "payload_ok": True,
                        "quic_gateway_frames_received": 1,
                    }
                ),
                encoding="utf-8",
            )
            quic_rmw_take_session = root / (
                "docker_quic_gateway_rmw_take_session_reuse_probe_summary.json"
            )
            quic_rmw_take_session.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_quic_gateway_rmw_take_session_reuse_probe.v1"
                        ),
                        "status": "ok",
                        "session_reuse_file_configured": True,
                        "session_files_persisted": True,
                        "session_file_reused_by_multiple_downloads": True,
                        "token_file_read_count": 5,
                        "token_file_missing_count": 1,
                        "session_resumption_attempted_observed": True,
                        "session_resumption_observed": False,
                        "zero_rtt_packet_observed": True,
                        "zero_rtt_tx_packet_count": 4,
                        "zero_rtt_tx_frame_count": 16,
                        "zero_rtt_accepted_observed": False,
                        "zero_rtt_claim": False,
                        "rmw_take_path_integrated": True,
                        "quic_gateway_take_path_download": True,
                        "requested_download_count": 5,
                        "download_count": 5,
                        "client_handshake_count": 5,
                        "server_handshake_count": 5,
                        "qlog_file_count": 10,
                    }
                ),
                encoding="utf-8",
            )
            quic_bidirectional = root / "docker_quic_gateway_bidirectional_probe_summary.json"
            quic_bidirectional.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_quic_gateway_bidirectional_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "quic_gateway_bidirectional_boundary_claim": True,
                        "quic_gateway_bidirectional_repeated_claim": True,
                        "bidirectional_scope": (
                            "sequential_post_then_get_same_gtlsserver_shared_session_files"
                        ),
                        "rmw_publish_path_integrated": True,
                        "rmw_take_path_integrated": True,
                        "quic_gateway_take_path_download": True,
                        "payload_ok": True,
                        "server_payload_bytes_match": True,
                        "quic_gateway_frames_sent": 5,
                        "quic_gateway_bytes_sent": 2675,
                        "quic_gateway_frames_received": 5,
                        "quic_gateway_bytes_received": 2575,
                        "session_reuse_file_configured": True,
                        "session_files_persisted": True,
                        "session_file_reused_by_upload_and_download": True,
                        "token_file_read_count": 10,
                        "token_file_missing_count": 1,
                        "session_resumption_attempted_observed": True,
                        "session_resumption_observed": False,
                        "zero_rtt_packet_observed": True,
                        "zero_rtt_tx_packet_count": 5,
                        "zero_rtt_tx_frame_count": 20,
                        "zero_rtt_accepted_observed": False,
                        "zero_rtt_claim": False,
                        "production_quic_backend": False,
                        "full_bidirectional_quic_backend": False,
                        "client_handshake_count": 10,
                        "server_handshake_count": 10,
                        "qlog_file_count": 20,
                    }
                ),
                encoding="utf-8",
            )
            quic_no_0rtt = root / (
                "docker_quic_gateway_bidirectional_no_0rtt_probe_summary.json"
            )
            quic_no_0rtt.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_quic_gateway_bidirectional_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "quic_gateway_bidirectional_boundary_claim": True,
                        "quic_gateway_bidirectional_repeated_claim": True,
                        "rmw_publish_path_integrated": True,
                        "rmw_take_path_integrated": True,
                        "session_files_persisted": True,
                        "session_file_reused_by_upload_and_download": True,
                        "early_data_disabled": True,
                        "session_resumption_attempted_observed": False,
                        "session_resumption_observed": False,
                        "zero_rtt_packet_observed": False,
                        "zero_rtt_tx_packet_count": 0,
                        "zero_rtt_tx_frame_count": 0,
                        "zero_rtt_accepted_observed": False,
                        "zero_rtt_disabled_control_claim": True,
                        "zero_rtt_claim": False,
                        "production_quic_backend": False,
                        "full_bidirectional_quic_backend": False,
                        "client_handshake_count": 10,
                        "server_handshake_count": 10,
                        "qlog_file_count": 20,
                    }
                ),
                encoding="utf-8",
            )
            nav2_pc = root / "docker_nav2_planner_controller_lifecycle_probe_summary.json"
            nav2_pc.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_planner_controller_lifecycle_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "nav2_controller_server_available": True,
                        "planner_configure_transition": True,
                        "controller_configure_transition": True,
                        "lifecycle_transport": True,
                        "activation_claim": False,
                        "activation_gap": "map_tf_costmap_runtime_not_started",
                        "full_nav2_navigation_stack_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_concurrency8 = (
                root / "docker_router_nav2_rmf_action_workload_concurrency8_summary.json"
            )
            nav2_rmf_concurrency8.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v5",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 8,
                        "expected_service_frames": 106,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_concurrency16 = (
                root / "docker_router_nav2_rmf_action_workload_concurrency16_summary.json"
            )
            nav2_rmf_concurrency16.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v5",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 16,
                        "expected_service_frames": 154,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_concurrency32 = (
                root / "docker_router_nav2_rmf_action_workload_concurrency32_summary.json"
            )
            nav2_rmf_concurrency32.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v5",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 32,
                        "expected_service_frames": 250,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_concurrency64 = (
                root / "docker_router_nav2_rmf_action_workload_concurrency64_summary.json"
            )
            nav2_rmf_concurrency64.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v5",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 64,
                        "expected_service_frames": 442,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_concurrency128 = (
                root / "docker_router_nav2_rmf_action_workload_concurrency128_summary.json"
            )
            nav2_rmf_concurrency128.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v5",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 128,
                        "expected_service_frames": 826,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_concurrency256 = (
                root / "docker_router_nav2_rmf_action_workload_concurrency256_summary.json"
            )
            nav2_rmf_concurrency256.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v5",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 256,
                        "expected_service_frames": 1594,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_concurrency512 = (
                root / "docker_router_nav2_rmf_action_workload_concurrency512_summary.json"
            )
            nav2_rmf_concurrency512.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v5",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 512,
                        "expected_service_frames": 3130,
                        "batch_timeout_s": 76,
                        "router_timeout_ms": 232000,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_concurrency1024 = (
                root / "docker_router_nav2_rmf_action_workload_concurrency1024_summary.json"
            )
            nav2_rmf_concurrency1024.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v5",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 1024,
                        "expected_service_frames": 6202,
                        "batch_timeout_s": 120,
                        "router_timeout_ms": 320000,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_concurrency2048 = (
                root / "docker_router_nav2_rmf_action_workload_concurrency2048_summary.json"
            )
            nav2_rmf_concurrency2048.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v5",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 2048,
                        "expected_service_frames": 12346,
                        "batch_timeout_s": 120,
                        "router_timeout_ms": 320000,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_concurrency4096 = (
                root / "docker_router_nav2_rmf_action_workload_concurrency4096_summary.json"
            )
            nav2_rmf_concurrency4096.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v6",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 4096,
                        "expected_service_frames": 24634,
                        "batch_timeout_s": 524,
                        "server_timeout_s": 1168,
                        "goal_batch_size": 4096,
                        "goal_batch_count": 1,
                        "goal_batch_timeout_s": 720,
                        "goal_send_pacing_ms": 0.5,
                        "requested_goal_send_pacing_ms": 0.0,
                        "unwindowed_goal_batch": True,
                        "executor_spin_pacing_enabled": True,
                        "nav2_rmf_unwindowed_4096_claim": True,
                        "goal_recreate_client_per_batch": False,
                        "executor_threads": 12,
                        "result_window_size": 512,
                        "udp_socket_buffer_bytes": 16777216,
                        "udp_send_pacing_us": 250,
                        "service_request_repeats": 3,
                        "service_response_repeats": 3,
                        "service_request_repeat_interval_ms": 1,
                        "service_response_repeat_interval_ms": 1,
                        "router_expected_service_frames": 0,
                        "router_post_satisfaction_ms": 30000,
                        "router_timeout_ms": 1128000,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_rmf_total4096_goalbatch8 = (
                root / "docker_router_nav2_rmf_action_workload_total4096_goalbatch8_summary.json"
            )
            nav2_rmf_total4096_goalbatch8.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.rmw_router_nav2_rmf_action_workload.v5",
                        "status": "ok",
                        "nav2_compatible": True,
                        "rmf_compatible": True,
                        "nav2_upstream": True,
                        "rmf_upstream": True,
                        "upstream_concurrency": 4096,
                        "expected_service_frames": 24634,
                        "batch_timeout_s": 524,
                        "goal_batch_size": 8,
                        "goal_batch_count": 512,
                        "goal_batch_timeout_s": 20.0,
                        "goal_send_pacing_ms": 1.0,
                        "goal_batch_delay_ms": 50.0,
                        "goal_recreate_client_per_batch": False,
                        "server_timeout_s": 1800,
                        "executor_threads": 12,
                        "result_window_size": 512,
                        "udp_socket_buffer_bytes": 16777216,
                        "udp_send_pacing_us": 250,
                        "service_request_repeats": 3,
                        "service_response_repeats": 3,
                        "service_request_repeat_interval_ms": 1,
                        "service_response_repeat_interval_ms": 1,
                        "router_expected_service_frames": 0,
                        "router_post_satisfaction_ms": 30000,
                        "router_timeout_ms": 10844000,
                        "navigation_batch": True,
                        "rmf_batch": True,
                        "lifecycle_transport": True,
                        "nav2_lifecycle_manager_upstream": True,
                        "manager_running_after_workload": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_activation = (
                root / "docker_nav2_planner_controller_activation_probe_summary.json"
            )
            nav2_activation.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_planner_controller_activation_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "nav2_controller_server_available": True,
                        "planner_configure_transition": True,
                        "controller_configure_transition": True,
                        "planner_activate_transition": True,
                        "controller_activate_transition": True,
                        "dynamic_tf_runtime": True,
                        "tf_topic_advertised": True,
                        "tf_topic_forwarded": True,
                        "lifecycle_transport": True,
                        "activation_claim": True,
                        "activation_scope": (
                            "planner_controller_lifecycle_active_with_dynamic_tf"
                        ),
                        "map_server_claim": False,
                        "odometry_source_claim": False,
                        "navigation_goal_claim": False,
                        "full_nav2_navigation_stack_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            nav2_compute_path = root / "docker_nav2_planner_compute_path_probe_summary.json"
            nav2_compute_path.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_planner_compute_path_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "planner_configure_transition": True,
                        "planner_activate_transition": True,
                        "dynamic_tf_runtime": True,
                        "map_runtime": True,
                        "tf_topic_advertised": True,
                        "tf_topic_forwarded": True,
                        "map_topic_advertised": True,
                        "map_topic_forwarded": True,
                        "compute_path_action": True,
                        "compute_path_goal_accepted": True,
                        "compute_path_goal_succeeded": True,
                        "compute_path_error_code": 0,
                        "compute_path_path_pose_count": 14,
                        "planner_action_execution_claim": True,
                        "planner_action_execution_scope": (
                            "compute_path_to_pose_with_repeated_map_and_tf"
                        ),
                        "controller_execution_claim": False,
                        "odometry_source_claim": False,
                        "navigation_goal_claim": False,
                        "full_nav2_navigation_stack_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            nav2_follow_path = root / "docker_nav2_controller_follow_path_probe_summary.json"
            nav2_follow_path.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_controller_follow_path_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_controller_server_available": True,
                        "controller_configure_transition": True,
                        "controller_activate_transition": True,
                        "dynamic_tf_runtime": True,
                        "map_runtime": True,
                        "odometry_runtime": True,
                        "tf_topic_advertised": True,
                        "tf_topic_forwarded": True,
                        "map_topic_advertised": True,
                        "map_topic_forwarded": True,
                        "odom_topic_advertised": True,
                        "odom_topic_forwarded": True,
                        "follow_path_action": True,
                        "follow_path_goal_accepted": True,
                        "follow_path_goal_succeeded": True,
                        "follow_path_error_code": 0,
                        "controller_execution_claim": True,
                        "controller_execution_scope": (
                            "follow_path_current_pose_with_repeated_map_tf_odom"
                        ),
                        "planner_action_execution_claim": False,
                        "navigation_goal_claim": False,
                        "full_nav2_navigation_stack_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            nav2_navigate = root / "docker_nav2_navigate_to_pose_probe_summary.json"
            nav2_navigate.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_navigate_to_pose_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "nav2_controller_server_available": True,
                        "nav2_bt_navigator_available": True,
                        "planner_activate_transition": True,
                        "controller_activate_transition": True,
                        "bt_navigator_activate_transition": True,
                        "dynamic_tf_runtime": True,
                        "map_runtime": True,
                        "odometry_runtime": True,
                        "tf_topic_forwarded": True,
                        "map_topic_forwarded": True,
                        "odom_topic_forwarded": True,
                        "compute_path_status_forwarded": True,
                        "follow_path_feedback_forwarded": True,
                        "navigate_to_pose_status_forwarded": True,
                        "navigate_to_pose_action": True,
                        "navigate_to_pose_goal_accepted": True,
                        "navigate_to_pose_goal_succeeded": True,
                        "navigate_to_pose_error_code": 0,
                        "navigate_to_pose_goal_scope": (
                            "current_pose_minimal_bt_no_motion"
                        ),
                        "planner_action_execution_claim": True,
                        "controller_execution_claim": True,
                        "navigate_to_pose_execution_claim": True,
                        "navigate_to_pose_execution_scope": (
                            "same_pose_minimal_bt_pipeline"
                        ),
                        "full_nav2_navigation_stack_claim": True,
                        "full_nav2_navigation_stack_scope": (
                            "ci_light_same_pose_nav2_bt_pipeline_no_motion"
                        ),
                        "moving_robot_navigation_claim": False,
                        "recovery_behavior_claim": False,
                        "long_navigation_workload_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            nav2_repeated = root / "docker_nav2_navigate_to_pose_repeated_probe_summary.json"
            nav2_repeated.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_navigate_to_pose_repeated_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 2,
                        "ok_run_count": 2,
                        "failed_run_count": 0,
                        "navigate_to_pose_repeated_smoke": True,
                        "navigate_to_pose_goal_succeeded_run_count": 2,
                        "full_nav2_navigation_stack_claim": True,
                        "moving_robot_navigation_claim": False,
                        "recovery_behavior_claim": False,
                        "long_navigation_workload_claim": False,
                        "total_fleetqox_router_service_frames": 108,
                        "total_fleetqox_router_forwarded_frames": 220,
                        "total_fleetqox_router_received_frames": 220,
                        "min_service_frames_per_run": 54,
                    }
                ),
                encoding="utf-8",
            )
            nav2_moving = root / "docker_nav2_navigate_to_pose_moving_probe_summary.json"
            nav2_moving.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_navigate_to_pose_moving_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "nav2_controller_server_available": True,
                        "nav2_bt_navigator_available": True,
                        "planner_activate_transition": True,
                        "controller_activate_transition": True,
                        "bt_navigator_activate_transition": True,
                        "dynamic_tf_runtime": True,
                        "map_runtime": True,
                        "odometry_runtime": True,
                        "tf_topic_forwarded": True,
                        "map_topic_forwarded": True,
                        "odom_topic_forwarded": True,
                        "cmd_vel_topic_forwarded": True,
                        "fake_base_cmd_vel_count": 4,
                        "fake_base_max_abs_cmd_x": 0.2463,
                        "fake_base_final_x": 0.4064,
                        "fake_base_max_x": 0.4064,
                        "fake_base_moved_distance": 0.4064,
                        "navigation_goal_x": 0.6,
                        "navigation_goal_y": 0.0,
                        "compute_path_status_forwarded": True,
                        "follow_path_feedback_forwarded": True,
                        "navigate_to_pose_status_forwarded": True,
                        "navigate_to_pose_action": True,
                        "navigate_to_pose_goal_accepted": True,
                        "navigate_to_pose_goal_succeeded": True,
                        "navigate_to_pose_error_code": 0,
                        "navigate_to_pose_goal_scope": "moving_base_minimal_bt",
                        "planner_action_execution_claim": True,
                        "controller_execution_claim": True,
                        "navigate_to_pose_execution_claim": True,
                        "navigate_to_pose_execution_scope": (
                            "moving_base_minimal_bt_pipeline"
                        ),
                        "full_nav2_navigation_stack_claim": True,
                        "full_nav2_navigation_stack_scope": (
                            "ci_light_moving_base_nav2_bt_pipeline"
                        ),
                        "moving_robot_navigation_claim": True,
                        "recovery_behavior_claim": False,
                        "long_navigation_workload_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            nav2_extended_moving = (
                root / "docker_nav2_navigate_to_pose_extended_moving_probe_summary.json"
            )
            nav2_extended_moving.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_navigate_to_pose_extended_moving_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "nav2_controller_server_available": True,
                        "nav2_bt_navigator_available": True,
                        "planner_activate_transition": True,
                        "controller_activate_transition": True,
                        "bt_navigator_activate_transition": True,
                        "dynamic_tf_runtime": True,
                        "map_runtime": True,
                        "odometry_runtime": True,
                        "tf_topic_forwarded": True,
                        "map_topic_forwarded": True,
                        "odom_topic_forwarded": True,
                        "cmd_vel_topic_forwarded": True,
                        "fake_base_cmd_vel_count": 6,
                        "fake_base_max_abs_cmd_x": 0.26,
                        "fake_base_final_x": 0.956,
                        "fake_base_max_x": 0.956,
                        "fake_base_moved_distance": 0.956,
                        "navigation_goal_x": 1.2,
                        "navigation_goal_y": 0.0,
                        "compute_path_status_forwarded": True,
                        "follow_path_feedback_forwarded": True,
                        "navigate_to_pose_status_forwarded": True,
                        "navigate_to_pose_action": True,
                        "navigate_to_pose_goal_accepted": True,
                        "navigate_to_pose_goal_succeeded": True,
                        "navigate_to_pose_error_code": 0,
                        "navigate_to_pose_goal_scope": "moving_base_minimal_bt",
                        "planner_action_execution_claim": True,
                        "controller_execution_claim": True,
                        "navigate_to_pose_execution_claim": True,
                        "full_nav2_navigation_stack_claim": True,
                        "moving_robot_navigation_claim": True,
                        "extended_moving_navigation_claim": True,
                        "extended_moving_navigation_scope": (
                            "single_goal_unobstructed_1m_plus_fake_base_nav2_bt_pipeline"
                        ),
                        "recovery_behavior_claim": False,
                        "long_navigation_workload_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            nav2_long_moving = (
                root / "docker_nav2_navigate_to_pose_long_moving_probe_summary.json"
            )
            nav2_long_moving.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_navigate_to_pose_long_moving_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 3,
                        "ok_run_count": 3,
                        "failed_run_count": 0,
                        "navigate_to_pose_long_moving_workload": True,
                        "navigate_to_pose_goal_succeeded_run_count": 3,
                        "extended_moving_navigation_run_count": 3,
                        "moving_robot_navigation_claim": True,
                        "extended_moving_navigation_claim": True,
                        "long_navigation_workload_claim": True,
                        "long_navigation_workload_scope": (
                            "repeated_unobstructed_1m_plus_moving_base_nav2_bt_pipeline"
                        ),
                        "obstacle_field_recovery_claim": False,
                        "total_fake_base_cmd_vel_count": 21,
                        "total_fake_base_moved_distance": 2.94,
                        "min_fake_base_moved_distance": 0.95,
                        "max_navigation_goal_x": 1.2,
                        "min_required_iterations": 3,
                        "min_required_total_fake_base_moved_distance": 2.4,
                        "min_required_total_fake_base_cmd_vel_count": 18,
                        "total_fleetqox_router_service_frames": 165,
                        "min_service_frames_per_run": 54,
                    }
                ),
                encoding="utf-8",
            )
            nav2_obstacle_repair = (
                root / "docker_nav2_planner_obstacle_repair_probe_summary.json"
            )
            nav2_obstacle_repair.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_planner_obstacle_repair_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "planner_configure_transition": True,
                        "planner_activate_transition": True,
                        "dynamic_tf_runtime": True,
                        "map_runtime": True,
                        "blocked_map_wall_x_index": 19,
                        "blocked_map_obstacle_cells": 30,
                        "clear_map_obstacle_cells": 0,
                        "blocked_compute_path_goal_accepted": True,
                        "blocked_compute_path_goal_succeeded": False,
                        "blocked_compute_path_error_code": 208,
                        "blocked_compute_path_failed": True,
                        "clear_compute_path_goal_accepted": True,
                        "clear_compute_path_goal_succeeded": True,
                        "clear_compute_path_error_code": 0,
                        "clear_compute_path_path_pose_count": 14,
                        "planner_static_obstacle_repair_claim": True,
                        "planner_static_obstacle_repair_scope": (
                            "blocked_static_occupancy_grid_then_clear_map_replan"
                        ),
                        "obstacle_field_recovery_claim": True,
                        "obstacle_field_recovery_scope": (
                            "planner_level_static_map_obstacle_blocks_then_clear_map_replans"
                        ),
                        "full_nav2_obstacle_recovery_claim": False,
                        "full_nav2_obstacle_recovery_scope": (
                            "not_bt_navigate_to_pose_controller_recovery"
                        ),
                        "lifecycle_transport": True,
                        "expected_service_frames": 18,
                        "fleetqox_router_service_frames": 22,
                        "fleetqox_router_service_forwarded": 22,
                    }
                ),
                encoding="utf-8",
            )
            nav2_obstacle_retry = (
                root / "docker_nav2_navigate_to_pose_obstacle_retry_probe_summary.json"
            )
            nav2_obstacle_retry.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_navigate_to_pose_obstacle_retry_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "nav2_controller_server_available": True,
                        "nav2_bt_navigator_available": True,
                        "planner_activate_transition": True,
                        "controller_activate_transition": True,
                        "bt_navigator_activate_transition": True,
                        "dynamic_tf_runtime": True,
                        "map_runtime": True,
                        "odometry_runtime": True,
                        "cmd_vel_topic_forwarded": True,
                        "blocked_map_wall_x_index": 19,
                        "blocked_map_obstacle_cells": 30,
                        "clear_map_obstacle_cells": 0,
                        "blocked_navigate_to_pose_goal_accepted": True,
                        "blocked_navigate_to_pose_goal_succeeded": False,
                        "blocked_navigate_to_pose_status": "ABORTED",
                        "blocked_navigate_to_pose_error_code": 208,
                        "blocked_navigate_to_pose_failed": True,
                        "planner_blocked_failure_observed": True,
                        "clear_navigate_to_pose_goal_accepted": True,
                        "clear_navigate_to_pose_goal_succeeded": True,
                        "clear_navigate_to_pose_status": "SUCCEEDED",
                        "clear_navigate_to_pose_error_code": 0,
                        "fake_base_cmd_vel_count": 6,
                        "fake_base_moved_distance": 0.61,
                        "navigation_goal_x": 0.8,
                        "lifecycle_transport": True,
                        "expected_service_frames": 58,
                        "fleetqox_router_service_frames": 62,
                        "fleetqox_router_service_forwarded": 62,
                        "nav2_obstacle_retry_after_clear_claim": True,
                        "nav2_obstacle_retry_after_clear_scope": (
                            "blocked_static_map_navigate_to_pose_then_clear_map_retry_success"
                        ),
                        "full_nav2_obstacle_recovery_claim": True,
                        "full_nav2_obstacle_recovery_scope": (
                            "full_stack_two_goal_static_map_blocked_then_clear_map_retry"
                        ),
                        "autonomous_same_goal_nav2_obstacle_recovery_claim": False,
                        "obstacle_field_recovery_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_autonomous_obstacle = (
                root
                / "docker_nav2_navigate_to_pose_autonomous_obstacle_recovery_probe_summary.json"
            )
            nav2_autonomous_obstacle.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_navigate_to_pose_autonomous_obstacle_recovery_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "nav2_controller_server_available": True,
                        "nav2_behavior_server_available": True,
                        "nav2_bt_navigator_available": True,
                        "planner_activate_transition": True,
                        "controller_activate_transition": True,
                        "behavior_server_activate_transition": True,
                        "bt_navigator_activate_transition": True,
                        "dynamic_tf_runtime": True,
                        "map_runtime": True,
                        "odometry_runtime": True,
                        "cmd_vel_topic_forwarded": True,
                        "wait_action_forwarded": True,
                        "blocked_map_wall_x_index": 19,
                        "blocked_map_obstacle_cells": 30,
                        "clear_map_obstacle_cells": 0,
                        "clear_map_delay_s": 6.0,
                        "bt_wait_duration_s": 2.0,
                        "bt_compute_path_retry_count": 8,
                        "navigate_to_pose_goal_accepted": True,
                        "navigate_to_pose_goal_succeeded": True,
                        "navigate_to_pose_status": "SUCCEEDED",
                        "navigate_to_pose_error_code": 0,
                        "same_goal_obstacle_recovery_observed": True,
                        "planner_blocked_failure_observed": True,
                        "clear_map_published_during_goal": True,
                        "fake_base_cmd_vel_count": 6,
                        "fake_base_moved_distance": 0.61,
                        "navigation_goal_x": 0.8,
                        "lifecycle_transport": True,
                        "expected_service_frames": 72,
                        "fleetqox_router_service_frames": 1989,
                        "fleetqox_router_service_forwarded": 1989,
                        "nav2_obstacle_retry_after_clear_claim": True,
                        "full_nav2_obstacle_recovery_claim": True,
                        "autonomous_same_goal_nav2_obstacle_recovery_claim": True,
                        "autonomous_same_goal_nav2_obstacle_recovery_scope": (
                            "same_goal_bt_compute_path_wait_retry_after_external_static_map_repair"
                        ),
                        "obstacle_field_recovery_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            nav2_spin = root / "docker_nav2_behavior_spin_probe_summary.json"
            nav2_spin.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_nav2_behavior_spin_probe.v1",
                        "status": "ok",
                        "nav2_behavior_server_available": True,
                        "behavior_plugin": "nav2_behaviors::Spin",
                        "behavior_server_activate_transition": True,
                        "dynamic_tf_runtime": True,
                        "odometry_runtime": True,
                        "tf_topic_forwarded": True,
                        "odom_topic_advertised": True,
                        "cmd_vel_topic_forwarded": True,
                        "spin_action": True,
                        "spin_goal_accepted": True,
                        "spin_goal_succeeded": True,
                        "spin_error_code": 0,
                        "spin_target_yaw": 0.6,
                        "spin_status_forwarded": True,
                        "spin_feedback_forwarded": True,
                        "fake_base_cmd_vel_count": 8,
                        "fake_base_max_abs_cmd_theta": 1.0,
                        "fake_base_final_theta": 0.616,
                        "fake_base_max_abs_theta": 0.616,
                        "fake_base_angular_distance": 0.616,
                        "lifecycle_transport": True,
                        "recovery_behavior_action_claim": True,
                        "recovery_behavior_scope": (
                            "nav2_behavior_server_spin_action_with_fake_base"
                        ),
                        "nav2_recovery_behavior_claim": True,
                        "navigate_to_pose_recovery_tree_claim": False,
                        "long_navigation_workload_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            nav2_recovery_tree = (
                root / "docker_nav2_navigate_to_pose_recovery_tree_probe_summary.json"
            )
            nav2_recovery_tree.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_navigate_to_pose_recovery_tree_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "nav2_behavior_server_available": True,
                        "nav2_bt_navigator_available": True,
                        "planner_activate_transition": True,
                        "behavior_server_activate_transition": True,
                        "bt_navigator_activate_transition": True,
                        "behavior_tree": (
                            "recovery_node_compute_path_missing_planner_then_spin"
                        ),
                        "intentional_planner_failure": True,
                        "planner_failure_observed": True,
                        "navigate_to_pose_action": True,
                        "navigate_to_pose_goal_accepted": True,
                        "navigate_to_pose_goal_succeeded": False,
                        "navigate_to_pose_result_observed": True,
                        "navigate_to_pose_status": "ABORTED",
                        "navigate_to_pose_error_code": 201,
                        "navigate_to_pose_goal_scope": (
                            "intentional_planner_failure_recovery_tree"
                        ),
                        "navigate_to_pose_status_forwarded": True,
                        "compute_path_status_forwarded": True,
                        "spin_action": True,
                        "spin_goal_succeeded": True,
                        "spin_target_yaw": 0.6,
                        "spin_status_forwarded": True,
                        "spin_feedback_forwarded": True,
                        "cmd_vel_topic_forwarded": True,
                        "fake_base_cmd_vel_count": 8,
                        "fake_base_angular_distance": 0.616,
                        "dynamic_tf_runtime": True,
                        "map_runtime": True,
                        "odometry_runtime": True,
                        "tf_topic_forwarded": True,
                        "map_topic_forwarded": True,
                        "odom_topic_forwarded": True,
                        "lifecycle_transport": True,
                        "fleetqox_router_service_frames": 58,
                        "nav2_recovery_behavior_claim": True,
                        "navigate_to_pose_recovery_tree_claim": True,
                        "navigate_to_pose_recovery_tree_scope": (
                            "intentional_compute_path_failure_spin_recovery_branch"
                        ),
                        "successful_recovered_navigation_claim": False,
                        "long_navigation_workload_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            nav2_recovered_success = (
                root / "docker_nav2_navigate_to_pose_recovered_success_probe_summary.json"
            )
            nav2_recovered_success.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_navigate_to_pose_recovered_success_probe.v1"
                        ),
                        "status": "ok",
                        "nav2_planner_server_available": True,
                        "nav2_controller_server_available": True,
                        "nav2_behavior_server_available": True,
                        "nav2_bt_navigator_available": True,
                        "planner_activate_transition": True,
                        "controller_activate_transition": True,
                        "behavior_server_activate_transition": True,
                        "bt_navigator_activate_transition": True,
                        "behavior_tree": "spin_then_compute_path_then_follow_path",
                        "navigate_to_pose_action": True,
                        "navigate_to_pose_goal_accepted": True,
                        "navigate_to_pose_goal_succeeded": True,
                        "navigate_to_pose_error_code": 0,
                        "navigate_to_pose_goal_scope": (
                            "spin_recovery_action_then_successful_navigation"
                        ),
                        "navigate_to_pose_status_forwarded": True,
                        "compute_path_status_forwarded": True,
                        "follow_path_feedback_forwarded": True,
                        "spin_action": True,
                        "spin_goal_succeeded": True,
                        "spin_target_yaw": 0.35,
                        "spin_status_forwarded": True,
                        "spin_feedback_forwarded": True,
                        "cmd_vel_topic_forwarded": True,
                        "fake_base_cmd_vel_count": 9,
                        "fake_base_moved_distance": 0.405,
                        "fake_base_max_abs_theta": 0.355,
                        "fake_base_angular_distance": 0.171,
                        "navigation_goal_x": 0.6,
                        "dynamic_tf_runtime": True,
                        "map_runtime": True,
                        "odometry_runtime": True,
                        "tf_topic_forwarded": True,
                        "map_topic_forwarded": True,
                        "odom_topic_forwarded": True,
                        "lifecycle_transport": True,
                        "fleetqox_router_service_frames": 72,
                        "nav2_recovery_behavior_claim": True,
                        "successful_recovered_navigation_claim": True,
                        "successful_recovered_navigation_scope": (
                            "spin_recovery_action_then_successful_navigate_to_pose"
                        ),
                        "obstacle_field_recovery_claim": False,
                        "long_navigation_workload_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            nav2_recovered_success_repeated = (
                root
                / "docker_nav2_navigate_to_pose_recovered_success_repeated_probe_summary.json"
            )
            nav2_recovered_success_repeated.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_nav2_navigate_to_pose_recovered_success_repeated_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 2,
                        "ok_run_count": 2,
                        "failed_run_count": 0,
                        "navigate_to_pose_recovered_success_repeated_smoke": True,
                        "spin_goal_succeeded_run_count": 2,
                        "successful_recovered_navigation_run_count": 2,
                        "navigate_to_pose_goal_succeeded_run_count": 2,
                        "nav2_recovery_behavior_claim": True,
                        "successful_recovered_navigation_claim": True,
                        "successful_recovered_navigation_scope": (
                            "repeated_spin_recovery_action_then_successful_navigate_to_pose"
                        ),
                        "repeated_recovered_navigation_claim": True,
                        "obstacle_field_recovery_claim": False,
                        "long_navigation_workload_claim": False,
                        "total_fake_base_cmd_vel_count": 18,
                        "total_fake_base_moved_distance": 0.81,
                        "max_fake_base_abs_theta": 0.356,
                        "total_fleetqox_router_service_frames": 144,
                        "min_service_frames_per_run": 72,
                    }
                ),
                encoding="utf-8",
            )
            matched_event = root / "docker_matched_event_probe_summary.json"
            matched_event.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_matched_event_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "matched_event_production": True,
                        "matched_event_scope": (
                            "local_same_process_compatible_endpoint_create_destroy"
                        ),
                        "publication_callback_events": 2,
                        "subscription_callback_events": 2,
                        "publication_disconnect_current_count_change": -1,
                        "subscription_disconnect_current_count_change": -1,
                        "matched_event_repeated_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            remote_event = root / "docker_remote_event_probe_summary.json"
            remote_event.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_remote_event_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "explicit_remove_run_count": 3,
                        "lease_expiry_run_count": 2,
                        "remote_matched_event_production": True,
                        "remote_qos_event_production": True,
                        "remote_qos_policy_matrix": [
                            "reliability",
                            "durability",
                            "deadline",
                        ],
                        "remote_type_event_production": True,
                        "remote_liveliness_event_production": True,
                        "renewal_deduplication": True,
                        "real_udp_multicontainer": True,
                    }
                ),
                encoding="utf-8",
            )
            type_incompatible = root / "docker_type_incompatible_event_probe_summary.json"
            type_incompatible.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_type_incompatible_event_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "type_incompatible_event_production": True,
                        "type_incompatible_event_scope": (
                            "local_same_process_same_topic_type_mismatch"
                        ),
                        "publisher_total_count": 1,
                        "subscription_total_count": 1,
                        "type_incompatible_repeated_event_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            qos_incompatible = root / "docker_qos_incompatible_event_probe_summary.json"
            qos_incompatible.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_qos_incompatible_event_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "qos_incompatible_event_production": True,
                        "qos_incompatible_event_scope": (
                            "local_same_process_reliability_and_durability_mismatch"
                        ),
                        "offered_last_policy_kind": 16,
                        "requested_last_policy_kind": 16,
                        "durability_offered_last_policy_kind": 2,
                        "durability_requested_last_policy_kind": 2,
                        "durability_offered_total_count": 1,
                        "durability_requested_total_count": 1,
                        "qos_incompatible_repeated_event_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            qos_deadline_incompatible = (
                root / "docker_qos_deadline_incompatible_event_probe_summary.json"
            )
            qos_deadline_incompatible.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_qos_deadline_incompatible_event_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "qos_deadline_incompatible_event_production": True,
                        "qos_deadline_incompatible_event_scope": (
                            "local_same_process_deadline_mismatch"
                        ),
                        "offered_last_policy_kind": 4,
                        "requested_last_policy_kind": 4,
                        "offered_total_count": 1,
                        "requested_total_count": 1,
                        "qos_deadline_incompatible_repeated_event_claim": True,
                        "qos_missing_offered_deadline_incompatible_event_claim": True,
                        "qos_missing_offered_deadline_incompatible_repeated_claim": True,
                        "missing_offered_deadline_offered_event_claim": True,
                        "missing_offered_deadline_requested_event_claim": True,
                        "missing_offered_total_count": 1,
                        "missing_requested_total_count": 1,
                        "missing_offered_last_policy_kind": 4,
                        "missing_requested_last_policy_kind": 4,
                        "scenario_count": 4,
                    }
                ),
                encoding="utf-8",
            )
            remote_deadline = root / "docker_remote_deadline_event_probe_summary.json"
            remote_deadline.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_remote_deadline_event_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "real_udp_multicontainer": True,
                        "netem_applied": True,
                        "deadline_ms": 100,
                        "remote_offered_deadline_missed_event_claim": True,
                        "remote_requested_deadline_missed_event_claim": True,
                        "remote_deadline_missed_event_repeated_claim": True,
                        "offered_total_count": 1,
                        "offered_callback_events": 1,
                        "requested_total_count": 1,
                        "requested_callback_events": 1,
                        "clean_teardown": True,
                    }
                ),
                encoding="utf-8",
            )
            message_lost = root / "docker_message_lost_event_probe_summary.json"
            message_lost.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_message_lost_event_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "message_lost_event_production": True,
                        "message_lost_event_scope": (
                            "local_keep_last_overwrite_best_effort_gap_repair_"
                            "suppression_and_reliable_history_exhaustion"
                        ),
                        "message_lost_total_count": 1,
                        "best_effort_gap_detected": True,
                        "best_effort_gap_received_frames": 3,
                        "best_effort_gap_total_count": 1,
                        "best_effort_gap_callback_events": 1,
                        "repair_suppressed_false_message_lost": True,
                        "repair_received_frames": 4,
                        "repair_observer_message_lost_total_count": 0,
                        "reliable_history_exhaustion_detected": True,
                        "reliable_history_exhaustion_received_frames": 3,
                        "reliable_history_exhaustion_total_count": 1,
                        "unrecoverable_loss_notices_sent": 1,
                        "unrecoverable_loss_notices_received": 1,
                        "unrecoverable_loss_samples_reported": 1,
                        "message_lost_repeated_event_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            message_lost_interprocess = (
                root / "docker_message_lost_interprocess_probe_summary.json"
            )
            message_lost_interprocess.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_message_lost_interprocess_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 20,
                        "ok_run_count": 20,
                        "netem": "delay 8ms 2ms on publisher and subscriber",
                        "netem_applied": True,
                        "remote_unrecoverable_loss_notice_claim": True,
                        "remote_message_lost_waitable_claim": True,
                        "duplicate_unrecoverable_loss_notice_deduplication_claim": True,
                        "repeated_remote_message_lost_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            message_lost_terminal_repair = (
                root / "docker_message_lost_terminal_repair_probe_summary.json"
            )
            message_lost_terminal_repair.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_message_lost_terminal_repair_probe.v1"
                        ),
                        "status": "ok",
                        "scenario_count": 3,
                        "iterations_per_scenario": 5,
                        "run_count": 15,
                        "ok_run_count": 15,
                        "netem_applied": True,
                        "repair_budget_terminal_loss_notice_claim": True,
                        "repair_attempt_limit_terminal_loss_notice_claim": True,
                        "repair_admission_terminal_loss_notice_claim": True,
                        "terminal_repair_duplicate_notice_deduplication_claim": True,
                        "terminal_repair_clean_teardown_claim": True,
                        "terminal_repair_controls_repeated_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            liveliness = root / "docker_liveliness_event_probe_summary.json"
            liveliness.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_liveliness_event_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "liveliness_event_production": True,
                        "liveliness_event_scope": (
                            "local_same_process_finite_lease_timeout_and_reassert"
                        ),
                        "lost_total_count": 1,
                        "liveliness_repeated_event_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            automatic_liveliness = (
                root / "docker_automatic_liveliness_probe_summary.json"
            )
            automatic_liveliness.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_automatic_liveliness_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "automatic_liveliness_idle_renewal_claim": True,
                        "automatic_liveliness_false_loss_suppression_claim": True,
                        "automatic_liveliness_repeated_claim": True,
                        "lease_ms": 20,
                        "idle_ms": 120,
                        "idle_lease_multiples": 6,
                        "alive_count": 1,
                        "not_alive_count": 0,
                        "liveliness_lost_total_count": 0,
                        "lost_callback_events": 0,
                        "changed_callback_events": 1,
                        "clean_teardown": True,
                    }
                ),
                encoding="utf-8",
            )
            remote_manual_liveliness = (
                root / "docker_remote_manual_liveliness_probe_summary.json"
            )
            remote_manual_liveliness.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_remote_manual_liveliness_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "real_udp_multicontainer": True,
                        "netem_applied": True,
                        "liveliness_policy": "MANUAL_BY_TOPIC",
                        "liveliness_lease_ms": 200,
                        "graph_renew_interval_ms": 100,
                        "remote_manual_liveliness_idle_timeout_claim": True,
                        "remote_manual_liveliness_explicit_assert_claim": True,
                        "remote_manual_liveliness_publish_assert_claim": True,
                        "remote_publisher_liveliness_lost_event_claim": True,
                        "remote_manual_liveliness_graph_lease_independence_claim": True,
                        "remote_manual_liveliness_repeated_claim": True,
                        "assertions_received": 10,
                        "manual_liveliness_expiries": 2,
                        "manual_liveliness_reassertions": 2,
                        "publisher_liveliness_lost_total_count": 2,
                        "publisher_liveliness_lost_callback_events": 2,
                        "clean_teardown": True,
                    }
                ),
                encoding="utf-8",
            )
            remote_liveliness_multi = (
                root
                / "docker_remote_liveliness_multi_endpoint_probe_summary.json"
            )
            remote_liveliness_multi.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_remote_liveliness_multi_endpoint_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "real_udp_multicontainer": True,
                        "netem_applied": True,
                        "liveliness_policy": "MANUAL_BY_TOPIC",
                        "liveliness_lease_ms": 500,
                        "graph_renew_interval_ms": 100,
                        "remote_liveliness_multi_endpoint_independence_claim": True,
                        "remote_liveliness_alive_not_alive_remove_claim": True,
                        "remote_liveliness_endpoint_churn_recreate_claim": True,
                        "remote_liveliness_expiry_preserves_matching_claim": True,
                        "remote_liveliness_multi_endpoint_repeated_claim": True,
                        "publishers_during_single_endpoint_expiry": 2,
                        "publishers_after_churn": 0,
                        "assertions_received": 11,
                        "manual_liveliness_expiries": 2,
                        "manual_liveliness_reassertions": 1,
                        "clean_teardown": True,
                    }
                ),
                encoding="utf-8",
            )
            liveliness_incompatible = (
                root
                / "docker_qos_liveliness_incompatible_event_probe_summary.json"
            )
            liveliness_incompatible.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_qos_liveliness_incompatible_event_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "qos_liveliness_incompatible_event_production_claim": True,
                        "qos_liveliness_incompatible_event_repeated_claim": True,
                        "liveliness_kind_offered_event_claim": True,
                        "liveliness_kind_requested_event_claim": True,
                        "liveliness_slow_lease_offered_event_claim": True,
                        "liveliness_slow_lease_requested_event_claim": True,
                        "liveliness_missing_lease_offered_event_claim": True,
                        "liveliness_missing_lease_requested_event_claim": True,
                        "liveliness_compatible_control_claim": True,
                        "scenario_count": 7,
                        "incompatible_event_count": 6,
                        "last_policy_kind": 8,
                        "callback_events": 6,
                        "clean_teardown": True,
                    }
                ),
                encoding="utf-8",
            )
            best_available = root / "docker_qos_best_available_probe_summary.json"
            best_available.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_qos_best_available_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "qos_best_available_endpoint_adaptation_claim": True,
                        "qos_best_available_endpoint_adaptation_repeated_claim": True,
                        "best_publisher_manual_selection_claim": True,
                        "best_subscription_automatic_selection_claim": True,
                        "zero_endpoint_best_available_defaults_claim": True,
                        "mixed_publishers_automatic_max_lease_claim": True,
                        "best_available_policy_frozen_after_create_claim": True,
                        "publisher_selected_lease_ms": 200,
                        "subscription_selected_lease_ms": 300,
                        "mixed_selected_lease_ms": 500,
                        "zero_publisher_liveliness_policy": 1,
                        "zero_subscription_liveliness_policy": 1,
                        "scenario_count": 4,
                        "clean_teardown": True,
                    }
                ),
                encoding="utf-8",
            )
            liveliness_scale = root / "docker_liveliness_scale_probe_summary.json"
            liveliness_scale.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_liveliness_scale_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "liveliness_manual_multi_endpoint_scale_claim": True,
                        "liveliness_system_default_automatic_renewal_claim": True,
                        "liveliness_scale_repeated_claim": True,
                        "manual_multi_endpoint_scale_claim": True,
                        "system_default_automatic_renewal_claim": True,
                        "manual_publisher_count": 64,
                        "kept_alive_publisher_count": 32,
                        "half_expired_alive_count": 32,
                        "half_expired_not_alive_count": 32,
                        "all_expired_not_alive_count": 64,
                        "system_default_publisher_count": 16,
                        "system_default_idle_lease_multiples": 6,
                        "system_default_idle_alive_count": 16,
                        "keepalive_assertions": 224,
                        "callback_events": 10432,
                        "clean_teardown": True,
                    }
                ),
                encoding="utf-8",
            )
            remote_liveliness_scale = (
                root / "docker_remote_liveliness_scale_probe_summary.json"
            )
            remote_liveliness_scale.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_remote_liveliness_scale_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "real_udp_multicontainer": True,
                        "netem_applied": True,
                        "liveliness_policy": "MANUAL_BY_TOPIC",
                        "liveliness_lease_ms": 1000,
                        "graph_renew_interval_ms": 100,
                        "remote_liveliness_64_endpoint_scale_claim": True,
                        "remote_liveliness_exact_aggregate_transition_claim": True,
                        "remote_liveliness_scale_repeated_claim": True,
                        "remote_manual_64_endpoint_scale_claim": True,
                        "exact_aggregate_transition_claim": True,
                        "expiry_preserves_matching_claim": True,
                        "publisher_count": 64,
                        "kept_alive_publisher_count": 32,
                        "publishers_during_half_expiry": 64,
                        "publishers_after_remove": 0,
                        "connected_alive_change_sum": 64,
                        "half_expired_alive_change_sum": -32,
                        "half_expired_not_alive_change_sum": 32,
                        "reasserted_alive_change_sum": 32,
                        "all_expired_alive_change_sum": -64,
                        "all_expired_not_alive_change_sum": 64,
                        "removed_not_alive_change_sum": -64,
                        "matched_change_sum": 64,
                        "unmatched_change_sum": -64,
                        "assertions_received": 640,
                        "manual_liveliness_expiries": 96,
                        "manual_liveliness_reassertions": 32,
                        "clean_teardown": True,
                    }
                ),
                encoding="utf-8",
            )
            liveliness_default_lease = (
                root / "docker_liveliness_default_lease_probe_summary.json"
            )
            liveliness_default_lease.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_liveliness_default_lease_probe.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "liveliness_default_lease_lifecycle_claim": True,
                        "liveliness_unresolved_policy_fail_closed_claim": True,
                        "liveliness_default_lease_repeated_claim": True,
                        "non_expiring_liveliness_lifecycle_claim": True,
                        "system_default_infinite_lease_lifecycle_claim": True,
                        "automatic_infinite_lease_lifecycle_claim": True,
                        "manual_infinite_lease_lifecycle_claim": True,
                        "best_available_infinite_lease_lifecycle_claim": True,
                        "unknown_liveliness_fail_closed_claim": True,
                        "manual_by_node_infinite_lease_lifecycle_claim": True,
                        "scenario_count": 6,
                        "clean_teardown": True,
                    }
                ),
                encoding="utf-8",
            )
            allocation = root / "docker_allocation_probe_summary.json"
            allocation.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_allocation_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "allocation_abi_supported": True,
                        "deep_preallocation": False,
                        "allocation_repeated_lifecycle_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            qos_event = root / "docker_qos_event_probe_summary.json"
            qos_event.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_qos_event_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "qos_event_object_abi_supported": True,
                        "event_production": True,
                        "deadline_event_production_scope": (
                            "timer_idle_and_next_publish_or_receive_after_gap"
                        ),
                        "wait_event_readiness": True,
                        "wait_event_readiness_scope": "deadline_status_unread_count",
                        "timer_driven_idle_deadline_events": True,
                        "timer_driven_idle_deadline_scope": (
                            "after_first_publish_or_receive"
                        ),
                        "qos_event_repeated_deadline_waitable_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            qos_event_waitability = (
                root / "docker_qos_event_waitability_matrix_summary.json"
            )
            qos_event_waitability.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "fleetrmw.docker_qos_event_waitability_matrix.v1"
                        ),
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "component_count": 7,
                        "component_execution_count": 35,
                        "event_type_count": 11,
                        "event_types_covered": [
                            "RMW_EVENT_LIVELINESS_CHANGED",
                            "RMW_EVENT_REQUESTED_DEADLINE_MISSED",
                            "RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE",
                            "RMW_EVENT_MESSAGE_LOST",
                            "RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE",
                            "RMW_EVENT_SUBSCRIPTION_MATCHED",
                            "RMW_EVENT_LIVELINESS_LOST",
                            "RMW_EVENT_OFFERED_DEADLINE_MISSED",
                            "RMW_EVENT_OFFERED_QOS_INCOMPATIBLE",
                            "RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE",
                            "RMW_EVENT_PUBLICATION_MATCHED",
                        ],
                        "waitability_scope": (
                            "all_11_jazzy_rmw_event_types_local_production_wait_take"
                        ),
                        "qos_event_waitability_matrix_claim": True,
                        "full_qos_event_waitable_readiness_claim": True,
                        "qos_event_waitability_repeated_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            content_filter = root / "docker_content_filter_probe_summary.json"
            content_filter.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_content_filter_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "content_filter_set_get_abi_supported": True,
                        "filter_enforcement": True,
                        "content_filter_enforcement_scope": (
                            "key_value_payload_and_std_msgs_string_text"
                        ),
                        "raw_content_filter_enforcement": True,
                        "std_msgs_content_filter_enforcement": True,
                        "disabled_content_filter_bypass": True,
                        "content_filters_set_delta": 3,
                        "content_filters_got_delta": 3,
                        "raw_content_filters_evaluated_delta": 3,
                        "raw_content_filters_matched_delta": 1,
                        "raw_content_filters_dropped_delta": 2,
                        "std_msgs_content_filters_evaluated_delta": 4,
                        "std_msgs_content_filters_matched_delta": 1,
                        "std_msgs_content_filters_dropped_delta": 3,
                        "disabled_content_filters_evaluated_delta": 0,
                        "disabled_content_filters_matched_delta": 0,
                        "disabled_content_filters_dropped_delta": 0,
                        "content_filters_evaluated_delta": 7,
                        "content_filters_matched_delta": 2,
                        "content_filters_dropped_delta": 5,
                        "content_filter_repeated_enforcement_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            security_options = root / "docker_security_options_probe_summary.json"
            security_options.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_security_options_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "security_options_lifecycle_abi_supported": True,
                        "default_enclave_initialized": True,
                        "custom_enclave_configured": True,
                        "init_options_copy_preserves_enclave": True,
                        "init_options_copy_deep_copies_enclave": True,
                        "context_init_copies_security_options": True,
                        "context_shutdown_fini_ok": True,
                        "ros2_cli_available": True,
                        "sros2_cli_available": True,
                        "openssl_available": True,
                        "security_policy_enforcement_executed": False,
                        "security_policy_enforcement_gap_reason": (
                            "full_sros2_policy_enforcement_not_implemented"
                        ),
                        "security_hardening_blocker": (
                            "full_sros2_policy_enforcement_not_implemented"
                        ),
                        "security_gap_next_step": (
                            "extend scoped signed authorization with remote peer "
                            "authentication, transport security, and revocation checks"
                        ),
                        "sros2_policy_enforcement_scope": "not_executed_lifecycle_only",
                        "sros2_policy_enforcement_claim": False,
                        "production_security_hardening_claim": False,
                        "security_options_repeated_lifecycle_claim": True,
                    }
                ),
                encoding="utf-8",
            )
            security_policy = root / "docker_security_policy_probe_summary.json"
            security_policy.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_security_policy_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "policy_configured": True,
                        "fleetqox_security_policy_enforcement_claim": True,
                        "security_policy_enforcement_scope": (
                            "fleetqox_publish_allow_deny_env_policy"
                        ),
                        "security_policy_repeated_enforcement_claim": True,
                        "allowed_publish_returncode": 0,
                        "allowed_taken": True,
                        "denied_publish_returncode": 1,
                        "denied_taken": False,
                        "security_policy_denied_delta": 1,
                        "sros2_policy_enforcement_claim": False,
                        "production_security_hardening_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            sros2_permissions = root / "docker_sros2_permissions_probe_summary.json"
            sros2_permissions.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_sros2_permissions_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "service_ok_run_count": 5,
                        "action_ok_run_count": 5,
                        "governance_ok_run_count": 5,
                        "identity_ok_run_count": 5,
                        "sros2_cli_generated_artifacts": True,
                        "signed_permissions_verified_preflight": True,
                        "permissions_xsd_validated": True,
                        "governance_xsd_validated": True,
                        "signed_governance_verified_preflight": True,
                        "tampered_signed_governance_created": True,
                        "identity_negative_controls_created": True,
                        "permissions_xml_loaded": True,
                        "allowed_publish_returncode": 0,
                        "allowed_taken": True,
                        "explicit_denied_publish_returncode": 1,
                        "explicit_denied_taken": False,
                        "default_denied_publish_returncode": 1,
                        "default_denied_taken": False,
                        "subscribe_denied_publish_returncode": 0,
                        "subscribe_default_denied_publish_returncode": 0,
                        "subscribe_decisions_ready": True,
                        "subscribe_denied_taken": False,
                        "subscribe_default_denied_taken": False,
                        "security_policy_denied_delta": 2,
                        "sros2_permissions_xml_allowed_delta": 3,
                        "sros2_permissions_xml_denied_delta": 2,
                        "sros2_permissions_xml_parse_errors_delta": 0,
                        "sros2_permissions_xml_subscribe_allowed_delta": 1,
                        "sros2_permissions_xml_subscribe_denied_delta": 2,
                        "malformed_permissions_fail_closed_control": True,
                        "runtime_permissions_signature_validation": True,
                        "runtime_sros2_permissions_signature_validation_claim": True,
                        "tampered_signed_permissions_created": True,
                        "tampered_signed_permissions_fail_closed_control": True,
                        "security_policy_enforcement_executed": True,
                        "sros2_permissions_xml_publish_enforcement_claim": True,
                        "sros2_permissions_xml_subscribe_enforcement_claim": True,
                        "sros2_permissions_xml_pubsub_enforcement_claim": True,
                        "sros2_permissions_xml_repeated_enforcement_claim": True,
                        "sros2_permissions_xml_subscribe_repeated_enforcement_claim": True,
                        "malformed_permissions_fail_closed_claim": True,
                        "tampered_signed_permissions_fail_closed_claim": True,
                        "sros2_service_request_reply_authorization_claim": True,
                        "sros2_service_repeated_authorization_claim": True,
                        "sros2_action_authorization_claim": True,
                        "sros2_action_repeated_authorization_claim": True,
                        "sros2_action_allowed_end_to_end_claim": True,
                        "sros2_action_call_denied_fail_closed_claim": True,
                        "sros2_action_execute_denied_fail_closed_claim": True,
                        "sros2_action_call_execute_decision_matrix_claim": True,
                        "sros2_action_authorization_metrics_claim": True,
                        "action_call_denied_request_publish_denied_delta": 1,
                        "action_execute_denied_request_subscribe_denied_delta": 1,
                        "sros2_governance_access_control_claim": True,
                        "sros2_governance_repeated_access_control_claim": True,
                        "sros2_governance_runtime_signature_validation_claim": True,
                        "sros2_governance_transport_protection_fail_closed_claim": True,
                        "sros2_tampered_signed_governance_fail_closed_claim": True,
                        "governance_uncontrolled_publish_returncode": 0,
                        "governance_uncontrolled_taken": True,
                        "sros2_local_identity_credentials_validation_claim": True,
                        "sros2_local_identity_credentials_repeated_validation_claim": True,
                        "sros2_tampered_identity_certificate_fail_closed_claim": True,
                        "sros2_identity_private_key_mismatch_fail_closed_claim": True,
                        "sros2_identity_enclave_mismatch_fail_closed_claim": True,
                        "sros2_peer_identity_authentication_claim": False,
                        "allowed_service_request_returncode": 0,
                        "allowed_service_request_taken": True,
                        "allowed_service_response_returncode": 0,
                        "allowed_service_response_taken": True,
                        "request_denied_service_returncode": 1,
                        "default_denied_service_returncode": 1,
                        "reply_denied_service_request_taken": False,
                        "reply_denied_service_response_returncode": 1,
                        "service_request_publish_allowed_delta": 2,
                        "service_request_publish_denied_delta": 2,
                        "service_request_subscribe_allowed_delta": 1,
                        "service_request_subscribe_denied_delta": 1,
                        "service_response_publish_allowed_delta": 1,
                        "service_response_publish_denied_delta": 1,
                        "service_response_subscribe_allowed_delta": 1,
                        "service_response_subscribe_denied_delta": 0,
                        "sros2_permissions_xml_scope": (
                            "sros2_generated_signed_permissions_runtime_ca_validation_then_"
                            "grant_enclave_domain_validity_publish_subscribe_service_action_"
                            "default_runtime_enforcement"
                        ),
                        "sros2_policy_enforcement_scope": (
                            "signed_permissions_and_governance_access_control_subset"
                        ),
                        "sros2_policy_enforcement_claim": False,
                        "governance_xml_enforcement_claim": True,
                        "governance_transport_security_claim": False,
                        "production_security_hardening_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            udp_aead = root / "docker_udp_aead_probe_summary.json"
            udp_aead.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_udp_aead_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "udp_aead_cipher": "AES-256-GCM",
                        "udp_aead_key_management": "pre_shared_test_key",
                        "udp_aead_authenticated_encryption_claim": True,
                        "udp_aead_repeated_authenticated_encryption_claim": True,
                        "udp_aead_tamper_fail_closed_claim": True,
                        "udp_aead_strict_missing_key_fail_closed_claim": True,
                        "udp_authenticated_psk_session_key_derivation_claim": True,
                        "udp_session_key_rotation_claim": True,
                        "session_key_establishment_claim": True,
                        "forward_secrecy_claim": False,
                        "asymmetric_session_key_exchange_claim": False,
                        "session_keys_derived_delta": 2,
                        "session_key_rotations_delta": 1,
                        "encrypted_frames_delta": 2,
                        "decrypted_frames_delta": 2,
                        "authentication_failures_delta": 1,
                        "sros2_peer_identity_authentication_claim": False,
                        "dds_security_interoperability_claim": False,
                        "production_security_hardening_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            udp_peer_auth = root / "docker_udp_peer_auth_probe_summary.json"
            udp_peer_auth.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_udp_peer_auth_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "peer_auth_scheme": (
                            "SROS2 X.509 CA + SHA-256 certificate signature"
                        ),
                        "payload_protection": "AES-256-GCM PSK",
                        "sros2_peer_identity_authentication_claim": True,
                        "sros2_peer_identity_repeated_authentication_claim": True,
                        "udp_peer_identity_allowlist_fail_closed_claim": True,
                        "udp_peer_signature_tamper_fail_closed_claim": True,
                        "udp_peer_untrusted_certificate_fail_closed_claim": True,
                        "udp_certificate_authenticated_aead_claim": True,
                        "session_key_establishment_claim": False,
                        "certificate_revocation_claim": True,
                        "dds_security_interoperability_claim": False,
                        "production_security_hardening_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            dynamic_message = root / "docker_dynamic_message_probe_summary.json"
            dynamic_message.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.docker_dynamic_message_probe.v1",
                        "status": "ok",
                        "run_count": 5,
                        "ok_run_count": 5,
                        "serialization_library": (
                            "rosidl_dynamic_typesupport_fastrtps"
                        ),
                        "dynamic_serialization_support_claim": True,
                        "dynamic_serialization_support_repeated_claim": True,
                        "dynamic_message_take_claim": True,
                        "dynamic_message_take_with_info_claim": True,
                        "message_info_sequence_features_claim": True,
                        "dds_independent_core_transport_claim": True,
                        "dynamic_serialization_plugin_scope": (
                            "optional_rosidl_dynamic_typesupport_fastrtps_plugin"
                        ),
                    }
                ),
                encoding="utf-8",
            )
            omnetpp = root / "omnetpp_template_integrity_probe_summary.json"
            omnetpp.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetqox.omnetpp_template_integrity_probe.v1",
                        "status": "ok",
                        "template_files_present": True,
                        "template_tokens_present": True,
                        "omnetpp_scenario_count": 2,
                        "prepared_trace_count": 2,
                        "total_packet_rows": 1362218,
                        "omnetpp_runtime_ready": False,
                        "omnetpp_runtime_required": False,
                        "omnetpp_runtime_executed": False,
                        "omnetpp_missing_runtime_commands": ["opp_run", "nedtool"],
                        "omnetpp_runtime_gap_reason": (
                            "omnetpp_runtime_commands_missing"
                        ),
                        "omnetpp_runtime_gap_next_step": (
                            "install OMNeT++ and INET, then put opp_run and nedtool on PATH"
                        ),
                        "omnetpp_parity_blocker": "omnetpp_runtime_commands_missing",
                        "omnetpp_template_integrity_claim": True,
                        "omnetpp_input_trace_claim": True,
                        "omnetpp_inet_runtime_claim": False,
                        "omnetpp_parity_claim": False,
                        "ns3_ready": True,
                        "ns3_omnetpp_parity_scope": (
                            "template_input_only_no_runtime_comparison"
                        ),
                        "ns3_omnetpp_parity_claim": False,
                    }
                ),
                encoding="utf-8",
            )
            failed = root / "failed_summary.json"
            failed.write_text(
                json.dumps(
                    {
                        "schema_version": "fleetrmw.example.v1",
                        "status": "failed",
                        "stage": "probe",
                    }
                ),
                encoding="utf-8",
            )

            report = build_unified_benchmark_report(
                root=root,
                artifact_paths=[
                    quic,
                    quic_rmw_take,
                    quic_rmw_take_session,
                    quic_bidirectional,
                    quic_no_0rtt,
                    nav2_rmf_concurrency8,
                    nav2_rmf_concurrency16,
                    nav2_rmf_concurrency32,
                    nav2_rmf_concurrency64,
                    nav2_rmf_concurrency128,
                    nav2_rmf_concurrency256,
                    nav2_rmf_concurrency512,
                    nav2_rmf_concurrency1024,
                    nav2_rmf_concurrency2048,
                    nav2_rmf_concurrency4096,
                    nav2_rmf_total4096_goalbatch8,
                    nav2_pc,
                    nav2_activation,
                    nav2_compute_path,
                    nav2_follow_path,
                    nav2_navigate,
                    nav2_repeated,
                    nav2_moving,
                    nav2_extended_moving,
                    nav2_long_moving,
                    nav2_obstacle_repair,
                    nav2_obstacle_retry,
                    nav2_autonomous_obstacle,
                    nav2_spin,
                    nav2_recovery_tree,
                    nav2_recovered_success,
                    nav2_recovered_success_repeated,
                    matched_event,
                    remote_event,
                    qos_incompatible,
                    qos_deadline_incompatible,
                    remote_deadline,
                    type_incompatible,
                    message_lost,
                    message_lost_interprocess,
                    message_lost_terminal_repair,
                    liveliness,
                    automatic_liveliness,
                    remote_manual_liveliness,
                    remote_liveliness_multi,
                    liveliness_incompatible,
                    best_available,
                    liveliness_scale,
                    remote_liveliness_scale,
                    liveliness_default_lease,
                    allocation,
                    qos_event,
                    qos_event_waitability,
                    content_filter,
                    security_options,
                    security_policy,
                    sros2_permissions,
                    udp_aead,
                    udp_peer_auth,
                    dynamic_message,
                    stress_security,
                    omnetpp,
                    failed,
                ],
                capability_path=capabilities,
                patterns=["*_summary.json"],
            )
            markdown = render_unified_benchmark_markdown(report)

            self.assertEqual(
                report["schema_version"],
                "fleetrmw.unified_benchmark_report.v1",
            )
            self.assertEqual(report["status"], "partial")
            self.assertEqual(report["artifact_history_status"], "partial")
            self.assertEqual(
                report["artifact_status_scope"],
                "all_discovered_artifacts_including_retained_historical_runs",
            )
            self.assertEqual(report["capability_manifest_status"], "ok")
            self.assertGreater(report["claim_boundary_true_count"], 0)
            self.assertGreater(report["claim_boundary_false_count"], 0)
            self.assertEqual(report["artifact_count"], 63)
            self.assertEqual(report["ok_artifact_count"], 62)
            self.assertEqual(report["failed_artifact_count"], 1)
            self.assertFalse(
                report["claim_boundary_summary"]["production_quic_backend_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_quic_gateway_async_burst_soak_smoke"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_quic_gateway_async_burst_soak_3run_netem"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_quic_gateway_async_burst_soak_10run_netem"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_stress_security_campaign_smoke"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["stress_security_repeated_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "long_stress_security_campaign_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_ngtcp2_quic_gateway_take_path_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_ngtcp2_quic_gateway_rmw_take_path_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_ngtcp2_quic_gateway_rmw_take_session_reuse_file_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_ngtcp2_quic_gateway_rmw_take_session_reuse_5download_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_ngtcp2_quic_gateway_bidirectional_publish_take_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_ngtcp2_quic_gateway_bidirectional_publish_take_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_quic_gateway_disable_early_data_control"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_planner_controller_lifecycle_configure"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_planner_controller_lifecycle_activate_dynamic_tf"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_planner_compute_path_action_map_tf"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_controller_follow_path_action_map_tf_odom"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_navigate_to_pose_same_pose_bt_pipeline"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_navigate_to_pose_repeated_same_pose_bt_pipeline"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_navigate_to_pose_moving_base_bt_pipeline"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_navigate_to_pose_extended_moving_base_bt_pipeline"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_behavior_server_spin_action"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_navigate_to_pose_recovery_tree_fallback"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_navigate_to_pose_recovered_success_after_spin"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_navigate_to_pose_recovered_success_repeated_smoke"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_navigate_to_pose_long_moving_workload"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_concurrency8"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_concurrency16"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_concurrency32"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_concurrency64"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_concurrency128"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_concurrency256"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_concurrency512"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_concurrency1024"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_concurrency2048"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_concurrency4096"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "nav2_rmf_unwindowed_4096_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_rmf_upstream_total4096_admission_window8"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["full_nav2_navigation_stack_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "nav2_rmf_larger_upstream_client_count_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "nav2_rmf_total4096_admission_window_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["moving_robot_navigation_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["extended_moving_navigation_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["nav2_recovery_behavior_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["navigate_to_pose_recovery_tree_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["successful_recovered_navigation_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["repeated_recovered_navigation_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_planner_static_obstacle_repair"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_navigate_to_pose_obstacle_retry_after_clear"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_nav2_navigate_to_pose_autonomous_same_goal_obstacle_recovery"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "planner_static_obstacle_repair_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "nav2_obstacle_retry_after_clear_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["obstacle_field_recovery_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["full_nav2_obstacle_recovery_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "autonomous_same_goal_nav2_obstacle_recovery_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["long_navigation_workload_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_matched_event_production"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["docker_qos_matched_event_5run_probe"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["qos_matched_event_production_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_remote_graph_matched_qos_type_liveliness_event_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["remote_graph_matched_event_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["remote_graph_incompatible_qos_event_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["remote_graph_incompatible_type_event_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["remote_graph_liveliness_lifecycle_event_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["remote_graph_renewal_deduplication_claim"]
            )
            self.assertFalse(
                report["claim_boundary_summary"]["full_remote_graph_event_production_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_reliability_incompatible_event_production"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_reliability_durability_incompatible_event_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_type_incompatible_event_production"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_type_incompatible_event_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_durability_incompatible_event_production"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_deadline_incompatible_event_production"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_deadline_incompatible_event_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "qos_missing_offered_deadline_incompatible_event_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "qos_missing_offered_deadline_incompatible_repeated_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_offered_deadline_missed_event_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_requested_deadline_missed_event_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_deadline_missed_event_repeated_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_message_lost_event_production"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_message_lost_event_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "qos_message_lost_best_effort_sequence_gap_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "qos_message_lost_repair_reorder_suppression_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "qos_message_lost_reliable_history_exhaustion_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_unrecoverable_loss_notice_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["remote_message_lost_waitable_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "duplicate_unrecoverable_loss_notice_deduplication_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["repeated_remote_message_lost_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "repair_budget_terminal_loss_notice_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "repair_attempt_limit_terminal_loss_notice_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "repair_admission_terminal_loss_notice_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "terminal_repair_duplicate_notice_deduplication_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["terminal_repair_clean_teardown_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "terminal_repair_controls_repeated_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_liveliness_event_production"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["docker_qos_liveliness_event_5run_probe"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "automatic_liveliness_idle_renewal_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "automatic_liveliness_false_loss_suppression_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "automatic_liveliness_repeated_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_manual_liveliness_idle_timeout_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_manual_liveliness_explicit_assert_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_manual_liveliness_publish_assert_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_publisher_liveliness_lost_event_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_manual_liveliness_graph_lease_independence_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_manual_liveliness_repeated_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_liveliness_multi_endpoint_independence_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_liveliness_alive_not_alive_remove_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_liveliness_endpoint_churn_recreate_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_liveliness_expiry_preserves_matching_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_liveliness_multi_endpoint_repeated_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "liveliness_manual_multi_endpoint_scale_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "liveliness_system_default_automatic_renewal_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["liveliness_scale_repeated_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_liveliness_64_endpoint_scale_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_liveliness_exact_aggregate_transition_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "remote_liveliness_scale_repeated_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "liveliness_default_lease_lifecycle_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "liveliness_unresolved_policy_fail_closed_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "liveliness_default_lease_repeated_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "qos_liveliness_incompatible_event_production_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "qos_liveliness_incompatible_event_repeated_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "liveliness_missing_lease_requested_event_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "qos_best_available_endpoint_adaptation_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "best_available_policy_frozen_after_create_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_publisher_subscription_payload_scratch_allocation"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_publisher_subscription_payload_scratch_allocation_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "publisher_subscription_payload_scratch_reuse_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_event_deadline_waitable_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_qos_event_waitability_matrix_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "qos_event_waitability_matrix_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "qos_event_waitability_repeated_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "full_qos_event_waitable_readiness_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_content_filter_std_msgs_string_text_enforcement"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_content_filter_dynamic_reconfigure_disable"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_content_filter_repeated_enforcement_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["omnetpp_template_integrity_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["omnetpp_input_trace_claim"]
            )
            self.assertFalse(
                report["claim_boundary_summary"]["omnetpp_inet_runtime_claim"]
            )
            self.assertFalse(
                report["claim_boundary_summary"]["omnetpp_parity_claim"]
            )
            self.assertFalse(
                report["claim_boundary_summary"]["ns3_omnetpp_parity_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["docker_security_options_lifecycle_probe"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_security_options_lifecycle_5run_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["security_options_lifecycle_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_fleetqox_security_policy_enforcement_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "fleetqox_security_policy_enforcement_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "security_policy_repeated_enforcement_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "docker_sros2_permissions_xml_publish_enforcement_probe"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_permissions_xml_publish_enforcement_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_permissions_xml_subscribe_enforcement_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_permissions_xml_pubsub_enforcement_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_permissions_xml_repeated_enforcement_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_permissions_xml_subscribe_repeated_enforcement_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "malformed_permissions_fail_closed_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "runtime_sros2_permissions_signature_validation_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "tampered_signed_permissions_fail_closed_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_service_request_reply_authorization_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_service_repeated_authorization_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_action_authorization_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_action_repeated_authorization_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "governance_xml_enforcement_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_governance_repeated_access_control_claim"
                ]
            )
            self.assertFalse(
                report["claim_boundary_summary"][
                    "governance_transport_security_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_local_identity_credentials_validation_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_local_identity_credentials_repeated_validation_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_peer_identity_authentication_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "sros2_peer_identity_repeated_authentication_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["session_key_establishment_claim"]
            )
            self.assertFalse(
                report["claim_boundary_summary"]["forward_secrecy_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["certificate_revocation_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "dynamic_serialization_support_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"]["dynamic_message_take_claim"]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "message_info_sequence_features_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "udp_aead_authenticated_encryption_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "udp_aead_repeated_authenticated_encryption_claim"
                ]
            )
            self.assertTrue(
                report["claim_boundary_summary"][
                    "udp_aead_strict_missing_key_fail_closed_claim"
                ]
            )
            self.assertFalse(
                report["claim_boundary_summary"][
                    "dds_security_interoperability_claim"
                ]
            )
            self.assertFalse(
                report["claim_boundary_summary"]["sros2_policy_enforcement_claim"]
            )
            self.assertFalse(
                report["claim_boundary_summary"]["production_security_hardening_claim"]
            )
            matched_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_matched_event_probe.v1"
            )
            self.assertEqual(matched_artifact["category"], "rmw-abi")
            self.assertEqual(matched_artifact["run_count"], 5)
            self.assertEqual(matched_artifact["ok_run_count"], 5)
            self.assertTrue(matched_artifact["metrics"]["matched_event_production"])
            self.assertTrue(matched_artifact["metrics"]["matched_event_repeated_claim"])
            remote_event_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_remote_event_probe.v1"
            )
            self.assertEqual(remote_event_artifact["category"], "rmw-abi")
            self.assertEqual(remote_event_artifact["run_count"], 5)
            self.assertEqual(remote_event_artifact["ok_run_count"], 5)
            self.assertTrue(
                remote_event_artifact["metrics"]["remote_matched_event_production"]
            )
            self.assertTrue(remote_event_artifact["metrics"]["remote_qos_event_production"])
            self.assertEqual(
                remote_event_artifact["metrics"]["remote_qos_policy_matrix"],
                ["reliability", "durability", "deadline"],
            )
            self.assertTrue(remote_event_artifact["metrics"]["remote_type_event_production"])
            self.assertTrue(
                remote_event_artifact["metrics"]["remote_liveliness_event_production"]
            )
            self.assertTrue(remote_event_artifact["metrics"]["renewal_deduplication"])
            self.assertTrue(remote_event_artifact["metrics"]["real_udp_multicontainer"])
            self.assertEqual(remote_event_artifact["metrics"]["explicit_remove_run_count"], 3)
            self.assertEqual(remote_event_artifact["metrics"]["lease_expiry_run_count"], 2)
            type_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_type_incompatible_event_probe.v1"
            )
            self.assertEqual(type_artifact["category"], "rmw-abi")
            self.assertEqual(type_artifact["run_count"], 5)
            self.assertEqual(type_artifact["ok_run_count"], 5)
            self.assertTrue(
                type_artifact["metrics"]["type_incompatible_event_production"]
            )
            self.assertTrue(
                type_artifact["metrics"]["type_incompatible_repeated_event_claim"]
            )
            self.assertEqual(type_artifact["metrics"]["publisher_total_count"], 1)
            qos_incompatible_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_qos_incompatible_event_probe.v1"
            )
            self.assertEqual(qos_incompatible_artifact["category"], "rmw-abi")
            self.assertEqual(qos_incompatible_artifact["run_count"], 5)
            self.assertEqual(qos_incompatible_artifact["ok_run_count"], 5)
            self.assertTrue(
                qos_incompatible_artifact["metrics"]["qos_incompatible_event_production"]
            )
            self.assertTrue(
                qos_incompatible_artifact["metrics"][
                    "qos_incompatible_repeated_event_claim"
                ]
            )
            self.assertEqual(
                qos_incompatible_artifact["metrics"]["durability_offered_last_policy_kind"],
                2,
            )
            qos_deadline_incompatible_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_qos_deadline_incompatible_event_probe.v1"
            )
            self.assertEqual(qos_deadline_incompatible_artifact["category"], "rmw-abi")
            self.assertEqual(qos_deadline_incompatible_artifact["run_count"], 5)
            self.assertEqual(qos_deadline_incompatible_artifact["ok_run_count"], 5)
            self.assertTrue(
                qos_deadline_incompatible_artifact["metrics"][
                    "qos_deadline_incompatible_event_production"
                ]
            )
            self.assertTrue(
                qos_deadline_incompatible_artifact["metrics"][
                    "qos_deadline_incompatible_repeated_event_claim"
                ]
            )
            self.assertTrue(
                qos_deadline_incompatible_artifact["metrics"][
                    "qos_missing_offered_deadline_incompatible_event_claim"
                ]
            )
            self.assertTrue(
                qos_deadline_incompatible_artifact["metrics"][
                    "missing_offered_deadline_requested_event_claim"
                ]
            )
            self.assertEqual(
                qos_deadline_incompatible_artifact["metrics"][
                    "missing_offered_total_count"
                ],
                1,
            )
            remote_deadline_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_remote_deadline_event_probe.v1"
            )
            self.assertEqual(remote_deadline_artifact["category"], "rmw-abi")
            self.assertEqual(remote_deadline_artifact["run_count"], 5)
            self.assertEqual(remote_deadline_artifact["ok_run_count"], 5)
            self.assertTrue(
                remote_deadline_artifact["metrics"][
                    "remote_offered_deadline_missed_event_claim"
                ]
            )
            self.assertTrue(
                remote_deadline_artifact["metrics"][
                    "remote_requested_deadline_missed_event_claim"
                ]
            )
            self.assertEqual(
                remote_deadline_artifact["metrics"]["deadline_ms"], 100
            )
            quic_rmw_take_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_quic_gateway_rmw_take_probe.v1"
            )
            self.assertEqual(quic_rmw_take_artifact["category"], "transport/quic")
            self.assertTrue(
                quic_rmw_take_artifact["metrics"]["rmw_take_path_integrated"]
            )
            self.assertTrue(quic_rmw_take_artifact["metrics"]["payload_ok"])
            quic_rmw_take_session_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_quic_gateway_rmw_take_session_reuse_probe.v1"
            )
            self.assertTrue(
                quic_rmw_take_session_artifact["metrics"][
                    "session_file_reused_by_multiple_downloads"
                ]
            )
            self.assertEqual(
                quic_rmw_take_session_artifact["metrics"]["download_count"], 5
            )
            self.assertEqual(
                quic_rmw_take_session_artifact["metrics"]["client_handshake_count"], 5
            )
            self.assertEqual(
                quic_rmw_take_session_artifact["metrics"]["qlog_file_count"], 10
            )
            self.assertTrue(
                quic_rmw_take_session_artifact["metrics"][
                    "session_resumption_attempted_observed"
                ]
            )
            self.assertTrue(
                quic_rmw_take_session_artifact["metrics"]["zero_rtt_packet_observed"]
            )
            self.assertFalse(
                quic_rmw_take_session_artifact["metrics"][
                    "zero_rtt_accepted_observed"
                ]
            )
            quic_bidirectional_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_quic_gateway_bidirectional_probe_summary.json"
                )
            )
            self.assertEqual(quic_bidirectional_artifact["category"], "transport/quic")
            self.assertEqual(quic_bidirectional_artifact["run_count"], 5)
            self.assertEqual(quic_bidirectional_artifact["ok_run_count"], 5)
            self.assertTrue(
                quic_bidirectional_artifact["metrics"][
                    "quic_gateway_bidirectional_boundary_claim"
                ]
            )
            self.assertTrue(
                quic_bidirectional_artifact["metrics"][
                    "quic_gateway_bidirectional_repeated_claim"
                ]
            )
            self.assertTrue(
                quic_bidirectional_artifact["metrics"]["rmw_publish_path_integrated"]
            )
            self.assertTrue(
                quic_bidirectional_artifact["metrics"]["rmw_take_path_integrated"]
            )
            self.assertTrue(
                quic_bidirectional_artifact["metrics"][
                    "session_file_reused_by_upload_and_download"
                ]
            )
            self.assertFalse(
                quic_bidirectional_artifact["metrics"]["full_bidirectional_quic_backend"]
            )
            self.assertFalse(
                quic_bidirectional_artifact["metrics"]["production_quic_backend"]
            )
            self.assertEqual(
                quic_bidirectional_artifact["metrics"]["client_handshake_count"],
                10,
            )
            self.assertEqual(
                quic_bidirectional_artifact["metrics"]["qlog_file_count"],
                20,
            )
            self.assertTrue(
                quic_bidirectional_artifact["metrics"]["zero_rtt_packet_observed"]
            )
            self.assertFalse(
                quic_bidirectional_artifact["metrics"]["zero_rtt_accepted_observed"]
            )
            quic_no_0rtt_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_quic_gateway_bidirectional_no_0rtt_probe_summary.json"
                )
            )
            self.assertEqual(quic_no_0rtt_artifact["category"], "transport/quic")
            self.assertTrue(quic_no_0rtt_artifact["metrics"]["early_data_disabled"])
            self.assertFalse(
                quic_no_0rtt_artifact["metrics"]["zero_rtt_packet_observed"]
            )
            self.assertTrue(
                quic_no_0rtt_artifact["metrics"]["zero_rtt_disabled_control_claim"]
            )
            nav2_pc_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_planner_controller_lifecycle_probe.v1"
            )
            self.assertEqual(nav2_pc_artifact["category"], "workload/nav2-rmf")
            self.assertTrue(nav2_pc_artifact["metrics"]["planner_configure_transition"])
            self.assertTrue(nav2_pc_artifact["metrics"]["controller_configure_transition"])
            self.assertFalse(nav2_pc_artifact["metrics"]["full_nav2_navigation_stack_claim"])
            nav2_activation_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_planner_controller_activation_probe.v1"
            )
            self.assertEqual(nav2_activation_artifact["category"], "workload/nav2-rmf")
            self.assertTrue(
                nav2_activation_artifact["metrics"]["planner_activate_transition"]
            )
            self.assertTrue(
                nav2_activation_artifact["metrics"]["controller_activate_transition"]
            )
            self.assertTrue(nav2_activation_artifact["metrics"]["tf_topic_forwarded"])
            self.assertFalse(
                nav2_activation_artifact["metrics"]["full_nav2_navigation_stack_claim"]
            )
            nav2_compute_path_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_planner_compute_path_probe.v1"
            )
            self.assertEqual(nav2_compute_path_artifact["category"], "workload/nav2-rmf")
            self.assertTrue(
                nav2_compute_path_artifact["metrics"]["compute_path_goal_succeeded"]
            )
            self.assertEqual(
                nav2_compute_path_artifact["metrics"]["compute_path_error_code"],
                0,
            )
            self.assertEqual(
                nav2_compute_path_artifact["metrics"]["compute_path_path_pose_count"],
                14,
            )
            self.assertTrue(nav2_compute_path_artifact["metrics"]["map_topic_forwarded"])
            self.assertFalse(
                nav2_compute_path_artifact["metrics"]["controller_execution_claim"]
            )
            nav2_follow_path_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_controller_follow_path_probe.v1"
            )
            self.assertEqual(nav2_follow_path_artifact["category"], "workload/nav2-rmf")
            self.assertTrue(
                nav2_follow_path_artifact["metrics"]["follow_path_goal_succeeded"]
            )
            self.assertEqual(
                nav2_follow_path_artifact["metrics"]["follow_path_error_code"],
                0,
            )
            self.assertTrue(nav2_follow_path_artifact["metrics"]["odom_topic_forwarded"])
            self.assertTrue(
                nav2_follow_path_artifact["metrics"]["controller_execution_claim"]
            )
            nav2_navigate_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_navigate_to_pose_probe.v1"
            )
            self.assertEqual(nav2_navigate_artifact["category"], "workload/nav2-rmf")
            self.assertTrue(
                nav2_navigate_artifact["metrics"]["navigate_to_pose_goal_succeeded"]
            )
            self.assertEqual(
                nav2_navigate_artifact["metrics"]["navigate_to_pose_error_code"],
                0,
            )
            self.assertTrue(
                nav2_navigate_artifact["metrics"]["bt_navigator_activate_transition"]
            )
            self.assertTrue(
                nav2_navigate_artifact["metrics"]["full_nav2_navigation_stack_claim"]
            )
            self.assertFalse(
                nav2_navigate_artifact["metrics"]["moving_robot_navigation_claim"]
            )
            nav2_repeated_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_navigate_to_pose_repeated_probe.v1"
            )
            self.assertEqual(nav2_repeated_artifact["category"], "workload/nav2-rmf")
            self.assertTrue(
                nav2_repeated_artifact["metrics"]["navigate_to_pose_repeated_smoke"]
            )
            self.assertEqual(
                nav2_repeated_artifact["metrics"][
                    "navigate_to_pose_goal_succeeded_run_count"
                ],
                2,
            )
            self.assertEqual(
                nav2_repeated_artifact["metrics"]["min_service_frames_per_run"],
                54,
            )
            self.assertTrue(
                nav2_repeated_artifact["metrics"]["full_nav2_navigation_stack_claim"]
            )
            self.assertFalse(
                nav2_repeated_artifact["metrics"]["moving_robot_navigation_claim"]
            )
            nav2_moving_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_navigate_to_pose_moving_probe.v1"
            )
            self.assertEqual(nav2_moving_artifact["category"], "workload/nav2-rmf")
            self.assertTrue(
                nav2_moving_artifact["metrics"]["navigate_to_pose_goal_succeeded"]
            )
            self.assertTrue(nav2_moving_artifact["metrics"]["cmd_vel_topic_forwarded"])
            self.assertEqual(
                nav2_moving_artifact["metrics"]["fake_base_cmd_vel_count"],
                4,
            )
            self.assertEqual(
                nav2_moving_artifact["metrics"]["navigation_goal_x"],
                0.6,
            )
            self.assertTrue(
                nav2_moving_artifact["metrics"]["moving_robot_navigation_claim"]
            )
            nav2_extended_moving_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_navigate_to_pose_extended_moving_probe.v1"
            )
            self.assertEqual(
                nav2_extended_moving_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_extended_moving_artifact["metrics"][
                    "navigate_to_pose_goal_succeeded"
                ]
            )
            self.assertEqual(
                nav2_extended_moving_artifact["metrics"]["navigation_goal_x"],
                1.2,
            )
            self.assertGreaterEqual(
                nav2_extended_moving_artifact["metrics"][
                    "fake_base_moved_distance"
                ],
                0.9,
            )
            self.assertTrue(
                nav2_extended_moving_artifact["metrics"][
                    "extended_moving_navigation_claim"
                ]
            )
            self.assertFalse(
                nav2_extended_moving_artifact["metrics"][
                    "long_navigation_workload_claim"
                ]
            )
            nav2_long_moving_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_navigate_to_pose_long_moving_probe.v1"
            )
            self.assertEqual(
                nav2_long_moving_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_long_moving_artifact["metrics"][
                    "navigate_to_pose_long_moving_workload"
                ]
            )
            self.assertEqual(
                nav2_long_moving_artifact["metrics"][
                    "navigate_to_pose_goal_succeeded_run_count"
                ],
                3,
            )
            self.assertEqual(
                nav2_long_moving_artifact["metrics"][
                    "extended_moving_navigation_run_count"
                ],
                3,
            )
            self.assertGreaterEqual(
                nav2_long_moving_artifact["metrics"][
                    "total_fake_base_moved_distance"
                ],
                2.4,
            )
            self.assertTrue(
                nav2_long_moving_artifact["metrics"][
                    "long_navigation_workload_claim"
                ]
            )
            self.assertFalse(
                nav2_long_moving_artifact["metrics"][
                    "obstacle_field_recovery_claim"
                ]
            )
            nav2_obstacle_repair_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_planner_obstacle_repair_probe.v1"
            )
            self.assertEqual(
                nav2_obstacle_repair_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_obstacle_repair_artifact["metrics"][
                    "blocked_compute_path_failed"
                ]
            )
            self.assertEqual(
                nav2_obstacle_repair_artifact["metrics"][
                    "blocked_compute_path_error_code"
                ],
                208,
            )
            self.assertTrue(
                nav2_obstacle_repair_artifact["metrics"][
                    "clear_compute_path_goal_succeeded"
                ]
            )
            self.assertEqual(
                nav2_obstacle_repair_artifact["metrics"][
                    "clear_compute_path_path_pose_count"
                ],
                14,
            )
            self.assertTrue(
                nav2_obstacle_repair_artifact["metrics"][
                    "planner_static_obstacle_repair_claim"
                ]
            )
            self.assertTrue(
                nav2_obstacle_repair_artifact["metrics"][
                    "obstacle_field_recovery_claim"
                ]
            )
            self.assertFalse(
                nav2_obstacle_repair_artifact["metrics"][
                    "full_nav2_obstacle_recovery_claim"
                ]
            )
            nav2_obstacle_retry_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_navigate_to_pose_obstacle_retry_probe.v1"
            )
            self.assertEqual(
                nav2_obstacle_retry_artifact["category"], "workload/nav2-rmf"
            )
            self.assertEqual(
                nav2_obstacle_retry_artifact["metrics"][
                    "blocked_navigate_to_pose_status"
                ],
                "ABORTED",
            )
            self.assertEqual(
                nav2_obstacle_retry_artifact["metrics"][
                    "blocked_navigate_to_pose_error_code"
                ],
                208,
            )
            self.assertTrue(
                nav2_obstacle_retry_artifact["metrics"][
                    "planner_blocked_failure_observed"
                ]
            )
            self.assertEqual(
                nav2_obstacle_retry_artifact["metrics"][
                    "clear_navigate_to_pose_status"
                ],
                "SUCCEEDED",
            )
            self.assertTrue(
                nav2_obstacle_retry_artifact["metrics"][
                    "clear_navigate_to_pose_goal_succeeded"
                ]
            )
            self.assertGreaterEqual(
                nav2_obstacle_retry_artifact["metrics"][
                    "fake_base_moved_distance"
                ],
                0.6,
            )
            self.assertTrue(
                nav2_obstacle_retry_artifact["metrics"][
                    "nav2_obstacle_retry_after_clear_claim"
                ]
            )
            self.assertTrue(
                nav2_obstacle_retry_artifact["metrics"][
                    "full_nav2_obstacle_recovery_claim"
                ]
            )
            self.assertFalse(
                nav2_obstacle_retry_artifact["metrics"][
                    "autonomous_same_goal_nav2_obstacle_recovery_claim"
                ]
            )
            nav2_autonomous_obstacle_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == (
                    "fleetrmw.docker_nav2_navigate_to_pose_autonomous_obstacle_recovery_probe.v1"
                )
            )
            self.assertEqual(
                nav2_autonomous_obstacle_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_autonomous_obstacle_artifact["metrics"][
                    "same_goal_obstacle_recovery_observed"
                ]
            )
            self.assertTrue(
                nav2_autonomous_obstacle_artifact["metrics"][
                    "planner_blocked_failure_observed"
                ]
            )
            self.assertTrue(
                nav2_autonomous_obstacle_artifact["metrics"][
                    "clear_map_published_during_goal"
                ]
            )
            self.assertTrue(
                nav2_autonomous_obstacle_artifact["metrics"]["wait_action_forwarded"]
            )
            self.assertEqual(
                nav2_autonomous_obstacle_artifact["metrics"]["navigate_to_pose_status"],
                "SUCCEEDED",
            )
            self.assertEqual(
                nav2_autonomous_obstacle_artifact["metrics"][
                    "navigate_to_pose_error_code"
                ],
                0,
            )
            self.assertGreaterEqual(
                nav2_autonomous_obstacle_artifact["metrics"][
                    "fake_base_moved_distance"
                ],
                0.6,
            )
            self.assertTrue(
                nav2_autonomous_obstacle_artifact["metrics"][
                    "autonomous_same_goal_nav2_obstacle_recovery_claim"
                ]
            )
            nav2_rmf_concurrency8_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_concurrency8_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_concurrency8_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_rmf_concurrency8_artifact["metrics"]["nav2_upstream"]
            )
            self.assertTrue(
                nav2_rmf_concurrency8_artifact["metrics"]["rmf_upstream"]
            )
            self.assertEqual(
                nav2_rmf_concurrency8_artifact["metrics"]["upstream_concurrency"],
                8,
            )
            self.assertEqual(
                nav2_rmf_concurrency8_artifact["metrics"]["expected_service_frames"],
                106,
            )
            self.assertTrue(
                nav2_rmf_concurrency8_artifact["metrics"]["navigation_batch"]
            )
            self.assertTrue(nav2_rmf_concurrency8_artifact["metrics"]["rmf_batch"])
            nav2_rmf_concurrency16_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_concurrency16_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_concurrency16_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_rmf_concurrency16_artifact["metrics"]["nav2_upstream"]
            )
            self.assertTrue(
                nav2_rmf_concurrency16_artifact["metrics"]["rmf_upstream"]
            )
            self.assertEqual(
                nav2_rmf_concurrency16_artifact["metrics"]["upstream_concurrency"],
                16,
            )
            self.assertEqual(
                nav2_rmf_concurrency16_artifact["metrics"]["expected_service_frames"],
                154,
            )
            self.assertTrue(
                nav2_rmf_concurrency16_artifact["metrics"]["navigation_batch"]
            )
            self.assertTrue(nav2_rmf_concurrency16_artifact["metrics"]["rmf_batch"])
            nav2_rmf_concurrency32_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_concurrency32_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_concurrency32_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_rmf_concurrency32_artifact["metrics"]["nav2_upstream"]
            )
            self.assertTrue(
                nav2_rmf_concurrency32_artifact["metrics"]["rmf_upstream"]
            )
            self.assertEqual(
                nav2_rmf_concurrency32_artifact["metrics"]["upstream_concurrency"],
                32,
            )
            self.assertEqual(
                nav2_rmf_concurrency32_artifact["metrics"]["expected_service_frames"],
                250,
            )
            self.assertTrue(
                nav2_rmf_concurrency32_artifact["metrics"]["navigation_batch"]
            )
            self.assertTrue(nav2_rmf_concurrency32_artifact["metrics"]["rmf_batch"])
            nav2_rmf_concurrency64_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_concurrency64_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_concurrency64_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_rmf_concurrency64_artifact["metrics"]["nav2_upstream"]
            )
            self.assertTrue(
                nav2_rmf_concurrency64_artifact["metrics"]["rmf_upstream"]
            )
            self.assertEqual(
                nav2_rmf_concurrency64_artifact["metrics"]["upstream_concurrency"],
                64,
            )
            self.assertEqual(
                nav2_rmf_concurrency64_artifact["metrics"]["expected_service_frames"],
                442,
            )
            self.assertTrue(
                nav2_rmf_concurrency64_artifact["metrics"]["navigation_batch"]
            )
            self.assertTrue(nav2_rmf_concurrency64_artifact["metrics"]["rmf_batch"])
            nav2_rmf_concurrency128_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_concurrency128_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_concurrency128_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_rmf_concurrency128_artifact["metrics"]["nav2_upstream"]
            )
            self.assertTrue(
                nav2_rmf_concurrency128_artifact["metrics"]["rmf_upstream"]
            )
            self.assertEqual(
                nav2_rmf_concurrency128_artifact["metrics"]["upstream_concurrency"],
                128,
            )
            self.assertEqual(
                nav2_rmf_concurrency128_artifact["metrics"]["expected_service_frames"],
                826,
            )
            self.assertTrue(
                nav2_rmf_concurrency128_artifact["metrics"]["navigation_batch"]
            )
            self.assertTrue(nav2_rmf_concurrency128_artifact["metrics"]["rmf_batch"])
            nav2_rmf_concurrency256_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_concurrency256_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_concurrency256_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_rmf_concurrency256_artifact["metrics"]["nav2_upstream"]
            )
            self.assertTrue(
                nav2_rmf_concurrency256_artifact["metrics"]["rmf_upstream"]
            )
            self.assertEqual(
                nav2_rmf_concurrency256_artifact["metrics"]["upstream_concurrency"],
                256,
            )
            self.assertEqual(
                nav2_rmf_concurrency256_artifact["metrics"]["expected_service_frames"],
                1594,
            )
            self.assertTrue(
                nav2_rmf_concurrency256_artifact["metrics"]["navigation_batch"]
            )
            self.assertTrue(nav2_rmf_concurrency256_artifact["metrics"]["rmf_batch"])
            nav2_rmf_concurrency512_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_concurrency512_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_concurrency512_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_rmf_concurrency512_artifact["metrics"]["nav2_upstream"]
            )
            self.assertTrue(
                nav2_rmf_concurrency512_artifact["metrics"]["rmf_upstream"]
            )
            self.assertEqual(
                nav2_rmf_concurrency512_artifact["metrics"]["upstream_concurrency"],
                512,
            )
            self.assertEqual(
                nav2_rmf_concurrency512_artifact["metrics"]["expected_service_frames"],
                3130,
            )
            self.assertEqual(
                nav2_rmf_concurrency512_artifact["metrics"]["batch_timeout_s"],
                76,
            )
            self.assertEqual(
                nav2_rmf_concurrency512_artifact["metrics"]["router_timeout_ms"],
                232000,
            )
            self.assertTrue(
                nav2_rmf_concurrency512_artifact["metrics"]["navigation_batch"]
            )
            self.assertTrue(nav2_rmf_concurrency512_artifact["metrics"]["rmf_batch"])
            nav2_rmf_concurrency1024_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_concurrency1024_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_concurrency1024_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_rmf_concurrency1024_artifact["metrics"]["nav2_upstream"]
            )
            self.assertTrue(
                nav2_rmf_concurrency1024_artifact["metrics"]["rmf_upstream"]
            )
            self.assertEqual(
                nav2_rmf_concurrency1024_artifact["metrics"]["upstream_concurrency"],
                1024,
            )
            self.assertEqual(
                nav2_rmf_concurrency1024_artifact["metrics"]["expected_service_frames"],
                6202,
            )
            self.assertEqual(
                nav2_rmf_concurrency1024_artifact["metrics"]["batch_timeout_s"],
                120,
            )
            self.assertEqual(
                nav2_rmf_concurrency1024_artifact["metrics"]["router_timeout_ms"],
                320000,
            )
            self.assertTrue(
                nav2_rmf_concurrency1024_artifact["metrics"]["navigation_batch"]
            )
            self.assertTrue(nav2_rmf_concurrency1024_artifact["metrics"]["rmf_batch"])
            nav2_rmf_concurrency2048_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_concurrency2048_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_concurrency2048_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_rmf_concurrency2048_artifact["metrics"]["nav2_upstream"]
            )
            self.assertTrue(
                nav2_rmf_concurrency2048_artifact["metrics"]["rmf_upstream"]
            )
            self.assertEqual(
                nav2_rmf_concurrency2048_artifact["metrics"]["upstream_concurrency"],
                2048,
            )
            self.assertEqual(
                nav2_rmf_concurrency2048_artifact["metrics"]["expected_service_frames"],
                12346,
            )
            self.assertEqual(
                nav2_rmf_concurrency2048_artifact["metrics"]["batch_timeout_s"],
                120,
            )
            self.assertEqual(
                nav2_rmf_concurrency2048_artifact["metrics"]["router_timeout_ms"],
                320000,
            )
            self.assertTrue(
                nav2_rmf_concurrency2048_artifact["metrics"]["navigation_batch"]
            )
            self.assertTrue(nav2_rmf_concurrency2048_artifact["metrics"]["rmf_batch"])
            nav2_rmf_concurrency4096_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_concurrency4096_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["category"], "workload/nav2-rmf"
            )
            self.assertEqual(nav2_rmf_concurrency4096_artifact["status"], "ok")
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"]["upstream_concurrency"],
                4096,
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"]["expected_service_frames"],
                24634,
            )
            self.assertTrue(
                nav2_rmf_concurrency4096_artifact["metrics"]["navigation_batch"]
            )
            self.assertTrue(nav2_rmf_concurrency4096_artifact["metrics"]["rmf_batch"])
            self.assertTrue(
                nav2_rmf_concurrency4096_artifact["metrics"]["lifecycle_transport"]
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"]["server_timeout_s"],
                1168,
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"][
                    "goal_send_pacing_ms"
                ],
                0.5,
            )
            self.assertTrue(
                nav2_rmf_concurrency4096_artifact["metrics"][
                    "unwindowed_goal_batch"
                ]
            )
            self.assertFalse(
                nav2_rmf_concurrency4096_artifact["metrics"][
                    "goal_recreate_client_per_batch"
                ]
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"][
                    "udp_socket_buffer_bytes"
                ],
                16777216,
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"]["udp_send_pacing_us"],
                250,
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"][
                    "service_request_repeats"
                ],
                3,
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"][
                    "service_response_repeats"
                ],
                3,
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"][
                    "service_request_repeat_interval_ms"
                ],
                1,
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"][
                    "service_response_repeat_interval_ms"
                ],
                1,
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"][
                    "router_post_satisfaction_ms"
                ],
                30000,
            )
            self.assertEqual(
                nav2_rmf_concurrency4096_artifact["metrics"][
                    "router_expected_service_frames"
                ],
                0,
            )
            nav2_rmf_total4096_goalbatch8_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["path"].endswith(
                    "docker_router_nav2_rmf_action_workload_total4096_goalbatch8_summary.json"
                )
            )
            self.assertEqual(
                nav2_rmf_total4096_goalbatch8_artifact["category"],
                "workload/nav2-rmf",
            )
            self.assertEqual(nav2_rmf_total4096_goalbatch8_artifact["status"], "ok")
            self.assertEqual(
                nav2_rmf_total4096_goalbatch8_artifact["metrics"][
                    "upstream_concurrency"
                ],
                4096,
            )
            self.assertEqual(
                nav2_rmf_total4096_goalbatch8_artifact["metrics"]["goal_batch_size"],
                8,
            )
            self.assertEqual(
                nav2_rmf_total4096_goalbatch8_artifact["metrics"]["goal_batch_count"],
                512,
            )
            self.assertTrue(
                nav2_rmf_total4096_goalbatch8_artifact["metrics"][
                    "navigation_batch"
                ]
            )
            self.assertTrue(nav2_rmf_total4096_goalbatch8_artifact["metrics"]["rmf_batch"])
            self.assertTrue(
                nav2_rmf_total4096_goalbatch8_artifact["metrics"][
                    "lifecycle_transport"
                ]
            )
            nav2_spin_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_behavior_spin_probe.v1"
            )
            self.assertEqual(nav2_spin_artifact["category"], "workload/nav2-rmf")
            self.assertTrue(nav2_spin_artifact["metrics"]["spin_goal_succeeded"])
            self.assertEqual(nav2_spin_artifact["metrics"]["spin_error_code"], 0)
            self.assertTrue(nav2_spin_artifact["metrics"]["cmd_vel_topic_forwarded"])
            self.assertEqual(
                nav2_spin_artifact["metrics"]["fake_base_cmd_vel_count"],
                8,
            )
            self.assertTrue(
                nav2_spin_artifact["metrics"]["recovery_behavior_action_claim"]
            )
            self.assertTrue(
                nav2_spin_artifact["metrics"]["nav2_recovery_behavior_claim"]
            )
            self.assertFalse(
                nav2_spin_artifact["metrics"]["navigate_to_pose_recovery_tree_claim"]
            )
            nav2_recovery_tree_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_navigate_to_pose_recovery_tree_probe.v1"
            )
            self.assertEqual(
                nav2_recovery_tree_artifact["category"], "workload/nav2-rmf"
            )
            self.assertFalse(
                nav2_recovery_tree_artifact["metrics"][
                    "navigate_to_pose_goal_succeeded"
                ]
            )
            self.assertEqual(
                nav2_recovery_tree_artifact["metrics"]["navigate_to_pose_status"],
                "ABORTED",
            )
            self.assertTrue(
                nav2_recovery_tree_artifact["metrics"]["planner_failure_observed"]
            )
            self.assertTrue(
                nav2_recovery_tree_artifact["metrics"]["spin_goal_succeeded"]
            )
            self.assertTrue(
                nav2_recovery_tree_artifact["metrics"][
                    "navigate_to_pose_recovery_tree_claim"
                ]
            )
            self.assertFalse(
                nav2_recovery_tree_artifact["metrics"][
                    "successful_recovered_navigation_claim"
                ]
            )
            nav2_recovered_success_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_nav2_navigate_to_pose_recovered_success_probe.v1"
            )
            self.assertEqual(
                nav2_recovered_success_artifact["category"], "workload/nav2-rmf"
            )
            self.assertTrue(
                nav2_recovered_success_artifact["metrics"][
                    "navigate_to_pose_goal_succeeded"
                ]
            )
            self.assertEqual(
                nav2_recovered_success_artifact["metrics"][
                    "navigate_to_pose_error_code"
                ],
                0,
            )
            self.assertTrue(
                nav2_recovered_success_artifact["metrics"]["spin_goal_succeeded"]
            )
            self.assertTrue(
                nav2_recovered_success_artifact["metrics"][
                    "successful_recovered_navigation_claim"
                ]
            )
            self.assertEqual(
                nav2_recovered_success_artifact["metrics"][
                    "successful_recovered_navigation_scope"
                ],
                "spin_recovery_action_then_successful_navigate_to_pose",
            )
            self.assertFalse(
                nav2_recovered_success_artifact["metrics"][
                    "obstacle_field_recovery_claim"
                ]
            )
            nav2_recovered_success_repeated_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == (
                    "fleetrmw.docker_nav2_navigate_to_pose_recovered_success_"
                    "repeated_probe.v1"
                )
            )
            self.assertEqual(
                nav2_recovered_success_repeated_artifact["category"],
                "workload/nav2-rmf",
            )
            self.assertTrue(
                nav2_recovered_success_repeated_artifact["metrics"][
                    "navigate_to_pose_recovered_success_repeated_smoke"
                ]
            )
            self.assertEqual(
                nav2_recovered_success_repeated_artifact["metrics"][
                    "successful_recovered_navigation_run_count"
                ],
                2,
            )
            self.assertEqual(
                nav2_recovered_success_repeated_artifact["metrics"][
                    "spin_goal_succeeded_run_count"
                ],
                2,
            )
            self.assertTrue(
                nav2_recovered_success_repeated_artifact["metrics"][
                    "repeated_recovered_navigation_claim"
                ]
            )
            self.assertFalse(
                nav2_recovered_success_repeated_artifact["metrics"][
                    "long_navigation_workload_claim"
                ]
            )
            message_lost_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_message_lost_event_probe.v1"
            )
            self.assertEqual(message_lost_artifact["category"], "rmw-abi")
            self.assertEqual(message_lost_artifact["run_count"], 5)
            self.assertEqual(message_lost_artifact["ok_run_count"], 5)
            self.assertTrue(
                message_lost_artifact["metrics"]["message_lost_event_production"]
            )
            self.assertTrue(
                message_lost_artifact["metrics"]["message_lost_repeated_event_claim"]
            )
            self.assertEqual(message_lost_artifact["metrics"]["message_lost_total_count"], 1)
            self.assertTrue(
                message_lost_artifact["metrics"]["best_effort_gap_detected"]
            )
            self.assertEqual(
                message_lost_artifact["metrics"]["best_effort_gap_received_frames"], 3
            )
            self.assertTrue(
                message_lost_artifact["metrics"]["repair_suppressed_false_message_lost"]
            )
            self.assertEqual(
                message_lost_artifact["metrics"][
                    "repair_observer_message_lost_total_count"
                ],
                0,
            )
            self.assertTrue(
                message_lost_artifact["metrics"][
                    "reliable_history_exhaustion_detected"
                ]
            )
            self.assertEqual(
                message_lost_artifact["metrics"]["unrecoverable_loss_notices_sent"],
                1,
            )
            message_lost_interprocess_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_message_lost_interprocess_probe.v1"
            )
            self.assertEqual(message_lost_interprocess_artifact["category"], "rmw-abi")
            self.assertEqual(message_lost_interprocess_artifact["run_count"], 20)
            self.assertEqual(message_lost_interprocess_artifact["ok_run_count"], 20)
            self.assertTrue(
                message_lost_interprocess_artifact["metrics"]["netem_applied"]
            )
            self.assertTrue(
                message_lost_interprocess_artifact["metrics"][
                    "remote_unrecoverable_loss_notice_claim"
                ]
            )
            self.assertTrue(
                message_lost_interprocess_artifact["metrics"][
                    "remote_message_lost_waitable_claim"
                ]
            )
            self.assertTrue(
                message_lost_interprocess_artifact["metrics"][
                    "duplicate_unrecoverable_loss_notice_deduplication_claim"
                ]
            )
            self.assertTrue(
                message_lost_interprocess_artifact["metrics"][
                    "repeated_remote_message_lost_claim"
                ]
            )
            message_lost_terminal_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_message_lost_terminal_repair_probe.v1"
            )
            self.assertEqual(message_lost_terminal_artifact["category"], "rmw-abi")
            self.assertEqual(message_lost_terminal_artifact["run_count"], 15)
            self.assertEqual(message_lost_terminal_artifact["ok_run_count"], 15)
            self.assertEqual(message_lost_terminal_artifact["metrics"]["scenario_count"], 3)
            self.assertEqual(
                message_lost_terminal_artifact["metrics"]["iterations_per_scenario"],
                5,
            )
            self.assertTrue(
                message_lost_terminal_artifact["metrics"][
                    "repair_budget_terminal_loss_notice_claim"
                ]
            )
            self.assertTrue(
                message_lost_terminal_artifact["metrics"][
                    "repair_attempt_limit_terminal_loss_notice_claim"
                ]
            )
            self.assertTrue(
                message_lost_terminal_artifact["metrics"][
                    "repair_admission_terminal_loss_notice_claim"
                ]
            )
            self.assertTrue(
                message_lost_terminal_artifact["metrics"][
                    "terminal_repair_controls_repeated_claim"
                ]
            )
            liveliness_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_liveliness_event_probe.v1"
            )
            self.assertEqual(liveliness_artifact["category"], "rmw-abi")
            self.assertEqual(liveliness_artifact["run_count"], 5)
            self.assertEqual(liveliness_artifact["ok_run_count"], 5)
            self.assertTrue(
                liveliness_artifact["metrics"]["liveliness_event_production"]
            )
            self.assertTrue(
                liveliness_artifact["metrics"]["liveliness_repeated_event_claim"]
            )
            self.assertEqual(liveliness_artifact["metrics"]["lost_total_count"], 1)
            automatic_liveliness_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_automatic_liveliness_probe.v1"
            )
            self.assertEqual(automatic_liveliness_artifact["category"], "rmw-abi")
            self.assertEqual(automatic_liveliness_artifact["run_count"], 5)
            self.assertEqual(automatic_liveliness_artifact["ok_run_count"], 5)
            self.assertTrue(
                automatic_liveliness_artifact["metrics"][
                    "automatic_liveliness_idle_renewal_claim"
                ]
            )
            self.assertTrue(
                automatic_liveliness_artifact["metrics"][
                    "automatic_liveliness_false_loss_suppression_claim"
                ]
            )
            self.assertEqual(
                automatic_liveliness_artifact["metrics"]["idle_lease_multiples"],
                6,
            )
            self.assertEqual(
                automatic_liveliness_artifact["metrics"][
                    "liveliness_lost_total_count"
                ],
                0,
            )
            self.assertTrue(
                automatic_liveliness_artifact["metrics"]["clean_teardown"]
            )
            remote_manual_liveliness_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_remote_manual_liveliness_probe.v1"
            )
            self.assertEqual(remote_manual_liveliness_artifact["category"], "rmw-abi")
            self.assertEqual(remote_manual_liveliness_artifact["run_count"], 5)
            self.assertEqual(remote_manual_liveliness_artifact["ok_run_count"], 5)
            self.assertTrue(
                remote_manual_liveliness_artifact["metrics"][
                    "remote_manual_liveliness_idle_timeout_claim"
                ]
            )
            self.assertTrue(
                remote_manual_liveliness_artifact["metrics"][
                    "remote_manual_liveliness_explicit_assert_claim"
                ]
            )
            self.assertTrue(
                remote_manual_liveliness_artifact["metrics"][
                    "remote_manual_liveliness_publish_assert_claim"
                ]
            )
            self.assertTrue(
                remote_manual_liveliness_artifact["metrics"][
                    "remote_publisher_liveliness_lost_event_claim"
                ]
            )
            self.assertEqual(
                remote_manual_liveliness_artifact["metrics"][
                    "publisher_liveliness_lost_total_count"
                ],
                2,
            )
            self.assertEqual(
                remote_manual_liveliness_artifact["metrics"][
                    "manual_liveliness_expiries"
                ],
                2,
            )
            self.assertEqual(
                remote_manual_liveliness_artifact["metrics"][
                    "manual_liveliness_reassertions"
                ],
                2,
            )
            remote_liveliness_multi_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_remote_liveliness_multi_endpoint_probe.v1"
            )
            self.assertEqual(remote_liveliness_multi_artifact["category"], "rmw-abi")
            self.assertEqual(remote_liveliness_multi_artifact["run_count"], 5)
            self.assertEqual(remote_liveliness_multi_artifact["ok_run_count"], 5)
            self.assertTrue(
                remote_liveliness_multi_artifact["metrics"][
                    "remote_liveliness_multi_endpoint_independence_claim"
                ]
            )
            self.assertTrue(
                remote_liveliness_multi_artifact["metrics"][
                    "remote_liveliness_endpoint_churn_recreate_claim"
                ]
            )
            self.assertEqual(
                remote_liveliness_multi_artifact["metrics"][
                    "publishers_during_single_endpoint_expiry"
                ],
                2,
            )
            self.assertEqual(
                remote_liveliness_multi_artifact["metrics"][
                    "manual_liveliness_reassertions"
                ],
                1,
            )
            liveliness_incompatible_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_qos_liveliness_incompatible_event_probe.v1"
            )
            self.assertEqual(liveliness_incompatible_artifact["category"], "rmw-abi")
            self.assertEqual(liveliness_incompatible_artifact["run_count"], 5)
            self.assertEqual(liveliness_incompatible_artifact["ok_run_count"], 5)
            self.assertTrue(
                liveliness_incompatible_artifact["metrics"][
                    "qos_liveliness_incompatible_event_production_claim"
                ]
            )
            self.assertTrue(
                liveliness_incompatible_artifact["metrics"][
                    "liveliness_slow_lease_requested_event_claim"
                ]
            )
            self.assertEqual(
                liveliness_incompatible_artifact["metrics"][
                    "incompatible_event_count"
                ],
                6,
            )
            best_available_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_qos_best_available_probe.v1"
            )
            self.assertEqual(best_available_artifact["category"], "fleet-qos-qoe")
            self.assertEqual(best_available_artifact["run_count"], 5)
            self.assertEqual(best_available_artifact["ok_run_count"], 5)
            self.assertTrue(
                best_available_artifact["metrics"][
                    "qos_best_available_endpoint_adaptation_claim"
                ]
            )
            self.assertTrue(
                best_available_artifact["metrics"][
                    "mixed_publishers_automatic_max_lease_claim"
                ]
            )
            self.assertEqual(
                best_available_artifact["metrics"]["mixed_selected_lease_ms"],
                500,
            )
            liveliness_scale_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_liveliness_scale_probe.v1"
            )
            self.assertEqual(liveliness_scale_artifact["category"], "rmw-abi")
            self.assertEqual(liveliness_scale_artifact["run_count"], 5)
            self.assertEqual(liveliness_scale_artifact["ok_run_count"], 5)
            self.assertTrue(
                liveliness_scale_artifact["metrics"][
                    "liveliness_manual_multi_endpoint_scale_claim"
                ]
            )
            self.assertTrue(
                liveliness_scale_artifact["metrics"][
                    "liveliness_system_default_automatic_renewal_claim"
                ]
            )
            self.assertEqual(
                liveliness_scale_artifact["metrics"]["manual_publisher_count"], 64
            )
            self.assertEqual(
                liveliness_scale_artifact["metrics"][
                    "system_default_idle_alive_count"
                ],
                16,
            )
            remote_liveliness_scale_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_remote_liveliness_scale_probe.v1"
            )
            self.assertEqual(
                remote_liveliness_scale_artifact["category"], "rmw-abi"
            )
            self.assertEqual(remote_liveliness_scale_artifact["run_count"], 5)
            self.assertEqual(
                remote_liveliness_scale_artifact["ok_run_count"], 5
            )
            self.assertTrue(
                remote_liveliness_scale_artifact["metrics"][
                    "remote_liveliness_64_endpoint_scale_claim"
                ]
            )
            self.assertTrue(
                remote_liveliness_scale_artifact["metrics"][
                    "remote_liveliness_exact_aggregate_transition_claim"
                ]
            )
            self.assertEqual(
                remote_liveliness_scale_artifact["metrics"][
                    "manual_liveliness_expiries"
                ],
                96,
            )
            self.assertEqual(
                remote_liveliness_scale_artifact["metrics"][
                    "manual_liveliness_reassertions"
                ],
                32,
            )
            liveliness_default_lease_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_liveliness_default_lease_probe.v1"
            )
            self.assertEqual(
                liveliness_default_lease_artifact["category"], "rmw-abi"
            )
            self.assertEqual(liveliness_default_lease_artifact["run_count"], 5)
            self.assertEqual(
                liveliness_default_lease_artifact["ok_run_count"], 5
            )
            self.assertTrue(
                liveliness_default_lease_artifact["metrics"][
                    "non_expiring_liveliness_lifecycle_claim"
                ]
            )
            self.assertTrue(
                liveliness_default_lease_artifact["metrics"][
                    "unknown_liveliness_fail_closed_claim"
                ]
            )
            self.assertTrue(
                liveliness_default_lease_artifact["metrics"][
                    "manual_by_node_infinite_lease_lifecycle_claim"
                ]
            )
            allocation_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_allocation_probe.v1"
            )
            self.assertEqual(allocation_artifact["category"], "rmw-abi")
            self.assertEqual(allocation_artifact["run_count"], 5)
            self.assertEqual(allocation_artifact["ok_run_count"], 5)
            self.assertTrue(allocation_artifact["metrics"]["allocation_abi_supported"])
            self.assertFalse(allocation_artifact["metrics"]["deep_preallocation"])
            self.assertTrue(
                allocation_artifact["metrics"]["allocation_repeated_lifecycle_claim"]
            )
            qos_event_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_qos_event_probe.v1"
            )
            self.assertEqual(qos_event_artifact["category"], "rmw-abi")
            self.assertEqual(qos_event_artifact["run_count"], 5)
            self.assertEqual(qos_event_artifact["ok_run_count"], 5)
            self.assertTrue(
                qos_event_artifact["metrics"]["qos_event_object_abi_supported"]
            )
            self.assertTrue(qos_event_artifact["metrics"]["event_production"])
            self.assertTrue(qos_event_artifact["metrics"]["wait_event_readiness"])
            self.assertTrue(
                qos_event_artifact["metrics"][
                    "qos_event_repeated_deadline_waitable_claim"
                ]
            )
            qos_event_waitability_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_qos_event_waitability_matrix.v1"
            )
            self.assertEqual(qos_event_waitability_artifact["category"], "rmw-abi")
            self.assertEqual(qos_event_waitability_artifact["run_count"], 5)
            self.assertEqual(qos_event_waitability_artifact["ok_run_count"], 5)
            self.assertEqual(
                qos_event_waitability_artifact["metrics"]["component_execution_count"],
                35,
            )
            self.assertEqual(
                qos_event_waitability_artifact["metrics"]["event_type_count"],
                11,
            )
            self.assertTrue(
                qos_event_waitability_artifact["metrics"][
                    "full_qos_event_waitable_readiness_claim"
                ]
            )
            self.assertTrue(
                qos_event_waitability_artifact["metrics"][
                    "qos_event_waitability_repeated_claim"
                ]
            )
            content_filter_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_content_filter_probe.v1"
            )
            self.assertEqual(content_filter_artifact["category"], "rmw-abi")
            self.assertEqual(content_filter_artifact["run_count"], 5)
            self.assertEqual(content_filter_artifact["ok_run_count"], 5)
            self.assertTrue(
                content_filter_artifact["metrics"][
                    "content_filter_set_get_abi_supported"
                ]
            )
            self.assertTrue(
                content_filter_artifact["metrics"]["std_msgs_content_filter_enforcement"]
            )
            self.assertTrue(
                content_filter_artifact["metrics"]["disabled_content_filter_bypass"]
            )
            self.assertTrue(
                content_filter_artifact["metrics"][
                    "content_filter_repeated_enforcement_claim"
                ]
            )
            security_options_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_security_options_probe.v1"
            )
            self.assertEqual(security_options_artifact["category"], "rmw-abi")
            self.assertEqual(security_options_artifact["run_count"], 5)
            self.assertEqual(security_options_artifact["ok_run_count"], 5)
            self.assertTrue(
                security_options_artifact["metrics"][
                    "security_options_lifecycle_abi_supported"
                ]
            )
            self.assertTrue(
                security_options_artifact["metrics"][
                    "init_options_copy_deep_copies_enclave"
                ]
            )
            self.assertTrue(
                security_options_artifact["metrics"][
                    "context_init_copies_security_options"
                ]
            )
            self.assertTrue(
                security_options_artifact["metrics"][
                    "security_options_repeated_lifecycle_claim"
                ]
            )
            self.assertFalse(
                security_options_artifact["metrics"][
                    "security_policy_enforcement_executed"
                ]
            )
            self.assertEqual(
                security_options_artifact["metrics"]["security_hardening_blocker"],
                "full_sros2_policy_enforcement_not_implemented",
            )
            self.assertEqual(
                security_options_artifact["metrics"]["sros2_policy_enforcement_scope"],
                "not_executed_lifecycle_only",
            )
            self.assertFalse(
                security_options_artifact["metrics"]["sros2_policy_enforcement_claim"]
            )
            self.assertFalse(
                security_options_artifact["metrics"][
                    "production_security_hardening_claim"
                ]
            )
            security_policy_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_security_policy_probe.v1"
            )
            self.assertEqual(security_policy_artifact["category"], "rmw-abi")
            self.assertEqual(security_policy_artifact["run_count"], 5)
            self.assertEqual(security_policy_artifact["ok_run_count"], 5)
            self.assertTrue(
                security_policy_artifact["metrics"][
                    "fleetqox_security_policy_enforcement_claim"
                ]
            )
            self.assertEqual(
                security_policy_artifact["metrics"][
                    "security_policy_enforcement_scope"
                ],
                "fleetqox_publish_allow_deny_env_policy",
            )
            self.assertTrue(
                security_policy_artifact["metrics"][
                    "security_policy_repeated_enforcement_claim"
                ]
            )
            self.assertEqual(
                security_policy_artifact["metrics"]["allowed_publish_returncode"],
                0,
            )
            self.assertTrue(security_policy_artifact["metrics"]["allowed_taken"])
            self.assertNotEqual(
                security_policy_artifact["metrics"]["denied_publish_returncode"],
                0,
            )
            self.assertFalse(security_policy_artifact["metrics"]["denied_taken"])
            self.assertEqual(
                security_policy_artifact["metrics"]["security_policy_denied_delta"],
                1,
            )
            self.assertFalse(
                security_policy_artifact["metrics"]["sros2_policy_enforcement_claim"]
            )
            self.assertFalse(
                security_policy_artifact["metrics"][
                    "production_security_hardening_claim"
                ]
            )
            sros2_permissions_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_sros2_permissions_probe.v1"
            )
            self.assertEqual(sros2_permissions_artifact["category"], "rmw-abi")
            self.assertEqual(sros2_permissions_artifact["run_count"], 5)
            self.assertEqual(sros2_permissions_artifact["ok_run_count"], 5)
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_cli_generated_artifacts"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "signed_permissions_verified_preflight"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"]["permissions_xsd_validated"]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_permissions_xml_publish_enforcement_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_permissions_xml_subscribe_enforcement_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_permissions_xml_pubsub_enforcement_claim"
                ]
            )
            self.assertEqual(
                sros2_permissions_artifact["metrics"][
                    "sros2_permissions_xml_subscribe_allowed_delta"
                ],
                1,
            )
            self.assertEqual(
                sros2_permissions_artifact["metrics"][
                    "sros2_permissions_xml_subscribe_denied_delta"
                ],
                2,
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_permissions_xml_repeated_enforcement_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "malformed_permissions_fail_closed_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "runtime_permissions_signature_validation"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "runtime_sros2_permissions_signature_validation_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "tampered_signed_permissions_fail_closed_claim"
                ]
            )
            self.assertEqual(
                sros2_permissions_artifact["metrics"]["service_ok_run_count"],
                5,
            )
            self.assertEqual(
                sros2_permissions_artifact["metrics"]["action_ok_run_count"],
                5,
            )
            self.assertEqual(
                sros2_permissions_artifact["metrics"]["governance_ok_run_count"],
                5,
            )
            self.assertEqual(
                sros2_permissions_artifact["metrics"]["identity_ok_run_count"],
                5,
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_service_request_reply_authorization_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_service_repeated_authorization_claim"
                ]
            )
            self.assertEqual(
                sros2_permissions_artifact["metrics"][
                    "service_request_publish_denied_delta"
                ],
                2,
            )
            self.assertEqual(
                sros2_permissions_artifact["metrics"][
                    "service_request_subscribe_denied_delta"
                ],
                1,
            )
            self.assertEqual(
                sros2_permissions_artifact["metrics"][
                    "service_response_publish_denied_delta"
                ],
                1,
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_action_authorization_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_action_repeated_authorization_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_action_call_denied_fail_closed_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_action_execute_denied_fail_closed_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "governance_xml_enforcement_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_governance_access_control_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_governance_repeated_access_control_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_governance_transport_protection_fail_closed_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_tampered_signed_governance_fail_closed_claim"
                ]
            )
            self.assertFalse(
                sros2_permissions_artifact["metrics"][
                    "governance_transport_security_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_local_identity_credentials_validation_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_local_identity_credentials_repeated_validation_claim"
                ]
            )
            self.assertTrue(
                sros2_permissions_artifact["metrics"][
                    "sros2_identity_private_key_mismatch_fail_closed_claim"
                ]
            )
            self.assertFalse(
                sros2_permissions_artifact["metrics"][
                    "sros2_peer_identity_authentication_claim"
                ]
            )
            udp_aead_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_udp_aead_probe.v1"
            )
            self.assertEqual(udp_aead_artifact["category"], "transport/security")
            self.assertEqual(udp_aead_artifact["run_count"], 5)
            self.assertEqual(udp_aead_artifact["ok_run_count"], 5)
            self.assertTrue(
                udp_aead_artifact["metrics"][
                    "udp_aead_authenticated_encryption_claim"
                ]
            )
            self.assertTrue(
                udp_aead_artifact["metrics"][
                    "udp_aead_tamper_fail_closed_claim"
                ]
            )
            self.assertTrue(
                udp_aead_artifact["metrics"][
                    "udp_aead_strict_missing_key_fail_closed_claim"
                ]
            )
            self.assertTrue(
                udp_aead_artifact["metrics"][
                    "udp_authenticated_psk_session_key_derivation_claim"
                ]
            )
            self.assertTrue(
                udp_aead_artifact["metrics"]["udp_session_key_rotation_claim"]
            )
            self.assertFalse(
                udp_aead_artifact["metrics"]["dds_security_interoperability_claim"]
            )
            udp_peer_auth_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_udp_peer_auth_probe.v1"
            )
            self.assertEqual(
                udp_peer_auth_artifact["category"], "transport/security"
            )
            self.assertEqual(udp_peer_auth_artifact["run_count"], 5)
            self.assertEqual(udp_peer_auth_artifact["ok_run_count"], 5)
            self.assertTrue(
                udp_peer_auth_artifact["metrics"][
                    "sros2_peer_identity_authentication_claim"
                ]
            )
            self.assertTrue(
                udp_peer_auth_artifact["metrics"][
                    "udp_peer_untrusted_certificate_fail_closed_claim"
                ]
            )
            self.assertFalse(
                udp_peer_auth_artifact["metrics"]["session_key_establishment_claim"]
            )
            self.assertTrue(
                udp_peer_auth_artifact["metrics"]["certificate_revocation_claim"]
            )
            dynamic_message_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_dynamic_message_probe.v1"
            )
            self.assertEqual(
                dynamic_message_artifact["category"], "abi/dynamic-message"
            )
            self.assertEqual(dynamic_message_artifact["run_count"], 5)
            self.assertEqual(dynamic_message_artifact["ok_run_count"], 5)
            self.assertTrue(
                dynamic_message_artifact["metrics"]["dynamic_message_take_claim"]
            )
            self.assertTrue(
                dynamic_message_artifact["metrics"][
                    "dynamic_message_take_with_info_claim"
                ]
            )
            self.assertTrue(
                dynamic_message_artifact["metrics"][
                    "message_info_sequence_features_claim"
                ]
            )
            quic_soak_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_quic_gateway_async_burst_soak.v1"
            )
            self.assertEqual(quic_soak_artifact["category"], "transport/quic")
            self.assertEqual(quic_soak_artifact["run_count"], 10)
            self.assertEqual(quic_soak_artifact["ok_run_count"], 10)
            self.assertEqual(
                quic_soak_artifact["metrics"]["total_quic_gateway_frames_sent"],
                40,
            )
            self.assertEqual(quic_soak_artifact["metrics"]["netem_sent_packets"], 208)
            self.assertEqual(quic_soak_artifact["metrics"]["rtt_sample_count"], 160)
            stress_security_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetrmw.docker_stress_security_campaign.v1"
            )
            self.assertEqual(stress_security_artifact["category"], "stress/security")
            self.assertEqual(stress_security_artifact["run_count"], 10)
            self.assertEqual(stress_security_artifact["ok_run_count"], 10)
            self.assertEqual(stress_security_artifact["metrics"]["component_count"], 10)
            self.assertEqual(
                stress_security_artifact["metrics"]["total_component_runs"],
                48,
            )
            self.assertTrue(
                stress_security_artifact["metrics"]["stress_security_smoke_claim"]
            )
            self.assertTrue(
                stress_security_artifact["metrics"]["stress_security_repeated_claim"]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "fleetqox_security_policy_enforcement_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "security_policy_repeated_enforcement_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "sros2_permissions_xml_publish_enforcement_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "sros2_permissions_xml_subscribe_enforcement_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "sros2_permissions_xml_pubsub_enforcement_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "sros2_permissions_xml_repeated_enforcement_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "malformed_permissions_fail_closed_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "runtime_sros2_permissions_signature_validation_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "tampered_signed_permissions_fail_closed_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "sros2_service_request_reply_authorization_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "sros2_service_repeated_authorization_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "sros2_action_authorization_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "governance_xml_enforcement_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "sros2_governance_repeated_access_control_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "sros2_local_identity_credentials_repeated_validation_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "udp_aead_repeated_authenticated_encryption_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "udp_aead_tamper_fail_closed_claim"
                ]
            )
            self.assertTrue(
                stress_security_artifact["metrics"][
                    "udp_aead_strict_missing_key_fail_closed_claim"
                ]
            )
            self.assertFalse(
                stress_security_artifact["metrics"][
                    "long_stress_security_campaign_claim"
                ]
            )
            self.assertEqual(
                stress_security_artifact["metrics"]["quic_soak_total_frames_sent"],
                12,
            )
            omnetpp_artifact = next(
                artifact
                for artifact in report["artifacts"]
                if artifact["schema_version"]
                == "fleetqox.omnetpp_template_integrity_probe.v1"
            )
            self.assertEqual(omnetpp_artifact["category"], "simulation/omnetpp")
            self.assertEqual(omnetpp_artifact["metrics"]["omnetpp_scenario_count"], 2)
            self.assertEqual(omnetpp_artifact["metrics"]["prepared_trace_count"], 2)
            self.assertEqual(omnetpp_artifact["metrics"]["total_packet_rows"], 1362218)
            self.assertTrue(
                omnetpp_artifact["metrics"]["omnetpp_template_integrity_claim"]
            )
            self.assertTrue(omnetpp_artifact["metrics"]["omnetpp_input_trace_claim"])
            self.assertFalse(omnetpp_artifact["metrics"]["omnetpp_runtime_executed"])
            self.assertEqual(
                omnetpp_artifact["metrics"]["omnetpp_parity_blocker"],
                "omnetpp_runtime_commands_missing",
            )
            self.assertEqual(
                omnetpp_artifact["metrics"]["ns3_omnetpp_parity_scope"],
                "template_input_only_no_runtime_comparison",
            )
            self.assertFalse(omnetpp_artifact["metrics"]["omnetpp_inet_runtime_claim"])
            self.assertFalse(omnetpp_artifact["metrics"]["omnetpp_parity_claim"])
            self.assertIn("production_ready: `false`", markdown)
            self.assertNotIn("- `dynamic_messages`", markdown)
            self.assertIn("dynamic_message_take_claim=True", markdown)
            self.assertIn("total_quic_gateway_frames_sent=40", markdown)
            self.assertIn("stress_security_smoke_claim=True", markdown)
            self.assertIn("stress_security_repeated_claim=True", markdown)
            self.assertIn(
                "`long_stress_security_campaign_claim`: `true`", markdown
            )
            self.assertIn("quic_soak_total_frames_sent=12", markdown)
            self.assertIn("netem_sent_packets=208", markdown)
            self.assertIn("rtt_sample_count=160", markdown)
            self.assertIn("rmw_take_path_integrated=True", markdown)
            self.assertIn("session_file_reused_by_multiple_downloads=True", markdown)
            self.assertIn("download_count=5", markdown)
            self.assertIn("client_handshake_count=5", markdown)
            self.assertIn("planner_configure_transition=True", markdown)
            self.assertIn("planner_activate_transition=True", markdown)
            self.assertIn("tf_topic_forwarded=True", markdown)
            self.assertIn("compute_path_goal_succeeded=True", markdown)
            self.assertIn("compute_path_path_pose_count=14", markdown)
            self.assertIn("follow_path_goal_succeeded=True", markdown)
            self.assertIn("follow_path_error_code=0", markdown)
            self.assertIn("navigate_to_pose_goal_succeeded=True", markdown)
            self.assertIn("navigate_to_pose_repeated_smoke=True", markdown)
            self.assertIn("navigate_to_pose_goal_succeeded_run_count=2", markdown)
            self.assertIn("cmd_vel_topic_forwarded=True", markdown)
            self.assertIn("fake_base_cmd_vel_count=4", markdown)
            self.assertIn("moving_robot_navigation_claim=True", markdown)
            self.assertIn("extended_moving_navigation_claim=True", markdown)
            self.assertIn(
                "extended_moving_navigation_scope=single_goal_unobstructed_1m_plus_fake_base_nav2_bt_pipeline",
                markdown,
            )
            self.assertIn("navigate_to_pose_long_moving_workload=True", markdown)
            self.assertIn("extended_moving_navigation_run_count=3", markdown)
            self.assertIn("total_fake_base_moved_distance=2.94", markdown)
            self.assertIn("long_navigation_workload_claim=True", markdown)
            self.assertIn("upstream_concurrency=8", markdown)
            self.assertIn("expected_service_frames=106", markdown)
            self.assertIn("upstream_concurrency=16", markdown)
            self.assertIn("expected_service_frames=154", markdown)
            self.assertIn("upstream_concurrency=32", markdown)
            self.assertIn("expected_service_frames=250", markdown)
            self.assertIn("upstream_concurrency=64", markdown)
            self.assertIn("expected_service_frames=442", markdown)
            self.assertIn("upstream_concurrency=128", markdown)
            self.assertIn("expected_service_frames=826", markdown)
            self.assertIn("upstream_concurrency=256", markdown)
            self.assertIn("expected_service_frames=1594", markdown)
            self.assertIn("upstream_concurrency=512", markdown)
            self.assertIn("expected_service_frames=3130", markdown)
            self.assertIn("batch_timeout_s=76", markdown)
            self.assertIn("router_timeout_ms=232000", markdown)
            self.assertIn("upstream_concurrency=1024", markdown)
            self.assertIn("expected_service_frames=6202", markdown)
            self.assertIn("batch_timeout_s=120", markdown)
            self.assertIn("router_timeout_ms=320000", markdown)
            self.assertIn("upstream_concurrency=2048", markdown)
            self.assertIn("expected_service_frames=12346", markdown)
            self.assertIn("upstream_concurrency=4096", markdown)
            self.assertIn("expected_service_frames=24634", markdown)
            self.assertIn("nav2_upstream=True", markdown)
            self.assertIn("rmf_upstream=True", markdown)
            self.assertIn("spin_goal_succeeded=True", markdown)
            self.assertIn("spin_error_code=0", markdown)
            self.assertIn("recovery_behavior_action_claim=True", markdown)
            self.assertIn("navigate_to_pose_status=ABORTED", markdown)
            self.assertIn("planner_failure_observed=True", markdown)
            self.assertIn("navigate_to_pose_recovery_tree_claim=True", markdown)
            self.assertIn("successful_recovered_navigation_claim=False", markdown)
            self.assertIn("successful_recovered_navigation_claim=True", markdown)
            self.assertIn(
                "successful_recovered_navigation_scope=spin_recovery_action_then_successful_navigate_to_pose",
                markdown,
            )
            self.assertIn("navigate_to_pose_recovered_success_repeated_smoke=True", markdown)
            self.assertIn("successful_recovered_navigation_run_count=2", markdown)
            self.assertIn("spin_goal_succeeded_run_count=2", markdown)
            self.assertIn("repeated_recovered_navigation_claim=True", markdown)
            self.assertIn("obstacle_field_recovery_claim=False", markdown)
            self.assertIn("planner_static_obstacle_repair_claim=True", markdown)
            self.assertIn("blocked_compute_path_error_code=208", markdown)
            self.assertIn("clear_compute_path_path_pose_count=14", markdown)
            self.assertIn("obstacle_field_recovery_claim=True", markdown)
            self.assertIn("blocked_navigate_to_pose_status=ABORTED", markdown)
            self.assertIn("clear_navigate_to_pose_status=SUCCEEDED", markdown)
            self.assertIn("nav2_obstacle_retry_after_clear_claim=True", markdown)
            self.assertIn("autonomous_same_goal_nav2_obstacle_recovery_claim=False", markdown)
            self.assertIn("autonomous_same_goal_nav2_obstacle_recovery_claim=True", markdown)
            self.assertIn("same_goal_obstacle_recovery_observed=True", markdown)
            self.assertIn("wait_action_forwarded=True", markdown)
            self.assertIn("clear_map_published_during_goal=True", markdown)
            self.assertIn("full_nav2_obstacle_recovery_claim=False", markdown)
            self.assertIn("full_nav2_obstacle_recovery_claim=True", markdown)
            self.assertIn("quic_gateway_bidirectional_boundary_claim=True", markdown)
            self.assertIn("quic_gateway_bidirectional_repeated_claim=True", markdown)
            self.assertIn("session_file_reused_by_upload_and_download=True", markdown)
            self.assertIn("zero_rtt_packet_observed=True", markdown)
            self.assertIn("zero_rtt_accepted_observed=False", markdown)
            self.assertIn("zero_rtt_disabled_control_claim=True", markdown)
            self.assertIn("qos_incompatible_event_production=True", markdown)
            self.assertIn("qos_incompatible_repeated_event_claim=True", markdown)
            self.assertIn("durability_offered_last_policy_kind=2", markdown)
            self.assertIn("type_incompatible_event_production=True", markdown)
            self.assertIn("type_incompatible_repeated_event_claim=True", markdown)
            self.assertIn("message_lost_event_production=True", markdown)
            self.assertIn("message_lost_repeated_event_claim=True", markdown)
            self.assertIn("remote_unrecoverable_loss_notice_claim=True", markdown)
            self.assertIn("remote_message_lost_waitable_claim=True", markdown)
            self.assertIn(
                "duplicate_unrecoverable_loss_notice_deduplication_claim=True",
                markdown,
            )
            self.assertIn("repeated_remote_message_lost_claim=True", markdown)
            self.assertIn("repair_budget_terminal_loss_notice_claim=True", markdown)
            self.assertIn(
                "repair_attempt_limit_terminal_loss_notice_claim=True",
                markdown,
            )
            self.assertIn(
                "repair_admission_terminal_loss_notice_claim=True",
                markdown,
            )
            self.assertIn("terminal_repair_controls_repeated_claim=True", markdown)
            self.assertIn("liveliness_event_production=True", markdown)
            self.assertIn("liveliness_repeated_event_claim=True", markdown)
            self.assertIn(
                "automatic_liveliness_idle_renewal_claim=True", markdown
            )
            self.assertIn(
                "automatic_liveliness_false_loss_suppression_claim=True",
                markdown,
            )
            self.assertIn("automatic_liveliness_repeated_claim=True", markdown)
            self.assertIn("remote_manual_liveliness_idle_timeout_claim=True", markdown)
            self.assertIn("remote_manual_liveliness_explicit_assert_claim=True", markdown)
            self.assertIn("remote_manual_liveliness_publish_assert_claim=True", markdown)
            self.assertIn(
                "remote_publisher_liveliness_lost_event_claim=True",
                markdown,
            )
            self.assertIn(
                "remote_manual_liveliness_graph_lease_independence_claim=True",
                markdown,
            )
            self.assertIn("remote_manual_liveliness_repeated_claim=True", markdown)
            self.assertIn(
                "remote_liveliness_multi_endpoint_independence_claim=True",
                markdown,
            )
            self.assertIn(
                "remote_liveliness_alive_not_alive_remove_claim=True", markdown
            )
            self.assertIn(
                "remote_liveliness_endpoint_churn_recreate_claim=True", markdown
            )
            self.assertIn(
                "remote_liveliness_multi_endpoint_repeated_claim=True", markdown
            )
            self.assertIn("publishers_during_single_endpoint_expiry=2", markdown)
            self.assertIn("publishers_after_churn=0", markdown)
            self.assertIn(
                "qos_liveliness_incompatible_event_production_claim=True",
                markdown,
            )
            self.assertIn(
                "qos_liveliness_incompatible_event_repeated_claim=True", markdown
            )
            self.assertIn("liveliness_kind_offered_event_claim=True", markdown)
            self.assertIn(
                "liveliness_missing_lease_requested_event_claim=True", markdown
            )
            self.assertIn("incompatible_event_count=6", markdown)
            self.assertIn(
                "qos_best_available_endpoint_adaptation_claim=True", markdown
            )
            self.assertIn(
                "qos_best_available_endpoint_adaptation_repeated_claim=True",
                markdown,
            )
            self.assertIn("best_publisher_manual_selection_claim=True", markdown)
            self.assertIn(
                "best_available_policy_frozen_after_create_claim=True", markdown
            )
            self.assertIn("mixed_selected_lease_ms=500", markdown)
            self.assertIn("liveliness_scale_repeated_claim=True", markdown)
            self.assertIn("manual_publisher_count=64", markdown)
            self.assertIn("system_default_idle_alive_count=16", markdown)
            self.assertIn("remote_liveliness_scale_repeated_claim=True", markdown)
            self.assertIn("publishers_during_half_expiry=64", markdown)
            self.assertIn("manual_liveliness_expiries=96", markdown)
            self.assertIn("liveliness_default_lease_repeated_claim=True", markdown)
            self.assertIn("unknown_liveliness_fail_closed_claim=True", markdown)
            self.assertIn("manual_liveliness_expiries=2", markdown)
            self.assertIn("manual_liveliness_reassertions=2", markdown)
            self.assertIn("idle_lease_multiples=6", markdown)
            self.assertIn("liveliness_lost_total_count=0", markdown)
            self.assertIn("matched_event_repeated_claim=True", markdown)
            self.assertIn("remote_matched_event_production=True", markdown)
            self.assertIn("remote_qos_event_production=True", markdown)
            self.assertIn("remote_type_event_production=True", markdown)
            self.assertIn("remote_liveliness_event_production=True", markdown)
            self.assertIn("renewal_deduplication=True", markdown)
            self.assertIn("qos_deadline_incompatible_repeated_event_claim=True", markdown)
            self.assertIn(
                "qos_missing_offered_deadline_incompatible_repeated_claim=True",
                markdown,
            )
            self.assertIn(
                "remote_deadline_missed_event_repeated_claim=True", markdown
            )
            self.assertIn("allocation_repeated_lifecycle_claim=True", markdown)
            self.assertIn("qos_event_repeated_deadline_waitable_claim=True", markdown)
            self.assertIn("component_execution_count=35", markdown)
            self.assertIn("event_type_count=11", markdown)
            self.assertIn("qos_event_waitability_matrix_claim=True", markdown)
            self.assertIn(
                "full_qos_event_waitable_readiness_claim=True", markdown
            )
            self.assertIn("qos_event_waitability_repeated_claim=True", markdown)
            self.assertIn("content_filter_repeated_enforcement_claim=True", markdown)
            self.assertIn("security_options_lifecycle_abi_supported=True", markdown)
            self.assertIn("security_options_repeated_lifecycle_claim=True", markdown)
            self.assertIn("init_options_copy_deep_copies_enclave=True", markdown)
            self.assertIn("context_init_copies_security_options=True", markdown)
            self.assertIn(
                "fleetqox_security_policy_enforcement_claim=True",
                markdown,
            )
            self.assertIn(
                "security_policy_repeated_enforcement_claim=True",
                markdown,
            )
            self.assertIn("security_policy_denied_delta=1", markdown)
            self.assertIn("sros2_cli_generated_artifacts=True", markdown)
            self.assertIn("signed_permissions_verified_preflight=True", markdown)
            self.assertIn("permissions_xsd_validated=True", markdown)
            self.assertIn(
                "sros2_permissions_xml_publish_enforcement_claim=True",
                markdown,
            )
            self.assertIn(
                "sros2_permissions_xml_subscribe_enforcement_claim=True",
                markdown,
            )
            self.assertIn(
                "sros2_permissions_xml_pubsub_enforcement_claim=True",
                markdown,
            )
            self.assertIn("sros2_permissions_xml_subscribe_allowed_delta=1", markdown)
            self.assertIn("sros2_permissions_xml_subscribe_denied_delta=2", markdown)
            self.assertIn(
                "sros2_permissions_xml_repeated_enforcement_claim=True",
                markdown,
            )
            self.assertIn("malformed_permissions_fail_closed_claim=True", markdown)
            self.assertIn("runtime_permissions_signature_validation=True", markdown)
            self.assertIn(
                "runtime_sros2_permissions_signature_validation_claim=True",
                markdown,
            )
            self.assertIn(
                "sros2_service_request_reply_authorization_claim=True",
                markdown,
            )
            self.assertIn(
                "sros2_service_repeated_authorization_claim=True",
                markdown,
            )
            self.assertIn("service_request_publish_denied_delta=2", markdown)
            self.assertIn("service_request_subscribe_denied_delta=1", markdown)
            self.assertIn("service_response_publish_denied_delta=1", markdown)
            self.assertIn("sros2_action_authorization_claim=True", markdown)
            self.assertIn(
                "sros2_action_repeated_authorization_claim=True",
                markdown,
            )
            self.assertIn(
                "tampered_signed_permissions_fail_closed_claim=True",
                markdown,
            )
            self.assertIn("governance_xml_enforcement_claim=True", markdown)
            self.assertIn(
                "sros2_governance_repeated_access_control_claim=True",
                markdown,
            )
            self.assertIn("governance_transport_security_claim=False", markdown)
            self.assertIn(
                "sros2_local_identity_credentials_repeated_validation_claim=True",
                markdown,
            )
            self.assertIn("sros2_peer_identity_authentication_claim=True", markdown)
            self.assertIn("session_key_establishment_claim=True", markdown)
            self.assertIn("forward_secrecy_claim=False", markdown)
            self.assertIn(
                "udp_aead_repeated_authenticated_encryption_claim=True",
                markdown,
            )
            self.assertIn("udp_aead_tamper_fail_closed_claim=True", markdown)
            self.assertIn("dds_security_interoperability_claim=False", markdown)
            self.assertIn("security_policy_enforcement_executed=False", markdown)
            self.assertIn(
                "security_hardening_blocker=full_sros2_policy_enforcement_not_implemented",
                markdown,
            )
            self.assertIn("sros2_policy_enforcement_claim=False", markdown)
            self.assertIn("production_security_hardening_claim=False", markdown)
            self.assertIn("omnetpp_template_integrity_claim=True", markdown)
            self.assertIn("omnetpp_input_trace_claim=True", markdown)
            self.assertIn("omnetpp_runtime_executed=False", markdown)
            self.assertIn("omnetpp_parity_blocker=omnetpp_runtime_commands_missing", markdown)
            self.assertIn("omnetpp_inet_runtime_claim=False", markdown)
            self.assertIn("omnetpp_parity_claim=False", markdown)
            self.assertIn("ns3_omnetpp_parity_claim=False", markdown)

    def test_frontier_aggregate_tracks_admission_monotonicity(self) -> None:
        rows = [
            {
                "status": "ok",
                "admission_ok": True,
                "live_qoe_ok": True,
                "repair_actuation_ok": True,
                "robot_count": 8,
                "capacity_bytes": 700,
                "admitted_count": 1,
                "repair_qualified_ratio": 0.25,
                "live_qoe_qualified_ratio": 1.0,
                "repair_path_transmission_overhead": 1,
                "max_latency_ms": 200.0,
            },
            {
                "status": "ok",
                "admission_ok": True,
                "live_qoe_ok": True,
                "repair_actuation_ok": True,
                "robot_count": 8,
                "capacity_bytes": 1400,
                "admitted_count": 2,
                "repair_qualified_ratio": 0.5,
                "live_qoe_qualified_ratio": 1.0,
                "repair_path_transmission_overhead": 2,
                "max_latency_ms": 210.0,
            },
        ]

        frontier = aggregate_frontier_rows(rows)

        self.assertEqual(len(frontier), 2)
        self.assertTrue(all(row["monotonic"] for row in frontier))
        self.assertEqual(frontier[0]["admitted_count_mean"], 1.0)
        self.assertEqual(frontier[1]["admission_qualified_ratio_mean"], 0.5)
        self.assertEqual(frontier[1]["repair_qualified_ratio_mean"], 0.5)
        self.assertIn("max_latency_ms_ci95_low", frontier[1])

    def test_frontier_resume_rejects_pre_actuation_semantics(self) -> None:
        result = {"repair_capacity_fault": True}
        self.assertFalse(reusable_prior_row({
            "runner_semantics_version": "fleetrmw.fleet_repair_capacity_frontier.live_qoe.v2",
            "result": result,
        }))
        self.assertTrue(reusable_prior_row({
            "runner_semantics_version": RUNNER_SEMANTICS_VERSION,
            "result": result,
        }))

    def test_frontier_monotonicity_includes_live_qoe(self) -> None:
        common = {
            "status": "ok",
            "admission_ok": True,
            "repair_actuation_ok": True,
            "live_qoe_ok": True,
            "robot_count": 8,
            "repair_path_transmission_overhead": 1,
            "max_latency_ms": 200.0,
        }
        frontier = aggregate_frontier_rows([
            {
                **common,
                "capacity_bytes": 700,
                "admitted_count": 1,
                "repair_qualified_ratio": 0.25,
                "live_qoe_qualified_ratio": 0.875,
            },
            {
                **common,
                "capacity_bytes": 1400,
                "admitted_count": 2,
                "repair_qualified_ratio": 0.5,
                "live_qoe_qualified_ratio": 0.75,
            },
        ])

        self.assertTrue(frontier[0]["monotonic"])
        self.assertFalse(frontier[1]["monotonic"])

    def test_frontier_report_names_admission_semantics(self) -> None:
        summary = {
            "status": "ok",
            "ok_run_count": 1,
            "admission_ok_run_count": 1,
            "run_count": 1,
            "frontier": [
                {
                    "robot_count": 8,
                    "capacity_bytes": 700,
                    "ok_run_count": 1,
                    "admission_ok_run_count": 1,
                    "run_count": 1,
                    "admitted_count_mean": 1.0,
                    "admitted_count_ci95_low": 1.0,
                    "admitted_count_ci95_high": 1.0,
                    "repair_qualified_ratio_mean": 0.25,
                    "live_qoe_qualified_ratio_mean": 1.0,
                    "live_qoe_qualified_ratio_ci95_low": 1.0,
                    "live_qoe_qualified_ratio_ci95_high": 1.0,
                    "admission_qualified_ratio_mean": 0.25,
                    "admission_qualified_ratio_ci95_low": 0.25,
                    "admission_qualified_ratio_ci95_high": 0.25,
                    "repair_overhead_mean": 1.0,
                    "repair_overhead_ci95_low": 1.0,
                    "repair_overhead_ci95_high": 1.0,
                    "max_latency_ms_mean": 200.0,
                    "max_latency_ms_ci95_low": 200.0,
                    "max_latency_ms_ci95_high": 200.0,
                    "monotonic": True,
                }
            ],
        }

        markdown = render_frontier_markdown(summary)

        self.assertIn("admission-qualified ratio", markdown)
        self.assertIn("admitted gaps are repaired on time", markdown)

    def test_frontier_row_requires_admission_and_actuated_repair(self) -> None:
        base_result = {
            "status": "ok",
            "qoe_recovery_ok": False,
            "repair_capacity_fault": True,
            "repair_capacity_outcome_ok": True,
            "repair_deadline_robots_ok": 5,
            "fleet_repair_schedule": {
                "admitted_count": 1,
                "deferred_count": 3,
                "allocated_bytes": 700,
                "decisions": [
                    {"robot_id": "robot_0000", "action": "repair"},
                    {"robot_id": "robot_0001", "action": "defer"},
                    {"robot_id": "robot_0002", "action": "defer"},
                    {"robot_id": "robot_0003", "action": "defer"},
                ],
            },
            "fallback_repair": {
                "robots": [
                    {
                        "robot_id": "robot_0000",
                        "status": "repaired_on_time",
                        "repair_evidence": True,
                        "publisher_repair_plan_frames": 1,
                    },
                    *[
                        {
                            "robot_id": f"robot_{index:04d}",
                            "status": "unresolved",
                            "missing_sequences": [2],
                            "publisher_repair_not_admitted": 1,
                        }
                        for index in range(1, 4)
                    ],
                ]
            },
        }
        passed = frontier_row(
            result=base_result,
            robot_count=8,
            protected_count=4,
            repetition_id=7,
            capacity_fraction=0.25,
            capacity_bytes=700,
            admitted_slots=1,
        )
        failed = frontier_row(
            result={**base_result, "repair_deadline_robots_ok": 4},
            robot_count=8,
            protected_count=4,
            repetition_id=7,
            capacity_fraction=0.25,
            capacity_bytes=700,
            admitted_slots=1,
        )

        self.assertEqual(passed["status"], "ok")
        self.assertTrue(passed["admission_ok"])
        self.assertTrue(passed["repair_actuation_ok"])
        self.assertEqual(passed["repair_qualified_ratio"], 0.25)
        self.assertEqual(passed["live_qoe_qualified_ratio"], 0.625)
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["admission_ok"])
        self.assertFalse(failed["repair_actuation_ok"])

    def test_large_scale_comparison_reports_delivery_failures_and_latency_passes(self) -> None:
        rows = [
            normalize_row(
                {
                    "status": "ok",
                    "robot_count": 8,
                    "topic_count": 16,
                    "control_delivery_ratio": 1.0,
                    "state_delivery_ratio": 1.0,
                    "min_topic_delivery_ratio": 1.0,
                    "control_latency_ms_p95": 80.0,
                    "state_latency_ms_p95": 82.0,
                },
                system="rmw_fleetqox_cpp_router",
            ),
            normalize_row(
                {
                    "status": "failed",
                    "robot_count": 8,
                    "topic_count": 16,
                    "control_delivery_ratio": 0.0,
                    "state_delivery_ratio": 0.0,
                    "min_topic_delivery_ratio": 0.0,
                    "control_latency_ms_p95": 0.0,
                    "state_latency_ms_p95": 0.0,
                },
                system="rmw_fleetqox_cpp_router",
            ),
        ]

        aggregates = aggregate_comparison(rows)

        self.assertEqual(aggregates[0]["run_count"], 2)
        self.assertEqual(aggregates[0]["ok_run_count"], 1)
        self.assertEqual(aggregates[0]["control_delivery_ratio_mean"], 0.5)
        self.assertEqual(aggregates[0]["control_latency_ms_p95_mean"], 80.0)
        self.assertEqual(aggregates[0]["success_rate_mean"], 0.5)

    def test_metric_summary_reports_three_seed_student_t_interval(self) -> None:
        summary = metric_summary(
            [{"value": 90.0}, {"value": 100.0}, {"value": 110.0}],
            "value",
        )

        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["mean"], 100.0)
        self.assertLess(summary["ci95_low"], 90.0)
        self.assertGreater(summary["ci95_high"], 110.0)

    def test_resume_only_reruns_infrastructure_failures(self) -> None:
        delivery_failure = normalize_row(
            {
                "status": "failed",
                "robot_count": 8,
                "topic_count": 16,
                "control_payload_count": 39,
                "control_expected_count": 40,
                "state_payload_count": 40,
                "state_expected_count": 40,
                "subscriber_returncode": 1,
            },
            system="rmw_fastrtps_cpp",
        )
        lifecycle_failure = normalize_row(
            {
                "status": "failed",
                "robot_count": 16,
                "topic_count": 32,
                "control_payload_count": 80,
                "control_expected_count": 80,
                "state_payload_count": 80,
                "state_expected_count": 80,
                "publisher_returncode": 139,
            },
            system="rmw_fleetqox_cpp_router",
        )

        self.assertFalse(row_needs_infrastructure_rerun(delivery_failure))
        self.assertTrue(row_needs_infrastructure_rerun(lifecycle_failure))

    def test_large_scale_report_preserves_topology_caveat(self) -> None:
        markdown = render_comparison_markdown(
            {
                "comparison_design": "split_scope_topology_caveated",
                "direct_claim_allowed": False,
                "topology_note": (
                    "FleetRMW uses publisher-router-subscriber; DDS/Zenoh rows "
                    "use direct publisher-subscriber."
                ),
                "aggregates": [
                    {
                        "system": "rmw_fleetqox_cpp_router",
                        "robot_count": 8,
                        "ok_run_count": 1,
                        "run_count": 1,
                        "success_rate_mean": 1.0,
                        "success_rate_ci95_low": 0.2,
                        "success_rate_ci95_high": 1.0,
                        "control_delivery_ratio_mean": 1.0,
                        "control_delivery_ratio_ci95_low": 1.0,
                        "control_delivery_ratio_ci95_high": 1.0,
                        "state_delivery_ratio_mean": 1.0,
                        "state_delivery_ratio_ci95_low": 1.0,
                        "state_delivery_ratio_ci95_high": 1.0,
                        "min_topic_delivery_ratio_mean": 1.0,
                        "min_topic_delivery_ratio_ci95_low": 1.0,
                        "min_topic_delivery_ratio_ci95_high": 1.0,
                        "control_latency_ms_p95_mean": 80.0,
                        "control_latency_ms_p95_ci95_low": 80.0,
                        "control_latency_ms_p95_ci95_high": 80.0,
                        "state_latency_ms_p95_mean": 82.0,
                        "state_latency_ms_p95_ci95_low": 82.0,
                        "state_latency_ms_p95_ci95_high": 82.0,
                        "reliability_modes": ["ack_timeout_retransmit"],
                    }
                ],
            }
        )

        self.assertIn("mixed-hop table", markdown)
        self.assertIn("publisher-router-subscriber", markdown)
        self.assertIn("cross-scope superiority allowed: `false`", markdown)
        self.assertIn("Disallowed scope", markdown)
        self.assertIn("ack_timeout_retransmit", markdown)


if __name__ == "__main__":
    unittest.main()
