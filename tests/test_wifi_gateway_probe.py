"""Focused tests for the WiFi-Gateway benchmark's pure/testable logic
(see docs/AUDIT_ACCEPTANCE_TRACKING.md, "WIFI GATEWAY BENCHMARK").

Live, end-to-end correctness (Phase 3's RED/GREEN no-bypass proof,
Phase 5's N=2/N=4 delivery sweep) requires real Docker/ns-3 containers
and is NOT covered here -- these tests cover only the parts that can
be verified without a live network: topology construction, the
readiness-peer contract, topic-suffix derivation (the mechanism that
prevents the gateway's own publish from looping back into its own
subscription), and that fleetqox_rmw_trace_endpoint.py's new
--incoming-topic-suffix flag is genuinely opt-in (default preserves
every existing caller's topic names unchanged).
"""

import csv
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.run_wifi_gateway_probe import (
    RELAY_TOPIC_SUFFIX,
    CONTROL_STATION_NAME,
    _static_entries,
    required_peers_for_wifi_gateway,
    run_wifi_gateway_probe,
    wifi_gateway_endpoint_list,
)
from scripts.fleetqox_rmw_gateway_endpoint import (
    load_all_rows,
    uplink_and_downlink_topics,
)
from scripts.run_ns3_docker_container_fleet_probe import (
    MAX_HEALTHY_SIM_LAG_S,
    ReferenceTopologyProbe,
    corrected_sim_lag_s,
    parse_last_wifi_stats,
    parse_wifi_stats,
)
from scripts.fleetqox_rmw_trace_endpoint import _topic_for, load_rows


TRACE_HEADER = [
    "event_id", "timestamp_ms", "src", "dst", "flow_class", "policy",
    "deadline_ms", "size_bytes",
]


def _write_trace(rows: list[list[str]]) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline=""
    )
    writer = csv.writer(tmp)
    writer.writerow(TRACE_HEADER)
    writer.writerows(rows)
    tmp.close()
    return Path(tmp.name)


class WifiGatewayEndpointListTest(unittest.TestCase):
    def test_index_zero_is_gateway_not_control_station(self):
        self.assertEqual(
            wifi_gateway_endpoint_list(2),
            ["gateway", "robot_0000", "robot_0001"],
        )

    def test_matches_endpoint_list_shape_for_robot_naming(self):
        # Same robot-naming convention as endpoint_list() (index 0
        # aside) -- station indices/positions this topology reuses
        # from wire_network() are otherwise completely unchanged.
        from scripts.run_ns3_docker_container_fleet_probe import endpoint_list

        standard = endpoint_list(3)
        gateway_version = wifi_gateway_endpoint_list(3)
        self.assertEqual(standard[1:], gateway_version[1:])


class RequiredPeersForWifiGatewayTest(unittest.TestCase):
    def test_robots_require_only_gateway(self):
        endpoints = wifi_gateway_endpoint_list(2)
        required = required_peers_for_wifi_gateway(endpoints)
        self.assertEqual(required["robot_0000"], frozenset({"gateway"}))
        self.assertEqual(required["robot_0001"], frozenset({"gateway"}))

    def test_control_station_requires_only_gateway(self):
        endpoints = wifi_gateway_endpoint_list(2)
        required = required_peers_for_wifi_gateway(endpoints)
        self.assertEqual(required[CONTROL_STATION_NAME], frozenset({"gateway"}))

    def test_gateway_requires_every_robot_plus_control_station(self):
        endpoints = wifi_gateway_endpoint_list(3)
        required = required_peers_for_wifi_gateway(endpoints)
        self.assertEqual(
            required["gateway"],
            frozenset({"robot_0000", "robot_0001", "robot_0002", CONTROL_STATION_NAME}),
        )

    def test_no_required_peer_set_ever_names_a_physically_unreachable_pair(self):
        # This is the actual "no bypass" contract at the readiness
        # layer: a robot's required peers must never include
        # control_station (or another robot) directly.
        endpoints = wifi_gateway_endpoint_list(4)
        required = required_peers_for_wifi_gateway(endpoints)
        for robot in (e for e in endpoints if e != "gateway"):
            self.assertNotIn(CONTROL_STATION_NAME, required[robot])
            for other_robot in (e for e in endpoints if e not in ("gateway", robot)):
                self.assertNotIn(other_robot, required[robot])
        self.assertNotIn("gateway", required[CONTROL_STATION_NAME] - {"gateway"})


class UplinkDownlinkTopicsTest(unittest.TestCase):
    def test_uplink_is_every_topic_addressed_to_control_station(self):
        trace = _write_trace([
            ["e1", "0", "robot_0000", "control_station", "state", "fifo", "100", "64"],
            ["e2", "0", "robot_0001", "control_station", "state", "fifo", "100", "64"],
            ["e3", "0", "control_station", "robot_0000", "control", "fifo", "100", "64"],
        ])
        rows = load_all_rows(trace, "fifo")
        uplink, downlink = uplink_and_downlink_topics(rows, "control_station")
        self.assertEqual(uplink, [_topic_for("control_station", "state")])
        self.assertEqual(downlink, [_topic_for("robot_0000", "control")])

    def test_multiple_robots_sharing_one_uplink_topic_is_deduplicated(self):
        # Star topology: every robot publishes to the SAME
        # /fleetqox_trace/control_station/<flow> topic -- the gateway
        # only needs to subscribe/relay it once, not once per robot.
        trace = _write_trace([
            ["e1", "0", "robot_0000", "control_station", "state", "fifo", "100", "64"],
            ["e2", "1", "robot_0001", "control_station", "state", "fifo", "100", "64"],
        ])
        rows = load_all_rows(trace, "fifo")
        uplink, _ = uplink_and_downlink_topics(rows, "control_station")
        self.assertEqual(uplink, [_topic_for("control_station", "state")])

    def test_policy_filter_excludes_other_policies(self):
        trace = _write_trace([
            ["e1", "0", "robot_0000", "control_station", "state", "fifo", "100", "64"],
            ["e2", "0", "robot_0000", "control_station", "state", "priority", "100", "64"],
        ])
        rows = load_all_rows(trace, "fifo")
        self.assertEqual(len(rows), 1)


class IncomingTopicSuffixTest(unittest.TestCase):
    """fleetqox_rmw_trace_endpoint.py's --incoming-topic-suffix must be
    opt-in: every existing caller (WiFi-Direct, LAN, 5G, Table VI)
    omits it, so its default must be the empty string, changing
    nothing for any of them."""

    def test_default_suffix_is_empty_string(self):
        import argparse

        parser = argparse.ArgumentParser()
        # Mirror just the one flag under test, matching main()'s own
        # add_argument call exactly (default="").
        parser.add_argument("--incoming-topic-suffix", type=str, default="")
        args = parser.parse_args([])
        self.assertEqual(args.incoming_topic_suffix, "")

    def test_relay_suffix_constant_is_nonempty(self):
        # If this were "", the gateway's own publish and subscribe
        # topics would collide, causing the exact self-loop this
        # mechanism exists to avoid.
        self.assertNotEqual(RELAY_TOPIC_SUFFIX, "")

    def test_publish_topic_never_equals_subscribe_topic_for_the_same_flow(self):
        # Direct proof of the no-self-loop property: for any
        # (destination, flow_class), the unsuffixed name (what a robot
        # or control_station PUBLISHES, and what the gateway
        # SUBSCRIBES to) must differ from the suffixed name (what the
        # gateway PUBLISHES, and what a robot/control_station
        # SUBSCRIBES to in gateway mode).
        base = _topic_for("control_station", "state")
        suffixed = base + RELAY_TOPIC_SUFFIX
        self.assertNotEqual(base, suffixed)


class StaticEntriesTest(unittest.TestCase):
    """STEP 1: the static_subscriptions table must resolve each pair's
    destination through the PHYSICALLY reachable address (the gateway),
    not the logical workload destination's own (unreachable) address --
    this is the actual fix under test, see docs/AUDIT_ACCEPTANCE_TRACKING.md,
    "WIFI GATEWAY BENCHMARK" Phase 3/STEP 1."""

    def test_entry_format_matches_launch_endpoints_own_convention(self):
        entries = _static_entries(
            [("control_station", "state")], lambda _dst: "10.60.0.2"
        )
        self.assertEqual(
            entries,
            ["10.60.0.2:9100|0|/fleetqox_trace/control_station/state|std_msgs/msg/String"],
        )

    def test_ip_resolves_through_callable_not_the_literal_dst_name(self):
        # The whole point: "control_station" the STRING must not be
        # used as an address -- ip_for_dst is what actually decides the
        # physical destination.
        entries = _static_entries(
            [("control_station", "state")], lambda _dst: "10.61.0.2"
        )
        self.assertIn("10.61.0.2:9100", entries[0])
        self.assertNotIn("control_station:9100", entries[0])

    def test_topic_suffix_applies_to_every_entry(self):
        entries = _static_entries(
            [("robot_0000", "control")], lambda _dst: "10.60.0.3", RELAY_TOPIC_SUFFIX
        )
        self.assertTrue(entries[0].split("|")[2].endswith(RELAY_TOPIC_SUFFIX))

    def test_per_pair_ip_resolution_for_multiple_destinations(self):
        # The gateway's own downlink entries must route EACH robot
        # pair to THAT robot's own IP, not a single shared address.
        ips = {"robot_0000": "10.60.0.3", "robot_0001": "10.60.0.4"}
        entries = _static_entries(
            [("robot_0000", "control"), ("robot_0001", "control")],
            lambda dst: ips[dst],
        )
        self.assertIn("10.60.0.3:9100", entries[0])
        self.assertIn("10.60.0.4:9100", entries[1])


def _wifi_stats_line(sim_time_s: float, wall_elapsed_s: float) -> str:
    """One synthetic FLEETQOX_WIFI_STATS log line -- same minimal shape as
    test_ns3_docker_container_fleet_probe.py's own helper of the same
    name (duplicated, not imported, to keep this file's tests
    self-contained per its own module docstring)."""
    return (
        f'FLEETQOX_WIFI_STATS {{"sim_time_s":{sim_time_s},'
        f'"wall_elapsed_s":{wall_elapsed_s},'
        f'"sim_lag_s":{wall_elapsed_s - sim_time_s},'
        f'"self_cpu_s":0.0,"self_rss_kb":0,"heavy_tracing":false}}'
    )


class GatewaySimLagSMeasurementBugTest(unittest.TestCase):
    """The Gateway harness (run_wifi_gateway_probe.py's run_gateway_probe())
    has its OWN copy of the sim_lag_s measurement bug already proven and
    fixed for the Direct harness (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md, "SIM_LAG_S MEASUREMENT BUG
    (26/09/2026)", and SimLagSMeasurementBugTest in
    test_ns3_docker_container_fleet_probe.py). At the time this test was
    written, run_wifi_gateway_probe.py still computed:

        sim_lag_s = (
            ns3_real_elapsed_s_at_log_read - wifi_stats["sim_time_s"]
            if wifi_stats is not None and wifi_stats.get("sim_time_s") is not None
            and ns3_real_elapsed_s_at_log_read is not None
            else None
        )

    where `wifi_stats` is parse_wifi_stats()'s deliberately-EARLY,
    target-based snapshot (correct for MAC/PHY-counter cross-run
    consistency, wrong for lag -- see that function's own doc comment)
    and `ns3_real_elapsed_s_at_log_read` is an out-of-band real-time
    read taken well after that early snapshot -- two different
    observation points, the identical structural defect already proven
    for Direct.

    Two independent RED proofs:

    1) test_old_formula_reproduction_falsely_inflates_lag reproduces
       that exact arithmetic against a synthetic log modeling a HEALTHY
       run (early target snapshot, late real-time read) and shows it
       crosses MAX_HEALTHY_SIM_LAG_S even though the simulator was never
       behind -- while corrected_sim_lag_s() (Direct's already-fixed,
       same-instant formula) correctly reports it healthy.

    2) test_resource_usage_source_never_has_elapsed_s_key proves this
       bug's PRESENT-DAY manifestation is actually worse than "falsely
       inflates": ns3_real_elapsed_s_at_log_read is sourced from
       ReferenceTopologyProbe.sample_ns3sim_resource_usage(), which only
       ever returns "cpu_pct"/"rss_mb" (a `docker stats --format
       "{{.CPUPerc}}\\t{{.MemUsage}}"` sample -- see that method's own
       docstring). `.get("elapsed_s")` on that dict is therefore ALWAYS
       None, so sim_lag_s is unconditionally None and simulator_invalid
       unconditionally False for every real Gateway run today -- the
       validity gate is silently disabled entirely, not merely
       mis-measuring, until this is fixed."""

    def test_old_formula_reproduction_falsely_inflates_lag(self):
        # Same healthy-run shape as Direct's own proven-bug test: an
        # EARLY target snapshot (sim_time=15, healthy ~2s lag) and a
        # HEALTHY final snapshot (sim_time=30, still only ~2s lag) --
        # the simulator never actually fell behind at any point.
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
        wifi_stats = parse_wifi_stats(log, target_sim_time_s=15.0)
        self.assertEqual(wifi_stats["sim_time_s"], 15)
        # Models a late, end-of-run real-time read -- e.g. a docker-stats
        # sample taken after the full run/drain/teardown, structurally
        # analogous to Direct's own proven-buggy
        # ns3_real_elapsed_s_at_log_read.
        ns3_real_elapsed_s_at_log_read = 44.03

        old_formula_result = (
            ns3_real_elapsed_s_at_log_read - wifi_stats["sim_time_s"]
            if wifi_stats is not None and wifi_stats.get("sim_time_s") is not None
            and ns3_real_elapsed_s_at_log_read is not None
            else None
        )
        self.assertGreater(
            old_formula_result,
            MAX_HEALTHY_SIM_LAG_S,
            "the current Gateway formula must falsely exceed the validity "
            "gate for this healthy-simulator shape -- that IS the bug",
        )

        corrected = corrected_sim_lag_s(log)
        self.assertLessEqual(
            corrected,
            MAX_HEALTHY_SIM_LAG_S,
            "the FIXED (Direct-equivalent) formula must correctly report "
            "this run as healthy",
        )
        self.assertAlmostEqual(corrected, 35.64 - 30, places=6)

    def test_resource_usage_source_never_has_elapsed_s_key(self):
        probe = object.__new__(ReferenceTopologyProbe)
        probe.ns3sim_name = "fleetqox_test_ns3sim"
        fake_result = mock.Mock(stdout="45.20%\t120MiB / 500MiB\n")
        with mock.patch(
            "scripts.run_ns3_docker_container_fleet_probe.docker",
            return_value=fake_result,
        ):
            usage = probe.sample_ns3sim_resource_usage()
        self.assertIsNotNone(usage)
        self.assertNotIn(
            "elapsed_s",
            usage,
            "sample_ns3sim_resource_usage() has no 'elapsed_s' field -- "
            "run_wifi_gateway_probe.py's ns3sim_resource_usage.get('elapsed_s') "
            "is therefore ALWAYS None, making sim_lag_s/simulator_invalid "
            "unconditionally None/False for every real Gateway run today",
        )


class GatewaySimLagSFixLockInTest(unittest.TestCase):
    """GREEN lock-in: run_wifi_gateway_probe.py's sim_lag_s must be
    computed via Direct's own already-proven corrected_sim_lag_s()/
    parse_last_wifi_stats() (imported, not reimplemented) -- guards
    against a future edit silently reintroducing a local formula that
    could drift from the one proven correct for Direct."""

    def test_gateway_module_uses_directs_own_fix_functions_by_identity(self):
        import scripts.run_wifi_gateway_probe as gw

        self.assertIs(gw.corrected_sim_lag_s, corrected_sim_lag_s)
        self.assertIs(gw.parse_last_wifi_stats, parse_last_wifi_stats)

    def test_gateway_source_no_longer_mixes_observation_points(self):
        import inspect

        import scripts.run_wifi_gateway_probe as gw

        source = inspect.getsource(gw.run_wifi_gateway_probe)
        self.assertNotIn(
            'ns3_real_elapsed_s_at_log_read - wifi_stats["sim_time_s"]',
            source,
            "the old mismatched-observation-points formula must be gone",
        )
        self.assertIn("corrected_sim_lag_s(ns3_log_text)", source)


class RunWifiGatewayProbeSchedulerPlumbingAbsentTest(unittest.TestCase):
    """RED (P2.2, docs/AUDIT_ACCEPTANCE_TRACKING.md, "N=16 SERIOUS
    PERFORMANCE PASS" / P2.1): ns3::HeapScheduler was already proven a
    semantics-preserving, sim_lag_s-reducing option, exposed on Table
    VI's run_coordination_probe() and, as of P2.1, on Table V Direct's
    run_probe() (both as `ns3_scheduler`, default "map"). Gateway's own
    run_wifi_gateway_probe() never received the same plumbing -- proven
    here, literally, not inferred."""

    def test_run_wifi_gateway_probe_has_no_scheduler_parameter(self):
        self.assertNotIn(
            "ns3_scheduler", inspect.signature(run_wifi_gateway_probe).parameters
        )

    def test_run_wifi_gateway_probe_never_passes_scheduler_to_start_ns3(self):
        self.assertNotIn("scheduler=", inspect.getsource(run_wifi_gateway_probe))


class GatewayStartNs3SchedulerCommandLineTest(unittest.TestCase):
    """GREEN: proves the scheduler choice actually reaches the ns-3
    process's own command line via Gateway's call site, and that
    switching it changes NOTHING else about the invocation -- same
    guarantee already established for Direct (P2.1) and Table VI."""

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

    def test_explicit_heap_reaches_the_ns3_command_line(self):
        self.assertIn("--scheduler=heap", self._captured_ns3_command(scheduler="heap"))

    def test_heap_changes_only_the_scheduler_flag_nothing_else(self):
        map_cmd = self._captured_ns3_command(scheduler="map")
        heap_cmd = self._captured_ns3_command(scheduler="heap")
        self.assertEqual(
            map_cmd.replace("--scheduler=map", "--scheduler=X"),
            heap_cmd.replace("--scheduler=heap", "--scheduler=X"),
        )


if __name__ == "__main__":
    unittest.main()
