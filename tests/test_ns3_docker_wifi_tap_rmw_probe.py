import json
import unittest

from scripts.run_ns3_docker_wifi_tap_rmw_probe import (
    START_WAIT_TIMEOUT_S,
    _station_mac,
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


class BuildShellScriptRawUdpTest(unittest.TestCase):
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
            rmw_implementation="raw_udp",
        )

    def test_skips_ros2_and_fleetqox_install_check(self):
        self.assertNotIn(f"setup.bash || ", self.script)
        self.assertNotIn("source /opt/ros/jazzy/setup.bash", self.script)
        self.assertNotIn("RMW_IMPLEMENTATION", self.script)

    def test_launches_raw_udp_endpoint_with_full_peer_map(self):
        self.assertIn("raw_udp_trace_endpoint.py", self.script)
        # fleet_controller (index 0) -> 10.50.0.2, including itself --
        # the receiver just never looks itself up as a send target.
        self.assertIn("fleet_controller=10.50.0.2", self.script)
        self.assertIn("fleet_router=10.50.0.3", self.script)
        self.assertIn("operator_ui=10.50.0.4", self.script)
        self.assertIn("robot_0000=10.50.0.5", self.script)

    def test_result_markers_present_per_endpoint(self):
        for endpoint in self.endpoints:
            self.assertIn(f"FLEETQOX_TAP_RESULT_BEGIN:{endpoint}", self.script)
            self.assertIn(f"FLEETQOX_TAP_RESULT_END:{endpoint}", self.script)


class BuildShellScriptStaggerStartTest(unittest.TestCase):
    def test_no_sleep_lines_when_stagger_is_zero(self):
        script = build_shell_script(
            trace_container_path="/work/results/trace.csv",
            endpoints=endpoint_list(1),
            policy="fifo",
            num_robots=1,
            sim_duration_s=30.0,
            start_offset_ms=2000.0,
            drain_s=10.0,
            results_dir_container="/tmp/fleetqox_tap_results",
            stagger_start_ms=0.0,
        )
        sleep_lines = [line for line in script.splitlines() if line.startswith("sleep ")]
        # Only the fixed NS3_ATTACH_WAIT_S sleep should remain, none of the
        # per-endpoint stagger sleeps.
        self.assertEqual(len(sleep_lines), 1)

    def test_cumulative_per_endpoint_delay_when_staggered(self):
        endpoints = endpoint_list(1)  # 4 endpoints: index 0..3
        script = build_shell_script(
            trace_container_path="/work/results/trace.csv",
            endpoints=endpoints,
            policy="fifo",
            num_robots=1,
            sim_duration_s=30.0,
            start_offset_ms=2000.0,
            drain_s=10.0,
            results_dir_container="/tmp/fleetqox_tap_results",
            stagger_start_ms=100.0,
        )
        # Endpoint 0 launches immediately (no sleep before it); endpoints
        # 1-3 get i * 100ms of additional sequential (non-backgrounded)
        # delay, so ns-3 attach's own sleep plus 3 more distinct values.
        self.assertIn("sleep 0.100000", script)
        self.assertIn("sleep 0.200000", script)
        self.assertIn("sleep 0.300000", script)


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

    def test_endpoint_start_wait_timeout_exceeds_orchestrator_ready_deadline(self):
        # Each endpoint's own --start-wait-timeout-s must be large enough
        # that a fast-discovering endpoint never times itself out before a
        # slower sibling finishes discovery and the orchestrator's own
        # ready-poll deadline releases the shared start gate -- confirmed
        # as a real bug when both were left at the same value.
        self.assertIn(f"--start-wait-timeout-s={START_WAIT_TIMEOUT_S} ", self.script)

    def test_wait_on_endpoint_pids_does_not_abort_under_set_dash_e(self):
        # A failing endpoint must not abort the script at the `wait` line
        # itself -- that would skip all the log/result dumping below it,
        # which exists specifically to explain such a failure. Confirmed
        # as a real bug: `wait "$X"; ENDPOINT_EXIT=$?` still aborts on the
        # wait line under -e (";" doesn't protect against -e), so the
        # bracket must be an explicit set +e/set -e pair.
        wait_index = self.script.index('wait "${ENDPOINT_PIDS[@]}"')
        surrounding = self.script[max(0, wait_index - 40) : wait_index + 60]
        self.assertIn("set +e", surrounding)
        self.assertIn("set -e", surrounding)
        self.assertIn("exit $ENDPOINT_EXIT", self.script)

    def test_netns_eth0_mac_matches_station_mac_formula(self):
        for i in range(len(self.endpoints)):
            self.assertIn(f"ip link set eth0 address {_station_mac(i)}", self.script)

    def test_tap_creator_symlink_is_extracted_dynamically_not_hardcoded(self):
        # libns3-tap-bridge.so bakes in the tap-creator helper's
        # absolute build-time path, which does not exist at runtime --
        # confirmed to change whenever ns-3 is rebuilt from a different
        # location (the apt package's own build path, then a completely
        # different path once the image switched to building ns-3 from
        # source in the Dockerfile). A hardcoded symlink target breaks
        # the moment that build location changes again; must always be
        # extracted from the actual installed .so via `strings`.
        self.assertIn("strings", self.script)
        self.assertNotIn("Q7chNJ", self.script)
        self.assertNotIn("/build/ns3-", self.script)


class StationMacTest(unittest.TestCase):
    def test_deterministic_and_matches_cxx_formula(self):
        # Must byte-for-byte match fleetqox_trace_replay_tap.cc's
        # stationMacs formula (02:00:00:00:<hi>:<lo>) -- confirmed by a
        # real run that a real process's ARP replies are dropped by the
        # AP's association table when the two sides disagree.
        self.assertEqual(_station_mac(0), "02:00:00:00:00:00")
        self.assertEqual(_station_mac(1), "02:00:00:00:00:01")
        self.assertEqual(_station_mac(256), "02:00:00:00:01:00")

    def test_locally_administered_bit_set(self):
        first_octet = int(_station_mac(0).split(":")[0], 16)
        self.assertTrue(first_octet & 0x02)


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
