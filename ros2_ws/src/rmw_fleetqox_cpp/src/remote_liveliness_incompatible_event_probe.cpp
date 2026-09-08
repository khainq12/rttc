// Proves that a liveliness-incompatible-QoS event fires correctly across a
// REAL two-process/UDP-wire boundary, not just in-process. Every existing
// liveliness-incompatible test (qos_liveliness_incompatible_event_probe.cpp)
// creates both endpoints in the same process; every existing remote-graph
// probe that checks incompatible-QoS events (remote_event_probe.cpp) covers
// reliability/durability/deadline but never liveliness. The underlying code
// path (ContentFilterExpressionParser::incompatible_qos_policy_kind() in
// rmw_pubsub.cpp, shared by both local match-checking and remote-endpoint
// discovery) appeared correct on inspection, but had never actually been
// exercised end to end for liveliness over the wire -- this is that
// verification, in both directions (a local subscription against a
// remote-learned publisher, and a local publisher against a
// remote-learned subscription), for both liveliness-incompatible causes
// (kind mismatch and lease-duration mismatch).
#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <thread>

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

constexpr const char * kKindOfferedTopic =
  "/fleetqox/remote_liveliness_incompatible/kind_offered";
constexpr const char * kKindRequestedTopic =
  "/fleetqox/remote_liveliness_incompatible/kind_requested";
constexpr const char * kLeaseOfferedTopic =
  "/fleetqox/remote_liveliness_incompatible/lease_offered";
constexpr const char * kLeaseRequestedTopic =
  "/fleetqox/remote_liveliness_incompatible/lease_requested";
// Strictness ordering is AUTOMATIC(1) < MANUAL_BY_NODE(2) <
// MANUAL_BY_TOPIC(3); these two cover the middle-rank pairs a prior bug in
// liveliness_qos_incompatible() missed (it only special-cased the
// AUTOMATIC-vs-MANUAL_BY_TOPIC pair above). Offered direction only -- the
// requested direction exercises the same rank comparison, already proven
// bidirectionally by kKindOfferedTopic/kKindRequestedTopic above.
constexpr const char * kMidKindAutomaticVsManualNodeTopic =
  "/fleetqox/remote_liveliness_incompatible/mid_kind_automatic_vs_manual_node";
constexpr const char * kMidKindManualNodeVsManualTopicTopic =
  "/fleetqox/remote_liveliness_incompatible/mid_kind_manual_node_vs_manual_topic";
// Referenced numerically rather than the deprecated
// RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_NODE symbol to avoid a
// -Wdeprecated-declarations warning, matching the rest of this codebase.
constexpr auto kManualByNode = static_cast<rmw_qos_liveliness_policy_t>(2);

struct ProbeConfig
{
  std::string mode{"observer"};
  int hold_ms{2200};
  int timeout_ms{9500};
};

ProbeConfig parse_args(int argc, char ** argv)
{
  ProbeConfig config;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--mode" && i + 1 < argc) {
      config.mode = argv[++i];
    } else if (arg == "--hold-ms" && i + 1 < argc) {
      config.hold_ms = std::stoi(argv[++i]);
    } else if (arg == "--timeout-ms" && i + 1 < argc) {
      config.timeout_ms = std::stoi(argv[++i]);
    }
  }
  return config;
}

bool init_context(
  rcutils_allocator_t allocator, rmw_init_options_t * options, rmw_context_t * context)
{
  if (rmw_init_options_init(options, allocator) != RMW_RET_OK) {
    return false;
  }
  options->instance_id = 6041;
  if (rmw_init(options, context) != RMW_RET_OK) {
    const rmw_ret_t ret = rmw_init_options_fini(options);
    (void)ret;
    return false;
  }
  return true;
}

void cleanup_context(rmw_context_t * context, rmw_init_options_t * options)
{
  const rmw_ret_t shutdown_ret = rmw_shutdown(context);
  const rmw_ret_t context_ret = rmw_context_fini(context);
  const rmw_ret_t options_ret = rmw_init_options_fini(options);
  (void)shutdown_ret;
  (void)context_ret;
  (void)options_ret;
}

rmw_qos_profile_t liveliness_qos(rmw_qos_liveliness_policy_t policy, std::uint64_t lease_ms)
{
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 8;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_RELIABLE;
  qos.liveliness = policy;
  qos.liveliness_lease_duration.sec = static_cast<uint32_t>(lease_ms / 1000u);
  qos.liveliness_lease_duration.nsec = static_cast<uint32_t>((lease_ms % 1000u) * 1000000u);
  return qos;
}

bool wait_take_event(
  rmw_event_t * event,
  void * status,
  std::chrono::steady_clock::time_point deadline)
{
  while (std::chrono::steady_clock::now() < deadline) {
    bool taken = false;
    if (rmw_take_event(event, status, &taken) == RMW_RET_OK && taken) {
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }
  return false;
}

int run_advertiser(const ProbeConfig & config)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options{};
  rmw_context_t context{};
  if (!init_context(allocator, &options, &context)) {
    std::cout << "{\"status\":\"init_failed\"}" << std::endl;
    return 1;
  }
  rmw_node_t * node =
    rmw_create_node(&context, "remote_liveliness_incompatible_advertiser", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetqox_remote_liveliness_incompatible_type";
  const rmw_qos_profile_t automatic_100 = liveliness_qos(RMW_QOS_POLICY_LIVELINESS_AUTOMATIC, 100);
  const rmw_qos_profile_t manual_100 =
    liveliness_qos(RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC, 100);
  const rmw_qos_profile_t manual_500 =
    liveliness_qos(RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC, 500);
  const rmw_qos_profile_t manual_node_100 = liveliness_qos(kManualByNode, 100);
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();

  // Liveliness compatibility: offered kind must be at least as strict as
  // requested (AUTOMATIC weaker than MANUAL_BY_TOPIC), and offered lease
  // must be <= requested lease. Everything below is deliberately on the
  // wrong side of one of those rules so the observer sees an incompatible
  // match against a remote-learned endpoint.

  // Remote publisher offers weak AUTOMATIC liveliness on kKindRequestedTopic;
  // the observer's local subscription there requires MANUAL_BY_TOPIC --
  // exercises REQUESTED_QOS_INCOMPATIBLE on the observer against a
  // remote-learned publisher.
  rmw_publisher_t * kind_offered_publisher = rmw_create_publisher(
    node, &type_support, kKindRequestedTopic, &automatic_100, &publisher_options);
  // Remote subscription requires strict MANUAL_BY_TOPIC liveliness on
  // kKindOfferedTopic; the observer's local publisher there only offers weak
  // AUTOMATIC -- exercises OFFERED_QOS_INCOMPATIBLE on the observer against a
  // remote-learned subscription.
  rmw_subscription_t * kind_requested_subscription = rmw_create_subscription(
    node, &type_support, kKindOfferedTopic, &manual_100, &subscription_options);
  // Remote publisher offers a slow 500ms lease on kLeaseRequestedTopic; the
  // observer's local subscription there requires a fast 100ms lease.
  rmw_publisher_t * lease_offered_publisher = rmw_create_publisher(
    node, &type_support, kLeaseRequestedTopic, &manual_500, &publisher_options);
  // Remote subscription requires a fast 100ms lease on kLeaseOfferedTopic;
  // the observer's local publisher there only offers a slow 500ms lease.
  rmw_subscription_t * lease_requested_subscription = rmw_create_subscription(
    node, &type_support, kLeaseOfferedTopic, &manual_100, &subscription_options);
  // Remote subscription requires MANUAL_BY_NODE (rank 2); the observer's
  // local publisher there only offers AUTOMATIC (rank 1).
  rmw_subscription_t * mid_kind_automatic_vs_manual_node_subscription =
    rmw_create_subscription(
    node, &type_support, kMidKindAutomaticVsManualNodeTopic, &manual_node_100,
    &subscription_options);
  // Remote subscription requires MANUAL_BY_TOPIC (rank 3); the observer's
  // local publisher there only offers MANUAL_BY_NODE (rank 2).
  rmw_subscription_t * mid_kind_manual_node_vs_manual_topic_subscription =
    rmw_create_subscription(
    node, &type_support, kMidKindManualNodeVsManualTopicTopic, &manual_100,
    &subscription_options);

  const bool created = kind_offered_publisher != nullptr &&
    kind_requested_subscription != nullptr &&
    lease_offered_publisher != nullptr && lease_requested_subscription != nullptr &&
    mid_kind_automatic_vs_manual_node_subscription != nullptr &&
    mid_kind_manual_node_vs_manual_topic_subscription != nullptr;
  std::cout << "{\"schema_version\":\"fleetrmw.remote_liveliness_incompatible_event_probe.v1\","
            << "\"mode\":\"advertiser\",\"phase\":\"ready\","
            << "\"created\":" << (created ? "true" : "false") << "}" << std::endl;

  if (created) {
    std::this_thread::sleep_for(std::chrono::milliseconds(config.hold_ms));
  }

  bool cleanup_ok = true;
  if (mid_kind_manual_node_vs_manual_topic_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(
      node, mid_kind_manual_node_vs_manual_topic_subscription) == RMW_RET_OK && cleanup_ok;
  }
  if (mid_kind_automatic_vs_manual_node_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(
      node, mid_kind_automatic_vs_manual_node_subscription) == RMW_RET_OK && cleanup_ok;
  }
  if (lease_requested_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, lease_requested_subscription) == RMW_RET_OK &&
      cleanup_ok;
  }
  if (lease_offered_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, lease_offered_publisher) == RMW_RET_OK && cleanup_ok;
  }
  if (kind_requested_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, kind_requested_subscription) == RMW_RET_OK &&
      cleanup_ok;
  }
  if (kind_offered_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, kind_offered_publisher) == RMW_RET_OK && cleanup_ok;
  }
  cleanup_ok = rmw_destroy_node(node) == RMW_RET_OK && cleanup_ok;
  cleanup_context(&context, &options);
  std::cout << "{\"schema_version\":\"fleetrmw.remote_liveliness_incompatible_event_probe.v1\","
            << "\"mode\":\"advertiser\",\"status\":\"" << (created && cleanup_ok ? "ok" : "failed")
            << "\"}" << std::endl;
  return created && cleanup_ok ? 0 : 1;
}

int run_observer(const ProbeConfig & config)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options{};
  rmw_context_t context{};
  if (!init_context(allocator, &options, &context)) {
    std::cout << "{\"status\":\"init_failed\"}" << std::endl;
    return 1;
  }
  rmw_node_t * node =
    rmw_create_node(&context, "remote_liveliness_incompatible_observer", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetqox_remote_liveliness_incompatible_type";
  const rmw_qos_profile_t automatic_100 = liveliness_qos(RMW_QOS_POLICY_LIVELINESS_AUTOMATIC, 100);
  const rmw_qos_profile_t manual_100 =
    liveliness_qos(RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC, 100);
  const rmw_qos_profile_t manual_500 =
    liveliness_qos(RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC, 500);
  const rmw_qos_profile_t manual_node_100 = liveliness_qos(kManualByNode, 100);
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();

  // Weak AUTOMATIC offer; paired against the remote's strict MANUAL_BY_TOPIC
  // subscription request on kKindOfferedTopic -> OFFERED_QOS_INCOMPATIBLE.
  rmw_publisher_t * kind_offered_publisher = rmw_create_publisher(
    node, &type_support, kKindOfferedTopic, &automatic_100, &publisher_options);
  // Strict MANUAL_BY_TOPIC request; paired against the remote's weak
  // AUTOMATIC publisher offer on kKindRequestedTopic ->
  // REQUESTED_QOS_INCOMPATIBLE.
  rmw_subscription_t * kind_requested_subscription = rmw_create_subscription(
    node, &type_support, kKindRequestedTopic, &manual_100, &subscription_options);
  // Slow 500ms lease offer; paired against the remote's fast 100ms
  // subscription request on kLeaseOfferedTopic -> OFFERED_QOS_INCOMPATIBLE.
  rmw_publisher_t * lease_offered_publisher = rmw_create_publisher(
    node, &type_support, kLeaseOfferedTopic, &manual_500, &publisher_options);
  // Fast 100ms lease request; paired against the remote's slow 500ms
  // publisher offer on kLeaseRequestedTopic -> REQUESTED_QOS_INCOMPATIBLE.
  rmw_subscription_t * lease_requested_subscription = rmw_create_subscription(
    node, &type_support, kLeaseRequestedTopic, &manual_100, &subscription_options);
  // Weak AUTOMATIC offer; paired against the remote's MANUAL_BY_NODE
  // subscription request on kMidKindAutomaticVsManualNodeTopic ->
  // OFFERED_QOS_INCOMPATIBLE.
  rmw_publisher_t * mid_kind_automatic_vs_manual_node_publisher = rmw_create_publisher(
    node, &type_support, kMidKindAutomaticVsManualNodeTopic, &automatic_100,
    &publisher_options);
  // MANUAL_BY_NODE offer; paired against the remote's stricter
  // MANUAL_BY_TOPIC subscription request on
  // kMidKindManualNodeVsManualTopicTopic -> OFFERED_QOS_INCOMPATIBLE.
  rmw_publisher_t * mid_kind_manual_node_vs_manual_topic_publisher = rmw_create_publisher(
    node, &type_support, kMidKindManualNodeVsManualTopicTopic, &manual_node_100,
    &publisher_options);

  rmw_event_t kind_offered_event = rmw_get_zero_initialized_event();
  rmw_event_t kind_requested_event = rmw_get_zero_initialized_event();
  rmw_event_t lease_offered_event = rmw_get_zero_initialized_event();
  rmw_event_t lease_requested_event = rmw_get_zero_initialized_event();
  rmw_event_t mid_kind_automatic_vs_manual_node_event = rmw_get_zero_initialized_event();
  rmw_event_t mid_kind_manual_node_vs_manual_topic_event = rmw_get_zero_initialized_event();

  bool initialized = kind_offered_publisher != nullptr &&
    kind_requested_subscription != nullptr && lease_offered_publisher != nullptr &&
    lease_requested_subscription != nullptr &&
    mid_kind_automatic_vs_manual_node_publisher != nullptr &&
    mid_kind_manual_node_vs_manual_topic_publisher != nullptr;
  initialized = initialized && rmw_publisher_event_init(
    &kind_offered_event, kind_offered_publisher, RMW_EVENT_OFFERED_QOS_INCOMPATIBLE) ==
    RMW_RET_OK;
  initialized = initialized && rmw_subscription_event_init(
    &kind_requested_event, kind_requested_subscription,
    RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE) == RMW_RET_OK;
  initialized = initialized && rmw_publisher_event_init(
    &lease_offered_event, lease_offered_publisher, RMW_EVENT_OFFERED_QOS_INCOMPATIBLE) ==
    RMW_RET_OK;
  initialized = initialized && rmw_subscription_event_init(
    &lease_requested_event, lease_requested_subscription,
    RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE) == RMW_RET_OK;
  initialized = initialized && rmw_publisher_event_init(
    &mid_kind_automatic_vs_manual_node_event, mid_kind_automatic_vs_manual_node_publisher,
    RMW_EVENT_OFFERED_QOS_INCOMPATIBLE) == RMW_RET_OK;
  initialized = initialized && rmw_publisher_event_init(
    &mid_kind_manual_node_vs_manual_topic_event,
    mid_kind_manual_node_vs_manual_topic_publisher,
    RMW_EVENT_OFFERED_QOS_INCOMPATIBLE) == RMW_RET_OK;

  std::cout << "{\"schema_version\":\"fleetrmw.remote_liveliness_incompatible_event_probe.v1\","
            << "\"mode\":\"observer\",\"phase\":\"ready\","
            << "\"initialized\":" << (initialized ? "true" : "false") << "}" << std::endl;

  const auto deadline =
    std::chrono::steady_clock::now() + std::chrono::milliseconds(config.timeout_ms);
  rmw_offered_qos_incompatible_event_status_t kind_offered_status{};
  rmw_requested_qos_incompatible_event_status_t kind_requested_status{};
  rmw_offered_qos_incompatible_event_status_t lease_offered_status{};
  rmw_requested_qos_incompatible_event_status_t lease_requested_status{};
  const bool kind_offered_taken = initialized &&
    wait_take_event(&kind_offered_event, &kind_offered_status, deadline);
  const bool kind_requested_taken = initialized &&
    wait_take_event(&kind_requested_event, &kind_requested_status, deadline);
  const bool lease_offered_taken = initialized &&
    wait_take_event(&lease_offered_event, &lease_offered_status, deadline);
  const bool lease_requested_taken = initialized &&
    wait_take_event(&lease_requested_event, &lease_requested_status, deadline);
  rmw_offered_qos_incompatible_event_status_t mid_kind_automatic_vs_manual_node_status{};
  rmw_offered_qos_incompatible_event_status_t mid_kind_manual_node_vs_manual_topic_status{};
  const bool mid_kind_automatic_vs_manual_node_taken = initialized &&
    wait_take_event(
    &mid_kind_automatic_vs_manual_node_event, &mid_kind_automatic_vs_manual_node_status,
    deadline);
  const bool mid_kind_manual_node_vs_manual_topic_taken = initialized &&
    wait_take_event(
    &mid_kind_manual_node_vs_manual_topic_event,
    &mid_kind_manual_node_vs_manual_topic_status, deadline);

  const bool kind_ok =
    kind_offered_taken && kind_offered_status.total_count == 1 &&
    kind_offered_status.total_count_change == 1 &&
    kind_offered_status.last_policy_kind == RMW_QOS_POLICY_LIVELINESS &&
    kind_requested_taken && kind_requested_status.total_count == 1 &&
    kind_requested_status.total_count_change == 1 &&
    kind_requested_status.last_policy_kind == RMW_QOS_POLICY_LIVELINESS;
  const bool lease_ok =
    lease_offered_taken && lease_offered_status.total_count == 1 &&
    lease_offered_status.total_count_change == 1 &&
    lease_offered_status.last_policy_kind == RMW_QOS_POLICY_LIVELINESS &&
    lease_requested_taken && lease_requested_status.total_count == 1 &&
    lease_requested_status.total_count_change == 1 &&
    lease_requested_status.last_policy_kind == RMW_QOS_POLICY_LIVELINESS;
  const bool mid_kind_ok =
    mid_kind_automatic_vs_manual_node_taken &&
    mid_kind_automatic_vs_manual_node_status.total_count == 1 &&
    mid_kind_automatic_vs_manual_node_status.total_count_change == 1 &&
    mid_kind_automatic_vs_manual_node_status.last_policy_kind == RMW_QOS_POLICY_LIVELINESS &&
    mid_kind_manual_node_vs_manual_topic_taken &&
    mid_kind_manual_node_vs_manual_topic_status.total_count == 1 &&
    mid_kind_manual_node_vs_manual_topic_status.total_count_change == 1 &&
    mid_kind_manual_node_vs_manual_topic_status.last_policy_kind == RMW_QOS_POLICY_LIVELINESS;
  const bool ok = initialized && kind_ok && lease_ok && mid_kind_ok;

  std::cout << "{\"schema_version\":\"fleetrmw.remote_liveliness_incompatible_event_probe.v1\","
            << "\"mode\":\"observer\",\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"kind_ok\":" << (kind_ok ? "true" : "false") << ","
            << "\"lease_ok\":" << (lease_ok ? "true" : "false") << ","
            << "\"mid_kind_ok\":" << (mid_kind_ok ? "true" : "false") << ","
            << "\"kind_offered_last_policy_kind\":" << kind_offered_status.last_policy_kind << ","
            << "\"kind_requested_last_policy_kind\":" << kind_requested_status.last_policy_kind
            << ",\"lease_offered_last_policy_kind\":" << lease_offered_status.last_policy_kind
            << ",\"lease_requested_last_policy_kind\":" << lease_requested_status.last_policy_kind
            << ",\"mid_kind_automatic_vs_manual_node_last_policy_kind\":"
            << mid_kind_automatic_vs_manual_node_status.last_policy_kind
            << ",\"mid_kind_manual_node_vs_manual_topic_last_policy_kind\":"
            << mid_kind_manual_node_vs_manual_topic_status.last_policy_kind
            << "}" << std::endl;

  const rmw_ret_t kind_offered_fini = rmw_event_fini(&kind_offered_event);
  const rmw_ret_t kind_requested_fini = rmw_event_fini(&kind_requested_event);
  const rmw_ret_t lease_offered_fini = rmw_event_fini(&lease_offered_event);
  const rmw_ret_t lease_requested_fini = rmw_event_fini(&lease_requested_event);
  const rmw_ret_t mid_kind_automatic_vs_manual_node_fini =
    rmw_event_fini(&mid_kind_automatic_vs_manual_node_event);
  const rmw_ret_t mid_kind_manual_node_vs_manual_topic_fini =
    rmw_event_fini(&mid_kind_manual_node_vs_manual_topic_event);
  (void)kind_offered_fini;
  (void)kind_requested_fini;
  (void)lease_offered_fini;
  (void)lease_requested_fini;
  (void)mid_kind_automatic_vs_manual_node_fini;
  (void)mid_kind_manual_node_vs_manual_topic_fini;
  bool cleanup_ok = true;
  if (mid_kind_manual_node_vs_manual_topic_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, mid_kind_manual_node_vs_manual_topic_publisher) ==
      RMW_RET_OK && cleanup_ok;
  }
  if (mid_kind_automatic_vs_manual_node_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, mid_kind_automatic_vs_manual_node_publisher) ==
      RMW_RET_OK && cleanup_ok;
  }
  if (lease_requested_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, lease_requested_subscription) == RMW_RET_OK &&
      cleanup_ok;
  }
  if (lease_offered_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, lease_offered_publisher) == RMW_RET_OK && cleanup_ok;
  }
  if (kind_requested_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, kind_requested_subscription) == RMW_RET_OK &&
      cleanup_ok;
  }
  if (kind_offered_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, kind_offered_publisher) == RMW_RET_OK && cleanup_ok;
  }
  cleanup_ok = rmw_destroy_node(node) == RMW_RET_OK && cleanup_ok;
  cleanup_context(&context, &options);
  return ok && cleanup_ok ? 0 : 1;
}

}  // namespace

int main(int argc, char ** argv)
{
  const ProbeConfig config = parse_args(argc, argv);
  if (config.mode == "advertiser") {
    return run_advertiser(config);
  }
  if (config.mode == "observer") {
    return run_observer(config);
  }
  std::cout << "{\"status\":\"unknown_mode\"}" << std::endl;
  return 1;
}
