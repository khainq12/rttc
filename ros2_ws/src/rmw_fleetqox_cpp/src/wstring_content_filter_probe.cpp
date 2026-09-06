#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include "fleetrmw_interfaces/msg/detail/w_string_sample__struct.hpp"
#include "fleetrmw_interfaces/msg/detail/w_string_sample__type_support.hpp"
#include "fleetrmw_interfaces/msg/w_string_sample.hpp"
#include "rcutils/allocator.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/publisher_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/subscription_content_filter_options.h"
#include "rmw/subscription_options.h"
#include "rosidl_typesupport_interface/macros.h"

// Probes the two things WSTRING support actually needs to prove:
//  1. Round-trip fidelity through the general introspection
//     serialize/deserialize path (not content-filter specific), including a
//     surrogate-pair code point outside the Basic Multilingual Plane, which
//     the earlier absence of any WSTRING branch there would have rejected
//     outright (max_serialized_size_introspection_cpp_member falls through
//     to primitive_size(), which returns 0 for an unrecognized type_id).
//  2. Content-filter predicate matching against a wstring field, which
//     depends on the UTF-16-to-UTF-8 reflection path added alongside it.
extern "C" std::uint64_t rmw_fleetqox_cpp_content_filters_evaluated();
extern "C" std::uint64_t rmw_fleetqox_cpp_content_filters_matched();
extern "C" std::uint64_t rmw_fleetqox_cpp_content_filters_dropped();

namespace
{

bool set_filter(
  rmw_subscription_t * subscription,
  rcutils_allocator_t * allocator,
  const std::string & expression,
  const std::vector<std::string> & parameters)
{
  std::vector<const char *> parameter_pointers;
  parameter_pointers.reserve(parameters.size());
  for (const std::string & parameter : parameters) {
    parameter_pointers.push_back(parameter.c_str());
  }
  rmw_subscription_content_filter_options_t options =
    rmw_get_zero_initialized_content_filter_options();
  const rmw_ret_t init_ret = rmw_subscription_content_filter_options_init(
    expression.c_str(),
    parameter_pointers.size(),
    parameter_pointers.empty() ? nullptr : parameter_pointers.data(),
    allocator,
    &options);
  const rmw_ret_t set_ret = init_ret == RMW_RET_OK ?
    rmw_subscription_set_content_filter(subscription, &options) : init_ret;
  const rmw_ret_t fini_ret = init_ret == RMW_RET_OK ?
    rmw_subscription_content_filter_options_fini(&options, allocator) : RMW_RET_ERROR;
  return init_ret == RMW_RET_OK && set_ret == RMW_RET_OK &&
         fini_ret == RMW_RET_OK && subscription->is_cft_enabled;
}

bool wait_for_evaluations(std::uint64_t baseline, std::uint64_t expected)
{
  for (int attempt = 0; attempt < 150; ++attempt) {
    if (rmw_fleetqox_cpp_content_filters_evaluated() >= baseline + expected) {
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  return false;
}

}  // namespace

int main()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK ||
    rmw_init(&options, &context) != RMW_RET_OK)
  {
    std::cout << "{\"status\":\"context_init_failed\"}" << std::endl;
    return 1;
  }
  rmw_node_t * node = rmw_create_node(&context, "wstring_content_filter_probe", "/fleetqox");
  const auto * type_support =
    ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(
    rosidl_typesupport_cpp, fleetrmw_interfaces, msg, WStringSample)();
  const rmw_qos_profile_t qos = rmw_qos_profile_default;
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();
  rmw_publisher_t * publisher = node == nullptr ? nullptr : rmw_create_publisher(
    node, type_support, "/fleetqox/wstring_content_filter_probe", &qos, &publisher_options);
  rmw_subscription_t * subscription = node == nullptr ? nullptr : rmw_create_subscription(
    node, type_support, "/fleetqox/wstring_content_filter_probe", &qos, &subscription_options);
  if (publisher == nullptr || subscription == nullptr) {
    std::cout << "{\"status\":\"endpoint_create_failed\"}" << std::endl;
    return 1;
  }

  // "caf" + U+00E9 (e-acute, 2-byte UTF-8, still in the BMP) +
  // U+1F600 (grinning face, requires a UTF-16 surrogate pair and a 4-byte
  // UTF-8 sequence) -- exercises both the common accented-text case and the
  // surrogate-pair edge case in one field.
  const std::u16string surrogate_label =
    u"café\U0001F600";
  const std::u16string plain_label = u"tea";

  // The filter parameter is the UTF-8 form of surrogate_label -- this is
  // what the wstring reflection path (utf16_to_utf8) must produce from the
  // wire-format UTF-16 field for the predicate to match at all.
  const bool filter_ok = set_filter(
    subscription, &allocator, "label = %0", {u8"café\U0001F600"});

  const std::uint64_t evaluated_before = rmw_fleetqox_cpp_content_filters_evaluated();
  const std::uint64_t matched_before = rmw_fleetqox_cpp_content_filters_matched();
  const std::uint64_t dropped_before = rmw_fleetqox_cpp_content_filters_dropped();

  fleetrmw_interfaces::msg::WStringSample matching;
  matching.label = surrogate_label;
  matching.tag = "match";
  fleetrmw_interfaces::msg::WStringSample nonmatching;
  nonmatching.label = plain_label;
  nonmatching.tag = "nomatch";

  bool publish_ok = filter_ok;
  publish_ok = publish_ok && rmw_publish(publisher, &matching, nullptr) == RMW_RET_OK;
  publish_ok = publish_ok && rmw_publish(publisher, &nonmatching, nullptr) == RMW_RET_OK;
  const bool evaluated_ok = publish_ok && wait_for_evaluations(evaluated_before, 2);

  fleetrmw_interfaces::msg::WStringSample incoming;
  bool taken = false;
  const bool take_ret_ok =
    rmw_take(subscription, &incoming, &taken, nullptr) == RMW_RET_OK;
  const bool round_trip_ok = take_ret_ok && taken &&
    incoming.label == surrogate_label && incoming.tag == "match";

  bool second_taken = false;
  fleetrmw_interfaces::msg::WStringSample unused;
  const bool no_second_match =
    rmw_take(subscription, &unused, &second_taken, nullptr) == RMW_RET_OK && !second_taken;

  const std::uint64_t evaluated_delta =
    rmw_fleetqox_cpp_content_filters_evaluated() - evaluated_before;
  const std::uint64_t matched_delta =
    rmw_fleetqox_cpp_content_filters_matched() - matched_before;
  const std::uint64_t dropped_delta =
    rmw_fleetqox_cpp_content_filters_dropped() - dropped_before;
  const bool filter_counts_ok =
    evaluated_delta == 2 && matched_delta == 1 && dropped_delta == 1;

  const bool destroy_ok =
    rmw_destroy_publisher(node, publisher) == RMW_RET_OK &&
    rmw_destroy_subscription(node, subscription) == RMW_RET_OK &&
    rmw_destroy_node(node) == RMW_RET_OK;

  const bool ok = filter_ok && evaluated_ok && round_trip_ok && no_second_match &&
    filter_counts_ok && destroy_ok;

  std::cout << "{\"schema_version\":\"fleetrmw.wstring_content_filter_probe.v1\",";
  std::cout << "\"status\":\"" << (ok ? "ok" : "failed") << "\",";
  std::cout << "\"filter_set_ok\":" << (filter_ok ? "true" : "false") << ",";
  std::cout << "\"publish_ok\":" << (publish_ok ? "true" : "false") << ",";
  std::cout << "\"round_trip_ok\":" << (round_trip_ok ? "true" : "false") << ",";
  std::cout << "\"surrogate_pair_preserved\":" <<
    (round_trip_ok && incoming.label == surrogate_label ? "true" : "false") << ",";
  std::cout << "\"no_second_match\":" << (no_second_match ? "true" : "false") << ",";
  std::cout << "\"content_filters_evaluated\":" << evaluated_delta << ",";
  std::cout << "\"content_filters_matched\":" << matched_delta << ",";
  std::cout << "\"content_filters_dropped\":" << dropped_delta << "}" << std::endl;

  const rmw_ret_t shutdown_ret = rmw_shutdown(&context);
  const rmw_ret_t context_fini_ret = rmw_context_fini(&context);
  const rmw_ret_t options_fini_ret = rmw_init_options_fini(&options);
  (void)shutdown_ret;
  (void)context_fini_ret;
  (void)options_fini_ret;
  return ok ? 0 : 1;
}
