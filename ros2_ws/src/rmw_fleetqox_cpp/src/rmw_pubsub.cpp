#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cctype>
#include <condition_variable>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <ctime>
#include <deque>
#include <cerrno>
#include <fstream>
#include <iomanip>
#include <limits>
#include <memory>
#include <mutex>
#include <new>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <tinyxml2.h>

#include <openssl/bio.h>
#include <openssl/buffer.h>
#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/pkcs7.h>
#include <openssl/pem.h>
#include <openssl/rand.h>
#include <openssl/x509.h>

#include "rmw_fleetqox_cpp/data_frame.hpp"
#include "rmw_fleetqox_cpp/message_allocation.hpp"
#include "rmw_fleetqox_cpp/quic_gateway_transport.hpp"
#include "rmw_fleetqox_cpp/shared_memory_transport.hpp"

#include "rcutils/allocator.h"
#include "rosidl_runtime_c/string.h"
#include "rosidl_runtime_c/string_functions.h"
#include "rosidl_typesupport_c/identifier.h"
#include "rosidl_typesupport_c/message_type_support_dispatch.h"
#include "rosidl_typesupport_cpp/identifier.hpp"
#include "rosidl_typesupport_cpp/message_type_support_dispatch.hpp"
#include "rosidl_typesupport_introspection_c/field_types.h"
#include "rosidl_typesupport_introspection_c/identifier.h"
#include "rosidl_typesupport_introspection_c/message_introspection.h"
#include "rosidl_typesupport_introspection_cpp/field_types.hpp"
#include "rosidl_typesupport_introspection_cpp/identifier.hpp"
#include "rosidl_typesupport_introspection_cpp/message_introspection.hpp"
#include "rmw/allocators.h"
#include "rmw/error_handling.h"
#include "rmw/event.h"
#include "rmw/events_statuses/events_statuses.h"
#include "rmw/get_topic_endpoint_info.h"
#include "rmw/rmw.h"
#include "rmw/serialized_message.h"
#include "rmw/subscription_content_filter_options.h"
#include "rmw/time.h"
#include "rmw_dds_common/qos.hpp"

struct rmw_context_impl_s
{
  bool is_shutdown;
  rcutils_allocator_t allocator;
};

extern "C" void rmw_fleetqox_cpp_graph_apply_remote_advertisement_with_info_in_domain(
  const char * action,
  const char * entity_kind,
  const char * node_name,
  const char * node_namespace,
  const char * topic_name,
  const char * type_name,
  const char * endpoint_id,
  const std::uint8_t * endpoint_gid,
  size_t endpoint_gid_size,
  const rmw_qos_profile_t * qos,
  std::uint64_t domain_id,
  std::uint64_t lease_ms);
extern "C" size_t rmw_fleetqox_cpp_graph_publisher_count(
  const char * topic_name, std::size_t domain_id);
extern "C" size_t rmw_fleetqox_cpp_graph_subscription_count(
  const char * topic_name, std::size_t domain_id);
extern "C" bool rmw_fleetqox_cpp_handle_service_frame(const char * encoded_frame, size_t size);
std::vector<std::string> rmw_fleetqox_cpp_graph_matched_subscription_endpoint_ids(
  std::size_t domain_id,
  const std::string & topic_name,
  const std::string & type_name,
  const rmw_qos_profile_t & publisher_qos);

namespace
{

constexpr const char * kIdentifier = "rmw_fleetqox_cpp";
constexpr const char * kTypeErasedTypeSupportIdentifier = "rmw_fleetqox_cpp_type_erased_probe";
constexpr std::uint32_t kTypeErasedDescriptorSchemaVersion = 1;

struct FleetQoxPublisherData
{
  rcutils_allocator_t allocator;
  rmw_context_t * context;
  const rmw_node_t * owner_node;
  std::string topic_name;
  std::string type_name;
  std::string node_name;
  std::string node_namespace;
  std::string enclave;
  std::size_t domain_id;
  std::string publisher_id;
  std::string endpoint_id;
  std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> endpoint_gid;
  rmw_qos_profile_t qos;
  const rosidl_message_type_support_t * type_support;
  size_t typed_message_size;
  std::uint64_t next_source_sequence;
  std::int64_t last_graph_advertisement_ns;
  std::int64_t last_publish_ns;
  std::int64_t last_liveliness_assert_ns;
  bool liveliness_alive;
  std::int32_t liveliness_lost_total_count;
  std::int32_t liveliness_lost_total_count_change;
  rmw_event_callback_t liveliness_lost_callback;
  const void * liveliness_lost_user_data;
  std::int32_t offered_deadline_total_count;
  std::int32_t offered_deadline_unread_count;
  rmw_event_callback_t offered_deadline_callback;
  const void * offered_deadline_user_data;
  std::int32_t offered_incompatible_qos_total_count;
  std::int32_t offered_incompatible_qos_total_count_change;
  rmw_qos_policy_kind_t offered_incompatible_qos_last_policy_kind;
  rmw_event_callback_t offered_incompatible_qos_callback;
  const void * offered_incompatible_qos_user_data;
  std::int32_t publisher_incompatible_type_total_count;
  std::int32_t publisher_incompatible_type_total_count_change;
  rmw_event_callback_t publisher_incompatible_type_callback;
  const void * publisher_incompatible_type_user_data;
  size_t publication_matched_total_count;
  size_t publication_matched_total_count_change;
  size_t publication_matched_current_count;
  std::int32_t publication_matched_current_count_change;
  rmw_event_callback_t publication_matched_callback;
  const void * publication_matched_user_data;
  bool destroying{false};
  size_t inflight_callbacks{0};
  std::mutex publish_mutex{};
};

struct FleetQoxSubscriptionData
{
  rcutils_allocator_t allocator;
  rmw_context_t * context;
  const rmw_node_t * owner_node;
  std::string topic_name;
  std::string type_name;
  std::string node_name;
  std::string node_namespace;
  std::string enclave;
  std::size_t domain_id;
  std::string subscription_id;
  std::string endpoint_id;
  std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> endpoint_gid;
  const rosidl_message_type_support_t * type_support;
  size_t typed_message_size;
  rmw_qos_profile_t qos;
  std::deque<std::string> frame_queue;
  std::unordered_map<std::string, rmw_fleetqox_cpp::SequenceState> sequence_states;
  rmw_event_callback_t on_new_message_callback;
  const void * on_new_message_user_data;
  std::string content_filter_expression;
  std::vector<std::string> content_filter_parameters;
  std::int64_t last_received_ns;
  std::unordered_set<std::string> liveliness_alive_publishers;
  std::unordered_set<std::string> liveliness_not_alive_publishers;
  std::int32_t liveliness_alive_count_change;
  std::int32_t liveliness_not_alive_count_change;
  rmw_event_callback_t liveliness_changed_callback;
  const void * liveliness_changed_user_data;
  std::int32_t requested_deadline_total_count;
  std::int32_t requested_deadline_unread_count;
  rmw_event_callback_t requested_deadline_callback;
  const void * requested_deadline_user_data;
  std::int32_t requested_incompatible_qos_total_count;
  std::int32_t requested_incompatible_qos_total_count_change;
  rmw_qos_policy_kind_t requested_incompatible_qos_last_policy_kind;
  rmw_event_callback_t requested_incompatible_qos_callback;
  const void * requested_incompatible_qos_user_data;
  std::int32_t subscription_incompatible_type_total_count;
  std::int32_t subscription_incompatible_type_total_count_change;
  rmw_event_callback_t subscription_incompatible_type_callback;
  const void * subscription_incompatible_type_user_data;
  size_t message_lost_total_count;
  size_t message_lost_total_count_change;
  rmw_event_callback_t message_lost_callback;
  const void * message_lost_user_data;
  size_t subscription_matched_total_count;
  size_t subscription_matched_total_count_change;
  size_t subscription_matched_current_count;
  std::int32_t subscription_matched_current_count_change;
  rmw_event_callback_t subscription_matched_callback;
  const void * subscription_matched_user_data;
  std::uint64_t next_reception_sequence{1};
  bool destroying{false};
  size_t inflight_callbacks{0};
  std::recursive_mutex take_mutex{};
};

struct ReliableRetransmitEntry
{
  std::string encoded_frame;
  rmw_qos_profile_t qos;
  std::string publisher_id;
  std::size_t domain_id;
  std::uint64_t source_sequence_number;
  std::int64_t source_timestamp_ns;
  std::int64_t last_send_ns;
  std::uint64_t timeout_retransmissions;
  bool reliable;
  bool acknowledged;
  size_t expected_acknowledgments{0};
  size_t acknowledgments_observed{0};
  std::unordered_set<std::string> pending_subscriber_ids{};
  bool fragment_observed_by_reader{false};
  bool fragment_timeout_suppression_recorded{false};
  size_t fragment_initial_send_batches_pending{0};
  bool fragment_initial_pending_suppression_recorded{false};
  bool fragment_fallback_grace_deferral_recorded{false};
};

struct RemotePubSubEndpoint
{
  bool publisher{false};
  std::uint64_t domain_id{0};
  std::string topic_name;
  std::string type_name;
  std::string endpoint_id;
  rmw_qos_profile_t qos{rmw_qos_profile_default};
  std::int64_t expires_at_ns{0};
  std::int64_t last_liveliness_assert_ns{0};
  bool liveliness_alive{false};
};

struct FleetQoxTypeErasedMessageDescriptor
{
  std::uint32_t schema_version;
  size_t message_size;
};

enum class LoanOwnerKind
{
  Publisher,
  Subscription,
};

struct LoanRecord
{
  const void * owner;
  LoanOwnerKind owner_kind;
  rcutils_allocator_t allocator;
  const rosidl_typesupport_introspection_c__MessageMembers * c_members;
  const rosidl_typesupport_introspection_cpp::MessageMembers * cpp_members;
};

struct EventCallbackNotification
{
  rmw_event_callback_t callback;
  const void * user_data;
  size_t event_count;
  FleetQoxPublisherData * publisher_owner{nullptr};
  FleetQoxSubscriptionData * subscription_owner{nullptr};
};

std::mutex g_bus_mutex;
std::condition_variable g_all_acked_condition;
std::condition_variable g_entity_callback_condition;
std::vector<FleetQoxPublisherData *> g_publishers;
std::vector<FleetQoxSubscriptionData *> g_subscriptions;
std::vector<rmw_subscription_t *> g_subscription_handles;
std::unordered_map<std::string, ReliableRetransmitEntry> g_retransmit_ledger;
std::unordered_map<std::string, RemotePubSubEndpoint> g_remote_pubsub_endpoints;
std::atomic<std::uint64_t> g_next_publisher_id{1};
std::atomic<std::uint64_t> g_next_subscription_id{1};
std::atomic<bool> g_pubsub_graph_renewal_started{false};
std::atomic<bool> g_pubsub_graph_renewal_running{false};
std::atomic<bool> g_reliable_retransmit_started{false};
std::atomic<bool> g_reliable_retransmit_running{false};
std::atomic<bool> g_qos_deadline_monitor_started{false};
std::atomic<bool> g_qos_deadline_monitor_running{false};
std::mutex g_reliable_retransmit_lifecycle_mutex;
std::mutex g_qos_deadline_monitor_lifecycle_mutex;
std::mutex g_pubsub_graph_renewal_lifecycle_mutex;
std::thread g_reliable_retransmit_thread;
std::thread g_qos_deadline_monitor_thread;
std::thread g_pubsub_graph_renewal_thread;
std::once_flag g_reliable_retransmit_atexit_once;
std::once_flag g_qos_deadline_monitor_atexit_once;
std::once_flag g_pubsub_graph_renewal_atexit_once;

std::mutex g_last_take_mutex;
std::string g_last_take_topic;
std::string g_last_take_publisher_id;
std::uint64_t g_last_take_source_sequence{0};
std::int64_t g_last_take_source_timestamp_ns{0};
std::int64_t g_last_take_timestamp_ns{0};
thread_local rmw_message_info_t * g_typed_take_message_info{nullptr};
std::atomic<std::uint64_t> g_duplicate_data_frames_deduped{0};
std::atomic<std::uint64_t> g_out_of_order_data_frames_observed{0};
std::atomic<std::uint64_t> g_idle_repair_ack_nack_sent{0};
std::atomic<std::uint64_t> g_reliable_timeout_retransmissions{0};
std::atomic<std::uint64_t> g_fragment_observed_timeout_retransmissions_suppressed{0};
std::atomic<std::uint64_t> g_fragment_whole_fallback_pacing_deferrals{0};
std::atomic<std::uint64_t> g_fragment_async_send_completions{0};
std::atomic<std::uint64_t> g_fragment_initial_pending_timeout_suppressions{0};
std::atomic<std::uint64_t> g_fragment_whole_fallback_grace_deferrals{0};
std::atomic<std::uint64_t> g_unrecoverable_loss_samples_reported{0};
std::atomic<std::uint64_t> g_wait_for_all_acked_calls{0};
std::atomic<std::uint64_t> g_wait_for_all_acked_successes{0};
std::atomic<std::uint64_t> g_wait_for_all_acked_timeouts{0};
std::atomic<std::uint64_t> g_last_wait_for_all_acked_expected{0};
std::atomic<std::uint64_t> g_last_wait_for_all_acked_observed{0};
std::atomic<std::uint64_t> g_remote_graph_event_advertisements_received{0};
std::atomic<std::uint64_t> g_remote_graph_event_endpoint_adds{0};
std::atomic<std::uint64_t> g_remote_graph_event_endpoint_renewals{0};
std::atomic<std::uint64_t> g_remote_graph_event_endpoint_removes{0};
std::atomic<std::uint64_t> g_remote_graph_event_endpoint_expiries{0};
std::atomic<std::uint64_t> g_remote_manual_liveliness_assertions_received{0};
std::atomic<std::uint64_t> g_remote_manual_liveliness_expiries{0};
std::atomic<std::uint64_t> g_remote_manual_liveliness_reassertions{0};
std::atomic<std::uint64_t> g_content_filters_set{0};
std::atomic<std::uint64_t> g_content_filters_got{0};
std::atomic<std::uint64_t> g_content_filters_evaluated{0};
std::atomic<std::uint64_t> g_content_filters_matched{0};
std::atomic<std::uint64_t> g_content_filters_dropped{0};
std::atomic<std::uint64_t> g_content_filter_typed_reflections{0};
std::atomic<std::uint64_t> g_security_policy_denied{0};
std::atomic<std::uint64_t> g_sros2_permissions_xml_allowed{0};
std::atomic<std::uint64_t> g_sros2_permissions_xml_denied{0};
std::atomic<std::uint64_t> g_sros2_permissions_xml_parse_errors{0};
std::atomic<std::uint64_t> g_sros2_permissions_xml_subscribe_allowed{0};
std::atomic<std::uint64_t> g_sros2_permissions_xml_subscribe_denied{0};
std::mutex g_loan_mutex;
std::unordered_map<void *, LoanRecord> g_loans;

void enqueue_received_frame(const std::string & encoded_frame);
bool apply_received_graph_advertisement(const std::string & encoded_frame);
bool handle_ack_nack_feedback(const std::string & encoded_frame);
bool handle_unrecoverable_loss_notice(const std::string & encoded_frame);
void record_fragment_repair_observation(const std::string & encoded_frame);
void record_fragment_async_send_started(const std::string & encoded_frame);
void record_fragment_async_send_complete(const std::string & encoded_frame);
void record_fragment_async_send_failed(const std::string & encoded_frame);
int loss_resilient_fragment_chunk_bytes();
void record_subscription_message_lost_locked(
  FleetQoxSubscriptionData * data,
  size_t count,
  std::vector<EventCallbackNotification> * callbacks);
std::string retransmit_ledger_key(const std::string & publisher_id, std::uint64_t sequence);
std::int64_t monotonic_timestamp_ns();
const rosidl_typesupport_introspection_c__MessageMembers * introspection_c_members(
  const rosidl_message_type_support_t * type_support);
const rosidl_typesupport_introspection_cpp::MessageMembers * introspection_cpp_members(
  const rosidl_message_type_support_t * type_support);
const rosidl_message_type_support_t * resolve_effective_type_support(
  const rosidl_message_type_support_t * type_support);
std::string type_name_from_type_support(const rosidl_message_type_support_t * type_support);

void fini_loan(void * message, const LoanRecord & record)
{
  if (record.c_members != nullptr && record.c_members->fini_function != nullptr) {
    record.c_members->fini_function(message);
  } else if (record.cpp_members != nullptr && record.cpp_members->fini_function != nullptr) {
    record.cpp_members->fini_function(message);
  }
  record.allocator.deallocate(message, record.allocator.state);
}

rmw_ret_t borrow_loan(
  const rosidl_message_type_support_t * type_support,
  size_t typed_message_size,
  const std::string & expected_type_name,
  rcutils_allocator_t allocator,
  const void * owner,
  LoanOwnerKind owner_kind,
  void ** ros_message)
{
  if (type_support == nullptr || ros_message == nullptr || *ros_message != nullptr ||
    owner == nullptr || !rcutils_allocator_is_valid(&allocator))
  {
    RMW_SET_ERROR_MSG("invalid loaned message allocation arguments");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const auto * effective = resolve_effective_type_support(type_support);
  if (type_name_from_type_support(effective) != expected_type_name) {
    RMW_SET_ERROR_MSG("loaned message type support does not match endpoint type");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const auto * c_members = introspection_c_members(effective);
  const auto * cpp_members = introspection_cpp_members(effective);
  const size_t message_size = c_members != nullptr ? c_members->size_of_ :
    (cpp_members != nullptr ? cpp_members->size_of_ : typed_message_size);
  if (message_size == 0) {
    RMW_SET_ERROR_MSG("loaned message requires introspection C/C++ or a sized type-erased descriptor");
    return RMW_RET_UNSUPPORTED;
  }
  void * message = allocator.allocate(message_size, allocator.state);
  if (message == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate loaned message");
    return RMW_RET_BAD_ALLOC;
  }
  std::memset(message, 0, message_size);
  if (c_members != nullptr && c_members->init_function != nullptr) {
    c_members->init_function(message, ROSIDL_RUNTIME_C_MSG_INIT_ALL);
  } else if (cpp_members != nullptr && cpp_members->init_function != nullptr) {
    cpp_members->init_function(message, rosidl_runtime_cpp::MessageInitialization::ALL);
  }
  {
    std::lock_guard<std::mutex> lock(g_loan_mutex);
    g_loans.emplace(
      message,
      LoanRecord{owner, owner_kind, allocator, c_members, cpp_members});
  }
  *ros_message = message;
  return RMW_RET_OK;
}

rmw_ret_t release_loan(const void * owner, LoanOwnerKind owner_kind, void * ros_message)
{
  if (owner == nullptr || ros_message == nullptr) {
    RMW_SET_ERROR_MSG("loaned message owner and pointer must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  LoanRecord record{};
  {
    std::lock_guard<std::mutex> lock(g_loan_mutex);
    const auto found = g_loans.find(ros_message);
    if (found == g_loans.end() || found->second.owner != owner ||
      found->second.owner_kind != owner_kind)
    {
      RMW_SET_ERROR_MSG("loaned message is not owned by this endpoint");
      return RMW_RET_INVALID_ARGUMENT;
    }
    record = found->second;
    g_loans.erase(found);
  }
  fini_loan(ros_message, record);
  return RMW_RET_OK;
}

void release_owner_loans(const void * owner, LoanOwnerKind owner_kind)
{
  std::vector<std::pair<void *, LoanRecord>> loans;
  {
    std::lock_guard<std::mutex> lock(g_loan_mutex);
    for (auto it = g_loans.begin(); it != g_loans.end();) {
      if (it->second.owner == owner && it->second.owner_kind == owner_kind) {
        loans.push_back(*it);
        it = g_loans.erase(it);
      } else {
        ++it;
      }
    }
  }
  for (const auto & loan : loans) {
    fini_loan(loan.first, loan.second);
  }
}

void merge_confirmed_lost_range(
  rmw_fleetqox_cpp::SequenceState * state,
  std::uint64_t first,
  std::uint64_t last)
{
  if (state == nullptr || first == 0 || first > last) {
    return;
  }
  state->confirmed_lost_ranges.emplace_back(first, last);
  std::sort(state->confirmed_lost_ranges.begin(), state->confirmed_lost_ranges.end());
  std::vector<std::pair<std::uint64_t, std::uint64_t>> merged;
  merged.reserve(state->confirmed_lost_ranges.size());
  for (const auto & range : state->confirmed_lost_ranges) {
    if (merged.empty()) {
      merged.push_back(range);
      continue;
    }
    auto & previous = merged.back();
    const bool adjacent = previous.second != std::numeric_limits<std::uint64_t>::max() &&
      range.first == previous.second + 1;
    if (range.first <= previous.second || adjacent) {
      previous.second = std::max(previous.second, range.second);
    } else {
      merged.push_back(range);
    }
  }
  state->confirmed_lost_ranges = std::move(merged);
}

size_t confirmed_lost_range_count(
  const std::pair<std::uint64_t, std::uint64_t> & range)
{
  if (range.first == 0 || range.first > range.second) {
    return 0;
  }
  const std::uint64_t span = range.second - range.first;
  if (span >= static_cast<std::uint64_t>(std::numeric_limits<size_t>::max())) {
    return std::numeric_limits<size_t>::max();
  }
  return static_cast<size_t>(span + 1);
}

size_t apply_confirmed_lost_ranges_locked(
  rmw_fleetqox_cpp::SequenceState * state,
  const std::vector<std::pair<std::uint64_t, std::uint64_t>> & ranges)
{
  if (state == nullptr || !state->initialized) {
    return 0;
  }
  for (const auto & range : ranges) {
    merge_confirmed_lost_range(state, range.first, range.second);
  }

  size_t lost_count = 0;
  while (true) {
    while (state->highest_contiguous_sequence !=
      std::numeric_limits<std::uint64_t>::max() &&
      state->observed_sequences.find(state->highest_contiguous_sequence + 1) !=
      state->observed_sequences.end())
    {
      ++state->highest_contiguous_sequence;
    }
    if (state->confirmed_lost_ranges.empty()) {
      break;
    }
    auto & first = state->confirmed_lost_ranges.front();
    if (first.second <= state->highest_contiguous_sequence) {
      state->confirmed_lost_ranges.erase(state->confirmed_lost_ranges.begin());
      continue;
    }
    if (first.first <= state->highest_contiguous_sequence) {
      if (state->highest_contiguous_sequence ==
        std::numeric_limits<std::uint64_t>::max())
      {
        state->confirmed_lost_ranges.clear();
        break;
      }
      first.first = state->highest_contiguous_sequence + 1;
    }
    if (state->highest_contiguous_sequence ==
      std::numeric_limits<std::uint64_t>::max() ||
      first.first != state->highest_contiguous_sequence + 1)
    {
      break;
    }
    const size_t range_count = confirmed_lost_range_count(first);
    lost_count = std::numeric_limits<size_t>::max() - lost_count < range_count ?
      std::numeric_limits<size_t>::max() : lost_count + range_count;
    state->highest_contiguous_sequence = first.second;
    state->confirmed_lost_ranges.erase(state->confirmed_lost_ranges.begin());
  }
  return lost_count;
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

std::string endpoint_to_string(const sockaddr_in & address)
{
  char host[INET_ADDRSTRLEN] = {};
  if (::inet_ntop(AF_INET, &address.sin_addr, host, sizeof(host)) == nullptr) {
    return "unknown:0";
  }
  return std::string(host) + ":" + std::to_string(ntohs(address.sin_port));
}

bool endpoints_match(const sockaddr_in & left, const sockaddr_in & right)
{
  return left.sin_family == right.sin_family &&
         left.sin_addr.s_addr == right.sin_addr.s_addr &&
         left.sin_port == right.sin_port;
}

std::string trim_copy(const std::string & text)
{
  size_t begin = 0;
  while (begin < text.size() && std::isspace(static_cast<unsigned char>(text[begin]))) {
    ++begin;
  }
  size_t end = text.size();
  while (end > begin && std::isspace(static_cast<unsigned char>(text[end - 1]))) {
    --end;
  }
  return text.substr(begin, end - begin);
}

std::vector<std::string> split_nonempty(const std::string & text, char delimiter)
{
  std::vector<std::string> values;
  size_t start = 0;
  while (start <= text.size()) {
    const size_t found = text.find(delimiter, start);
    const std::string item = trim_copy(text.substr(
      start,
      found == std::string::npos ? std::string::npos : found - start));
    if (!item.empty()) {
      values.push_back(item);
    }
    if (found == std::string::npos) {
      break;
    }
    start = found + 1;
  }
  return values;
}

struct FleetQoxSecurityPolicy
{
  bool configured = false;
  bool publish_allow_configured = false;
  std::unordered_set<std::string> publish_allow;
  std::unordered_set<std::string> publish_deny;
};

struct Sros2DomainRange
{
  std::uint32_t minimum = 0;
  std::uint32_t maximum = 0;
};

struct Sros2AccessRule
{
  bool allow = false;
  std::vector<Sros2DomainRange> domains;
  std::vector<std::string> publish_topic_expressions;
  std::vector<std::string> subscribe_topic_expressions;
  bool publish_unsupported_criteria = false;
  bool subscribe_unsupported_criteria = false;
};

struct Sros2Grant
{
  std::string name;
  std::string subject_name;
  std::int64_t not_before_s = 0;
  std::int64_t not_after_s = 0;
  std::vector<Sros2AccessRule> rules;
  bool default_allow = false;
};

struct Sros2PermissionsPolicy
{
  bool configured = false;
  bool valid = false;
  bool signed_source = false;
  bool runtime_signature_verified = false;
  std::string path;
  std::string permissions_ca_path;
  std::string error;
  std::vector<Sros2Grant> grants;
};

struct Sros2GovernanceTopicRule
{
  std::string topic_expression;
  bool enable_discovery_protection = false;
  bool enable_liveliness_protection = false;
  bool enable_read_access_control = false;
  bool enable_write_access_control = false;
  std::string metadata_protection_kind;
  std::string data_protection_kind;
};

struct Sros2GovernanceDomainRule
{
  std::vector<Sros2DomainRange> domains;
  bool allow_unauthenticated_participants = false;
  bool enable_join_access_control = false;
  std::string discovery_protection_kind;
  std::string liveliness_protection_kind;
  std::string rtps_protection_kind;
  std::vector<Sros2GovernanceTopicRule> topic_rules;
};

struct Sros2GovernancePolicy
{
  bool configured = false;
  bool valid = false;
  bool signed_source = false;
  bool runtime_signature_verified = false;
  std::string path;
  std::string governance_ca_path;
  std::string error;
  std::vector<Sros2GovernanceDomainRule> domain_rules;
};

struct Sros2IdentityCredentials
{
  bool configured = false;
  bool valid = false;
  bool certificate_chain_verified = false;
  bool private_key_matches = false;
  std::string certificate_path;
  std::string private_key_path;
  std::string identity_ca_path;
  std::string subject_common_name;
  std::string error;
};

enum class SecurityDecision
{
  not_configured,
  allow,
  deny,
  invalid,
};

enum class Sros2Operation
{
  publish,
  subscribe,
};

enum class Sros2GovernanceDecision
{
  not_configured = 0,
  allow_without_access_control = 1,
  require_access_control = 2,
  participant_authentication_required = 3,
  transport_protection_required = 4,
  no_matching_rule = 5,
  invalid = 6,
};

bool topic_set_contains(const std::unordered_set<std::string> & topics, const std::string & topic)
{
  return topics.find("*") != topics.end() || topics.find(topic) != topics.end();
}

bool parse_fixed_decimal(
  const std::string & text, size_t offset, size_t width, int * value)
{
  if (value == nullptr || offset + width > text.size() || width == 0) {
    return false;
  }
  int parsed = 0;
  for (size_t index = offset; index < offset + width; ++index) {
    const unsigned char c = static_cast<unsigned char>(text[index]);
    if (!std::isdigit(c)) {
      return false;
    }
    parsed = parsed * 10 + static_cast<int>(c - static_cast<unsigned char>('0'));
  }
  *value = parsed;
  return true;
}

bool is_leap_year(int year)
{
  return (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
}

int days_in_month(int year, int month)
{
  static constexpr std::array<int, 12> kDays{
    31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  if (month < 1 || month > 12) {
    return 0;
  }
  return month == 2 && is_leap_year(year) ? 29 : kDays[static_cast<size_t>(month - 1)];
}

std::int64_t days_from_civil(int year, unsigned month, unsigned day)
{
  year -= month <= 2;
  const int era = (year >= 0 ? year : year - 399) / 400;
  const unsigned year_of_era = static_cast<unsigned>(year - era * 400);
  const int adjusted_month = static_cast<int>(month) + (month > 2 ? -3 : 9);
  const unsigned day_of_year =
    static_cast<unsigned>((153 * adjusted_month + 2) / 5) + day - 1;
  const unsigned day_of_era =
    year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
  return static_cast<std::int64_t>(era) * 146097 + static_cast<std::int64_t>(day_of_era) -
         719468;
}

std::optional<std::int64_t> parse_iso8601_epoch_seconds(const std::string & raw_text)
{
  const std::string text = trim_copy(raw_text);
  if (text.size() < 19 || (text[10] != 'T' && text[10] != ' ')) {
    return std::nullopt;
  }
  int year = 0;
  int month = 0;
  int day = 0;
  int hour = 0;
  int minute = 0;
  int second = 0;
  if (!parse_fixed_decimal(text, 0, 4, &year) || text[4] != '-' ||
    !parse_fixed_decimal(text, 5, 2, &month) || text[7] != '-' ||
    !parse_fixed_decimal(text, 8, 2, &day) ||
    !parse_fixed_decimal(text, 11, 2, &hour) || text[13] != ':' ||
    !parse_fixed_decimal(text, 14, 2, &minute) || text[16] != ':' ||
    !parse_fixed_decimal(text, 17, 2, &second))
  {
    return std::nullopt;
  }
  if (day < 1 || day > days_in_month(year, month) || hour > 23 || minute > 59 || second > 60) {
    return std::nullopt;
  }

  size_t cursor = 19;
  if (cursor < text.size() && text[cursor] == '.') {
    ++cursor;
    const size_t fraction_begin = cursor;
    while (cursor < text.size() && std::isdigit(static_cast<unsigned char>(text[cursor]))) {
      ++cursor;
    }
    if (cursor == fraction_begin) {
      return std::nullopt;
    }
  }

  int timezone_offset_s = 0;
  if (cursor < text.size() && (text[cursor] == 'Z' || text[cursor] == 'z')) {
    ++cursor;
  } else if (cursor < text.size() && (text[cursor] == '+' || text[cursor] == '-')) {
    const int sign = text[cursor] == '+' ? 1 : -1;
    ++cursor;
    int timezone_hour = 0;
    int timezone_minute = 0;
    if (!parse_fixed_decimal(text, cursor, 2, &timezone_hour)) {
      return std::nullopt;
    }
    cursor += 2;
    if (cursor < text.size() && text[cursor] == ':') {
      ++cursor;
    }
    if (!parse_fixed_decimal(text, cursor, 2, &timezone_minute)) {
      return std::nullopt;
    }
    cursor += 2;
    if (timezone_hour > 23 || timezone_minute > 59) {
      return std::nullopt;
    }
    timezone_offset_s = sign * (timezone_hour * 3600 + timezone_minute * 60);
  }
  if (cursor != text.size()) {
    return std::nullopt;
  }

  const std::int64_t days = days_from_civil(
    year, static_cast<unsigned>(month), static_cast<unsigned>(day));
  return days * 86400 + hour * 3600 + minute * 60 + second - timezone_offset_s;
}

bool parse_uint32_element(const tinyxml2::XMLElement * element, std::uint32_t * value)
{
  if (element == nullptr || value == nullptr || element->GetText() == nullptr) {
    return false;
  }
  const std::string text = trim_copy(element->GetText());
  if (text.empty() ||
    !std::all_of(text.begin(), text.end(), [](unsigned char c) {return std::isdigit(c);}))
  {
    return false;
  }
  errno = 0;
  char * end = nullptr;
  const unsigned long parsed = std::strtoul(text.c_str(), &end, 10);
  if (errno != 0 || end == nullptr || *end != '\0' ||
    parsed > std::numeric_limits<std::uint32_t>::max())
  {
    return false;
  }
  *value = static_cast<std::uint32_t>(parsed);
  return true;
}

bool parse_sros2_domains(
  const tinyxml2::XMLElement * domains_element,
  std::vector<Sros2DomainRange> * domains)
{
  if (domains_element == nullptr || domains == nullptr) {
    return false;
  }
  for (const tinyxml2::XMLElement * child = domains_element->FirstChildElement();
    child != nullptr; child = child->NextSiblingElement())
  {
    const std::string name = child->Name() == nullptr ? "" : child->Name();
    if (name == "id") {
      std::uint32_t id = 0;
      if (!parse_uint32_element(child, &id)) {
        return false;
      }
      domains->push_back(Sros2DomainRange{id, id});
    } else if (name == "id_range") {
      const tinyxml2::XMLElement * minimum_element = child->FirstChildElement("min");
      const tinyxml2::XMLElement * maximum_element = child->FirstChildElement("max");
      std::uint32_t minimum = 0;
      std::uint32_t maximum = std::numeric_limits<std::uint32_t>::max();
      if (minimum_element == nullptr && maximum_element == nullptr) {
        return false;
      }
      if (minimum_element != nullptr && !parse_uint32_element(minimum_element, &minimum)) {
        return false;
      }
      if (maximum_element != nullptr && !parse_uint32_element(maximum_element, &maximum)) {
        return false;
      }
      if (minimum > maximum) {
        return false;
      }
      domains->push_back(Sros2DomainRange{minimum, maximum});
    } else {
      return false;
    }
  }
  return !domains->empty();
}

bool parse_sros2_rule_criteria(
  const tinyxml2::XMLElement * rule_element,
  const char * operation_name,
  std::vector<std::string> * topic_expressions,
  bool * unsupported_criteria)
{
  if (rule_element == nullptr || operation_name == nullptr ||
    topic_expressions == nullptr || unsupported_criteria == nullptr)
  {
    return false;
  }
  for (const tinyxml2::XMLElement * operation =
    rule_element->FirstChildElement(operation_name);
    operation != nullptr; operation = operation->NextSiblingElement(operation_name))
  {
    if (operation->FirstChildElement("partitions") != nullptr ||
      operation->FirstChildElement("data_tags") != nullptr)
    {
      *unsupported_criteria = true;
    }
    const tinyxml2::XMLElement * topics = operation->FirstChildElement("topics");
    if (topics == nullptr) {
      return false;
    }
    for (const tinyxml2::XMLElement * topic = topics->FirstChildElement("topic");
      topic != nullptr; topic = topic->NextSiblingElement("topic"))
    {
      if (topic->GetText() != nullptr) {
        const std::string expression = trim_copy(topic->GetText());
        if (!expression.empty()) {
          topic_expressions->push_back(expression);
        }
      }
    }
  }
  return true;
}

bool parse_sros2_access_rule(
  const tinyxml2::XMLElement * rule_element,
  bool allow,
  Sros2AccessRule * rule)
{
  if (rule_element == nullptr || rule == nullptr) {
    return false;
  }
  rule->allow = allow;
  if (!parse_sros2_domains(rule_element->FirstChildElement("domains"), &rule->domains)) {
    return false;
  }
  return parse_sros2_rule_criteria(
    rule_element, "publish", &rule->publish_topic_expressions,
    &rule->publish_unsupported_criteria) &&
    parse_sros2_rule_criteria(
    rule_element, "subscribe", &rule->subscribe_topic_expressions,
    &rule->subscribe_unsupported_criteria);
}

std::string openssl_error_text(const std::string & prefix)
{
  const unsigned long error_code = ERR_get_error();
  if (error_code == 0) {
    return prefix;
  }
  std::array<char, 256> buffer{};
  ERR_error_string_n(error_code, buffer.data(), buffer.size());
  return prefix + ":" + buffer.data();
}

bool verify_sros2_signed_permissions(
  const std::string & signed_permissions_path,
  const std::string & permissions_ca_path,
  std::string * verified_xml,
  std::string * error)
{
  if (verified_xml == nullptr || error == nullptr) {
    return false;
  }
  verified_xml->clear();
  error->clear();
  ERR_clear_error();

  using BioPointer = std::unique_ptr<BIO, decltype(&BIO_free)>;
  using Pkcs7Pointer = std::unique_ptr<PKCS7, decltype(&PKCS7_free)>;
  using StorePointer = std::unique_ptr<X509_STORE, decltype(&X509_STORE_free)>;

  BioPointer input(BIO_new_file(signed_permissions_path.c_str(), "rb"), BIO_free);
  if (!input) {
    *error = openssl_error_text("permissions_p7s_open_failed");
    return false;
  }
  BIO * detached_content_raw = nullptr;
  Pkcs7Pointer signed_message(
    SMIME_read_PKCS7(input.get(), &detached_content_raw), PKCS7_free);
  BioPointer detached_content(detached_content_raw, BIO_free);
  if (!signed_message) {
    *error = openssl_error_text("permissions_p7s_parse_failed");
    return false;
  }
  StorePointer trust_store(X509_STORE_new(), X509_STORE_free);
  if (!trust_store) {
    *error = openssl_error_text("permissions_ca_store_allocation_failed");
    return false;
  }
  if (X509_STORE_load_locations(trust_store.get(), permissions_ca_path.c_str(), nullptr) != 1) {
    *error = openssl_error_text("permissions_ca_load_failed");
    return false;
  }
  BioPointer verified_content(BIO_new(BIO_s_mem()), BIO_free);
  if (!verified_content) {
    *error = openssl_error_text("permissions_verified_content_allocation_failed");
    return false;
  }
  if (PKCS7_verify(
      signed_message.get(), nullptr, trust_store.get(), detached_content.get(),
      verified_content.get(), 0) != 1)
  {
    *error = openssl_error_text("permissions_p7s_verify_failed");
    return false;
  }

  BUF_MEM * verified_buffer = nullptr;
  BIO_get_mem_ptr(verified_content.get(), &verified_buffer);
  if (verified_buffer == nullptr || verified_buffer->data == nullptr || verified_buffer->length == 0) {
    *error = "permissions_p7s_verified_content_empty";
    return false;
  }
  std::string content(verified_buffer->data, verified_buffer->length);
  const size_t xml_begin = content.find("<dds");
  if (xml_begin == std::string::npos) {
    *error = "permissions_p7s_verified_content_missing_dds_xml";
    return false;
  }
  *verified_xml = content.substr(xml_begin);
  return true;
}

Sros2PermissionsPolicy parse_sros2_permissions_policy()
{
  Sros2PermissionsPolicy policy;
  const char * path_env = std::getenv("FLEETQOX_RMW_SROS2_PERMISSIONS_FILE");
  const char * signed_path_env =
    std::getenv("FLEETQOX_RMW_SROS2_PERMISSIONS_P7S_FILE");
  const char * permissions_ca_env =
    std::getenv("FLEETQOX_RMW_SROS2_PERMISSIONS_CA_FILE");
  const bool signed_source_configured =
    signed_path_env != nullptr && signed_path_env[0] != '\0';
  const bool xml_source_configured = path_env != nullptr && path_env[0] != '\0';
  if (!signed_source_configured && !xml_source_configured) {
    return policy;
  }
  policy.configured = true;
  policy.signed_source = signed_source_configured;
  policy.path = signed_source_configured ? signed_path_env : path_env;
  tinyxml2::XMLDocument document;
  tinyxml2::XMLError load_result = tinyxml2::XML_SUCCESS;
  if (signed_source_configured) {
    if (permissions_ca_env == nullptr || permissions_ca_env[0] == '\0') {
      policy.error = "permissions_p7s_ca_file_not_configured";
      return policy;
    }
    policy.permissions_ca_path = permissions_ca_env;
    std::string verified_xml;
    if (!verify_sros2_signed_permissions(
        policy.path, policy.permissions_ca_path, &verified_xml, &policy.error))
    {
      return policy;
    }
    policy.runtime_signature_verified = true;
    load_result = document.Parse(verified_xml.data(), verified_xml.size());
  } else {
    load_result = document.LoadFile(policy.path.c_str());
  }
  if (load_result != tinyxml2::XML_SUCCESS) {
    policy.error = std::string("permissions_xml_parse_failed:") + document.ErrorStr();
    return policy;
  }
  const tinyxml2::XMLElement * root = document.FirstChildElement("dds");
  const tinyxml2::XMLElement * permissions =
    root == nullptr ? nullptr : root->FirstChildElement("permissions");
  if (permissions == nullptr) {
    policy.error = "permissions_xml_missing_dds_permissions";
    return policy;
  }

  for (const tinyxml2::XMLElement * grant_element = permissions->FirstChildElement("grant");
    grant_element != nullptr; grant_element = grant_element->NextSiblingElement("grant"))
  {
    Sros2Grant grant;
    const char * name = grant_element->Attribute("name");
    const tinyxml2::XMLElement * subject = grant_element->FirstChildElement("subject_name");
    const tinyxml2::XMLElement * validity = grant_element->FirstChildElement("validity");
    const tinyxml2::XMLElement * not_before =
      validity == nullptr ? nullptr : validity->FirstChildElement("not_before");
    const tinyxml2::XMLElement * not_after =
      validity == nullptr ? nullptr : validity->FirstChildElement("not_after");
    const tinyxml2::XMLElement * default_element = grant_element->FirstChildElement("default");
    if (name == nullptr || subject == nullptr || subject->GetText() == nullptr ||
      not_before == nullptr || not_before->GetText() == nullptr ||
      not_after == nullptr || not_after->GetText() == nullptr ||
      default_element == nullptr || default_element->GetText() == nullptr)
    {
      policy.error = "permissions_xml_malformed_grant";
      return policy;
    }
    grant.name = trim_copy(name);
    grant.subject_name = trim_copy(subject->GetText());
    const std::optional<std::int64_t> parsed_not_before =
      parse_iso8601_epoch_seconds(not_before->GetText());
    const std::optional<std::int64_t> parsed_not_after =
      parse_iso8601_epoch_seconds(not_after->GetText());
    if (!parsed_not_before.has_value() || !parsed_not_after.has_value() ||
      *parsed_not_before > *parsed_not_after)
    {
      policy.error = "permissions_xml_invalid_validity";
      return policy;
    }
    grant.not_before_s = *parsed_not_before;
    grant.not_after_s = *parsed_not_after;
    const std::string default_action = trim_copy(default_element->GetText());
    if (default_action != "ALLOW" && default_action != "DENY") {
      policy.error = "permissions_xml_invalid_default_action";
      return policy;
    }
    grant.default_allow = default_action == "ALLOW";

    for (const tinyxml2::XMLElement * child = grant_element->FirstChildElement();
      child != nullptr; child = child->NextSiblingElement())
    {
      const std::string child_name = child->Name() == nullptr ? "" : child->Name();
      if (child_name != "allow_rule" && child_name != "deny_rule") {
        continue;
      }
      Sros2AccessRule rule;
      if (!parse_sros2_access_rule(child, child_name == "allow_rule", &rule)) {
        policy.error = "permissions_xml_malformed_rule";
        return policy;
      }
      grant.rules.push_back(std::move(rule));
    }
    policy.grants.push_back(std::move(grant));
  }
  if (policy.grants.empty()) {
    policy.error = "permissions_xml_has_no_grants";
    return policy;
  }
  policy.valid = true;
  return policy;
}

bool parse_sros2_boolean(
  const tinyxml2::XMLElement * parent,
  const char * name,
  bool * value)
{
  if (parent == nullptr || name == nullptr || value == nullptr) {
    return false;
  }
  const tinyxml2::XMLElement * element = parent->FirstChildElement(name);
  if (element == nullptr || element->GetText() == nullptr) {
    return false;
  }
  const std::string text = trim_copy(element->GetText());
  if (text == "true") {
    *value = true;
    return true;
  }
  if (text == "false") {
    *value = false;
    return true;
  }
  return false;
}

bool parse_sros2_protection_kind(
  const tinyxml2::XMLElement * parent,
  const char * name,
  std::string * value)
{
  if (parent == nullptr || name == nullptr || value == nullptr) {
    return false;
  }
  const tinyxml2::XMLElement * element = parent->FirstChildElement(name);
  if (element == nullptr || element->GetText() == nullptr) {
    return false;
  }
  *value = trim_copy(element->GetText());
  return *value == "NONE" || *value == "SIGN" || *value == "ENCRYPT";
}

std::string governance_verification_error(std::string error)
{
  size_t offset = 0;
  while ((offset = error.find("permissions", offset)) != std::string::npos) {
    error.replace(offset, std::strlen("permissions"), "governance");
    offset += std::strlen("governance");
  }
  return error;
}

Sros2GovernancePolicy parse_sros2_governance_policy()
{
  Sros2GovernancePolicy policy;
  const char * signed_path_env =
    std::getenv("FLEETQOX_RMW_SROS2_GOVERNANCE_P7S_FILE");
  if (signed_path_env == nullptr || signed_path_env[0] == '\0') {
    return policy;
  }
  policy.configured = true;
  policy.signed_source = true;
  policy.path = signed_path_env;
  const char * governance_ca_env =
    std::getenv("FLEETQOX_RMW_SROS2_GOVERNANCE_CA_FILE");
  if (governance_ca_env == nullptr || governance_ca_env[0] == '\0') {
    governance_ca_env = std::getenv("FLEETQOX_RMW_SROS2_PERMISSIONS_CA_FILE");
  }
  if (governance_ca_env == nullptr || governance_ca_env[0] == '\0') {
    policy.error = "governance_p7s_ca_file_not_configured";
    return policy;
  }
  policy.governance_ca_path = governance_ca_env;
  std::string verified_xml;
  if (!verify_sros2_signed_permissions(
      policy.path, policy.governance_ca_path, &verified_xml, &policy.error))
  {
    policy.error = governance_verification_error(policy.error);
    return policy;
  }
  policy.runtime_signature_verified = true;

  tinyxml2::XMLDocument document;
  if (document.Parse(verified_xml.data(), verified_xml.size()) != tinyxml2::XML_SUCCESS) {
    policy.error = std::string("governance_xml_parse_failed:") + document.ErrorStr();
    return policy;
  }
  const tinyxml2::XMLElement * root = document.FirstChildElement("dds");
  const tinyxml2::XMLElement * access_rules =
    root == nullptr ? nullptr : root->FirstChildElement("domain_access_rules");
  if (access_rules == nullptr) {
    policy.error = "governance_xml_missing_domain_access_rules";
    return policy;
  }
  for (const tinyxml2::XMLElement * domain = access_rules->FirstChildElement("domain_rule");
    domain != nullptr; domain = domain->NextSiblingElement("domain_rule"))
  {
    Sros2GovernanceDomainRule domain_rule;
    if (!parse_sros2_domains(domain->FirstChildElement("domains"), &domain_rule.domains) ||
      !parse_sros2_boolean(
        domain, "allow_unauthenticated_participants",
        &domain_rule.allow_unauthenticated_participants) ||
      !parse_sros2_boolean(
        domain, "enable_join_access_control", &domain_rule.enable_join_access_control) ||
      !parse_sros2_protection_kind(
        domain, "discovery_protection_kind", &domain_rule.discovery_protection_kind) ||
      !parse_sros2_protection_kind(
        domain, "liveliness_protection_kind", &domain_rule.liveliness_protection_kind) ||
      !parse_sros2_protection_kind(
        domain, "rtps_protection_kind", &domain_rule.rtps_protection_kind))
    {
      policy.error = "governance_xml_malformed_domain_rule";
      return policy;
    }
    const tinyxml2::XMLElement * topic_access_rules =
      domain->FirstChildElement("topic_access_rules");
    if (topic_access_rules == nullptr) {
      policy.error = "governance_xml_missing_topic_access_rules";
      return policy;
    }
    for (const tinyxml2::XMLElement * topic =
      topic_access_rules->FirstChildElement("topic_rule");
      topic != nullptr; topic = topic->NextSiblingElement("topic_rule"))
    {
      Sros2GovernanceTopicRule topic_rule;
      const tinyxml2::XMLElement * expression = topic->FirstChildElement("topic_expression");
      if (expression == nullptr || expression->GetText() == nullptr) {
        policy.error = "governance_xml_missing_topic_expression";
        return policy;
      }
      topic_rule.topic_expression = trim_copy(expression->GetText());
      if (topic_rule.topic_expression.empty() ||
        !parse_sros2_boolean(
          topic, "enable_discovery_protection",
          &topic_rule.enable_discovery_protection) ||
        !parse_sros2_boolean(
          topic, "enable_liveliness_protection",
          &topic_rule.enable_liveliness_protection) ||
        !parse_sros2_boolean(
          topic, "enable_read_access_control", &topic_rule.enable_read_access_control) ||
        !parse_sros2_boolean(
          topic, "enable_write_access_control", &topic_rule.enable_write_access_control) ||
        !parse_sros2_protection_kind(
          topic, "metadata_protection_kind", &topic_rule.metadata_protection_kind) ||
        !parse_sros2_protection_kind(
          topic, "data_protection_kind", &topic_rule.data_protection_kind))
      {
        policy.error = "governance_xml_malformed_topic_rule";
        return policy;
      }
      domain_rule.topic_rules.push_back(std::move(topic_rule));
    }
    if (domain_rule.topic_rules.empty()) {
      policy.error = "governance_xml_has_no_topic_rules";
      return policy;
    }
    policy.domain_rules.push_back(std::move(domain_rule));
  }
  if (policy.domain_rules.empty()) {
    policy.error = "governance_xml_has_no_domain_rules";
    return policy;
  }
  policy.valid = true;
  return policy;
}

Sros2IdentityCredentials parse_sros2_identity_credentials()
{
  Sros2IdentityCredentials credentials;
  const char * certificate_env =
    std::getenv("FLEETQOX_RMW_SROS2_IDENTITY_CERT_FILE");
  const char * private_key_env =
    std::getenv("FLEETQOX_RMW_SROS2_IDENTITY_KEY_FILE");
  const char * identity_ca_env =
    std::getenv("FLEETQOX_RMW_SROS2_IDENTITY_CA_FILE");
  const bool certificate_configured = certificate_env != nullptr && certificate_env[0] != '\0';
  const bool private_key_configured = private_key_env != nullptr && private_key_env[0] != '\0';
  const bool identity_ca_configured = identity_ca_env != nullptr && identity_ca_env[0] != '\0';
  if (!certificate_configured && !private_key_configured && !identity_ca_configured) {
    return credentials;
  }
  credentials.configured = true;
  if (!certificate_configured || !private_key_configured || !identity_ca_configured) {
    credentials.error = "identity_credentials_incomplete_configuration";
    return credentials;
  }
  credentials.certificate_path = certificate_env;
  credentials.private_key_path = private_key_env;
  credentials.identity_ca_path = identity_ca_env;

  ERR_clear_error();
  using BioPointer = std::unique_ptr<BIO, decltype(&BIO_free)>;
  using X509Pointer = std::unique_ptr<X509, decltype(&X509_free)>;
  using PkeyPointer = std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)>;
  using StorePointer = std::unique_ptr<X509_STORE, decltype(&X509_STORE_free)>;
  using StoreContextPointer =
    std::unique_ptr<X509_STORE_CTX, decltype(&X509_STORE_CTX_free)>;

  BioPointer certificate_bio(
    BIO_new_file(credentials.certificate_path.c_str(), "rb"), BIO_free);
  if (!certificate_bio) {
    credentials.error = openssl_error_text("identity_certificate_open_failed");
    return credentials;
  }
  X509Pointer certificate(
    PEM_read_bio_X509(certificate_bio.get(), nullptr, nullptr, nullptr), X509_free);
  if (!certificate) {
    credentials.error = openssl_error_text("identity_certificate_parse_failed");
    return credentials;
  }
  BioPointer private_key_bio(
    BIO_new_file(credentials.private_key_path.c_str(), "rb"), BIO_free);
  if (!private_key_bio) {
    credentials.error = openssl_error_text("identity_private_key_open_failed");
    return credentials;
  }
  PkeyPointer private_key(
    PEM_read_bio_PrivateKey(private_key_bio.get(), nullptr, nullptr, nullptr), EVP_PKEY_free);
  if (!private_key) {
    credentials.error = openssl_error_text("identity_private_key_parse_failed");
    return credentials;
  }
  if (X509_check_private_key(certificate.get(), private_key.get()) != 1) {
    credentials.error = openssl_error_text("identity_private_key_mismatch");
    return credentials;
  }
  credentials.private_key_matches = true;

  StorePointer trust_store(X509_STORE_new(), X509_STORE_free);
  if (!trust_store) {
    credentials.error = openssl_error_text("identity_ca_store_allocation_failed");
    return credentials;
  }
  if (X509_STORE_load_locations(
      trust_store.get(), credentials.identity_ca_path.c_str(), nullptr) != 1)
  {
    credentials.error = openssl_error_text("identity_ca_load_failed");
    return credentials;
  }
  StoreContextPointer verify_context(X509_STORE_CTX_new(), X509_STORE_CTX_free);
  if (!verify_context ||
    X509_STORE_CTX_init(
      verify_context.get(), trust_store.get(), certificate.get(), nullptr) != 1)
  {
    credentials.error = openssl_error_text("identity_verify_context_failed");
    return credentials;
  }
  if (X509_verify_cert(verify_context.get()) != 1) {
    const int verify_error = X509_STORE_CTX_get_error(verify_context.get());
    credentials.error = std::string("identity_certificate_chain_verify_failed:") +
      X509_verify_cert_error_string(verify_error);
    return credentials;
  }
  credentials.certificate_chain_verified = true;

  std::array<char, 1024> common_name{};
  const int common_name_length = X509_NAME_get_text_by_NID(
    X509_get_subject_name(certificate.get()), NID_commonName,
    common_name.data(), static_cast<int>(common_name.size()));
  if (common_name_length <= 0 ||
    static_cast<size_t>(common_name_length) >= common_name.size())
  {
    credentials.error = "identity_certificate_common_name_missing";
    return credentials;
  }
  credentials.subject_common_name.assign(
    common_name.data(), static_cast<size_t>(common_name_length));
  credentials.valid = true;
  return credentials;
}

const Sros2PermissionsPolicy & sros2_permissions_policy()
{
  static const Sros2PermissionsPolicy policy = parse_sros2_permissions_policy();
  return policy;
}

const Sros2GovernancePolicy & sros2_governance_policy()
{
  static const Sros2GovernancePolicy policy = parse_sros2_governance_policy();
  return policy;
}

const Sros2IdentityCredentials & sros2_identity_credentials()
{
  static const Sros2IdentityCredentials credentials =
    parse_sros2_identity_credentials();
  return credentials;
}

bool wildcard_match(const std::string & pattern, const std::string & value)
{
  size_t pattern_index = 0;
  size_t value_index = 0;
  size_t star_index = std::string::npos;
  size_t star_value_index = 0;
  while (value_index < value.size()) {
    if (pattern_index < pattern.size() &&
      (pattern[pattern_index] == '?' || pattern[pattern_index] == value[value_index]))
    {
      ++pattern_index;
      ++value_index;
    } else if (pattern_index < pattern.size() && pattern[pattern_index] == '*') {
      star_index = pattern_index++;
      star_value_index = value_index;
    } else if (star_index != std::string::npos) {
      pattern_index = star_index + 1;
      value_index = ++star_value_index;
    } else {
      return false;
    }
  }
  while (pattern_index < pattern.size() && pattern[pattern_index] == '*') {
    ++pattern_index;
  }
  return pattern_index == pattern.size();
}

bool sros2_topic_matches(const std::string & expression, const std::string & ros_topic)
{
  const std::string dds_topic = "rt" + ros_topic;
  return wildcard_match(expression, dds_topic) || wildcard_match(expression, ros_topic);
}

bool sros2_domain_matches(
  const std::vector<Sros2DomainRange> & domains, std::size_t domain_id)
{
  if (domain_id > std::numeric_limits<std::uint32_t>::max()) {
    return false;
  }
  const std::uint32_t checked_domain = static_cast<std::uint32_t>(domain_id);
  return std::any_of(
    domains.begin(), domains.end(),
    [checked_domain](const Sros2DomainRange & range) {
      return checked_domain >= range.minimum && checked_domain <= range.maximum;
    });
}

Sros2GovernanceDecision evaluate_sros2_governance_policy(
  Sros2Operation operation,
  const std::string & topic_name,
  std::size_t domain_id)
{
  const Sros2GovernancePolicy & policy = sros2_governance_policy();
  if (!policy.configured) {
    return Sros2GovernanceDecision::not_configured;
  }
  if (!policy.valid) {
    return Sros2GovernanceDecision::invalid;
  }
  const auto domain_it = std::find_if(
    policy.domain_rules.begin(), policy.domain_rules.end(),
    [domain_id](const Sros2GovernanceDomainRule & rule) {
      return sros2_domain_matches(rule.domains, domain_id);
    });
  if (domain_it == policy.domain_rules.end()) {
    return Sros2GovernanceDecision::no_matching_rule;
  }
  if (domain_it->discovery_protection_kind != "NONE" ||
    domain_it->liveliness_protection_kind != "NONE" ||
    domain_it->rtps_protection_kind != "NONE")
  {
    return Sros2GovernanceDecision::transport_protection_required;
  }
  if (!domain_it->allow_unauthenticated_participants) {
    return Sros2GovernanceDecision::participant_authentication_required;
  }
  if (domain_it->enable_join_access_control) {
    const Sros2PermissionsPolicy & permissions = sros2_permissions_policy();
    if (!permissions.configured || !permissions.valid) {
      return Sros2GovernanceDecision::invalid;
    }
  }
  const auto topic_it = std::find_if(
    domain_it->topic_rules.begin(), domain_it->topic_rules.end(),
    [&topic_name](const Sros2GovernanceTopicRule & rule) {
      return sros2_topic_matches(rule.topic_expression, topic_name);
    });
  if (topic_it == domain_it->topic_rules.end()) {
    return Sros2GovernanceDecision::no_matching_rule;
  }
  if (topic_it->metadata_protection_kind != "NONE" ||
    topic_it->data_protection_kind != "NONE")
  {
    return Sros2GovernanceDecision::transport_protection_required;
  }
  const bool access_control_enabled = operation == Sros2Operation::publish ?
    topic_it->enable_write_access_control : topic_it->enable_read_access_control;
  return access_control_enabled ? Sros2GovernanceDecision::require_access_control :
         Sros2GovernanceDecision::allow_without_access_control;
}

bool sros2_grant_matches(const Sros2Grant & grant, const std::string & enclave)
{
  return grant.name == enclave || grant.subject_name == "CN=" + enclave;
}

SecurityDecision evaluate_sros2_permissions_policy(
  Sros2Operation operation,
  const std::string & topic_name,
  const std::string & enclave,
  std::size_t domain_id)
{
  const Sros2PermissionsPolicy & policy = sros2_permissions_policy();
  if (!policy.configured) {
    return SecurityDecision::not_configured;
  }
  if (!policy.valid) {
    return SecurityDecision::invalid;
  }
  const std::int64_t now_s = std::chrono::duration_cast<std::chrono::seconds>(
    std::chrono::system_clock::now().time_since_epoch()).count();
  const auto grant_it = std::find_if(
    policy.grants.begin(), policy.grants.end(),
    [&enclave](const Sros2Grant & grant) {return sros2_grant_matches(grant, enclave);});
  if (grant_it == policy.grants.end() || now_s < grant_it->not_before_s ||
    now_s > grant_it->not_after_s)
  {
    return SecurityDecision::deny;
  }
  for (const Sros2AccessRule & rule : grant_it->rules) {
    if (!sros2_domain_matches(rule.domains, domain_id)) {
      continue;
    }
    const std::vector<std::string> & topic_expressions =
      operation == Sros2Operation::publish ?
      rule.publish_topic_expressions : rule.subscribe_topic_expressions;
    const bool unsupported_criteria = operation == Sros2Operation::publish ?
      rule.publish_unsupported_criteria : rule.subscribe_unsupported_criteria;
    const bool topic_matches = std::any_of(
      topic_expressions.begin(), topic_expressions.end(),
      [&topic_name](const std::string & expression) {
        return sros2_topic_matches(expression, topic_name);
      });
    if (!topic_matches) {
      continue;
    }
    if (unsupported_criteria && rule.allow) {
      continue;
    }
    return rule.allow ? SecurityDecision::allow : SecurityDecision::deny;
  }
  return grant_it->default_allow ? SecurityDecision::allow : SecurityDecision::deny;
}

SecurityDecision evaluate_sros2_topic_policy(
  Sros2Operation operation,
  const std::string & topic_name,
  const std::string & enclave,
  std::size_t domain_id)
{
  const Sros2GovernanceDecision governance =
    evaluate_sros2_governance_policy(operation, topic_name, domain_id);
  if (governance == Sros2GovernanceDecision::invalid) {
    return SecurityDecision::invalid;
  }
  if (governance == Sros2GovernanceDecision::participant_authentication_required ||
    governance == Sros2GovernanceDecision::transport_protection_required ||
    governance == Sros2GovernanceDecision::no_matching_rule)
  {
    return SecurityDecision::deny;
  }
  if (governance == Sros2GovernanceDecision::allow_without_access_control) {
    return SecurityDecision::allow;
  }
  const SecurityDecision permissions =
    evaluate_sros2_permissions_policy(operation, topic_name, enclave, domain_id);
  if (governance == Sros2GovernanceDecision::require_access_control &&
    permissions == SecurityDecision::not_configured)
  {
    return SecurityDecision::deny;
  }
  return permissions;
}

FleetQoxSecurityPolicy parse_security_policy_env()
{
  FleetQoxSecurityPolicy policy;
  const char * policy_env = std::getenv("FLEETQOX_RMW_SECURITY_POLICY");
  if (policy_env == nullptr || policy_env[0] == '\0') {
    return policy;
  }
  policy.configured = true;
  for (const std::string & rule_text : split_nonempty(policy_env, ';')) {
    const size_t equals = rule_text.find('=');
    if (equals == std::string::npos || equals == 0 || equals + 1 >= rule_text.size()) {
      continue;
    }
    const std::string key = trim_copy(rule_text.substr(0, equals));
    const std::vector<std::string> topics = split_nonempty(rule_text.substr(equals + 1), ',');
    if (key == "publish_allow" || key == "allow_publish") {
      policy.publish_allow_configured = true;
      policy.publish_allow.insert(topics.begin(), topics.end());
    } else if (key == "publish_deny" || key == "deny_publish") {
      policy.publish_deny.insert(topics.begin(), topics.end());
    }
  }
  return policy;
}

const FleetQoxSecurityPolicy & security_policy()
{
  static const FleetQoxSecurityPolicy policy = parse_security_policy_env();
  return policy;
}

bool publish_allowed_by_security_policy(
  const std::string & topic_name,
  const std::string & enclave,
  std::size_t domain_id)
{
  const FleetQoxSecurityPolicy & policy = security_policy();
  if (policy.configured && topic_set_contains(policy.publish_deny, topic_name)) {
    return false;
  }
  if (policy.configured && policy.publish_allow_configured &&
    !topic_set_contains(policy.publish_allow, topic_name))
  {
    return false;
  }
  const SecurityDecision sros2_decision =
    evaluate_sros2_topic_policy(Sros2Operation::publish, topic_name, enclave, domain_id);
  if (sros2_decision == SecurityDecision::invalid) {
    g_sros2_permissions_xml_parse_errors.fetch_add(1, std::memory_order_relaxed);
    g_sros2_permissions_xml_denied.fetch_add(1, std::memory_order_relaxed);
    return false;
  }
  if (sros2_decision == SecurityDecision::deny) {
    g_sros2_permissions_xml_denied.fetch_add(1, std::memory_order_relaxed);
    return false;
  }
  if (sros2_decision == SecurityDecision::allow) {
    g_sros2_permissions_xml_allowed.fetch_add(1, std::memory_order_relaxed);
  }
  return true;
}

bool subscribe_allowed_by_security_policy(
  const std::string & topic_name,
  const std::string & enclave,
  std::size_t domain_id)
{
  const SecurityDecision sros2_decision =
    evaluate_sros2_topic_policy(Sros2Operation::subscribe, topic_name, enclave, domain_id);
  if (sros2_decision == SecurityDecision::invalid) {
    g_sros2_permissions_xml_parse_errors.fetch_add(1, std::memory_order_relaxed);
    g_sros2_permissions_xml_subscribe_denied.fetch_add(1, std::memory_order_relaxed);
    return false;
  }
  if (sros2_decision == SecurityDecision::deny) {
    g_sros2_permissions_xml_subscribe_denied.fetch_add(1, std::memory_order_relaxed);
    return false;
  }
  if (sros2_decision == SecurityDecision::allow) {
    g_sros2_permissions_xml_subscribe_allowed.fetch_add(1, std::memory_order_relaxed);
  }
  return true;
}

struct FleetPathPlanRule
{
  std::string topic;
  std::vector<std::string> path_ids;
};

struct FleetRepairPlanRule
{
  std::string topic;
  std::vector<std::string> path_ids;
  std::vector<std::uint64_t> source_sequences;
  int max_attempts = 0;
};

struct RepairAttemptState
{
  std::int64_t last_request_ns = 0;
  std::uint64_t attempts = 0;
};

bool parse_peer_endpoints(
  const char * peer_env,
  std::vector<sockaddr_in> * peer_addresses,
  std::vector<std::string> * peer_path_ids,
  std::string * error)
{
  if (peer_addresses == nullptr || peer_path_ids == nullptr) {
    return false;
  }
  if (peer_env == nullptr || peer_env[0] == '\0') {
    return true;
  }

  std::string peers(peer_env);
  size_t start = 0;
  while (start < peers.size()) {
    const size_t comma = peers.find(',', start);
    std::string endpoint = trim_copy(peers.substr(
      start,
      comma == std::string::npos ? std::string::npos : comma - start));
    std::string path_id = "peer_" + std::to_string(peer_addresses->size());
    const size_t equals = endpoint.find('=');
    if (equals != std::string::npos && equals > 0 && equals + 1 < endpoint.size()) {
      path_id = trim_copy(endpoint.substr(0, equals));
      endpoint = trim_copy(endpoint.substr(equals + 1));
    }
    sockaddr_in parsed{};
    if (!parse_ipv4_endpoint(endpoint, &parsed)) {
      if (error != nullptr) {
        *error = "invalid FLEETQOX_RMW_PEERS endpoint: " + endpoint;
      }
      return false;
    }
    peer_addresses->push_back(parsed);
    peer_path_ids->push_back(path_id);
    if (comma == std::string::npos) {
      break;
    }
    start = comma + 1;
  }
  return true;
}

std::vector<FleetPathPlanRule> parse_fleet_path_plan(const char * plan_env)
{
  std::vector<FleetPathPlanRule> rules;
  if (plan_env == nullptr || plan_env[0] == '\0') {
    return rules;
  }
  for (const std::string & rule_text : split_nonempty(plan_env, ';')) {
    const size_t equals = rule_text.find('=');
    if (equals == std::string::npos || equals == 0 || equals + 1 >= rule_text.size()) {
      continue;
    }
    FleetPathPlanRule rule;
    rule.topic = trim_copy(rule_text.substr(0, equals));
    rule.path_ids = split_nonempty(rule_text.substr(equals + 1), '+');
    if (!rule.topic.empty() && !rule.path_ids.empty()) {
      rules.push_back(rule);
    }
  }
  return rules;
}

std::vector<std::uint64_t> parse_sequence_list(const char * sequence_env)
{
  std::vector<std::uint64_t> sequences;
  if (sequence_env == nullptr || sequence_env[0] == '\0') {
    return sequences;
  }
  std::string text(sequence_env);
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

std::vector<FleetRepairPlanRule> parse_fleet_repair_plan(const char * plan_env)
{
  std::vector<FleetRepairPlanRule> rules;
  if (plan_env == nullptr || plan_env[0] == '\0') {
    return rules;
  }
  for (const std::string & rule_text : split_nonempty(plan_env, ';')) {
    const std::vector<std::string> fields = split_nonempty(rule_text, '|');
    if (fields.empty()) {
      continue;
    }
    const size_t equals = fields[0].find('=');
    if (equals == std::string::npos || equals == 0 || equals + 1 >= fields[0].size()) {
      continue;
    }
    FleetRepairPlanRule rule;
    rule.topic = trim_copy(fields[0].substr(0, equals));
    rule.path_ids = split_nonempty(fields[0].substr(equals + 1), '+');
    for (size_t i = 1; i < fields.size(); ++i) {
      const size_t field_equals = fields[i].find('=');
      if (field_equals == std::string::npos || field_equals == 0 ||
        field_equals + 1 >= fields[i].size())
      {
        continue;
      }
      const std::string key = trim_copy(fields[i].substr(0, field_equals));
      const std::string value = trim_copy(fields[i].substr(field_equals + 1));
      if (key == "sequences") {
        rule.source_sequences = parse_sequence_list(value.c_str());
      } else if (key == "attempts") {
        char * end = nullptr;
        errno = 0;
        const long parsed = std::strtol(value.c_str(), &end, 10);
        if (errno == 0 && end != value.c_str() && *end == '\0' && parsed >= 0) {
          rule.max_attempts = static_cast<int>(std::min<long>(parsed, 1000));
        }
      }
    }
    if (!rule.topic.empty() && !rule.path_ids.empty()) {
      rules.push_back(rule);
    }
  }
  return rules;
}

int parse_nonnegative_int_env(const char * name, int default_value, int max_value)
{
  const char * value = std::getenv(name);
  if (value == nullptr || value[0] == '\0') {
    return default_value;
  }
  char * end = nullptr;
  errno = 0;
  const long parsed = std::strtol(value, &end, 10);
  if (errno != 0 || end == value || *end != '\0' || parsed < 0) {
    return default_value;
  }
  return static_cast<int>(std::min<long>(parsed, max_value));
}

std::uint64_t fnv1a64(const std::string & text, std::uint64_t seed)
{
  std::uint64_t hash = seed;
  for (const unsigned char c : text) {
    hash ^= static_cast<std::uint64_t>(c);
    hash *= 1099511628211ULL;
  }
  return hash;
}

std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> make_endpoint_gid(const std::string & endpoint_id)
{
  std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> gid{};
  size_t offset = 0;
  std::uint64_t block = 0;
  while (offset < gid.size()) {
    const std::uint64_t value = fnv1a64(
      endpoint_id + "#" + std::to_string(block),
      1469598103934665603ULL + block);
    for (int byte = 0; byte < 8 && offset < gid.size(); ++byte) {
      gid[offset++] = static_cast<std::uint8_t>((value >> (byte * 8)) & 0xFFu);
    }
    ++block;
  }
  return gid;
}

std::string hex_encode_bytes(const std::uint8_t * data, size_t size)
{
  static constexpr char kHex[] = "0123456789abcdef";
  std::string encoded;
  encoded.reserve(size * 2);
  for (size_t i = 0; i < size; ++i) {
    encoded.push_back(kHex[(data[i] >> 4) & 0x0F]);
    encoded.push_back(kHex[data[i] & 0x0F]);
  }
  return encoded;
}

int hex_nibble(char c)
{
  if (c >= '0' && c <= '9') {
    return c - '0';
  }
  if (c >= 'a' && c <= 'f') {
    return c - 'a' + 10;
  }
  if (c >= 'A' && c <= 'F') {
    return c - 'A' + 10;
  }
  return -1;
}

std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> endpoint_gid_from_hex(
  const std::string & endpoint_gid,
  const std::string & endpoint_id)
{
  if (endpoint_gid.empty() || endpoint_gid.size() % 2 != 0) {
    return make_endpoint_gid(endpoint_id);
  }
  std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> gid{};
  const size_t max_bytes = std::min(endpoint_gid.size() / 2, gid.size());
  for (size_t i = 0; i < max_bytes; ++i) {
    const int high = hex_nibble(endpoint_gid[i * 2]);
    const int low = hex_nibble(endpoint_gid[i * 2 + 1]);
    if (high < 0 || low < 0) {
      return make_endpoint_gid(endpoint_id);
    }
    gid[i] = static_cast<std::uint8_t>((high << 4) | low);
  }
  return gid;
}

rmw_fleetqox_cpp::GraphQosProfile graph_qos_from_rmw(const rmw_qos_profile_t & qos)
{
  rmw_fleetqox_cpp::GraphQosProfile graph_qos{};
  graph_qos.history = static_cast<std::uint64_t>(qos.history);
  graph_qos.depth = static_cast<std::uint64_t>(qos.depth);
  graph_qos.reliability = static_cast<std::uint64_t>(qos.reliability);
  graph_qos.durability = static_cast<std::uint64_t>(qos.durability);
  graph_qos.deadline_sec = qos.deadline.sec;
  graph_qos.deadline_nsec = qos.deadline.nsec;
  graph_qos.lifespan_sec = qos.lifespan.sec;
  graph_qos.lifespan_nsec = qos.lifespan.nsec;
  graph_qos.liveliness = static_cast<std::uint64_t>(qos.liveliness);
  graph_qos.liveliness_lease_duration_sec = qos.liveliness_lease_duration.sec;
  graph_qos.liveliness_lease_duration_nsec = qos.liveliness_lease_duration.nsec;
  graph_qos.avoid_ros_namespace_conventions = qos.avoid_ros_namespace_conventions ? 1u : 0u;
  return graph_qos;
}

rmw_qos_profile_t rmw_qos_from_graph(const rmw_fleetqox_cpp::GraphQosProfile & graph_qos)
{
  rmw_qos_profile_t qos = rmw_qos_profile_default;
  qos.history = static_cast<rmw_qos_history_policy_t>(graph_qos.history);
  qos.depth = static_cast<size_t>(graph_qos.depth);
  qos.reliability = static_cast<rmw_qos_reliability_policy_t>(graph_qos.reliability);
  qos.durability = static_cast<rmw_qos_durability_policy_t>(graph_qos.durability);
  qos.deadline.sec = graph_qos.deadline_sec;
  qos.deadline.nsec = graph_qos.deadline_nsec;
  qos.lifespan.sec = graph_qos.lifespan_sec;
  qos.lifespan.nsec = graph_qos.lifespan_nsec;
  qos.liveliness = static_cast<rmw_qos_liveliness_policy_t>(graph_qos.liveliness);
  qos.liveliness_lease_duration.sec = graph_qos.liveliness_lease_duration_sec;
  qos.liveliness_lease_duration.nsec = graph_qos.liveliness_lease_duration_nsec;
  qos.avoid_ros_namespace_conventions = graph_qos.avoid_ros_namespace_conventions != 0;
  return qos;
}

class LoopbackSocketTransport
{
public:
  LoopbackSocketTransport()
  {
    start();
  }

  ~LoopbackSocketTransport()
  {
    stop();
  }

  bool ensure_started()
  {
    std::lock_guard<std::mutex> lock(lifecycle_mutex_);
    if (!ready_) {
      start();
    }
    return ready_;
  }

  void shutdown()
  {
    std::lock_guard<std::mutex> lock(lifecycle_mutex_);
    stop();
  }

  rmw_ret_t send_frame(const std::string & encoded_frame)
  {
    return send_frame_with_qos(encoded_frame, nullptr);
  }

  rmw_ret_t send_data_frame(const std::string & encoded_frame, const rmw_qos_profile_t & qos)
  {
    return send_frame_with_qos(encoded_frame, &qos);
  }

  rmw_ret_t send_frame_with_qos(
    const std::string & encoded_frame,
    const rmw_qos_profile_t * qos)
  {
    if (!ready_) {
      RMW_SET_ERROR_MSG(init_error_.empty() ? "socket transport is not ready" : init_error_.c_str());
      return RMW_RET_ERROR;
    }
    if (encoded_frame.empty()) {
      RMW_SET_ERROR_MSG("encoded FleetRMW frame is empty");
      return RMW_RET_INVALID_ARGUMENT;
    }
    const bool quic_gateway_enabled = quic_gateway_transport_.enabled();

    if (should_drop_outbound_data_frame_for_test(encoded_frame)) {
      return RMW_RET_OK;
    }

    const std::optional<rmw_fleetqox_cpp::DataFrame> data_frame =
      rmw_fleetqox_cpp::decode_data_frame(encoded_frame);
    const bool is_data_frame = data_frame.has_value();
    const bool include_udp_local = !shared_memory_active();
    const std::vector<sockaddr_in> targets =
      is_data_frame ? data_frame_targets(include_udp_local, qos, data_frame) :
      frame_targets(include_udp_local);
    if (targets.empty() && !shared_memory_only() && !quic_gateway_enabled) {
      RMW_SET_ERROR_MSG("socket transport has no local or peer target for frame");
      return RMW_RET_ERROR;
    }
    auto send_once = [&]() -> rmw_ret_t {
      rmw_ret_t send_ret = RMW_RET_OK;
      if (shared_memory_active()) {
        send_ret = send_shared_memory_payload(encoded_frame);
      }
      if (send_ret == RMW_RET_OK && quic_gateway_enabled) {
        send_ret = send_quic_gateway_payload(encoded_frame);
      }
      if (send_ret == RMW_RET_OK && !shared_memory_only()) {
        send_ret = send_payload_to_targets(encoded_frame, targets, "FleetRMW frame");
      }
      if (send_ret == RMW_RET_OK) {
        frames_sent_.fetch_add(1, std::memory_order_relaxed);
      }
      return send_ret;
    };
    rmw_ret_t send_ret = send_once();
    if (send_ret != RMW_RET_OK || !is_data_frame || proactive_data_repeats_ <= 0) {
      return send_ret;
    }
    for (int repeat = 0; repeat < proactive_data_repeats_; ++repeat) {
      if (proactive_data_repeat_interval_ms_ > 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(proactive_data_repeat_interval_ms_));
      }
      send_ret = send_once();
      if (send_ret != RMW_RET_OK) {
        return send_ret;
      }
    }
    return RMW_RET_OK;
  }

  rmw_ret_t send_ack_nack(const std::string & payload)
  {
    const rmw_ret_t ret = send_control_payload(payload, true);
    if (ret == RMW_RET_OK) {
      ack_nack_sent_.fetch_add(1, std::memory_order_relaxed);
    }
    return ret;
  }

  rmw_ret_t send_unrecoverable_loss_notice(const std::string & payload)
  {
    const rmw_ret_t ret = send_control_payload(payload, true);
    if (ret == RMW_RET_OK) {
      unrecoverable_loss_notices_sent_.fetch_add(1, std::memory_order_relaxed);
    }
    return ret;
  }

  rmw_ret_t send_retransmission_frame(const std::string & encoded_frame)
  {
    const auto data_frame = rmw_fleetqox_cpp::decode_data_frame(encoded_frame);
    const std::optional<FleetRepairPlanRule> repair_rule =
      data_frame.has_value() ? repair_plan_rule_for_frame(*data_frame) : std::nullopt;
    if (data_frame.has_value() && repair_plan_configured() && !repair_rule.has_value()) {
      repair_not_admitted_.fetch_add(1, std::memory_order_relaxed);
      return RMW_RET_UNSUPPORTED;
    }
    std::string repair_key;
    if (data_frame.has_value()) {
      repair_key = retransmit_ledger_key(
        data_frame->publisher_id,
        data_frame->source_sequence_number);
      const std::int64_t now = monotonic_timestamp_ns();
      std::lock_guard<std::mutex> lock(repair_attempt_mutex_);
      RepairAttemptState & state = repair_attempts_[repair_key];
      const std::int64_t min_interval_ns =
        static_cast<std::int64_t>(repair_min_interval_ms_) * 1000000ll;
      if (min_interval_ns > 0 && state.last_request_ns > 0 &&
        now - state.last_request_ns < min_interval_ns)
      {
        repair_requests_coalesced_.fetch_add(1, std::memory_order_relaxed);
        return RMW_RET_OK;
      }
      state.last_request_ns = now;
      const int max_attempts =
        repair_rule.has_value() && repair_rule->max_attempts > 0 ?
        repair_rule->max_attempts : repair_max_attempts_per_sequence_;
      if (max_attempts > 0 &&
        state.attempts >= static_cast<std::uint64_t>(max_attempts))
      {
        repair_sequence_attempt_limit_exhausted_.fetch_add(1, std::memory_order_relaxed);
        return RMW_RET_UNSUPPORTED;
      }
    }
    if (repair_retransmission_budget_ >= 0 &&
      nack_retransmissions_.load(std::memory_order_relaxed) >=
      static_cast<std::uint64_t>(repair_retransmission_budget_))
    {
      repair_budget_exhausted_.fetch_add(1, std::memory_order_relaxed);
      return RMW_RET_UNSUPPORTED;
    }
    const std::vector<sockaddr_in> repair_targets = repair_rule.has_value() ?
      repair_targets_for_path_ids(repair_rule->path_ids) : std::vector<sockaddr_in>{};
    rmw_ret_t ret = RMW_RET_OK;
    if (repair_targets.empty()) {
      ret = send_frame(encoded_frame);
    } else {
      ret = send_payload_to_targets(
        encoded_frame,
        repair_targets,
        "FleetRMW repair frame");
      if (ret == RMW_RET_OK) {
        frames_sent_.fetch_add(1, std::memory_order_relaxed);
        repair_plan_frames_.fetch_add(1, std::memory_order_relaxed);
        repair_plan_selected_path_count_.fetch_add(
          repair_targets.size(),
          std::memory_order_relaxed);
        if (repair_targets.size() > 1) {
          repair_plan_redundant_frames_.fetch_add(1, std::memory_order_relaxed);
        }
      }
    }
    if (ret == RMW_RET_OK) {
      nack_retransmissions_.fetch_add(1, std::memory_order_relaxed);
      if (!repair_key.empty()) {
        std::lock_guard<std::mutex> lock(repair_attempt_mutex_);
        repair_attempts_[repair_key].attempts += 1;
      }
    }
    return ret;
  }

  std::uint64_t frames_sent() const
  {
    return frames_sent_.load(std::memory_order_relaxed);
  }

  std::uint64_t frames_received() const
  {
    return frames_received_.load(std::memory_order_relaxed);
  }

  std::uint64_t data_frames_received() const
  {
    return data_frames_received_.load(std::memory_order_relaxed);
  }

  bool udp_aead_enabled() const
  {
    return udp_aead_enabled_;
  }

  std::uint64_t udp_aead_encrypted_frames() const
  {
    return udp_aead_encrypted_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_aead_decrypted_frames() const
  {
    return udp_aead_decrypted_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_aead_authentication_failures() const
  {
    return udp_aead_authentication_failures_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_aead_unprotected_drops() const
  {
    return udp_aead_unprotected_drops_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_aead_replay_drops() const
  {
    return udp_aead_replay_drops_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_aead_session_keys_derived() const
  {
    return udp_aead_session_keys_derived_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_aead_session_key_rotations() const
  {
    return udp_aead_session_key_rotations_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_aead_session_key_reuses() const
  {
    return udp_aead_session_key_reuses_.load(std::memory_order_relaxed);
  }

  bool udp_peer_auth_enabled() const
  {
    return udp_peer_auth_enabled_;
  }

  std::uint64_t udp_peer_auth_signed_frames() const
  {
    return udp_peer_auth_signed_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_peer_auth_verified_frames() const
  {
    return udp_peer_auth_verified_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_peer_auth_failures() const
  {
    return udp_peer_auth_failures_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_peer_auth_chain_failures() const
  {
    return udp_peer_auth_chain_failures_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_peer_auth_signature_failures() const
  {
    return udp_peer_auth_signature_failures_.load(std::memory_order_relaxed);
  }

  std::uint64_t udp_peer_auth_identity_denied() const
  {
    return udp_peer_auth_identity_denied_.load(std::memory_order_relaxed);
  }

  bool udp_peer_auth_crl_enabled() const
  {
    return udp_peer_auth_crl_enabled_;
  }

  std::uint64_t udp_peer_auth_revoked_certificate_drops() const
  {
    return udp_peer_auth_revoked_certificate_drops_.load(std::memory_order_relaxed);
  }

  std::string udp_peer_auth_last_identity() const
  {
    std::lock_guard<std::mutex> lock(udp_peer_auth_identity_mutex_);
    return udp_peer_auth_last_identity_;
  }

  std::uint64_t ack_nack_sent() const
  {
    return ack_nack_sent_.load(std::memory_order_relaxed);
  }

  std::uint64_t ack_nack_received() const
  {
    return ack_nack_received_.load(std::memory_order_relaxed);
  }

  std::uint64_t ack_nack_duplicate_received() const
  {
    return ack_nack_duplicate_received_.load(std::memory_order_relaxed);
  }

  std::uint64_t ack_nack_out_of_order_received() const
  {
    return ack_nack_out_of_order_received_.load(std::memory_order_relaxed);
  }

  std::uint64_t unrecoverable_loss_notices_sent() const
  {
    return unrecoverable_loss_notices_sent_.load(std::memory_order_relaxed);
  }

  std::uint64_t unrecoverable_loss_notices_received() const
  {
    return unrecoverable_loss_notices_received_.load(std::memory_order_relaxed);
  }

  std::uint64_t nack_retransmissions() const
  {
    return nack_retransmissions_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_nacks_sent() const
  {
    return fragment_nacks_sent_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_nacks_received() const
  {
    return fragment_nacks_received_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragments_selectively_retransmitted() const
  {
    return fragments_selectively_retransmitted_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_repair_requests_coalesced() const
  {
    return fragment_repair_requests_coalesced_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_repair_cooldown_coalesced() const
  {
    return fragment_repair_cooldown_coalesced_.load(std::memory_order_relaxed);
  }

  std::uint64_t completed_fragment_duplicates_dropped() const
  {
    return completed_fragment_duplicates_dropped_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_duplicate_no_progress_drops() const
  {
    return fragment_duplicate_no_progress_drops_.load(std::memory_order_relaxed);
  }

  std::uint64_t test_dropped_fragments() const
  {
    return test_dropped_fragments_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_send_queue_rejections() const
  {
    return fragment_send_queue_rejections_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_send_failures() const
  {
    return fragment_send_failures_.load(std::memory_order_relaxed);
  }

  size_t fragment_send_queue_high_water() const
  {
    return fragment_send_queue_high_water_.load(std::memory_order_relaxed);
  }

  size_t fragment_repair_queue_high_water() const
  {
    return fragment_repair_queue_high_water_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_repair_round_robin_rotations() const
  {
    return fragment_repair_round_robin_rotations_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_repair_frame_switches() const
  {
    return fragment_repair_frame_switches_.load(std::memory_order_relaxed);
  }

  size_t fragment_repair_max_active_frames() const
  {
    return fragment_repair_max_active_frames_.load(std::memory_order_relaxed);
  }

  size_t fragment_repair_max_consecutive_same_frame_while_contended() const
  {
    return fragment_repair_max_consecutive_same_frame_while_contended_.load(
      std::memory_order_relaxed);
  }

  size_t udp_datagram_size_high_water() const
  {
    return udp_datagram_size_high_water_.load(std::memory_order_relaxed);
  }

  size_t fragment_effective_chunk_bytes_min() const
  {
    return fragment_effective_chunk_bytes_min_.load(
      std::memory_order_relaxed);
  }

  size_t fragment_effective_chunk_bytes_max() const
  {
    return fragment_effective_chunk_bytes_max_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_chunk_budget_reductions() const
  {
    return fragment_chunk_budget_reductions_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t udp_datagram_budget_failures() const
  {
    return udp_datagram_budget_failures_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_queue_admission_waits() const
  {
    return fragment_queue_admission_waits_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_queue_admission_timeouts() const
  {
    return fragment_queue_admission_timeouts_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_queue_admission_wait_ns() const
  {
    return fragment_queue_admission_wait_ns_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_repair_queue_deferrals() const
  {
    return fragment_repair_queue_deferrals_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_repair_pressure_priority_promotions() const
  {
    return fragment_repair_pressure_priority_promotions_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_completion_markers_sent() const
  {
    return fragment_completion_markers_sent_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_completion_markers_received() const
  {
    return fragment_completion_markers_received_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_completion_marker_orphans() const
  {
    return fragment_completion_marker_orphans_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_completion_marker_failures() const
  {
    return fragment_completion_marker_failures_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_repair_source_denials() const
  {
    return fragment_repair_source_denials_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_repair_reader_budget_exhausted() const
  {
    return fragment_repair_reader_budget_exhausted_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_initial_round_robin_rotations() const
  {
    return fragment_initial_round_robin_rotations_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_initial_frame_switches() const
  {
    return fragment_initial_frame_switches_.load(std::memory_order_relaxed);
  }

  size_t fragment_initial_max_consecutive_same_frame() const
  {
    return fragment_initial_max_consecutive_same_frame_.load(
      std::memory_order_relaxed);
  }

  size_t fragment_initial_max_consecutive_same_frame_while_contended() const
  {
    return fragment_initial_max_consecutive_same_frame_while_contended_.load(
      std::memory_order_relaxed);
  }

  size_t fragment_initial_max_active_frames() const
  {
    return fragment_initial_max_active_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_nack_indexes_requested() const
  {
    return fragment_nack_indexes_requested_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_nack_index_budget_reductions() const
  {
    return fragment_nack_index_budget_reductions_.load(
      std::memory_order_relaxed);
  }

  size_t fragment_nack_max_sweep_indexes_requested() const
  {
    return fragment_nack_max_sweep_indexes_requested_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_nack_sweep_budget_exhaustions() const
  {
    return fragment_nack_sweep_budget_exhaustions_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_progressive_nacks_sent() const
  {
    return fragment_progressive_nacks_sent_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_progress_grace_deferrals() const
  {
    return fragment_progress_grace_deferrals_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_active_assemblies()
  {
    std::lock_guard<std::mutex> lock(fragment_mutex_);
    return static_cast<std::uint64_t>(fragment_assemblies_.size());
  }

  std::uint64_t fragment_active_missing_indexes()
  {
    std::lock_guard<std::mutex> lock(fragment_mutex_);
    std::uint64_t missing = 0;
    for (const auto & item : fragment_assemblies_) {
      const FragmentAssembly & assembly = item.second;
      missing += static_cast<std::uint64_t>(
        assembly.fragment_count - std::min(
          assembly.received_count, assembly.fragment_count));
    }
    return missing;
  }

  std::uint64_t fragment_nack_exhausted_assemblies()
  {
    std::lock_guard<std::mutex> lock(fragment_mutex_);
    return static_cast<std::uint64_t>(std::count_if(
      fragment_assemblies_.begin(),
      fragment_assemblies_.end(),
      [this](const auto & item) {
        return item.second.nack_count >=
               static_cast<size_t>(fragment_nack_max_requests_);
      }));
  }

  std::uint64_t fragment_oldest_assembly_age_ms()
  {
    const std::int64_t now_ns = monotonic_timestamp_ns();
    std::lock_guard<std::mutex> lock(fragment_mutex_);
    std::int64_t oldest_ns = now_ns;
    bool found = false;
    for (const auto & item : fragment_assemblies_) {
      if (item.second.first_update_ns > 0) {
        oldest_ns = found ?
          std::min(oldest_ns, item.second.first_update_ns) :
          item.second.first_update_ns;
        found = true;
      }
    }
    return found && now_ns > oldest_ns ?
           static_cast<std::uint64_t>((now_ns - oldest_ns) / 1000000ll) : 0;
  }

  std::uint64_t fragment_history_request_exhausted()
  {
    std::lock_guard<std::mutex> lock(fragment_history_mutex_);
    return static_cast<std::uint64_t>(std::count_if(
      fragment_history_.begin(),
      fragment_history_.end(),
      [this](const auto & item) {
        return std::any_of(
          item.second.request_count_by_target.begin(),
          item.second.request_count_by_target.end(),
          [this](const auto & target_count) {
            return target_count.second >=
                   static_cast<size_t>(fragment_nack_max_requests_);
          });
      }));
  }

  std::uint64_t fragment_assembly_evictions() const
  {
    return fragment_assembly_evictions_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_assembly_oversize_drops() const
  {
    return fragment_assembly_oversize_drops_.load(std::memory_order_relaxed);
  }

  std::uint64_t fragment_assembly_metadata_mismatch_drops() const
  {
    return fragment_assembly_metadata_mismatch_drops_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_assembly_ttl_expirations() const
  {
    return fragment_assembly_ttl_expirations_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t fragment_assembly_ttl_expired_missing_indexes() const
  {
    return fragment_assembly_ttl_expired_missing_indexes_.load(
      std::memory_order_relaxed);
  }

  std::uint64_t test_dropped_frames() const
  {
    return test_dropped_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t adaptive_failovers() const
  {
    return adaptive_failovers_.load(std::memory_order_relaxed);
  }

  std::uint64_t adaptive_unicast_frames() const
  {
    return adaptive_unicast_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t adaptive_redundant_frames() const
  {
    return adaptive_redundant_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t fleet_plan_frames() const
  {
    return fleet_plan_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t fleet_plan_redundant_frames() const
  {
    return fleet_plan_redundant_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t fleet_plan_selected_path_count() const
  {
    return fleet_plan_selected_path_count_.load(std::memory_order_relaxed);
  }

  std::string fleet_plan_last_paths() const
  {
    std::lock_guard<std::mutex> lock(fleet_plan_mutex_);
    return fleet_plan_last_paths_;
  }

  std::uint64_t repair_plan_frames() const
  {
    return repair_plan_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t repair_plan_redundant_frames() const
  {
    return repair_plan_redundant_frames_.load(std::memory_order_relaxed);
  }

  std::uint64_t repair_plan_selected_path_count() const
  {
    return repair_plan_selected_path_count_.load(std::memory_order_relaxed);
  }

  std::uint64_t repair_budget_exhausted() const
  {
    return repair_budget_exhausted_.load(std::memory_order_relaxed);
  }

  std::uint64_t repair_requests_coalesced() const
  {
    return repair_requests_coalesced_.load(std::memory_order_relaxed);
  }

  std::uint64_t repair_sequence_attempt_limit_exhausted() const
  {
    return repair_sequence_attempt_limit_exhausted_.load(std::memory_order_relaxed);
  }

  std::uint64_t repair_not_admitted() const
  {
    return repair_not_admitted_.load(std::memory_order_relaxed);
  }

  int repair_retransmission_budget() const
  {
    return repair_retransmission_budget_;
  }

  int repair_min_interval_ms() const
  {
    return repair_min_interval_ms_;
  }

  int repair_max_attempts_per_sequence() const
  {
    return repair_max_attempts_per_sequence_;
  }

  std::string repair_plan_last_paths() const
  {
    std::lock_guard<std::mutex> lock(repair_plan_mutex_);
    return repair_plan_last_paths_;
  }

  std::uint64_t adaptive_peer_score_sum() const
  {
    std::lock_guard<std::mutex> lock(adaptive_mutex_);
    std::uint64_t sum = 0;
    for (const std::uint64_t score : adaptive_peer_scores_) {
      sum += score;
    }
    return sum;
  }

  size_t adaptive_selected_peer_index() const
  {
    if (peer_addresses_.empty()) {
      return 0;
    }
    return adaptive_selected_peer_index_.load(std::memory_order_relaxed) % peer_addresses_.size();
  }

  const std::string & peer_policy() const
  {
    return peer_policy_;
  }

  void record_ack_nack_received()
  {
    ack_nack_received_.fetch_add(1, std::memory_order_relaxed);
  }

  void record_ack_nack_feedback(const rmw_fleetqox_cpp::AckNackFrame & frame)
  {
    if (frame.duplicate) {
      ack_nack_duplicate_received_.fetch_add(1, std::memory_order_relaxed);
    }
    if (frame.out_of_order) {
      ack_nack_out_of_order_received_.fetch_add(1, std::memory_order_relaxed);
    }
    const bool adaptive_policy =
      peer_policy_ == "adaptive_failover" ||
      peer_policy_ == "adaptive_score" ||
      peer_policy_ == "adaptive_qos";
    if (!adaptive_policy || peer_addresses_.size() < 2) {
      return;
    }
    if (frame.missing_sequence_ranges.empty()) {
      if (peer_policy_ == "adaptive_score" || peer_policy_ == "adaptive_qos") {
        std::lock_guard<std::mutex> lock(adaptive_mutex_);
        const size_t selected = adaptive_selected_peer_index();
        if (selected < adaptive_peer_scores_.size() && adaptive_peer_scores_[selected] > 0) {
          --adaptive_peer_scores_[selected];
        }
      }
      return;
    }
    std::uint64_t missing_count = 0;
    for (const auto & range : frame.missing_sequence_ranges) {
      if (range.second >= range.first) {
        missing_count += range.second - range.first + 1;
      }
    }
    if (missing_count == 0) {
      missing_count = 1;
    }
    {
      std::ostringstream key;
      key << frame.publisher_id << "|";
      for (const auto & range : frame.missing_sequence_ranges) {
        key << range.first << "-" << range.second << ",";
      }
      std::lock_guard<std::mutex> lock(adaptive_mutex_);
      const std::string nack_key = key.str();
      if (nack_key == last_adaptive_nack_key_) {
        return;
      }
      last_adaptive_nack_key_ = nack_key;
      const size_t previous = adaptive_selected_peer_index();
      if ((peer_policy_ == "adaptive_score" || peer_policy_ == "adaptive_qos") &&
        previous < adaptive_peer_scores_.size())
      {
        adaptive_peer_scores_[previous] += 1000u * missing_count;
      }
      size_t next = (previous + 1) % peer_addresses_.size();
      if (peer_policy_ == "adaptive_score" || peer_policy_ == "adaptive_qos") {
        next = best_scored_peer_index_locked();
      }
      adaptive_selected_peer_index_.store(next, std::memory_order_relaxed);
      if (next != previous) {
        adaptive_failovers_.fetch_add(1, std::memory_order_relaxed);
      }
    }
  }

  void record_unrecoverable_loss_notice_received()
  {
    unrecoverable_loss_notices_received_.fetch_add(1, std::memory_order_relaxed);
  }

  bool adaptive_data_unicast_enabled() const
  {
    return peer_policy_ == "adaptive_failover" ||
           peer_policy_ == "adaptive_score" ||
           peer_policy_ == "adaptive_qos";
  }

  static std::int64_t duration_ns(const rmw_time_t & duration)
  {
    if (duration.sec == 0 && duration.nsec == 0) {
      return 0;
    }
    constexpr std::uint64_t kNanosecondsPerSecond = 1000000000ull;
    if (duration.sec > static_cast<std::uint64_t>(
        std::numeric_limits<std::int64_t>::max() / static_cast<std::int64_t>(kNanosecondsPerSecond)))
    {
      return std::numeric_limits<std::int64_t>::max();
    }
    const auto sec_ns = static_cast<std::int64_t>(duration.sec * kNanosecondsPerSecond);
    const auto nsec = static_cast<std::int64_t>(duration.nsec);
    if (std::numeric_limits<std::int64_t>::max() - sec_ns < nsec) {
      return std::numeric_limits<std::int64_t>::max();
    }
    return sec_ns + nsec;
  }

  bool qos_prefers_redundancy(const rmw_qos_profile_t * qos) const
  {
    if (peer_policy_ != "adaptive_qos" || qos == nullptr || peer_addresses_.size() < 2) {
      return false;
    }
    const std::int64_t deadline_ns = duration_ns(qos->deadline);
    return deadline_ns > 0 &&
           adaptive_redundant_deadline_ns_ > 0 &&
           deadline_ns <= adaptive_redundant_deadline_ns_;
  }

  size_t best_scored_peer_index_locked() const
  {
    if (adaptive_peer_scores_.empty()) {
      return adaptive_selected_peer_index();
    }
    size_t best = 0;
    std::uint64_t best_score = adaptive_peer_scores_[0];
    for (size_t i = 1; i < adaptive_peer_scores_.size(); ++i) {
      if (adaptive_peer_scores_[i] < best_score) {
        best = i;
        best_score = adaptive_peer_scores_[i];
      }
    }
    return best;
  }

  bool ready() const
  {
    return ready_;
  }

  const std::string & init_error() const
  {
    return init_error_;
  }

  const std::string & bound_endpoint() const
  {
    return bound_endpoint_;
  }

  const std::string & transport_mode() const
  {
    return transport_mode_;
  }

  std::uint64_t quic_gateway_frames_sent() const
  {
    return quic_gateway_transport_.frames_sent();
  }

  std::uint64_t quic_gateway_bytes_sent() const
  {
    return quic_gateway_transport_.bytes_sent();
  }

  std::uint64_t quic_gateway_frames_received() const
  {
    return quic_gateway_transport_.frames_received();
  }

  std::uint64_t quic_gateway_bytes_received() const
  {
    return quic_gateway_transport_.bytes_received();
  }

  std::uint64_t quic_gateway_frames_enqueued() const
  {
    return quic_gateway_transport_.frames_enqueued();
  }

  std::uint64_t quic_gateway_frames_failed() const
  {
    return quic_gateway_transport_.frames_failed();
  }

  std::uint64_t quic_gateway_frames_dropped() const
  {
    return quic_gateway_transport_.frames_dropped();
  }

  std::size_t quic_gateway_queue_depth() const
  {
    return quic_gateway_transport_.queue_depth();
  }

  std::size_t quic_gateway_max_queue_frames() const
  {
    return quic_gateway_transport_.max_queue_frames();
  }

  bool quic_gateway_async_enabled() const
  {
    return quic_gateway_transport_.async_enabled();
  }

  int quic_gateway_last_exit_code() const
  {
    return quic_gateway_transport_.last_exit_code();
  }

  std::string quic_gateway_uri() const
  {
    return quic_gateway_transport_.endpoint_uri();
  }

  std::string quic_gateway_backend() const
  {
    return quic_gateway_transport_.backend_name();
  }

  bool quic_gateway_subprocess_backed() const
  {
    return quic_gateway_transport_.subprocess_backed();
  }

  std::uint64_t quic_gateway_connections_created() const
  {
    return quic_gateway_transport_.connections_created();
  }

  std::uint64_t quic_gateway_handshakes_completed() const
  {
    return quic_gateway_transport_.handshakes_completed();
  }

  std::uint64_t quic_gateway_streams_opened() const
  {
    return quic_gateway_transport_.streams_opened();
  }

  std::uint64_t quic_gateway_connection_reuse_count() const
  {
    return quic_gateway_transport_.connection_reuse_count();
  }

  std::uint64_t quic_gateway_packets_sent() const
  {
    return quic_gateway_transport_.packets_sent();
  }

  std::uint64_t quic_gateway_packets_received() const
  {
    return quic_gateway_transport_.packets_received();
  }

  std::uint64_t quic_gateway_reconnects() const
  {
    return quic_gateway_transport_.reconnects();
  }

  std::uint64_t quic_gateway_concurrent_stream_pairs() const
  {
    return quic_gateway_transport_.concurrent_stream_pairs();
  }

  std::uint64_t quic_gateway_max_concurrent_request_streams() const
  {
    return quic_gateway_transport_.max_concurrent_request_streams();
  }

  std::uint64_t quic_gateway_concurrent_api_operation_pairs() const
  {
    return quic_gateway_transport_.concurrent_api_operation_pairs();
  }

  std::uint64_t quic_gateway_max_concurrent_api_calls() const
  {
    return quic_gateway_transport_.max_concurrent_api_calls();
  }

  bool quic_gateway_enabled() const
  {
    return quic_gateway_transport_.enabled();
  }

  rmw_ret_t receive_quic_gateway_payload(std::string * payload)
  {
    if (payload == nullptr) {
      RMW_SET_ERROR_MSG("QUIC gateway receive payload output is null");
      return RMW_RET_INVALID_ARGUMENT;
    }
    if (!quic_gateway_transport_.enabled()) {
      return RMW_RET_UNSUPPORTED;
    }
    if (!quic_gateway_transport_.receive(payload)) {
      const std::string error = quic_gateway_transport_.error();
      RMW_SET_ERROR_MSG(error.empty() ?
        "failed to receive FleetRMW payload through QUIC gateway transport" :
        error.c_str());
      return RMW_RET_ERROR;
    }
    return RMW_RET_OK;
  }

  std::uint64_t shared_memory_frames_sent() const
  {
    return shared_memory_transport_.frames_sent();
  }

  std::uint64_t shared_memory_frames_received() const
  {
    return shared_memory_transport_.frames_received();
  }

  std::uint64_t shared_memory_overwritten_frames() const
  {
    return shared_memory_transport_.overwritten_frames();
  }

  size_t peer_count() const
  {
    return peer_addresses_.size();
  }

  rmw_ret_t send_subscription_advertisement(
    const std::string & topic_name,
    const std::string & type_name,
    std::size_t domain_id)
  {
    if (peer_addresses_.empty()) {
      return RMW_RET_OK;
    }
    const rmw_fleetqox_cpp::RouteAdvertisement advertisement{
      bound_endpoint_,
      "subscriber",
      topic_name,
      type_name,
      5000u,
      domain_id};
    return send_to_peers(rmw_fleetqox_cpp::encode_route_advertisement(advertisement));
  }

  rmw_ret_t send_graph_advertisement(
    const std::string & action,
    const std::string & entity_kind,
    const std::string & node_name,
    const std::string & node_namespace,
    const std::string & topic_name,
    const std::string & type_name,
    const std::string & endpoint_id,
    const std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> & endpoint_gid,
    const rmw_qos_profile_t & qos,
    std::size_t domain_id)
  {
    if (peer_addresses_.empty()) {
      return RMW_RET_OK;
    }
    const rmw_fleetqox_cpp::GraphAdvertisement advertisement{
      endpoint_id,
      action,
      entity_kind,
      node_name,
      node_namespace,
      topic_name,
      type_name,
      hex_encode_bytes(endpoint_gid.data(), endpoint_gid.size()),
      graph_qos_from_rmw(qos),
      5000u,
      domain_id};
    return send_to_peers(rmw_fleetqox_cpp::encode_graph_advertisement(advertisement));
  }

private:
  static constexpr size_t kMaxUdpPayloadBytes = 65507;
  static constexpr size_t kUdpFragmentChunkBytes = 60000;
  static constexpr std::int64_t kFragmentHistoryTtlNs = 60000000000ll;
  static constexpr size_t kMaxFragmentRepairIndexesPerRequest = 64;
  static constexpr size_t kFleetFragmentRepairIndexesPerSweep = 512;
  static constexpr const char * kFragmentPrefix = "FLEETQOX_FRAGMENT_V1|";
  static constexpr const char * kRepairFragmentPrefix =
    "FLEETQOX_REPAIR_FRAGMENT_V1|";
  static constexpr const char * kRepairFragmentNackPrefix =
    "FLEETQOX_REPAIR_FRAGMENT_NACK_V1|";
  static constexpr const char * kRepairFragmentCompletionPrefix =
    "FLEETQOX_REPAIR_FRAGMENT_END_V1|";

  struct FragmentAssembly
  {
    size_t total_size{0};
    size_t fragment_count{0};
    size_t received_count{0};
    size_t highest_received_index{0};
    std::vector<std::string> chunks;
    std::vector<bool> received;
    sockaddr_in source{};
    bool source_available{false};
    std::int64_t first_update_ns{0};
    std::int64_t last_update_ns{0};
    std::int64_t last_nack_ns{0};
    size_t nack_count{0};
    std::string fragment_id;
    bool repair_capable{false};
    bool sender_complete_observed{false};
  };

  struct FragmentRepairHistory
  {
    std::shared_ptr<const std::string> payload;
    std::vector<sockaddr_in> targets;
    size_t chunk_bytes{0};
    size_t fragment_count{0};
    bool is_data_frame{false};
    std::int64_t last_update_ns{0};
    std::unordered_map<std::string, size_t> request_count_by_target;
  };

  struct PendingFragmentSend
  {
    std::shared_ptr<const std::string> payload;
    std::vector<sockaddr_in> targets;
    std::string fragment_id;
    size_t chunk_bytes{0};
    size_t fragment_count{0};
    size_t fragment_index{0};
    bool is_data_frame{false};
    bool selective_retransmission{false};
    std::string repair_key;
    bool initial_send_completion{false};
  };

  std::vector<sockaddr_in> frame_targets(bool include_local) const
  {
    std::vector<sockaddr_in> targets;
    if (include_local && address_.sin_addr.s_addr != htonl(INADDR_ANY)) {
      targets.push_back(address_);
    }
    for (const sockaddr_in & peer : peer_addresses_) {
      if (std::none_of(targets.begin(), targets.end(), [&](const sockaddr_in & target) {
          return endpoints_match(target, peer);
        }))
      {
        targets.push_back(peer);
      }
    }
    return targets;
  }

  std::vector<sockaddr_in> data_frame_targets(
    bool include_local,
    const rmw_qos_profile_t * qos,
    const std::optional<rmw_fleetqox_cpp::DataFrame> & frame)
  {
    if (peer_policy_ == "fleet_plan" && frame.has_value()) {
      const std::vector<sockaddr_in> planned_targets = fleet_plan_targets(*frame);
      if (!planned_targets.empty()) {
        fleet_plan_frames_.fetch_add(1, std::memory_order_relaxed);
        fleet_plan_selected_path_count_.fetch_add(planned_targets.size(), std::memory_order_relaxed);
        if (planned_targets.size() > 1) {
          fleet_plan_redundant_frames_.fetch_add(1, std::memory_order_relaxed);
        }
        return planned_targets;
      }
    }
    if (qos_prefers_redundancy(qos)) {
      adaptive_redundant_frames_.fetch_add(1, std::memory_order_relaxed);
      return frame_targets(include_local);
    }
    if (adaptive_data_unicast_enabled() && !peer_addresses_.empty()) {
      size_t selected = adaptive_selected_peer_index();
      if (peer_policy_ == "adaptive_score" || peer_policy_ == "adaptive_qos") {
        std::lock_guard<std::mutex> lock(adaptive_mutex_);
        selected = best_scored_peer_index_locked();
        adaptive_selected_peer_index_.store(selected, std::memory_order_relaxed);
      }
      adaptive_unicast_frames_.fetch_add(1, std::memory_order_relaxed);
      return std::vector<sockaddr_in>{peer_addresses_[selected]};
    }
    return frame_targets(include_local);
  }

  std::vector<sockaddr_in> fleet_plan_targets(const rmw_fleetqox_cpp::DataFrame & frame)
  {
    const std::vector<std::string> path_ids = fleet_plan_path_ids_for_topic(frame.topic);
    if (path_ids.empty()) {
      return {};
    }
    std::vector<sockaddr_in> targets;
    std::ostringstream selected_paths;
    for (const std::string & path_id : path_ids) {
      for (size_t i = 0; i < peer_addresses_.size() && i < peer_path_ids_.size(); ++i) {
        if (peer_path_ids_[i] != path_id) {
          continue;
        }
        if (std::none_of(targets.begin(), targets.end(), [&](const sockaddr_in & target) {
            return endpoints_match(target, peer_addresses_[i]);
          }))
        {
          if (selected_paths.tellp() > 0) {
            selected_paths << ",";
          }
          selected_paths << path_id;
          targets.push_back(peer_addresses_[i]);
        }
      }
    }
    if (!targets.empty()) {
      std::lock_guard<std::mutex> lock(fleet_plan_mutex_);
      fleet_plan_last_paths_ = selected_paths.str();
    }
    return targets;
  }

  std::vector<std::string> fleet_plan_path_ids_for_topic(const std::string & topic) const
  {
    refresh_fleet_path_plan_from_file();
    std::lock_guard<std::mutex> lock(fleet_plan_mutex_);
    for (const FleetPathPlanRule & rule : fleet_path_plan_) {
      if (rule.topic == topic) {
        return rule.path_ids;
      }
    }
    for (const FleetPathPlanRule & rule : fleet_path_plan_) {
      if (rule.topic == "*") {
        return rule.path_ids;
      }
    }
    return {};
  }

  void refresh_fleet_path_plan_from_file() const
  {
    if (fleet_path_plan_file_.empty()) {
      return;
    }
    std::ifstream input(fleet_path_plan_file_);
    if (!input) {
      return;
    }
    std::ostringstream buffer;
    buffer << input.rdbuf();
    const std::string plan_text = trim_copy(buffer.str());
    std::lock_guard<std::mutex> lock(fleet_plan_mutex_);
    if (plan_text.empty() && !fleet_path_plan_file_contents_.empty()) {
      return;
    }
    if (plan_text == fleet_path_plan_file_contents_) {
      return;
    }
    fleet_path_plan_ = parse_fleet_path_plan(plan_text.c_str());
    fleet_path_plan_file_contents_ = plan_text;
  }

  std::vector<sockaddr_in> repair_targets_for_path_ids(
    const std::vector<std::string> & path_ids)
  {
    if (path_ids.empty()) {
      return {};
    }
    std::vector<sockaddr_in> targets;
    std::ostringstream selected_paths;
    for (const std::string & path_id : path_ids) {
      for (size_t i = 0; i < peer_addresses_.size() && i < peer_path_ids_.size(); ++i) {
        if (peer_path_ids_[i] != path_id) {
          continue;
        }
        if (std::none_of(targets.begin(), targets.end(), [&](const sockaddr_in & target) {
            return endpoints_match(target, peer_addresses_[i]);
          }))
        {
          if (selected_paths.tellp() > 0) {
            selected_paths << ",";
          }
          selected_paths << path_id;
          targets.push_back(peer_addresses_[i]);
        }
      }
    }
    if (!targets.empty()) {
      std::lock_guard<std::mutex> lock(repair_plan_mutex_);
      repair_plan_last_paths_ = selected_paths.str();
    }
    return targets;
  }

  std::optional<FleetRepairPlanRule> repair_plan_rule_for_frame(
    const rmw_fleetqox_cpp::DataFrame & frame) const
  {
    refresh_repair_path_plan_from_file();
    std::lock_guard<std::mutex> lock(repair_plan_mutex_);
    for (const FleetRepairPlanRule & rule : repair_path_plan_) {
      if (rule.topic == frame.topic && repair_rule_admits_sequence(rule, frame.source_sequence_number)) {
        return rule;
      }
    }
    for (const FleetRepairPlanRule & rule : repair_path_plan_) {
      if (rule.topic == "*" && repair_rule_admits_sequence(rule, frame.source_sequence_number)) {
        return rule;
      }
    }
    return std::nullopt;
  }

  bool repair_plan_configured() const
  {
    refresh_repair_path_plan_from_file();
    std::lock_guard<std::mutex> lock(repair_plan_mutex_);
    return repair_admission_strict_ || !repair_path_plan_.empty();
  }

  static bool repair_rule_admits_sequence(
    const FleetRepairPlanRule & rule,
    std::uint64_t sequence)
  {
    return rule.source_sequences.empty() ||
           std::find(
      rule.source_sequences.begin(),
      rule.source_sequences.end(),
      sequence) != rule.source_sequences.end();
  }

  void refresh_repair_path_plan_from_file() const
  {
    if (repair_path_plan_file_.empty()) {
      return;
    }
    std::ifstream input(repair_path_plan_file_);
    if (!input) {
      return;
    }
    std::ostringstream buffer;
    buffer << input.rdbuf();
    const std::string plan_text = trim_copy(buffer.str());
    std::lock_guard<std::mutex> lock(repair_plan_mutex_);
    if (plan_text.empty() && !repair_path_plan_file_contents_.empty()) {
      return;
    }
    if (plan_text == repair_path_plan_file_contents_) {
      return;
    }
    repair_path_plan_ = parse_fleet_repair_plan(plan_text.c_str());
    repair_path_plan_file_contents_ = plan_text;
  }

  static int hex_nibble(char value)
  {
    if (value >= '0' && value <= '9') {
      return value - '0';
    }
    if (value >= 'a' && value <= 'f') {
      return value - 'a' + 10;
    }
    if (value >= 'A' && value <= 'F') {
      return value - 'A' + 10;
    }
    return -1;
  }

  bool configure_udp_aead()
  {
    const char * require_env = std::getenv("FLEETQOX_RMW_UDP_AEAD_REQUIRE");
    udp_aead_required_ = require_env != nullptr &&
      (trim_copy(require_env) == "1" || trim_copy(require_env) == "true" ||
      trim_copy(require_env) == "yes");
    const char * key_env = std::getenv("FLEETQOX_RMW_UDP_AEAD_KEY_HEX");
    if (key_env == nullptr || key_env[0] == '\0') {
      if (udp_aead_required_) {
        init_error_ = "FLEETQOX_RMW_UDP_AEAD_REQUIRE needs a 32-byte hex key";
        return false;
      }
      return true;
    }
    const std::string key_text = trim_copy(key_env);
    if (key_text.size() != udp_aead_key_.size() * 2) {
      init_error_ = "FLEETQOX_RMW_UDP_AEAD_KEY_HEX must contain 64 hex characters";
      return false;
    }
    for (size_t index = 0; index < udp_aead_key_.size(); ++index) {
      const int high = hex_nibble(key_text[index * 2]);
      const int low = hex_nibble(key_text[index * 2 + 1]);
      if (high < 0 || low < 0) {
        init_error_ = "FLEETQOX_RMW_UDP_AEAD_KEY_HEX contains a non-hex character";
        return false;
      }
      udp_aead_key_[index] = static_cast<unsigned char>((high << 4) | low);
    }
    if (RAND_bytes(
        udp_aead_nonce_prefix_.data(),
        static_cast<int>(udp_aead_nonce_prefix_.size())) != 1 ||
      RAND_bytes(
        udp_aead_session_salt_.data(),
        static_cast<int>(udp_aead_session_salt_.size())) != 1)
    {
      init_error_ = openssl_error_text("udp_aead_random_material_generation_failed");
      return false;
    }
    if (!derive_udp_aead_session_key(
        udp_aead_session_salt_, &udp_aead_session_key_))
    {
      init_error_ = openssl_error_text("udp_aead_session_key_derivation_failed");
      return false;
    }
    udp_aead_session_keys_derived_.fetch_add(1, std::memory_order_relaxed);
    udp_aead_session_key_rotate_frames_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_UDP_SESSION_KEY_ROTATE_FRAMES", 0, 1000000);
    const char * tamper_env = std::getenv("FLEETQOX_RMW_UDP_AEAD_TAMPER_OUTBOUND_ONCE");
    udp_aead_tamper_outbound_once_ = tamper_env != nullptr &&
      (trim_copy(tamper_env) == "1" || trim_copy(tamper_env) == "true" ||
      trim_copy(tamper_env) == "yes");
    udp_aead_enabled_ = true;
    return true;
  }

  bool derive_udp_aead_session_key(
    const std::array<unsigned char, 16> & salt,
    std::array<unsigned char, 32> * session_key) const
  {
    if (session_key == nullptr) {
      return false;
    }
    std::array<unsigned char, EVP_MAX_MD_SIZE> pseudorandom_key{};
    unsigned int pseudorandom_key_size = 0;
    if (HMAC(
        EVP_sha256(), salt.data(), static_cast<int>(salt.size()),
        udp_aead_key_.data(), udp_aead_key_.size(),
        pseudorandom_key.data(), &pseudorandom_key_size) == nullptr ||
      pseudorandom_key_size == 0)
    {
      return false;
    }
    constexpr char kInfo[] = "FleetRMW-UDP-AEAD-session-v1";
    std::array<unsigned char, sizeof(kInfo)> expand_input{};
    std::memcpy(expand_input.data(), kInfo, sizeof(kInfo) - 1);
    expand_input.back() = 1;
    std::array<unsigned char, EVP_MAX_MD_SIZE> expanded{};
    unsigned int expanded_size = 0;
    if (HMAC(
        EVP_sha256(), pseudorandom_key.data(),
        static_cast<int>(pseudorandom_key_size),
        expand_input.data(), expand_input.size(),
        expanded.data(), &expanded_size) == nullptr ||
      expanded_size < session_key->size())
    {
      return false;
    }
    std::copy_n(expanded.begin(), session_key->size(), session_key->begin());
    return true;
  }

  bool current_udp_aead_session_material(
    std::array<unsigned char, 16> * salt,
    std::array<unsigned char, 32> * session_key)
  {
    if (salt == nullptr || session_key == nullptr) {
      return false;
    }
    std::lock_guard<std::mutex> lock(udp_aead_session_mutex_);
    if (udp_aead_session_key_rotate_frames_ > 0 &&
      udp_aead_session_frames_ >=
      static_cast<std::uint64_t>(udp_aead_session_key_rotate_frames_))
    {
      if (RAND_bytes(
          udp_aead_session_salt_.data(),
          static_cast<int>(udp_aead_session_salt_.size())) != 1 ||
        !derive_udp_aead_session_key(
          udp_aead_session_salt_, &udp_aead_session_key_))
      {
        return false;
      }
      udp_aead_session_frames_ = 0;
      udp_aead_session_keys_derived_.fetch_add(1, std::memory_order_relaxed);
      udp_aead_session_key_rotations_.fetch_add(1, std::memory_order_relaxed);
    }
    if (udp_aead_session_frames_ > 0) {
      udp_aead_session_key_reuses_.fetch_add(1, std::memory_order_relaxed);
    }
    ++udp_aead_session_frames_;
    *salt = udp_aead_session_salt_;
    *session_key = udp_aead_session_key_;
    return true;
  }

  bool received_udp_aead_session_key(
    const unsigned char * salt_data,
    std::array<unsigned char, 32> * session_key)
  {
    if (salt_data == nullptr || session_key == nullptr) {
      return false;
    }
    std::array<unsigned char, 16> salt{};
    std::copy_n(salt_data, salt.size(), salt.begin());
    const std::string cache_key(
      reinterpret_cast<const char *>(salt.data()), salt.size());
    std::lock_guard<std::mutex> lock(udp_aead_received_session_mutex_);
    const auto found = udp_aead_received_session_keys_.find(cache_key);
    if (found != udp_aead_received_session_keys_.end()) {
      *session_key = found->second;
      udp_aead_session_key_reuses_.fetch_add(1, std::memory_order_relaxed);
      return true;
    }
    if (!derive_udp_aead_session_key(salt, session_key)) {
      return false;
    }
    udp_aead_received_session_keys_[cache_key] = *session_key;
    udp_aead_received_session_order_.push_back(cache_key);
    while (udp_aead_received_session_order_.size() > 256) {
      udp_aead_received_session_keys_.erase(
        udp_aead_received_session_order_.front());
      udp_aead_received_session_order_.pop_front();
    }
    udp_aead_session_keys_derived_.fetch_add(1, std::memory_order_relaxed);
    return true;
  }

  static void append_u32_be(std::string * output, std::uint32_t value)
  {
    const std::uint32_t network_value = htonl(value);
    output->append(
      reinterpret_cast<const char *>(&network_value), sizeof(network_value));
  }

  static bool read_u32_be(
    const std::string & input, size_t offset, std::uint32_t * value)
  {
    if (value == nullptr || offset + sizeof(std::uint32_t) > input.size()) {
      return false;
    }
    std::uint32_t network_value = 0;
    std::memcpy(&network_value, input.data() + offset, sizeof(network_value));
    *value = ntohl(network_value);
    return true;
  }

  bool configure_udp_peer_auth()
  {
    const char * require_env = std::getenv("FLEETQOX_RMW_UDP_PEER_AUTH_REQUIRE");
    udp_peer_auth_required_ = require_env != nullptr &&
      (trim_copy(require_env) == "1" || trim_copy(require_env) == "true" ||
      trim_copy(require_env) == "yes");
    if (!udp_peer_auth_required_) {
      return true;
    }
    if (!udp_aead_enabled_) {
      init_error_ = "UDP peer authentication requires UDP AES-256-GCM protection";
      return false;
    }
    const Sros2IdentityCredentials & credentials = sros2_identity_credentials();
    if (!credentials.configured || !credentials.valid) {
      init_error_ = credentials.error.empty() ?
        "UDP peer authentication requires valid SROS2 identity credentials" :
        credentials.error;
      return false;
    }

    BIO * certificate_bio = BIO_new_file(credentials.certificate_path.c_str(), "rb");
    if (certificate_bio == nullptr) {
      init_error_ = openssl_error_text("udp_peer_auth_certificate_open_failed");
      return false;
    }
    udp_peer_auth_local_certificate_ =
      PEM_read_bio_X509(certificate_bio, nullptr, nullptr, nullptr);
    BIO_free(certificate_bio);
    if (udp_peer_auth_local_certificate_ == nullptr) {
      init_error_ = openssl_error_text("udp_peer_auth_certificate_parse_failed");
      return false;
    }
    BIO * private_key_bio = BIO_new_file(credentials.private_key_path.c_str(), "rb");
    if (private_key_bio == nullptr) {
      init_error_ = openssl_error_text("udp_peer_auth_private_key_open_failed");
      return false;
    }
    udp_peer_auth_local_private_key_ =
      PEM_read_bio_PrivateKey(private_key_bio, nullptr, nullptr, nullptr);
    BIO_free(private_key_bio);
    if (udp_peer_auth_local_private_key_ == nullptr ||
      X509_check_private_key(
        udp_peer_auth_local_certificate_, udp_peer_auth_local_private_key_) != 1)
    {
      init_error_ = openssl_error_text("udp_peer_auth_private_key_mismatch");
      return false;
    }
    udp_peer_auth_trust_store_ = X509_STORE_new();
    if (udp_peer_auth_trust_store_ == nullptr ||
      X509_STORE_load_locations(
        udp_peer_auth_trust_store_, credentials.identity_ca_path.c_str(), nullptr) != 1)
    {
      init_error_ = openssl_error_text("udp_peer_auth_identity_ca_load_failed");
      return false;
    }
    const char * crl_env = std::getenv("FLEETQOX_RMW_SROS2_IDENTITY_CRL_FILE");
    if (crl_env != nullptr && crl_env[0] != '\0') {
      BIO * crl_bio = BIO_new_file(crl_env, "rb");
      X509_CRL * crl = crl_bio == nullptr ? nullptr :
        PEM_read_bio_X509_CRL(crl_bio, nullptr, nullptr, nullptr);
      BIO_free(crl_bio);
      if (crl == nullptr ||
        X509_STORE_add_crl(udp_peer_auth_trust_store_, crl) != 1 ||
        X509_STORE_set_flags(udp_peer_auth_trust_store_, X509_V_FLAG_CRL_CHECK) != 1)
      {
        X509_CRL_free(crl);
        init_error_ = openssl_error_text("udp_peer_auth_identity_crl_load_failed");
        return false;
      }
      X509_CRL_free(crl);
      udp_peer_auth_crl_enabled_ = true;
    }
    const int certificate_der_size =
      i2d_X509(udp_peer_auth_local_certificate_, nullptr);
    if (certificate_der_size <= 0) {
      init_error_ = openssl_error_text("udp_peer_auth_certificate_der_failed");
      return false;
    }
    udp_peer_auth_local_certificate_der_.resize(
      static_cast<size_t>(certificate_der_size));
    unsigned char * certificate_der_output =
      reinterpret_cast<unsigned char *>(udp_peer_auth_local_certificate_der_.data());
    if (i2d_X509(
        udp_peer_auth_local_certificate_, &certificate_der_output) != certificate_der_size)
    {
      init_error_ = openssl_error_text("udp_peer_auth_certificate_der_failed");
      return false;
    }
    const char * identities_env = std::getenv("FLEETQOX_RMW_UDP_PEER_IDENTITIES");
    const std::string identities = identities_env == nullptr ? "*" : identities_env;
    size_t start = 0;
    while (start <= identities.size()) {
      const size_t comma = identities.find(',', start);
      const std::string identity = trim_copy(identities.substr(
        start, comma == std::string::npos ? std::string::npos : comma - start));
      if (!identity.empty()) {
        udp_peer_auth_allowed_identities_.push_back(identity);
      }
      if (comma == std::string::npos) {
        break;
      }
      start = comma + 1;
    }
    if (udp_peer_auth_allowed_identities_.empty()) {
      init_error_ = "FLEETQOX_RMW_UDP_PEER_IDENTITIES has no valid identity";
      return false;
    }
    const char * tamper_env =
      std::getenv("FLEETQOX_RMW_UDP_PEER_AUTH_TAMPER_OUTBOUND_ONCE");
    udp_peer_auth_tamper_outbound_once_ = tamper_env != nullptr &&
      (trim_copy(tamper_env) == "1" || trim_copy(tamper_env) == "true" ||
      trim_copy(tamper_env) == "yes");
    udp_peer_auth_enabled_ = true;
    return true;
  }

  bool udp_peer_identity_allowed(const std::string & identity) const
  {
    return std::any_of(
      udp_peer_auth_allowed_identities_.begin(),
      udp_peer_auth_allowed_identities_.end(),
      [&identity](const std::string & pattern) {
        return wildcard_match(pattern, identity);
      });
  }

  bool protect_udp_peer_authenticated_payload(
    const std::string & payload,
    bool is_data_frame,
    std::string * authenticated_payload)
  {
    if (authenticated_payload == nullptr) {
      return false;
    }
    if (!udp_peer_auth_enabled_) {
      *authenticated_payload = payload;
      return true;
    }
    constexpr char kMagic[] = "FQPAUTH1|";
    constexpr size_t kMagicSize = sizeof(kMagic) - 1;
    std::string signed_content(kMagic, kMagicSize);
    signed_content.append(udp_peer_auth_local_certificate_der_);
    signed_content.append(payload);
    EVP_MD_CTX * digest_context = EVP_MD_CTX_new();
    if (digest_context == nullptr ||
      EVP_DigestSignInit(
        digest_context, nullptr, EVP_sha256(), nullptr,
        udp_peer_auth_local_private_key_) != 1 ||
      EVP_DigestSignUpdate(
        digest_context, signed_content.data(), signed_content.size()) != 1)
    {
      EVP_MD_CTX_free(digest_context);
      return false;
    }
    size_t signature_size = 0;
    if (EVP_DigestSignFinal(digest_context, nullptr, &signature_size) != 1 ||
      signature_size == 0 || signature_size > 65536)
    {
      EVP_MD_CTX_free(digest_context);
      return false;
    }
    std::string signature(signature_size, '\0');
    if (EVP_DigestSignFinal(
        digest_context,
        reinterpret_cast<unsigned char *>(signature.data()), &signature_size) != 1)
    {
      EVP_MD_CTX_free(digest_context);
      return false;
    }
    EVP_MD_CTX_free(digest_context);
    signature.resize(signature_size);
    if (udp_peer_auth_local_certificate_der_.size() > 65536 ||
      signature.size() > 65536)
    {
      return false;
    }
    authenticated_payload->assign(kMagic, kMagicSize);
    append_u32_be(
      authenticated_payload,
      static_cast<std::uint32_t>(udp_peer_auth_local_certificate_der_.size()));
    append_u32_be(
      authenticated_payload, static_cast<std::uint32_t>(signature.size()));
    authenticated_payload->append(udp_peer_auth_local_certificate_der_);
    const size_t signature_offset = authenticated_payload->size();
    authenticated_payload->append(signature);
    authenticated_payload->append(payload);
    udp_peer_auth_signed_frames_.fetch_add(1, std::memory_order_relaxed);
    if (udp_peer_auth_tamper_outbound_once_ && is_data_frame &&
      !udp_peer_auth_tamper_done_.exchange(true, std::memory_order_relaxed) &&
      !signature.empty())
    {
      (*authenticated_payload)[signature_offset] = static_cast<char>(
        (*authenticated_payload)[signature_offset] ^ 0x01);
    }
    return true;
  }

  bool unprotect_udp_peer_authenticated_payload(
    const std::string & payload,
    std::string * authenticated_content)
  {
    if (authenticated_content == nullptr) {
      return false;
    }
    if (!udp_peer_auth_enabled_) {
      *authenticated_content = payload;
      return true;
    }
    constexpr char kMagic[] = "FQPAUTH1|";
    constexpr size_t kMagicSize = sizeof(kMagic) - 1;
    constexpr size_t kHeaderSize = kMagicSize + 2 * sizeof(std::uint32_t);
    if (payload.size() < kHeaderSize ||
      payload.compare(0, kMagicSize, kMagic, kMagicSize) != 0)
    {
      udp_peer_auth_failures_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    std::uint32_t certificate_size = 0;
    std::uint32_t signature_size = 0;
    if (!read_u32_be(payload, kMagicSize, &certificate_size) ||
      !read_u32_be(
        payload, kMagicSize + sizeof(std::uint32_t), &signature_size) ||
      certificate_size == 0 || certificate_size > 65536 ||
      signature_size == 0 || signature_size > 65536 ||
      kHeaderSize + static_cast<size_t>(certificate_size) +
      static_cast<size_t>(signature_size) >= payload.size())
    {
      udp_peer_auth_failures_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    const auto * certificate_der = reinterpret_cast<const unsigned char *>(
      payload.data() + kHeaderSize);
    const unsigned char * certificate_der_cursor = certificate_der;
    X509 * peer_certificate = d2i_X509(
      nullptr, &certificate_der_cursor, static_cast<long>(certificate_size));
    if (peer_certificate == nullptr ||
      certificate_der_cursor != certificate_der + certificate_size)
    {
      X509_free(peer_certificate);
      udp_peer_auth_failures_.fetch_add(1, std::memory_order_relaxed);
      udp_peer_auth_chain_failures_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    X509_STORE_CTX * verify_context = X509_STORE_CTX_new();
    const bool verify_context_ready = verify_context != nullptr &&
      X509_STORE_CTX_init(
        verify_context, udp_peer_auth_trust_store_, peer_certificate, nullptr) == 1;
    const bool chain_valid = verify_context_ready && X509_verify_cert(verify_context) == 1;
    const int verify_error = verify_context_ready ?
      X509_STORE_CTX_get_error(verify_context) : X509_V_ERR_UNSPECIFIED;
    X509_STORE_CTX_free(verify_context);
    if (!chain_valid) {
      X509_free(peer_certificate);
      udp_peer_auth_failures_.fetch_add(1, std::memory_order_relaxed);
      udp_peer_auth_chain_failures_.fetch_add(1, std::memory_order_relaxed);
      if (verify_error == X509_V_ERR_CERT_REVOKED) {
        udp_peer_auth_revoked_certificate_drops_.fetch_add(1, std::memory_order_relaxed);
      }
      return false;
    }
    std::array<char, 1024> common_name{};
    const int common_name_size = X509_NAME_get_text_by_NID(
      X509_get_subject_name(peer_certificate), NID_commonName,
      common_name.data(), static_cast<int>(common_name.size()));
    const std::string peer_identity =
      common_name_size > 0 && static_cast<size_t>(common_name_size) < common_name.size() ?
      std::string(common_name.data(), static_cast<size_t>(common_name_size)) : "";
    if (peer_identity.empty() || !udp_peer_identity_allowed(peer_identity)) {
      X509_free(peer_certificate);
      udp_peer_auth_failures_.fetch_add(1, std::memory_order_relaxed);
      udp_peer_auth_identity_denied_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    EVP_PKEY * peer_public_key = X509_get_pubkey(peer_certificate);
    const size_t certificate_offset = kHeaderSize;
    const size_t signature_offset = certificate_offset + certificate_size;
    const size_t content_offset = signature_offset + signature_size;
    const std::string content = payload.substr(content_offset);
    std::string signed_content(kMagic, kMagicSize);
    signed_content.append(payload, certificate_offset, certificate_size);
    signed_content.append(content);
    EVP_MD_CTX * digest_context = EVP_MD_CTX_new();
    const bool signature_valid = peer_public_key != nullptr && digest_context != nullptr &&
      EVP_DigestVerifyInit(
        digest_context, nullptr, EVP_sha256(), nullptr, peer_public_key) == 1 &&
      EVP_DigestVerifyUpdate(
        digest_context, signed_content.data(), signed_content.size()) == 1 &&
      EVP_DigestVerifyFinal(
        digest_context,
        reinterpret_cast<const unsigned char *>(payload.data() + signature_offset),
        signature_size) == 1;
    EVP_MD_CTX_free(digest_context);
    EVP_PKEY_free(peer_public_key);
    X509_free(peer_certificate);
    if (!signature_valid) {
      udp_peer_auth_failures_.fetch_add(1, std::memory_order_relaxed);
      udp_peer_auth_signature_failures_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    {
      std::lock_guard<std::mutex> lock(udp_peer_auth_identity_mutex_);
      udp_peer_auth_last_identity_ = peer_identity;
    }
    udp_peer_auth_verified_frames_.fetch_add(1, std::memory_order_relaxed);
    *authenticated_content = content;
    return true;
  }

  bool protect_udp_payload(const std::string & plaintext, std::string * protected_payload)
  {
    if (protected_payload == nullptr) {
      return false;
    }
    if (!udp_aead_enabled_) {
      *protected_payload = plaintext;
      return true;
    }
    constexpr char kMagic[] = "FQAEAD2|";
    constexpr size_t kMagicSize = sizeof(kMagic) - 1;
    constexpr size_t kSaltSize = 16;
    constexpr size_t kNonceSize = 12;
    constexpr size_t kTagSize = 16;
    std::array<unsigned char, kNonceSize> nonce{};
    std::array<unsigned char, kSaltSize> session_salt{};
    std::array<unsigned char, 32> session_key{};
    if (!current_udp_aead_session_material(&session_salt, &session_key)) {
      return false;
    }
    std::copy(
      udp_aead_nonce_prefix_.begin(), udp_aead_nonce_prefix_.end(), nonce.begin());
    const std::uint64_t sequence =
      udp_aead_nonce_sequence_.fetch_add(1, std::memory_order_relaxed) + 1;
    for (size_t index = 0; index < sizeof(sequence); ++index) {
      nonce[udp_aead_nonce_prefix_.size() + index] = static_cast<unsigned char>(
        (sequence >> ((sizeof(sequence) - index - 1) * 8)) & 0xffu);
    }
    using CipherContext = std::unique_ptr<EVP_CIPHER_CTX, decltype(&EVP_CIPHER_CTX_free)>;
    CipherContext context(EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
    if (!context ||
      EVP_EncryptInit_ex(context.get(), EVP_aes_256_gcm(), nullptr, nullptr, nullptr) != 1 ||
      EVP_CIPHER_CTX_ctrl(
        context.get(), EVP_CTRL_GCM_SET_IVLEN, static_cast<int>(nonce.size()), nullptr) != 1 ||
      EVP_EncryptInit_ex(
        context.get(), nullptr, nullptr, session_key.data(), nonce.data()) != 1)
    {
      return false;
    }
    int output_size = 0;
    if (EVP_EncryptUpdate(
        context.get(), nullptr, &output_size,
        reinterpret_cast<const unsigned char *>(kMagic), static_cast<int>(kMagicSize)) != 1)
    {
      return false;
    }
    if (EVP_EncryptUpdate(
        context.get(), nullptr, &output_size,
        session_salt.data(), static_cast<int>(session_salt.size())) != 1)
    {
      return false;
    }
    std::string ciphertext(plaintext.size() + 16, '\0');
    int ciphertext_size = 0;
    if (EVP_EncryptUpdate(
        context.get(), reinterpret_cast<unsigned char *>(ciphertext.data()),
        &output_size, reinterpret_cast<const unsigned char *>(plaintext.data()),
        static_cast<int>(plaintext.size())) != 1)
    {
      return false;
    }
    ciphertext_size = output_size;
    if (EVP_EncryptFinal_ex(
        context.get(), reinterpret_cast<unsigned char *>(ciphertext.data()) + ciphertext_size,
        &output_size) != 1)
    {
      return false;
    }
    ciphertext_size += output_size;
    ciphertext.resize(static_cast<size_t>(ciphertext_size));
    std::array<unsigned char, kTagSize> tag{};
    if (EVP_CIPHER_CTX_ctrl(
        context.get(), EVP_CTRL_GCM_GET_TAG, static_cast<int>(tag.size()), tag.data()) != 1)
    {
      return false;
    }
    protected_payload->assign(kMagic, kMagicSize);
    protected_payload->append(
      reinterpret_cast<const char *>(session_salt.data()), session_salt.size());
    protected_payload->append(reinterpret_cast<const char *>(nonce.data()), nonce.size());
    protected_payload->append(reinterpret_cast<const char *>(tag.data()), tag.size());
    protected_payload->append(ciphertext);
    udp_aead_encrypted_frames_.fetch_add(1, std::memory_order_relaxed);
    if (udp_aead_tamper_outbound_once_ &&
      rmw_fleetqox_cpp::decode_data_frame(plaintext).has_value() &&
      !udp_aead_tamper_done_.exchange(true, std::memory_order_relaxed) &&
      protected_payload->size() > kMagicSize + kSaltSize + kNonceSize)
    {
      protected_payload->back() = static_cast<char>(protected_payload->back() ^ 0x01);
    }
    return true;
  }

  bool unprotect_udp_payload(const std::string & payload, std::string * plaintext)
  {
    if (plaintext == nullptr) {
      return false;
    }
    constexpr char kMagic[] = "FQAEAD2|";
    constexpr size_t kMagicSize = sizeof(kMagic) - 1;
    constexpr size_t kSaltSize = 16;
    constexpr size_t kNonceSize = 12;
    constexpr size_t kTagSize = 16;
    if (!udp_aead_enabled_) {
      *plaintext = payload;
      return true;
    }
    if (payload.size() < kMagicSize + kSaltSize + kNonceSize + kTagSize ||
      payload.compare(0, kMagicSize, kMagic, kMagicSize) != 0)
    {
      udp_aead_unprotected_drops_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    const auto * session_salt = reinterpret_cast<const unsigned char *>(
      payload.data() + kMagicSize);
    const auto * nonce = session_salt + kSaltSize;
    const auto * tag = reinterpret_cast<const unsigned char *>(
      payload.data() + kMagicSize + kSaltSize + kNonceSize);
    const auto * ciphertext = reinterpret_cast<const unsigned char *>(
      payload.data() + kMagicSize + kSaltSize + kNonceSize + kTagSize);
    const size_t ciphertext_size =
      payload.size() - kMagicSize - kSaltSize - kNonceSize - kTagSize;
    std::array<unsigned char, 32> session_key{};
    if (!received_udp_aead_session_key(session_salt, &session_key)) {
      udp_aead_authentication_failures_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    using CipherContext = std::unique_ptr<EVP_CIPHER_CTX, decltype(&EVP_CIPHER_CTX_free)>;
    CipherContext context(EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
    if (!context ||
      EVP_DecryptInit_ex(context.get(), EVP_aes_256_gcm(), nullptr, nullptr, nullptr) != 1 ||
      EVP_CIPHER_CTX_ctrl(
        context.get(), EVP_CTRL_GCM_SET_IVLEN, static_cast<int>(kNonceSize), nullptr) != 1 ||
      EVP_DecryptInit_ex(
        context.get(), nullptr, nullptr, session_key.data(), nonce) != 1)
    {
      udp_aead_authentication_failures_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    int output_size = 0;
    if (EVP_DecryptUpdate(
        context.get(), nullptr, &output_size,
        reinterpret_cast<const unsigned char *>(kMagic), static_cast<int>(kMagicSize)) != 1)
    {
      udp_aead_authentication_failures_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    if (EVP_DecryptUpdate(
        context.get(), nullptr, &output_size,
        session_salt, static_cast<int>(kSaltSize)) != 1)
    {
      udp_aead_authentication_failures_.fetch_add(1, std::memory_order_relaxed);
      return false;
    }
    plaintext->assign(ciphertext_size + 16, '\0');
    int plaintext_size = 0;
    if (EVP_DecryptUpdate(
        context.get(), reinterpret_cast<unsigned char *>(plaintext->data()), &output_size,
        ciphertext, static_cast<int>(ciphertext_size)) != 1)
    {
      udp_aead_authentication_failures_.fetch_add(1, std::memory_order_relaxed);
      plaintext->clear();
      return false;
    }
    plaintext_size = output_size;
    std::array<unsigned char, kTagSize> mutable_tag{};
    std::copy(tag, tag + kTagSize, mutable_tag.begin());
    if (EVP_CIPHER_CTX_ctrl(
        context.get(), EVP_CTRL_GCM_SET_TAG,
        static_cast<int>(mutable_tag.size()), mutable_tag.data()) != 1 ||
      EVP_DecryptFinal_ex(
        context.get(), reinterpret_cast<unsigned char *>(plaintext->data()) + plaintext_size,
        &output_size) != 1)
    {
      udp_aead_authentication_failures_.fetch_add(1, std::memory_order_relaxed);
      plaintext->clear();
      return false;
    }
    plaintext_size += output_size;
    plaintext->resize(static_cast<size_t>(plaintext_size));
    const std::string nonce_key(reinterpret_cast<const char *>(nonce), kNonceSize);
    {
      std::lock_guard<std::mutex> lock(udp_aead_replay_mutex_);
      if (udp_aead_seen_nonces_.find(nonce_key) != udp_aead_seen_nonces_.end()) {
        udp_aead_replay_drops_.fetch_add(1, std::memory_order_relaxed);
        plaintext->clear();
        return false;
      }
      udp_aead_seen_nonces_.insert(nonce_key);
      udp_aead_nonce_order_.push_back(nonce_key);
      while (udp_aead_nonce_order_.size() > 16384) {
        udp_aead_seen_nonces_.erase(udp_aead_nonce_order_.front());
        udp_aead_nonce_order_.pop_front();
      }
    }
    udp_aead_decrypted_frames_.fetch_add(1, std::memory_order_relaxed);
    return true;
  }

  rmw_ret_t send_payload_to_targets(
    const std::string & payload,
    const std::vector<sockaddr_in> & targets,
    const char * label)
  {
    const bool is_data_frame =
      rmw_fleetqox_cpp::decode_data_frame(payload).has_value();
    if (loss_resilient_fragment_chunk_bytes_ > 0 &&
      payload.size() > static_cast<size_t>(loss_resilient_fragment_chunk_bytes_))
    {
      return send_loss_resilient_fragmented_payload_to_targets(
        payload, targets, label, is_data_frame);
    }
    if (udp_datagram_budget_bytes_ > 0) {
      const size_t budget = static_cast<size_t>(udp_datagram_budget_bytes_);
      const size_t protection_overhead =
        udp_protection_overhead_upper_bound();
      if (protection_overhead > budget ||
        payload.size() > budget - protection_overhead)
      {
        return send_loss_resilient_fragmented_payload_to_targets(
          payload,
          targets,
          label,
          is_data_frame,
          loss_resilient_fragment_chunk_bytes_ > 0 ?
          static_cast<size_t>(loss_resilient_fragment_chunk_bytes_) :
          kMaxUdpPayloadBytes);
      }
    }
    std::string wire_payload;
    if (!protect_udp_payload(payload, &wire_payload)) {
      RMW_SET_ERROR_MSG("failed to encrypt FleetRMW UDP payload with AES-256-GCM");
      return RMW_RET_ERROR;
    }
    std::string authenticated_payload;
    if (!protect_udp_peer_authenticated_payload(
        wire_payload,
        is_data_frame,
        &authenticated_payload))
    {
      RMW_SET_ERROR_MSG("failed to sign FleetRMW UDP payload with SROS2 identity key");
      return RMW_RET_ERROR;
    }
    wire_payload = std::move(authenticated_payload);
    if (udp_datagram_budget_bytes_ > 0 &&
      wire_payload.size() > static_cast<size_t>(udp_datagram_budget_bytes_))
    {
      return send_loss_resilient_fragmented_payload_to_targets(
        payload,
        targets,
        label,
        is_data_frame,
        loss_resilient_fragment_chunk_bytes_ > 0 ?
        static_cast<size_t>(loss_resilient_fragment_chunk_bytes_) :
        kMaxUdpPayloadBytes);
    }
    if (wire_payload.size() > kMaxUdpPayloadBytes) {
      if (udp_aead_enabled_ || udp_peer_auth_enabled_) {
        return send_loss_resilient_fragmented_payload_to_targets(
          payload, targets, label, is_data_frame, 60000);
      }
      return send_fragmented_payload_to_targets(wire_payload, targets, label);
    }
    return send_datagram_to_targets(wire_payload, targets, label);
  }

  static std::string stable_fragment_id(const std::string & payload)
  {
    std::uint64_t hash = 1469598103934665603ull;
    for (const unsigned char byte : payload) {
      hash ^= static_cast<std::uint64_t>(byte);
      hash *= 1099511628211ull;
    }
    std::ostringstream output;
    output << std::hex << std::setw(16) << std::setfill('0') << hash <<
      "-" << std::dec << payload.size();
    return output.str();
  }

  size_t udp_protection_overhead_upper_bound() const
  {
    size_t overhead = 0;
    if (udp_aead_enabled_) {
      overhead += 8 + 16 + 12 + 16;
    }
    if (udp_peer_auth_enabled_) {
      const int signature_size = udp_peer_auth_local_private_key_ == nullptr ?
        0 : EVP_PKEY_get_size(udp_peer_auth_local_private_key_);
      if (signature_size <= 0) {
        return std::numeric_limits<size_t>::max();
      }
      overhead += 9 + 2 * sizeof(std::uint32_t) +
        udp_peer_auth_local_certificate_der_.size() +
        static_cast<size_t>(signature_size);
    }
    return overhead;
  }

  size_t effective_loss_resilient_fragment_chunk_bytes(
    const std::string & payload,
    const std::string & fragment_id,
    size_t requested_chunk_bytes)
  {
    if (payload.empty() || fragment_id.empty() || requested_chunk_bytes == 0) {
      return 0;
    }
    if (udp_datagram_budget_bytes_ <= 0) {
      return requested_chunk_bytes;
    }
    const size_t protection_overhead = udp_protection_overhead_upper_bound();
    if (protection_overhead == std::numeric_limits<size_t>::max()) {
      return 0;
    }
    const size_t budget = static_cast<size_t>(udp_datagram_budget_bytes_);
    size_t effective_chunk_bytes = std::min(requested_chunk_bytes, budget);
    for (size_t iteration = 0; iteration < 8; ++iteration) {
      if (effective_chunk_bytes == 0) {
        return 0;
      }
      const size_t fragment_count =
        (payload.size() + effective_chunk_bytes - 1) /
        effective_chunk_bytes;
      const size_t largest_index = fragment_count == 0 ? 0 : fragment_count - 1;
      const size_t wrapper_bytes =
        std::char_traits<char>::length(kRepairFragmentPrefix) +
        fragment_id.size() + 4 +
        std::to_string(largest_index).size() +
        std::to_string(fragment_count).size() +
        std::to_string(payload.size()).size();
      if (wrapper_bytes > budget ||
        protection_overhead > budget - wrapper_bytes)
      {
        return 0;
      }
      const size_t allowed_chunk_bytes =
        budget - wrapper_bytes - protection_overhead;
      const size_t next_chunk_bytes =
        std::min(requested_chunk_bytes, allowed_chunk_bytes);
      if (next_chunk_bytes == effective_chunk_bytes) {
        return effective_chunk_bytes;
      }
      effective_chunk_bytes = next_chunk_bytes;
    }
    return effective_chunk_bytes;
  }

  void record_effective_fragment_chunk_bytes(
    size_t requested_chunk_bytes,
    size_t effective_chunk_bytes)
  {
    if (effective_chunk_bytes == 0) {
      return;
    }
    if (effective_chunk_bytes < requested_chunk_bytes) {
      fragment_chunk_budget_reductions_.fetch_add(
        1, std::memory_order_relaxed);
    }
    size_t previous_min = fragment_effective_chunk_bytes_min_.load(
      std::memory_order_relaxed);
    while ((previous_min == 0 || effective_chunk_bytes < previous_min) &&
      !fragment_effective_chunk_bytes_min_.compare_exchange_weak(
        previous_min, effective_chunk_bytes, std::memory_order_relaxed))
    {
    }
    size_t previous_max = fragment_effective_chunk_bytes_max_.load(
      std::memory_order_relaxed);
    while (effective_chunk_bytes > previous_max &&
      !fragment_effective_chunk_bytes_max_.compare_exchange_weak(
        previous_max, effective_chunk_bytes, std::memory_order_relaxed))
    {
    }
  }

  rmw_ret_t send_loss_resilient_fragmented_payload_to_targets(
    const std::string & payload,
    const std::vector<sockaddr_in> & targets,
    const char * label,
    bool is_data_frame,
    size_t protected_chunk_bytes = 0)
  {
    const size_t requested_chunk_bytes =
      protected_chunk_bytes > 0 ?
      protected_chunk_bytes :
      static_cast<size_t>(loss_resilient_fragment_chunk_bytes_);
    if (payload.empty() || requested_chunk_bytes == 0) {
      return RMW_RET_INVALID_ARGUMENT;
    }
    const std::string fragment_id = stable_fragment_id(payload);
    const size_t chunk_bytes = effective_loss_resilient_fragment_chunk_bytes(
      payload, fragment_id, requested_chunk_bytes);
    if (chunk_bytes == 0) {
      udp_datagram_budget_failures_.fetch_add(1, std::memory_order_relaxed);
      RMW_SET_ERROR_MSG(
        "FleetRMW UDP datagram budget cannot fit a protected fragment");
      return RMW_RET_ERROR;
    }
    record_effective_fragment_chunk_bytes(requested_chunk_bytes, chunk_bytes);
    const size_t fragment_count =
      (payload.size() + chunk_bytes - 1) / chunk_bytes;
    if (fragment_count == 0 || fragment_count > 4096) {
      RMW_SET_ERROR_MSG("loss-resilient FleetRMW fragment count is out of range");
      return RMW_RET_ERROR;
    }
    const auto shared_payload = std::make_shared<const std::string>(payload);
    remember_fragment_history(
      fragment_id,
      shared_payload,
      targets,
      chunk_bytes,
      fragment_count,
      is_data_frame);
    std::vector<size_t> indexes(fragment_count);
    for (size_t index = 0; index < fragment_count; ++index) {
      indexes[index] = index;
    }
    if (fragment_async_send_enabled_) {
      record_fragment_async_send_started(payload);
      const rmw_ret_t enqueue_ret = enqueue_loss_resilient_fragment_indexes(
        shared_payload,
        targets,
        is_data_frame,
        fragment_id,
        chunk_bytes,
        fragment_count,
        indexes,
        false);
      if (enqueue_ret != RMW_RET_OK) {
        record_fragment_async_send_failed(payload);
      }
      return enqueue_ret;
    }
    const rmw_ret_t send_ret = send_loss_resilient_fragment_indexes(
      *shared_payload,
      targets,
      label,
      is_data_frame,
      fragment_id,
      chunk_bytes,
      fragment_count,
      indexes,
      false);
    if (send_ret != RMW_RET_OK) {
      return send_ret;
    }
    return send_fragment_completion_marker(
      targets,
      fragment_id,
      fragment_count,
      payload.size(),
      is_data_frame);
  }

  void cleanup_fragment_history_locked(std::int64_t now_ns)
  {
    for (auto it = fragment_history_.begin(); it != fragment_history_.end();) {
      if (it->second.last_update_ns > 0 &&
        now_ns - it->second.last_update_ns > kFragmentHistoryTtlNs)
      {
        it = fragment_history_.erase(it);
      } else {
        ++it;
      }
    }
  }

  void remember_fragment_history(
    const std::string & fragment_id,
    const std::shared_ptr<const std::string> & payload,
    const std::vector<sockaddr_in> & targets,
    size_t chunk_bytes,
    size_t fragment_count,
    bool is_data_frame)
  {
    if (fragment_history_limit_ <= 0 || fragment_nack_max_requests_ <= 0) {
      return;
    }
    const std::int64_t now_ns = monotonic_timestamp_ns();
    std::lock_guard<std::mutex> lock(fragment_history_mutex_);
    cleanup_fragment_history_locked(now_ns);
    FragmentRepairHistory & history = fragment_history_[fragment_id];
    auto prior_request_counts = std::move(history.request_count_by_target);
    history.payload = payload;
    history.targets = targets;
    history.chunk_bytes = chunk_bytes;
    history.fragment_count = fragment_count;
    history.is_data_frame = is_data_frame;
    history.last_update_ns = now_ns;
    history.request_count_by_target = std::move(prior_request_counts);
    while (fragment_history_.size() > static_cast<size_t>(fragment_history_limit_)) {
      auto oldest = fragment_history_.end();
      for (auto it = fragment_history_.begin(); it != fragment_history_.end(); ++it) {
        if (oldest == fragment_history_.end() ||
          it->second.last_update_ns < oldest->second.last_update_ns)
        {
          oldest = it;
        }
      }
      if (oldest == fragment_history_.end()) {
        break;
      }
      fragment_history_.erase(oldest);
    }
  }

  bool should_drop_fragment_for_test(
    const std::string & fragment_id,
    size_t fragment_index)
  {
    if (std::find(
        drop_fragment_indexes_.begin(),
        drop_fragment_indexes_.end(),
        static_cast<std::uint64_t>(fragment_index)) == drop_fragment_indexes_.end())
    {
      return false;
    }
    const std::string key = fragment_id + "|" + std::to_string(fragment_index);
    std::lock_guard<std::mutex> lock(test_drop_mutex_);
    if (!dropped_fragment_keys_.insert(key).second) {
      return false;
    }
    test_dropped_fragments_.fetch_add(1, std::memory_order_relaxed);
    return true;
  }

  rmw_ret_t enqueue_loss_resilient_fragment_indexes(
    const std::shared_ptr<const std::string> & payload,
    const std::vector<sockaddr_in> & targets,
    bool is_data_frame,
    const std::string & fragment_id,
    size_t chunk_bytes,
    size_t fragment_count,
    const std::vector<size_t> & indexes,
    bool selective_retransmission)
  {
    if (!payload || targets.empty() || indexes.empty()) {
      return RMW_RET_INVALID_ARGUMENT;
    }
    if (std::any_of(indexes.begin(), indexes.end(), [fragment_count](size_t index) {
        return index >= fragment_count;
      }))
    {
      return RMW_RET_INVALID_ARGUMENT;
    }
    {
      std::unique_lock<std::mutex> lock(fragment_send_queue_mutex_);
      const std::int64_t enqueue_now_ns = monotonic_timestamp_ns();
      if (selective_retransmission) {
        cleanup_recent_fragment_repairs_locked(enqueue_now_ns);
      }
      std::vector<size_t> accepted_indexes;
      accepted_indexes.reserve(indexes.size());
      const std::string repair_target_scope =
        selective_retransmission ?
        fragment_repair_target_scope_key(targets) : std::string{};
      for (const size_t index : indexes) {
        if (!selective_retransmission) {
          accepted_indexes.push_back(index);
          continue;
        }
        const std::string key = fragment_repair_queue_key(
          fragment_id, index, repair_target_scope);
        const auto recent = fragment_repair_recent_send_ns_.find(key);
        const bool cooldown_active =
          fragment_repair_cooldown_ms_ > 0 &&
          recent != fragment_repair_recent_send_ns_.end() &&
          enqueue_now_ns - recent->second <
          static_cast<std::int64_t>(fragment_repair_cooldown_ms_) * 1000000ll;
        if (fragment_repair_pending_keys_.find(key) !=
          fragment_repair_pending_keys_.end() || cooldown_active)
        {
          fragment_repair_requests_coalesced_.fetch_add(1, std::memory_order_relaxed);
          if (cooldown_active) {
            fragment_repair_cooldown_coalesced_.fetch_add(
              1, std::memory_order_relaxed);
          }
          continue;
        }
        accepted_indexes.push_back(index);
      }
      if (accepted_indexes.empty()) {
        return RMW_RET_OK;
      }
      if (selective_retransmission && fragment_repair_queue_limit_ > 0) {
        const size_t repair_capacity =
          static_cast<size_t>(fragment_repair_queue_limit_);
        const size_t available =
          repair_capacity - std::min(
          fragment_repair_send_queue_size_, repair_capacity);
        if (accepted_indexes.size() > available) {
          fragment_repair_queue_deferrals_.fetch_add(
            static_cast<std::uint64_t>(accepted_indexes.size() - available),
            std::memory_order_relaxed);
          accepted_indexes.resize(available);
        }
        if (accepted_indexes.empty()) {
          return RMW_RET_TIMEOUT;
        }
      }
      if (!selective_retransmission &&
        fragment_queue_admission_threshold_ > 0)
      {
        const size_t admission_capacity = std::max(
          accepted_indexes.size(),
          static_cast<size_t>(fragment_queue_admission_threshold_));
        const auto admission_ready = [this, admission_capacity, &accepted_indexes]() {
            const size_t pending =
              fragment_initial_send_queue_size_ +
              fragment_repair_send_queue_size_;
            return !fragment_sender_running_.load(std::memory_order_acquire) ||
                   accepted_indexes.size() <=
                   admission_capacity - std::min(pending, admission_capacity);
          };
        if (!admission_ready()) {
          const auto wait_started = std::chrono::steady_clock::now();
          fragment_queue_admission_waits_.fetch_add(1, std::memory_order_relaxed);
          bool admitted = false;
          if (fragment_queue_admission_timeout_ms_ > 0) {
            admitted = fragment_send_queue_cv_.wait_for(
              lock,
              std::chrono::milliseconds(fragment_queue_admission_timeout_ms_),
              admission_ready);
          } else {
            fragment_send_queue_cv_.wait(lock, admission_ready);
            admitted = true;
          }
          const auto wait_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - wait_started).count();
          fragment_queue_admission_wait_ns_.fetch_add(
            static_cast<std::uint64_t>(std::max<std::int64_t>(wait_ns, 0)),
            std::memory_order_relaxed);
          if (!admitted ||
            !fragment_sender_running_.load(std::memory_order_acquire))
          {
            RMW_SET_ERROR_MSG(
              "FleetRMW fragment queue admission timed out or stopped");
            fragment_queue_admission_timeouts_.fetch_add(
              1, std::memory_order_relaxed);
            return RMW_RET_TIMEOUT;
          }
        }
      }
      const size_t pending_count =
        fragment_initial_send_queue_size_ + fragment_repair_send_queue_size_;
      if (fragment_send_queue_limit_ <= 0 ||
        accepted_indexes.size() >
        static_cast<size_t>(fragment_send_queue_limit_) - std::min(
          pending_count,
          static_cast<size_t>(fragment_send_queue_limit_)))
      {
        RMW_SET_ERROR_MSG("FleetRMW async fragment send queue is full");
        fragment_send_queue_rejections_.fetch_add(1, std::memory_order_relaxed);
        return RMW_RET_ERROR;
      }
      if (selective_retransmission) {
        const std::string repair_frame_scope =
          fragment_repair_frame_queue_key(fragment_id, repair_target_scope);
        auto found = fragment_repair_send_queues_.find(repair_frame_scope);
        if (found == fragment_repair_send_queues_.end()) {
          found = fragment_repair_send_queues_.emplace(
            repair_frame_scope, std::deque<PendingFragmentSend>{}).first;
          fragment_repair_send_order_.push_back(repair_frame_scope);
          const size_t active_frames = fragment_repair_send_queues_.size();
          size_t previous_active_max =
            fragment_repair_max_active_frames_.load(
            std::memory_order_relaxed);
          while (previous_active_max < active_frames &&
            !fragment_repair_max_active_frames_.compare_exchange_weak(
              previous_active_max,
              active_frames,
              std::memory_order_relaxed))
          {
          }
        }
        for (const size_t index : accepted_indexes) {
          fragment_repair_pending_keys_.insert(
            fragment_repair_queue_key(
              fragment_id, index, repair_target_scope));
          found->second.push_back(PendingFragmentSend{
            payload,
            targets,
            fragment_id,
            chunk_bytes,
            fragment_count,
            index,
            is_data_frame,
            true,
            fragment_repair_queue_key(
              fragment_id, index, repair_target_scope)});
          ++fragment_repair_send_queue_size_;
        }
        const size_t repair_queue_size = fragment_repair_send_queue_size_;
        size_t observed_repair_high_water =
          fragment_repair_queue_high_water_.load(std::memory_order_relaxed);
        while (repair_queue_size > observed_repair_high_water &&
          !fragment_repair_queue_high_water_.compare_exchange_weak(
            observed_repair_high_water,
            repair_queue_size,
            std::memory_order_relaxed))
        {
        }
      } else {
        auto found = fragment_initial_send_queues_.find(fragment_id);
        if (found == fragment_initial_send_queues_.end()) {
          found = fragment_initial_send_queues_.emplace(
            fragment_id, std::deque<PendingFragmentSend>{}).first;
          fragment_initial_send_order_.push_back(fragment_id);
          const size_t active_frames = fragment_initial_send_queues_.size();
          size_t previous_active_max =
            fragment_initial_max_active_frames_.load(
            std::memory_order_relaxed);
          while (previous_active_max < active_frames &&
            !fragment_initial_max_active_frames_.compare_exchange_weak(
              previous_active_max,
              active_frames,
              std::memory_order_relaxed))
          {
          }
        }
        for (const size_t index : accepted_indexes) {
          found->second.push_back(PendingFragmentSend{
            payload,
            targets,
            fragment_id,
            chunk_bytes,
            fragment_count,
            index,
            is_data_frame,
            false,
            std::string{}});
          ++fragment_initial_send_queue_size_;
        }
        found->second.back().initial_send_completion = true;
      }
      const size_t updated_count =
        fragment_initial_send_queue_size_ + fragment_repair_send_queue_size_;
      size_t observed_high_water =
        fragment_send_queue_high_water_.load(std::memory_order_relaxed);
      while (updated_count > observed_high_water &&
        !fragment_send_queue_high_water_.compare_exchange_weak(
          observed_high_water,
          updated_count,
          std::memory_order_relaxed))
      {
      }
    }
    fragment_send_queue_cv_.notify_one();
    return RMW_RET_OK;
  }

  void fragment_sender_loop()
  {
    size_t consecutive_initial_fragments = 0;
    size_t consecutive_same_initial_frame = 0;
    size_t consecutive_same_contended_initial_frame = 0;
    size_t consecutive_same_contended_repair_frame = 0;
    std::string last_initial_fragment_id;
    std::string last_contended_initial_fragment_id;
    std::string last_repair_frame_scope;
    std::string last_contended_repair_frame_scope;
    while (true) {
      PendingFragmentSend task;
      {
        std::unique_lock<std::mutex> lock(fragment_send_queue_mutex_);
        fragment_send_queue_cv_.wait(lock, [this]() {
          return !fragment_sender_running_.load(std::memory_order_acquire) ||
                 fragment_initial_send_queue_size_ > 0 ||
                 fragment_repair_send_queue_size_ > 0;
        });
        if (!fragment_sender_running_.load(std::memory_order_acquire)) {
          break;
        }
        size_t effective_initial_burst = 8;
        if (fragment_repair_send_queue_size_ > 0 &&
          fragment_repair_queue_limit_ > 0)
        {
          const size_t repair_queue_size = fragment_repair_send_queue_size_;
          const size_t repair_capacity =
            static_cast<size_t>(fragment_repair_queue_limit_);
          if (repair_queue_size * 4 >= repair_capacity * 3) {
            effective_initial_burst = 1;
          } else if (repair_queue_size * 2 >= repair_capacity) {
            effective_initial_burst = 2;
          } else if (repair_queue_size * 4 >= repair_capacity) {
            effective_initial_burst = 4;
          }
        }
        const bool choose_repair =
          fragment_repair_send_queue_size_ > 0 &&
          (fragment_initial_send_queue_size_ == 0 ||
          consecutive_initial_fragments >= effective_initial_burst);
        if (choose_repair) {
          if (fragment_initial_send_queue_size_ > 0 &&
            effective_initial_burst < 8)
          {
            fragment_repair_pressure_priority_promotions_.fetch_add(
              1, std::memory_order_relaxed);
          }
          const bool repair_frame_contention =
            fragment_repair_send_order_.size() > 1;
          const std::string repair_frame_scope =
            std::move(fragment_repair_send_order_.front());
          fragment_repair_send_order_.pop_front();
          auto found = fragment_repair_send_queues_.find(repair_frame_scope);
          if (found == fragment_repair_send_queues_.end() ||
            found->second.empty())
          {
            continue;
          }
          task = std::move(found->second.front());
          found->second.pop_front();
          --fragment_repair_send_queue_size_;
          if (found->second.empty()) {
            fragment_repair_send_queues_.erase(found);
          } else {
            fragment_repair_send_order_.push_back(repair_frame_scope);
            fragment_repair_round_robin_rotations_.fetch_add(
              1, std::memory_order_relaxed);
          }
          if (last_repair_frame_scope != repair_frame_scope) {
            if (!last_repair_frame_scope.empty()) {
              fragment_repair_frame_switches_.fetch_add(
                1, std::memory_order_relaxed);
            }
            last_repair_frame_scope = repair_frame_scope;
          }
          if (repair_frame_contention) {
            if (last_contended_repair_frame_scope == repair_frame_scope) {
              ++consecutive_same_contended_repair_frame;
            } else {
              last_contended_repair_frame_scope = repair_frame_scope;
              consecutive_same_contended_repair_frame = 1;
            }
            size_t previous_contended_max =
              fragment_repair_max_consecutive_same_frame_while_contended_.load(
              std::memory_order_relaxed);
            while (previous_contended_max <
              consecutive_same_contended_repair_frame &&
              !fragment_repair_max_consecutive_same_frame_while_contended_
              .compare_exchange_weak(
                previous_contended_max,
                consecutive_same_contended_repair_frame,
                std::memory_order_relaxed))
            {
            }
          } else {
            last_contended_repair_frame_scope.clear();
            consecutive_same_contended_repair_frame = 0;
          }
        } else {
          const bool initial_frame_contention =
            fragment_initial_send_order_.size() > 1;
          const std::string fragment_id =
            std::move(fragment_initial_send_order_.front());
          fragment_initial_send_order_.pop_front();
          auto found = fragment_initial_send_queues_.find(fragment_id);
          if (found == fragment_initial_send_queues_.end() ||
            found->second.empty())
          {
            continue;
          }
          task = std::move(found->second.front());
          found->second.pop_front();
          --fragment_initial_send_queue_size_;
          if (found->second.empty()) {
            fragment_initial_send_queues_.erase(found);
          } else {
            fragment_initial_send_order_.push_back(fragment_id);
            fragment_initial_round_robin_rotations_.fetch_add(
              1, std::memory_order_relaxed);
          }
          if (last_initial_fragment_id == task.fragment_id) {
            ++consecutive_same_initial_frame;
          } else {
            if (!last_initial_fragment_id.empty()) {
              fragment_initial_frame_switches_.fetch_add(
                1, std::memory_order_relaxed);
            }
            last_initial_fragment_id = task.fragment_id;
            consecutive_same_initial_frame = 1;
          }
          size_t previous_max =
            fragment_initial_max_consecutive_same_frame_.load(
            std::memory_order_relaxed);
          while (previous_max < consecutive_same_initial_frame &&
            !fragment_initial_max_consecutive_same_frame_.compare_exchange_weak(
              previous_max,
              consecutive_same_initial_frame,
              std::memory_order_relaxed))
          {
          }
          if (initial_frame_contention) {
            if (last_contended_initial_fragment_id == task.fragment_id) {
              ++consecutive_same_contended_initial_frame;
            } else {
              last_contended_initial_fragment_id = task.fragment_id;
              consecutive_same_contended_initial_frame = 1;
            }
            size_t previous_contended_max =
              fragment_initial_max_consecutive_same_frame_while_contended_.load(
              std::memory_order_relaxed);
            while (previous_contended_max <
              consecutive_same_contended_initial_frame &&
              !fragment_initial_max_consecutive_same_frame_while_contended_
              .compare_exchange_weak(
                previous_contended_max,
                consecutive_same_contended_initial_frame,
                std::memory_order_relaxed))
            {
            }
          } else {
            last_contended_initial_fragment_id.clear();
            consecutive_same_contended_initial_frame = 0;
          }
        }
        consecutive_initial_fragments =
          choose_repair ? 0 : consecutive_initial_fragments + 1;
        fragment_send_queue_cv_.notify_all();
      }
      const rmw_ret_t ret = send_loss_resilient_fragment_indexes(
        *task.payload,
        task.targets,
        task.selective_retransmission ?
        "FleetRMW queued selective fragment repair" :
        "FleetRMW queued initial fragment",
        task.is_data_frame,
        task.fragment_id,
        task.chunk_bytes,
        task.fragment_count,
        std::vector<size_t>{task.fragment_index},
        task.selective_retransmission);
      if (ret != RMW_RET_OK) {
        fragment_send_failures_.fetch_add(1, std::memory_order_relaxed);
      }
      if (!task.selective_retransmission && task.initial_send_completion) {
        const rmw_ret_t completion_ret =
          ret == RMW_RET_OK ?
          send_fragment_completion_marker(
            task.targets,
            task.fragment_id,
            task.fragment_count,
            task.payload->size(),
            task.is_data_frame) :
          ret;
        if (completion_ret == RMW_RET_OK) {
          record_fragment_async_send_complete(*task.payload);
        } else {
          if (ret == RMW_RET_OK) {
            fragment_send_failures_.fetch_add(1, std::memory_order_relaxed);
          }
          record_fragment_async_send_failed(*task.payload);
        }
      }
      if (task.selective_retransmission) {
        std::lock_guard<std::mutex> lock(fragment_send_queue_mutex_);
        if (ret == RMW_RET_OK) {
          fragment_repair_recent_send_ns_[task.repair_key] =
            monotonic_timestamp_ns();
        }
        fragment_repair_pending_keys_.erase(task.repair_key);
      }
    }
  }

  static std::string fragment_repair_target_scope_key(
    const std::vector<sockaddr_in> & targets)
  {
    std::vector<std::string> endpoints;
    endpoints.reserve(targets.size());
    for (const sockaddr_in & target : targets) {
      endpoints.push_back(endpoint_to_string(target));
    }
    std::sort(endpoints.begin(), endpoints.end());
    endpoints.erase(
      std::unique(endpoints.begin(), endpoints.end()), endpoints.end());
    std::ostringstream output;
    for (const std::string & endpoint : endpoints) {
      if (output.tellp() > 0) {
        output << ",";
      }
      output << endpoint;
    }
    return output.str();
  }

  static std::string fragment_repair_queue_key(
    const std::string & fragment_id,
    size_t fragment_index,
    const std::string & target_scope)
  {
    return fragment_id + "|" + std::to_string(fragment_index) + "|" +
           target_scope;
  }

  static std::string fragment_repair_frame_queue_key(
    const std::string & fragment_id,
    const std::string & target_scope)
  {
    return fragment_id + "|" + target_scope;
  }

  void cleanup_recent_fragment_repairs_locked(std::int64_t now_ns)
  {
    for (auto it = fragment_repair_recent_send_ns_.begin();
      it != fragment_repair_recent_send_ns_.end();)
    {
      if (now_ns - it->second > kFragmentHistoryTtlNs) {
        it = fragment_repair_recent_send_ns_.erase(it);
      } else {
        ++it;
      }
    }
    const size_t limit = std::max<size_t>(
      64,
      static_cast<size_t>(std::max(fragment_history_limit_, 1)) *
      kMaxFragmentRepairIndexesPerRequest);
    while (fragment_repair_recent_send_ns_.size() > limit) {
      auto oldest = fragment_repair_recent_send_ns_.end();
      for (auto it = fragment_repair_recent_send_ns_.begin();
        it != fragment_repair_recent_send_ns_.end(); ++it)
      {
        if (oldest == fragment_repair_recent_send_ns_.end() ||
          it->second < oldest->second)
        {
          oldest = it;
        }
      }
      if (oldest == fragment_repair_recent_send_ns_.end()) {
        break;
      }
      fragment_repair_recent_send_ns_.erase(oldest);
    }
  }

  rmw_ret_t send_fragment_completion_marker(
    const std::vector<sockaddr_in> & targets,
    const std::string & fragment_id,
    size_t fragment_count,
    size_t total_size,
    bool is_data_frame)
  {
    if (targets.empty() || fragment_id.empty() ||
      fragment_count == 0 || total_size == 0)
    {
      fragment_completion_marker_failures_.fetch_add(
        1, std::memory_order_relaxed);
      return RMW_RET_INVALID_ARGUMENT;
    }
    std::string marker(kRepairFragmentCompletionPrefix);
    marker.append(fragment_id);
    marker.push_back('|');
    marker.append(std::to_string(fragment_count));
    marker.push_back('|');
    marker.append(std::to_string(total_size));
    std::string protected_marker;
    if (!protect_udp_payload(marker, &protected_marker)) {
      fragment_completion_marker_failures_.fetch_add(
        1, std::memory_order_relaxed);
      return RMW_RET_ERROR;
    }
    std::string authenticated_marker;
    if (!protect_udp_peer_authenticated_payload(
        protected_marker, is_data_frame, &authenticated_marker))
    {
      fragment_completion_marker_failures_.fetch_add(
        1, std::memory_order_relaxed);
      return RMW_RET_ERROR;
    }
    const rmw_ret_t ret = send_datagram_to_targets(
      authenticated_marker,
      targets,
      "FleetRMW fragment completion marker");
    if (ret == RMW_RET_OK) {
      fragment_completion_markers_sent_.fetch_add(
        1, std::memory_order_relaxed);
    } else {
      fragment_completion_marker_failures_.fetch_add(
        1, std::memory_order_relaxed);
    }
    return ret;
  }

  rmw_ret_t send_loss_resilient_fragment_indexes(
    const std::string & payload,
    const std::vector<sockaddr_in> & targets,
    const char * label,
    bool is_data_frame,
    const std::string & fragment_id,
    size_t chunk_bytes,
    size_t fragment_count,
    const std::vector<size_t> & indexes,
    bool selective_retransmission)
  {
    for (const size_t index : indexes) {
      if (index >= fragment_count) {
        return RMW_RET_INVALID_ARGUMENT;
      }
      const size_t offset = index * chunk_bytes;
      const size_t chunk_size = std::min(chunk_bytes, payload.size() - offset);
      if (should_drop_fragment_for_test(fragment_id, index)) {
        continue;
      }
      std::string fragment;
      fragment.reserve(chunk_size + 128);
      fragment.append(kRepairFragmentPrefix);
      fragment.append(fragment_id);
      fragment.push_back('|');
      fragment.append(std::to_string(index));
      fragment.push_back('|');
      fragment.append(std::to_string(fragment_count));
      fragment.push_back('|');
      fragment.append(std::to_string(payload.size()));
      fragment.push_back('|');
      fragment.append(payload.data() + offset, chunk_size);

      std::string protected_fragment;
      if (!protect_udp_payload(fragment, &protected_fragment)) {
        RMW_SET_ERROR_MSG(
          "failed to encrypt loss-resilient FleetRMW UDP fragment");
        return RMW_RET_ERROR;
      }
      std::string authenticated_fragment;
      if (!protect_udp_peer_authenticated_payload(
          protected_fragment, is_data_frame, &authenticated_fragment))
      {
        RMW_SET_ERROR_MSG(
          "failed to sign loss-resilient FleetRMW UDP fragment");
        return RMW_RET_ERROR;
      }
      if (authenticated_fragment.size() > kMaxUdpPayloadBytes) {
        RMW_SET_ERROR_MSG(
          "protected loss-resilient FleetRMW fragment exceeds UDP payload limit");
        return RMW_RET_ERROR;
      }
      const rmw_ret_t ret =
        send_datagram_to_targets(authenticated_fragment, targets, label);
      if (ret != RMW_RET_OK) {
        return ret;
      }
      if (selective_retransmission) {
        fragments_selectively_retransmitted_.fetch_add(1, std::memory_order_relaxed);
      }
    }
    return RMW_RET_OK;
  }

  rmw_ret_t send_datagram_to_targets(
    const std::string & payload,
    const std::vector<sockaddr_in> & targets,
    const char * label)
  {
    const size_t payload_size = payload.size();
    size_t previous_high_water = udp_datagram_size_high_water_.load(
      std::memory_order_relaxed);
    while (payload_size > previous_high_water &&
      !udp_datagram_size_high_water_.compare_exchange_weak(
        previous_high_water, payload_size, std::memory_order_relaxed))
    {
    }
    if (udp_datagram_budget_bytes_ > 0 &&
      payload_size > static_cast<size_t>(udp_datagram_budget_bytes_))
    {
      udp_datagram_budget_failures_.fetch_add(1, std::memory_order_relaxed);
      RMW_SET_ERROR_MSG("FleetRMW UDP payload exceeds configured datagram budget");
      return RMW_RET_ERROR;
    }
    std::lock_guard<std::mutex> lock(udp_send_mutex_);
    for (const sockaddr_in & target : targets) {
      pace_udp_send_locked();
      const auto sent = ::sendto(
        fd_,
        payload.data(),
        payload.size(),
        0,
        reinterpret_cast<const sockaddr *>(&target),
        sizeof(target));
      if (sent < 0 || static_cast<size_t>(sent) != payload.size()) {
        RMW_SET_ERROR_MSG(label == nullptr ?
          "failed to send FleetRMW payload through UDP transport" :
          "failed to send FleetRMW payload through UDP transport");
        return RMW_RET_ERROR;
      }
    }
    return RMW_RET_OK;
  }

  void pace_udp_send_locked()
  {
    if (udp_send_pacing_us_ <= 0) {
      return;
    }
    const auto interval = std::chrono::microseconds(udp_send_pacing_us_);
    const auto now = std::chrono::steady_clock::now();
    if (next_udp_send_time_.time_since_epoch().count() > 0 &&
      next_udp_send_time_ > now)
    {
      std::this_thread::sleep_until(next_udp_send_time_);
      next_udp_send_time_ += interval;
      return;
    }
    next_udp_send_time_ = now + interval;
  }

  rmw_ret_t send_fragmented_payload_to_targets(
    const std::string & payload,
    const std::vector<sockaddr_in> & targets,
    const char * label)
  {
    if (payload.empty()) {
      return send_datagram_to_targets(payload, targets, label);
    }
    const size_t fragment_count =
      (payload.size() + kUdpFragmentChunkBytes - 1) / kUdpFragmentChunkBytes;
    const std::uint64_t fragment_sequence =
      fragment_sequence_.fetch_add(1, std::memory_order_relaxed) + 1;
    const std::string fragment_id =
      bound_endpoint_ + ":" + std::to_string(fragment_sequence);
    for (size_t index = 0; index < fragment_count; ++index) {
      const size_t offset = index * kUdpFragmentChunkBytes;
      const size_t chunk_size =
        std::min(kUdpFragmentChunkBytes, payload.size() - offset);
      std::string datagram;
      datagram.reserve(chunk_size + 128);
      datagram.append(kFragmentPrefix);
      datagram.append(fragment_id);
      datagram.push_back('|');
      datagram.append(std::to_string(index));
      datagram.push_back('|');
      datagram.append(std::to_string(fragment_count));
      datagram.push_back('|');
      datagram.append(std::to_string(payload.size()));
      datagram.push_back('|');
      datagram.append(payload.data() + offset, chunk_size);
      if (datagram.size() > kMaxUdpPayloadBytes) {
        RMW_SET_ERROR_MSG("fragmented FleetRMW datagram exceeds UDP payload limit");
        return RMW_RET_ERROR;
      }
      const rmw_ret_t ret = send_datagram_to_targets(datagram, targets, label);
      if (ret != RMW_RET_OK) {
        return ret;
      }
    }
    return RMW_RET_OK;
  }

  bool should_drop_outbound_data_frame_for_test(const std::string & encoded_frame)
  {
    if (drop_source_sequences_.empty() || drop_source_sequence_send_count_ == 0) {
      return false;
    }
    const auto frame = rmw_fleetqox_cpp::decode_data_frame(encoded_frame);
    if (!frame) {
      return false;
    }
    const auto should_drop_sequence = std::find(
      drop_source_sequences_.begin(),
      drop_source_sequences_.end(),
      frame->source_sequence_number) != drop_source_sequences_.end();
    if (!should_drop_sequence) {
      return false;
    }
    const std::string key =
      frame->publisher_id + "|" + std::to_string(frame->source_sequence_number);
    std::lock_guard<std::mutex> lock(test_drop_mutex_);
    std::uint64_t & dropped_count = dropped_source_sequence_counts_[key];
    if (dropped_count >= static_cast<std::uint64_t>(drop_source_sequence_send_count_)) {
      return false;
    }
    ++dropped_count;
    test_dropped_frames_.fetch_add(1, std::memory_order_relaxed);
    return true;
  }

  rmw_ret_t send_to_peers(const std::string & payload)
  {
    return send_control_payload(payload, false);
  }

  rmw_ret_t send_control_payload(const std::string & payload, bool include_local)
  {
    if (!ready_) {
      RMW_SET_ERROR_MSG(init_error_.empty() ? "socket transport is not ready" : init_error_.c_str());
      return RMW_RET_ERROR;
    }
    if (payload.empty()) {
      RMW_SET_ERROR_MSG("FleetRMW control payload is empty");
      return RMW_RET_INVALID_ARGUMENT;
    }
    if (shared_memory_active()) {
      const rmw_ret_t shm_ret = send_shared_memory_payload(payload);
      if (shm_ret != RMW_RET_OK || shared_memory_only()) {
        return shm_ret;
      }
    }
    return send_payload_to_targets(
      payload,
      frame_targets(hybrid_transport() ? false : include_local),
      "FleetRMW control payload");
  }

  void start()
  {
    init_error_.clear();
    peer_addresses_.clear();
    peer_path_ids_.clear();
    transport_mode_ = "udp";
    fragment_async_send_enabled_ = false;
    udp_aead_enabled_ = false;
    udp_aead_required_ = false;
    udp_aead_tamper_outbound_once_ = false;
    udp_aead_session_frames_ = 0;
    udp_aead_nonce_sequence_.store(0, std::memory_order_relaxed);
    udp_aead_tamper_done_.store(false, std::memory_order_relaxed);
    {
      std::lock_guard<std::mutex> lock(udp_aead_received_session_mutex_);
      udp_aead_received_session_keys_.clear();
      udp_aead_received_session_order_.clear();
    }
    {
      std::lock_guard<std::mutex> lock(udp_aead_replay_mutex_);
      udp_aead_seen_nonces_.clear();
      udp_aead_nonce_order_.clear();
    }
    udp_peer_auth_enabled_ = false;
    udp_peer_auth_required_ = false;
    udp_peer_auth_tamper_outbound_once_ = false;
    udp_peer_auth_crl_enabled_ = false;
    udp_peer_auth_local_certificate_der_.clear();
    udp_peer_auth_allowed_identities_.clear();
    udp_peer_auth_tamper_done_.store(false, std::memory_order_relaxed);
    {
      std::lock_guard<std::mutex> lock(udp_peer_auth_identity_mutex_);
      udp_peer_auth_last_identity_.clear();
    }
    if (!configure_udp_aead()) {
      return;
    }
    if (!configure_udp_peer_auth()) {
      return;
    }
    fd_ = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (fd_ < 0) {
      init_error_ = "failed to create UDP loopback socket";
      return;
    }

    udp_socket_buffer_bytes_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_UDP_SOCKET_BUFFER_BYTES", 4 * 1024 * 1024, 64 * 1024 * 1024);
    if (udp_socket_buffer_bytes_ > 0) {
      const int buffer_bytes = udp_socket_buffer_bytes_;
      (void)::setsockopt(fd_, SOL_SOCKET, SO_RCVBUF, &buffer_bytes, sizeof(buffer_bytes));
      (void)::setsockopt(fd_, SOL_SOCKET, SO_SNDBUF, &buffer_bytes, sizeof(buffer_bytes));
    }
    udp_send_pacing_us_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_UDP_SEND_PACING_US", 0, 100000);
    udp_datagram_budget_bytes_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_UDP_DATAGRAM_BUDGET_BYTES", 0,
      static_cast<int>(kMaxUdpPayloadBytes));
    if (udp_datagram_budget_bytes_ > 0) {
      udp_datagram_budget_bytes_ = std::max(512, udp_datagram_budget_bytes_);
    }
    loss_resilient_fragment_chunk_bytes_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES", 0, 60000);
    fragment_nack_interval_ms_ = std::max(
      10,
      parse_nonnegative_int_env(
        "FLEETQOX_RMW_FRAGMENT_NACK_INTERVAL_MS", 50, 1000));
    fragment_nack_max_requests_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_FRAGMENT_NACK_MAX_REQUESTS", 6, 100);
    fragment_nack_max_indexes_per_request_ = std::max(
      1,
      parse_nonnegative_int_env(
        "FLEETQOX_RMW_FRAGMENT_NACK_MAX_INDEXES_PER_REQUEST", 8, 64));
    fragment_tail_guard_ms_ = std::max(
      100,
      parse_nonnegative_int_env(
        "FLEETQOX_RMW_FRAGMENT_TAIL_GUARD_MS", 1000, 60000));
    fragment_history_limit_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_FRAGMENT_HISTORY_LIMIT", 1024, 4096);
    fragment_assembly_limit_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_FRAGMENT_ASSEMBLY_LIMIT", 1024, 16384);
    fragment_max_assembly_bytes_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_FRAGMENT_MAX_ASSEMBLY_BYTES",
      16 * 1024 * 1024,
      256 * 1024 * 1024);
    fragment_assembly_ttl_ms_ = std::max(
      1000,
      parse_nonnegative_int_env(
        "FLEETQOX_RMW_FRAGMENT_ASSEMBLY_TTL_MS", 60000, 600000));
    fragment_send_queue_limit_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_FRAGMENT_SEND_QUEUE_LIMIT", 32768, 262144);
    fragment_queue_admission_threshold_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_FRAGMENT_QUEUE_ADMISSION_THRESHOLD", 0, 262144);
    fragment_queue_admission_timeout_ms_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_FRAGMENT_QUEUE_ADMISSION_TIMEOUT_MS", 0, 60000);
    fragment_repair_queue_limit_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_FRAGMENT_REPAIR_QUEUE_LIMIT", 64, 262144);
    fragment_repair_cooldown_ms_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_FRAGMENT_REPAIR_COOLDOWN_MS", 100, 60000);
    if (const char * async_send_env =
      std::getenv("FLEETQOX_RMW_FRAGMENT_ASYNC_SEND");
      async_send_env != nullptr)
    {
      const std::string value = trim_copy(async_send_env);
      fragment_async_send_enabled_ =
        value == "1" || value == "true" || value == "yes";
    }
    drop_fragment_indexes_ = parse_sequence_list(
      std::getenv("FLEETQOX_RMW_TEST_DROP_FRAGMENT_INDEXES"));

    timeval timeout{};
    timeout.tv_sec = 0;
    timeout.tv_usec = 100000;
    if (::setsockopt(fd_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) != 0) {
      init_error_ = "failed to configure UDP loopback receive timeout";
      ::close(fd_);
      fd_ = -1;
      return;
    }

    sockaddr_in bind_address{};
    const char * bind_env = std::getenv("FLEETQOX_RMW_BIND");
    if (bind_env != nullptr && bind_env[0] != '\0') {
      if (!parse_ipv4_endpoint(bind_env, &bind_address)) {
        init_error_ = "invalid FLEETQOX_RMW_BIND endpoint";
        ::close(fd_);
        fd_ = -1;
        return;
      }
    } else {
      bind_address.sin_family = AF_INET;
      bind_address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
      bind_address.sin_port = 0;
    }
    if (::bind(fd_, reinterpret_cast<const sockaddr *>(&bind_address), sizeof(bind_address)) != 0) {
      init_error_ = "failed to bind UDP loopback socket";
      ::close(fd_);
      fd_ = -1;
      return;
    }

    socklen_t address_length = sizeof(address_);
    if (::getsockname(fd_, reinterpret_cast<sockaddr *>(&address_), &address_length) != 0) {
      init_error_ = "failed to read UDP loopback socket address";
      ::close(fd_);
      fd_ = -1;
      return;
    }
    bound_endpoint_ = endpoint_to_string(address_);

    if (!parse_peer_endpoints(
        std::getenv("FLEETQOX_RMW_PEERS"),
        &peer_addresses_,
        &peer_path_ids_,
        &init_error_))
    {
      ::close(fd_);
      fd_ = -1;
      return;
    }
    fleet_path_plan_ = parse_fleet_path_plan(std::getenv("FLEETQOX_RMW_FLEET_PATH_PLAN"));
    if (const char * plan_file_env = std::getenv("FLEETQOX_RMW_FLEET_PATH_PLAN_FILE");
      plan_file_env != nullptr && plan_file_env[0] != '\0')
    {
      fleet_path_plan_file_ = plan_file_env;
      refresh_fleet_path_plan_from_file();
    }
    repair_path_plan_ = parse_fleet_repair_plan(std::getenv("FLEETQOX_RMW_REPAIR_PATH_PLAN"));
    if (const char * repair_plan_file_env = std::getenv("FLEETQOX_RMW_REPAIR_PATH_PLAN_FILE");
      repair_plan_file_env != nullptr && repair_plan_file_env[0] != '\0')
    {
      repair_path_plan_file_ = repair_plan_file_env;
      refresh_repair_path_plan_from_file();
    }
    repair_retransmission_budget_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_REPAIR_RETRANSMISSION_BUDGET", -1, 1000000);
    repair_min_interval_ms_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_REPAIR_MIN_INTERVAL_MS", 0, 5000);
    repair_max_attempts_per_sequence_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_REPAIR_MAX_ATTEMPTS_PER_SEQUENCE", 0, 1000);
    if (const char * strict_env = std::getenv("FLEETQOX_RMW_REPAIR_ADMISSION_STRICT");
      strict_env != nullptr)
    {
      const std::string value = trim_copy(strict_env);
      repair_admission_strict_ = value == "1" || value == "true" || value == "yes";
    }
    drop_source_sequences_ = parse_sequence_list(std::getenv("FLEETQOX_RMW_DROP_SOURCE_SEQUENCES"));
    drop_source_sequence_send_count_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_DROP_SOURCE_SEQUENCE_SEND_COUNT", 1, 1000);
    proactive_data_repeats_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_PROACTIVE_DATA_REPEATS", 0, 5);
    proactive_data_repeat_interval_ms_ = parse_nonnegative_int_env(
      "FLEETQOX_RMW_PROACTIVE_DATA_REPEAT_INTERVAL_MS", 5, 100);
    if (const char * policy_env = std::getenv("FLEETQOX_RMW_PEER_POLICY");
      policy_env != nullptr && policy_env[0] != '\0')
    {
      peer_policy_ = policy_env;
    }
    if (const char * deadline_env = std::getenv("FLEETQOX_RMW_REDUNDANT_DEADLINE_MS");
      deadline_env != nullptr && deadline_env[0] != '\0')
    {
      char * end = nullptr;
      errno = 0;
      const long deadline_ms = std::strtol(deadline_env, &end, 10);
      if (errno == 0 && end != deadline_env && *end == '\0' && deadline_ms >= 0) {
        adaptive_redundant_deadline_ns_ =
          static_cast<std::int64_t>(deadline_ms) * 1000000ll;
      }
    }
    adaptive_peer_scores_.assign(peer_addresses_.size(), 0);
    if (!quic_gateway_transport_.configure_from_environment()) {
      init_error_ = quic_gateway_transport_.error();
      ::close(fd_);
      fd_ = -1;
      return;
    }
    if (quic_gateway_transport_.enabled()) {
      transport_mode_ = peer_addresses_.empty() ? "quic_gateway" : "udp_quic_gateway_hybrid";
    }

    if (const char * local_transport = std::getenv("FLEETQOX_RMW_LOCAL_TRANSPORT");
      local_transport != nullptr && trim_copy(local_transport) == "shm")
    {
      const char * fallback_env = std::getenv("FLEETQOX_RMW_SHM_FALLBACK_UDP");
      const bool fallback_udp = fallback_env == nullptr ||
        trim_copy(fallback_env) == "1" || trim_copy(fallback_env) == "true" ||
        trim_copy(fallback_env) == "yes";
      const char * name_env = std::getenv("FLEETQOX_RMW_SHM_NAME");
      const std::string shm_name = name_env != nullptr && name_env[0] != '\0' ?
        name_env : "/fleetrmw_default";
      const char * unlink_env = std::getenv("FLEETQOX_RMW_SHM_UNLINK_OWNER");
      const bool unlink_owner = unlink_env != nullptr &&
        (trim_copy(unlink_env) == "1" || trim_copy(unlink_env) == "true" ||
        trim_copy(unlink_env) == "yes");
      if (shared_memory_transport_.start(
          shm_name,
          [this](const std::string & payload) {handle_received_datagram(payload, false);},
          unlink_owner))
      {
        if (quic_gateway_transport_.enabled()) {
          transport_mode_ = peer_addresses_.empty() ?
            "shm_quic_gateway_hybrid" : "shm_udp_quic_gateway_hybrid";
        } else {
          transport_mode_ = peer_addresses_.empty() ? "shm" : "shm_udp_hybrid";
        }
      } else if (fallback_udp) {
        transport_mode_ = quic_gateway_transport_.enabled() ?
          "udp_fallback_quic_gateway_hybrid" : "udp_fallback";
      } else {
        init_error_ = shared_memory_transport_.error();
        ::close(fd_);
        fd_ = -1;
        return;
      }
    }

    running_.store(true, std::memory_order_release);
    if (fragment_async_send_enabled_) {
      fragment_sender_running_.store(true, std::memory_order_release);
      try {
        fragment_sender_thread_ = std::thread([this]() {fragment_sender_loop();});
      } catch (...) {
        fragment_sender_running_.store(false, std::memory_order_release);
        running_.store(false, std::memory_order_release);
        ::close(fd_);
        fd_ = -1;
        init_error_ = "failed to start FleetRMW fragment sender thread";
        return;
      }
    }
    try {
      receive_thread_ = std::thread([this]() { receive_loop(); });
    } catch (...) {
      running_.store(false, std::memory_order_release);
      fragment_sender_running_.store(false, std::memory_order_release);
      fragment_send_queue_cv_.notify_all();
      if (fragment_sender_thread_.joinable()) {
        fragment_sender_thread_.join();
      }
      ::close(fd_);
      fd_ = -1;
      init_error_ = "failed to start UDP loopback receive thread";
      return;
    }
    ready_ = true;
  }

  void stop()
  {
    quic_gateway_transport_.stop();
    shared_memory_transport_.stop();
    running_.store(false, std::memory_order_release);
    fragment_sender_running_.store(false, std::memory_order_release);
    fragment_send_queue_cv_.notify_all();
    if (receive_thread_.joinable()) {
      receive_thread_.join();
    }
    if (fragment_sender_thread_.joinable()) {
      fragment_sender_thread_.join();
    }
    {
      std::lock_guard<std::mutex> lock(fragment_send_queue_mutex_);
      fragment_initial_send_queues_.clear();
      fragment_initial_send_order_.clear();
      fragment_initial_send_queue_size_ = 0;
      fragment_repair_send_queues_.clear();
      fragment_repair_send_order_.clear();
      fragment_repair_send_queue_size_ = 0;
      fragment_repair_pending_keys_.clear();
      fragment_repair_recent_send_ns_.clear();
    }
    {
      std::lock_guard<std::mutex> lock(fragment_mutex_);
      fragment_assemblies_.clear();
      completed_fragment_assemblies_.clear();
    }
    {
      std::lock_guard<std::mutex> lock(fragment_history_mutex_);
      fragment_history_.clear();
    }
    {
      std::lock_guard<std::mutex> lock(test_drop_mutex_);
      dropped_fragment_keys_.clear();
    }
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
    EVP_PKEY_free(udp_peer_auth_local_private_key_);
    udp_peer_auth_local_private_key_ = nullptr;
    X509_free(udp_peer_auth_local_certificate_);
    udp_peer_auth_local_certificate_ = nullptr;
    X509_STORE_free(udp_peer_auth_trust_store_);
    udp_peer_auth_trust_store_ = nullptr;
    ready_ = false;
  }

  void receive_loop()
  {
    std::array<char, kMaxUdpPayloadBytes> buffer{};
    while (running_.load(std::memory_order_acquire)) {
      sockaddr_in source{};
      socklen_t source_size = sizeof(source);
      const auto received = ::recvfrom(
        fd_,
        buffer.data(),
        buffer.size(),
        0,
        reinterpret_cast<sockaddr *>(&source),
        &source_size);
      if (received < 0) {
        if (!running_.load(std::memory_order_acquire)) {
          break;
        }
        if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) {
          request_stale_missing_fragments();
          continue;
        }
        break;
      }
      if (received == 0) {
        continue;
      }
      const std::string encoded_frame(buffer.data(), static_cast<size_t>(received));
      if (hybrid_transport() && !udp_aead_enabled_) {
        if (!shared_memory_transport_.send(encoded_frame)) {
          handle_received_datagram(encoded_frame, true, &source);
        }
      } else {
        handle_received_datagram(encoded_frame, true, &source);
      }
      request_stale_missing_fragments();
    }
  }

  bool shared_memory_only() const
  {
    return transport_mode_ == "shm" && shared_memory_transport_.ready() &&
           peer_addresses_.empty();
  }

  bool hybrid_transport() const
  {
    return transport_mode_ == "shm_udp_hybrid" && shared_memory_transport_.ready();
  }

  bool shared_memory_active() const
  {
    return shared_memory_only() || hybrid_transport();
  }

  rmw_ret_t send_shared_memory_payload(const std::string & payload)
  {
    if (!shared_memory_transport_.send(payload)) {
      RMW_SET_ERROR_MSG(shared_memory_transport_.error().empty() ?
        "failed to send FleetRMW payload through shared memory" :
        shared_memory_transport_.error().c_str());
      return payload.size() > rmw_fleetqox_cpp::SharedMemoryTransport::max_payload_size() ?
             RMW_RET_UNSUPPORTED : RMW_RET_ERROR;
    }
    return RMW_RET_OK;
  }

  rmw_ret_t send_quic_gateway_payload(const std::string & payload)
  {
    if (!quic_gateway_transport_.send(payload)) {
      const std::string error = quic_gateway_transport_.error();
      RMW_SET_ERROR_MSG(error.empty() ?
        "failed to send FleetRMW payload through QUIC gateway transport" :
        error.c_str());
      return RMW_RET_ERROR;
    }
    return RMW_RET_OK;
  }

  static bool parse_size_token(const std::string & token, size_t * value)
  {
    if (value == nullptr || token.empty()) {
      return false;
    }
    char * end = nullptr;
    errno = 0;
    const unsigned long long parsed = std::strtoull(token.c_str(), &end, 10);
    if (errno != 0 || end == token.c_str() || *end != '\0') {
      return false;
    }
    if (parsed > static_cast<unsigned long long>(std::numeric_limits<size_t>::max())) {
      return false;
    }
    *value = static_cast<size_t>(parsed);
    return true;
  }

  void cleanup_stale_fragment_assemblies_locked(std::int64_t now_ns)
  {
    const std::int64_t assembly_ttl_ns =
      static_cast<std::int64_t>(fragment_assembly_ttl_ms_) * 1000000ll;
    for (auto it = fragment_assemblies_.begin(); it != fragment_assemblies_.end();) {
      if (it->second.last_update_ns > 0 &&
        now_ns - it->second.last_update_ns > assembly_ttl_ns)
      {
        const size_t missing =
          it->second.fragment_count - std::min(
          it->second.received_count, it->second.fragment_count);
        fragment_assembly_ttl_expirations_.fetch_add(
          1, std::memory_order_relaxed);
        fragment_assembly_ttl_expired_missing_indexes_.fetch_add(
          static_cast<std::uint64_t>(missing), std::memory_order_relaxed);
        it = fragment_assemblies_.erase(it);
      } else {
        ++it;
      }
    }
    for (auto it = completed_fragment_assemblies_.begin();
      it != completed_fragment_assemblies_.end();)
    {
      if (now_ns - it->second > kFragmentHistoryTtlNs) {
        it = completed_fragment_assemblies_.erase(it);
      } else {
        ++it;
      }
    }
  }

  void remember_completed_fragment_assembly_locked(
    const std::string & assembly_key,
    std::int64_t now_ns)
  {
    completed_fragment_assemblies_[assembly_key] = now_ns;
    const size_t limit = std::max<size_t>(
      64,
      static_cast<size_t>(std::max(fragment_history_limit_, 1)) * 4);
    while (completed_fragment_assemblies_.size() > limit) {
      auto oldest = completed_fragment_assemblies_.end();
      for (auto it = completed_fragment_assemblies_.begin();
        it != completed_fragment_assemblies_.end(); ++it)
      {
        if (oldest == completed_fragment_assemblies_.end() ||
          it->second < oldest->second)
        {
          oldest = it;
        }
      }
      if (oldest == completed_fragment_assemblies_.end()) {
        break;
      }
      completed_fragment_assemblies_.erase(oldest);
    }
  }

  static std::vector<size_t> missing_fragment_indexes(
    const FragmentAssembly & assembly,
    bool include_trailing_indexes,
    size_t max_indexes)
  {
    std::vector<size_t> missing;
    missing.reserve(std::min(
      assembly.fragment_count,
      max_indexes));
    const size_t scan_count = include_trailing_indexes ?
      assembly.received.size() :
      std::min(
      assembly.received.size(),
      assembly.highest_received_index + 1);
    for (size_t index = 0;
      index < scan_count &&
      missing.size() < max_indexes;
      ++index)
    {
      if (!assembly.received[index]) {
        missing.push_back(index);
      }
    }
    return missing;
  }

  static std::string encode_fragment_indexes(const std::vector<size_t> & indexes)
  {
    std::ostringstream output;
    size_t position = 0;
    while (position < indexes.size()) {
      const size_t first = indexes[position];
      size_t last = first;
      while (position + 1 < indexes.size() &&
        indexes[position + 1] == last + 1)
      {
        last = indexes[++position];
      }
      if (output.tellp() > 0) {
        output << ",";
      }
      output << first;
      if (last != first) {
        output << "-" << last;
      }
      ++position;
    }
    return output.str();
  }

  void send_fragment_repair_request(
    const std::string & fragment_id,
    size_t fragment_count,
    const std::vector<size_t> & missing,
    const sockaddr_in & source)
  {
    if (fragment_id.empty() || missing.empty()) {
      return;
    }
    std::string request(kRepairFragmentNackPrefix);
    request.append(fragment_id);
    request.push_back('|');
    request.append(std::to_string(fragment_count));
    request.push_back('|');
    request.append(encode_fragment_indexes(missing));
    if (send_payload_to_targets(
        request,
        std::vector<sockaddr_in>{source},
        "FleetRMW fragment repair request") == RMW_RET_OK)
    {
      fragment_nacks_sent_.fetch_add(1, std::memory_order_relaxed);
    }
  }

  void request_stale_missing_fragments()
  {
    struct PendingRequest
    {
      std::string fragment_id;
      size_t fragment_count{0};
      std::vector<size_t> missing;
      sockaddr_in source{};
    };
    struct CandidateRequest
    {
      std::string assembly_key;
      FragmentAssembly * assembly{nullptr};
      std::vector<size_t> missing;
      bool progress_since_previous_nack{false};
    };
    const std::int64_t now_ns = monotonic_timestamp_ns();
    const std::int64_t interval_ns =
      static_cast<std::int64_t>(fragment_nack_interval_ms_) * 1000000ll;
    std::vector<PendingRequest> pending;
    {
      std::lock_guard<std::mutex> lock(fragment_mutex_);
      cleanup_stale_fragment_assemblies_locked(now_ns);
      std::vector<CandidateRequest> candidates;
      candidates.reserve(fragment_assemblies_.size());
      for (auto & item : fragment_assemblies_) {
        FragmentAssembly & assembly = item.second;
        const size_t backoff_shift = std::min<size_t>(assembly.nack_count, 3);
        const std::int64_t retry_interval_ns =
          interval_ns * static_cast<std::int64_t>(1u << backoff_shift);
        const bool progress_since_previous_nack =
          assembly.nack_count > 0 &&
          assembly.last_update_ns > assembly.last_nack_ns;
        const bool initial_quiescence_pending =
          assembly.nack_count == 0 &&
          now_ns - assembly.last_update_ns < interval_ns;
        const bool retry_backoff_pending =
          assembly.last_nack_ns > 0 &&
          now_ns - assembly.last_nack_ns < retry_interval_ns;
        const bool bounded_progress_grace_pending =
          progress_since_previous_nack &&
          now_ns - assembly.last_update_ns < interval_ns &&
          now_ns - assembly.last_nack_ns < retry_interval_ns + interval_ns;
        if (!assembly.repair_capable || !assembly.source_available ||
          assembly.received_count >= assembly.fragment_count ||
          assembly.nack_count >= static_cast<size_t>(fragment_nack_max_requests_) ||
          initial_quiescence_pending ||
          retry_backoff_pending)
        {
          continue;
        }
        if (bounded_progress_grace_pending) {
          fragment_progress_grace_deferrals_.fetch_add(
            1, std::memory_order_relaxed);
          continue;
        }
        const std::int64_t tail_guard_ns = std::max<std::int64_t>(
          static_cast<std::int64_t>(fragment_tail_guard_ms_) * 1000000ll,
          interval_ns * 4);
        const bool last_fragment_observed =
          !assembly.received.empty() && assembly.received.back();
        const bool include_trailing_indexes =
          last_fragment_observed ||
          assembly.sender_complete_observed ||
          now_ns - assembly.last_update_ns >= tail_guard_ns;
        std::vector<size_t> missing = missing_fragment_indexes(
          assembly,
          include_trailing_indexes,
          static_cast<size_t>(fragment_nack_max_indexes_per_request_));
        if (!missing.empty()) {
          candidates.push_back(CandidateRequest{
            item.first,
            &assembly,
            std::move(missing),
            progress_since_previous_nack});
        }
      }
      if (candidates.empty()) {
        fragment_nack_sweep_cursor_ = 0;
        return;
      }
      std::sort(
        candidates.begin(),
        candidates.end(),
        [](const CandidateRequest & left, const CandidateRequest & right) {
          return left.assembly_key < right.assembly_key;
        });
      if (fragment_nack_sweep_window_start_ns_ == 0 ||
        now_ns - fragment_nack_sweep_window_start_ns_ >= interval_ns)
      {
        // Refill the fleet-wide repair-index budget at most once per
        // fragment_nack_interval_ms_ window, instead of on every call to
        // this function. Without this, a receive loop that invokes this
        // sweep many times within one window (one call per received
        // datagram) grants a fresh kFleetFragmentRepairIndexesPerSweep
        // budget each time, so the cumulative index count over the window
        // scales with call frequency rather than staying bounded.
        fragment_nack_sweep_window_budget_ = kFleetFragmentRepairIndexesPerSweep;
        fragment_nack_sweep_window_start_ns_ = now_ns;
      }
      const size_t eligible_assemblies = candidates.size();
      const size_t fleet_fair_share = std::max<size_t>(
        1,
        fragment_nack_sweep_window_budget_ / eligible_assemblies);
      const size_t request_index_limit = std::min(
        static_cast<size_t>(fragment_nack_max_indexes_per_request_),
        fleet_fair_share);
      const size_t window_budget_at_call_start =
        fragment_nack_sweep_window_budget_;
      size_t remaining_index_budget = window_budget_at_call_start;
      const size_t start_index =
        fragment_nack_sweep_cursor_ % eligible_assemblies;
      size_t visited = 0;
      for (; visited < eligible_assemblies && remaining_index_budget > 0;
        ++visited)
      {
        const size_t candidate_index =
          (start_index + visited) % eligible_assemblies;
        CandidateRequest & candidate = candidates[candidate_index];
        FragmentAssembly & assembly = *candidate.assembly;
        const size_t candidate_limit = std::min(
          request_index_limit, remaining_index_budget);
        if (candidate.missing.size() > candidate_limit) {
          fragment_nack_index_budget_reductions_.fetch_add(
            1, std::memory_order_relaxed);
          candidate.missing.resize(candidate_limit);
        }
        fragment_nack_indexes_requested_.fetch_add(
          static_cast<std::uint64_t>(candidate.missing.size()),
          std::memory_order_relaxed);
        remaining_index_budget -= candidate.missing.size();
        assembly.last_nack_ns = now_ns;
        ++assembly.nack_count;
        if (candidate.progress_since_previous_nack) {
          fragment_progressive_nacks_sent_.fetch_add(
            1, std::memory_order_relaxed);
        }
        pending.push_back(PendingRequest{
          assembly.fragment_id,
          assembly.fragment_count,
          std::move(candidate.missing),
          assembly.source});
      }
      fragment_nack_sweep_window_budget_ = remaining_index_budget;
      const size_t sweep_indexes_requested =
        window_budget_at_call_start - remaining_index_budget;
      size_t previous_max = fragment_nack_max_sweep_indexes_requested_.load(
        std::memory_order_relaxed);
      while (previous_max < sweep_indexes_requested &&
        !fragment_nack_max_sweep_indexes_requested_.compare_exchange_weak(
          previous_max,
          sweep_indexes_requested,
          std::memory_order_relaxed))
      {
      }
      if (remaining_index_budget == 0 && visited < eligible_assemblies) {
        fragment_nack_sweep_budget_exhaustions_.fetch_add(
          1, std::memory_order_relaxed);
      }
      fragment_nack_sweep_cursor_ =
        (start_index + std::max<size_t>(visited, 1)) % eligible_assemblies;
    }
    for (const PendingRequest & request : pending) {
      send_fragment_repair_request(
        request.fragment_id,
        request.fragment_count,
        request.missing,
        request.source);
    }
  }

  static bool parse_fragment_index_ranges(
    const std::string & text,
    size_t fragment_count,
    std::vector<size_t> * indexes)
  {
    if (indexes == nullptr || text.empty() || fragment_count == 0) {
      return false;
    }
    indexes->clear();
    size_t start = 0;
    while (start < text.size() &&
      indexes->size() < kMaxFragmentRepairIndexesPerRequest)
    {
      const size_t end = text.find(',', start);
      const std::string token = text.substr(
        start,
        end == std::string::npos ? std::string::npos : end - start);
      const size_t dash = token.find('-');
      size_t first = 0;
      size_t last = 0;
      if (dash == std::string::npos) {
        if (!parse_size_token(token, &first)) {
          return false;
        }
        last = first;
      } else {
        if (!parse_size_token(token.substr(0, dash), &first) ||
          !parse_size_token(token.substr(dash + 1), &last) ||
          first > last)
        {
          return false;
        }
      }
      if (last >= fragment_count) {
        return false;
      }
      for (size_t index = first;
        index <= last &&
        indexes->size() < kMaxFragmentRepairIndexesPerRequest;
        ++index)
      {
        if (indexes->empty() || indexes->back() != index) {
          indexes->push_back(index);
        }
      }
      if (end == std::string::npos) {
        break;
      }
      start = end + 1;
    }
    return !indexes->empty();
  }

  bool handle_fragment_completion_marker(
    const std::string & payload,
    const sockaddr_in * source)
  {
    const std::string prefix(kRepairFragmentCompletionPrefix);
    if (payload.rfind(prefix, 0) != 0) {
      return false;
    }
    const size_t first_separator = payload.find('|', prefix.size());
    const size_t second_separator =
      first_separator == std::string::npos ?
      std::string::npos : payload.find('|', first_separator + 1);
    if (first_separator == std::string::npos ||
      second_separator == std::string::npos)
    {
      fragment_completion_marker_failures_.fetch_add(
        1, std::memory_order_relaxed);
      return true;
    }
    const std::string fragment_id =
      payload.substr(prefix.size(), first_separator - prefix.size());
    size_t fragment_count = 0;
    size_t total_size = 0;
    if (fragment_id.empty() ||
      !parse_size_token(
        payload.substr(
          first_separator + 1,
          second_separator - first_separator - 1),
        &fragment_count) ||
      !parse_size_token(payload.substr(second_separator + 1), &total_size) ||
      fragment_count == 0 || total_size == 0)
    {
      fragment_completion_marker_failures_.fetch_add(
        1, std::memory_order_relaxed);
      return true;
    }
    fragment_completion_markers_received_.fetch_add(
      1, std::memory_order_relaxed);
    const std::int64_t now_ns = monotonic_timestamp_ns();
    std::lock_guard<std::mutex> lock(fragment_mutex_);
    cleanup_stale_fragment_assemblies_locked(now_ns);
    const std::string assembly_key =
      std::string(kRepairFragmentPrefix) + fragment_id;
    if (completed_fragment_assemblies_.find(assembly_key) !=
      completed_fragment_assemblies_.end())
    {
      return true;
    }
    const auto found = fragment_assemblies_.find(assembly_key);
    if (found == fragment_assemblies_.end()) {
      fragment_completion_marker_orphans_.fetch_add(
        1, std::memory_order_relaxed);
      return true;
    }
    FragmentAssembly & assembly = found->second;
    if (assembly.fragment_count != fragment_count ||
      assembly.total_size != total_size ||
      (source != nullptr && assembly.source_available &&
      !endpoints_match(*source, assembly.source)))
    {
      fragment_completion_marker_failures_.fetch_add(
        1, std::memory_order_relaxed);
      return true;
    }
    if (source != nullptr) {
      assembly.source = *source;
      assembly.source_available = true;
    }
    assembly.sender_complete_observed = true;
    assembly.last_update_ns = now_ns;
    return true;
  }

  bool handle_fragment_repair_request(
    const std::string & payload,
    const sockaddr_in * source)
  {
    const std::string prefix(kRepairFragmentNackPrefix);
    if (payload.rfind(prefix, 0) != 0) {
      return false;
    }
    const size_t first_separator = payload.find('|', prefix.size());
    const size_t second_separator =
      first_separator == std::string::npos ?
      std::string::npos : payload.find('|', first_separator + 1);
    if (first_separator == std::string::npos ||
      second_separator == std::string::npos)
    {
      return true;
    }
    const std::string fragment_id =
      payload.substr(prefix.size(), first_separator - prefix.size());
    size_t fragment_count = 0;
    if (fragment_id.empty() ||
      !parse_size_token(
        payload.substr(
          first_separator + 1,
          second_separator - first_separator - 1),
        &fragment_count))
    {
      return true;
    }
    std::vector<size_t> indexes;
    if (!parse_fragment_index_ranges(
        payload.substr(second_separator + 1),
        fragment_count,
        &indexes))
    {
      return true;
    }

    FragmentRepairHistory history;
    std::string request_target_scope;
    {
      const std::int64_t now_ns = monotonic_timestamp_ns();
      std::lock_guard<std::mutex> lock(fragment_history_mutex_);
      cleanup_fragment_history_locked(now_ns);
      const auto found = fragment_history_.find(fragment_id);
      if (found == fragment_history_.end() ||
        found->second.fragment_count != fragment_count)
      {
        return true;
      }
      if (source != nullptr) {
        const bool source_was_targeted = std::any_of(
          found->second.targets.begin(),
          found->second.targets.end(),
          [source](const sockaddr_in & target) {
            return endpoints_match(target, *source);
          });
        if (!source_was_targeted) {
          fragment_repair_source_denials_.fetch_add(
            1, std::memory_order_relaxed);
          return true;
        }
        request_target_scope = endpoint_to_string(*source);
      } else {
        request_target_scope =
          fragment_repair_target_scope_key(found->second.targets);
      }
      const auto target_count =
        found->second.request_count_by_target.find(request_target_scope);
      const size_t current_target_count =
        target_count == found->second.request_count_by_target.end() ?
        0 : target_count->second;
      if (current_target_count >=
        static_cast<size_t>(fragment_nack_max_requests_))
      {
        fragment_repair_reader_budget_exhausted_.fetch_add(
          1, std::memory_order_relaxed);
        return true;
      }
      found->second.last_update_ns = now_ns;
      history = found->second;
    }
    std::vector<sockaddr_in> targets =
      source == nullptr ? history.targets : std::vector<sockaddr_in>{*source};
    if (targets.empty()) {
      return true;
    }
    if (history.payload) {
      record_fragment_repair_observation(*history.payload);
    }
    fragment_nacks_received_.fetch_add(1, std::memory_order_relaxed);
    rmw_ret_t repair_ret = RMW_RET_ERROR;
    if (fragment_async_send_enabled_) {
      repair_ret = enqueue_loss_resilient_fragment_indexes(
        history.payload,
        targets,
        history.is_data_frame,
        fragment_id,
        history.chunk_bytes,
        history.fragment_count,
        indexes,
        true);
    } else if (history.payload) {
      repair_ret = send_loss_resilient_fragment_indexes(
        *history.payload,
        targets,
        "FleetRMW selective fragment repair",
        history.is_data_frame,
        fragment_id,
        history.chunk_bytes,
        history.fragment_count,
        indexes,
        true);
    }
    if (repair_ret == RMW_RET_OK) {
      const std::int64_t now_ns = monotonic_timestamp_ns();
      std::lock_guard<std::mutex> lock(fragment_history_mutex_);
      const auto found = fragment_history_.find(fragment_id);
      if (found != fragment_history_.end() &&
        found->second.fragment_count == fragment_count)
      {
        ++found->second.request_count_by_target[request_target_scope];
        found->second.last_update_ns = now_ns;
      }
    }
    return true;
  }

  bool try_reassemble_fragment(
    const std::string & datagram,
    std::string * complete_payload,
    const sockaddr_in * source = nullptr)
  {
    if (complete_payload != nullptr) {
      complete_payload->clear();
    }
    std::string prefix;
    if (datagram.rfind(kFragmentPrefix, 0) == 0) {
      prefix = kFragmentPrefix;
    } else if (datagram.rfind(kRepairFragmentPrefix, 0) == 0) {
      prefix = kRepairFragmentPrefix;
    } else {
      return false;
    }
    size_t field_start = prefix.size();
    std::array<size_t, 4> separators{};
    for (size_t i = 0; i < separators.size(); ++i) {
      separators[i] = datagram.find('|', field_start);
      if (separators[i] == std::string::npos) {
        return true;
      }
      field_start = separators[i] + 1;
    }
    const std::string fragment_id =
      datagram.substr(prefix.size(), separators[0] - prefix.size());
    size_t fragment_index = 0;
    size_t fragment_count = 0;
    size_t total_size = 0;
    if (fragment_id.empty() ||
      !parse_size_token(
        datagram.substr(separators[0] + 1, separators[1] - separators[0] - 1),
        &fragment_index) ||
      !parse_size_token(
        datagram.substr(separators[1] + 1, separators[2] - separators[1] - 1),
        &fragment_count) ||
      !parse_size_token(
        datagram.substr(separators[2] + 1, separators[3] - separators[2] - 1),
        &total_size) ||
      fragment_count == 0 ||
      fragment_index >= fragment_count ||
      total_size == 0 ||
      fragment_count > 4096)
    {
      return true;
    }
    const std::string chunk = datagram.substr(separators[3] + 1);
    if ((fragment_max_assembly_bytes_ > 0 &&
      total_size > static_cast<size_t>(fragment_max_assembly_bytes_)) ||
      chunk.size() > total_size)
    {
      fragment_assembly_oversize_drops_.fetch_add(
        1, std::memory_order_relaxed);
      return true;
    }
    const std::int64_t now_ns = monotonic_timestamp_ns();
    std::unique_lock<std::mutex> lock(fragment_mutex_);
    cleanup_stale_fragment_assemblies_locked(now_ns);
    const std::string assembly_key = prefix + fragment_id;
    if (completed_fragment_assemblies_.find(assembly_key) !=
      completed_fragment_assemblies_.end())
    {
      completed_fragment_duplicates_dropped_.fetch_add(
        1, std::memory_order_relaxed);
      return true;
    }
    auto assembly_it = fragment_assemblies_.find(assembly_key);
    if (assembly_it == fragment_assemblies_.end() &&
      fragment_assembly_limit_ > 0)
    {
      const size_t limit = static_cast<size_t>(fragment_assembly_limit_);
      while (fragment_assemblies_.size() >= limit) {
        auto oldest = fragment_assemblies_.end();
        for (auto it = fragment_assemblies_.begin();
          it != fragment_assemblies_.end(); ++it)
        {
          if (oldest == fragment_assemblies_.end() ||
            it->second.last_update_ns < oldest->second.last_update_ns)
          {
            oldest = it;
          }
        }
        if (oldest == fragment_assemblies_.end()) {
          break;
        }
        fragment_assemblies_.erase(oldest);
        fragment_assembly_evictions_.fetch_add(
          1, std::memory_order_relaxed);
      }
      assembly_it = fragment_assemblies_.emplace(
        assembly_key, FragmentAssembly{}).first;
    } else if (assembly_it == fragment_assemblies_.end()) {
      assembly_it = fragment_assemblies_.emplace(
        assembly_key, FragmentAssembly{}).first;
    }
    FragmentAssembly & assembly = assembly_it->second;
    if (assembly.fragment_count == 0) {
      assembly.fragment_count = fragment_count;
      assembly.total_size = total_size;
      assembly.chunks.assign(fragment_count, std::string{});
      assembly.received.assign(fragment_count, false);
      assembly.first_update_ns = now_ns;
      assembly.fragment_id = fragment_id;
      assembly.repair_capable = prefix == kRepairFragmentPrefix;
    }
    if (assembly.fragment_count != fragment_count || assembly.total_size != total_size) {
      fragment_assembly_metadata_mismatch_drops_.fetch_add(
        1, std::memory_order_relaxed);
      return true;
    }
    if (source != nullptr) {
      assembly.source = *source;
      assembly.source_available = true;
    }
    if (!assembly.received[fragment_index]) {
      assembly.chunks[fragment_index] = chunk;
      assembly.received[fragment_index] = true;
      assembly.received_count += 1;
      assembly.highest_received_index = std::max(
        assembly.highest_received_index, fragment_index);
      assembly.last_update_ns = now_ns;
    } else {
      fragment_duplicate_no_progress_drops_.fetch_add(
        1, std::memory_order_relaxed);
    }
    if (assembly.received_count != assembly.fragment_count) {
      lock.unlock();
      return true;
    }
    std::string reassembled;
    reassembled.reserve(assembly.total_size);
    for (const std::string & part : assembly.chunks) {
      reassembled.append(part);
    }
    if (reassembled.size() != total_size) {
      fragment_assemblies_.erase(assembly_key);
      return true;
    }
    fragment_assemblies_.erase(assembly_key);
    remember_completed_fragment_assembly_locked(assembly_key, now_ns);
    if (complete_payload != nullptr) {
      *complete_payload = std::move(reassembled);
    }
    return true;
  }

  void handle_received_datagram(
    const std::string & datagram,
    bool udp_wire_payload,
    const sockaddr_in * source = nullptr)
  {
    std::string complete_payload;
    std::string wire_payload;
    const bool strict_protected_fragment_wrapper =
      udp_wire_payload && (udp_aead_required_ || udp_peer_auth_required_) &&
      (datagram.rfind(kRepairFragmentPrefix, 0) == 0 ||
      datagram.rfind(kRepairFragmentCompletionPrefix, 0) == 0 ||
      datagram.rfind(kFragmentPrefix, 0) == 0);
    if (!strict_protected_fragment_wrapper &&
      try_reassemble_fragment(datagram, &complete_payload, source))
    {
      if (complete_payload.empty()) {
        return;
      }
      wire_payload = std::move(complete_payload);
    } else {
      wire_payload = datagram;
    }
    if (!udp_wire_payload) {
      handle_received_payload(wire_payload);
      return;
    }
    std::string authenticated_content;
    if (!unprotect_udp_peer_authenticated_payload(
        wire_payload, &authenticated_content))
    {
      return;
    }
    std::string plaintext;
    if (!unprotect_udp_payload(authenticated_content, &plaintext)) {
      return;
    }
    std::string repaired_payload;
    if (handle_fragment_completion_marker(plaintext, source)) {
      return;
    }
    if (handle_fragment_repair_request(plaintext, source)) {
      return;
    }
    if (try_reassemble_fragment(plaintext, &repaired_payload, source)) {
      if (repaired_payload.empty()) {
        return;
      }
      handle_received_payload(repaired_payload);
      return;
    }
    handle_received_payload(plaintext);
  }

  void handle_received_payload(const std::string & encoded_frame)
  {
    frames_received_.fetch_add(1, std::memory_order_relaxed);
    if (handle_unrecoverable_loss_notice(encoded_frame)) {
      return;
    }
    if (handle_ack_nack_feedback(encoded_frame)) {
      return;
    }
    if (apply_received_graph_advertisement(encoded_frame)) {
      return;
    }
    if (rmw_fleetqox_cpp_handle_service_frame(encoded_frame.data(), encoded_frame.size())) {
      return;
    }
    if (!rmw_fleetqox_cpp::decode_data_frame(encoded_frame)) {
      return;
    }
    data_frames_received_.fetch_add(1, std::memory_order_relaxed);
    enqueue_received_frame(encoded_frame);
  }

  int fd_{-1};
  sockaddr_in address_{};
  std::thread receive_thread_;
  std::thread fragment_sender_thread_;
  std::mutex lifecycle_mutex_;
  std::atomic_bool running_{false};
  std::atomic_bool fragment_sender_running_{false};
  std::atomic<std::uint64_t> frames_sent_{0};
  std::atomic<std::uint64_t> frames_received_{0};
  std::atomic<std::uint64_t> data_frames_received_{0};
  bool udp_aead_enabled_{false};
  bool udp_aead_required_{false};
  bool udp_aead_tamper_outbound_once_{false};
  std::array<unsigned char, 32> udp_aead_key_{};
  std::array<unsigned char, 16> udp_aead_session_salt_{};
  std::array<unsigned char, 32> udp_aead_session_key_{};
  int udp_aead_session_key_rotate_frames_{0};
  std::uint64_t udp_aead_session_frames_{0};
  std::mutex udp_aead_session_mutex_;
  std::mutex udp_aead_received_session_mutex_;
  std::unordered_map<std::string, std::array<unsigned char, 32>>
    udp_aead_received_session_keys_;
  std::deque<std::string> udp_aead_received_session_order_;
  std::array<unsigned char, 4> udp_aead_nonce_prefix_{};
  std::atomic<std::uint64_t> udp_aead_nonce_sequence_{0};
  std::atomic<bool> udp_aead_tamper_done_{false};
  std::atomic<std::uint64_t> udp_aead_encrypted_frames_{0};
  std::atomic<std::uint64_t> udp_aead_decrypted_frames_{0};
  std::atomic<std::uint64_t> udp_aead_authentication_failures_{0};
  std::atomic<std::uint64_t> udp_aead_unprotected_drops_{0};
  std::atomic<std::uint64_t> udp_aead_replay_drops_{0};
  std::atomic<std::uint64_t> udp_aead_session_keys_derived_{0};
  std::atomic<std::uint64_t> udp_aead_session_key_rotations_{0};
  std::atomic<std::uint64_t> udp_aead_session_key_reuses_{0};
  std::mutex udp_aead_replay_mutex_;
  std::unordered_set<std::string> udp_aead_seen_nonces_;
  std::deque<std::string> udp_aead_nonce_order_;
  bool udp_peer_auth_enabled_{false};
  bool udp_peer_auth_required_{false};
  bool udp_peer_auth_tamper_outbound_once_{false};
  bool udp_peer_auth_crl_enabled_{false};
  X509 * udp_peer_auth_local_certificate_{nullptr};
  EVP_PKEY * udp_peer_auth_local_private_key_{nullptr};
  X509_STORE * udp_peer_auth_trust_store_{nullptr};
  std::string udp_peer_auth_local_certificate_der_;
  std::vector<std::string> udp_peer_auth_allowed_identities_;
  std::atomic<bool> udp_peer_auth_tamper_done_{false};
  std::atomic<std::uint64_t> udp_peer_auth_signed_frames_{0};
  std::atomic<std::uint64_t> udp_peer_auth_verified_frames_{0};
  std::atomic<std::uint64_t> udp_peer_auth_failures_{0};
  std::atomic<std::uint64_t> udp_peer_auth_chain_failures_{0};
  std::atomic<std::uint64_t> udp_peer_auth_signature_failures_{0};
  std::atomic<std::uint64_t> udp_peer_auth_identity_denied_{0};
  std::atomic<std::uint64_t> udp_peer_auth_revoked_certificate_drops_{0};
  mutable std::mutex udp_peer_auth_identity_mutex_;
  std::string udp_peer_auth_last_identity_;
  std::atomic<std::uint64_t> fragment_sequence_{0};
  std::atomic<std::uint64_t> ack_nack_sent_{0};
  std::atomic<std::uint64_t> ack_nack_received_{0};
  std::atomic<std::uint64_t> ack_nack_duplicate_received_{0};
  std::atomic<std::uint64_t> ack_nack_out_of_order_received_{0};
  std::atomic<std::uint64_t> unrecoverable_loss_notices_sent_{0};
  std::atomic<std::uint64_t> unrecoverable_loss_notices_received_{0};
  std::atomic<std::uint64_t> nack_retransmissions_{0};
  std::atomic<std::uint64_t> fragment_nacks_sent_{0};
  std::atomic<std::uint64_t> fragment_nacks_received_{0};
  std::atomic<std::uint64_t> fragments_selectively_retransmitted_{0};
  std::atomic<std::uint64_t> fragment_repair_requests_coalesced_{0};
  std::atomic<std::uint64_t> fragment_repair_cooldown_coalesced_{0};
  std::atomic<std::uint64_t> completed_fragment_duplicates_dropped_{0};
  std::atomic<std::uint64_t> fragment_duplicate_no_progress_drops_{0};
  std::atomic<std::uint64_t> test_dropped_fragments_{0};
  std::atomic<std::uint64_t> fragment_send_queue_rejections_{0};
  std::atomic<std::uint64_t> fragment_send_failures_{0};
  std::atomic<size_t> fragment_send_queue_high_water_{0};
  std::atomic<size_t> fragment_repair_queue_high_water_{0};
  std::atomic<std::uint64_t> fragment_repair_round_robin_rotations_{0};
  std::atomic<std::uint64_t> fragment_repair_frame_switches_{0};
  std::atomic<size_t> fragment_repair_max_active_frames_{0};
  std::atomic<size_t>
    fragment_repair_max_consecutive_same_frame_while_contended_{0};
  std::atomic<size_t> udp_datagram_size_high_water_{0};
  std::atomic<size_t> fragment_effective_chunk_bytes_min_{0};
  std::atomic<size_t> fragment_effective_chunk_bytes_max_{0};
  std::atomic<std::uint64_t> fragment_chunk_budget_reductions_{0};
  std::atomic<std::uint64_t> udp_datagram_budget_failures_{0};
  std::atomic<std::uint64_t> fragment_queue_admission_waits_{0};
  std::atomic<std::uint64_t> fragment_queue_admission_timeouts_{0};
  std::atomic<std::uint64_t> fragment_queue_admission_wait_ns_{0};
  std::atomic<std::uint64_t> fragment_repair_queue_deferrals_{0};
  std::atomic<std::uint64_t> fragment_repair_pressure_priority_promotions_{0};
  std::atomic<std::uint64_t> fragment_completion_markers_sent_{0};
  std::atomic<std::uint64_t> fragment_completion_markers_received_{0};
  std::atomic<std::uint64_t> fragment_completion_marker_orphans_{0};
  std::atomic<std::uint64_t> fragment_completion_marker_failures_{0};
  std::atomic<std::uint64_t> fragment_repair_source_denials_{0};
  std::atomic<std::uint64_t> fragment_repair_reader_budget_exhausted_{0};
  std::atomic<std::uint64_t> fragment_initial_round_robin_rotations_{0};
  std::atomic<std::uint64_t> fragment_initial_frame_switches_{0};
  std::atomic<size_t> fragment_initial_max_consecutive_same_frame_{0};
  std::atomic<size_t>
    fragment_initial_max_consecutive_same_frame_while_contended_{0};
  std::atomic<size_t> fragment_initial_max_active_frames_{0};
  std::atomic<std::uint64_t> fragment_nack_indexes_requested_{0};
  std::atomic<std::uint64_t> fragment_nack_index_budget_reductions_{0};
  std::atomic<size_t> fragment_nack_max_sweep_indexes_requested_{0};
  std::atomic<std::uint64_t> fragment_nack_sweep_budget_exhaustions_{0};
  std::atomic<std::uint64_t> fragment_progressive_nacks_sent_{0};
  std::atomic<std::uint64_t> fragment_progress_grace_deferrals_{0};
  std::atomic<std::uint64_t> fragment_assembly_evictions_{0};
  std::atomic<std::uint64_t> fragment_assembly_oversize_drops_{0};
  std::atomic<std::uint64_t> fragment_assembly_metadata_mismatch_drops_{0};
  std::atomic<std::uint64_t> fragment_assembly_ttl_expirations_{0};
  std::atomic<std::uint64_t> fragment_assembly_ttl_expired_missing_indexes_{0};
  std::atomic<std::uint64_t> test_dropped_frames_{0};
  std::atomic<std::uint64_t> adaptive_failovers_{0};
  std::atomic<std::uint64_t> adaptive_unicast_frames_{0};
  std::atomic<std::uint64_t> adaptive_redundant_frames_{0};
  std::atomic<std::uint64_t> fleet_plan_frames_{0};
  std::atomic<std::uint64_t> fleet_plan_redundant_frames_{0};
  std::atomic<std::uint64_t> fleet_plan_selected_path_count_{0};
  std::atomic<std::uint64_t> repair_plan_frames_{0};
  std::atomic<std::uint64_t> repair_plan_redundant_frames_{0};
  std::atomic<std::uint64_t> repair_plan_selected_path_count_{0};
  std::atomic<std::uint64_t> repair_budget_exhausted_{0};
  std::atomic<std::uint64_t> repair_requests_coalesced_{0};
  std::atomic<std::uint64_t> repair_sequence_attempt_limit_exhausted_{0};
  std::atomic<std::uint64_t> repair_not_admitted_{0};
  std::atomic<size_t> adaptive_selected_peer_index_{0};
  std::int64_t adaptive_redundant_deadline_ns_{50000000ll};
  bool ready_{false};
  std::string init_error_;
  std::string bound_endpoint_;
  std::string transport_mode_{"udp"};
  rmw_fleetqox_cpp::QuicGatewayTransport quic_gateway_transport_;
  rmw_fleetqox_cpp::SharedMemoryTransport shared_memory_transport_;
  std::mutex fragment_mutex_;
  std::unordered_map<std::string, FragmentAssembly> fragment_assemblies_;
  std::unordered_map<std::string, std::int64_t> completed_fragment_assemblies_;
  size_t fragment_nack_sweep_cursor_{0};
  size_t fragment_nack_sweep_window_budget_{kFleetFragmentRepairIndexesPerSweep};
  std::int64_t fragment_nack_sweep_window_start_ns_{0};
  std::mutex fragment_history_mutex_;
  std::unordered_map<std::string, FragmentRepairHistory> fragment_history_;
  std::mutex fragment_send_queue_mutex_;
  std::condition_variable fragment_send_queue_cv_;
  std::unordered_map<std::string, std::deque<PendingFragmentSend>>
    fragment_initial_send_queues_;
  std::deque<std::string> fragment_initial_send_order_;
  size_t fragment_initial_send_queue_size_{0};
  std::unordered_map<std::string, std::deque<PendingFragmentSend>>
    fragment_repair_send_queues_;
  std::deque<std::string> fragment_repair_send_order_;
  size_t fragment_repair_send_queue_size_{0};
  std::unordered_set<std::string> fragment_repair_pending_keys_;
  std::unordered_map<std::string, std::int64_t>
    fragment_repair_recent_send_ns_;
  std::mutex udp_send_mutex_;
  std::chrono::steady_clock::time_point next_udp_send_time_{};
  int udp_socket_buffer_bytes_{0};
  int udp_send_pacing_us_{0};
  int udp_datagram_budget_bytes_{0};
  int loss_resilient_fragment_chunk_bytes_{0};
  int fragment_nack_interval_ms_{50};
  int fragment_nack_max_requests_{6};
  int fragment_nack_max_indexes_per_request_{8};
  int fragment_tail_guard_ms_{1000};
  int fragment_history_limit_{1024};
  int fragment_assembly_limit_{1024};
  int fragment_max_assembly_bytes_{16 * 1024 * 1024};
  int fragment_assembly_ttl_ms_{60000};
  int fragment_send_queue_limit_{32768};
  int fragment_queue_admission_threshold_{0};
  int fragment_queue_admission_timeout_ms_{0};
  int fragment_repair_queue_limit_{64};
  int fragment_repair_cooldown_ms_{100};
  bool fragment_async_send_enabled_{false};
  std::string peer_policy_{"all"};
  std::vector<sockaddr_in> peer_addresses_;
  std::vector<std::string> peer_path_ids_;
  mutable std::vector<FleetPathPlanRule> fleet_path_plan_;
  mutable std::string fleet_path_plan_file_;
  mutable std::string fleet_path_plan_file_contents_;
  std::vector<std::uint64_t> drop_source_sequences_;
  std::vector<std::uint64_t> drop_fragment_indexes_;
  int drop_source_sequence_send_count_{1};
  int proactive_data_repeats_{0};
  int proactive_data_repeat_interval_ms_{5};
  std::mutex test_drop_mutex_;
  std::unordered_map<std::string, std::uint64_t> dropped_source_sequence_counts_;
  std::unordered_set<std::string> dropped_fragment_keys_;
  mutable std::mutex adaptive_mutex_;
  std::string last_adaptive_nack_key_;
  std::vector<std::uint64_t> adaptive_peer_scores_;
  mutable std::mutex fleet_plan_mutex_;
  std::string fleet_plan_last_paths_;
  mutable std::vector<FleetRepairPlanRule> repair_path_plan_;
  mutable std::string repair_path_plan_file_;
  mutable std::string repair_path_plan_file_contents_;
  mutable std::mutex repair_plan_mutex_;
  std::string repair_plan_last_paths_;
  int repair_retransmission_budget_{-1};
  int repair_min_interval_ms_{0};
  int repair_max_attempts_per_sequence_{0};
  bool repair_admission_strict_{false};
  std::mutex repair_attempt_mutex_;
  std::unordered_map<std::string, RepairAttemptState> repair_attempts_;
};

LoopbackSocketTransport & socket_transport()
{
  // Deliberately heap-allocated and never destroyed (classic "leak on
  // purpose" singleton). A plain function-local static is destroyed by the
  // C++ runtime during process exit in an order that is not coordinated
  // across shared libraries: rclcpp's global Context (in librclcpp.so) can
  // outlive this singleton and, on its own later teardown, call back into
  // rmw_context_fini() -> shutdown_pubsub_runtime() -> socket_transport(),
  // touching an already-destructed object (observed as a heap-use-after-free
  // on SharedMemoryTransport::Impl::running under AddressSanitizer, and as
  // the "assertion failed: e != ESRCH || !robust" glibc abort in the
  // corrupted-heap case without ASan). Leaking this process-lifetime
  // singleton avoids the cross-library static destruction order entirely;
  // explicit teardown still happens exactly once via ::shutdown().
  static LoopbackSocketTransport * transport = new LoopbackSocketTransport();
  return *transport;
}

void record_fragment_repair_observation(const std::string & encoded_frame)
{
  const auto frame = rmw_fleetqox_cpp::decode_data_frame(encoded_frame);
  if (!frame.has_value()) {
    return;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  const auto found = g_retransmit_ledger.find(
    retransmit_ledger_key(
      frame->publisher_id,
      frame->source_sequence_number));
  if (found != g_retransmit_ledger.end()) {
    found->second.fragment_observed_by_reader = true;
  }
}

void record_fragment_async_send_started(const std::string & encoded_frame)
{
  const auto frame = rmw_fleetqox_cpp::decode_data_frame(encoded_frame);
  if (!frame.has_value()) {
    return;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  const auto found = g_retransmit_ledger.find(
    retransmit_ledger_key(
      frame->publisher_id,
      frame->source_sequence_number));
  if (found == g_retransmit_ledger.end() || !found->second.reliable) {
    return;
  }
  ++found->second.fragment_initial_send_batches_pending;
  found->second.fragment_initial_pending_suppression_recorded = false;
  found->second.fragment_fallback_grace_deferral_recorded = false;
}

void record_fragment_async_send_terminal(
  const std::string & encoded_frame,
  bool completed)
{
  const auto frame = rmw_fleetqox_cpp::decode_data_frame(encoded_frame);
  if (!frame.has_value()) {
    return;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  const auto found = g_retransmit_ledger.find(
    retransmit_ledger_key(
      frame->publisher_id,
      frame->source_sequence_number));
  if (found == g_retransmit_ledger.end()) {
    return;
  }
  ReliableRetransmitEntry & entry = found->second;
  if (entry.fragment_initial_send_batches_pending == 0) {
    return;
  }
  --entry.fragment_initial_send_batches_pending;
  if (entry.fragment_initial_send_batches_pending == 0) {
    entry.fragment_initial_pending_suppression_recorded = false;
    entry.fragment_fallback_grace_deferral_recorded = false;
  }
  if (completed && entry.fragment_initial_send_batches_pending == 0) {
    entry.last_send_ns = monotonic_timestamp_ns();
    g_fragment_async_send_completions.fetch_add(
      1, std::memory_order_relaxed);
  }
}

void record_fragment_async_send_complete(const std::string & encoded_frame)
{
  record_fragment_async_send_terminal(encoded_frame, true);
}

void record_fragment_async_send_failed(const std::string & encoded_frame)
{
  record_fragment_async_send_terminal(encoded_frame, false);
}

bool handle_ack_nack_feedback(const std::string & encoded_frame)
{
  const auto ack_nack = rmw_fleetqox_cpp::decode_ack_nack(encoded_frame);
  if (!ack_nack) {
    return false;
  }
  socket_transport().record_ack_nack_received();
  socket_transport().record_ack_nack_feedback(*ack_nack);

  std::vector<std::pair<std::uint64_t, std::string>> retransmit_frames;
  std::optional<rmw_fleetqox_cpp::UnrecoverableLossNotice> loss_notice;
  bool acknowledgment_changed = false;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    bool local_reliable_publisher = false;
    for (const FleetQoxPublisherData * publisher : g_publishers) {
      if (publisher != nullptr && publisher->publisher_id == ack_nack->publisher_id &&
        publisher->domain_id == ack_nack->domain_id &&
        publisher->topic_name == ack_nack->topic &&
        publisher->qos.reliability == RMW_QOS_POLICY_RELIABILITY_RELIABLE)
      {
        local_reliable_publisher = true;
        break;
      }
    }

    std::vector<std::pair<std::uint64_t, std::string>> available_history;
    for (const auto & item : g_retransmit_ledger) {
      const ReliableRetransmitEntry & entry = item.second;
      if (entry.reliable && entry.publisher_id == ack_nack->publisher_id &&
        entry.domain_id == ack_nack->domain_id)
      {
        available_history.emplace_back(entry.source_sequence_number, entry.encoded_frame);
      }
    }
    std::sort(available_history.begin(), available_history.end());
    std::unordered_set<std::uint64_t> retransmit_sequences;
    std::vector<std::pair<std::uint64_t, std::uint64_t>> unavailable_ranges;
    for (const auto & range : ack_nack->missing_sequence_ranges) {
      if (range.first == 0 || range.first > range.second) {
        continue;
      }
      std::uint64_t cursor = range.first;
      bool complete = false;
      for (const auto & available : available_history) {
        if (available.first < cursor) {
          continue;
        }
        if (available.first > range.second) {
          break;
        }
        if (available.first > cursor) {
          unavailable_ranges.emplace_back(cursor, available.first - 1);
        }
        if (retransmit_sequences.insert(available.first).second) {
          retransmit_frames.push_back(available);
        }
        if (available.first == std::numeric_limits<std::uint64_t>::max()) {
          complete = true;
          break;
        }
        cursor = available.first + 1;
      }
      if (!complete && cursor <= range.second) {
        unavailable_ranges.emplace_back(cursor, range.second);
      }
    }
    if (local_reliable_publisher && !ack_nack->subscriber_id.empty()) {
      loss_notice = rmw_fleetqox_cpp::UnrecoverableLossNotice{
        ack_nack->robot_id,
        ack_nack->topic,
        ack_nack->publisher_id,
        ack_nack->subscriber_id,
        monotonic_timestamp_ns(),
        unavailable_ranges,
        ack_nack->domain_id};
    }
    for (auto & entry : g_retransmit_ledger) {
      ReliableRetransmitEntry & state = entry.second;
      if (!state.reliable || state.publisher_id != ack_nack->publisher_id ||
        state.domain_id != ack_nack->domain_id)
      {
        continue;
      }
      const bool sequence_acknowledged =
        rmw_fleetqox_cpp::ack_nack_acknowledges_sequence(
        *ack_nack, state.source_sequence_number);
      if (sequence_acknowledged)
      {
        const size_t pending_before = state.pending_subscriber_ids.size();
        if (!ack_nack->subscriber_id.empty()) {
          state.acknowledgments_observed +=
            state.pending_subscriber_ids.erase(ack_nack->subscriber_id);
        } else if (state.pending_subscriber_ids.size() == 1) {
          // Backward compatibility for a single pre-subscriber-id ACK source.
          state.pending_subscriber_ids.clear();
          ++state.acknowledgments_observed;
        }
        state.acknowledged = state.pending_subscriber_ids.empty();
        acknowledgment_changed = acknowledgment_changed ||
          state.pending_subscriber_ids.size() != pending_before;
      }
    }
  }
  if (acknowledgment_changed) {
    g_all_acked_condition.notify_all();
  }
  for (const auto & frame : retransmit_frames) {
    const rmw_ret_t ret = socket_transport().send_retransmission_frame(frame.second);
    if (ret == RMW_RET_UNSUPPORTED && loss_notice.has_value()) {
      loss_notice->lost_sequence_ranges.emplace_back(frame.first, frame.first);
    }
  }
  if (loss_notice.has_value() && !loss_notice->lost_sequence_ranges.empty()) {
    const rmw_ret_t ret = socket_transport().send_unrecoverable_loss_notice(
      rmw_fleetqox_cpp::encode_unrecoverable_loss_notice(*loss_notice));
    (void)ret;
  }
  return true;
}

std::string endpoint_id_for_local_id(const std::string & local_id)
{
  return socket_transport().bound_endpoint() + "|" + local_id;
}

std::string retransmit_ledger_key(const std::string & publisher_id, std::uint64_t sequence)
{
  return publisher_id + "|" + std::to_string(sequence);
}

bool identifier_matches(const char * identifier)
{
  return identifier != nullptr && std::strcmp(identifier, kIdentifier) == 0;
}

rmw_ret_t require_identifier(const char * identifier)
{
  if (!identifier_matches(identifier)) {
    RMW_SET_ERROR_MSG("rmw_fleetqox_cpp implementation identifier mismatch");
    return RMW_RET_INCORRECT_RMW_IMPLEMENTATION;
  }
  return RMW_RET_OK;
}

bool context_is_valid(const rmw_context_t * context)
{
  return context != nullptr &&
         identifier_matches(context->implementation_identifier) &&
         context->impl != nullptr &&
         !context->impl->is_shutdown;
}

bool node_is_valid(const rmw_node_t * node)
{
  return node != nullptr &&
         identifier_matches(node->implementation_identifier) &&
         context_is_valid(node->context);
}

bool topic_is_valid(const char * topic_name)
{
  return topic_name != nullptr && topic_name[0] == '/';
}

bool trace_take_enabled()
{
  const char * value = std::getenv("FLEETQOX_RMW_TRACE_TAKE");
  return value != nullptr && value[0] != '\0' && std::strcmp(value, "0") != 0;
}

bool env_flag_enabled(const char * name)
{
  const char * value = std::getenv(name);
  return value != nullptr && value[0] != '\0' &&
         std::strcmp(value, "0") != 0 &&
         std::strcmp(value, "false") != 0 &&
         std::strcmp(value, "off") != 0 &&
         std::strcmp(value, "no") != 0;
}

bool quic_gateway_take_on_demand_enabled()
{
  return env_flag_enabled("FLEETQOX_RMW_QUIC_GATEWAY_TAKE_ON_DEMAND");
}

std::int64_t monotonic_timestamp_ns()
{
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
}

const std::string & local_robot_id()
{
  static const std::string robot_id = []() {
      const char * configured = std::getenv("FLEETQOX_RMW_ROBOT_ID");
      return configured != nullptr && configured[0] != '\0' ?
             std::string(configured) : std::string("local");
    }();
  return robot_id;
}

std::int64_t qos_duration_ns(const rmw_time_t & duration)
{
  if (duration.sec == 0 && duration.nsec == 0) {
    return 0;
  }
  constexpr std::uint64_t kNanosecondsPerSecond = 1000000000ull;
  if (duration.sec > static_cast<std::uint64_t>(
      std::numeric_limits<std::int64_t>::max() / static_cast<std::int64_t>(kNanosecondsPerSecond)))
  {
    return std::numeric_limits<std::int64_t>::max();
  }
  const auto sec_ns = static_cast<std::int64_t>(duration.sec * kNanosecondsPerSecond);
  const auto nsec = static_cast<std::int64_t>(duration.nsec);
  if (std::numeric_limits<std::int64_t>::max() - sec_ns < nsec) {
    return std::numeric_limits<std::int64_t>::max();
  }
  return sec_ns + nsec;
}

bool frame_exceeds_lifespan(const rmw_qos_profile_t & qos, std::int64_t source_timestamp_ns)
{
  const std::int64_t lifespan_ns = qos_duration_ns(qos.lifespan);
  if (lifespan_ns <= 0 || source_timestamp_ns <= 0) {
    return false;
  }
  const std::int64_t now = monotonic_timestamp_ns();
  return now > source_timestamp_ns && now - source_timestamp_ns > lifespan_ns;
}

void queue_event_callback_locked(
  std::vector<EventCallbackNotification> * callbacks,
  rmw_event_callback_t callback,
  const void * user_data,
  size_t event_count,
  FleetQoxPublisherData * publisher_owner = nullptr,
  FleetQoxSubscriptionData * subscription_owner = nullptr)
{
  if (callbacks == nullptr || callback == nullptr || event_count == 0 ||
    (publisher_owner != nullptr && publisher_owner->destroying) ||
    (subscription_owner != nullptr && subscription_owner->destroying))
  {
    return;
  }
  if (publisher_owner != nullptr) {
    ++publisher_owner->inflight_callbacks;
  }
  if (subscription_owner != nullptr) {
    ++subscription_owner->inflight_callbacks;
  }
  callbacks->push_back(EventCallbackNotification{
    callback,
    user_data,
    event_count,
    publisher_owner,
    subscription_owner});
}

std::int32_t missed_deadline_periods(
  const rmw_qos_profile_t & qos,
  std::int64_t previous_ns,
  std::int64_t now_ns)
{
  const std::int64_t deadline_ns = qos_duration_ns(qos.deadline);
  if (deadline_ns <= 0 || previous_ns <= 0 || now_ns <= previous_ns) {
    return 0;
  }
  const std::int64_t elapsed_ns = now_ns - previous_ns;
  if (elapsed_ns <= deadline_ns) {
    return 0;
  }
  const std::int64_t missed = elapsed_ns / deadline_ns;
  return static_cast<std::int32_t>(
    std::min<std::int64_t>(missed, std::numeric_limits<std::int32_t>::max()));
}

std::int32_t record_offered_deadline_miss_locked(
  FleetQoxPublisherData * data,
  std::int64_t now_ns,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (data == nullptr || callbacks == nullptr) {
    return 0;
  }
  const std::int32_t missed = missed_deadline_periods(data->qos, data->last_publish_ns, now_ns);
  if (missed <= 0) {
    return 0;
  }
  data->offered_deadline_total_count =
    std::min<std::int64_t>(
      static_cast<std::int64_t>(data->offered_deadline_total_count) + missed,
      std::numeric_limits<std::int32_t>::max());
  data->offered_deadline_unread_count =
    std::min<std::int64_t>(
      static_cast<std::int64_t>(data->offered_deadline_unread_count) + missed,
      std::numeric_limits<std::int32_t>::max());
  if (data->offered_deadline_callback != nullptr) {
    queue_event_callback_locked(
      callbacks,
      data->offered_deadline_callback,
      data->offered_deadline_user_data,
      static_cast<size_t>(missed),
      data);
  }
  return missed;
}

std::int32_t record_requested_deadline_miss_locked(
  FleetQoxSubscriptionData * data,
  std::int64_t now_ns,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (data == nullptr || callbacks == nullptr) {
    return 0;
  }
  const std::int32_t missed = missed_deadline_periods(data->qos, data->last_received_ns, now_ns);
  if (missed <= 0) {
    return 0;
  }
  data->requested_deadline_total_count =
    std::min<std::int64_t>(
      static_cast<std::int64_t>(data->requested_deadline_total_count) + missed,
      std::numeric_limits<std::int32_t>::max());
  data->requested_deadline_unread_count =
    std::min<std::int64_t>(
      static_cast<std::int64_t>(data->requested_deadline_unread_count) + missed,
      std::numeric_limits<std::int32_t>::max());
  if (data->requested_deadline_callback != nullptr) {
    queue_event_callback_locked(
      callbacks,
      data->requested_deadline_callback,
      data->requested_deadline_user_data,
      static_cast<size_t>(missed),
      nullptr,
      data);
  }
  return missed;
}

void advance_deadline_anchor_after_miss(
  std::int64_t * anchor_ns,
  const rmw_qos_profile_t & qos,
  std::int32_t missed,
  std::int64_t now_ns)
{
  if (anchor_ns == nullptr || *anchor_ns <= 0 || missed <= 0) {
    return;
  }
  const std::int64_t deadline_ns = qos_duration_ns(qos.deadline);
  if (deadline_ns <= 0) {
    return;
  }
  const std::int64_t max_multiplier = std::numeric_limits<std::int64_t>::max() / deadline_ns;
  const std::int64_t periods =
    std::min<std::int64_t>(static_cast<std::int64_t>(missed), max_multiplier);
  if (periods <= 0) {
    return;
  }
  const std::int64_t advanced = *anchor_ns + periods * deadline_ns;
  *anchor_ns = std::min(advanced, now_ns);
}

std::int32_t saturating_i32_add(std::int32_t value, std::int64_t delta)
{
  const std::int64_t next = static_cast<std::int64_t>(value) + delta;
  return static_cast<std::int32_t>(
    std::max<std::int64_t>(
      std::numeric_limits<std::int32_t>::min(),
      std::min<std::int64_t>(next, std::numeric_limits<std::int32_t>::max())));
}

size_t saturating_size_add(size_t value, size_t delta)
{
  return std::numeric_limits<size_t>::max() - value < delta ?
         std::numeric_limits<size_t>::max() :
         value + delta;
}

bool reliability_qos_incompatible(
  const rmw_qos_profile_t & offered,
  const rmw_qos_profile_t & requested)
{
  return offered.reliability == RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT &&
         requested.reliability == RMW_QOS_POLICY_RELIABILITY_RELIABLE;
}

bool durability_qos_incompatible(
  const rmw_qos_profile_t & offered,
  const rmw_qos_profile_t & requested)
{
  return offered.durability == RMW_QOS_POLICY_DURABILITY_VOLATILE &&
         requested.durability == RMW_QOS_POLICY_DURABILITY_TRANSIENT_LOCAL;
}

bool deadline_qos_incompatible(
  const rmw_qos_profile_t & offered,
  const rmw_qos_profile_t & requested)
{
  const std::int64_t offered_ns = qos_duration_ns(offered.deadline);
  const std::int64_t requested_ns = qos_duration_ns(requested.deadline);
  return requested_ns > 0 && (offered_ns <= 0 || offered_ns > requested_ns);
}

bool liveliness_qos_incompatible(
  const rmw_qos_profile_t & offered,
  const rmw_qos_profile_t & requested)
{
  const bool kind_incompatible =
    offered.liveliness == RMW_QOS_POLICY_LIVELINESS_AUTOMATIC &&
    requested.liveliness == RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC;
  const std::int64_t offered_lease_ns =
    qos_duration_ns(offered.liveliness_lease_duration);
  const std::int64_t requested_lease_ns =
    qos_duration_ns(requested.liveliness_lease_duration);
  const bool lease_incompatible = requested_lease_ns > 0 &&
    (offered_lease_ns <= 0 || offered_lease_ns > requested_lease_ns);
  return kind_incompatible || lease_incompatible;
}

rmw_qos_policy_kind_t incompatible_qos_policy_kind(
  const rmw_qos_profile_t & offered,
  const rmw_qos_profile_t & requested)
{
  if (reliability_qos_incompatible(offered, requested)) {
    return RMW_QOS_POLICY_RELIABILITY;
  }
  if (durability_qos_incompatible(offered, requested)) {
    return RMW_QOS_POLICY_DURABILITY;
  }
  if (deadline_qos_incompatible(offered, requested)) {
    return RMW_QOS_POLICY_DEADLINE;
  }
  if (liveliness_qos_incompatible(offered, requested)) {
    return RMW_QOS_POLICY_LIVELINESS;
  }
  return RMW_QOS_POLICY_INVALID;
}

bool local_pubsub_match_compatible(
  const FleetQoxPublisherData * publisher,
  const FleetQoxSubscriptionData * subscription)
{
  return publisher != nullptr &&
         subscription != nullptr &&
         publisher->domain_id == subscription->domain_id &&
         publisher->topic_name == subscription->topic_name &&
         publisher->type_name == subscription->type_name &&
         incompatible_qos_policy_kind(publisher->qos, subscription->qos) ==
         RMW_QOS_POLICY_INVALID;
}

std::string remote_pubsub_endpoint_key(
  bool publisher,
  const std::string & endpoint_id,
  std::uint64_t domain_id)
{
  return std::to_string(domain_id) + "|" +
         std::string(publisher ? "publisher|" : "subscription|") + endpoint_id;
}

std::string remote_liveliness_publisher_id(const RemotePubSubEndpoint & endpoint)
{
  return "remote-endpoint:" + std::to_string(endpoint.domain_id) + ":" + endpoint.endpoint_id;
}

bool qos_profiles_equal(
  const rmw_qos_profile_t & left,
  const rmw_qos_profile_t & right)
{
  return left.history == right.history &&
         left.depth == right.depth &&
         left.reliability == right.reliability &&
         left.durability == right.durability &&
         left.deadline.sec == right.deadline.sec &&
         left.deadline.nsec == right.deadline.nsec &&
         left.lifespan.sec == right.lifespan.sec &&
         left.lifespan.nsec == right.lifespan.nsec &&
         left.liveliness == right.liveliness &&
         left.liveliness_lease_duration.sec == right.liveliness_lease_duration.sec &&
         left.liveliness_lease_duration.nsec == right.liveliness_lease_duration.nsec &&
         left.avoid_ros_namespace_conventions == right.avoid_ros_namespace_conventions;
}

bool remote_endpoint_descriptor_equal(
  const RemotePubSubEndpoint & left,
  const RemotePubSubEndpoint & right)
{
  return left.publisher == right.publisher &&
         left.domain_id == right.domain_id &&
         left.topic_name == right.topic_name &&
         left.type_name == right.type_name &&
         left.endpoint_id == right.endpoint_id &&
         qos_profiles_equal(left.qos, right.qos);
}

bool remote_subscription_match_compatible(
  const FleetQoxPublisherData * publisher,
  const RemotePubSubEndpoint & subscription)
{
  return publisher != nullptr &&
         !subscription.publisher &&
         publisher->domain_id == subscription.domain_id &&
         publisher->topic_name == subscription.topic_name &&
         publisher->type_name == subscription.type_name &&
         incompatible_qos_policy_kind(publisher->qos, subscription.qos) ==
         RMW_QOS_POLICY_INVALID;
}

bool remote_publisher_match_compatible(
  const RemotePubSubEndpoint & publisher,
  const FleetQoxSubscriptionData * subscription)
{
  return subscription != nullptr &&
         publisher.publisher &&
         publisher.domain_id == subscription->domain_id &&
         publisher.topic_name == subscription->topic_name &&
         publisher.type_name == subscription->type_name &&
         incompatible_qos_policy_kind(publisher.qos, subscription->qos) ==
         RMW_QOS_POLICY_INVALID;
}

bool local_endpoint_id_exists_locked(bool publisher, const std::string & endpoint_id)
{
  if (publisher) {
    return std::any_of(
      g_publishers.begin(), g_publishers.end(), [&](const FleetQoxPublisherData * endpoint) {
        return endpoint != nullptr && endpoint->endpoint_id == endpoint_id;
      });
  }
  return std::any_of(
    g_subscriptions.begin(), g_subscriptions.end(),
    [&](const FleetQoxSubscriptionData * endpoint) {
      return endpoint != nullptr && endpoint->endpoint_id == endpoint_id;
    });
}

size_t local_matched_subscription_count_locked(const FleetQoxPublisherData * publisher)
{
  size_t count = 0;
  if (publisher == nullptr) {
    return count;
  }
  for (const FleetQoxSubscriptionData * subscription : g_subscriptions) {
    if (local_pubsub_match_compatible(publisher, subscription)) {
      ++count;
    }
  }
  return count;
}

size_t local_matched_publisher_count_locked(const FleetQoxSubscriptionData * subscription)
{
  size_t count = 0;
  if (subscription == nullptr) {
    return count;
  }
  for (const FleetQoxPublisherData * publisher : g_publishers) {
    if (local_pubsub_match_compatible(publisher, subscription)) {
      ++count;
    }
  }
  return count;
}

size_t matched_subscription_count_locked(const FleetQoxPublisherData * publisher)
{
  size_t count = local_matched_subscription_count_locked(publisher);
  if (publisher == nullptr) {
    return count;
  }
  for (const auto & item : g_remote_pubsub_endpoints) {
    if (remote_subscription_match_compatible(publisher, item.second)) {
      ++count;
    }
  }
  return count;
}

size_t matched_publisher_count_locked(const FleetQoxSubscriptionData * subscription)
{
  size_t count = local_matched_publisher_count_locked(subscription);
  if (subscription == nullptr) {
    return count;
  }
  for (const auto & item : g_remote_pubsub_endpoints) {
    if (remote_publisher_match_compatible(item.second, subscription)) {
      ++count;
    }
  }
  return count;
}

size_t matched_pending_count(size_t total_count_change, std::int32_t current_count_change)
{
  if (current_count_change < 0) {
    return static_cast<size_t>(-static_cast<std::int64_t>(current_count_change));
  }
  if (current_count_change > 0) {
    return static_cast<size_t>(current_count_change);
  }
  return total_count_change;
}

void record_publication_matched_change_locked(
  FleetQoxPublisherData * data,
  size_t current_count,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (data == nullptr || callbacks == nullptr ||
    current_count == data->publication_matched_current_count)
  {
    return;
  }
  const std::int64_t delta =
    current_count > data->publication_matched_current_count ?
    static_cast<std::int64_t>(current_count - data->publication_matched_current_count) :
    -static_cast<std::int64_t>(data->publication_matched_current_count - current_count);
  if (delta > 0) {
    const size_t positive_delta = static_cast<size_t>(delta);
    data->publication_matched_total_count =
      std::numeric_limits<size_t>::max() - data->publication_matched_total_count < positive_delta ?
      std::numeric_limits<size_t>::max() :
      data->publication_matched_total_count + positive_delta;
    data->publication_matched_total_count_change =
      std::numeric_limits<size_t>::max() -
        data->publication_matched_total_count_change < positive_delta ?
      std::numeric_limits<size_t>::max() :
      data->publication_matched_total_count_change + positive_delta;
  }
  data->publication_matched_current_count = current_count;
  data->publication_matched_current_count_change =
    saturating_i32_add(data->publication_matched_current_count_change, delta);
  const size_t pending = matched_pending_count(
    data->publication_matched_total_count_change,
    data->publication_matched_current_count_change);
  if (data->publication_matched_callback != nullptr && pending > 0) {
    queue_event_callback_locked(
      callbacks,
      data->publication_matched_callback,
      data->publication_matched_user_data,
      pending,
      data);
  }
}

void record_subscription_matched_change_locked(
  FleetQoxSubscriptionData * data,
  size_t current_count,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (data == nullptr || callbacks == nullptr ||
    current_count == data->subscription_matched_current_count)
  {
    return;
  }
  const std::int64_t delta =
    current_count > data->subscription_matched_current_count ?
    static_cast<std::int64_t>(current_count - data->subscription_matched_current_count) :
    -static_cast<std::int64_t>(data->subscription_matched_current_count - current_count);
  if (delta > 0) {
    const size_t positive_delta = static_cast<size_t>(delta);
    data->subscription_matched_total_count =
      std::numeric_limits<size_t>::max() - data->subscription_matched_total_count < positive_delta ?
      std::numeric_limits<size_t>::max() :
      data->subscription_matched_total_count + positive_delta;
    data->subscription_matched_total_count_change =
      std::numeric_limits<size_t>::max() -
        data->subscription_matched_total_count_change < positive_delta ?
      std::numeric_limits<size_t>::max() :
      data->subscription_matched_total_count_change + positive_delta;
  }
  data->subscription_matched_current_count = current_count;
  data->subscription_matched_current_count_change =
    saturating_i32_add(data->subscription_matched_current_count_change, delta);
  const size_t pending = matched_pending_count(
    data->subscription_matched_total_count_change,
    data->subscription_matched_current_count_change);
  if (data->subscription_matched_callback != nullptr && pending > 0) {
    queue_event_callback_locked(
      callbacks,
      data->subscription_matched_callback,
      data->subscription_matched_user_data,
      pending,
      nullptr,
      data);
  }
}

void refresh_publication_matched_events_locked(
  std::vector<EventCallbackNotification> * callbacks)
{
  for (FleetQoxPublisherData * publisher : g_publishers) {
    if (publisher == nullptr) {
      continue;
    }
    record_publication_matched_change_locked(
      publisher,
      matched_subscription_count_locked(publisher),
      callbacks);
  }
}

void refresh_subscription_matched_events_locked(
  std::vector<EventCallbackNotification> * callbacks)
{
  for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
    if (subscription == nullptr) {
      continue;
    }
    record_subscription_matched_change_locked(
      subscription,
      matched_publisher_count_locked(subscription),
      callbacks);
  }
}

void notify_event_callbacks(const std::vector<EventCallbackNotification> & callbacks)
{
  for (const EventCallbackNotification & notification : callbacks) {
    notification.callback(notification.user_data, notification.event_count);
    {
      std::lock_guard<std::mutex> lock(g_bus_mutex);
      if (notification.publisher_owner != nullptr &&
        notification.publisher_owner->inflight_callbacks > 0)
      {
        --notification.publisher_owner->inflight_callbacks;
      }
      if (notification.subscription_owner != nullptr &&
        notification.subscription_owner->inflight_callbacks > 0)
      {
        --notification.subscription_owner->inflight_callbacks;
      }
    }
    g_entity_callback_condition.notify_all();
  }
}

void record_offered_qos_incompatible_locked(
  FleetQoxPublisherData * data,
  rmw_qos_policy_kind_t policy_kind,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (data == nullptr || callbacks == nullptr || policy_kind == RMW_QOS_POLICY_INVALID) {
    return;
  }
  data->offered_incompatible_qos_total_count =
    saturating_i32_add(data->offered_incompatible_qos_total_count, 1);
  data->offered_incompatible_qos_total_count_change =
    saturating_i32_add(data->offered_incompatible_qos_total_count_change, 1);
  data->offered_incompatible_qos_last_policy_kind = policy_kind;
  if (data->offered_incompatible_qos_callback != nullptr) {
    queue_event_callback_locked(
      callbacks,
      data->offered_incompatible_qos_callback,
      data->offered_incompatible_qos_user_data,
      static_cast<size_t>(data->offered_incompatible_qos_total_count_change),
      data);
  }
}

void record_requested_qos_incompatible_locked(
  FleetQoxSubscriptionData * data,
  rmw_qos_policy_kind_t policy_kind,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (data == nullptr || callbacks == nullptr || policy_kind == RMW_QOS_POLICY_INVALID) {
    return;
  }
  data->requested_incompatible_qos_total_count =
    saturating_i32_add(data->requested_incompatible_qos_total_count, 1);
  data->requested_incompatible_qos_total_count_change =
    saturating_i32_add(data->requested_incompatible_qos_total_count_change, 1);
  data->requested_incompatible_qos_last_policy_kind = policy_kind;
  if (data->requested_incompatible_qos_callback != nullptr) {
    queue_event_callback_locked(
      callbacks,
      data->requested_incompatible_qos_callback,
      data->requested_incompatible_qos_user_data,
      static_cast<size_t>(data->requested_incompatible_qos_total_count_change),
      nullptr,
      data);
  }
}

void record_publisher_incompatible_type_locked(
  FleetQoxPublisherData * data,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (data == nullptr || callbacks == nullptr) {
    return;
  }
  data->publisher_incompatible_type_total_count =
    saturating_i32_add(data->publisher_incompatible_type_total_count, 1);
  data->publisher_incompatible_type_total_count_change =
    saturating_i32_add(data->publisher_incompatible_type_total_count_change, 1);
  if (data->publisher_incompatible_type_callback != nullptr) {
    queue_event_callback_locked(
      callbacks,
      data->publisher_incompatible_type_callback,
      data->publisher_incompatible_type_user_data,
      static_cast<size_t>(data->publisher_incompatible_type_total_count_change),
      data);
  }
}

void record_subscription_incompatible_type_locked(
  FleetQoxSubscriptionData * data,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (data == nullptr || callbacks == nullptr) {
    return;
  }
  data->subscription_incompatible_type_total_count =
    saturating_i32_add(data->subscription_incompatible_type_total_count, 1);
  data->subscription_incompatible_type_total_count_change =
    saturating_i32_add(data->subscription_incompatible_type_total_count_change, 1);
  if (data->subscription_incompatible_type_callback != nullptr) {
    queue_event_callback_locked(
      callbacks,
      data->subscription_incompatible_type_callback,
      data->subscription_incompatible_type_user_data,
      static_cast<size_t>(data->subscription_incompatible_type_total_count_change),
      nullptr,
      data);
  }
}

void record_subscription_message_lost_locked(
  FleetQoxSubscriptionData * data,
  size_t count,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (data == nullptr || callbacks == nullptr || count == 0) {
    return;
  }
  data->message_lost_total_count =
    saturating_size_add(data->message_lost_total_count, count);
  data->message_lost_total_count_change =
    saturating_size_add(data->message_lost_total_count_change, count);
  if (data->message_lost_callback != nullptr) {
    queue_event_callback_locked(
      callbacks,
      data->message_lost_callback,
      data->message_lost_user_data,
      data->message_lost_total_count_change,
      nullptr,
      data);
  }
}

bool handle_unrecoverable_loss_notice(const std::string & encoded_frame)
{
  const auto notice = rmw_fleetqox_cpp::decode_unrecoverable_loss_notice(encoded_frame);
  if (!notice) {
    return false;
  }
  socket_transport().record_unrecoverable_loss_notice_received();
  std::vector<EventCallbackNotification> callbacks;
  size_t reported = 0;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    const rmw_fleetqox_cpp::DataFrame stream_marker{
      notice->robot_id,
      notice->topic,
      notice->publisher_id,
      0,
      notice->source_timestamp_ns,
      {},
      notice->domain_id,
      {}};
    const std::string key = rmw_fleetqox_cpp::stream_key(stream_marker);
    for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
      if (subscription == nullptr || subscription->endpoint_id != notice->subscriber_id ||
        subscription->domain_id != notice->domain_id ||
        subscription->topic_name != notice->topic ||
        subscription->qos.reliability == RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT)
      {
        continue;
      }
      const auto state = subscription->sequence_states.find(key);
      if (state == subscription->sequence_states.end()) {
        continue;
      }
      const size_t subscription_reported = apply_confirmed_lost_ranges_locked(
        &state->second, notice->lost_sequence_ranges);
      reported = saturating_size_add(reported, subscription_reported);
      record_subscription_message_lost_locked(
        subscription, subscription_reported, &callbacks);
    }
  }
  g_unrecoverable_loss_samples_reported.fetch_add(reported, std::memory_order_relaxed);
  notify_event_callbacks(callbacks);
  return true;
}

void record_qos_incompatibilities_for_new_publisher_locked(
  FleetQoxPublisherData * publisher,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (publisher == nullptr || callbacks == nullptr) {
    return;
  }
  for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
    if (subscription == nullptr || subscription->domain_id != publisher->domain_id ||
      subscription->topic_name != publisher->topic_name)
    {
      continue;
    }
    const rmw_qos_policy_kind_t policy_kind =
      incompatible_qos_policy_kind(publisher->qos, subscription->qos);
    if (policy_kind != RMW_QOS_POLICY_INVALID) {
      record_offered_qos_incompatible_locked(publisher, policy_kind, callbacks);
      record_requested_qos_incompatible_locked(subscription, policy_kind, callbacks);
    }
  }
  for (const auto & item : g_remote_pubsub_endpoints) {
    const RemotePubSubEndpoint & subscription = item.second;
    if (subscription.publisher || subscription.domain_id != publisher->domain_id ||
      subscription.topic_name != publisher->topic_name)
    {
      continue;
    }
    const rmw_qos_policy_kind_t policy_kind =
      incompatible_qos_policy_kind(publisher->qos, subscription.qos);
    if (policy_kind != RMW_QOS_POLICY_INVALID) {
      record_offered_qos_incompatible_locked(publisher, policy_kind, callbacks);
    }
  }
}

void record_qos_incompatibilities_for_new_subscription_locked(
  FleetQoxSubscriptionData * subscription,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (subscription == nullptr || callbacks == nullptr) {
    return;
  }
  for (FleetQoxPublisherData * publisher : g_publishers) {
    if (publisher == nullptr || publisher->domain_id != subscription->domain_id ||
      publisher->topic_name != subscription->topic_name)
    {
      continue;
    }
    const rmw_qos_policy_kind_t policy_kind =
      incompatible_qos_policy_kind(publisher->qos, subscription->qos);
    if (policy_kind != RMW_QOS_POLICY_INVALID) {
      record_offered_qos_incompatible_locked(publisher, policy_kind, callbacks);
      record_requested_qos_incompatible_locked(subscription, policy_kind, callbacks);
    }
  }
  for (const auto & item : g_remote_pubsub_endpoints) {
    const RemotePubSubEndpoint & publisher = item.second;
    if (!publisher.publisher || publisher.domain_id != subscription->domain_id ||
      publisher.topic_name != subscription->topic_name)
    {
      continue;
    }
    const rmw_qos_policy_kind_t policy_kind =
      incompatible_qos_policy_kind(publisher.qos, subscription->qos);
    if (policy_kind != RMW_QOS_POLICY_INVALID) {
      record_requested_qos_incompatible_locked(subscription, policy_kind, callbacks);
    }
  }
}

void record_type_incompatibilities_for_new_publisher_locked(
  FleetQoxPublisherData * publisher,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (publisher == nullptr || callbacks == nullptr) {
    return;
  }
  for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
    if (subscription == nullptr ||
      subscription->domain_id != publisher->domain_id ||
      subscription->topic_name != publisher->topic_name ||
      subscription->type_name == publisher->type_name)
    {
      continue;
    }
    record_publisher_incompatible_type_locked(publisher, callbacks);
    record_subscription_incompatible_type_locked(subscription, callbacks);
  }
  for (const auto & item : g_remote_pubsub_endpoints) {
    const RemotePubSubEndpoint & subscription = item.second;
    if (subscription.publisher ||
      subscription.domain_id != publisher->domain_id ||
      subscription.topic_name != publisher->topic_name ||
      subscription.type_name == publisher->type_name)
    {
      continue;
    }
    record_publisher_incompatible_type_locked(publisher, callbacks);
  }
}

void record_type_incompatibilities_for_new_subscription_locked(
  FleetQoxSubscriptionData * subscription,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (subscription == nullptr || callbacks == nullptr) {
    return;
  }
  for (FleetQoxPublisherData * publisher : g_publishers) {
    if (publisher == nullptr ||
      publisher->domain_id != subscription->domain_id ||
      publisher->topic_name != subscription->topic_name ||
      publisher->type_name == subscription->type_name)
    {
      continue;
    }
    record_publisher_incompatible_type_locked(publisher, callbacks);
    record_subscription_incompatible_type_locked(subscription, callbacks);
  }
  for (const auto & item : g_remote_pubsub_endpoints) {
    const RemotePubSubEndpoint & publisher = item.second;
    if (!publisher.publisher ||
      publisher.domain_id != subscription->domain_id ||
      publisher.topic_name != subscription->topic_name ||
      publisher.type_name == subscription->type_name)
    {
      continue;
    }
    record_subscription_incompatible_type_locked(subscription, callbacks);
  }
}

void enforce_subscription_depth_locked(
  FleetQoxSubscriptionData * data,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (data == nullptr ||
    data->qos.history != RMW_QOS_POLICY_HISTORY_KEEP_LAST ||
    data->qos.depth == 0)
  {
    return;
  }
  size_t dropped = 0;
  while (data->frame_queue.size() > data->qos.depth) {
    data->frame_queue.pop_front();
    ++dropped;
  }
  record_subscription_message_lost_locked(data, dropped, callbacks);
}

std::string allocate_publisher_id()
{
  return "fpubcpp-" + socket_transport().bound_endpoint() + "-" +
         std::to_string(g_next_publisher_id.fetch_add(1));
}

std::string allocate_subscription_id()
{
  return "fsubcpp-" + socket_transport().bound_endpoint() + "-" +
         std::to_string(g_next_subscription_id.fetch_add(1));
}

std::string ros_type_name_from_introspection_members(
  const rosidl_typesupport_introspection_c__MessageMembers * members)
{
  if (members == nullptr || members->message_namespace_ == nullptr ||
    members->message_name_ == nullptr)
  {
    return "unknown";
  }
  std::string namespace_text = members->message_namespace_;
  size_t separator = 0;
  while ((separator = namespace_text.find("__", separator)) != std::string::npos) {
    namespace_text.replace(separator, 2, "/");
    separator += 1;
  }
  return namespace_text + "/" + members->message_name_;
}

size_t typed_message_size_from_type_support(const rosidl_message_type_support_t * type_support)
{
  if (type_support == nullptr ||
    type_support->typesupport_identifier == nullptr ||
    std::strcmp(type_support->typesupport_identifier, kTypeErasedTypeSupportIdentifier) != 0 ||
    type_support->data == nullptr)
  {
    return 0;
  }
  const auto * descriptor =
    static_cast<const FleetQoxTypeErasedMessageDescriptor *>(type_support->data);
  if (descriptor->schema_version != kTypeErasedDescriptorSchemaVersion ||
    descriptor->message_size == 0)
  {
    return 0;
  }
  return descriptor->message_size;
}

const rosidl_typesupport_introspection_c__MessageMembers * introspection_c_members(
  const rosidl_message_type_support_t * type_support)
{
  if (type_support == nullptr ||
    type_support->typesupport_identifier == nullptr ||
    std::strcmp(type_support->typesupport_identifier, rosidl_typesupport_introspection_c__identifier) != 0 ||
    type_support->data == nullptr)
  {
    return nullptr;
  }
  return static_cast<const rosidl_typesupport_introspection_c__MessageMembers *>(type_support->data);
}

const rosidl_typesupport_introspection_cpp::MessageMembers * introspection_cpp_members(
  const rosidl_message_type_support_t * type_support)
{
  if (type_support == nullptr ||
    type_support->typesupport_identifier == nullptr ||
    std::strcmp(
      type_support->typesupport_identifier,
      rosidl_typesupport_introspection_cpp::typesupport_identifier) != 0 ||
    type_support->data == nullptr)
  {
    return nullptr;
  }
  return static_cast<const rosidl_typesupport_introspection_cpp::MessageMembers *>(
    type_support->data);
}

const rosidl_message_type_support_t * resolve_effective_type_support(
  const rosidl_message_type_support_t * type_support)
{
  if (type_support == nullptr || type_support->typesupport_identifier == nullptr) {
    return type_support;
  }
  if (std::strcmp(
      type_support->typesupport_identifier,
      rosidl_typesupport_introspection_c__identifier) == 0)
  {
    return type_support;
  }
  if (std::strcmp(
      type_support->typesupport_identifier,
      rosidl_typesupport_c__typesupport_identifier) == 0)
  {
    const rosidl_message_type_support_t * resolved =
      rosidl_typesupport_c__get_message_typesupport_handle_function(
      type_support,
      rosidl_typesupport_introspection_c__identifier);
    if (resolved != nullptr) {
      return resolved;
    }
  }
  if (std::strcmp(
      type_support->typesupport_identifier,
      rosidl_typesupport_cpp::typesupport_identifier) == 0)
  {
    const rosidl_message_type_support_t * resolved =
      rosidl_typesupport_cpp::get_message_typesupport_handle_function(
      type_support,
      rosidl_typesupport_introspection_cpp::typesupport_identifier);
    if (resolved != nullptr) {
      return resolved;
    }
  }
  if (type_support->func != nullptr) {
    const rosidl_message_type_support_t * resolved =
      type_support->func(type_support, rosidl_typesupport_introspection_c__identifier);
    if (resolved != nullptr) {
      return resolved;
    }
  }
  return type_support;
}

std::string type_name_from_type_support(const rosidl_message_type_support_t * type_support)
{
  const auto * effective = resolve_effective_type_support(type_support);
  const auto * introspection_members = introspection_c_members(effective);
  if (introspection_members != nullptr) {
    return ros_type_name_from_introspection_members(introspection_members);
  }
  const auto * cpp_members = introspection_cpp_members(effective);
  if (cpp_members != nullptr && cpp_members->message_namespace_ != nullptr &&
    cpp_members->message_name_ != nullptr)
  {
    std::string namespace_text = cpp_members->message_namespace_;
    size_t separator = 0;
    while ((separator = namespace_text.find("::", separator)) != std::string::npos) {
      namespace_text.replace(separator, 2, "/");
      separator += 1;
    }
    return namespace_text + "/" + cpp_members->message_name_;
  }
  return type_support != nullptr && type_support->typesupport_identifier != nullptr ?
         type_support->typesupport_identifier : "unknown";
}

size_t primitive_size(uint8_t type_id)
{
  switch (type_id) {
    case rosidl_typesupport_introspection_c__ROS_TYPE_FLOAT:
      return sizeof(float);
    case rosidl_typesupport_introspection_c__ROS_TYPE_DOUBLE:
      return sizeof(double);
    case rosidl_typesupport_introspection_c__ROS_TYPE_LONG_DOUBLE:
      return sizeof(long double);
    case rosidl_typesupport_introspection_c__ROS_TYPE_CHAR:
      return sizeof(char);
    case rosidl_typesupport_introspection_c__ROS_TYPE_WCHAR:
      return sizeof(char16_t);
    case rosidl_typesupport_introspection_c__ROS_TYPE_BOOLEAN:
      return sizeof(bool);
    case rosidl_typesupport_introspection_c__ROS_TYPE_OCTET:
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT8:
      return sizeof(std::uint8_t);
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT8:
      return sizeof(std::int8_t);
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT16:
      return sizeof(std::uint16_t);
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT16:
      return sizeof(std::int16_t);
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT32:
      return sizeof(std::uint32_t);
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT32:
      return sizeof(std::int32_t);
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT64:
      return sizeof(std::uint64_t);
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT64:
      return sizeof(std::int64_t);
    default:
      return 0;
  }
}

bool checked_size_add(size_t left, size_t right, size_t * result)
{
  if (result == nullptr || right > std::numeric_limits<size_t>::max() - left) {
    return false;
  }
  *result = left + right;
  return true;
}

bool checked_size_multiply(size_t left, size_t right, size_t * result)
{
  if (result == nullptr || (left != 0 && right > std::numeric_limits<size_t>::max() / left)) {
    return false;
  }
  *result = left * right;
  return true;
}

bool max_serialized_size_introspection_c_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  size_t * size);

bool max_serialized_size_introspection_c_member(
  const rosidl_typesupport_introspection_c__MessageMember & member,
  size_t * size)
{
  if (size == nullptr) {
    return false;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_STRING) {
    if (member.string_upper_bound_ == 0) {
      return false;
    }
    return checked_size_add(8, member.string_upper_bound_, size);
  }
  if (member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE) {
    return max_serialized_size_introspection_c_message(
      introspection_c_members(member.members_), size);
  }
  *size = primitive_size(member.type_id_);
  return *size > 0;
}

bool max_serialized_size_introspection_c_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  size_t * size)
{
  if (members == nullptr || size == nullptr) {
    return false;
  }
  size_t total = 8;
  for (uint32_t i = 0; i < members->member_count_; ++i) {
    const auto & member = members->members_[i];
    size_t element_size = 0;
    if (!max_serialized_size_introspection_c_member(member, &element_size)) {
      return false;
    }
    size_t field_size = element_size;
    if (member.is_array_) {
      if (member.array_size_ == 0) {
        return false;
      }
      if (!checked_size_multiply(element_size, member.array_size_, &field_size) ||
        !checked_size_add(8, field_size, &field_size))
      {
        return false;
      }
    }
    if (!checked_size_add(total, field_size, &total)) {
      return false;
    }
  }
  *size = total;
  return true;
}

bool max_serialized_size_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  size_t * size);

bool max_serialized_size_introspection_cpp_member(
  const rosidl_typesupport_introspection_cpp::MessageMember & member,
  size_t * size)
{
  if (size == nullptr) {
    return false;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_STRING) {
    if (member.string_upper_bound_ == 0) {
      return false;
    }
    return checked_size_add(8, member.string_upper_bound_, size);
  }
  if (member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE) {
    return max_serialized_size_introspection_cpp_message(
      introspection_cpp_members(member.members_), size);
  }
  *size = primitive_size(member.type_id_);
  return *size > 0;
}

bool max_serialized_size_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  size_t * size)
{
  if (members == nullptr || size == nullptr) {
    return false;
  }
  size_t total = 8;
  for (uint32_t i = 0; i < members->member_count_; ++i) {
    const auto & member = members->members_[i];
    size_t element_size = 0;
    if (!max_serialized_size_introspection_cpp_member(member, &element_size)) {
      return false;
    }
    size_t field_size = element_size;
    if (member.is_array_) {
      if (member.array_size_ == 0) {
        return false;
      }
      if (!checked_size_multiply(element_size, member.array_size_, &field_size) ||
        !checked_size_add(8, field_size, &field_size))
      {
        return false;
      }
    }
    if (!checked_size_add(total, field_size, &total)) {
      return false;
    }
  }
  *size = total;
  return true;
}

void append_u64(std::vector<std::uint8_t> * out, std::uint64_t value)
{
  for (int i = 0; i < 8; ++i) {
    out->push_back(static_cast<std::uint8_t>((value >> (8 * i)) & 0xFFu));
  }
}

bool read_u64(const std::vector<std::uint8_t> & payload, size_t * offset, std::uint64_t * value)
{
  if (offset == nullptr || value == nullptr || *offset + 8 > payload.size()) {
    return false;
  }
  std::uint64_t decoded = 0;
  for (int i = 0; i < 8; ++i) {
    decoded |= static_cast<std::uint64_t>(payload[*offset + i]) << (8 * i);
  }
  *offset += 8;
  *value = decoded;
  return true;
}

void append_bytes(
  std::vector<std::uint8_t> * out,
  const void * data,
  size_t size)
{
  const auto * bytes = static_cast<const std::uint8_t *>(data);
  out->insert(out->end(), bytes, bytes + size);
}

bool read_bytes(
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  void * data,
  size_t size)
{
  if (offset == nullptr || data == nullptr || *offset + size > payload.size()) {
    return false;
  }
  std::memcpy(data, payload.data() + *offset, size);
  *offset += size;
  return true;
}

bool serialize_introspection_c_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  const void * ros_message,
  std::vector<std::uint8_t> * out);

bool deserialize_introspection_c_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  void * ros_message);

bool serialize_introspection_c_member(
  const rosidl_typesupport_introspection_c__MessageMember & member,
  const void * member_data,
  std::vector<std::uint8_t> * out)
{
  if (out == nullptr || member_data == nullptr) {
    return false;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_STRING) {
    const auto * value = static_cast<const rosidl_runtime_c__String *>(member_data);
    const size_t size = value->data == nullptr ? 0 : value->size;
    append_u64(out, static_cast<std::uint64_t>(size));
    if (size > 0) {
      append_bytes(out, value->data, size);
    }
    return true;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE) {
    return serialize_introspection_c_message(introspection_c_members(member.members_), member_data, out);
  }
  const size_t size = primitive_size(member.type_id_);
  if (size == 0) {
    return false;
  }
  append_bytes(out, member_data, size);
  return true;
}

const void * array_const_member_ptr(
  const rosidl_typesupport_introspection_c__MessageMember & member,
  const void * array_data,
  size_t index)
{
  if (member.get_const_function != nullptr) {
    return member.get_const_function(array_data, index);
  }
  const auto * nested_members = member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE ?
    introspection_c_members(member.members_) : nullptr;
  const size_t element_size = member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE ?
    (nested_members == nullptr ? 0 : nested_members->size_of_) : primitive_size(member.type_id_);
  if (element_size == 0) {
    return nullptr;
  }
  return static_cast<const std::uint8_t *>(array_data) + (element_size * index);
}

void * array_member_ptr(
  const rosidl_typesupport_introspection_c__MessageMember & member,
  void * array_data,
  size_t index)
{
  if (member.get_function != nullptr) {
    return member.get_function(array_data, index);
  }
  const auto * nested_members = member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE ?
    introspection_c_members(member.members_) : nullptr;
  const size_t element_size = member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE ?
    (nested_members == nullptr ? 0 : nested_members->size_of_) : primitive_size(member.type_id_);
  if (element_size == 0) {
    return nullptr;
  }
  return static_cast<std::uint8_t *>(array_data) + (element_size * index);
}

bool serialize_introspection_c_field(
  const rosidl_typesupport_introspection_c__MessageMember & member,
  const void * ros_message,
  std::vector<std::uint8_t> * out)
{
  const auto * member_data = static_cast<const std::uint8_t *>(ros_message) + member.offset_;
  if (!member.is_array_) {
    return serialize_introspection_c_member(member, member_data, out);
  }

  const size_t element_count = member.size_function != nullptr ?
    member.size_function(member_data) : member.array_size_;
  append_u64(out, static_cast<std::uint64_t>(element_count));
  for (size_t i = 0; i < element_count; ++i) {
    const void * element = array_const_member_ptr(member, member_data, i);
    if (element == nullptr || !serialize_introspection_c_member(member, element, out)) {
      return false;
    }
  }
  return true;
}

bool serialize_introspection_c_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  const void * ros_message,
  std::vector<std::uint8_t> * out)
{
  if (members == nullptr || ros_message == nullptr || out == nullptr) {
    return false;
  }
  append_u64(out, static_cast<std::uint64_t>(members->member_count_));
  for (uint32_t i = 0; i < members->member_count_; ++i) {
    if (!serialize_introspection_c_field(members->members_[i], ros_message, out)) {
      return false;
    }
  }
  return true;
}

bool deserialize_introspection_c_member(
  const rosidl_typesupport_introspection_c__MessageMember & member,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  void * member_data)
{
  if (offset == nullptr || member_data == nullptr) {
    return false;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_STRING) {
    std::uint64_t size = 0;
    if (!read_u64(payload, offset, &size) || *offset + size > payload.size()) {
      return false;
    }
    if (member.string_upper_bound_ > 0 && size > member.string_upper_bound_) {
      return false;
    }
    auto * value = static_cast<rosidl_runtime_c__String *>(member_data);
    const char * source = reinterpret_cast<const char *>(payload.data() + *offset);
    if (!rosidl_runtime_c__String__assignn(value, source, static_cast<size_t>(size))) {
      return false;
    }
    *offset += static_cast<size_t>(size);
    return true;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE) {
    return deserialize_introspection_c_message(
      introspection_c_members(member.members_), payload, offset, member_data);
  }
  const size_t size = primitive_size(member.type_id_);
  if (size == 0) {
    return false;
  }
  return read_bytes(payload, offset, member_data, size);
}

bool deserialize_introspection_c_field(
  const rosidl_typesupport_introspection_c__MessageMember & member,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  void * ros_message)
{
  auto * member_data = static_cast<std::uint8_t *>(ros_message) + member.offset_;
  if (!member.is_array_) {
    return deserialize_introspection_c_member(member, payload, offset, member_data);
  }

  std::uint64_t element_count = 0;
  if (!read_u64(payload, offset, &element_count)) {
    return false;
  }
  if (member.is_upper_bound_ && element_count > member.array_size_) {
    return false;
  }
  if (member.resize_function != nullptr) {
    if (!member.resize_function(member_data, static_cast<size_t>(element_count))) {
      return false;
    }
  } else if (element_count != member.array_size_) {
    return false;
  }
  for (size_t i = 0; i < static_cast<size_t>(element_count); ++i) {
    void * element = array_member_ptr(member, member_data, i);
    if (element == nullptr ||
      !deserialize_introspection_c_member(member, payload, offset, element))
    {
      return false;
    }
  }
  return true;
}

bool deserialize_introspection_c_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  void * ros_message)
{
  if (members == nullptr || offset == nullptr || ros_message == nullptr) {
    return false;
  }
  std::uint64_t member_count = 0;
  if (!read_u64(payload, offset, &member_count) || member_count != members->member_count_) {
    return false;
  }
  for (uint32_t i = 0; i < members->member_count_; ++i) {
    if (!deserialize_introspection_c_field(members->members_[i], payload, offset, ros_message)) {
      return false;
    }
  }
  return true;
}

bool serialize_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  const void * ros_message,
  std::vector<std::uint8_t> * out);

bool deserialize_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  void * ros_message);

bool serialize_introspection_cpp_member(
  const rosidl_typesupport_introspection_cpp::MessageMember & member,
  const void * member_data,
  std::vector<std::uint8_t> * out)
{
  if (out == nullptr || member_data == nullptr) {
    return false;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_STRING) {
    const auto & value = *static_cast<const std::string *>(member_data);
    append_u64(out, static_cast<std::uint64_t>(value.size()));
    if (!value.empty()) {
      append_bytes(out, value.data(), value.size());
    }
    return true;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE) {
    return serialize_introspection_cpp_message(
      introspection_cpp_members(member.members_), member_data, out);
  }
  const size_t size = primitive_size(member.type_id_);
  if (size == 0) {
    return false;
  }
  append_bytes(out, member_data, size);
  return true;
}

const void * cpp_array_const_member_ptr(
  const rosidl_typesupport_introspection_cpp::MessageMember & member,
  const void * array_data,
  size_t index)
{
  if (member.get_const_function != nullptr) {
    return member.get_const_function(array_data, index);
  }
  const auto * nested_members =
    member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE ?
    introspection_cpp_members(member.members_) : nullptr;
  const size_t element_size =
    member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE ?
    (nested_members == nullptr ? 0 : nested_members->size_of_) : primitive_size(member.type_id_);
  if (element_size == 0) {
    return nullptr;
  }
  return static_cast<const std::uint8_t *>(array_data) + (element_size * index);
}

void * cpp_array_member_ptr(
  const rosidl_typesupport_introspection_cpp::MessageMember & member,
  void * array_data,
  size_t index)
{
  if (member.get_function != nullptr) {
    return member.get_function(array_data, index);
  }
  const auto * nested_members =
    member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE ?
    introspection_cpp_members(member.members_) : nullptr;
  const size_t element_size =
    member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE ?
    (nested_members == nullptr ? 0 : nested_members->size_of_) : primitive_size(member.type_id_);
  if (element_size == 0) {
    return nullptr;
  }
  return static_cast<std::uint8_t *>(array_data) + (element_size * index);
}

bool serialize_introspection_cpp_field(
  const rosidl_typesupport_introspection_cpp::MessageMember & member,
  const void * ros_message,
  std::vector<std::uint8_t> * out)
{
  const auto * member_data = static_cast<const std::uint8_t *>(ros_message) + member.offset_;
  if (!member.is_array_) {
    return serialize_introspection_cpp_member(member, member_data, out);
  }
  const size_t element_count = member.size_function != nullptr ?
    member.size_function(member_data) : member.array_size_;
  append_u64(out, static_cast<std::uint64_t>(element_count));
  for (size_t i = 0; i < element_count; ++i) {
    const void * element = cpp_array_const_member_ptr(member, member_data, i);
    union PrimitiveScratch
    {
      long double alignment;
      std::uint8_t bytes[32];
    } scratch{};
    if (element == nullptr && member.fetch_function != nullptr &&
      primitive_size(member.type_id_) <= sizeof(scratch.bytes))
    {
      member.fetch_function(member_data, i, scratch.bytes);
      element = scratch.bytes;
    }
    if (element == nullptr || !serialize_introspection_cpp_member(member, element, out)) {
      return false;
    }
  }
  return true;
}

bool serialize_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  const void * ros_message,
  std::vector<std::uint8_t> * out)
{
  if (members == nullptr || ros_message == nullptr || out == nullptr) {
    return false;
  }
  append_u64(out, static_cast<std::uint64_t>(members->member_count_));
  for (uint32_t i = 0; i < members->member_count_; ++i) {
    if (!serialize_introspection_cpp_field(members->members_[i], ros_message, out)) {
      return false;
    }
  }
  return true;
}

bool deserialize_introspection_cpp_member(
  const rosidl_typesupport_introspection_cpp::MessageMember & member,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  void * member_data)
{
  if (offset == nullptr || member_data == nullptr) {
    return false;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_STRING) {
    std::uint64_t size = 0;
    if (!read_u64(payload, offset, &size) || *offset + size > payload.size() ||
      (member.string_upper_bound_ > 0 && size > member.string_upper_bound_))
    {
      return false;
    }
    auto & value = *static_cast<std::string *>(member_data);
    value.assign(reinterpret_cast<const char *>(payload.data() + *offset), static_cast<size_t>(size));
    *offset += static_cast<size_t>(size);
    return true;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE) {
    return deserialize_introspection_cpp_message(
      introspection_cpp_members(member.members_), payload, offset, member_data);
  }
  const size_t size = primitive_size(member.type_id_);
  return size > 0 && read_bytes(payload, offset, member_data, size);
}

bool deserialize_introspection_cpp_field(
  const rosidl_typesupport_introspection_cpp::MessageMember & member,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  void * ros_message)
{
  auto * member_data = static_cast<std::uint8_t *>(ros_message) + member.offset_;
  if (!member.is_array_) {
    return deserialize_introspection_cpp_member(member, payload, offset, member_data);
  }
  std::uint64_t element_count = 0;
  if (!read_u64(payload, offset, &element_count) ||
    (member.is_upper_bound_ && element_count > member.array_size_))
  {
    return false;
  }
  if (member.resize_function != nullptr) {
    member.resize_function(member_data, static_cast<size_t>(element_count));
  } else if (element_count != member.array_size_) {
    return false;
  }
  for (size_t i = 0; i < static_cast<size_t>(element_count); ++i) {
    void * element = cpp_array_member_ptr(member, member_data, i);
    if (element != nullptr) {
      if (!deserialize_introspection_cpp_member(member, payload, offset, element)) {
        return false;
      }
      continue;
    }
    const size_t size = primitive_size(member.type_id_);
    union PrimitiveScratch
    {
      long double alignment;
      std::uint8_t bytes[32];
    } scratch{};
    if (member.assign_function == nullptr || size == 0 || size > sizeof(scratch.bytes) ||
      !read_bytes(payload, offset, scratch.bytes, size))
    {
      return false;
    }
    member.assign_function(member_data, i, scratch.bytes);
  }
  return true;
}

bool deserialize_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  void * ros_message)
{
  if (members == nullptr || offset == nullptr || ros_message == nullptr) {
    return false;
  }
  std::uint64_t member_count = 0;
  if (!read_u64(payload, offset, &member_count) || member_count != members->member_count_) {
    return false;
  }
  for (uint32_t i = 0; i < members->member_count_; ++i) {
    if (!deserialize_introspection_cpp_field(members->members_[i], payload, offset, ros_message)) {
      return false;
    }
  }
  return true;
}

template<typename T>
bool read_content_filter_primitive(
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  T * value)
{
  return value != nullptr && read_bytes(payload, offset, value, sizeof(T));
}

template<typename T>
std::string content_filter_floating_text(T value)
{
  std::ostringstream out;
  out << std::setprecision(std::numeric_limits<T>::max_digits10) << value;
  return out.str();
}

bool content_filter_primitive_text(
  uint8_t type_id,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  std::string * text)
{
  if (offset == nullptr || text == nullptr) {
    return false;
  }
  switch (type_id) {
    case rosidl_typesupport_introspection_c__ROS_TYPE_FLOAT:
      {
        float value = 0.0F;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = content_filter_floating_text(value);
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_DOUBLE:
      {
        double value = 0.0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = content_filter_floating_text(value);
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_LONG_DOUBLE:
      {
        long double value = 0.0L;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = content_filter_floating_text(value);
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_BOOLEAN:
      {
        bool value = false;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = value ? "true" : "false";
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_CHAR:
      {
        char value = 0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = std::to_string(static_cast<unsigned int>(static_cast<unsigned char>(value)));
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_WCHAR:
      {
        char16_t value = 0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = std::to_string(static_cast<unsigned int>(value));
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_OCTET:
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT8:
      {
        std::uint8_t value = 0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = std::to_string(static_cast<unsigned int>(value));
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT8:
      {
        std::int8_t value = 0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = std::to_string(static_cast<int>(value));
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT16:
      {
        std::uint16_t value = 0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = std::to_string(value);
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT16:
      {
        std::int16_t value = 0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = std::to_string(value);
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT32:
      {
        std::uint32_t value = 0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = std::to_string(value);
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT32:
      {
        std::int32_t value = 0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = std::to_string(value);
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT64:
      {
        std::uint64_t value = 0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = std::to_string(value);
        return true;
      }
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT64:
      {
        std::int64_t value = 0;
        if (!read_content_filter_primitive(payload, offset, &value)) {
          return false;
        }
        *text = std::to_string(value);
        return true;
      }
    default:
      return false;
  }
}

std::string nested_content_filter_path(
  const std::string & prefix,
  const char * member_name)
{
  if (member_name == nullptr || member_name[0] == '\0') {
    return {};
  }
  return prefix.empty() ? member_name : prefix + "." + member_name;
}

bool reflect_introspection_c_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  const std::string & prefix,
  std::unordered_map<std::string, std::string> * fields);

bool reflect_introspection_c_member(
  const rosidl_typesupport_introspection_c__MessageMember & member,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  const std::string & path,
  std::unordered_map<std::string, std::string> * fields)
{
  if (offset == nullptr || fields == nullptr || path.empty()) {
    return false;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_STRING) {
    std::uint64_t size = 0;
    if (!read_u64(payload, offset, &size) ||
      size > std::numeric_limits<size_t>::max() ||
      size > payload.size() - *offset ||
      (member.string_upper_bound_ > 0 && size > member.string_upper_bound_))
    {
      return false;
    }
    (*fields)[path] = std::string(
      reinterpret_cast<const char *>(payload.data() + *offset),
      static_cast<size_t>(size));
    *offset += static_cast<size_t>(size);
    return true;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE) {
    return reflect_introspection_c_message(
      introspection_c_members(member.members_), payload, offset, path, fields);
  }
  std::string text;
  if (!content_filter_primitive_text(member.type_id_, payload, offset, &text)) {
    return false;
  }
  (*fields)[path] = std::move(text);
  return true;
}

bool reflect_introspection_c_field(
  const rosidl_typesupport_introspection_c__MessageMember & member,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  const std::string & prefix,
  std::unordered_map<std::string, std::string> * fields)
{
  const std::string path = nested_content_filter_path(prefix, member.name_);
  if (path.empty()) {
    return false;
  }
  if (!member.is_array_) {
    return reflect_introspection_c_member(member, payload, offset, path, fields);
  }
  std::uint64_t element_count = 0;
  if (!read_u64(payload, offset, &element_count) ||
    element_count > std::numeric_limits<size_t>::max() ||
    element_count > payload.size() - *offset ||
    (member.is_upper_bound_ && element_count > member.array_size_) ||
    (member.resize_function == nullptr && element_count != member.array_size_))
  {
    return false;
  }
  (*fields)[path + "._length"] = std::to_string(element_count);
  for (size_t index = 0; index < static_cast<size_t>(element_count); ++index) {
    if (!reflect_introspection_c_member(
        member, payload, offset, path + "[" + std::to_string(index) + "]", fields))
    {
      return false;
    }
  }
  return true;
}

bool reflect_introspection_c_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  const std::string & prefix,
  std::unordered_map<std::string, std::string> * fields)
{
  if (members == nullptr || offset == nullptr || fields == nullptr) {
    return false;
  }
  std::uint64_t member_count = 0;
  if (!read_u64(payload, offset, &member_count) || member_count != members->member_count_) {
    return false;
  }
  for (uint32_t index = 0; index < members->member_count_; ++index) {
    if (!reflect_introspection_c_field(
        members->members_[index], payload, offset, prefix, fields))
    {
      return false;
    }
  }
  return true;
}

bool reflect_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  const std::string & prefix,
  std::unordered_map<std::string, std::string> * fields);

bool reflect_introspection_cpp_member(
  const rosidl_typesupport_introspection_cpp::MessageMember & member,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  const std::string & path,
  std::unordered_map<std::string, std::string> * fields)
{
  if (offset == nullptr || fields == nullptr || path.empty()) {
    return false;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_STRING) {
    std::uint64_t size = 0;
    if (!read_u64(payload, offset, &size) ||
      size > std::numeric_limits<size_t>::max() ||
      size > payload.size() - *offset ||
      (member.string_upper_bound_ > 0 && size > member.string_upper_bound_))
    {
      return false;
    }
    (*fields)[path] = std::string(
      reinterpret_cast<const char *>(payload.data() + *offset),
      static_cast<size_t>(size));
    *offset += static_cast<size_t>(size);
    return true;
  }
  if (member.type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE) {
    return reflect_introspection_cpp_message(
      introspection_cpp_members(member.members_), payload, offset, path, fields);
  }
  std::string text;
  if (!content_filter_primitive_text(member.type_id_, payload, offset, &text)) {
    return false;
  }
  (*fields)[path] = std::move(text);
  return true;
}

bool reflect_introspection_cpp_field(
  const rosidl_typesupport_introspection_cpp::MessageMember & member,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  const std::string & prefix,
  std::unordered_map<std::string, std::string> * fields)
{
  const std::string path = nested_content_filter_path(prefix, member.name_);
  if (path.empty()) {
    return false;
  }
  if (!member.is_array_) {
    return reflect_introspection_cpp_member(member, payload, offset, path, fields);
  }
  std::uint64_t element_count = 0;
  if (!read_u64(payload, offset, &element_count) ||
    element_count > std::numeric_limits<size_t>::max() ||
    element_count > payload.size() - *offset ||
    (member.is_upper_bound_ && element_count > member.array_size_) ||
    (member.resize_function == nullptr && element_count != member.array_size_))
  {
    return false;
  }
  (*fields)[path + "._length"] = std::to_string(element_count);
  for (size_t index = 0; index < static_cast<size_t>(element_count); ++index) {
    if (!reflect_introspection_cpp_member(
        member, payload, offset, path + "[" + std::to_string(index) + "]", fields))
    {
      return false;
    }
  }
  return true;
}

bool reflect_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  const std::vector<std::uint8_t> & payload,
  size_t * offset,
  const std::string & prefix,
  std::unordered_map<std::string, std::string> * fields)
{
  if (members == nullptr || offset == nullptr || fields == nullptr) {
    return false;
  }
  std::uint64_t member_count = 0;
  if (!read_u64(payload, offset, &member_count) || member_count != members->member_count_) {
    return false;
  }
  for (uint32_t index = 0; index < members->member_count_; ++index) {
    if (!reflect_introspection_cpp_field(
        members->members_[index], payload, offset, prefix, fields))
    {
      return false;
    }
  }
  return true;
}

std::optional<std::unordered_map<std::string, std::string>>
content_filter_typed_fields(
  const rosidl_message_type_support_t * type_support,
  const std::vector<std::uint8_t> & payload)
{
  const rosidl_message_type_support_t * effective =
    resolve_effective_type_support(type_support);
  std::unordered_map<std::string, std::string> fields;
  size_t offset = 0;
  const auto * c_members = introspection_c_members(effective);
  if (c_members != nullptr) {
    if (reflect_introspection_c_message(c_members, payload, &offset, "", &fields) &&
      offset == payload.size())
    {
      return fields;
    }
    return std::nullopt;
  }
  const auto * cpp_members = introspection_cpp_members(effective);
  if (cpp_members != nullptr &&
    reflect_introspection_cpp_message(cpp_members, payload, &offset, "", &fields) &&
    offset == payload.size())
  {
    return fields;
  }
  return std::nullopt;
}

std::string trim_text(const std::string & value)
{
  const auto begin = std::find_if_not(
    value.begin(), value.end(), [](unsigned char c) {return std::isspace(c) != 0;});
  if (begin == value.end()) {
    return {};
  }
  const auto end = std::find_if_not(
    value.rbegin(), value.rend(), [](unsigned char c) {return std::isspace(c) != 0;}).base();
  return std::string(begin, end);
}

std::string strip_quotes(std::string value)
{
  value = trim_text(value);
  if (value.size() >= 2 &&
    ((value.front() == '"' && value.back() == '"') ||
    (value.front() == '\'' && value.back() == '\'')))
  {
    return value.substr(1, value.size() - 2);
  }
  return value;
}

bool is_text_payload(const std::string & value)
{
  return std::all_of(value.begin(), value.end(), [](unsigned char c) {
      return c == '\n' || c == '\r' || c == '\t' || (c >= 0x20 && c < 0x7f);
    });
}

std::optional<std::uint64_t> read_little_u64_at(
  const std::vector<std::uint8_t> & payload,
  size_t offset)
{
  if (offset + 8 > payload.size()) {
    return std::nullopt;
  }
  std::uint64_t value = 0;
  for (int i = 0; i < 8; ++i) {
    value |= static_cast<std::uint64_t>(payload[offset + i]) << (8 * i);
  }
  return value;
}

std::vector<std::string> content_filter_payload_texts(
  const std::vector<std::uint8_t> & payload)
{
  std::vector<std::string> texts;
  if (!payload.empty()) {
    std::string raw(reinterpret_cast<const char *>(payload.data()), payload.size());
    if (is_text_payload(raw)) {
      texts.push_back(raw);
    }
  }
  const auto member_count = read_little_u64_at(payload, 0);
  const auto string_size = read_little_u64_at(payload, 8);
  if (member_count.has_value() && string_size.has_value() && *member_count == 1 &&
    16 + *string_size == payload.size())
  {
    std::string embedded(
      reinterpret_cast<const char *>(payload.data() + 16),
      static_cast<size_t>(*string_size));
    if (is_text_payload(embedded) &&
      std::find(texts.begin(), texts.end(), embedded) == texts.end())
    {
      texts.push_back(embedded);
    }
  }
  return texts;
}

std::unordered_map<std::string, std::string> parse_content_filter_fields(
  const std::string & text)
{
  std::unordered_map<std::string, std::string> fields;
  std::string normalized;
  normalized.reserve(text.size());
  for (const char c : text) {
    if (c == '{' || c == '}') {
      normalized.push_back(',');
    } else {
      normalized.push_back(c);
    }
  }
  size_t start = 0;
  while (start <= normalized.size()) {
    const size_t end = normalized.find_first_of(";\n,", start);
    const std::string segment = normalized.substr(
      start,
      end == std::string::npos ? std::string::npos : end - start);
    const size_t separator = segment.find_first_of("=:");
    if (separator != std::string::npos) {
      const std::string key = strip_quotes(segment.substr(0, separator));
      const std::string value = strip_quotes(segment.substr(separator + 1));
      if (!key.empty()) {
        fields[key] = value;
      }
    }
    if (end == std::string::npos) {
      break;
    }
    start = end + 1;
  }
  return fields;
}

std::optional<double> parse_number(const std::string & value)
{
  const std::string trimmed = trim_text(value);
  if (trimmed.empty()) {
    return std::nullopt;
  }
  char * end = nullptr;
  errno = 0;
  const double parsed = std::strtod(trimmed.c_str(), &end);
  if (errno != 0 || end == trimmed.c_str() || *end != '\0') {
    return std::nullopt;
  }
  return parsed;
}

enum class ContentFilterTokenKind
{
  word,
  string_value,
  parameter,
  left_parenthesis,
  right_parenthesis,
  comma,
  equal,
  not_equal,
  greater,
  greater_equal,
  less,
  less_equal,
  logical_and,
  logical_or,
  logical_not,
  like,
  in,
  between,
  is,
  null_value,
  end,
  invalid,
};

struct ContentFilterToken
{
  ContentFilterTokenKind kind{ContentFilterTokenKind::invalid};
  std::string text;
};

std::string uppercase_text(const std::string & value)
{
  std::string result = value;
  std::transform(result.begin(), result.end(), result.begin(), [](unsigned char c) {
      return static_cast<char>(std::toupper(c));
    });
  return result;
}

std::vector<ContentFilterToken> tokenize_content_filter_expression(
  const std::string & expression)
{
  std::vector<ContentFilterToken> tokens;
  size_t offset = 0;
  while (offset < expression.size()) {
    const unsigned char current = static_cast<unsigned char>(expression[offset]);
    if (std::isspace(current) != 0) {
      ++offset;
      continue;
    }
    if (expression[offset] == '(') {
      tokens.push_back({ContentFilterTokenKind::left_parenthesis, "("});
      ++offset;
      continue;
    }
    if (expression[offset] == ')') {
      tokens.push_back({ContentFilterTokenKind::right_parenthesis, ")"});
      ++offset;
      continue;
    }
    if (expression[offset] == ',') {
      tokens.push_back({ContentFilterTokenKind::comma, ","});
      ++offset;
      continue;
    }
    if (expression[offset] == '\'' || expression[offset] == '"') {
      const char quote = expression[offset++];
      std::string value;
      bool closed = false;
      while (offset < expression.size()) {
        const char c = expression[offset++];
        if (c == quote) {
          if (offset < expression.size() && expression[offset] == quote) {
            value.push_back(quote);
            ++offset;
            continue;
          }
          closed = true;
          break;
        }
        if (c == '\\' && offset < expression.size()) {
          value.push_back(expression[offset++]);
        } else {
          value.push_back(c);
        }
      }
      tokens.push_back({
        closed ? ContentFilterTokenKind::string_value : ContentFilterTokenKind::invalid,
        value});
      if (!closed) {
        break;
      }
      continue;
    }
    if (expression[offset] == '%' && offset + 1 < expression.size() &&
      std::isdigit(static_cast<unsigned char>(expression[offset + 1])) != 0)
    {
      const size_t start = offset++;
      while (offset < expression.size() &&
        std::isdigit(static_cast<unsigned char>(expression[offset])) != 0)
      {
        ++offset;
      }
      tokens.push_back({
        ContentFilterTokenKind::parameter, expression.substr(start, offset - start)});
      continue;
    }
    const auto push_operator = [&tokens](ContentFilterTokenKind kind, const char * text) {
        tokens.push_back({kind, text});
      };
    if (expression[offset] == '=') {
      push_operator(ContentFilterTokenKind::equal, "=");
      ++offset;
      continue;
    }
    if (expression[offset] == '!' && offset + 1 < expression.size() &&
      expression[offset + 1] == '=')
    {
      push_operator(ContentFilterTokenKind::not_equal, "!=");
      offset += 2;
      continue;
    }
    if (expression[offset] == '<') {
      if (offset + 1 < expression.size() && expression[offset + 1] == '=') {
        push_operator(ContentFilterTokenKind::less_equal, "<=");
        offset += 2;
      } else if (offset + 1 < expression.size() && expression[offset + 1] == '>') {
        push_operator(ContentFilterTokenKind::not_equal, "<>");
        offset += 2;
      } else {
        push_operator(ContentFilterTokenKind::less, "<");
        ++offset;
      }
      continue;
    }
    if (expression[offset] == '>') {
      if (offset + 1 < expression.size() && expression[offset + 1] == '=') {
        push_operator(ContentFilterTokenKind::greater_equal, ">=");
        offset += 2;
      } else {
        push_operator(ContentFilterTokenKind::greater, ">");
        ++offset;
      }
      continue;
    }
    const size_t start = offset;
    while (offset < expression.size() &&
      std::isspace(static_cast<unsigned char>(expression[offset])) == 0 &&
      std::string("(),=<>!\"'").find(expression[offset]) == std::string::npos)
    {
      ++offset;
    }
    if (start == offset) {
      tokens.push_back({ContentFilterTokenKind::invalid, expression.substr(offset, 1)});
      ++offset;
      continue;
    }
    const std::string word = expression.substr(start, offset - start);
    const std::string keyword = uppercase_text(word);
    ContentFilterTokenKind kind = ContentFilterTokenKind::word;
    if (keyword == "AND") {
      kind = ContentFilterTokenKind::logical_and;
    } else if (keyword == "OR") {
      kind = ContentFilterTokenKind::logical_or;
    } else if (keyword == "NOT") {
      kind = ContentFilterTokenKind::logical_not;
    } else if (keyword == "LIKE") {
      kind = ContentFilterTokenKind::like;
    } else if (keyword == "IN") {
      kind = ContentFilterTokenKind::in;
    } else if (keyword == "BETWEEN") {
      kind = ContentFilterTokenKind::between;
    } else if (keyword == "IS") {
      kind = ContentFilterTokenKind::is;
    } else if (keyword == "NULL") {
      kind = ContentFilterTokenKind::null_value;
    }
    tokens.push_back({kind, word});
  }
  tokens.push_back({ContentFilterTokenKind::end, ""});
  return tokens;
}

bool content_filter_like(const std::string & value, const std::string & pattern)
{
  size_t value_offset = 0;
  size_t pattern_offset = 0;
  size_t wildcard_offset = std::string::npos;
  size_t wildcard_value_offset = 0;
  while (value_offset < value.size()) {
    if (pattern_offset < pattern.size() &&
      (pattern[pattern_offset] == '_' || pattern[pattern_offset] == value[value_offset]))
    {
      ++value_offset;
      ++pattern_offset;
    } else if (pattern_offset < pattern.size() && pattern[pattern_offset] == '%') {
      wildcard_offset = pattern_offset++;
      wildcard_value_offset = value_offset;
    } else if (wildcard_offset != std::string::npos) {
      pattern_offset = wildcard_offset + 1;
      value_offset = ++wildcard_value_offset;
    } else {
      return false;
    }
  }
  while (pattern_offset < pattern.size() && pattern[pattern_offset] == '%') {
    ++pattern_offset;
  }
  return pattern_offset == pattern.size();
}

enum class ContentFilterTruth
{
  false_value,
  true_value,
  unknown,
};

ContentFilterTruth content_filter_truth(bool value)
{
  return value ? ContentFilterTruth::true_value : ContentFilterTruth::false_value;
}

ContentFilterTruth content_filter_not(ContentFilterTruth value)
{
  if (value == ContentFilterTruth::unknown) {
    return ContentFilterTruth::unknown;
  }
  return value == ContentFilterTruth::true_value ?
         ContentFilterTruth::false_value : ContentFilterTruth::true_value;
}

ContentFilterTruth content_filter_and(ContentFilterTruth left, ContentFilterTruth right)
{
  if (left == ContentFilterTruth::false_value || right == ContentFilterTruth::false_value) {
    return ContentFilterTruth::false_value;
  }
  if (left == ContentFilterTruth::true_value && right == ContentFilterTruth::true_value) {
    return ContentFilterTruth::true_value;
  }
  return ContentFilterTruth::unknown;
}

ContentFilterTruth content_filter_or(ContentFilterTruth left, ContentFilterTruth right)
{
  if (left == ContentFilterTruth::true_value || right == ContentFilterTruth::true_value) {
    return ContentFilterTruth::true_value;
  }
  if (left == ContentFilterTruth::false_value && right == ContentFilterTruth::false_value) {
    return ContentFilterTruth::false_value;
  }
  return ContentFilterTruth::unknown;
}

class ContentFilterExpressionParser
{
public:
  ContentFilterExpressionParser(
    const std::unordered_map<std::string, std::string> & fields,
    const std::vector<std::string> & parameters,
    const std::string & expression)
  : fields_(fields), parameters_(parameters), tokens_(tokenize_content_filter_expression(expression))
  {}

  bool evaluate(bool * valid)
  {
    valid_ = true;
    const ContentFilterTruth result = parse_or_expression();
    valid_ = valid_ && current().kind == ContentFilterTokenKind::end;
    if (valid != nullptr) {
      *valid = valid_;
    }
    return valid_ && result == ContentFilterTruth::true_value;
  }

private:
  const ContentFilterToken & current() const
  {
    return tokens_[std::min(offset_, tokens_.size() - 1)];
  }

  bool match(ContentFilterTokenKind kind)
  {
    if (current().kind != kind) {
      return false;
    }
    ++offset_;
    return true;
  }

  bool expect(ContentFilterTokenKind kind)
  {
    if (!match(kind)) {
      valid_ = false;
      return false;
    }
    return true;
  }

  ContentFilterTruth parse_or_expression()
  {
    ContentFilterTruth result = parse_and_expression();
    while (match(ContentFilterTokenKind::logical_or)) {
      const ContentFilterTruth right = parse_and_expression();
      result = content_filter_or(result, right);
    }
    return result;
  }

  ContentFilterTruth parse_and_expression()
  {
    ContentFilterTruth result = parse_not_expression();
    while (match(ContentFilterTokenKind::logical_and)) {
      const ContentFilterTruth right = parse_not_expression();
      result = content_filter_and(result, right);
    }
    return result;
  }

  ContentFilterTruth parse_not_expression()
  {
    if (match(ContentFilterTokenKind::logical_not)) {
      return content_filter_not(parse_not_expression());
    }
    return parse_primary_expression();
  }

  ContentFilterTruth parse_primary_expression()
  {
    if (match(ContentFilterTokenKind::left_parenthesis)) {
      const ContentFilterTruth result = parse_or_expression();
      expect(ContentFilterTokenKind::right_parenthesis);
      return result;
    }
    return parse_predicate();
  }

  std::optional<std::string> parse_value()
  {
    const ContentFilterToken token = current();
    if (token.kind == ContentFilterTokenKind::parameter) {
      ++offset_;
      char * end = nullptr;
      errno = 0;
      const unsigned long index = std::strtoul(token.text.c_str() + 1, &end, 10);
      if (errno != 0 || end == token.text.c_str() + 1 || *end != '\0' ||
        index >= parameters_.size())
      {
        valid_ = false;
        return std::nullopt;
      }
      return parameters_[index];
    }
    if (token.kind == ContentFilterTokenKind::word ||
      token.kind == ContentFilterTokenKind::string_value)
    {
      ++offset_;
      return token.text;
    }
    valid_ = false;
    return std::nullopt;
  }

  bool compare(
    const std::string & actual,
    const std::string & expected,
    ContentFilterTokenKind operation)
  {
    if (operation == ContentFilterTokenKind::equal) {
      return actual == expected;
    }
    if (operation == ContentFilterTokenKind::not_equal) {
      return actual != expected;
    }
    const auto left = parse_number(actual);
    const auto right = parse_number(expected);
    if (!left.has_value() || !right.has_value()) {
      return false;
    }
    if (operation == ContentFilterTokenKind::greater) {
      return *left > *right;
    }
    if (operation == ContentFilterTokenKind::greater_equal) {
      return *left >= *right;
    }
    if (operation == ContentFilterTokenKind::less) {
      return *left < *right;
    }
    return operation == ContentFilterTokenKind::less_equal && *left <= *right;
  }

  ContentFilterTruth parse_predicate()
  {
    if (current().kind != ContentFilterTokenKind::word) {
      valid_ = false;
      return ContentFilterTruth::unknown;
    }
    const std::string key = current().text;
    ++offset_;
    const auto found = fields_.find(key);

    if (match(ContentFilterTokenKind::is)) {
      const bool negate = match(ContentFilterTokenKind::logical_not);
      if (!expect(ContentFilterTokenKind::null_value)) {
        return ContentFilterTruth::unknown;
      }
      const bool is_null = found == fields_.end() || found->second.empty();
      return content_filter_truth(negate ? !is_null : is_null);
    }

    const bool negate = match(ContentFilterTokenKind::logical_not);
    if (match(ContentFilterTokenKind::between)) {
      const auto lower = parse_value();
      expect(ContentFilterTokenKind::logical_and);
      const auto upper = parse_value();
      if (!lower.has_value() || !upper.has_value() || found == fields_.end()) {
        return ContentFilterTruth::unknown;
      }
      const auto actual_number = parse_number(found->second);
      const auto lower_number = parse_number(*lower);
      const auto upper_number = parse_number(*upper);
      const bool result = actual_number.has_value() && lower_number.has_value() &&
        upper_number.has_value() && *actual_number >= *lower_number &&
        *actual_number <= *upper_number;
      const ContentFilterTruth truth = content_filter_truth(result);
      return negate ? content_filter_not(truth) : truth;
    }
    if (match(ContentFilterTokenKind::in)) {
      if (!expect(ContentFilterTokenKind::left_parenthesis)) {
        return ContentFilterTruth::unknown;
      }
      bool any = false;
      bool has_value = false;
      do {
        const auto expected = parse_value();
        if (!expected.has_value()) {
          return ContentFilterTruth::unknown;
        }
        has_value = true;
        any = any || (found != fields_.end() && found->second == *expected);
      } while (match(ContentFilterTokenKind::comma));
      expect(ContentFilterTokenKind::right_parenthesis);
      if (!has_value) {
        valid_ = false;
        return ContentFilterTruth::unknown;
      }
      if (found == fields_.end()) {
        return ContentFilterTruth::unknown;
      }
      const ContentFilterTruth truth = content_filter_truth(any);
      return negate ? content_filter_not(truth) : truth;
    }
    if (match(ContentFilterTokenKind::like)) {
      const auto pattern = parse_value();
      if (!pattern.has_value()) {
        return ContentFilterTruth::unknown;
      }
      if (found == fields_.end()) {
        return ContentFilterTruth::unknown;
      }
      const ContentFilterTruth truth = content_filter_truth(
        content_filter_like(found->second, *pattern));
      return negate ? content_filter_not(truth) : truth;
    }
    if (negate) {
      valid_ = false;
      return ContentFilterTruth::unknown;
    }

    const ContentFilterTokenKind operation = current().kind;
    if (operation != ContentFilterTokenKind::equal &&
      operation != ContentFilterTokenKind::not_equal &&
      operation != ContentFilterTokenKind::greater &&
      operation != ContentFilterTokenKind::greater_equal &&
      operation != ContentFilterTokenKind::less &&
      operation != ContentFilterTokenKind::less_equal)
    {
      valid_ = false;
      return ContentFilterTruth::unknown;
    }
    ++offset_;
    const auto expected = parse_value();
    if (!expected.has_value() || found == fields_.end()) {
      return ContentFilterTruth::unknown;
    }
    return content_filter_truth(compare(found->second, *expected, operation));
  }

  const std::unordered_map<std::string, std::string> & fields_;
  const std::vector<std::string> & parameters_;
  std::vector<ContentFilterToken> tokens_;
  size_t offset_{0};
  bool valid_{true};
};

bool content_filter_matches_payload(
  const rosidl_message_type_support_t * type_support,
  const std::string & expression,
  const std::vector<std::string> & parameters,
  const std::vector<std::uint8_t> & payload)
{
  if (expression.empty()) {
    return true;
  }
  const auto typed_fields = content_filter_typed_fields(type_support, payload);
  if (typed_fields.has_value()) {
    g_content_filter_typed_reflections.fetch_add(1, std::memory_order_relaxed);
    bool valid = false;
    ContentFilterExpressionParser parser(*typed_fields, parameters, expression);
    if (parser.evaluate(&valid) && valid) {
      return true;
    }
  }
  for (const std::string & text : content_filter_payload_texts(payload)) {
    const auto fields = parse_content_filter_fields(text);
    if (fields.empty()) {
      continue;
    }
    bool valid = false;
    ContentFilterExpressionParser parser(fields, parameters, expression);
    if (parser.evaluate(&valid) && valid) {
      return true;
    }
  }
  return false;
}

bool content_filter_expression_is_valid(
  const std::string & expression,
  const std::vector<std::string> & parameters)
{
  if (expression.empty()) {
    return true;
  }
  const std::unordered_map<std::string, std::string> empty_fields;
  bool valid = false;
  ContentFilterExpressionParser parser(empty_fields, parameters, expression);
  (void)parser.evaluate(&valid);
  return valid;
}

bool subscription_content_filter_matches_locked(
  const FleetQoxSubscriptionData * subscription,
  const std::vector<std::uint8_t> & payload)
{
  if (subscription == nullptr || subscription->content_filter_expression.empty()) {
    return true;
  }
  return content_filter_matches_payload(
    subscription->type_support,
    subscription->content_filter_expression,
    subscription->content_filter_parameters,
    payload);
}

template<typename T, typename... Args>
T * allocate_data(rcutils_allocator_t allocator, Args &&... args)
{
  if (!rcutils_allocator_is_valid(&allocator)) {
    return nullptr;
  }
  void * memory = allocator.allocate(sizeof(T), allocator.state);
  if (memory == nullptr) {
    return nullptr;
  }
  try {
    return new (memory) T{std::forward<Args>(args)...};
  } catch (...) {
    allocator.deallocate(memory, allocator.state);
    return nullptr;
  }
}

template<typename T>
void deallocate_data(T * data)
{
  if (data == nullptr) {
    return;
  }
  rcutils_allocator_t allocator = data->allocator;
  data->~T();
  allocator.deallocate(data, allocator.state);
}

FleetQoxPublisherData * publisher_data(const rmw_publisher_t * publisher)
{
  return publisher == nullptr ? nullptr : static_cast<FleetQoxPublisherData *>(publisher->data);
}

FleetQoxSubscriptionData * subscription_data(const rmw_subscription_t * subscription)
{
  return subscription == nullptr ? nullptr : static_cast<FleetQoxSubscriptionData *>(subscription->data);
}

class PayloadScratch
{
public:
  PayloadScratch(
    bool supplied,
    const char * implementation_identifier,
    void * raw_data,
    rmw_fleetqox_cpp::MessageAllocationKind expected_kind,
    const rosidl_message_type_support_t * expected_type_support)
  {
    if (!supplied) {
      return;
    }
    if (implementation_identifier == nullptr ||
      std::strcmp(implementation_identifier, kIdentifier) != 0)
    {
      status_ = RMW_RET_INCORRECT_RMW_IMPLEMENTATION;
      RMW_SET_ERROR_MSG("message allocation is not from rmw_fleetqox_cpp");
      return;
    }
    data_ = static_cast<rmw_fleetqox_cpp::MessageAllocationData *>(raw_data);
    if (data_ == nullptr ||
      data_->magic != rmw_fleetqox_cpp::kMessageAllocationMagic ||
      data_->kind != expected_kind)
    {
      data_ = nullptr;
      status_ = RMW_RET_INVALID_ARGUMENT;
      RMW_SET_ERROR_MSG("message allocation kind or data is invalid");
      return;
    }
    if (data_->type_support != expected_type_support) {
      data_ = nullptr;
      status_ = RMW_RET_INVALID_ARGUMENT;
      RMW_SET_ERROR_MSG("message allocation type support does not match endpoint");
      return;
    }
    lock_ = std::unique_lock<std::mutex>(data_->mutex);
    data_->payload.clear();
    capacity_before_ = data_->payload.capacity();
  }

  ~PayloadScratch()
  {
    if (data_ != nullptr) {
      if (data_->payload.capacity() > capacity_before_) {
        data_->capacity_growths.fetch_add(1, std::memory_order_relaxed);
      }
      data_->uses.fetch_add(1, std::memory_order_relaxed);
    }
  }

  rmw_ret_t status() const
  {
    return status_;
  }

  std::vector<std::uint8_t> & payload()
  {
    return data_ == nullptr ? local_payload_ : data_->payload;
  }

private:
  rmw_ret_t status_{RMW_RET_OK};
  rmw_fleetqox_cpp::MessageAllocationData * data_{nullptr};
  std::size_t capacity_before_{0};
  std::vector<std::uint8_t> local_payload_;
  std::unique_lock<std::mutex> lock_;
};

void maybe_renew_publisher_graph(FleetQoxPublisherData * data);

void send_publisher_graph_advertisement(const FleetQoxPublisherData * data, const char * action);

void record_liveliness_assert_locked(
  FleetQoxPublisherData * publisher,
  std::int64_t now_ns,
  std::vector<EventCallbackNotification> * callbacks);

rmw_ret_t publish_payload(FleetQoxPublisherData * data, const std::vector<std::uint8_t> & payload)
{
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("publisher data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!publish_allowed_by_security_policy(data->topic_name, data->enclave, data->domain_id)) {
    g_security_policy_denied.fetch_add(1, std::memory_order_relaxed);
    RMW_SET_ERROR_MSG("publish denied by FleetRMW security policy");
    return RMW_RET_ERROR;
  }
  std::lock_guard<std::mutex> publish_lock(data->publish_mutex);
  std::vector<EventCallbackNotification> deadline_callbacks;
  const auto source_sequence = data->next_source_sequence++;
  const std::int64_t now_ns = monotonic_timestamp_ns();
  const rmw_fleetqox_cpp::DataFrame frame{
    local_robot_id(),
    data->topic_name,
    data->publisher_id,
    source_sequence,
    now_ns,
    payload,
    data->domain_id,
    data->type_name};
  const std::string encoded_frame = rmw_fleetqox_cpp::encode_data_frame(frame);
  const bool reliable = data->qos.reliability == RMW_QOS_POLICY_RELIABILITY_RELIABLE;
  const std::vector<std::string> matched_subscription_ids = reliable ?
    rmw_fleetqox_cpp_graph_matched_subscription_endpoint_ids(
      data->domain_id, data->topic_name, data->type_name, data->qos) :
    std::vector<std::string>{};
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    record_offered_deadline_miss_locked(data, now_ns, &deadline_callbacks);
    data->last_publish_ns = now_ns;
    record_liveliness_assert_locked(data, now_ns, &deadline_callbacks);
    const size_t history_limit =
      data->qos.history == RMW_QOS_POLICY_HISTORY_KEEP_LAST ?
      std::max<size_t>(1, data->qos.depth) : 4096;
    size_t publisher_history_size = 0;
    for (auto it = g_retransmit_ledger.begin(); it != g_retransmit_ledger.end();) {
      const ReliableRetransmitEntry & entry = it->second;
      if (entry.publisher_id != data->publisher_id) {
        ++it;
        continue;
      }
      if (entry.acknowledged || frame_exceeds_lifespan(entry.qos, entry.source_timestamp_ns)) {
        it = g_retransmit_ledger.erase(it);
        continue;
      }
      ++publisher_history_size;
      ++it;
    }
    while (publisher_history_size >= history_limit) {
      auto oldest = g_retransmit_ledger.end();
      for (auto it = g_retransmit_ledger.begin(); it != g_retransmit_ledger.end(); ++it) {
        if (it->second.publisher_id == data->publisher_id &&
          (oldest == g_retransmit_ledger.end() ||
          it->second.source_sequence_number < oldest->second.source_sequence_number))
        {
          oldest = it;
        }
      }
      if (oldest == g_retransmit_ledger.end()) {
        break;
      }
      g_retransmit_ledger.erase(oldest);
      --publisher_history_size;
    }
    ReliableRetransmitEntry retransmit_entry{
      encoded_frame,
      data->qos,
      data->publisher_id,
      data->domain_id,
      source_sequence,
      frame.source_timestamp_ns,
      frame.source_timestamp_ns,
      0,
      reliable,
      // Not marking this acknowledged=true just because no subscriber is
      // matched *yet*: graph advertisement relay can lag publish by a
      // nontrivial amount (more so at higher peer counts), and marking the
      // entry acknowledged makes it eviction-eligible on this publisher's
      // very next send -- long before a delayed subscriber match or a
      // NACK-triggered repair retransmission could ever use it. A truly
      // subscriber-less reliable publisher still bounds fine via the
      // existing history_limit eviction just below.
      !reliable};
    retransmit_entry.expected_acknowledgments = matched_subscription_ids.size();
    retransmit_entry.pending_subscriber_ids.insert(
      matched_subscription_ids.begin(), matched_subscription_ids.end());
    g_retransmit_ledger[retransmit_ledger_key(data->publisher_id, source_sequence)] =
      std::move(retransmit_entry);
  }
  notify_event_callbacks(deadline_callbacks);
  if (data->qos.liveliness == RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC) {
    send_publisher_graph_advertisement(data, "liveliness_assert");
  }
  maybe_renew_publisher_graph(data);
  const rmw_ret_t send_ret =
    socket_transport().send_data_frame(encoded_frame, data->qos);
  if (send_ret != RMW_RET_OK) {
    record_fragment_async_send_failed(encoded_frame);
  }
  return send_ret;
}

int reliable_ack_timeout_ms()
{
  static const int timeout_ms = parse_nonnegative_int_env(
    "FLEETQOX_RMW_RELIABLE_ACK_TIMEOUT_MS", 0, 30000);
  return timeout_ms;
}

int reliable_max_timeout_retransmissions()
{
  static const int max_retransmissions = parse_nonnegative_int_env(
    "FLEETQOX_RMW_RELIABLE_MAX_RETRANSMISSIONS", 3, 100);
  return max_retransmissions;
}

int loss_resilient_fragment_chunk_bytes()
{
  static const int chunk_bytes = parse_nonnegative_int_env(
    "FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES", 0, 60000);
  return chunk_bytes;
}

int fragment_whole_fallback_interval_ms()
{
  static const int interval_ms = parse_nonnegative_int_env(
    "FLEETQOX_RMW_FRAGMENT_WHOLE_FALLBACK_INTERVAL_MS", 250, 60000);
  return interval_ms;
}

int fragment_whole_fallback_grace_ms()
{
  static const int grace_ms = parse_nonnegative_int_env(
    "FLEETQOX_RMW_FRAGMENT_WHOLE_FALLBACK_GRACE_MS", 1000, 60000);
  return grace_ms;
}

bool loss_resilient_fragment_mode_configured()
{
  return loss_resilient_fragment_chunk_bytes() > 0;
}

void reliable_retransmit_loop()
{
  const int timeout_ms = reliable_ack_timeout_ms();
  const int max_retransmissions = reliable_max_timeout_retransmissions();
  if (timeout_ms <= 0 || max_retransmissions <= 0) {
    return;
  }
  const auto poll_interval = std::chrono::milliseconds(
    std::max(5, std::min(50, timeout_ms / 4)));
  const std::int64_t timeout_ns = static_cast<std::int64_t>(timeout_ms) * 1000000ll;
  const bool pace_fragment_fallback =
    loss_resilient_fragment_mode_configured() &&
    fragment_whole_fallback_interval_ms() > 0;
  const std::int64_t fragment_fallback_interval_ns =
    static_cast<std::int64_t>(
    fragment_whole_fallback_interval_ms()) * 1000000ll;
  const std::int64_t fragment_fallback_grace_ns =
    static_cast<std::int64_t>(
    fragment_whole_fallback_grace_ms()) * 1000000ll;
  std::int64_t next_fragment_fallback_ns = 0;
  while (g_reliable_retransmit_running.load(std::memory_order_acquire)) {
    std::this_thread::sleep_for(poll_interval);
    if (!g_reliable_retransmit_running.load(std::memory_order_acquire)) {
      break;
    }
    const std::int64_t now = monotonic_timestamp_ns();
    std::vector<std::pair<std::string, rmw_qos_profile_t>> pending;
    bool fragment_fallback_scheduled = false;
    bool fragment_fallback_deferred = false;
    {
      std::lock_guard<std::mutex> lock(g_bus_mutex);
      for (auto it = g_retransmit_ledger.begin(); it != g_retransmit_ledger.end();) {
        ReliableRetransmitEntry & entry = it->second;
        if (entry.acknowledged || frame_exceeds_lifespan(entry.qos, entry.source_timestamp_ns)) {
          it = g_retransmit_ledger.erase(it);
          continue;
        }
        if (!entry.reliable ||
          entry.timeout_retransmissions >= static_cast<std::uint64_t>(max_retransmissions))
        {
          ++it;
          continue;
        }
        if (now - entry.last_send_ns < timeout_ns) {
          ++it;
          continue;
        }
        if (entry.fragment_initial_send_batches_pending > 0) {
          if (!entry.fragment_initial_pending_suppression_recorded) {
            g_fragment_initial_pending_timeout_suppressions.fetch_add(
              1, std::memory_order_relaxed);
            entry.fragment_initial_pending_suppression_recorded = true;
          }
          ++it;
          continue;
        }
        if (entry.fragment_observed_by_reader) {
          if (!entry.fragment_timeout_suppression_recorded) {
            g_fragment_observed_timeout_retransmissions_suppressed.fetch_add(
              1, std::memory_order_relaxed);
            entry.fragment_timeout_suppression_recorded = true;
          }
          ++it;
          continue;
        }
        if (pace_fragment_fallback &&
          now - entry.last_send_ns < fragment_fallback_grace_ns)
        {
          if (!entry.fragment_fallback_grace_deferral_recorded) {
            g_fragment_whole_fallback_grace_deferrals.fetch_add(
              1, std::memory_order_relaxed);
            entry.fragment_fallback_grace_deferral_recorded = true;
          }
          ++it;
          continue;
        }
        if (pace_fragment_fallback &&
          (fragment_fallback_scheduled ||
          now < next_fragment_fallback_ns))
        {
          fragment_fallback_deferred = true;
          ++it;
          continue;
        }
        entry.last_send_ns = now;
        ++entry.timeout_retransmissions;
        entry.fragment_initial_pending_suppression_recorded = false;
        entry.fragment_fallback_grace_deferral_recorded = false;
        pending.emplace_back(entry.encoded_frame, entry.qos);
        if (pace_fragment_fallback) {
          fragment_fallback_scheduled = true;
          next_fragment_fallback_ns =
            now + fragment_fallback_interval_ns;
        }
        ++it;
      }
    }
    if (fragment_fallback_deferred) {
      g_fragment_whole_fallback_pacing_deferrals.fetch_add(
        1, std::memory_order_relaxed);
    }
    for (const auto & retransmission : pending) {
      if (socket_transport().send_data_frame(
          retransmission.first, retransmission.second) == RMW_RET_OK)
      {
        g_reliable_timeout_retransmissions.fetch_add(1, std::memory_order_relaxed);
      } else {
        record_fragment_async_send_failed(retransmission.first);
      }
    }
  }
}

void stop_reliable_retransmit_thread()
{
  std::lock_guard<std::mutex> lifecycle_lock(g_reliable_retransmit_lifecycle_mutex);
  g_reliable_retransmit_running.store(false, std::memory_order_release);
  if (g_reliable_retransmit_thread.joinable()) {
    g_reliable_retransmit_thread.join();
  }
  g_reliable_retransmit_started.store(false, std::memory_order_release);
}

void ensure_reliable_retransmit_thread()
{
  if (reliable_ack_timeout_ms() <= 0 || reliable_max_timeout_retransmissions() <= 0) {
    return;
  }
  std::lock_guard<std::mutex> lifecycle_lock(g_reliable_retransmit_lifecycle_mutex);
  if (g_reliable_retransmit_started.load(std::memory_order_acquire)) {
    return;
  }
  if (g_reliable_retransmit_thread.joinable()) {
    g_reliable_retransmit_thread.join();
  }
  g_reliable_retransmit_running.store(true, std::memory_order_release);
  g_reliable_retransmit_started.store(true, std::memory_order_release);
  g_reliable_retransmit_thread = std::thread(reliable_retransmit_loop);
  std::call_once(g_reliable_retransmit_atexit_once, []() {
    std::atexit(stop_reliable_retransmit_thread);
  });
}

int qos_deadline_monitor_interval_ms()
{
  static const int interval_ms = parse_nonnegative_int_env(
    "FLEETQOX_RMW_QOS_DEADLINE_MONITOR_MS", 1, 1000);
  return std::max(1, interval_ms);
}

int message_lost_gap_grace_ms()
{
  static const int grace_ms = parse_nonnegative_int_env(
    "FLEETQOX_RMW_MESSAGE_LOST_GAP_GRACE_MS", 25, 60000);
  return grace_ms;
}

void append_pending_missing_range(
  std::vector<rmw_fleetqox_cpp::TimedMissingSequenceRange> * ranges,
  std::uint64_t first,
  std::uint64_t last,
  std::int64_t first_observed_ns)
{
  if (ranges == nullptr || first == 0 || first > last) {
    return;
  }
  if (!ranges->empty()) {
    auto & previous = ranges->back();
    if (previous.first_observed_ns == first_observed_ns &&
      previous.last != std::numeric_limits<std::uint64_t>::max() &&
      previous.last + 1 == first)
    {
      previous.last = last;
      return;
    }
  }
  ranges->push_back(
    rmw_fleetqox_cpp::TimedMissingSequenceRange{first, last, first_observed_ns});
}

void track_best_effort_sequence_gaps_locked(
  rmw_fleetqox_cpp::SequenceState * state,
  const rmw_fleetqox_cpp::AckNackFeedback & feedback,
  std::int64_t receive_ns)
{
  if (state == nullptr) {
    return;
  }
  const auto previous_ranges = state->pending_missing_ranges;
  std::vector<rmw_fleetqox_cpp::TimedMissingSequenceRange> updated_ranges;
  updated_ranges.reserve(previous_ranges.size() + feedback.missing_sequence_ranges.size());
  for (const auto & missing : feedback.missing_sequence_ranges) {
    std::uint64_t cursor = missing.first;
    bool complete = false;
    for (const auto & previous : previous_ranges) {
      if (previous.last < cursor) {
        continue;
      }
      if (previous.first > missing.second) {
        break;
      }
      if (previous.first > cursor) {
        append_pending_missing_range(
          &updated_ranges, cursor, std::min(missing.second, previous.first - 1), receive_ns);
      }
      const std::uint64_t overlap_first = std::max(cursor, previous.first);
      const std::uint64_t overlap_last = std::min(missing.second, previous.last);
      if (overlap_first <= overlap_last) {
        append_pending_missing_range(
          &updated_ranges, overlap_first, overlap_last, previous.first_observed_ns);
      }
      if (overlap_last >= missing.second ||
        overlap_last == std::numeric_limits<std::uint64_t>::max())
      {
        complete = true;
        break;
      }
      cursor = overlap_last + 1;
    }
    if (!complete && cursor <= missing.second) {
      append_pending_missing_range(
        &updated_ranges, cursor, missing.second, receive_ns);
    }
  }
  state->pending_missing_ranges = std::move(updated_ranges);
}

size_t missing_sequence_range_count(
  const rmw_fleetqox_cpp::TimedMissingSequenceRange & range)
{
  if (range.first == 0 || range.first > range.last) {
    return 0;
  }
  const std::uint64_t span = range.last - range.first;
  if (span >= static_cast<std::uint64_t>(std::numeric_limits<size_t>::max())) {
    return std::numeric_limits<size_t>::max();
  }
  return static_cast<size_t>(span + 1);
}

size_t finalize_best_effort_sequence_gaps_locked(
  FleetQoxSubscriptionData * subscription,
  std::int64_t now_ns)
{
  if (subscription == nullptr ||
    subscription->qos.reliability != RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT)
  {
    return 0;
  }
  const std::int64_t grace_ns =
    static_cast<std::int64_t>(message_lost_gap_grace_ms()) * 1000000ll;
  size_t lost_count = 0;
  for (auto & item : subscription->sequence_states) {
    auto & state = item.second;
    auto & pending = state.pending_missing_ranges;
    while (!pending.empty()) {
      auto & first = pending.front();
      if (first.last <= state.highest_contiguous_sequence) {
        pending.erase(pending.begin());
        continue;
      }
      if (first.first <= state.highest_contiguous_sequence) {
        if (state.highest_contiguous_sequence ==
          std::numeric_limits<std::uint64_t>::max())
        {
          pending.clear();
          break;
        }
        first.first = state.highest_contiguous_sequence + 1;
      }
      if (state.highest_contiguous_sequence ==
        std::numeric_limits<std::uint64_t>::max() ||
        first.first != state.highest_contiguous_sequence + 1)
      {
        break;
      }
      if (now_ns < first.first_observed_ns ||
        now_ns - first.first_observed_ns < grace_ns)
      {
        break;
      }
      lost_count = saturating_size_add(lost_count, missing_sequence_range_count(first));
      state.highest_contiguous_sequence = first.last;
      pending.erase(pending.begin());
      while (state.highest_contiguous_sequence !=
        std::numeric_limits<std::uint64_t>::max() &&
        state.observed_sequences.find(state.highest_contiguous_sequence + 1) !=
        state.observed_sequences.end())
      {
        ++state.highest_contiguous_sequence;
      }
    }
  }
  return lost_count;
}

bool qos_deadline_enabled(const rmw_qos_profile_t & qos)
{
  return qos_duration_ns(qos.deadline) > 0;
}

bool qos_liveliness_policy_supported(rmw_qos_liveliness_policy_t policy)
{
  return policy == RMW_QOS_POLICY_LIVELINESS_SYSTEM_DEFAULT ||
         policy == RMW_QOS_POLICY_LIVELINESS_AUTOMATIC ||
         policy == RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC;
}

bool qos_liveliness_enabled(const rmw_qos_profile_t & qos)
{
  // A default (zero) lease is non-expiring, not disabled.  It must still
  // contribute alive/remove transitions to RMW_EVENT_LIVELINESS_CHANGED.
  return qos_liveliness_policy_supported(qos.liveliness);
}

bool qos_liveliness_automatic(const rmw_qos_profile_t & qos)
{
  return qos.liveliness == RMW_QOS_POLICY_LIVELINESS_AUTOMATIC ||
         qos.liveliness == RMW_QOS_POLICY_LIVELINESS_SYSTEM_DEFAULT ||
         qos.liveliness == RMW_QOS_POLICY_LIVELINESS_BEST_AVAILABLE;
}

bool qos_liveliness_manual_by_topic(const rmw_qos_profile_t & qos)
{
  return qos.liveliness == RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC;
}

size_t liveliness_changed_pending_count(
  std::int32_t alive_count_change,
  std::int32_t not_alive_count_change)
{
  const std::int64_t alive_abs = alive_count_change < 0 ?
    -static_cast<std::int64_t>(alive_count_change) :
    static_cast<std::int64_t>(alive_count_change);
  const std::int64_t not_alive_abs = not_alive_count_change < 0 ?
    -static_cast<std::int64_t>(not_alive_count_change) :
    static_cast<std::int64_t>(not_alive_count_change);
  return static_cast<size_t>(alive_abs + not_alive_abs);
}

void record_subscription_liveliness_change_locked(
  FleetQoxSubscriptionData * subscription,
  const std::string & publisher_id,
  bool publisher_alive,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (subscription == nullptr || callbacks == nullptr || publisher_id.empty()) {
    return;
  }
  std::int64_t alive_delta = 0;
  std::int64_t not_alive_delta = 0;
  if (publisher_alive) {
    const bool was_not_alive =
      subscription->liveliness_not_alive_publishers.erase(publisher_id) > 0;
    const bool inserted_alive =
      subscription->liveliness_alive_publishers.insert(publisher_id).second;
    alive_delta += inserted_alive ? 1 : 0;
    not_alive_delta -= was_not_alive ? 1 : 0;
  } else {
    const bool was_alive =
      subscription->liveliness_alive_publishers.erase(publisher_id) > 0;
    const bool inserted_not_alive =
      subscription->liveliness_not_alive_publishers.insert(publisher_id).second;
    alive_delta -= was_alive ? 1 : 0;
    not_alive_delta += inserted_not_alive ? 1 : 0;
  }
  if (alive_delta == 0 && not_alive_delta == 0) {
    return;
  }
  subscription->liveliness_alive_count_change =
    saturating_i32_add(subscription->liveliness_alive_count_change, alive_delta);
  subscription->liveliness_not_alive_count_change =
    saturating_i32_add(subscription->liveliness_not_alive_count_change, not_alive_delta);
  const size_t pending = liveliness_changed_pending_count(
    subscription->liveliness_alive_count_change,
    subscription->liveliness_not_alive_count_change);
  if (subscription->liveliness_changed_callback != nullptr && pending > 0) {
    queue_event_callback_locked(
      callbacks,
      subscription->liveliness_changed_callback,
      subscription->liveliness_changed_user_data,
      pending,
      nullptr,
      subscription);
  }
}

void record_subscription_liveliness_remove_locked(
  FleetQoxSubscriptionData * subscription,
  const std::string & publisher_id,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (subscription == nullptr || callbacks == nullptr || publisher_id.empty()) {
    return;
  }
  std::int64_t alive_delta = 0;
  std::int64_t not_alive_delta = 0;
  alive_delta -= subscription->liveliness_alive_publishers.erase(publisher_id) > 0 ? 1 : 0;
  not_alive_delta -=
    subscription->liveliness_not_alive_publishers.erase(publisher_id) > 0 ? 1 : 0;
  if (alive_delta == 0 && not_alive_delta == 0) {
    return;
  }
  subscription->liveliness_alive_count_change =
    saturating_i32_add(subscription->liveliness_alive_count_change, alive_delta);
  subscription->liveliness_not_alive_count_change =
    saturating_i32_add(subscription->liveliness_not_alive_count_change, not_alive_delta);
  const size_t pending = liveliness_changed_pending_count(
    subscription->liveliness_alive_count_change,
    subscription->liveliness_not_alive_count_change);
  if (subscription->liveliness_changed_callback != nullptr && pending > 0) {
    queue_event_callback_locked(
      callbacks,
      subscription->liveliness_changed_callback,
      subscription->liveliness_changed_user_data,
      pending,
      nullptr,
      subscription);
  }
}

void record_liveliness_lost_locked(
  FleetQoxPublisherData * publisher,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (publisher == nullptr || callbacks == nullptr || !publisher->liveliness_alive) {
    return;
  }
  publisher->liveliness_alive = false;
  publisher->liveliness_lost_total_count =
    saturating_i32_add(publisher->liveliness_lost_total_count, 1);
  publisher->liveliness_lost_total_count_change =
    saturating_i32_add(publisher->liveliness_lost_total_count_change, 1);
  if (publisher->liveliness_lost_callback != nullptr) {
    queue_event_callback_locked(
      callbacks,
      publisher->liveliness_lost_callback,
      publisher->liveliness_lost_user_data,
      static_cast<size_t>(publisher->liveliness_lost_total_count_change),
      publisher);
  }
  for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
    if (local_pubsub_match_compatible(publisher, subscription)) {
      record_subscription_liveliness_change_locked(
        subscription, publisher->publisher_id, false, callbacks);
    }
  }
}

void record_liveliness_assert_locked(
  FleetQoxPublisherData * publisher,
  std::int64_t now_ns,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (publisher == nullptr || callbacks == nullptr || !qos_liveliness_enabled(publisher->qos)) {
    return;
  }
  const bool was_alive = publisher->liveliness_alive;
  publisher->last_liveliness_assert_ns = now_ns;
  publisher->liveliness_alive = true;
  if (was_alive) {
    return;
  }
  for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
    if (local_pubsub_match_compatible(publisher, subscription)) {
      record_subscription_liveliness_change_locked(
        subscription, publisher->publisher_id, true, callbacks);
    }
  }
}

void record_liveliness_for_new_publisher_locked(
  FleetQoxPublisherData * publisher,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (publisher == nullptr || callbacks == nullptr || !qos_liveliness_enabled(publisher->qos)) {
    return;
  }
  for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
    if (local_pubsub_match_compatible(publisher, subscription)) {
      record_subscription_liveliness_change_locked(
        subscription, publisher->publisher_id, publisher->liveliness_alive, callbacks);
    }
  }
}

void record_liveliness_for_new_subscription_locked(
  FleetQoxSubscriptionData * subscription,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (subscription == nullptr || callbacks == nullptr) {
    return;
  }
  for (FleetQoxPublisherData * publisher : g_publishers) {
    if (publisher != nullptr &&
      qos_liveliness_enabled(publisher->qos) &&
      local_pubsub_match_compatible(publisher, subscription))
    {
      record_subscription_liveliness_change_locked(
        subscription, publisher->publisher_id, publisher->liveliness_alive, callbacks);
    }
  }
  for (const auto & item : g_remote_pubsub_endpoints) {
    const RemotePubSubEndpoint & publisher = item.second;
    if (qos_liveliness_enabled(publisher.qos) &&
      remote_publisher_match_compatible(publisher, subscription))
    {
      record_subscription_liveliness_change_locked(
        subscription,
        remote_liveliness_publisher_id(publisher),
        publisher.liveliness_alive,
        callbacks);
    }
  }
}

std::int64_t remote_graph_endpoint_expiry_ns(
  std::int64_t now_ns,
  std::uint64_t lease_ms)
{
  constexpr std::uint64_t kDefaultLeaseMs = 5000;
  const std::uint64_t effective_lease_ms = lease_ms == 0 ? kDefaultLeaseMs : lease_ms;
  const std::uint64_t max_delta_ns = static_cast<std::uint64_t>(
    std::numeric_limits<std::int64_t>::max() - std::max<std::int64_t>(now_ns, 0));
  if (effective_lease_ms > max_delta_ns / 1000000u) {
    return std::numeric_limits<std::int64_t>::max();
  }
  return now_ns + static_cast<std::int64_t>(effective_lease_ms * 1000000u);
}

void record_remote_endpoint_discovered_locked(
  const RemotePubSubEndpoint & endpoint,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (callbacks == nullptr) {
    return;
  }
  if (endpoint.publisher) {
    for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
      if (subscription == nullptr || subscription->domain_id != endpoint.domain_id ||
        subscription->topic_name != endpoint.topic_name)
      {
        continue;
      }
      const rmw_qos_policy_kind_t policy_kind =
        incompatible_qos_policy_kind(endpoint.qos, subscription->qos);
      if (policy_kind != RMW_QOS_POLICY_INVALID) {
        record_requested_qos_incompatible_locked(subscription, policy_kind, callbacks);
      }
      if (subscription->type_name != endpoint.type_name) {
        record_subscription_incompatible_type_locked(subscription, callbacks);
      }
      if (qos_liveliness_enabled(endpoint.qos) &&
        remote_publisher_match_compatible(endpoint, subscription))
      {
        record_subscription_liveliness_change_locked(
          subscription,
          remote_liveliness_publisher_id(endpoint),
          endpoint.liveliness_alive,
          callbacks);
      }
    }
  } else {
    for (FleetQoxPublisherData * publisher : g_publishers) {
      if (publisher == nullptr || publisher->domain_id != endpoint.domain_id ||
        publisher->topic_name != endpoint.topic_name)
      {
        continue;
      }
      const rmw_qos_policy_kind_t policy_kind =
        incompatible_qos_policy_kind(publisher->qos, endpoint.qos);
      if (policy_kind != RMW_QOS_POLICY_INVALID) {
        record_offered_qos_incompatible_locked(publisher, policy_kind, callbacks);
      }
      if (publisher->type_name != endpoint.type_name) {
        record_publisher_incompatible_type_locked(publisher, callbacks);
      }
    }
  }
  refresh_publication_matched_events_locked(callbacks);
  refresh_subscription_matched_events_locked(callbacks);
}

void record_remote_endpoint_removed_locked(
  const RemotePubSubEndpoint & endpoint,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (callbacks == nullptr) {
    return;
  }
  if (endpoint.publisher && qos_liveliness_enabled(endpoint.qos)) {
    for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
      if (remote_publisher_match_compatible(endpoint, subscription)) {
        record_subscription_liveliness_remove_locked(
          subscription, remote_liveliness_publisher_id(endpoint), callbacks);
      }
    }
  }
}

void record_remote_publisher_liveliness_state_locked(
  RemotePubSubEndpoint * endpoint,
  bool alive,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (endpoint == nullptr || callbacks == nullptr || !endpoint->publisher ||
    !qos_liveliness_enabled(endpoint->qos) || endpoint->liveliness_alive == alive)
  {
    return;
  }
  endpoint->liveliness_alive = alive;
  for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
    if (remote_publisher_match_compatible(*endpoint, subscription)) {
      record_subscription_liveliness_change_locked(
        subscription, remote_liveliness_publisher_id(*endpoint), alive, callbacks);
    }
  }
}

void expire_remote_manual_liveliness_locked(
  std::int64_t now_ns,
  std::vector<EventCallbackNotification> * callbacks)
{
  if (callbacks == nullptr) {
    return;
  }
  for (auto & item : g_remote_pubsub_endpoints) {
    RemotePubSubEndpoint & endpoint = item.second;
    if (!endpoint.publisher || !endpoint.liveliness_alive ||
      !qos_liveliness_manual_by_topic(endpoint.qos) ||
      endpoint.last_liveliness_assert_ns <= 0)
    {
      continue;
    }
    const std::int64_t lease_ns = qos_duration_ns(endpoint.qos.liveliness_lease_duration);
    if (lease_ns > 0 && now_ns > endpoint.last_liveliness_assert_ns &&
      now_ns - endpoint.last_liveliness_assert_ns > lease_ns)
    {
      record_remote_publisher_liveliness_state_locked(&endpoint, false, callbacks);
      g_remote_manual_liveliness_expiries.fetch_add(1, std::memory_order_relaxed);
    }
  }
}

bool purge_expired_remote_pubsub_endpoints_locked(
  std::int64_t now_ns,
  std::vector<EventCallbackNotification> * callbacks)
{
  bool changed = false;
  for (auto it = g_remote_pubsub_endpoints.begin(); it != g_remote_pubsub_endpoints.end();) {
    if (it->second.expires_at_ns > now_ns) {
      ++it;
      continue;
    }
    record_remote_endpoint_removed_locked(it->second, callbacks);
    it = g_remote_pubsub_endpoints.erase(it);
    g_remote_graph_event_endpoint_expiries.fetch_add(1, std::memory_order_relaxed);
    changed = true;
  }
  if (changed) {
    refresh_publication_matched_events_locked(callbacks);
    refresh_subscription_matched_events_locked(callbacks);
    g_all_acked_condition.notify_all();
  }
  return changed;
}

bool apply_remote_pubsub_event_advertisement(
  const rmw_fleetqox_cpp::GraphAdvertisement & advertisement,
  const rmw_qos_profile_t & qos)
{
  const bool is_publisher = advertisement.entity_kind == "publisher";
  const bool is_subscription = advertisement.entity_kind == "subscription";
  if ((!is_publisher && !is_subscription) || advertisement.endpoint_id.empty()) {
    return false;
  }
  const bool is_add = advertisement.action == "add";
  const bool is_remove = advertisement.action == "remove";
  const bool is_liveliness_assert = advertisement.action == "liveliness_assert";
  if (!is_add && !is_remove && !is_liveliness_assert) {
    return false;
  }
  if (is_liveliness_assert &&
    (!is_publisher || !qos_liveliness_manual_by_topic(qos)))
  {
    return false;
  }

  g_remote_graph_event_advertisements_received.fetch_add(1, std::memory_order_relaxed);
  std::vector<EventCallbackNotification> callbacks;
  const std::int64_t now_ns = monotonic_timestamp_ns();
  const std::string key = remote_pubsub_endpoint_key(
    is_publisher, advertisement.endpoint_id, advertisement.domain_id);
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    purge_expired_remote_pubsub_endpoints_locked(now_ns, &callbacks);
    expire_remote_manual_liveliness_locked(now_ns, &callbacks);
    auto found = g_remote_pubsub_endpoints.find(key);
    if (is_remove) {
      if (found != g_remote_pubsub_endpoints.end()) {
        record_remote_endpoint_removed_locked(found->second, &callbacks);
        g_remote_pubsub_endpoints.erase(found);
        refresh_publication_matched_events_locked(&callbacks);
        refresh_subscription_matched_events_locked(&callbacks);
        g_remote_graph_event_endpoint_removes.fetch_add(1, std::memory_order_relaxed);
      }
    } else {
      RemotePubSubEndpoint incoming{
        is_publisher,
        advertisement.domain_id,
        advertisement.topic,
        advertisement.type_name,
        advertisement.endpoint_id,
        qos,
        remote_graph_endpoint_expiry_ns(now_ns, advertisement.lease_ms)};
      incoming.last_liveliness_assert_ns = now_ns;
      incoming.liveliness_alive = is_publisher && qos_liveliness_enabled(qos);
      if (found != g_remote_pubsub_endpoints.end() &&
        remote_endpoint_descriptor_equal(found->second, incoming))
      {
        found->second.expires_at_ns = incoming.expires_at_ns;
        if (is_liveliness_assert) {
          const bool was_alive = found->second.liveliness_alive;
          found->second.last_liveliness_assert_ns = now_ns;
          record_remote_publisher_liveliness_state_locked(
            &found->second, true, &callbacks);
          g_remote_manual_liveliness_assertions_received.fetch_add(
            1, std::memory_order_relaxed);
          if (!was_alive && found->second.liveliness_alive) {
            g_remote_manual_liveliness_reassertions.fetch_add(
              1, std::memory_order_relaxed);
          }
        } else {
          if (qos_liveliness_automatic(found->second.qos)) {
            found->second.last_liveliness_assert_ns = now_ns;
            record_remote_publisher_liveliness_state_locked(
              &found->second, true, &callbacks);
          }
          g_remote_graph_event_endpoint_renewals.fetch_add(1, std::memory_order_relaxed);
        }
      } else {
        if (found != g_remote_pubsub_endpoints.end()) {
          record_remote_endpoint_removed_locked(found->second, &callbacks);
          g_remote_pubsub_endpoints.erase(found);
          refresh_publication_matched_events_locked(&callbacks);
          refresh_subscription_matched_events_locked(&callbacks);
        }
        const auto inserted = g_remote_pubsub_endpoints.emplace(key, incoming);
        record_remote_endpoint_discovered_locked(inserted.first->second, &callbacks);
        g_remote_graph_event_endpoint_adds.fetch_add(1, std::memory_order_relaxed);
        if (is_liveliness_assert) {
          g_remote_manual_liveliness_assertions_received.fetch_add(
            1, std::memory_order_relaxed);
        }
      }
    }
  }
  notify_event_callbacks(callbacks);
  g_all_acked_condition.notify_all();
  return is_add || is_liveliness_assert;
}

void qos_deadline_monitor_loop()
{
  const auto poll_interval = std::chrono::milliseconds(qos_deadline_monitor_interval_ms());
  while (g_qos_deadline_monitor_running.load(std::memory_order_acquire)) {
    std::this_thread::sleep_for(poll_interval);
    if (!g_qos_deadline_monitor_running.load(std::memory_order_acquire)) {
      break;
    }
    const std::int64_t now = monotonic_timestamp_ns();
    std::vector<EventCallbackNotification> callbacks;
    {
      std::lock_guard<std::mutex> lock(g_bus_mutex);
      purge_expired_remote_pubsub_endpoints_locked(now, &callbacks);
      expire_remote_manual_liveliness_locked(now, &callbacks);
      for (FleetQoxPublisherData * publisher : g_publishers) {
        if (publisher == nullptr || publisher->last_publish_ns <= 0 ||
          !qos_deadline_enabled(publisher->qos))
        {
          continue;
        }
        const std::int32_t missed =
          record_offered_deadline_miss_locked(publisher, now, &callbacks);
        advance_deadline_anchor_after_miss(
          &publisher->last_publish_ns, publisher->qos, missed, now);
      }
      for (FleetQoxPublisherData * publisher : g_publishers) {
        if (publisher == nullptr || !publisher->liveliness_alive ||
          !qos_liveliness_enabled(publisher->qos) ||
          publisher->last_liveliness_assert_ns <= 0)
        {
          continue;
        }
        if (qos_liveliness_automatic(publisher->qos)) {
          // AUTOMATIC liveliness is asserted by the middleware while the local
          // publisher exists; application silence must not expire the lease.
          publisher->last_liveliness_assert_ns = now;
          continue;
        }
        const std::int64_t lease_ns = qos_duration_ns(publisher->qos.liveliness_lease_duration);
        if (lease_ns > 0 &&
          now > publisher->last_liveliness_assert_ns &&
          now - publisher->last_liveliness_assert_ns > lease_ns)
        {
          record_liveliness_lost_locked(publisher, &callbacks);
        }
      }
      for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
        if (subscription == nullptr) {
          continue;
        }
        record_subscription_message_lost_locked(
          subscription,
          finalize_best_effort_sequence_gaps_locked(subscription, now),
          &callbacks);
        if (subscription->last_received_ns <= 0 ||
          !qos_deadline_enabled(subscription->qos))
        {
          continue;
        }
        const std::int32_t missed =
          record_requested_deadline_miss_locked(subscription, now, &callbacks);
        advance_deadline_anchor_after_miss(
          &subscription->last_received_ns, subscription->qos, missed, now);
      }
    }
    notify_event_callbacks(callbacks);
  }
}

void stop_qos_deadline_monitor_thread()
{
  std::lock_guard<std::mutex> lifecycle_lock(g_qos_deadline_monitor_lifecycle_mutex);
  g_qos_deadline_monitor_running.store(false, std::memory_order_release);
  if (g_qos_deadline_monitor_thread.joinable()) {
    g_qos_deadline_monitor_thread.join();
  }
  g_qos_deadline_monitor_started.store(false, std::memory_order_release);
}

void ensure_qos_deadline_monitor_thread()
{
  std::lock_guard<std::mutex> lifecycle_lock(g_qos_deadline_monitor_lifecycle_mutex);
  if (g_qos_deadline_monitor_started.load(std::memory_order_acquire)) {
    return;
  }
  if (g_qos_deadline_monitor_thread.joinable()) {
    g_qos_deadline_monitor_thread.join();
  }
  g_qos_deadline_monitor_running.store(true, std::memory_order_release);
  g_qos_deadline_monitor_started.store(true, std::memory_order_release);
  g_qos_deadline_monitor_thread = std::thread(qos_deadline_monitor_loop);
  std::call_once(g_qos_deadline_monitor_atexit_once, []() {
    std::atexit(stop_qos_deadline_monitor_thread);
  });
}

int repair_nack_interval_ms()
{
  static const int interval_ms = parse_nonnegative_int_env(
    "FLEETQOX_RMW_REPAIR_NACK_INTERVAL_MS", 75, 5000);
  return interval_ms;
}

std::optional<rmw_fleetqox_cpp::DataFrame> repair_marker_frame_from_stream_key(
  const std::string & key,
  std::uint64_t highest_observed_sequence,
  std::int64_t timestamp_ns)
{
  const std::vector<std::string> parts = split_nonempty(key, '|');
  if (parts.size() != 3) {
    return std::nullopt;
  }
  return rmw_fleetqox_cpp::DataFrame{
    parts[0],
    parts[1],
    parts[2],
    highest_observed_sequence,
    timestamp_ns,
    {},
    0,
    {}};
}

std::vector<std::string> idle_repair_ack_nacks(FleetQoxSubscriptionData * data)
{
  std::vector<std::string> payloads;
  if (data == nullptr ||
    data->qos.reliability == RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT)
  {
    return payloads;
  }
  const std::int64_t now = monotonic_timestamp_ns();
  const std::int64_t min_interval_ns =
    static_cast<std::int64_t>(repair_nack_interval_ms()) * 1000000ll;
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  for (auto & entry : data->sequence_states) {
    rmw_fleetqox_cpp::SequenceState & state = entry.second;
    if (!state.initialized ||
      state.highest_contiguous_sequence >= state.highest_observed_sequence)
    {
      continue;
    }
    if (state.last_repair_request_ns > 0 &&
      now - state.last_repair_request_ns < min_interval_ns)
    {
      continue;
    }
    auto marker = repair_marker_frame_from_stream_key(
      entry.first,
      state.highest_observed_sequence,
      now);
    if (!marker) {
      continue;
    }
    marker->domain_id = data->domain_id;
    const rmw_fleetqox_cpp::AckNackFeedback feedback =
      rmw_fleetqox_cpp::feedback_from_sequence_state(state);
    if (feedback.missing_sequence_ranges.empty()) {
      continue;
    }
    state.last_repair_request_ns = now;
    payloads.push_back(
      rmw_fleetqox_cpp::encode_ack_nack(*marker, feedback, data->endpoint_id));
  }
  return payloads;
}

void maybe_send_idle_repair_ack_nacks(FleetQoxSubscriptionData * data)
{
  const std::vector<std::string> payloads = idle_repair_ack_nacks(data);
  for (const std::string & payload : payloads) {
    const rmw_ret_t ret = socket_transport().send_ack_nack(payload);
    if (ret == RMW_RET_OK) {
      g_idle_repair_ack_nack_sent.fetch_add(1, std::memory_order_relaxed);
    }
    (void)ret;
  }
}

rmw_ret_t maybe_receive_quic_gateway_frame_for_take(
  const FleetQoxSubscriptionData * data,
  bool * received)
{
  if (received == nullptr) {
    RMW_SET_ERROR_MSG("received output must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *received = false;
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!quic_gateway_take_on_demand_enabled() || !socket_transport().quic_gateway_enabled()) {
    return RMW_RET_OK;
  }

  std::string encoded_frame;
  const rmw_ret_t receive_ret = socket_transport().receive_quic_gateway_payload(&encoded_frame);
  if (receive_ret == RMW_RET_UNSUPPORTED) {
    return RMW_RET_OK;
  }
  if (receive_ret != RMW_RET_OK) {
    return receive_ret;
  }
  if (encoded_frame.empty()) {
    return RMW_RET_OK;
  }
  const auto decoded_frame = rmw_fleetqox_cpp::decode_data_frame(encoded_frame);
  if (!decoded_frame) {
    RMW_SET_ERROR_MSG("failed to decode FleetRMW data frame received through QUIC gateway take path");
    return RMW_RET_ERROR;
  }
  enqueue_received_frame(encoded_frame);
  *received = true;
  if (trace_take_enabled()) {
    std::fprintf(
      stderr,
      "fleetqox quic_take topic=%s target_subscription=%s\n",
      decoded_frame->topic.c_str(),
      data->topic_name.c_str());
  }
  return RMW_RET_OK;
}

rmw_ret_t take_payload(
  FleetQoxSubscriptionData * data,
  std::vector<std::uint8_t> * payload,
  bool * taken,
  rmw_message_info_t * message_info = nullptr)
{
  if (data == nullptr || payload == nullptr || taken == nullptr) {
    RMW_SET_ERROR_MSG("subscription data, payload, and taken must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *taken = false;
  if (message_info == nullptr) {
    message_info = g_typed_take_message_info;
  }
  bool quic_gateway_take_polled = false;
  while (true) {
    std::string encoded_frame;
    {
      std::lock_guard<std::mutex> lock(g_bus_mutex);
      if (data->frame_queue.empty()) {
        encoded_frame.clear();
      } else {
        encoded_frame = std::move(data->frame_queue.front());
        data->frame_queue.pop_front();
      }
    }
    if (encoded_frame.empty()) {
      if (!quic_gateway_take_polled) {
        quic_gateway_take_polled = true;
        bool received = false;
        const rmw_ret_t receive_ret = maybe_receive_quic_gateway_frame_for_take(data, &received);
        if (receive_ret != RMW_RET_OK) {
          return receive_ret;
        }
        if (received) {
          continue;
        }
      }
      maybe_send_idle_repair_ack_nacks(data);
      return RMW_RET_OK;
    }
    const auto decoded_frame = rmw_fleetqox_cpp::decode_data_frame(encoded_frame);
    if (!decoded_frame) {
      RMW_SET_ERROR_MSG("failed to decode FleetRMW data frame from subscription queue");
      return RMW_RET_ERROR;
    }
    if (frame_exceeds_lifespan(data->qos, decoded_frame->source_timestamp_ns)) {
      continue;
    }
    {
      std::lock_guard<std::mutex> lock(g_last_take_mutex);
      g_last_take_topic = decoded_frame->topic;
      g_last_take_publisher_id = decoded_frame->publisher_id;
      g_last_take_source_sequence = decoded_frame->source_sequence_number;
      g_last_take_source_timestamp_ns = decoded_frame->source_timestamp_ns;
      g_last_take_timestamp_ns = monotonic_timestamp_ns();
    }
    if (message_info != nullptr) {
      *message_info = rmw_get_zero_initialized_message_info();
      message_info->source_timestamp = decoded_frame->source_timestamp_ns;
      message_info->received_timestamp = monotonic_timestamp_ns();
      message_info->publication_sequence_number =
        decoded_frame->source_sequence_number;
      {
        std::lock_guard<std::mutex> lock(g_bus_mutex);
        message_info->reception_sequence_number = data->next_reception_sequence++;
      }
      message_info->publisher_gid.implementation_identifier = kIdentifier;
      const auto publisher_gid = make_endpoint_gid(
        endpoint_id_for_local_id(decoded_frame->publisher_id));
      std::copy(
        publisher_gid.begin(), publisher_gid.end(),
        message_info->publisher_gid.data);
      message_info->from_intra_process = false;
    }
    *payload = decoded_frame->serialized_payload;
    *taken = true;
    return RMW_RET_OK;
  }
}

void send_publisher_graph_advertisement(const FleetQoxPublisherData * data, const char * action)
{
  if (data == nullptr || action == nullptr) {
    return;
  }
  const rmw_ret_t graph_advertisement_ret =
    socket_transport().send_graph_advertisement(
      action,
      "publisher",
      data->node_name,
      data->node_namespace,
      data->topic_name,
      data->type_name,
      data->endpoint_id,
      data->endpoint_gid,
      data->qos,
      data->domain_id);
  (void)graph_advertisement_ret;
}

void send_subscription_graph_advertisement(const FleetQoxSubscriptionData * data, const char * action)
{
  if (data == nullptr || action == nullptr) {
    return;
  }
  const rmw_ret_t graph_advertisement_ret =
    socket_transport().send_graph_advertisement(
      action,
      "subscription",
      data->node_name,
      data->node_namespace,
      data->topic_name,
      data->type_name,
      data->endpoint_id,
      data->endpoint_gid,
      data->qos,
      data->domain_id);
  (void)graph_advertisement_ret;
  if (std::strcmp(action, "add") == 0) {
    const rmw_ret_t advertisement_ret =
      socket_transport().send_subscription_advertisement(
        data->topic_name, data->type_name, data->domain_id);
    (void)advertisement_ret;
  }
}

void pubsub_graph_renewal_loop()
{
  const auto renew_interval = std::chrono::milliseconds(std::max(
      100,
      parse_nonnegative_int_env("FLEETQOX_RMW_GRAPH_RENEW_INTERVAL_MS", 500, 4000)));
  while (g_pubsub_graph_renewal_running.load(std::memory_order_acquire)) {
    std::this_thread::sleep_for(renew_interval);
    if (!g_pubsub_graph_renewal_running.load(std::memory_order_acquire)) {
      break;
    }
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    for (const FleetQoxPublisherData * data : g_publishers) {
      send_publisher_graph_advertisement(data, "add");
    }
    for (const FleetQoxSubscriptionData * data : g_subscriptions) {
      send_subscription_graph_advertisement(data, "add");
    }
  }
}

void stop_pubsub_graph_renewal_thread()
{
  std::lock_guard<std::mutex> lifecycle_lock(g_pubsub_graph_renewal_lifecycle_mutex);
  g_pubsub_graph_renewal_running.store(false, std::memory_order_release);
  if (g_pubsub_graph_renewal_thread.joinable()) {
    g_pubsub_graph_renewal_thread.join();
  }
  g_pubsub_graph_renewal_started.store(false, std::memory_order_release);
}

void ensure_pubsub_graph_renewal_thread()
{
  std::lock_guard<std::mutex> lifecycle_lock(g_pubsub_graph_renewal_lifecycle_mutex);
  if (g_pubsub_graph_renewal_started.load(std::memory_order_acquire)) {
    return;
  }
  if (g_pubsub_graph_renewal_thread.joinable()) {
    g_pubsub_graph_renewal_thread.join();
  }
  g_pubsub_graph_renewal_running.store(true, std::memory_order_release);
  g_pubsub_graph_renewal_started.store(true, std::memory_order_release);
  g_pubsub_graph_renewal_thread = std::thread(pubsub_graph_renewal_loop);
  std::call_once(g_pubsub_graph_renewal_atexit_once, []() {
    std::atexit(stop_pubsub_graph_renewal_thread);
  });
}

void maybe_renew_publisher_graph(FleetQoxPublisherData * data)
{
  constexpr std::int64_t kGraphRenewIntervalNs = 500000000;
  if (data == nullptr || socket_transport().peer_count() == 0) {
    return;
  }
  const std::int64_t now = monotonic_timestamp_ns();
  if (now - data->last_graph_advertisement_ns < kGraphRenewIntervalNs) {
    return;
  }
  data->last_graph_advertisement_ns = now;
  send_publisher_graph_advertisement(data, "add");
}

int test_ack_delay_ms_for_subscription(const FleetQoxSubscriptionData * subscription)
{
  if (subscription == nullptr) {
    return 0;
  }
  const char * suffix_value = std::getenv(
    "FLEETQOX_RMW_TEST_ACK_DELAY_SUBSCRIPTION_SUFFIX");
  if (suffix_value == nullptr || suffix_value[0] == '\0') {
    return 0;
  }
  const std::string suffix(suffix_value);
  if (subscription->endpoint_id.size() < suffix.size() ||
    subscription->endpoint_id.compare(
      subscription->endpoint_id.size() - suffix.size(), suffix.size(), suffix) != 0)
  {
    return 0;
  }
  return parse_nonnegative_int_env("FLEETQOX_RMW_TEST_ACK_DELAY_MS", 0, 10000);
}

void enqueue_received_frame(const std::string & encoded_frame)
{
  const auto decoded_frame = rmw_fleetqox_cpp::decode_data_frame(encoded_frame);
  if (!decoded_frame) {
    return;
  }

  std::vector<EventCallbackNotification> callbacks;
  std::vector<EventCallbackNotification> event_callbacks;
  std::vector<std::pair<std::string, int>> ack_nack_payloads;
  size_t matched_subscriptions = 0;
  const std::int64_t receive_ns = monotonic_timestamp_ns();
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    for (FleetQoxSubscriptionData * subscription : g_subscriptions) {
      if (subscription != nullptr && subscription->domain_id == decoded_frame->domain_id &&
        subscription->topic_name == decoded_frame->topic &&
        (decoded_frame->type_name.empty() ||
        subscription->type_name == decoded_frame->type_name))
      {
        if (!subscribe_allowed_by_security_policy(
            subscription->topic_name, subscription->enclave, subscription->domain_id))
        {
          continue;
        }
        if (frame_exceeds_lifespan(subscription->qos, decoded_frame->source_timestamp_ns)) {
          continue;
        }
        record_requested_deadline_miss_locked(
          subscription,
          receive_ns,
          &event_callbacks);
        subscription->last_received_ns = receive_ns;
        auto & sequence_state =
          subscription->sequence_states[rmw_fleetqox_cpp::stream_key(*decoded_frame)];
        const bool establish_reception_baseline =
          !sequence_state.reception_sequence_baseline_initialized;
        rmw_fleetqox_cpp::AckNackFeedback feedback =
          rmw_fleetqox_cpp::observe_frame(sequence_state, *decoded_frame);
        if (establish_reception_baseline) {
          // Without a writer heartbeat carrying its current sequence, samples
          // published before this reader's first observation are not provable losses.
          feedback =
            rmw_fleetqox_cpp::establish_reception_sequence_baseline(sequence_state);
        }
        if (subscription->qos.reliability == RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT) {
          track_best_effort_sequence_gaps_locked(&sequence_state, feedback, receive_ns);
        } else {
          ack_nack_payloads.emplace_back(
            rmw_fleetqox_cpp::encode_ack_nack(
              *decoded_frame, feedback, subscription->endpoint_id),
            test_ack_delay_ms_for_subscription(subscription));
        }
        if (feedback.out_of_order) {
          g_out_of_order_data_frames_observed.fetch_add(1, std::memory_order_relaxed);
        }
        if (feedback.duplicate) {
          g_duplicate_data_frames_deduped.fetch_add(1, std::memory_order_relaxed);
          continue;
        }
        if (!subscription->content_filter_expression.empty()) {
          g_content_filters_evaluated.fetch_add(1, std::memory_order_relaxed);
          if (!subscription_content_filter_matches_locked(
              subscription,
              decoded_frame->serialized_payload))
          {
            g_content_filters_dropped.fetch_add(1, std::memory_order_relaxed);
            continue;
          }
          g_content_filters_matched.fetch_add(1, std::memory_order_relaxed);
        }
        ++matched_subscriptions;
        subscription->frame_queue.push_back(encoded_frame);
        enforce_subscription_depth_locked(subscription, &event_callbacks);
        if (!subscription->destroying &&
          subscription->on_new_message_callback != nullptr)
        {
          queue_event_callback_locked(
            &callbacks,
            subscription->on_new_message_callback,
            subscription->on_new_message_user_data,
            1,
            nullptr,
            subscription);
        }
      }
    }
  }
  for (const auto & payload : ack_nack_payloads) {
    if (payload.second > 0) {
      std::thread(
        [encoded = payload.first, delay_ms = payload.second]() {
          std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));
          const rmw_ret_t delayed_ret = socket_transport().send_ack_nack(encoded);
          (void)delayed_ret;
        }).detach();
      continue;
    }
    const rmw_ret_t ret = socket_transport().send_ack_nack(payload.first);
    (void)ret;
  }
  if (trace_take_enabled()) {
    std::fprintf(
      stderr,
      "fleetqox enqueue topic=%s matched_subscriptions=%zu callbacks=%zu\n",
      decoded_frame->topic.c_str(),
      matched_subscriptions,
      callbacks.size());
  }
  notify_event_callbacks(callbacks);
  notify_event_callbacks(event_callbacks);
}

bool apply_received_graph_advertisement(const std::string & encoded_frame)
{
  const auto advertisement = rmw_fleetqox_cpp::decode_graph_advertisement(encoded_frame);
  if (!advertisement) {
    return false;
  }
  const bool topic_publisher = advertisement->entity_kind == "publisher";
  const bool topic_subscription = advertisement->entity_kind == "subscription";
  if (topic_publisher || topic_subscription) {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    if (local_endpoint_id_exists_locked(topic_publisher, advertisement->endpoint_id)) {
      return true;
    }
  }
  const std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> endpoint_gid =
    endpoint_gid_from_hex(advertisement->endpoint_gid, advertisement->endpoint_id);
  const rmw_qos_profile_t qos = rmw_qos_from_graph(advertisement->qos);
  if (advertisement->action != "liveliness_assert") {
    rmw_fleetqox_cpp_graph_apply_remote_advertisement_with_info_in_domain(
      advertisement->action.c_str(),
      advertisement->entity_kind.c_str(),
      advertisement->node_name.c_str(),
      advertisement->node_namespace.c_str(),
      advertisement->topic.c_str(),
      advertisement->type_name.c_str(),
      advertisement->endpoint_id.c_str(),
      endpoint_gid.data(),
      endpoint_gid.size(),
      &qos,
      advertisement->domain_id,
      advertisement->lease_ms);
  }
  if (apply_remote_pubsub_event_advertisement(*advertisement, qos)) {
    ensure_qos_deadline_monitor_thread();
  }
  return true;
}

rmw_ret_t wait_for_all_acked_impl(
  const rmw_publisher_t * publisher,
  rmw_time_t wait_timeout)
{
  if (publisher == nullptr) {
    RMW_SET_ERROR_MSG("publisher is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const rmw_ret_t identifier_ret = require_identifier(publisher->implementation_identifier);
  if (identifier_ret != RMW_RET_OK) {
    return identifier_ret;
  }
  FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("publisher data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }

  g_wait_for_all_acked_calls.fetch_add(1, std::memory_order_relaxed);
  std::uint64_t target_sequence = 0;
  size_t target_expected = 0;
  bool has_target = false;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    for (const auto & item : g_retransmit_ledger) {
      const ReliableRetransmitEntry & entry = item.second;
      if (entry.publisher_id != data->publisher_id || !entry.reliable) {
        continue;
      }
      has_target = true;
      target_sequence = std::max(target_sequence, entry.source_sequence_number);
      target_expected = std::max(target_expected, entry.expected_acknowledgments);
    }
  }
  if (!has_target) {
    g_last_wait_for_all_acked_expected.store(0, std::memory_order_relaxed);
    g_last_wait_for_all_acked_observed.store(0, std::memory_order_relaxed);
    g_wait_for_all_acked_successes.fetch_add(1, std::memory_order_relaxed);
    return RMW_RET_OK;
  }

  const rmw_duration_t timeout_ns = rmw_time_total_nsec(wait_timeout);
  bool infinite_timeout = timeout_ns == std::numeric_limits<rmw_duration_t>::max();
  const auto start = std::chrono::steady_clock::now();
  auto deadline = std::chrono::steady_clock::time_point::max();
  if (!infinite_timeout) {
    const auto requested = std::chrono::nanoseconds(timeout_ns);
    if (requested < std::chrono::steady_clock::time_point::max() - start) {
      deadline = start + requested;
    } else {
      infinite_timeout = true;
    }
  }
  constexpr auto graph_refresh_interval = std::chrono::milliseconds(10);

  while (true) {
    const std::vector<std::string> active_vector =
      rmw_fleetqox_cpp_graph_matched_subscription_endpoint_ids(
        data->domain_id, data->topic_name, data->type_name, data->qos);
    const std::unordered_set<std::string> active_subscribers(
      active_vector.begin(), active_vector.end());
    std::unique_lock<std::mutex> lock(g_bus_mutex);
    bool pending = false;
    size_t observed = target_expected;
    for (auto & item : g_retransmit_ledger) {
      ReliableRetransmitEntry & entry = item.second;
      if (entry.publisher_id != data->publisher_id || !entry.reliable ||
        entry.source_sequence_number > target_sequence)
      {
        continue;
      }
      for (auto it = entry.pending_subscriber_ids.begin();
        it != entry.pending_subscriber_ids.end();)
      {
        if (active_subscribers.find(*it) == active_subscribers.end()) {
          it = entry.pending_subscriber_ids.erase(it);
        } else {
          ++it;
        }
      }
      entry.acknowledged = entry.pending_subscriber_ids.empty();
      observed = std::min(observed, entry.acknowledgments_observed);
      pending = pending || !entry.acknowledged;
    }
    g_last_wait_for_all_acked_expected.store(target_expected, std::memory_order_relaxed);
    g_last_wait_for_all_acked_observed.store(observed, std::memory_order_relaxed);
    if (!pending) {
      g_wait_for_all_acked_successes.fetch_add(1, std::memory_order_relaxed);
      return RMW_RET_OK;
    }

    const auto now = std::chrono::steady_clock::now();
    if (!infinite_timeout && now >= deadline) {
      g_wait_for_all_acked_timeouts.fetch_add(1, std::memory_order_relaxed);
      return RMW_RET_TIMEOUT;
    }
    const auto wake_at = infinite_timeout ?
      now + graph_refresh_interval :
      std::min(deadline, now + graph_refresh_interval);
    g_all_acked_condition.wait_until(lock, wake_at);
  }
}

}  // namespace

extern "C"
{

void rmw_fleetqox_cpp_graph_register_publisher_endpoint(
  const char * node_name,
  const char * node_namespace,
  const char * topic_name,
  const char * type_name,
  const char * endpoint_id,
  const std::uint8_t * endpoint_gid,
  size_t endpoint_gid_size,
  const rmw_qos_profile_t * qos,
  std::size_t domain_id);
void rmw_fleetqox_cpp_graph_unregister_publisher_endpoint(const char * endpoint_id);
void rmw_fleetqox_cpp_graph_register_subscription_endpoint(
  const char * node_name,
  const char * node_namespace,
  const char * topic_name,
  const char * type_name,
  const char * endpoint_id,
  const std::uint8_t * endpoint_gid,
  size_t endpoint_gid_size,
  const rmw_qos_profile_t * qos,
  std::size_t domain_id);
void rmw_fleetqox_cpp_graph_unregister_subscription_endpoint(const char * endpoint_id);
void rmw_fleetqox_cpp_graph_apply_remote_advertisement_with_info_in_domain(
  const char * action,
  const char * entity_kind,
  const char * node_name,
  const char * node_namespace,
  const char * topic_name,
  const char * type_name,
  const char * endpoint_id,
  const std::uint8_t * endpoint_gid,
  size_t endpoint_gid_size,
  const rmw_qos_profile_t * qos,
  std::uint64_t domain_id,
  std::uint64_t lease_ms);

rmw_ret_t rmw_fleetqox_cpp_publisher_wait_for_all_acked(
  const rmw_publisher_t * publisher,
  rmw_time_t wait_timeout)
{
  return wait_for_all_acked_impl(publisher, wait_timeout);
}

std::uint64_t rmw_fleetqox_cpp_wait_for_all_acked_calls()
{
  return g_wait_for_all_acked_calls.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_wait_for_all_acked_successes()
{
  return g_wait_for_all_acked_successes.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_wait_for_all_acked_timeouts()
{
  return g_wait_for_all_acked_timeouts.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_last_wait_for_all_acked_expected()
{
  return g_last_wait_for_all_acked_expected.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_last_wait_for_all_acked_observed()
{
  return g_last_wait_for_all_acked_observed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_remote_graph_event_advertisements_received()
{
  return g_remote_graph_event_advertisements_received.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_remote_graph_event_endpoint_adds()
{
  return g_remote_graph_event_endpoint_adds.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_remote_graph_event_endpoint_renewals()
{
  return g_remote_graph_event_endpoint_renewals.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_remote_graph_event_endpoint_removes()
{
  return g_remote_graph_event_endpoint_removes.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_remote_graph_event_endpoint_expiries()
{
  return g_remote_graph_event_endpoint_expiries.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_remote_manual_liveliness_assertions_received()
{
  return g_remote_manual_liveliness_assertions_received.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_remote_manual_liveliness_expiries()
{
  return g_remote_manual_liveliness_expiries.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_remote_manual_liveliness_reassertions()
{
  return g_remote_manual_liveliness_reassertions.load(std::memory_order_relaxed);
}

size_t rmw_fleetqox_cpp_remote_graph_event_endpoint_count()
{
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  return g_remote_pubsub_endpoints.size();
}

bool rmw_fleetqox_cpp_subscription_has_data(const rmw_subscription_t * subscription)
{
  if (subscription == nullptr || !identifier_matches(subscription->implementation_identifier)) {
    return false;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    return false;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  return !data->frame_queue.empty();
}

const rmw_context_t * rmw_fleetqox_cpp_publisher_context(const rmw_publisher_t * publisher)
{
  if (publisher == nullptr || !identifier_matches(publisher->implementation_identifier)) {
    return nullptr;
  }
  const FleetQoxPublisherData * data = publisher_data(publisher);
  return data == nullptr ? nullptr : data->context;
}

const rmw_context_t * rmw_fleetqox_cpp_subscription_context(
  const rmw_subscription_t * subscription)
{
  if (subscription == nullptr || !identifier_matches(subscription->implementation_identifier)) {
    return nullptr;
  }
  const FleetQoxSubscriptionData * data = subscription_data(subscription);
  return data == nullptr ? nullptr : data->context;
}

const rmw_context_t * rmw_fleetqox_cpp_waitable_subscription_context(const void * waitable)
{
  if (waitable == nullptr) {
    return nullptr;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  for (const FleetQoxSubscriptionData * data : g_subscriptions) {
    if (data == waitable) {
      return data->context;
    }
  }
  for (const rmw_subscription_t * subscription : g_subscription_handles) {
    if (subscription == waitable) {
      const auto * data = static_cast<const FleetQoxSubscriptionData *>(subscription->data);
      return data == nullptr ? nullptr : data->context;
    }
  }
  return nullptr;
}

bool rmw_fleetqox_cpp_subscription_data_has_data(const void * subscription_impl)
{
  if (subscription_impl == nullptr) {
    return false;
  }
  const auto * data = static_cast<const FleetQoxSubscriptionData *>(subscription_impl);
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  return !data->frame_queue.empty();
}

bool rmw_fleetqox_cpp_waitable_subscription_has_data(const void * waitable)
{
  if (waitable == nullptr) {
    return false;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  for (const FleetQoxSubscriptionData * data : g_subscriptions) {
    if (data == waitable) {
      return !data->frame_queue.empty();
    }
  }
  for (const rmw_subscription_t * subscription : g_subscription_handles) {
    if (subscription == waitable) {
      const auto * data = static_cast<const FleetQoxSubscriptionData *>(subscription->data);
      return data != nullptr && !data->frame_queue.empty();
    }
  }
  return false;
}

std::uint64_t rmw_fleetqox_cpp_socket_frames_sent()
{
  return socket_transport().frames_sent();
}

std::uint64_t rmw_fleetqox_cpp_socket_frames_received()
{
  return socket_transport().frames_received();
}

std::uint64_t rmw_fleetqox_cpp_socket_data_frames_received()
{
  return socket_transport().data_frames_received();
}

bool rmw_fleetqox_cpp_udp_aead_enabled()
{
  return socket_transport().udp_aead_enabled();
}

std::uint64_t rmw_fleetqox_cpp_udp_aead_encrypted_frames()
{
  return socket_transport().udp_aead_encrypted_frames();
}

std::uint64_t rmw_fleetqox_cpp_udp_aead_decrypted_frames()
{
  return socket_transport().udp_aead_decrypted_frames();
}

std::uint64_t rmw_fleetqox_cpp_udp_aead_authentication_failures()
{
  return socket_transport().udp_aead_authentication_failures();
}

std::uint64_t rmw_fleetqox_cpp_udp_aead_unprotected_drops()
{
  return socket_transport().udp_aead_unprotected_drops();
}

std::uint64_t rmw_fleetqox_cpp_udp_aead_replay_drops()
{
  return socket_transport().udp_aead_replay_drops();
}

std::uint64_t rmw_fleetqox_cpp_udp_aead_session_keys_derived()
{
  return socket_transport().udp_aead_session_keys_derived();
}

std::uint64_t rmw_fleetqox_cpp_udp_aead_session_key_rotations()
{
  return socket_transport().udp_aead_session_key_rotations();
}

std::uint64_t rmw_fleetqox_cpp_udp_aead_session_key_reuses()
{
  return socket_transport().udp_aead_session_key_reuses();
}

bool rmw_fleetqox_cpp_udp_peer_auth_enabled()
{
  return socket_transport().udp_peer_auth_enabled();
}

std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_signed_frames()
{
  return socket_transport().udp_peer_auth_signed_frames();
}

std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_verified_frames()
{
  return socket_transport().udp_peer_auth_verified_frames();
}

std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_failures()
{
  return socket_transport().udp_peer_auth_failures();
}

std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_chain_failures()
{
  return socket_transport().udp_peer_auth_chain_failures();
}

std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_signature_failures()
{
  return socket_transport().udp_peer_auth_signature_failures();
}

std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_identity_denied()
{
  return socket_transport().udp_peer_auth_identity_denied();
}

bool rmw_fleetqox_cpp_udp_peer_auth_crl_enabled()
{
  return socket_transport().udp_peer_auth_crl_enabled();
}

std::uint64_t rmw_fleetqox_cpp_udp_peer_auth_revoked_certificate_drops()
{
  return socket_transport().udp_peer_auth_revoked_certificate_drops();
}

const char * rmw_fleetqox_cpp_udp_peer_auth_last_identity()
{
  static thread_local std::string identity;
  identity = socket_transport().udp_peer_auth_last_identity();
  return identity.c_str();
}

std::uint64_t rmw_fleetqox_cpp_socket_ack_nack_sent()
{
  return socket_transport().ack_nack_sent();
}

std::uint64_t rmw_fleetqox_cpp_socket_ack_nack_received()
{
  return socket_transport().ack_nack_received();
}

std::uint64_t rmw_fleetqox_cpp_socket_ack_nack_duplicate_received()
{
  return socket_transport().ack_nack_duplicate_received();
}

std::uint64_t rmw_fleetqox_cpp_socket_ack_nack_out_of_order_received()
{
  return socket_transport().ack_nack_out_of_order_received();
}

std::uint64_t rmw_fleetqox_cpp_socket_unrecoverable_loss_notices_sent()
{
  return socket_transport().unrecoverable_loss_notices_sent();
}

std::uint64_t rmw_fleetqox_cpp_socket_unrecoverable_loss_notices_received()
{
  return socket_transport().unrecoverable_loss_notices_received();
}

std::uint64_t rmw_fleetqox_cpp_unrecoverable_loss_samples_reported()
{
  return g_unrecoverable_loss_samples_reported.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_socket_nack_retransmissions()
{
  return socket_transport().nack_retransmissions();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_nacks_sent()
{
  return socket_transport().fragment_nacks_sent();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_nacks_received()
{
  return socket_transport().fragment_nacks_received();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragments_selectively_retransmitted()
{
  return socket_transport().fragments_selectively_retransmitted();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_repair_requests_coalesced()
{
  return socket_transport().fragment_repair_requests_coalesced();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_repair_cooldown_coalesced()
{
  return socket_transport().fragment_repair_cooldown_coalesced();
}

std::uint64_t rmw_fleetqox_cpp_socket_completed_fragment_duplicates_dropped()
{
  return socket_transport().completed_fragment_duplicates_dropped();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_duplicate_no_progress_drops()
{
  return socket_transport().fragment_duplicate_no_progress_drops();
}

std::uint64_t rmw_fleetqox_cpp_socket_test_dropped_fragments()
{
  return socket_transport().test_dropped_fragments();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_send_queue_rejections()
{
  return socket_transport().fragment_send_queue_rejections();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_send_failures()
{
  return socket_transport().fragment_send_failures();
}

size_t rmw_fleetqox_cpp_socket_fragment_send_queue_high_water()
{
  return socket_transport().fragment_send_queue_high_water();
}

size_t rmw_fleetqox_cpp_socket_fragment_repair_queue_high_water()
{
  return socket_transport().fragment_repair_queue_high_water();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_repair_round_robin_rotations()
{
  return socket_transport().fragment_repair_round_robin_rotations();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_repair_frame_switches()
{
  return socket_transport().fragment_repair_frame_switches();
}

size_t rmw_fleetqox_cpp_socket_fragment_repair_max_active_frames()
{
  return socket_transport().fragment_repair_max_active_frames();
}

size_t
rmw_fleetqox_cpp_socket_fragment_repair_max_consecutive_same_frame_while_contended()
{
  return socket_transport().
         fragment_repair_max_consecutive_same_frame_while_contended();
}

size_t rmw_fleetqox_cpp_socket_udp_datagram_size_high_water()
{
  return socket_transport().udp_datagram_size_high_water();
}

size_t rmw_fleetqox_cpp_socket_fragment_effective_chunk_bytes_min()
{
  return socket_transport().fragment_effective_chunk_bytes_min();
}

size_t rmw_fleetqox_cpp_socket_fragment_effective_chunk_bytes_max()
{
  return socket_transport().fragment_effective_chunk_bytes_max();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_chunk_budget_reductions()
{
  return socket_transport().fragment_chunk_budget_reductions();
}

std::uint64_t rmw_fleetqox_cpp_socket_udp_datagram_budget_failures()
{
  return socket_transport().udp_datagram_budget_failures();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_queue_admission_waits()
{
  return socket_transport().fragment_queue_admission_waits();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_queue_admission_timeouts()
{
  return socket_transport().fragment_queue_admission_timeouts();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_queue_admission_wait_ns()
{
  return socket_transport().fragment_queue_admission_wait_ns();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_repair_queue_deferrals()
{
  return socket_transport().fragment_repair_queue_deferrals();
}

std::uint64_t
rmw_fleetqox_cpp_socket_fragment_repair_pressure_priority_promotions()
{
  return socket_transport().fragment_repair_pressure_priority_promotions();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_completion_markers_sent()
{
  return socket_transport().fragment_completion_markers_sent();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_completion_markers_received()
{
  return socket_transport().fragment_completion_markers_received();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_completion_marker_orphans()
{
  return socket_transport().fragment_completion_marker_orphans();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_completion_marker_failures()
{
  return socket_transport().fragment_completion_marker_failures();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_repair_source_denials()
{
  return socket_transport().fragment_repair_source_denials();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_repair_reader_budget_exhausted()
{
  return socket_transport().fragment_repair_reader_budget_exhausted();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_initial_round_robin_rotations()
{
  return socket_transport().fragment_initial_round_robin_rotations();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_initial_frame_switches()
{
  return socket_transport().fragment_initial_frame_switches();
}

size_t rmw_fleetqox_cpp_socket_fragment_initial_max_consecutive_same_frame()
{
  return socket_transport().fragment_initial_max_consecutive_same_frame();
}

size_t
rmw_fleetqox_cpp_socket_fragment_initial_max_consecutive_same_frame_while_contended()
{
  return socket_transport()
         .fragment_initial_max_consecutive_same_frame_while_contended();
}

size_t rmw_fleetqox_cpp_socket_fragment_initial_max_active_frames()
{
  return socket_transport().fragment_initial_max_active_frames();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_nack_indexes_requested()
{
  return socket_transport().fragment_nack_indexes_requested();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_nack_index_budget_reductions()
{
  return socket_transport().fragment_nack_index_budget_reductions();
}

size_t rmw_fleetqox_cpp_socket_fragment_nack_max_sweep_indexes_requested()
{
  return socket_transport().fragment_nack_max_sweep_indexes_requested();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_nack_sweep_budget_exhaustions()
{
  return socket_transport().fragment_nack_sweep_budget_exhaustions();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_progressive_nacks_sent()
{
  return socket_transport().fragment_progressive_nacks_sent();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_progress_grace_deferrals()
{
  return socket_transport().fragment_progress_grace_deferrals();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_active_assemblies()
{
  return socket_transport().fragment_active_assemblies();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_active_missing_indexes()
{
  return socket_transport().fragment_active_missing_indexes();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_nack_exhausted_assemblies()
{
  return socket_transport().fragment_nack_exhausted_assemblies();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_oldest_assembly_age_ms()
{
  return socket_transport().fragment_oldest_assembly_age_ms();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_history_request_exhausted()
{
  return socket_transport().fragment_history_request_exhausted();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_assembly_evictions()
{
  return socket_transport().fragment_assembly_evictions();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_assembly_oversize_drops()
{
  return socket_transport().fragment_assembly_oversize_drops();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_assembly_metadata_mismatch_drops()
{
  return socket_transport().fragment_assembly_metadata_mismatch_drops();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_assembly_ttl_expirations()
{
  return socket_transport().fragment_assembly_ttl_expirations();
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_assembly_ttl_expired_missing_indexes()
{
  return socket_transport().fragment_assembly_ttl_expired_missing_indexes();
}

std::uint64_t rmw_fleetqox_cpp_socket_reliable_timeout_retransmissions()
{
  return g_reliable_timeout_retransmissions.load(std::memory_order_relaxed);
}

std::uint64_t
rmw_fleetqox_cpp_socket_fragment_observed_timeout_retransmissions_suppressed()
{
  return g_fragment_observed_timeout_retransmissions_suppressed.load(
    std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_whole_fallback_pacing_deferrals()
{
  return g_fragment_whole_fallback_pacing_deferrals.load(
    std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_socket_fragment_async_send_completions()
{
  return g_fragment_async_send_completions.load(std::memory_order_relaxed);
}

std::uint64_t
rmw_fleetqox_cpp_socket_fragment_initial_pending_timeout_suppressions()
{
  return g_fragment_initial_pending_timeout_suppressions.load(
    std::memory_order_relaxed);
}

std::uint64_t
rmw_fleetqox_cpp_socket_fragment_whole_fallback_grace_deferrals()
{
  return g_fragment_whole_fallback_grace_deferrals.load(
    std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_socket_idle_repair_ack_nack_sent()
{
  return g_idle_repair_ack_nack_sent.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_socket_test_dropped_frames()
{
  return socket_transport().test_dropped_frames();
}

std::uint64_t rmw_fleetqox_cpp_duplicate_data_frames_deduped()
{
  return g_duplicate_data_frames_deduped.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_out_of_order_data_frames_observed()
{
  return g_out_of_order_data_frames_observed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_last_take_source_sequence()
{
  std::lock_guard<std::mutex> lock(g_last_take_mutex);
  return g_last_take_source_sequence;
}

std::int64_t rmw_fleetqox_cpp_last_take_source_timestamp_ns()
{
  std::lock_guard<std::mutex> lock(g_last_take_mutex);
  return g_last_take_source_timestamp_ns;
}

std::int64_t rmw_fleetqox_cpp_last_take_timestamp_ns()
{
  std::lock_guard<std::mutex> lock(g_last_take_mutex);
  return g_last_take_timestamp_ns;
}

const char * rmw_fleetqox_cpp_last_take_topic()
{
  static thread_local std::string topic;
  std::lock_guard<std::mutex> lock(g_last_take_mutex);
  topic = g_last_take_topic;
  return topic.c_str();
}

const char * rmw_fleetqox_cpp_last_take_publisher_id()
{
  static thread_local std::string publisher_id;
  std::lock_guard<std::mutex> lock(g_last_take_mutex);
  publisher_id = g_last_take_publisher_id;
  return publisher_id.c_str();
}

std::uint64_t rmw_fleetqox_cpp_socket_adaptive_failovers()
{
  return socket_transport().adaptive_failovers();
}

std::uint64_t rmw_fleetqox_cpp_socket_adaptive_unicast_frames()
{
  return socket_transport().adaptive_unicast_frames();
}

std::uint64_t rmw_fleetqox_cpp_socket_adaptive_redundant_frames()
{
  return socket_transport().adaptive_redundant_frames();
}

std::uint64_t rmw_fleetqox_cpp_socket_fleet_plan_frames()
{
  return socket_transport().fleet_plan_frames();
}

std::uint64_t rmw_fleetqox_cpp_socket_fleet_plan_redundant_frames()
{
  return socket_transport().fleet_plan_redundant_frames();
}

std::uint64_t rmw_fleetqox_cpp_socket_fleet_plan_selected_path_count()
{
  return socket_transport().fleet_plan_selected_path_count();
}

const char * rmw_fleetqox_cpp_socket_fleet_plan_last_paths()
{
  static thread_local std::string paths;
  paths = socket_transport().fleet_plan_last_paths();
  return paths.c_str();
}

std::uint64_t rmw_fleetqox_cpp_socket_repair_plan_frames()
{
  return socket_transport().repair_plan_frames();
}

std::uint64_t rmw_fleetqox_cpp_socket_repair_plan_redundant_frames()
{
  return socket_transport().repair_plan_redundant_frames();
}

std::uint64_t rmw_fleetqox_cpp_socket_repair_plan_selected_path_count()
{
  return socket_transport().repair_plan_selected_path_count();
}

std::uint64_t rmw_fleetqox_cpp_socket_repair_budget_exhausted()
{
  return socket_transport().repair_budget_exhausted();
}

std::uint64_t rmw_fleetqox_cpp_socket_repair_requests_coalesced()
{
  return socket_transport().repair_requests_coalesced();
}

std::uint64_t rmw_fleetqox_cpp_socket_repair_sequence_attempt_limit_exhausted()
{
  return socket_transport().repair_sequence_attempt_limit_exhausted();
}

std::uint64_t rmw_fleetqox_cpp_socket_repair_not_admitted()
{
  return socket_transport().repair_not_admitted();
}

int rmw_fleetqox_cpp_socket_repair_retransmission_budget()
{
  return socket_transport().repair_retransmission_budget();
}

int rmw_fleetqox_cpp_socket_repair_min_interval_ms()
{
  return socket_transport().repair_min_interval_ms();
}

int rmw_fleetqox_cpp_socket_repair_max_attempts_per_sequence()
{
  return socket_transport().repair_max_attempts_per_sequence();
}

const char * rmw_fleetqox_cpp_socket_repair_plan_last_paths()
{
  static thread_local std::string paths;
  paths = socket_transport().repair_plan_last_paths();
  return paths.c_str();
}

std::uint64_t rmw_fleetqox_cpp_socket_adaptive_peer_score_sum()
{
  return socket_transport().adaptive_peer_score_sum();
}

size_t rmw_fleetqox_cpp_socket_adaptive_selected_peer_index()
{
  return socket_transport().adaptive_selected_peer_index();
}

const char * rmw_fleetqox_cpp_socket_peer_policy()
{
  return socket_transport().peer_policy().c_str();
}

const char * rmw_fleetqox_cpp_socket_bound_endpoint()
{
  return socket_transport().bound_endpoint().c_str();
}

const char * rmw_fleetqox_cpp_transport_mode()
{
  return socket_transport().transport_mode().c_str();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_frames_sent()
{
  return socket_transport().quic_gateway_frames_sent();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_bytes_sent()
{
  return socket_transport().quic_gateway_bytes_sent();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_frames_received()
{
  return socket_transport().quic_gateway_frames_received();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_bytes_received()
{
  return socket_transport().quic_gateway_bytes_received();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_frames_enqueued()
{
  return socket_transport().quic_gateway_frames_enqueued();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_frames_failed()
{
  return socket_transport().quic_gateway_frames_failed();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_frames_dropped()
{
  return socket_transport().quic_gateway_frames_dropped();
}

size_t rmw_fleetqox_cpp_quic_gateway_queue_depth()
{
  return socket_transport().quic_gateway_queue_depth();
}

size_t rmw_fleetqox_cpp_quic_gateway_max_queue_frames()
{
  return socket_transport().quic_gateway_max_queue_frames();
}

bool rmw_fleetqox_cpp_quic_gateway_async_enabled()
{
  return socket_transport().quic_gateway_async_enabled();
}

int rmw_fleetqox_cpp_quic_gateway_last_exit_code()
{
  return socket_transport().quic_gateway_last_exit_code();
}

const char * rmw_fleetqox_cpp_quic_gateway_uri()
{
  static thread_local std::string uri;
  uri = socket_transport().quic_gateway_uri();
  return uri.c_str();
}

const char * rmw_fleetqox_cpp_quic_gateway_backend()
{
  static thread_local std::string backend;
  backend = socket_transport().quic_gateway_backend();
  return backend.c_str();
}

bool rmw_fleetqox_cpp_quic_gateway_subprocess_backed()
{
  return socket_transport().quic_gateway_subprocess_backed();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_connections_created()
{
  return socket_transport().quic_gateway_connections_created();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_handshakes_completed()
{
  return socket_transport().quic_gateway_handshakes_completed();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_streams_opened()
{
  return socket_transport().quic_gateway_streams_opened();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_connection_reuse_count()
{
  return socket_transport().quic_gateway_connection_reuse_count();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_packets_sent()
{
  return socket_transport().quic_gateway_packets_sent();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_packets_received()
{
  return socket_transport().quic_gateway_packets_received();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_reconnects()
{
  return socket_transport().quic_gateway_reconnects();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_concurrent_stream_pairs()
{
  return socket_transport().quic_gateway_concurrent_stream_pairs();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_max_concurrent_request_streams()
{
  return socket_transport().quic_gateway_max_concurrent_request_streams();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_concurrent_api_operation_pairs()
{
  return socket_transport().quic_gateway_concurrent_api_operation_pairs();
}

std::uint64_t rmw_fleetqox_cpp_quic_gateway_max_concurrent_api_calls()
{
  return socket_transport().quic_gateway_max_concurrent_api_calls();
}

std::uint64_t rmw_fleetqox_cpp_shared_memory_frames_sent()
{
  return socket_transport().shared_memory_frames_sent();
}

std::uint64_t rmw_fleetqox_cpp_shared_memory_frames_received()
{
  return socket_transport().shared_memory_frames_received();
}

std::uint64_t rmw_fleetqox_cpp_shared_memory_overwritten_frames()
{
  return socket_transport().shared_memory_overwritten_frames();
}

bool rmw_fleetqox_cpp_socket_ensure_started()
{
  return socket_transport().ensure_started();
}

const char * rmw_fleetqox_cpp_socket_init_error()
{
  return socket_transport().init_error().c_str();
}

extern "C" void rmw_fleetqox_cpp_stop_remote_graph_lease_monitor_thread();
extern "C" void rmw_fleetqox_cpp_stop_service_graph_renewal_thread();
extern "C" void rmw_fleetqox_cpp_stop_service_request_repair_worker();

void rmw_fleetqox_cpp_shutdown_pubsub_runtime()
{
  stop_pubsub_graph_renewal_thread();
  stop_reliable_retransmit_thread();
  stop_qos_deadline_monitor_thread();
  rmw_fleetqox_cpp_stop_remote_graph_lease_monitor_thread();
  rmw_fleetqox_cpp_stop_service_graph_renewal_thread();
  rmw_fleetqox_cpp_stop_service_request_repair_worker();
  socket_transport().shutdown();
}

size_t rmw_fleetqox_cpp_socket_peer_count()
{
  return socket_transport().peer_count();
}

rmw_ret_t rmw_fleetqox_cpp_send_encoded_frame(const char * encoded_frame, size_t size)
{
  if (encoded_frame == nullptr || size == 0) {
    RMW_SET_ERROR_MSG("encoded frame must be non-empty");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return socket_transport().send_frame(std::string(encoded_frame, size));
}

rmw_ret_t rmw_fleetqox_cpp_send_graph_advertisement(
  const char * action,
  const char * entity_kind,
  const char * node_name,
  const char * node_namespace,
  const char * topic_name,
  const char * type_name,
  const char * endpoint_id,
  const rmw_qos_profile_t * qos,
  std::size_t domain_id)
{
  if (action == nullptr || entity_kind == nullptr || topic_name == nullptr || type_name == nullptr ||
    endpoint_id == nullptr)
  {
    RMW_SET_ERROR_MSG("graph advertisement arguments must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const rmw_qos_profile_t effective_qos = qos != nullptr ? *qos : rmw_qos_profile_default;
  return socket_transport().send_graph_advertisement(
    action,
    entity_kind,
    node_name != nullptr ? node_name : "",
    node_namespace != nullptr ? node_namespace : "",
    topic_name,
    type_name,
    endpoint_id,
    make_endpoint_gid(endpoint_id),
    effective_qos,
    domain_id);
}

bool rmw_fleetqox_cpp_serialize_introspection_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  const void * ros_message,
  std::vector<std::uint8_t> * payload)
{
  if (members == nullptr || ros_message == nullptr || payload == nullptr) {
    return false;
  }
  return serialize_introspection_c_message(members, ros_message, payload);
}

bool rmw_fleetqox_cpp_max_serialized_size_introspection_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  size_t * size)
{
  return max_serialized_size_introspection_c_message(members, size);
}

bool rmw_fleetqox_cpp_max_serialized_size_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  size_t * size)
{
  return max_serialized_size_introspection_cpp_message(members, size);
}

rmw_ret_t rmw_fleetqox_cpp_borrow_publisher_loan(
  const rmw_publisher_t * publisher,
  const rosidl_message_type_support_t * type_support,
  void ** ros_message)
{
  FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("publisher data is null while borrowing message");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return borrow_loan(
    type_support,
    data->typed_message_size,
    data->type_name,
    data->allocator,
    publisher,
    LoanOwnerKind::Publisher,
    ros_message);
}

rmw_ret_t rmw_fleetqox_cpp_release_publisher_loan(
  const rmw_publisher_t * publisher,
  void * ros_message)
{
  return release_loan(publisher, LoanOwnerKind::Publisher, ros_message);
}

rmw_ret_t rmw_fleetqox_cpp_borrow_subscription_loan(
  const rmw_subscription_t * subscription,
  void ** ros_message)
{
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null while borrowing message");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return borrow_loan(
    data->type_support,
    data->typed_message_size,
    data->type_name,
    data->allocator,
    subscription,
    LoanOwnerKind::Subscription,
    ros_message);
}

rmw_ret_t rmw_fleetqox_cpp_release_subscription_loan(
  const rmw_subscription_t * subscription,
  void * ros_message)
{
  return release_loan(subscription, LoanOwnerKind::Subscription, ros_message);
}

bool rmw_fleetqox_cpp_deserialize_introspection_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  const std::vector<std::uint8_t> * payload,
  void * ros_message)
{
  if (members == nullptr || payload == nullptr || ros_message == nullptr) {
    return false;
  }
  size_t offset = 0;
  return deserialize_introspection_c_message(members, *payload, &offset, ros_message) &&
         offset == payload->size();
}

bool rmw_fleetqox_cpp_serialize_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  const void * ros_message,
  std::vector<std::uint8_t> * payload)
{
  return members != nullptr && ros_message != nullptr && payload != nullptr &&
         serialize_introspection_cpp_message(members, ros_message, payload);
}

bool rmw_fleetqox_cpp_deserialize_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  const std::vector<std::uint8_t> * payload,
  void * ros_message)
{
  if (members == nullptr || payload == nullptr || ros_message == nullptr) {
    return false;
  }
  size_t offset = 0;
  return deserialize_introspection_cpp_message(members, *payload, &offset, ros_message) &&
         offset == payload->size();
}

bool rmw_fleetqox_cpp_publisher_gid(const rmw_publisher_t * publisher, rmw_gid_t * gid)
{
  if (publisher == nullptr || gid == nullptr || !identifier_matches(publisher->implementation_identifier)) {
    return false;
  }
  const FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    return false;
  }
  gid->implementation_identifier = kIdentifier;
  std::memset(gid->data, 0, sizeof(gid->data));
  std::memcpy(gid->data, data->endpoint_gid.data(), data->endpoint_gid.size());
  return true;
}

rmw_publisher_t * rmw_create_publisher(
  const rmw_node_t * node,
  const rosidl_message_type_support_t * type_support,
  const char * topic_name,
  const rmw_qos_profile_t * qos_profile,
  const rmw_publisher_options_t * publisher_options)
{
  if (!node_is_valid(node)) {
    RMW_SET_ERROR_MSG("node is not a valid rmw_fleetqox_cpp node");
    return nullptr;
  }
  if (type_support == nullptr || qos_profile == nullptr || publisher_options == nullptr) {
    RMW_SET_ERROR_MSG("publisher type support, qos, and options must be non-null");
    return nullptr;
  }
  if (!topic_is_valid(topic_name)) {
    RMW_SET_ERROR_MSG("publisher topic must be a fully qualified ROS topic");
    return nullptr;
  }
  if (!socket_transport().ready()) {
    RMW_SET_ERROR_MSG(socket_transport().init_error().empty() ?
      "socket transport is not ready" : socket_transport().init_error().c_str());
    return nullptr;
  }
  const rosidl_message_type_support_t * effective_type_support =
    resolve_effective_type_support(type_support);
  rmw_qos_profile_t adapted_qos = *qos_profile;
  const rmw_ret_t adapt_qos_ret =
    rmw_dds_common::qos_profile_get_best_available_for_topic_publisher(
    node,
    topic_name,
    &adapted_qos,
    rmw_get_subscriptions_info_by_topic);
  if (adapt_qos_ret != RMW_RET_OK) {
    RMW_SET_ERROR_MSG("failed to resolve publisher BEST_AVAILABLE QoS policies");
    return nullptr;
  }
  if (!qos_liveliness_policy_supported(adapted_qos.liveliness)) {
    RMW_SET_ERROR_MSG(
      "publisher liveliness policy must be a resolved, non-deprecated policy");
    return nullptr;
  }

  rmw_publisher_t * publisher = rmw_publisher_allocate();
  if (publisher == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate publisher handle");
    return nullptr;
  }
  rcutils_allocator_t allocator = node->context->options.allocator;
  const std::string type_name = type_name_from_type_support(effective_type_support);
  const std::string publisher_id = allocate_publisher_id();
  const std::string endpoint_id = endpoint_id_for_local_id(publisher_id);
  const std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> endpoint_gid =
    make_endpoint_gid(endpoint_id);
  FleetQoxPublisherData * data = allocate_data<FleetQoxPublisherData>(
    allocator,
    allocator,
    node->context,
    node,
    std::string(topic_name),
    type_name,
    std::string(node->name != nullptr ? node->name : ""),
    std::string(node->namespace_ != nullptr ? node->namespace_ : ""),
    std::string(node->context->options.enclave != nullptr ? node->context->options.enclave : ""),
    node->context->actual_domain_id,
    publisher_id,
    endpoint_id,
    endpoint_gid,
    adapted_qos,
    effective_type_support,
    typed_message_size_from_type_support(effective_type_support),
    1u,
    monotonic_timestamp_ns(),
    0,
    monotonic_timestamp_ns(),
    true,
    0,
    0,
    nullptr,
    nullptr,
    0,
    0,
    nullptr,
    nullptr,
    0,
    0,
    RMW_QOS_POLICY_INVALID,
    nullptr,
    nullptr,
    0,
    0,
    nullptr,
    nullptr,
    size_t{0},
    size_t{0},
    size_t{0},
    std::int32_t{0},
    nullptr,
    nullptr);
  if (data == nullptr) {
    rmw_publisher_free(publisher);
    RMW_SET_ERROR_MSG("failed to allocate publisher data");
    return nullptr;
  }

  publisher->implementation_identifier = kIdentifier;
  publisher->data = data;
  publisher->topic_name = data->topic_name.c_str();
  publisher->options = *publisher_options;
  publisher->can_loan_messages =
    introspection_c_members(data->type_support) != nullptr ||
    introspection_cpp_members(data->type_support) != nullptr || data->typed_message_size > 0;

  std::vector<EventCallbackNotification> matched_callbacks;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    purge_expired_remote_pubsub_endpoints_locked(
      monotonic_timestamp_ns(), &matched_callbacks);
    g_publishers.push_back(data);
    record_publication_matched_change_locked(
      data,
      matched_subscription_count_locked(data),
      &matched_callbacks);
    refresh_subscription_matched_events_locked(&matched_callbacks);
    record_qos_incompatibilities_for_new_publisher_locked(data, &matched_callbacks);
    record_type_incompatibilities_for_new_publisher_locked(data, &matched_callbacks);
    record_liveliness_for_new_publisher_locked(data, &matched_callbacks);
  }
  notify_event_callbacks(matched_callbacks);
  rmw_fleetqox_cpp_graph_register_publisher_endpoint(
    data->node_name.c_str(),
    data->node_namespace.c_str(),
    data->topic_name.c_str(),
    data->type_name.c_str(),
    data->endpoint_id.c_str(),
    data->endpoint_gid.data(),
    data->endpoint_gid.size(),
    &data->qos,
    data->domain_id);
  send_publisher_graph_advertisement(data, "add");
  ensure_pubsub_graph_renewal_thread();
  ensure_reliable_retransmit_thread();
  if (qos_deadline_enabled(data->qos) || qos_liveliness_enabled(data->qos)) {
    ensure_qos_deadline_monitor_thread();
  }
  return publisher;
}

rmw_ret_t rmw_destroy_publisher(rmw_node_t * node, rmw_publisher_t * publisher)
{
  if (!node_is_valid(node) || publisher == nullptr) {
    RMW_SET_ERROR_MSG("node and publisher must be valid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(publisher->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr || data->owner_node != node) {
    RMW_SET_ERROR_MSG("publisher was not created by the supplied node");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_fleetqox_cpp_graph_unregister_publisher_endpoint(data->endpoint_id.c_str());
  send_publisher_graph_advertisement(data, "remove");
  std::vector<EventCallbackNotification> matched_callbacks;
  {
    std::unique_lock<std::mutex> lock(g_bus_mutex);
    data->destroying = true;
    data->liveliness_lost_callback = nullptr;
    data->offered_deadline_callback = nullptr;
    data->offered_incompatible_qos_callback = nullptr;
    data->publisher_incompatible_type_callback = nullptr;
    data->publication_matched_callback = nullptr;
    g_publishers.erase(std::remove(g_publishers.begin(), g_publishers.end(), data), g_publishers.end());
    if (data != nullptr) {
      const std::string prefix = data->publisher_id + "|";
      for (auto it = g_retransmit_ledger.begin(); it != g_retransmit_ledger.end();) {
        if (it->first.rfind(prefix, 0) == 0) {
          it = g_retransmit_ledger.erase(it);
        } else {
          ++it;
        }
      }
    }
    for (FleetQoxSubscriptionData * subscription_data_item : g_subscriptions) {
      if (local_pubsub_match_compatible(data, subscription_data_item)) {
        record_subscription_liveliness_remove_locked(
          subscription_data_item, data->publisher_id, &matched_callbacks);
      }
    }
    refresh_subscription_matched_events_locked(&matched_callbacks);
    g_entity_callback_condition.wait(lock, [data]() {
      return data->inflight_callbacks == 0;
    });
  }
  notify_event_callbacks(matched_callbacks);
  bool stop_retransmit = false;
  bool stop_deadline_monitor = false;
  bool stop_graph_renewal = false;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    stop_retransmit = g_publishers.empty();
    stop_graph_renewal = g_publishers.empty() && g_subscriptions.empty();
    stop_deadline_monitor = g_publishers.empty() && g_subscriptions.empty();
  }
  if (stop_graph_renewal) {
    stop_pubsub_graph_renewal_thread();
  }
  if (stop_retransmit) {
    stop_reliable_retransmit_thread();
  }
  if (stop_deadline_monitor) {
    stop_qos_deadline_monitor_thread();
  }
  release_owner_loans(publisher, LoanOwnerKind::Publisher);
  deallocate_data(data);
  rmw_publisher_free(publisher);
  return RMW_RET_OK;
}

rmw_subscription_t * rmw_create_subscription(
  const rmw_node_t * node,
  const rosidl_message_type_support_t * type_support,
  const char * topic_name,
  const rmw_qos_profile_t * qos_policies,
  const rmw_subscription_options_t * subscription_options)
{
  if (!node_is_valid(node)) {
    RMW_SET_ERROR_MSG("node is not a valid rmw_fleetqox_cpp node");
    return nullptr;
  }
  if (type_support == nullptr || qos_policies == nullptr || subscription_options == nullptr) {
    RMW_SET_ERROR_MSG("subscription type support, qos, and options must be non-null");
    return nullptr;
  }
  if (!topic_is_valid(topic_name)) {
    RMW_SET_ERROR_MSG("subscription topic must be a fully qualified ROS topic");
    return nullptr;
  }
  if (!socket_transport().ready()) {
    RMW_SET_ERROR_MSG(socket_transport().init_error().empty() ?
      "socket transport is not ready" : socket_transport().init_error().c_str());
    return nullptr;
  }
  const rosidl_message_type_support_t * effective_type_support =
    resolve_effective_type_support(type_support);
  rmw_qos_profile_t adapted_qos = *qos_policies;
  const rmw_ret_t adapt_qos_ret =
    rmw_dds_common::qos_profile_get_best_available_for_topic_subscription(
    node,
    topic_name,
    &adapted_qos,
    rmw_get_publishers_info_by_topic);
  if (adapt_qos_ret != RMW_RET_OK) {
    RMW_SET_ERROR_MSG("failed to resolve subscription BEST_AVAILABLE QoS policies");
    return nullptr;
  }
  if (!qos_liveliness_policy_supported(adapted_qos.liveliness)) {
    RMW_SET_ERROR_MSG(
      "subscription liveliness policy must be a resolved, non-deprecated policy");
    return nullptr;
  }

  rmw_subscription_t * subscription = rmw_subscription_allocate();
  if (subscription == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate subscription handle");
    return nullptr;
  }
  rcutils_allocator_t allocator = node->context->options.allocator;
  const std::string type_name = type_name_from_type_support(effective_type_support);
  const std::string subscription_id = allocate_subscription_id();
  const std::string endpoint_id = endpoint_id_for_local_id(subscription_id);
  const std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> endpoint_gid =
    make_endpoint_gid(endpoint_id);
  FleetQoxSubscriptionData * data = allocate_data<FleetQoxSubscriptionData>(
    allocator,
    allocator,
    node->context,
    node,
    std::string(topic_name),
    type_name,
    std::string(node->name != nullptr ? node->name : ""),
    std::string(node->namespace_ != nullptr ? node->namespace_ : ""),
    std::string(node->context->options.enclave != nullptr ? node->context->options.enclave : ""),
    node->context->actual_domain_id,
    subscription_id,
    endpoint_id,
    endpoint_gid,
    effective_type_support,
    typed_message_size_from_type_support(effective_type_support),
    adapted_qos,
    std::deque<std::string>{},
    std::unordered_map<std::string, rmw_fleetqox_cpp::SequenceState>{},
    nullptr,
    nullptr,
    std::string{},
    std::vector<std::string>{},
    0,
    std::unordered_set<std::string>{},
    std::unordered_set<std::string>{},
    0,
    0,
    nullptr,
    nullptr,
    0,
    0,
    nullptr,
    nullptr,
    0,
    0,
    RMW_QOS_POLICY_INVALID,
    nullptr,
    nullptr,
    0,
    0,
    nullptr,
    nullptr,
    size_t{0},
    size_t{0},
    nullptr,
    nullptr,
    size_t{0},
    size_t{0},
    size_t{0},
    std::int32_t{0},
    nullptr,
    nullptr);
  if (data == nullptr) {
    rmw_subscription_free(subscription);
    RMW_SET_ERROR_MSG("failed to allocate subscription data");
    return nullptr;
  }

  subscription->implementation_identifier = kIdentifier;
  subscription->data = data;
  subscription->topic_name = data->topic_name.c_str();
  subscription->options = *subscription_options;
  subscription->can_loan_messages =
    introspection_c_members(data->type_support) != nullptr ||
    introspection_cpp_members(data->type_support) != nullptr || data->typed_message_size > 0;
  subscription->is_cft_enabled = false;

  std::vector<EventCallbackNotification> matched_callbacks;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    purge_expired_remote_pubsub_endpoints_locked(
      monotonic_timestamp_ns(), &matched_callbacks);
    g_subscriptions.push_back(data);
    g_subscription_handles.push_back(subscription);
    record_subscription_matched_change_locked(
      data,
      matched_publisher_count_locked(data),
      &matched_callbacks);
    refresh_publication_matched_events_locked(&matched_callbacks);
    record_qos_incompatibilities_for_new_subscription_locked(data, &matched_callbacks);
    record_type_incompatibilities_for_new_subscription_locked(data, &matched_callbacks);
    record_liveliness_for_new_subscription_locked(data, &matched_callbacks);
  }
  notify_event_callbacks(matched_callbacks);
  rmw_fleetqox_cpp_graph_register_subscription_endpoint(
    data->node_name.c_str(),
    data->node_namespace.c_str(),
    data->topic_name.c_str(),
    data->type_name.c_str(),
    data->endpoint_id.c_str(),
    data->endpoint_gid.data(),
    data->endpoint_gid.size(),
    &data->qos,
    data->domain_id);
  send_subscription_graph_advertisement(data, "add");
  ensure_pubsub_graph_renewal_thread();
  if (qos_deadline_enabled(data->qos) ||
    data->qos.reliability == RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT)
  {
    ensure_qos_deadline_monitor_thread();
  }
  return subscription;
}

rmw_ret_t rmw_destroy_subscription(rmw_node_t * node, rmw_subscription_t * subscription)
{
  if (!node_is_valid(node) || subscription == nullptr) {
    RMW_SET_ERROR_MSG("node and subscription must be valid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr || data->owner_node != node) {
    RMW_SET_ERROR_MSG("subscription was not created by the supplied node");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_fleetqox_cpp_graph_unregister_subscription_endpoint(data->endpoint_id.c_str());
  send_subscription_graph_advertisement(data, "remove");
  std::vector<EventCallbackNotification> matched_callbacks;
  {
    std::unique_lock<std::mutex> lock(g_bus_mutex);
    data->destroying = true;
    data->on_new_message_callback = nullptr;
    data->on_new_message_user_data = nullptr;
    data->liveliness_changed_callback = nullptr;
    data->requested_deadline_callback = nullptr;
    data->requested_incompatible_qos_callback = nullptr;
    data->subscription_incompatible_type_callback = nullptr;
    data->message_lost_callback = nullptr;
    data->subscription_matched_callback = nullptr;
    g_subscriptions.erase(
      std::remove(g_subscriptions.begin(), g_subscriptions.end(), data),
      g_subscriptions.end());
    g_subscription_handles.erase(
      std::remove(g_subscription_handles.begin(), g_subscription_handles.end(), subscription),
      g_subscription_handles.end());
    refresh_publication_matched_events_locked(&matched_callbacks);
    g_entity_callback_condition.wait(lock, [data]() {
      return data->inflight_callbacks == 0;
    });
  }
  notify_event_callbacks(matched_callbacks);
  bool stop_deadline_monitor = false;
  bool stop_graph_renewal = false;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    stop_graph_renewal = g_publishers.empty() && g_subscriptions.empty();
    stop_deadline_monitor = g_publishers.empty() && g_subscriptions.empty();
  }
  if (stop_graph_renewal) {
    stop_pubsub_graph_renewal_thread();
  }
  if (stop_deadline_monitor) {
    stop_qos_deadline_monitor_thread();
  }
  release_owner_loans(subscription, LoanOwnerKind::Subscription);
  deallocate_data(data);
  rmw_subscription_free(subscription);
  return RMW_RET_OK;
}

rmw_ret_t rmw_publish(
  const rmw_publisher_t * publisher,
  const void * ros_message,
  rmw_publisher_allocation_t * allocation)
{
  if (publisher == nullptr || ros_message == nullptr) {
    RMW_SET_ERROR_MSG("publisher and ros_message must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(publisher->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("publisher data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  PayloadScratch scratch(
    allocation != nullptr,
    allocation == nullptr ? nullptr : allocation->implementation_identifier,
    allocation == nullptr ? nullptr : allocation->data,
    rmw_fleetqox_cpp::MessageAllocationKind::Publisher,
    data->type_support);
  if (scratch.status() != RMW_RET_OK) {
    return scratch.status();
  }
  auto & payload = scratch.payload();
  const auto * introspection_members = introspection_c_members(data->type_support);
  if (introspection_members != nullptr) {
    if (!serialize_introspection_c_message(introspection_members, ros_message, &payload)) {
      RMW_SET_ERROR_MSG("failed to serialize ROS message with introspection C type support");
      return RMW_RET_UNSUPPORTED;
    }
    return publish_payload(data, payload);
  }
  const auto * introspection_cpp = introspection_cpp_members(data->type_support);
  if (introspection_cpp != nullptr) {
    if (!serialize_introspection_cpp_message(introspection_cpp, ros_message, &payload)) {
      RMW_SET_ERROR_MSG("failed to serialize ROS message with introspection C++ type support");
      return RMW_RET_UNSUPPORTED;
    }
    return publish_payload(data, payload);
  }
  if (data->typed_message_size == 0) {
    RMW_SET_ERROR_MSG("typed rmw_publish requires introspection C/C++ type support or rmw_fleetqox_cpp type-erased descriptor");
    return RMW_RET_UNSUPPORTED;
  }
  const auto * typed_bytes = static_cast<const std::uint8_t *>(ros_message);
  payload.assign(typed_bytes, typed_bytes + data->typed_message_size);
  return publish_payload(data, payload);
}

rmw_ret_t rmw_publish_serialized_message(
  const rmw_publisher_t * publisher,
  const rmw_serialized_message_t * serialized_message,
  rmw_publisher_allocation_t * allocation)
{
  if (publisher == nullptr || serialized_message == nullptr) {
    RMW_SET_ERROR_MSG("publisher and serialized_message must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(publisher->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("publisher data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (serialized_message->buffer_length > 0 && serialized_message->buffer == nullptr) {
    RMW_SET_ERROR_MSG("serialized message buffer is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  PayloadScratch scratch(
    allocation != nullptr,
    allocation == nullptr ? nullptr : allocation->implementation_identifier,
    allocation == nullptr ? nullptr : allocation->data,
    rmw_fleetqox_cpp::MessageAllocationKind::Publisher,
    data->type_support);
  if (scratch.status() != RMW_RET_OK) {
    return scratch.status();
  }
  auto & payload = scratch.payload();
  payload.assign(
    serialized_message->buffer,
    serialized_message->buffer + serialized_message->buffer_length);
  return publish_payload(data, payload);
}

rmw_ret_t rmw_take(
  const rmw_subscription_t * subscription,
  void * ros_message,
  bool * taken,
  rmw_subscription_allocation_t * allocation)
{
  if (subscription == nullptr || ros_message == nullptr || taken == nullptr) {
    RMW_SET_ERROR_MSG("subscription, ros_message, and taken must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  std::lock_guard<std::recursive_mutex> take_lock(data->take_mutex);
  PayloadScratch scratch(
    allocation != nullptr,
    allocation == nullptr ? nullptr : allocation->implementation_identifier,
    allocation == nullptr ? nullptr : allocation->data,
    rmw_fleetqox_cpp::MessageAllocationKind::Subscription,
    data->type_support);
  if (scratch.status() != RMW_RET_OK) {
    return scratch.status();
  }
  auto & payload = scratch.payload();
  const auto * introspection_members = introspection_c_members(data->type_support);
  if (introspection_members != nullptr) {
    ret = take_payload(data, &payload, taken);
    if (trace_take_enabled()) {
      std::fprintf(
        stderr,
        "fleetqox rmw_take topic=%s taken=%s payload_size=%zu introspection=true\n",
        data->topic_name.c_str(),
        *taken ? "true" : "false",
        payload.size());
    }
    if (ret != RMW_RET_OK || !*taken) {
      return ret;
    }
    size_t offset = 0;
    if (!deserialize_introspection_c_message(introspection_members, payload, &offset, ros_message) ||
      offset != payload.size())
    {
      *taken = false;
      if (trace_take_enabled()) {
        std::fprintf(
          stderr,
          "fleetqox rmw_take deserialize_failed topic=%s offset=%zu payload_size=%zu\n",
          data->topic_name.c_str(),
          offset,
          payload.size());
      }
      RMW_SET_ERROR_MSG("failed to deserialize ROS message with introspection C type support");
      return RMW_RET_ERROR;
    }
    if (trace_take_enabled()) {
      std::fprintf(
        stderr,
        "fleetqox rmw_take deserialize_ok topic=%s offset=%zu\n",
        data->topic_name.c_str(),
        offset);
    }
    return RMW_RET_OK;
  }
  const auto * introspection_cpp = introspection_cpp_members(data->type_support);
  if (introspection_cpp != nullptr) {
    ret = take_payload(data, &payload, taken);
    if (ret != RMW_RET_OK || !*taken) {
      return ret;
    }
    size_t offset = 0;
    if (!deserialize_introspection_cpp_message(
        introspection_cpp, payload, &offset, ros_message) || offset != payload.size())
    {
      *taken = false;
      RMW_SET_ERROR_MSG("failed to deserialize ROS message with introspection C++ type support");
      return RMW_RET_ERROR;
    }
    return RMW_RET_OK;
  }
  if (data->typed_message_size == 0) {
    *taken = false;
    RMW_SET_ERROR_MSG("typed rmw_take requires introspection C/C++ type support or rmw_fleetqox_cpp type-erased descriptor");
    return RMW_RET_UNSUPPORTED;
  }
  ret = take_payload(data, &payload, taken);
  if (ret != RMW_RET_OK || !*taken) {
    return ret;
  }
  if (payload.size() != data->typed_message_size) {
    *taken = false;
    RMW_SET_ERROR_MSG("typed FleetRMW payload size does not match descriptor");
    return RMW_RET_ERROR;
  }
  std::memcpy(ros_message, payload.data(), payload.size());
  return RMW_RET_OK;
}

rmw_ret_t rmw_take_with_info(
  const rmw_subscription_t * subscription,
  void * ros_message,
  bool * taken,
  rmw_message_info_t * message_info,
  rmw_subscription_allocation_t * allocation)
{
  if (message_info == nullptr) {
    RMW_SET_ERROR_MSG("message_info must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *message_info = rmw_get_zero_initialized_message_info();
  g_typed_take_message_info = message_info;
  const rmw_ret_t ret = rmw_take(subscription, ros_message, taken, allocation);
  g_typed_take_message_info = nullptr;
  return ret;
}

rmw_ret_t rmw_take_sequence(
  const rmw_subscription_t * subscription,
  size_t count,
  rmw_message_sequence_t * message_sequence,
  rmw_message_info_sequence_t * message_info_sequence,
  size_t * taken,
  rmw_subscription_allocation_t * allocation)
{
  if (subscription == nullptr || message_sequence == nullptr ||
    message_info_sequence == nullptr || taken == nullptr || count == 0)
  {
    RMW_SET_ERROR_MSG("take sequence arguments must be non-null and count must be positive");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const rmw_ret_t identifier_ret = require_identifier(subscription->implementation_identifier);
  if (identifier_ret != RMW_RET_OK) {
    return identifier_ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (message_sequence->capacity < count || message_info_sequence->capacity < count ||
    message_sequence->data == nullptr || message_info_sequence->data == nullptr)
  {
    RMW_SET_ERROR_MSG("take sequence buffers must have capacity for the requested count");
    return RMW_RET_INVALID_ARGUMENT;
  }
  for (size_t index = 0; index < count; ++index) {
    if (message_sequence->data[index] == nullptr) {
      RMW_SET_ERROR_MSG("take sequence ROS message entries must be preallocated");
      return RMW_RET_INVALID_ARGUMENT;
    }
  }

  std::lock_guard<std::recursive_mutex> take_lock(data->take_mutex);
  size_t taken_count = 0;
  for (size_t index = 0; index < count; ++index) {
    bool one_taken = false;
    rmw_message_info_t info = rmw_get_zero_initialized_message_info();
    const rmw_ret_t ret = rmw_take_with_info(
      subscription,
      message_sequence->data[index],
      &one_taken,
      &info,
      allocation);
    if (ret != RMW_RET_OK) {
      *taken = taken_count;
      if (taken_count > 0) {
        message_sequence->size = taken_count;
        message_info_sequence->size = taken_count;
      }
      return ret;
    }
    if (!one_taken) {
      break;
    }
    message_info_sequence->data[index] = info;
    ++taken_count;
  }
  *taken = taken_count;
  if (taken_count > 0) {
    message_sequence->size = taken_count;
    message_info_sequence->size = taken_count;
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_take_serialized_message(
  const rmw_subscription_t * subscription,
  rmw_serialized_message_t * serialized_message,
  bool * taken,
  rmw_subscription_allocation_t * allocation)
{
  if (subscription == nullptr || serialized_message == nullptr || taken == nullptr) {
    RMW_SET_ERROR_MSG("subscription, serialized_message, and taken must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  std::lock_guard<std::recursive_mutex> take_lock(data->take_mutex);
  PayloadScratch scratch(
    allocation != nullptr,
    allocation == nullptr ? nullptr : allocation->implementation_identifier,
    allocation == nullptr ? nullptr : allocation->data,
    rmw_fleetqox_cpp::MessageAllocationKind::Subscription,
    data->type_support);
  if (scratch.status() != RMW_RET_OK) {
    return scratch.status();
  }
  auto & payload = scratch.payload();
  ret = take_payload(data, &payload, taken);
  if (ret != RMW_RET_OK || !*taken) {
    return ret;
  }

  if (payload.size() > serialized_message->buffer_capacity) {
    const auto resize_ret = rmw_serialized_message_resize(serialized_message, payload.size());
    if (resize_ret != RMW_RET_OK) {
      RMW_SET_ERROR_MSG("failed to resize serialized message output");
      return RMW_RET_BAD_ALLOC;
    }
  }
  if (!payload.empty()) {
    std::memcpy(serialized_message->buffer, payload.data(), payload.size());
  }
  serialized_message->buffer_length = payload.size();
  *taken = true;
  return RMW_RET_OK;
}

rmw_ret_t rmw_take_serialized_message_with_info(
  const rmw_subscription_t * subscription,
  rmw_serialized_message_t * serialized_message,
  bool * taken,
  rmw_message_info_t * message_info,
  rmw_subscription_allocation_t * allocation)
{
  if (message_info == nullptr) {
    RMW_SET_ERROR_MSG("message_info must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (subscription == nullptr || serialized_message == nullptr || taken == nullptr) {
    RMW_SET_ERROR_MSG("subscription, serialized_message, and taken must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  std::lock_guard<std::recursive_mutex> take_lock(data->take_mutex);
  *message_info = rmw_get_zero_initialized_message_info();
  PayloadScratch scratch(
    allocation != nullptr,
    allocation == nullptr ? nullptr : allocation->implementation_identifier,
    allocation == nullptr ? nullptr : allocation->data,
    rmw_fleetqox_cpp::MessageAllocationKind::Subscription,
    data->type_support);
  if (scratch.status() != RMW_RET_OK) {
    return scratch.status();
  }
  auto & payload = scratch.payload();
  ret = take_payload(data, &payload, taken, message_info);
  if (ret != RMW_RET_OK || !*taken) {
    return ret;
  }
  if (payload.size() > serialized_message->buffer_capacity) {
    const rmw_ret_t resize_ret =
      rmw_serialized_message_resize(serialized_message, payload.size());
    if (resize_ret != RMW_RET_OK) {
      *taken = false;
      RMW_SET_ERROR_MSG("failed to resize serialized message output with info");
      return RMW_RET_BAD_ALLOC;
    }
  }
  if (!payload.empty()) {
    std::memcpy(serialized_message->buffer, payload.data(), payload.size());
  }
  serialized_message->buffer_length = payload.size();
  return RMW_RET_OK;
}

rmw_ret_t rmw_publisher_count_matched_subscriptions(
  const rmw_publisher_t * publisher,
  size_t * subscription_count)
{
  if (publisher == nullptr || subscription_count == nullptr) {
    RMW_SET_ERROR_MSG("publisher and subscription_count must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(publisher->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("publisher data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *subscription_count = rmw_fleetqox_cpp_graph_subscription_count(
    data->topic_name.c_str(), data->domain_id);
  return RMW_RET_OK;
}

rmw_ret_t rmw_subscription_count_matched_publishers(
  const rmw_subscription_t * subscription,
  size_t * publisher_count)
{
  if (subscription == nullptr || publisher_count == nullptr) {
    RMW_SET_ERROR_MSG("subscription and publisher_count must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *publisher_count = rmw_fleetqox_cpp_graph_publisher_count(
    data->topic_name.c_str(), data->domain_id);
  return RMW_RET_OK;
}

rmw_ret_t rmw_publisher_get_actual_qos(
  const rmw_publisher_t * publisher,
  rmw_qos_profile_t * qos)
{
  if (publisher == nullptr || qos == nullptr) {
    RMW_SET_ERROR_MSG("publisher and qos must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(publisher->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("publisher data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *qos = data->qos;
  return RMW_RET_OK;
}

rmw_ret_t rmw_subscription_get_actual_qos(
  const rmw_subscription_t * subscription,
  rmw_qos_profile_t * qos)
{
  if (subscription == nullptr || qos == nullptr) {
    RMW_SET_ERROR_MSG("subscription and qos must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *qos = data->qos;
  return RMW_RET_OK;
}

rmw_ret_t rmw_fleetqox_cpp_set_publisher_qos_event_callback(
  const rmw_publisher_t * publisher,
  rmw_event_type_t event_type,
  rmw_event_callback_t callback,
  const void * user_data)
{
  if (publisher == nullptr) {
    RMW_SET_ERROR_MSG("publisher is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(publisher->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("publisher data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (event_type != RMW_EVENT_OFFERED_DEADLINE_MISSED &&
    event_type != RMW_EVENT_LIVELINESS_LOST &&
    event_type != RMW_EVENT_OFFERED_QOS_INCOMPATIBLE &&
    event_type != RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE &&
    event_type != RMW_EVENT_PUBLICATION_MATCHED)
  {
    return RMW_RET_OK;
  }
  size_t pending = 0;
  std::vector<EventCallbackNotification> callbacks;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    if (data->destroying) {
      RMW_SET_ERROR_MSG("publisher is being destroyed");
      return RMW_RET_INVALID_ARGUMENT;
    }
    if (event_type == RMW_EVENT_OFFERED_DEADLINE_MISSED) {
      data->offered_deadline_callback = callback;
      data->offered_deadline_user_data = user_data;
      pending = data->offered_deadline_unread_count > 0 ?
        static_cast<size_t>(data->offered_deadline_unread_count) : 0;
    } else if (event_type == RMW_EVENT_LIVELINESS_LOST) {
      data->liveliness_lost_callback = callback;
      data->liveliness_lost_user_data = user_data;
      pending = data->liveliness_lost_total_count_change > 0 ?
        static_cast<size_t>(data->liveliness_lost_total_count_change) : 0;
    } else if (event_type == RMW_EVENT_OFFERED_QOS_INCOMPATIBLE) {
      data->offered_incompatible_qos_callback = callback;
      data->offered_incompatible_qos_user_data = user_data;
      pending = data->offered_incompatible_qos_total_count_change > 0 ?
        static_cast<size_t>(data->offered_incompatible_qos_total_count_change) : 0;
    } else if (event_type == RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE) {
      data->publisher_incompatible_type_callback = callback;
      data->publisher_incompatible_type_user_data = user_data;
      pending = data->publisher_incompatible_type_total_count_change > 0 ?
        static_cast<size_t>(data->publisher_incompatible_type_total_count_change) : 0;
    } else {
      data->publication_matched_callback = callback;
      data->publication_matched_user_data = user_data;
      pending = matched_pending_count(
        data->publication_matched_total_count_change,
        data->publication_matched_current_count_change);
    }
    queue_event_callback_locked(
      &callbacks, callback, user_data, pending, data);
  }
  notify_event_callbacks(callbacks);
  return RMW_RET_OK;
}

rmw_ret_t rmw_fleetqox_cpp_assert_publisher_liveliness(
  const rmw_publisher_t * publisher)
{
  if (publisher == nullptr) {
    RMW_SET_ERROR_MSG("publisher is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(publisher->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("publisher data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  std::vector<EventCallbackNotification> callbacks;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    record_liveliness_assert_locked(data, monotonic_timestamp_ns(), &callbacks);
  }
  notify_event_callbacks(callbacks);
  if (qos_liveliness_manual_by_topic(data->qos)) {
    send_publisher_graph_advertisement(data, "liveliness_assert");
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_fleetqox_cpp_set_subscription_qos_event_callback(
  const rmw_subscription_t * subscription,
  rmw_event_type_t event_type,
  rmw_event_callback_t callback,
  const void * user_data)
{
  if (subscription == nullptr) {
    RMW_SET_ERROR_MSG("subscription is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (event_type != RMW_EVENT_REQUESTED_DEADLINE_MISSED &&
    event_type != RMW_EVENT_LIVELINESS_CHANGED &&
    event_type != RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE &&
    event_type != RMW_EVENT_MESSAGE_LOST &&
    event_type != RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE &&
    event_type != RMW_EVENT_SUBSCRIPTION_MATCHED)
  {
    return RMW_RET_OK;
  }
  size_t pending = 0;
  std::vector<EventCallbackNotification> callbacks;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    if (data->destroying) {
      RMW_SET_ERROR_MSG("subscription is being destroyed");
      return RMW_RET_INVALID_ARGUMENT;
    }
    if (event_type == RMW_EVENT_REQUESTED_DEADLINE_MISSED) {
      data->requested_deadline_callback = callback;
      data->requested_deadline_user_data = user_data;
      pending = data->requested_deadline_unread_count > 0 ?
        static_cast<size_t>(data->requested_deadline_unread_count) : 0;
    } else if (event_type == RMW_EVENT_LIVELINESS_CHANGED) {
      data->liveliness_changed_callback = callback;
      data->liveliness_changed_user_data = user_data;
      pending = liveliness_changed_pending_count(
        data->liveliness_alive_count_change,
        data->liveliness_not_alive_count_change);
    } else if (event_type == RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE) {
      data->requested_incompatible_qos_callback = callback;
      data->requested_incompatible_qos_user_data = user_data;
      pending = data->requested_incompatible_qos_total_count_change > 0 ?
        static_cast<size_t>(data->requested_incompatible_qos_total_count_change) : 0;
    } else if (event_type == RMW_EVENT_MESSAGE_LOST) {
      data->message_lost_callback = callback;
      data->message_lost_user_data = user_data;
      pending = data->message_lost_total_count_change;
    } else if (event_type == RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE) {
      data->subscription_incompatible_type_callback = callback;
      data->subscription_incompatible_type_user_data = user_data;
      pending = data->subscription_incompatible_type_total_count_change > 0 ?
        static_cast<size_t>(data->subscription_incompatible_type_total_count_change) : 0;
    } else {
      data->subscription_matched_callback = callback;
      data->subscription_matched_user_data = user_data;
      pending = matched_pending_count(
        data->subscription_matched_total_count_change,
        data->subscription_matched_current_count_change);
    }
    queue_event_callback_locked(
      &callbacks, callback, user_data, pending, nullptr, data);
  }
  notify_event_callbacks(callbacks);
  return RMW_RET_OK;
}

bool rmw_fleetqox_cpp_publisher_qos_event_has_status(
  const rmw_publisher_t * publisher,
  rmw_event_type_t event_type)
{
  if (publisher == nullptr ||
    !identifier_matches(publisher->implementation_identifier) ||
    (event_type != RMW_EVENT_OFFERED_DEADLINE_MISSED &&
    event_type != RMW_EVENT_LIVELINESS_LOST &&
    event_type != RMW_EVENT_OFFERED_QOS_INCOMPATIBLE &&
    event_type != RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE &&
    event_type != RMW_EVENT_PUBLICATION_MATCHED))
  {
    return false;
  }
  const FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    return false;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  if (event_type == RMW_EVENT_OFFERED_DEADLINE_MISSED) {
    return data->offered_deadline_unread_count > 0;
  }
  if (event_type == RMW_EVENT_LIVELINESS_LOST) {
    return data->liveliness_lost_total_count_change > 0;
  }
  if (event_type == RMW_EVENT_OFFERED_QOS_INCOMPATIBLE) {
    return data->offered_incompatible_qos_total_count_change > 0;
  }
  if (event_type == RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE) {
    return data->publisher_incompatible_type_total_count_change > 0;
  }
  return data->publication_matched_total_count_change > 0 ||
         data->publication_matched_current_count_change != 0;
}

bool rmw_fleetqox_cpp_subscription_qos_event_has_status(
  const rmw_subscription_t * subscription,
  rmw_event_type_t event_type)
{
  if (subscription == nullptr ||
    !identifier_matches(subscription->implementation_identifier) ||
    (event_type != RMW_EVENT_REQUESTED_DEADLINE_MISSED &&
    event_type != RMW_EVENT_LIVELINESS_CHANGED &&
    event_type != RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE &&
    event_type != RMW_EVENT_MESSAGE_LOST &&
    event_type != RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE &&
    event_type != RMW_EVENT_SUBSCRIPTION_MATCHED))
  {
    return false;
  }
  const FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    return false;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  if (event_type == RMW_EVENT_REQUESTED_DEADLINE_MISSED) {
    return data->requested_deadline_unread_count > 0;
  }
  if (event_type == RMW_EVENT_LIVELINESS_CHANGED) {
    return data->liveliness_alive_count_change != 0 ||
           data->liveliness_not_alive_count_change != 0;
  }
  if (event_type == RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE) {
    return data->requested_incompatible_qos_total_count_change > 0;
  }
  if (event_type == RMW_EVENT_MESSAGE_LOST) {
    return data->message_lost_total_count_change > 0;
  }
  if (event_type == RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE) {
    return data->subscription_incompatible_type_total_count_change > 0;
  }
  return data->subscription_matched_total_count_change > 0 ||
         data->subscription_matched_current_count_change != 0;
}

rmw_ret_t rmw_fleetqox_cpp_take_publisher_qos_event(
  const rmw_publisher_t * publisher,
  rmw_event_type_t event_type,
  void * event_info,
  bool * taken)
{
  if (publisher == nullptr || event_info == nullptr || taken == nullptr) {
    RMW_SET_ERROR_MSG("publisher event take arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(publisher->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxPublisherData * data = publisher_data(publisher);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("publisher data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (event_type != RMW_EVENT_OFFERED_DEADLINE_MISSED &&
    event_type != RMW_EVENT_LIVELINESS_LOST &&
    event_type != RMW_EVENT_OFFERED_QOS_INCOMPATIBLE &&
    event_type != RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE &&
    event_type != RMW_EVENT_PUBLICATION_MATCHED)
  {
    *taken = false;
    return RMW_RET_OK;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  if (event_type == RMW_EVENT_OFFERED_DEADLINE_MISSED) {
    auto * status = static_cast<rmw_offered_deadline_missed_status_t *>(event_info);
    status->total_count = data->offered_deadline_total_count;
    status->total_count_change = data->offered_deadline_unread_count;
    *taken = data->offered_deadline_unread_count > 0;
    data->offered_deadline_unread_count = 0;
    return RMW_RET_OK;
  }
  if (event_type == RMW_EVENT_LIVELINESS_LOST) {
    auto * status = static_cast<rmw_liveliness_lost_status_t *>(event_info);
    status->total_count = data->liveliness_lost_total_count;
    status->total_count_change = data->liveliness_lost_total_count_change;
    *taken = data->liveliness_lost_total_count_change > 0;
    data->liveliness_lost_total_count_change = 0;
    return RMW_RET_OK;
  }
  if (event_type == RMW_EVENT_OFFERED_QOS_INCOMPATIBLE) {
    auto * status = static_cast<rmw_offered_qos_incompatible_event_status_t *>(event_info);
    status->total_count = data->offered_incompatible_qos_total_count;
    status->total_count_change = data->offered_incompatible_qos_total_count_change;
    status->last_policy_kind = data->offered_incompatible_qos_last_policy_kind;
    *taken = data->offered_incompatible_qos_total_count_change > 0;
    data->offered_incompatible_qos_total_count_change = 0;
    return RMW_RET_OK;
  }
  if (event_type == RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE) {
    auto * status = static_cast<rmw_incompatible_type_status_t *>(event_info);
    status->total_count = data->publisher_incompatible_type_total_count;
    status->total_count_change = data->publisher_incompatible_type_total_count_change;
    *taken = data->publisher_incompatible_type_total_count_change > 0;
    data->publisher_incompatible_type_total_count_change = 0;
    return RMW_RET_OK;
  }
  auto * status = static_cast<rmw_matched_status_t *>(event_info);
  status->total_count = data->publication_matched_total_count;
  status->total_count_change = data->publication_matched_total_count_change;
  status->current_count = data->publication_matched_current_count;
  status->current_count_change = data->publication_matched_current_count_change;
  *taken = data->publication_matched_total_count_change > 0 ||
    data->publication_matched_current_count_change != 0;
  data->publication_matched_total_count_change = 0;
  data->publication_matched_current_count_change = 0;
  return RMW_RET_OK;
}

rmw_ret_t rmw_fleetqox_cpp_take_subscription_qos_event(
  const rmw_subscription_t * subscription,
  rmw_event_type_t event_type,
  void * event_info,
  bool * taken)
{
  if (subscription == nullptr || event_info == nullptr || taken == nullptr) {
    RMW_SET_ERROR_MSG("subscription event take arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (event_type != RMW_EVENT_REQUESTED_DEADLINE_MISSED &&
    event_type != RMW_EVENT_LIVELINESS_CHANGED &&
    event_type != RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE &&
    event_type != RMW_EVENT_MESSAGE_LOST &&
    event_type != RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE &&
    event_type != RMW_EVENT_SUBSCRIPTION_MATCHED)
  {
    *taken = false;
    return RMW_RET_OK;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  if (event_type == RMW_EVENT_REQUESTED_DEADLINE_MISSED) {
    auto * status = static_cast<rmw_requested_deadline_missed_status_t *>(event_info);
    status->total_count = data->requested_deadline_total_count;
    status->total_count_change = data->requested_deadline_unread_count;
    *taken = data->requested_deadline_unread_count > 0;
    data->requested_deadline_unread_count = 0;
    return RMW_RET_OK;
  }
  if (event_type == RMW_EVENT_LIVELINESS_CHANGED) {
    auto * status = static_cast<rmw_liveliness_changed_status_t *>(event_info);
    status->alive_count = static_cast<std::int32_t>(
      std::min<size_t>(
        data->liveliness_alive_publishers.size(),
        static_cast<size_t>(std::numeric_limits<std::int32_t>::max())));
    status->not_alive_count = static_cast<std::int32_t>(
      std::min<size_t>(
        data->liveliness_not_alive_publishers.size(),
        static_cast<size_t>(std::numeric_limits<std::int32_t>::max())));
    status->alive_count_change = data->liveliness_alive_count_change;
    status->not_alive_count_change = data->liveliness_not_alive_count_change;
    *taken = data->liveliness_alive_count_change != 0 ||
      data->liveliness_not_alive_count_change != 0;
    data->liveliness_alive_count_change = 0;
    data->liveliness_not_alive_count_change = 0;
    return RMW_RET_OK;
  }
  if (event_type == RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE) {
    auto * status = static_cast<rmw_requested_qos_incompatible_event_status_t *>(event_info);
    status->total_count = data->requested_incompatible_qos_total_count;
    status->total_count_change = data->requested_incompatible_qos_total_count_change;
    status->last_policy_kind = data->requested_incompatible_qos_last_policy_kind;
    *taken = data->requested_incompatible_qos_total_count_change > 0;
    data->requested_incompatible_qos_total_count_change = 0;
    return RMW_RET_OK;
  }
  if (event_type == RMW_EVENT_MESSAGE_LOST) {
    auto * status = static_cast<rmw_message_lost_status_t *>(event_info);
    status->total_count = data->message_lost_total_count;
    status->total_count_change = data->message_lost_total_count_change;
    *taken = data->message_lost_total_count_change > 0;
    data->message_lost_total_count_change = 0;
    return RMW_RET_OK;
  }
  if (event_type == RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE) {
    auto * status = static_cast<rmw_incompatible_type_status_t *>(event_info);
    status->total_count = data->subscription_incompatible_type_total_count;
    status->total_count_change = data->subscription_incompatible_type_total_count_change;
    *taken = data->subscription_incompatible_type_total_count_change > 0;
    data->subscription_incompatible_type_total_count_change = 0;
    return RMW_RET_OK;
  }
  auto * status = static_cast<rmw_matched_status_t *>(event_info);
  status->total_count = data->subscription_matched_total_count;
  status->total_count_change = data->subscription_matched_total_count_change;
  status->current_count = data->subscription_matched_current_count;
  status->current_count_change = data->subscription_matched_current_count_change;
  *taken = data->subscription_matched_total_count_change > 0 ||
    data->subscription_matched_current_count_change != 0;
  data->subscription_matched_total_count_change = 0;
  data->subscription_matched_current_count_change = 0;
  return RMW_RET_OK;
}

rmw_ret_t rmw_subscription_set_on_new_message_callback(
  rmw_subscription_t * subscription,
  rmw_event_callback_t callback,
  const void * user_data)
{
  if (subscription == nullptr) {
    RMW_SET_ERROR_MSG("subscription is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  std::lock_guard<std::mutex> lock(g_bus_mutex);
  if (data->destroying) {
    RMW_SET_ERROR_MSG("subscription is being destroyed");
    return RMW_RET_INVALID_ARGUMENT;
  }
  data->on_new_message_callback = callback;
  data->on_new_message_user_data = user_data;
  return RMW_RET_OK;
}

rmw_ret_t rmw_fleetqox_cpp_subscription_set_content_filter(
  rmw_subscription_t * subscription,
  const rmw_subscription_content_filter_options_t * options)
{
  if (subscription == nullptr || options == nullptr) {
    RMW_SET_ERROR_MSG("subscription and content filter options must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (options->expression_parameters.size > 0 && options->expression_parameters.data == nullptr) {
    RMW_SET_ERROR_MSG("content filter expression parameters are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  std::vector<std::string> parameters;
  parameters.reserve(options->expression_parameters.size);
  for (size_t i = 0; i < options->expression_parameters.size; ++i) {
    const char * parameter = options->expression_parameters.data[i];
    parameters.emplace_back(parameter == nullptr ? "" : parameter);
  }
  const std::string expression =
    options->filter_expression == nullptr ? "" : options->filter_expression;
  if (!content_filter_expression_is_valid(expression, parameters)) {
    RMW_SET_ERROR_MSG("content filter expression is invalid or references a missing parameter");
    return RMW_RET_INVALID_ARGUMENT;
  }
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    data->content_filter_expression = expression;
    data->content_filter_parameters = std::move(parameters);
    subscription->is_cft_enabled = !data->content_filter_expression.empty();
  }
  g_content_filters_set.fetch_add(1, std::memory_order_relaxed);
  return RMW_RET_OK;
}

rmw_ret_t rmw_fleetqox_cpp_subscription_get_content_filter(
  const rmw_subscription_t * subscription,
  rcutils_allocator_t * allocator,
  rmw_subscription_content_filter_options_t * options)
{
  if (subscription == nullptr || allocator == nullptr || options == nullptr ||
    !rcutils_allocator_is_valid(allocator))
  {
    RMW_SET_ERROR_MSG("content filter get arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = require_identifier(subscription->implementation_identifier);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxSubscriptionData * data = subscription_data(subscription);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("subscription data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  std::string expression;
  std::vector<std::string> parameters;
  {
    std::lock_guard<std::mutex> lock(g_bus_mutex);
    expression = data->content_filter_expression;
    parameters = data->content_filter_parameters;
  }
  std::vector<const char *> parameter_ptrs;
  parameter_ptrs.reserve(parameters.size());
  for (const std::string & parameter : parameters) {
    parameter_ptrs.push_back(parameter.c_str());
  }
  ret = rmw_subscription_content_filter_options_set(
    expression.c_str(),
    parameter_ptrs.size(),
    parameter_ptrs.empty() ? nullptr : parameter_ptrs.data(),
    allocator,
    options);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  g_content_filters_got.fetch_add(1, std::memory_order_relaxed);
  return RMW_RET_OK;
}

std::uint64_t rmw_fleetqox_cpp_content_filters_set()
{
  return g_content_filters_set.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_content_filters_got()
{
  return g_content_filters_got.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_content_filters_evaluated()
{
  return g_content_filters_evaluated.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_content_filters_matched()
{
  return g_content_filters_matched.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_content_filters_dropped()
{
  return g_content_filters_dropped.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_content_filter_typed_reflections()
{
  return g_content_filter_typed_reflections.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_security_policy_denied()
{
  return g_security_policy_denied.load(std::memory_order_relaxed);
}

bool rmw_fleetqox_cpp_sros2_permissions_xml_loaded()
{
  const Sros2PermissionsPolicy & policy = sros2_permissions_policy();
  return policy.configured && policy.valid;
}

bool rmw_fleetqox_cpp_sros2_signed_permissions_source()
{
  return sros2_permissions_policy().signed_source;
}

bool rmw_fleetqox_cpp_sros2_runtime_signature_verified()
{
  return sros2_permissions_policy().runtime_signature_verified;
}

const char * rmw_fleetqox_cpp_sros2_permissions_xml_error()
{
  return sros2_permissions_policy().error.c_str();
}

bool rmw_fleetqox_cpp_sros2_governance_xml_loaded()
{
  const Sros2GovernancePolicy & policy = sros2_governance_policy();
  return policy.configured && policy.valid;
}

bool rmw_fleetqox_cpp_sros2_signed_governance_source()
{
  return sros2_governance_policy().signed_source;
}

bool rmw_fleetqox_cpp_sros2_governance_runtime_signature_verified()
{
  return sros2_governance_policy().runtime_signature_verified;
}

const char * rmw_fleetqox_cpp_sros2_governance_xml_error()
{
  return sros2_governance_policy().error.c_str();
}

int rmw_fleetqox_cpp_sros2_governance_authorization_decision(
  const char * operation,
  const char * topic_name,
  std::size_t domain_id)
{
  if (operation == nullptr || topic_name == nullptr) {
    return static_cast<int>(Sros2GovernanceDecision::invalid);
  }
  Sros2Operation checked_operation;
  if (std::strcmp(operation, "publish") == 0) {
    checked_operation = Sros2Operation::publish;
  } else if (std::strcmp(operation, "subscribe") == 0) {
    checked_operation = Sros2Operation::subscribe;
  } else {
    return static_cast<int>(Sros2GovernanceDecision::invalid);
  }
  return static_cast<int>(evaluate_sros2_governance_policy(
      checked_operation, topic_name, domain_id));
}

bool rmw_fleetqox_cpp_sros2_identity_credentials_configured()
{
  return sros2_identity_credentials().configured;
}

bool rmw_fleetqox_cpp_sros2_identity_certificate_chain_verified()
{
  return sros2_identity_credentials().certificate_chain_verified;
}

bool rmw_fleetqox_cpp_sros2_identity_private_key_matches()
{
  return sros2_identity_credentials().private_key_matches;
}

const char * rmw_fleetqox_cpp_sros2_identity_subject_common_name()
{
  return sros2_identity_credentials().subject_common_name.c_str();
}

const char * rmw_fleetqox_cpp_sros2_identity_credentials_error()
{
  return sros2_identity_credentials().error.c_str();
}

int rmw_fleetqox_cpp_sros2_identity_validation_decision(const char * enclave)
{
  const Sros2IdentityCredentials & credentials = sros2_identity_credentials();
  if (!credentials.configured) {
    return 0;
  }
  if (!credentials.valid) {
    return 2;
  }
  if (enclave == nullptr || credentials.subject_common_name != enclave) {
    return 3;
  }
  return 1;
}

std::uint64_t rmw_fleetqox_cpp_sros2_permissions_xml_allowed()
{
  return g_sros2_permissions_xml_allowed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_permissions_xml_denied()
{
  return g_sros2_permissions_xml_denied.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_permissions_xml_parse_errors()
{
  return g_sros2_permissions_xml_parse_errors.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_permissions_xml_subscribe_allowed()
{
  return g_sros2_permissions_xml_subscribe_allowed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_permissions_xml_subscribe_denied()
{
  return g_sros2_permissions_xml_subscribe_denied.load(std::memory_order_relaxed);
}

int rmw_fleetqox_cpp_sros2_topic_authorization_decision(
  const char * operation,
  const char * topic_name,
  const char * enclave,
  std::size_t domain_id)
{
  if (operation == nullptr || topic_name == nullptr || enclave == nullptr) {
    return static_cast<int>(SecurityDecision::invalid);
  }
  Sros2Operation checked_operation;
  if (std::strcmp(operation, "publish") == 0) {
    checked_operation = Sros2Operation::publish;
  } else if (std::strcmp(operation, "subscribe") == 0) {
    checked_operation = Sros2Operation::subscribe;
  } else {
    return static_cast<int>(SecurityDecision::invalid);
  }
  return static_cast<int>(evaluate_sros2_topic_policy(
      checked_operation, topic_name, enclave, domain_id));
}

}  // extern "C"
