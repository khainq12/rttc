#include <cstring>
#include <iostream>

#include "rcutils/allocator.h"
#include "rmw/get_topic_endpoint_info.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/publisher_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/subscription_options.h"
#include "rmw/topic_endpoint_info_array.h"
#include "rosidl_typesupport_interface/macros.h"
#include "std_msgs/msg/detail/string__type_support.hpp"
#include "std_msgs/msg/string.hpp"

namespace
{

bool hash_matches_expected(
  const rosidl_type_hash_t & hash, const rosidl_type_hash_t & expected)
{
  return hash.version == expected.version &&
    std::memcmp(hash.value, expected.value, ROSIDL_TYPE_HASH_SIZE) == 0;
}

}  // namespace

int main()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK ||
    rmw_init(&options, &context) != RMW_RET_OK)
  {
    std::cout << "{\"status\":\"context_init_failed\"}" << std::endl;
    return 1;
  }

  rmw_node_t * node = rmw_create_node(&context, "topic_type_hash_probe", "/fleetqox");
  const auto * type_support =
    ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(
    rosidl_typesupport_cpp, std_msgs, msg, String)();
  const rosidl_type_hash_t * expected_hash =
    type_support != nullptr && type_support->get_type_hash_func != nullptr ?
    type_support->get_type_hash_func(type_support) : nullptr;
  const char * topic_name = "/fleetqox/topic_type_hash_probe";
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  rmw_subscription_options_t subscription_options = rmw_get_default_subscription_options();
  rmw_publisher_t * publisher = node == nullptr ? nullptr : rmw_create_publisher(
    node, type_support, topic_name, &qos, &publisher_options);
  rmw_subscription_t * subscription = node == nullptr ? nullptr : rmw_create_subscription(
    node, type_support, topic_name, &qos, &subscription_options);

  rmw_topic_endpoint_info_array_t publishers_info =
    rmw_get_zero_initialized_topic_endpoint_info_array();
  rmw_topic_endpoint_info_array_t subscriptions_info =
    rmw_get_zero_initialized_topic_endpoint_info_array();
  const rmw_ret_t publishers_ret = publisher == nullptr ? RMW_RET_ERROR :
    rmw_get_publishers_info_by_topic(node, &allocator, topic_name, false, &publishers_info);
  const rmw_ret_t subscriptions_ret = subscription == nullptr ? RMW_RET_ERROR :
    rmw_get_subscriptions_info_by_topic(
    node, &allocator, topic_name, false, &subscriptions_info);

  const bool publisher_hash_ok = publishers_ret == RMW_RET_OK &&
    publishers_info.size == 1 &&
    expected_hash != nullptr &&
    hash_matches_expected(publishers_info.info_array[0].topic_type_hash, *expected_hash);
  const bool subscription_hash_ok = subscriptions_ret == RMW_RET_OK &&
    subscriptions_info.size == 1 &&
    expected_hash != nullptr &&
    hash_matches_expected(subscriptions_info.info_array[0].topic_type_hash, *expected_hash);
  const bool ok = expected_hash != nullptr &&
    expected_hash->version != ROSIDL_TYPE_HASH_VERSION_UNSET &&
    publisher_hash_ok && subscription_hash_ok;

  std::cout << "{\"schema_version\":\"fleetrmw.rmw_topic_type_hash_probe.v1\",";
  std::cout << "\"status\":\"" << (ok ? "ok" : "failed") << "\",";
  std::cout << "\"expected_hash_available\":" <<
    (expected_hash != nullptr ? "true" : "false") << ",";
  std::cout << "\"expected_hash_version\":" <<
    (expected_hash != nullptr ? static_cast<int>(expected_hash->version) : -1) << ",";
  std::cout << "\"publisher_hash_matches\":" << (publisher_hash_ok ? "true" : "false") << ",";
  std::cout << "\"subscription_hash_matches\":" <<
    (subscription_hash_ok ? "true" : "false") << "}" << std::endl;

  if (publishers_info.info_array != nullptr) {
    const rmw_ret_t ret = rmw_topic_endpoint_info_array_fini(&publishers_info, &allocator);
    (void)ret;
  }
  if (subscriptions_info.info_array != nullptr) {
    const rmw_ret_t ret = rmw_topic_endpoint_info_array_fini(&subscriptions_info, &allocator);
    (void)ret;
  }
  if (subscription != nullptr) {
    const rmw_ret_t ret = rmw_destroy_subscription(node, subscription);
    (void)ret;
  }
  if (publisher != nullptr) {
    const rmw_ret_t ret = rmw_destroy_publisher(node, publisher);
    (void)ret;
  }
  if (node != nullptr) {
    const rmw_ret_t ret = rmw_destroy_node(node);
    (void)ret;
  }
  const rmw_ret_t shutdown_ret = rmw_shutdown(&context);
  const rmw_ret_t context_fini_ret = rmw_context_fini(&context);
  const rmw_ret_t options_fini_ret = rmw_init_options_fini(&options);
  (void)shutdown_ret;
  (void)context_fini_ret;
  (void)options_fini_ret;
  return ok ? 0 : 1;
}
