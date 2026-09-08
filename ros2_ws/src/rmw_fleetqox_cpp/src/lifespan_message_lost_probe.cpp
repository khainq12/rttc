// Proves that a LIFESPAN-expired frame produces a message-lost event, over
// a real two-process/UDP wire, even when it is the LAST frame ever sent on
// the stream (no later message ever arrives to reveal the gap via ordinary
// out-of-order sequence-gap detection).
//
// frame_exceeds_lifespan() was originally checked in enqueue_received_frame()
// BEFORE observe_frame() recorded the frame's sequence number, so a
// lifespan-dropped frame's sequence was never observed at all -- it never
// advanced highest_observed_sequence and never created a detectable "hole"
// for the existing gap-detection/repair machinery to catch. If no
// subsequent, non-expired frame ever arrived on the same stream, the loss
// was 100% invisible: message_lost_total_count never incremented, no
// callback ever fired, rmw_take_event(RMW_EVENT_MESSAGE_LOST) had nothing to
// report. Fixed by moving the lifespan check to after observe_frame() and
// explicitly calling record_subscription_message_lost_locked() when it
// fires; the take-path lifespan drop (a frame that was NOT yet expired at
// enqueue but became stale sitting in the queue) got the same treatment for
// the same reason. This probe publishes exactly one frame and never
// publishes again, so it can only pass if the loss is now visible with zero
// follow-up traffic.
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

constexpr const char * kTopic = "/fleetqox/lifespan_message_lost_probe";

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
  options->instance_id = 6044;
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

rmw_qos_profile_t lifespan_qos()
{
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 4;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT;
  // 2ms: shorter than this suite's standard 5ms netem delay, so the frame
  // is provably already expired by the time it reaches the subscriber --
  // exercising the enqueue-time drop, not just a take-time one.
  qos.lifespan.sec = 0;
  qos.lifespan.nsec = 2000000;
  return qos;
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
  rmw_node_t * node = rmw_create_node(&context, "lifespan_message_lost_publisher", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetqox_lifespan_message_lost_type";
  const rmw_qos_profile_t qos = lifespan_qos();
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  rmw_publisher_t * publisher =
    rmw_create_publisher(node, &type_support, kTopic, &qos, &publisher_options);

  std::cout << "{\"schema_version\":\"fleetrmw.lifespan_message_lost_probe.v1\","
            << "\"mode\":\"advertiser\",\"phase\":\"ready\","
            << "\"created\":" << (publisher != nullptr ? "true" : "false") << "}" << std::endl;

  const std::string payload = "only-frame-ever-sent";
  rmw_serialized_message_t message = rmw_get_zero_initialized_serialized_message();
  bool publish_ok = publisher != nullptr &&
    rmw_serialized_message_init(&message, payload.size(), &allocator) == RMW_RET_OK;
  if (publish_ok) {
    std::memcpy(message.buffer, payload.data(), payload.size());
    message.buffer_length = payload.size();
    publish_ok = rmw_publish_serialized_message(publisher, &message, nullptr) == RMW_RET_OK;
    const rmw_ret_t fini_ret = rmw_serialized_message_fini(&message);
    publish_ok = fini_ret == RMW_RET_OK && publish_ok;
  }
  // No second publish -- this is deliberately the only frame ever sent on
  // this stream, so nothing but the fix under test can reveal its loss.
  std::this_thread::sleep_for(std::chrono::milliseconds(1500));

  bool cleanup_ok = true;
  if (publisher != nullptr) {
    cleanup_ok = rmw_destroy_publisher(node, publisher) == RMW_RET_OK && cleanup_ok;
  }
  cleanup_ok = rmw_destroy_node(node) == RMW_RET_OK && cleanup_ok;
  cleanup_context(&context, &options);
  const bool ok = publish_ok && cleanup_ok;
  std::cout << "{\"schema_version\":\"fleetrmw.lifespan_message_lost_probe.v1\","
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
  rmw_node_t * node = rmw_create_node(&context, "lifespan_message_lost_subscriber", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetqox_lifespan_message_lost_type";
  const rmw_qos_profile_t qos = lifespan_qos();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();
  rmw_subscription_t * subscription =
    rmw_create_subscription(node, &type_support, kTopic, &qos, &subscription_options);
  rmw_event_t event = rmw_get_zero_initialized_event();
  const bool initialized = subscription != nullptr &&
    rmw_subscription_event_init(&event, subscription, RMW_EVENT_MESSAGE_LOST) == RMW_RET_OK;

  std::cout << "{\"schema_version\":\"fleetrmw.lifespan_message_lost_probe.v1\","
            << "\"mode\":\"observer\",\"phase\":\"ready\","
            << "\"initialized\":" << (initialized ? "true" : "false") << "}" << std::endl;

  rmw_serialized_message_t incoming = rmw_get_zero_initialized_serialized_message();
  const bool message_ok = rmw_serialized_message_init(&incoming, 1, &allocator) == RMW_RET_OK;
  bool taken_any = false;
  rmw_ret_t take_ret = RMW_RET_OK;
  const auto deadline =
    std::chrono::steady_clock::now() + std::chrono::milliseconds(config.timeout_ms);
  rmw_message_lost_status_t status{};
  bool status_taken = false;
  while (initialized && message_ok && std::chrono::steady_clock::now() < deadline) {
    bool taken = false;
    take_ret = rmw_take_serialized_message(subscription, &incoming, &taken, nullptr);
    if (take_ret != RMW_RET_OK) {
      break;
    }
    taken_any = taken_any || taken;
    if (rmw_take_event(&event, &status, &status_taken) == RMW_RET_OK && status_taken) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }

  const bool ok = initialized && message_ok && take_ret == RMW_RET_OK && !taken_any &&
    status_taken && status.total_count == 1 && status.total_count_change == 1;
  std::cout << "{\"schema_version\":\"fleetrmw.lifespan_message_lost_probe.v1\","
            << "\"mode\":\"observer\",\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"taken_any\":" << (taken_any ? "true" : "false") << ","
            << "\"message_lost_taken\":" << (status_taken ? "true" : "false") << ","
            << "\"message_lost_total_count\":" << status.total_count << ","
            << "\"message_lost_total_count_change\":" << status.total_count_change << "}"
            << std::endl;

  bool cleanup_ok = true;
  if (message_ok) {
    cleanup_ok = rmw_serialized_message_fini(&incoming) == RMW_RET_OK && cleanup_ok;
  }
  if (initialized) {
    cleanup_ok = rmw_event_fini(&event) == RMW_RET_OK && cleanup_ok;
  }
  if (subscription != nullptr) {
    cleanup_ok = rmw_destroy_subscription(node, subscription) == RMW_RET_OK && cleanup_ok;
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
