// Proves the FleetQoX PRESENTATION extension (see qos_extensions.hpp,
// presentation_group.hpp) at GROUP access scope: coherent+ordered delivery
// across MULTIPLE TOPICS sharing one presentation_group_id, which real DDS
// models as a property of the Publisher/Subscriber entity spanning many
// DataWriters/DataReaders -- rmw has no equivalent above a single
// publisher/subscription, so there is no standard hook for this at all.
//
// Single process, real loopback UDP wire (this RMW's own local-delivery
// socket, the same one every other probe's same-process pub/sub already
// uses) -- the property under test (does begin/end_coherent_changes defer
// and then atomically flush) is an RMW-instance-local behavior, not a
// distributed one, so a second host or container adds nothing here.
//
// Uses the raw rmw C API (like manual_by_node_liveliness_probe.cpp) rather
// than rclcpp, both because rclcpp has no coherent-changes concept to wrap
// and because calling rmw_fleetqox_cpp_begin/end_coherent_changes directly
// requires linking against this library (see presentation_group.hpp),
// which every other QoS-extension probe this session avoided needing.
// Payload content is carried via rmw_publish_serialized_message/
// rmw_take_serialized_message with a fabricated typesupport identifier
// (like every other raw-C probe here) -- this RMW never interprets those
// bytes, so a plain label string works fine.
//
// Claims:
//  - control_immediate_delivery_claim: a publish made before any
//    begin_coherent_changes call is delivered immediately, unaffected by
//    this publisher's own presentation_coherent_access=true setting --
//    proves buffering is triggered by begin/end, not by QoS configuration
//    alone.
//  - held_before_flush_claim: publishes made *inside* a begin/end span are
//    not observable by either topic's subscription at any point before end
//    is called, even after a real hold window -- proves the RMW actually
//    defers the wire send, not just receiver-side visibility.
//  - atomic_joint_delivery_claim: once end_coherent_changes flushes, BOTH
//    topics' messages for that round become available together, with the
//    correct content -- proves the GROUP-scope flush actually spans both
//    topics as one set, not two independent sends that merely raced to
//    arrive close together.
//  - ordered_release_claim: two publishes on the SAME topic inside one
//    coherent set, with a different topic's publish interleaved between
//    them, are still released in original publish order -- proves
//    ordered_access is honored across the whole flush, not just FIFO
//    within one topic's own queue.
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

#include "rcutils/allocator.h"
#include "rmw/rmw.h"
#include "rmw/serialized_message.h"
#include "rmw_fleetqox_cpp/presentation_group.hpp"
#include "rmw_fleetqox_cpp/qos_extensions.hpp"
#include "rosidl_runtime_c/message_type_support_struct.h"

namespace
{

constexpr const char * kTopicA = "/fleetqox/presentation/topic_a";
constexpr const char * kTopicB = "/fleetqox/presentation/topic_b";
constexpr const char * kGroupId = "presentation_group_1";

rmw_qos_profile_t coherent_qos()
{
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 8;
  qos.reliability = RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT;
  return qos;
}

bool publish_string(rmw_publisher_t * publisher, const std::string & text)
{
  if (publisher == nullptr) {
    return false;
  }
  rmw_serialized_message_t message = rmw_get_zero_initialized_serialized_message();
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (rmw_serialized_message_init(&message, text.size(), &allocator) != RMW_RET_OK) {
    return false;
  }
  std::memcpy(message.buffer, text.data(), text.size());
  message.buffer_length = text.size();
  const rmw_ret_t ret = rmw_publish_serialized_message(publisher, &message, nullptr);
  (void)rmw_serialized_message_fini(&message);
  return ret == RMW_RET_OK;
}

bool try_take_string(rmw_subscription_t * subscription, std::string * out)
{
  if (subscription == nullptr) {
    return false;
  }
  rmw_serialized_message_t message = rmw_get_zero_initialized_serialized_message();
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (rmw_serialized_message_init(&message, 0, &allocator) != RMW_RET_OK) {
    return false;
  }
  bool taken = false;
  const rmw_ret_t ret = rmw_take_serialized_message(subscription, &message, &taken, nullptr);
  bool ok = false;
  if (ret == RMW_RET_OK && taken) {
    out->assign(reinterpret_cast<const char *>(message.buffer), message.buffer_length);
    ok = true;
  }
  (void)rmw_serialized_message_fini(&message);
  return ok;
}

// Polls every 10ms up to timeout_ms; returns true as soon as a message is
// taken, false if the timeout elapses with nothing available.
bool wait_take_string(rmw_subscription_t * subscription, int timeout_ms, std::string * out)
{
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    if (try_take_string(subscription, out)) {
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  return false;
}

// Polls every 10ms for hold_ms and returns true only if NEITHER
// subscription ever has data available during that whole window --
// i.e. proves the frames genuinely were not sent yet, not merely that we
// got unlucky on timing.
bool confirm_held(rmw_subscription_t * sub_a, rmw_subscription_t * sub_b, int hold_ms)
{
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(hold_ms);
  std::string scratch;
  while (std::chrono::steady_clock::now() < deadline) {
    if (try_take_string(sub_a, &scratch) || try_take_string(sub_b, &scratch)) {
      return false;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  return true;
}

}  // namespace

int main()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK) {
    return 1;
  }
  options.instance_id = 941;
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init(&options, &context) != RMW_RET_OK) {
    (void)rmw_init_options_fini(&options);
    return 1;
  }

  rmw_node_t * node = rmw_create_node(&context, "presentation_probe", "/fleetqox");
  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetrmw_presentation_probe_type";
  const rmw_qos_profile_t qos = coherent_qos();

  rmw_fleetqox_cpp::FleetQoxExtendedQosPayload group_qos;
  group_qos.presentation_access_scope = rmw_fleetqox_cpp::PresentationAccessScope::kGroup;
  group_qos.presentation_coherent_access = true;
  group_qos.presentation_ordered_access = true;
  group_qos.presentation_group_id = kGroupId;

  rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  publisher_options.rmw_specific_publisher_payload = &group_qos;
  rmw_subscription_options_t subscription_options = rmw_get_default_subscription_options();
  subscription_options.rmw_specific_subscription_payload = &group_qos;

  rmw_subscription_t * sub_a = rmw_create_subscription(
    node, &type_support, kTopicA, &qos, &subscription_options);
  rmw_subscription_t * sub_b = rmw_create_subscription(
    node, &type_support, kTopicB, &qos, &subscription_options);
  rmw_publisher_t * pub_a = rmw_create_publisher(
    node, &type_support, kTopicA, &qos, &publisher_options);
  rmw_publisher_t * pub_b = rmw_create_publisher(
    node, &type_support, kTopicB, &qos, &publisher_options);
  const bool created = sub_a != nullptr && sub_b != nullptr && pub_a != nullptr &&
    pub_b != nullptr;

  // Give local-delivery graph matching a moment to settle, matching the
  // brief settle windows every other same-process probe in this codebase
  // uses before its first publish.
  std::this_thread::sleep_for(std::chrono::milliseconds(200));

  // Control: a publish outside any begin/end span must be delivered
  // immediately, unaffected by presentation_coherent_access=true.
  bool control_ok = created && publish_string(pub_a, "control-message");
  std::string control_received;
  control_ok = control_ok && wait_take_string(sub_a, 500, &control_received) &&
    control_received == "control-message";

  // Round 1: begin, publish on both topics, hold, end -- proves deferral
  // and atomic joint delivery.
  const bool begin1_ok = created &&
    rmw_fleetqox_cpp_begin_coherent_changes(pub_a) == RMW_RET_OK;
  const bool publish1_ok = begin1_ok &&
    publish_string(pub_a, "coherent-a-1") && publish_string(pub_b, "coherent-b-1");
  const bool held_before_flush_claim = publish1_ok && confirm_held(sub_a, sub_b, 500);
  const bool end1_ok = held_before_flush_claim &&
    rmw_fleetqox_cpp_end_coherent_changes(pub_a) == RMW_RET_OK;

  std::string round1_a;
  std::string round1_b;
  const bool atomic_joint_delivery_claim = end1_ok &&
    wait_take_string(sub_a, 1000, &round1_a) && round1_a == "coherent-a-1" &&
    wait_take_string(sub_b, 1000, &round1_b) && round1_b == "coherent-b-1";

  // Round 2: two publishes on topic A with a topic B publish interleaved
  // between them, all inside one coherent set -- proves ordered_access
  // preserves cross-instance publish order across the whole flush, not
  // just FIFO within one topic's own queue.
  const bool begin2_ok = atomic_joint_delivery_claim &&
    rmw_fleetqox_cpp_begin_coherent_changes(pub_a) == RMW_RET_OK;
  const bool publish2_ok = begin2_ok &&
    publish_string(pub_a, "coherent-a-2-first") &&
    publish_string(pub_b, "coherent-b-2") &&
    publish_string(pub_a, "coherent-a-2-second");
  const bool end2_ok = publish2_ok &&
    rmw_fleetqox_cpp_end_coherent_changes(pub_a) == RMW_RET_OK;

  std::string round2_a_first;
  std::string round2_a_second;
  std::string round2_b;
  const bool ordered_release_claim = end2_ok &&
    wait_take_string(sub_a, 1000, &round2_a_first) && round2_a_first == "coherent-a-2-first" &&
    wait_take_string(sub_a, 500, &round2_a_second) && round2_a_second == "coherent-a-2-second" &&
    wait_take_string(sub_b, 500, &round2_b) && round2_b == "coherent-b-2";

  const rmw_ret_t pub_a_ret = pub_a == nullptr ? RMW_RET_ERROR : rmw_destroy_publisher(node, pub_a);
  const rmw_ret_t pub_b_ret = pub_b == nullptr ? RMW_RET_ERROR : rmw_destroy_publisher(node, pub_b);
  const rmw_ret_t sub_a_ret = sub_a == nullptr ? RMW_RET_ERROR :
    rmw_destroy_subscription(node, sub_a);
  const rmw_ret_t sub_b_ret = sub_b == nullptr ? RMW_RET_ERROR :
    rmw_destroy_subscription(node, sub_b);
  const rmw_ret_t node_ret = node == nullptr ? RMW_RET_ERROR : rmw_destroy_node(node);
  const rmw_ret_t shutdown_ret = rmw_shutdown(&context);
  const rmw_ret_t context_ret = rmw_context_fini(&context);
  const rmw_ret_t options_ret = rmw_init_options_fini(&options);
  const bool teardown_ok = pub_a_ret == RMW_RET_OK && pub_b_ret == RMW_RET_OK &&
    sub_a_ret == RMW_RET_OK && sub_b_ret == RMW_RET_OK && node_ret == RMW_RET_OK &&
    shutdown_ret == RMW_RET_OK && context_ret == RMW_RET_OK && options_ret == RMW_RET_OK;

  const bool ok = created && control_ok && held_before_flush_claim &&
    atomic_joint_delivery_claim && ordered_release_claim && teardown_ok;
  std::cout << "{\"schema_version\":\"fleetrmw.presentation_probe.v1\","
            << "\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"created\":" << (created ? "true" : "false") << ","
            << "\"control_immediate_delivery_claim\":" << (control_ok ? "true" : "false") << ","
            << "\"held_before_flush_claim\":" << (held_before_flush_claim ? "true" : "false")
            << ","
            << "\"atomic_joint_delivery_claim\":"
            << (atomic_joint_delivery_claim ? "true" : "false") << ","
            << "\"ordered_release_claim\":" << (ordered_release_claim ? "true" : "false") << ","
            << "\"clean_teardown\":" << (teardown_ok ? "true" : "false") << "}" << std::endl;
  return ok ? 0 : 1;
}
