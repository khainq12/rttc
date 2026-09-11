import json
import unittest

from scripts.run_ns3_docker_wifi_tap_rmw_probe import (
    build_shell_script,
    endpoint_list,
    parse_endpoint_results,
)


class EndpointListTest(unittest.TestCase):
    def test_fixed_three_plus_robots_in_order(self):
        self.assertEqual(
            endpoint_list(1),
            ["fleet_controller", "fleet_router", "operator_ui", "robot_0000"],
        )

    def test_robot_count_scales(self):
        endpoints = endpoint_list(3)
        self.assertEqual(len(endpoints), 6)
        self.assertEqual(endpoints[3:], ["robot_0000", "robot_0001", "robot_0002"])


class BuildShellScriptTest(unittest.TestCase):
    def setUp(self):
        self.endpoints = endpoint_list(1)
        self.script = build_shell_script(
            trace_container_path="/work/results/trace.csv",
            endpoints=self.endpoints,
            policy="fifo",
            num_robots=1,
            sim_duration_s=30.0,
            start_offset_ms=2000.0,
            drain_s=10.0,
            results_dir_container="/tmp/fleetqox_tap_results",
        )

    def test_starts_with_set_dash_e(self):
        self.assertTrue(self.script.startswith("set -e"))

    def test_one_network_namespace_per_endpoint(self):
        for i in range(len(self.endpoints)):
            self.assertIn(f"ip netns add ns{i}", self.script)
            self.assertIn(f"ip tuntap add dev ftap{i} mode tap", self.script)

    def test_every_endpoint_gets_its_own_ip_and_peer_list_excludes_itself(self):
        # fleet_controller is index 0 -> 10.50.0.2; its peer list should
        # contain the other 3 endpoints' IPs but not its own.
        self.assertIn("FLEETQOX_RMW_BIND=0.0.0.0:9100", self.script)
        self.assertIn("10.50.0.3:9100", self.script)  # fleet_router
        self.assertIn("10.50.0.4:9100", self.script)  # operator_ui
        self.assertIn("10.50.0.5:9100", self.script)  # robot_0000

    def test_waits_on_endpoint_pids_not_bare_wait(self):
        self.assertIn('wait "${ENDPOINT_PIDS[@]}"', self.script)
        self.assertNotIn("\nwait\n", self.script)

    def test_every_endpoint_gets_ready_and_start_file_flags(self):
        for i in range(len(self.endpoints)):
            self.assertIn(f"--ready-file=/tmp/fleetqox_tap_results/ready_{i}", self.script)
        self.assertIn("--start-file=/tmp/fleetqox_tap_results/start", self.script)

    def test_start_file_touched_only_after_waiting_for_every_ready_file(self):
        ready_wait_index = self.script.index("while true;")
        touch_index = self.script.index("touch /tmp/fleetqox_tap_results/start")
        wait_pids_index = self.script.index('wait "${ENDPOINT_PIDS[@]}"')
        self.assertLess(ready_wait_index, touch_index)
        self.assertLess(touch_index, wait_pids_index)

    def test_result_markers_present_per_endpoint(self):
        for endpoint in self.endpoints:
            self.assertIn(f"FLEETQOX_TAP_RESULT_BEGIN:{endpoint}", self.script)
            self.assertIn(f"FLEETQOX_TAP_RESULT_END:{endpoint}", self.script)


class ParseEndpointResultsTest(unittest.TestCase):
    def test_extracts_json_between_markers(self):
        payload = {"endpoint": "robot_0000", "tx": 5, "rx": 5}
        stdout = (
            "some noise\n"
            "FLEETQOX_TAP_RESULT_BEGIN:robot_0000\n"
            f"{json.dumps(payload)}\n"
            "FLEETQOX_TAP_RESULT_END:robot_0000\n"
            "more noise\n"
        )
        results = parse_endpoint_results(stdout, ["robot_0000"])
        self.assertEqual(results["robot_0000"], payload)

    def test_missing_marker_yields_none_not_a_crash(self):
        results = parse_endpoint_results("nothing relevant here", ["robot_0000"])
        self.assertIsNone(results["robot_0000"])

    def test_empty_body_between_markers_yields_none(self):
        stdout = (
            "FLEETQOX_TAP_RESULT_BEGIN:robot_0000\n"
            "FLEETQOX_TAP_RESULT_END:robot_0000\n"
        )
        results = parse_endpoint_results(stdout, ["robot_0000"])
        self.assertIsNone(results["robot_0000"])


if __name__ == "__main__":
    unittest.main()
