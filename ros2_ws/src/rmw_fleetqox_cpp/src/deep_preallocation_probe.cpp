#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

#include "rcutils/allocator.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/publisher_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/serialized_message.h"
#include "rmw/subscription_options.h"
#include "rosidl_runtime_c/message_type_support_struct.h"

extern "C" std::uint64_t rmw_fleetqox_cpp_socket_data_frames_received();
extern "C" size_t rmw_fleetqox_cpp_test_publisher_frame_base64_scratch_capacity(
  const rmw_publisher_t * publisher);

namespace
{

bool init_serialized_message(
  rmw_serialized_message_t * message,
  const std::string & payload,
  rcutils_allocator_t * allocator)
{
  if (rmw_serialized_message_init(message, payload.size(), allocator) != RMW_RET_OK) {
    return false;
  }
  if (!payload.empty()) {
    std::memcpy(message->buffer, payload.data(), payload.size());
  }
  message->buffer_length = payload.size();
  return true;
}

std::string serialized_message_string(const rmw_serialized_message_t & message)
{
  if (message.buffer == nullptr || message.buffer_length == 0) {
    return "";
  }
  return std::string(
    reinterpret_cast<const char *>(message.buffer),
    reinterpret_cast<const char *>(message.buffer + message.buffer_length));
}

bool wait_for_received_frames(std::uint64_t baseline, std::uint64_t expected_delta)
{
  for (int attempt = 0; attempt < 200; ++attempt) {
    if (rmw_fleetqox_cpp_socket_data_frames_received() >= baseline + expected_delta) {
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  return false;
}

void cleanup_context(rmw_context_t * context, rmw_init_options_t * options)
{
  const rmw_ret_t shutdown_ret = rmw_shutdown(context);
  const rmw_ret_t context_fini_ret = rmw_context_fini(context);
  const rmw_ret_t options_fini_ret = rmw_init_options_fini(options);
  (void)shutdown_ret;
  (void)context_fini_ret;
  (void)options_fini_ret;
}

}  // namespace

int main()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  rmw_ret_t ret = rmw_init_options_init(&options, allocator);
  if (ret != RMW_RET_OK) {
    std::cout << "{\"status\":\"init_options_failed\",\"ret\":" << ret << "}" << std::endl;
    return 1;
  }
  options.instance_id = 56;

  rmw_context_t context = rmw_get_zero_initialized_context();
  ret = rmw_init(&options, &context);
  if (ret != RMW_RET_OK) {
    const rmw_ret_t fini_ret = rmw_init_options_fini(&options);
    (void)fini_ret;
    std::cout << "{\"status\":\"init_failed\",\"ret\":" << ret << "}" << std::endl;
    return 1;
  }

  rmw_node_t * node = rmw_create_node(&context, "fleetqox_deep_preallocation_probe", "/fleetqox");
  if (node == nullptr) {
    cleanup_context(&context, &options);
    std::cout << "{\"status\":\"create_node_failed\"}" << std::endl;
    return 1;
  }

  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "rmw_fleetqox_cpp_deep_preallocation_probe";
  rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  rmw_subscription_options_t subscription_options = rmw_get_default_subscription_options();
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
  qos.depth = 4;
  const char * topic = "/fleetqox/deep_preallocation_probe";
  rmw_publisher_t * publisher = rmw_create_publisher(
    node, &type_support, topic, &qos, &publisher_options);
  rmw_subscription_t * subscription = rmw_create_subscription(
    node, &type_support, topic, &qos, &subscription_options);

  // A same-size payload every publish: after the frame-encode base64
  // scratch buffer (FleetQoxPublisherData::frame_base64_scratch) warms up
  // on the first publish, every subsequent publish with this same payload
  // size must reuse that buffer's capacity rather than reallocate.
  const std::string payload(256, 'q');
  rmw_serialized_message_t outgoing = rmw_get_zero_initialized_serialized_message();
  rmw_serialized_message_t incoming = rmw_get_zero_initialized_serialized_message();
  const bool messages_initialized =
    init_serialized_message(&outgoing, payload, &allocator) &&
    rmw_serialized_message_init(&incoming, 1, &allocator) == RMW_RET_OK;

  constexpr std::uint64_t kOperationCount = 8;
  const size_t scratch_capacity_before_any_publish =
    rmw_fleetqox_cpp_test_publisher_frame_base64_scratch_capacity(publisher);
  const std::uint64_t frames_before = rmw_fleetqox_cpp_socket_data_frames_received();
  rmw_ret_t publish_ret = RMW_RET_OK;
  rmw_ret_t take_ret = RMW_RET_OK;
  bool receive_ready = true;
  bool taken = false;
  bool all_received_match = true;
  std::uint64_t completed_operations = 0;
  size_t scratch_capacity_after_first_publish = 0;
  size_t scratch_capacity_after_last_publish = 0;
  for (std::uint64_t index = 0;
    index < kOperationCount && publisher != nullptr && subscription != nullptr &&
    messages_initialized;
    ++index)
  {
    publish_ret = rmw_publish_serialized_message(publisher, &outgoing, nullptr);
    if (index == 0) {
      scratch_capacity_after_first_publish =
        rmw_fleetqox_cpp_test_publisher_frame_base64_scratch_capacity(publisher);
    }
    receive_ready = publish_ret == RMW_RET_OK &&
      wait_for_received_frames(frames_before, index + 1);
    taken = false;
    take_ret = receive_ready ?
      rmw_take_serialized_message(subscription, &incoming, &taken, nullptr) :
      RMW_RET_ERROR;
    const std::string received = serialized_message_string(incoming);
    if (publish_ret != RMW_RET_OK || !receive_ready || take_ret != RMW_RET_OK ||
      !taken || received != payload)
    {
      all_received_match = false;
      break;
    }
    ++completed_operations;
  }
  scratch_capacity_after_last_publish =
    rmw_fleetqox_cpp_test_publisher_frame_base64_scratch_capacity(publisher);

  const rmw_ret_t outgoing_fini_ret = rmw_serialized_message_fini(&outgoing);
  const rmw_ret_t incoming_fini_ret = rmw_serialized_message_fini(&incoming);
  const rmw_ret_t destroy_pub_ret = publisher == nullptr ?
    RMW_RET_ERROR : rmw_destroy_publisher(node, publisher);
  const rmw_ret_t destroy_sub_ret = subscription == nullptr ?
    RMW_RET_ERROR : rmw_destroy_subscription(node, subscription);
  const rmw_ret_t destroy_node_ret = rmw_destroy_node(node);
  cleanup_context(&context, &options);

  const bool publish_take_ok =
    publisher != nullptr &&
    subscription != nullptr &&
    messages_initialized &&
    completed_operations == kOperationCount &&
    all_received_match;
  const bool base64_scratch_reuse_ok =
    // Small-string optimization gives a default-constructed std::string a
    // small nonzero capacity (implementation-defined), so this only checks
    // that it was not already sized for the payload -- the real claim is
    // the growth-then-stable pattern checked by the next two conditions.
    scratch_capacity_before_any_publish < payload.size() &&
    scratch_capacity_after_first_publish >= payload.size() &&
    scratch_capacity_after_last_publish == scratch_capacity_after_first_publish;
  const bool cleanup_ok =
    outgoing_fini_ret == RMW_RET_OK &&
    incoming_fini_ret == RMW_RET_OK &&
    destroy_pub_ret == RMW_RET_OK &&
    destroy_sub_ret == RMW_RET_OK &&
    destroy_node_ret == RMW_RET_OK;
  const bool ok = publish_take_ok && base64_scratch_reuse_ok && cleanup_ok;

  std::cout << "{\"schema_version\":\"fleetrmw.deep_preallocation_probe.v1\",";
  std::cout << "\"status\":\"" << (ok ? "ok" : "failed") << "\",";
  std::cout << "\"topic\":\"" << topic << "\",";
  std::cout << "\"operation_count\":" << kOperationCount << ",";
  std::cout << "\"completed_operations\":" << completed_operations << ",";
  std::cout << "\"scratch_capacity_before_any_publish\":" <<
    scratch_capacity_before_any_publish << ",";
  std::cout << "\"scratch_capacity_after_first_publish\":" <<
    scratch_capacity_after_first_publish << ",";
  std::cout << "\"scratch_capacity_after_last_publish\":" <<
    scratch_capacity_after_last_publish << ",";
  std::cout << "\"publish_take_ok\":" << (publish_take_ok ? "true" : "false") << ",";
  std::cout << "\"base64_scratch_reuse_ok\":" <<
    (base64_scratch_reuse_ok ? "true" : "false") << ",";
  std::cout << "\"cleanup_ok\":" << (cleanup_ok ? "true" : "false") << "}" << std::endl;
  return ok ? 0 : 1;
}
