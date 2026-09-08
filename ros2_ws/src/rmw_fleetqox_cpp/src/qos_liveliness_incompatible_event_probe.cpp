#include <cstdint>
#include <iostream>
#include <string>

#include "rcutils/allocator.h"
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

struct CallbackState
{
  std::uint64_t calls{0};
  std::uint64_t events{0};
};

struct ScenarioResult
{
  bool ok{false};
  bool wait_ready{false};
  bool taken{false};
  bool matched_wait_ready{true};
  bool matched_taken{true};
  std::int32_t total_count{0};
  std::int32_t total_count_change{0};
  rmw_qos_policy_kind_t last_policy_kind{RMW_QOS_POLICY_INVALID};
  std::uint64_t callback_events{0};
};

void event_callback(const void * user_data, size_t number_of_events)
{
  auto * state = const_cast<CallbackState *>(
    static_cast<const CallbackState *>(user_data));
  if (state != nullptr) {
    ++state->calls;
    state->events += number_of_events;
  }
}

rmw_ret_t wait_event_once(rmw_context_t * context, rmw_event_t * event, bool * ready)
{
  if (context == nullptr || event == nullptr || ready == nullptr) {
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_wait_set_t * wait_set = rmw_create_wait_set(context, 1);
  if (wait_set == nullptr) {
    return RMW_RET_ERROR;
  }
  rmw_time_t zero_timeout{};
  void * handles[1] = {event};
  rmw_events_t events{1, handles};
  const rmw_ret_t wait_ret =
    rmw_wait(nullptr, nullptr, nullptr, nullptr, &events, wait_set, &zero_timeout);
  *ready = handles[0] != nullptr;
  const rmw_ret_t destroy_ret = rmw_destroy_wait_set(wait_set);
  if (wait_ret != RMW_RET_OK && wait_ret != RMW_RET_TIMEOUT) {
    return wait_ret;
  }
  return destroy_ret == RMW_RET_OK ? wait_ret : destroy_ret;
}

rmw_qos_profile_t liveliness_qos(
  rmw_qos_liveliness_policy_t policy,
  std::uint64_t lease_ms)
{
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 8;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_RELIABLE;
  qos.liveliness = policy;
  qos.liveliness_lease_duration.sec = lease_ms / 1000u;
  qos.liveliness_lease_duration.nsec = (lease_ms % 1000u) * 1000000u;
  return qos;
}

ScenarioResult run_incompatible_scenario(
  rmw_context_t * context,
  rmw_node_t * node,
  const rosidl_message_type_support_t * type_support,
  const rmw_publisher_options_t * publisher_options,
  const rmw_subscription_options_t * subscription_options,
  const std::string & topic,
  const rmw_qos_profile_t & offered_qos,
  const rmw_qos_profile_t & requested_qos,
  bool offered_side)
{
  ScenarioResult result;
  rmw_publisher_t * publisher = nullptr;
  rmw_subscription_t * subscription = nullptr;
  rmw_event_t event = rmw_get_zero_initialized_event();
  rmw_event_t matched_event = rmw_get_zero_initialized_event();

  rmw_ret_t event_init_ret = RMW_RET_ERROR;
  rmw_ret_t matched_event_init_ret = RMW_RET_ERROR;
  if (offered_side) {
    publisher = rmw_create_publisher(
      node, type_support, topic.c_str(), &offered_qos, publisher_options);
    if (publisher != nullptr) {
      event_init_ret = rmw_publisher_event_init(
        &event, publisher, RMW_EVENT_OFFERED_QOS_INCOMPATIBLE);
      matched_event_init_ret = rmw_publisher_event_init(
        &matched_event, publisher, RMW_EVENT_PUBLICATION_MATCHED);
    }
  } else {
    subscription = rmw_create_subscription(
      node, type_support, topic.c_str(), &requested_qos, subscription_options);
    if (subscription != nullptr) {
      event_init_ret = rmw_subscription_event_init(
        &event, subscription, RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE);
      matched_event_init_ret = rmw_subscription_event_init(
        &matched_event, subscription, RMW_EVENT_SUBSCRIPTION_MATCHED);
    }
  }

  CallbackState callback_state;
  const rmw_ret_t callback_ret =
    event_init_ret == RMW_RET_OK ?
    rmw_event_set_callback(&event, event_callback, &callback_state) : RMW_RET_ERROR;
  if (offered_side) {
    subscription = rmw_create_subscription(
      node, type_support, topic.c_str(), &requested_qos, subscription_options);
  } else {
    publisher = rmw_create_publisher(
      node, type_support, topic.c_str(), &offered_qos, publisher_options);
  }

  const rmw_ret_t matched_wait_ret =
    wait_event_once(context, &matched_event, &result.matched_wait_ready);
  rmw_matched_status_t matched_status{};
  const rmw_ret_t matched_take_ret =
    rmw_take_event(&matched_event, &matched_status, &result.matched_taken);
  const rmw_ret_t wait_ret = wait_event_once(context, &event, &result.wait_ready);
  rmw_qos_incompatible_event_status_t status{};
  const rmw_ret_t take_ret = rmw_take_event(&event, &status, &result.taken);
  result.total_count = status.total_count;
  result.total_count_change = status.total_count_change;
  result.last_policy_kind = status.last_policy_kind;
  result.callback_events = callback_state.events;

  rmw_qos_incompatible_event_status_t cleared_status{};
  bool cleared_taken = true;
  const rmw_ret_t cleared_take_ret =
    rmw_take_event(&event, &cleared_status, &cleared_taken);
  bool cleared_ready = true;
  const rmw_ret_t cleared_wait_ret = wait_event_once(context, &event, &cleared_ready);

  rmw_ret_t subscription_ret = RMW_RET_ERROR;
  rmw_ret_t publisher_ret = RMW_RET_ERROR;
  rmw_ret_t matched_event_ret = RMW_RET_ERROR;
  rmw_ret_t event_ret = RMW_RET_ERROR;
  if (offered_side) {
    subscription_ret = subscription == nullptr ?
      RMW_RET_ERROR : rmw_destroy_subscription(node, subscription);
    matched_event_ret = matched_event_init_ret == RMW_RET_OK ?
      rmw_event_fini(&matched_event) : RMW_RET_ERROR;
    event_ret = event_init_ret == RMW_RET_OK ?
      rmw_event_fini(&event) : RMW_RET_ERROR;
    publisher_ret = publisher == nullptr ?
      RMW_RET_ERROR : rmw_destroy_publisher(node, publisher);
  } else {
    publisher_ret = publisher == nullptr ?
      RMW_RET_ERROR : rmw_destroy_publisher(node, publisher);
    matched_event_ret = matched_event_init_ret == RMW_RET_OK ?
      rmw_event_fini(&matched_event) : RMW_RET_ERROR;
    event_ret = event_init_ret == RMW_RET_OK ?
      rmw_event_fini(&event) : RMW_RET_ERROR;
    subscription_ret = subscription == nullptr ?
      RMW_RET_ERROR : rmw_destroy_subscription(node, subscription);
  }

  result.ok = publisher != nullptr && subscription != nullptr &&
    event_init_ret == RMW_RET_OK && matched_event_init_ret == RMW_RET_OK &&
    callback_ret == RMW_RET_OK &&
    matched_wait_ret == RMW_RET_TIMEOUT && !result.matched_wait_ready &&
    matched_take_ret == RMW_RET_OK && !result.matched_taken &&
    matched_status.current_count == 0 && matched_status.current_count_change == 0 &&
    wait_ret == RMW_RET_OK && result.wait_ready && take_ret == RMW_RET_OK &&
    result.taken && result.total_count == 1 && result.total_count_change == 1 &&
    result.last_policy_kind == RMW_QOS_POLICY_LIVELINESS &&
    callback_state.calls >= 1 && callback_state.events >= 1 &&
    cleared_take_ret == RMW_RET_OK && !cleared_taken &&
    cleared_status.total_count == 1 && cleared_status.total_count_change == 0 &&
    cleared_wait_ret == RMW_RET_TIMEOUT && !cleared_ready &&
    subscription_ret == RMW_RET_OK && publisher_ret == RMW_RET_OK &&
    matched_event_ret == RMW_RET_OK && event_ret == RMW_RET_OK;
  return result;
}

bool run_compatible_control(
  rmw_context_t * context,
  rmw_node_t * node,
  const rosidl_message_type_support_t * type_support,
  const rmw_publisher_options_t * publisher_options,
  const rmw_subscription_options_t * subscription_options)
{
  const rmw_qos_profile_t offered_qos = liveliness_qos(
    RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC, 100);
  const rmw_qos_profile_t requested_qos = liveliness_qos(
    RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC, 500);
  const char * topic = "/fleetqox/qos_liveliness_compatible_control";
  rmw_publisher_t * publisher = rmw_create_publisher(
    node, type_support, topic, &offered_qos, publisher_options);
  rmw_event_t event = rmw_get_zero_initialized_event();
  rmw_event_t matched_event = rmw_get_zero_initialized_event();
  const rmw_ret_t event_init_ret = publisher == nullptr ? RMW_RET_ERROR :
    rmw_publisher_event_init(&event, publisher, RMW_EVENT_OFFERED_QOS_INCOMPATIBLE);
  const rmw_ret_t matched_init_ret = publisher == nullptr ? RMW_RET_ERROR :
    rmw_publisher_event_init(&matched_event, publisher, RMW_EVENT_PUBLICATION_MATCHED);
  rmw_subscription_t * subscription = rmw_create_subscription(
    node, type_support, topic, &requested_qos, subscription_options);
  bool event_ready = true;
  const rmw_ret_t event_wait_ret = wait_event_once(context, &event, &event_ready);
  rmw_qos_incompatible_event_status_t status{};
  bool taken = true;
  const rmw_ret_t take_ret = rmw_take_event(&event, &status, &taken);
  bool matched_ready = false;
  const rmw_ret_t matched_wait_ret =
    wait_event_once(context, &matched_event, &matched_ready);
  rmw_matched_status_t matched_status{};
  bool matched_taken = false;
  const rmw_ret_t matched_take_ret =
    rmw_take_event(&matched_event, &matched_status, &matched_taken);

  const rmw_ret_t subscription_ret = subscription == nullptr ?
    RMW_RET_ERROR : rmw_destroy_subscription(node, subscription);
  const rmw_ret_t event_ret = event_init_ret == RMW_RET_OK ?
    rmw_event_fini(&event) : RMW_RET_ERROR;
  const rmw_ret_t matched_event_ret = matched_init_ret == RMW_RET_OK ?
    rmw_event_fini(&matched_event) : RMW_RET_ERROR;
  const rmw_ret_t publisher_ret = publisher == nullptr ?
    RMW_RET_ERROR : rmw_destroy_publisher(node, publisher);
  return publisher != nullptr && subscription != nullptr &&
         event_init_ret == RMW_RET_OK && matched_init_ret == RMW_RET_OK &&
         event_wait_ret == RMW_RET_TIMEOUT && !event_ready &&
         take_ret == RMW_RET_OK && !taken && status.total_count == 0 &&
         matched_wait_ret == RMW_RET_OK && matched_ready &&
         matched_take_ret == RMW_RET_OK && matched_taken &&
         matched_status.current_count == 1 && matched_status.current_count_change == 1 &&
         subscription_ret == RMW_RET_OK && event_ret == RMW_RET_OK &&
         matched_event_ret == RMW_RET_OK && publisher_ret == RMW_RET_OK;
}

}  // namespace

int main()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK) {
    std::cout << "{\"status\":\"init_options_failed\"}\n";
    return 1;
  }
  options.instance_id = 823;
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init(&options, &context) != RMW_RET_OK) {
    const rmw_ret_t options_ret = rmw_init_options_fini(&options);
    (void)options_ret;
    std::cout << "{\"status\":\"init_failed\"}\n";
    return 1;
  }
  rmw_node_t * node =
    rmw_create_node(&context, "qos_liveliness_incompatible_probe", "/fleetqox");
  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetrmw_qos_liveliness_incompatible_type";
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();

  // Referenced numerically rather than the deprecated
  // RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_NODE symbol to avoid a
  // -Wdeprecated-declarations warning, matching the rest of this codebase.
  constexpr auto kManualByNode = static_cast<rmw_qos_liveliness_policy_t>(2);
  const rmw_qos_profile_t automatic_100 = liveliness_qos(
    RMW_QOS_POLICY_LIVELINESS_AUTOMATIC, 100);
  const rmw_qos_profile_t manual_node_100 = liveliness_qos(kManualByNode, 100);
  const rmw_qos_profile_t manual_100 = liveliness_qos(
    RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC, 100);
  const rmw_qos_profile_t manual_500 = liveliness_qos(
    RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC, 500);
  const rmw_qos_profile_t manual_default = liveliness_qos(
    RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC, 0);

  const ScenarioResult kind_offered = run_incompatible_scenario(
    &context, node, &type_support, &publisher_options, &subscription_options,
    "/fleetqox/qos_liveliness_kind_offered", automatic_100, manual_100, true);
  const ScenarioResult kind_requested = run_incompatible_scenario(
    &context, node, &type_support, &publisher_options, &subscription_options,
    "/fleetqox/qos_liveliness_kind_requested", automatic_100, manual_100, false);
  // Strictness ordering is AUTOMATIC(1) < MANUAL_BY_NODE(2) <
  // MANUAL_BY_TOPIC(3); these two scenarios cover the middle-rank pairs the
  // original single AUTOMATIC-vs-MANUAL_BY_TOPIC check missed.
  const ScenarioResult kind_automatic_vs_manual_node_offered = run_incompatible_scenario(
    &context, node, &type_support, &publisher_options, &subscription_options,
    "/fleetqox/qos_liveliness_kind_automatic_vs_manual_node_offered",
    automatic_100, manual_node_100, true);
  const ScenarioResult kind_automatic_vs_manual_node_requested = run_incompatible_scenario(
    &context, node, &type_support, &publisher_options, &subscription_options,
    "/fleetqox/qos_liveliness_kind_automatic_vs_manual_node_requested",
    automatic_100, manual_node_100, false);
  const ScenarioResult kind_manual_node_vs_manual_topic_offered = run_incompatible_scenario(
    &context, node, &type_support, &publisher_options, &subscription_options,
    "/fleetqox/qos_liveliness_kind_manual_node_vs_manual_topic_offered",
    manual_node_100, manual_100, true);
  const ScenarioResult kind_manual_node_vs_manual_topic_requested = run_incompatible_scenario(
    &context, node, &type_support, &publisher_options, &subscription_options,
    "/fleetqox/qos_liveliness_kind_manual_node_vs_manual_topic_requested",
    manual_node_100, manual_100, false);
  const ScenarioResult slow_lease_offered = run_incompatible_scenario(
    &context, node, &type_support, &publisher_options, &subscription_options,
    "/fleetqox/qos_liveliness_slow_lease_offered", manual_500, manual_100, true);
  const ScenarioResult slow_lease_requested = run_incompatible_scenario(
    &context, node, &type_support, &publisher_options, &subscription_options,
    "/fleetqox/qos_liveliness_slow_lease_requested", manual_500, manual_100, false);
  const ScenarioResult missing_lease_offered = run_incompatible_scenario(
    &context, node, &type_support, &publisher_options, &subscription_options,
    "/fleetqox/qos_liveliness_missing_lease_offered", manual_default, manual_100, true);
  const ScenarioResult missing_lease_requested = run_incompatible_scenario(
    &context, node, &type_support, &publisher_options, &subscription_options,
    "/fleetqox/qos_liveliness_missing_lease_requested", manual_default, manual_100, false);
  const bool compatible_control = run_compatible_control(
    &context, node, &type_support, &publisher_options, &subscription_options);

  const rmw_ret_t node_ret = node == nullptr ? RMW_RET_ERROR : rmw_destroy_node(node);
  const rmw_ret_t shutdown_ret = rmw_shutdown(&context);
  const rmw_ret_t context_ret = rmw_context_fini(&context);
  const rmw_ret_t options_ret = rmw_init_options_fini(&options);
  const bool clean_teardown = node_ret == RMW_RET_OK && shutdown_ret == RMW_RET_OK &&
    context_ret == RMW_RET_OK && options_ret == RMW_RET_OK;
  const bool ok = node != nullptr && kind_offered.ok && kind_requested.ok &&
    kind_automatic_vs_manual_node_offered.ok && kind_automatic_vs_manual_node_requested.ok &&
    kind_manual_node_vs_manual_topic_offered.ok &&
    kind_manual_node_vs_manual_topic_requested.ok &&
    slow_lease_offered.ok && slow_lease_requested.ok && missing_lease_offered.ok &&
    missing_lease_requested.ok && compatible_control && clean_teardown;
  const std::uint64_t callback_events = kind_offered.callback_events +
    kind_requested.callback_events + kind_automatic_vs_manual_node_offered.callback_events +
    kind_automatic_vs_manual_node_requested.callback_events +
    kind_manual_node_vs_manual_topic_offered.callback_events +
    kind_manual_node_vs_manual_topic_requested.callback_events +
    slow_lease_offered.callback_events +
    slow_lease_requested.callback_events + missing_lease_offered.callback_events +
    missing_lease_requested.callback_events;

  std::cout << "{\"schema_version\":"
            << "\"fleetrmw.qos_liveliness_incompatible_event_probe.v1\","
            << "\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"liveliness_kind_offered_event_claim\":"
            << (kind_offered.ok ? "true" : "false") << ","
            << "\"liveliness_kind_requested_event_claim\":"
            << (kind_requested.ok ? "true" : "false") << ","
            << "\"liveliness_kind_automatic_vs_manual_node_offered_claim\":"
            << (kind_automatic_vs_manual_node_offered.ok ? "true" : "false") << ","
            << "\"liveliness_kind_automatic_vs_manual_node_requested_claim\":"
            << (kind_automatic_vs_manual_node_requested.ok ? "true" : "false") << ","
            << "\"liveliness_kind_manual_node_vs_manual_topic_offered_claim\":"
            << (kind_manual_node_vs_manual_topic_offered.ok ? "true" : "false") << ","
            << "\"liveliness_kind_manual_node_vs_manual_topic_requested_claim\":"
            << (kind_manual_node_vs_manual_topic_requested.ok ? "true" : "false") << ","
            << "\"liveliness_slow_lease_offered_event_claim\":"
            << (slow_lease_offered.ok ? "true" : "false") << ","
            << "\"liveliness_slow_lease_requested_event_claim\":"
            << (slow_lease_requested.ok ? "true" : "false") << ","
            << "\"liveliness_missing_lease_offered_event_claim\":"
            << (missing_lease_offered.ok ? "true" : "false") << ","
            << "\"liveliness_missing_lease_requested_event_claim\":"
            << (missing_lease_requested.ok ? "true" : "false") << ","
            << "\"liveliness_compatible_control_claim\":"
            << (compatible_control ? "true" : "false") << ","
            << "\"scenario_count\":11,"
            << "\"incompatible_event_count\":10,"
            << "\"last_policy_kind\":" << RMW_QOS_POLICY_LIVELINESS << ","
            << "\"callback_events\":" << callback_events << ","
            << "\"clean_teardown\":" << (clean_teardown ? "true" : "false") << "}"
            << std::endl;
  return ok ? 0 : 1;
}
