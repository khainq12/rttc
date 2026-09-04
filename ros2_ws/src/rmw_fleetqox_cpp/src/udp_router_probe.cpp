#include <array>
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include "rmw_fleetqox_cpp/data_frame.hpp"

namespace
{

constexpr size_t kMaxUdpPayloadBytes = 65507;
constexpr const char * kFragmentPrefix = "FLEETQOX_FRAGMENT_V1|";
constexpr const char * kRepairFragmentPrefix =
  "FLEETQOX_REPAIR_FRAGMENT_V1|";
constexpr const char * kRepairFragmentNackPrefix =
  "FLEETQOX_REPAIR_FRAGMENT_NACK_V1|";

struct RouterConfig
{
  std::string bind{"127.0.0.1:48200"};
  std::string peers;
  std::string graph_peers;
  int expected_frames{1};
  int expected_service_frames{0};
  int expected_action_frames{0};
  int expected_ack_nack_frames{0};
  int expected_ack_nack_forwarded{-1};
  int expected_route_advertisements{0};
  int expected_graph_advertisements{0};
  int expected_qos_drops{0};
  std::vector<std::pair<std::string, std::uint64_t>> expected_forwarded_topic_source_sequences;
  int forward_delay_ms{0};
  int scheduler_window_ms{0};
  int scheduler_expected_frames{0};
  int scheduler_urgent_deadline_ms{0};
  std::string scheduler_admission_policy{"always"};
  double scheduler_admission_min_service_ratio{0.0};
  double scheduler_admission_exit_service_ratio{0.0};
  double scheduler_admission_ewma_alpha{0.5};
  int scheduler_admission_min_epoch_frames{1};
  std::string scheduler_topic_prefix;
  int post_satisfaction_ms{0};
  std::vector<std::uint64_t> drop_source_sequences;
  std::string drop_topic_prefix;
  int timeout_ms{3000};
  std::string path_id{"router_path"};
  std::string telemetry_file;
  double telemetry_latency_ms{0.0};
  double telemetry_jitter_ms{0.0};
  double telemetry_loss{-1.0};
  double telemetry_nack_rate{-1.0};
  double telemetry_deadline_miss_ratio{-1.0};
  int telemetry_capacity_bytes{0};
};

struct TopicRoute
{
  std::string topic;
  sockaddr_in address{};
  std::chrono::steady_clock::time_point expires_at{};
  std::uint64_t domain_id{0};
};

struct ServiceRoute
{
  std::string role;
  std::string service_name;
  std::string endpoint_id;
  sockaddr_in address{};
  std::chrono::steady_clock::time_point expires_at{};
  std::uint64_t domain_id{0};
};

struct ActionRoute
{
  std::string role;
  std::string action_name;
  std::string endpoint_id;
  sockaddr_in address{};
  std::chrono::steady_clock::time_point expires_at{};
  std::uint64_t domain_id{0};
};

struct PublisherRoute
{
  std::string publisher_id;
  std::string topic;
  sockaddr_in address{};
  std::chrono::steady_clock::time_point expires_at{};
  std::uint64_t domain_id{0};
};

struct TopicQosLease
{
  std::string endpoint_id;
  std::string topic;
  rmw_fleetqox_cpp::GraphQosProfile qos;
  std::chrono::steady_clock::time_point expires_at{};
  std::uint64_t domain_id{0};
};

struct QueuedDataFrame
{
  std::string encoded_frame;
  rmw_fleetqox_cpp::DataFrame frame;
  sockaddr_in source_address{};
  std::chrono::steady_clock::time_point enqueued_at{};
  std::int64_t absolute_deadline_ns = 0;
  std::uint64_t order = 0;
  bool test_repair = false;
};

struct RobotSchedulerStats
{
  std::string robot_id;
  int forwarded = 0;
  int deadline_misses = 0;
};

struct SchedulerDeadlineMiss
{
  std::string topic;
  std::string robot_id;
  std::uint64_t source_sequence_number = 0;
  bool test_repair = false;
  double lateness_ms = 0.0;
};

struct SchedulerAdmissionState
{
  bool holdback_enabled = false;
  bool ewma_initialized = false;
  double service_ratio_ewma = 0.0;
  double service_ratio_max = 0.0;
  int samples = 0;
  int frames_since_switch = 0;
  int switches = 0;
  int holdback_decisions = 0;
  int bypass_decisions = 0;
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

bool parse_ipv4_endpoint(const std::string & endpoint, sockaddr_in * address)
{
  if (address == nullptr) {
    return false;
  }
  const auto separator = endpoint.rfind(':');
  if (separator == std::string::npos || separator == 0 || separator + 1 >= endpoint.size()) {
    return false;
  }

  const std::string host = endpoint.substr(0, separator);
  const std::string port_text = endpoint.substr(separator + 1);
  char * port_end = nullptr;
  errno = 0;
  const long port = std::strtol(port_text.c_str(), &port_end, 10);
  if (errno != 0 || port_end == port_text.c_str() || *port_end != '\0' || port < 0 || port > 65535) {
    return false;
  }

  sockaddr_in parsed{};
  parsed.sin_family = AF_INET;
  parsed.sin_port = htons(static_cast<std::uint16_t>(port));
  if (::inet_pton(AF_INET, host.c_str(), &parsed.sin_addr) != 1) {
    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    addrinfo * result = nullptr;
    if (::getaddrinfo(host.c_str(), nullptr, &hints, &result) != 0 || result == nullptr) {
      return false;
    }
    parsed.sin_addr = reinterpret_cast<sockaddr_in *>(result->ai_addr)->sin_addr;
    ::freeaddrinfo(result);
  }
  *address = parsed;
  return true;
}

bool endpoints_match(const sockaddr_in & left, const sockaddr_in & right)
{
  return left.sin_family == right.sin_family &&
         left.sin_addr.s_addr == right.sin_addr.s_addr &&
         left.sin_port == right.sin_port;
}

bool is_fragment_datagram(const std::string & payload)
{
  return payload.rfind(kFragmentPrefix, 0) == 0 ||
         payload.rfind(kRepairFragmentPrefix, 0) == 0 ||
         payload.rfind(kRepairFragmentNackPrefix, 0) == 0;
}

// The sender embeds "^^<domain_id>^^<topic>" in the fragment_id field (the
// text between the fragment prefix and the first '|') whenever it knows the
// fragment carries a DataFrame -- see rmw_pubsub.cpp's fragment_id
// construction. A relay router is a separate process with no reassembly
// state of its own, so without this hint it can only broadcast every
// fragment to every known peer (forward_passthrough_datagram), which
// multiplies traffic by the fan-out size. Parsing it out lets fragments be
// routed the same precise way as ordinary data frames.
bool try_parse_fragment_route(
  const std::string & payload,
  std::uint64_t * domain_id,
  std::string * topic)
{
  std::string prefix;
  if (payload.rfind(kFragmentPrefix, 0) == 0) {
    prefix = kFragmentPrefix;
  } else if (payload.rfind(kRepairFragmentPrefix, 0) == 0) {
    prefix = kRepairFragmentPrefix;
  } else if (payload.rfind(kRepairFragmentNackPrefix, 0) == 0) {
    prefix = kRepairFragmentNackPrefix;
  } else {
    return false;
  }
  const size_t id_end = payload.find('|', prefix.size());
  if (id_end == std::string::npos) {
    return false;
  }
  const std::string fragment_id = payload.substr(prefix.size(), id_end - prefix.size());
  const size_t marker1 = fragment_id.find("^^");
  if (marker1 == std::string::npos) {
    return false;
  }
  const size_t domain_start = marker1 + 2;
  const size_t marker2 = fragment_id.find("^^", domain_start);
  if (marker2 == std::string::npos) {
    return false;
  }
  const std::string domain_text = fragment_id.substr(domain_start, marker2 - domain_start);
  if (domain_text.empty() ||
    !std::all_of(domain_text.begin(), domain_text.end(), [](unsigned char c) {
        return c >= '0' && c <= '9';
      }))
  {
    return false;
  }
  const std::string topic_text = fragment_id.substr(marker2 + 2);
  if (topic_text.empty()) {
    return false;
  }
  if (domain_id != nullptr) {
    *domain_id = std::strtoull(domain_text.c_str(), nullptr, 10);
  }
  if (topic != nullptr) {
    *topic = topic_text;
  }
  return true;
}

bool parse_peer_endpoints(const std::string & peers, std::vector<sockaddr_in> * peer_addresses)
{
  if (peer_addresses == nullptr) {
    return false;
  }
  if (peers.empty()) {
    return true;
  }

  size_t start = 0;
  while (start < peers.size()) {
    const size_t comma = peers.find(',', start);
    const std::string endpoint = peers.substr(
      start,
      comma == std::string::npos ? std::string::npos : comma - start);
    sockaddr_in parsed{};
    if (!parse_ipv4_endpoint(endpoint, &parsed)) {
      return false;
    }
    peer_addresses->push_back(parsed);
    if (comma == std::string::npos) {
      break;
    }
    start = comma + 1;
  }
  return true;
}

size_t endpoint_count(const std::string & endpoints)
{
  return endpoints.empty() ? 0 : static_cast<size_t>(std::count(endpoints.begin(), endpoints.end(), ',') + 1);
}

void append_unique_peer(std::vector<sockaddr_in> * peers, const sockaddr_in & candidate)
{
  if (peers == nullptr) {
    return;
  }
  const auto already_known = std::any_of(
    peers->begin(),
    peers->end(),
    [&](const sockaddr_in & peer) {
      return endpoints_match(peer, candidate);
    });
  if (!already_known) {
    peers->push_back(candidate);
  }
}

std::chrono::milliseconds lease_duration(std::uint64_t lease_ms)
{
  return std::chrono::milliseconds(lease_ms == 0 ? 5000 : lease_ms);
}

std::vector<std::uint64_t> parse_sequence_list(const std::string & text)
{
  std::vector<std::uint64_t> sequences;
  size_t start = 0;
  while (start < text.size()) {
    const size_t comma = text.find(',', start);
    const std::string item = text.substr(
      start,
      comma == std::string::npos ? std::string::npos : comma - start);
    if (!item.empty()) {
      char * end = nullptr;
      errno = 0;
      const auto value = std::strtoull(item.c_str(), &end, 10);
      if (errno == 0 && end != item.c_str() && *end == '\0') {
        sequences.push_back(static_cast<std::uint64_t>(value));
      }
    }
    if (comma == std::string::npos) {
      break;
    }
    start = comma + 1;
  }
  return sequences;
}

std::vector<std::pair<std::string, std::uint64_t>> parse_topic_sequence_expectations(
  const std::string & text)
{
  std::vector<std::pair<std::string, std::uint64_t>> expectations;
  size_t start = 0;
  while (start < text.size()) {
    const size_t semicolon = text.find(';', start);
    const std::string item = text.substr(
      start,
      semicolon == std::string::npos ? std::string::npos : semicolon - start);
    const size_t separator = item.rfind('=');
    if (separator != std::string::npos && separator > 0 && separator + 1 < item.size()) {
      const std::string sequence_text = item.substr(separator + 1);
      char * end = nullptr;
      errno = 0;
      const auto value = std::strtoull(sequence_text.c_str(), &end, 10);
      if (errno == 0 && end != sequence_text.c_str() && *end == '\0') {
        expectations.emplace_back(item.substr(0, separator), static_cast<std::uint64_t>(value));
      }
    }
    if (semicolon == std::string::npos) {
      break;
    }
    start = semicolon + 1;
  }
  return expectations;
}

void record_topic_source_sequence(
  std::vector<std::pair<std::string, std::uint64_t>> * sequences,
  const rmw_fleetqox_cpp::DataFrame & frame)
{
  if (sequences == nullptr) {
    return;
  }
  auto existing = std::find_if(
    sequences->begin(),
    sequences->end(),
    [&](const std::pair<std::string, std::uint64_t> & item) {
      return item.first == frame.topic;
    });
  if (existing == sequences->end()) {
    sequences->emplace_back(frame.topic, frame.source_sequence_number);
  } else if (frame.source_sequence_number > existing->second) {
    existing->second = frame.source_sequence_number;
  }
}

void increment_topic_count(
  std::vector<std::pair<std::string, int>> * counts,
  const std::string & topic)
{
  if (counts == nullptr) {
    return;
  }
  auto existing = std::find_if(
    counts->begin(),
    counts->end(),
    [&](const std::pair<std::string, int> & item) {
      return item.first == topic;
    });
  if (existing == counts->end()) {
    counts->emplace_back(topic, 1);
  } else {
    ++existing->second;
  }
}

void record_robot_scheduler_result(
  std::vector<RobotSchedulerStats> * stats,
  const std::string & robot_id,
  bool deadline_missed)
{
  if (stats == nullptr) {
    return;
  }
  auto existing = std::find_if(
    stats->begin(),
    stats->end(),
    [&](const RobotSchedulerStats & item) {
      return item.robot_id == robot_id;
    });
  if (existing == stats->end()) {
    stats->push_back(RobotSchedulerStats{robot_id, 1, deadline_missed ? 1 : 0});
  } else {
    ++existing->forwarded;
    if (deadline_missed) {
      ++existing->deadline_misses;
    }
  }
}

double scheduler_deadline_success_jain_index(
  const std::vector<RobotSchedulerStats> & stats)
{
  if (stats.empty()) {
    return 1.0;
  }
  double success_sum = 0.0;
  double success_square_sum = 0.0;
  for (const RobotSchedulerStats & item : stats) {
    const double success = static_cast<double>(item.forwarded - item.deadline_misses);
    success_sum += success;
    success_square_sum += success * success;
  }
  if (success_square_sum <= 0.0) {
    return 0.0;
  }
  return success_sum * success_sum /
         (static_cast<double>(stats.size()) * success_square_sum);
}

bool topic_source_sequence_expectations_satisfied(
  const std::vector<std::pair<std::string, std::uint64_t>> & expected,
  const std::vector<std::pair<std::string, std::uint64_t>> & observed)
{
  for (const auto & expectation : expected) {
    const auto match = std::find_if(
      observed.begin(),
      observed.end(),
      [&](const std::pair<std::string, std::uint64_t> & item) {
        return item.first == expectation.first;
      });
    if (match == observed.end() || match->second < expectation.second) {
      return false;
    }
  }
  return true;
}

double parse_double(const std::string & text, double fallback)
{
  char * end = nullptr;
  errno = 0;
  const double value = std::strtod(text.c_str(), &end);
  if (errno != 0 || end == text.c_str() || *end != '\0') {
    return fallback;
  }
  return value;
}

void purge_expired_routes(
  std::vector<TopicRoute> * route_table,
  std::chrono::steady_clock::time_point now)
{
  if (route_table == nullptr) {
    return;
  }
  route_table->erase(
    std::remove_if(
      route_table->begin(),
      route_table->end(),
      [&](const TopicRoute & route) {
        return route.expires_at <= now;
      }),
    route_table->end());
}

void purge_expired_publisher_routes(
  std::vector<PublisherRoute> * route_table,
  std::chrono::steady_clock::time_point now)
{
  if (route_table == nullptr) {
    return;
  }
  route_table->erase(
    std::remove_if(
      route_table->begin(),
      route_table->end(),
      [&](const PublisherRoute & route) {
        return route.expires_at <= now;
      }),
    route_table->end());
}

void purge_expired_service_routes(
  std::vector<ServiceRoute> * route_table,
  std::chrono::steady_clock::time_point now)
{
  if (route_table == nullptr) {
    return;
  }
  route_table->erase(
    std::remove_if(
      route_table->begin(),
      route_table->end(),
      [&](const ServiceRoute & route) {
        return route.expires_at <= now;
      }),
    route_table->end());
}

void purge_expired_action_routes(
  std::vector<ActionRoute> * route_table,
  std::chrono::steady_clock::time_point now)
{
  if (route_table == nullptr) {
    return;
  }
  route_table->erase(
    std::remove_if(
      route_table->begin(),
      route_table->end(),
      [&](const ActionRoute & route) {
        return route.expires_at <= now;
      }),
    route_table->end());
}

void purge_expired_topic_qos(
  std::vector<TopicQosLease> * qos_table,
  std::chrono::steady_clock::time_point now)
{
  if (qos_table == nullptr) {
    return;
  }
  qos_table->erase(
    std::remove_if(
      qos_table->begin(),
      qos_table->end(),
      [&](const TopicQosLease & lease) {
        return lease.expires_at <= now;
      }),
    qos_table->end());
}

std::int64_t monotonic_timestamp_ns()
{
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
}

int parse_nonnegative_int_env(const char * name, int default_value, int max_value)
{
  const char * raw = std::getenv(name);
  if (raw == nullptr || raw[0] == '\0') {
    return default_value;
  }
  char * end = nullptr;
  errno = 0;
  const long parsed = std::strtol(raw, &end, 10);
  if (errno != 0 || end == raw || *end != '\0' || parsed < 0) {
    return default_value;
  }
  return static_cast<int>(std::min<long>(parsed, max_value));
}

std::int64_t graph_duration_ns(std::uint64_t sec, std::uint64_t nsec)
{
  if (sec == 0 && nsec == 0) {
    return 0;
  }
  constexpr std::uint64_t kNanosecondsPerSecond = 1000000000ull;
  if (sec > static_cast<std::uint64_t>(
      std::numeric_limits<std::int64_t>::max() / static_cast<std::int64_t>(kNanosecondsPerSecond)))
  {
    return std::numeric_limits<std::int64_t>::max();
  }
  const auto sec_ns = static_cast<std::int64_t>(sec * kNanosecondsPerSecond);
  const auto nsec_ns = static_cast<std::int64_t>(nsec);
  if (std::numeric_limits<std::int64_t>::max() - sec_ns < nsec_ns) {
    return std::numeric_limits<std::int64_t>::max();
  }
  return sec_ns + nsec_ns;
}

bool frame_exceeds_learned_lifespan(
  const std::vector<TopicQosLease> & qos_table,
  const rmw_fleetqox_cpp::DataFrame & frame)
{
  std::int64_t effective_lifespan_ns = 0;
  for (const TopicQosLease & lease : qos_table) {
    if (lease.domain_id != frame.domain_id || lease.topic != frame.topic) {
      continue;
    }
    const std::int64_t lifespan_ns =
      graph_duration_ns(lease.qos.lifespan_sec, lease.qos.lifespan_nsec);
    if (lifespan_ns <= 0) {
      continue;
    }
    if (effective_lifespan_ns == 0 || lifespan_ns < effective_lifespan_ns) {
      effective_lifespan_ns = lifespan_ns;
    }
  }
  if (effective_lifespan_ns <= 0 || frame.source_timestamp_ns <= 0) {
    return false;
  }
  const std::int64_t now = monotonic_timestamp_ns();
  return now > frame.source_timestamp_ns &&
         now - frame.source_timestamp_ns > effective_lifespan_ns;
}

std::int64_t learned_deadline_ns(
  const std::vector<TopicQosLease> & qos_table,
  const std::string & topic,
  std::uint64_t domain_id)
{
  std::int64_t effective_deadline_ns = 0;
  for (const TopicQosLease & lease : qos_table) {
    if (lease.domain_id != domain_id || lease.topic != topic) {
      continue;
    }
    const std::int64_t deadline_ns =
      graph_duration_ns(lease.qos.deadline_sec, lease.qos.deadline_nsec);
    if (deadline_ns <= 0) {
      continue;
    }
    if (effective_deadline_ns == 0 || deadline_ns < effective_deadline_ns) {
      effective_deadline_ns = deadline_ns;
    }
  }
  return effective_deadline_ns;
}

std::int64_t absolute_deadline_ns_for_frame(
  const std::vector<TopicQosLease> & qos_table,
  const rmw_fleetqox_cpp::DataFrame & frame)
{
  const std::int64_t deadline_ns = learned_deadline_ns(
    qos_table, frame.topic, frame.domain_id);
  if (deadline_ns <= 0 || frame.source_timestamp_ns <= 0 ||
    std::numeric_limits<std::int64_t>::max() - frame.source_timestamp_ns < deadline_ns)
  {
    return std::numeric_limits<std::int64_t>::max();
  }
  return frame.source_timestamp_ns + deadline_ns;
}

double scheduler_service_ratio(
  const RouterConfig & config,
  size_t encoded_size)
{
  if (config.telemetry_capacity_bytes <= 0 || config.scheduler_urgent_deadline_ms <= 0) {
    return 0.0;
  }
  const double service_ms =
    static_cast<double>(encoded_size) * 1000.0 /
    static_cast<double>(config.telemetry_capacity_bytes);
  return service_ms / static_cast<double>(config.scheduler_urgent_deadline_ms);
}

bool scheduler_admits_holdback(
  const RouterConfig & config,
  size_t encoded_size,
  SchedulerAdmissionState * state)
{
  if (config.scheduler_admission_policy.empty() ||
    config.scheduler_admission_policy == "always")
  {
    if (state != nullptr) {
      ++state->holdback_decisions;
    }
    return true;
  }
  if (config.scheduler_admission_policy == "never") {
    if (state != nullptr) {
      ++state->bypass_decisions;
    }
    return false;
  }
  const double service_ratio = scheduler_service_ratio(config, encoded_size);
  if (config.scheduler_admission_policy == "slo_service_time") {
    const bool admit = service_ratio >= config.scheduler_admission_min_service_ratio;
    if (state != nullptr) {
      ++state->samples;
      state->service_ratio_max = std::max(state->service_ratio_max, service_ratio);
      state->service_ratio_ewma = service_ratio;
      state->ewma_initialized = true;
      if (admit) {
        ++state->holdback_decisions;
      } else {
        ++state->bypass_decisions;
      }
    }
    return admit;
  }
  if (config.scheduler_admission_policy == "slo_service_epoch") {
    if (state == nullptr) {
      return service_ratio >= config.scheduler_admission_min_service_ratio;
    }
    const double alpha = std::max(
      0.0,
      std::min(1.0, config.scheduler_admission_ewma_alpha));
    if (!state->ewma_initialized) {
      state->service_ratio_ewma = service_ratio;
      state->ewma_initialized = true;
      state->frames_since_switch = std::max(
        1,
        config.scheduler_admission_min_epoch_frames);
    } else {
      state->service_ratio_ewma =
        alpha * service_ratio + (1.0 - alpha) * state->service_ratio_ewma;
      ++state->frames_since_switch;
    }
    ++state->samples;
    state->service_ratio_max = std::max(state->service_ratio_max, service_ratio);
    const int min_epoch_frames = std::max(1, config.scheduler_admission_min_epoch_frames);
    const double exit_ratio =
      config.scheduler_admission_exit_service_ratio > 0.0 ?
      config.scheduler_admission_exit_service_ratio :
      config.scheduler_admission_min_service_ratio;
    if (state->frames_since_switch >= min_epoch_frames) {
      if (!state->holdback_enabled &&
        state->service_ratio_ewma >= config.scheduler_admission_min_service_ratio)
      {
        state->holdback_enabled = true;
        state->frames_since_switch = 0;
        ++state->switches;
      } else if (state->holdback_enabled && state->service_ratio_ewma <= exit_ratio) {
        state->holdback_enabled = false;
        state->frames_since_switch = 0;
        ++state->switches;
      }
    }
    if (state->holdback_enabled) {
      ++state->holdback_decisions;
    } else {
      ++state->bypass_decisions;
    }
    return state->holdback_enabled;
  }
  if (state != nullptr) {
    ++state->holdback_decisions;
  }
  return true;
}

bool action_role_targets_server(const std::string & role)
{
  return role == "goal" || role == "cancel";
}

bool action_role_targets_client(const std::string & role)
{
  return role == "feedback" || role == "status" || role == "result";
}

int forward_data_frame(
  int fd,
  const std::string & encoded_frame,
  const rmw_fleetqox_cpp::DataFrame & frame,
  const sockaddr_in & source_address,
  const std::vector<sockaddr_in> & peer_addresses,
  const std::vector<TopicRoute> & route_table,
  std::vector<std::string> * forwarded_topics)
{
  std::vector<sockaddr_in> targets = peer_addresses;
  for (const TopicRoute & route : route_table) {
    if (route.domain_id != frame.domain_id || route.topic != frame.topic) {
      continue;
    }
    const auto already_targeted = std::any_of(
      targets.begin(),
      targets.end(),
      [&](const sockaddr_in & target) {
        return endpoints_match(target, route.address);
      });
    if (!already_targeted) {
      targets.push_back(route.address);
    }
  }

  int sent_count = 0;
  for (const sockaddr_in & peer : targets) {
    if (endpoints_match(peer, source_address)) {
      continue;
    }
    const auto sent = ::sendto(
      fd,
      encoded_frame.data(),
      encoded_frame.size(),
      0,
      reinterpret_cast<const sockaddr *>(&peer),
      sizeof(peer));
    if (sent >= 0 && static_cast<size_t>(sent) == encoded_frame.size()) {
      ++sent_count;
    }
  }
  if (sent_count > 0 && forwarded_topics != nullptr) {
    forwarded_topics->push_back(frame.topic);
  }
  return sent_count;
}

int forward_passthrough_datagram(
  int fd,
  const std::string & payload,
  const sockaddr_in & source_address,
  const std::vector<sockaddr_in> & peer_addresses,
  const std::vector<TopicRoute> & route_table,
  const std::vector<PublisherRoute> & publisher_route_table,
  const std::vector<ServiceRoute> & service_route_table,
  const std::vector<ActionRoute> & action_route_table)
{
  std::vector<sockaddr_in> targets = peer_addresses;
  for (const TopicRoute & route : route_table) {
    append_unique_peer(&targets, route.address);
  }
  for (const PublisherRoute & route : publisher_route_table) {
    append_unique_peer(&targets, route.address);
  }
  for (const ServiceRoute & route : service_route_table) {
    append_unique_peer(&targets, route.address);
  }
  for (const ActionRoute & route : action_route_table) {
    append_unique_peer(&targets, route.address);
  }
  int sent_count = 0;
  for (const sockaddr_in & peer : targets) {
    if (endpoints_match(peer, source_address)) {
      continue;
    }
    const auto sent = ::sendto(
      fd,
      payload.data(),
      payload.size(),
      0,
      reinterpret_cast<const sockaddr *>(&peer),
      sizeof(peer));
    if (sent >= 0 && static_cast<size_t>(sent) == payload.size()) {
      ++sent_count;
    }
  }
  return sent_count;
}

int forward_fragment_datagram(
  int fd,
  const std::string & payload,
  const sockaddr_in & source_address,
  const std::vector<sockaddr_in> & peer_addresses,
  const std::vector<TopicRoute> & route_table,
  const std::vector<PublisherRoute> & publisher_route_table,
  const std::vector<ServiceRoute> & service_route_table,
  const std::vector<ActionRoute> & action_route_table)
{
  std::uint64_t domain_id = 0;
  std::string topic;
  if (try_parse_fragment_route(payload, &domain_id, &topic)) {
    std::vector<sockaddr_in> targets = peer_addresses;
    for (const TopicRoute & route : route_table) {
      if (route.domain_id == domain_id && route.topic == topic) {
        append_unique_peer(&targets, route.address);
      }
    }
    if (!targets.empty()) {
      int sent_count = 0;
      for (const sockaddr_in & peer : targets) {
        if (endpoints_match(peer, source_address)) {
          continue;
        }
        const auto sent = ::sendto(
          fd,
          payload.data(),
          payload.size(),
          0,
          reinterpret_cast<const sockaddr *>(&peer),
          sizeof(peer));
        if (sent >= 0 && static_cast<size_t>(sent) == payload.size()) {
          ++sent_count;
        }
      }
      return sent_count;
    }
    // No route learned for this topic yet -- fall through to the broadcast
    // passthrough below rather than silently dropping the fragment.
  }
  return forward_passthrough_datagram(
    fd,
    payload,
    source_address,
    peer_addresses,
    route_table,
    publisher_route_table,
    service_route_table,
    action_route_table);
}

void append_router_path_telemetry(
  const RouterConfig & config,
  const rmw_fleetqox_cpp::DataFrame & frame,
  size_t encoded_size,
  bool delivered)
{
  if (config.telemetry_file.empty()) {
    return;
  }
  std::ofstream out(config.telemetry_file, std::ios::app);
  if (!out) {
    return;
  }
  out << "{\"schema_version\":\"fleetrmw.router_path_telemetry.v1\",";
  out << "\"event\":\"data_frame\",";
  out << "\"path_id\":\"" << json_escape(config.path_id) << "\",";
  out << "\"topic\":\"" << json_escape(frame.topic) << "\",";
  out << "\"publisher_id\":\"" << json_escape(frame.publisher_id) << "\",";
  out << "\"source_sequence_number\":" << frame.source_sequence_number << ",";
  out << "\"sent_frames\":1,";
  out << "\"delivered_frames\":" << (delivered ? 1 : 0) << ",";
  out << "\"nack_frames\":0,";
  out << "\"latency_ms\":" << config.telemetry_latency_ms << ",";
  out << "\"jitter_ms\":" << config.telemetry_jitter_ms << ",";
  if (config.telemetry_loss >= 0.0) {
    out << "\"loss\":" << config.telemetry_loss << ",";
  }
  if (config.telemetry_nack_rate >= 0.0) {
    out << "\"nack_rate\":" << config.telemetry_nack_rate << ",";
  }
  if (config.telemetry_deadline_miss_ratio >= 0.0) {
    out << "\"deadline_miss_ratio\":" << config.telemetry_deadline_miss_ratio << ",";
  }
  out << "\"bytes_sent\":" << encoded_size << ",";
  out << "\"capacity_bytes\":" << config.telemetry_capacity_bytes;
  out << "}" << std::endl;
}

bool should_drop_source_sequence_once(
  const RouterConfig & config,
  const rmw_fleetqox_cpp::DataFrame & frame,
  std::vector<std::string> * dropped_keys)
{
  if (dropped_keys == nullptr || config.drop_source_sequences.empty()) {
    return false;
  }
  if (!config.drop_topic_prefix.empty() &&
    frame.topic.rfind(config.drop_topic_prefix, 0) != 0)
  {
    return false;
  }
  const bool sequence_matches = std::find(
    config.drop_source_sequences.begin(),
    config.drop_source_sequences.end(),
    frame.source_sequence_number) != config.drop_source_sequences.end();
  if (!sequence_matches) {
    return false;
  }
  const std::string key =
    frame.publisher_id + "|" + std::to_string(frame.source_sequence_number);
  if (std::find(dropped_keys->begin(), dropped_keys->end(), key) != dropped_keys->end()) {
    return false;
  }
  dropped_keys->push_back(key);
  return true;
}

bool was_test_dropped(
  const rmw_fleetqox_cpp::DataFrame & frame,
  const std::vector<std::string> & dropped_keys)
{
  const std::string key =
    frame.publisher_id + "|" + std::to_string(frame.source_sequence_number);
  return std::find(dropped_keys.begin(), dropped_keys.end(), key) != dropped_keys.end();
}

RouterConfig parse_args(int argc, char ** argv)
{
  RouterConfig config;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--bind" && i + 1 < argc) {
      config.bind = argv[++i];
    } else if (arg == "--peers" && i + 1 < argc) {
      config.peers = argv[++i];
    } else if (arg == "--graph-peers" && i + 1 < argc) {
      config.graph_peers = argv[++i];
    } else if (arg == "--expected-frames" && i + 1 < argc) {
      config.expected_frames = std::stoi(argv[++i]);
    } else if (arg == "--expected-service-frames" && i + 1 < argc) {
      config.expected_service_frames = std::stoi(argv[++i]);
    } else if (arg == "--expected-action-frames" && i + 1 < argc) {
      config.expected_action_frames = std::stoi(argv[++i]);
    } else if (arg == "--expected-ack-nack-frames" && i + 1 < argc) {
      config.expected_ack_nack_frames = std::stoi(argv[++i]);
    } else if (arg == "--expected-ack-nack-forwarded" && i + 1 < argc) {
      config.expected_ack_nack_forwarded = std::stoi(argv[++i]);
    } else if (arg == "--expected-route-advertisements" && i + 1 < argc) {
      config.expected_route_advertisements = std::stoi(argv[++i]);
    } else if (arg == "--expected-graph-advertisements" && i + 1 < argc) {
      config.expected_graph_advertisements = std::stoi(argv[++i]);
    } else if (arg == "--expected-qos-drops" && i + 1 < argc) {
      config.expected_qos_drops = std::stoi(argv[++i]);
    } else if (arg == "--expected-forwarded-topic-source-sequences" && i + 1 < argc) {
      config.expected_forwarded_topic_source_sequences =
        parse_topic_sequence_expectations(argv[++i]);
    } else if (arg == "--forward-delay-ms" && i + 1 < argc) {
      config.forward_delay_ms = std::stoi(argv[++i]);
    } else if (arg == "--scheduler-window-ms" && i + 1 < argc) {
      config.scheduler_window_ms = std::stoi(argv[++i]);
    } else if (arg == "--scheduler-expected-frames" && i + 1 < argc) {
      config.scheduler_expected_frames = std::stoi(argv[++i]);
    } else if (arg == "--scheduler-urgent-deadline-ms" && i + 1 < argc) {
      config.scheduler_urgent_deadline_ms = std::stoi(argv[++i]);
    } else if (arg == "--scheduler-admission-policy" && i + 1 < argc) {
      config.scheduler_admission_policy = argv[++i];
    } else if (arg == "--scheduler-admission-min-service-ratio" && i + 1 < argc) {
      config.scheduler_admission_min_service_ratio =
        parse_double(argv[++i], config.scheduler_admission_min_service_ratio);
    } else if (arg == "--scheduler-admission-exit-service-ratio" && i + 1 < argc) {
      config.scheduler_admission_exit_service_ratio =
        parse_double(argv[++i], config.scheduler_admission_exit_service_ratio);
    } else if (arg == "--scheduler-admission-ewma-alpha" && i + 1 < argc) {
      config.scheduler_admission_ewma_alpha =
        parse_double(argv[++i], config.scheduler_admission_ewma_alpha);
    } else if (arg == "--scheduler-admission-min-epoch-frames" && i + 1 < argc) {
      config.scheduler_admission_min_epoch_frames = std::stoi(argv[++i]);
    } else if (arg == "--scheduler-topic-prefix" && i + 1 < argc) {
      config.scheduler_topic_prefix = argv[++i];
    } else if (arg == "--post-satisfaction-ms" && i + 1 < argc) {
      config.post_satisfaction_ms = std::stoi(argv[++i]);
    } else if (arg == "--drop-source-sequences" && i + 1 < argc) {
      config.drop_source_sequences = parse_sequence_list(argv[++i]);
    } else if (arg == "--drop-topic-prefix" && i + 1 < argc) {
      config.drop_topic_prefix = argv[++i];
    } else if (arg == "--timeout-ms" && i + 1 < argc) {
      config.timeout_ms = std::stoi(argv[++i]);
    } else if (arg == "--path-id" && i + 1 < argc) {
      config.path_id = argv[++i];
    } else if (arg == "--telemetry-file" && i + 1 < argc) {
      config.telemetry_file = argv[++i];
    } else if (arg == "--telemetry-latency-ms" && i + 1 < argc) {
      config.telemetry_latency_ms = parse_double(argv[++i], config.telemetry_latency_ms);
    } else if (arg == "--telemetry-jitter-ms" && i + 1 < argc) {
      config.telemetry_jitter_ms = parse_double(argv[++i], config.telemetry_jitter_ms);
    } else if (arg == "--telemetry-loss" && i + 1 < argc) {
      config.telemetry_loss = parse_double(argv[++i], config.telemetry_loss);
    } else if (arg == "--telemetry-nack-rate" && i + 1 < argc) {
      config.telemetry_nack_rate = parse_double(argv[++i], config.telemetry_nack_rate);
    } else if (arg == "--telemetry-deadline-miss-ratio" && i + 1 < argc) {
      config.telemetry_deadline_miss_ratio =
        parse_double(argv[++i], config.telemetry_deadline_miss_ratio);
    } else if (arg == "--telemetry-capacity-bytes" && i + 1 < argc) {
      config.telemetry_capacity_bytes = std::stoi(argv[++i]);
    }
  }
  return config;
}

void print_router_json(
  const std::string & status,
  const RouterConfig & config,
  int received,
  int service_frames,
  int ack_nack_frames,
  int forwarded,
  int qos_dropped,
  int test_dropped,
  int service_forwarded,
  int ack_nack_forwarded,
  int invalid,
  int route_advertisements,
  int learned_routes,
  int graph_advertisements,
  int graph_forwarded,
  int graph_publishers,
  int graph_subscriptions,
  int graph_services,
  int graph_clients,
  const std::vector<std::string> & forwarded_topics,
  const std::vector<std::string> & graph_topics,
  const std::vector<std::string> & service_names,
  const std::vector<std::string> & topics,
  const std::vector<std::pair<std::string, std::uint64_t>> & forwarded_topic_source_sequences =
    std::vector<std::pair<std::string, std::uint64_t>>(),
  int action_frames = 0,
  int action_forwarded = 0,
  int graph_action_servers = 0,
  int graph_action_clients = 0,
  const std::vector<std::string> & action_names = std::vector<std::string>(),
  const std::vector<std::pair<std::string, int>> & qos_dropped_topic_counts =
    std::vector<std::pair<std::string, int>>(),
  int scheduler_queued_frames = 0,
  int scheduler_urgent_frames = 0,
  int scheduler_paced_frames = 0,
  int scheduler_drain_pacing_ms = 0,
  int scheduler_forwarded_frames = 0,
  int scheduler_admission_bypassed_frames = 0,
  int scheduler_deadline_misses = 0,
  int scheduler_fresh_deadline_misses = 0,
  int scheduler_repair_frames = 0,
  int scheduler_repair_deadline_misses = 0,
  double scheduler_queue_wait_ms_mean = 0.0,
  double scheduler_queue_wait_ms_max = 0.0,
  double scheduler_admission_service_ratio_max = 0.0,
  double scheduler_admission_service_ratio_ewma = 0.0,
  int scheduler_admission_epoch_samples = 0,
  int scheduler_admission_switches = 0,
  int scheduler_admission_holdback_decisions = 0,
  int scheduler_admission_bypass_decisions = 0,
  bool scheduler_admission_holdback_enabled = false,
  const std::vector<RobotSchedulerStats> & robot_scheduler_stats =
    std::vector<RobotSchedulerStats>(),
  const std::vector<SchedulerDeadlineMiss> & scheduler_deadline_miss_frames =
    std::vector<SchedulerDeadlineMiss>(),
  int unrecoverable_loss_notice_frames = 0,
  int unrecoverable_loss_notice_forwarded = 0)
{
  std::cout << "{\"schema_version\":\"fleetrmw.rmw_udp_router_probe.v1\",";
  std::cout << "\"status\":\"" << status << "\",";
  std::cout << "\"bind\":\"" << json_escape(config.bind) << "\",";
  std::cout << "\"peer_count\":" << endpoint_count(config.peers) << ",";
  std::cout << "\"graph_peer_count\":" << endpoint_count(config.graph_peers) << ",";
  std::cout << "\"expected_frames\":" << config.expected_frames << ",";
  std::cout << "\"expected_service_frames\":" << config.expected_service_frames << ",";
  std::cout << "\"expected_action_frames\":" << config.expected_action_frames << ",";
  std::cout << "\"expected_ack_nack_frames\":" << config.expected_ack_nack_frames << ",";
  std::cout << "\"expected_ack_nack_forwarded\":"
            << (config.expected_ack_nack_forwarded >= 0 ?
              config.expected_ack_nack_forwarded : config.expected_ack_nack_frames) << ",";
  std::cout << "\"expected_route_advertisements\":" << config.expected_route_advertisements << ",";
  std::cout << "\"expected_graph_advertisements\":" << config.expected_graph_advertisements << ",";
  std::cout << "\"expected_qos_drops\":" << config.expected_qos_drops << ",";
  std::cout << "\"expected_forwarded_topic_source_sequences\":[";
  for (size_t i = 0; i < config.expected_forwarded_topic_source_sequences.size(); ++i) {
    if (i > 0) {
      std::cout << ",";
    }
    std::cout << "{\"topic\":\"" <<
      json_escape(config.expected_forwarded_topic_source_sequences[i].first) <<
      "\",\"source_sequence_number\":" <<
      config.expected_forwarded_topic_source_sequences[i].second << "}";
  }
  std::cout << "],";
  std::cout << "\"forward_delay_ms\":" << config.forward_delay_ms << ",";
  std::cout << "\"scheduler_window_ms\":" << config.scheduler_window_ms << ",";
  std::cout << "\"scheduler_expected_frames\":" << config.scheduler_expected_frames << ",";
  std::cout << "\"scheduler_urgent_deadline_ms\":" <<
    config.scheduler_urgent_deadline_ms << ",";
  std::cout << "\"scheduler_admission_policy\":\"" <<
    json_escape(config.scheduler_admission_policy) << "\",";
  std::cout << "\"scheduler_admission_min_service_ratio\":" <<
    config.scheduler_admission_min_service_ratio << ",";
  std::cout << "\"scheduler_admission_exit_service_ratio\":" <<
    config.scheduler_admission_exit_service_ratio << ",";
  std::cout << "\"scheduler_admission_ewma_alpha\":" <<
    config.scheduler_admission_ewma_alpha << ",";
  std::cout << "\"scheduler_admission_min_epoch_frames\":" <<
    config.scheduler_admission_min_epoch_frames << ",";
  std::cout << "\"scheduler_topic_prefix\":\"" <<
    json_escape(config.scheduler_topic_prefix) << "\",";
  std::cout << "\"drop_topic_prefix\":\"" <<
    json_escape(config.drop_topic_prefix) << "\",";
  std::cout << "\"scheduler_queued_frames\":" << scheduler_queued_frames << ",";
  std::cout << "\"scheduler_urgent_frames\":" << scheduler_urgent_frames << ",";
  std::cout << "\"scheduler_paced_frames\":" << scheduler_paced_frames << ",";
  std::cout << "\"scheduler_drain_pacing_ms\":" << scheduler_drain_pacing_ms << ",";
  std::cout << "\"scheduler_forwarded_frames\":" << scheduler_forwarded_frames << ",";
  std::cout << "\"scheduler_admission_bypassed_frames\":" <<
    scheduler_admission_bypassed_frames << ",";
  std::cout << "\"scheduler_deadline_misses\":" << scheduler_deadline_misses << ",";
  std::cout << "\"scheduler_fresh_deadline_misses\":" <<
    scheduler_fresh_deadline_misses << ",";
  std::cout << "\"scheduler_repair_frames\":" << scheduler_repair_frames << ",";
  std::cout << "\"scheduler_repair_deadline_misses\":" <<
    scheduler_repair_deadline_misses << ",";
  std::cout << "\"scheduler_deadline_miss_frames\":[";
  for (size_t i = 0; i < scheduler_deadline_miss_frames.size(); ++i) {
    if (i > 0) {
      std::cout << ",";
    }
    const SchedulerDeadlineMiss & miss = scheduler_deadline_miss_frames[i];
    std::cout << "{\"topic\":\"" << json_escape(miss.topic) << "\",";
    std::cout << "\"robot_id\":\"" << json_escape(miss.robot_id) << "\",";
    std::cout << "\"source_sequence_number\":" << miss.source_sequence_number << ",";
    std::cout << "\"test_repair\":" << (miss.test_repair ? "true" : "false") << ",";
    std::cout << "\"lateness_ms\":" << miss.lateness_ms << "}";
  }
  std::cout << "],";
  std::cout << "\"scheduler_queue_wait_ms_mean\":" << scheduler_queue_wait_ms_mean << ",";
  std::cout << "\"scheduler_queue_wait_ms_max\":" << scheduler_queue_wait_ms_max << ",";
  std::cout << "\"scheduler_admission_service_ratio_max\":" <<
    scheduler_admission_service_ratio_max << ",";
  std::cout << "\"scheduler_admission_service_ratio_ewma\":" <<
    scheduler_admission_service_ratio_ewma << ",";
  std::cout << "\"scheduler_admission_epoch_samples\":" <<
    scheduler_admission_epoch_samples << ",";
  std::cout << "\"scheduler_admission_switches\":" <<
    scheduler_admission_switches << ",";
  std::cout << "\"scheduler_admission_holdback_decisions\":" <<
    scheduler_admission_holdback_decisions << ",";
  std::cout << "\"scheduler_admission_bypass_decisions\":" <<
    scheduler_admission_bypass_decisions << ",";
  std::cout << "\"scheduler_admission_holdback_enabled\":" <<
    (scheduler_admission_holdback_enabled ? "true" : "false") << ",";
  std::cout << "\"scheduler_deadline_success_jain_index\":" <<
    scheduler_deadline_success_jain_index(robot_scheduler_stats) << ",";
  std::cout << "\"scheduler_per_robot\":{";
  for (size_t i = 0; i < robot_scheduler_stats.size(); ++i) {
    if (i > 0) {
      std::cout << ",";
    }
    const RobotSchedulerStats & item = robot_scheduler_stats[i];
    std::cout << "\"" << json_escape(item.robot_id) << "\":{";
    std::cout << "\"forwarded\":" << item.forwarded << ",";
    std::cout << "\"deadline_misses\":" << item.deadline_misses << ",";
    std::cout << "\"deadline_success_ratio\":" <<
      (item.forwarded > 0 ?
      static_cast<double>(item.forwarded - item.deadline_misses) /
      static_cast<double>(item.forwarded) : 0.0);
    std::cout << "}";
  }
  std::cout << "},";
  std::cout << "\"post_satisfaction_ms\":" << config.post_satisfaction_ms << ",";
  std::cout << "\"received_frames\":" << received << ",";
  std::cout << "\"service_frames\":" << service_frames << ",";
  std::cout << "\"ack_nack_frames\":" << ack_nack_frames << ",";
  std::cout << "\"forwarded_frames\":" << forwarded << ",";
  std::cout << "\"qos_dropped_frames\":" << qos_dropped << ",";
  std::cout << "\"qos_dropped_topic_counts\":{";
  for (size_t i = 0; i < qos_dropped_topic_counts.size(); ++i) {
    if (i > 0) {
      std::cout << ",";
    }
    std::cout << "\"" << json_escape(qos_dropped_topic_counts[i].first) << "\":" <<
      qos_dropped_topic_counts[i].second;
  }
  std::cout << "},";
  std::cout << "\"test_dropped_frames\":" << test_dropped << ",";
  std::cout << "\"service_forwarded\":" << service_forwarded << ",";
  std::cout << "\"action_frames\":" << action_frames << ",";
  std::cout << "\"action_forwarded\":" << action_forwarded << ",";
  std::cout << "\"ack_nack_forwarded\":" << ack_nack_forwarded << ",";
  std::cout << "\"unrecoverable_loss_notice_frames\":" <<
    unrecoverable_loss_notice_frames << ",";
  std::cout << "\"unrecoverable_loss_notice_forwarded\":" <<
    unrecoverable_loss_notice_forwarded << ",";
  std::cout << "\"invalid_frames\":" << invalid << ",";
  std::cout << "\"route_advertisements\":" << route_advertisements << ",";
  std::cout << "\"learned_routes\":" << learned_routes << ",";
  std::cout << "\"graph_advertisements\":" << graph_advertisements << ",";
  std::cout << "\"graph_forwarded\":" << graph_forwarded << ",";
  std::cout << "\"graph_publishers\":" << graph_publishers << ",";
  std::cout << "\"graph_subscriptions\":" << graph_subscriptions << ",";
  std::cout << "\"graph_services\":" << graph_services << ",";
  std::cout << "\"graph_clients\":" << graph_clients << ",";
  std::cout << "\"graph_action_servers\":" << graph_action_servers << ",";
  std::cout << "\"graph_action_clients\":" << graph_action_clients << ",";
  std::cout << "\"forwarded_topics\":[";
  for (size_t i = 0; i < forwarded_topics.size(); ++i) {
    if (i > 0) {
      std::cout << ",";
    }
    std::cout << "\"" << json_escape(forwarded_topics[i]) << "\"";
  }
  std::cout << "],";
  std::cout << "\"graph_topics\":[";
  for (size_t i = 0; i < graph_topics.size(); ++i) {
    if (i > 0) {
      std::cout << ",";
    }
    std::cout << "\"" << json_escape(graph_topics[i]) << "\"";
  }
  std::cout << "],";
  std::cout << "\"service_names\":[";
  for (size_t i = 0; i < service_names.size(); ++i) {
    if (i > 0) {
      std::cout << ",";
    }
    std::cout << "\"" << json_escape(service_names[i]) << "\"";
  }
  std::cout << "],";
  std::cout << "\"action_names\":[";
  for (size_t i = 0; i < action_names.size(); ++i) {
    if (i > 0) {
      std::cout << ",";
    }
    std::cout << "\"" << json_escape(action_names[i]) << "\"";
  }
  std::cout << "],";
  std::cout << "\"topics\":[";
  for (size_t i = 0; i < topics.size(); ++i) {
    if (i > 0) {
      std::cout << ",";
    }
    std::cout << "\"" << json_escape(topics[i]) << "\"";
  }
  std::cout << "],";
  std::cout << "\"forwarded_topic_source_sequences\":[";
  for (size_t i = 0; i < forwarded_topic_source_sequences.size(); ++i) {
    if (i > 0) {
      std::cout << ",";
    }
    std::cout << "{\"topic\":\"" <<
      json_escape(forwarded_topic_source_sequences[i].first) <<
      "\",\"source_sequence_number\":" <<
      forwarded_topic_source_sequences[i].second << "}";
  }
  std::cout << "]}" << std::endl;
}

}  // namespace

int main(int argc, char ** argv)
{
  const RouterConfig config = parse_args(argc, argv);

  sockaddr_in bind_address{};
  std::vector<sockaddr_in> peer_addresses;
  std::vector<sockaddr_in> graph_peer_addresses;
  if (!parse_ipv4_endpoint(config.bind, &bind_address) ||
    !parse_peer_endpoints(config.peers, &peer_addresses) ||
    !parse_peer_endpoints(config.graph_peers, &graph_peer_addresses))
  {
    print_router_json("invalid_config", config, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, {}, {}, {}, {});
    return 1;
  }

  const int fd = ::socket(AF_INET, SOCK_DGRAM, 0);
  if (fd < 0) {
    print_router_json("socket_failed", config, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, {}, {}, {}, {});
    return 1;
  }

  const int udp_socket_buffer_bytes = parse_nonnegative_int_env(
    "FLEETQOX_ROUTER_UDP_SOCKET_BUFFER_BYTES",
    4 * 1024 * 1024,
    64 * 1024 * 1024);
  if (udp_socket_buffer_bytes > 0) {
    const int buffer_bytes = udp_socket_buffer_bytes;
    (void)::setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &buffer_bytes, sizeof(buffer_bytes));
    (void)::setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &buffer_bytes, sizeof(buffer_bytes));
  }

  timeval timeout{};
  timeout.tv_sec = 0;
  timeout.tv_usec = 100000;
  if (::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) != 0) {
    ::close(fd);
    print_router_json("setsockopt_failed", config, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, {}, {}, {}, {});
    return 1;
  }
  if (::bind(fd, reinterpret_cast<const sockaddr *>(&bind_address), sizeof(bind_address)) != 0) {
    ::close(fd);
    print_router_json("bind_failed", config, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, {}, {}, {}, {});
    return 1;
  }

  int received = 0;
  int service_frames = 0;
  int action_frames = 0;
  int ack_nack_frames = 0;
  int forwarded = 0;
  int qos_dropped = 0;
  int test_dropped = 0;
  int service_forwarded = 0;
  int action_forwarded = 0;
  int ack_nack_forwarded = 0;
  int unrecoverable_loss_notice_frames = 0;
  int unrecoverable_loss_notice_forwarded = 0;
  int invalid = 0;
  int route_advertisements = 0;
  int graph_advertisements = 0;
  int graph_forwarded = 0;
  int graph_publishers = 0;
  int graph_subscriptions = 0;
  int graph_services = 0;
  int graph_clients = 0;
  int graph_action_servers = 0;
  int graph_action_clients = 0;
  std::vector<std::string> topics;
  std::vector<std::string> forwarded_topics;
  std::vector<std::string> graph_topics;
  std::vector<std::string> service_names;
  std::vector<std::string> action_names;
  std::vector<std::pair<std::string, int>> qos_dropped_topic_counts;
  std::vector<TopicRoute> route_table;
  std::vector<PublisherRoute> publisher_route_table;
  std::vector<ServiceRoute> service_route_table;
  std::vector<ActionRoute> action_route_table;
  std::vector<TopicQosLease> topic_qos_table;
  std::vector<std::string> dropped_source_sequence_keys;
  std::vector<QueuedDataFrame> queued_data_frames;
  std::vector<std::pair<std::string, std::uint64_t>> forwarded_topic_source_sequences;
  std::vector<RobotSchedulerStats> robot_scheduler_stats;
  int scheduler_queued_frames = 0;
  int scheduler_urgent_frames = 0;
  int scheduler_paced_frames = 0;
  int scheduler_drain_pacing_ms = 0;
  int scheduler_forwarded_frames = 0;
  int scheduler_admission_bypassed_frames = 0;
  int scheduler_deadline_misses = 0;
  int scheduler_fresh_deadline_misses = 0;
  int scheduler_repair_frames = 0;
  int scheduler_repair_deadline_misses = 0;
  std::vector<SchedulerDeadlineMiss> scheduler_deadline_miss_frames;
  SchedulerAdmissionState scheduler_admission_state;
  std::int64_t scheduler_queue_wait_ns_sum = 0;
  std::int64_t scheduler_queue_wait_ns_max = 0;
  std::uint64_t next_queue_order = 0;
  auto forward_scheduled_data_frame = [&](const QueuedDataFrame & queued) {
      const auto forwarding_time = std::chrono::steady_clock::now();
      const std::int64_t forwarding_time_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(
        forwarding_time.time_since_epoch()).count();
      const std::int64_t queue_wait_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(
        forwarding_time - queued.enqueued_at).count();
      const int forwarded_now = forward_data_frame(
        fd,
        queued.encoded_frame,
        queued.frame,
        queued.source_address,
        peer_addresses,
        route_table,
        &forwarded_topics);
      forwarded += forwarded_now;
      if (forwarded_now > 0) {
        const bool deadline_missed =
          queued.absolute_deadline_ns != std::numeric_limits<std::int64_t>::max() &&
          forwarding_time_ns > queued.absolute_deadline_ns;
        ++scheduler_forwarded_frames;
        if (queued.test_repair) {
          ++scheduler_repair_frames;
        }
        if (deadline_missed) {
          ++scheduler_deadline_misses;
          scheduler_deadline_miss_frames.push_back(
            SchedulerDeadlineMiss{
              queued.frame.topic,
              queued.frame.robot_id,
              queued.frame.source_sequence_number,
              queued.test_repair,
              static_cast<double>(forwarding_time_ns - queued.absolute_deadline_ns) / 1000000.0});
          if (queued.test_repair) {
            ++scheduler_repair_deadline_misses;
          } else {
            ++scheduler_fresh_deadline_misses;
          }
        }
        scheduler_queue_wait_ns_sum += queue_wait_ns;
        scheduler_queue_wait_ns_max = std::max(scheduler_queue_wait_ns_max, queue_wait_ns);
        record_robot_scheduler_result(
          &robot_scheduler_stats, queued.frame.robot_id, deadline_missed);
        record_topic_source_sequence(&forwarded_topic_source_sequences, queued.frame);
      }
      append_router_path_telemetry(
        config, queued.frame, queued.encoded_frame.size(), forwarded_now > 0);
    };
  auto flush_queued_data_frames = [&]() {
    std::stable_sort(
      queued_data_frames.begin(),
      queued_data_frames.end(),
      [](const QueuedDataFrame & left, const QueuedDataFrame & right) {
        if (left.absolute_deadline_ns != right.absolute_deadline_ns) {
          return left.absolute_deadline_ns < right.absolute_deadline_ns;
        }
        return left.order < right.order;
      });
    // This sleep_for() blocks the single-threaded main loop for its full
    // duration -- the router can't receive or forward anything else
    // (including urgent control frames) while draining a paced batch.
    // With the old 100ms cap, releasing 8 queued frames could block the
    // loop for up to ~700ms, which was long enough for a control frame
    // to arrive mid-drain and sit unprocessed until the loop woke back
    // up. Capping at 10ms instead keeps per-frame spacing (still useful
    // to avoid slamming a low-bandwidth link) without turning a queue
    // flush into a near-second-long stall of everything else.
    const int drain_pacing_ms =
      config.scheduler_urgent_deadline_ms > 0 &&
      config.scheduler_window_ms > 0 &&
      queued_data_frames.size() > 1 ?
      std::min(
        2,
        std::max(1, config.scheduler_window_ms / static_cast<int>(queued_data_frames.size()))) :
      0;
    scheduler_drain_pacing_ms = std::max(scheduler_drain_pacing_ms, drain_pacing_ms);
    for (size_t i = 0; i < queued_data_frames.size(); ++i) {
      forward_scheduled_data_frame(queued_data_frames[i]);
      if (drain_pacing_ms > 0 && i + 1 < queued_data_frames.size()) {
        ++scheduler_paced_frames;
        std::this_thread::sleep_for(std::chrono::milliseconds(drain_pacing_ms));
      }
    }
    queued_data_frames.clear();
  };
  std::array<char, kMaxUdpPayloadBytes> buffer{};
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(config.timeout_ms);
  auto expectations_satisfied = [&]() {
    return received >= config.expected_frames &&
           service_frames >= config.expected_service_frames &&
           action_frames >= config.expected_action_frames &&
           action_forwarded >= config.expected_action_frames &&
           ack_nack_frames >= config.expected_ack_nack_frames &&
           qos_dropped >= config.expected_qos_drops &&
           route_advertisements >= config.expected_route_advertisements &&
           graph_advertisements >= config.expected_graph_advertisements &&
           topic_source_sequence_expectations_satisfied(
             config.expected_forwarded_topic_source_sequences,
           forwarded_topic_source_sequences) &&
           queued_data_frames.empty();
  };
  auto activity_counter = [&]() -> std::int64_t {
    return static_cast<std::int64_t>(received) +
           static_cast<std::int64_t>(service_frames) +
           static_cast<std::int64_t>(action_frames) +
           static_cast<std::int64_t>(ack_nack_frames) +
           static_cast<std::int64_t>(forwarded) +
           static_cast<std::int64_t>(service_forwarded) +
           static_cast<std::int64_t>(action_forwarded) +
           static_cast<std::int64_t>(ack_nack_forwarded) +
           static_cast<std::int64_t>(unrecoverable_loss_notice_frames) +
           static_cast<std::int64_t>(unrecoverable_loss_notice_forwarded) +
           static_cast<std::int64_t>(qos_dropped) +
           static_cast<std::int64_t>(test_dropped) +
           static_cast<std::int64_t>(invalid) +
           static_cast<std::int64_t>(scheduler_queued_frames) +
           static_cast<std::int64_t>(scheduler_forwarded_frames);
  };
  bool satisfaction_dwell_started = false;
  std::chrono::steady_clock::time_point satisfaction_dwell_start{};
  std::int64_t satisfaction_dwell_activity_counter = -1;
  while (std::chrono::steady_clock::now() < deadline) {
    const auto now = std::chrono::steady_clock::now();
    if (expectations_satisfied()) {
      if (config.post_satisfaction_ms <= 0) {
        break;
      }
      const std::int64_t current_activity_counter = activity_counter();
      if (!satisfaction_dwell_started ||
        current_activity_counter != satisfaction_dwell_activity_counter)
      {
        satisfaction_dwell_started = true;
        satisfaction_dwell_start = now;
        satisfaction_dwell_activity_counter = current_activity_counter;
      } else if (
        now - satisfaction_dwell_start >=
        std::chrono::milliseconds(std::max(config.post_satisfaction_ms, 0)))
      {
        break;
      }
    } else {
      satisfaction_dwell_started = false;
      satisfaction_dwell_activity_counter = -1;
    }
    purge_expired_routes(&route_table, now);
    purge_expired_publisher_routes(&publisher_route_table, now);
    purge_expired_service_routes(&service_route_table, now);
    purge_expired_action_routes(&action_route_table, now);
    purge_expired_topic_qos(&topic_qos_table, now);
    const bool scheduler_batch_ready =
      config.scheduler_expected_frames > 0 ?
      queued_data_frames.size() >= static_cast<size_t>(config.scheduler_expected_frames) :
      received >= config.expected_frames;
    if (!queued_data_frames.empty() &&
      (scheduler_batch_ready ||
      config.scheduler_window_ms <= 0 ||
      now - queued_data_frames.front().enqueued_at >= std::chrono::milliseconds(config.scheduler_window_ms)))
    {
      flush_queued_data_frames();
      continue;
    }
    sockaddr_in source_address{};
    socklen_t source_length = sizeof(source_address);
    const auto size = ::recvfrom(
      fd,
      buffer.data(),
      buffer.size(),
      0,
      reinterpret_cast<sockaddr *>(&source_address),
      &source_length);
    if (size < 0) {
      if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) {
        continue;
      }
      break;
    }
    if (size == 0) {
      continue;
    }

    const std::string encoded_frame(buffer.data(), static_cast<size_t>(size));
    if (is_fragment_datagram(encoded_frame)) {
      forwarded += forward_fragment_datagram(
        fd,
        encoded_frame,
        source_address,
        peer_addresses,
        route_table,
        publisher_route_table,
        service_route_table,
        action_route_table);
      continue;
    }

    const auto advertisement = rmw_fleetqox_cpp::decode_route_advertisement(encoded_frame);
    if (advertisement) {
      ++route_advertisements;
      if (advertisement->role == "subscriber") {
        auto already_known = std::find_if(
          route_table.begin(),
          route_table.end(),
          [&](const TopicRoute & route) {
            return route.topic == advertisement->topic &&
                   route.domain_id == advertisement->domain_id &&
                   endpoints_match(route.address, source_address);
          });
        if (already_known == route_table.end()) {
          route_table.push_back(
            TopicRoute{
              advertisement->topic,
              source_address,
              now + lease_duration(advertisement->lease_ms),
              advertisement->domain_id});
        } else {
          already_known->expires_at = now + lease_duration(advertisement->lease_ms);
        }
      }
      continue;
    }

    const auto graph_advertisement = rmw_fleetqox_cpp::decode_graph_advertisement(encoded_frame);
    if (graph_advertisement) {
      ++graph_advertisements;
      if (graph_advertisement->entity_kind == "publisher") {
        ++graph_publishers;
      } else if (graph_advertisement->entity_kind == "subscription") {
        ++graph_subscriptions;
      } else if (graph_advertisement->entity_kind == "service") {
        ++graph_services;
      } else if (graph_advertisement->entity_kind == "client") {
        ++graph_clients;
      } else if (graph_advertisement->entity_kind == "action_server") {
        ++graph_action_servers;
      } else if (graph_advertisement->entity_kind == "action_client") {
        ++graph_action_clients;
      }
      if (!graph_advertisement->topic.empty() &&
        std::find(graph_topics.begin(), graph_topics.end(), graph_advertisement->topic) == graph_topics.end())
      {
        graph_topics.push_back(graph_advertisement->topic);
      }
      if (graph_advertisement->entity_kind == "publisher") {
        if (graph_advertisement->action == "remove") {
          topic_qos_table.erase(
            std::remove_if(
              topic_qos_table.begin(),
              topic_qos_table.end(),
              [&](const TopicQosLease & lease) {
                return lease.domain_id == graph_advertisement->domain_id &&
                       lease.endpoint_id == graph_advertisement->endpoint_id;
              }),
            topic_qos_table.end());
        } else {
          auto already_known = std::find_if(
            topic_qos_table.begin(),
            topic_qos_table.end(),
            [&](const TopicQosLease & lease) {
              return lease.domain_id == graph_advertisement->domain_id &&
                     lease.endpoint_id == graph_advertisement->endpoint_id;
            });
          if (already_known == topic_qos_table.end()) {
            topic_qos_table.push_back(
              TopicQosLease{
                graph_advertisement->endpoint_id,
                graph_advertisement->topic,
                graph_advertisement->qos,
                now + lease_duration(graph_advertisement->lease_ms),
                graph_advertisement->domain_id});
          } else {
            already_known->topic = graph_advertisement->topic;
            already_known->qos = graph_advertisement->qos;
            already_known->expires_at = now + lease_duration(graph_advertisement->lease_ms);
          }
        }
      }
      const bool is_service_graph =
        graph_advertisement->entity_kind == "service" || graph_advertisement->entity_kind == "client";
      if (is_service_graph) {
        if (graph_advertisement->action == "remove") {
          service_route_table.erase(
            std::remove_if(
              service_route_table.begin(),
              service_route_table.end(),
              [&](const ServiceRoute & route) {
                return route.domain_id == graph_advertisement->domain_id &&
                       route.endpoint_id == graph_advertisement->endpoint_id;
              }),
            service_route_table.end());
        } else {
          auto already_known = std::find_if(
            service_route_table.begin(),
            service_route_table.end(),
            [&](const ServiceRoute & route) {
              return route.endpoint_id == graph_advertisement->endpoint_id &&
                     route.domain_id == graph_advertisement->domain_id &&
                     endpoints_match(route.address, source_address);
            });
          if (already_known == service_route_table.end()) {
            service_route_table.push_back(
              ServiceRoute{
                graph_advertisement->entity_kind,
                graph_advertisement->topic,
                graph_advertisement->endpoint_id,
                source_address,
                now + lease_duration(graph_advertisement->lease_ms),
                graph_advertisement->domain_id});
          } else {
            already_known->role = graph_advertisement->entity_kind;
            already_known->service_name = graph_advertisement->topic;
            already_known->expires_at = now + lease_duration(graph_advertisement->lease_ms);
          }
        }
      }
      const bool is_action_graph =
        graph_advertisement->entity_kind == "action_server" ||
        graph_advertisement->entity_kind == "action_client";
      if (is_action_graph) {
        if (graph_advertisement->action == "remove") {
          action_route_table.erase(
            std::remove_if(
              action_route_table.begin(),
              action_route_table.end(),
              [&](const ActionRoute & route) {
                return route.domain_id == graph_advertisement->domain_id &&
                       route.endpoint_id == graph_advertisement->endpoint_id;
              }),
            action_route_table.end());
        } else {
          auto already_known = std::find_if(
            action_route_table.begin(),
            action_route_table.end(),
            [&](const ActionRoute & route) {
              return route.endpoint_id == graph_advertisement->endpoint_id &&
                     route.domain_id == graph_advertisement->domain_id &&
                     endpoints_match(route.address, source_address);
            });
          if (already_known == action_route_table.end()) {
            action_route_table.push_back(
              ActionRoute{
                graph_advertisement->entity_kind,
                graph_advertisement->topic,
                graph_advertisement->endpoint_id,
                source_address,
                now + lease_duration(graph_advertisement->lease_ms),
                graph_advertisement->domain_id});
          } else {
            already_known->role = graph_advertisement->entity_kind;
            already_known->action_name = graph_advertisement->topic;
            already_known->expires_at = now + lease_duration(graph_advertisement->lease_ms);
          }
        }
      }
      std::vector<sockaddr_in> graph_targets = peer_addresses;
      for (const sockaddr_in & peer : graph_peer_addresses) {
        append_unique_peer(&graph_targets, peer);
      }
      for (const ServiceRoute & route : service_route_table) {
        append_unique_peer(&graph_targets, route.address);
      }
      for (const ActionRoute & route : action_route_table) {
        append_unique_peer(&graph_targets, route.address);
      }
      // Plain pub/sub clients are never in peer_addresses/graph_peer_addresses
      // (those are for static router-to-router/graph-peer config, and a
      // publisher's ephemeral bind port can't be known ahead of time
      // anyway). Without this, a subscription's graph advertisement never
      // reaches the publisher unless it happens to also be a service/action
      // endpoint, so the publisher's reliable-retransmit ledger never learns
      // of a matched subscriber and treats every send as already
      // acknowledged. route_table/publisher_route_table are already learned
      // dynamically from route advertisements and data frames, so reuse
      // them here the same way service/action routes are reused above.
      for (const TopicRoute & route : route_table) {
        append_unique_peer(&graph_targets, route.address);
      }
      for (const PublisherRoute & route : publisher_route_table) {
        append_unique_peer(&graph_targets, route.address);
      }
      for (const sockaddr_in & peer : graph_targets) {
        if (endpoints_match(peer, source_address)) {
          continue;
        }
        const auto sent = ::sendto(
          fd,
          encoded_frame.data(),
          encoded_frame.size(),
          0,
          reinterpret_cast<const sockaddr *>(&peer),
          sizeof(peer));
        if (sent >= 0 && static_cast<size_t>(sent) == encoded_frame.size()) {
          ++graph_forwarded;
        }
      }
      continue;
    }

    const auto service_frame = rmw_fleetqox_cpp::decode_service_frame(encoded_frame);
    if (service_frame) {
      ++service_frames;
      if (std::find(service_names.begin(), service_names.end(), service_frame->service_name) == service_names.end()) {
        service_names.push_back(service_frame->service_name);
      }
      std::vector<sockaddr_in> targets = peer_addresses;
      for (const ServiceRoute & route : service_route_table) {
        if (route.domain_id == service_frame->domain_id &&
          service_frame->role == "request" &&
          route.role == "service" &&
          route.service_name == service_frame->service_name)
        {
          append_unique_peer(&targets, route.address);
        } else if (route.domain_id == service_frame->domain_id &&
          service_frame->role == "response" &&
          route.role == "client" &&
          route.endpoint_id == service_frame->client_endpoint_id)
        {
          append_unique_peer(&targets, route.address);
        }
      }
      for (const sockaddr_in & peer : targets) {
        if (endpoints_match(peer, source_address)) {
          continue;
        }
        const auto sent = ::sendto(
          fd,
          encoded_frame.data(),
          encoded_frame.size(),
          0,
          reinterpret_cast<const sockaddr *>(&peer),
          sizeof(peer));
        if (sent >= 0 && static_cast<size_t>(sent) == encoded_frame.size()) {
          ++service_forwarded;
        }
      }
      continue;
    }

    const auto action_frame = rmw_fleetqox_cpp::decode_action_frame(encoded_frame);
    if (action_frame) {
      ++action_frames;
      if (std::find(action_names.begin(), action_names.end(), action_frame->action_name) ==
        action_names.end())
      {
        action_names.push_back(action_frame->action_name);
      }
      std::vector<sockaddr_in> targets = peer_addresses;
      for (const ActionRoute & route : action_route_table) {
        if (route.domain_id == action_frame->domain_id &&
          action_role_targets_server(action_frame->role) &&
          route.role == "action_server" &&
          route.action_name == action_frame->action_name)
        {
          append_unique_peer(&targets, route.address);
        } else if (route.domain_id == action_frame->domain_id &&
          action_role_targets_client(action_frame->role) &&
          route.role == "action_client" &&
          (route.endpoint_id == action_frame->endpoint_id ||
          route.action_name == action_frame->action_name))
        {
          append_unique_peer(&targets, route.address);
        }
      }
      for (const sockaddr_in & peer : targets) {
        if (endpoints_match(peer, source_address)) {
          continue;
        }
        const auto sent = ::sendto(
          fd,
          encoded_frame.data(),
          encoded_frame.size(),
          0,
          reinterpret_cast<const sockaddr *>(&peer),
          sizeof(peer));
        if (sent >= 0 && static_cast<size_t>(sent) == encoded_frame.size()) {
          ++action_forwarded;
        }
      }
      continue;
    }

    const auto loss_notice =
      rmw_fleetqox_cpp::decode_unrecoverable_loss_notice(encoded_frame);
    if (loss_notice) {
      ++unrecoverable_loss_notice_frames;
      std::vector<sockaddr_in> targets;
      for (const TopicRoute & route : route_table) {
        if (route.domain_id == loss_notice->domain_id &&
          route.topic == loss_notice->topic)
        {
          append_unique_peer(&targets, route.address);
        }
      }
      for (const sockaddr_in & peer : targets) {
        if (endpoints_match(peer, source_address)) {
          continue;
        }
        const auto sent = ::sendto(
          fd,
          encoded_frame.data(),
          encoded_frame.size(),
          0,
          reinterpret_cast<const sockaddr *>(&peer),
          sizeof(peer));
        if (sent >= 0 && static_cast<size_t>(sent) == encoded_frame.size()) {
          ++unrecoverable_loss_notice_forwarded;
        }
      }
      continue;
    }

    const auto ack_nack = rmw_fleetqox_cpp::decode_ack_nack(encoded_frame);
    if (ack_nack) {
      ++ack_nack_frames;
      std::vector<sockaddr_in> targets = peer_addresses;
      for (const PublisherRoute & route : publisher_route_table) {
        if (route.domain_id == ack_nack->domain_id &&
          route.publisher_id == ack_nack->publisher_id)
        {
          append_unique_peer(&targets, route.address);
        }
      }
      for (const sockaddr_in & peer : targets) {
        if (endpoints_match(peer, source_address)) {
          continue;
        }
        const auto sent = ::sendto(
          fd,
          encoded_frame.data(),
          encoded_frame.size(),
          0,
          reinterpret_cast<const sockaddr *>(&peer),
          sizeof(peer));
        if (sent >= 0 && static_cast<size_t>(sent) == encoded_frame.size()) {
          ++ack_nack_forwarded;
        }
      }
      continue;
    }

    const auto decoded = rmw_fleetqox_cpp::decode_data_frame(encoded_frame);
    if (!decoded) {
      ++invalid;
      continue;
    }
    ++received;
    topics.push_back(decoded->topic);
    auto publisher_route = std::find_if(
      publisher_route_table.begin(),
      publisher_route_table.end(),
      [&](const PublisherRoute & route) {
        return route.publisher_id == decoded->publisher_id &&
               route.domain_id == decoded->domain_id &&
               endpoints_match(route.address, source_address);
      });
    if (publisher_route == publisher_route_table.end()) {
      publisher_route_table.push_back(
        PublisherRoute{
          decoded->publisher_id,
          decoded->topic,
          source_address,
          now + lease_duration(5000u),
          decoded->domain_id});
    } else {
      publisher_route->topic = decoded->topic;
      publisher_route->expires_at = now + lease_duration(5000u);
    }
    if (should_drop_source_sequence_once(config, *decoded, &dropped_source_sequence_keys)) {
      ++test_dropped;
      append_router_path_telemetry(config, *decoded, encoded_frame.size(), false);
      continue;
    }
    const bool test_repair = was_test_dropped(*decoded, dropped_source_sequence_keys);
    if (config.forward_delay_ms > 0) {
      std::this_thread::sleep_for(std::chrono::milliseconds(config.forward_delay_ms));
    }
    if (frame_exceeds_learned_lifespan(topic_qos_table, *decoded)) {
      ++qos_dropped;
      increment_topic_count(&qos_dropped_topic_counts, decoded->topic);
      append_router_path_telemetry(config, *decoded, encoded_frame.size(), false);
      continue;
    }

    const bool scheduler_topic_matches =
      config.scheduler_topic_prefix.empty() ||
      decoded->topic.rfind(config.scheduler_topic_prefix, 0) == 0;
    if (config.scheduler_window_ms > 0 && scheduler_topic_matches) {
      const std::int64_t deadline_ns = learned_deadline_ns(
        topic_qos_table, decoded->topic, decoded->domain_id);
      const bool urgent_deadline =
        config.scheduler_urgent_deadline_ms > 0 &&
        deadline_ns > 0 &&
        deadline_ns <= static_cast<std::int64_t>(config.scheduler_urgent_deadline_ms) * 1000000ll;
      if (urgent_deadline) {
        ++scheduler_urgent_frames;
        forward_scheduled_data_frame(
          QueuedDataFrame{
            encoded_frame,
            *decoded,
            source_address,
            std::chrono::steady_clock::now(),
            absolute_deadline_ns_for_frame(topic_qos_table, *decoded),
            next_queue_order++,
            test_repair});
        continue;
      }
      if (!scheduler_admits_holdback(config, encoded_frame.size(), &scheduler_admission_state)) {
        ++scheduler_admission_bypassed_frames;
        forward_scheduled_data_frame(
          QueuedDataFrame{
            encoded_frame,
            *decoded,
            source_address,
            std::chrono::steady_clock::now(),
            absolute_deadline_ns_for_frame(topic_qos_table, *decoded),
            next_queue_order++,
            test_repair});
        continue;
      }
      ++scheduler_queued_frames;
      queued_data_frames.push_back(
        QueuedDataFrame{
          encoded_frame,
          *decoded,
          source_address,
          std::chrono::steady_clock::now(),
          absolute_deadline_ns_for_frame(topic_qos_table, *decoded),
          next_queue_order++,
          test_repair});
      continue;
    }
    const int forwarded_now = forward_data_frame(
      fd,
      encoded_frame,
      *decoded,
      source_address,
      peer_addresses,
      route_table,
      &forwarded_topics);
    forwarded += forwarded_now;
    if (forwarded_now > 0) {
      record_topic_source_sequence(&forwarded_topic_source_sequences, *decoded);
    }
    append_router_path_telemetry(config, *decoded, encoded_frame.size(), forwarded_now > 0);
  }

  ::close(fd);
  const int expected_ack_nack_forwarded =
    config.expected_ack_nack_forwarded >= 0 ?
    config.expected_ack_nack_forwarded : config.expected_ack_nack_frames;
  const int expected_accounted_frames =
    config.expected_frames == 0 ? 0 : std::min(received, config.expected_frames);
  const bool observed_router_state =
    !peer_addresses.empty() || !graph_peer_addresses.empty() ||
    !route_table.empty() || !publisher_route_table.empty() ||
    !service_route_table.empty() || !action_route_table.empty() ||
    forwarded > 0 || service_forwarded > 0 || action_forwarded > 0 ||
    ack_nack_forwarded > 0 || graph_forwarded > 0;
  const bool ok = received >= config.expected_frames &&
                  service_frames >= config.expected_service_frames &&
                  action_frames >= config.expected_action_frames &&
                  ack_nack_frames >= config.expected_ack_nack_frames &&
                  qos_dropped >= config.expected_qos_drops &&
                  invalid == 0 &&
                  (config.expected_frames == 0 ||
                  forwarded + qos_dropped + test_dropped >= expected_accounted_frames) &&
                  service_forwarded >= config.expected_service_frames &&
                  action_forwarded >= config.expected_action_frames &&
                  ack_nack_forwarded >= expected_ack_nack_forwarded &&
                  route_advertisements >= config.expected_route_advertisements &&
                  graph_advertisements >= config.expected_graph_advertisements &&
                  topic_source_sequence_expectations_satisfied(
                    config.expected_forwarded_topic_source_sequences,
                    forwarded_topic_source_sequences) &&
                  observed_router_state;
  print_router_json(
    ok ? "ok" : "failed",
    config,
    received,
    service_frames,
    ack_nack_frames,
    forwarded,
    qos_dropped,
    test_dropped,
    service_forwarded,
    ack_nack_forwarded,
    invalid,
    route_advertisements,
    static_cast<int>(route_table.size()),
    graph_advertisements,
    graph_forwarded,
    graph_publishers,
    graph_subscriptions,
    graph_services,
    graph_clients,
    forwarded_topics,
    graph_topics,
    service_names,
    topics,
    forwarded_topic_source_sequences,
    action_frames,
    action_forwarded,
    graph_action_servers,
    graph_action_clients,
    action_names,
    qos_dropped_topic_counts,
    scheduler_queued_frames,
    scheduler_urgent_frames,
    scheduler_paced_frames,
    scheduler_drain_pacing_ms,
    scheduler_forwarded_frames,
    scheduler_admission_bypassed_frames,
    scheduler_deadline_misses,
    scheduler_fresh_deadline_misses,
    scheduler_repair_frames,
    scheduler_repair_deadline_misses,
    scheduler_forwarded_frames > 0 ?
    static_cast<double>(scheduler_queue_wait_ns_sum) /
    static_cast<double>(scheduler_forwarded_frames) / 1000000.0 : 0.0,
    static_cast<double>(scheduler_queue_wait_ns_max) / 1000000.0,
    scheduler_admission_state.service_ratio_max,
    scheduler_admission_state.service_ratio_ewma,
    scheduler_admission_state.samples,
    scheduler_admission_state.switches,
    scheduler_admission_state.holdback_decisions,
    scheduler_admission_state.bypass_decisions,
    scheduler_admission_state.holdback_enabled,
    robot_scheduler_stats,
    scheduler_deadline_miss_frames,
    unrecoverable_loss_notice_frames,
    unrecoverable_loss_notice_forwarded);
  return ok ? 0 : 1;
}
