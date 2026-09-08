// Proves MANUAL_BY_NODE liveliness assertion-sharing across a real
// two-process/UDP wire: asserting one remote publisher renews every OTHER
// remote MANUAL_BY_NODE publisher owned by the SAME remote node (grouped by
// domain_id + node_name + node_namespace, which already travel in
// GraphAdvertisement), mirroring the local same-process fan-out proven by
// manual_by_node_liveliness_probe.cpp.
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

// Referenced numerically rather than the deprecated
// RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_NODE symbol to avoid a
// -Wdeprecated-declarations warning, matching the rest of this codebase.
constexpr auto kManualByNode = static_cast<rmw_qos_liveliness_policy_t>(2);

constexpr const char * kTopicA = "/fleetqox/remote_manual_by_node/topic_a";
constexpr const char * kTopicB = "/fleetqox/remote_manual_by_node/topic_b";

struct ProbeConfig
{
  std::string mode{"observer"};
  int timeout_ms{9500};
};

ProbeConfig parse_args(int argc, char ** argv)
{
  ProbeConfig config;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--mode" && i + 1 < argc) {
      config.mode = argv[++i];
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
  options->instance_id = 6043;
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

rmw_qos_profile_t manual_by_node_qos(std::uint64_t lease_ms)
{
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 8;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT;
  qos.liveliness = kManualByNode;
  qos.liveliness_lease_duration.sec = static_cast<uint32_t>(lease_ms / 1000u);
  qos.liveliness_lease_duration.nsec = static_cast<uint32_t>((lease_ms % 1000u) * 1000000u);
  return qos;
}

bool wait_take_event(
  rmw_event_t * event,
  rmw_liveliness_changed_status_t * status,
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

bool event_not_ready_once(rmw_event_t * event)
{
  rmw_liveliness_changed_status_t status{};
  bool taken = false;
  return rmw_take_event(event, &status, &taken) == RMW_RET_OK && !taken;
}

int run_advertiser(const ProbeConfig & config)
{
  (void)config;
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options{};
  rmw_context_t context{};
  if (!init_context(allocator, &options, &context)) {
    std::cout << "{\"status\":\"init_failed\"}" << std::endl;
    return 1;
  }
  rmw_node_t * node =
    rmw_create_node(&context, "remote_manual_by_node_advertiser", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetqox_remote_manual_by_node_type";
  const rmw_qos_profile_t qos = manual_by_node_qos(200);
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();

  // Both publishers are owned by this SAME node -- that shared node_name/
  // node_namespace (already carried in every GraphAdvertisement) is what
  // the observer's remote sibling fan-out groups on.
  rmw_publisher_t * publisher_a =
    rmw_create_publisher(node, &type_support, kTopicA, &qos, &publisher_options);
  rmw_publisher_t * publisher_b =
    rmw_create_publisher(node, &type_support, kTopicB, &qos, &publisher_options);
  const bool created = publisher_a != nullptr && publisher_b != nullptr;
  std::cout << "{\"schema_version\":\"fleetrmw.remote_manual_by_node_liveliness_probe.v1\","
            << "\"mode\":\"advertiser\",\"phase\":\"ready\","
            << "\"created\":" << (created ? "true" : "false") << "}" << std::endl;

  // Assert publisher_a only, every 60ms for 700ms; publisher_b never
  // asserts and never publishes.
  bool assert_ok = created;
  if (created) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(700);
    while (std::chrono::steady_clock::now() < deadline) {
      assert_ok = rmw_publisher_assert_liveliness(publisher_a) == RMW_RET_OK && assert_ok;
      std::this_thread::sleep_for(std::chrono::milliseconds(60));
    }
  }
  std::cout << "{\"schema_version\":\"fleetrmw.remote_manual_by_node_liveliness_probe.v1\","
            << "\"mode\":\"advertiser\",\"phase\":\"sharing_window_done\","
            << "\"assert_ok\":" << (assert_ok ? "true" : "false") << "}" << std::endl;

  // Now go fully idle on both publishers so the observer can additionally
  // confirm the shared lease still expires normally once nobody asserts.
  std::this_thread::sleep_for(std::chrono::milliseconds(700));

  bool cleanup_ok = true;
  if (publisher_b != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, publisher_b) == RMW_RET_OK && cleanup_ok;
  }
  if (publisher_a != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, publisher_a) == RMW_RET_OK && cleanup_ok;
  }
  cleanup_ok = rmw_destroy_node(node) == RMW_RET_OK && cleanup_ok;
  cleanup_context(&context, &options);
  const bool ok = created && assert_ok && cleanup_ok;
  std::cout << "{\"schema_version\":\"fleetrmw.remote_manual_by_node_liveliness_probe.v1\","
            << "\"mode\":\"advertiser\",\"status\":\"" << (ok ? "ok" : "failed") << "\"}"
            << std::endl;
  return ok ? 0 : 1;
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
    rmw_create_node(&context, "remote_manual_by_node_observer", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetqox_remote_manual_by_node_type";
  const rmw_qos_profile_t qos = manual_by_node_qos(200);
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();

  rmw_subscription_t * subscription_a =
    rmw_create_subscription(node, &type_support, kTopicA, &qos, &subscription_options);
  rmw_subscription_t * subscription_b =
    rmw_create_subscription(node, &type_support, kTopicB, &qos, &subscription_options);
  rmw_event_t event_a = rmw_get_zero_initialized_event();
  rmw_event_t event_b = rmw_get_zero_initialized_event();
  bool initialized = subscription_a != nullptr && subscription_b != nullptr;
  initialized = initialized && rmw_subscription_event_init(
    &event_a, subscription_a, RMW_EVENT_LIVELINESS_CHANGED) == RMW_RET_OK;
  initialized = initialized && rmw_subscription_event_init(
    &event_b, subscription_b, RMW_EVENT_LIVELINESS_CHANGED) == RMW_RET_OK;

  std::cout << "{\"schema_version\":\"fleetrmw.remote_manual_by_node_liveliness_probe.v1\","
            << "\"mode\":\"observer\",\"phase\":\"ready\","
            << "\"initialized\":" << (initialized ? "true" : "false") << "}" << std::endl;

  const auto overall_deadline =
    std::chrono::steady_clock::now() + std::chrono::milliseconds(config.timeout_ms);
  rmw_liveliness_changed_status_t connect_a{};
  rmw_liveliness_changed_status_t connect_b{};
  const bool connected = initialized &&
    wait_take_event(&event_a, &connect_a, overall_deadline) &&
    connect_a.alive_count == 1 && connect_a.alive_count_change == 1 &&
    wait_take_event(&event_b, &connect_b, overall_deadline) &&
    connect_b.alive_count == 1 && connect_b.alive_count_change == 1;

  // During the advertiser's 700ms sharing window (publisher_a asserting,
  // publisher_b silent), poll for ~600ms: neither topic should see a
  // not-alive transition.
  bool sharing_ok = connected;
  if (connected) {
    const auto sharing_deadline =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(600);
    while (std::chrono::steady_clock::now() < sharing_deadline) {
      if (!event_not_ready_once(&event_a) || !event_not_ready_once(&event_b)) {
        sharing_ok = false;
        break;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
  }

  // After the advertiser goes fully idle, both must eventually expire.
  rmw_liveliness_changed_status_t expiry_a{};
  rmw_liveliness_changed_status_t expiry_b{};
  const bool expiry_ok = sharing_ok &&
    wait_take_event(&event_a, &expiry_a, overall_deadline) &&
    expiry_a.not_alive_count == 1 && expiry_a.alive_count == 0 &&
    wait_take_event(&event_b, &expiry_b, overall_deadline) &&
    expiry_b.not_alive_count == 1 && expiry_b.alive_count == 0;

  const bool ok = initialized && connected && sharing_ok && expiry_ok;
  std::cout << "{\"schema_version\":\"fleetrmw.remote_manual_by_node_liveliness_probe.v1\","
            << "\"mode\":\"observer\",\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"connected\":" << (connected ? "true" : "false") << ","
            << "\"remote_manual_by_node_assertion_sharing_claim\":"
            << (sharing_ok ? "true" : "false") << ","
            << "\"remote_manual_by_node_shared_lease_still_expires_claim\":"
            << (expiry_ok ? "true" : "false") << "}" << std::endl;

  const rmw_ret_t event_a_fini = rmw_event_fini(&event_a);
  const rmw_ret_t event_b_fini = rmw_event_fini(&event_b);
  (void)event_a_fini;
  (void)event_b_fini;
  bool cleanup_ok = true;
  if (subscription_b != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, subscription_b) == RMW_RET_OK && cleanup_ok;
  }
  if (subscription_a != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, subscription_a) == RMW_RET_OK && cleanup_ok;
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
