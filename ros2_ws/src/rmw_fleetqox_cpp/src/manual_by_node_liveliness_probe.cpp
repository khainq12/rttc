// Proves the distinguishing behavior of MANUAL_BY_NODE liveliness (upstream
// rmw/types.h value 2, deprecated in favor of MANUAL_BY_TOPIC but still
// accepted from applications that have not migrated): asserting liveliness
// on ONE publisher renews every OTHER MANUAL_BY_NODE publisher owned by the
// SAME node, matching the DDS spec's equivalent MANUAL_BY_PARTICIPANT kind.
// liveliness_default_lease_probe.cpp already proves the baseline single-
// publisher non-expiring-lease lifecycle for this kind; this probe is the
// dedicated coverage for the actual cross-publisher sharing behavior,
// including the node-scope boundary (assertions on one node must NOT keep a
// different node's MANUAL_BY_NODE publisher alive).
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

bool wait_take_event(
  rmw_context_t * context,
  rmw_event_t * event,
  rmw_liveliness_changed_status_t * status,
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

}  // namespace

int main()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK) {
    return 1;
  }
  options.instance_id = 829;
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init(&options, &context) != RMW_RET_OK) {
    const rmw_ret_t ret = rmw_init_options_fini(&options);
    (void)ret;
    return 1;
  }

  rmw_node_t * node_a = rmw_create_node(&context, "manual_by_node_probe_a", "/fleetqox");
  rmw_node_t * node_b = rmw_create_node(&context, "manual_by_node_probe_b", "/fleetqox");
  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "fleetrmw_manual_by_node_liveliness_type";
  const rmw_qos_profile_t qos = manual_by_node_qos(200);
  const rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const rmw_subscription_options_t subscription_options =
    rmw_get_default_subscription_options();

  // Same node (node_a): P1 asserts repeatedly, P2 never asserts. P2 must
  // stay alive throughout -- the assertion-sharing behavior under test.
  rmw_subscription_t * s1 = rmw_create_subscription(
    node_a, &type_support, "/fleetqox/manual_by_node/topic_a", &qos, &subscription_options);
  rmw_subscription_t * s2 = rmw_create_subscription(
    node_a, &type_support, "/fleetqox/manual_by_node/topic_b", &qos, &subscription_options);
  rmw_event_t s1_event = rmw_get_zero_initialized_event();
  rmw_event_t s2_event = rmw_get_zero_initialized_event();
  const bool s1_event_ok = s1 != nullptr &&
    rmw_subscription_event_init(&s1_event, s1, RMW_EVENT_LIVELINESS_CHANGED) == RMW_RET_OK;
  const bool s2_event_ok = s2 != nullptr &&
    rmw_subscription_event_init(&s2_event, s2, RMW_EVENT_LIVELINESS_CHANGED) == RMW_RET_OK;
  rmw_publisher_t * p1 = (s1_event_ok && s2_event_ok) ? rmw_create_publisher(
    node_a, &type_support, "/fleetqox/manual_by_node/topic_a", &qos, &publisher_options) :
    nullptr;
  rmw_publisher_t * p2 = p1 != nullptr ? rmw_create_publisher(
    node_a, &type_support, "/fleetqox/manual_by_node/topic_b", &qos, &publisher_options) :
    nullptr;

  rmw_liveliness_changed_status_t s1_connect{};
  rmw_liveliness_changed_status_t s2_connect{};
  const bool connected = p1 != nullptr && p2 != nullptr &&
    wait_take_event(&context, &s1_event, &s1_connect, 2000) &&
    s1_connect.alive_count == 1 && s1_connect.alive_count_change == 1 &&
    wait_take_event(&context, &s2_event, &s2_connect, 2000) &&
    s2_connect.alive_count == 1 && s2_connect.alive_count_change == 1;

  // P1 asserts every 60ms for 700ms (well past the 200ms lease); P2 never
  // asserts and never publishes.
  bool p1_assert_ok = connected;
  if (connected) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(700);
    while (std::chrono::steady_clock::now() < deadline) {
      p1_assert_ok = rmw_publisher_assert_liveliness(p1) == RMW_RET_OK && p1_assert_ok;
      std::this_thread::sleep_for(std::chrono::milliseconds(60));
    }
  }
  // Neither S1 nor S2 should have observed a not-alive transition: P1's
  // periodic asserts renew both P1 and P2 (same node), so P2 never expires
  // despite never asserting itself.
  const bool sharing_ok = connected && p1_assert_ok &&
    event_not_ready(&context, &s1_event, 0) && event_not_ready(&context, &s2_event, 0);

  // Now let BOTH go idle. After another 700ms (well past the 200ms lease)
  // both must expire normally -- sharing renews the lease, it does not
  // disable expiry.
  std::this_thread::sleep_for(std::chrono::milliseconds(700));
  rmw_liveliness_changed_status_t s1_expiry{};
  rmw_liveliness_changed_status_t s2_expiry{};
  const bool expiry_ok = sharing_ok &&
    wait_take_event(&context, &s1_event, &s1_expiry, 500) &&
    s1_expiry.not_alive_count == 1 && s1_expiry.alive_count == 0 &&
    wait_take_event(&context, &s2_event, &s2_expiry, 500) &&
    s2_expiry.not_alive_count == 1 && s2_expiry.alive_count == 0;

  // Node-scope boundary control: P3 lives on a SEPARATE node (node_b). P1
  // (node_a) asserting must NOT keep P3 alive -- sharing is node-scoped,
  // not global.
  rmw_subscription_t * s3 = rmw_create_subscription(
    node_b, &type_support, "/fleetqox/manual_by_node/topic_c", &qos, &subscription_options);
  rmw_event_t s3_event = rmw_get_zero_initialized_event();
  const bool s3_event_ok = s3 != nullptr &&
    rmw_subscription_event_init(&s3_event, s3, RMW_EVENT_LIVELINESS_CHANGED) == RMW_RET_OK;
  rmw_publisher_t * p3 = s3_event_ok ? rmw_create_publisher(
    node_b, &type_support, "/fleetqox/manual_by_node/topic_c", &qos, &publisher_options) :
    nullptr;
  rmw_liveliness_changed_status_t s3_connect{};
  const bool p3_connected = p3 != nullptr &&
    wait_take_event(&context, &s3_event, &s3_connect, 2000) &&
    s3_connect.alive_count == 1 && s3_connect.alive_count_change == 1;

  bool p1_second_assert_ok = p3_connected;
  if (p3_connected) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(700);
    while (std::chrono::steady_clock::now() < deadline) {
      p1_second_assert_ok = rmw_publisher_assert_liveliness(p1) == RMW_RET_OK &&
        p1_second_assert_ok;
      std::this_thread::sleep_for(std::chrono::milliseconds(60));
    }
  }
  rmw_liveliness_changed_status_t s3_expiry{};
  const bool node_scope_ok = p3_connected && p1_second_assert_ok &&
    wait_take_event(&context, &s3_event, &s3_expiry, 500) &&
    s3_expiry.not_alive_count == 1 && s3_expiry.alive_count == 0;

  const rmw_ret_t s3_event_fini = s3_event_ok ? rmw_event_fini(&s3_event) : RMW_RET_ERROR;
  const rmw_ret_t p3_ret = p3 == nullptr ? RMW_RET_ERROR : rmw_destroy_publisher(node_b, p3);
  const rmw_ret_t s3_ret = s3 == nullptr ? RMW_RET_ERROR : rmw_destroy_subscription(node_b, s3);
  const rmw_ret_t s1_event_fini = s1_event_ok ? rmw_event_fini(&s1_event) : RMW_RET_ERROR;
  const rmw_ret_t s2_event_fini = s2_event_ok ? rmw_event_fini(&s2_event) : RMW_RET_ERROR;
  const rmw_ret_t p1_ret = p1 == nullptr ? RMW_RET_ERROR : rmw_destroy_publisher(node_a, p1);
  const rmw_ret_t p2_ret = p2 == nullptr ? RMW_RET_ERROR : rmw_destroy_publisher(node_a, p2);
  const rmw_ret_t s1_ret = s1 == nullptr ? RMW_RET_ERROR : rmw_destroy_subscription(node_a, s1);
  const rmw_ret_t s2_ret = s2 == nullptr ? RMW_RET_ERROR : rmw_destroy_subscription(node_a, s2);
  const rmw_ret_t node_a_ret = node_a == nullptr ? RMW_RET_ERROR : rmw_destroy_node(node_a);
  const rmw_ret_t node_b_ret = node_b == nullptr ? RMW_RET_ERROR : rmw_destroy_node(node_b);
  const rmw_ret_t shutdown_ret = rmw_shutdown(&context);
  const rmw_ret_t context_ret = rmw_context_fini(&context);
  const rmw_ret_t options_ret = rmw_init_options_fini(&options);
  const bool teardown_ok = s3_event_fini == RMW_RET_OK && p3_ret == RMW_RET_OK &&
    s3_ret == RMW_RET_OK && s1_event_fini == RMW_RET_OK && s2_event_fini == RMW_RET_OK &&
    p1_ret == RMW_RET_OK && p2_ret == RMW_RET_OK && s1_ret == RMW_RET_OK &&
    s2_ret == RMW_RET_OK && node_a_ret == RMW_RET_OK && node_b_ret == RMW_RET_OK &&
    shutdown_ret == RMW_RET_OK && context_ret == RMW_RET_OK && options_ret == RMW_RET_OK;

  const bool ok = connected && sharing_ok && expiry_ok && node_scope_ok && teardown_ok;
  std::cout << "{\"schema_version\":\"fleetrmw.manual_by_node_liveliness_probe.v1\","
            << "\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"connected\":" << (connected ? "true" : "false") << ","
            << "\"manual_by_node_assertion_sharing_claim\":"
            << (sharing_ok ? "true" : "false") << ","
            << "\"manual_by_node_shared_lease_still_expires_claim\":"
            << (expiry_ok ? "true" : "false") << ","
            << "\"manual_by_node_sharing_scoped_to_node_claim\":"
            << (node_scope_ok ? "true" : "false") << ","
            << "\"clean_teardown\":" << (teardown_ok ? "true" : "false") << "}"
            << std::endl;
  return ok ? 0 : 1;
}
