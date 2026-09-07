#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

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

// Publisher half of a two-process, real-network, lossy, large-sample soak
// probe for B0 (an intermittent `free(): invalid next size (fast)`
// corruption seen twice in long lossy 32-KiB runs, root cause unknown).
// A same-process 5,000/5,000 ASan/UBSan run of the typed publish/take path
// already came back clean, narrowing this to something that only surfaces
// across a genuine interprocess/lossy/fragment-repair boundary -- so unlike
// every other probe in this codebase, this one deliberately runs as two
// independent OS processes (not in-process) and is meant to be built with
// ASan/UBSan and run for a long time against a real netem-lossy Docker
// link, not to prove a claim on its own.
//
// Each sample's data is a fixed-width payload with an 8-byte big-endian
// sequence number prefix followed by a repeating byte pattern derived from
// that sequence number, so the subscriber side can detect corruption that
// manifests as wrong content rather than a crash, not just count deliveries.
//
// Usage: heap_soak_publisher_probe <payload_bytes> <sample_count>
//        <interval_ms> <linger_s>
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

}  // namespace

int main(int argc, char ** argv)
{
  const std::size_t payload_bytes = argc > 1 ? std::strtoul(argv[1], nullptr, 10) : 32768;
  const std::uint64_t sample_count = argc > 2 ? std::strtoull(argv[2], nullptr, 10) : 200;
  const int interval_ms = argc > 3 ? std::atoi(argv[3]) : 50;
  const int linger_s = argc > 4 ? std::atoi(argv[4]) : 10;

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
  rmw_publisher_t * publisher = node == nullptr ? nullptr : rmw_create_publisher(
    node, type_support, "/fleetqox/heap_soak", &qos, &publisher_options);
  if (publisher == nullptr) {
    std::cout << "{\"status\":\"endpoint_create_failed\"}" << std::endl;
    return 1;
  }

  std::uint64_t publish_failures = 0;
  for (std::uint64_t sequence = 0; sequence < sample_count; ++sequence) {
    std_msgs::msg::String sample;
    sample.data = make_payload(sequence, payload_bytes);
    if (rmw_publish(publisher, &sample, nullptr) != RMW_RET_OK) {
      ++publish_failures;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(interval_ms));
  }
  std::this_thread::sleep_for(std::chrono::seconds(linger_s));

  const bool destroy_ok =
    rmw_destroy_publisher(node, publisher) == RMW_RET_OK &&
    rmw_destroy_node(node) == RMW_RET_OK;

  std::cout << "{\"schema_version\":\"fleetrmw.heap_soak_publisher_probe.v1\",";
  std::cout << "\"status\":\"" << (destroy_ok ? "ok" : "failed") << "\",";
  std::cout << "\"payload_bytes\":" << payload_bytes << ",";
  std::cout << "\"sample_count\":" << sample_count << ",";
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
