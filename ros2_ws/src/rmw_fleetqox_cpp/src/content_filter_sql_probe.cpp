#include <chrono>
#include <cstdint>
#include <cstring>
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
#include "rmw/serialized_message.h"
#include "rmw/subscription_content_filter_options.h"
#include "rmw/subscription_options.h"
#include "rosidl_runtime_c/message_type_support_struct.h"

extern "C" std::uint64_t rmw_fleetqox_cpp_content_filters_set();
extern "C" std::uint64_t rmw_fleetqox_cpp_content_filters_evaluated();
extern "C" std::uint64_t rmw_fleetqox_cpp_content_filters_matched();
extern "C" std::uint64_t rmw_fleetqox_cpp_content_filters_dropped();

namespace
{

struct ScenarioResult
{
  bool ok{false};
  std::uint64_t evaluated{0};
  std::uint64_t matched{0};
  std::uint64_t dropped{0};
  std::vector<std::string> received;
};

bool init_message(
  rmw_serialized_message_t * message,
  const std::string & payload,
  rcutils_allocator_t * allocator)
{
  if (message == nullptr || allocator == nullptr ||
    rmw_serialized_message_init(message, payload.size(), allocator) != RMW_RET_OK)
  {
    return false;
  }
  if (!payload.empty()) {
    std::memcpy(message->buffer, payload.data(), payload.size());
  }
  message->buffer_length = payload.size();
  return true;
}

std::string message_text(const rmw_serialized_message_t & message)
{
  if (message.buffer == nullptr || message.buffer_length == 0) {
    return "";
  }
  return std::string(
    reinterpret_cast<const char *>(message.buffer),
    reinterpret_cast<const char *>(message.buffer + message.buffer_length));
}

bool wait_for_evaluations(std::uint64_t baseline, std::uint64_t expected)
{
  for (int i = 0; i < 150; ++i) {
    if (rmw_fleetqox_cpp_content_filters_evaluated() >= baseline + expected) {
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  return false;
}

ScenarioResult run_scenario(
  rmw_publisher_t * publisher,
  rmw_subscription_t * subscription,
  rcutils_allocator_t * allocator,
  const std::string & expression,
  const std::vector<std::string> & parameters,
  const std::vector<std::string> & payloads,
  const std::vector<std::string> & expected)
{
  ScenarioResult result;
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
  const std::uint64_t evaluated_before = rmw_fleetqox_cpp_content_filters_evaluated();
  const std::uint64_t matched_before = rmw_fleetqox_cpp_content_filters_matched();
  const std::uint64_t dropped_before = rmw_fleetqox_cpp_content_filters_dropped();

  bool publishes_ok = set_ret == RMW_RET_OK;
  for (const std::string & payload : payloads) {
    rmw_serialized_message_t outgoing = rmw_get_zero_initialized_serialized_message();
    const bool initialized = init_message(&outgoing, payload, allocator);
    const rmw_ret_t publish_ret = initialized ?
      rmw_publish_serialized_message(publisher, &outgoing, nullptr) : RMW_RET_ERROR;
    const rmw_ret_t fini_ret = initialized ?
      rmw_serialized_message_fini(&outgoing) : RMW_RET_ERROR;
    publishes_ok = publishes_ok && publish_ret == RMW_RET_OK && fini_ret == RMW_RET_OK;
  }
  const bool evaluated_ready = publishes_ok &&
    wait_for_evaluations(evaluated_before, payloads.size());

  rmw_serialized_message_t incoming = rmw_get_zero_initialized_serialized_message();
  const bool incoming_initialized =
    rmw_serialized_message_init(&incoming, 1, allocator) == RMW_RET_OK;
  bool takes_ok = incoming_initialized;
  for (size_t i = 0; i <= expected.size() && takes_ok; ++i) {
    bool taken = false;
    incoming.buffer_length = 0;
    const rmw_ret_t take_ret =
      rmw_take_serialized_message(subscription, &incoming, &taken, nullptr);
    takes_ok = take_ret == RMW_RET_OK;
    if (i < expected.size()) {
      takes_ok = takes_ok && taken;
      if (taken) {
        result.received.push_back(message_text(incoming));
      }
    } else {
      takes_ok = takes_ok && !taken;
    }
  }
  const rmw_ret_t incoming_fini_ret = incoming_initialized ?
    rmw_serialized_message_fini(&incoming) : RMW_RET_ERROR;
  const rmw_ret_t options_fini_ret = init_ret == RMW_RET_OK ?
    rmw_subscription_content_filter_options_fini(&options, allocator) : RMW_RET_ERROR;

  result.evaluated = rmw_fleetqox_cpp_content_filters_evaluated() - evaluated_before;
  result.matched = rmw_fleetqox_cpp_content_filters_matched() - matched_before;
  result.dropped = rmw_fleetqox_cpp_content_filters_dropped() - dropped_before;
  result.ok =
    set_ret == RMW_RET_OK && subscription->is_cft_enabled && publishes_ok &&
    evaluated_ready && takes_ok && result.received == expected &&
    result.evaluated == payloads.size() && result.matched == expected.size() &&
    result.dropped == payloads.size() - expected.size() &&
    incoming_fini_ret == RMW_RET_OK && options_fini_ret == RMW_RET_OK;
  return result;
}

bool invalid_expression_rejected(
  rmw_subscription_t * subscription,
  rcutils_allocator_t * allocator,
  const char * expression,
  size_t parameter_count,
  const char ** parameters)
{
  rmw_subscription_content_filter_options_t options =
    rmw_get_zero_initialized_content_filter_options();
  const rmw_ret_t init_ret = rmw_subscription_content_filter_options_init(
    expression, parameter_count, parameters, allocator, &options);
  const rmw_ret_t set_ret = init_ret == RMW_RET_OK ?
    rmw_subscription_set_content_filter(subscription, &options) : init_ret;
  const rmw_ret_t fini_ret = init_ret == RMW_RET_OK ?
    rmw_subscription_content_filter_options_fini(&options, allocator) : RMW_RET_ERROR;
  return init_ret == RMW_RET_OK && set_ret == RMW_RET_INVALID_ARGUMENT &&
         fini_ret == RMW_RET_OK && subscription->is_cft_enabled;
}

bool cleanup_context(rmw_context_t * context, rmw_init_options_t * options)
{
  return rmw_shutdown(context) == RMW_RET_OK &&
         rmw_context_fini(context) == RMW_RET_OK &&
         rmw_init_options_fini(options) == RMW_RET_OK;
}

}  // namespace

int main()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK) {
    return 1;
  }
  options.instance_id = 1057;
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init(&options, &context) != RMW_RET_OK) {
    const rmw_ret_t options_ret = rmw_init_options_fini(&options);
    (void)options_ret;
    return 1;
  }
  rmw_node_t * node =
    rmw_create_node(&context, "fleetqox_content_filter_sql_probe", "/fleetqox");
  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "rmw_fleetqox_cpp_content_filter_sql_probe";
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 16;
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();
  rmw_publisher_t * publisher = node == nullptr ? nullptr :
    rmw_create_publisher(
    node, &type_support, "/fleetqox/content_filter_sql", &qos, &publisher_options);
  rmw_subscription_t * subscription = node == nullptr ? nullptr :
    rmw_create_subscription(
    node, &type_support, "/fleetqox/content_filter_sql", &qos, &subscription_options);
  if (publisher == nullptr || subscription == nullptr) {
    if (publisher != nullptr) {
      const rmw_ret_t publisher_ret = rmw_destroy_publisher(node, publisher);
      (void)publisher_ret;
    }
    if (subscription != nullptr) {
      const rmw_ret_t subscription_ret = rmw_destroy_subscription(node, subscription);
      (void)subscription_ret;
    }
    if (node != nullptr) {
      const rmw_ret_t node_ret = rmw_destroy_node(node);
      (void)node_ret;
    }
    (void)cleanup_context(&context, &options);
    return 1;
  }

  const std::uint64_t set_before = rmw_fleetqox_cpp_content_filters_set();
  const std::string advanced_expression =
    "((robot_id LIKE %0 AND sequence BETWEEN %1 AND %2) OR "
    "(robot_id IN (%3, %4) AND mode NOT IN ('paused', 'fault'))) AND "
    "optional IS NULL AND mode IS NOT NULL AND priority <> %6 AND NOT (priority < %5)";
  const std::vector<std::string> advanced_parameters = {
    "robot_00%", "50", "52", "special_1", "special_2", "5", "999"};
  const std::vector<std::string> advanced_payloads = {
    "robot_id=robot_0001;sequence=51;mode=paused;priority=5",
    "robot_id=special_2;sequence=99;mode=active;priority=7",
    "robot_id=robot_0001;sequence=53;mode=active;priority=5",
    "robot_id=special_1;sequence=99;mode=paused;priority=5",
    "robot_id=other;sequence=51;mode=active;priority=5",
    "robot_id=robot_0001;sequence=51;mode=active;priority=5;optional=ready",
    "robot_id=special_1;sequence=99",
  };
  const std::vector<std::string> advanced_expected = {
    advanced_payloads[0], advanced_payloads[1]};
  const ScenarioResult advanced = run_scenario(
    publisher,
    subscription,
    &allocator,
    advanced_expression,
    advanced_parameters,
    advanced_payloads,
    advanced_expected);

  const std::string precedence_expression =
    "robot_id = %0 OR robot_id = %1 AND sequence > %2";
  const std::vector<std::string> precedence_parameters = {"robot_a", "robot_b", "10"};
  const std::vector<std::string> precedence_payloads = {
    "robot_id=robot_a;sequence=0",
    "robot_id=robot_b;sequence=11",
    "robot_id=robot_b;sequence=9",
    "robot_id=robot_c;sequence=99",
  };
  const std::vector<std::string> precedence_expected = {
    precedence_payloads[0], precedence_payloads[1]};
  const ScenarioResult precedence = run_scenario(
    publisher,
    subscription,
    &allocator,
    precedence_expression,
    precedence_parameters,
    precedence_payloads,
    precedence_expected);

  const std::string reversed_expression = "%0 = robot_id AND %1 < sequence";
  const std::vector<std::string> reversed_parameters = {"robot_a", "10"};
  const std::vector<std::string> reversed_payloads = {
    "robot_id=robot_a;sequence=11",
    "robot_id=robot_a;sequence=9",
    "robot_id=robot_b;sequence=99",
  };
  const std::vector<std::string> reversed_expected = {reversed_payloads[0]};
  const ScenarioResult reversed = run_scenario(
    publisher,
    subscription,
    &allocator,
    reversed_expression,
    reversed_parameters,
    reversed_payloads,
    reversed_expected);

  // LIKE ... ESCAPE: without it, '%' and '_' inside a pattern are always
  // wildcards, so a field containing a *literal* '%' (e.g. "50%") can
  // never be matched exactly. The escaped pattern "50\%" (parameter
  // string, not re-parsed as a quoted SQL literal) must match only the
  // literal three-character value "50%", not "50X" or "5000" -- which a
  // no-escape "50%" pattern would treat as "50" followed by anything.
  const std::string escape_expression = "value LIKE %0 ESCAPE %1";
  const std::vector<std::string> escape_parameters = {"50\\%", "\\"};
  const std::vector<std::string> escape_payloads = {
    "value=50%",
    "value=50X",
    "value=5000",
  };
  const std::vector<std::string> escape_expected = {escape_payloads[0]};
  const ScenarioResult escape_scenario = run_scenario(
    publisher,
    subscription,
    &allocator,
    escape_expression,
    escape_parameters,
    escape_payloads,
    escape_expected);

  const char * one_parameter[] = {"robot_a"};
  const char * two_parameters[] = {"a", "b"};
  const char * multi_char_escape_parameters[] = {"x", "ab"};
  const bool malformed_rejected = invalid_expression_rejected(
    subscription, &allocator, "robot_id =", 0, nullptr);
  const bool missing_parameter_rejected = invalid_expression_rejected(
    subscription, &allocator, "robot_id = %9", 1, one_parameter);
  const bool reversed_missing_field_rejected = invalid_expression_rejected(
    subscription, &allocator, "%0 = %1", 2, two_parameters);
  // DDS-SQL requires exactly one character as the ESCAPE argument; a
  // multi-character argument is malformed, same fail-closed treatment as
  // every other parse error this probe already covers.
  const bool multi_char_escape_rejected = invalid_expression_rejected(
    subscription, &allocator, "value LIKE %0 ESCAPE %1", 2, multi_char_escape_parameters);

  rmw_subscription_content_filter_options_t disabled =
    rmw_get_zero_initialized_content_filter_options();
  const rmw_ret_t disable_ret = rmw_subscription_set_content_filter(subscription, &disabled);
  const bool disabled_ok = disable_ret == RMW_RET_OK && !subscription->is_cft_enabled;
  const std::uint64_t set_delta = rmw_fleetqox_cpp_content_filters_set() - set_before;

  const rmw_ret_t publisher_ret = rmw_destroy_publisher(node, publisher);
  const rmw_ret_t subscription_ret = rmw_destroy_subscription(node, subscription);
  const rmw_ret_t node_ret = rmw_destroy_node(node);
  const bool context_ok = cleanup_context(&context, &options);
  const bool invalid_ok = malformed_rejected && missing_parameter_rejected &&
    reversed_missing_field_rejected && multi_char_escape_rejected;
  const bool cleanup_ok = publisher_ret == RMW_RET_OK &&
    subscription_ret == RMW_RET_OK && node_ret == RMW_RET_OK && context_ok;
  const bool ok = advanced.ok && precedence.ok && reversed.ok && escape_scenario.ok &&
    invalid_ok && disabled_ok && set_delta == 5 && cleanup_ok;

  std::cout << "{\"schema_version\":\"fleetrmw.content_filter_sql_probe.v1\","
            << "\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"sql_boolean_parentheses_enforcement\":"
            << (advanced.ok ? "true" : "false") << ","
            << "\"sql_and_or_precedence_enforcement\":"
            << (precedence.ok ? "true" : "false") << ","
            << "\"sql_reversed_comparison_operand_order_claim\":"
            << (reversed.ok ? "true" : "false") << ","
            << "\"invalid_expression_fail_closed\":"
            << (invalid_ok ? "true" : "false") << ","
            << "\"disable_after_invalid_expression\":"
            << (disabled_ok ? "true" : "false") << ","
            << "\"advanced_evaluated\":" << advanced.evaluated << ","
            << "\"advanced_matched\":" << advanced.matched << ","
            << "\"advanced_dropped\":" << advanced.dropped << ","
            << "\"precedence_evaluated\":" << precedence.evaluated << ","
            << "\"precedence_matched\":" << precedence.matched << ","
            << "\"precedence_dropped\":" << precedence.dropped << ","
            << "\"reversed_evaluated\":" << reversed.evaluated << ","
            << "\"reversed_matched\":" << reversed.matched << ","
            << "\"reversed_dropped\":" << reversed.dropped << ","
            << "\"sql_like_escape_claim\":" << (escape_scenario.ok ? "true" : "false") << ","
            << "\"escape_evaluated\":" << escape_scenario.evaluated << ","
            << "\"escape_matched\":" << escape_scenario.matched << ","
            << "\"escape_dropped\":" << escape_scenario.dropped << ","
            << "\"multi_char_escape_rejected\":"
            << (multi_char_escape_rejected ? "true" : "false") << ","
            << "\"content_filters_set_delta\":" << set_delta << ","
            << "\"content_filter_sql_subset_claim\":"
            << ((advanced.ok && precedence.ok && reversed.ok && escape_scenario.ok) ?
              "true" : "false") << ","
            << "\"clean_teardown\":" << (cleanup_ok ? "true" : "false") << "}"
            << std::endl;
  return ok ? 0 : 1;
}
