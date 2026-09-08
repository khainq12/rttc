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
#include "rosidl_typesupport_interface/macros.h"
#include "std_msgs/msg/detail/string__struct.h"
#include "std_msgs/msg/string.hpp"

extern "C" std::uint64_t rmw_fleetqox_cpp_test_loan_fresh_allocations();
extern "C" std::uint64_t rmw_fleetqox_cpp_test_loan_pool_reuses();

namespace
{

bool take_loaned_string(
  rmw_subscription_t * subscription,
  const std::string & expected,
  bool * ok)
{
  void * loan = nullptr;
  bool taken = false;
  rmw_ret_t take_ret = RMW_RET_OK;
  for (int attempt = 0; attempt < 500 && !taken; ++attempt) {
    take_ret = rmw_take_loaned_message(subscription, &loan, &taken, nullptr);
    if (take_ret != RMW_RET_OK || taken) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  *ok = taken && loan != nullptr &&
    static_cast<std_msgs::msg::String *>(loan)->data == expected;
  const rmw_ret_t return_ret = loan == nullptr ? RMW_RET_ERROR :
    rmw_return_loaned_message_from_subscription(subscription, loan);
  return take_ret == RMW_RET_OK && *ok && return_ret == RMW_RET_OK;
}

}  // namespace

int main()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  rmw_context_t context = rmw_get_zero_initialized_context();
  bool initialized = rmw_init_options_init(&options, allocator) == RMW_RET_OK;
  if (initialized) {
    options.instance_id = 92;
    initialized = rmw_init(&options, &context) == RMW_RET_OK;
  }
  rmw_node_t * node = initialized ?
    rmw_create_node(&context, "fleetrmw_deep_preallocation_loaned_message_probe", "/fleetqox") :
    nullptr;
  const auto * type_support =
    ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(
    rosidl_typesupport_cpp, std_msgs, msg, String)();
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 4;
  rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  rmw_subscription_options_t subscription_options = rmw_get_default_subscription_options();
  rmw_publisher_t * publisher = node == nullptr ? nullptr : rmw_create_publisher(
    node, type_support, "/fleetqox/deep_preallocation_loaned", &qos, &publisher_options);
  rmw_subscription_t * subscription = node == nullptr ? nullptr : rmw_create_subscription(
    node, type_support, "/fleetqox/deep_preallocation_loaned", &qos, &subscription_options);
  const bool capabilities_ok = publisher != nullptr && subscription != nullptr &&
    publisher->can_loan_messages && subscription->can_loan_messages;

  constexpr int kOperationCount = 8;
  const std::uint64_t fresh_before = rmw_fleetqox_cpp_test_loan_fresh_allocations();
  const std::uint64_t reuse_before = rmw_fleetqox_cpp_test_loan_pool_reuses();

  bool all_cycles_ok = capabilities_ok;
  int completed_operations = 0;
  for (int index = 0; index < kOperationCount && all_cycles_ok; ++index) {
    std_msgs::msg::String message;
    message.data = "fleetrmw-deep-preallocation-loan-" + std::to_string(index);
    const rmw_ret_t publish_ret = rmw_publish(publisher, &message, nullptr);
    bool cycle_ok = false;
    all_cycles_ok = publish_ret == RMW_RET_OK &&
      take_loaned_string(subscription, message.data, &cycle_ok) && cycle_ok;
    if (all_cycles_ok) {
      ++completed_operations;
    }
  }

  const std::uint64_t fresh_after = rmw_fleetqox_cpp_test_loan_fresh_allocations();
  const std::uint64_t reuse_after = rmw_fleetqox_cpp_test_loan_pool_reuses();
  const std::uint64_t fresh_delta = fresh_after - fresh_before;
  const std::uint64_t reuse_delta = reuse_after - reuse_before;

  // Only the very first ever borrow_loan() call for this subscription
  // should allocate fresh; every subsequent borrow/release cycle -- both
  // the one that lands a successful take and any "not taken yet" polling
  // attempts before it, each of which still runs a full
  // borrow+release cycle inside rmw_take_loaned_message -- must reuse the
  // pooled buffer instead (see g_loan_pool in rmw_pubsub.cpp). There is at
  // least one such cycle per operation, so reuse_delta is a lower bound,
  // not an exact count.
  const bool loan_pool_reuse_ok =
    all_cycles_ok &&
    completed_operations == kOperationCount &&
    fresh_delta == 1 &&
    reuse_delta >= static_cast<std::uint64_t>(kOperationCount - 1);

  const bool ok = initialized && capabilities_ok && loan_pool_reuse_ok;

  std::cout << "{\"schema_version\":\"fleetrmw.deep_preallocation_loaned_message_probe.v1\","
            << "\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"capabilities_ok\":" << (capabilities_ok ? "true" : "false") << ","
            << "\"operation_count\":" << kOperationCount << ","
            << "\"completed_operations\":" << completed_operations << ","
            << "\"fresh_allocations_delta\":" << fresh_delta << ","
            << "\"pool_reuses_delta\":" << reuse_delta << ","
            << "\"loan_pool_reuse_ok\":" << (loan_pool_reuse_ok ? "true" : "false") << "}\n";

  if (subscription != nullptr) {
    const rmw_ret_t destroy_subscription_ret = rmw_destroy_subscription(node, subscription);
    (void)destroy_subscription_ret;
  }
  if (publisher != nullptr) {
    const rmw_ret_t destroy_publisher_ret = rmw_destroy_publisher(node, publisher);
    (void)destroy_publisher_ret;
  }
  if (node != nullptr) {
    const rmw_ret_t destroy_node_ret = rmw_destroy_node(node);
    (void)destroy_node_ret;
  }
  if (initialized) {
    const rmw_ret_t shutdown_ret = rmw_shutdown(&context);
    const rmw_ret_t context_fini_ret = rmw_context_fini(&context);
    (void)shutdown_ret;
    (void)context_fini_ret;
  }
  const rmw_ret_t options_fini_ret = rmw_init_options_fini(&options);
  (void)options_fini_ret;
  return ok ? 0 : 1;
}
