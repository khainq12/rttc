#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

#include "rcutils/allocator.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/subscription_options.h"
#include "rosidl_typesupport_interface/macros.h"
#include "std_msgs/msg/detail/string__struct.hpp"
#include "std_msgs/msg/detail/string__type_support.hpp"
#include "std_msgs/msg/string.hpp"

// Subscriber half of heap_soak_publisher_probe; see that file for the
// rationale (B0 heap-corruption reproduction attempt under ASan/UBSan
// across a genuine two-process, real-network, lossy, large-sample link).
//
// Verifies each received sample's embedded sequence-number header against
// its expected repeating byte pattern, so corruption that manifests as
// wrong content (not just a crash) is also caught, not just counted.
//
// Usage: heap_soak_subscriber_probe <payload_bytes> <sample_count>
//        <total_wait_s>
namespace
{

bool payload_matches_sequence(const std::string & data, std::size_t payload_bytes)
{
  if (data.size() != payload_bytes) {
    return false;
  }
  std::uint64_t sequence = 0;
  const std::size_t header = payload_bytes < 8 ? payload_bytes : 8;
  for (std::size_t index = 0; index < header; ++index) {
    sequence = (sequence << 8) | static_cast<std::uint8_t>(data[index]);
  }
  for (std::size_t index = header; index < payload_bytes; ++index) {
    const auto expected = static_cast<char>((sequence + index) & 0xFF);
    if (data[index] != expected) {
      return false;
    }
  }
  return true;
}

}  // namespace

int main(int argc, char ** argv)
{
  const std::size_t payload_bytes = argc > 1 ? std::strtoul(argv[1], nullptr, 10) : 32768;
  const std::uint64_t sample_count = argc > 2 ? std::strtoull(argv[2], nullptr, 10) : 200;
  const int total_wait_s = argc > 3 ? std::atoi(argv[3]) : 60;

  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK ||
    rmw_init(&options, &context) != RMW_RET_OK)
  {
    std::cout << "{\"status\":\"context_init_failed\"}" << std::endl;
    return 1;
  }
  rmw_node_t * node = rmw_create_node(&context, "heap_soak_subscriber", "/fleetqox");
  const auto * type_support =
    ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(
    rosidl_typesupport_cpp, std_msgs, msg, String)();

  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_RELIABLE;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 16;

  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();
  rmw_subscription_t * subscription = node == nullptr ? nullptr : rmw_create_subscription(
    node, type_support, "/fleetqox/heap_soak", &qos, &subscription_options);
  if (subscription == nullptr) {
    std::cout << "{\"status\":\"endpoint_create_failed\"}" << std::endl;
    return 1;
  }

  rmw_wait_set_t * wait_set = rmw_create_wait_set(&context, 1);

  std::uint64_t received = 0;
  std::uint64_t mismatches = 0;
  std::uint64_t take_errors = 0;
  const auto deadline =
    std::chrono::steady_clock::now() + std::chrono::seconds(total_wait_s);
  while (received < sample_count && std::chrono::steady_clock::now() < deadline) {
    void * subscription_items[1] = {subscription};
    rmw_subscriptions_t subscriptions{1, subscription_items};
    rmw_time_t timeout{1, 0};
    const rmw_ret_t wait_ret = rmw_wait(
      &subscriptions, nullptr, nullptr, nullptr, nullptr, wait_set, &timeout);
    if (wait_ret != RMW_RET_OK || subscriptions.subscribers[0] == nullptr) {
      continue;
    }
    for (;;) {
      std_msgs::msg::String sample;
      bool taken = false;
      const rmw_ret_t take_ret = rmw_take(subscription, &sample, &taken, nullptr);
      if (take_ret != RMW_RET_OK) {
        ++take_errors;
        break;
      }
      if (!taken) {
        break;
      }
      ++received;
      if (!payload_matches_sequence(sample.data, payload_bytes)) {
        ++mismatches;
      }
    }
  }

  const bool destroy_ok =
    rmw_destroy_wait_set(wait_set) == RMW_RET_OK &&
    rmw_destroy_subscription(node, subscription) == RMW_RET_OK &&
    rmw_destroy_node(node) == RMW_RET_OK;

  const bool ok = destroy_ok && mismatches == 0 && take_errors == 0;

  std::cout << "{\"schema_version\":\"fleetrmw.heap_soak_subscriber_probe.v1\",";
  std::cout << "\"status\":\"" << (ok ? "ok" : "failed") << "\",";
  std::cout << "\"payload_bytes\":" << payload_bytes << ",";
  std::cout << "\"expected_sample_count\":" << sample_count << ",";
  std::cout << "\"received\":" << received << ",";
  std::cout << "\"mismatches\":" << mismatches << ",";
  std::cout << "\"take_errors\":" << take_errors << ",";
  std::cout << "\"destroy_ok\":" << (destroy_ok ? "true" : "false") << "}" << std::endl;

  const rmw_ret_t shutdown_ret = rmw_shutdown(&context);
  const rmw_ret_t context_fini_ret = rmw_context_fini(&context);
  const rmw_ret_t options_fini_ret = rmw_init_options_fini(&options);
  (void)shutdown_ret;
  (void)context_fini_ret;
  (void)options_fini_ret;
  return ok ? 0 : 1;
}
