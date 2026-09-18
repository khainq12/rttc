import csv
import tempfile
import unittest
from pathlib import Path

from scripts.run_ns3_docker_container_fleet_probe import (
    BASE_IP_PREFIX,
    RMW_PORT,
    STATIC_SUBSCRIPTION_TYPE_NAME,
    ReferenceTopologyProbe,
    build_static_subscriptions,
    compute_coordination_metrics,
    compute_graph_join_failures,
    compute_jitter_stale_repair_stats,
    compute_latency_stats_ms,
    endpoint_list,
    fleetqox_rmw_env_prefix,
    parse_docker_mem_usage_mb,
    station_mac,
    topic_for,
)


class EndpointListTest(unittest.TestCase):
    def test_control_station_plus_robots_in_order(self):
        self.assertEqual(
            endpoint_list(3),
            ["control_station", "robot_0000", "robot_0001", "robot_0002"],
        )

    def test_zero_robots_is_just_control_station(self):
        self.assertEqual(endpoint_list(0), ["control_station"])

    def test_endpoint_count_matches_reference_diagram(self):
        # The reference topology diagram specifies 16 robots + 1 control
        # station == 17 total endpoints.
        self.assertEqual(len(endpoint_list(16)), 17)


class StationMacTest(unittest.TestCase):
    def test_index_zero(self):
        self.assertEqual(station_mac(0), "02:00:00:00:00:00")

    def test_index_encodes_into_last_two_octets(self):
        self.assertEqual(station_mac(1), "02:00:00:00:00:01")
        self.assertEqual(station_mac(256), "02:00:00:00:01:00")

    def test_matches_run_ns3_docker_wifi_tap_rmw_probe_formula(self):
        # MUST stay identical to the existing harness's _station_mac() --
        # both are matched independently against
        # fleetqox_trace_replay_tap.cc's own stationMacs formula, so a
        # drift between the two Python implementations would only surface
        # as a real-run failure, never a test failure, unless checked here.
        from scripts.run_ns3_docker_wifi_tap_rmw_probe import _station_mac

        for index in (0, 1, 2, 255, 256, 4095):
            self.assertEqual(station_mac(index), _station_mac(index))


class TopicForTest(unittest.TestCase):
    def test_point_to_point_topic(self):
        self.assertEqual(topic_for("robot_0000", "control"), "/fleetqox_trace/robot_0000/control")

    def test_sanitizes_non_ros2_topic_characters(self):
        self.assertEqual(
            topic_for("robot-0000", "human/qoe"), "/fleetqox_trace/robot_0000/human_qoe"
        )


def _simulate_effective_stream_identity(
    robot_id_env: str | None, topic: str, publisher_creation_index: int
) -> str:
    """Mirrors rmw_pubsub.cpp's local_robot_id() + allocate_publisher_id()
    + stream_key() formula purely in Python, so the harness's env-var
    wiring can be regression-tested without a live RMW process (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md 18/09/2026 "ROOT CAUSE TÌM RA" for
    the full C++ trail this was read directly from):
      - local_robot_id() (rmw_pubsub.cpp:9376) returns the literal
        string "local" whenever FLEETQOX_RMW_ROBOT_ID is unset in the
        process environment.
      - allocate_publisher_id() (rmw_pubsub.cpp:10325) =
        "fpubcpp-" + LOCAL bind address + "-" + a per-process counter --
        the bind address ("0.0.0.0:9100" here) is IDENTICAL across
        every container, so the Nth publisher created in ANY process
        gets the same id string.
      - stream_key() (data_frame.cpp:621) = robot_id + "|" + topic +
        "|" + publisher_id.
    """
    robot_id = robot_id_env if robot_id_env else "local"
    bound_endpoint = "0.0.0.0:9100"
    publisher_id = f"fpubcpp-{bound_endpoint}-{publisher_creation_index}"
    return f"{robot_id}|{topic}|{publisher_id}"


class FleetqoxRmwEnvPrefixTest(unittest.TestCase):
    """RED/GREEN for the 18/09/2026 FLEETQOX_RMW_ROBOT_ID collision fix
    (see docs/AUDIT_ACCEPTANCE_TRACKING.md "ROOT CAUSE TÌM RA: publisher_id/
    robot_id COLLISION"). Live LAN N=16 measurement (previous pass)
    proved 463/463 (100.0%, all 3 reps) residual loss on the 5 uplink
    flows was explained by control_station's per-subscription
    duplicate-detection SequenceState being silently SHARED across all
    16 robots -- because none of them ever had a unique
    FLEETQOX_RMW_ROBOT_ID, so every robot's Nth-created publisher for a
    shared topic collided in full stream identity with every other
    robot's Nth-created publisher.
    """

    def test_red_old_env_prefix_never_set_robot_id_and_collides(self):
        """RED: reproduces the OLD env_prefix formula inline -- exactly
        as it existed in launch_endpoints() before this fix, with no
        FLEETQOX_RMW_ROBOT_ID key at all -- and shows two DIFFERENT
        robot endpoints' Nth-created publisher for the same topic
        produce the IDENTICAL effective stream identity. This is the
        proven bug being reproduced, not a test error."""

        def old_env_prefix(peers: str) -> str:
            return (
                f"RMW_IMPLEMENTATION=rmw_fleetqox_cpp FLEETQOX_RMW_BIND=0.0.0.0:{RMW_PORT} "
                f"FLEETQOX_RMW_PEERS={peers} "
            )

        env_robot_a = old_env_prefix("10.60.0.2:9100")
        env_robot_b = old_env_prefix("10.60.0.2:9100")
        self.assertNotIn(
            "FLEETQOX_RMW_ROBOT_ID", env_robot_a,
            "sanity: the OLD formula really never set this var",
        )
        self.assertNotIn("FLEETQOX_RMW_ROBOT_ID", env_robot_b)

        # Neither container's env sets FLEETQOX_RMW_ROBOT_ID -> both
        # processes' local_robot_id() falls back to "local" regardless
        # of which robot they actually are.
        identity_robot_a = _simulate_effective_stream_identity(
            robot_id_env=None,
            topic="/fleetqox_trace/control_station/debug",
            publisher_creation_index=4,
        )
        identity_robot_b = _simulate_effective_stream_identity(
            robot_id_env=None,
            topic="/fleetqox_trace/control_station/debug",
            publisher_creation_index=4,
        )
        self.assertEqual(
            identity_robot_a, identity_robot_b,
            "RED: two DIFFERENT robots' 4th-created publisher for the "
            "same topic must collide under the OLD (pre-fix) env",
        )

    def test_green_sets_robot_id_matching_the_canonical_endpoint_name(self):
        env = fleetqox_rmw_env_prefix("robot_0000", "10.60.0.2:9100", False, [], None)
        self.assertIn("FLEETQOX_RMW_ROBOT_ID=robot_0000 ", env)

    def test_green_control_station_also_gets_its_own_id(self):
        env = fleetqox_rmw_env_prefix("control_station", "10.60.0.3:9100", False, [], None)
        self.assertIn("FLEETQOX_RMW_ROBOT_ID=control_station ", env)

    def test_green_all_endpoints_get_unique_deterministic_ids(self):
        endpoints = endpoint_list(16)
        ids = []
        for endpoint in endpoints:
            env = fleetqox_rmw_env_prefix(endpoint, "peer:9100", False, [], None)
            tokens = [tok for tok in env.split() if tok.startswith("FLEETQOX_RMW_ROBOT_ID=")]
            self.assertEqual(len(tokens), 1, f"exactly one ROBOT_ID entry for {endpoint}")
            robot_id = tokens[0].split("=", 1)[1]
            self.assertEqual(robot_id, endpoint, "id must match the intended canonical endpoint name")
            ids.append(robot_id)
        self.assertEqual(len(ids), len(set(ids)), "every endpoint must get a UNIQUE id")
        # Deterministic across repeated calls (repeatable benchmark reps).
        for endpoint in endpoints:
            self.assertEqual(
                fleetqox_rmw_env_prefix(endpoint, "peer:9100", False, [], None),
                fleetqox_rmw_env_prefix(endpoint, "peer:9100", False, [], None),
            )

    def test_green_static_mode_and_extra_env_still_present(self):
        env = fleetqox_rmw_env_prefix(
            "robot_0000", "peer:9100", True, ["1.2.3.4:9100|0|/t|Type"], {"FOO": "bar"}
        )
        self.assertIn("FLEETQOX_RMW_ROBOT_ID=robot_0000 ", env)
        self.assertIn("FLEETQOX_RMW_STATIC_MODE=1", env)
        self.assertIn("FOO=bar", env)

    def test_green_two_robots_no_longer_collide_in_stream_identity(self):
        identity_robot_a = _simulate_effective_stream_identity(
            robot_id_env="robot_0000",
            topic="/fleetqox_trace/control_station/debug",
            publisher_creation_index=4,
        )
        identity_robot_b = _simulate_effective_stream_identity(
            robot_id_env="robot_0001",
            topic="/fleetqox_trace/control_station/debug",
            publisher_creation_index=4,
        )
        self.assertNotEqual(
            identity_robot_a, identity_robot_b,
            "GREEN: a distinct robot_id per endpoint must break the collision",
        )


class BuildStaticSubscriptionsTest(unittest.TestCase):
    def test_maps_publisher_to_dst_flow_class_pairs(self):
        endpoints = ["control_station", "robot_0000", "robot_0001"]
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.csv"
            with trace_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["policy", "src", "dst", "flow_class", "event_id"]
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "policy": "fifo", "src": "control_station", "dst": "robot_0000",
                        "flow_class": "control", "event_id": 0,
                    }
                )
                writer.writerow(
                    {
                        "policy": "fifo", "src": "robot_0000", "dst": "control_station",
                        "flow_class": "state", "event_id": 1,
                    }
                )
                writer.writerow(
                    {
                        "policy": "static_priority", "src": "robot_0001", "dst": "control_station",
                        "flow_class": "state", "event_id": 2,
                    }
                )
            result = build_static_subscriptions(trace_path, "fifo", endpoints)
        self.assertEqual(
            result,
            {
                "control_station": [("robot_0000", "control")],
                "robot_0000": [("control_station", "state")],
                "robot_0001": [],
            },
        )


class ComputeLatencyStatsMsTest(unittest.TestCase):
    def test_none_when_nothing_delivered(self):
        self.assertIsNone(compute_latency_stats_ms({"robot_0000": {"received": []}}))
        self.assertIsNone(compute_latency_stats_ms({"robot_0000": None}))

    def test_aggregates_across_endpoints_and_converts_to_ms(self):
        # 1ms, 2ms, ..., 100ms spread across two endpoints -- p50 should
        # land near the middle and p99 near the top of that range.
        endpoint_results = {
            "control_station": {
                "received": [
                    {"sent_wall_ns": 0, "recv_wall_ns": i * 1_000_000} for i in range(1, 51)
                ]
            },
            "robot_0000": {
                "received": [
                    {"sent_wall_ns": 0, "recv_wall_ns": i * 1_000_000} for i in range(51, 101)
                ]
            },
        }
        stats = compute_latency_stats_ms(endpoint_results)
        self.assertEqual(stats["n"], 100)
        self.assertAlmostEqual(stats["p50_ms"], 50, delta=1)
        self.assertAlmostEqual(stats["p99_ms"], 99, delta=1)
        self.assertAlmostEqual(stats["max_ms"], 100, delta=0.001)


class ComputeJitterStaleRepairStatsTest(unittest.TestCase):
    def test_empty_gives_none_everywhere(self):
        stats = compute_jitter_stale_repair_stats({"robot_0000": {"received": []}})
        self.assertIsNone(stats["jitter_ms"])
        self.assertIsNone(stats["stale_ratio"])
        self.assertIsNone(stats["repair_amp"])
        self.assertFalse(stats["repair_amp_available"])

    def test_stale_ratio_counts_late_messages_only(self):
        endpoint_results = {
            "control_station": {
                "received": [
                    # on-time: 50ms latency, 100ms deadline
                    {"sent_wall_ns": 0, "recv_wall_ns": 50_000_000, "deadline_ms": 100.0},
                    # late: 150ms latency, 100ms deadline
                    {"sent_wall_ns": 0, "recv_wall_ns": 150_000_000, "deadline_ms": 100.0},
                ]
            }
        }
        stats = compute_jitter_stale_repair_stats(endpoint_results)
        self.assertEqual(stats["stale_ratio"], 0.5)
        self.assertIsNotNone(stats["jitter_ms"])

    def test_repair_amp_only_available_with_fleetqox_metrics(self):
        endpoint_results = {
            "control_station": {
                "received": [],
                "fleetqox_transport_metrics": {
                    "frames_sent": 100,
                    "nack_retransmissions": 5,
                    "fragments_selectively_retransmitted": 3,
                    "reliable_timeout_retransmissions": 2,
                },
            },
        }
        stats = compute_jitter_stale_repair_stats(endpoint_results)
        self.assertTrue(stats["repair_amp_available"])
        self.assertAlmostEqual(stats["repair_amp"], 10 / 100)

    def test_repair_amp_none_for_non_fleetqox_rmws(self):
        # e.g. a CycloneDDS/Zenoh/FastDDS run -- no fleetqox_transport_metrics
        # key at all, since that's FleetRMW-specific instrumentation.
        endpoint_results = {"control_station": {"received": []}}
        stats = compute_jitter_stale_repair_stats(endpoint_results)
        self.assertFalse(stats["repair_amp_available"])
        self.assertIsNone(stats["repair_amp"])


class ComputeGraphJoinFailuresTest(unittest.TestCase):
    def test_none_when_no_endpoint_ran_the_beacon(self):
        # e.g. an all-rmw_fleetqox_cpp run -- static mode has no discovery
        # step by design, so expected_peers is 0/absent everywhere.
        endpoint_results = {
            "control_station": {"discovery_expected_peers": 0, "discovery_peers_seen": 0},
            "robot_0000": {"discovery_expected_peers": 0, "discovery_peers_seen": 0},
        }
        self.assertIsNone(compute_graph_join_failures(endpoint_results))

    def test_counts_endpoints_that_never_reached_full_peer_count(self):
        endpoint_results = {
            "control_station": {"discovery_expected_peers": 2, "discovery_peers_seen": 2},
            "robot_0000": {"discovery_expected_peers": 2, "discovery_peers_seen": 2},
            "robot_0001": {"discovery_expected_peers": 2, "discovery_peers_seen": 0},
        }
        result = compute_graph_join_failures(endpoint_results)
        self.assertEqual(result["total_endpoints"], 3)
        self.assertEqual(result["failures"], 1)
        self.assertAlmostEqual(result["failure_rate"], 1 / 3)
        self.assertTrue(result["per_endpoint"]["robot_0001"]["failed"])
        self.assertFalse(result["per_endpoint"]["control_station"]["failed"])

    def test_skips_endpoints_with_no_result(self):
        endpoint_results = {
            "control_station": {"discovery_expected_peers": 1, "discovery_peers_seen": 1},
            "robot_0000": None,
        }
        result = compute_graph_join_failures(endpoint_results)
        self.assertEqual(result["total_endpoints"], 1)


class ParseDockerMemUsageMbTest(unittest.TestCase):
    def test_parses_mib_used_side(self):
        self.assertAlmostEqual(
            parse_docker_mem_usage_mb("45.2MiB / 3.678GiB"), 45.2 * 1024**2 / 1e6, places=3
        )

    def test_parses_gib_used_side(self):
        self.assertAlmostEqual(
            parse_docker_mem_usage_mb("1.5GiB / 3.678GiB"), 1.5 * 1024**3 / 1e6, places=3
        )

    def test_rejects_unrecognized_format(self):
        with self.assertRaises(ValueError):
            parse_docker_mem_usage_mb("not a mem string")


class ParseNrMappingTest(unittest.TestCase):
    """fleetqox_trace_replay_nr.cc's FLEETQOX_NR_MAPPING lines are the
    only channel through which the orchestrator learns each endpoint's
    real EPC-assigned overlay IP (see ReferenceTopologyProbe.start_ns3_nr()/
    finish_wire_network_nr()) -- worth a direct unit test independent of
    any actual ns-3 run."""

    def test_parses_mapping_lines_ignoring_header_and_noise(self):
        log_text = (
            "some ns-3 setup noise\n"
            "FLEETQOX_NR_MAPPING station_index,endpoint,tap_device,ue_overlay_ip,"
            "ghost_link_local_ip\n"
            "FLEETQOX_NR_MAPPING 0,control_station,ntap0,7.0.0.2,172.16.0.1\n"
            "FLEETQOX_NR_MAPPING 1,robot_0000,ntap1,7.0.0.3,172.16.1.1\n"
            "more noise after\n"
        )
        mapping = ReferenceTopologyProbe._parse_nr_mapping(log_text)
        self.assertEqual(
            mapping,
            {
                "control_station": {
                    "tap_device": "ntap0",
                    "ue_overlay_ip": "7.0.0.2",
                    "ghost_link_local_ip": "172.16.0.1",
                },
                "robot_0000": {
                    "tap_device": "ntap1",
                    "ue_overlay_ip": "7.0.0.3",
                    "ghost_link_local_ip": "172.16.1.1",
                },
            },
        )

    def test_empty_log_gives_empty_mapping(self):
        self.assertEqual(ReferenceTopologyProbe._parse_nr_mapping(""), {})

    def test_malformed_line_is_skipped(self):
        log_text = "FLEETQOX_NR_MAPPING 0,control_station,ntap0,7.0.0.2\n"  # missing a field
        self.assertEqual(ReferenceTopologyProbe._parse_nr_mapping(log_text), {})


class ComputeCoordinationMetricsTest(unittest.TestCase):
    """Bảng VI's 4 columns -- see compute_coordination_metrics()'s own
    docstring for the exact definitions this tests against."""

    def test_averages_message_ages_and_clean_resolution_delays(self):
        endpoint_results = {
            "robot_0000": {
                "coordination_message_ages_ms": [10.0, 20.0],
                "coordination_retry_count": 1,
                "task_completion_s": 12.0,
                "crossings": [
                    {"conflict_resolution_delay_ms": 100.0, "forced_entry": False},
                    {"conflict_resolution_delay_ms": 300.0, "forced_entry": False},
                ],
            },
            "robot_0001": {
                "coordination_message_ages_ms": [30.0],
                "coordination_retry_count": 2,
                "task_completion_s": 15.0,
                "crossings": [
                    {"conflict_resolution_delay_ms": 200.0, "forced_entry": False},
                ],
            },
        }
        metrics = compute_coordination_metrics(endpoint_results)
        self.assertAlmostEqual(metrics["coordination_update_age_ms"], 20.0)  # (10+20+30)/3
        self.assertAlmostEqual(metrics["conflict_resolution_delay_ms"], 200.0)  # (100+300+200)/3
        self.assertEqual(metrics["coordination_retry_count"], 3)
        self.assertAlmostEqual(metrics["task_completion_s"], 15.0)  # max, not mean
        self.assertEqual(metrics["total_crossings"], 3)
        self.assertEqual(metrics["forced_crossings"], 0)
        self.assertAlmostEqual(metrics["forced_entry_rate"], 0.0)

    def test_forced_entries_excluded_from_delay_average_but_counted_separately(self):
        endpoint_results = {
            "robot_0000": {
                "coordination_message_ages_ms": [],
                "coordination_retry_count": 5,
                "task_completion_s": 60.0,
                "crossings": [
                    {"conflict_resolution_delay_ms": 9999.0, "forced_entry": True},
                    {"conflict_resolution_delay_ms": 150.0, "forced_entry": False},
                ],
            },
        }
        metrics = compute_coordination_metrics(endpoint_results)
        # Forced entry's delay must NOT pollute the "genuine consensus" average.
        self.assertAlmostEqual(metrics["conflict_resolution_delay_ms"], 150.0)
        self.assertEqual(metrics["total_crossings"], 2)
        self.assertEqual(metrics["forced_crossings"], 1)
        self.assertAlmostEqual(metrics["forced_entry_rate"], 0.5)

    def test_all_forced_gives_none_delay_not_zero(self):
        endpoint_results = {
            "robot_0000": {
                "coordination_message_ages_ms": [],
                "coordination_retry_count": 10,
                "task_completion_s": 120.0,
                "crossings": [
                    {"conflict_resolution_delay_ms": 9999.0, "forced_entry": True},
                ],
            },
        }
        metrics = compute_coordination_metrics(endpoint_results)
        self.assertIsNone(metrics["conflict_resolution_delay_ms"])
        self.assertAlmostEqual(metrics["forced_entry_rate"], 1.0)

    def test_skips_endpoints_with_no_result(self):
        endpoint_results = {"robot_0000": None, "robot_0001": None}
        metrics = compute_coordination_metrics(endpoint_results)
        self.assertIsNone(metrics["coordination_update_age_ms"])
        self.assertIsNone(metrics["conflict_resolution_delay_ms"])
        self.assertEqual(metrics["coordination_retry_count"], 0)
        self.assertIsNone(metrics["task_completion_s"])
        self.assertIsNone(metrics["forced_entry_rate"])


class ConstantsTest(unittest.TestCase):
    def test_distinct_ip_prefix_from_single_container_harness(self):
        from scripts.run_ns3_docker_wifi_tap_rmw_probe import BASE_IP_PREFIX as OLD_PREFIX

        self.assertNotEqual(BASE_IP_PREFIX, OLD_PREFIX)

    def test_rmw_port_and_type_name_are_sane(self):
        self.assertEqual(RMW_PORT, 9100)
        self.assertEqual(STATIC_SUBSCRIPTION_TYPE_NAME, "std_msgs/msg/String")


if __name__ == "__main__":
    unittest.main()
