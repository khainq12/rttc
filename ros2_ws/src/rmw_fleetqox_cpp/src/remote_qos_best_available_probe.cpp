// Proves that BEST_AVAILABLE liveliness resolution correctly takes a real,
// UDP-discovered REMOTE endpoint's QoS into account, not just local ones.
// qos_best_available_probe.cpp is the only existing coverage for
// RMW_QOS_POLICY_LIVELINESS_BEST_AVAILABLE, and it only ever pairs a
// BEST_AVAILABLE endpoint against a LOCAL, same-process endpoint.
// rmw_create_publisher/rmw_create_subscription resolve BEST_AVAILABLE via
// rmw_dds_common::qos_profile_get_best_available_for_topic_publisher/
// subscription, using rmw_get_publishers/subscriptions_info_by_topic as the
// data source -- and that data source's endpoint_snapshot() does merge
// UDP-learned remote endpoints with local ones, so this plausibly already
// worked, but had never actually been exercised end to end. Resolution
// happens synchronously and exactly once at creation time
// (best_available_policy_frozen_after_create_claim), so this also probes
// the creation-order dependency directly: the remote endpoint's graph
// advertisement must have already been received before the BEST_AVAILABLE
// side is created, or resolution falls back to as-if no endpoint existed.
#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <thread>

#include "rcutils/allocator.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/publisher_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/subscription_options.h"
#include "rosidl_runtime_c/message_type_support_struct.h"

namespace
{

constexpr const char * kRemoteSubscriptionTopic =
  "/fleetqox/remote_qos_best_available/remote_subscription";
constexpr const char * kRemotePublisherTopic =
  "/fleetqox/remote_qos_best_available/remote_publisher";

struct Config
{
  std::string mode{"observer"};
  // Margin for the advertiser's remote endpoint to actually be discovered
  // (its GraphAdvertisement received and applied) before the observer
  // creates its BEST_AVAILABLE endpoints. Resolution happens exactly once,
  // synchronously, at creation time, so this must comfortably cover
  // container/process startup variance, not just wire transit -- 1000ms
  // measured flaky in practice, 3000ms did not.
  int settle_ms{3000};
};

Config parse_args(int argc, char ** argv)
{
  Config config;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--mode" && i + 1 < argc) {
      config.mode = argv[++i];
    } else if (arg == "--settle-ms" && i + 1 < argc) {
      config.settle_ms = std::stoi(argv[++i]);
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
  options->instance_id = 6045;
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
  qos.durability = RMW_QOS_POLICY_DURABILITY_VOLATILE;
  qos.liveliness = policy;
  qos.liveliness_lease_duration.sec = static_cast<uint32_t>(lease_ms / 1000u);
  qos.liveliness_lease_duration.nsec = static_cast<uint32_t>((lease_ms % 1000u) * 1000000u);
  return qos;
}

bool duration_ms_is(const rmw_time_t & duration, std::uint64_t expected_ms)
{
  return duration.sec == expected_ms / 1000u &&
         duration.nsec == (expected_ms % 1000u) * 1000000u;
}

// The advertiser holds a MANUAL_BY_TOPIC/200ms remote subscription on
// kRemoteSubscriptionTopic (feeding the observer's BEST_AVAILABLE
// publisher there) and an AUTOMATIC/300ms remote publisher on
// kRemotePublisherTopic (feeding the observer's BEST_AVAILABLE
// subscription there).
int run_advertiser(const Config & config)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options{};
  rmw_context_t context{};
  if (!init_context(allocator, &options, &context)) {
    std::cout << "{\"status\":\"init_failed\"}" << std::endl;
    return 1;
  }
  rmw_node_t * node =
    rmw_create_node(&context, "remote_qos_best_available_advertiser", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetqox_remote_qos_best_available_type";
  const rmw_qos_profile_t manual_200 =
    liveliness_qos(RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC, 200);
  const rmw_qos_profile_t automatic_300 =
    liveliness_qos(RMW_QOS_POLICY_LIVELINESS_AUTOMATIC, 300);
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();

  rmw_subscription_t * remote_subscription = rmw_create_subscription(
    node, &type_support, kRemoteSubscriptionTopic, &manual_200, &subscription_options);
  rmw_publisher_t * remote_publisher = rmw_create_publisher(
    node, &type_support, kRemotePublisherTopic, &automatic_300, &publisher_options);

  const bool created = remote_subscription != nullptr && remote_publisher != nullptr;
  std::cout << "{\"schema_version\":\"fleetrmw.remote_qos_best_available_probe.v1\","
            << "\"mode\":\"advertiser\",\"phase\":\"ready\","
            << "\"created\":" << (created ? "true" : "false") << "}" << std::endl;

  if (created) {
    std::this_thread::sleep_for(std::chrono::milliseconds(config.settle_ms + 1500));
  }

  bool cleanup_ok = true;
  if (remote_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, remote_publisher) == RMW_RET_OK && cleanup_ok;
  }
  if (remote_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, remote_subscription) == RMW_RET_OK &&
      cleanup_ok;
  }
  cleanup_ok = rmw_destroy_node(node) == RMW_RET_OK && cleanup_ok;
  cleanup_context(&context, &options);
  const bool ok = created && cleanup_ok;
  std::cout << "{\"schema_version\":\"fleetrmw.remote_qos_best_available_probe.v1\","
            << "\"mode\":\"advertiser\",\"status\":\"" << (ok ? "ok" : "failed") << "\"}"
            << std::endl;
  return ok ? 0 : 1;
}

int run_observer(const Config & config)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options{};
  rmw_context_t context{};
  if (!init_context(allocator, &options, &context)) {
    std::cout << "{\"status\":\"init_failed\"}" << std::endl;
    return 1;
  }
  rmw_node_t * node =
    rmw_create_node(&context, "remote_qos_best_available_observer", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }
  std::cout << "{\"schema_version\":\"fleetrmw.remote_qos_best_available_probe.v1\","
            << "\"mode\":\"observer\",\"phase\":\"ready\","
            << "\"initialized\":true}" << std::endl;

  // The advertiser's remote subscription/publisher must have already been
  // discovered (its GraphAdvertisement received and applied) before these
  // BEST_AVAILABLE endpoints are created -- resolution happens exactly
  // once, synchronously, at creation time.
  std::this_thread::sleep_for(std::chrono::milliseconds(config.settle_ms));

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetqox_remote_qos_best_available_type";
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();

  // Against the advertiser's remote MANUAL_BY_TOPIC/200ms subscription:
  // a local BEST_AVAILABLE publisher should resolve to MANUAL_BY_TOPIC/200ms.
  rmw_publisher_t * best_publisher = rmw_create_publisher(
    node, &type_support, kRemoteSubscriptionTopic, &rmw_qos_profile_best_available,
    &publisher_options);
  rmw_qos_profile_t best_publisher_actual{};
  const bool best_publisher_resolved = best_publisher != nullptr &&
    rmw_publisher_get_actual_qos(best_publisher, &best_publisher_actual) == RMW_RET_OK &&
    best_publisher_actual.liveliness == RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC &&
    duration_ms_is(best_publisher_actual.liveliness_lease_duration, 200);

  // Against the advertiser's remote AUTOMATIC/300ms publisher: a local
  // BEST_AVAILABLE subscription should resolve to AUTOMATIC/300ms.
  rmw_subscription_t * best_subscription = rmw_create_subscription(
    node, &type_support, kRemotePublisherTopic, &rmw_qos_profile_best_available,
    &subscription_options);
  rmw_qos_profile_t best_subscription_actual{};
  const bool best_subscription_resolved = best_subscription != nullptr &&
    rmw_subscription_get_actual_qos(best_subscription, &best_subscription_actual) ==
    RMW_RET_OK &&
    best_subscription_actual.liveliness == RMW_QOS_POLICY_LIVELINESS_AUTOMATIC &&
    duration_ms_is(best_subscription_actual.liveliness_lease_duration, 300);

  const bool ok = best_publisher_resolved && best_subscription_resolved;
  std::cout << "{\"schema_version\":\"fleetrmw.remote_qos_best_available_probe.v1\","
            << "\"mode\":\"observer\",\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"best_publisher_resolved_against_remote_subscription_claim\":"
            << (best_publisher_resolved ? "true" : "false") << ","
            << "\"best_subscription_resolved_against_remote_publisher_claim\":"
            << (best_subscription_resolved ? "true" : "false") << ","
            << "\"best_publisher_liveliness\":" << best_publisher_actual.liveliness << ","
            << "\"best_publisher_lease_sec\":"
            << best_publisher_actual.liveliness_lease_duration.sec << ","
            << "\"best_publisher_lease_nsec\":"
            << best_publisher_actual.liveliness_lease_duration.nsec << ","
            << "\"best_subscription_liveliness\":" << best_subscription_actual.liveliness
            << ","
            << "\"best_subscription_lease_sec\":"
            << best_subscription_actual.liveliness_lease_duration.sec << ","
            << "\"best_subscription_lease_nsec\":"
            << best_subscription_actual.liveliness_lease_duration.nsec << "}" << std::endl;

  bool cleanup_ok = true;
  if (best_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, best_subscription) == RMW_RET_OK && cleanup_ok;
  }
  if (best_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, best_publisher) == RMW_RET_OK && cleanup_ok;
  }
  cleanup_ok = rmw_destroy_node(node) == RMW_RET_OK && cleanup_ok;
  cleanup_context(&context, &options);
  return ok && cleanup_ok ? 0 : 1;
}

}  // namespace

int main(int argc, char ** argv)
{
  const Config config = parse_args(argc, argv);
  if (config.mode == "advertiser") {
    return run_advertiser(config);
  }
  if (config.mode == "observer") {
    return run_observer(config);
  }
  std::cout << "{\"status\":\"unknown_mode\"}" << std::endl;
  return 1;
}
