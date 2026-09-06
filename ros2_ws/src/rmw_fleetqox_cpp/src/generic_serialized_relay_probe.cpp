#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <dlfcn.h>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "rclcpp/generic_publisher.hpp"
#include "rclcpp/generic_subscription.hpp"
#include "rclcpp/rclcpp.hpp"
#include "rclcpp/serialized_message.hpp"

namespace
{

struct RelayRoute
{
  std::string source;
  std::string destination;
  std::shared_ptr<rclcpp::GenericPublisher> publisher;
  std::shared_ptr<rclcpp::GenericSubscription> subscription;
  std::uint64_t count{0};
  std::uint64_t serialized_bytes{0};
};

std::string json_escape(const std::string & value)
{
  std::string escaped;
  escaped.reserve(value.size());
  for (const char character : value) {
    switch (character) {
      case '\\':
        escaped += "\\\\";
        break;
      case '"':
        escaped += "\\\"";
        break;
      case '\n':
        escaped += "\\n";
        break;
      case '\r':
        escaped += "\\r";
        break;
      case '\t':
        escaped += "\\t";
        break;
      default:
        escaped += character;
        break;
    }
  }
  return escaped;
}

bool parse_nonnegative_int(const char * text, int * value)
{
  if (text == nullptr || value == nullptr) {
    return false;
  }
  char * end = nullptr;
  const long parsed = std::strtol(text, &end, 10);
  if (end == text || end == nullptr || *end != '\0' || parsed < 0 ||
    parsed > 2147483647L)
  {
    return false;
  }
  *value = static_cast<int>(parsed);
  return true;
}

bool all_downstream_ready(
  const std::vector<std::shared_ptr<RelayRoute>> & routes)
{
  return std::all_of(
    routes.begin(),
    routes.end(),
    [](const std::shared_ptr<RelayRoute> & route) {
      return route != nullptr && route->publisher != nullptr &&
             route->publisher->get_subscription_count() > 0;
    });
}

bool all_samples_relayed(
  const std::vector<std::shared_ptr<RelayRoute>> & routes,
  std::uint64_t samples)
{
  return std::all_of(
    routes.begin(),
    routes.end(),
    [samples](const std::shared_ptr<RelayRoute> & route) {
      return route != nullptr && route->count >= samples;
    });
}

std::uint64_t fleetqox_metric(const char * symbol_name, bool * available)
{
  const char * implementation = std::getenv("RMW_IMPLEMENTATION");
  if (implementation == nullptr ||
    std::string(implementation) != "rmw_fleetqox_cpp")
  {
    return 0;
  }
  static void * library = []() {
      void * handle = dlopen(
        "librmw_fleetqox_cpp.so",
        RTLD_LAZY | RTLD_NOLOAD);
      return handle != nullptr ? handle :
             dlopen("librmw_fleetqox_cpp.so", RTLD_LAZY);
    }();
  if (library == nullptr || symbol_name == nullptr) {
    return 0;
  }
  using MetricFunction = std::uint64_t (*)();
  auto function = reinterpret_cast<MetricFunction>(dlsym(library, symbol_name));
  if (function == nullptr) {
    return 0;
  }
  if (available != nullptr) {
    *available = true;
  }
  return function();
}

}  // namespace

int main(int argc, char ** argv)
{
  std::vector<std::pair<std::string, std::string>> mappings;
  int samples = 1;
  int timeout_ms = 25000;
  int linger_ms = 0;
  for (int index = 1; index < argc; ++index) {
    const std::string argument = argv[index];
    if (argument == "--mapping" && index + 1 < argc) {
      const std::string mapping = argv[++index];
      const std::size_t separator = mapping.find('=');
      if (separator == std::string::npos || separator == 0 ||
        separator + 1 >= mapping.size())
      {
        std::cerr << "invalid --mapping: " << mapping << std::endl;
        return 2;
      }
      mappings.emplace_back(
        mapping.substr(0, separator),
        mapping.substr(separator + 1));
    } else if (argument == "--samples" && index + 1 < argc) {
      if (!parse_nonnegative_int(argv[++index], &samples) || samples <= 0) {
        std::cerr << "invalid --samples" << std::endl;
        return 2;
      }
    } else if (argument == "--timeout-ms" && index + 1 < argc) {
      if (!parse_nonnegative_int(argv[++index], &timeout_ms) || timeout_ms <= 0) {
        std::cerr << "invalid --timeout-ms" << std::endl;
        return 2;
      }
    } else if (argument == "--linger-ms" && index + 1 < argc) {
      if (!parse_nonnegative_int(argv[++index], &linger_ms)) {
        std::cerr << "invalid --linger-ms" << std::endl;
        return 2;
      }
    } else {
      std::cerr << "unknown or incomplete argument: " << argument << std::endl;
      return 2;
    }
  }
  if (mappings.empty()) {
    std::cerr << "at least one --mapping is required" << std::endl;
    return 2;
  }

  rclcpp::init(argc, argv);
  auto node = std::make_shared<rclcpp::Node>(
    "fleetrmw_generic_serialized_relay");
  const rclcpp::QoS qos(
    rclcpp::KeepLast(
      static_cast<std::size_t>(std::max(10, samples * 2))));
  const rclcpp::QoS reliable_qos = rclcpp::QoS(qos).reliable();
  std::vector<std::shared_ptr<RelayRoute>> routes;
  routes.reserve(mappings.size());
  try {
    for (const auto & mapping : mappings) {
      auto route = std::make_shared<RelayRoute>();
      route->source = mapping.first;
      route->destination = mapping.second;
      route->publisher = node->create_generic_publisher(
        route->destination,
        "std_msgs/msg/String",
        reliable_qos);
      routes.push_back(route);
    }
    for (const auto & route : routes) {
      route->subscription = node->create_generic_subscription(
        route->source,
        "std_msgs/msg/String",
        reliable_qos,
        [route](std::shared_ptr<rclcpp::SerializedMessage> message) {
          if (route == nullptr || route->publisher == nullptr || message == nullptr) {
            return;
          }
          route->publisher->publish(*message);
          ++route->count;
          route->serialized_bytes += message->size();
        });
    }
  } catch (const std::exception & error) {
    std::cout << "{\"schema_version\":"
      "\"fleetrmw.generic_serialized_relay_probe.v2\","
      "\"status\":\"failed\",\"reason\":\"entity_creation\","
      "\"error\":\"" << json_escape(error.what()) << "\"}" << std::endl;
    node.reset();
    rclcpp::shutdown();
    return 1;
  }

  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(node);
  const auto discovery_deadline =
    std::chrono::steady_clock::now() + std::chrono::seconds(8);
  while (std::chrono::steady_clock::now() < discovery_deadline &&
    !all_downstream_ready(routes))
  {
    executor.spin_once(std::chrono::milliseconds(50));
  }
  const bool downstream_ready = all_downstream_ready(routes);
  const auto deadline =
    std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline &&
    !all_samples_relayed(routes, static_cast<std::uint64_t>(samples)))
  {
    executor.spin_some(std::chrono::milliseconds(100));
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  const bool samples_relayed =
    all_samples_relayed(routes, static_cast<std::uint64_t>(samples));
  const auto reliability_deadline =
    std::chrono::steady_clock::now() + std::chrono::milliseconds(linger_ms);
  const auto ack_wait_started = std::chrono::steady_clock::now();
  bool downstream_ack_wait_supported = true;
  bool downstream_ack_wait_complete = samples_relayed;
  if (samples_relayed) {
    for (const auto & route : routes) {
      const auto now = std::chrono::steady_clock::now();
      const auto remaining = now < reliability_deadline ?
        std::chrono::duration_cast<std::chrono::milliseconds>(
        reliability_deadline - now) :
        std::chrono::milliseconds(0);
      try {
        if (route == nullptr || route->publisher == nullptr ||
          !route->publisher->wait_for_all_acked(remaining))
        {
          downstream_ack_wait_complete = false;
          break;
        }
      } catch (const std::exception &) {
        downstream_ack_wait_supported = false;
        downstream_ack_wait_complete = false;
        break;
      }
    }
  }
  const auto ack_wait_elapsed_ms =
    std::chrono::duration_cast<std::chrono::milliseconds>(
    std::chrono::steady_clock::now() - ack_wait_started).count();
  while (std::chrono::steady_clock::now() < reliability_deadline) {
    executor.spin_some(std::chrono::milliseconds(50));
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }

  std::uint64_t relayed_count = 0;
  std::uint64_t serialized_bytes = 0;
  std::uint64_t min_source_count = routes.front()->count;
  for (const auto & route : routes) {
    relayed_count += route->count;
    serialized_bytes += route->serialized_bytes;
    min_source_count = std::min(min_source_count, route->count);
  }
  const bool downstream_reliability_complete =
    !downstream_ack_wait_supported || downstream_ack_wait_complete;
  const bool ok =
    downstream_ready && samples_relayed && downstream_reliability_complete;
  std::cout << "{\"schema_version\":"
    "\"fleetrmw.generic_serialized_relay_probe.v2\",";
  std::cout << "\"status\":\"" << (ok ? "ok" : "failed") << "\",";
  std::cout << "\"relay_scope\":\"rclcpp_generic_serialized_passthrough\",";
  std::cout << "\"message_type\":\"std_msgs/msg/String\",";
  std::cout << "\"generic_subscription\":true,";
  std::cout << "\"generic_publisher\":true,";
  std::cout << "\"application_deserialization\":false,";
  std::cout << "\"executor_drain_mode\":\"spin_some_bounded\",";
  std::cout << "\"downstream_ack_wait_supported\":" <<
    (downstream_ack_wait_supported ? "true" : "false") << ",";
  std::cout << "\"downstream_ack_wait_complete\":" <<
    (downstream_ack_wait_complete ? "true" : "false") << ",";
  std::cout << "\"downstream_ack_wait_elapsed_ms\":" <<
    ack_wait_elapsed_ms << ",";
  std::cout << "\"downstream_ready\":" <<
    (downstream_ready ? "true" : "false") << ",";
  std::cout << "\"relayed_count\":" << relayed_count << ",";
  std::cout << "\"expected_count\":" <<
    static_cast<std::uint64_t>(samples) * routes.size() << ",";
  std::cout << "\"serialized_bytes\":" << serialized_bytes << ",";
  std::cout << "\"min_source_count\":" << min_source_count << ",";
  std::cout << "\"mapping_count\":" << routes.size() << ",";
  bool transport_metrics_available = false;
  const std::uint64_t transport_data_frames_received = fleetqox_metric(
    "rmw_fleetqox_cpp_socket_data_frames_received",
    &transport_metrics_available);
  std::cout << "\"fleetqox_transport_metrics\":{";
  std::cout << "\"available\":" <<
    (transport_metrics_available ? "true" : "false") << ",";
  std::cout << "\"data_frames_received\":" << transport_data_frames_received << ",";
  std::cout << "\"fragment_nacks_sent\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_nacks_sent", nullptr) << ",";
  std::cout << "\"fragment_nacks_received\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_nacks_received", nullptr) << ",";
  std::cout << "\"fragments_selectively_retransmitted\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragments_selectively_retransmitted", nullptr) << ",";
  std::cout << "\"fragment_repair_requests_coalesced\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_requests_coalesced", nullptr) << ",";
  std::cout << "\"fragment_repair_cooldown_coalesced\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_cooldown_coalesced", nullptr) << ",";
  std::cout << "\"completed_fragment_duplicates_dropped\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_completed_fragment_duplicates_dropped", nullptr) << ",";
  std::cout << "\"fragment_duplicate_no_progress_drops\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_duplicate_no_progress_drops", nullptr) << ",";
  std::cout << "\"fragment_send_queue_rejections\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_send_queue_rejections", nullptr) << ",";
  std::cout << "\"fragment_send_failures\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_send_failures", nullptr) << ",";
  std::cout << "\"fragment_send_queue_high_water\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_send_queue_high_water", nullptr) << ",";
  std::cout << "\"fragment_repair_queue_high_water\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_queue_high_water", nullptr) << ",";
  std::cout << "\"fragment_repair_round_robin_rotations\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_round_robin_rotations", nullptr) << ",";
  std::cout << "\"fragment_repair_frame_switches\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_frame_switches", nullptr) << ",";
  std::cout << "\"fragment_repair_max_active_frames\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_max_active_frames", nullptr) << ",";
  std::cout << "\"fragment_repair_max_consecutive_same_frame_while_contended\":" <<
    fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_max_consecutive_same_frame_while_contended",
    nullptr) << ",";
  std::cout << "\"udp_datagram_size_high_water\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_udp_datagram_size_high_water", nullptr) << ",";
  std::cout << "\"fragment_effective_chunk_bytes_min\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_effective_chunk_bytes_min", nullptr) << ",";
  std::cout << "\"fragment_effective_chunk_bytes_max\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_effective_chunk_bytes_max", nullptr) << ",";
  std::cout << "\"fragment_chunk_budget_reductions\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_chunk_budget_reductions", nullptr) << ",";
  std::cout << "\"udp_datagram_budget_failures\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_udp_datagram_budget_failures", nullptr) << ",";
  std::cout << "\"udp_pmtu_discovery_events\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_udp_pmtu_discovery_events", nullptr) << ",";
  std::cout << "\"udp_pmtu_rejections\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_udp_pmtu_rejections", nullptr) << ",";
  std::cout << "\"udp_pmtu_discovered_min_bytes\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_udp_pmtu_discovered_min_bytes", nullptr) << ",";
  std::cout << "\"fragment_queue_admission_waits\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_queue_admission_waits", nullptr) << ",";
  std::cout << "\"fragment_queue_admission_timeouts\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_queue_admission_timeouts", nullptr) << ",";
  std::cout << "\"fragment_queue_admission_wait_ns\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_queue_admission_wait_ns", nullptr) << ",";
  std::cout << "\"fragment_repair_queue_deferrals\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_queue_deferrals", nullptr) << ",";
  std::cout << "\"fragment_repair_pressure_priority_promotions\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_pressure_priority_promotions",
    nullptr) << ",";
  std::cout << "\"fragment_completion_markers_sent\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_completion_markers_sent", nullptr) << ",";
  std::cout << "\"fragment_completion_markers_received\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_completion_markers_received", nullptr) << ",";
  std::cout << "\"fragment_completion_marker_orphans\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_completion_marker_orphans", nullptr) << ",";
  std::cout << "\"fragment_completion_marker_failures\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_completion_marker_failures", nullptr) << ",";
  std::cout << "\"fragment_repair_source_denials\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_source_denials", nullptr) << ",";
  std::cout << "\"fragment_repair_reader_budget_exhausted\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_repair_reader_budget_exhausted", nullptr) << ",";
  std::cout << "\"fragment_initial_round_robin_rotations\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_initial_round_robin_rotations", nullptr) << ",";
  std::cout << "\"fragment_initial_frame_switches\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_initial_frame_switches", nullptr) << ",";
  std::cout << "\"fragment_initial_max_consecutive_same_frame\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_initial_max_consecutive_same_frame", nullptr) << ",";
  std::cout << "\"fragment_initial_max_consecutive_same_frame_while_contended\":" <<
    fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_initial_max_consecutive_same_frame_while_contended",
    nullptr) << ",";
  std::cout << "\"fragment_initial_max_active_frames\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_initial_max_active_frames", nullptr) << ",";
  std::cout << "\"fragment_async_send_completions\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_async_send_completions", nullptr) << ",";
  std::cout << "\"fragment_initial_pending_timeout_suppressions\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_initial_pending_timeout_suppressions", nullptr) << ",";
  std::cout << "\"fragment_whole_fallback_grace_deferrals\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_whole_fallback_grace_deferrals", nullptr) << ",";
  std::cout << "\"fragment_nack_indexes_requested\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_nack_indexes_requested", nullptr) << ",";
  std::cout << "\"fragment_nack_index_budget_reductions\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_nack_index_budget_reductions", nullptr) << ",";
  std::cout << "\"fragment_nack_max_sweep_indexes_requested\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_nack_max_sweep_indexes_requested", nullptr) << ",";
  std::cout << "\"fragment_nack_sweep_budget_exhaustions\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_nack_sweep_budget_exhaustions", nullptr) << ",";
  std::cout << "\"fragment_progressive_nacks_sent\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_progressive_nacks_sent", nullptr) << ",";
  std::cout << "\"fragment_progress_grace_deferrals\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_progress_grace_deferrals", nullptr) << ",";
  std::cout << "\"fragment_active_assemblies\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_active_assemblies", nullptr) << ",";
  std::cout << "\"fragment_active_missing_indexes\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_active_missing_indexes", nullptr) << ",";
  std::cout << "\"fragment_nack_exhausted_assemblies\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_nack_exhausted_assemblies", nullptr) << ",";
  std::cout << "\"fragment_oldest_assembly_age_ms\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_oldest_assembly_age_ms", nullptr) << ",";
  std::cout << "\"fragment_history_request_exhausted\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_history_request_exhausted", nullptr) << ",";
  std::cout << "\"fragment_assembly_evictions\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_assembly_evictions", nullptr) << ",";
  std::cout << "\"fragment_assembly_oversize_drops\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_assembly_oversize_drops", nullptr) << ",";
  std::cout << "\"fragment_assembly_metadata_mismatch_drops\":" <<
    fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_assembly_metadata_mismatch_drops",
    nullptr) << ",";
  std::cout << "\"fragment_assembly_ttl_expirations\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_assembly_ttl_expirations", nullptr) << ",";
  std::cout << "\"fragment_assembly_ttl_expired_missing_indexes\":" <<
    fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_assembly_ttl_expired_missing_indexes",
    nullptr) << ",";
  std::cout << "\"fragment_observed_timeout_retransmissions_suppressed\":" <<
    fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_observed_timeout_retransmissions_suppressed",
    nullptr) << ",";
  std::cout << "\"fragment_whole_fallback_pacing_deferrals\":" <<
    fleetqox_metric(
    "rmw_fleetqox_cpp_socket_fragment_whole_fallback_pacing_deferrals",
    nullptr) << ",";
  std::cout << "\"nack_retransmissions\":" << fleetqox_metric(
    "rmw_fleetqox_cpp_socket_nack_retransmissions", nullptr) << "},";
  std::cout << "\"per_source_count\":{";
  for (std::size_t index = 0; index < routes.size(); ++index) {
    if (index > 0) {
      std::cout << ",";
    }
    std::cout << "\"" << json_escape(routes[index]->source) << "\":" <<
      routes[index]->count;
  }
  std::cout << "}}" << std::endl;

  executor.remove_node(node);
  routes.clear();
  node.reset();
  rclcpp::shutdown();
  return ok ? 0 : 1;
}
