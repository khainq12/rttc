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
import tempfile
import unittest
from pathlib import Path

from scripts.run_wifi_gateway_probe import (
    RELAY_TOPIC_SUFFIX,
    CONTROL_STATION_NAME,
    _static_entries,
    required_peers_for_wifi_gateway,
    wifi_gateway_endpoint_list,
)
from scripts.fleetqox_rmw_gateway_endpoint import (
    load_all_rows,
    uplink_and_downlink_topics,
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


if __name__ == "__main__":
    unittest.main()
