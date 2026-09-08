#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <thread>

#include "rcutils/allocator.h"
#include "rmw/error_handling.h"
#include "rmw/event.h"
#include "rmw/events_statuses/events_statuses.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/publisher_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/subscription_options.h"
#include "rosidl_runtime_c/message_type_support_struct.h"

namespace
{

struct ScenarioResult
{
  bool ok{false};
  bool connected{false};
  bool idle_quiet{false};
  bool assertion_quiet{false};
  bool removed{false};
  bool actual_qos{false};
};

template<typename StatusT>
bool wait_take_event(
  rmw_context_t * context,
  rmw_event_t * event,
  StatusT * status,
  int timeout_ms)
{
  rmw_wait_set_t * wait_set = rmw_create_wait_set(context, 1);
  if (wait_set == nullptr) {
    return false;
  }
  const auto deadline =
    std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  bool ok = false;
  while (std::chrono::steady_clock::now() < deadline) {
    void * handles[1] = {event};
    rmw_events_t events{1, handles};
    rmw_time_t timeout{};
    timeout.sec = 0;
    timeout.nsec = 20000000;
    const rmw_ret_t wait_ret =
      rmw_wait(nullptr, nullptr, nullptr, nullptr, &events, wait_set, &timeout);
    if (wait_ret == RMW_RET_TIMEOUT) {
      continue;
    }
    if (wait_ret != RMW_RET_OK || handles[0] == nullptr) {
      break;
    }
    bool taken = false;
    ok = rmw_take_event(event, status, &taken) == RMW_RET_OK && taken;
    break;
  }
  const rmw_ret_t destroy_ret = rmw_destroy_wait_set(wait_set);
  return ok && destroy_ret == RMW_RET_OK;
}

bool event_not_ready(rmw_context_t * context, rmw_event_t * event, int timeout_ms)
{
  rmw_wait_set_t * wait_set = rmw_create_wait_set(context, 1);
  if (wait_set == nullptr) {
    return false;
  }
  void * handles[1] = {event};
  rmw_events_t events{1, handles};
  rmw_time_t timeout{};
  timeout.sec = static_cast<std::uint64_t>(timeout_ms / 1000);
  timeout.nsec = static_cast<std::uint64_t>(timeout_ms % 1000) * 1000000u;
  const rmw_ret_t wait_ret =
    rmw_wait(nullptr, nullptr, nullptr, nullptr, &events, wait_set, &timeout);
  const bool not_ready = wait_ret == RMW_RET_TIMEOUT && handles[0] == nullptr;
  const rmw_ret_t destroy_ret = rmw_destroy_wait_set(wait_set);
  return not_ready && destroy_ret == RMW_RET_OK;
}

bool duration_is_default(const rmw_time_t & duration)
{
  return duration.sec == 0 && duration.nsec == 0;
}

ScenarioResult run_non_expiring_scenario(
  rmw_context_t * context,
  rmw_node_t * node,
  const rosidl_message_type_support_t * type_support,
  const std::string & topic,
  rmw_qos_liveliness_policy_t requested_policy,
  rmw_qos_liveliness_policy_t expected_actual_policy,
  bool assert_manually)
{
  ScenarioResult result;
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 8;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT;
  qos.liveliness = requested_policy;
  qos.liveliness_lease_duration.sec = 0;
  qos.liveliness_lease_duration.nsec = 0;
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();
  rmw_subscription_t * subscription = rmw_create_subscription(
    node, type_support, topic.c_str(), &qos, &subscription_options);
  rmw_event_t changed_event = rmw_get_zero_initialized_event();
  const bool event_initialized = subscription != nullptr &&
    rmw_subscription_event_init(
    &changed_event, subscription, RMW_EVENT_LIVELINESS_CHANGED) == RMW_RET_OK;
  rmw_publisher_t * publisher = event_initialized ? rmw_create_publisher(
    node, type_support, topic.c_str(), &qos, &publisher_options) : nullptr;

  rmw_liveliness_changed_status_t connected{};
  result.connected = publisher != nullptr && wait_take_event(
    context, &changed_event, &connected, 2000) &&
    connected.alive_count == 1 && connected.not_alive_count == 0 &&
    connected.alive_count_change == 1 && connected.not_alive_count_change == 0;
  rmw_qos_profile_t publisher_actual{};
  rmw_qos_profile_t subscription_actual{};
  result.actual_qos = publisher != nullptr && subscription != nullptr &&
    rmw_publisher_get_actual_qos(publisher, &publisher_actual) == RMW_RET_OK &&
    rmw_subscription_get_actual_qos(subscription, &subscription_actual) == RMW_RET_OK &&
    publisher_actual.liveliness == expected_actual_policy &&
    subscription_actual.liveliness == expected_actual_policy &&
    duration_is_default(publisher_actual.liveliness_lease_duration) &&
    duration_is_default(subscription_actual.liveliness_lease_duration);

  std::this_thread::sleep_for(std::chrono::milliseconds(120));
  result.idle_quiet = event_initialized && event_not_ready(context, &changed_event, 0);
  result.assertion_quiet = true;
  if (assert_manually) {
    result.assertion_quiet = publisher != nullptr &&
      rmw_publisher_assert_liveliness(publisher) == RMW_RET_OK &&
      event_not_ready(context, &changed_event, 50);
  }
  const rmw_ret_t publisher_ret = publisher == nullptr ?
    RMW_RET_ERROR : rmw_destroy_publisher(node, publisher);
  rmw_liveliness_changed_status_t removed{};
  result.removed = publisher_ret == RMW_RET_OK && event_initialized && wait_take_event(
    context, &changed_event, &removed, 2000) &&
    removed.alive_count == 0 && removed.not_alive_count == 0 &&
    removed.alive_count_change == -1 && removed.not_alive_count_change == 0;

  bool teardown_ok = true;
  if (event_initialized) {
    teardown_ok = rmw_event_fini(&changed_event) == RMW_RET_OK && teardown_ok;
  }
  if (subscription != nullptr) {
    teardown_ok =
      rmw_destroy_subscription(node, subscription) == RMW_RET_OK && teardown_ok;
  }
  result.ok = event_initialized && result.connected && result.idle_quiet &&
    result.assertion_quiet && result.removed && result.actual_qos && teardown_ok;
  return result;
}

bool rejected_policy(
  rmw_node_t * node,
  const rosidl_message_type_support_t * type_support,
  const std::string & topic,
  rmw_qos_liveliness_policy_t policy)
{
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.liveliness = policy;
  qos.liveliness_lease_duration.sec = 0;
  qos.liveliness_lease_duration.nsec = 100000000;
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();
  rmw_publisher_t * publisher = rmw_create_publisher(
    node, type_support, topic.c_str(), &qos, &publisher_options);
  const bool publisher_rejected = publisher == nullptr;
  if (publisher != nullptr) {
    const rmw_ret_t ret = rmw_destroy_publisher(node, publisher);
    (void)ret;
  }
  rmw_reset_error();
  rmw_subscription_t * subscription = rmw_create_subscription(
    node, type_support, topic.c_str(), &qos, &subscription_options);
  const bool subscription_rejected = subscription == nullptr;
  if (subscription != nullptr) {
    const rmw_ret_t ret = rmw_destroy_subscription(node, subscription);
    (void)ret;
  }
  rmw_reset_error();
  return publisher_rejected && subscription_rejected;
}

}  // namespace

int main()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK) {
    return 1;
  }
  options.instance_id = 828;
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init(&options, &context) != RMW_RET_OK) {
    const rmw_ret_t ret = rmw_init_options_fini(&options);
    (void)ret;
    return 1;
  }
  rmw_node_t * node =
    rmw_create_node(&context, "liveliness_default_lease_probe", "/fleetqox");
  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetrmw_liveliness_default_lease_type";

  const ScenarioResult system_default = run_non_expiring_scenario(
    &context,
    node,
    &type_support,
    "/fleetqox/liveliness_default_lease/system_default",
    RMW_QOS_POLICY_LIVELINESS_SYSTEM_DEFAULT,
    RMW_QOS_POLICY_LIVELINESS_SYSTEM_DEFAULT,
    false);
  const ScenarioResult automatic = run_non_expiring_scenario(
    &context,
    node,
    &type_support,
    "/fleetqox/liveliness_default_lease/automatic",
    RMW_QOS_POLICY_LIVELINESS_AUTOMATIC,
    RMW_QOS_POLICY_LIVELINESS_AUTOMATIC,
    false);
  const ScenarioResult manual = run_non_expiring_scenario(
    &context,
    node,
    &type_support,
    "/fleetqox/liveliness_default_lease/manual",
    RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC,
    RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC,
    true);
  const ScenarioResult best_available = run_non_expiring_scenario(
    &context,
    node,
    &type_support,
    "/fleetqox/liveliness_default_lease/best_available",
    RMW_QOS_POLICY_LIVELINESS_BEST_AVAILABLE,
    RMW_QOS_POLICY_LIVELINESS_AUTOMATIC,
    false);
  // Value 2 is the deprecated RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_NODE,
  // referenced numerically to avoid a -Wdeprecated-declarations warning.
  // It is supported (not fail-closed): its distinguishing node-wide
  // assertion-sharing behavior is covered separately by
  // manual_by_node_liveliness_probe.cpp; this scenario only proves the
  // baseline single-publisher non-expiring-lease lifecycle matches every
  // other supported kind.
  const ScenarioResult manual_by_node = run_non_expiring_scenario(
    &context,
    node,
    &type_support,
    "/fleetqox/liveliness_default_lease/manual_by_node",
    static_cast<rmw_qos_liveliness_policy_t>(2),
    static_cast<rmw_qos_liveliness_policy_t>(2),
    true);
  const bool unknown_rejected = rejected_policy(
    node,
    &type_support,
    "/fleetqox/liveliness_default_lease/unknown",
    RMW_QOS_POLICY_LIVELINESS_UNKNOWN);

  const rmw_ret_t node_ret = node == nullptr ? RMW_RET_ERROR : rmw_destroy_node(node);
  const rmw_ret_t shutdown_ret = rmw_shutdown(&context);
  const rmw_ret_t context_ret = rmw_context_fini(&context);
  const rmw_ret_t options_ret = rmw_init_options_fini(&options);
  const bool teardown_ok = node_ret == RMW_RET_OK && shutdown_ret == RMW_RET_OK &&
    context_ret == RMW_RET_OK && options_ret == RMW_RET_OK;
  const bool lifecycle_ok = system_default.ok && automatic.ok && manual.ok &&
    best_available.ok && manual_by_node.ok;
  const bool ok = node != nullptr && lifecycle_ok && unknown_rejected && teardown_ok;
  std::cout <<
    "{\"schema_version\":\"fleetrmw.liveliness_default_lease_probe.v1\","
            << "\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"non_expiring_liveliness_lifecycle_claim\":"
            << (lifecycle_ok ? "true" : "false") << ","
            << "\"system_default_infinite_lease_lifecycle_claim\":"
            << (system_default.ok ? "true" : "false") << ","
            << "\"automatic_infinite_lease_lifecycle_claim\":"
            << (automatic.ok ? "true" : "false") << ","
            << "\"manual_infinite_lease_lifecycle_claim\":"
            << (manual.ok ? "true" : "false") << ","
            << "\"best_available_infinite_lease_lifecycle_claim\":"
            << (best_available.ok ? "true" : "false") << ","
            << "\"manual_by_node_infinite_lease_lifecycle_claim\":"
            << (manual_by_node.ok ? "true" : "false") << ","
            << "\"unknown_liveliness_fail_closed_claim\":"
            << (unknown_rejected ? "true" : "false") << ","
            << "\"scenario_count\":6,"
            << "\"clean_teardown\":" << (teardown_ok ? "true" : "false") << "}"
            << std::endl;
  return ok ? 0 : 1;
}
