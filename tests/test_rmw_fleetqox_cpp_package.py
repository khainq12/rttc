import ctypes
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from fleetqox.rmw_boundary import FleetRmwBoundary
from fleetqox.ros2_shim import Ros2Sample


ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "ros2_ws" / "src" / "rmw_fleetqox_cpp"
IFACE_PKG = ROOT / "ros2_ws" / "src" / "fleetrmw_interfaces"


class RmwFleetQoxCppPackageTest(unittest.TestCase):
    def test_package_manifest_and_targets_exist(self) -> None:
        self.assertTrue((PKG / "package.xml").exists())
        cmake = (PKG / "CMakeLists.txt").read_text()
        self.assertIn("${PROJECT_NAME}_transport", cmake)
        self.assertIn("POSITION_INDEPENDENT_CODE ON", cmake)
        self.assertIn("src/rmw_identifier.cpp", cmake)
        self.assertIn("src/rmw_graph.cpp", cmake)
        self.assertIn("src/rmw_lifecycle.cpp", cmake)
        self.assertIn("src/rmw_pubsub.cpp", cmake)
        self.assertIn("src/rmw_stubs.cpp", cmake)
        self.assertIn("src/rmw_wait.cpp", cmake)
        self.assertIn("fleetrmw_transport_loop_smoke", cmake)
        self.assertIn("fleetrmw_frame_probe", cmake)
        self.assertIn("fleetrmw_action_frame_probe", cmake)
        self.assertIn("fleetrmw_lifecycle_probe", cmake)
        self.assertIn("fleetrmw_serialized_pubsub_probe", cmake)
        self.assertIn("fleetrmw_qos_probe", cmake)
        self.assertIn("fleetrmw_qos_best_available_probe", cmake)
        self.assertIn("fleetrmw_take_sequence_probe", cmake)
        self.assertIn("fleetrmw_take_sequence_ordering_smoke", cmake)
        self.assertIn("fleetrmw_qos_event_probe", cmake)
        self.assertIn("fleetrmw_matched_event_probe", cmake)
        self.assertIn("fleetrmw_callback_teardown_probe", cmake)
        self.assertIn("fleetrmw_callback_teardown_quiescence_smoke", cmake)
        self.assertIn("fleetrmw_remote_event_probe", cmake)
        self.assertIn("fleetrmw_remote_deadline_event_probe", cmake)
        self.assertIn("fleetrmw_qos_incompatible_event_probe", cmake)
        self.assertIn("fleetrmw_qos_deadline_incompatible_event_probe", cmake)
        self.assertIn("fleetrmw_qos_liveliness_incompatible_event_probe", cmake)
        self.assertIn("fleetrmw_type_incompatible_event_probe", cmake)
        self.assertIn("fleetrmw_message_lost_event_probe", cmake)
        self.assertIn("fleetrmw_message_lost_interprocess_probe", cmake)
        self.assertIn("fleetrmw_liveliness_event_probe", cmake)
        self.assertIn("fleetrmw_automatic_liveliness_probe", cmake)
        self.assertIn("fleetrmw_automatic_liveliness_idle_renewal_smoke", cmake)
        self.assertIn("fleetrmw_remote_manual_liveliness_probe", cmake)
        self.assertIn("fleetrmw_remote_liveliness_multi_endpoint_probe", cmake)
        self.assertIn("fleetrmw_liveliness_scale_probe", cmake)
        self.assertIn("fleetrmw_remote_liveliness_scale_probe", cmake)
        self.assertIn("fleetrmw_liveliness_default_lease_probe", cmake)
        self.assertIn("fleetrmw_content_filter_probe", cmake)
        self.assertIn("fleetrmw_content_filter_sql_probe", cmake)
        self.assertIn("fleetrmw_content_filter_typed_probe", cmake)
        self.assertIn("fleetrmw_wstring_content_filter_probe", cmake)
        self.assertIn("fleetrmw_keyed_instance_retransmit_probe", cmake)
        self.assertIn("fleetrmw_service_qos_probe", cmake)
        self.assertIn("fleetrmw_domain_isolation_probe", cmake)
        self.assertIn("fleetrmw_domain_isolation_smoke", cmake)
        self.assertIn("fleetrmw_graph_guard_wait_smoke", cmake)
        self.assertIn("fleetrmw_service_error_probe", cmake)
        self.assertIn("fleetrmw_reliability_probe", cmake)
        self.assertIn("fleetrmw_reliable_interprocess_probe", cmake)
        self.assertIn("fleetrmw_typed_pubsub_probe", cmake)
        self.assertIn("fleetrmw_std_msgs_string_probe", cmake)
        self.assertIn("fleetrmw_geometry_twist_probe", cmake)
        self.assertIn("fleetrmw_cpp_typesupport_probe", cmake)
        self.assertIn("src/quic_gateway_transport.cpp", cmake)
        self.assertIn("fleetrmw_quic_gateway_publish_probe", cmake)
        self.assertIn("fleetrmw_quic_gateway_burst_publish_probe", cmake)
        self.assertIn("fleetrmw_quic_gateway_take_probe", cmake)
        self.assertIn("fleetrmw_quic_gateway_rmw_take_probe", cmake)
        self.assertIn("fleetrmw_quic_stateful_gateway_probe", cmake)
        self.assertIn("fleetrmw_quic_stateful_rmw_probe", cmake)
        self.assertIn("fleetrmw_quic_task_outcome_submit_probe", cmake)
        self.assertIn("fleetrmw_allocation_probe", cmake)
        self.assertIn("fleetrmw_security_options_probe", cmake)
        self.assertIn("fleetrmw_security_policy_probe", cmake)
        self.assertIn("fleetrmw_sros2_permissions_probe", cmake)
        self.assertIn("fleetrmw_sros2_service_permissions_probe", cmake)
        self.assertIn("fleetrmw_sros2_governance_probe", cmake)
        self.assertIn("fleetrmw_sros2_identity_probe", cmake)
        self.assertIn("fleetrmw_udp_aead_probe", cmake)
        self.assertIn("fleetrmw_dynamic_message_probe", cmake)
        self.assertIn("rosidl_dynamic_typesupport", cmake)
        self.assertIn("OpenSSL::Crypto", cmake)
        self.assertIn("tinyxml2::tinyxml2", cmake)
        self.assertIn("fleetrmw_quic_dependency_probe", cmake)
        self.assertIn("PkgConfig::NGTCP2_GNUTLS", cmake)
        self.assertIn("PkgConfig::GNUTLS", cmake)
        self.assertIn("fleetrmw_rclcpp_interprocess_probe", cmake)
        self.assertIn("fleetrmw_rcl_string_probe", cmake)
        self.assertIn("fleetrmw_rcl_graph_talker", cmake)
        self.assertIn("fleetrmw_rcl_service_node", cmake)
        self.assertIn('"c:rosidl_typesupport_introspection_c"', cmake)
        self.assertIn('"cpp:rosidl_typesupport_introspection_cpp"', cmake)
        self.assertIn("fleetrmw_wait_probe", cmake)
        docker_wait_probe = ROOT / "scripts" / "run_rmw_docker_wait_probe.py"
        self.assertTrue(docker_wait_probe.exists())
        docker_wait_source = docker_wait_probe.read_text()
        self.assertIn("fleetrmw.rmw_docker_wait_probe.v1", docker_wait_source)
        self.assertIn("graph_guard_automatic", docker_wait_source)
        self.assertIn("max_conditions_enforced", docker_wait_source)
        self.assertIn("guard_conditions_external_to_capacity", docker_wait_source)
        self.assertIn("cross_context_rejected", docker_wait_source)
        self.assertIn("publisher_owner_node_enforced", docker_wait_source)
        self.assertIn("fleetrmw_graph_probe", cmake)
        self.assertIn("fleetrmw_interprocess_pubsub_probe", cmake)
        self.assertIn("fleetrmw_udp_router_probe", cmake)
        self.assertIn("fleetrmw_remote_graph_probe", cmake)
        self.assertIn("fleetrmw_remote_graph_lease_probe", cmake)
        self.assertIn("fleetrmw_remote_graph_lease_identity_smoke", cmake)
        manifest = (PKG / "package.xml").read_text()
        self.assertIn("<depend>rmw</depend>", manifest)
        self.assertIn("<depend>rcutils</depend>", manifest)
        self.assertIn("<depend>rcl</depend>", manifest)
        self.assertIn("<depend>rclcpp</depend>", manifest)
        self.assertIn("<depend>openssl</depend>", manifest)
        self.assertIn("<depend>tinyxml2_vendor</depend>", manifest)
        self.assertIn("<depend>rosidl_typesupport_c</depend>", manifest)
        self.assertIn("<depend>rosidl_typesupport_cpp</depend>", manifest)
        self.assertIn("<depend>rosidl_typesupport_introspection_cpp</depend>", manifest)
        wait_source = (PKG / "src" / "rmw_wait.cpp").read_text()
        self.assertIn("guard_data_from_waitable", wait_source)
        self.assertIn("std::atomic<bool> triggered", wait_source)
        self.assertIn("rmw_fleetqox_cpp_waitable_event_has_status", wait_source)
        self.assertIn("ready_events", wait_source)
        self.assertIn("validate_wait_inputs", wait_source)
        self.assertIn("wait input exceeds wait set max_conditions", wait_source)
        self.assertIn("capacity_condition_count", wait_source)
        self.assertIn("belongs to another context", wait_source)
        service_source = (PKG / "src" / "rmw_stubs.cpp").read_text()
        self.assertIn("service_cpp_introspection_members", service_source)
        self.assertIn("const rmw_node_t * owner_node", service_source)
        self.assertIn("client was not created by the supplied node", service_source)
        self.assertIn(
            "rosidl_typesupport_cpp::get_service_typesupport_handle_function",
            service_source,
        )
        self.assertIn("<depend>std_srvs</depend>", manifest)
        self.assertIn("<depend>nav_msgs</depend>", manifest)
        cpp_probe = (PKG / "src" / "cpp_typesupport_probe.cpp").read_text()
        self.assertIn("fleetrmw.cpp_typesupport_probe.v1", cpp_probe)
        self.assertIn("rosidl_typesupport_cpp", cpp_probe)
        self.assertIn("geometry_msgs::msg::PoseStamped", cpp_probe)
        self.assertIn("bounded_pose_size_ok", cpp_probe)
        self.assertIn("bounded_c_pose_size_ok", cpp_probe)
        self.assertIn("rmw_get_serialized_message_size", cpp_probe)
        cpp_runner = ROOT / "scripts" / "run_rmw_docker_cpp_typesupport_probe.py"
        self.assertTrue(cpp_runner.exists())
        self.assertIn(
            "fleetrmw.docker_cpp_typesupport_probe.v1",
            cpp_runner.read_text(),
        )
        rclcpp_probe = (PKG / "src" / "rclcpp_interprocess_probe.cpp").read_text()
        self.assertIn("fleetrmw.rclcpp_interprocess_client.v1", rclcpp_probe)
        self.assertIn("geometry_msgs::msg::PoseStamped", rclcpp_probe)
        self.assertIn("nav_msgs::msg::Path", rclcpp_probe)
        self.assertIn("kPathPoseCount = 64", rclcpp_probe)
        self.assertIn("nav_msgs::srv::GetPlan", rclcpp_probe)
        self.assertIn("kPlanPoseCount = 512", rclcpp_probe)
        self.assertIn("valid_plan_request", rclcpp_probe)
        self.assertIn("valid_plan_response", rclcpp_probe)
        self.assertIn("path_roundtrip", rclcpp_probe)
        self.assertIn("publisher_network_flow", rclcpp_probe)
        self.assertIn("subscription_network_flow", rclcpp_probe)
        self.assertIn("request_callback_observed", rclcpp_probe)
        self.assertIn("response_callback_observed", rclcpp_probe)
        rclcpp_runner = ROOT / "scripts" / "run_rmw_docker_router_rclcpp_interprocess_probe.py"
        self.assertTrue(rclcpp_runner.exists())
        self.assertIn(
            "fleetrmw.docker_router_rclcpp_interprocess_probe.v2",
            rclcpp_runner.read_text(),
        )
        self.assertIn('client.get("path_roundtrip") is True', rclcpp_runner.read_text())
        cross_language_endpoint = (
            ROOT / "scripts" / "rclpy_cpp_interprocess_endpoint.py"
        )
        self.assertTrue(cross_language_endpoint.exists())
        cross_language_endpoint_source = cross_language_endpoint.read_text()
        self.assertIn(
            "fleetrmw.rclpy_cpp_interprocess_endpoint.v1",
            cross_language_endpoint_source,
        )
        self.assertIn("PATH_POSE_COUNT = 64", cross_language_endpoint_source)
        self.assertIn("PLAN_POSE_COUNT = 512", cross_language_endpoint_source)
        self.assertIn("valid_path_request", cross_language_endpoint_source)
        self.assertIn("valid_path_reply", cross_language_endpoint_source)
        self.assertIn("GetPlan", cross_language_endpoint_source)
        self.assertIn("valid_plan_request", cross_language_endpoint_source)
        self.assertIn("valid_plan_response", cross_language_endpoint_source)
        cross_language_runner = (
            ROOT / "scripts" / "run_rmw_docker_router_cpp_python_path_probe.py"
        )
        self.assertTrue(cross_language_runner.exists())
        cross_language_runner_source = cross_language_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_router_cpp_python_path_probe.v2",
            cross_language_runner_source,
        )
        self.assertIn("cpp_server_python_client", cross_language_runner_source)
        self.assertIn("cpp_client_python_server", cross_language_runner_source)
        self.assertIn("--iterations", cross_language_runner_source)
        self.assertIn("tc qdisc replace dev eth0 root netem", cross_language_runner_source)
        self.assertIn(
            'service_request_repair_configuration": "middleware_default"',
            cross_language_runner_source,
        )
        self.assertNotIn(
            "export FLEETQOX_RMW_SERVICE_REQUEST_REPEATS=",
            cross_language_runner_source,
        )
        self.assertIn(
            "bounded_service_discovery_repair_claim",
            cross_language_runner_source,
        )
        self.assertIn(
            "nonblocking_async_service_request_repair_claim",
            cross_language_runner_source,
        )
        self.assertIn(
            "response_cancelled_request_repair_claim",
            cross_language_runner_source,
        )
        self.assertIn("service_exactly_once_claim", cross_language_runner_source)
        self.assertIn(
            "large_sequence_service_fragmentation_claim",
            cross_language_runner_source,
        )
        self.assertIn("plan_response_payload_bytes > 65507", cross_language_runner_source)
        header = (PKG / "include" / "rmw_fleetqox_cpp" / "data_frame.hpp").read_text()
        self.assertIn("fleetrmw.data_frame.v1", header)
        self.assertIn("fleetrmw.ack_nack.v1", header)
        self.assertIn("fleetrmw.route_advertisement.v1", header)
        self.assertIn("fleetrmw.graph_advertisement.v1", header)
        self.assertIn("fleetrmw.service_frame.v1", header)
        self.assertIn("fleetrmw.action_frame.v1", header)
        self.assertIn("AckNackFrame", header)
        self.assertIn("ActionFrame", header)
        self.assertIn("decode_action_frame", header)
        self.assertIn("decode_ack_nack", header)
        self.assertIn("initialized", header)
        self.assertIn("std::uint64_t domain_id", header)

    def test_transport_loop_smoke_compiles_and_runs_without_ros(self) -> None:
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("c++ compiler is not available")
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "fleetrmw_transport_loop_smoke"
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-I",
                    str(PKG / "include"),
                    str(PKG / "src" / "data_frame.cpp"),
                    str(PKG / "src" / "transport_loop_smoke.cpp"),
                    "-o",
                    str(binary),
                ],
                check=True,
                cwd=ROOT,
            )
            result = subprocess.run(
                [
                    str(binary),
                    "--robot-count",
                    "3",
                    "--samples-per-robot",
                    "5",
                    "--skip-every",
                    "2",
                    "--json",
                ],
                check=True,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
            )
            first_loss_result = subprocess.run(
                [
                    str(binary),
                    "--robot-count",
                    "1",
                    "--samples-per-robot",
                    "3",
                    "--skip-first",
                    "--json",
                ],
                check=True,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
            )
        summary = json.loads(result.stdout)
        first_loss_summary = json.loads(first_loss_result.stdout)
        self.assertEqual(summary["published"], 15)
        self.assertEqual(summary["taken"], 15)
        self.assertEqual(summary["retransmitted"], 6)
        self.assertEqual(summary["missing_sequence_range_count"], 6)
        self.assertEqual(first_loss_summary["published"], 3)
        self.assertEqual(first_loss_summary["taken"], 3)
        self.assertEqual(first_loss_summary["retransmitted"], 1)
        self.assertEqual(first_loss_summary["missing_sequence_range_count"], 1)
        self.assertEqual(first_loss_summary["late_out_of_order_count"], 1)
        self.assertTrue(summary["baseline_reorder_ack_safe"])
        self.assertTrue(summary["delayed_missing_sequence_exactly_acked"])
        self.assertTrue(first_loss_summary["baseline_reorder_ack_safe"])
        self.assertTrue(first_loss_summary["delayed_missing_sequence_exactly_acked"])

    def test_cpp_frame_probe_decodes_python_data_frame(self) -> None:
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("c++ compiler is not available")
        boundary = FleetRmwBoundary()
        published = boundary.publish(
            Ros2Sample(
                topic="/robot_0005/cmd_vel",
                msg_type="geometry_msgs/msg/Twist",
                robot_id="robot_0005",
                sequence_number=42,
                source_timestamp_ns=42_000_000,
            ),
            timestamp_ms=42.0,
            tick=42,
        )
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "fleetrmw_frame_probe"
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-I",
                    str(PKG / "include"),
                    str(PKG / "src" / "data_frame.cpp"),
                    str(PKG / "src" / "frame_probe.cpp"),
                    "-o",
                    str(binary),
                ],
                check=True,
                cwd=ROOT,
            )
            result = subprocess.run(
                [str(binary)],
                input=published["encoded"],
                check=True,
                cwd=ROOT,
                stdout=subprocess.PIPE,
            )
        decoded = json.loads(result.stdout)
        self.assertEqual(decoded["status"], "decoded")
        self.assertEqual(decoded["robot_id"], "robot_0005")
        self.assertEqual(decoded["topic"], "/robot_0005/cmd_vel")
        self.assertEqual(decoded["source_sequence_number"], 42)

    def test_cpp_data_frame_round_trips_serialized_payload(self) -> None:
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("c++ compiler is not available")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "payload_roundtrip.cpp"
            source.write_text(
                r'''
#include "rmw_fleetqox_cpp/data_frame.hpp"

#include <cstdint>
#include <iostream>
#include <vector>

int main()
{
  rmw_fleetqox_cpp::DataFrame frame{
    "robot_0001",
    "/robot_0001/cmd_vel",
    "fpubcpp-test",
    7,
    7000000,
    std::vector<std::uint8_t>{0x66, 0x72, 0x6d, 0x77},
    0,
    "fleetqox/test/Frame"};
  const std::string encoded = rmw_fleetqox_cpp::encode_data_frame(frame);
  const auto decoded = rmw_fleetqox_cpp::decode_data_frame(encoded);
  if (!decoded || decoded->serialized_payload != frame.serialized_payload) {
    return 1;
  }
  std::cout << decoded->serialized_payload.size() << std::endl;
  return 0;
}
''',
                encoding="utf-8",
            )
            binary = Path(tmp) / "payload_roundtrip"
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-I",
                    str(PKG / "include"),
                    str(PKG / "src" / "data_frame.cpp"),
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
                cwd=ROOT,
            )
            result = subprocess.run(
                [str(binary)],
                check=True,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
            )
        self.assertEqual(result.stdout.strip(), "4")

    def test_cpp_route_advertisement_round_trips(self) -> None:
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("c++ compiler is not available")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "route_advertisement_roundtrip.cpp"
            source.write_text(
                r'''
#include "rmw_fleetqox_cpp/data_frame.hpp"

#include <iostream>

int main()
{
  rmw_fleetqox_cpp::RouteAdvertisement advertisement{
    "subscriber-1",
    "subscriber",
    "/fleetqox/discovery_probe",
    "std_msgs/msg/String",
    5000};
  const std::string encoded = rmw_fleetqox_cpp::encode_route_advertisement(advertisement);
  const auto decoded = rmw_fleetqox_cpp::decode_route_advertisement(encoded);
  if (!decoded || decoded->topic != advertisement.topic ||
    decoded->role != advertisement.role || decoded->lease_ms != advertisement.lease_ms)
  {
    return 1;
  }
  std::cout << decoded->topic << std::endl;
  return 0;
}
''',
                encoding="utf-8",
            )
            binary = Path(tmp) / "route_advertisement_roundtrip"
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-I",
                    str(PKG / "include"),
                    str(PKG / "src" / "data_frame.cpp"),
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
                cwd=ROOT,
            )
            result = subprocess.run(
                [str(binary)],
                check=True,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
            )
        self.assertEqual(result.stdout.strip(), "/fleetqox/discovery_probe")

    def test_cpp_graph_advertisement_round_trips(self) -> None:
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("c++ compiler is not available")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "graph_advertisement_roundtrip.cpp"
            source.write_text(
                r'''
#include "rmw_fleetqox_cpp/data_frame.hpp"

#include <iostream>

int main()
{
  rmw_fleetqox_cpp::GraphAdvertisement advertisement{
    "publisher-1",
    "add",
    "publisher",
    "talker",
    "/fleetqox",
    "/fleetqox/chatter",
    "std_msgs/msg/String",
    "00112233445566778899aabbccddeeff",
    rmw_fleetqox_cpp::GraphQosProfile{1, 10, 2, 2, 0, 0, 0, 0, 1, 0, 0, 0},
    5000};
  const std::string encoded = rmw_fleetqox_cpp::encode_graph_advertisement(advertisement);
  const auto decoded = rmw_fleetqox_cpp::decode_graph_advertisement(encoded);
  if (!decoded || decoded->entity_kind != advertisement.entity_kind ||
    decoded->topic != advertisement.topic || decoded->node_name != advertisement.node_name ||
    decoded->endpoint_gid != advertisement.endpoint_gid || decoded->qos.depth != 10)
  {
    return 1;
  }
  std::cout << decoded->entity_kind << ":" << decoded->topic << std::endl;
  return 0;
}
''',
                encoding="utf-8",
            )
            binary = Path(tmp) / "graph_advertisement_roundtrip"
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-I",
                    str(PKG / "include"),
                    str(PKG / "src" / "data_frame.cpp"),
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
                cwd=ROOT,
            )
            result = subprocess.run(
                [str(binary)],
                check=True,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
            )
        self.assertEqual(result.stdout.strip(), "publisher:/fleetqox/chatter")

    def test_cpp_service_frame_round_trips(self) -> None:
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("c++ compiler is not available")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "service_frame_roundtrip.cpp"
            source.write_text(
                r'''
#include "rmw_fleetqox_cpp/data_frame.hpp"

#include <iostream>

int main()
{
  rmw_fleetqox_cpp::ServiceFrame frame{
    "request",
    "/fleetqox/set_bool",
    "std_srvs/srv/SetBool",
    "client-1",
    "service-1",
    9,
    12345,
    5000000,
    {0x01, 0x02, 0x03}};
  frame.domain_id = 31;
  frame.client_priority = 7;
  frame.client_weight = 3;
  frame.request_deadline_ns = 42000000;
  const std::string encoded = rmw_fleetqox_cpp::encode_service_frame(frame);
  const auto decoded = rmw_fleetqox_cpp::decode_service_frame(encoded);
  if (!decoded || decoded->role != frame.role ||
    decoded->service_name != frame.service_name ||
    decoded->client_endpoint_id != frame.client_endpoint_id ||
    decoded->sequence_id != frame.sequence_id ||
    decoded->lifespan_ns != frame.lifespan_ns ||
    decoded->domain_id != frame.domain_id ||
    decoded->client_priority != frame.client_priority ||
    decoded->client_weight != frame.client_weight ||
    decoded->request_deadline_ns != frame.request_deadline_ns ||
    decoded->serialized_payload != frame.serialized_payload)
  {
    return 1;
  }
  if (rmw_fleetqox_cpp::service_frame_expired(frame, frame.source_timestamp_ns + 4999999)) {
    return 2;
  }
  if (!rmw_fleetqox_cpp::service_frame_expired(frame, frame.source_timestamp_ns + 5000001)) {
    return 3;
  }
  const std::string legacy = std::string(rmw_fleetqox_cpp::kDataFrameMagic) +
    "{\"schema_version\":\"fleetrmw.service_frame.v1\",\"kind\":\"service_frame\","
    "\"role\":\"response\",\"service_name\":\"/fleetqox/set_bool\","
    "\"type_name\":\"std_srvs/srv/SetBool\",\"client_endpoint_id\":\"client-1\","
    "\"service_endpoint_id\":\"service-1\",\"sequence_id\":10,"
    "\"source_timestamp_ns\":12345}";
  const auto legacy_decoded = rmw_fleetqox_cpp::decode_service_frame(legacy);
  if (!legacy_decoded || legacy_decoded->lifespan_ns != 0 ||
    legacy_decoded->domain_id != 0 ||
    legacy_decoded->client_priority != 0 ||
    legacy_decoded->client_weight != 1 ||
    legacy_decoded->request_deadline_ns != 0 ||
    rmw_fleetqox_cpp::service_frame_expired(*legacy_decoded, 999999999))
  {
    return 4;
  }
  std::cout << decoded->role << ":" << decoded->service_name << std::endl;
  return 0;
}
''',
                encoding="utf-8",
            )
            binary = Path(tmp) / "service_frame_roundtrip"
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-I",
                    str(PKG / "include"),
                    str(PKG / "src" / "data_frame.cpp"),
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
                cwd=ROOT,
            )
            result = subprocess.run(
                [str(binary)],
                check=True,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
            )
        self.assertEqual(result.stdout.strip(), "request:/fleetqox/set_bool")

    def test_cpp_action_frame_round_trips(self) -> None:
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("c++ compiler is not available")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "action_frame_roundtrip.cpp"
            source.write_text(
                r'''
#include "rmw_fleetqox_cpp/data_frame.hpp"

#include <iostream>
#include <string>

int main()
{
  rmw_fleetqox_cpp::ActionFrame frame{
    "feedback",
    "/fleetqox/navigate_to_pose",
    "nav2_msgs/action/NavigateToPose",
    "action-endpoint-1",
    "goal-00112233",
    42,
    12345,
    5000000,
    {0x42, 0xA0, 0x5A}};
  frame.domain_id = 31;
  const std::string encoded = rmw_fleetqox_cpp::encode_action_frame(frame);
  const auto decoded = rmw_fleetqox_cpp::decode_action_frame(encoded);
  if (!decoded || decoded->role != frame.role ||
    decoded->action_name != frame.action_name ||
    decoded->type_name != frame.type_name ||
    decoded->endpoint_id != frame.endpoint_id ||
    decoded->goal_id != frame.goal_id ||
    decoded->sequence_id != frame.sequence_id ||
    decoded->lifespan_ns != frame.lifespan_ns ||
    decoded->domain_id != frame.domain_id ||
    decoded->serialized_payload != frame.serialized_payload)
  {
    return 1;
  }
  if (rmw_fleetqox_cpp::action_frame_expired(frame, frame.source_timestamp_ns + 4999999)) {
    return 2;
  }
  if (!rmw_fleetqox_cpp::action_frame_expired(frame, frame.source_timestamp_ns + 5000001)) {
    return 3;
  }
  const std::string legacy = std::string(rmw_fleetqox_cpp::kDataFrameMagic) +
    "{\"schema_version\":\"fleetrmw.action_frame.v1\",\"kind\":\"action_frame\","
    "\"role\":\"result\",\"action_name\":\"/fleetqox/navigate_to_pose\","
    "\"type_name\":\"nav2_msgs/action/NavigateToPose\","
    "\"endpoint_id\":\"action-endpoint-1\",\"goal_id\":\"goal-00112233\","
    "\"sequence_id\":43,\"source_timestamp_ns\":12345}";
  const auto legacy_decoded = rmw_fleetqox_cpp::decode_action_frame(legacy);
  if (!legacy_decoded || legacy_decoded->lifespan_ns != 0 ||
    legacy_decoded->domain_id != 0 ||
    rmw_fleetqox_cpp::action_frame_expired(*legacy_decoded, 999999999))
  {
    return 4;
  }
  const bool rejects_service_schema = !rmw_fleetqox_cpp::decode_action_frame(
    rmw_fleetqox_cpp::encode_service_frame(
      rmw_fleetqox_cpp::ServiceFrame{
        "request",
        "/fleetqox/set_bool",
        "std_srvs/srv/SetBool",
        "client-1",
        "service-1",
        1,
        1000000,
        5000000,
        {0x01}}));
  if (!rejects_service_schema) {
    return 5;
  }
  std::cout << decoded->role << ":" << decoded->action_name << std::endl;
  return 0;
}
''',
                encoding="utf-8",
            )
            binary = Path(tmp) / "action_frame_roundtrip"
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-I",
                    str(PKG / "include"),
                    str(PKG / "src" / "data_frame.cpp"),
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
                cwd=ROOT,
            )
            result = subprocess.run(
                [str(binary)],
                check=True,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
            )
        self.assertEqual(result.stdout.strip(), "feedback:/fleetqox/navigate_to_pose")

    def test_take_sequence_contract_is_exported_probed_and_thread_safe(self) -> None:
        cmake = (PKG / "CMakeLists.txt").read_text()
        self.assertIn("fleetrmw_take_sequence_probe", cmake)
        self.assertIn("fleetrmw_take_sequence_ordering_smoke", cmake)

        source = (PKG / "src" / "rmw_pubsub.cpp").read_text()
        self.assertIn("rmw_ret_t rmw_take_sequence(", source)
        self.assertIn("std::recursive_mutex take_mutex{}", source)
        self.assertIn("message_sequence->capacity < count", source)
        self.assertIn("message_sequence->size = taken_count", source)

        probe_source = (PKG / "src" / "take_sequence_probe.cpp").read_text()
        self.assertIn("fleetrmw.rmw_take_sequence_probe.v1", probe_source)
        self.assertIn("rmw_take_sequence(", probe_source)
        self.assertIn("rmw_fleetqox_cpp_socket_data_frames_received", probe_source)
        self.assertIn("concurrent_call_count", probe_source)
        self.assertIn("invalid_capacity_unchanged", probe_source)
        self.assertIn("thread_safe_same_subscription_take_sequence", probe_source)

        runner_source = (
            ROOT / "scripts" / "run_rmw_docker_take_sequence_probe.py"
        ).read_text()
        self.assertIn("fleetrmw.rmw_docker_take_sequence_probe.v1", runner_source)
        self.assertIn("missing_baseline_symbols", runner_source)
        self.assertIn("rmw_take_sequence_exported", runner_source)
        self.assertIn("required_symbol_parity", runner_source)
        self.assertIn("successful_runs == run_count", runner_source)

        capabilities = json.loads((PKG / "capabilities.json").read_text())
        supported = capabilities["supported"]
        claims = capabilities["claim_boundaries"]
        self.assertTrue(supported["rmw_take_sequence_ordered_partial_empty_semantics"])
        self.assertTrue(supported["rmw_take_sequence_same_subscription_concurrent_ordering"])
        self.assertTrue(claims["docker_rmw_take_sequence_5run_probe"])
        self.assertTrue(claims["rmw_take_sequence_thread_safety_claim"])
        self.assertTrue(claims["rmw_required_symbol_parity_with_fastrtps_jazzy_claim"])

    def test_wait_for_all_acked_tracks_each_matched_subscriber(self) -> None:
        cmake = (PKG / "CMakeLists.txt").read_text()
        self.assertIn("fleetrmw_wait_for_all_acked_probe", cmake)
        self.assertIn("fleetrmw_wait_for_all_acked_smoke", cmake)
        self.assertIn("fleetrmw_remote_wait_for_all_acked_probe", cmake)

        frame_header = (PKG / "include" / "rmw_fleetqox_cpp" / "data_frame.hpp").read_text()
        frame_source = (PKG / "src" / "data_frame.cpp").read_text()
        self.assertIn("std::string subscriber_id", frame_header)
        self.assertIn('json_escape(subscriber_id)', frame_source)
        self.assertIn('json_string_value(body, "subscriber_id")', frame_source)

        pubsub_source = (PKG / "src" / "rmw_pubsub.cpp").read_text()
        stubs_source = (PKG / "src" / "rmw_stubs.cpp").read_text()
        self.assertIn("wait_for_all_acked_impl", pubsub_source)
        self.assertIn("pending_subscriber_ids", pubsub_source)
        self.assertIn("g_all_acked_condition", pubsub_source)
        self.assertIn("FLEETQOX_RMW_TEST_ACK_DELAY_SUBSCRIPTION_SUFFIX", pubsub_source)
        self.assertIn("rmw_fleetqox_cpp_publisher_wait_for_all_acked", stubs_source)

        probe_source = (PKG / "src" / "wait_for_all_acked_probe.cpp").read_text()
        self.assertIn("fleetrmw.rmw_wait_for_all_acked_probe.v2", probe_source)
        self.assertIn("partial_observed_ack_count", probe_source)
        self.assertIn("completed_observed_ack_count", probe_source)
        self.assertIn("snapshot_excludes_later_publish", probe_source)
        self.assertIn("concurrent_waiters_ok", probe_source)
        self.assertIn("RMW_DURATION_INFINITE", probe_source)
        self.assertIn("unmatch_releases_wait", probe_source)
        self.assertIn("best_effort_immediate_ok", probe_source)
        runner_source = (
            ROOT / "scripts" / "run_rmw_docker_wait_for_all_acked_probe.py"
        ).read_text()
        self.assertIn("fleetrmw.rmw_docker_wait_for_all_acked_probe.v2", runner_source)
        self.assertIn("partial_ack_never_misreported_complete", runner_source)
        self.assertIn("snapshot_excludes_later_publish_all", runner_source)
        self.assertIn("concurrent_waiters_all", runner_source)
        self.assertIn("unmatch_releases_wait_all", runner_source)
        self.assertIn("best_effort_immediate_all", runner_source)
        remote_probe_source = (
            PKG / "src" / "remote_wait_for_all_acked_probe.cpp"
        ).read_text()
        self.assertIn(
            "fleetrmw.remote_wait_for_all_acked_probe.v1", remote_probe_source
        )
        self.assertIn("remote_two_reader_ack_snapshot_claim", remote_probe_source)
        remote_runner_source = (
            ROOT / "scripts" / "run_rmw_docker_remote_wait_for_all_acked_probe.py"
        ).read_text()
        self.assertIn(
            "fleetrmw.docker_remote_wait_for_all_acked_probe.v1",
            remote_runner_source,
        )
        self.assertIn("--expected-ack-nack-forwarded", remote_runner_source)
        self.assertIn("FLEETQOX_RMW_TEST_ACK_DELAY_MS=450", remote_runner_source)

        capabilities = json.loads((PKG / "capabilities.json").read_text())
        self.assertTrue(capabilities["supported"]["ack_nack_subscriber_identity"])
        self.assertTrue(
            capabilities["claim_boundaries"][
                "publisher_wait_for_all_acked_matched_snapshot_claim"
            ]
        )
        self.assertTrue(
            capabilities["claim_boundaries"][
                "publisher_wait_for_all_acked_public_rmw_contract_claim"
            ]
        )
        self.assertTrue(
            capabilities["claim_boundaries"][
                "publisher_wait_for_all_acked_post_snapshot_publish_exclusion_claim"
            ]
        )
        self.assertTrue(
            capabilities["claim_boundaries"][
                "publisher_wait_for_all_acked_concurrent_waiters_claim"
            ]
        )
        self.assertTrue(
            capabilities["claim_boundaries"][
                "publisher_wait_for_all_acked_infinite_timeout_claim"
            ]
        )
        self.assertTrue(
            capabilities["claim_boundaries"][
                "publisher_wait_for_all_acked_reader_unmatch_release_claim"
            ]
        )
        self.assertTrue(
            capabilities["claim_boundaries"][
                "publisher_wait_for_all_acked_best_effort_immediate_claim"
            ]
        )
        self.assertTrue(
            capabilities["supported"][
                "publisher_wait_for_all_acked_post_snapshot_publish_exclusion"
            ]
        )
        self.assertTrue(
            capabilities["supported"][
                "publisher_wait_for_all_acked_concurrent_thread_safety"
            ]
        )
        self.assertTrue(
            capabilities["supported"][
                "publisher_wait_for_all_acked_infinite_timeout"
            ]
        )
        self.assertTrue(
            capabilities["supported"][
                "publisher_wait_for_all_acked_reader_unmatch_release"
            ]
        )
        self.assertTrue(
            capabilities["supported"][
                "publisher_wait_for_all_acked_best_effort_immediate_success"
            ]
        )
        self.assertTrue(
            capabilities["supported"][
                "remote_publisher_wait_for_all_acked_two_reader_udp_router_netem"
            ]
        )
        self.assertTrue(
            capabilities["claim_boundaries"][
                "docker_remote_wait_for_all_acked_5run_probe"
            ]
        )
        self.assertTrue(
            capabilities["claim_boundaries"][
                "remote_publisher_wait_for_all_acked_two_reader_claim"
            ]
        )
        self.assertFalse(
            capabilities["claim_boundaries"]["full_dds_wait_for_all_acked_semantics_claim"]
        )

    def test_rmw_pubsub_uses_socket_backed_data_frame_transport(self) -> None:
        source = (PKG / "src" / "rmw_pubsub.cpp").read_text()
        self.assertIn("LoopbackSocketTransport", source)
        self.assertIn("sendto(", source)
        self.assertIn("recvfrom(", source)
        self.assertIn("FLEETQOX_RMW_BIND", source)
        self.assertIn("FLEETQOX_RMW_PEERS", source)
        self.assertIn("getaddrinfo", source)
        self.assertIn("decode_data_frame(encoded_frame)", source)
        self.assertIn("rmw_fleetqox_cpp_socket_frames_sent", source)
        self.assertIn("rmw_fleetqox_cpp_socket_frames_received", source)
        self.assertIn("rmw_fleetqox_cpp_socket_data_frames_received", source)
        self.assertIn("send_subscription_advertisement", source)
        self.assertIn("send_graph_advertisement", source)
        self.assertIn("apply_received_graph_advertisement", source)
        self.assertIn("rmw_fleetqox_cpp_graph_apply_remote_advertisement", source)
        self.assertIn("typed_message_size_from_type_support", source)
        self.assertIn("rmw_fleetqox_cpp_type_erased_probe", source)
        self.assertIn("rosidl_typesupport_introspection_c__identifier", source)
        self.assertIn("serialize_introspection_c_message", source)
        self.assertIn("deserialize_introspection_c_message", source)
        self.assertIn("serialize_introspection_cpp_message", source)
        self.assertIn("deserialize_introspection_cpp_message", source)
        self.assertIn("rosidl_typesupport_introspection_cpp::typesupport_identifier", source)
        self.assertIn("rosidl_typesupport_cpp::get_message_typesupport_handle_function", source)
        self.assertIn("type_name_from_type_support", source)
        self.assertIn("resolve_effective_type_support", source)
        self.assertIn("rosidl_typesupport_c__get_message_typesupport_handle_function", source)
        self.assertIn("rmw_fleetqox_cpp_socket_ensure_started", source)
        self.assertIn("g_retransmit_ledger", source)
        self.assertIn("decode_ack_nack", source)
        self.assertIn("send_ack_nack", source)
        self.assertIn("send_retransmission_frame", source)
        self.assertIn("FLEETQOX_RMW_DROP_SOURCE_SEQUENCES", source)
        self.assertIn("FLEETQOX_RMW_DROP_SOURCE_SEQUENCE_SEND_COUNT", source)
        self.assertIn("qos_liveliness_automatic", source)
        self.assertIn("RMW_QOS_POLICY_LIVELINESS_AUTOMATIC", source)
        self.assertIn("liveliness_assert", source)
        self.assertIn("expire_remote_manual_liveliness_locked", source)
        self.assertIn("g_remote_manual_liveliness_reassertions", source)
        self.assertIn("liveliness_qos_incompatible", source)
        self.assertIn("RMW_QOS_POLICY_LIVELINESS", source)
        self.assertIn(
            "qos_profile_get_best_available_for_topic_publisher", source
        )
        self.assertIn(
            "qos_profile_get_best_available_for_topic_subscription", source
        )
        self.assertIn("FLEETQOX_RMW_PEER_POLICY", source)
        self.assertIn("adaptive_failover", source)
        self.assertIn("adaptive_score", source)
        self.assertIn("adaptive_qos", source)
        self.assertIn("fleet_plan", source)
        self.assertIn("FLEETQOX_RMW_FLEET_PATH_PLAN", source)
        self.assertIn("FLEETQOX_RMW_FLEET_PATH_PLAN_FILE", source)
        self.assertIn("FLEETQOX_RMW_REPAIR_PATH_PLAN", source)
        self.assertIn("FLEETQOX_RMW_REPAIR_PATH_PLAN_FILE", source)
        self.assertIn("FLEETQOX_RMW_REPAIR_RETRANSMISSION_BUDGET", source)
        self.assertIn("FLEETQOX_RMW_REPAIR_MIN_INTERVAL_MS", source)
        self.assertIn("FLEETQOX_RMW_REPAIR_MAX_ATTEMPTS_PER_SEQUENCE", source)
        self.assertIn("FLEETQOX_RMW_REPAIR_ADMISSION_STRICT", source)
        self.assertIn("parse_fleet_repair_plan", source)
        self.assertIn("source_sequences", source)
        self.assertIn("FleetPathPlanRule", source)
        self.assertIn("parse_fleet_path_plan", source)
        self.assertIn("refresh_fleet_path_plan_from_file", source)
        self.assertIn("refresh_repair_path_plan_from_file", source)
        self.assertIn("rmw_fleetqox_cpp_last_take_source_sequence", source)
        self.assertIn("rmw_fleetqox_cpp_last_take_source_timestamp_ns", source)
        self.assertIn("rmw_fleetqox_cpp_last_take_timestamp_ns", source)
        self.assertIn("rmw_fleetqox_cpp_duplicate_data_frames_deduped", source)
        self.assertIn("rmw_fleetqox_cpp_out_of_order_data_frames_observed", source)
        self.assertIn("rmw_fleetqox_cpp_socket_ack_nack_duplicate_received", source)
        self.assertIn("rmw_fleetqox_cpp_socket_ack_nack_out_of_order_received", source)
        self.assertIn("rmw_fleetqox_cpp_socket_idle_repair_ack_nack_sent", source)
        self.assertIn("g_idle_repair_ack_nack_sent", source)
        self.assertIn("feedback_from_sequence_state", source)
        self.assertIn("last_repair_request_ns", source)
        self.assertIn('"fpubcpp-" + socket_transport().bound_endpoint()', source)
        self.assertIn('"fsubcpp-" + socket_transport().bound_endpoint()', source)
        self.assertIn("peer_path_ids_", source)
        self.assertIn("fleet_plan_targets", source)
        self.assertIn("FLEETQOX_RMW_REDUNDANT_DEADLINE_MS", source)
        self.assertIn("adaptive_failovers", source)
        self.assertIn("adaptive_unicast_frames", source)
        self.assertIn("adaptive_redundant_frames", source)
        self.assertIn("fleet_plan_frames", source)
        self.assertIn("fleet_plan_redundant_frames", source)
        self.assertIn("fleet_plan_selected_path_count", source)
        self.assertIn("rmw_fleetqox_cpp_socket_fleet_plan_last_paths", source)
        self.assertIn("repair_plan_selected_path_count", source)
        self.assertIn("rmw_fleetqox_cpp_socket_repair_plan_last_paths", source)
        self.assertIn("rmw_fleetqox_cpp_socket_repair_budget_exhausted", source)
        self.assertIn("rmw_fleetqox_cpp_socket_repair_requests_coalesced", source)
        self.assertIn(
            "rmw_fleetqox_cpp_socket_repair_sequence_attempt_limit_exhausted",
            source,
        )
        self.assertIn("rmw_fleetqox_cpp_socket_repair_not_admitted", source)
        self.assertIn("adaptive_peer_score_sum", source)
        self.assertIn("adaptive_selected_peer_index", source)
        self.assertIn("rmw_fleetqox_cpp_socket_peer_policy", source)
        self.assertIn("send_data_frame", source)
        self.assertIn("rmw_fleetqox_cpp_socket_nack_retransmissions", source)
        self.assertIn("ReliableRetransmitEntry", source)
        self.assertIn("FLEETQOX_RMW_RELIABLE_ACK_TIMEOUT_MS", source)
        self.assertIn("FLEETQOX_RMW_RELIABLE_MAX_RETRANSMISSIONS", source)
        self.assertIn("FLEETQOX_RMW_GRAPH_RENEW_INTERVAL_MS", source)
        self.assertIn("reliable_retransmit_loop", source)
        self.assertIn("rmw_fleetqox_cpp_socket_reliable_timeout_retransmissions", source)
        self.assertIn("rmw_subscription_set_on_new_message_callback", source)
        self.assertIn("on_new_message_callback", source)
        self.assertIn("queue_event_callback_locked", source)
        self.assertIn("inflight_callbacks", source)
        self.assertIn("g_entity_callback_condition.wait", source)
        self.assertIn("data->destroying = true", source)
        callback_teardown_probe = PKG / "src" / "callback_teardown_probe.cpp"
        self.assertTrue(callback_teardown_probe.exists())
        callback_teardown_source = callback_teardown_probe.read_text()
        self.assertIn("destroy_blocked_before_release", callback_teardown_source)
        self.assertIn("fleetrmw.callback_teardown_probe.v1", callback_teardown_source)
        callback_teardown_runner = (
            ROOT / "scripts" / "run_rmw_docker_callback_teardown_probe.py"
        )
        self.assertTrue(callback_teardown_runner.exists())
        callback_teardown_runner_source = callback_teardown_runner.read_text()
        self.assertIn(
            "callback_teardown_quiescence_claim",
            callback_teardown_runner_source,
        )
        self.assertIn("callback_teardown_repeated_claim", callback_teardown_runner_source)
        self.assertIn("rmw_fleetqox_cpp_waitable_subscription_has_data", source)
        self.assertIn("make_endpoint_gid", source)
        self.assertIn("endpoint_gid", source)
        self.assertIn("graph_qos_from_rmw", source)
        self.assertIn("rmw_fleetqox_cpp_send_graph_advertisement", source)
        self.assertIn("rmw_fleetqox_cpp_send_encoded_frame", source)
        self.assertIn("FLEETQOX_FRAGMENT_V1", source)
        self.assertIn("FLEETQOX_REPAIR_FRAGMENT_V1", source)
        self.assertIn("FLEETQOX_REPAIR_FRAGMENT_NACK_V1", source)
        self.assertIn("send_fragmented_payload_to_targets", source)
        self.assertIn(
            "send_loss_resilient_fragmented_payload_to_targets",
            source,
        )
        self.assertIn(
            "FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES",
            source,
        )
        self.assertIn("try_reassemble_fragment", source)
        self.assertIn("handle_received_datagram", source)
        self.assertIn("strict_protected_fragment_wrapper", source)
        self.assertIn(
            "udp_wire_payload && (udp_aead_required_ || udp_peer_auth_required_)",
            source,
        )
        self.assertIn("FLEETQOX_RMW_UDP_SOCKET_BUFFER_BYTES", source)
        self.assertIn("FLEETQOX_RMW_UDP_SEND_PACING_US", source)
        self.assertIn("FLEETQOX_RMW_UDP_DATAGRAM_BUDGET_BYTES", source)
        self.assertIn(
            "effective_loss_resilient_fragment_chunk_bytes",
            source,
        )
        self.assertIn("udp_protection_overhead_upper_bound", source)
        self.assertIn("pace_udp_send_locked", source)
        self.assertIn("FLEETQOX_RMW_FRAGMENT_NACK_INTERVAL_MS", source)
        self.assertIn("FLEETQOX_RMW_FRAGMENT_NACK_MAX_REQUESTS", source)
        self.assertIn("FLEETQOX_RMW_FRAGMENT_HISTORY_LIMIT", source)
        self.assertIn("FLEETQOX_RMW_FRAGMENT_ASYNC_SEND", source)
        self.assertIn("FLEETQOX_RMW_FRAGMENT_SEND_QUEUE_LIMIT", source)
        self.assertIn(
            "FLEETQOX_RMW_FRAGMENT_QUEUE_ADMISSION_THRESHOLD",
            source,
        )
        self.assertIn(
            "FLEETQOX_RMW_FRAGMENT_QUEUE_ADMISSION_TIMEOUT_MS",
            source,
        )
        self.assertIn("FLEETQOX_RMW_FRAGMENT_REPAIR_COOLDOWN_MS", source)
        self.assertIn("FLEETQOX_RMW_FRAGMENT_REPAIR_QUEUE_LIMIT", source)
        self.assertIn("fragment_repair_recent_send_ns_", source)
        self.assertIn(
            "consecutive_initial_fragments >= effective_initial_burst",
            source,
        )
        self.assertIn("completed_fragment_assemblies_", source)
        self.assertIn("include_trailing_indexes", source)
        self.assertIn("tail_guard_ns", source)
        self.assertIn(
            "now_ns - assembly.last_update_ns >= tail_guard_ns",
            source,
        )
        self.assertIn("FLEETQOX_RMW_FRAGMENT_TAIL_GUARD_MS", source)
        self.assertIn("fragment_repair_pending_keys_", source)
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_repair_requests_coalesced",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_repair_cooldown_coalesced",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_completed_fragment_duplicates_dropped",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_duplicate_no_progress_drops",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_queue_admission_waits",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_repair_queue_deferrals",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_repair_queue_high_water",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_repair_round_robin_rotations",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_repair_max_active_frames",
            source,
        )
        self.assertIn("fragment_repair_send_queues_", source)
        self.assertIn("fragment_repair_send_order_", source)
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_repair_pressure_priority_promotions",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_completion_markers_sent",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_completion_markers_received",
            source,
        )
        self.assertIn("FLEETQOX_REPAIR_FRAGMENT_END_V1", source)
        self.assertIn("sender_complete_observed", source)
        self.assertIn("effective_initial_burst = 1", source)
        self.assertIn("effective_initial_burst = 2", source)
        self.assertIn("effective_initial_burst = 4", source)
        self.assertIn(
            "FLEETQOX_RMW_FRAGMENT_NACK_MAX_INDEXES_PER_REQUEST",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_nack_indexes_requested",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_progressive_nacks_sent",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_progress_grace_deferrals",
            source,
        )
        self.assertIn("initial_quiescence_pending", source)
        self.assertIn("bounded_progress_grace_pending", source)
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_repair_source_denials",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_repair_reader_budget_exhausted",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_initial_round_robin_rotations",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_initial_frame_switches",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_initial_max_consecutive_same_frame",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_initial_max_consecutive_same_frame_while_contended",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_initial_max_active_frames",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_async_send_completions",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_udp_datagram_size_high_water",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_effective_chunk_bytes_min",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_chunk_budget_reductions",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_udp_datagram_budget_failures",
            source,
        )
        self.assertIn("!shared_memory_only()", source)
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_initial_pending_timeout_suppressions",
            source,
        )
        self.assertIn(
            "FLEETQOX_RMW_FRAGMENT_WHOLE_FALLBACK_GRACE_MS",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_whole_fallback_grace_deferrals",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_nack_index_budget_reductions",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_nack_max_sweep_indexes_requested",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_nack_sweep_budget_exhaustions",
            source,
        )
        self.assertIn("FLEETQOX_RMW_FRAGMENT_ASSEMBLY_TTL_MS", source)
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_assembly_ttl_expirations",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_assembly_ttl_expired_missing_indexes",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_nack_exhausted_assemblies",
            source,
        )
        self.assertIn("fragment_observed_by_reader", source)
        self.assertIn(
            "rmw_fleetqox_cpp_socket_"
            "fragment_observed_timeout_retransmissions_suppressed",
            source,
        )
        self.assertIn(
            "FLEETQOX_RMW_FRAGMENT_WHOLE_FALLBACK_INTERVAL_MS",
            source,
        )
        self.assertIn("FLEETQOX_RMW_FRAGMENT_ASSEMBLY_LIMIT", source)
        self.assertIn("FLEETQOX_RMW_FRAGMENT_MAX_ASSEMBLY_BYTES", source)
        self.assertIn(
            "rmw_fleetqox_cpp_socket_"
            "fragment_whole_fallback_pacing_deferrals",
            source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_fragment_assembly_evictions",
            source,
        )
        self.assertIn("rmw_fleetqox_cpp_shutdown_pubsub_runtime", source)
        stubs_source = (PKG / "src" / "rmw_stubs.cpp").read_text()
        self.assertIn("FLEETQOX_RMW_SERVICE_REQUEST_REPEATS", stubs_source)
        self.assertIn("FLEETQOX_RMW_SERVICE_RESPONSE_REPEATS", stubs_source)
        self.assertIn("FLEETQOX_RMW_SERVICE_REQUEST_REPEAT_INTERVAL_MS", stubs_source)
        self.assertIn("FLEETQOX_RMW_SERVICE_RESPONSE_REPEAT_INTERVAL_MS", stubs_source)
        self.assertIn("response_replay_cache", stubs_source)
        self.assertIn("service_response_replay_key", stubs_source)
        self.assertIn("drop_duplicate_request", stubs_source)
        self.assertIn("drop_duplicate_response", stubs_source)
        self.assertIn("schedule_service_request_repair", stubs_source)
        self.assertIn("cancel_service_request_repair", stubs_source)
        self.assertIn("service_request_repair_worker", stubs_source)
        send_request_source = stubs_source.split(
            "rmw_ret_t rmw_send_request(", 1
        )[1].split("rmw_ret_t rmw_take_response(", 1)[0]
        self.assertNotIn("send_service_frame_with_repeats", send_request_source)
        router_source = (PKG / "src" / "udp_router_probe.cpp").read_text()
        self.assertIn("fleetrmw.router_path_telemetry.v1", router_source)
        self.assertIn("FLEETQOX_FRAGMENT_V1", router_source)
        self.assertIn("FLEETQOX_REPAIR_FRAGMENT_V1", router_source)
        self.assertIn("FLEETQOX_REPAIR_FRAGMENT_NACK_V1", router_source)
        self.assertIn("FLEETQOX_ROUTER_UDP_SOCKET_BUFFER_BYTES", router_source)
        self.assertIn("activity_counter", router_source)
        self.assertIn("satisfaction_dwell_activity_counter", router_source)
        self.assertIn("observed_router_state", router_source)
        self.assertIn("is_fragment_datagram", router_source)
        self.assertIn("forward_passthrough_datagram", router_source)
        self.assertIn("--path-id", router_source)
        self.assertIn("--telemetry-file", router_source)
        self.assertIn("--telemetry-latency-ms", router_source)
        self.assertIn("--telemetry-deadline-miss-ratio", router_source)
        self.assertIn("expected_ack_nack_forwarded", router_source)
        self.assertIn("--expected-ack-nack-forwarded", router_source)
        self.assertIn("drop_topic_prefix", router_source)
        self.assertIn("--drop-topic-prefix", router_source)
        self.assertIn("scheduler_fresh_deadline_misses", router_source)
        self.assertIn("scheduler_repair_deadline_misses", router_source)
        self.assertIn("scheduler_deadline_miss_frames", router_source)
        self.assertIn("append_router_path_telemetry", router_source)
        self.assertIn("rmw_fleetqox_cpp_handle_service_frame", source)
        self.assertIn("rmw_fleetqox_cpp_serialize_introspection_message", source)
        self.assertIn("frame_exceeds_lifespan", source)
        self.assertIn("enforce_subscription_depth_locked", source)
        self.assertIn("qos.lifespan", source)
        self.assertIn("RMW_QOS_POLICY_HISTORY_KEEP_LAST", source)
        typed_probe = PKG / "src" / "typed_pubsub_probe.cpp"
        self.assertTrue(typed_probe.exists())
        typed_probe_source = typed_probe.read_text()
        self.assertIn("fleetrmw.rmw_typed_pubsub_probe.v1", typed_probe_source)
        self.assertIn("rmw_publish(publisher", typed_probe_source)
        self.assertIn("rmw_take(subscription", typed_probe_source)
        std_msgs_probe = PKG / "src" / "std_msgs_string_probe.cpp"
        self.assertTrue(std_msgs_probe.exists())
        std_msgs_probe_source = std_msgs_probe.read_text()
        self.assertIn("fleetrmw.rmw_std_msgs_string_probe.v1", std_msgs_probe_source)
        self.assertIn("rmw_serialize(&outgoing", std_msgs_probe_source)
        self.assertIn("rmw_deserialize(&standalone", std_msgs_probe_source)
        self.assertIn("standalone_serialization", std_msgs_probe_source)
        self.assertIn("std_msgs__msg__String", std_msgs_probe_source)
        self.assertIn("FLEETQOX_PROBE_PAYLOAD_BYTES", std_msgs_probe_source)
        self.assertIn("FLEETQOX_PROBE_ITERATIONS", std_msgs_probe_source)
        self.assertIn("messages_taken", std_msgs_probe_source)
        twist_probe = PKG / "src" / "geometry_twist_probe.cpp"
        self.assertTrue(twist_probe.exists())
        twist_probe_source = twist_probe.read_text()
        self.assertIn("fleetrmw.rmw_geometry_twist_probe.v1", twist_probe_source)
        self.assertIn("geometry_msgs__msg__Twist", twist_probe_source)
        rcl_probe = PKG / "src" / "rcl_string_probe.cpp"
        self.assertTrue(rcl_probe.exists())
        rcl_probe_source = rcl_probe.read_text()
        self.assertIn("fleetrmw.rcl_string_probe.v1", rcl_probe_source)
        self.assertIn("RMW_IMPLEMENTATION", rcl_probe_source)
        rcl_talker = PKG / "src" / "rcl_graph_talker.cpp"
        self.assertTrue(rcl_talker.exists())
        rcl_talker_source = rcl_talker.read_text()
        self.assertIn("fleetrmw.rcl_graph_talker.v1", rcl_talker_source)
        self.assertIn("rcl_publisher_init", rcl_talker_source)
        self.assertIn("std_msgs/msg/String", rcl_talker_source)
        rcl_service_node = PKG / "src" / "rcl_service_node.cpp"
        self.assertTrue(rcl_service_node.exists())
        rcl_service_node_source = rcl_service_node.read_text()
        self.assertIn("fleetrmw.rcl_service_node.v1", rcl_service_node_source)
        self.assertIn("rcl_service_init", rcl_service_node_source)
        self.assertIn("rcl_take_request", rcl_service_node_source)
        self.assertIn("rcl_send_response", rcl_service_node_source)
        self.assertIn("--response-delay-ms", rcl_service_node_source)
        self.assertIn("response_delay_ms", rcl_service_node_source)
        self.assertIn("std_srvs/srv/SetBool", rcl_service_node_source)
        graph_source = (PKG / "src" / "rmw_graph.cpp").read_text()
        self.assertIn("purge_expired_remote_graph_locked", graph_source)
        self.assertIn("g_remote_graph_endpoints", graph_source)
        self.assertIn("g_local_graph_endpoints", graph_source)
        self.assertIn("rmw_get_publishers_info_by_topic", graph_source)
        self.assertIn("rmw_get_subscriptions_info_by_topic", graph_source)
        self.assertIn("rmw_get_publisher_names_and_types_by_node", graph_source)
        self.assertIn("rmw_get_subscriber_names_and_types_by_node", graph_source)
        self.assertIn("g_remote_service_endpoints", graph_source)
        self.assertIn("rmw_get_service_names_and_types", graph_source)
        self.assertIn("rmw_get_service_names_and_types_by_node", graph_source)
        self.assertIn("rmw_get_client_names_and_types_by_node", graph_source)
        self.assertIn("rmw_count_services", graph_source)
        self.assertIn("rmw_count_clients", graph_source)
        self.assertIn("rmw_fleetqox_cpp_graph_service_count", graph_source)
        self.assertIn("rmw_fleetqox_cpp_graph_publisher_count", graph_source)
        self.assertIn("rmw_fleetqox_cpp_graph_subscription_count", graph_source)
        self.assertIn("rmw_topic_endpoint_info_set_qos_profile", graph_source)
        pubsub_source = (PKG / "src" / "rmw_pubsub.cpp").read_text()
        self.assertIn("rmw_publisher_count_matched_subscriptions", pubsub_source)
        self.assertIn("rmw_subscription_count_matched_publishers", pubsub_source)
        self.assertIn("rmw_fleetqox_cpp_graph_publisher_count", pubsub_source)
        self.assertIn("rmw_fleetqox_cpp_graph_subscription_count", pubsub_source)
        stub_source = (PKG / "src" / "rmw_stubs.cpp").read_text()
        self.assertIn("rmw_init_publisher_allocation", stub_source)
        self.assertIn("publisher allocation is not from rmw_fleetqox_cpp", stub_source)
        self.assertIn("g_publisher_allocations_initialized", stub_source)
        self.assertIn("rmw_fleetqox_cpp_subscription_allocations_finalized", stub_source)
        self.assertIn("rmw_create_client", stub_source)
        self.assertIn("rmw_create_service", stub_source)
        self.assertIn("service_type_name_from_type_support", stub_source)
        self.assertIn("service_graph_renewal_loop", stub_source)
        self.assertIn("rmw_send_request", stub_source)
        self.assertIn("rmw_take_request", stub_source)
        self.assertIn("rmw_send_response", stub_source)
        self.assertIn("rmw_take_response", stub_source)
        self.assertIn("rmw_fleetqox_cpp_handle_service_frame", stub_source)
        self.assertIn("rmw_fleetqox_cpp_service_frames_received", stub_source)
        self.assertIn("rmw_fleetqox_cpp_service_expired_frames_dropped", stub_source)
        self.assertIn("rmw_fleetqox_cpp_service_endpoint_id", stub_source)
        self.assertIn("rmw_fleetqox_cpp_client_endpoint_id", stub_source)
        self.assertIn("drop_if_expired_service_frame", stub_source)
        self.assertIn("service_frame_expired(frame, monotonic_timestamp_ns())", stub_source)
        self.assertIn("qos_duration_ns(data->qos.lifespan)", stub_source)
        self.assertIn("frame = rmw_fleetqox_cpp::ServiceFrame{};", stub_source)
        self.assertIn("response_queue", stub_source)
        self.assertIn("request_queue", stub_source)
        self.assertIn("rmw_fleetqox_cpp_graph_register_service_endpoint", stub_source)
        self.assertIn("rmw_fleetqox_cpp_graph_register_client_endpoint", stub_source)
        self.assertIn("rmw_fleetqox_cpp_graph_service_count", stub_source)
        self.assertIn("rmw_qos_profile_check_compatible", stub_source)
        self.assertIn("subscription deadline is less than publisher deadline", stub_source)
        self.assertIn("subscription liveliness lease is less", stub_source)
        self.assertIn("RMW_QOS_COMPATIBILITY_WARNING", stub_source)
        self.assertIn("append_qos_reason", stub_source)
        self.assertIn("rmw_event_fini", stub_source)
        self.assertIn("qos_event_type_supported", stub_source)
        self.assertIn("g_qos_event_callbacks_set", stub_source)
        self.assertIn("rmw_fleetqox_cpp_subscription_set_content_filter", stub_source)
        self.assertIn("ContentFilterExpressionParser", pubsub_source)
        self.assertIn("content_filter_expression_is_valid", pubsub_source)
        self.assertIn("ContentFilterTokenKind::between", pubsub_source)
        self.assertIn("content_filter_like", pubsub_source)
        self.assertIn("ContentFilterTruth::unknown", pubsub_source)
        self.assertIn("content_filter_typed_fields", pubsub_source)
        self.assertIn("reflect_introspection_c_message", pubsub_source)
        self.assertIn("reflect_introspection_cpp_message", pubsub_source)
        self.assertIn("g_content_filter_typed_reflections", pubsub_source)
        self.assertIn("rmw_take_dynamic_message", stub_source)
        self.assertIn("rmw_serialization_support_init", stub_source)
        self.assertIn("RMW_RET_UNSUPPORTED", stub_source)
        self.assertTrue((PKG / "src" / "interprocess_pubsub_probe.cpp").exists())
        remote_graph_probe = PKG / "src" / "remote_graph_probe.cpp"
        self.assertTrue(remote_graph_probe.exists())
        remote_graph_source = remote_graph_probe.read_text()
        self.assertIn("fleetrmw.rmw_remote_graph_probe.v1", remote_graph_source)
        self.assertIn("rmw_get_topic_names_and_types", remote_graph_source)
        self.assertIn("rmw_count_publishers", remote_graph_source)
        self.assertIn("rmw_count_subscribers", remote_graph_source)
        remote_graph_lease_probe = PKG / "src" / "remote_graph_lease_probe.cpp"
        self.assertTrue(remote_graph_lease_probe.exists())
        remote_graph_lease_source = remote_graph_lease_probe.read_text()
        self.assertIn("fleetrmw.rmw_remote_graph_lease_probe.v1", remote_graph_lease_source)
        self.assertIn("publisher_count_after", remote_graph_lease_source)
        self.assertIn("identity_update_ok", remote_graph_lease_source)
        self.assertIn("moved_topic_count_after_stale_remove", remote_graph_lease_source)
        router = (PKG / "src" / "udp_router_probe.cpp").read_text()
        self.assertIn("fleetrmw.rmw_udp_router_probe.v1", router)
        self.assertIn("expected_route_advertisements", router)
        self.assertIn("expected_graph_advertisements", router)
        self.assertIn("expected_service_frames", router)
        self.assertIn("expected_ack_nack_frames", router)
        self.assertIn("expected_qos_drops", router)
        self.assertIn("ack_nack_forwarded", router)
        self.assertIn("decode_ack_nack", router)
        self.assertIn("drop_source_sequences", router)
        self.assertIn("forward_delay_ms", router)
        self.assertIn("scheduler_window_ms", router)
        self.assertIn("scheduler_expected_frames", router)
        self.assertIn("scheduler_topic_prefix", router)
        self.assertIn("scheduler_batch_ready", router)
        self.assertIn("qos_dropped_frames", router)
        self.assertIn("qos_dropped_topic_counts", router)
        self.assertIn("increment_topic_count", router)
        self.assertIn("forwarded_topics", router)
        self.assertIn("frame_exceeds_learned_lifespan", router)
        self.assertIn("absolute_deadline_ns_for_frame", router)
        self.assertIn("decode_service_frame", router)
        self.assertIn("service_forwarded", router)
        self.assertIn("graph_services", router)
        self.assertIn("graph_clients", router)
        self.assertIn("graph_peer_count", router)
        self.assertIn("graph_forwarded", router)
        self.assertIn("purge_expired_routes", router)
        self.assertIn("decode_route_advertisement", router)
        self.assertIn("decode_graph_advertisement", router)
        self.assertIn("getaddrinfo", router)
        self.assertIn("decode_data_frame(encoded_frame)", router)
        self.assertIn("sendto(", router)
        interprocess_probe = (PKG / "src" / "interprocess_pubsub_probe.cpp").read_text()
        self.assertIn("expect_taken", interprocess_probe)
        self.assertIn("lifespan_ms", interprocess_probe)
        self.assertIn("deadline_ms", interprocess_probe)
        probe = (PKG / "src" / "serialized_pubsub_probe.cpp").read_text()
        self.assertIn('socket_backed', probe)
        self.assertIn("socket_frames_sent >= 1", probe)
        self.assertIn("socket_frames_received >= 1", probe)
        qos_probe = PKG / "src" / "qos_probe.cpp"
        self.assertTrue(qos_probe.exists())
        qos_probe_source = qos_probe.read_text()
        self.assertIn("fleetrmw.rmw_qos_probe.v2", qos_probe_source)
        self.assertIn("RMW_QOS_POLICY_HISTORY_KEEP_LAST", qos_probe_source)
        self.assertIn("lifespan_qos.lifespan.nsec", qos_probe_source)
        self.assertIn("depth_received == \"second\"", qos_probe_source)
        self.assertIn("qos_compatibility_full_matrix", qos_probe_source)
        self.assertIn("RMW_QOS_COMPATIBILITY_WARNING", qos_probe_source)
        self.assertIn("deadline_order_case_ok", qos_probe_source)
        self.assertIn("lease_order_case_ok", qos_probe_source)
        reliability_probe = PKG / "src" / "reliability_probe.cpp"
        self.assertTrue(reliability_probe.exists())
        reliability_probe_source = reliability_probe.read_text()
        self.assertIn("fleetrmw.rmw_reliability_probe.v1", reliability_probe_source)
        self.assertIn("rmw_fleetqox_cpp_socket_test_dropped_frames", reliability_probe_source)
        self.assertIn("rmw_fleetqox_cpp_socket_nack_retransmissions", reliability_probe_source)
        self.assertIn("\"one\"", reliability_probe_source)
        self.assertIn("\"two\"", reliability_probe_source)
        self.assertIn("\"three\"", reliability_probe_source)
        self.assertIn("reliable_timeout_retransmissions", reliability_probe_source)
        reliable_interprocess_probe = PKG / "src" / "reliable_interprocess_probe.cpp"
        self.assertTrue(reliable_interprocess_probe.exists())
        reliable_interprocess_source = reliable_interprocess_probe.read_text()
        self.assertIn("fleetrmw.rmw_reliable_interprocess_probe.v1", reliable_interprocess_source)
        self.assertIn("rmw_fleetqox_cpp_socket_ack_nack_received", reliable_interprocess_source)
        self.assertIn("rmw_fleetqox_cpp_socket_nack_retransmissions", reliable_interprocess_source)
        self.assertIn("min_ack_nack_received", reliable_interprocess_source)
        self.assertIn("min_ack_nack_sent", reliable_interprocess_source)
        self.assertIn("min_retransmissions", reliable_interprocess_source)
        self.assertIn("deadline_ms", reliable_interprocess_source)
        self.assertIn("pre_publish_wait_ms", reliable_interprocess_source)
        self.assertIn("pre_payload_warmup_count", reliable_interprocess_source)
        self.assertIn("pre_payload_warmup_ack_count", reliable_interprocess_source)
        self.assertIn("pre_payload_warmup_ack_timeout_ms", reliable_interprocess_source)
        self.assertIn("app_repair_cycle_count", reliable_interprocess_source)
        self.assertIn("tail_repair_repeat_count", reliable_interprocess_source)
        self.assertIn("publish_interval_ms", reliable_interprocess_source)
        self.assertIn("--payload-sequence", reliable_interprocess_source)
        self.assertIn("split_payloads(config.payload_sequence)", reliable_interprocess_source)
        self.assertIn("--pre-publish-wait-ms", reliable_interprocess_source)
        self.assertIn("--pre-payload-warmup-count", reliable_interprocess_source)
        self.assertIn("--pre-payload-warmup-payload", reliable_interprocess_source)
        self.assertIn("--pre-payload-warmup-ack-count", reliable_interprocess_source)
        self.assertIn("--pre-payload-warmup-ack-timeout-ms", reliable_interprocess_source)
        self.assertIn("--app-repair-cycle-count", reliable_interprocess_source)
        self.assertIn("--app-repair-cycle-payloads", reliable_interprocess_source)
        self.assertIn("--tail-repair-repeat-count", reliable_interprocess_source)
        self.assertIn("--tail-repair-payload", reliable_interprocess_source)
        self.assertIn("--publish-interval-ms", reliable_interprocess_source)
        self.assertIn("plan_update_after_publishes", reliable_interprocess_source)
        self.assertIn("--plan-update-after-publishes", reliable_interprocess_source)
        self.assertIn("--plan-update-text", reliable_interprocess_source)
        self.assertIn("FLEETQOX_RMW_FLEET_PATH_PLAN_FILE", reliable_interprocess_source)
        self.assertIn("fleetrmw.subscriber_delivery_telemetry.v1", reliable_interprocess_source)
        self.assertIn("--subscriber-telemetry-file", reliable_interprocess_source)
        self.assertIn("--subscriber-deadline-ms", reliable_interprocess_source)
        self.assertIn("append_subscriber_telemetry", reliable_interprocess_source)
        self.assertIn("duplicate_data_frames_deduped", reliable_interprocess_source)
        self.assertIn("ack_nack_duplicate_received", reliable_interprocess_source)
        self.assertIn("idle_repair_ack_nack_sent", reliable_interprocess_source)
        self.assertIn("post_recovery_payload", reliable_interprocess_source)
        self.assertIn("post_recovery_before_hold", reliable_interprocess_source)
        self.assertIn("post_payload_wait_ms", reliable_interprocess_source)
        self.assertIn("--post-recovery-before-hold", reliable_interprocess_source)
        self.assertIn("--post-recovery-repeat-count", reliable_interprocess_source)
        self.assertIn("--post-payload-wait-ms", reliable_interprocess_source)
        self.assertIn("--require-post-recovery-payload", reliable_interprocess_source)
        self.assertIn("publish_payload_once", reliable_interprocess_source)
        self.assertIn("required_payloads", reliable_interprocess_source)
        self.assertIn("rmw_fleetqox_cpp_socket_adaptive_failovers", reliable_interprocess_source)
        self.assertIn("rmw_fleetqox_cpp_socket_adaptive_unicast_frames", reliable_interprocess_source)
        self.assertIn("rmw_fleetqox_cpp_socket_adaptive_redundant_frames", reliable_interprocess_source)
        self.assertIn("rmw_fleetqox_cpp_socket_adaptive_peer_score_sum", reliable_interprocess_source)
        self.assertIn("rmw_fleetqox_cpp_socket_adaptive_selected_peer_index", reliable_interprocess_source)
        self.assertIn("rmw_fleetqox_cpp_socket_fleet_plan_frames", reliable_interprocess_source)
        self.assertIn("rmw_fleetqox_cpp_socket_fleet_plan_redundant_frames", reliable_interprocess_source)
        self.assertIn("rmw_fleetqox_cpp_socket_fleet_plan_selected_path_count", reliable_interprocess_source)
        self.assertIn("fleet_plan_last_paths", reliable_interprocess_source)
        self.assertIn("repair_plan_frames", reliable_interprocess_source)
        self.assertIn("repair_plan_selected_path_count", reliable_interprocess_source)
        self.assertIn("repair_retransmission_budget", reliable_interprocess_source)
        self.assertIn("repair_budget_exhausted", reliable_interprocess_source)
        self.assertIn("repair_requests_coalesced", reliable_interprocess_source)
        self.assertIn(
            "repair_sequence_attempt_limit_exhausted",
            reliable_interprocess_source,
        )
        self.assertIn("repair_not_admitted", reliable_interprocess_source)
        self.assertIn("reliable_timeout_retransmissions", reliable_interprocess_source)
        self.assertIn("peer_policy", reliable_interprocess_source)
        docker_router_script = ROOT / "scripts" / "run_rmw_docker_multicontainer_router_probe.py"
        self.assertTrue(docker_router_script.exists())
        docker_router_source = docker_router_script.read_text()
        self.assertIn("fleetrmw.rmw_multicontainer_router_probe.v1", docker_router_source)
        self.assertIn("fleetrmw_udp_router_probe", docker_router_source)
        self.assertIn("fleetrmw_remote_graph_probe", docker_router_source)
        self.assertIn("expected-route-advertisements", docker_router_source)
        self.assertIn("expected-graph-advertisements", docker_router_source)
        self.assertIn("graph-peers", docker_router_source)
        self.assertIn("observer", docker_router_source)
        udp_router_probe = PKG / "src" / "udp_router_probe.cpp"
        udp_router_source = udp_router_probe.read_text()
        self.assertIn("--expected-forwarded-topic-source-sequences", udp_router_source)
        self.assertIn("--post-satisfaction-ms", udp_router_source)
        self.assertIn("topic_source_sequence_expectations_satisfied", udp_router_source)
        docker_topic_list_script = ROOT / "scripts" / "run_rmw_docker_ros2_topic_list_probe.py"
        self.assertTrue(docker_topic_list_script.exists())
        docker_topic_list_source = docker_topic_list_script.read_text()
        self.assertIn("fleetrmw.rmw_ros2_topic_list_probe.v1", docker_topic_list_source)
        self.assertIn("ros2 topic list --no-daemon", docker_topic_list_source)
        self.assertIn("fleetrmw_rcl_graph_talker", docker_topic_list_source)
        docker_pub_echo_script = ROOT / "scripts" / "run_rmw_docker_ros2_pub_echo_probe.py"
        self.assertTrue(docker_pub_echo_script.exists())
        docker_pub_echo_source = docker_pub_echo_script.read_text()
        self.assertIn("fleetrmw.rmw_ros2_pub_echo_probe.v1", docker_pub_echo_source)
        self.assertIn("ros2 topic echo --no-daemon", docker_pub_echo_source)
        self.assertIn("ros2 topic pub --times 3", docker_pub_echo_source)
        docker_topic_info_script = ROOT / "scripts" / "run_rmw_docker_ros2_topic_info_probe.py"
        self.assertTrue(docker_topic_info_script.exists())
        docker_topic_info_source = docker_topic_info_script.read_text()
        self.assertIn("fleetrmw.rmw_ros2_topic_info_probe.v1", docker_topic_info_source)
        self.assertIn("ros2 topic info --no-daemon", docker_topic_info_source)
        self.assertIn("--verbose", docker_topic_info_source)
        self.assertIn("Endpoint type: PUBLISHER", docker_topic_info_source)
        docker_cli_matrix_script = ROOT / "scripts" / "run_rmw_docker_ros2_cli_message_matrix.py"
        self.assertTrue(docker_cli_matrix_script.exists())
        docker_cli_matrix_source = docker_cli_matrix_script.read_text()
        self.assertIn("fleetrmw.rmw_ros2_cli_message_matrix.v1", docker_cli_matrix_source)
        self.assertIn("builtin_interfaces/msg/Time", docker_cli_matrix_source)
        self.assertIn("builtin_interfaces/msg/Duration", docker_cli_matrix_source)
        self.assertIn("geometry_msgs/msg/PoseStamped", docker_cli_matrix_source)
        self.assertIn("sensor_msgs/msg/LaserScan", docker_cli_matrix_source)
        self.assertIn("nav_msgs/msg/Odometry", docker_cli_matrix_source)
        self.assertIn("nav_msgs/msg/Path", docker_cli_matrix_source)
        self.assertIn("ros2\", \"topic\", \"echo", docker_cli_matrix_source)
        docker_node_info_script = ROOT / "scripts" / "run_rmw_docker_ros2_node_info_probe.py"
        self.assertTrue(docker_node_info_script.exists())
        docker_node_info_source = docker_node_info_script.read_text()
        self.assertIn("fleetrmw.rmw_ros2_node_info_probe.v1", docker_node_info_source)
        self.assertIn("ros2 node list --no-daemon", docker_node_info_source)
        self.assertIn("ros2 node info --no-daemon", docker_node_info_source)
        rcl_service_node_source = (PKG / "src" / "rcl_service_node.cpp").read_text()
        self.assertIn("rmw_fleetqox_cpp_send_malformed_response", rcl_service_node_source)
        self.assertIn("--malformed-response", rcl_service_node_source)
        self.assertIn("--exit-after-request", rcl_service_node_source)
        docker_service_graph_script = ROOT / "scripts" / "run_rmw_docker_ros2_service_graph_probe.py"
        self.assertTrue(docker_service_graph_script.exists())
        docker_service_graph_source = docker_service_graph_script.read_text()
        self.assertIn("fleetrmw.rmw_ros2_service_graph_probe.v1", docker_service_graph_source)
        self.assertIn("ros2 service list --no-daemon", docker_service_graph_source)
        self.assertIn("ros2 node info --no-daemon", docker_service_graph_source)
        self.assertIn("fleetrmw_rcl_service_node", docker_service_graph_source)
        docker_service_call_script = ROOT / "scripts" / "run_rmw_docker_ros2_service_call_probe.py"
        self.assertTrue(docker_service_call_script.exists())
        docker_service_call_source = docker_service_call_script.read_text()
        self.assertIn("fleetrmw.rmw_ros2_service_call_probe.v1", docker_service_call_source)
        self.assertIn("ros2 service call", docker_service_call_source)
        self.assertIn("fleetqox set_bool accepted", docker_service_call_source)
        docker_service_timeout_script = ROOT / "scripts" / "run_rmw_docker_ros2_service_timeout_probe.py"
        self.assertTrue(docker_service_timeout_script.exists())
        docker_service_timeout_source = docker_service_timeout_script.read_text()
        self.assertIn("fleetrmw.rmw_ros2_service_timeout_probe.v1", docker_service_timeout_source)
        self.assertIn("--response-delay-ms", docker_service_timeout_source)
        self.assertIn("service_call_returncode", docker_service_timeout_source)
        self.assertIn("timed_out", docker_service_timeout_source)
        self.assertIn("server_saw_request", docker_service_timeout_source)
        malformed_service_script = (
            ROOT / "scripts" / "run_rmw_docker_router_ros2_malformed_service_response_probe.py"
        )
        self.assertTrue(malformed_service_script.exists())
        malformed_service_source = malformed_service_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_ros2_malformed_service_response_probe.v1",
            malformed_service_source,
        )
        self.assertIn("--malformed-response", malformed_service_source)
        self.assertIn("diagnostic_observed", malformed_service_source)
        self.assertIn("client_failed_cleanly", malformed_service_source)
        docker_router_service_call_script = ROOT / "scripts" / "run_rmw_docker_router_service_call_probe.py"
        self.assertTrue(docker_router_service_call_script.exists())
        docker_router_service_call_source = docker_router_service_call_script.read_text()
        self.assertIn("fleetrmw.rmw_router_service_call_probe.v1", docker_router_service_call_source)
        self.assertIn("expected-service-frames", docker_router_service_call_source)
        self.assertIn("ros2 service call", docker_router_service_call_source)
        docker_qos_script = ROOT / "scripts" / "run_rmw_docker_qos_probe.py"
        self.assertTrue(docker_qos_script.exists())
        docker_qos_source = docker_qos_script.read_text()
        self.assertIn("fleetrmw.rmw_docker_qos_probe.v2", docker_qos_source)
        self.assertIn("fleetrmw_qos_probe", docker_qos_source)
        self.assertIn("qos_compatibility_full_matrix", docker_qos_source)
        self.assertIn("depth_received", docker_qos_source)
        self.assertIn("lifespan_taken", docker_qos_source)
        service_qos_probe = PKG / "src" / "service_qos_probe.cpp"
        self.assertTrue(service_qos_probe.exists())
        service_qos_source = service_qos_probe.read_text()
        self.assertIn("fleetrmw.rmw_service_qos_probe.v1", service_qos_source)
        self.assertIn("rmw_send_request", service_qos_source)
        self.assertIn("rmw_take_request", service_qos_source)
        self.assertIn("rmw_send_response", service_qos_source)
        self.assertIn("rmw_take_response", service_qos_source)
        self.assertIn("rmw_fleetqox_cpp_service_expired_frames_dropped", service_qos_source)
        self.assertIn("rmw_fleetqox_cpp_service_frames_received", service_qos_source)
        self.assertIn("stale_request_taken", service_qos_source)
        self.assertIn("stale_response_taken", service_qos_source)
        self.assertIn("unknown_response_error", service_qos_source)
        self.assertIn("unknown_response_sent_delta", service_qos_source)
        self.assertIn("rmw_get_gid_for_client", service_qos_source)
        self.assertIn("rmw_compare_gids_equal", service_qos_source)
        self.assertIn("client_gid_stable", service_qos_source)
        self.assertIn("client_gids_distinct", service_qos_source)
        self.assertIn("request_writer_gid_matches_client", service_qos_source)
        self.assertIn("request_sequence_matches", service_qos_source)
        self.assertIn("rmw_service_server_is_available", service_qos_source)
        self.assertIn("service_availability_matching_ok", service_qos_source)
        self.assertIn("service_availability_type_filter_ok", service_qos_source)
        self.assertIn("service_availability_qos_filter_ok", service_qos_source)
        self.assertIn("service_request_type_filter_ok", service_qos_source)
        self.assertIn("service_request_qos_filter_ok", service_qos_source)
        self.assertIn("service_availability_owner_node_enforced", service_qos_source)
        self.assertIn("client_destroy_owner_node_enforced", service_qos_source)
        self.assertIn("service_destroy_owner_node_enforced", service_qos_source)
        docker_service_qos_script = ROOT / "scripts" / "run_rmw_docker_service_qos_probe.py"
        self.assertTrue(docker_service_qos_script.exists())
        docker_service_qos_source = docker_service_qos_script.read_text()
        self.assertIn("fleetrmw.rmw_docker_service_qos_probe.v1", docker_service_qos_source)
        self.assertIn("fleetrmw_service_qos_probe", docker_service_qos_source)
        self.assertIn("expired_frames_dropped_delta", docker_service_qos_source)
        self.assertIn("unknown_response_error", docker_service_qos_source)
        self.assertIn("request_writer_gid_matches_client", docker_service_qos_source)
        self.assertIn("service_availability_qos_filter_ok", docker_service_qos_source)
        self.assertIn("service_availability_owner_node_enforced", docker_service_qos_source)
        domain_probe = PKG / "src" / "domain_isolation_probe.cpp"
        self.assertTrue(domain_probe.exists())
        domain_probe_source = domain_probe.read_text()
        self.assertIn("fleetrmw.rmw_domain_isolation_probe.v1", domain_probe_source)
        self.assertIn("graph_guard_cross_domain_suppressed", domain_probe_source)
        self.assertIn("data_plane_isolated", domain_probe_source)
        self.assertIn("pubsub_type_data_plane_isolated", domain_probe_source)
        self.assertIn("service_data_plane_isolated", domain_probe_source)
        self.assertIn("remote_graph_isolated", domain_probe_source)
        docker_domain_probe = ROOT / "scripts" / "run_rmw_docker_domain_isolation_probe.py"
        self.assertTrue(docker_domain_probe.exists())
        docker_domain_source = docker_domain_probe.read_text()
        self.assertIn("fleetrmw.rmw_docker_domain_isolation_probe.v1", docker_domain_source)
        self.assertIn("fleetrmw_domain_isolation_probe", docker_domain_source)
        self.assertIn("cross_domain_sample_taken", docker_domain_source)
        graph_source = (PKG / "src" / "rmw_graph.cpp").read_text()
        self.assertIn("rmw_fleetqox_cpp_graph_matching_service_count", graph_source)
        self.assertIn("service_qos_matches_client", graph_source)
        self.assertIn("GraphNameKey{domain_id", graph_source)
        self.assertIn("trigger_graph_guard_conditions_for_domain", graph_source)
        remote_graph_probe_source = (
            PKG / "src" / "remote_graph_lease_probe.cpp"
        ).read_text()
        self.assertIn("remote_service_matching_ok", remote_graph_probe_source)
        self.assertIn("remote_incompatible_qos_count", remote_graph_probe_source)
        self.assertIn("remote_client_matching_ok", remote_graph_probe_source)
        manifest = json.loads((PKG / "capabilities.json").read_text())
        self.assertTrue(
            manifest["supported"]["service_server_availability_type_qos_matching"]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "remote_service_graph_qos_renewal_matching_claim"
            ]
        )
        service_error_probe = PKG / "src" / "service_error_probe.cpp"
        self.assertTrue(service_error_probe.exists())
        service_error_source = service_error_probe.read_text()
        self.assertIn("fleetrmw.rmw_service_error_probe.v1", service_error_source)
        self.assertIn("rmw_fleetqox_cpp_handle_service_frame", service_error_source)
        self.assertIn("rmw_fleetqox_cpp_client_endpoint_id", service_error_source)
        self.assertIn("empty_response_taken", service_error_source)
        self.assertIn("malformed_response_error", service_error_source)
        self.assertIn("invalid_frame_rejected", service_error_source)
        docker_service_error_script = ROOT / "scripts" / "run_rmw_docker_service_error_probe.py"
        self.assertTrue(docker_service_error_script.exists())
        docker_service_error_source = docker_service_error_script.read_text()
        self.assertIn("fleetrmw.rmw_docker_service_error_probe.v1", docker_service_error_source)
        self.assertIn("fleetrmw_service_error_probe", docker_service_error_source)
        service_resource_probe = PKG / "src" / "service_resource_limit_probe.cpp"
        self.assertTrue(service_resource_probe.exists())
        service_resource_source = service_resource_probe.read_text()
        self.assertIn(
            "fleetrmw.rmw_service_resource_limit_probe.v1",
            service_resource_source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_service_request_queue_resource_drops",
            service_resource_source,
        )
        self.assertIn("resource_repair_exact_delivery", service_resource_source)
        docker_service_resource_script = (
            ROOT / "scripts" / "run_rmw_docker_service_resource_limit_probe.py"
        )
        self.assertTrue(docker_service_resource_script.exists())
        docker_service_resource_source = docker_service_resource_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_docker_service_resource_limit_probe.v1",
            docker_service_resource_source,
        )
        self.assertIn(
            "service_resource_backpressure_repair_claim",
            docker_service_resource_source,
        )
        self.assertIn("tc qdisc replace dev lo root netem", docker_service_resource_source)
        service_isolation_probe = (
            PKG / "src" / "service_client_isolation_probe.cpp"
        )
        self.assertTrue(service_isolation_probe.exists())
        service_isolation_source = service_isolation_probe.read_text()
        self.assertIn(
            "fleetrmw.rmw_service_client_isolation_probe.v1",
            service_isolation_source,
        )
        self.assertIn("quiet_admitted_first_wave", service_isolation_source)
        self.assertIn(
            "rmw_fleetqox_cpp_service_request_per_client_resource_drops",
            service_isolation_source,
        )
        docker_service_isolation_script = (
            ROOT / "scripts" / "run_rmw_docker_service_client_isolation_probe.py"
        )
        self.assertTrue(docker_service_isolation_script.exists())
        docker_service_isolation_source = docker_service_isolation_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_docker_service_client_isolation_probe.v1",
            docker_service_isolation_source,
        )
        self.assertIn(
            "service_noisy_neighbor_bounded_fairness_claim",
            docker_service_isolation_source,
        )
        self.assertIn(
            "service_inter_client_round_robin_claim",
            docker_service_isolation_source,
        )
        self.assertIn("first_wave_round_robin", service_isolation_source)
        cmake_source = (PKG / "CMakeLists.txt").read_text()
        self.assertIn("fleetrmw_service_resource_limit_probe", cmake_source)
        self.assertIn("fleetrmw_service_client_isolation_probe", cmake_source)
        service_repair_admission_probe = (
            PKG / "src" / "service_repair_admission_probe.cpp"
        )
        self.assertTrue(service_repair_admission_probe.exists())
        service_repair_admission_source = service_repair_admission_probe.read_text()
        self.assertIn(
            "fleetrmw.rmw_service_repair_admission_probe.v1",
            service_repair_admission_source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_service_request_repair_pending_max_observed",
            service_repair_admission_source,
        )
        docker_repair_admission_script = (
            ROOT / "scripts" / "run_rmw_docker_service_repair_admission_probe.py"
        )
        self.assertTrue(docker_repair_admission_script.exists())
        docker_repair_admission_source = docker_repair_admission_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_docker_service_repair_admission_probe.v1",
            docker_repair_admission_source,
        )
        self.assertIn(
            "bounded_service_repair_pending_claim",
            docker_repair_admission_source,
        )
        self.assertIn("fleetrmw_service_repair_admission_probe", cmake_source)
        service_priority_probe = PKG / "src" / "service_priority_probe.cpp"
        self.assertTrue(service_priority_probe.exists())
        service_priority_source = service_priority_probe.read_text()
        self.assertIn(
            "fleetrmw.rmw_service_priority_probe.v1",
            service_priority_source,
        )
        self.assertIn("strict_priority_claim", service_priority_source)
        self.assertIn("aging_starvation_bound_claim", service_priority_source)
        docker_service_priority_script = (
            ROOT / "scripts" / "run_rmw_docker_service_priority_probe.py"
        )
        self.assertTrue(docker_service_priority_script.exists())
        docker_service_priority_source = docker_service_priority_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_docker_service_priority_probe.v1",
            docker_service_priority_source,
        )
        self.assertIn(
            "service_priority_aging_starvation_bound_claim",
            docker_service_priority_source,
        )
        self.assertIn("fleetrmw_service_priority_probe", cmake_source)
        weighted_service_probe = (
            PKG / "src" / "service_weighted_fairness_probe.cpp"
        )
        self.assertTrue(weighted_service_probe.exists())
        weighted_service_source = weighted_service_probe.read_text()
        self.assertIn(
            "fleetrmw.rmw_service_weighted_fairness_probe.v1",
            weighted_service_source,
        )
        self.assertIn("weighted_service_ratio_claim", weighted_service_source)
        self.assertIn("rmw_send_request", weighted_service_source)
        docker_weighted_service_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_service_weighted_fairness_probe.py"
        )
        self.assertTrue(docker_weighted_service_script.exists())
        docker_weighted_service_source = (
            docker_weighted_service_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_docker_service_weighted_fairness_probe.v1",
            docker_weighted_service_source,
        )
        self.assertIn(
            "weighted_service_starvation_bound_claim",
            docker_weighted_service_source,
        )
        self.assertIn(
            '"request_path": "rmw_send_request"',
            docker_weighted_service_source,
        )
        self.assertIn(
            "fleetrmw_service_weighted_fairness_probe",
            cmake_source,
        )
        deadline_service_probe = (
            PKG / "src" / "service_deadline_scheduler_probe.cpp"
        )
        self.assertTrue(deadline_service_probe.exists())
        deadline_service_source = deadline_service_probe.read_text()
        self.assertIn(
            "fleetrmw.rmw_service_deadline_scheduler_probe.v1",
            deadline_service_source,
        )
        self.assertIn("earliest_deadline_first_claim", deadline_service_source)
        self.assertIn("rmw_send_request", deadline_service_source)
        docker_deadline_service_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_service_deadline_scheduler_probe.py"
        )
        self.assertTrue(docker_deadline_service_script.exists())
        docker_deadline_service_source = (
            docker_deadline_service_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_docker_service_deadline_scheduler_probe.v1",
            docker_deadline_service_source,
        )
        self.assertIn(
            "deadline_aware_service_scheduling_claim",
            docker_deadline_service_source,
        )
        self.assertIn(
            "fleetrmw_service_deadline_scheduler_probe",
            cmake_source,
        )
        durable_service_probe = (
            PKG / "src" / "service_durable_replay_probe.cpp"
        )
        self.assertTrue(durable_service_probe.exists())
        durable_service_source = durable_service_probe.read_text()
        self.assertIn(
            "fleetrmw.rmw_service_durable_replay_probe.v1",
            durable_service_source,
        )
        self.assertIn("server-crash", durable_service_source)
        docker_durable_service_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_service_durable_replay_probe.py"
        )
        self.assertTrue(docker_durable_service_script.exists())
        docker_durable_service_source = (
            docker_durable_service_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_docker_service_durable_replay_probe.v1",
            docker_durable_service_source,
        )
        self.assertIn(
            "crash_persistent_completed_service_deduplication_claim",
            docker_durable_service_source,
        )
        self.assertIn(
            "fleetrmw_service_durable_replay_probe",
            cmake_source,
        )
        self.assertIn(
            "FLEETQOX_RMW_SERVICE_DURABLE_REPLAY_DIR",
            stub_source,
        )
        self.assertIn("persist_durable_service_replay", stub_source)
        self.assertIn("g_service_durable_replay_mutex", stub_source)
        self.assertIn("malformed_response_error", docker_service_error_source)
        self.assertIn("after_invalid_response_taken", docker_service_error_source)
        action_probe = PKG / "src" / "action_frame_probe.cpp"
        self.assertTrue(action_probe.exists())
        action_probe_source = action_probe.read_text()
        self.assertIn("fleetrmw.rmw_action_frame_probe.v1", action_probe_source)
        self.assertIn("encode_action_frame", action_probe_source)
        self.assertIn("decode_action_frame", action_probe_source)
        self.assertIn("goal\", \"feedback\", \"status\", \"result\", \"cancel", action_probe_source)
        self.assertIn("action_frame_expired", action_probe_source)
        self.assertIn("rejects_service_schema", action_probe_source)
        docker_action_script = ROOT / "scripts" / "run_rmw_docker_action_frame_probe.py"
        self.assertTrue(docker_action_script.exists())
        docker_action_source = docker_action_script.read_text()
        self.assertIn("fleetrmw.rmw_docker_action_frame_probe.v1", docker_action_source)
        self.assertIn("fleetrmw_action_frame_probe", docker_action_source)
        self.assertIn("expected_roles", docker_action_source)
        self.assertIn("rejects_service_schema", docker_action_source)
        action_router_probe = PKG / "src" / "action_router_probe.cpp"
        self.assertTrue(action_router_probe.exists())
        action_router_probe_source = action_router_probe.read_text()
        self.assertIn("fleetrmw.rmw_action_router_probe.v1", action_router_probe_source)
        self.assertIn("action_server", action_router_probe_source)
        self.assertIn("action_client", action_router_probe_source)
        self.assertIn("server_received_roles", action_router_probe_source)
        self.assertIn("client_received_roles", action_router_probe_source)
        docker_action_router_script = ROOT / "scripts" / "run_rmw_docker_router_action_frame_probe.py"
        self.assertTrue(docker_action_router_script.exists())
        docker_action_router_source = docker_action_router_script.read_text()
        self.assertIn("fleetrmw.rmw_docker_router_action_frame_probe.v1", docker_action_router_source)
        self.assertIn("fleetrmw_action_router_probe", docker_action_router_source)
        self.assertIn("expected-action-frames", docker_action_router_source)
        self.assertIn("action_forwarded", docker_action_router_source)
        udp_router_source = (PKG / "src" / "udp_router_probe.cpp").read_text()
        self.assertIn("expected_action_frames", udp_router_source)
        self.assertIn("decode_action_frame", udp_router_source)
        self.assertIn("ActionRoute", udp_router_source)
        self.assertIn("graph_action_servers", udp_router_source)
        self.assertIn("graph_action_clients", udp_router_source)
        self.assertIn("action_forwarded", udp_router_source)
        cmake_source = (PKG / "CMakeLists.txt").read_text()
        self.assertIn("fleetrmw_action_router_probe", cmake_source)
        docker_rclpy_action_script = ROOT / "scripts" / "run_rmw_docker_rclpy_action_probe.py"
        self.assertTrue(docker_rclpy_action_script.exists())
        docker_rclpy_action_source = docker_rclpy_action_script.read_text()
        self.assertIn("fleetrmw.rmw_docker_rclpy_action_probe.v1", docker_rclpy_action_source)
        self.assertIn("ActionServer", docker_rclpy_action_source)
        self.assertIn("ActionClient", docker_rclpy_action_source)
        self.assertIn("LookupTransform", docker_rclpy_action_source)
        self.assertIn("spin_until", docker_rclpy_action_source)
        self.assertIn("result_status", docker_rclpy_action_source)
        self.assertIn("result_child_frame", docker_rclpy_action_source)
        docker_router_rclpy_action_script = ROOT / "scripts" / "run_rmw_docker_router_rclpy_action_probe.py"
        self.assertTrue(docker_router_rclpy_action_script.exists())
        docker_router_rclpy_action_source = docker_router_rclpy_action_script.read_text()
        self.assertIn("fleetrmw.rmw_docker_router_rclpy_action_probe.v1", docker_router_rclpy_action_source)
        self.assertIn("ActionServer", docker_router_rclpy_action_source)
        self.assertIn("ActionClient", docker_router_rclpy_action_source)
        self.assertIn("expected-service-frames 10", docker_router_rclpy_action_source)
        self.assertIn("service_forwarded", docker_router_rclpy_action_source)
        self.assertIn("graph_services", docker_router_rclpy_action_source)
        self.assertIn("graph_clients", docker_router_rclpy_action_source)
        self.assertIn("available_before_send", docker_router_rclpy_action_source)
        self.assertIn("available_after_result", docker_router_rclpy_action_source)
        self.assertIn("status_subscribers", docker_router_rclpy_action_source)
        self.assertIn("feedback_subscribers", docker_router_rclpy_action_source)
        self.assertIn("feedback_callbacks", docker_router_rclpy_action_source)
        self.assertIn("cancel_goal_async", docker_router_rclpy_action_source)
        self.assertIn("cancel_result_status", docker_router_rclpy_action_source)
        self.assertIn("GoalStatusArray", docker_router_rclpy_action_source)
        self.assertIn("status_observed", docker_router_rclpy_action_source)
        self.assertIn("feedback_pub_qos_profile", docker_router_rclpy_action_source)
        self.assertIn("status_pub_qos_profile", docker_router_rclpy_action_source)
        self.assertIn("feedback_lifespan_ms", docker_router_rclpy_action_source)
        self.assertIn("feedback_deadline_ms", docker_router_rclpy_action_source)
        self.assertIn("scheduler_window_ms", docker_router_rclpy_action_source)
        self.assertIn("scheduler-expected-frames", docker_router_rclpy_action_source)
        self.assertIn("scheduler-topic-prefix", docker_router_rclpy_action_source)
        self.assertIn("expected_data_frames", docker_router_rclpy_action_source)
        docker_router_rclpy_action_qos_script = (
            ROOT / "scripts" / "run_rmw_docker_router_rclpy_action_qos_probe.py"
        )
        self.assertTrue(docker_router_rclpy_action_qos_script.exists())
        docker_router_rclpy_action_qos_source = docker_router_rclpy_action_qos_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_docker_router_rclpy_action_qos_probe.v1",
            docker_router_rclpy_action_qos_source,
        )
        self.assertIn("expired_observation", docker_router_rclpy_action_qos_source)
        self.assertIn("deadline_priority", docker_router_rclpy_action_qos_source)
        self.assertIn("qos_dropped_topic_counts", docker_router_rclpy_action_qos_source)
        self.assertIn("drop_topics_verified", docker_router_rclpy_action_qos_source)
        self.assertIn("deadline_order_verified", docker_router_rclpy_action_qos_source)
        docker_reliability_script = ROOT / "scripts" / "run_rmw_docker_reliability_probe.py"
        self.assertTrue(docker_reliability_script.exists())
        docker_reliability_source = docker_reliability_script.read_text()
        self.assertIn("fleetrmw.rmw_docker_reliability_probe.v1", docker_reliability_source)
        self.assertIn("FLEETQOX_RMW_DROP_SOURCE_SEQUENCES=2", docker_reliability_source)
        self.assertIn("FLEETQOX_RMW_DROP_SOURCE_SEQUENCES=1", docker_reliability_source)
        self.assertIn("FLEETQOX_RMW_RELIABLE_ACK_TIMEOUT_MS=100", docker_reliability_source)
        self.assertIn("initial_sequence_probe", docker_reliability_source)
        self.assertIn("fleetrmw_reliability_probe", docker_reliability_source)
        self.assertIn("nack_retransmissions", docker_reliability_source)
        docker_router_reliability_script = ROOT / "scripts" / "run_rmw_docker_router_reliability_probe.py"
        self.assertTrue(docker_router_reliability_script.exists())
        docker_router_reliability_source = docker_router_reliability_script.read_text()
        self.assertIn("fleetrmw.rmw_router_reliability_probe.v1", docker_router_reliability_source)
        self.assertIn("expected-ack-nack-frames", docker_router_reliability_source)
        self.assertIn("drop-source-sequences", docker_router_reliability_source)
        self.assertIn("ack_nack_forwarded", docker_router_reliability_source)
        docker_router_scheduled_reliability_script = (
            ROOT / "scripts" / "run_rmw_docker_router_scheduled_reliability_probe.py"
        )
        self.assertTrue(docker_router_scheduled_reliability_script.exists())
        docker_router_scheduled_reliability_source = (
            docker_router_scheduled_reliability_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_scheduled_reliability_probe.v1",
            docker_router_scheduled_reliability_source,
        )
        self.assertIn("--scheduler-window-ms 150", docker_router_scheduled_reliability_source)
        self.assertIn("--scheduler-expected-frames 2", docker_router_scheduled_reliability_source)
        self.assertIn("--drop-source-sequences 2", docker_router_scheduled_reliability_source)
        self.assertIn("scheduler_forwarded_frames", docker_router_scheduled_reliability_source)
        self.assertIn("nack_retransmissions", docker_router_scheduled_reliability_source)
        self.assertIn("NETEM_PROFILES", docker_router_scheduled_reliability_source)
        self.assertIn("netem_loss_percent", docker_router_scheduled_reliability_source)
        self.assertIn("netem_qdisc", docker_router_scheduled_reliability_source)
        self.assertIn('"--cap-add", "NET_ADMIN"', docker_router_scheduled_reliability_source)
        self.assertIn("post_satisfaction_ms", docker_router_scheduled_reliability_source)
        docker_router_scheduled_repeated_loss_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_scheduled_reliability_repeated_loss_matrix.py"
        )
        self.assertTrue(docker_router_scheduled_repeated_loss_script.exists())
        docker_router_scheduled_repeated_loss_source = (
            docker_router_scheduled_repeated_loss_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_scheduled_reliability_repeated_loss_matrix.v1",
            docker_router_scheduled_repeated_loss_source,
        )
        self.assertIn("SEED_SEMANTICS", docker_router_scheduled_repeated_loss_source)
        self.assertIn("loss_percents", docker_router_scheduled_repeated_loss_source)
        self.assertIn('"partial"', docker_router_scheduled_repeated_loss_source)
        self.assertIn("run_probe(", docker_router_scheduled_repeated_loss_source)
        self.assertIn(
            "netem_loss_percent=loss_percent",
            docker_router_scheduled_repeated_loss_source,
        )
        docker_router_multi_robot_scheduled_reliability_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_multi_robot_scheduled_reliability_probe.py"
        )
        self.assertTrue(
            docker_router_multi_robot_scheduled_reliability_script.exists()
        )
        docker_router_multi_robot_scheduled_reliability_source = (
            docker_router_multi_robot_scheduled_reliability_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_scheduled_reliability_probe.v1",
            docker_router_multi_robot_scheduled_reliability_source,
        )
        self.assertIn(
            "FLEETQOX_RMW_ROBOT_ID",
            docker_router_multi_robot_scheduled_reliability_source,
        )
        self.assertIn(
            "--drop-source-sequences 2",
            docker_router_multi_robot_scheduled_reliability_source,
        )
        self.assertIn(
            "scheduler_per_robot",
            docker_router_multi_robot_scheduled_reliability_source,
        )
        self.assertIn(
            "total_nack_retransmissions",
            docker_router_multi_robot_scheduled_reliability_source,
        )
        docker_router_mixed_workload_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_mixed_action_control_state_probe.py"
        )
        self.assertTrue(docker_router_mixed_workload_script.exists())
        docker_router_mixed_workload_source = (
            docker_router_mixed_workload_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_mixed_action_control_state_probe.v1",
            docker_router_mixed_workload_source,
        )
        self.assertIn("mixed_robot_count", docker_router_mixed_workload_source)
        self.assertIn("scheduler_urgent_frames", docker_router_mixed_workload_source)
        self.assertIn("/fleetqox/mixed/", docker_router_mixed_workload_source)
        docker_router_proactive_diversity_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_proactive_deadline_diversity_probe.py"
        )
        self.assertTrue(docker_router_proactive_diversity_script.exists())
        docker_router_proactive_diversity_source = (
            docker_router_proactive_diversity_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_proactive_deadline_diversity_probe.v1",
            docker_router_proactive_diversity_source,
        )
        self.assertIn("FLEETQOX_RMW_PEER_POLICY=adaptive_qos", docker_router_proactive_diversity_source)
        self.assertIn("on_time_sequences", docker_router_proactive_diversity_source)
        self.assertIn("subscriber-deadline-ms", docker_router_proactive_diversity_source)
        docker_router_proactive_repeated_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_proactive_deadline_diversity_repeated_loss_matrix.py"
        )
        self.assertTrue(docker_router_proactive_repeated_script.exists())
        docker_router_proactive_repeated_source = (
            docker_router_proactive_repeated_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_proactive_deadline_diversity_repeated_loss_matrix.v1",
            docker_router_proactive_repeated_source,
        )
        self.assertIn("SEED_SEMANTICS", docker_router_proactive_repeated_source)
        self.assertIn("max_observed_latency_ms", docker_router_proactive_repeated_source)
        docker_router_multi_proactive_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_multi_robot_proactive_deadline_diversity_probe.py"
        )
        self.assertTrue(docker_router_multi_proactive_script.exists())
        docker_router_multi_proactive_source = (
            docker_router_multi_proactive_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_proactive_deadline_diversity_probe.v1",
            docker_router_multi_proactive_source,
        )
        self.assertIn("deadline_success_jain_index", docker_router_multi_proactive_source)
        self.assertIn("proactive_path_transmissions", docker_router_multi_proactive_source)
        self.assertIn("FLEETQOX_RMW_PEER_POLICY=adaptive_qos", docker_router_multi_proactive_source)
        docker_router_budgeted_fleet_plan_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_multi_robot_budgeted_fleet_plan_probe.py"
        )
        self.assertTrue(docker_router_budgeted_fleet_plan_script.exists())
        docker_router_budgeted_fleet_plan_source = (
            docker_router_budgeted_fleet_plan_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_budgeted_fleet_plan_probe.v1",
            docker_router_budgeted_fleet_plan_source,
        )
        self.assertIn("redundancy_budget_bytes_per_tick", docker_router_budgeted_fleet_plan_source)
        self.assertIn("failure_domain=\"private_5g_core\"", docker_router_budgeted_fleet_plan_source)
        self.assertIn("FLEETQOX_RMW_PEER_POLICY=fleet_plan", docker_router_budgeted_fleet_plan_source)
        self.assertIn("path_transmission_reduction_ratio", docker_router_budgeted_fleet_plan_source)
        self.assertIn("sequential_confidence_fallback", docker_router_budgeted_fleet_plan_source)
        self.assertIn("sequential_separation_margin", docker_router_budgeted_fleet_plan_source)
        self.assertIn("confidence_fallback_actuations", docker_router_budgeted_fleet_plan_source)
        self.assertIn("feedback_safe_mode_count", docker_router_budgeted_fleet_plan_source)
        self.assertIn("fallback_recovery_samples", docker_router_budgeted_fleet_plan_source)
        self.assertIn("fallback_recovery", docker_router_budgeted_fleet_plan_source)
        self.assertIn("subscriber_timeout_ms", docker_router_budgeted_fleet_plan_source)
        self.assertIn("publisher_trigger_timeout_ms", docker_router_budgeted_fleet_plan_source)
        self.assertIn("graph_renew_interval_ms", docker_router_budgeted_fleet_plan_source)
        docker_router_budgeted_epoch_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_multi_robot_budgeted_fleet_plan_epoch_probe.py"
        )
        self.assertTrue(docker_router_budgeted_epoch_script.exists())
        docker_router_budgeted_epoch_source = docker_router_budgeted_epoch_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_budgeted_fleet_plan_epoch_probe.v1",
            docker_router_budgeted_epoch_source,
        )
        self.assertIn("epoch_transition=True", docker_router_budgeted_epoch_source)
        self.assertIn("actual_path_transmissions", docker_router_budgeted_epoch_source)
        docker_router_qoe_feedback_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_multi_robot_qoe_feedback_budget_probe.py"
        )
        self.assertTrue(docker_router_qoe_feedback_script.exists())
        docker_router_qoe_feedback_source = docker_router_qoe_feedback_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_qoe_feedback_budget_probe.v1",
            docker_router_qoe_feedback_source,
        )
        self.assertIn("qoe_feedback=True", docker_router_qoe_feedback_source)
        self.assertIn("protected_robots", docker_router_qoe_feedback_source)
        docker_router_qoe_feedback_matrix_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_multi_robot_qoe_feedback_budget_repeated_matrix.py"
        )
        self.assertTrue(docker_router_qoe_feedback_matrix_script.exists())
        docker_router_qoe_feedback_matrix_source = (
            docker_router_qoe_feedback_matrix_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_qoe_feedback_budget_repeated_matrix.v1",
            docker_router_qoe_feedback_matrix_source,
        )
        self.assertIn("SEED_SEMANTICS", docker_router_qoe_feedback_matrix_source)
        self.assertIn("total_actual_path_transmissions", docker_router_qoe_feedback_matrix_source)
        docker_router_qoe_migration_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_multi_robot_qoe_protection_migration_probe.py"
        )
        self.assertTrue(docker_router_qoe_migration_script.exists())
        docker_router_qoe_migration_source = docker_router_qoe_migration_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_qoe_protection_migration_probe.v1",
            docker_router_qoe_migration_source,
        )
        self.assertIn("qoe_migration=True", docker_router_qoe_migration_source)
        self.assertIn("epoch_path_plans", docker_router_qoe_migration_source)
        self.assertIn("max_epoch_convergence_ms", docker_router_budgeted_fleet_plan_source)
        self.assertIn("protected_set_churn", docker_router_budgeted_fleet_plan_source)
        self.assertIn("--publish-trigger-file", reliable_interprocess_source)
        self.assertIn("wait_for_publish_trigger", reliable_interprocess_source)
        self.assertIn("--publisher-ready-file", reliable_interprocess_source)
        self.assertIn("mark_publisher_ready", reliable_interprocess_source)
        docker_router_qoe_migration_scale_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_qoe_protection_migration_scale_matrix.py"
        )
        self.assertTrue(docker_router_qoe_migration_scale_script.exists())
        docker_router_qoe_migration_scale_source = (
            docker_router_qoe_migration_scale_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_qoe_protection_migration_scale_matrix.v1",
            docker_router_qoe_migration_scale_source,
        )
        self.assertIn("--robot-counts", docker_router_qoe_migration_scale_source)
        self.assertIn("aggregate_path_transmission_reduction_ratio", docker_router_qoe_migration_scale_source)
        self.assertIn("total_protection_migrations", docker_router_qoe_migration_scale_source)
        self.assertIn("event_triggered_feedback=True", docker_router_qoe_migration_scale_source)
        self.assertIn("sequential_qoe_feedback=True", docker_router_qoe_migration_scale_source)
        docker_router_qoe_migration_repeated_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_qoe_protection_migration_sequential_repeated_matrix.py"
        )
        self.assertTrue(docker_router_qoe_migration_repeated_script.exists())
        docker_router_qoe_migration_repeated_source = (
            docker_router_qoe_migration_repeated_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_qoe_protection_migration_sequential_repeated_matrix.v1",
            docker_router_qoe_migration_repeated_source,
        )
        self.assertIn("SEED_SEMANTICS", docker_router_qoe_migration_repeated_source)
        self.assertIn("confidence_epoch_ratio", docker_router_qoe_migration_repeated_source)
        self.assertIn("failure_mode_counts", docker_router_qoe_migration_repeated_source)
        self.assertIn("confidence_not_separated", docker_router_qoe_migration_repeated_source)
        self.assertIn("confidence_fallback_applied", docker_router_qoe_migration_repeated_source)
        self.assertIn("confidence_fallback_run_count", docker_router_qoe_migration_repeated_source)
        self.assertIn("confidence_fallback_delivery_failure", docker_router_qoe_migration_repeated_source)
        self.assertIn("confidence_fallback_recovered_window", docker_router_qoe_migration_repeated_source)
        self.assertIn("feedback_safe_mode_delivery_failure", docker_router_qoe_migration_repeated_source)
        self.assertIn("feedback_safe_mode_run_count", docker_router_qoe_migration_repeated_source)
        self.assertIn("fallback_recovery_ok_run_count", docker_router_qoe_migration_repeated_source)
        self.assertIn("sequential_separation_margin", docker_router_qoe_migration_repeated_source)
        docker_router_service_timeout_script = (
            ROOT / "scripts" / "run_rmw_docker_router_ros2_service_timeout_probe.py"
        )
        self.assertTrue(docker_router_service_timeout_script.exists())
        docker_router_service_timeout_source = docker_router_service_timeout_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_ros2_service_timeout_probe.v1",
            docker_router_service_timeout_source,
        )
        self.assertIn("--expected-service-frames 2", docker_router_service_timeout_source)
        self.assertIn("timed_out", docker_router_service_timeout_source)
        docker_router_multi_proactive_repeated_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_router_multi_robot_proactive_deadline_diversity_repeated_loss_matrix.py"
        )
        self.assertTrue(docker_router_multi_proactive_repeated_script.exists())
        docker_router_multi_proactive_repeated_source = (
            docker_router_multi_proactive_repeated_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_proactive_deadline_diversity_repeated_loss_matrix.v1",
            docker_router_multi_proactive_repeated_source,
        )
        self.assertIn("min_deadline_success_jain_index", docker_router_multi_proactive_repeated_source)
        self.assertIn("total_proactive_path_transmissions", docker_router_multi_proactive_repeated_source)
        docker_router_multihop_reliability_script = (
            ROOT / "scripts" / "run_rmw_docker_router_multihop_reliability_probe.py"
        )
        self.assertTrue(docker_router_multihop_reliability_script.exists())
        docker_router_multihop_reliability_source = docker_router_multihop_reliability_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_multihop_reliability_probe.v1",
            docker_router_multihop_reliability_source,
        )
        self.assertIn("router_a", docker_router_multihop_reliability_source)
        self.assertIn("router_b", docker_router_multihop_reliability_source)
        self.assertIn("--peers {router_b_ip}:48351", docker_router_multihop_reliability_source)
        self.assertIn("--graph-peers {router_b_ip}:48351", docker_router_multihop_reliability_source)
        self.assertIn("--drop-source-sequences 2", docker_router_multihop_reliability_source)
        self.assertIn("ack_nack_forwarded", docker_router_multihop_reliability_source)
        self.assertIn("nack_retransmissions", docker_router_multihop_reliability_source)
        docker_router_path_diversity_script = ROOT / "scripts" / "run_rmw_docker_router_path_diversity_probe.py"
        self.assertTrue(docker_router_path_diversity_script.exists())
        docker_router_path_diversity_source = docker_router_path_diversity_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_path_diversity_probe.v1",
            docker_router_path_diversity_source,
        )
        self.assertIn("primary_router", docker_router_path_diversity_source)
        self.assertIn("backup_router", docker_router_path_diversity_source)
        self.assertIn("--drop-source-sequences 2", docker_router_path_diversity_source)
        self.assertIn("--min-retransmissions 0", docker_router_path_diversity_source)
        self.assertIn('nack_retransmissions", 0) == 0', docker_router_path_diversity_source)
        docker_router_adaptive_failover_script = ROOT / "scripts" / "run_rmw_docker_router_adaptive_failover_probe.py"
        self.assertTrue(docker_router_adaptive_failover_script.exists())
        docker_router_adaptive_failover_source = docker_router_adaptive_failover_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_adaptive_failover_probe.v1",
            docker_router_adaptive_failover_source,
        )
        self.assertIn("FLEETQOX_RMW_PEER_POLICY=adaptive_failover", docker_router_adaptive_failover_source)
        self.assertIn("adaptive_failovers", docker_router_adaptive_failover_source)
        self.assertIn("adaptive_selected_peer_index", docker_router_adaptive_failover_source)
        self.assertIn("adaptive_unicast_frames", docker_router_adaptive_failover_source)
        self.assertIn("primary_router", docker_router_adaptive_failover_source)
        self.assertIn("backup_router", docker_router_adaptive_failover_source)
        docker_router_adaptive_score_script = ROOT / "scripts" / "run_rmw_docker_router_adaptive_score_probe.py"
        self.assertTrue(docker_router_adaptive_score_script.exists())
        docker_router_adaptive_score_source = docker_router_adaptive_score_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_adaptive_score_probe.v1",
            docker_router_adaptive_score_source,
        )
        self.assertIn("FLEETQOX_RMW_PEER_POLICY=adaptive_score", docker_router_adaptive_score_source)
        self.assertIn("--post-recovery-payload", docker_router_adaptive_score_source)
        self.assertIn("adaptive_peer_score_sum", docker_router_adaptive_score_source)
        self.assertIn("adaptive_selected_peer_index", docker_router_adaptive_score_source)
        self.assertIn("backup_router", docker_router_adaptive_score_source)
        docker_router_adaptive_qos_script = ROOT / "scripts" / "run_rmw_docker_router_adaptive_qos_probe.py"
        self.assertTrue(docker_router_adaptive_qos_script.exists())
        docker_router_adaptive_qos_source = docker_router_adaptive_qos_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_adaptive_qos_probe.v1",
            docker_router_adaptive_qos_source,
        )
        self.assertIn("FLEETQOX_RMW_PEER_POLICY=adaptive_qos", docker_router_adaptive_qos_source)
        self.assertIn("FLEETQOX_RMW_REDUNDANT_DEADLINE_MS=50", docker_router_adaptive_qos_source)
        self.assertIn("--deadline-ms 20", docker_router_adaptive_qos_source)
        self.assertIn("adaptive_redundant_frames", docker_router_adaptive_qos_source)
        self.assertIn("adaptive_unicast_frames", docker_router_adaptive_qos_source)
        self.assertIn('nack_retransmissions", 0) == 0', docker_router_adaptive_qos_source)
        docker_router_fleet_plan_script = ROOT / "scripts" / "run_rmw_docker_router_fleet_plan_probe.py"
        self.assertTrue(docker_router_fleet_plan_script.exists())
        docker_router_fleet_plan_source = docker_router_fleet_plan_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_fleet_plan_probe.v1",
            docker_router_fleet_plan_source,
        )
        self.assertIn("FLEETQOX_RMW_PEER_POLICY=fleet_plan", docker_router_fleet_plan_source)
        self.assertIn("FLEETQOX_RMW_FLEET_PATH_PLAN_FILE", docker_router_fleet_plan_source)
        self.assertIn("OnlineFleetPathPlanner", docker_router_fleet_plan_source)
        self.assertIn("PathObservation", docker_router_fleet_plan_source)
        self.assertIn("primary_wifi=", docker_router_fleet_plan_source)
        self.assertIn("backup_5g=", docker_router_fleet_plan_source)
        self.assertIn("fleet_plan_redundant_frames", docker_router_fleet_plan_source)
        self.assertIn("fleet_plan_selected_path_count", docker_router_fleet_plan_source)
        docker_router_live_plan_script = (
            ROOT / "scripts" / "run_rmw_docker_router_live_telemetry_plan_probe.py"
        )
        self.assertTrue(docker_router_live_plan_script.exists())
        docker_router_live_plan_source = docker_router_live_plan_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_live_telemetry_plan_probe.v1",
            docker_router_live_plan_source,
        )
        self.assertIn("LivePathPlanController", docker_router_live_plan_source)
        self.assertIn("ROUTER_TELEMETRY_SCHEMA_VERSION", docker_router_live_plan_source)
        self.assertIn("subscriber_telemetry_file", docker_router_live_plan_source)
        self.assertIn("--subscriber-telemetry-file", docker_router_live_plan_source)
        self.assertIn("--telemetry-file", docker_router_live_plan_source)
        self.assertIn("FLEETQOX_RMW_FLEET_PATH_PLAN_FILE", docker_router_live_plan_source)
        docker_multi_robot_live_plan_script = (
            ROOT / "scripts" / "run_rmw_docker_multi_robot_live_telemetry_plan_probe.py"
        )
        self.assertTrue(docker_multi_robot_live_plan_script.exists())
        docker_multi_robot_live_plan_source = docker_multi_robot_live_plan_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_multi_robot_live_telemetry_plan_probe.v1",
            docker_multi_robot_live_plan_source,
        )
        self.assertIn("CONTROL_TOPIC = \"/robot_0000/cmd_vel\"", docker_multi_robot_live_plan_source)
        self.assertIn("STATE_TOPIC = \"/robot_0001/odom\"", docker_multi_robot_live_plan_source)
        self.assertIn("FINAL_PATH_PLAN", docker_multi_robot_live_plan_source)
        self.assertIn("backup_5g+primary_wifi", docker_multi_robot_live_plan_source)
        self.assertIn("subscriber_telemetry_files", docker_multi_robot_live_plan_source)
        self.assertIn("LivePathPlanController", docker_multi_robot_live_plan_source)
        self.assertIn("wait_for_path_plan", docker_multi_robot_live_plan_source)
        self.assertIn("duplicate_data_frames_deduped", docker_multi_robot_live_plan_source)
        self.assertIn("ack_nack_duplicate_received", docker_multi_robot_live_plan_source)
        self.assertIn("ROUTER_TELEMETRY_PROFILES", docker_multi_robot_live_plan_source)
        self.assertIn("--profile", docker_multi_robot_live_plan_source)
        self.assertIn("NETEM_SCHEMA_VERSION", docker_multi_robot_live_plan_source)
        self.assertIn("--enable-netem", docker_multi_robot_live_plan_source)
        self.assertIn("--require-netem", docker_multi_robot_live_plan_source)
        self.assertIn("--netem-drain-s", docker_multi_robot_live_plan_source)
        self.assertIn("--reuse-build", docker_multi_robot_live_plan_source)
        self.assertIn("ensure_live_plan_build", docker_multi_robot_live_plan_source)
        self.assertIn("cleanup_live_plan_build", docker_multi_robot_live_plan_source)
        self.assertIn("--cap-add", docker_multi_robot_live_plan_source)
        self.assertIn("tc qdisc replace dev eth0 root netem", docker_multi_robot_live_plan_source)
        self.assertIn("loss random", docker_multi_robot_live_plan_source)
        self.assertIn("NETEM_SEED_SEMANTICS", docker_multi_robot_live_plan_source)
        self.assertIn("router_netem_drain_suffix", docker_multi_robot_live_plan_source)
        self.assertIn("--expected-ack-nack-forwarded", docker_multi_robot_live_plan_source)
        self.assertIn("control_duplicate_ack_required", docker_multi_robot_live_plan_source)
        self.assertIn("stochastic_netem", docker_multi_robot_live_plan_source)
        self.assertIn("state_proactive_data_repeats", docker_multi_robot_live_plan_source)
        self.assertIn("FLEETQOX_RMW_PROACTIVE_DATA_REPEATS", docker_multi_robot_live_plan_source)
        docker_multi_robot_live_matrix_script = (
            ROOT / "scripts" / "run_rmw_docker_multi_robot_live_telemetry_matrix.py"
        )
        self.assertTrue(docker_multi_robot_live_matrix_script.exists())
        docker_multi_robot_live_matrix_source = docker_multi_robot_live_matrix_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_multi_robot_live_telemetry_matrix.v1",
            docker_multi_robot_live_matrix_source,
        )
        self.assertIn("DEFAULT_PROFILES = \"wifi,wan,roaming\"", docker_multi_robot_live_matrix_source)
        self.assertIn("run_record_from_summary", docker_multi_robot_live_matrix_source)
        self.assertIn("render_markdown", docker_multi_robot_live_matrix_source)
        self.assertIn("control_duplicate_data_frames_deduped", docker_multi_robot_live_matrix_source)
        self.assertIn("netem_applied_run_count", docker_multi_robot_live_matrix_source)
        self.assertIn("repetition_seed=seed", docker_multi_robot_live_matrix_source)
        self.assertIn("reuse_build", docker_multi_robot_live_matrix_source)
        self.assertIn("control_duplicate_ack_required", docker_multi_robot_live_matrix_source)
        self.assertIn("stochastic_netem", docker_multi_robot_live_matrix_source)
        self.assertIn("state_proactive_data_repeats", docker_multi_robot_live_matrix_source)
        docker_multi_robot_netem_matrix_script = (
            ROOT / "scripts" / "run_rmw_docker_multi_robot_live_netem_matrix.py"
        )
        self.assertTrue(docker_multi_robot_netem_matrix_script.exists())
        docker_multi_robot_netem_matrix_source = docker_multi_robot_netem_matrix_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_multi_robot_live_netem_matrix.v1",
            docker_multi_robot_netem_matrix_source,
        )
        self.assertIn("enable_netem=True", docker_multi_robot_netem_matrix_source)
        self.assertIn("--require-netem", docker_multi_robot_netem_matrix_source)
        self.assertIn("--netem-drain-s", docker_multi_robot_netem_matrix_source)
        self.assertIn("--reuse-build", docker_multi_robot_netem_matrix_source)
        docker_multi_robot_stochastic_netem_matrix_script = (
            ROOT / "scripts" / "run_rmw_docker_multi_robot_live_stochastic_netem_matrix.py"
        )
        self.assertTrue(docker_multi_robot_stochastic_netem_matrix_script.exists())
        docker_multi_robot_stochastic_netem_matrix_source = (
            docker_multi_robot_stochastic_netem_matrix_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_multi_robot_live_stochastic_netem_matrix.v1",
            docker_multi_robot_stochastic_netem_matrix_source,
        )
        self.assertIn("DEFAULT_LOSS_SCALE = 0.1", docker_multi_robot_stochastic_netem_matrix_source)
        self.assertIn("--reuse-build", docker_multi_robot_stochastic_netem_matrix_source)
        docker_multi_robot_stochastic_netem_sweep_script = (
            ROOT / "scripts" / "run_rmw_docker_multi_robot_live_stochastic_netem_sweep.py"
        )
        self.assertTrue(docker_multi_robot_stochastic_netem_sweep_script.exists())
        docker_multi_robot_stochastic_netem_sweep_source = (
            docker_multi_robot_stochastic_netem_sweep_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_multi_robot_live_stochastic_netem_sweep.v1",
            docker_multi_robot_stochastic_netem_sweep_source,
        )
        self.assertIn("DEFAULT_LOSS_SCALES = \"0.1,0.25,0.5\"", docker_multi_robot_stochastic_netem_sweep_source)
        self.assertIn("--reuse-build", docker_multi_robot_stochastic_netem_sweep_source)
        self.assertIn("prepare_reused_build=False", docker_multi_robot_stochastic_netem_sweep_source)
        self.assertIn("prepare_reused_build: bool = True", docker_multi_robot_stochastic_netem_sweep_source)
        self.assertIn("contract_evidence_failed", docker_multi_robot_stochastic_netem_sweep_source)
        integration_validation_doc = ROOT / "docs" / "INTEGRATION_AND_VALIDATION.md"
        self.assertTrue(integration_validation_doc.exists())
        integration_validation_source = integration_validation_doc.read_text()
        self.assertIn("Docker/tc-netem", integration_validation_source)
        self.assertIn("8/16/32 robot scales", integration_validation_source)
        docker_multi_robot_stochastic_netem_ablation_script = (
            ROOT / "scripts" / "run_rmw_docker_multi_robot_live_stochastic_netem_ablation.py"
        )
        self.assertTrue(docker_multi_robot_stochastic_netem_ablation_script.exists())
        docker_multi_robot_stochastic_netem_ablation_source = (
            docker_multi_robot_stochastic_netem_ablation_script.read_text()
        )
        self.assertIn(
            "fleetrmw.rmw_multi_robot_live_stochastic_netem_ablation.v1",
            docker_multi_robot_stochastic_netem_ablation_source,
        )
        self.assertIn("DEFAULT_MODES = \"none,state_only,control_state\"", docker_multi_robot_stochastic_netem_ablation_source)
        self.assertIn("mode_record_from_sweep", docker_multi_robot_stochastic_netem_ablation_source)
        self.assertIn("repair_cost_frames_mean", docker_multi_robot_stochastic_netem_ablation_source)
        self.assertIn("prepare_reused_build=not reuse_build", docker_multi_robot_stochastic_netem_ablation_source)
        self.assertIn(
            "fragment loss/repair/admission/fairness",
            integration_validation_source,
        )
        live_baseline_comparison_script = ROOT / "scripts" / "compare_fleetrmw_live_baselines.py"
        self.assertTrue(live_baseline_comparison_script.exists())
        live_baseline_comparison_source = live_baseline_comparison_script.read_text()
        self.assertIn("fleetrmw.live_baseline_comparison.v1", live_baseline_comparison_source)
        self.assertIn("direct_claim_allowed", live_baseline_comparison_source)
        self.assertIn("indirect_named_profile", live_baseline_comparison_source)
        self.assertIn("fleet_router_terminal_horizon", live_baseline_comparison_source)
        self.assertIn("FleetRMW Matched 4-Robot Profile Rows", live_baseline_comparison_source)
        self.assertIn("common-middle harness", integration_validation_source)
        self.assertIn(
            "No current result supports a universal latency or superiority claim.",
            integration_validation_source,
        )
        ros2_direct_rmw_netem_probe_script = ROOT / "scripts" / "run_ros2_direct_rmw_netem_probe.py"
        self.assertTrue(ros2_direct_rmw_netem_probe_script.exists())
        ros2_direct_rmw_netem_probe_source = ros2_direct_rmw_netem_probe_script.read_text()
        self.assertIn("fleetrmw.ros2_direct_rmw_netem_probe.v1", ros2_direct_rmw_netem_probe_source)
        self.assertIn("rmw_unavailable", ros2_direct_rmw_netem_probe_source)
        self.assertIn("control_delivery_ratio", ros2_direct_rmw_netem_probe_source)
        self.assertIn("netem_shell_prefix", ros2_direct_rmw_netem_probe_source)
        ros2_direct_rmw_netem_matrix_script = ROOT / "scripts" / "run_ros2_direct_rmw_netem_matrix.py"
        self.assertTrue(ros2_direct_rmw_netem_matrix_script.exists())
        ros2_direct_rmw_netem_matrix_source = ros2_direct_rmw_netem_matrix_script.read_text()
        self.assertIn("fleetrmw.ros2_direct_rmw_netem_matrix.v1", ros2_direct_rmw_netem_matrix_source)
        self.assertIn("skipped_run_count", ros2_direct_rmw_netem_matrix_source)
        self.assertIn("run_probe", ros2_direct_rmw_netem_matrix_source)
        self.assertIn("Fast DDS", integration_validation_source)
        self.assertIn("Cyclone DDS", integration_validation_source)
        self.assertIn("Zenoh RMW", integration_validation_source)
        manifest_source = (ROOT / "experiments" / "testbed_manifest.json").read_text()
        self.assertIn("fleetrmw_multi_robot_live_stochastic_netem_ablation", manifest_source)
        self.assertIn("ros2_direct_rmw_netem_matrix", manifest_source)
        rmw_netem_dockerfile = ROOT / "external" / "rmw-netem" / "Dockerfile"
        self.assertTrue(rmw_netem_dockerfile.exists())
        rmw_netem_dockerfile_source = rmw_netem_dockerfile.read_text()
        self.assertIn("iproute2", rmw_netem_dockerfile_source)
        self.assertIn("python3-colcon-common-extensions", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-nav2-msgs", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-nav2-behaviors", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-nav2-behavior-tree", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-nav2-bt-navigator", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-nav2-planner", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-nav2-controller", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-nav2-navfn-planner", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-nav2-dwb-controller", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-tf2-msgs", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-rmf-task-msgs", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-rmf-fleet-msgs", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-rmw-cyclonedds-cpp", rmw_netem_dockerfile_source)
        self.assertIn("ros-jazzy-rmw-zenoh-cpp", rmw_netem_dockerfile_source)
        rmw_netem_readme = ROOT / "external" / "rmw-netem" / "README.md"
        self.assertTrue(rmw_netem_readme.exists())
        self.assertIn("localhost/fleetrmw/rmw-netem:jazzy", rmw_netem_readme.read_text())
        docker_router_qos_script = ROOT / "scripts" / "run_rmw_docker_router_qos_drop_probe.py"
        self.assertTrue(docker_router_qos_script.exists())
        docker_router_qos_source = docker_router_qos_script.read_text()
        self.assertIn("fleetrmw.rmw_router_qos_drop_probe.v1", docker_router_qos_source)
        self.assertIn("expected-qos-drops", docker_router_qos_source)
        self.assertIn("forward-delay-ms", docker_router_qos_source)
        self.assertIn("--expect-taken false", docker_router_qos_source)
        self.assertIn("--entrypoint", docker_router_qos_source)
        self.assertIn("parse_last_json", docker_router_qos_source)
        self.assertIn("publisher_stderr", docker_router_qos_source)
        docker_router_priority_script = ROOT / "scripts" / "run_rmw_docker_router_qos_priority_probe.py"
        self.assertTrue(docker_router_priority_script.exists())
        docker_router_priority_source = docker_router_priority_script.read_text()
        self.assertIn("fleetrmw.rmw_router_qos_priority_probe.v1", docker_router_priority_source)
        self.assertIn("scheduler-window-ms", docker_router_priority_source)
        self.assertIn("deadline-ms", docker_router_priority_source)
        self.assertIn("expected-order", docker_router_priority_source)
        self.assertIn("forwarded_topics", docker_router_priority_source)
        docker_router_priority_matrix = ROOT / "scripts" / "run_rmw_docker_router_qos_priority_matrix.py"
        self.assertTrue(docker_router_priority_matrix.exists())
        docker_router_priority_matrix_source = docker_router_priority_matrix.read_text()
        self.assertIn("fleetrmw.rmw_router_qos_priority_matrix.v1", docker_router_priority_matrix_source)
        self.assertIn("fifo_baseline", docker_router_priority_matrix_source)
        self.assertIn("deadline_scheduler", docker_router_priority_matrix_source)
        self.assertIn("priority_improved", docker_router_priority_matrix_source)
        multi_robot_qos_script = (
            ROOT / "scripts" / "run_rmw_docker_router_multi_robot_qos_matrix.py"
        )
        self.assertTrue(multi_robot_qos_script.exists())
        multi_robot_qos_source = multi_robot_qos_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_qos_matrix.v1",
            multi_robot_qos_source,
        )
        self.assertIn("FLEETQOX_RMW_ROBOT_ID", multi_robot_qos_source)
        self.assertIn("scheduler_deadline_success_jain_index", multi_robot_qos_source)
        self.assertIn("per_robot_complete", multi_robot_qos_source)
        multi_robot_qos_doc = ROOT / "docs" / "RMW_MULTI_ROBOT_QOS_SCHEDULER_V1.md"
        self.assertTrue(multi_robot_qos_doc.exists())
        rmw_pubsub_source = (PKG / "src" / "rmw_pubsub.cpp").read_text()
        self.assertIn("FLEETQOX_RMW_ROBOT_ID", rmw_pubsub_source)
        self.assertIn("local_robot_id()", rmw_pubsub_source)
        self.assertIn("scheduler_per_robot", router_source)
        self.assertIn("scheduler_deadline_misses", router_source)
        self.assertIn("scheduler_queue_wait_ms_mean", router_source)
        self.assertIn("scheduler_urgent_deadline_ms", router_source)
        self.assertIn("scheduler_urgent_frames", router_source)
        self.assertIn("scheduler_paced_frames", router_source)
        self.assertIn("scheduler_drain_pacing_ms", router_source)
        self.assertIn("scheduler_admission_policy", router_source)
        self.assertIn("scheduler_admits_holdback", router_source)
        self.assertIn("slo_service_time", router_source)
        self.assertIn("slo_service_epoch", router_source)
        self.assertIn("scheduler_admission_ewma_alpha", router_source)
        self.assertIn("scheduler_admission_min_epoch_frames", router_source)
        self.assertIn("scheduler_admission_switches", router_source)
        self.assertIn("scheduler_admission_holdback_enabled", router_source)
        self.assertIn("scheduler_admission_bypassed_frames", router_source)
        self.assertIn("scheduler_admission_service_ratio_max", router_source)
        self.assertIn("take_age_ms", multi_robot_qos_source)
        self.assertIn("payload-size", multi_robot_qos_source)
        self.assertIn("e2e_deadline_misses", multi_robot_qos_source)
        self.assertIn("scheduler_admission_policy", multi_robot_qos_source)
        self.assertIn("scheduler_admission_min_service_ratio", multi_robot_qos_source)
        self.assertIn("netem_loss_percent", multi_robot_qos_source)
        self.assertIn("netem_config_for_profile", multi_robot_qos_source)
        multi_robot_netem_script = (
            ROOT / "scripts" / "run_rmw_docker_router_multi_robot_qos_netem_matrix.py"
        )
        self.assertTrue(multi_robot_netem_script.exists())
        multi_robot_netem_source = multi_robot_netem_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_qos_netem_matrix.v1",
            multi_robot_netem_source,
        )
        self.assertIn("control_p95_reduction_ms", multi_robot_netem_source)
        self.assertIn("adaptive_selected_policy", multi_robot_netem_source)
        self.assertIn("adaptive_worse_profile_count", multi_robot_netem_source)
        self.assertIn("adaptive_mean_control_p95_reduction_ms", multi_robot_netem_source)
        self.assertIn("adaptive_mean_reduction > 0.0", multi_robot_netem_source)
        self.assertIn("deadline_gated_holdback", multi_robot_netem_source)
        self.assertIn("adaptive_selected_policy(", multi_robot_netem_source)
        self.assertIn("scheduler_urgent_frames", multi_robot_netem_source)
        self.assertIn("netem_qdisc", multi_robot_netem_source)
        live_adaptive_script = (
            ROOT / "scripts" / "run_rmw_docker_router_multi_robot_qos_live_adaptive_matrix.py"
        )
        self.assertTrue(live_adaptive_script.exists())
        live_adaptive_source = live_adaptive_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_qos_live_adaptive_matrix.v1",
            live_adaptive_source,
        )
        self.assertIn("slo_service_epoch", live_adaptive_source)
        self.assertIn("queued_profile_count", live_adaptive_source)
        self.assertIn("bypassed_profile_count", live_adaptive_source)
        self.assertIn("control_p95_regression_count", live_adaptive_source)
        self.assertIn("scheduler_admission_bypassed_frames", live_adaptive_source)
        self.assertIn("scheduler_admission_epoch_samples", live_adaptive_source)
        self.assertIn("scheduler_admission_switches", live_adaptive_source)
        repeated_loss_script = (
            ROOT / "scripts" /
            "run_rmw_docker_router_multi_robot_qos_live_adaptive_repeated_loss_matrix.py"
        )
        self.assertTrue(repeated_loss_script.exists())
        repeated_loss_source = repeated_loss_script.read_text()
        self.assertIn(
            "fleetrmw.rmw_router_multi_robot_qos_live_adaptive_repeated_loss_matrix.v1",
            repeated_loss_source,
        )
        self.assertIn("loss_percents", repeated_loss_source)
        self.assertIn("SEED_SEMANTICS", repeated_loss_source)
        self.assertIn("partial", repeated_loss_source)
        self.assertIn("fail_on_row_failure", repeated_loss_source)
        self.assertIn("netem_loss_percent=loss_percent", repeated_loss_source)

    def test_fleet_scale_frontier_action_and_comparison_contracts_exist(self) -> None:
        iface_cmake = (IFACE_PKG / "CMakeLists.txt").read_text()
        iface_manifest = (IFACE_PKG / "package.xml").read_text()
        self.assertIn("action/NavigateFleet.action", iface_cmake)
        self.assertIn("action/DispatchFleetTask.action", iface_cmake)
        self.assertIn("srv/FleetShape.srv", iface_cmake)
        self.assertIn("fleetrmw_bounded_shape_service_probe", iface_cmake)
        self.assertIn("DEPENDENCIES builtin_interfaces geometry_msgs nav_msgs sensor_msgs", iface_cmake)
        self.assertIn("<depend>builtin_interfaces</depend>", iface_manifest)
        self.assertIn("<depend>geometry_msgs</depend>", iface_manifest)
        navigate_action = (IFACE_PKG / "action" / "NavigateFleet.action").read_text()
        dispatch_action = (IFACE_PKG / "action" / "DispatchFleetTask.action").read_text()
        self.assertIn("geometry_msgs/PoseStamped pose", navigate_action)
        self.assertIn("builtin_interfaces/Duration navigation_time", navigate_action)
        self.assertIn("uint16 number_of_recoveries", navigate_action)
        self.assertIn("string[] phases", dispatch_action)
        self.assertIn("builtin_interfaces/Time completion_time", dispatch_action)
        self.assertIn("float32 progress", dispatch_action)
        bounded_service = (IFACE_PKG / "srv" / "FleetShape.srv").read_text()
        self.assertIn("string<=32 robot_id", bounded_service)
        self.assertIn("uint8[16] session_token", bounded_service)
        self.assertIn("float32[<=128] ranges", bounded_service)
        self.assertIn(
            "geometry_msgs/PoseStamped[<=16] waypoints",
            bounded_service,
        )
        self.assertIn("uint32[<=64] admitted_indices", bounded_service)
        bounded_cpp_probe = (
            IFACE_PKG / "src" / "bounded_shape_service_probe.cpp"
        ).read_text()
        self.assertIn("fleetrmw.bounded_shape_cpp_server.v1", bounded_cpp_probe)
        self.assertIn("kRangeCount = 128", bounded_cpp_probe)
        self.assertIn("kWaypointCount = 16", bounded_cpp_probe)
        bounded_python_probe = (
            ROOT / "scripts" / "rclpy_bounded_shape_service_endpoint.py"
        ).read_text()
        self.assertIn("fleetrmw.bounded_shape_python_endpoint.v1", bounded_python_probe)
        self.assertIn("RANGE_COUNT = 128", bounded_python_probe)
        bounded_runner = (
            ROOT / "scripts" / "run_rmw_docker_router_bounded_shape_service_probe.py"
        ).read_text()
        self.assertIn(
            "fleetrmw.docker_router_bounded_shape_service_probe.v2",
            bounded_runner,
        )
        self.assertIn("bounded_nested_message_sequence_claim", bounded_runner)
        self.assertIn(
            "service_discovery_repair_without_runner_override_claim",
            bounded_runner,
        )
        self.assertNotIn(
            "export FLEETQOX_RMW_SERVICE_REQUEST_REPEATS=",
            bounded_runner,
        )
        self.assertIn("--iterations", bounded_runner)

        docker_cli_matrix_script = ROOT / "scripts" / "run_rmw_docker_ros2_cli_message_matrix.py"
        docker_cli_matrix_source = docker_cli_matrix_script.read_text()
        for msg_type in (
            "sensor_msgs/msg/PointCloud2",
            "trajectory_msgs/msg/JointTrajectory",
            "diagnostic_msgs/msg/DiagnosticArray",
            "fleetrmw_interfaces/msg/SampleIdentity",
            "fleetrmw_interfaces/msg/ProjectionQuality",
        ):
            self.assertIn(msg_type, docker_cli_matrix_source)

        nav_rmf_script = ROOT / "scripts" / "run_rmw_docker_router_nav2_rmf_action_workload.py"
        self.assertTrue(nav_rmf_script.exists())
        nav_rmf_source = nav_rmf_script.read_text()
        self.assertIn("fleetrmw.rmw_router_nav2_rmf_action_workload.v6", nav_rmf_source)
        self.assertIn("NavigateFleet", nav_rmf_source)
        self.assertIn("DispatchFleetTask", nav_rmf_source)
        self.assertIn("NavigateToPose", nav_rmf_source)
        self.assertIn("SubmitTask", nav_rmf_source)
        self.assertIn("CancelTask", nav_rmf_source)
        self.assertIn("--upstream-concurrency", nav_rmf_source)
        self.assertIn("--goal-batch-size", nav_rmf_source)
        self.assertIn("--goal-batch-timeout-s", nav_rmf_source)
        self.assertIn("--goal-send-pacing-ms", nav_rmf_source)
        self.assertIn("upstream_concurrency >= 4096", nav_rmf_source)
        self.assertIn("goal_send_pacing_ms = 0.5", nav_rmf_source)
        self.assertIn("nav2_rmf_unwindowed_4096_claim", nav_rmf_source)
        self.assertIn("--goal-batch-delay-ms", nav_rmf_source)
        self.assertIn("FLEETQOX_GOAL_RECREATE_CLIENT_PER_BATCH", nav_rmf_source)
        self.assertIn("goal_recreate_client_per_batch", nav_rmf_source)
        self.assertIn("early_client_failed", nav_rmf_source)
        self.assertIn("docker_unavailable", nav_rmf_source)
        self.assertIn("preserved_existing_summary", nav_rmf_source)
        self.assertIn("FLEETQOX_RMW_UDP_SOCKET_BUFFER_BYTES", nav_rmf_source)
        self.assertIn("FLEETQOX_RMW_UDP_SEND_PACING_US", nav_rmf_source)
        self.assertIn("FLEETQOX_ROUTER_UDP_SOCKET_BUFFER_BYTES", nav_rmf_source)
        self.assertIn("FLEETQOX_RMW_SERVICE_REQUEST_REPEATS", nav_rmf_source)
        self.assertIn("FLEETQOX_RMW_SERVICE_RESPONSE_REPEATS", nav_rmf_source)
        self.assertIn("FLEETQOX_RMW_SERVICE_REQUEST_REPEAT_INTERVAL_MS", nav_rmf_source)
        self.assertIn("FLEETQOX_RMW_SERVICE_RESPONSE_REPEAT_INTERVAL_MS", nav_rmf_source)
        self.assertIn("service_request_repeat_interval_ms", nav_rmf_source)
        self.assertIn("service_response_repeat_interval_ms", nav_rmf_source)
        self.assertIn("router_expected_service_frames", nav_rmf_source)
        self.assertIn("router_post_satisfaction_ms", nav_rmf_source)
        self.assertIn("upstream_concurrency", nav_rmf_source)
        self.assertIn("ActionServer", nav_rmf_source)
        self.assertIn("ActionClient", nav_rmf_source)
        self.assertIn("expected_service_frames = 58 + upstream_concurrency * 6", nav_rmf_source)
        self.assertIn("ManagedNavLifecycle", nav_rmf_source)
        self.assertIn("ManageLifecycleNodes", nav_rmf_source)
        self.assertIn("nav2_lifecycle_manager lifecycle_manager", nav_rmf_source)
        self.assertIn("nav2_lifecycle_manager_upstream", nav_rmf_source)
        self.assertIn("lifecycle_transport", nav_rmf_source)
        self.assertIn("nav2_compatible", nav_rmf_source)
        self.assertIn("rmf_compatible", nav_rmf_source)
        self.assertIn("nav2_upstream", nav_rmf_source)
        self.assertIn("rmf_upstream", nav_rmf_source)
        nav2_pc_script = ROOT / "scripts" / "run_rmw_docker_nav2_planner_controller_lifecycle_probe.py"
        self.assertTrue(nav2_pc_script.exists())
        nav2_pc_source = nav2_pc_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_planner_controller_lifecycle_probe.v1",
            nav2_pc_source,
        )
        self.assertIn("nav2_navfn_planner::NavfnPlanner", nav2_pc_source)
        self.assertIn("dwb_core::DWBLocalPlanner", nav2_pc_source)
        self.assertIn("activation_gap", nav2_pc_source)
        self.assertIn("full_nav2_navigation_stack_claim", nav2_pc_source)
        nav2_activation_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_planner_controller_activation_probe.py"
        )
        self.assertTrue(nav2_activation_script.exists())
        nav2_activation_source = nav2_activation_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_planner_controller_activation_probe.v1",
            nav2_activation_source,
        )
        self.assertIn("tf2_msgs/msg/TFMessage", nav2_activation_source)
        self.assertIn("planner_activate_transition", nav2_activation_source)
        self.assertIn("controller_activate_transition", nav2_activation_source)
        self.assertIn("map_server_claim", nav2_activation_source)
        self.assertIn("navigation_goal_claim", nav2_activation_source)
        self.assertIn("full_nav2_navigation_stack_claim", nav2_activation_source)
        nav2_compute_path_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_planner_compute_path_probe.py"
        )
        self.assertTrue(nav2_compute_path_script.exists())
        nav2_compute_path_source = nav2_compute_path_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_planner_compute_path_probe.v1",
            nav2_compute_path_source,
        )
        self.assertIn("ComputePathToPose", nav2_compute_path_source)
        self.assertIn("nav_msgs/msg/OccupancyGrid", nav2_compute_path_source)
        self.assertIn("compute_path_goal_succeeded", nav2_compute_path_source)
        self.assertIn("compute_path_path_pose_count", nav2_compute_path_source)
        self.assertIn("controller_execution_claim", nav2_compute_path_source)
        nav2_follow_path_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_controller_follow_path_probe.py"
        )
        self.assertTrue(nav2_follow_path_script.exists())
        nav2_follow_path_source = nav2_follow_path_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_controller_follow_path_probe.v1",
            nav2_follow_path_source,
        )
        self.assertIn("FollowPath", nav2_follow_path_source)
        self.assertIn("nav_msgs/msg/Odometry", nav2_follow_path_source)
        self.assertIn("follow_path_goal_succeeded", nav2_follow_path_source)
        self.assertIn("controller_execution_scope", nav2_follow_path_source)
        nav2_navigate_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_navigate_to_pose_probe.py"
        )
        self.assertTrue(nav2_navigate_script.exists())
        nav2_navigate_source = nav2_navigate_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_navigate_to_pose_probe.v1",
            nav2_navigate_source,
        )
        self.assertIn("NavigateToPose", nav2_navigate_source)
        self.assertIn("minimal_compute_path_to_pose_then_follow_path", nav2_navigate_source)
        self.assertIn("navigate_to_pose_goal_succeeded", nav2_navigate_source)
        self.assertIn("moving_robot_navigation_claim", nav2_navigate_source)
        nav2_repeated_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_navigate_to_pose_repeated_probe.py"
        )
        self.assertTrue(nav2_repeated_script.exists())
        nav2_repeated_source = nav2_repeated_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_navigate_to_pose_repeated_probe.v1",
            nav2_repeated_source,
        )
        self.assertIn("run_rmw_docker_nav2_navigate_to_pose_probe", nav2_repeated_source)
        self.assertIn("navigate_to_pose_repeated_smoke", nav2_repeated_source)
        self.assertIn("navigate_to_pose_goal_succeeded_run_count", nav2_repeated_source)
        self.assertIn("min_service_frames_per_run", nav2_repeated_source)
        nav2_moving_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_navigate_to_pose_moving_probe.py"
        )
        self.assertTrue(nav2_moving_script.exists())
        nav2_moving_source = nav2_moving_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_navigate_to_pose_moving_probe.v1",
            nav2_moving_source,
        )
        self.assertIn("moving_base=True", nav2_moving_source)
        self.assertIn("goal_x", nav2_moving_source)
        self.assertIn("moving_robot_navigation_claim", nav2_moving_source)
        nav2_extended_moving_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_navigate_to_pose_extended_moving_probe.py"
        )
        self.assertTrue(nav2_extended_moving_script.exists())
        nav2_extended_moving_source = nav2_extended_moving_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_navigate_to_pose_extended_moving_probe.v1",
            nav2_extended_moving_source,
        )
        self.assertIn("extended_moving_navigation_claim", nav2_extended_moving_source)
        self.assertIn("goal-x", nav2_extended_moving_source)
        nav2_long_moving_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_navigate_to_pose_long_moving_probe.py"
        )
        self.assertTrue(nav2_long_moving_script.exists())
        nav2_long_moving_source = nav2_long_moving_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_navigate_to_pose_long_moving_probe.v1",
            nav2_long_moving_source,
        )
        self.assertIn("navigate_to_pose_long_moving_workload", nav2_long_moving_source)
        self.assertIn("long_navigation_workload_claim", nav2_long_moving_source)
        self.assertIn("min-total-moved-distance", nav2_long_moving_source)
        nav2_obstacle_repair_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_planner_obstacle_repair_probe.py"
        )
        self.assertTrue(nav2_obstacle_repair_script.exists())
        nav2_obstacle_repair_source = nav2_obstacle_repair_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_planner_obstacle_repair_probe.v1",
            nav2_obstacle_repair_source,
        )
        self.assertIn("blocked_occupancy_grid_yaml", nav2_obstacle_repair_source)
        self.assertIn(
            "planner_static_obstacle_repair_claim",
            nav2_obstacle_repair_source,
        )
        self.assertIn("full_nav2_obstacle_recovery_claim", nav2_obstacle_repair_source)
        nav2_obstacle_retry_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_navigate_to_pose_obstacle_retry_probe.py"
        )
        self.assertTrue(nav2_obstacle_retry_script.exists())
        nav2_obstacle_retry_source = nav2_obstacle_retry_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_navigate_to_pose_obstacle_retry_probe.v1",
            nav2_obstacle_retry_source,
        )
        self.assertIn(
            "nav2_obstacle_retry_after_clear_claim",
            nav2_obstacle_retry_source,
        )
        self.assertIn(
            "autonomous_same_goal_nav2_obstacle_recovery_claim",
            nav2_obstacle_retry_source,
        )
        nav2_autonomous_obstacle_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_nav2_navigate_to_pose_autonomous_obstacle_recovery_probe.py"
        )
        self.assertTrue(nav2_autonomous_obstacle_script.exists())
        nav2_autonomous_obstacle_source = nav2_autonomous_obstacle_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_navigate_to_pose_autonomous_obstacle_recovery_probe.v1",
            nav2_autonomous_obstacle_source,
        )
        self.assertIn("same_goal_obstacle_recovery_observed", nav2_autonomous_obstacle_source)
        self.assertIn("nav2_behaviors::Wait", nav2_autonomous_obstacle_source)
        nav2_behavior_spin_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_behavior_spin_probe.py"
        )
        self.assertTrue(nav2_behavior_spin_script.exists())
        nav2_behavior_spin_source = nav2_behavior_spin_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_behavior_spin_probe.v1",
            nav2_behavior_spin_source,
        )
        self.assertIn("nav2_behaviors::Spin", nav2_behavior_spin_source)
        self.assertIn("nav2_msgs/action/Spin", nav2_behavior_spin_source)
        self.assertIn("spin_goal_succeeded", nav2_behavior_spin_source)
        self.assertIn("nav2_recovery_behavior_claim", nav2_behavior_spin_source)
        nav2_recovery_tree_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_navigate_to_pose_recovery_tree_probe.py"
        )
        self.assertTrue(nav2_recovery_tree_script.exists())
        nav2_recovery_tree_source = nav2_recovery_tree_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_navigate_to_pose_recovery_tree_probe.v1",
            nav2_recovery_tree_source,
        )
        self.assertIn("RecoveryNode", nav2_recovery_tree_source)
        self.assertIn("MissingPlanner", nav2_recovery_tree_source)
        self.assertIn("navigate_to_pose_recovery_tree_claim", nav2_recovery_tree_source)
        self.assertIn("successful_recovered_navigation_claim", nav2_recovery_tree_source)
        nav2_recovered_success_script = (
            ROOT / "scripts" / "run_rmw_docker_nav2_navigate_to_pose_recovered_success_probe.py"
        )
        self.assertTrue(nav2_recovered_success_script.exists())
        nav2_recovered_success_source = nav2_recovered_success_script.read_text()
        self.assertIn(
            "fleetrmw.docker_nav2_navigate_to_pose_recovered_success_probe.v1",
            nav2_recovered_success_source,
        )
        self.assertIn("SpinThenNavigate", nav2_recovered_success_source)
        self.assertIn("spin_then_compute_path_then_follow_path", nav2_recovered_success_source)
        self.assertIn("successful_recovered_navigation_claim", nav2_recovered_success_source)
        self.assertIn("obstacle_field_recovery_claim", nav2_recovered_success_source)
        nav2_recovered_success_repeated_script = (
            ROOT
            / "scripts"
            / "run_rmw_docker_nav2_navigate_to_pose_recovered_success_repeated_probe.py"
        )
        self.assertTrue(nav2_recovered_success_repeated_script.exists())
        nav2_recovered_success_repeated_source = (
            nav2_recovered_success_repeated_script.read_text()
        )
        self.assertIn(
            "fleetrmw.docker_nav2_navigate_to_pose_recovered_success_repeated_probe.v1",
            nav2_recovered_success_repeated_source,
        )
        self.assertIn(
            "navigate_to_pose_recovered_success_repeated_smoke",
            nav2_recovered_success_repeated_source,
        )
        self.assertIn(
            "successful_recovered_navigation_run_count",
            nav2_recovered_success_repeated_source,
        )

        ns3_runner = ROOT / "scripts" / "run_ns3_docker_fleet_matrix.py"
        self.assertTrue(ns3_runner.exists())
        ns3_source = ns3_runner.read_text()
        self.assertIn("fleetqox.ns3_docker_fleet_matrix.v1", ns3_source)
        self.assertIn("high_fidelity_wireless_claim_allowed", ns3_source)
        ns3_wifi_runner = ROOT / "scripts" / "run_ns3_docker_wifi_mobility_matrix.py"
        self.assertTrue(ns3_wifi_runner.exists())
        ns3_wifi_source = ns3_wifi_runner.read_text()
        self.assertIn("fleetqox.ns3_docker_wifi_mobility_matrix.v1", ns3_wifi_source)
        self.assertIn("roaming_handoff_claim_allowed", ns3_wifi_source)
        self.assertIn("single_ap_80211g_infrastructure", ns3_wifi_source)
        ns3_roaming_runner = ROOT / "scripts" / "run_ns3_docker_wifi_roaming_matrix.py"
        self.assertTrue(ns3_roaming_runner.exists())
        ns3_roaming_source = ns3_roaming_runner.read_text()
        self.assertIn("fleetqox.ns3_docker_wifi_roaming_matrix.v1", ns3_roaming_source)
        self.assertIn("bridged_dual_ap_80211g", ns3_roaming_source)
        self.assertIn("association_transition_events_measured", ns3_roaming_source)
        omnetpp_probe = ROOT / "scripts" / "run_omnetpp_template_integrity_probe.py"
        self.assertTrue(omnetpp_probe.exists())
        omnetpp_source = omnetpp_probe.read_text()
        self.assertIn("fleetqox.omnetpp_template_integrity_probe.v1", omnetpp_source)
        self.assertIn("omnetpp_template_integrity_claim", omnetpp_source)
        self.assertIn("omnetpp_parity_claim", omnetpp_source)
        self.assertIn("omnetpp_runtime_gap_reason", omnetpp_source)
        self.assertIn("omnetpp_parity_blocker", omnetpp_source)
        self.assertIn("prepare_trace_input", omnetpp_source)
        omnetpp_parity_runner = ROOT / "scripts" / "run_omnetpp_docker_parity.py"
        self.assertTrue(omnetpp_parity_runner.exists())
        omnetpp_parity_source = omnetpp_parity_runner.read_text()
        self.assertIn("fleetqox.omnetpp_ns3_docker_parity.v1", omnetpp_parity_source)
        self.assertIn("omnetpp_inet_runtime_claim", omnetpp_parity_source)
        self.assertIn("ns3_omnetpp_parity_claim", omnetpp_parity_source)
        self.assertIn('"full_tsn_mesh_parity_claim": False', omnetpp_parity_source)
        shm_header = PKG / "include" / "rmw_fleetqox_cpp" / "shared_memory_transport.hpp"
        shm_source = PKG / "src" / "shared_memory_transport.cpp"
        self.assertTrue(shm_header.exists())
        self.assertTrue(shm_source.exists())
        self.assertIn("PTHREAD_PROCESS_SHARED", shm_source.read_text())
        self.assertIn("shm_open", shm_source.read_text())
        shm_runner = ROOT / "scripts" / "run_rmw_docker_shared_memory_probe.py"
        self.assertTrue(shm_runner.exists())
        self.assertIn("fleetrmw.docker_shared_memory_probe.v1", shm_runner.read_text())
        self.assertIn("udp_fallback", shm_runner.read_text())
        hybrid_runner = ROOT / "scripts" / "run_rmw_docker_shm_udp_hybrid_probe.py"
        self.assertTrue(hybrid_runner.exists())
        self.assertIn("fleetrmw.docker_shm_udp_hybrid_probe.v1", hybrid_runner.read_text())
        self.assertIn("duplicate_data_frames_deduped", hybrid_runner.read_text())
        loan_probe = PKG / "src" / "loaned_message_probe.cpp"
        self.assertTrue(loan_probe.exists())
        self.assertIn("fleetrmw.loaned_message_probe.v1", loan_probe.read_text())
        allocation_probe = PKG / "src" / "allocation_probe.cpp"
        self.assertTrue(allocation_probe.exists())
        allocation_probe_source = allocation_probe.read_text()
        self.assertIn("fleetrmw.allocation_probe.v2", allocation_probe_source)
        self.assertIn("rmw_init_publisher_allocation", allocation_probe_source)
        self.assertIn("publish_take_with_allocation_ok", allocation_probe_source)
        self.assertIn("payload_scratch_reuse_ok", allocation_probe_source)
        self.assertIn("publisher_capacity_growths", allocation_probe_source)
        allocation_runner = ROOT / "scripts" / "run_rmw_docker_allocation_probe.py"
        self.assertTrue(allocation_runner.exists())
        allocation_runner_source = allocation_runner.read_text()
        self.assertIn("fleetrmw.docker_allocation_probe.v2", allocation_runner_source)
        self.assertIn("--iterations", allocation_runner_source)
        self.assertIn("allocation_payload_scratch_reuse", allocation_runner_source)
        self.assertIn("payload_scratch_total_capacity_growths", allocation_runner_source)
        self.assertIn("deep_preallocation", allocation_runner_source)
        self.assertIn("allocation_repeated_lifecycle_claim", allocation_runner_source)
        security_options_probe = PKG / "src" / "security_options_probe.cpp"
        self.assertTrue(security_options_probe.exists())
        security_options_source = security_options_probe.read_text()
        self.assertIn("fleetrmw.security_options_probe.v1", security_options_source)
        self.assertIn("security_options_lifecycle_abi_supported", security_options_source)
        self.assertIn("sros2_policy_enforcement_claim", security_options_source)
        security_options_runner = ROOT / "scripts" / "run_rmw_docker_security_options_probe.py"
        self.assertTrue(security_options_runner.exists())
        security_options_runner_source = security_options_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_security_options_probe.v1",
            security_options_runner_source,
        )
        self.assertIn("--iterations", security_options_runner_source)
        self.assertIn("security_options_repeated_lifecycle_claim", security_options_runner_source)
        self.assertIn("sros2_cli_available", security_options_runner_source)
        self.assertIn("security_policy_enforcement_gap_reason", security_options_runner_source)
        self.assertIn("production_security_hardening_claim", security_options_runner_source)
        security_policy_probe = PKG / "src" / "security_policy_probe.cpp"
        self.assertTrue(security_policy_probe.exists())
        security_policy_source = security_policy_probe.read_text()
        self.assertIn("fleetrmw.security_policy_probe.v1", security_policy_source)
        self.assertIn("FLEETQOX_RMW_SECURITY_POLICY", security_policy_source)
        self.assertIn("fleetqox_security_policy_enforcement_claim", security_policy_source)
        security_policy_runner = ROOT / "scripts" / "run_rmw_docker_security_policy_probe.py"
        self.assertTrue(security_policy_runner.exists())
        security_policy_runner_source = security_policy_runner.read_text()
        self.assertIn("fleetrmw.docker_security_policy_probe.v1", security_policy_runner_source)
        self.assertIn("security_policy_repeated_enforcement_claim", security_policy_runner_source)
        self.assertIn("publish_allow=/fleetqox/security_allowed", security_policy_runner_source)
        sros2_permissions_probe = PKG / "src" / "sros2_permissions_probe.cpp"
        self.assertTrue(sros2_permissions_probe.exists())
        sros2_permissions_source = sros2_permissions_probe.read_text()
        self.assertIn("fleetrmw.sros2_permissions_probe.v1", sros2_permissions_source)
        self.assertIn("FLEETQOX_RMW_SROS2_PERMISSIONS_FILE", sros2_permissions_source)
        self.assertIn(
            "FLEETQOX_RMW_SROS2_PERMISSIONS_P7S_FILE",
            sros2_permissions_source,
        )
        self.assertIn("runtime_signature_verified", sros2_permissions_source)
        self.assertIn(
            "sros2_permissions_xml_publish_enforcement_claim",
            sros2_permissions_source,
        )
        self.assertIn(
            "sros2_permissions_xml_subscribe_enforcement_claim",
            sros2_permissions_source,
        )
        self.assertIn(
            "sros2_permissions_xml_subscribe_denied_delta",
            sros2_permissions_source,
        )
        self.assertIn("malformed_permissions_fail_closed_claim", sros2_permissions_source)
        self.assertIn(
            "tampered_signed_permissions_fail_closed_claim",
            sros2_permissions_source,
        )
        sros2_permissions_runner = (
            ROOT / "scripts" / "run_rmw_docker_sros2_permissions_probe.py"
        )
        self.assertTrue(sros2_permissions_runner.exists())
        sros2_permissions_runner_source = sros2_permissions_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_sros2_permissions_probe.v1",
            sros2_permissions_runner_source,
        )
        self.assertIn("ros2 security create_permission", sros2_permissions_runner_source)
        self.assertIn("openssl smime -verify", sros2_permissions_runner_source)
        self.assertIn("permissions_xsd_validated", sros2_permissions_runner_source)
        self.assertIn("tampered_signature_fail_closed_ok", sros2_permissions_runner_source)
        self.assertIn(
            "sros2_permissions_xml_repeated_enforcement_claim",
            sros2_permissions_runner_source,
        )
        self.assertIn("RMW_IMPLEMENTATION=rmw_fleetqox_cpp", sros2_permissions_runner_source)
        self.assertIn("sros2_action_permissions_probe.py", sros2_permissions_runner_source)
        self.assertTrue((PKG / "test" / "security" / "sros2_policy.xml").exists())
        self.assertTrue(
            (PKG / "test" / "security" / "malformed_permissions.xml").exists()
        )
        sros2_service_probe = PKG / "src" / "sros2_service_permissions_probe.cpp"
        self.assertTrue(sros2_service_probe.exists())
        sros2_service_source = sros2_service_probe.read_text()
        self.assertIn(
            "fleetrmw.sros2_service_permissions_probe.v1",
            sros2_service_source,
        )
        self.assertIn(
            "sros2_service_request_reply_authorization_claim",
            sros2_service_source,
        )
        self.assertIn("service_request_publish_denied_delta", sros2_service_source)
        sros2_action_probe = ROOT / "scripts" / "sros2_action_permissions_probe.py"
        self.assertTrue(sros2_action_probe.exists())
        sros2_action_source = sros2_action_probe.read_text()
        self.assertIn(
            "fleetrmw.sros2_action_permissions_probe.v1",
            sros2_action_source,
        )
        self.assertIn("sros2_action_authorization_claim", sros2_action_source)
        self.assertIn("sros2_action_call_denied_fail_closed_claim", sros2_action_source)
        self.assertIn("sros2_action_execute_denied_fail_closed_claim", sros2_action_source)
        sros2_governance_probe = PKG / "src" / "sros2_governance_probe.cpp"
        self.assertTrue(sros2_governance_probe.exists())
        sros2_governance_source = sros2_governance_probe.read_text()
        self.assertIn("fleetrmw.sros2_governance_probe.v1", sros2_governance_source)
        self.assertIn("sros2_governance_access_control_claim", sros2_governance_source)
        self.assertIn(
            "sros2_governance_transport_protection_fail_closed_claim",
            sros2_governance_source,
        )
        self.assertTrue(
            (PKG / "test" / "security" / "sros2_governance_access_control.xml").exists()
        )
        sros2_identity_probe = PKG / "src" / "sros2_identity_probe.cpp"
        self.assertTrue(sros2_identity_probe.exists())
        sros2_identity_source = sros2_identity_probe.read_text()
        self.assertIn("fleetrmw.sros2_identity_probe.v1", sros2_identity_source)
        self.assertIn(
            "sros2_local_identity_credentials_validation_claim",
            sros2_identity_source,
        )
        self.assertIn(
            "sros2_identity_enclave_mismatch_fail_closed_claim",
            sros2_identity_source,
        )
        udp_aead_probe = PKG / "src" / "udp_aead_probe.cpp"
        self.assertTrue(udp_aead_probe.exists())
        udp_aead_source = udp_aead_probe.read_text()
        self.assertIn("fleetrmw.udp_aead_probe.v1", udp_aead_source)
        self.assertIn("udp_aead_authenticated_encryption_claim", udp_aead_source)
        pubsub_source = (PKG / "src" / "rmw_pubsub.cpp").read_text()
        self.assertIn("FLEETQOX_RMW_UDP_PEER_AUTH_REQUIRE", pubsub_source)
        self.assertIn("FLEETQOX_RMW_UDP_SESSION_KEY_ROTATE_FRAMES", pubsub_source)
        self.assertIn("FQAEAD2|", pubsub_source)
        self.assertIn("FleetRMW-UDP-AEAD-session-v1", pubsub_source)
        self.assertIn("FQPAUTH1|", pubsub_source)
        self.assertIn("X509_verify_cert", pubsub_source)
        self.assertIn("EVP_DigestVerifyFinal", pubsub_source)
        self.assertIn("FLEETQOX_RMW_SROS2_IDENTITY_CRL_FILE", pubsub_source)
        self.assertIn("X509_V_ERR_CERT_REVOKED", pubsub_source)
        udp_aead_runner = ROOT / "scripts" / "run_rmw_docker_udp_aead_probe.py"
        self.assertTrue(udp_aead_runner.exists())
        self.assertIn(
            "fleetrmw.docker_udp_aead_probe.v1",
            udp_aead_runner.read_text(),
        )
        udp_peer_auth_runner = ROOT / "scripts" / "run_rmw_docker_udp_peer_auth_probe.py"
        self.assertTrue(udp_peer_auth_runner.exists())
        udp_peer_auth_source = udp_peer_auth_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_udp_peer_auth_probe.v1", udp_peer_auth_source
        )
        self.assertIn("udp_peer_signature_tamper_fail_closed_claim", udp_peer_auth_source)
        self.assertIn("revoked_certificate_control", udp_peer_auth_source)
        self.assertIn("session_key_establishment_claim", udp_peer_auth_source)
        dynamic_probe = PKG / "src" / "dynamic_message_probe.cpp"
        self.assertTrue(dynamic_probe.exists())
        dynamic_source = dynamic_probe.read_text()
        self.assertIn("rmw_take_dynamic_message_with_info", dynamic_source)
        self.assertIn("dynamic_message_take_claim", dynamic_source)
        dynamic_runner = ROOT / "scripts" / "run_rmw_docker_dynamic_message_probe.py"
        self.assertTrue(dynamic_runner.exists())
        self.assertIn(
            "fleetrmw.docker_dynamic_message_probe.v1",
            dynamic_runner.read_text(),
        )
        stress_security_runner = (
            ROOT / "scripts" / "run_rmw_docker_stress_security_campaign.py"
        )
        self.assertTrue(stress_security_runner.exists())
        stress_security_source = stress_security_runner.read_text()
        self.assertIn("fleetrmw.docker_stress_security_campaign.v1", stress_security_source)
        self.assertIn("stress_security_smoke_claim", stress_security_source)
        self.assertIn("long_stress_security_campaign_claim", stress_security_source)
        self.assertIn("security_policy", stress_security_source)
        self.assertIn("quic_gateway_async_burst_soak", stress_security_source)
        qos_event_probe = PKG / "src" / "qos_event_probe.cpp"
        self.assertTrue(qos_event_probe.exists())
        qos_event_probe_source = qos_event_probe.read_text()
        self.assertIn("fleetrmw.qos_event_probe.v1", qos_event_probe_source)
        self.assertIn("rmw_publisher_event_init", qos_event_probe_source)
        self.assertIn("event_object_abi_ok", qos_event_probe_source)
        self.assertIn("deadline_event_production_scope", qos_event_probe_source)
        self.assertIn("offered_total_count_change", qos_event_probe_source)
        self.assertIn("wait_event_readiness_scope", qos_event_probe_source)
        self.assertIn("publisher_wait_ready", qos_event_probe_source)
        self.assertIn("timer_driven_idle_deadline_events", qos_event_probe_source)
        self.assertIn("idle_publisher_wait_ready", qos_event_probe_source)
        qos_event_runner = ROOT / "scripts" / "run_rmw_docker_qos_event_probe.py"
        self.assertTrue(qos_event_runner.exists())
        qos_event_runner_source = qos_event_runner.read_text()
        self.assertIn("fleetrmw.docker_qos_event_probe.v1", qos_event_runner_source)
        self.assertIn("--iterations", qos_event_runner_source)
        self.assertIn("event_production", qos_event_runner_source)
        self.assertIn("timer_idle_and_next_publish_or_receive_after_gap", qos_event_runner_source)
        self.assertIn("deadline_status_unread_count", qos_event_runner_source)
        self.assertIn("after_first_publish_or_receive", qos_event_runner_source)
        self.assertIn("qos_event_repeated_deadline_waitable_claim", qos_event_runner_source)
        qos_event_matrix_runner = (
            ROOT / "scripts" / "run_rmw_docker_qos_event_waitability_matrix.py"
        )
        self.assertTrue(qos_event_matrix_runner.exists())
        qos_event_matrix_source = qos_event_matrix_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_qos_event_waitability_matrix.v1",
            qos_event_matrix_source,
        )
        self.assertIn("event_type_count", qos_event_matrix_source)
        self.assertIn("RMW_EVENT_PUBLICATION_MATCHED", qos_event_matrix_source)
        self.assertIn("RMW_EVENT_MESSAGE_LOST", qos_event_matrix_source)
        self.assertIn("full_qos_event_waitable_readiness_claim", qos_event_matrix_source)
        matched_event_probe = PKG / "src" / "matched_event_probe.cpp"
        self.assertTrue(matched_event_probe.exists())
        matched_event_probe_source = matched_event_probe.read_text()
        self.assertIn("fleetrmw.matched_event_probe.v1", matched_event_probe_source)
        self.assertIn("RMW_EVENT_PUBLICATION_MATCHED", matched_event_probe_source)
        self.assertIn("RMW_EVENT_SUBSCRIPTION_MATCHED", matched_event_probe_source)
        self.assertIn("matched_event_scope", matched_event_probe_source)
        matched_event_runner = ROOT / "scripts" / "run_rmw_docker_matched_event_probe.py"
        self.assertTrue(matched_event_runner.exists())
        matched_event_runner_source = matched_event_runner.read_text()
        self.assertIn("fleetrmw.docker_matched_event_probe.v1", matched_event_runner_source)
        self.assertIn(
            "local_same_process_compatible_endpoint_create_destroy",
            matched_event_runner_source,
        )
        self.assertIn("--iterations", matched_event_runner_source)
        self.assertIn("matched_event_repeated_claim", matched_event_runner_source)
        self.assertIn("publication_disconnect_current_count_change", matched_event_runner_source)
        qos_incompatible_probe = PKG / "src" / "qos_incompatible_event_probe.cpp"
        self.assertTrue(qos_incompatible_probe.exists())
        qos_incompatible_probe_source = qos_incompatible_probe.read_text()
        self.assertIn("fleetrmw.qos_incompatible_event_probe.v1", qos_incompatible_probe_source)
        self.assertIn("RMW_EVENT_OFFERED_QOS_INCOMPATIBLE", qos_incompatible_probe_source)
        self.assertIn("RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE", qos_incompatible_probe_source)
        self.assertIn("RMW_EVENT_PUBLICATION_MATCHED", qos_incompatible_probe_source)
        self.assertIn("RMW_EVENT_SUBSCRIPTION_MATCHED", qos_incompatible_probe_source)
        self.assertIn("RMW_QOS_POLICY_RELIABILITY", qos_incompatible_probe_source)
        self.assertIn("RMW_QOS_POLICY_DURABILITY", qos_incompatible_probe_source)
        self.assertIn("incompatible_endpoint_matched_taken", qos_incompatible_probe_source)
        self.assertIn(
            "local_same_process_reliability_and_durability_mismatch",
            qos_incompatible_probe_source,
        )
        qos_incompatible_runner = (
            ROOT / "scripts" / "run_rmw_docker_qos_incompatible_event_probe.py"
        )
        self.assertTrue(qos_incompatible_runner.exists())
        qos_incompatible_runner_source = qos_incompatible_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_qos_incompatible_event_probe.v1",
            qos_incompatible_runner_source,
        )
        self.assertIn(
            "local_same_process_reliability_and_durability_mismatch",
            qos_incompatible_runner_source,
        )
        self.assertIn("RMW_QOS_POLICY_DURABILITY", qos_incompatible_runner_source)
        self.assertIn("durability_offered_last_policy_kind", qos_incompatible_runner_source)
        self.assertIn("durability_requested_last_policy_kind", qos_incompatible_runner_source)
        self.assertIn("incompatible_endpoint_matched_wait_ready", qos_incompatible_runner_source)
        self.assertIn("--iterations", qos_incompatible_runner_source)
        self.assertIn(
            "qos_incompatible_repeated_event_claim",
            qos_incompatible_runner_source,
        )
        qos_deadline_incompatible_probe = (
            PKG / "src" / "qos_deadline_incompatible_event_probe.cpp"
        )
        self.assertTrue(qos_deadline_incompatible_probe.exists())
        qos_deadline_incompatible_probe_source = qos_deadline_incompatible_probe.read_text()
        self.assertIn(
            "fleetrmw.qos_deadline_incompatible_event_probe.v1",
            qos_deadline_incompatible_probe_source,
        )
        self.assertIn("RMW_QOS_POLICY_DEADLINE", qos_deadline_incompatible_probe_source)
        self.assertIn("local_same_process_deadline_mismatch", qos_deadline_incompatible_probe_source)
        self.assertIn(
            "missing_offered_deadline_offered_event_claim",
            qos_deadline_incompatible_probe_source,
        )
        qos_deadline_incompatible_runner = (
            ROOT / "scripts" / "run_rmw_docker_qos_deadline_incompatible_event_probe.py"
        )
        self.assertTrue(qos_deadline_incompatible_runner.exists())
        qos_deadline_incompatible_runner_source = qos_deadline_incompatible_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_qos_deadline_incompatible_event_probe.v1",
            qos_deadline_incompatible_runner_source,
        )
        self.assertIn("RMW_QOS_POLICY_DEADLINE", qos_deadline_incompatible_runner_source)
        self.assertIn(
            "local_same_process_deadline_mismatch",
            qos_deadline_incompatible_runner_source,
        )
        self.assertIn("--iterations", qos_deadline_incompatible_runner_source)
        self.assertIn(
            "qos_deadline_incompatible_repeated_event_claim",
            qos_deadline_incompatible_runner_source,
        )
        self.assertIn(
            "qos_missing_offered_deadline_incompatible_repeated_claim",
            qos_deadline_incompatible_runner_source,
        )
        type_incompatible_probe = PKG / "src" / "type_incompatible_event_probe.cpp"
        self.assertTrue(type_incompatible_probe.exists())
        type_incompatible_probe_source = type_incompatible_probe.read_text()
        self.assertIn("fleetrmw.type_incompatible_event_probe.v1", type_incompatible_probe_source)
        self.assertIn("RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE", type_incompatible_probe_source)
        self.assertIn("RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE", type_incompatible_probe_source)
        self.assertIn("RMW_EVENT_PUBLICATION_MATCHED", type_incompatible_probe_source)
        self.assertIn("RMW_EVENT_SUBSCRIPTION_MATCHED", type_incompatible_probe_source)
        self.assertIn("mismatched_endpoint_matched_taken", type_incompatible_probe_source)
        self.assertIn("local_same_process_same_topic_type_mismatch", type_incompatible_probe_source)
        type_incompatible_runner = (
            ROOT / "scripts" / "run_rmw_docker_type_incompatible_event_probe.py"
        )
        self.assertTrue(type_incompatible_runner.exists())
        type_incompatible_runner_source = type_incompatible_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_type_incompatible_event_probe.v1",
            type_incompatible_runner_source,
        )
        self.assertIn(
            "local_same_process_same_topic_type_mismatch",
            type_incompatible_runner_source,
        )
        self.assertIn("mismatched_endpoint_matched_wait_ready", type_incompatible_runner_source)
        self.assertIn("--iterations", type_incompatible_runner_source)
        self.assertIn(
            "type_incompatible_repeated_event_claim",
            type_incompatible_runner_source,
        )
        message_lost_probe = PKG / "src" / "message_lost_event_probe.cpp"
        self.assertTrue(message_lost_probe.exists())
        message_lost_probe_source = message_lost_probe.read_text()
        self.assertIn("fleetrmw.message_lost_event_probe.v1", message_lost_probe_source)
        self.assertIn("RMW_EVENT_MESSAGE_LOST", message_lost_probe_source)
        self.assertIn("best_effort_gap_detected", message_lost_probe_source)
        self.assertIn("repair_suppressed_false_message_lost", message_lost_probe_source)
        self.assertIn("reliable_history_exhaustion_detected", message_lost_probe_source)
        self.assertIn("unrecoverable_loss_notices_sent", message_lost_probe_source)
        self.assertIn("FLEETQOX_RMW_DROP_SOURCE_SEQUENCES", message_lost_probe_source)
        message_lost_runner = (
            ROOT / "scripts" / "run_rmw_docker_message_lost_event_probe.py"
        )
        self.assertTrue(message_lost_runner.exists())
        message_lost_runner_source = message_lost_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_message_lost_event_probe.v1",
            message_lost_runner_source,
        )
        self.assertIn(
            "reliable_history_exhaustion",
            message_lost_runner_source,
        )
        self.assertIn("--iterations", message_lost_runner_source)
        self.assertIn(
            "message_lost_repeated_event_claim",
            message_lost_runner_source,
        )
        message_lost_interprocess_probe = (
            PKG / "src" / "message_lost_interprocess_probe.cpp"
        )
        self.assertTrue(message_lost_interprocess_probe.exists())
        message_lost_interprocess_probe_source = (
            message_lost_interprocess_probe.read_text()
        )
        self.assertIn(
            "fleetrmw.message_lost_interprocess_probe.v1",
            message_lost_interprocess_probe_source,
        )
        self.assertIn("RMW_EVENT_MESSAGE_LOST", message_lost_interprocess_probe_source)
        self.assertIn(
            "rmw_fleetqox_cpp_socket_unrecoverable_loss_notices_received",
            message_lost_interprocess_probe_source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_unrecoverable_loss_samples_reported",
            message_lost_interprocess_probe_source,
        )
        self.assertIn("--terminal-repair-mode", message_lost_interprocess_probe_source)
        self.assertIn("budget_exhaustion", message_lost_interprocess_probe_source)
        self.assertIn("attempt_limit", message_lost_interprocess_probe_source)
        self.assertIn("admission_rejection", message_lost_interprocess_probe_source)
        self.assertIn(
            "rmw_fleetqox_cpp_socket_repair_budget_exhausted",
            message_lost_interprocess_probe_source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_repair_sequence_attempt_limit_exhausted",
            message_lost_interprocess_probe_source,
        )
        self.assertIn(
            "rmw_fleetqox_cpp_socket_repair_not_admitted",
            message_lost_interprocess_probe_source,
        )
        message_lost_interprocess_runner = (
            ROOT / "scripts" / "run_rmw_docker_message_lost_interprocess_probe.py"
        )
        self.assertTrue(message_lost_interprocess_runner.exists())
        message_lost_interprocess_runner_source = (
            message_lost_interprocess_runner.read_text()
        )
        self.assertIn(
            "fleetrmw.docker_message_lost_interprocess_probe.v1",
            message_lost_interprocess_runner_source,
        )
        self.assertIn("tc qdisc replace dev eth0 root netem", message_lost_interprocess_runner_source)
        self.assertIn("--iterations", message_lost_interprocess_runner_source)
        self.assertIn(
            "remote_unrecoverable_loss_notice_claim",
            message_lost_interprocess_runner_source,
        )
        self.assertIn(
            "duplicate_unrecoverable_loss_notice_deduplication_claim",
            message_lost_interprocess_runner_source,
        )
        self.assertIn(
            "repeated_remote_message_lost_claim",
            message_lost_interprocess_runner_source,
        )
        self.assertIn(
            "FLEETQOX_RMW_DROP_SOURCE_SEQUENCE_SEND_COUNT",
            message_lost_interprocess_runner_source,
        )
        self.assertIn(
            "FLEETQOX_RMW_REPAIR_RETRANSMISSION_BUDGET",
            message_lost_interprocess_runner_source,
        )
        self.assertIn(
            "FLEETQOX_RMW_REPAIR_MAX_ATTEMPTS_PER_SEQUENCE",
            message_lost_interprocess_runner_source,
        )
        self.assertIn(
            "FLEETQOX_RMW_REPAIR_ADMISSION_STRICT",
            message_lost_interprocess_runner_source,
        )
        message_lost_terminal_runner = (
            ROOT / "scripts" / "run_rmw_docker_message_lost_terminal_repair_probe.py"
        )
        self.assertTrue(message_lost_terminal_runner.exists())
        message_lost_terminal_runner_source = message_lost_terminal_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_message_lost_terminal_repair_probe.v1",
            message_lost_terminal_runner_source,
        )
        self.assertIn("--iterations", message_lost_terminal_runner_source)
        self.assertIn(
            "repair_budget_terminal_loss_notice_claim",
            message_lost_terminal_runner_source,
        )
        self.assertIn(
            "repair_attempt_limit_terminal_loss_notice_claim",
            message_lost_terminal_runner_source,
        )
        self.assertIn(
            "repair_admission_terminal_loss_notice_claim",
            message_lost_terminal_runner_source,
        )
        self.assertIn(
            "terminal_repair_controls_repeated_claim",
            message_lost_terminal_runner_source,
        )
        liveliness_probe = PKG / "src" / "liveliness_event_probe.cpp"
        self.assertTrue(liveliness_probe.exists())
        liveliness_probe_source = liveliness_probe.read_text()
        self.assertIn("fleetrmw.liveliness_event_probe.v1", liveliness_probe_source)
        self.assertIn("RMW_EVENT_LIVELINESS_LOST", liveliness_probe_source)
        self.assertIn("RMW_EVENT_LIVELINESS_CHANGED", liveliness_probe_source)
        self.assertIn("rmw_publisher_assert_liveliness", liveliness_probe_source)
        self.assertIn(
            "local_same_process_finite_lease_timeout_and_reassert",
            liveliness_probe_source,
        )
        liveliness_runner = (
            ROOT / "scripts" / "run_rmw_docker_liveliness_event_probe.py"
        )
        self.assertTrue(liveliness_runner.exists())
        liveliness_runner_source = liveliness_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_liveliness_event_probe.v1",
            liveliness_runner_source,
        )
        self.assertIn(
            "local_same_process_finite_lease_timeout_and_reassert",
            liveliness_runner_source,
        )
        self.assertIn("--iterations", liveliness_runner_source)
        self.assertIn("liveliness_repeated_event_claim", liveliness_runner_source)
        automatic_liveliness_probe = PKG / "src" / "automatic_liveliness_probe.cpp"
        self.assertTrue(automatic_liveliness_probe.exists())
        automatic_liveliness_probe_source = automatic_liveliness_probe.read_text()
        self.assertIn(
            "fleetrmw.automatic_liveliness_probe.v1",
            automatic_liveliness_probe_source,
        )
        self.assertIn(
            "RMW_QOS_POLICY_LIVELINESS_AUTOMATIC",
            automatic_liveliness_probe_source,
        )
        self.assertIn("idle_lease_multiples", automatic_liveliness_probe_source)
        self.assertIn("idle_lost_wait_ready", automatic_liveliness_probe_source)
        automatic_liveliness_runner = (
            ROOT / "scripts" / "run_rmw_docker_automatic_liveliness_probe.py"
        )
        self.assertTrue(automatic_liveliness_runner.exists())
        automatic_liveliness_runner_source = automatic_liveliness_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_automatic_liveliness_probe.v1",
            automatic_liveliness_runner_source,
        )
        self.assertIn("--iterations", automatic_liveliness_runner_source)
        self.assertIn(
            "automatic_liveliness_idle_renewal_claim",
            automatic_liveliness_runner_source,
        )
        self.assertIn(
            "automatic_liveliness_false_loss_suppression_claim",
            automatic_liveliness_runner_source,
        )
        self.assertIn(
            "automatic_liveliness_repeated_claim",
            automatic_liveliness_runner_source,
        )
        remote_manual_liveliness_probe = (
            PKG / "src" / "remote_manual_liveliness_probe.cpp"
        )
        self.assertTrue(remote_manual_liveliness_probe.exists())
        remote_manual_liveliness_probe_source = (
            remote_manual_liveliness_probe.read_text()
        )
        self.assertIn(
            "fleetrmw.remote_manual_liveliness_probe.v1",
            remote_manual_liveliness_probe_source,
        )
        self.assertIn("RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC", remote_manual_liveliness_probe_source)
        self.assertIn("graph_lease_independent_of_liveliness_lease", remote_manual_liveliness_probe_source)
        self.assertIn("rmw_publisher_assert_liveliness", remote_manual_liveliness_probe_source)
        self.assertIn("RMW_EVENT_LIVELINESS_LOST", remote_manual_liveliness_probe_source)
        self.assertIn("remote_publisher_liveliness_lost_event_claim", remote_manual_liveliness_probe_source)
        remote_manual_liveliness_runner = (
            ROOT / "scripts" / "run_rmw_docker_remote_manual_liveliness_probe.py"
        )
        self.assertTrue(remote_manual_liveliness_runner.exists())
        remote_manual_liveliness_runner_source = (
            remote_manual_liveliness_runner.read_text()
        )
        self.assertIn(
            "fleetrmw.docker_remote_manual_liveliness_probe.v1",
            remote_manual_liveliness_runner_source,
        )
        self.assertIn("tc qdisc replace", remote_manual_liveliness_runner_source)
        self.assertIn("remote_manual_liveliness_repeated_claim", remote_manual_liveliness_runner_source)
        self.assertIn("remote_publisher_liveliness_lost_event_claim", remote_manual_liveliness_runner_source)
        remote_event_coverage = (
            ROOT / "scripts" / "summarize_remote_qos_event_coverage.py"
        )
        self.assertTrue(remote_event_coverage.exists())
        remote_event_coverage_source = remote_event_coverage.read_text()
        self.assertIn(
            "fleetrmw.remote_qos_event_coverage.v1",
            remote_event_coverage_source,
        )
        self.assertIn("EVENT_TYPES", remote_event_coverage_source)
        self.assertIn(
            "remote_all_jazzy_event_types_path_claim",
            remote_event_coverage_source,
        )
        remote_liveliness_multi_probe = (
            PKG / "src" / "remote_liveliness_multi_endpoint_probe.cpp"
        )
        self.assertTrue(remote_liveliness_multi_probe.exists())
        remote_liveliness_multi_probe_source = (
            remote_liveliness_multi_probe.read_text()
        )
        self.assertIn(
            "fleetrmw.remote_liveliness_multi_endpoint_probe.v1",
            remote_liveliness_multi_probe_source,
        )
        self.assertIn("publisher_two_reasserted", remote_liveliness_multi_probe_source)
        self.assertIn("publishers_during_single_endpoint_expiry", remote_liveliness_multi_probe_source)
        self.assertIn("endpoint_churn_recreate_claim", remote_liveliness_multi_probe_source)
        remote_liveliness_multi_runner = (
            ROOT
            / "scripts"
            / "run_rmw_docker_remote_liveliness_multi_endpoint_probe.py"
        )
        self.assertTrue(remote_liveliness_multi_runner.exists())
        remote_liveliness_multi_runner_source = (
            remote_liveliness_multi_runner.read_text()
        )
        self.assertIn(
            "fleetrmw.docker_remote_liveliness_multi_endpoint_probe.v1",
            remote_liveliness_multi_runner_source,
        )
        self.assertIn("tc qdisc replace", remote_liveliness_multi_runner_source)
        self.assertIn(
            "remote_liveliness_multi_endpoint_repeated_claim",
            remote_liveliness_multi_runner_source,
        )
        liveliness_scale_probe = PKG / "src" / "liveliness_scale_probe.cpp"
        self.assertTrue(liveliness_scale_probe.exists())
        liveliness_scale_probe_source = liveliness_scale_probe.read_text()
        self.assertIn(
            "fleetrmw.liveliness_scale_probe.v1",
            liveliness_scale_probe_source,
        )
        self.assertIn("kManualPublisherCount = 64", liveliness_scale_probe_source)
        self.assertIn(
            "system_default_automatic_renewal_claim",
            liveliness_scale_probe_source,
        )
        liveliness_scale_runner = (
            ROOT / "scripts" / "run_rmw_docker_liveliness_scale_probe.py"
        )
        self.assertTrue(liveliness_scale_runner.exists())
        liveliness_scale_runner_source = liveliness_scale_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_liveliness_scale_probe.v1",
            liveliness_scale_runner_source,
        )
        self.assertIn("liveliness_scale_repeated_claim", liveliness_scale_runner_source)
        remote_liveliness_scale_probe = (
            PKG / "src" / "remote_liveliness_scale_probe.cpp"
        )
        self.assertTrue(remote_liveliness_scale_probe.exists())
        remote_liveliness_scale_probe_source = (
            remote_liveliness_scale_probe.read_text()
        )
        self.assertIn(
            "fleetrmw.remote_liveliness_scale_probe.v1",
            remote_liveliness_scale_probe_source,
        )
        self.assertIn("kPublisherCount = 64", remote_liveliness_scale_probe_source)
        self.assertIn(
            "remote_manual_64_endpoint_scale_claim",
            remote_liveliness_scale_probe_source,
        )
        remote_liveliness_scale_runner = (
            ROOT / "scripts" / "run_rmw_docker_remote_liveliness_scale_probe.py"
        )
        self.assertTrue(remote_liveliness_scale_runner.exists())
        remote_liveliness_scale_runner_source = (
            remote_liveliness_scale_runner.read_text()
        )
        self.assertIn(
            "fleetrmw.docker_remote_liveliness_scale_probe.v1",
            remote_liveliness_scale_runner_source,
        )
        self.assertIn("tc qdisc replace", remote_liveliness_scale_runner_source)
        self.assertIn(
            "remote_liveliness_scale_repeated_claim",
            remote_liveliness_scale_runner_source,
        )
        liveliness_default_lease_probe = (
            PKG / "src" / "liveliness_default_lease_probe.cpp"
        )
        self.assertTrue(liveliness_default_lease_probe.exists())
        liveliness_default_lease_probe_source = (
            liveliness_default_lease_probe.read_text()
        )
        self.assertIn(
            "fleetrmw.liveliness_default_lease_probe.v1",
            liveliness_default_lease_probe_source,
        )
        self.assertIn(
            "unknown_liveliness_fail_closed_claim",
            liveliness_default_lease_probe_source,
        )
        self.assertIn(
            "deprecated_manual_by_node_fail_closed_claim",
            liveliness_default_lease_probe_source,
        )
        liveliness_default_lease_runner = (
            ROOT / "scripts" / "run_rmw_docker_liveliness_default_lease_probe.py"
        )
        self.assertTrue(liveliness_default_lease_runner.exists())
        liveliness_default_lease_runner_source = (
            liveliness_default_lease_runner.read_text()
        )
        self.assertIn(
            "fleetrmw.docker_liveliness_default_lease_probe.v1",
            liveliness_default_lease_runner_source,
        )
        self.assertIn(
            "liveliness_default_lease_repeated_claim",
            liveliness_default_lease_runner_source,
        )
        liveliness_incompatible_probe = (
            PKG / "src" / "qos_liveliness_incompatible_event_probe.cpp"
        )
        self.assertTrue(liveliness_incompatible_probe.exists())
        liveliness_incompatible_probe_source = (
            liveliness_incompatible_probe.read_text()
        )
        self.assertIn(
            "fleetrmw.qos_liveliness_incompatible_event_probe.v1",
            liveliness_incompatible_probe_source,
        )
        self.assertIn("liveliness_kind_offered_event_claim", liveliness_incompatible_probe_source)
        self.assertIn("liveliness_missing_lease_requested_event_claim", liveliness_incompatible_probe_source)
        self.assertIn("RMW_QOS_POLICY_LIVELINESS", liveliness_incompatible_probe_source)
        liveliness_incompatible_runner = (
            ROOT
            / "scripts"
            / "run_rmw_docker_qos_liveliness_incompatible_event_probe.py"
        )
        self.assertTrue(liveliness_incompatible_runner.exists())
        liveliness_incompatible_runner_source = (
            liveliness_incompatible_runner.read_text()
        )
        self.assertIn(
            "fleetrmw.docker_qos_liveliness_incompatible_event_probe.v1",
            liveliness_incompatible_runner_source,
        )
        self.assertIn(
            "qos_liveliness_incompatible_event_repeated_claim",
            liveliness_incompatible_runner_source,
        )
        best_available_probe = PKG / "src" / "qos_best_available_probe.cpp"
        self.assertTrue(best_available_probe.exists())
        best_available_probe_source = best_available_probe.read_text()
        self.assertIn(
            "fleetrmw.qos_best_available_probe.v1", best_available_probe_source
        )
        self.assertIn(
            "best_publisher_manual_selection_claim", best_available_probe_source
        )
        self.assertIn(
            "mixed_publishers_automatic_max_lease_claim",
            best_available_probe_source,
        )
        self.assertIn(
            "best_available_policy_frozen_after_create_claim",
            best_available_probe_source,
        )
        best_available_runner = (
            ROOT / "scripts" / "run_rmw_docker_qos_best_available_probe.py"
        )
        self.assertTrue(best_available_runner.exists())
        best_available_runner_source = best_available_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_qos_best_available_probe.v1",
            best_available_runner_source,
        )
        self.assertIn(
            "qos_best_available_endpoint_adaptation_repeated_claim",
            best_available_runner_source,
        )
        content_filter_probe = PKG / "src" / "content_filter_probe.cpp"
        self.assertTrue(content_filter_probe.exists())
        content_filter_probe_source = content_filter_probe.read_text()
        self.assertIn("fleetrmw.content_filter_probe.v1", content_filter_probe_source)
        self.assertIn("rmw_subscription_set_content_filter", content_filter_probe_source)
        self.assertIn("content_filters_dropped_delta", content_filter_probe_source)
        self.assertIn("std_msgs_content_filters_dropped_delta", content_filter_probe_source)
        self.assertIn("disabled_content_filter_bypass", content_filter_probe_source)
        self.assertIn("robot_id != %0 AND sequence >= %1 AND sequence <= %2", content_filter_probe_source)
        self.assertIn("content_filter_enforcement", content_filter_probe_source)
        content_filter_runner = ROOT / "scripts" / "run_rmw_docker_content_filter_probe.py"
        self.assertTrue(content_filter_runner.exists())
        content_filter_runner_source = content_filter_runner.read_text()
        self.assertIn("fleetrmw.docker_content_filter_probe.v1", content_filter_runner_source)
        self.assertIn("--iterations", content_filter_runner_source)
        self.assertIn("content_filter_set_get_abi_supported", content_filter_runner_source)
        self.assertIn("key_value_payload_and_std_msgs_string_text", content_filter_runner_source)
        self.assertIn("std_msgs_content_filter_enforcement", content_filter_runner_source)
        self.assertIn("disabled_content_filter_bypass", content_filter_runner_source)
        self.assertIn("content_filter_repeated_enforcement_claim", content_filter_runner_source)
        content_filter_sql_probe = PKG / "src" / "content_filter_sql_probe.cpp"
        self.assertTrue(content_filter_sql_probe.exists())
        content_filter_sql_probe_source = content_filter_sql_probe.read_text()
        self.assertIn(
            "fleetrmw.content_filter_sql_probe.v1",
            content_filter_sql_probe_source,
        )
        self.assertIn("BETWEEN", content_filter_sql_probe_source)
        self.assertIn("NOT IN", content_filter_sql_probe_source)
        self.assertIn("IS NULL", content_filter_sql_probe_source)
        self.assertIn("IS NOT NULL", content_filter_sql_probe_source)
        self.assertIn("priority <> %6", content_filter_sql_probe_source)
        self.assertIn("invalid_expression_fail_closed", content_filter_sql_probe_source)
        content_filter_sql_runner = (
            ROOT / "scripts" / "run_rmw_docker_content_filter_sql_probe.py"
        )
        self.assertTrue(content_filter_sql_runner.exists())
        content_filter_sql_runner_source = content_filter_sql_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_content_filter_sql_probe.v1",
            content_filter_sql_runner_source,
        )
        self.assertIn(
            "content_filter_sql_subset_repeated_claim",
            content_filter_sql_runner_source,
        )
        self.assertIn(
            "sql_reversed_comparison_operand_order_claim",
            content_filter_sql_runner_source,
        )
        self.assertIn("%0 = robot_id AND %1 < sequence", content_filter_sql_probe_source)
        self.assertIn("reversed_missing_field_rejected", content_filter_sql_probe_source)
        rmw_pubsub_source = (PKG / "src" / "rmw_pubsub.cpp").read_text()
        self.assertIn("parse_reversed_comparison_predicate", rmw_pubsub_source)
        self.assertIn("reverse_comparison", rmw_pubsub_source)
        content_filter_typed_probe = (
            PKG / "src" / "content_filter_typed_probe.cpp"
        )
        self.assertTrue(content_filter_typed_probe.exists())
        content_filter_typed_probe_source = content_filter_typed_probe.read_text()
        self.assertIn(
            "fleetrmw.content_filter_typed_probe.v1",
            content_filter_typed_probe_source,
        )
        self.assertIn("linear.x >= %0", content_filter_typed_probe_source)
        self.assertIn("position.x BETWEEN %0", content_filter_typed_probe_source)
        self.assertIn("data._length = %0", content_filter_typed_probe_source)
        self.assertIn("layout.dim[0].label", content_filter_typed_probe_source)
        self.assertIn("typed_reflections", content_filter_typed_probe_source)
        self.assertIn(
            "malformed_typed_payload_fail_closed",
            content_filter_typed_probe_source,
        )
        content_filter_typed_runner = (
            ROOT / "scripts" / "run_rmw_docker_content_filter_typed_probe.py"
        )
        self.assertTrue(content_filter_typed_runner.exists())
        content_filter_typed_runner_source = content_filter_typed_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_content_filter_typed_probe.v1",
            content_filter_typed_runner_source,
        )
        self.assertIn(
            "content_filter_introspection_cpp_nested_fields_claim",
            content_filter_typed_runner_source,
        )
        self.assertIn(
            "content_filter_introspection_c_nested_fields_claim",
            content_filter_typed_runner_source,
        )
        self.assertIn(
            "content_filter_introspection_cpp_array_fields_claim",
            content_filter_typed_runner_source,
        )
        self.assertIn(
            "content_filter_malformed_typed_payload_fail_closed_claim",
            content_filter_typed_runner_source,
        )
        self.assertIn(
            "content_filter_typed_reflection_repeated_claim",
            content_filter_typed_runner_source,
        )
        wstring_content_filter_probe = (
            PKG / "src" / "wstring_content_filter_probe.cpp"
        )
        self.assertTrue(wstring_content_filter_probe.exists())
        wstring_content_filter_probe_source = wstring_content_filter_probe.read_text()
        self.assertIn(
            "fleetrmw.wstring_content_filter_probe.v1",
            wstring_content_filter_probe_source,
        )
        self.assertIn("surrogate_pair_preserved", wstring_content_filter_probe_source)
        self.assertIn("label = %0", wstring_content_filter_probe_source)
        wstring_content_filter_runner = (
            ROOT / "scripts" / "run_rmw_docker_wstring_content_filter_probe.py"
        )
        self.assertTrue(wstring_content_filter_runner.exists())
        wstring_content_filter_runner_source = wstring_content_filter_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_wstring_content_filter_probe.v1",
            wstring_content_filter_runner_source,
        )
        self.assertIn(
            "content_filter_wstring_field_claim",
            wstring_content_filter_runner_source,
        )
        keyed_instance_retransmit_probe = (
            PKG / "src" / "keyed_instance_retransmit_probe.cpp"
        )
        self.assertTrue(keyed_instance_retransmit_probe.exists())
        keyed_instance_retransmit_probe_source = (
            keyed_instance_retransmit_probe.read_text()
        )
        self.assertIn(
            "fleetrmw.keyed_instance_retransmit_probe.v1",
            keyed_instance_retransmit_probe_source,
        )
        self.assertIn(
            "per_instance_bounding_observed", keyed_instance_retransmit_probe_source
        )
        self.assertIn(
            "keyless_behavior_unchanged", keyed_instance_retransmit_probe_source
        )
        keyed_instance_idl = PKG.parent / "fleetrmw_interfaces" / "msg" / "KeyedInstanceSample.idl"
        self.assertTrue(keyed_instance_idl.exists())
        self.assertIn("@key", keyed_instance_idl.read_text())
        keyed_instance_retransmit_runner = (
            ROOT / "scripts" / "run_rmw_docker_keyed_instance_retransmit_probe.py"
        )
        self.assertTrue(keyed_instance_retransmit_runner.exists())
        keyed_instance_retransmit_runner_source = (
            keyed_instance_retransmit_runner.read_text()
        )
        self.assertIn(
            "fleetrmw.docker_keyed_instance_retransmit_probe.v1",
            keyed_instance_retransmit_runner_source,
        )
        self.assertIn(
            "reliability_key_instance_bound_claim",
            keyed_instance_retransmit_runner_source,
        )
        loan_runner = ROOT / "scripts" / "run_rmw_docker_loaned_message_probe.py"
        self.assertTrue(loan_runner.exists())
        self.assertIn("zero_copy_claim_allowed", loan_runner.read_text())
        quic_dependency_probe = PKG / "src" / "quic_dependency_probe.cpp"
        self.assertTrue(quic_dependency_probe.exists())
        quic_dependency_source = quic_dependency_probe.read_text()
        self.assertIn("fleetrmw.quic_dependency_probe.v1", quic_dependency_source)
        self.assertIn("ngtcp2_is_supported_version", quic_dependency_source)
        self.assertIn("ngtcp2_crypto_gnutls_from_ngtcp2_level", quic_dependency_source)
        quic_runner = ROOT / "scripts" / "run_rmw_docker_quic_tls_probe.py"
        self.assertTrue(quic_runner.exists())
        quic_runner_source = quic_runner.read_text()
        self.assertIn("fleetrmw.docker_quic_tls_probe.v1", quic_runner_source)
        self.assertIn("gtlsclient", quic_runner_source)
        self.assertIn("gtlsserver", quic_runner_source)
        self.assertIn("rmw_integrated_backend", quic_runner_source)
        quic_frame_runner = ROOT / "scripts" / "run_rmw_docker_quic_fleet_frame_probe.py"
        self.assertTrue(quic_frame_runner.exists())
        quic_frame_runner_source = quic_frame_runner.read_text()
        self.assertIn("fleetrmw.docker_quic_fleet_frame_probe.v1", quic_frame_runner_source)
        self.assertIn("fleetrmw.data_frame.v1", quic_frame_runner_source)
        self.assertIn("fleetrmw_frame_probe", quic_frame_runner_source)
        self.assertIn("rmw_integrated_backend", quic_frame_runner_source)
        quic_gateway_header = PKG / "include" / "rmw_fleetqox_cpp" / "quic_gateway_transport.hpp"
        quic_gateway_source = PKG / "src" / "quic_gateway_transport.cpp"
        quic_gateway_probe = PKG / "src" / "quic_gateway_publish_probe.cpp"
        quic_gateway_burst_probe = PKG / "src" / "quic_gateway_burst_publish_probe.cpp"
        quic_gateway_take_probe = PKG / "src" / "quic_gateway_take_probe.cpp"
        quic_gateway_rmw_take_probe = PKG / "src" / "quic_gateway_rmw_take_probe.cpp"
        self.assertTrue(quic_gateway_header.exists())
        self.assertTrue(quic_gateway_source.exists())
        self.assertTrue(quic_gateway_probe.exists())
        self.assertTrue(quic_gateway_burst_probe.exists())
        self.assertTrue(quic_gateway_take_probe.exists())
        self.assertTrue(quic_gateway_rmw_take_probe.exists())
        self.assertIn("FLEETQOX_RMW_QUIC_GATEWAY", quic_gateway_source.read_text())
        self.assertIn("FLEETQOX_RMW_QUIC_GATEWAY_ASYNC", quic_gateway_source.read_text())
        self.assertIn("frames_enqueued", quic_gateway_source.read_text())
        self.assertIn("frames_received", quic_gateway_source.read_text())
        self.assertIn("run_download_client", quic_gateway_source.read_text())
        self.assertIn("--download", quic_gateway_source.read_text())
        self.assertIn("FLEETQOX_RMW_QUIC_SESSION_FILE", quic_gateway_source.read_text())
        self.assertIn("--session-file=", quic_gateway_source.read_text())
        self.assertIn("--tp-file=", quic_gateway_source.read_text())
        self.assertIn("std::condition_variable", quic_gateway_header.read_text())
        self.assertIn("execvp", quic_gateway_source.read_text())
        self.assertIn("fleetrmw.quic_gateway_publish_probe.v1", quic_gateway_probe.read_text())
        self.assertIn("publish_returned_after_enqueue", quic_gateway_probe.read_text())
        self.assertIn(
            "fleetrmw.quic_gateway_burst_publish_probe.v1",
            quic_gateway_burst_probe.read_text(),
        )
        self.assertIn("FLEETQOX_RMW_QUIC_GATEWAY_BURST_COUNT", quic_gateway_burst_probe.read_text())
        self.assertIn("fleetrmw.quic_gateway_take_probe.v1", quic_gateway_take_probe.read_text())
        self.assertIn("rmw_take_path_integrated", quic_gateway_take_probe.read_text())
        self.assertIn("quic_gateway_take_path_download", quic_gateway_take_probe.read_text())
        rmw_pubsub_source = (PKG / "src" / "rmw_pubsub.cpp").read_text()
        self.assertIn("FLEETQOX_RMW_QUIC_GATEWAY_TAKE_ON_DEMAND", rmw_pubsub_source)
        self.assertIn("receive_quic_gateway_payload", rmw_pubsub_source)
        self.assertIn(
            "fleetrmw.quic_gateway_rmw_take_probe.v1",
            quic_gateway_rmw_take_probe.read_text(),
        )
        self.assertIn("rmw_take_serialized_message", quic_gateway_rmw_take_probe.read_text())
        self.assertIn("rmw_take_path_integrated", quic_gateway_rmw_take_probe.read_text())
        self.assertIn("quic_gateway_frames_received", quic_gateway_rmw_take_probe.read_text())
        inprocess_header = (
            PKG / "include" / "rmw_fleetqox_cpp" / "inprocess_quic_client.hpp"
        )
        inprocess_source = PKG / "src" / "inprocess_quic_client.cpp"
        inprocess_rmw_probe = (
            PKG / "src" / "quic_inprocess_rmw_bidirectional_probe.cpp"
        )
        inprocess_concurrent_probe = (
            PKG / "src" / "quic_inprocess_concurrent_stream_probe.cpp"
        )
        inprocess_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_inprocess_bidirectional_probe.py"
        )
        self.assertTrue(inprocess_header.exists())
        self.assertTrue(inprocess_source.exists())
        self.assertTrue(inprocess_rmw_probe.exists())
        self.assertTrue(inprocess_concurrent_probe.exists())
        self.assertTrue(inprocess_runner.exists())
        self.assertIn("nghttp3_conn_client_new", inprocess_source.read_text())
        self.assertIn("gnutls_certificate_verify_peers3", inprocess_source.read_text())
        self.assertIn("settings.qlog.write = qlog_write_cb", inprocess_source.read_text())
        self.assertIn("fleetrmw.quic_inprocess_rmw_bidirectional_probe.v1", inprocess_rmw_probe.read_text())
        self.assertIn("same_connection_bidirectional", inprocess_rmw_probe.read_text())
        self.assertIn(
            "fleetrmw.quic_inprocess_concurrent_stream_probe.v1",
            inprocess_concurrent_probe.read_text(),
        )
        self.assertIn("send_and_receive", inprocess_concurrent_probe.read_text())
        self.assertIn("concurrent_stream_pairs", inprocess_source.read_text())
        self.assertIn("exchange_coordinated", inprocess_source.read_text())
        self.assertIn("concurrent_api_operation_pairs", inprocess_rmw_probe.read_text())
        self.assertIn("multi_threaded_rmw_api_claim", inprocess_rmw_probe.read_text())
        self.assertIn("negative_untrusted_ca", inprocess_runner.read_text())
        self.assertIn("FLEETQOX_RMW_QUIC_QLOG_DIR", inprocess_runner.read_text())
        self.assertIn("client_qlog_file_count", inprocess_runner.read_text())
        quic_gateway_runner = ROOT / "scripts" / "run_rmw_docker_quic_gateway_publish_probe.py"
        self.assertTrue(quic_gateway_runner.exists())
        quic_gateway_runner_source = quic_gateway_runner.read_text()
        self.assertIn("fleetrmw.docker_quic_gateway_publish_probe.v1", quic_gateway_runner_source)
        self.assertIn("server_payload_matches_rmw_frame_bytes", quic_gateway_runner_source)
        self.assertIn("async_worker_queue_observed", quic_gateway_runner_source)
        self.assertIn("full_bidirectional_quic_backend", quic_gateway_runner_source)
        self.assertIn("parse_quic_session_reuse_telemetry", quic_gateway_runner_source)
        self.assertIn("zero_rtt_packet_observed", quic_gateway_runner_source)
        quic_gateway_async_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_gateway_async_publish_probe.py"
        )
        self.assertTrue(quic_gateway_async_runner.exists())
        quic_gateway_async_source = quic_gateway_async_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_quic_gateway_async_publish_probe.v1",
            quic_gateway_async_source,
        )
        self.assertIn("async_gateway=True", quic_gateway_async_source)
        quic_gateway_async_burst_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_gateway_async_burst_probe.py"
        )
        self.assertTrue(quic_gateway_async_burst_runner.exists())
        quic_gateway_async_burst_source = quic_gateway_async_burst_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_quic_gateway_async_burst_probe.v1",
            quic_gateway_async_burst_source,
        )
        self.assertIn(
            "fleetrmw_quic_gateway_burst_publish_probe",
            quic_gateway_async_burst_source,
        )
        quic_gateway_netem_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_gateway_netem_publish_probe.py"
        )
        self.assertTrue(quic_gateway_netem_runner.exists())
        quic_gateway_netem_source = quic_gateway_netem_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_quic_gateway_netem_publish_probe.v1",
            quic_gateway_netem_source,
        )
        self.assertIn("FLEETQOX_RMW_QUIC_GATEWAY", quic_gateway_netem_source)
        self.assertIn("tc qdisc replace dev eth0 root netem", quic_gateway_netem_source)
        self.assertIn("path_telemetry", quic_gateway_netem_source)
        self.assertIn("async_worker_queue_observed", quic_gateway_netem_source)
        self.assertIn("server_payload_matches_rmw_frame_bytes", quic_gateway_netem_source)
        quic_gateway_netem_async_burst_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_gateway_netem_async_burst_probe.py"
        )
        self.assertTrue(quic_gateway_netem_async_burst_runner.exists())
        quic_gateway_netem_async_burst_source = (
            quic_gateway_netem_async_burst_runner.read_text()
        )
        self.assertIn(
            "fleetrmw.docker_quic_gateway_netem_async_burst_probe.v1",
            quic_gateway_netem_async_burst_source,
        )
        self.assertIn(
            "fleetrmw_quic_gateway_burst_publish_probe",
            quic_gateway_netem_async_burst_source,
        )
        self.assertIn("async_gateway=True", quic_gateway_netem_async_burst_source)
        quic_gateway_session_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_gateway_session_reuse_probe.py"
        )
        self.assertTrue(quic_gateway_session_runner.exists())
        quic_gateway_session_source = quic_gateway_session_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_quic_gateway_session_reuse_probe.v1",
            quic_gateway_session_source,
        )
        self.assertIn("FLEETQOX_RMW_QUIC_SESSION_FILE", quic_gateway_session_source)
        self.assertIn("session_resumption_observed", quic_gateway_session_source)
        self.assertIn("session_resumption_attempted_observed", quic_gateway_session_source)
        self.assertIn("zero_rtt_packet_observed", quic_gateway_session_source)
        self.assertIn("zero_rtt_claim", quic_gateway_session_source)
        quic_gateway_rmw_take_session_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_gateway_rmw_take_session_reuse_probe.py"
        )
        self.assertTrue(quic_gateway_rmw_take_session_runner.exists())
        quic_gateway_rmw_take_session_source = quic_gateway_rmw_take_session_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_quic_gateway_rmw_take_session_reuse_probe.v1",
            quic_gateway_rmw_take_session_source,
        )
        self.assertIn("FLEETQOX_RMW_QUIC_SESSION_FILE", quic_gateway_rmw_take_session_source)
        self.assertIn(
            "session_file_reused_by_multiple_downloads",
            quic_gateway_rmw_take_session_source,
        )
        self.assertIn("--downloads", quic_gateway_rmw_take_session_source)
        self.assertIn("requested_download_count", quic_gateway_rmw_take_session_source)
        self.assertIn("parse_quic_session_reuse_telemetry", quic_gateway_rmw_take_session_source)
        self.assertIn("zero_rtt_packet_observed", quic_gateway_rmw_take_session_source)
        self.assertIn("zero_rtt_claim", quic_gateway_rmw_take_session_source)
        quic_gateway_bidirectional_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_gateway_bidirectional_probe.py"
        )
        self.assertTrue(quic_gateway_bidirectional_runner.exists())
        quic_gateway_bidirectional_source = quic_gateway_bidirectional_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_quic_gateway_bidirectional_probe.v1",
            quic_gateway_bidirectional_source,
        )
        self.assertIn("fleetrmw_quic_gateway_publish_probe", quic_gateway_bidirectional_source)
        self.assertIn("fleetrmw_quic_gateway_rmw_take_probe", quic_gateway_bidirectional_source)
        self.assertIn("--iterations", quic_gateway_bidirectional_source)
        self.assertIn("quic_gateway_bidirectional_boundary_claim", quic_gateway_bidirectional_source)
        self.assertIn("quic_gateway_bidirectional_repeated_claim", quic_gateway_bidirectional_source)
        self.assertIn("session_file_reused_by_upload_and_download", quic_gateway_bidirectional_source)
        self.assertIn("parse_quic_session_reuse_telemetry", quic_gateway_bidirectional_source)
        self.assertIn("zero_rtt_packet_observed", quic_gateway_bidirectional_source)
        self.assertIn("--disable-early-data", quic_gateway_bidirectional_source)
        self.assertIn("zero_rtt_disabled_control_claim", quic_gateway_bidirectional_source)
        self.assertIn("full_bidirectional_quic_backend", quic_gateway_bidirectional_source)
        quic_gateway_take_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_gateway_take_probe.py"
        )
        self.assertTrue(quic_gateway_take_runner.exists())
        quic_gateway_take_source = quic_gateway_take_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_quic_gateway_take_probe.v1",
            quic_gateway_take_source,
        )
        self.assertIn("fleetrmw_quic_gateway_take_probe", quic_gateway_take_source)
        self.assertIn("quic_gateway_take_path_download", quic_gateway_take_source)
        self.assertIn("rmw_take_path_integrated", quic_gateway_take_source)
        self.assertIn("download_path_scope", quic_gateway_take_source)
        quic_gateway_rmw_take_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_gateway_rmw_take_probe.py"
        )
        self.assertTrue(quic_gateway_rmw_take_runner.exists())
        quic_gateway_rmw_take_source = quic_gateway_rmw_take_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_quic_gateway_rmw_take_probe.v1",
            quic_gateway_rmw_take_source,
        )
        self.assertIn("fleetrmw_quic_gateway_rmw_take_probe", quic_gateway_rmw_take_source)
        self.assertIn("FLEETQOX_RMW_QUIC_GATEWAY_TAKE_ON_DEMAND=1", quic_gateway_rmw_take_source)
        self.assertIn("rmw_take_path_integrated", quic_gateway_rmw_take_source)
        self.assertIn("take_path_scope", quic_gateway_rmw_take_source)
        quic_gateway_soak_runner = (
            ROOT / "scripts" / "run_rmw_docker_quic_gateway_async_burst_soak.py"
        )
        self.assertTrue(quic_gateway_soak_runner.exists())
        quic_gateway_soak_source = quic_gateway_soak_runner.read_text()
        self.assertIn(
            "fleetrmw.docker_quic_gateway_async_burst_soak.v1",
            quic_gateway_soak_source,
        )
        self.assertIn("total_quic_gateway_frames_dropped", quic_gateway_soak_source)
        self.assertIn("run_netem_probe", quic_gateway_soak_source)
        self.assertIn("--netem", quic_gateway_soak_source)
        quic_netem_runner = ROOT / "scripts" / "run_rmw_docker_quic_netem_frame_probe.py"
        self.assertTrue(quic_netem_runner.exists())
        quic_netem_source = quic_netem_runner.read_text()
        self.assertIn("fleetrmw.docker_quic_netem_frame_probe.v1", quic_netem_source)
        self.assertIn("tc qdisc replace dev eth0 root netem", quic_netem_source)
        self.assertIn("fleetrmw_frame_probe", quic_netem_source)
        self.assertIn("rmw_integrated_backend", quic_netem_source)
        self.assertIn("parse_ngtcp2_path_telemetry", quic_netem_source)
        self.assertIn("path_telemetry", quic_netem_source)
        self.assertIn("client_netem_status_after", quic_netem_source)
        dockerfile = (ROOT / "external" / "rmw-netem" / "Dockerfile").read_text()
        self.assertIn("libns3-dev", dockerfile)
        self.assertIn("libgsl-dev", dockerfile)
        self.assertIn("libgnutls28-dev", dockerfile)
        self.assertIn("ngtcp2-client", dockerfile)
        self.assertIn("ngtcp2-server", dockerfile)

        matched_script = ROOT / "scripts" / "run_rmw_docker_router_matched_multi_topic_probe.py"
        self.assertTrue(matched_script.exists())
        matched_source = matched_script.read_text()
        self.assertIn("fleetrmw.router_matched_multi_topic_probe.v1", matched_source)
        self.assertIn("publisher-router-subscriber", matched_source)
        self.assertIn("topic_specs_for_robot_count", matched_source)
        self.assertIn("--reuse-build", matched_source)
        self.assertIn("NETEM_SEED_SEMANTICS", matched_source)
        self.assertIn("--reliable-ack-timeout-ms", matched_source)
        self.assertIn("FLEETQOX_RMW_RELIABLE_ACK_TIMEOUT_MS", matched_source)
        self.assertIn("ack_timeout_retransmit", matched_source)
        self.assertIn("router_post_satisfaction_ms", matched_source)
        data_frame_header = (
            PKG / "include" / "rmw_fleetqox_cpp" / "data_frame.hpp"
        ).read_text()
        data_frame_source = (PKG / "src" / "data_frame.cpp").read_text()
        pubsub_source = (PKG / "src" / "rmw_pubsub.cpp").read_text()
        self.assertIn("lowest_observed_sequence", data_frame_header)
        self.assertIn("cumulative_ack_floor", data_frame_header)
        self.assertIn("establish_reception_sequence_baseline", data_frame_source)
        self.assertIn("ack_nack_acknowledges_sequence", data_frame_source)
        self.assertIn('"lowest_observed_sequence\\":', data_frame_source)
        self.assertIn("sequence_acknowledged", pubsub_source)

        comparison_script = ROOT / "scripts" / "run_large_scale_rmw_comparison.py"
        self.assertTrue(comparison_script.exists())
        comparison_source = comparison_script.read_text()
        self.assertIn("fleetrmw.large_scale_rmw_comparison.v2", comparison_source)
        self.assertIn("rmw_fleetqox_cpp_router", comparison_source)
        self.assertIn("rmw_fastrtps_cpp", comparison_source)
        self.assertIn("rmw_cyclonedds_cpp", comparison_source)
        self.assertIn("rmw_zenoh_cpp", comparison_source)
        self.assertIn("topology_note", comparison_source)
        self.assertIn("split_scope_topology_caveated", comparison_source)
        self.assertIn("direct_claim_allowed", comparison_source)
        self.assertIn("cross_scope_superiority", comparison_source)
        self.assertIn("run_fleetrmw(", comparison_source)
        self.assertIn("run_direct(", comparison_source)
        unified_report_script = ROOT / "scripts" / "generate_unified_benchmark_report.py"
        self.assertTrue(unified_report_script.exists())
        unified_report_source = unified_report_script.read_text()
        self.assertIn("fleetrmw.unified_benchmark_report.v1", unified_report_source)
        self.assertIn("claim_boundaries", unified_report_source)
        self.assertIn("type_incompatible_event_production", unified_report_source)
        self.assertIn("docker_qos_type_incompatible_event_production", unified_report_source)
        self.assertIn("durability_offered_last_policy_kind", unified_report_source)
        self.assertIn("docker_qos_durability_incompatible_event_production", unified_report_source)
        self.assertIn("qos_deadline_incompatible_event_production", unified_report_source)
        self.assertIn("docker_qos_deadline_incompatible_event_production", unified_report_source)
        self.assertIn("message_lost_event_production", unified_report_source)
        self.assertIn("docker_qos_message_lost_event_production", unified_report_source)
        self.assertIn("remote_unrecoverable_loss_notice_claim", unified_report_source)
        self.assertIn("remote_message_lost_waitable_claim", unified_report_source)
        self.assertIn(
            "duplicate_unrecoverable_loss_notice_deduplication_claim",
            unified_report_source,
        )
        self.assertIn("repeated_remote_message_lost_claim", unified_report_source)
        self.assertIn("repair_budget_terminal_loss_notice_claim", unified_report_source)
        self.assertIn(
            "repair_attempt_limit_terminal_loss_notice_claim",
            unified_report_source,
        )
        self.assertIn(
            "repair_admission_terminal_loss_notice_claim",
            unified_report_source,
        )
        self.assertIn("terminal_repair_controls_repeated_claim", unified_report_source)
        self.assertIn("liveliness_event_production", unified_report_source)
        self.assertIn("docker_qos_liveliness_event_production", unified_report_source)
        self.assertIn("automatic_liveliness_idle_renewal_claim", unified_report_source)
        self.assertIn(
            "automatic_liveliness_false_loss_suppression_claim",
            unified_report_source,
        )
        self.assertIn("automatic_liveliness_repeated_claim", unified_report_source)
        self.assertIn("remote_manual_liveliness_idle_timeout_claim", unified_report_source)
        self.assertIn("remote_manual_liveliness_explicit_assert_claim", unified_report_source)
        self.assertIn("remote_manual_liveliness_publish_assert_claim", unified_report_source)
        self.assertIn(
            "remote_publisher_liveliness_lost_event_claim",
            unified_report_source,
        )
        self.assertIn(
            "remote_all_jazzy_event_types_path_claim",
            unified_report_source,
        )
        self.assertIn(
            "remote_event_wait_take_callback_coverage_claim",
            unified_report_source,
        )
        self.assertIn(
            "remote_manual_liveliness_graph_lease_independence_claim",
            unified_report_source,
        )
        self.assertIn("remote_manual_liveliness_repeated_claim", unified_report_source)
        self.assertIn(
            "remote_liveliness_multi_endpoint_independence_claim",
            unified_report_source,
        )
        self.assertIn(
            "remote_liveliness_endpoint_churn_recreate_claim",
            unified_report_source,
        )
        self.assertIn(
            "remote_liveliness_multi_endpoint_repeated_claim",
            unified_report_source,
        )
        self.assertIn("liveliness_scale_repeated_claim", unified_report_source)
        self.assertIn("remote_liveliness_scale_repeated_claim", unified_report_source)
        self.assertIn(
            "liveliness_default_lease_repeated_claim", unified_report_source
        )
        self.assertIn(
            "qos_liveliness_incompatible_event_production_claim",
            unified_report_source,
        )
        self.assertIn(
            "liveliness_missing_lease_requested_event_claim",
            unified_report_source,
        )
        self.assertIn(
            "qos_best_available_endpoint_adaptation_claim", unified_report_source
        )
        self.assertIn(
            "best_available_policy_frozen_after_create_claim",
            unified_report_source,
        )
        self.assertIn("matched_event_repeated_claim", unified_report_source)
        self.assertIn("qos_incompatible_repeated_event_claim", unified_report_source)
        self.assertIn(
            "qos_deadline_incompatible_repeated_event_claim",
            unified_report_source,
        )
        self.assertIn(
            "qos_missing_offered_deadline_incompatible_repeated_claim",
            unified_report_source,
        )
        self.assertIn("type_incompatible_repeated_event_claim", unified_report_source)
        self.assertIn("message_lost_repeated_event_claim", unified_report_source)
        self.assertIn("liveliness_repeated_event_claim", unified_report_source)
        self.assertIn("security_options_lifecycle_abi_supported", unified_report_source)
        self.assertIn("security_policy_enforcement_executed", unified_report_source)
        self.assertIn("security_hardening_blocker", unified_report_source)
        self.assertIn("fleetqox_security_policy_enforcement_claim", unified_report_source)
        self.assertIn("security_policy_repeated_enforcement_claim", unified_report_source)
        self.assertIn(
            "sros2_permissions_xml_publish_enforcement_claim",
            unified_report_source,
        )
        self.assertIn(
            "sros2_permissions_xml_subscribe_enforcement_claim",
            unified_report_source,
        )
        self.assertIn(
            "sros2_permissions_xml_pubsub_enforcement_claim",
            unified_report_source,
        )
        self.assertIn("malformed_permissions_fail_closed_claim", unified_report_source)
        self.assertIn(
            "runtime_sros2_permissions_signature_validation_claim",
            unified_report_source,
        )
        self.assertIn(
            "tampered_signed_permissions_fail_closed_claim",
            unified_report_source,
        )
        self.assertIn(
            "sros2_service_request_reply_authorization_claim",
            unified_report_source,
        )
        self.assertIn("stress_security_smoke_claim", unified_report_source)
        self.assertIn("long_stress_security_campaign_claim", unified_report_source)
        self.assertIn("docker_security_options_lifecycle_probe", unified_report_source)
        self.assertIn(
            "docker_publisher_subscription_payload_scratch_allocation_5run_probe",
            unified_report_source,
        )
        self.assertIn("docker_qos_event_deadline_waitable_5run_probe", unified_report_source)
        self.assertIn(
            "docker_qos_event_waitability_matrix_5run_probe",
            unified_report_source,
        )
        self.assertIn("qos_event_waitability_matrix_claim", unified_report_source)
        self.assertIn("qos_event_waitability_repeated_claim", unified_report_source)
        self.assertIn(
            "docker_content_filter_repeated_enforcement_5run_probe",
            unified_report_source,
        )
        self.assertIn("sros2_policy_enforcement_claim", unified_report_source)
        self.assertIn("std_msgs_content_filter_enforcement", unified_report_source)
        self.assertIn("disabled_content_filter_bypass", unified_report_source)
        self.assertIn("content_filter_sql_subset_claim", unified_report_source)
        self.assertIn(
            "content_filter_invalid_expression_fail_closed_claim",
            unified_report_source,
        )
        self.assertIn(
            "content_filter_typed_reflection_repeated_claim",
            unified_report_source,
        )
        self.assertIn("quic_gateway_take_path_download", unified_report_source)
        self.assertIn("docker_ngtcp2_quic_gateway_take_path_probe", unified_report_source)
        self.assertIn("docker_ngtcp2_quic_gateway_rmw_take_path_probe", unified_report_source)
        self.assertIn(
            "docker_ngtcp2_quic_gateway_rmw_take_session_reuse_file_probe",
            unified_report_source,
        )
        self.assertIn(
            "docker_ngtcp2_quic_gateway_bidirectional_publish_take_probe",
            unified_report_source,
        )
        self.assertIn(
            "docker_ngtcp2_quic_gateway_bidirectional_publish_take_5run_probe",
            unified_report_source,
        )
        self.assertIn(
            "docker_netem_quic_inprocess_rmw_bidirectional_probe",
            unified_report_source,
        )
        self.assertIn("same_connection_bidirectional", unified_report_source)
        self.assertIn("concurrent_post_get_stream_pair", unified_report_source)
        self.assertIn("max_concurrent_request_streams", unified_report_source)
        self.assertIn(
            "quic_multi_threaded_rmw_publish_take_operation_claim",
            unified_report_source,
        )
        self.assertIn("connection_reuse_count", unified_report_source)
        self.assertIn("untrusted_ca_rejected", unified_report_source)
        self.assertIn("docker_quic_gateway_disable_early_data_control", unified_report_source)
        self.assertIn("quic_gateway_bidirectional_boundary_claim", unified_report_source)
        self.assertIn("quic_gateway_bidirectional_repeated_claim", unified_report_source)
        self.assertIn("session_resumption_attempted_observed", unified_report_source)
        self.assertIn("zero_rtt_packet_observed", unified_report_source)
        self.assertIn("zero_rtt_accepted_observed", unified_report_source)
        self.assertIn("zero_rtt_disabled_control_claim", unified_report_source)
        self.assertIn("docker_nav2_planner_controller_lifecycle_configure", unified_report_source)
        self.assertIn(
            "docker_nav2_planner_controller_lifecycle_activate_dynamic_tf",
            unified_report_source,
        )
        self.assertIn("docker_nav2_planner_compute_path_action_map_tf", unified_report_source)
        self.assertIn(
            "docker_nav2_controller_follow_path_action_map_tf_odom",
            unified_report_source,
        )
        self.assertIn("docker_nav2_navigate_to_pose_same_pose_bt_pipeline", unified_report_source)
        self.assertIn(
            "docker_nav2_navigate_to_pose_repeated_same_pose_bt_pipeline",
            unified_report_source,
        )
        self.assertIn(
            "docker_nav2_navigate_to_pose_moving_base_bt_pipeline",
            unified_report_source,
        )
        self.assertIn(
            "docker_nav2_navigate_to_pose_extended_moving_base_bt_pipeline",
            unified_report_source,
        )
        self.assertIn("docker_nav2_behavior_server_spin_action", unified_report_source)
        self.assertIn(
            "docker_nav2_navigate_to_pose_recovery_tree_fallback",
            unified_report_source,
        )
        self.assertIn(
            "docker_nav2_navigate_to_pose_recovered_success_after_spin",
            unified_report_source,
        )
        self.assertIn(
            "docker_nav2_navigate_to_pose_recovered_success_repeated_smoke",
            unified_report_source,
        )
        self.assertIn("planner_activate_transition", unified_report_source)
        self.assertIn("upstream_concurrency", unified_report_source)
        self.assertIn("nav2_lifecycle_manager_upstream", unified_report_source)
        self.assertIn("docker_nav2_rmf_upstream_concurrency8", unified_report_source)
        self.assertIn("docker_nav2_rmf_upstream_concurrency16", unified_report_source)
        self.assertIn("docker_nav2_rmf_upstream_concurrency32", unified_report_source)
        self.assertIn("docker_nav2_rmf_upstream_concurrency64", unified_report_source)
        self.assertIn("docker_nav2_rmf_upstream_concurrency128", unified_report_source)
        self.assertIn("docker_nav2_rmf_upstream_concurrency256", unified_report_source)
        self.assertIn("docker_nav2_rmf_upstream_concurrency512", unified_report_source)
        self.assertIn("docker_nav2_rmf_upstream_concurrency1024", unified_report_source)
        self.assertIn("docker_nav2_rmf_upstream_concurrency2048", unified_report_source)
        self.assertIn("docker_nav2_rmf_upstream_concurrency4096", unified_report_source)
        self.assertIn(
            "docker_nav2_rmf_upstream_total4096_admission_window8",
            unified_report_source,
        )
        self.assertIn("batch_timeout_s", unified_report_source)
        self.assertIn("router_timeout_ms", unified_report_source)
        self.assertIn("tf_topic_forwarded", unified_report_source)
        self.assertIn("compute_path_goal_succeeded", unified_report_source)
        self.assertIn("follow_path_goal_succeeded", unified_report_source)
        self.assertIn("navigate_to_pose_goal_succeeded", unified_report_source)
        self.assertIn("navigate_to_pose_repeated_smoke", unified_report_source)
        self.assertIn("navigate_to_pose_goal_succeeded_run_count", unified_report_source)
        self.assertIn("min_service_frames_per_run", unified_report_source)
        self.assertIn("cmd_vel_topic_forwarded", unified_report_source)
        self.assertIn("fake_base_cmd_vel_count", unified_report_source)
        self.assertIn("fake_base_moved_distance", unified_report_source)
        self.assertIn("fake_base_angular_distance", unified_report_source)
        self.assertIn("navigation_goal_x", unified_report_source)
        self.assertIn("extended_moving_navigation_claim", unified_report_source)
        self.assertIn("extended_moving_navigation_scope", unified_report_source)
        self.assertIn("navigate_to_pose_long_moving_workload", unified_report_source)
        self.assertIn("extended_moving_navigation_run_count", unified_report_source)
        self.assertIn("long_navigation_workload_scope", unified_report_source)
        self.assertIn("min_required_total_fake_base_moved_distance", unified_report_source)
        self.assertIn("spin_goal_succeeded", unified_report_source)
        self.assertIn("spin_error_code", unified_report_source)
        self.assertIn("recovery_behavior_action_claim", unified_report_source)
        self.assertIn("nav2_recovery_behavior_claim", unified_report_source)
        self.assertIn("navigate_to_pose_recovery_tree_claim", unified_report_source)
        self.assertIn("successful_recovered_navigation_claim", unified_report_source)
        self.assertIn("successful_recovered_navigation_scope", unified_report_source)
        self.assertIn("navigate_to_pose_recovered_success_repeated_smoke", unified_report_source)
        self.assertIn("successful_recovered_navigation_run_count", unified_report_source)
        self.assertIn("repeated_recovered_navigation_claim", unified_report_source)
        self.assertIn("docker_nav2_planner_static_obstacle_repair", unified_report_source)
        self.assertIn(
            "docker_nav2_navigate_to_pose_obstacle_retry_after_clear",
            unified_report_source,
        )
        self.assertIn(
            "docker_nav2_navigate_to_pose_autonomous_same_goal_obstacle_recovery",
            unified_report_source,
        )
        self.assertIn("planner_static_obstacle_repair_claim", unified_report_source)
        self.assertIn("nav2_obstacle_retry_after_clear_claim", unified_report_source)
        self.assertIn("blocked_compute_path_error_code", unified_report_source)
        self.assertIn("clear_compute_path_path_pose_count", unified_report_source)
        self.assertIn("blocked_navigate_to_pose_error_code", unified_report_source)
        self.assertIn("clear_navigate_to_pose_status", unified_report_source)
        self.assertIn("same_goal_obstacle_recovery_observed", unified_report_source)
        self.assertIn("clear_map_published_during_goal", unified_report_source)
        self.assertIn("wait_action_forwarded", unified_report_source)
        self.assertIn("obstacle_field_recovery_claim", unified_report_source)
        self.assertIn("full_nav2_obstacle_recovery_claim", unified_report_source)
        self.assertIn(
            "autonomous_same_goal_nav2_obstacle_recovery_claim",
            unified_report_source,
        )
        self.assertIn("planner_failure_observed", unified_report_source)
        self.assertIn("intentional_planner_failure", unified_report_source)
        self.assertIn("moving_robot_navigation_claim", unified_report_source)
        self.assertIn("full_nav2_navigation_stack_claim", unified_report_source)
        self.assertIn(
            "docker_content_filter_std_msgs_string_text_enforcement",
            unified_report_source,
        )
        self.assertIn(
            "docker_content_filter_dynamic_reconfigure_disable",
            unified_report_source,
        )
        self.assertIn("results_omnetpp/*summary.json", unified_report_source)
        self.assertIn("simulation/omnetpp", unified_report_source)
        self.assertIn("omnetpp_template_integrity_claim", unified_report_source)
        self.assertIn("omnetpp_input_trace_claim", unified_report_source)
        self.assertIn("omnetpp_runtime_executed", unified_report_source)
        self.assertIn("omnetpp_parity_blocker", unified_report_source)
        self.assertIn("omnetpp_inet_runtime_claim", unified_report_source)
        self.assertIn("omnetpp_parity_claim", unified_report_source)
        self.assertIn("ns3_omnetpp_parity_claim", unified_report_source)
        self.assertIn("render_markdown", unified_report_source)

        frontier_script = ROOT / "scripts" / "run_rmw_docker_fleet_repair_capacity_frontier.py"
        self.assertTrue(frontier_script.exists())
        frontier_source = frontier_script.read_text()
        self.assertIn("fleetrmw.fleet_repair_capacity_frontier.v1", frontier_source)
        self.assertIn("--robot-counts", frontier_source)
        self.assertIn("8,16,32", frontier_source)
        self.assertIn("--capacity-fractions", frontier_source)
        self.assertIn(
            "shared_budget_admission_actuated_repair_qoe_frontier",
            frontier_source,
        )
        self.assertIn("repair_admission_qualified_ratio", frontier_source)
        self.assertIn("fleet_repair_capacity_bytes=capacity_bytes", frontier_source)
        self.assertIn("repair_capacity_fault=True", frontier_source)
        self.assertIn("reuse_build=True", frontier_source)

    def test_capability_manifest_scopes_unsupported_abi(self) -> None:
        manifest_path = PKG / "capabilities.json"
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["schema_version"], "fleetrmw.rmw_capabilities.v1")
        self.assertFalse(manifest["production_ready"])
        self.assertTrue(
            manifest["supported"]["bounded_fragment_queue_admission_backpressure"]
        )
        self.assertTrue(
            manifest["supported"]["bounded_fragment_repair_queue_admission"]
        )
        self.assertTrue(
            manifest["supported"]["fleet_aware_fragment_nack_index_budget"]
        )
        self.assertTrue(
            manifest["supported"]["source_scoped_fragment_repair_admission"]
        )
        self.assertTrue(
            manifest["supported"]["configurable_fragment_assembly_ttl"]
        )
        self.assertTrue(
            manifest["supported"]["round_robin_initial_fragment_scheduling"]
        )
        self.assertTrue(
            manifest["supported"]["async_fragment_ack_timeout_after_drain"]
        )
        self.assertTrue(
            manifest["supported"]["fragment_whole_fallback_grace"]
        )
        self.assertTrue(
            manifest["supported"]["fragment_sender_completion_marker"]
        )
        self.assertTrue(
            manifest["supported"]["idle_based_fragment_tail_guard"]
        )
        self.assertTrue(
            manifest["supported"][
                "adaptive_fragment_repair_pressure_priority"
            ]
        )
        self.assertTrue(
            manifest["supported"]["mtu_aware_udp_datagram_budget"]
        )
        self.assertTrue(
            manifest["supported"][
                "protected_fragment_effective_chunk_sizing"
            ]
        )
        self.assertTrue(
            manifest["supported"]["completed_fragment_retransmission_deduplication"]
        )
        self.assertTrue(
            manifest["supported"]["bounded_fragment_assembly_admission"]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_authenticated_fragment_assembly_admission_probe"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "authenticated_fragment_assembly_admission_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "fleet_aware_fragment_nack_fairness_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "multi_reader_fragment_repair_isolation_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "bounded_fragment_assembly_ttl_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "round_robin_initial_fragment_scheduling_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "async_fragment_ack_timeout_after_drain_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "fragment_sender_completion_marker_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "mtu_aware_udp_datagram_budget_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "unauthorized_fragment_repair_source_fail_closed_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "udp_unprotected_fragment_fail_closed_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "peer_identity_fragment_pressure_isolation_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "unauthorized_identity_pre_reassembly_rejection_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "repeated_peer_identity_fragment_pressure_claim"
            ]
        )
        self.assertFalse(
            manifest["claim_boundaries"][
                "long_duration_peer_identity_fragment_soak_claim"
            ]
        )
        self.assertFalse(
            manifest["claim_boundaries"]["production_fragment_security_claim"]
        )
        self.assertEqual(
            manifest["serialization_format"],
            "fleetrmw.introspection_c.v1",
        )
        self.assertTrue(manifest["supported"]["source_sequence_ack_nack_repair"])
        self.assertTrue(
            manifest["supported"]["wait_set_capacity_null_entry_context_contract"]
        )
        self.assertTrue(
            manifest["supported"]["entity_owner_node_lifecycle_validation"]
        )
        self.assertTrue(
            manifest["claim_boundaries"]["wait_set_contract_validation_claim"]
        )
        self.assertTrue(
            manifest["claim_boundaries"]["entity_owner_node_validation_claim"]
        )
        self.assertNotIn("dynamic_messages", manifest["unsupported"])
        self.assertNotIn("dynamic_serialization_support", manifest["unsupported"])
        self.assertTrue(
            manifest["supported"]["dynamic_serialization_support_plugin_loader"]
        )
        self.assertTrue(manifest["supported"]["dynamic_message_take"])
        self.assertTrue(manifest["supported"]["dynamic_message_take_with_info"])
        self.assertTrue(
            manifest["supported"][
                "message_info_publication_reception_sequence_features"
            ]
        )
        self.assertTrue(manifest["supported"]["udp_network_flow_endpoints"])
        self.assertTrue(
            manifest["supported"]["new_message_request_response_callbacks"]
        )
        self.assertTrue(
            manifest["supported"]["interprocess_rclcpp_nav_msgs_path_64_pose_router"]
        )
        self.assertTrue(
            manifest["supported"]["bidirectional_rclcpp_rclpy_nav_msgs_path_router"]
        )
        self.assertTrue(
            manifest["supported"]["docker_bidirectional_cpp_python_path_5run_netem"]
        )
        self.assertTrue(
            manifest["supported"][
                "bidirectional_rclcpp_rclpy_nav_msgs_get_plan_512_pose"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_bidirectional_cpp_python_get_plan_5run_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"]["large_service_udp_fragmentation_reassembly"]
        )
        self.assertTrue(
            manifest["supported"]["generated_bounded_rosidl_service"]
        )
        self.assertTrue(
            manifest["supported"][
                "bidirectional_rclcpp_rclpy_bounded_rosidl_service"
            ]
        )
        self.assertTrue(
            manifest["supported"]["docker_bounded_rosidl_service_5run_netem"]
        )
        self.assertTrue(
            manifest["supported"]["service_request_bounded_discovery_repeat_dedup"]
        )
        self.assertTrue(
            manifest["supported"][
                "service_request_nonblocking_async_discovery_repair"
            ]
        )
        self.assertTrue(
            manifest["supported"]["service_request_response_cancelled_repair"]
        )
        self.assertTrue(
            manifest["supported"]["bounded_service_request_response_queues"]
        )
        self.assertTrue(
            manifest["supported"]["bounded_service_dedupe_and_response_replay"]
        )
        self.assertTrue(
            manifest["supported"]["docker_service_resource_limit_5run_netem"]
        )
        self.assertTrue(
            manifest["supported"]["per_client_service_request_queue_quota"]
        )
        self.assertTrue(
            manifest["supported"]["docker_service_client_isolation_5run_netem"]
        )
        self.assertTrue(
            manifest["supported"]["service_inter_client_round_robin_dequeue"]
        )
        self.assertTrue(
            manifest["supported"]["bounded_service_request_repair_pending_jobs"]
        )
        self.assertTrue(
            manifest["supported"]["docker_service_repair_admission_5run_netem"]
        )
        self.assertTrue(
            manifest["supported"]["service_client_priority_wire_metadata"]
        )
        self.assertTrue(
            manifest["supported"]["service_priority_aging_starvation_bound"]
        )
        self.assertTrue(
            manifest["supported"]["service_smooth_weighted_round_robin"]
        )
        self.assertTrue(
            manifest["supported"]["service_earliest_deadline_first"]
        )
        self.assertTrue(
            manifest["supported"]["service_process_crash_replay"]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "docker_router_cpp_python_path_5run_netem"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"]["bidirectional_cpp_python_path_claim"]
        )
        self.assertTrue(
            manifest["claim_boundaries"]["sequence_heavy_get_plan_service_claim"]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "large_sequence_service_fragmentation_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "docker_router_bounded_shape_service_5run_netem"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "bounded_rosidl_nested_message_sequence_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "nonblocking_async_service_request_repair_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "service_discovery_repair_without_runner_override_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "service_resource_backpressure_repair_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "service_noisy_neighbor_bounded_fairness_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "service_inter_client_round_robin_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "bounded_service_repair_pending_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "service_strict_priority_dequeue_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"]["weighted_service_ratio_claim"]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "deadline_aware_service_scheduling_claim"
            ]
        )
        self.assertTrue(
            manifest["claim_boundaries"][
                "crash_persistent_completed_service_deduplication_claim"
            ]
        )
        self.assertFalse(
            manifest["claim_boundaries"]["power_loss_durability_claim"]
        )
        self.assertFalse(
            manifest["claim_boundaries"]["full_exactly_once_service_semantics_claim"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_profile_compatibility_reliability_durability_deadline_liveliness_lease"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_qos_profile_compatibility_error_warning_reason_probe"
            ]
        )
        self.assertTrue(manifest["supported"]["qos_event_object_callback_noop_abi"])
        self.assertTrue(manifest["supported"]["qos_event_deadline_waitable_5run_abi"])
        self.assertTrue(
            manifest["supported"]["qos_event_waitability_all_11_jazzy_event_types"]
        )
        self.assertTrue(
            manifest["supported"]["docker_qos_event_waitability_matrix_5run"]
        )
        self.assertTrue(manifest["supported"]["qos_deadline_event_production_next_sample"])
        self.assertTrue(
            manifest["supported"]["qos_deadline_event_waitable_readiness_next_sample"]
        )
        self.assertTrue(
            manifest["supported"]["qos_deadline_timer_driven_idle_events_after_first_sample"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_offered_deadline_missed_events_remote_udp_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_requested_deadline_missed_events_remote_udp_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"]["docker_remote_deadline_missed_events_5run"]
        )
        self.assertTrue(
            manifest["supported"]["qos_publication_subscription_matched_events_local"]
        )
        self.assertTrue(
            manifest["supported"]["qos_publication_subscription_matched_events_5run"]
        )
        self.assertTrue(manifest["supported"]["qos_reliability_incompatible_events_local"])
        self.assertTrue(manifest["supported"]["qos_durability_incompatible_events_local"])
        self.assertTrue(
            manifest["supported"]["qos_reliability_durability_incompatible_events_5run"]
        )
        self.assertTrue(manifest["supported"]["qos_deadline_incompatible_events_local"])
        self.assertTrue(
            manifest["supported"][
                "qos_missing_offered_deadline_incompatible_events_local"
            ]
        )
        self.assertTrue(manifest["supported"]["qos_deadline_incompatible_events_5run"])
        self.assertTrue(manifest["supported"]["qos_type_incompatible_events_5run"])
        self.assertTrue(manifest["supported"]["qos_message_lost_events_5run"])
        self.assertTrue(
            manifest["supported"]["qos_message_lost_events_best_effort_sequence_gap_local"]
        )
        self.assertTrue(
            manifest["supported"]["qos_message_lost_events_repair_reorder_suppression_local"]
        )
        self.assertTrue(
            manifest["supported"]["qos_message_lost_events_reliable_history_exhaustion_local"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_message_lost_events_reliable_history_exhaustion_remote_udp_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_message_lost_events_repair_budget_exhaustion_remote_udp_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_message_lost_events_repair_attempt_limit_remote_udp_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_message_lost_events_repair_admission_rejection_remote_udp_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"]["unrecoverable_loss_notice_subscriber_identity"]
        )
        self.assertTrue(
            manifest["supported"][
                "unrecoverable_loss_notice_duplicate_deduplication_remote"
            ]
        )
        self.assertTrue(manifest["supported"]["docker_remote_message_lost_20run"])
        self.assertTrue(
            manifest["supported"][
                "docker_remote_message_lost_terminal_controls_5run_each"
            ]
        )
        self.assertTrue(
            manifest["supported"]["qos_best_effort_subscriptions_do_not_request_repair"]
        )
        self.assertTrue(manifest["supported"]["qos_liveliness_events_5run"])
        self.assertTrue(
            manifest["supported"]["qos_liveliness_automatic_idle_renewal_local"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_automatic_false_loss_suppression_5run"
            ]
        )
        self.assertTrue(manifest["supported"]["docker_automatic_liveliness_5run"])
        self.assertTrue(
            manifest["supported"]["qos_liveliness_manual_remote_idle_timeout_udp_netem"]
        )
        self.assertTrue(
            manifest["supported"]["qos_liveliness_manual_remote_explicit_assert_udp_netem"]
        )
        self.assertTrue(
            manifest["supported"]["qos_liveliness_manual_remote_publish_assert_udp_netem"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_lost_events_remote_publisher_udp_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"]["qos_liveliness_graph_lease_independent_from_manual_lease"]
        )
        self.assertTrue(manifest["supported"]["docker_remote_manual_liveliness_5run"])
        self.assertTrue(
            manifest["supported"][
                "qos_all_11_jazzy_event_types_remote_multicontainer_path"
            ]
        )
        self.assertTrue(
            manifest["supported"]["remote_qos_event_coverage_aggregate_report"]
        )
        self.assertTrue(
            manifest["supported"]["qos_liveliness_remote_multi_endpoint_independent_state"]
        )
        self.assertTrue(
            manifest["supported"]["qos_liveliness_remote_alive_not_alive_remove"]
        )
        self.assertTrue(
            manifest["supported"]["qos_liveliness_remote_endpoint_churn_recreate"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_remote_expiry_preserves_matching_multi_endpoint"
            ]
        )
        self.assertTrue(
            manifest["supported"]["docker_remote_liveliness_multi_endpoint_5run"]
        )
        self.assertTrue(
            manifest["supported"]["qos_liveliness_manual_local_64_endpoint_scale"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_system_default_automatic_idle_renewal_16_endpoint"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_aggregate_transition_counts_64_endpoint"
            ]
        )
        self.assertTrue(manifest["supported"]["docker_liveliness_scale_5run"])
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_manual_remote_64_endpoint_scale_udp_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_remote_64_endpoint_exact_aggregate_transitions"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_remote_scale_expiry_preserves_matching"
            ]
        )
        self.assertTrue(
            manifest["supported"]["docker_remote_liveliness_scale_5run"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_non_expiring_default_lease_lifecycle"
            ]
        )
        self.assertTrue(
            manifest["supported"]["qos_liveliness_unknown_policy_fail_closed"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_deprecated_manual_by_node_fail_closed"
            ]
        )
        self.assertTrue(
            manifest["supported"]["docker_liveliness_default_lease_5run"]
        )
        self.assertTrue(
            manifest["supported"]["qos_liveliness_kind_incompatible_events_local"]
        )
        self.assertTrue(
            manifest["supported"]["qos_liveliness_lease_incompatible_events_local"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_liveliness_missing_offered_lease_incompatible_events_local"
            ]
        )
        self.assertTrue(
            manifest["supported"]["docker_qos_liveliness_incompatible_events_5run"]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_best_available_endpoint_adaptation_rmw_dds_common"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_best_available_liveliness_publisher_discovery_selection"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "qos_best_available_liveliness_subscription_discovery_selection"
            ]
        )
        self.assertTrue(
            manifest["supported"]["qos_best_available_policy_frozen_after_create"]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_qos_best_available_endpoint_adaptation_5run"
            ]
        )
        self.assertTrue(manifest["supported"]["content_filter_set_get_stored_noop_abi"])
        self.assertTrue(manifest["supported"]["content_filter_key_value_payload_enforcement"])
        self.assertTrue(
            manifest["supported"]["content_filter_std_msgs_string_text_payload_enforcement"]
        )
        self.assertTrue(
            manifest["supported"]["content_filter_dynamic_reconfigure_and_disable"]
        )
        self.assertTrue(manifest["supported"]["content_filter_repeated_enforcement_5run"])
        self.assertTrue(
            manifest["supported"]["content_filter_sql_boolean_parentheses_subset"]
        )
        self.assertTrue(
            manifest["supported"]["content_filter_sql_like_between_in_null_subset"]
        )
        self.assertTrue(
            manifest["supported"]["content_filter_invalid_expression_fail_closed"]
        )
        self.assertTrue(
            manifest["supported"]["content_filter_sql_subset_repeated_5run"]
        )
        self.assertTrue(
            manifest["supported"][
                "content_filter_introspection_cpp_nested_field_reflection"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "content_filter_introspection_c_nested_field_reflection"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "content_filter_introspection_cpp_array_field_reflection"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "content_filter_malformed_typed_payload_fail_closed"
            ]
        )
        self.assertTrue(
            manifest["supported"]["content_filter_typed_reflection_repeated_5run"]
        )
        self.assertTrue(
            manifest["supported"]["fleetqox_generic_serialized_direct_peer_relay"]
        )
        self.assertTrue(
            manifest["supported"][
                "fleetqox_generic_serialized_middle_termination_republish"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_fleetqox_generic_serialized_relay_8robot_5run_netem"
            ]
        )
        self.assertTrue(manifest["supported"]["same_host_posix_shared_memory_pubsub"])
        self.assertTrue(manifest["supported"]["shared_memory_to_udp_fallback"])
        self.assertTrue(manifest["supported"]["shared_memory_udp_remote_hybrid"])
        self.assertTrue(
            manifest["supported"]["middleware_owned_loaned_messages_introspection_c_cpp"]
        )
        self.assertNotIn("loaned_messages", manifest["unsupported"])
        self.assertTrue(
            manifest["supported"][
                "bounded_standalone_serialization_size_introspection_c_cpp"
            ]
        )
        self.assertTrue(
            manifest["supported"]["publisher_subscription_payload_scratch_allocations"]
        )
        self.assertTrue(
            manifest["supported"]["publisher_subscription_payload_scratch_allocations_5run"]
        )
        self.assertTrue(
            manifest["supported"]["docker_netem_quic_path_telemetry_ngtcp2_logs"]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_publish_path_subprocess_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_netem_quic_gateway_publish_path_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_netem_quic_gateway_async_burst_path_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_async_publish_worker_subprocess_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_async_burst_worker_subprocess_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_session_reuse_files_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_take_path_download_subprocess_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_rmw_take_path_on_demand_subprocess_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_rmw_take_session_reuse_files_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_rmw_take_session_reuse_5download_files_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_bidirectional_publish_take_shared_session_files_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_bidirectional_publish_take_5run_shared_session_files_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"]["quic_inprocess_ngtcp2_gnutls_nghttp3_h3"]
        )
        self.assertTrue(
            manifest["supported"]["quic_inprocess_persistent_connection_stream_reuse"]
        )
        self.assertTrue(
            manifest["supported"]["quic_inprocess_rmw_publish_take_same_connection"]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_netem_quic_inprocess_rmw_bidirectional_128publish_1take"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_inprocess_concurrent_rmw_publish_take_operation_pair"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_netem_quic_inprocess_concurrent_rmw_publish_take_operation_pair"
            ]
        )
        self.assertTrue(
            manifest["supported"]["quic_inprocess_untrusted_ca_fail_closed"]
        )
        self.assertTrue(
            manifest["supported"]["quic_inprocess_client_qlog_export"]
        )
        self.assertTrue(
            manifest["supported"][
                "quic_gateway_disable_early_data_control_ngtcp2_gnutls"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_quic_gateway_async_burst_soak_runner"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_quic_gateway_async_burst_soak_3run_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_quic_gateway_async_burst_soak_10run_netem"
            ]
        )
        self.assertTrue(manifest["supported"]["qos_type_incompatible_events_local"])
        self.assertTrue(
            manifest["supported"]["qos_message_lost_events_keep_last_overwrite_local"]
        )
        self.assertTrue(
            manifest["supported"]["qos_message_lost_events_best_effort_sequence_gap_local"]
        )
        self.assertTrue(manifest["supported"]["qos_liveliness_events_finite_lease_local"])
        self.assertTrue(manifest["supported"]["security_options_lifecycle_abi"])
        self.assertTrue(manifest["supported"]["security_options_repeated_lifecycle_5run_abi"])
        self.assertTrue(manifest["supported"]["fleetqox_security_policy_enforcement"])
        self.assertTrue(
            manifest["supported"]["fleetqox_security_policy_repeated_enforcement_5run"]
        )
        self.assertTrue(
            manifest["supported"]["sros2_generated_permissions_xml_publish_enforcement"]
        )
        self.assertTrue(
            manifest["supported"][
                "sros2_generated_permissions_xml_publish_enforcement_5run"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "sros2_generated_permissions_xml_subscribe_enforcement"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "sros2_generated_permissions_xml_subscribe_enforcement_5run"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "sros2_generated_permissions_xml_pubsub_enforcement"
            ]
        )
        self.assertTrue(manifest["supported"]["sros2_signed_permissions_preflight"])
        self.assertTrue(
            manifest["supported"]["sros2_runtime_permissions_signature_ca_validation"]
        )
        self.assertTrue(manifest["supported"]["sros2_permissions_malformed_fail_closed"])
        self.assertTrue(
            manifest["supported"]["sros2_tampered_signed_permissions_fail_closed"]
        )
        self.assertTrue(
            manifest["supported"][
                "sros2_generated_permissions_service_request_reply_authorization"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "sros2_generated_permissions_service_request_reply_authorization_5run"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "sros2_generated_permissions_action_call_execute_authorization"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "sros2_generated_permissions_action_call_execute_authorization_5run"
            ]
        )
        self.assertTrue(manifest["supported"]["sros2_signed_governance_access_control"])
        self.assertTrue(
            manifest["supported"]["sros2_signed_governance_access_control_5run"]
        )
        self.assertTrue(
            manifest["supported"][
                "sros2_governance_transport_protection_fail_closed"
            ]
        )
        self.assertTrue(
            manifest["supported"]["sros2_local_identity_certificate_key_ca_validation"]
        )
        self.assertTrue(
            manifest["supported"][
                "sros2_local_identity_certificate_key_ca_validation_5run"
            ]
        )
        self.assertTrue(
            manifest["supported"]["sros2_identity_negative_controls_fail_closed"]
        )
        self.assertTrue(manifest["supported"]["udp_payload_aes_256_gcm_psk"])
        self.assertTrue(manifest["supported"]["udp_payload_aes_256_gcm_psk_5run"])
        self.assertTrue(manifest["supported"]["udp_payload_aead_tamper_fail_closed"])
        self.assertTrue(
            manifest["supported"][
                "udp_payload_aead_strict_missing_key_fail_closed"
            ]
        )
        self.assertTrue(
            manifest["supported"]["udp_sros2_x509_peer_identity_authentication"]
        )
        self.assertTrue(
            manifest["supported"][
                "udp_sros2_x509_peer_identity_authentication_5run"
            ]
        )
        self.assertTrue(
            manifest["supported"]["udp_peer_x509_crl_revocation_fail_closed"]
        )
        self.assertTrue(
            manifest["supported"][
                "udp_authenticated_psk_hkdf_session_key_derivation"
            ]
        )
        self.assertTrue(
            manifest["supported"]["udp_authenticated_psk_session_key_rotation"]
        )
        self.assertTrue(manifest["supported"]["docker_stress_security_campaign_runner"])
        self.assertTrue(
            manifest["supported"]["docker_long_campaign_active_round_runner"]
        )
        self.assertTrue(manifest["supported"]["unified_benchmark_report_aggregator"])
        self.assertTrue(
            manifest["supported"]["nav2_planner_controller_lifecycle_configure_plugins"]
        )
        self.assertTrue(
            manifest["supported"][
                "nav2_planner_controller_lifecycle_activate_with_dynamic_tf"
            ]
        )
        self.assertTrue(
            manifest["supported"]["nav2_planner_compute_path_action_with_map_tf"]
        )
        self.assertTrue(
            manifest["supported"][
                "nav2_controller_follow_path_action_with_map_tf_odom"
            ]
        )
        self.assertTrue(
            manifest["supported"]["nav2_navigate_to_pose_same_pose_bt_pipeline"]
        )
        self.assertTrue(
            manifest["supported"][
                "nav2_navigate_to_pose_repeated_same_pose_bt_pipeline"
            ]
        )
        self.assertTrue(
            manifest["supported"]["nav2_navigate_to_pose_moving_base_bt_pipeline"]
        )
        self.assertTrue(
            manifest["supported"][
                "nav2_navigate_to_pose_extended_moving_base_bt_pipeline"
            ]
        )
        self.assertTrue(
            manifest["supported"]["nav2_behavior_server_spin_recovery_action"]
        )
        self.assertTrue(
            manifest["supported"]["nav2_navigate_to_pose_recovery_tree_fallback"]
        )
        self.assertTrue(
            manifest["supported"]["nav2_navigate_to_pose_recovered_success_after_spin"]
        )
        self.assertTrue(
            manifest["supported"][
                "nav2_navigate_to_pose_recovered_success_repeated_smoke"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "nav2_navigate_to_pose_long_repeated_moving_base_workload"
            ]
        )
        self.assertTrue(manifest["supported"]["nav2_planner_static_obstacle_repair_replan"])
        self.assertTrue(
            manifest["supported"]["nav2_navigate_to_pose_obstacle_retry_after_clear"]
        )
        self.assertTrue(
            manifest["supported"][
                "nav2_navigate_to_pose_autonomous_same_goal_obstacle_recovery"
            ]
        )
        self.assertTrue(manifest["supported"]["nav2_rmf_upstream_concurrency8_workload"])
        self.assertTrue(manifest["supported"]["nav2_rmf_upstream_concurrency16_workload"])
        self.assertTrue(manifest["supported"]["nav2_rmf_upstream_concurrency32_workload"])
        self.assertTrue(manifest["supported"]["nav2_rmf_upstream_concurrency64_workload"])
        self.assertTrue(manifest["supported"]["nav2_rmf_upstream_concurrency128_workload"])
        self.assertTrue(manifest["supported"]["nav2_rmf_upstream_concurrency256_workload"])
        self.assertTrue(manifest["supported"]["nav2_rmf_upstream_concurrency512_workload"])
        self.assertTrue(manifest["supported"]["nav2_rmf_upstream_concurrency1024_workload"])
        self.assertTrue(manifest["supported"]["nav2_rmf_upstream_concurrency2048_workload"])
        self.assertTrue(
            manifest["supported"][
                "nav2_rmf_upstream_total4096_admission_window8_workload"
            ]
        )
        self.assertTrue(manifest["supported"]["omnetpp_template_input_generation"])
        self.assertTrue(
            manifest["supported"][
                "omnetpp_6_4_inet_4_7_docker_udp_trace_replay_runtime"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "ns3_omnetpp_matched_p2p_bounded_parity_8_16_32_3seed"
            ]
        )
        self.assertNotIn("network_flow_endpoints", manifest["unsupported"])
        self.assertNotIn("publisher_allocations", manifest["unsupported"])
        self.assertNotIn("subscription_allocations", manifest["unsupported"])
        self.assertNotIn("publisher_events", manifest["unsupported"])
        self.assertNotIn("subscription_events", manifest["unsupported"])
        self.assertNotIn("event_callbacks", manifest["unsupported"])
        self.assertNotIn("qos_event_callbacks", manifest["unsupported"])
        self.assertNotIn("qos_event_production", manifest["unsupported"])
        self.assertNotIn("qos_event_waitable_readiness", manifest["unsupported"])
        self.assertNotIn("timer_driven_idle_deadline_events", manifest["unsupported"])
        self.assertNotIn("full_qos_event_waitable_readiness", manifest["unsupported"])
        self.assertIn(
            "full_liveliness_event_production",
            manifest["unsupported"],
        )
        self.assertIn(
            "full_message_lost_event_production",
            manifest["unsupported"],
        )
        self.assertNotIn(
            "full_remote_graph_qos_type_message_lost_event_production",
            manifest["unsupported"],
        )
        self.assertIn(
            "full_remote_event_dds_vendor_semantics",
            manifest["unsupported"],
        )
        self.assertNotIn("liveliness_incompatible_type_and_message_lost_event_production", manifest["unsupported"])
        self.assertNotIn("content_filtered_topics", manifest["unsupported"])
        self.assertNotIn("content_filter_enforcement", manifest["unsupported"])
        self.assertIn("full_dds_content_filter_expression_dialect", manifest["unsupported"])
        self.assertNotIn("standalone_serialization_size", manifest["unsupported"])
        self.assertIn(
            "unbounded_standalone_serialization_size", manifest["unsupported"]
        )
        self.assertNotIn("long_stress_security_campaign", manifest["unsupported"])
        self.assertTrue(
            manifest["supported"]["docker_long_stress_security_campaign_1hour_netem"]
        )
        self.assertNotIn(
            "full_bidirectional_integrated_quic_transport_backend",
            manifest["unsupported"],
        )
        self.assertIn("production_quic_transport_backend", manifest["unsupported"])
        self.assertNotIn("concurrent_full_duplex_quic_operation_loop", manifest["unsupported"])
        self.assertNotIn(
            "multi_threaded_concurrent_rmw_publish_take_quic_operation_loop",
            manifest["unsupported"],
        )
        self.assertNotIn("stateful_fleetqox_quic_gateway_service", manifest["unsupported"])
        self.assertTrue(manifest["supported"]["stateful_fleetqox_quic_gateway_service"])
        self.assertTrue(manifest["supported"]["docker_quic_stateful_gateway_5run_netem"])
        self.assertTrue(
            manifest["supported"]["stateful_quic_gateway_interprocess_rmw_publish_take"]
        )
        self.assertTrue(
            manifest["supported"]["docker_quic_stateful_rmw_publish_take_5run_netem"]
        )
        self.assertNotIn("integrated_quic_qlog_export", manifest["unsupported"])
        self.assertIn("full_sros2_policy_enforcement", manifest["unsupported"])
        self.assertNotIn(
            "runtime_sros2_permissions_signature_validation",
            manifest["unsupported"],
        )
        self.assertNotIn("sros2_governance_xml_enforcement", manifest["unsupported"])
        self.assertNotIn(
            "sros2_remote_peer_identity_authentication",
            manifest["unsupported"],
        )
        self.assertIn(
            "forward_secret_asymmetric_session_key_exchange",
            manifest["unsupported"],
        )
        self.assertNotIn(
            "certificate_revocation_enforcement", manifest["unsupported"]
        )
        self.assertIn(
            "sros2_governance_transport_encryption_and_signing",
            manifest["unsupported"],
        )
        self.assertNotIn(
            "sros2_action_authorization",
            manifest["unsupported"],
        )
        self.assertIn("production_security_hardening", manifest["unsupported"])
        self.assertNotIn("omnetpp_inet_runtime_execution", manifest["unsupported"])
        self.assertNotIn("ns3_omnetpp_runtime_parity", manifest["unsupported"])
        self.assertIn(
            "omnetpp_inet_tsn_mesh_runtime_execution", manifest["unsupported"]
        )
        self.assertIn(
            "ns3_omnetpp_full_tsn_mesh_wireless_runtime_parity",
            manifest["unsupported"],
        )
        self.assertNotIn("integrated_quic_transport_backend", manifest["unsupported"])
        self.assertNotIn("quic_path_telemetry", manifest["unsupported"])
        claims = manifest["claim_boundaries"]
        self.assertTrue(
            manifest["supported"]["docker_split_scope_rmw_comparison_8_16_32_3seed"]
        )
        self.assertTrue(
            manifest["supported"]["docker_same_hop_rmw_comparison_8_16_32_3seed"]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_same_hop_generic_serialized_rmw_comparison_8_16_32_3seed"
            ]
        )
        self.assertTrue(
            manifest["supported"]["reception_baseline_cumulative_ack_floor"]
        )
        self.assertTrue(manifest["supported"]["ack_baseline_reorder_safety"])
        self.assertTrue(
            manifest["supported"][
                "docker_same_hop_generic_serialized_rmw_comparison_36of36_postfix"
            ]
        )
        self.assertTrue(
            manifest["supported"]["initial_source_sequence_timeout_repair"]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_initial_source_sequence_timeout_repair_probe"
            ]
        )
        self.assertFalse(claims["same_hop_cross_rmw_superiority"])
        self.assertTrue(claims["same_hop_delivery_reliability_comparison_claim"])
        self.assertTrue(
            claims["same_hop_opaque_serialized_payload_forwarding_claim"]
        )
        self.assertTrue(
            claims[
                "same_hop_middle_payload_serialization_state_claim"
            ]
        )
        self.assertFalse(claims["same_hop_latency_superiority_claim"])
        self.assertTrue(claims["same_hop_middle_processing_equivalence_claim"])
        self.assertTrue(
            claims["same_hop_latency_distribution_comparison_claim"]
        )
        self.assertTrue(
            claims["same_hop_common_middle_termination_republish_claim"]
        )
        self.assertTrue(
            claims["docker_same_hop_common_generic_middle_36of36_claim"]
        )
        self.assertTrue(
            claims["same_hop_resume_configuration_fail_closed_claim"]
        )
        self.assertTrue(
            claims["same_hop_exact_configuration_resume_36of36_claim"]
        )
        self.assertTrue(
            claims["docker_same_hop_profile_mismatch_rerun_2of2_claim"]
        )
        self.assertTrue(
            claims["same_hop_profile_sensitivity_comparison_claim"]
        )
        self.assertTrue(
            claims["docker_same_hop_profile_sensitivity_36of36_claim"]
        )
        self.assertTrue(
            claims[
                "same_hop_profile_robot_scale_sensitivity_comparison_claim"
            ]
        )
        self.assertTrue(
            claims[
                "docker_same_hop_profile_scale_sensitivity_108of108_claim"
            ]
        )
        self.assertTrue(claims["same_hop_exact_payload_size_contract_claim"])
        self.assertTrue(claims["same_hop_payload_sensitivity_comparison_claim"])
        self.assertTrue(
            claims["same_hop_payload_complete_measurement_matrix_claim"]
        )
        self.assertTrue(
            claims[
                "docker_same_hop_payload_sensitivity_36of36_measured_claim"
            ]
        )
        self.assertTrue(
            claims["docker_same_hop_payload_delivery_contract_18of36_claim"]
        )
        self.assertFalse(
            claims[
                "same_hop_payload_latency_distribution_comparison_claim"
            ]
        )
        self.assertTrue(
            claims["same_hop_offered_load_sensitivity_comparison_claim"]
        )
        self.assertTrue(
            claims["same_hop_offered_load_complete_measurement_matrix_claim"]
        )
        self.assertTrue(
            claims[
                "docker_same_hop_offered_load_sensitivity_36of36_measured_claim"
            ]
        )
        self.assertTrue(
            claims[
                "docker_same_hop_offered_load_delivery_contract_0of36_claim"
            ]
        )
        self.assertFalse(
            claims["same_hop_32768_fully_successful_schedule_claim"]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_same_hop_common_generic_middle_8_16_32_3seed"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "same_hop_common_middle_application_processing"
            ]
        )
        self.assertTrue(
            manifest["supported"]["same_hop_resume_configuration_validation"]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_same_hop_resume_profile_mismatch_rerun"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_same_hop_profile_sensitivity_16robot_3profile_3seed"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "same_hop_profile_sensitivity_common_middle"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_same_hop_profile_scale_sensitivity_8_16_32_3profile_3seed"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "same_hop_profile_robot_scale_full_factorial_common_middle"
            ]
        )
        self.assertTrue(
            manifest["supported"]["same_hop_exact_payload_size_injection"]
        )
        self.assertTrue(
            manifest["supported"]["same_hop_payload_size_resume_provenance"]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_same_hop_payload_sensitivity_16robot_3size_3seed"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "same_hop_payload_complete_measurement_matrix"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_same_hop_offered_load_sensitivity_32768b_16robot_3interval_3seed"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "same_hop_offered_load_complete_measurement_matrix"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "udp_stable_fragment_identity_across_retransmission"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "udp_loss_resilient_fragment_accumulation"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_loss_resilient_large_sample_fragment_32768b_5run_netem"
            ]
        )
        self.assertTrue(
            manifest["supported"][
                "udp_fragment_specific_nack_selective_retransmission"
            ]
        )
        self.assertTrue(
            manifest["supported"]["bounded_async_fragment_send_queue"]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_selective_fragment_repair_without_whole_sample_retry"
            ]
        )
        self.assertIn(
            "loss_resilient_large_sample_fragment_repair",
            manifest["partial"],
        )
        self.assertIn(
            "production_loss_resilient_large_sample_fragment_repair",
            manifest["unsupported"],
        )
        self.assertNotIn(
            "production_loss_resilient_large_sample_selective_fragment_repair",
            manifest["unsupported"],
        )
        self.assertTrue(
            claims["loss_resilient_large_sample_fragment_repair_claim"]
        )
        self.assertTrue(
            claims["docker_loss_resilient_large_sample_fragment_5of5_claim"]
        )
        self.assertTrue(
            claims["fragment_specific_nack_selective_retransmission_claim"]
        )
        self.assertTrue(
            claims[
                "docker_selective_fragment_repair_without_whole_sample_retry_claim"
            ]
        )
        self.assertTrue(claims["bounded_async_fragment_send_queue_claim"])
        self.assertTrue(claims["fleet_scale_selective_fragment_repair_claim"])
        self.assertTrue(claims["production_large_sample_reliability_claim"])
        self.assertTrue(
            claims[
                "docker_same_hop_generic_serialized_rmw_comparison_8_16_32_3seed"
            ]
        )
        self.assertTrue(
            claims["reception_baseline_cumulative_ack_floor_claim"]
        )
        self.assertTrue(claims["ack_baseline_reorder_safety_claim"])
        self.assertTrue(
            claims[
                "docker_same_hop_generic_serialized_rmw_comparison_36of36_postfix"
            ]
        )
        self.assertTrue(
            claims["initial_source_sequence_timeout_repair_claim"]
        )
        self.assertTrue(claims["docker_same_hop_rmw_comparison_8_16_32_3seed"])
        self.assertTrue(claims["docker_two_container_shared_memory_100kb"])
        self.assertTrue(claims["docker_shared_memory_udp_hybrid_dedup"])
        self.assertTrue(claims["docker_publisher_subscription_payload_scratch_allocation"])
        self.assertTrue(
            claims["docker_publisher_subscription_payload_scratch_allocation_5run_probe"]
        )
        self.assertTrue(claims["publisher_subscription_payload_scratch_reuse_claim"])
        self.assertTrue(claims["docker_nav2_planner_controller_lifecycle_configure"])
        self.assertTrue(claims["docker_nav2_planner_controller_lifecycle_activate_dynamic_tf"])
        self.assertTrue(claims["docker_nav2_planner_compute_path_action_map_tf"])
        self.assertTrue(claims["docker_nav2_controller_follow_path_action_map_tf_odom"])
        self.assertTrue(claims["docker_nav2_navigate_to_pose_same_pose_bt_pipeline"])
        self.assertTrue(claims["docker_nav2_navigate_to_pose_repeated_same_pose_bt_pipeline"])
        self.assertTrue(claims["docker_nav2_navigate_to_pose_moving_base_bt_pipeline"])
        self.assertTrue(
            claims["docker_nav2_navigate_to_pose_extended_moving_base_bt_pipeline"]
        )
        self.assertTrue(claims["docker_nav2_behavior_server_spin_action"])
        self.assertTrue(claims["docker_nav2_navigate_to_pose_recovery_tree_fallback"])
        self.assertTrue(
            claims["docker_nav2_navigate_to_pose_recovered_success_after_spin"]
        )
        self.assertTrue(
            claims["docker_nav2_navigate_to_pose_recovered_success_repeated_smoke"]
        )
        self.assertTrue(claims["docker_nav2_navigate_to_pose_long_moving_workload"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_concurrency8"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_concurrency16"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_concurrency32"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_concurrency64"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_concurrency128"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_concurrency256"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_concurrency512"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_concurrency1024"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_concurrency2048"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_concurrency4096"])
        self.assertTrue(claims["nav2_rmf_unwindowed_4096_claim"])
        self.assertTrue(claims["docker_nav2_rmf_upstream_total4096_admission_window8"])
        self.assertTrue(claims["full_nav2_navigation_stack_claim"])
        self.assertTrue(claims["nav2_rmf_larger_upstream_client_count_claim"])
        self.assertTrue(claims["nav2_rmf_total4096_admission_window_claim"])
        self.assertTrue(claims["moving_robot_navigation_claim"])
        self.assertTrue(claims["extended_moving_navigation_claim"])
        self.assertTrue(claims["nav2_recovery_behavior_claim"])
        self.assertTrue(claims["navigate_to_pose_recovery_tree_claim"])
        self.assertTrue(claims["successful_recovered_navigation_claim"])
        self.assertTrue(claims["repeated_recovered_navigation_claim"])
        self.assertTrue(claims["docker_nav2_planner_static_obstacle_repair"])
        self.assertTrue(claims["docker_nav2_navigate_to_pose_obstacle_retry_after_clear"])
        self.assertTrue(
            claims["docker_nav2_navigate_to_pose_autonomous_same_goal_obstacle_recovery"]
        )
        self.assertTrue(claims["planner_static_obstacle_repair_claim"])
        self.assertTrue(claims["nav2_obstacle_retry_after_clear_claim"])
        self.assertTrue(claims["obstacle_field_recovery_claim"])
        self.assertTrue(claims["full_nav2_obstacle_recovery_claim"])
        self.assertTrue(claims["autonomous_same_goal_nav2_obstacle_recovery_claim"])
        self.assertTrue(claims["long_navigation_workload_claim"])
        self.assertFalse(claims["deep_preallocation_claim"])
        self.assertTrue(claims["docker_qos_profile_compatibility_full_matrix_probe"])
        self.assertTrue(claims["qos_profile_compatibility_full_matrix_claim"])
        self.assertTrue(claims["docker_qos_event_noop_abi"])
        self.assertTrue(claims["docker_qos_deadline_event_production"])
        self.assertTrue(claims["qos_event_production_claim"])
        self.assertTrue(claims["docker_qos_deadline_event_waitable_readiness"])
        self.assertTrue(claims["qos_deadline_event_waitable_readiness_claim"])
        self.assertTrue(claims["docker_qos_deadline_timer_driven_idle_events"])
        self.assertTrue(claims["qos_deadline_timer_driven_idle_events_claim"])
        self.assertTrue(claims["remote_offered_deadline_missed_event_claim"])
        self.assertTrue(claims["remote_requested_deadline_missed_event_claim"])
        self.assertTrue(claims["remote_deadline_missed_event_repeated_claim"])
        self.assertTrue(claims["docker_qos_event_deadline_waitable_5run_probe"])
        self.assertTrue(claims["docker_qos_event_waitability_matrix_5run_probe"])
        self.assertTrue(claims["qos_event_waitability_matrix_claim"])
        self.assertTrue(claims["qos_event_waitability_repeated_claim"])
        self.assertTrue(claims["docker_qos_matched_event_production"])
        self.assertTrue(claims["docker_qos_matched_event_5run_probe"])
        self.assertTrue(claims["qos_matched_event_production_claim"])
        self.assertTrue(claims["docker_qos_reliability_incompatible_event_production"])
        self.assertTrue(claims["qos_reliability_incompatible_event_claim"])
        self.assertTrue(claims["docker_qos_durability_incompatible_event_production"])
        self.assertTrue(claims["qos_durability_incompatible_event_claim"])
        self.assertTrue(
            claims["docker_qos_reliability_durability_incompatible_event_5run_probe"]
        )
        self.assertTrue(claims["docker_qos_deadline_incompatible_event_production"])
        self.assertTrue(claims["docker_qos_deadline_incompatible_event_5run_probe"])
        self.assertTrue(claims["qos_deadline_incompatible_event_claim"])
        self.assertTrue(
            claims["qos_missing_offered_deadline_incompatible_event_claim"]
        )
        self.assertTrue(
            claims["qos_missing_offered_deadline_incompatible_repeated_claim"]
        )
        self.assertTrue(claims["missing_offered_deadline_offered_event_claim"])
        self.assertTrue(claims["missing_offered_deadline_requested_event_claim"])
        self.assertTrue(claims["docker_qos_type_incompatible_event_production"])
        self.assertTrue(claims["docker_qos_type_incompatible_event_5run_probe"])
        self.assertTrue(claims["qos_type_incompatible_event_claim"])
        self.assertTrue(claims["docker_qos_message_lost_event_production"])
        self.assertTrue(claims["docker_qos_message_lost_event_5run_probe"])
        self.assertTrue(claims["qos_message_lost_event_claim"])
        self.assertTrue(claims["qos_message_lost_best_effort_sequence_gap_claim"])
        self.assertTrue(claims["qos_message_lost_repair_reorder_suppression_claim"])
        self.assertTrue(claims["qos_message_lost_reliable_history_exhaustion_claim"])
        self.assertTrue(claims["unrecoverable_loss_notice_subscriber_identity_claim"])
        self.assertTrue(claims["remote_unrecoverable_loss_notice_claim"])
        self.assertTrue(claims["remote_message_lost_waitable_claim"])
        self.assertTrue(
            claims["duplicate_unrecoverable_loss_notice_deduplication_claim"]
        )
        self.assertTrue(claims["repeated_remote_message_lost_claim"])
        self.assertTrue(claims["repair_budget_terminal_loss_notice_claim"])
        self.assertTrue(claims["repair_attempt_limit_terminal_loss_notice_claim"])
        self.assertTrue(claims["repair_admission_terminal_loss_notice_claim"])
        self.assertTrue(
            claims["terminal_repair_duplicate_notice_deduplication_claim"]
        )
        self.assertTrue(claims["terminal_repair_clean_teardown_claim"])
        self.assertTrue(claims["terminal_repair_controls_repeated_claim"])
        self.assertTrue(claims["qos_best_effort_no_repair_request_claim"])
        self.assertFalse(claims["full_message_lost_event_production_claim"])
        self.assertTrue(claims["docker_qos_liveliness_event_production"])
        self.assertTrue(claims["docker_qos_liveliness_event_5run_probe"])
        self.assertTrue(claims["qos_liveliness_event_claim"])
        self.assertTrue(claims["automatic_liveliness_idle_renewal_claim"])
        self.assertTrue(
            claims["automatic_liveliness_false_loss_suppression_claim"]
        )
        self.assertTrue(claims["automatic_liveliness_repeated_claim"])
        self.assertTrue(claims["remote_manual_liveliness_idle_timeout_claim"])
        self.assertTrue(claims["remote_manual_liveliness_explicit_assert_claim"])
        self.assertTrue(claims["remote_manual_liveliness_publish_assert_claim"])
        self.assertTrue(claims["remote_publisher_liveliness_lost_event_claim"])
        self.assertTrue(
            claims["remote_manual_liveliness_graph_lease_independence_claim"]
        )
        self.assertTrue(claims["remote_manual_liveliness_repeated_claim"])
        self.assertTrue(claims["remote_all_jazzy_event_types_path_claim"])
        self.assertTrue(
            claims["remote_event_wait_take_callback_coverage_claim"]
        )
        self.assertTrue(
            claims["remote_liveliness_multi_endpoint_independence_claim"]
        )
        self.assertTrue(
            claims["remote_liveliness_alive_not_alive_remove_claim"]
        )
        self.assertTrue(claims["remote_liveliness_endpoint_churn_recreate_claim"])
        self.assertTrue(
            claims["remote_liveliness_expiry_preserves_matching_claim"]
        )
        self.assertTrue(
            claims["remote_liveliness_multi_endpoint_repeated_claim"]
        )
        self.assertTrue(claims["liveliness_manual_multi_endpoint_scale_claim"])
        self.assertTrue(
            claims["liveliness_system_default_automatic_renewal_claim"]
        )
        self.assertTrue(claims["liveliness_scale_repeated_claim"])
        self.assertTrue(claims["remote_liveliness_64_endpoint_scale_claim"])
        self.assertTrue(
            claims["remote_liveliness_exact_aggregate_transition_claim"]
        )
        self.assertTrue(claims["remote_liveliness_scale_repeated_claim"])
        self.assertTrue(claims["liveliness_default_lease_lifecycle_claim"])
        self.assertTrue(
            claims["liveliness_unresolved_policy_fail_closed_claim"]
        )
        self.assertTrue(claims["liveliness_default_lease_repeated_claim"])
        self.assertTrue(claims["system_default_infinite_lease_lifecycle_claim"])
        self.assertTrue(claims["automatic_infinite_lease_lifecycle_claim"])
        self.assertTrue(claims["manual_infinite_lease_lifecycle_claim"])
        self.assertTrue(claims["best_available_infinite_lease_lifecycle_claim"])
        self.assertTrue(claims["unknown_liveliness_fail_closed_claim"])
        self.assertTrue(claims["deprecated_manual_by_node_fail_closed_claim"])
        self.assertTrue(
            claims["qos_liveliness_incompatible_event_production_claim"]
        )
        self.assertTrue(
            claims["qos_liveliness_incompatible_event_repeated_claim"]
        )
        self.assertTrue(claims["liveliness_kind_offered_event_claim"])
        self.assertTrue(claims["liveliness_kind_requested_event_claim"])
        self.assertTrue(claims["liveliness_slow_lease_offered_event_claim"])
        self.assertTrue(claims["liveliness_slow_lease_requested_event_claim"])
        self.assertTrue(claims["liveliness_missing_lease_offered_event_claim"])
        self.assertTrue(claims["liveliness_missing_lease_requested_event_claim"])
        self.assertTrue(claims["liveliness_compatible_control_claim"])
        self.assertTrue(claims["qos_best_available_endpoint_adaptation_claim"])
        self.assertTrue(
            claims["qos_best_available_endpoint_adaptation_repeated_claim"]
        )
        self.assertTrue(claims["best_publisher_manual_selection_claim"])
        self.assertTrue(claims["best_subscription_automatic_selection_claim"])
        self.assertTrue(claims["zero_endpoint_best_available_defaults_claim"])
        self.assertTrue(claims["mixed_publishers_automatic_max_lease_claim"])
        self.assertTrue(claims["best_available_policy_frozen_after_create_claim"])
        self.assertFalse(claims["full_liveliness_event_production_claim"])
        self.assertFalse(claims["full_non_deadline_qos_event_production_claim"])
        self.assertTrue(claims["full_qos_event_waitable_readiness_claim"])
        self.assertTrue(claims["docker_content_filter_set_get_noop_abi"])
        self.assertTrue(claims["docker_content_filter_key_value_enforcement"])
        self.assertTrue(claims["docker_content_filter_std_msgs_string_text_enforcement"])
        self.assertTrue(claims["docker_content_filter_dynamic_reconfigure_disable"])
        self.assertTrue(claims["docker_content_filter_repeated_enforcement_5run_probe"])
        self.assertTrue(claims["content_filter_enforcement_claim"])
        self.assertTrue(claims["docker_content_filter_sql_subset_5run_probe"])
        self.assertTrue(claims["content_filter_sql_subset_claim"])
        self.assertTrue(
            claims["content_filter_invalid_expression_fail_closed_claim"]
        )
        self.assertTrue(claims["content_filter_sql_subset_repeated_claim"])
        self.assertTrue(
            claims["content_filter_sql_reversed_comparison_operand_order_claim"]
        )
        self.assertTrue(claims["docker_content_filter_typed_reflection_5run_probe"])
        self.assertTrue(
            claims["content_filter_introspection_cpp_nested_fields_claim"]
        )
        self.assertTrue(
            claims["content_filter_introspection_c_nested_fields_claim"]
        )
        self.assertTrue(
            claims["content_filter_introspection_cpp_array_fields_claim"]
        )
        self.assertTrue(
            claims["content_filter_malformed_typed_payload_fail_closed_claim"]
        )
        self.assertTrue(claims["content_filter_typed_reflection_repeated_claim"])
        self.assertTrue(claims["fleetqox_generic_serialized_middle_relay_claim"])
        self.assertTrue(
            claims["fleetqox_middle_rmw_termination_republish_claim"]
        )
        self.assertTrue(claims["fleetqox_direct_peer_topology_claim"])
        self.assertTrue(claims["fleetqox_generic_relay_repeated_netem_claim"])
        self.assertFalse(claims["full_dds_content_filter_expression_claim"])
        self.assertTrue(claims["docker_security_options_lifecycle_probe"])
        self.assertTrue(claims["docker_security_options_lifecycle_5run_probe"])
        self.assertTrue(claims["security_options_lifecycle_claim"])
        self.assertTrue(claims["docker_fleetqox_security_policy_enforcement_probe"])
        self.assertTrue(claims["fleetqox_security_policy_enforcement_claim"])
        self.assertTrue(claims["security_policy_repeated_enforcement_claim"])
        self.assertTrue(claims["docker_sros2_permissions_xml_publish_enforcement_probe"])
        self.assertTrue(claims["sros2_permissions_xml_publish_enforcement_claim"])
        self.assertTrue(claims["sros2_permissions_xml_subscribe_enforcement_claim"])
        self.assertTrue(claims["sros2_permissions_xml_pubsub_enforcement_claim"])
        self.assertTrue(claims["sros2_permissions_xml_repeated_enforcement_claim"])
        self.assertTrue(
            claims["sros2_permissions_xml_subscribe_repeated_enforcement_claim"]
        )
        self.assertTrue(claims["malformed_permissions_fail_closed_claim"])
        self.assertTrue(claims["runtime_sros2_permissions_signature_validation_claim"])
        self.assertTrue(claims["tampered_signed_permissions_fail_closed_claim"])
        self.assertTrue(claims["sros2_service_request_reply_authorization_claim"])
        self.assertTrue(claims["sros2_service_repeated_authorization_claim"])
        self.assertTrue(claims["sros2_action_authorization_claim"])
        self.assertTrue(claims["sros2_action_repeated_authorization_claim"])
        self.assertTrue(claims["sros2_action_allowed_end_to_end_claim"])
        self.assertTrue(claims["sros2_action_call_denied_fail_closed_claim"])
        self.assertTrue(claims["sros2_action_execute_denied_fail_closed_claim"])
        self.assertTrue(claims["sros2_action_call_execute_decision_matrix_claim"])
        self.assertTrue(claims["sros2_action_authorization_metrics_claim"])
        self.assertTrue(claims["governance_xml_enforcement_claim"])
        self.assertTrue(claims["sros2_governance_access_control_claim"])
        self.assertTrue(claims["sros2_governance_repeated_access_control_claim"])
        self.assertTrue(
            claims["sros2_governance_runtime_signature_validation_claim"]
        )
        self.assertTrue(
            claims["sros2_governance_transport_protection_fail_closed_claim"]
        )
        self.assertTrue(
            claims["sros2_tampered_signed_governance_fail_closed_claim"]
        )
        self.assertFalse(claims["governance_transport_security_claim"])
        self.assertTrue(claims["sros2_local_identity_credentials_validation_claim"])
        self.assertTrue(
            claims["sros2_local_identity_credentials_repeated_validation_claim"]
        )
        self.assertTrue(
            claims["sros2_tampered_identity_certificate_fail_closed_claim"]
        )
        self.assertTrue(
            claims["sros2_identity_private_key_mismatch_fail_closed_claim"]
        )
        self.assertTrue(
            claims["sros2_identity_enclave_mismatch_fail_closed_claim"]
        )
        self.assertTrue(claims["sros2_peer_identity_authentication_claim"])
        self.assertTrue(
            claims["sros2_peer_identity_repeated_authentication_claim"]
        )
        self.assertTrue(claims["udp_peer_identity_allowlist_fail_closed_claim"])
        self.assertTrue(claims["udp_peer_signature_tamper_fail_closed_claim"])
        self.assertTrue(
            claims["udp_peer_untrusted_certificate_fail_closed_claim"]
        )
        self.assertTrue(claims["udp_certificate_authenticated_aead_claim"])
        self.assertTrue(claims["udp_authenticated_psk_session_key_derivation_claim"])
        self.assertTrue(claims["udp_session_key_rotation_claim"])
        self.assertTrue(claims["dynamic_serialization_support_claim"])
        self.assertTrue(claims["dynamic_serialization_support_repeated_claim"])
        self.assertTrue(claims["dynamic_message_take_claim"])
        self.assertTrue(claims["dynamic_message_take_with_info_claim"])
        self.assertTrue(claims["message_info_sequence_features_claim"])
        self.assertTrue(claims["session_key_establishment_claim"])
        self.assertTrue(claims["forward_secrecy_claim"])
        self.assertTrue(claims["asymmetric_session_key_exchange_claim"])
        self.assertTrue(claims["certificate_revocation_claim"])
        self.assertTrue(claims["udp_aead_authenticated_encryption_claim"])
        self.assertTrue(claims["udp_aead_repeated_authenticated_encryption_claim"])
        self.assertTrue(claims["udp_aead_tamper_fail_closed_claim"])
        self.assertTrue(claims["udp_aead_strict_missing_key_fail_closed_claim"])
        self.assertFalse(claims["dds_security_interoperability_claim"])
        self.assertTrue(claims["docker_stress_security_campaign_smoke"])
        self.assertTrue(claims["stress_security_repeated_claim"])
        self.assertTrue(claims["long_stress_security_campaign_claim"])
        self.assertFalse(claims["sros2_policy_enforcement_claim"])
        self.assertFalse(claims["production_security_hardening_claim"])
        self.assertTrue(claims["docker_loaned_message_lifecycle_c_cpp"])
        self.assertTrue(claims["docker_netem_ngtcp2_quic_path_telemetry"])
        self.assertTrue(claims["docker_ngtcp2_quic_gateway_publish_path_probe"])
        self.assertTrue(claims["docker_ngtcp2_quic_gateway_async_publish_path_probe"])
        self.assertTrue(claims["docker_ngtcp2_quic_gateway_async_burst_path_probe"])
        self.assertTrue(claims["docker_netem_ngtcp2_quic_gateway_publish_path_probe"])
        self.assertTrue(claims["docker_netem_ngtcp2_quic_gateway_async_burst_path_probe"])
        self.assertTrue(claims["docker_ngtcp2_quic_gateway_session_reuse_file_probe"])
        self.assertTrue(claims["docker_ngtcp2_quic_gateway_take_path_probe"])
        self.assertTrue(claims["docker_ngtcp2_quic_gateway_rmw_take_path_probe"])
        self.assertTrue(
            claims["docker_ngtcp2_quic_gateway_rmw_take_session_reuse_file_probe"]
        )
        self.assertTrue(
            claims[
                "docker_ngtcp2_quic_gateway_rmw_take_session_reuse_5download_probe"
            ]
        )
        self.assertTrue(
            claims["docker_ngtcp2_quic_gateway_bidirectional_publish_take_probe"]
        )
        self.assertTrue(
            claims["docker_ngtcp2_quic_gateway_bidirectional_publish_take_5run_probe"]
        )
        self.assertTrue(claims["docker_netem_quic_inprocess_rmw_bidirectional_probe"])
        self.assertTrue(claims["quic_inprocess_same_connection_reuse_claim"])
        self.assertTrue(claims["quic_inprocess_untrusted_ca_rejection_claim"])
        self.assertTrue(claims["quic_inprocess_client_qlog_export_claim"])
        self.assertTrue(
            manifest["supported"]["quic_inprocess_concurrent_post_get_stream_pair"]
        )
        self.assertTrue(
            manifest["supported"][
                "docker_netem_quic_inprocess_concurrent_post_get_stream_pair"
            ]
        )
        self.assertTrue(
            claims["docker_netem_quic_inprocess_concurrent_stream_pair_probe"]
        )
        self.assertTrue(claims["quic_concurrent_full_duplex_operation_claim"])
        self.assertTrue(
            claims["quic_multi_threaded_rmw_publish_take_operation_claim"]
        )
        self.assertTrue(claims["docker_quic_gateway_disable_early_data_control"])
        self.assertTrue(claims["docker_quic_gateway_async_burst_soak_smoke"])
        self.assertTrue(claims["docker_quic_gateway_async_burst_soak_3run_netem"])
        self.assertTrue(claims["docker_quic_gateway_async_burst_soak_10run_netem"])
        self.assertTrue(claims["quic_zero_rtt_claim"])
        self.assertFalse(claims["production_quic_backend_claim"])
        self.assertTrue(claims["rmw_integrated_quic_backend_claim"])
        self.assertTrue(claims["full_bidirectional_quic_backend_claim"])
        self.assertFalse(claims["zero_copy_loaned_message_claim"])
        self.assertTrue(claims["native_ns3_wifi_mobility_matrix_8_16_32_3seed"])
        self.assertTrue(claims["native_ns3_wifi_roaming_matrix_8_16_32_3seed"])
        self.assertTrue(claims["ns3_wifi_model_claim"])
        self.assertTrue(claims["ns3_mobility_model_claim"])
        self.assertTrue(claims["ns3_roaming_handoff_claim"])
        self.assertTrue(claims["omnetpp_template_integrity_claim"])
        self.assertTrue(claims["omnetpp_input_trace_claim"])
        self.assertTrue(claims["omnetpp_inet_runtime_claim"])
        self.assertTrue(claims["omnetpp_parity_claim"])
        self.assertTrue(claims["ns3_omnetpp_parity_claim"])
        self.assertFalse(claims["full_tsn_mesh_parity_claim"])
        self.assertFalse(claims["high_fidelity_wireless_parity_claim"])
        self.assertFalse(claims["high_fidelity_wireless_simulator_claim"])
        cmake = (PKG / "CMakeLists.txt").read_text()
        self.assertIn("FILES capabilities.json", cmake)
        self.assertIn("fleetrmw_transport_loop_repair_smoke", cmake)
        self.assertIn("fleetrmw_action_frame_roundtrip_smoke", cmake)
        self.assertIn("fleetrmw_quic_dependency_link_smoke", cmake)

    def test_remote_graph_event_production_contract(self) -> None:
        pubsub = (PKG / "src" / "rmw_pubsub.cpp").read_text()
        self.assertIn("struct RemotePubSubEndpoint", pubsub)
        self.assertIn("g_remote_pubsub_endpoints", pubsub)
        self.assertIn("apply_remote_pubsub_event_advertisement", pubsub)
        self.assertIn("remote_endpoint_descriptor_equal", pubsub)
        self.assertIn("purge_expired_remote_pubsub_endpoints_locked", pubsub)
        self.assertIn("record_remote_endpoint_discovered_locked", pubsub)
        self.assertIn("record_remote_endpoint_removed_locked", pubsub)
        self.assertIn("matched_subscription_count_locked", pubsub)
        self.assertIn("matched_publisher_count_locked", pubsub)
        self.assertIn("rmw_fleetqox_cpp_remote_graph_event_endpoint_expiries", pubsub)

        probe = (PKG / "src" / "remote_event_probe.cpp").read_text()
        self.assertIn("fleetrmw.remote_event_probe.v1", probe)
        self.assertIn("RMW_EVENT_PUBLICATION_MATCHED", probe)
        self.assertIn("RMW_EVENT_SUBSCRIPTION_MATCHED", probe)
        self.assertIn("RMW_EVENT_OFFERED_QOS_INCOMPATIBLE", probe)
        self.assertIn("RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE", probe)
        self.assertIn("RMW_QOS_POLICY_DURABILITY_TRANSIENT_LOCAL", probe)
        self.assertIn("offered_durability_total_count", probe)
        self.assertIn("requested_deadline_total_count", probe)
        self.assertIn("RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE", probe)
        self.assertIn("RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE", probe)
        self.assertIn("RMW_EVENT_LIVELINESS_CHANGED", probe)
        self.assertIn("remote_graph_guard_renewal_suppressed", probe)
        self.assertIn("--crash-without-remove", probe)

        runner = (ROOT / "scripts" / "run_rmw_docker_remote_event_probe.py").read_text()
        self.assertIn("fleetrmw.docker_remote_event_probe.v1", runner)
        self.assertIn("real_udp_multicontainer", runner)
        self.assertIn("renewal_deduplication", runner)
        self.assertIn("remote_graph_guard_notification", runner)
        self.assertIn("lease_expiry_run_count", runner)

        remote_deadline_probe = (
            PKG / "src" / "remote_deadline_event_probe.cpp"
        ).read_text()
        self.assertIn(
            "fleetrmw.remote_deadline_event_probe.v1", remote_deadline_probe
        )
        self.assertIn("RMW_EVENT_OFFERED_DEADLINE_MISSED", remote_deadline_probe)
        self.assertIn("RMW_EVENT_REQUESTED_DEADLINE_MISSED", remote_deadline_probe)
        self.assertIn("rmw_publish_serialized_message", remote_deadline_probe)
        remote_deadline_runner = (
            ROOT / "scripts" / "run_rmw_docker_remote_deadline_event_probe.py"
        ).read_text()
        self.assertIn(
            "fleetrmw.docker_remote_deadline_event_probe.v1",
            remote_deadline_runner,
        )
        self.assertIn("tc qdisc replace", remote_deadline_runner)
        self.assertIn(
            "remote_deadline_missed_event_repeated_claim",
            remote_deadline_runner,
        )

        manifest = json.loads((PKG / "capabilities.json").read_text())
        supported = manifest["supported"]
        claims = manifest["claim_boundaries"]
        self.assertTrue(supported["qos_publication_subscription_matched_events_remote_udp"])
        self.assertTrue(supported["qos_incompatible_events_remote_udp"])
        self.assertTrue(supported["qos_type_incompatible_events_remote_udp"])
        self.assertTrue(supported["qos_liveliness_changed_remote_graph_lifecycle_udp"])
        self.assertTrue(supported["docker_remote_graph_event_production_5run"])
        self.assertTrue(supported["docker_remote_deadline_missed_events_5run"])
        self.assertTrue(claims["remote_offered_deadline_missed_event_claim"])
        self.assertTrue(claims["remote_requested_deadline_missed_event_claim"])
        self.assertTrue(claims["remote_deadline_missed_event_repeated_claim"])
        self.assertTrue(
            claims["docker_remote_graph_matched_qos_type_liveliness_event_5run_probe"]
        )
        self.assertTrue(claims["remote_graph_matched_event_claim"])
        self.assertTrue(claims["remote_graph_incompatible_qos_event_claim"])
        self.assertTrue(claims["remote_graph_incompatible_type_event_claim"])
        self.assertTrue(claims["remote_graph_liveliness_lifecycle_event_claim"])
        self.assertTrue(claims["remote_graph_renewal_deduplication_claim"])
        self.assertFalse(claims["full_remote_graph_event_production_claim"])

    def test_identifier_library_exports_initial_rmw_symbols(self) -> None:
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("c++ compiler is not available")
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "librmw_fleetqox_cpp.so"
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-shared",
                    "-fPIC",
                    str(PKG / "src" / "rmw_identifier.cpp"),
                    "-o",
                    str(library),
                ],
                check=True,
                cwd=ROOT,
            )
            loaded = ctypes.CDLL(str(library))
            loaded.rmw_get_implementation_identifier.restype = ctypes.c_char_p
            loaded.rmw_get_serialization_format.restype = ctypes.c_char_p
            self.assertEqual(
                loaded.rmw_get_implementation_identifier().decode(),
                "rmw_fleetqox_cpp",
            )
            self.assertEqual(
                loaded.rmw_get_serialization_format().decode(),
                "fleetrmw.introspection_c.v1",
            )


if __name__ == "__main__":
    unittest.main()
