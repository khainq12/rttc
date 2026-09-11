import csv
from pathlib import Path
import tempfile
import unittest

from scripts.fleetqox_rmw_trace_endpoint import _topic_for, build_payload, load_rows


class TopicForTest(unittest.TestCase):
    def test_point_to_point_topic_isolates_destination_and_flow_class(self):
        self.assertEqual(
            _topic_for("robot_0003", "control"), "/fleetqox_trace/robot_0003/control"
        )
        self.assertNotEqual(
            _topic_for("robot_0003", "control"), _topic_for("robot_0004", "control")
        )
        self.assertNotEqual(
            _topic_for("robot_0003", "control"), _topic_for("robot_0003", "state")
        )

    def test_sanitizes_non_ros2_topic_characters(self):
        topic = _topic_for("fleet-router!", "human/qoe")
        self.assertTrue(all(ch.isalnum() or ch in "/_" for ch in topic))


class LoadRowsTest(unittest.TestCase):
    def test_filters_by_policy_and_endpoint(self):
        rows = [
            {
                "event_id": "1",
                "policy": "fifo",
                "flow_class": "control",
                "src": "fleet_controller",
                "dst": "robot_0000",
                "bytes": "96",
                "deadline_ms": "45.0",
                "timestamp_ms": "0.0",
            },
            {
                "event_id": "2",
                "policy": "static_priority",
                "flow_class": "control",
                "src": "fleet_controller",
                "dst": "robot_0000",
                "bytes": "96",
                "deadline_ms": "45.0",
                "timestamp_ms": "20.0",
            },
            {
                "event_id": "3",
                "policy": "fifo",
                "flow_class": "state",
                "src": "robot_0001",
                "dst": "fleet_router",
                "bytes": "320",
                "deadline_ms": "120.0",
                "timestamp_ms": "0.0",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.csv"
            with trace_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            robot0_rows = load_rows(trace_path, "fifo", "robot_0000")
            self.assertEqual([r["event_id"] for r in robot0_rows], ["1"])

            router_rows = load_rows(trace_path, "fifo", "fleet_router")
            self.assertEqual([r["event_id"] for r in router_rows], ["3"])

            unrelated_rows = load_rows(trace_path, "fifo", "operator_ui")
            self.assertEqual(unrelated_rows, [])


class BuildPayloadTest(unittest.TestCase):
    ROW = {
        "event_id": "42",
        "policy": "fifo",
        "flow_class": "control",
        "src": "fleet_controller",
        "dst": "robot_0000",
        "deadline_ms": "45.0",
    }

    def test_payload_matches_target_byte_size_exactly(self):
        for target_bytes in (96, 320, 2200, 9000):
            payload = build_payload(self.ROW, target_bytes)
            self.assertEqual(len(payload.encode("utf-8")), target_bytes)

    def test_payload_roundtrips_the_row_fields(self):
        import json

        payload = json.loads(build_payload(self.ROW, 200))
        self.assertEqual(payload["e"], "42")
        self.assertEqual(payload["d"], 45.0)
        self.assertIn("s", payload)

    def test_target_smaller_than_metadata_floor_raises(self):
        with self.assertRaises(ValueError):
            build_payload(self.ROW, 5)


if __name__ == "__main__":
    unittest.main()
