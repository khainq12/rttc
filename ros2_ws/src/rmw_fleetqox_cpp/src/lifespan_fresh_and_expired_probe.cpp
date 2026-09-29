// P2.6 verification: a real two-process/UDP end-to-end proof that QoS
// Lifespan correctly distinguishes a FRESH message from an EXPIRED one
// through the full publisher -> serialize -> transport -> deserialize ->
// lifespan check -> rmw_take path, post cross-host-clock-fix.
//
// frame_exceeds_lifespan(qos, source_timestamp_ns) reads its lifespan
// threshold from the SUBSCRIBER's own configured QoS (not a value embedded
// per-frame on the wire) -- see rmw_pubsub.cpp's two call sites
// (`subscription->qos`/`data->qos`). So "fresh" and "expired" need two
// independent (topic, subscription) pairs, each with the subscriber-side
// lifespan appropriate to what it's testing, rather than one subscriber
// somehow treating two publishers differently. Two topics, each with its
// own publisher+subscription, both driven from the same two processes:
//   - .../fresh: 5s lifespan on both ends -- can never expire within this
//     probe's runtime, so a successful take proves the pipeline works
//     end-to-end for the ordinary case.
//   - .../expired: 2ms lifespan on both ends, shorter than this suite's
//     proven 5ms +-1ms netem delay (see lifespan_message_lost_probe.cpp),
//     so the frame is provably already expired on arrival.
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
#include "rmw/serialized_message.h"
#include "rmw/subscription_options.h"
#include "rosidl_runtime_c/message_type_support_struct.h"

namespace
{

constexpr const char * kFreshTopic = "/fleetqox/lifespan_fresh_and_expired_probe/fresh";
constexpr const char * kExpiredTopic = "/fleetqox/lifespan_fresh_and_expired_probe/expired";
constexpr const char * kPayload = "probe-message";

struct Config
{
  std::string mode{"observer"};
  int timeout_ms{9500};
};

Config parse_args(int argc, char ** argv)
{
  Config config;
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

rmw_qos_profile_t fresh_qos()
{
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 4;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT;
  // 5s: far longer than this probe's own runtime, so this message can never
  // be seen as expired regardless of scheduling jitter.
  qos.lifespan.sec = 5;
  qos.lifespan.nsec = 0;
  return qos;
}

rmw_qos_profile_t expired_qos()
{
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 4;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT;
  // 2ms: shorter than this suite's standard 5ms netem delay (see
  // lifespan_message_lost_probe.cpp), so the frame is provably already
  // expired by the time it reaches the subscriber. frame_exceeds_lifespan()
  // reads this threshold from the SUBSCRIBER's own QoS, so this profile
  // must be used on both the publisher and the subscription for this topic.
  qos.lifespan.sec = 0;
  qos.lifespan.nsec = 2000000;
  return qos;
}

bool publish_one(rmw_publisher_t * publisher)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  const std::string payload = kPayload;
  rmw_serialized_message_t message = rmw_get_zero_initialized_serialized_message();
  bool ok = publisher != nullptr &&
    rmw_serialized_message_init(&message, payload.size(), &allocator) == RMW_RET_OK;
  if (ok) {
    std::memcpy(message.buffer, payload.data(), payload.size());
    message.buffer_length = payload.size();
    ok = rmw_publish_serialized_message(publisher, &message, nullptr) == RMW_RET_OK;
    const rmw_ret_t fini_ret = rmw_serialized_message_fini(&message);
    ok = fini_ret == RMW_RET_OK && ok;
  }
  return ok;
}

int run_advertiser(const Config &)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options{};
  rmw_context_t context{};
  if (!init_context(allocator, &options, &context)) {
    std::cout << "{\"status\":\"init_failed\"}" << std::endl;
    return 1;
  }
  rmw_node_t * node =
    rmw_create_node(&context, "lifespan_fresh_and_expired_publisher", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetqox_lifespan_fresh_and_expired_type";
  const rmw_qos_profile_t fresh_profile = fresh_qos();
  const rmw_qos_profile_t expired_profile = expired_qos();
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  rmw_publisher_t * fresh_publisher =
    rmw_create_publisher(node, &type_support, kFreshTopic, &fresh_profile, &publisher_options);
  rmw_publisher_t * expired_publisher = rmw_create_publisher(
    node, &type_support, kExpiredTopic, &expired_profile, &publisher_options);

  std::cout << "{\"schema_version\":\"fleetrmw.lifespan_fresh_and_expired_probe.v1\","
            << "\"mode\":\"advertiser\",\"phase\":\"ready\","
            << "\"created\":"
            << ((fresh_publisher != nullptr && expired_publisher != nullptr) ? "true" : "false")
            << "}" << std::endl;

  // Let both subscriptions fully match before either publish -- avoids a
  // pre-match BEST_EFFORT loss being mistaken for a lifespan-driven drop.
  std::this_thread::sleep_for(std::chrono::milliseconds(500));

  const bool fresh_publish_ok = publish_one(fresh_publisher);
  const bool expired_publish_ok = publish_one(expired_publisher);

  std::this_thread::sleep_for(std::chrono::milliseconds(1500));

  bool cleanup_ok = true;
  if (fresh_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, fresh_publisher) == RMW_RET_OK && cleanup_ok;
  }
  if (expired_publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, expired_publisher) == RMW_RET_OK && cleanup_ok;
  }
  cleanup_ok = rmw_destroy_node(node) == RMW_RET_OK && cleanup_ok;
  cleanup_context(&context, &options);
  const bool ok = fresh_publish_ok && expired_publish_ok && cleanup_ok;
  std::cout << "{\"schema_version\":\"fleetrmw.lifespan_fresh_and_expired_probe.v1\","
            << "\"mode\":\"advertiser\",\"status\":\"" << (ok ? "ok" : "failed") << "\"}"
            << std::endl;
  return ok ? 0 : 1;
}

struct SubscriptionHarness
{
  rmw_subscription_t * subscription{nullptr};
  rmw_event_t event{};
  bool initialized{false};
  bool taken{false};
  bool status_taken{false};
  rmw_message_lost_status_t status{};
};

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
    rmw_create_node(&context, "lifespan_fresh_and_expired_subscriber", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetqox_lifespan_fresh_and_expired_type";
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();

  SubscriptionHarness fresh;
  SubscriptionHarness expired;
  const rmw_qos_profile_t fresh_profile = fresh_qos();
  const rmw_qos_profile_t expired_profile = expired_qos();
  fresh.subscription = rmw_create_subscription(
    node, &type_support, kFreshTopic, &fresh_profile, &subscription_options);
  expired.subscription = rmw_create_subscription(
    node, &type_support, kExpiredTopic, &expired_profile, &subscription_options);
  fresh.event = rmw_get_zero_initialized_event();
  expired.event = rmw_get_zero_initialized_event();
  fresh.initialized = fresh.subscription != nullptr &&
    rmw_subscription_event_init(&fresh.event, fresh.subscription, RMW_EVENT_MESSAGE_LOST) ==
    RMW_RET_OK;
  expired.initialized = expired.subscription != nullptr &&
    rmw_subscription_event_init(&expired.event, expired.subscription, RMW_EVENT_MESSAGE_LOST) ==
    RMW_RET_OK;

  std::cout << "{\"schema_version\":\"fleetrmw.lifespan_fresh_and_expired_probe.v1\","
            << "\"mode\":\"observer\",\"phase\":\"ready\","
            << "\"initialized\":" << ((fresh.initialized && expired.initialized) ? "true" : "false")
            << "}" << std::endl;

  rcutils_allocator_t msg_allocator = rcutils_get_default_allocator();
  rmw_serialized_message_t incoming = rmw_get_zero_initialized_serialized_message();
  const bool message_ok =
    rmw_serialized_message_init(&incoming, 64, &msg_allocator) == RMW_RET_OK;
  rmw_ret_t take_ret = RMW_RET_OK;
  const auto deadline =
    std::chrono::steady_clock::now() + std::chrono::milliseconds(config.timeout_ms);
  while (fresh.initialized && expired.initialized && message_ok &&
    std::chrono::steady_clock::now() < deadline)
  {
    bool taken = false;
    take_ret = rmw_take_serialized_message(fresh.subscription, &incoming, &taken, nullptr);
    if (take_ret != RMW_RET_OK) {
      break;
    }
    fresh.taken = fresh.taken || taken;
    if (!fresh.status_taken) {
      (void)rmw_take_event(&fresh.event, &fresh.status, &fresh.status_taken);
    }

    taken = false;
    take_ret = rmw_take_serialized_message(expired.subscription, &incoming, &taken, nullptr);
    if (take_ret != RMW_RET_OK) {
      break;
    }
    expired.taken = expired.taken || taken;
    if (!expired.status_taken) {
      (void)rmw_take_event(&expired.event, &expired.status, &expired.status_taken);
    }

    if (fresh.taken && expired.status_taken) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }

  const bool ok = fresh.initialized && expired.initialized && message_ok &&
    take_ret == RMW_RET_OK &&
    fresh.taken && !expired.taken &&
    expired.status_taken && expired.status.total_count >= 1 &&
    !fresh.status_taken;
  std::cout << "{\"schema_version\":\"fleetrmw.lifespan_fresh_and_expired_probe.v1\","
            << "\"mode\":\"observer\",\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"fresh_taken\":" << (fresh.taken ? "true" : "false") << ","
            << "\"fresh_message_lost_taken\":" << (fresh.status_taken ? "true" : "false") << ","
            << "\"expired_taken\":" << (expired.taken ? "true" : "false") << ","
            << "\"expired_message_lost_taken\":" << (expired.status_taken ? "true" : "false") << ","
            << "\"expired_message_lost_total_count\":" << expired.status.total_count << "}"
            << std::endl;

  bool cleanup_ok = true;
  if (message_ok) {
    cleanup_ok = rmw_serialized_message_fini(&incoming) == RMW_RET_OK && cleanup_ok;
  }
  if (fresh.initialized) {
    cleanup_ok = rmw_event_fini(&fresh.event) == RMW_RET_OK && cleanup_ok;
  }
  if (expired.initialized) {
    cleanup_ok = rmw_event_fini(&expired.event) == RMW_RET_OK && cleanup_ok;
  }
  if (fresh.subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, fresh.subscription) == RMW_RET_OK && cleanup_ok;
  }
  if (expired.subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, expired.subscription) == RMW_RET_OK &&
      cleanup_ok;
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
