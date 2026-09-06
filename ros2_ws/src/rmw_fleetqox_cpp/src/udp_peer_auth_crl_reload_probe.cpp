#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>

#include "rcutils/allocator.h"
#include "rcutils/strdup.h"
#include "rmw/error_handling.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/publisher_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/serialized_message.h"
#include "rmw/subscription_options.h"

extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_crl_reload_successes();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_crl_reload_failures();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_revoked_certificate_drops();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_verified_frames();

namespace
{

bool publish_string(rmw_publisher_t * publisher, const std::string & text)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_serialized_message_t message = rmw_get_zero_initialized_serialized_message();
  if (rmw_serialized_message_init(&message, text.size(), &allocator) != RMW_RET_OK) {
    return false;
  }
  std::memcpy(message.buffer, text.data(), text.size());
  message.buffer_length = text.size();
  const rmw_ret_t ret = rmw_publish_serialized_message(publisher, &message, nullptr);
  (void)rmw_serialized_message_fini(&message);
  return ret == RMW_RET_OK;
}

bool take_with_retry(rmw_subscription_t * subscription, int timeout_ms)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_serialized_message_t message = rmw_get_zero_initialized_serialized_message();
  if (rmw_serialized_message_init(&message, 0, &allocator) != RMW_RET_OK) {
    return false;
  }
  const auto deadline =
    std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  bool taken = false;
  while (std::chrono::steady_clock::now() < deadline) {
    if (rmw_take_serialized_message(subscription, &message, &taken, nullptr) == RMW_RET_OK &&
      taken)
    {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  (void)rmw_serialized_message_fini(&message);
  return taken;
}

// Waits for the orchestrating script to signal it has rewritten the CRL
// file on disk to revoke this process's own certificate, so the publish
// below happens strictly after that mutation rather than racing it.
bool wait_for_marker_file(const std::string & path, int timeout_ms)
{
  if (path.empty()) {
    return true;
  }
  const auto deadline =
    std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    std::ifstream probe(path);
    if (probe.good()) {
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  return false;
}

void write_marker_file(const std::string & path)
{
  if (path.empty()) {
    return;
  }
  std::ofstream output(path);
  output << "ready" << std::endl;
}

}  // namespace

int main()
{
  const char * ready_marker_env = std::getenv("FLEETQOX_PROBE_READY_FOR_REVOKE_FILE");
  const char * revoke_marker_env = std::getenv("FLEETQOX_PROBE_REVOKE_DONE_FILE");
  const std::string ready_marker = ready_marker_env != nullptr ? ready_marker_env : "";
  const std::string revoke_marker = revoke_marker_env != nullptr ? revoke_marker_env : "";

  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options = rmw_get_zero_initialized_init_options();
  rmw_context_t context = rmw_get_zero_initialized_context();
  bool options_ready = rmw_init_options_init(&options, allocator) == RMW_RET_OK;
  const char * enclave = std::getenv("FLEETQOX_RMW_PROBE_ENCLAVE");
  if (options_ready && enclave != nullptr && enclave[0] != '\0') {
    options.enclave = rcutils_strdup(enclave, options.allocator);
    options_ready = options.enclave != nullptr;
  }
  if (!options_ready || rmw_init(&options, &context) != RMW_RET_OK) {
    const rcutils_error_state_t * error_state = rcutils_get_error_state();
    std::cout << "{\"status\":\"context_init_failed\",\"error\":\"" <<
      (error_state != nullptr && error_state->message != nullptr ?
      error_state->message : "unknown") << "\"}" << std::endl;
    return 1;
  }

  rmw_node_t * node = rmw_create_node(&context, "udp_peer_auth_crl_reload_probe", "/fleetqox");
  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "rmw_fleetqox_cpp_udp_peer_auth_crl_reload_probe";
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  rmw_subscription_options_t subscription_options = rmw_get_default_subscription_options();
  const char * topic_name = "/fleetqox/udp_peer_auth_crl_reload_probe";
  rmw_publisher_t * publisher = node == nullptr ? nullptr : rmw_create_publisher(
    node, &type_support, topic_name, &qos, &publisher_options);
  rmw_subscription_t * subscription = node == nullptr ? nullptr : rmw_create_subscription(
    node, &type_support, topic_name, &qos, &subscription_options);
  if (publisher == nullptr || subscription == nullptr) {
    std::cout << "{\"status\":\"pubsub_create_failed\"}" << std::endl;
    return 1;
  }

  const bool before_publish_ok = publish_string(publisher, "before-revoke");
  const bool before_taken = before_publish_ok && take_with_retry(subscription, 3000);

  write_marker_file(ready_marker);
  const bool marker_observed = wait_for_marker_file(revoke_marker, 8000);

  const bool after_publish_call_ok = publish_string(publisher, "after-revoke");
  const bool after_taken = after_publish_call_ok && take_with_retry(subscription, 1500);

  const std::uint64_t reload_successes = rmw_fleetqox_cpp_udp_peer_auth_crl_reload_successes();
  const std::uint64_t reload_failures = rmw_fleetqox_cpp_udp_peer_auth_crl_reload_failures();
  const std::uint64_t revoked_drops = rmw_fleetqox_cpp_udp_peer_auth_revoked_certificate_drops();

  // Before revocation: the message must round-trip. After the CRL is
  // rewritten to revoke this exact identity's own certificate, the
  // self-authenticated publish still leaves this process (rmw_publish
  // succeeding just means the local send/attach step worked), but the
  // receiving side's own verification of that same attached certificate
  // must now reject it -- so the take must NOT observe the new payload,
  // and the live-reload path must show at least one success and zero
  // failures reloading the rewritten CRL.
  const bool ok = before_taken && marker_observed &&
    !after_taken && reload_successes >= 1 && reload_failures == 0 && revoked_drops >= 1;

  std::cout << "{\"schema_version\":\"fleetrmw.rmw_udp_peer_auth_crl_reload_probe.v1\",";
  std::cout << "\"status\":\"" << (ok ? "ok" : "failed") << "\",";
  std::cout << "\"before_taken\":" << (before_taken ? "true" : "false") << ",";
  std::cout << "\"marker_observed\":" << (marker_observed ? "true" : "false") << ",";
  std::cout << "\"after_taken\":" << (after_taken ? "true" : "false") << ",";
  std::cout << "\"udp_peer_auth_crl_reload_successes\":" << reload_successes << ",";
  std::cout << "\"udp_peer_auth_crl_reload_failures\":" << reload_failures << ",";
  std::cout << "\"udp_peer_auth_revoked_certificate_drops\":" << revoked_drops << "}" <<
    std::endl;

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
