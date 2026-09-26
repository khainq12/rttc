import csv
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.run_ns3_docker_container_fleet_probe import (
    BASE_IP_PREFIX,
    LAN_DISCOVERY_WATCHDOG_S,
    MAX_HEALTHY_SIM_LAG_S,
    NS3_SIM_DURATION_DRAIN_MARGIN_S,
    RMW_PORT,
    STATIC_SUBSCRIPTION_TYPE_NAME,
    ReferenceTopologyProbe,
    build_static_subscriptions,
    compute_coordination_metrics,
    compute_graph_join_failures,
    compute_jitter_stale_repair_stats,
    compute_latency_stats_ms,
    corrected_sim_lag_s,
    effective_ns3_sim_duration_s,
    endpoint_list,
    fleetqox_coordination_rmw_env_prefix,
    fleetqox_rmw_env_prefix,
    parse_docker_mem_usage_mb,
    parse_last_wifi_stats,
    parse_wifi_stats,
    required_peers_from_trace,
    run_coordination_probe,
    run_lan_probe,
    run_nr_probe,
    run_probe,
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


class FleetqoxCoordinationRmwEnvPrefixTest(unittest.TestCase):
    """RED/GREEN for the Table VI robot-identity-collision fix (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md "TABLE VI FLEETRMW TRANSPORT LOSS
    FUNNEL"): launch_coordination_endpoints() built its own, separate,
    inline env_prefix and never called fleetqox_rmw_env_prefix() (the
    function that already carries this exact fix for Table IV/V's
    launch_endpoints()) -- so every Table VI endpoint's
    local_robot_id() fell back to "local", proven at runtime (N=4
    seed=7) to cause 363/488 (74.4%) of physically-arrived coordination
    messages to be misclassified as duplicates and silently dropped.

    fleetqox_coordination_rmw_env_prefix() must set
    FLEETQOX_RMW_ROBOT_ID={endpoint} (fixing the collision) while
    preserving Table VI's own deliberate choice of NOT setting
    FLEETQOX_RMW_PEER_POLICY/FLEETQOX_RMW_STATIC_SUBSCRIPTIONS (Table
    VI's Ricart-Agrawala traffic is broadcast-to-everyone by design,
    unlike Table IV/V's subscription_aware star topology) -- i.e. fix
    ONLY the identity bug, change nothing else about the wire
    behavior.
    """

    def test_red_sets_robot_id(self):
        env = fleetqox_coordination_rmw_env_prefix("robot_0000", "peer:9100", None)
        self.assertIn("FLEETQOX_RMW_ROBOT_ID=robot_0000 ", env)

    def test_control_station_gets_its_own_id_too(self):
        env = fleetqox_coordination_rmw_env_prefix("control_station", "peer:9100", None)
        self.assertIn("FLEETQOX_RMW_ROBOT_ID=control_station ", env)

    def test_every_endpoint_gets_a_unique_id(self):
        endpoints = endpoint_list(4)
        ids = []
        for endpoint in endpoints:
            env = fleetqox_coordination_rmw_env_prefix(endpoint, "peer:9100", None)
            tokens = [tok for tok in env.split() if tok.startswith("FLEETQOX_RMW_ROBOT_ID=")]
            self.assertEqual(len(tokens), 1, f"exactly one ROBOT_ID entry for {endpoint}")
            self.assertEqual(tokens[0].split("=", 1)[1], endpoint)
            ids.append(tokens[0])
        self.assertEqual(len(ids), len(set(ids)), "every endpoint must get a UNIQUE id")

    def test_preserves_broadcast_to_everyone_peer_policy_untouched(self):
        # Table VI deliberately leaves FLEETQOX_RMW_PEER_POLICY at its
        # own RMW-side default ("all") -- see
        # launch_coordination_endpoints()'s own comment on why
        # subscription_aware mode/a static subscriptions map is wrong
        # for this scenario's broadcast-to-everyone shape. The identity
        # fix must not change this.
        env = fleetqox_coordination_rmw_env_prefix("robot_0000", "peer:9100", None)
        self.assertNotIn("FLEETQOX_RMW_PEER_POLICY", env)
        self.assertNotIn("FLEETQOX_RMW_STATIC_SUBSCRIPTIONS", env)

    def test_still_sets_static_mode(self):
        env = fleetqox_coordination_rmw_env_prefix("robot_0000", "peer:9100", None)
        self.assertIn("FLEETQOX_RMW_STATIC_MODE=1", env)

    def test_extra_rmw_env_still_passed_through(self):
        env = fleetqox_coordination_rmw_env_prefix(
            "robot_0000", "peer:9100", {"FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1"}
        )
        self.assertIn("FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING=1", env)

    def test_peers_are_wired_through(self):
        env = fleetqox_coordination_rmw_env_prefix("robot_0000", "10.60.0.5:9100", None)
        self.assertIn("FLEETQOX_RMW_PEERS=10.60.0.5:9100 ", env)


class AckNackRedundancyZeroTableViScopeTest(unittest.TestCase):
    """Proves the "TABLE VI ACK/NACK REDUNDANCY=0 ADOPTION" config change
    (see docs/AUDIT_ACCEPTANCE_TRACKING.md) is scoped EXACTLY to Table
    VI's coordination benchmark: a clean, harness-fixed, 5-seed N=8 A/B
    showed FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=10 (the
    production default) was simulator-INVALID on 5/5 seeds while =0 was
    VALID and fully healthy on 5/5 -- but this must NOT become a new
    global/production default. fleetqox_coordination_rmw_env_prefix()
    (Table VI only) must default to "0"; fleetqox_rmw_env_prefix()
    (Table IV/V, and the function every other FleetRMW workload's env
    construction is built from) must NEVER set this variable at all,
    leaving rmw_pubsub.cpp's own compiled-in default (10) untouched for
    every other workload."""

    def test_table_vi_defaults_to_redundancy_zero(self):
        env = fleetqox_coordination_rmw_env_prefix("robot_0000", "peer:9100", None)
        self.assertIn("FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=0", env)

    def test_table_vi_default_applies_to_every_endpoint(self):
        for endpoint in endpoint_list(8):
            env = fleetqox_coordination_rmw_env_prefix(endpoint, "peer:9100", None)
            self.assertIn("FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=0", env)

    def test_explicit_caller_override_still_wins(self):
        # This investigation's own A/B experiment scripts must still be
        # able to explicitly request "10" to reproduce/compare against
        # the unhealthy production-default case.
        env = fleetqox_coordination_rmw_env_prefix(
            "robot_0000", "peer:9100", {"FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT": "10"}
        )
        self.assertIn("FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=10", env)
        self.assertNotIn("FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=0", env)

    def test_other_extra_rmw_env_keys_unaffected(self):
        env = fleetqox_coordination_rmw_env_prefix(
            "robot_0000", "peer:9100", {"FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1"}
        )
        self.assertIn("FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=0", env)
        self.assertIn("FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING=1", env)

    def test_other_fleetrmw_workloads_do_not_inherit_this_setting(self):
        # fleetqox_rmw_env_prefix() backs Table IV/V's launch_endpoints()
        # -- and, transitively (per fleetqox_coordination_rmw_env_prefix's
        # own "thin wrapper" design), is the ONLY place any RMW env
        # actually gets constructed. Calling it DIRECTLY (as every non-
        # Table-VI workload does) must never see this key: the global/
        # production default is unchanged.
        env = fleetqox_rmw_env_prefix("robot_0000", "peer:9100", False, [], None)
        self.assertNotIn("FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT", env)

    def test_other_fleetrmw_workloads_do_not_inherit_even_with_extra_env(self):
        env = fleetqox_rmw_env_prefix(
            "robot_0000", "peer:9100", True, [], {"FOO": "bar"}
        )
        self.assertNotIn("FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT", env)
        self.assertIn("FOO=bar", env)


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


class RequiredPeersFromTraceTest(unittest.TestCase):
    """Phase 1 of the LAN topology-aware readiness fix (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN READINESS GATE: TOPOLOGY-
    AWARE FIX"): required_peers_from_trace() must derive each
    endpoint's required discovery peers from the trace's OWN src/dst
    edges, not assume any particular topology shape."""

    def test_star_workload_yields_star_peer_sets(self):
        # Exactly Table V's actual shape (empirically confirmed against
        # a real generate_trace_events() output): every flow is
        # control_station<->robot_i, never robot<->robot.
        endpoints = ["control_station", "robot_0000", "robot_0001", "robot_0002"]
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.csv"
            with trace_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["policy", "src", "dst", "flow_class", "event_id"]
                )
                writer.writeheader()
                writer.writerow(
                    {"policy": "fifo", "src": "control_station", "dst": "robot_0000",
                     "flow_class": "control", "event_id": 0}
                )
                writer.writerow(
                    {"policy": "fifo", "src": "robot_0000", "dst": "control_station",
                     "flow_class": "state", "event_id": 1}
                )
                writer.writerow(
                    {"policy": "fifo", "src": "control_station", "dst": "robot_0001",
                     "flow_class": "control", "event_id": 2}
                )
                # robot_0002 never appears at all in the "fifo" policy's
                # rows -- must end up with an EMPTY required-peer set,
                # not a full-mesh assumption.
                writer.writerow(
                    {"policy": "static_priority", "src": "robot_0002", "dst": "control_station",
                     "flow_class": "state", "event_id": 3}
                )
            result = required_peers_from_trace(trace_path, "fifo", endpoints)
        self.assertEqual(
            result,
            {
                "control_station": frozenset({"robot_0000", "robot_0001"}),
                "robot_0000": frozenset({"control_station"}),
                "robot_0001": frozenset({"control_station"}),
                "robot_0002": frozenset(),
            },
        )
        # The defining property of a star: no robot requires any other
        # robot.
        for robot in ("robot_0000", "robot_0001", "robot_0002"):
            self.assertNotIn("robot_0000", result[robot] - {robot})
            self.assertTrue(result[robot] <= {"control_station"})

    def test_non_star_workload_derives_its_own_edges(self):
        # If a future workload ever schedules a genuine robot<->robot
        # flow, this function must reflect THAT edge too -- it makes no
        # topology assumption of its own, it only reads the trace.
        endpoints = ["control_station", "robot_0000", "robot_0001"]
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.csv"
            with trace_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["policy", "src", "dst", "flow_class", "event_id"]
                )
                writer.writeheader()
                writer.writerow(
                    {"policy": "fifo", "src": "robot_0000", "dst": "robot_0001",
                     "flow_class": "state", "event_id": 0}
                )
            result = required_peers_from_trace(trace_path, "fifo", endpoints)
        self.assertEqual(
            result,
            {
                "control_station": frozenset(),
                "robot_0000": frozenset({"robot_0001"}),
                "robot_0001": frozenset({"robot_0000"}),
            },
        )


class ZenohSessionConfigJson5Test(unittest.TestCase):
    """LAN Zenoh listen-address fix (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN DISCOVERY SEMANTIC-LAYER
    INVESTIGATION"): RUST_LOG=zenoh=debug tracing of a real run proved
    control_station's session, when given no explicit config, falls
    back to `listen.endpoints = [tcp/localhost:0]` -- unreachable from
    any other container's own network namespace, unlike every other
    endpoint which listens on `tcp/[::]:0` (all interfaces) because it
    HAS an explicit config. needs_explicit_listen=True must produce a
    config with a `listen` clause on the endpoint's own real IP;
    needs_explicit_listen=False (every non-router endpoint) must be
    unchanged from before this fix -- connect-only, no listen clause."""

    def test_router_host_gets_explicit_real_ip_listen(self):
        config = ReferenceTopologyProbe.zenoh_session_config_json5(
            "10.60.0.2", "tcp/10.60.0.2:7447", True
        )
        self.assertIn('connect: { endpoints: ["tcp/10.60.0.2:7447"]', config)
        self.assertIn('listen: { endpoints: ["tcp/10.60.0.2:0"]', config)

    def test_non_router_host_is_connect_only_unchanged(self):
        config = ReferenceTopologyProbe.zenoh_session_config_json5(
            "10.60.0.3", "tcp/10.60.0.2:7447", False
        )
        self.assertEqual(config, '{ connect: { endpoints: ["tcp/10.60.0.2:7447"] } }')
        self.assertNotIn("listen", config)


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


class EffectiveNs3SimDurationSTest(unittest.TestCase):
    """PROVEN BUG (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "N=8 REALTIME-LAG
    VALIDATION" / "TABLE VI HARNESS DURATION MISMATCH FIX"):
    run_coordination_probe() used to hardcode sim_duration_s=60.0,
    completely independent of scenario_timeout_s (default 120.0) --
    ns-3's own Simulator::Stop() could fire, silently killing the
    simulated Wi-Fi network, while the coordination workload (Python
    side, a totally separate clock) was still legitimately running for
    up to another 60 real seconds. Every N=8 measurement in this
    investigation's history was made under that mismatch. This test
    proves the derived default always covers the workload's own full
    possible runtime (start_offset_ms + scenario_timeout_s) plus a
    drain margin, for any scenario_timeout_s -- not just the default."""

    def test_default_covers_full_scenario_timeout_plus_start_offset(self):
        result = effective_ns3_sim_duration_s(
            sim_duration_s=None, scenario_timeout_s=120.0, start_offset_ms=2000.0
        )
        self.assertGreaterEqual(result, 2000.0 / 1000.0 + 120.0)

    def test_covers_scenario_timeout_at_any_value_not_just_the_old_hardcoded_case(self):
        # The old bug (sim_duration_s=60.0 fixed) only happened to be
        # "close" at scenario_timeout_s values near 60 -- proving this
        # holds across a spread of values is what actually closes the
        # bug class, not just the one default combination.
        for scenario_timeout_s in (10.0, 30.0, 60.0, 90.0, 120.0, 300.0):
            with self.subTest(scenario_timeout_s=scenario_timeout_s):
                result = effective_ns3_sim_duration_s(
                    sim_duration_s=None,
                    scenario_timeout_s=scenario_timeout_s,
                    start_offset_ms=2000.0,
                )
                self.assertGreaterEqual(result, 2000.0 / 1000.0 + scenario_timeout_s)

    def test_includes_a_positive_drain_margin_beyond_the_bare_minimum(self):
        # Not just "greater or equal" by luck -- there must be actual
        # slack for in-flight repair/ACK traffic to resolve after the
        # workload's own last legitimate send.
        result = effective_ns3_sim_duration_s(
            sim_duration_s=None, scenario_timeout_s=120.0, start_offset_ms=2000.0
        )
        self.assertGreaterEqual(
            result, 2000.0 / 1000.0 + 120.0 + NS3_SIM_DURATION_DRAIN_MARGIN_S - 1e-9
        )

    def test_explicit_override_still_respected(self):
        # A caller deliberately requesting a short duration (e.g. this
        # investigation's own smoke tests) must still be able to opt out
        # of the derived default.
        result = effective_ns3_sim_duration_s(
            sim_duration_s=15.0, scenario_timeout_s=120.0, start_offset_ms=2000.0
        )
        self.assertEqual(result, 15.0)

    def test_drain_margin_matches_wait_for_completions_own_slack_constant(self):
        # One clear duration contract: the SAME slack this codebase
        # already grants the workload to finish (see
        # wait_for_completion()'s own timeout_s formula) is what backs
        # ns-3's own stop time too, rather than two independently-
        # guessed numbers that could drift apart again.
        self.assertEqual(NS3_SIM_DURATION_DRAIN_MARGIN_S, 60.0)


class LanReadinessWatchdogTest(unittest.TestCase):
    """PROVEN (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN SOURCE +
    OFFICIAL-DOCUMENTATION AUDIT" Phases 1-8): the old fixed
    discovery_timeout_s=15.0 for LAN Table V had no scientific
    justification (Phase 3, git-history audit) and is HALF of
    CycloneDDS 0.10.5's own documented default SPDPInterval (30s,
    Phase 1, cited from the installed ddsi_cfgelems.h) -- a
    still-normally-converging CycloneDDS participant relying on the
    plain periodic (non-burst-accelerated) SPDP cycle can legitimately
    need close to the full 30s and get incorrectly rejected by a 15s
    watchdog. This does not test any live network behavior (that's
    the already-completed live A/B evidence) -- it tests the single
    fair-contract policy decision: LAN's own watchdog constant must be
    evidence-derived and comfortably cover the slowest documented
    normal middleware behavior, while every OTHER profile (Wi-Fi,
    Table VI, 5G -- none audited or authorized to change here) keeps
    its original, untouched default."""

    def test_lan_watchdog_covers_cyclonedds_documented_spdp_interval(self):
        # CycloneDDS's own installed config schema documents
        # Discovery/SPDPInterval's default as "30 s" -- the watchdog
        # must be strictly greater than that, not merely equal, so a
        # worst-case-phase-aligned pair still has room to actually
        # send and be received before the deadline fires.
        cyclonedds_documented_default_spdp_interval_s = 30.0
        self.assertGreater(
            LAN_DISCOVERY_WATCHDOG_S, cyclonedds_documented_default_spdp_interval_s
        )

    def test_lan_watchdog_is_no_longer_the_unjustified_old_default(self):
        self.assertNotEqual(LAN_DISCOVERY_WATCHDOG_S, 15.0)

    def test_run_lan_probe_uses_the_derived_watchdog_by_default(self):
        default = inspect.signature(run_lan_probe).parameters["discovery_timeout_s"].default
        self.assertEqual(default, LAN_DISCOVERY_WATCHDOG_S)

    def test_wifi_table_vi_and_5g_defaults_are_untouched(self):
        # Strict rule: do not touch Wi-Fi/5G, and Table VI is a
        # different benchmark (coordination, not Table V) -- none of
        # these were audited here, so none of them may have silently
        # picked up the new LAN-specific watchdog value.
        self.assertEqual(
            inspect.signature(run_probe).parameters["discovery_timeout_s"].default, 15.0
        )
        self.assertEqual(
            inspect.signature(run_coordination_probe).parameters["discovery_timeout_s"].default,
            15.0,
        )
        self.assertEqual(
            inspect.signature(run_nr_probe).parameters["discovery_timeout_s"].default, 15.0
        )


class WifiReadinessRootCauseFixesTest(unittest.TestCase):
    """PROVEN 24/09/2026 (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "WI-FI
    READINESS ROOT-CAUSE"): three harness/config correctness bugs
    already fixed for LAN were never ported to Wi-Fi's run_probe() --
    full-mesh (not star) readiness, the beacon-starvation deadlock, and
    Zenoh's control_station localhost-listen fallback. All three are
    live-A/B-confirmed to restore N=2/N=4 readiness for CycloneDDS/
    Zenoh/Fast DDS with discovery_timeout_s left at its frozen 15.0.
    They are wired as opt-in run_probe() parameters (default False) so
    the already-committed 23/09/2026 corrected-image Table V numbers
    are reproducible byte-for-byte until a caller explicitly opts in."""

    def test_new_readiness_fix_params_default_to_prior_behavior(self):
        params = inspect.signature(run_probe).parameters
        self.assertFalse(params["topology_aware_readiness"].default)
        self.assertFalse(params["sustain_beacon_until_deadline"].default)
        self.assertFalse(params["zenoh_control_station_explicit_listen"].default)

    def test_discovery_timeout_s_default_unchanged_by_this_fix(self):
        # The three fixes are harness/config correctness fixes, not a
        # timing change -- discovery_timeout_s must stay exactly as it
        # was (15.0, matching LanReadinessWatchdogTest's own Wi-Fi
        # lock-in above) regardless of whether the new flags are used.
        self.assertEqual(
            inspect.signature(run_probe).parameters["discovery_timeout_s"].default, 15.0
        )


class RunProbeSchedulerPlumbingAbsentTest(unittest.TestCase):
    """RED (P2.1, docs/AUDIT_ACCEPTANCE_TRACKING.md, "N=16 SERIOUS
    PERFORMANCE PASS"): ns3::HeapScheduler was already proven a
    semantics-preserving, sim_lag_s-reducing option for Table VI's
    run_coordination_probe() (its own `ns3_scheduler` parameter) --
    ReferenceTopologyProbe.start_ns3() has supported a `scheduler`
    argument (default "map") since that pass. Table V's own run_probe()
    never received the same plumbing: no `ns3_scheduler` parameter on
    its signature, and its own `probe.start_ns3(...)` call site never
    passes a `scheduler=` keyword at all -- proven here, literally, not
    inferred. Every Table V Direct run is therefore silently locked to
    the default `map` scheduler with no way to select `heap`, even
    though the underlying ns-3/C++ support already exists and is
    already proven safe elsewhere in this same codebase."""

    def test_run_probe_has_no_scheduler_parameter(self):
        self.assertNotIn("ns3_scheduler", inspect.signature(run_probe).parameters)

    def test_run_probe_never_passes_scheduler_to_start_ns3(self):
        self.assertNotIn("scheduler=", inspect.getsource(run_probe))


class ReferenceTopologyProbeStartNs3SchedulerCommandLineTest(unittest.TestCase):
    """GREEN: proves the scheduler choice actually reaches the ns-3
    process's own command line (not just a Python-side no-op parameter),
    and that switching it changes NOTHING else about the invocation --
    the same "pure internal event-ordering swap" guarantee already
    established for Table VI (see --scheduler's own doc comment in
    fleetqox_trace_replay_tap.cc)."""

    def _captured_ns3_command(self, **start_ns3_kwargs) -> str:
        probe = object.__new__(ReferenceTopologyProbe)
        probe.num_robots = 8
        probe.ns3sim_name = "fleetqox_test_ns3sim"
        exec_d_calls: list[tuple] = []

        def fake_docker(*args, **kwargs):
            if args[:2] == ("exec", "-d"):
                exec_d_calls.append(args)
                return mock.Mock(returncode=0)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch(
            "scripts.run_ns3_docker_container_fleet_probe.docker", side_effect=fake_docker
        ), mock.patch("scripts.run_ns3_docker_container_fleet_probe.time.sleep"):
            probe.start_ns3(sim_duration_s=20.0, **start_ns3_kwargs)
        return exec_d_calls[0][-1]

    def test_default_scheduler_is_map_on_the_actual_command_line(self):
        self.assertIn("--scheduler=map", self._captured_ns3_command())

    def test_explicit_map_reaches_the_ns3_command_line(self):
        self.assertIn("--scheduler=map", self._captured_ns3_command(scheduler="map"))

    def test_explicit_heap_reaches_the_ns3_command_line(self):
        self.assertIn("--scheduler=heap", self._captured_ns3_command(scheduler="heap"))

    def test_heap_changes_only_the_scheduler_flag_nothing_else(self):
        map_cmd = self._captured_ns3_command(scheduler="map")
        heap_cmd = self._captured_ns3_command(scheduler="heap")
        self.assertEqual(
            map_cmd.replace("--scheduler=map", "--scheduler=X"),
            heap_cmd.replace("--scheduler=heap", "--scheduler=X"),
        )


def _wifi_stats_line(sim_time_s: float, wall_elapsed_s: float) -> str:
    """One synthetic FLEETQOX_WIFI_STATS log line -- only the two fields
    the sim_lag_s fix actually reads (sim_time_s, wall_elapsed_s) need to
    be realistic; the rest of a real line's fields are irrelevant to
    this test and omitted."""
    return (
        f'FLEETQOX_WIFI_STATS {{"sim_time_s":{sim_time_s},'
        f'"wall_elapsed_s":{wall_elapsed_s},'
        f'"sim_lag_s":{wall_elapsed_s - sim_time_s},'
        f'"self_cpu_s":0.0,"self_rss_kb":0,"heavy_tracing":false}}'
    )


class SimLagSMeasurementBugTest(unittest.TestCase):
    """PROVEN BUG (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "SIM_LAG_S
    MEASUREMENT BUG (26/09/2026)"): run_probe()'s old sim_lag_s formula
    was `ns3_real_elapsed_s_at_log_read - wifi_stats["sim_time_s"]` --
    an end-of-run Python wall-clock read (taken after the FULL
    sim_duration_s-length run, plus every endpoint's own drain/shutdown)
    minus the SIMULATED time of parse_wifi_stats()'s deliberately-EARLY,
    target-based snapshot (chosen for MAC/PHY-counter cross-run
    consistency, an unrelated and still-valid reason -- see that
    function's own doc comment). Two timestamps from DIFFERENT
    observation points, several/many real seconds apart -- structurally
    inflating "lag" by roughly the real time it took to run the rest of
    the simulation after that early snapshot, regardless of whether ns-3
    was actually keeping pace. Confirmed live: two independent pristine
    N=4 seed=53 runs showed the old formula swinging 20.3-29.0s while
    ns-3's own internally-consistent (same-instant) lag never exceeded
    6.5s in either run.

    RED: test_old_formula_reports_false_lag_from_mismatched_snapshots
    below reproduces the OLD formula's own arithmetic (not a re-import --
    that code path no longer exists in this file, replaced by the fix)
    against a synthetic log modeling exactly this run shape (early
    target snapshot, healthy final snapshot, large end-of-run real-time
    read) and proves it crosses MAX_HEALTHY_SIM_LAG_S even though the
    simulator was never actually behind. Confirmed by literally running
    this test class against the pre-fix source (via `git stash`) before
    the fix was applied: it failed at IMPORT (corrected_sim_lag_s/
    parse_last_wifi_stats did not exist yet), the same RED signature
    EffectiveNs3SimDurationSTest's own docstring documents for its own
    prior bug fix.

    FIX: corrected_sim_lag_s() reads wall_elapsed_s AND sim_time_s from
    the SAME snapshot line (parse_last_wifi_stats() -- the most recent
    available, i.e. freshest same-instant pair this kill-based
    architecture can offer) and subtracts them directly. No correction
    constant of any kind -- this is a same-instant subtraction using an
    authoritative source ns-3 itself already produces (PrintWifiStats()'s
    own `wallElapsedS - simTimeS`), not an offset applied to the old
    broken value."""

    def test_old_formula_reports_false_lag_from_mismatched_snapshots(self):
        # Models the exact observed pristine N=4 seed=53 shape: an EARLY
        # target snapshot (sim_time=15, healthy 2s lag) and a HEALTHY
        # final snapshot (sim_time=30, still only 2s lag) -- the
        # simulator never fell behind at any point. The old formula used
        # an end-of-run real-elapsed read (44.0s, modeling
        # ns3_real_elapsed_s_at_log_read after the full run + drain +
        # shutdown) combined with the EARLY snapshot's sim_time_s (15).
        log = "\n".join(
            [
                _wifi_stats_line(sim_time_s=5, wall_elapsed_s=5.02),
                _wifi_stats_line(sim_time_s=10, wall_elapsed_s=10.75),
                _wifi_stats_line(sim_time_s=15, wall_elapsed_s=17.06),  # target snapshot
                _wifi_stats_line(sim_time_s=20, wall_elapsed_s=23.30),
                _wifi_stats_line(sim_time_s=25, wall_elapsed_s=29.57),
                _wifi_stats_line(sim_time_s=30, wall_elapsed_s=35.64),  # last/final snapshot
            ]
        )
        target_snapshot = parse_wifi_stats(log, target_sim_time_s=15.0)
        self.assertEqual(target_snapshot["sim_time_s"], 15)
        ns3_real_elapsed_s_at_log_read = 44.03  # modeled end-of-run Python read

        old_formula_result = ns3_real_elapsed_s_at_log_read - target_snapshot["sim_time_s"]
        self.assertGreater(
            old_formula_result,
            MAX_HEALTHY_SIM_LAG_S,
            "the old formula must falsely exceed the validity gate for this "
            "healthy-simulator shape -- that IS the proven bug",
        )

        corrected = corrected_sim_lag_s(log)
        self.assertLessEqual(
            corrected,
            MAX_HEALTHY_SIM_LAG_S,
            "the FIXED formula must correctly report this run as healthy",
        )
        self.assertAlmostEqual(corrected, 35.64 - 30, places=6)

    def test_corrected_lag_uses_last_snapshot_not_target_snapshot(self):
        # Direct proof the fix reads a DIFFERENT snapshot than
        # parse_wifi_stats()'s target-based pick, on purpose.
        log = "\n".join(
            [
                _wifi_stats_line(sim_time_s=15, wall_elapsed_s=17.06),
                _wifi_stats_line(sim_time_s=30, wall_elapsed_s=32.5),
            ]
        )
        target_snapshot = parse_wifi_stats(log, target_sim_time_s=15.0)
        last_snapshot = parse_last_wifi_stats(log)
        self.assertEqual(target_snapshot["sim_time_s"], 15)
        self.assertEqual(last_snapshot["sim_time_s"], 30)
        self.assertAlmostEqual(corrected_sim_lag_s(log), 32.5 - 30, places=6)

    def test_simulator_genuinely_behind_is_still_correctly_flagged_invalid(self):
        # The fix must not become a "always healthy" rubber stamp -- a
        # run where the LAST snapshot itself shows real, growing lag
        # (wall-clock consistently outpacing sim-time) must still fail
        # the validity gate.
        log = "\n".join(
            [
                _wifi_stats_line(sim_time_s=5, wall_elapsed_s=6.0),
                _wifi_stats_line(sim_time_s=10, wall_elapsed_s=14.0),
                _wifi_stats_line(sim_time_s=15, wall_elapsed_s=25.0),
                _wifi_stats_line(sim_time_s=20, wall_elapsed_s=38.0),
            ]
        )
        corrected = corrected_sim_lag_s(log)
        self.assertGreater(corrected, MAX_HEALTHY_SIM_LAG_S)
        self.assertAlmostEqual(corrected, 38.0 - 20.0, places=6)

    def test_single_early_snapshot_target_and_last_coincide(self):
        # When the target-reaching snapshot IS the only (hence also the
        # last) snapshot available, the old and new approaches must
        # agree -- this is the degenerate case where the bug never had
        # room to manifest, a sanity check that the fix doesn't change
        # behavior when there is nothing to mismatch.
        log = _wifi_stats_line(sim_time_s=15, wall_elapsed_s=15.5)
        target_snapshot = parse_wifi_stats(log, target_sim_time_s=15.0)
        last_snapshot = parse_last_wifi_stats(log)
        self.assertEqual(target_snapshot["sim_time_s"], last_snapshot["sim_time_s"])
        self.assertAlmostEqual(corrected_sim_lag_s(log), 0.5, places=6)

    def test_different_sim_duration_and_stats_target_combinations(self):
        # The fix must hold regardless of how far apart
        # wifi_stats_target_s() and the run's actual sim_duration_s are
        # -- the old bug's magnitude scaled with exactly this gap, so a
        # fix that merely shrinks the gap (rather than removing the
        # mismatched-observation-point problem entirely) would still
        # show a residual, duration-dependent error.
        for target_s, last_sim_time_s, last_wall_elapsed_s in (
            (15.0, 30.0, 30.5),  # small gap, healthy
            (15.0, 90.0, 91.0),  # large gap (long sim_duration_s), still healthy
            (30.0, 30.0, 30.2),  # target == last snapshot
            (5.0, 120.0, 121.5),  # very early target, very long run, still healthy
        ):
            with self.subTest(
                target_s=target_s,
                last_sim_time_s=last_sim_time_s,
                last_wall_elapsed_s=last_wall_elapsed_s,
            ):
                log = "\n".join(
                    [
                        _wifi_stats_line(sim_time_s=target_s, wall_elapsed_s=target_s + 0.5),
                        _wifi_stats_line(
                            sim_time_s=last_sim_time_s, wall_elapsed_s=last_wall_elapsed_s
                        ),
                    ]
                )
                corrected = corrected_sim_lag_s(log)
                self.assertAlmostEqual(
                    corrected, last_wall_elapsed_s - last_sim_time_s, places=6
                )
                # None of these healthy scenarios should ever cross the
                # gate regardless of how large the target/duration gap is
                # -- proving the fix's correctness does not degrade as
                # that gap grows (unlike the old formula).
                self.assertLessEqual(corrected, MAX_HEALTHY_SIM_LAG_S)

    def test_validity_gate_constant_unchanged_by_this_fix(self):
        # This fix corrects WHAT is measured, not the threshold it is
        # judged against -- the task's own explicit constraint.
        self.assertEqual(MAX_HEALTHY_SIM_LAG_S, 10.0)

    def test_validity_decision_boundary_exact(self):
        # <=10s validity decision, exercised at and around the boundary.
        healthy_log = _wifi_stats_line(sim_time_s=20.0, wall_elapsed_s=30.0)  # exactly 10.0
        self.assertLessEqual(corrected_sim_lag_s(healthy_log), MAX_HEALTHY_SIM_LAG_S)
        unhealthy_log = _wifi_stats_line(sim_time_s=20.0, wall_elapsed_s=30.001)  # 10.001
        self.assertGreater(corrected_sim_lag_s(unhealthy_log), MAX_HEALTHY_SIM_LAG_S)

    def test_no_snapshot_at_all_returns_none_not_a_false_zero(self):
        self.assertIsNone(corrected_sim_lag_s(""))
        self.assertIsNone(corrected_sim_lag_s("some unrelated ns-3 log output\n"))

    def test_parse_wifi_stats_unaffected_by_this_fix(self):
        # The target-based selection used for MAC/PHY counter consistency
        # must be completely untouched by this fix -- only the LAG
        # computation changes which snapshot it reads.
        log = "\n".join(
            [
                _wifi_stats_line(sim_time_s=15, wall_elapsed_s=17.06),
                _wifi_stats_line(sim_time_s=30, wall_elapsed_s=35.64),
            ]
        )
        self.assertEqual(parse_wifi_stats(log, target_sim_time_s=15.0)["sim_time_s"], 15)


if __name__ == "__main__":
    unittest.main()
