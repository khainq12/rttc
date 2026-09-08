// Proves that remote type-incompatible-event detection uses the RIHS
// structural type hash (rosidl_type_hash_t, from each type support's
// get_type_hash_func) when available, catching a same-declared-type-name-
// but-different-structure mismatch across a real two-process/UDP wire that
// the prior plain type_name string comparison could not detect. Also
// proves the positive control (identical hash -> compatible) and the
// fallback path (neither side has a valid hash, e.g. this repository's own
// hand-built probe type supports elsewhere -> falls back to type_name
// equality, so none of that existing coverage regresses).
#include <chrono>
#include <cstdint>
#include <cstring>
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
#include "rosidl_runtime_c/type_hash.h"

namespace
{

constexpr const char * kHashMismatchOfferedTopic =
  "/fleetqox/remote_type_hash_incompatible/mismatch_offered";
constexpr const char * kHashMismatchRequestedTopic =
  "/fleetqox/remote_type_hash_incompatible/mismatch_requested";
constexpr const char * kHashMatchTopic =
  "/fleetqox/remote_type_hash_incompatible/match_control";
constexpr const char * kNoHashFallbackTopic =
  "/fleetqox/remote_type_hash_incompatible/no_hash_fallback";

// All four topics use type supports sharing this SAME typesupport_identifier
// (and therefore the same type_name, since these hand-built supports have no
// introspection members for type_name_from_type_support() to read instead).
// Only the hash each side's get_type_hash_func reports differs per scenario.
constexpr const char * kSharedTypeName = "fleetqox_remote_type_hash_probe_type";

rosidl_type_hash_t make_hash(std::uint8_t fill)
{
  rosidl_type_hash_t hash{};
  hash.version = 1;
  std::memset(hash.value, fill, ROSIDL_TYPE_HASH_SIZE);
  return hash;
}

const rosidl_type_hash_t kHashA = make_hash(0xAA);
const rosidl_type_hash_t kHashB = make_hash(0xBB);

const rosidl_type_hash_t * get_hash_a(const rosidl_message_type_support_t *)
{
  return &kHashA;
}

const rosidl_type_hash_t * get_hash_b(const rosidl_message_type_support_t *)
{
  return &kHashB;
}

rosidl_message_type_support_t make_type_support(rosidl_message_get_type_hash_function hash_fn)
{
  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = kSharedTypeName;
  type_support.get_type_hash_func = hash_fn;
  return type_support;
}

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
  options->instance_id = 6042;
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

bool wait_take_event(
  rmw_event_t * event,
  rmw_incompatible_type_status_t * status,
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
    rmw_create_node(&context, "remote_type_hash_incompatible_advertiser", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t hash_b_type = make_type_support(&get_hash_b);
  rosidl_message_type_support_t hash_a_type = make_type_support(&get_hash_a);
  rosidl_message_type_support_t no_hash_type = make_type_support(nullptr);
  const rmw_qos_profile_t qos = rmw_qos_profile_default;
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();

  // Remote subscription reports hash B; paired against the observer's
  // local publisher (hash A) on the same shared type name below.
  rmw_subscription_t * mismatch_offered_subscription = rmw_create_subscription(
    node, &hash_b_type, kHashMismatchOfferedTopic, &qos, &subscription_options);
  // Remote publisher reports hash B; paired against the observer's local
  // subscription (hash A) on the same shared type name below.
  rmw_publisher_t * mismatch_requested_publisher = rmw_create_publisher(
    node, &hash_b_type, kHashMismatchRequestedTopic, &qos, &publisher_options);
  // Remote subscription reports hash A -- identical to the observer's local
  // publisher's hash A -- a positive compatible control.
  rmw_subscription_t * match_subscription = rmw_create_subscription(
    node, &hash_a_type, kHashMatchTopic, &qos, &subscription_options);
  // Remote subscription has no valid hash at all, matching the observer's
  // local publisher which also has none -- exercises the type_name-equality
  // fallback this repository's other ~180 hand-built-type-support probes
  // depend on.
  rmw_subscription_t * fallback_subscription = rmw_create_subscription(
    node, &no_hash_type, kNoHashFallbackTopic, &qos, &subscription_options);

  const bool created = mismatch_offered_subscription != nullptr &&
    mismatch_requested_publisher != nullptr && match_subscription != nullptr &&
    fallback_subscription != nullptr;
  std::cout << "{\"schema_version\":\"fleetrmw.remote_type_hash_incompatible_event_probe.v1\","
            << "\"mode\":\"advertiser\",\"phase\":\"ready\","
            << "\"created\":" << (created ? "true" : "false") << "}" << std::endl;

  if (created) {
    std::this_thread::sleep_for(std::chrono::milliseconds(config.hold_ms));
  }

  bool cleanup_ok = true;
  if (fallback_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, fallback_subscription) == RMW_RET_OK &&
      cleanup_ok;
  }
  if (match_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, match_subscription) == RMW_RET_OK && cleanup_ok;
  }
  if (mismatch_requested_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, mismatch_requested_publisher) == RMW_RET_OK &&
      cleanup_ok;
  }
  if (mismatch_offered_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, mismatch_offered_subscription) == RMW_RET_OK &&
      cleanup_ok;
  }
  cleanup_ok = rmw_destroy_node(node) == RMW_RET_OK && cleanup_ok;
  cleanup_context(&context, &options);
  std::cout << "{\"schema_version\":\"fleetrmw.remote_type_hash_incompatible_event_probe.v1\","
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
    rmw_create_node(&context, "remote_type_hash_incompatible_observer", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t hash_a_type = make_type_support(&get_hash_a);
  rosidl_message_type_support_t no_hash_type = make_type_support(nullptr);
  const rmw_qos_profile_t qos = rmw_qos_profile_default;
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();

  rmw_publisher_t * mismatch_offered_publisher = rmw_create_publisher(
    node, &hash_a_type, kHashMismatchOfferedTopic, &qos, &publisher_options);
  rmw_subscription_t * mismatch_requested_subscription = rmw_create_subscription(
    node, &hash_a_type, kHashMismatchRequestedTopic, &qos, &subscription_options);
  rmw_publisher_t * match_publisher = rmw_create_publisher(
    node, &hash_a_type, kHashMatchTopic, &qos, &publisher_options);
  rmw_publisher_t * fallback_publisher = rmw_create_publisher(
    node, &no_hash_type, kNoHashFallbackTopic, &qos, &publisher_options);

  rmw_event_t mismatch_offered_event = rmw_get_zero_initialized_event();
  rmw_event_t mismatch_requested_event = rmw_get_zero_initialized_event();
  rmw_event_t match_event = rmw_get_zero_initialized_event();
  rmw_event_t fallback_event = rmw_get_zero_initialized_event();

  bool initialized = mismatch_offered_publisher != nullptr &&
    mismatch_requested_subscription != nullptr && match_publisher != nullptr &&
    fallback_publisher != nullptr;
  initialized = initialized && rmw_publisher_event_init(
    &mismatch_offered_event, mismatch_offered_publisher,
    RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE) == RMW_RET_OK;
  initialized = initialized && rmw_subscription_event_init(
    &mismatch_requested_event, mismatch_requested_subscription,
    RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE) == RMW_RET_OK;
  initialized = initialized && rmw_publisher_event_init(
    &match_event, match_publisher, RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE) == RMW_RET_OK;
  initialized = initialized && rmw_publisher_event_init(
    &fallback_event, fallback_publisher, RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE) == RMW_RET_OK;

  std::cout << "{\"schema_version\":\"fleetrmw.remote_type_hash_incompatible_event_probe.v1\","
            << "\"mode\":\"observer\",\"phase\":\"ready\","
            << "\"initialized\":" << (initialized ? "true" : "false") << "}" << std::endl;

  const auto deadline =
    std::chrono::steady_clock::now() + std::chrono::milliseconds(config.timeout_ms);
  rmw_incompatible_type_status_t mismatch_offered_status{};
  rmw_incompatible_type_status_t mismatch_requested_status{};
  const bool mismatch_offered_taken = initialized &&
    wait_take_event(&mismatch_offered_event, &mismatch_offered_status, deadline);
  const bool mismatch_requested_taken = initialized &&
    wait_take_event(&mismatch_requested_event, &mismatch_requested_status, deadline);

  // The match/fallback controls must NOT fire: give the wire the same
  // hold window, then take once more (non-blocking-in-effect, since the
  // advertiser has already sent everything by the time the mismatch
  // waits above return) and confirm nothing was ever queued.
  rmw_incompatible_type_status_t match_status{};
  bool match_taken = false;
  const rmw_ret_t match_take_ret =
    rmw_take_event(&match_event, &match_status, &match_taken);
  rmw_incompatible_type_status_t fallback_status{};
  bool fallback_taken = false;
  const rmw_ret_t fallback_take_ret =
    rmw_take_event(&fallback_event, &fallback_status, &fallback_taken);

  const bool mismatch_ok =
    mismatch_offered_taken && mismatch_offered_status.total_count == 1 &&
    mismatch_offered_status.total_count_change == 1 &&
    mismatch_requested_taken && mismatch_requested_status.total_count == 1 &&
    mismatch_requested_status.total_count_change == 1;
  const bool match_ok = match_take_ret == RMW_RET_OK && !match_taken;
  const bool fallback_ok = fallback_take_ret == RMW_RET_OK && !fallback_taken;
  const bool ok = initialized && mismatch_ok && match_ok && fallback_ok;

  std::cout << "{\"schema_version\":\"fleetrmw.remote_type_hash_incompatible_event_probe.v1\","
            << "\"mode\":\"observer\",\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"mismatch_ok\":" << (mismatch_ok ? "true" : "false") << ","
            << "\"match_ok\":" << (match_ok ? "true" : "false") << ","
            << "\"fallback_ok\":" << (fallback_ok ? "true" : "false") << "}" << std::endl;

  const rmw_ret_t mismatch_offered_fini = rmw_event_fini(&mismatch_offered_event);
  const rmw_ret_t mismatch_requested_fini = rmw_event_fini(&mismatch_requested_event);
  const rmw_ret_t match_fini = rmw_event_fini(&match_event);
  const rmw_ret_t fallback_fini = rmw_event_fini(&fallback_event);
  (void)mismatch_offered_fini;
  (void)mismatch_requested_fini;
  (void)match_fini;
  (void)fallback_fini;
  bool cleanup_ok = true;
  if (fallback_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, fallback_publisher) == RMW_RET_OK && cleanup_ok;
  }
  if (match_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, match_publisher) == RMW_RET_OK && cleanup_ok;
  }
  if (mismatch_requested_subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, mismatch_requested_subscription) == RMW_RET_OK &&
      cleanup_ok;
  }
  if (mismatch_offered_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, mismatch_offered_publisher) == RMW_RET_OK &&
      cleanup_ok;
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
