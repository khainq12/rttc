#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <thread>

#include "rcutils/allocator.h"
#include "rcutils/strdup.h"
#include "rmw/init.h"
#include "rmw/init_options.h"
#include "rmw/get_network_flow_endpoints.h"
#include "rmw/publisher_options.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/serialized_message.h"
#include "rmw/subscription_options.h"
#include "rosidl_runtime_c/message_type_support_struct.h"

extern "C" std::uint64_t rmw_fleetqox_cpp_socket_frames_sent();
extern "C" std::uint64_t rmw_fleetqox_cpp_socket_frames_received();
extern "C" const char * rmw_fleetqox_cpp_socket_bound_endpoint();
extern "C" size_t rmw_fleetqox_cpp_socket_peer_count();
extern "C" const char * rmw_fleetqox_cpp_transport_mode();
extern "C" std::uint64_t rmw_fleetqox_cpp_shared_memory_frames_sent();
extern "C" std::uint64_t rmw_fleetqox_cpp_shared_memory_frames_received();
extern "C" std::uint64_t rmw_fleetqox_cpp_shared_memory_overwritten_frames();
extern "C" std::uint64_t rmw_fleetqox_cpp_duplicate_data_frames_deduped();
extern "C" std::int64_t rmw_fleetqox_cpp_last_take_source_timestamp_ns();
extern "C" std::int64_t rmw_fleetqox_cpp_last_take_timestamp_ns();
extern "C" bool rmw_fleetqox_cpp_udp_peer_auth_enabled();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_signed_frames();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_verified_frames();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_failures();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_chain_failures();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_signature_failures();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_identity_denied();
extern "C" bool rmw_fleetqox_cpp_udp_peer_auth_crl_enabled();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_revoked_certificate_drops();
extern "C" const char * rmw_fleetqox_cpp_udp_peer_auth_last_identity();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_crl_reload_successes();
extern "C" std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_crl_reload_failures();

namespace
{

struct ProbeConfig
{
  std::string mode{"subscriber"};
  std::string topic{"/fleetqox/interprocess_probe"};
  std::string payload{"fleetqox-interprocess-cdr"};
  int payload_size{0};
  std::string payload_fill{"x"};
  int timeout_ms{2000};
  int lifespan_ms{0};
  int deadline_ms{0};
  int pre_publish_ms{0};
  int post_take_ms{0};
  int payload_output_limit{256};
  bool expect_taken{true};
};

std::string json_escape(const std::string & value)
{
  std::ostringstream out;
  for (const char c : value) {
    if (c == '\\' || c == '"') {
      out << '\\' << c;
    } else if (c == '\n') {
      out << "\\n";
    } else {
      out << c;
    }
  }
  return out.str();
}

bool parse_bool(const std::string & value)
{
  return !(value == "0" || value == "false" || value == "False" || value == "no" ||
         value == "NO");
}

ProbeConfig parse_args(int argc, char ** argv)
{
  ProbeConfig config;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--mode" && i + 1 < argc) {
      config.mode = argv[++i];
    } else if (arg == "--topic" && i + 1 < argc) {
      config.topic = argv[++i];
    } else if (arg == "--payload" && i + 1 < argc) {
      config.payload = argv[++i];
    } else if (arg == "--payload-size" && i + 1 < argc) {
      config.payload_size = std::max(0, std::stoi(argv[++i]));
    } else if (arg == "--payload-fill" && i + 1 < argc) {
      config.payload_fill = argv[++i];
    } else if (arg == "--lifespan-ms" && i + 1 < argc) {
      config.lifespan_ms = std::stoi(argv[++i]);
    } else if (arg == "--deadline-ms" && i + 1 < argc) {
      config.deadline_ms = std::stoi(argv[++i]);
    } else if (arg == "--pre-publish-ms" && i + 1 < argc) {
      config.pre_publish_ms = std::max(0, std::stoi(argv[++i]));
    } else if (arg == "--post-take-ms" && i + 1 < argc) {
      config.post_take_ms = std::max(0, std::stoi(argv[++i]));
    } else if (arg == "--payload-output-limit" && i + 1 < argc) {
      config.payload_output_limit = std::max(0, std::stoi(argv[++i]));
    } else if (arg == "--expect-taken" && i + 1 < argc) {
      config.expect_taken = parse_bool(argv[++i]);
    } else if (arg == "--timeout-ms" && i + 1 < argc) {
      config.timeout_ms = std::stoi(argv[++i]);
    }
  }
  if (config.payload_size > 0) {
    const char fill = config.payload_fill.empty() ? 'x' : config.payload_fill[0];
    if (static_cast<size_t>(config.payload_size) <= config.payload.size()) {
      config.payload.resize(static_cast<size_t>(config.payload_size));
    } else {
      config.payload.append(static_cast<size_t>(config.payload_size) - config.payload.size(), fill);
    }
  }
  return config;
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

bool init_context(
  rcutils_allocator_t allocator,
  rmw_init_options_t * options,
  rmw_context_t * context)
{
  *options = rmw_get_zero_initialized_init_options();
  if (rmw_init_options_init(options, allocator) != RMW_RET_OK) {
    return false;
  }
  const char * enclave = std::getenv("FLEETQOX_RMW_PROBE_ENCLAVE");
  if (enclave != nullptr && enclave[0] != '\0') {
    if (options->enclave != nullptr && options->allocator.deallocate != nullptr) {
      options->allocator.deallocate(
        const_cast<char *>(options->enclave), options->allocator.state);
    }
    options->enclave = rcutils_strdup(enclave, options->allocator);
    if (options->enclave == nullptr) {
      const rmw_ret_t fini_ret = rmw_init_options_fini(options);
      (void)fini_ret;
      return false;
    }
  }
  options->instance_id = 45;
  *context = rmw_get_zero_initialized_context();
  if (rmw_init(options, context) != RMW_RET_OK) {
    const rmw_ret_t fini_ret = rmw_init_options_fini(options);
    (void)fini_ret;
    return false;
  }
  return true;
}

size_t publisher_network_flow_count(
  const rmw_publisher_t * publisher,
  rcutils_allocator_t * allocator)
{
  rmw_network_flow_endpoint_array_t endpoints =
    rmw_get_zero_initialized_network_flow_endpoint_array();
  const rmw_ret_t ret = publisher == nullptr ? RMW_RET_INVALID_ARGUMENT :
    rmw_publisher_get_network_flow_endpoints(publisher, allocator, &endpoints);
  const size_t count = ret == RMW_RET_OK ? endpoints.size : std::numeric_limits<size_t>::max();
  if (endpoints.allocator != nullptr) {
    rmw_network_flow_endpoint_array_fini(&endpoints);
  }
  return count;
}

size_t subscription_network_flow_count(
  const rmw_subscription_t * subscription,
  rcutils_allocator_t * allocator)
{
  rmw_network_flow_endpoint_array_t endpoints =
    rmw_get_zero_initialized_network_flow_endpoint_array();
  const rmw_ret_t ret = subscription == nullptr ? RMW_RET_INVALID_ARGUMENT :
    rmw_subscription_get_network_flow_endpoints(subscription, allocator, &endpoints);
  const size_t count = ret == RMW_RET_OK ? endpoints.size : std::numeric_limits<size_t>::max();
  if (endpoints.allocator != nullptr) {
    rmw_network_flow_endpoint_array_fini(&endpoints);
  }
  return count;
}

void print_json_result(
  const ProbeConfig & config,
  const std::string & status,
  const std::string & endpoint,
  size_t peer_count,
  std::uint64_t socket_frames_sent,
  std::uint64_t socket_frames_received,
  std::uint64_t shared_memory_frames_sent,
  std::uint64_t shared_memory_frames_received,
  size_t network_flow_endpoint_count,
  std::uint64_t duplicate_data_frames_deduped,
  bool taken,
  size_t bytes,
  const std::string & payload,
  double take_age_ms)
{
  const size_t output_limit = static_cast<size_t>(config.payload_output_limit);
  const bool payload_truncated = payload.size() > output_limit;
  const std::string output_payload = payload_truncated ? payload.substr(0, output_limit) : payload;
  std::cout << "{\"schema_version\":\"fleetrmw.rmw_interprocess_pubsub_endpoint.v1\",";
  std::cout << "\"status\":\"" << status << "\",";
  std::cout << "\"mode\":\"" << config.mode << "\",";
  std::cout << "\"topic\":\"" << json_escape(config.topic) << "\",";
  std::cout << "\"lifespan_ms\":" << config.lifespan_ms << ",";
  std::cout << "\"deadline_ms\":" << config.deadline_ms << ",";
  std::cout << "\"payload_size\":" << config.payload.size() << ",";
  std::cout << "\"expect_taken\":" << (config.expect_taken ? "true" : "false") << ",";
  std::cout << "\"endpoint\":\"" << json_escape(endpoint) << "\",";
  std::cout << "\"peer_count\":" << peer_count << ",";
  std::cout << "\"transport_mode\":\"" << json_escape(rmw_fleetqox_cpp_transport_mode()) << "\",";
  std::cout << "\"socket_frames_sent\":" << socket_frames_sent << ",";
  std::cout << "\"socket_frames_received\":" << socket_frames_received << ",";
  std::cout << "\"shared_memory_frames_sent\":" << shared_memory_frames_sent << ",";
  std::cout << "\"shared_memory_frames_received\":" << shared_memory_frames_received << ",";
  std::cout << "\"shared_memory_overwritten_frames\":"
            << rmw_fleetqox_cpp_shared_memory_overwritten_frames() << ",";
  std::cout << "\"network_flow_endpoint_count\":" << network_flow_endpoint_count << ",";
  std::cout << "\"duplicate_data_frames_deduped\":" << duplicate_data_frames_deduped << ",";
  std::cout << "\"udp_peer_auth_enabled\":" <<
    (rmw_fleetqox_cpp_udp_peer_auth_enabled() ? "true" : "false") << ",";
  std::cout << "\"udp_peer_auth_signed_frames\":" <<
    rmw_fleetqox_cpp_udp_peer_auth_signed_frames() << ",";
  std::cout << "\"udp_peer_auth_verified_frames\":" <<
    rmw_fleetqox_cpp_udp_peer_auth_verified_frames() << ",";
  std::cout << "\"udp_peer_auth_failures\":" <<
    rmw_fleetqox_cpp_udp_peer_auth_failures() << ",";
  std::cout << "\"udp_peer_auth_chain_failures\":" <<
    rmw_fleetqox_cpp_udp_peer_auth_chain_failures() << ",";
  std::cout << "\"udp_peer_auth_signature_failures\":" <<
    rmw_fleetqox_cpp_udp_peer_auth_signature_failures() << ",";
  std::cout << "\"udp_peer_auth_identity_denied\":" <<
    rmw_fleetqox_cpp_udp_peer_auth_identity_denied() << ",";
  std::cout << "\"udp_peer_auth_crl_enabled\":" <<
    (rmw_fleetqox_cpp_udp_peer_auth_crl_enabled() ? "true" : "false") << ",";
  std::cout << "\"udp_peer_auth_revoked_certificate_drops\":" <<
    rmw_fleetqox_cpp_udp_peer_auth_revoked_certificate_drops() << ",";
  std::cout << "\"udp_peer_auth_last_identity\":\"" << json_escape(
    rmw_fleetqox_cpp_udp_peer_auth_last_identity()) << "\",";
  std::cout << "\"udp_peer_auth_crl_reload_successes\":" <<
    rmw_fleetqox_cpp_udp_peer_auth_crl_reload_successes() << ",";
  std::cout << "\"udp_peer_auth_crl_reload_failures\":" <<
    rmw_fleetqox_cpp_udp_peer_auth_crl_reload_failures() << ",";
  std::cout << "\"taken\":" << (taken ? "true" : "false") << ",";
  std::cout << "\"bytes\":" << bytes << ",";
  std::cout << "\"take_age_ms\":" << take_age_ms << ",";
  std::cout << "\"payload_truncated\":" << (payload_truncated ? "true" : "false") << ",";
  std::cout << "\"payload\":\"" << json_escape(output_payload) << "\"}" << std::endl;
}

int run_publisher(const ProbeConfig & config)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options{};
  rmw_context_t context{};
  if (!init_context(allocator, &options, &context)) {
    std::cout << "{\"status\":\"init_failed\",\"mode\":\"publisher\"}" << std::endl;
    return 1;
  }

  rmw_node_t * node = rmw_create_node(&context, "fleetqox_interprocess_publisher", "/fleetqox");
  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "rmw_fleetqox_cpp_interprocess_probe";
  rmw_publisher_options_t publisher_options = rmw_get_default_publisher_options();
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  if (config.lifespan_ms > 0) {
    qos.lifespan.sec = static_cast<std::uint64_t>(config.lifespan_ms / 1000);
    qos.lifespan.nsec = static_cast<std::uint64_t>((config.lifespan_ms % 1000) * 1000000);
  }
  if (config.deadline_ms > 0) {
    qos.deadline.sec = static_cast<std::uint64_t>(config.deadline_ms / 1000);
    qos.deadline.nsec = static_cast<std::uint64_t>((config.deadline_ms % 1000) * 1000000);
  }
  rmw_publisher_t * publisher =
    node == nullptr ? nullptr :
    rmw_create_publisher(node, &type_support, config.topic.c_str(), &qos, &publisher_options);

  rmw_serialized_message_t outgoing = rmw_get_zero_initialized_serialized_message();
  const bool message_init_ok =
    rmw_serialized_message_init(&outgoing, config.payload.size(), &allocator) == RMW_RET_OK;
  if (message_init_ok) {
    std::memcpy(outgoing.buffer, config.payload.data(), config.payload.size());
    outgoing.buffer_length = config.payload.size();
  }

  const std::uint64_t sent_before = rmw_fleetqox_cpp_socket_frames_sent();
  const std::uint64_t shm_sent_before = rmw_fleetqox_cpp_shared_memory_frames_sent();
  if (config.pre_publish_ms > 0) {
    std::this_thread::sleep_for(std::chrono::milliseconds(config.pre_publish_ms));
  }
  const rmw_ret_t publish_ret = publisher != nullptr && message_init_ok ?
    rmw_publish_serialized_message(publisher, &outgoing, nullptr) : RMW_RET_ERROR;
  std::this_thread::sleep_for(std::chrono::milliseconds(20));
  const std::uint64_t sent_delta = rmw_fleetqox_cpp_socket_frames_sent() - sent_before;
  const std::string endpoint = rmw_fleetqox_cpp_socket_bound_endpoint();
  const size_t peer_count = rmw_fleetqox_cpp_socket_peer_count();

  print_json_result(
    config,
    publish_ret == RMW_RET_OK && sent_delta >= 1 ? "ok" : "failed",
    endpoint,
    peer_count,
    sent_delta,
    0,
    rmw_fleetqox_cpp_shared_memory_frames_sent() - shm_sent_before,
    0,
    publisher_network_flow_count(publisher, &allocator),
    0,
    false,
    outgoing.buffer_length,
    config.payload,
    0.0);

  if (message_init_ok) {
    const rmw_ret_t message_fini_ret = rmw_serialized_message_fini(&outgoing);
    (void)message_fini_ret;
  }
  if (publisher != nullptr) {
    const rmw_ret_t destroy_pub_ret = rmw_destroy_publisher(node, publisher);
    (void)destroy_pub_ret;
  }
  if (node != nullptr) {
    const rmw_ret_t destroy_node_ret = rmw_destroy_node(node);
    (void)destroy_node_ret;
  }
  cleanup_context(&context, &options);
  return publish_ret == RMW_RET_OK && sent_delta >= 1 ? 0 : 1;
}

int run_subscriber(const ProbeConfig & config)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_init_options_t options{};
  rmw_context_t context{};
  if (!init_context(allocator, &options, &context)) {
    std::cout << "{\"status\":\"init_failed\",\"mode\":\"subscriber\"}" << std::endl;
    return 1;
  }

  rmw_node_t * node = rmw_create_node(&context, "fleetqox_interprocess_subscriber", "/fleetqox");
  rosidl_message_type_support_t type_support{};
  type_support.typesupport_identifier = "rmw_fleetqox_cpp_interprocess_probe";
  rmw_subscription_options_t subscription_options = rmw_get_default_subscription_options();
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  if (config.lifespan_ms > 0) {
    qos.lifespan.sec = static_cast<std::uint64_t>(config.lifespan_ms / 1000);
    qos.lifespan.nsec = static_cast<std::uint64_t>((config.lifespan_ms % 1000) * 1000000);
  }
  if (config.deadline_ms > 0) {
    qos.deadline.sec = static_cast<std::uint64_t>(config.deadline_ms / 1000);
    qos.deadline.nsec = static_cast<std::uint64_t>((config.deadline_ms % 1000) * 1000000);
  }
  rmw_subscription_t * subscription =
    node == nullptr ? nullptr :
    rmw_create_subscription(node, &type_support, config.topic.c_str(), &qos, &subscription_options);

  rmw_serialized_message_t incoming = rmw_get_zero_initialized_serialized_message();
  const bool message_init_ok = rmw_serialized_message_init(&incoming, 1, &allocator) == RMW_RET_OK;
  const std::uint64_t received_before = rmw_fleetqox_cpp_socket_frames_received();
  const std::uint64_t shm_received_before = rmw_fleetqox_cpp_shared_memory_frames_received();
  const std::uint64_t duplicate_before = rmw_fleetqox_cpp_duplicate_data_frames_deduped();

  bool taken = false;
  rmw_ret_t take_ret = RMW_RET_OK;
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(config.timeout_ms);
  while (subscription != nullptr && message_init_ok && std::chrono::steady_clock::now() < deadline) {
    take_ret = rmw_take_serialized_message(subscription, &incoming, &taken, nullptr);
    if (take_ret != RMW_RET_OK || taken) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }

  std::string received_payload;
  if (taken && incoming.buffer != nullptr) {
    received_payload.assign(
      reinterpret_cast<const char *>(incoming.buffer),
      reinterpret_cast<const char *>(incoming.buffer + incoming.buffer_length));
  }
  if (taken && config.post_take_ms > 0) {
    std::this_thread::sleep_for(std::chrono::milliseconds(config.post_take_ms));
  }
  const std::uint64_t received_delta =
    rmw_fleetqox_cpp_socket_frames_received() - received_before;
  const std::string endpoint = rmw_fleetqox_cpp_socket_bound_endpoint();
  const size_t peer_count = rmw_fleetqox_cpp_socket_peer_count();
  const std::int64_t source_timestamp_ns =
    rmw_fleetqox_cpp_last_take_source_timestamp_ns();
  const std::int64_t take_timestamp_ns = rmw_fleetqox_cpp_last_take_timestamp_ns();
  const double take_age_ms = taken && take_timestamp_ns >= source_timestamp_ns ?
    static_cast<double>(take_timestamp_ns - source_timestamp_ns) / 1000000.0 : 0.0;
  const bool ok = take_ret == RMW_RET_OK &&
                  ((config.expect_taken && taken && received_payload == config.payload &&
                  received_delta >= 1) ||
                  (!config.expect_taken && !taken));

  print_json_result(
    config,
    ok ? "ok" : "failed",
    endpoint,
    peer_count,
    0,
    received_delta,
    0,
    rmw_fleetqox_cpp_shared_memory_frames_received() - shm_received_before,
    subscription_network_flow_count(subscription, &allocator),
    rmw_fleetqox_cpp_duplicate_data_frames_deduped() - duplicate_before,
    taken,
    incoming.buffer_length,
    received_payload,
    take_age_ms);

  if (message_init_ok) {
    const rmw_ret_t message_fini_ret = rmw_serialized_message_fini(&incoming);
    (void)message_fini_ret;
  }
  if (subscription != nullptr) {
    const rmw_ret_t destroy_sub_ret = rmw_destroy_subscription(node, subscription);
    (void)destroy_sub_ret;
  }
  if (node != nullptr) {
    const rmw_ret_t destroy_node_ret = rmw_destroy_node(node);
    (void)destroy_node_ret;
  }
  cleanup_context(&context, &options);
  return ok ? 0 : 1;
}

}  // namespace

int main(int argc, char ** argv)
{
  const ProbeConfig config = parse_args(argc, argv);
  if (config.mode == "publisher") {
    return run_publisher(config);
  }
  if (config.mode == "subscriber") {
    return run_subscriber(config);
  }
  std::cout << "{\"status\":\"invalid_mode\",\"mode\":\"" << json_escape(config.mode) << "\"}" << std::endl;
  return 1;
}
