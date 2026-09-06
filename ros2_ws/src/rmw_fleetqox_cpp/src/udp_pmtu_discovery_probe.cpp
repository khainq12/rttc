#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
#include <chrono>

#include "rcutils/allocator.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/publisher_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/serialized_message.h"

extern "C" std::uint64_t rmw_fleetqox_cpp_socket_udp_pmtu_discovery_events();
extern "C" std::uint64_t rmw_fleetqox_cpp_socket_udp_pmtu_rejections();
extern "C" std::uint64_t rmw_fleetqox_cpp_socket_udp_pmtu_discovered_min_bytes();

namespace
{

bool publish_payload_of_size(rmw_publisher_t * publisher, size_t size)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_serialized_message_t message = rmw_get_zero_initialized_serialized_message();
  if (rmw_serialized_message_init(&message, size, &allocator) != RMW_RET_OK) {
    return false;
  }
  std::memset(message.buffer, 'x', size);
  message.buffer_length = size;
  const rmw_ret_t ret = rmw_publish_serialized_message(publisher, &message, nullptr);
  (void)rmw_serialized_message_fini(&message);
  return ret == RMW_RET_OK;
}

}  // namespace

int main()
{
  // Bind and peer both point at the same loopback endpoint so a published
  // sample takes a real round trip through the kernel UDP/IP stack over
  // `lo`, instead of the in-process shortcut used when no peer is
  // configured. The orchestrating script lowers `lo`'s MTU before this
  // process starts, so a large-enough payload should get rejected by the
  // new automatic path-MTU discovery instead of being sent blind.
  setenv("FLEETQOX_RMW_BIND", "127.0.0.1:58123", 1);
  setenv("FLEETQOX_RMW_PEERS", "127.0.0.1:58123", 1);

  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  rmw_context_t context = rmw_get_zero_initialized_context();
  if (rmw_init_options_init(&options, allocator) != RMW_RET_OK ||
    rmw_init(&options, &context) != RMW_RET_OK)
  {
    std::cout << "{\"status\":\"context_init_failed\"}" << std::endl;
    return 1;
  }

  rmw_node_t * node = rmw_create_node(&context, "udp_pmtu_discovery_probe", "/fleetqox");
  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "rmw_fleetqox_cpp_udp_pmtu_discovery_probe";
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  const char * topic_name = "/fleetqox/udp_pmtu_discovery_probe";
  rmw_publisher_t * publisher = node == nullptr ? nullptr : rmw_create_publisher(
    node, &type_support, topic_name, &qos, &publisher_options);
  if (publisher == nullptr) {
    std::cout << "{\"status\":\"publisher_create_failed\"}" << std::endl;
    return 1;
  }

  // Sized to comfortably exceed the effective budget of a 1000-byte MTU
  // (1000 - 20 IPv4 - 8 UDP = 972 usable bytes) while staying far below the
  // existing large-sample fragmentation threshold, so this goes out as one
  // frame through send_datagram_to_targets() rather than being pre-split.
  const bool oversized_rejected = !publish_payload_of_size(publisher, 4000);
  std::this_thread::sleep_for(std::chrono::milliseconds(50));

  const std::uint64_t discovery_events = rmw_fleetqox_cpp_socket_udp_pmtu_discovery_events();
  const std::uint64_t discovered_min_bytes =
    rmw_fleetqox_cpp_socket_udp_pmtu_discovered_min_bytes();

  // Now that a path MTU has been learned, a small payload well under the
  // discovered budget must still succeed -- discovery should shrink the
  // effective budget, not block the socket outright.
  const bool small_payload_succeeded = publish_payload_of_size(publisher, 64);

  const bool ok = oversized_rejected &&
    discovery_events >= 1 &&
    discovered_min_bytes > 0 && discovered_min_bytes < 1000 &&
    small_payload_succeeded;

  std::cout << "{\"schema_version\":\"fleetrmw.rmw_udp_pmtu_discovery_probe.v1\",";
  std::cout << "\"status\":\"" << (ok ? "ok" : "failed") << "\",";
  std::cout << "\"oversized_payload_rejected\":" <<
    (oversized_rejected ? "true" : "false") << ",";
  std::cout << "\"udp_pmtu_discovery_events\":" << discovery_events << ",";
  std::cout << "\"udp_pmtu_rejections\":" <<
    rmw_fleetqox_cpp_socket_udp_pmtu_rejections() << ",";
  std::cout << "\"udp_pmtu_discovered_min_bytes\":" << discovered_min_bytes << ",";
  std::cout << "\"small_payload_after_discovery_succeeded\":" <<
    (small_payload_succeeded ? "true" : "false") << "}" << std::endl;

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
