#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include "rcutils/allocator.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/publisher_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rosidl_typesupport_interface/macros.h"
#include "std_msgs/msg/detail/string__struct.hpp"
#include "std_msgs/msg/detail/string__type_support.hpp"
#include "std_msgs/msg/string.hpp"

// Publisher half of a three-process, real-network, lossy, large-sample soak
// probe for B0 (an intermittent `free(): invalid next size (fast)`
// corruption seen twice in "long lossy 32-KiB runs"). Reading the git
// history for that exact wording found it directly adjacent to, and almost
// certainly describing, the same 16-robot/32-KiB/roaming-loss/seed-7 fleet
// frontier campaign as B1 -- whose documented repro command was
// `run_ros2_relay_rmw_netem_probe.py --robot-count 16`: a *three*-hop
// publisher -> relay -> subscriber topology (not a direct two-process
// link), with "robot_count" multiplexing that many topics through each of
// the three single processes, not spawning that many processes. This probe
// (plus heap_soak_subscriber_probe and the existing, already-ASan-clean
// generic_serialized_relay_probe as the relay) reproduces that same
// three-process/N-topic shape, entirely in hand-written C++ so ASan/UBSan
// stays uniformly linked -- see run_heap_soak_asan_probe.py's module
// docstring for why LD_PRELOAD-ing ASan into the rclpy-based original
// harness is a dead end (a real, unrelated ASan/CPython interceptor bug,
// confirmed to reproduce even at reduced loss).
//
// Each robot gets its own topic (`<topic_prefix>/robot_<NNNN>/state`) and
// its own independent sequence counter; each sample's data is a
// fixed-width payload with an 8-byte big-endian sequence-number prefix
// followed by a repeating byte pattern derived from that sequence number,
// so the subscriber can detect content corruption, not just crashes.
// Robots are published round-robin (one sample per robot per pass) rather
// than one robot fully at a time, matching how a real fleet's publishers
// would interleave.
//
// Usage: heap_soak_publisher_probe <payload_bytes> <robot_count>
//        <samples_per_robot> <interval_ms> <linger_s> [topic_prefix]
namespace
{

std::string make_payload(std::uint64_t sequence, std::size_t payload_bytes)
{
  std::string payload(payload_bytes, '\0');
  for (int byte = 7; byte >= 0 && static_cast<std::size_t>(7 - byte) < payload_bytes; --byte) {
    payload[7 - byte] = static_cast<char>((sequence >> (byte * 8)) & 0xFF);
  }
  const std::size_t header = payload_bytes < 8 ? payload_bytes : 8;
  for (std::size_t index = header; index < payload_bytes; ++index) {
    payload[index] = static_cast<char>((sequence + index) & 0xFF);
  }
  return payload;
}

std::string robot_topic(const std::string & prefix, std::size_t robot_index)
{
  char buffer[32];
  std::snprintf(buffer, sizeof(buffer), "%04zu", robot_index);
  return prefix + "/robot_" + buffer + "/state";
}

}  // namespace

int main(int argc, char ** argv)
{
  const std::size_t payload_bytes = argc > 1 ? std::strtoul(argv[1], nullptr, 10) : 32768;
  const std::size_t robot_count = argc > 2 ? std::strtoul(argv[2], nullptr, 10) : 16;
  const std::uint64_t samples_per_robot = argc > 3 ? std::strtoull(argv[3], nullptr, 10) : 10;
  const int interval_ms = argc > 4 ? std::atoi(argv[4]) : 50;
  const int linger_s = argc > 5 ? std::atoi(argv[5]) : 10;
  const std::string topic_prefix = argc > 6 ? argv[6] : "/fleetqox/heap_soak";

  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK ||
    rmw_init(&options, &context) != RMW_RET_OK)
  {
    std::cout << "{\"status\":\"context_init_failed\"}" << std::endl;
    return 1;
  }
  rmw_node_t * node = rmw_create_node(&context, "heap_soak_publisher", "/fleetqox");
  const auto * type_support =
    ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(
    rosidl_typesupport_cpp, std_msgs, msg, String)();

  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_RELIABLE;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 16;

  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  std::vector<rmw_publisher_t *> publishers;
  publishers.reserve(robot_count);
  for (std::size_t robot_index = 0; robot_index < robot_count; ++robot_index) {
    rmw_publisher_t * publisher = node == nullptr ? nullptr : rmw_create_publisher(
      node, type_support, robot_topic(topic_prefix, robot_index).c_str(),
      &qos, &publisher_options);
    if (publisher == nullptr) {
      std::cout << "{\"status\":\"endpoint_create_failed\",\"robot_index\":"
                << robot_index << "}" << std::endl;
      return 1;
    }
    publishers.push_back(publisher);
  }

  std::uint64_t publish_failures = 0;
  for (std::uint64_t round = 0; round < samples_per_robot; ++round) {
    for (std::size_t robot_index = 0; robot_index < robot_count; ++robot_index) {
      std_msgs::msg::String sample;
      sample.data = make_payload(round, payload_bytes);
      if (rmw_publish(publishers[robot_index], &sample, nullptr) != RMW_RET_OK) {
        ++publish_failures;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(interval_ms));
  }
  std::this_thread::sleep_for(std::chrono::seconds(linger_s));

  bool destroy_ok = true;
  for (rmw_publisher_t * publisher : publishers) {
    destroy_ok = rmw_destroy_publisher(node, publisher) == RMW_RET_OK && destroy_ok;
  }
  destroy_ok = rmw_destroy_node(node) == RMW_RET_OK && destroy_ok;

  std::cout << "{\"schema_version\":\"fleetrmw.heap_soak_publisher_probe.v2\",";
  std::cout << "\"status\":\"" << (destroy_ok ? "ok" : "failed") << "\",";
  std::cout << "\"payload_bytes\":" << payload_bytes << ",";
  std::cout << "\"robot_count\":" << robot_count << ",";
  std::cout << "\"samples_per_robot\":" << samples_per_robot << ",";
  std::cout << "\"publish_failures\":" << publish_failures << ",";
  std::cout << "\"destroy_ok\":" << (destroy_ok ? "true" : "false") << "}" << std::endl;

  const rmw_ret_t shutdown_ret = rmw_shutdown(&context);
  const rmw_ret_t context_fini_ret = rmw_context_fini(&context);
  const rmw_ret_t options_fini_ret = rmw_init_options_fini(&options);
  (void)shutdown_ret;
  (void)context_fini_ret;
  (void)options_fini_ret;
  return destroy_ok ? 0 : 1;
}
