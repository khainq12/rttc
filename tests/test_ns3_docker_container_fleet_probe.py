import csv
import tempfile
import unittest
from pathlib import Path

from scripts.run_ns3_docker_container_fleet_probe import (
    BASE_IP_PREFIX,
    RMW_PORT,
    STATIC_SUBSCRIPTION_TYPE_NAME,
    build_static_subscriptions,
    endpoint_list,
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


class ConstantsTest(unittest.TestCase):
    def test_distinct_ip_prefix_from_single_container_harness(self):
        from scripts.run_ns3_docker_wifi_tap_rmw_probe import BASE_IP_PREFIX as OLD_PREFIX

        self.assertNotEqual(BASE_IP_PREFIX, OLD_PREFIX)

    def test_rmw_port_and_type_name_are_sane(self):
        self.assertEqual(RMW_PORT, 9100)
        self.assertEqual(STATIC_SUBSCRIPTION_TYPE_NAME, "std_msgs/msg/String")


if __name__ == "__main__":
    unittest.main()
