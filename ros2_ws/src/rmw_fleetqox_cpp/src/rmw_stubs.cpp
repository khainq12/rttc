#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <deque>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <fcntl.h>
#include <limits>
#include <map>
#include <mutex>
#include <new>
#include <string>
#include <thread>
#include <unordered_set>
#include <unistd.h>
#include <vector>

#include "rmw_fleetqox_cpp/data_frame.hpp"
#include "rmw_fleetqox_cpp/message_allocation.hpp"

#include "rcutils/allocator.h"
#include "rcutils/strdup.h"
#include "rosidl_typesupport_c/identifier.h"
#include "rosidl_typesupport_c/message_type_support_dispatch.h"
#include "rosidl_typesupport_c/service_type_support_dispatch.h"
#include "rosidl_typesupport_cpp/identifier.hpp"
#include "rosidl_typesupport_cpp/message_type_support_dispatch.hpp"
#include "rosidl_typesupport_cpp/service_type_support_dispatch.hpp"
#include "rosidl_typesupport_introspection_c/identifier.h"
#include "rosidl_typesupport_introspection_c/message_introspection.h"
#include "rosidl_typesupport_introspection_c/service_introspection.h"
#include "rosidl_typesupport_introspection_cpp/identifier.hpp"
#include "rosidl_typesupport_introspection_cpp/message_introspection.hpp"
#include "rosidl_typesupport_introspection_cpp/service_introspection.hpp"
#include "rmw/allocators.h"
#include "rmw/dynamic_message_type_support.h"
#include "rmw/error_handling.h"
#include "rmw/event.h"
#include "rmw/features.h"
#include "rmw/get_network_flow_endpoints.h"
#include "rmw/get_node_info_and_types.h"
#include "rmw/get_service_names_and_types.h"
#include "rmw/names_and_types.h"
#include "rmw/qos_profiles.h"
#include "rmw/rmw.h"
#include "rmw/serialized_message.h"

extern "C" bool rmw_fleetqox_cpp_publisher_gid(const rmw_publisher_t * publisher, rmw_gid_t * gid);
extern "C" const char * rmw_fleetqox_cpp_socket_bound_endpoint();
extern "C" const char * rmw_fleetqox_cpp_transport_mode();
extern "C" rmw_ret_t rmw_fleetqox_cpp_send_graph_advertisement(
  const char * action,
  const char * entity_kind,
  const char * node_name,
  const char * node_namespace,
  const char * topic_name,
  const char * type_name,
  const char * endpoint_id,
  const rmw_qos_profile_t * qos,
  std::size_t domain_id);
extern "C" rmw_ret_t rmw_fleetqox_cpp_send_encoded_frame(const char * encoded_frame, size_t size);
extern "C" bool rmw_fleetqox_cpp_serialize_introspection_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  const void * ros_message,
  std::vector<std::uint8_t> * payload);
extern "C" bool rmw_fleetqox_cpp_deserialize_introspection_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  const std::vector<std::uint8_t> * payload,
  void * ros_message);
extern "C" bool rmw_fleetqox_cpp_serialize_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  const void * ros_message,
  std::vector<std::uint8_t> * payload);
extern "C" bool rmw_fleetqox_cpp_max_serialized_size_introspection_message(
  const rosidl_typesupport_introspection_c__MessageMembers * members,
  size_t * size);
extern "C" bool rmw_fleetqox_cpp_max_serialized_size_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  size_t * size);
extern "C" rmw_ret_t rmw_fleetqox_cpp_borrow_publisher_loan(
  const rmw_publisher_t * publisher,
  const rosidl_message_type_support_t * type_support,
  void ** ros_message);
extern "C" rmw_ret_t rmw_fleetqox_cpp_release_publisher_loan(
  const rmw_publisher_t * publisher,
  void * ros_message);
extern "C" rmw_ret_t rmw_fleetqox_cpp_borrow_subscription_loan(
  const rmw_subscription_t * subscription,
  void ** ros_message);
extern "C" rmw_ret_t rmw_fleetqox_cpp_release_subscription_loan(
  const rmw_subscription_t * subscription,
  void * ros_message);
extern "C" rmw_ret_t rmw_fleetqox_cpp_subscription_set_content_filter(
  rmw_subscription_t * subscription,
  const rmw_subscription_content_filter_options_t * options);
extern "C" rmw_ret_t rmw_fleetqox_cpp_subscription_get_content_filter(
  const rmw_subscription_t * subscription,
  rcutils_allocator_t * allocator,
  rmw_subscription_content_filter_options_t * options);
extern "C" rmw_ret_t rmw_fleetqox_cpp_set_publisher_qos_event_callback(
  const rmw_publisher_t * publisher,
  rmw_event_type_t event_type,
  rmw_event_callback_t callback,
  const void * user_data);
extern "C" rmw_ret_t rmw_fleetqox_cpp_set_subscription_qos_event_callback(
  const rmw_subscription_t * subscription,
  rmw_event_type_t event_type,
  rmw_event_callback_t callback,
  const void * user_data);
extern "C" rmw_ret_t rmw_fleetqox_cpp_take_publisher_qos_event(
  const rmw_publisher_t * publisher,
  rmw_event_type_t event_type,
  void * event_info,
  bool * taken);
extern "C" rmw_ret_t rmw_fleetqox_cpp_take_subscription_qos_event(
  const rmw_subscription_t * subscription,
  rmw_event_type_t event_type,
  void * event_info,
  bool * taken);
extern "C" bool rmw_fleetqox_cpp_publisher_qos_event_has_status(
  const rmw_publisher_t * publisher,
  rmw_event_type_t event_type);
extern "C" bool rmw_fleetqox_cpp_subscription_qos_event_has_status(
  const rmw_subscription_t * subscription,
  rmw_event_type_t event_type);
extern "C" rmw_ret_t rmw_fleetqox_cpp_assert_publisher_liveliness(
  const rmw_publisher_t * publisher);
extern "C" rmw_ret_t rmw_fleetqox_cpp_publisher_wait_for_all_acked(
  const rmw_publisher_t * publisher,
  rmw_time_t wait_timeout);
extern "C" bool rmw_fleetqox_cpp_deserialize_introspection_cpp_message(
  const rosidl_typesupport_introspection_cpp::MessageMembers * members,
  const std::vector<std::uint8_t> * payload,
  void * ros_message);
extern "C" const rmw_context_t * rmw_fleetqox_cpp_publisher_context(
  const rmw_publisher_t * publisher);
extern "C" const rmw_context_t * rmw_fleetqox_cpp_subscription_context(
  const rmw_subscription_t * subscription);
extern "C" void rmw_fleetqox_cpp_graph_register_service_endpoint(
  const char * node_name,
  const char * node_namespace,
  const char * service_name,
  const char * type_name,
  const char * endpoint_id,
  const rmw_qos_profile_t * qos,
  std::size_t domain_id);
extern "C" void rmw_fleetqox_cpp_graph_unregister_service_endpoint(const char * endpoint_id);
extern "C" void rmw_fleetqox_cpp_graph_register_client_endpoint(
  const char * node_name,
  const char * node_namespace,
  const char * service_name,
  const char * type_name,
  const char * endpoint_id,
  const rmw_qos_profile_t * qos,
  std::size_t domain_id);
extern "C" void rmw_fleetqox_cpp_graph_unregister_client_endpoint(const char * endpoint_id);
extern "C" size_t rmw_fleetqox_cpp_graph_service_count(const char * service_name);
extern "C" size_t rmw_fleetqox_cpp_graph_matching_service_count(
  const char * service_name,
  const char * type_name,
  const rmw_qos_profile_t * client_qos);
extern "C" size_t rmw_fleetqox_cpp_graph_matching_service_count_in_domain(
  const char * service_name,
  const char * type_name,
  const rmw_qos_profile_t * client_qos,
  std::size_t domain_id);
extern "C" bool rmw_fleetqox_cpp_graph_client_matches_service(
  const char * client_endpoint_id,
  const char * service_name,
  const char * type_name,
  const rmw_qos_profile_t * service_qos);
extern "C" bool rmw_fleetqox_cpp_graph_client_matches_service_in_domain(
  const char * client_endpoint_id,
  const char * service_name,
  const char * type_name,
  const rmw_qos_profile_t * service_qos,
  std::size_t domain_id);
extern "C" int rmw_fleetqox_cpp_sros2_topic_authorization_decision(
  const char * operation,
  const char * topic_name,
  const char * enclave,
  std::size_t domain_id);

namespace
{

constexpr const char * kIdentifier = "rmw_fleetqox_cpp";

struct FleetQoxServiceData
{
  rcutils_allocator_t allocator;
  rmw_context_t * context;
  const rmw_node_t * owner_node;
  char * service_name;
  rmw_qos_profile_t qos;
  bool is_service;
  std::string type_name;
  std::string node_name;
  std::string node_namespace;
  std::string enclave;
  std::size_t domain_id;
  std::string endpoint_id;
  std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> endpoint_gid;
  const rosidl_typesupport_introspection_c__ServiceMembers * service_members;
  const rosidl_typesupport_introspection_c__MessageMembers * request_members;
  const rosidl_typesupport_introspection_c__MessageMembers * response_members;
  const rosidl_typesupport_introspection_cpp::ServiceMembers * cpp_service_members;
  const rosidl_typesupport_introspection_cpp::MessageMembers * cpp_request_members;
  const rosidl_typesupport_introspection_cpp::MessageMembers * cpp_response_members;
  rmw_event_callback_t on_new_request_callback;
  const void * on_new_request_user_data;
  rmw_event_callback_t on_new_response_callback;
  const void * on_new_response_user_data;
  std::int64_t next_sequence_id;
  std::deque<rmw_fleetqox_cpp::ServiceFrame> request_queue;
  std::deque<rmw_fleetqox_cpp::ServiceFrame> response_queue;
  std::string last_dequeued_client_endpoint_id;
  std::map<std::string, std::string> pending_response_clients;
  std::map<std::string, std::string> response_replay_cache;
  std::unordered_set<std::string> seen_request_keys;
  std::unordered_set<std::string> seen_response_keys;
  std::deque<std::string> seen_request_order;
  std::deque<std::string> seen_response_order;
  std::deque<std::string> response_replay_order;
  size_t request_queue_limit;
  size_t per_client_request_queue_limit;
  size_t response_queue_limit;
  size_t pending_response_limit;
  size_t dedupe_history_limit;
  size_t response_replay_limit;
  std::int64_t service_priority_aging_ns;
  bool weighted_service_scheduler;
  bool deadline_service_scheduler;
  std::int64_t service_deadline_aging_ns;
  std::map<std::string, std::int64_t> weighted_service_current;
  std::uint64_t service_client_priority;
  std::uint64_t service_client_weight;
  std::uint64_t service_client_deadline_ns;
  std::string durable_replay_path;
  std::unordered_set<std::string> durable_response_keys;
};

struct FleetQoxEventData
{
  const rmw_context_t * context;
  rmw_event_type_t event_type;
  const void * owner;
  bool publisher_event;
  rmw_event_callback_t callback;
  const void * user_data;
};

struct PendingServiceRequestRepair
{
  std::string client_endpoint_id;
  std::int64_t sequence_id;
  std::string encoded_frame;
  std::chrono::steady_clock::time_point next_retry;
  int remaining_retries;
  int interval_ms;
};

void stop_service_request_repair_worker();

struct ServiceRequestRepairShutdownGuard
{
  ~ServiceRequestRepairShutdownGuard();
};

std::mutex g_service_graph_mutex;
std::vector<FleetQoxServiceData *> g_service_graph_endpoints;
std::mutex g_service_bus_mutex;
std::vector<FleetQoxServiceData *> g_service_bus_endpoints;
std::vector<rmw_service_t *> g_service_handles;
std::vector<rmw_client_t *> g_client_handles;
std::atomic<bool> g_service_graph_renewal_started{false};
std::atomic<bool> g_service_graph_renewal_running{false};
std::thread g_service_graph_renewal_thread;
std::mutex g_service_graph_renewal_lifecycle_mutex;
std::once_flag g_service_graph_renewal_atexit_once;
std::atomic<std::uint64_t> g_next_service_endpoint_id{1};
std::atomic<std::uint64_t> g_next_client_endpoint_id{1};
std::atomic<std::uint64_t> g_service_expired_frames_dropped{0};
std::atomic<std::uint64_t> g_service_frames_received{0};
std::atomic<std::uint64_t> g_publisher_allocations_initialized{0};
std::atomic<std::uint64_t> g_publisher_allocations_finalized{0};
std::atomic<std::uint64_t> g_subscription_allocations_initialized{0};
std::atomic<std::uint64_t> g_subscription_allocations_finalized{0};
std::atomic<std::uint64_t> g_qos_events_initialized{0};
std::atomic<std::uint64_t> g_qos_events_finalized{0};
std::atomic<std::uint64_t> g_qos_event_callbacks_set{0};
std::atomic<std::uint64_t> g_sros2_service_request_publish_allowed{0};
std::atomic<std::uint64_t> g_sros2_service_request_publish_denied{0};
std::atomic<std::uint64_t> g_sros2_service_request_subscribe_allowed{0};
std::atomic<std::uint64_t> g_sros2_service_request_subscribe_denied{0};
std::atomic<std::uint64_t> g_sros2_service_response_publish_allowed{0};
std::atomic<std::uint64_t> g_sros2_service_response_publish_denied{0};
std::atomic<std::uint64_t> g_sros2_service_response_subscribe_allowed{0};
std::atomic<std::uint64_t> g_sros2_service_response_subscribe_denied{0};
std::atomic<std::uint64_t> g_sros2_service_authorization_parse_errors{0};
std::mutex g_service_request_repair_mutex;
std::condition_variable g_service_request_repair_cv;
std::vector<PendingServiceRequestRepair> g_pending_service_request_repairs;
std::thread g_service_request_repair_thread;
bool g_service_request_repair_stop{false};
std::atomic<std::uint64_t> g_service_request_repairs_scheduled{0};
std::atomic<std::uint64_t> g_service_request_retries_sent{0};
std::atomic<std::uint64_t> g_service_request_repairs_cancelled{0};
std::atomic<std::uint64_t> g_service_request_repairs_exhausted{0};
std::atomic<std::uint64_t> g_service_request_repair_global_admission_rejections{0};
std::atomic<std::uint64_t> g_service_request_repair_client_admission_rejections{0};
std::atomic<std::uint64_t> g_service_request_repair_pending_max_observed{0};
std::atomic<std::uint64_t> g_service_request_queue_resource_drops{0};
std::atomic<std::uint64_t> g_service_request_per_client_resource_drops{0};
std::atomic<std::uint64_t> g_service_response_queue_resource_drops{0};
std::atomic<std::uint64_t> g_service_pending_response_backpressure{0};
std::atomic<std::uint64_t> g_service_request_dedupe_evictions{0};
std::atomic<std::uint64_t> g_service_response_dedupe_evictions{0};
std::atomic<std::uint64_t> g_service_response_replay_evictions{0};
std::atomic<std::uint64_t> g_service_request_queue_max_observed{0};
std::atomic<std::uint64_t> g_service_request_per_client_max_observed{0};
std::atomic<std::uint64_t> g_service_response_queue_max_observed{0};
std::atomic<std::uint64_t> g_service_pending_response_max_observed{0};
std::atomic<std::uint64_t> g_service_response_replay_max_observed{0};
std::atomic<std::uint64_t> g_service_priority_dequeues{0};
std::atomic<std::uint64_t> g_service_aged_priority_dequeues{0};
std::atomic<std::uint64_t> g_service_weighted_dequeues{0};
std::atomic<std::uint64_t> g_service_deadline_dequeues{0};
std::atomic<std::uint64_t> g_service_deadline_aged_dequeues{0};
std::atomic<std::uint64_t> g_service_durable_replays_loaded{0};
std::atomic<std::uint64_t> g_service_durable_replays_persisted{0};
std::atomic<std::uint64_t> g_service_durable_replays_sent{0};
std::atomic<std::uint64_t> g_service_durable_replay_failures{0};
std::mutex g_service_durable_replay_mutex;
ServiceRequestRepairShutdownGuard g_service_request_repair_shutdown_guard;
std::mutex g_event_mutex;
std::vector<rmw_event_t *> g_event_handles;
std::vector<FleetQoxEventData *> g_event_data;
std::mutex g_dynamic_serialization_library_mutex;
std::vector<void *> g_dynamic_serialization_library_handles;

bool identifier_matches(const char * identifier)
{
  return identifier != nullptr && std::strcmp(identifier, kIdentifier) == 0;
}

bool trace_service_enabled()
{
  const char * value = std::getenv("FLEETQOX_RMW_TRACE_SERVICE");
  return value != nullptr && value[0] != '\0' && std::strcmp(value, "0") != 0;
}

std::string sros2_service_topic(const char * service_name, bool request)
{
  return std::string(request ? "rq" : "rr") +
         (service_name == nullptr ? "" : service_name) +
         (request ? "Request" : "Reply");
}

bool sros2_service_operation_allowed(
  const FleetQoxServiceData * data,
  bool publish,
  bool request)
{
  if (data == nullptr || data->service_name == nullptr) {
    return false;
  }
  const std::string topic = sros2_service_topic(data->service_name, request);
  const int decision = rmw_fleetqox_cpp_sros2_topic_authorization_decision(
    publish ? "publish" : "subscribe",
    topic.c_str(),
    data->enclave.c_str(),
    data->domain_id);
  if (decision == 0) {
    return true;
  }

  std::atomic<std::uint64_t> * allowed_counter = nullptr;
  std::atomic<std::uint64_t> * denied_counter = nullptr;
  if (request && publish) {
    allowed_counter = &g_sros2_service_request_publish_allowed;
    denied_counter = &g_sros2_service_request_publish_denied;
  } else if (request) {
    allowed_counter = &g_sros2_service_request_subscribe_allowed;
    denied_counter = &g_sros2_service_request_subscribe_denied;
  } else if (publish) {
    allowed_counter = &g_sros2_service_response_publish_allowed;
    denied_counter = &g_sros2_service_response_publish_denied;
  } else {
    allowed_counter = &g_sros2_service_response_subscribe_allowed;
    denied_counter = &g_sros2_service_response_subscribe_denied;
  }
  if (decision == 1) {
    allowed_counter->fetch_add(1, std::memory_order_relaxed);
    return true;
  }
  denied_counter->fetch_add(1, std::memory_order_relaxed);
  if (decision == 3) {
    g_sros2_service_authorization_parse_errors.fetch_add(1, std::memory_order_relaxed);
  }
  return false;
}

void trace_service_event(
  const char * event,
  const FleetQoxServiceData * data,
  const rmw_fleetqox_cpp::ServiceFrame * frame = nullptr,
  size_t queue_size = 0)
{
  if (!trace_service_enabled()) {
    return;
  }
  std::fprintf(
    stderr,
    "fleetqox service event=%s service=%s endpoint=%s is_service=%s",
    event == nullptr ? "unknown" : event,
    data != nullptr && data->service_name != nullptr ? data->service_name : "",
    data != nullptr ? data->endpoint_id.c_str() : "",
    data != nullptr && data->is_service ? "true" : "false");
  if (frame != nullptr) {
    std::fprintf(
      stderr,
      " role=%s client=%s service_endpoint=%s seq=%ld priority=%lu weight=%lu deadline_ns=%lu payload=%zu queue=%zu",
      frame->role.c_str(),
      frame->client_endpoint_id.c_str(),
      frame->service_endpoint_id.c_str(),
      static_cast<long>(frame->sequence_id),
      static_cast<unsigned long>(frame->client_priority),
      static_cast<unsigned long>(frame->client_weight),
      static_cast<unsigned long>(frame->request_deadline_ns),
      frame->serialized_payload.size(),
      queue_size);
  }
  std::fprintf(stderr, "\n");
}

rmw_ret_t require_identifier(const char * identifier, const char * entity_name)
{
  if (!identifier_matches(identifier)) {
    RMW_SET_ERROR_MSG(entity_name);
    return RMW_RET_INCORRECT_RMW_IMPLEMENTATION;
  }
  return RMW_RET_OK;
}

rmw_ret_t unsupported(const char * message)
{
  RMW_SET_ERROR_MSG(message);
  return RMW_RET_UNSUPPORTED;
}

bool publisher_event_type_supported(rmw_event_type_t event_type)
{
  return event_type == RMW_EVENT_LIVELINESS_LOST ||
         event_type == RMW_EVENT_OFFERED_DEADLINE_MISSED ||
         event_type == RMW_EVENT_OFFERED_QOS_INCOMPATIBLE ||
         event_type == RMW_EVENT_PUBLISHER_INCOMPATIBLE_TYPE ||
         event_type == RMW_EVENT_PUBLICATION_MATCHED;
}

bool subscription_event_type_supported(rmw_event_type_t event_type)
{
  return event_type == RMW_EVENT_LIVELINESS_CHANGED ||
         event_type == RMW_EVENT_REQUESTED_DEADLINE_MISSED ||
         event_type == RMW_EVENT_REQUESTED_QOS_INCOMPATIBLE ||
         event_type == RMW_EVENT_MESSAGE_LOST ||
         event_type == RMW_EVENT_SUBSCRIPTION_INCOMPATIBLE_TYPE ||
         event_type == RMW_EVENT_SUBSCRIPTION_MATCHED;
}

bool qos_event_type_supported(rmw_event_type_t event_type)
{
  return publisher_event_type_supported(event_type) ||
         subscription_event_type_supported(event_type);
}

FleetQoxEventData * event_data(const rmw_event_t * event)
{
  return event == nullptr ? nullptr : static_cast<FleetQoxEventData *>(event->data);
}

FleetQoxEventData * event_data_from_waitable_locked(const void * waitable)
{
  if (waitable == nullptr) {
    return nullptr;
  }
  for (FleetQoxEventData * data : g_event_data) {
    if (data == waitable) {
      return data;
    }
  }
  for (const rmw_event_t * handle : g_event_handles) {
    if (handle == waitable) {
      return event_data(handle);
    }
  }
  return nullptr;
}

rmw_ret_t init_event(
  rmw_event_t * rmw_event,
  rmw_event_type_t event_type,
  const void * owner,
  bool publisher_event)
{
  if (!qos_event_type_supported(event_type)) {
    return unsupported("QoS event type is not supported by rmw_fleetqox_cpp");
  }
  if (owner == nullptr) {
    RMW_SET_ERROR_MSG("event owner is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (!rcutils_allocator_is_valid(&allocator)) {
    RMW_SET_ERROR_MSG("default allocator is invalid");
    return RMW_RET_ERROR;
  }
  void * memory = allocator.allocate(sizeof(FleetQoxEventData), allocator.state);
  if (memory == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate FleetRMW event data");
    return RMW_RET_BAD_ALLOC;
  }
  const rmw_context_t * context = publisher_event ?
    rmw_fleetqox_cpp_publisher_context(static_cast<const rmw_publisher_t *>(owner)) :
    rmw_fleetqox_cpp_subscription_context(static_cast<const rmw_subscription_t *>(owner));
  if (context == nullptr) {
    allocator.deallocate(memory, allocator.state);
    RMW_SET_ERROR_MSG("event owner context is unavailable");
    return RMW_RET_INVALID_ARGUMENT;
  }
  auto * data = new (memory) FleetQoxEventData{
    context, event_type, owner, publisher_event, nullptr, nullptr};
  rmw_event->implementation_identifier = kIdentifier;
  rmw_event->data = data;
  rmw_event->event_type = event_type;
  {
    std::lock_guard<std::mutex> lock(g_event_mutex);
    g_event_handles.push_back(rmw_event);
    g_event_data.push_back(data);
  }
  g_qos_events_initialized.fetch_add(1, std::memory_order_relaxed);
  return RMW_RET_OK;
}

rmw_ret_t validate_node(const rmw_node_t * node)
{
  if (node == nullptr) {
    RMW_SET_ERROR_MSG("node is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return require_identifier(node->implementation_identifier, "node is not from rmw_fleetqox_cpp");
}

rmw_ret_t validate_publisher(const rmw_publisher_t * publisher)
{
  if (publisher == nullptr) {
    RMW_SET_ERROR_MSG("publisher is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return require_identifier(
    publisher->implementation_identifier,
    "publisher is not from rmw_fleetqox_cpp");
}

rmw_ret_t validate_subscription(const rmw_subscription_t * subscription)
{
  if (subscription == nullptr) {
    RMW_SET_ERROR_MSG("subscription is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return require_identifier(
    subscription->implementation_identifier,
    "subscription is not from rmw_fleetqox_cpp");
}

rmw_ret_t validate_client(const rmw_client_t * client)
{
  if (client == nullptr) {
    RMW_SET_ERROR_MSG("client is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return require_identifier(client->implementation_identifier, "client is not from rmw_fleetqox_cpp");
}

rmw_ret_t validate_service(const rmw_service_t * service)
{
  if (service == nullptr) {
    RMW_SET_ERROR_MSG("service is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return require_identifier(service->implementation_identifier, "service is not from rmw_fleetqox_cpp");
}

FleetQoxServiceData * service_data(const rmw_service_t * service)
{
  return service == nullptr ? nullptr : static_cast<FleetQoxServiceData *>(service->data);
}

FleetQoxServiceData * client_data(const rmw_client_t * client)
{
  return client == nullptr ? nullptr : static_cast<FleetQoxServiceData *>(client->data);
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

std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> endpoint_gid_from_id(const std::string & endpoint_id)
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

std::int64_t monotonic_timestamp_ns()
{
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::nanoseconds>(now).count();
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

bool service_frame_exceeds_lifespan(const rmw_fleetqox_cpp::ServiceFrame & frame)
{
  return rmw_fleetqox_cpp::service_frame_expired(frame, monotonic_timestamp_ns());
}

bool drop_if_expired_service_frame(const rmw_fleetqox_cpp::ServiceFrame & frame)
{
  if (!service_frame_exceeds_lifespan(frame)) {
    return false;
  }
  g_service_expired_frames_dropped.fetch_add(1);
  return true;
}

std::string request_key(const std::uint8_t * writer_guid, std::int64_t sequence_number)
{
  static constexpr char kHex[] = "0123456789abcdef";
  std::string key;
  key.reserve((RMW_GID_STORAGE_SIZE * 2) + 24);
  for (size_t i = 0; i < RMW_GID_STORAGE_SIZE; ++i) {
    const std::uint8_t byte = writer_guid[i];
    key.push_back(kHex[(byte >> 4) & 0x0F]);
    key.push_back(kHex[byte & 0x0F]);
  }
  key.push_back(':');
  key += std::to_string(sequence_number);
  return key;
}

std::string request_key(const rmw_request_id_t & request_id)
{
  return request_key(request_id.writer_guid, request_id.sequence_number);
}

std::string service_frame_dedupe_key(const rmw_fleetqox_cpp::ServiceFrame & frame)
{
  return frame.client_endpoint_id + "|" + frame.service_endpoint_id + "|" +
         frame.role + "|" + std::to_string(frame.sequence_id);
}

std::string service_response_replay_key(
  const std::string & client_endpoint_id,
  std::int64_t sequence_id)
{
  return client_endpoint_id + "|" + std::to_string(sequence_id);
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

bool weighted_service_scheduler_enabled()
{
  const char * raw = std::getenv("FLEETQOX_RMW_SERVICE_SCHEDULER");
  return raw != nullptr &&
         (std::strcmp(raw, "weighted") == 0 ||
         std::strcmp(raw, "weighted_fair") == 0);
}

bool deadline_service_scheduler_enabled()
{
  const char * raw = std::getenv("FLEETQOX_RMW_SERVICE_SCHEDULER");
  return raw != nullptr &&
         (std::strcmp(raw, "deadline") == 0 ||
         std::strcmp(raw, "edf") == 0);
}

size_t service_resource_limit(
  const char * name,
  size_t default_value,
  size_t max_value = 65536)
{
  const size_t bounded_default = std::max<size_t>(
    1, std::min(default_value, max_value));
  return static_cast<size_t>(std::max(
      1, parse_nonnegative_int_env(
        name, static_cast<int>(bounded_default), static_cast<int>(max_value))));
}

void update_max_observed(std::atomic<std::uint64_t> * maximum, size_t value)
{
  if (maximum == nullptr) {
    return;
  }
  std::uint64_t observed = maximum->load(std::memory_order_relaxed);
  const std::uint64_t candidate = static_cast<std::uint64_t>(value);
  while (observed < candidate &&
    !maximum->compare_exchange_weak(
      observed, candidate, std::memory_order_relaxed, std::memory_order_relaxed))
  {
  }
}

void remember_bounded_service_key(
  std::unordered_set<std::string> * keys,
  std::deque<std::string> * order,
  const std::string & key,
  size_t limit,
  std::atomic<std::uint64_t> * eviction_counter)
{
  if (keys == nullptr || order == nullptr || !keys->insert(key).second) {
    return;
  }
  order->push_back(key);
  while (order->size() > limit) {
    keys->erase(order->front());
    order->pop_front();
    if (eviction_counter != nullptr) {
      eviction_counter->fetch_add(1, std::memory_order_relaxed);
    }
  }
}

void store_bounded_service_response_replay(
  FleetQoxServiceData * data,
  const std::string & key,
  const std::string & encoded)
{
  if (data == nullptr) {
    return;
  }
  const bool new_key = data->response_replay_cache.find(key) ==
    data->response_replay_cache.end();
  data->response_replay_cache[key] = encoded;
  if (new_key) {
    data->response_replay_order.push_back(key);
  }
  while (data->response_replay_order.size() > data->response_replay_limit) {
    const std::string evicted = data->response_replay_order.front();
    data->response_replay_cache.erase(evicted);
    data->durable_response_keys.erase(evicted);
    data->response_replay_order.pop_front();
    g_service_response_replay_evictions.fetch_add(1, std::memory_order_relaxed);
  }
  update_max_observed(
    &g_service_response_replay_max_observed, data->response_replay_cache.size());
}

std::string durable_service_replay_path(
  const char * service_name,
  const std::string & type_name,
  std::size_t domain_id)
{
  const char * raw = std::getenv("FLEETQOX_RMW_SERVICE_DURABLE_REPLAY_DIR");
  if (raw == nullptr || raw[0] == '\0' || service_name == nullptr) {
    return {};
  }
  const std::filesystem::path directory(raw);
  std::error_code error;
  std::filesystem::create_directories(directory, error);
  if (error || !std::filesystem::is_directory(directory, error)) {
    g_service_durable_replay_failures.fetch_add(1, std::memory_order_relaxed);
    return {};
  }
  const std::uint64_t identity = fnv1a64(
    std::to_string(domain_id) + "|" + service_name + "|" + type_name,
    1469598103934665603ULL);
  return (directory / ("service-" + std::to_string(identity) + ".replay")).string();
}

std::string durable_service_replay_snapshot(const FleetQoxServiceData * data)
{
  if (data == nullptr) {
    return {};
  }
  std::string snapshot = "FLEETQOX_SERVICE_REPLAY_V1\n";
  static constexpr char kHex[] = "0123456789abcdef";
  for (const std::string & key : data->response_replay_order) {
    const auto found = data->response_replay_cache.find(key);
    if (found == data->response_replay_cache.end() ||
      key.find_first_of("\t\n") != std::string::npos)
    {
      continue;
    }
    snapshot += key;
    snapshot.push_back('\t');
    for (const unsigned char byte : found->second) {
      snapshot.push_back(kHex[(byte >> 4) & 0x0Fu]);
      snapshot.push_back(kHex[byte & 0x0Fu]);
    }
    snapshot.push_back('\n');
  }
  return snapshot;
}

int durable_replay_hex_value(char value)
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

bool decode_durable_replay_hex(
  const std::string & encoded,
  std::string * decoded)
{
  if (decoded == nullptr || encoded.size() % 2 != 0) {
    return false;
  }
  decoded->clear();
  decoded->reserve(encoded.size() / 2);
  for (std::size_t index = 0; index < encoded.size(); index += 2) {
    const int high = durable_replay_hex_value(encoded[index]);
    const int low = durable_replay_hex_value(encoded[index + 1]);
    if (high < 0 || low < 0) {
      decoded->clear();
      return false;
    }
    decoded->push_back(static_cast<char>((high << 4) | low));
  }
  return true;
}

bool persist_durable_service_replay(
  const std::string & path,
  const std::string & snapshot)
{
  if (path.empty() || snapshot.empty()) {
    return false;
  }
  const std::string temporary =
    path + ".tmp." + std::to_string(static_cast<long long>(::getpid()));
  const int descriptor = ::open(
    temporary.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
  if (descriptor < 0) {
    return false;
  }
  std::size_t offset = 0;
  bool ok = true;
  while (offset < snapshot.size()) {
    const ssize_t written = ::write(
      descriptor, snapshot.data() + offset, snapshot.size() - offset);
    if (written < 0 && errno == EINTR) {
      continue;
    }
    if (written <= 0) {
      ok = false;
      break;
    }
    offset += static_cast<std::size_t>(written);
  }
  if (ok && ::fsync(descriptor) != 0) {
    ok = false;
  }
  if (::close(descriptor) != 0) {
    ok = false;
  }
  if (ok && ::rename(temporary.c_str(), path.c_str()) != 0) {
    ok = false;
  }
  if (!ok) {
    (void)::unlink(temporary.c_str());
    return false;
  }
  const std::filesystem::path parent = std::filesystem::path(path).parent_path();
  const int directory_descriptor = ::open(parent.c_str(), O_RDONLY);
  if (directory_descriptor >= 0) {
    if (::fsync(directory_descriptor) != 0) {
      ok = false;
    }
    (void)::close(directory_descriptor);
  }
  return ok;
}

std::size_t load_durable_service_replays(FleetQoxServiceData * data)
{
  if (data == nullptr || data->durable_replay_path.empty() ||
    data->service_name == nullptr)
  {
    return 0;
  }
  std::ifstream input(data->durable_replay_path, std::ios::binary);
  if (!input) {
    return 0;
  }
  std::string line;
  if (!std::getline(input, line) || line != "FLEETQOX_SERVICE_REPLAY_V1") {
    g_service_durable_replay_failures.fetch_add(1, std::memory_order_relaxed);
    return 0;
  }
  std::size_t loaded = 0;
  while (loaded < data->response_replay_limit && std::getline(input, line)) {
    const std::size_t separator = line.find('\t');
    if (separator == std::string::npos) {
      continue;
    }
    const std::string key = line.substr(0, separator);
    std::string encoded;
    if (!decode_durable_replay_hex(line.substr(separator + 1), &encoded)) {
      continue;
    }
    const auto frame = rmw_fleetqox_cpp::decode_service_frame(encoded);
    if (!frame || frame->role != "response" ||
      frame->domain_id != data->domain_id ||
      frame->service_name != data->service_name ||
      frame->type_name != data->type_name ||
      key != service_response_replay_key(
        frame->client_endpoint_id, frame->sequence_id))
    {
      continue;
    }
    rmw_fleetqox_cpp::ServiceFrame rebound = *frame;
    rebound.service_endpoint_id = data->endpoint_id;
    const std::string rebound_encoded =
      rmw_fleetqox_cpp::encode_service_frame(rebound);
    store_bounded_service_response_replay(data, key, rebound_encoded);
    data->durable_response_keys.insert(key);
    remember_bounded_service_key(
      &data->seen_request_keys,
      &data->seen_request_order,
      rebound.client_endpoint_id + "||request|" +
      std::to_string(rebound.sequence_id),
      data->dedupe_history_limit,
      &g_service_request_dedupe_evictions);
    ++loaded;
  }
  g_service_durable_replays_loaded.fetch_add(loaded, std::memory_order_relaxed);
  return loaded;
}

std::uint64_t effective_service_priority(
  const rmw_fleetqox_cpp::ServiceFrame & frame,
  std::int64_t now_ns,
  std::int64_t aging_ns)
{
  std::uint64_t effective = frame.client_priority;
  if (aging_ns <= 0 || frame.local_enqueue_timestamp_ns <= 0 ||
    now_ns <= frame.local_enqueue_timestamp_ns)
  {
    return effective;
  }
  const std::uint64_t age_quanta = static_cast<std::uint64_t>(
    (now_ns - frame.local_enqueue_timestamp_ns) / aging_ns);
  const std::uint64_t available =
    std::numeric_limits<std::uint64_t>::max() - effective;
  return effective + std::min(age_quanta, available);
}

std::uint64_t effective_service_deadline(
  const rmw_fleetqox_cpp::ServiceFrame & frame,
  std::int64_t deadline_aging_ns)
{
  const std::uint64_t relative_deadline =
    frame.request_deadline_ns > 0 ?
    frame.request_deadline_ns :
    static_cast<std::uint64_t>(std::max<std::int64_t>(0, deadline_aging_ns));
  if (frame.local_enqueue_timestamp_ns <= 0 || relative_deadline == 0) {
    return std::numeric_limits<std::uint64_t>::max();
  }
  const std::uint64_t enqueued =
    static_cast<std::uint64_t>(frame.local_enqueue_timestamp_ns);
  if (std::numeric_limits<std::uint64_t>::max() - enqueued < relative_deadline) {
    return std::numeric_limits<std::uint64_t>::max();
  }
  return enqueued + relative_deadline;
}

void trace_service_request_repair_event(
  const char * event,
  const std::string & client_endpoint_id,
  std::int64_t sequence_id,
  int remaining_retries)
{
  if (!trace_service_enabled()) {
    return;
  }
  std::fprintf(
    stderr,
    "fleetqox service repair event=%s client=%s seq=%ld remaining=%d\n",
    event == nullptr ? "unknown" : event,
    client_endpoint_id.c_str(),
    static_cast<long>(sequence_id),
    remaining_retries);
}

void service_request_repair_worker()
{
  std::unique_lock<std::mutex> lock(g_service_request_repair_mutex);
  while (!g_service_request_repair_stop) {
    if (g_pending_service_request_repairs.empty()) {
      g_service_request_repair_cv.wait(
        lock, []() {
          return g_service_request_repair_stop ||
                 !g_pending_service_request_repairs.empty();
        });
      continue;
    }

    const auto earliest = std::min_element(
      g_pending_service_request_repairs.begin(),
      g_pending_service_request_repairs.end(),
      [](const PendingServiceRequestRepair & lhs, const PendingServiceRequestRepair & rhs) {
        return lhs.next_retry < rhs.next_retry;
      });
    const auto now = std::chrono::steady_clock::now();
    if (earliest->next_retry > now) {
      g_service_request_repair_cv.wait_until(lock, earliest->next_retry);
      continue;
    }

    const std::string client_endpoint_id = earliest->client_endpoint_id;
    const std::int64_t sequence_id = earliest->sequence_id;
    if (earliest->remaining_retries <= 0) {
      g_pending_service_request_repairs.erase(earliest);
      g_service_request_repairs_exhausted.fetch_add(1, std::memory_order_relaxed);
      lock.unlock();
      trace_service_request_repair_event("exhausted", client_endpoint_id, sequence_id, 0);
      lock.lock();
      continue;
    }

    const std::string encoded_frame = earliest->encoded_frame;
    --earliest->remaining_retries;
    const int remaining_retries = earliest->remaining_retries;
    earliest->next_retry =
      now + std::chrono::milliseconds(earliest->interval_ms);
    lock.unlock();
    const rmw_ret_t ret =
      rmw_fleetqox_cpp_send_encoded_frame(encoded_frame.data(), encoded_frame.size());
    if (ret == RMW_RET_OK) {
      g_service_request_retries_sent.fetch_add(1, std::memory_order_relaxed);
      trace_service_request_repair_event(
        "retry", client_endpoint_id, sequence_id, remaining_retries);
    } else {
      trace_service_request_repair_event(
        "retry_send_failed", client_endpoint_id, sequence_id, remaining_retries);
    }
    lock.lock();
  }
}

bool schedule_service_request_repair(
  const std::string & client_endpoint_id,
  std::int64_t sequence_id,
  const std::string & encoded_frame)
{
  const int retries = parse_nonnegative_int_env(
    "FLEETQOX_RMW_SERVICE_REQUEST_REPEATS", 5, 5);
  if (retries <= 0) {
    return true;
  }
  const int interval_ms = parse_nonnegative_int_env(
    "FLEETQOX_RMW_SERVICE_REQUEST_REPEAT_INTERVAL_MS", 100, 100);
  const size_t pending_limit = service_resource_limit(
    "FLEETQOX_RMW_SERVICE_REQUEST_REPAIR_PENDING_LIMIT", 4096);
  const size_t per_client_pending_limit = service_resource_limit(
    "FLEETQOX_RMW_SERVICE_REQUEST_REPAIR_PER_CLIENT_PENDING_LIMIT", 64);
  {
    std::lock_guard<std::mutex> lock(g_service_request_repair_mutex);
    const size_t client_pending = static_cast<size_t>(std::count_if(
        g_pending_service_request_repairs.begin(),
        g_pending_service_request_repairs.end(),
        [&client_endpoint_id](const PendingServiceRequestRepair & repair) {
          return repair.client_endpoint_id == client_endpoint_id;
        }));
    if (client_pending >= per_client_pending_limit) {
      g_service_request_repair_client_admission_rejections.fetch_add(
        1, std::memory_order_relaxed);
      trace_service_request_repair_event(
        "client_admission_rejected",
        client_endpoint_id,
        sequence_id,
        retries);
      return false;
    }
    if (g_pending_service_request_repairs.size() >= pending_limit) {
      g_service_request_repair_global_admission_rejections.fetch_add(
        1, std::memory_order_relaxed);
      trace_service_request_repair_event(
        "global_admission_rejected",
        client_endpoint_id,
        sequence_id,
        retries);
      return false;
    }
    if (!g_service_request_repair_thread.joinable()) {
      g_service_request_repair_stop = false;
      try {
        g_service_request_repair_thread = std::thread(service_request_repair_worker);
      } catch (...) {
        return false;
      }
    }
    g_pending_service_request_repairs.push_back(
      PendingServiceRequestRepair{
        client_endpoint_id,
        sequence_id,
        encoded_frame,
        std::chrono::steady_clock::now() + std::chrono::milliseconds(interval_ms),
        retries,
        interval_ms});
    update_max_observed(
      &g_service_request_repair_pending_max_observed,
      g_pending_service_request_repairs.size());
  }
  g_service_request_repairs_scheduled.fetch_add(1, std::memory_order_relaxed);
  trace_service_request_repair_event("scheduled", client_endpoint_id, sequence_id, retries);
  g_service_request_repair_cv.notify_all();
  return true;
}

bool cancel_service_request_repair(
  const std::string & client_endpoint_id,
  std::int64_t sequence_id,
  const char * event)
{
  int remaining_retries = 0;
  bool removed = false;
  {
    std::lock_guard<std::mutex> lock(g_service_request_repair_mutex);
    const auto repair = std::find_if(
      g_pending_service_request_repairs.begin(),
      g_pending_service_request_repairs.end(),
      [&client_endpoint_id, sequence_id](const PendingServiceRequestRepair & candidate) {
        return candidate.client_endpoint_id == client_endpoint_id &&
               candidate.sequence_id == sequence_id;
      });
    if (repair != g_pending_service_request_repairs.end()) {
      remaining_retries = repair->remaining_retries;
      g_pending_service_request_repairs.erase(repair);
      removed = true;
    }
  }
  if (removed) {
    g_service_request_repairs_cancelled.fetch_add(1, std::memory_order_relaxed);
    trace_service_request_repair_event(
      event, client_endpoint_id, sequence_id, remaining_retries);
    g_service_request_repair_cv.notify_all();
  }
  return removed;
}

void cancel_service_request_repairs_for_client(const std::string & client_endpoint_id)
{
  std::vector<std::pair<std::int64_t, int>> cancelled;
  {
    std::lock_guard<std::mutex> lock(g_service_request_repair_mutex);
    auto repair = g_pending_service_request_repairs.begin();
    while (repair != g_pending_service_request_repairs.end()) {
      if (repair->client_endpoint_id == client_endpoint_id) {
        cancelled.emplace_back(repair->sequence_id, repair->remaining_retries);
        repair = g_pending_service_request_repairs.erase(repair);
      } else {
        ++repair;
      }
    }
  }
  if (!cancelled.empty()) {
    g_service_request_repairs_cancelled.fetch_add(
      cancelled.size(), std::memory_order_relaxed);
    for (const auto & repair : cancelled) {
      trace_service_request_repair_event(
        "client_destroyed", client_endpoint_id, repair.first, repair.second);
    }
    g_service_request_repair_cv.notify_all();
  }
}

void stop_service_request_repair_worker()
{
  {
    std::lock_guard<std::mutex> lock(g_service_request_repair_mutex);
    g_service_request_repair_stop = true;
    g_pending_service_request_repairs.clear();
  }
  g_service_request_repair_cv.notify_all();
  if (g_service_request_repair_thread.joinable()) {
    g_service_request_repair_thread.join();
  }
}

ServiceRequestRepairShutdownGuard::~ServiceRequestRepairShutdownGuard()
{
  stop_service_request_repair_worker();
}

rmw_ret_t send_service_frame_with_repeats(
  const std::string & encoded,
  const char * repeat_env,
  const char * interval_env)
{
  rmw_ret_t ret = rmw_fleetqox_cpp_send_encoded_frame(encoded.data(), encoded.size());
  if (ret != RMW_RET_OK) {
    return ret;
  }
  const int repeats = parse_nonnegative_int_env(repeat_env, 0, 5);
  if (repeats <= 0) {
    return RMW_RET_OK;
  }
  const int interval_ms = parse_nonnegative_int_env(interval_env, 1, 100);
  for (int repeat = 0; repeat < repeats; ++repeat) {
    if (interval_ms > 0) {
      std::this_thread::sleep_for(std::chrono::milliseconds(interval_ms));
    }
    ret = rmw_fleetqox_cpp_send_encoded_frame(encoded.data(), encoded.size());
    if (ret != RMW_RET_OK) {
      return ret;
    }
  }
  return RMW_RET_OK;
}

void fill_request_id(
  const std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> & writer_gid,
  std::int64_t sequence_id,
  rmw_request_id_t * request_id)
{
  if (request_id == nullptr) {
    return;
  }
  std::memset(request_id, 0, sizeof(*request_id));
  std::memcpy(request_id->writer_guid, writer_gid.data(), writer_gid.size());
  request_id->sequence_number = sequence_id;
}

FleetQoxServiceData * allocate_service_data(
  rcutils_allocator_t allocator,
  rmw_context_t * context,
  const rmw_node_t * owner_node,
  const char * service_name,
  const rmw_qos_profile_t * qos,
  bool is_service,
  const std::string & type_name,
  const std::string & node_name,
  const std::string & node_namespace,
  const std::string & enclave,
  std::size_t domain_id,
  const std::string & endpoint_id,
  const std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> & endpoint_gid,
  const rosidl_typesupport_introspection_c__ServiceMembers * service_members,
  const rosidl_typesupport_introspection_cpp::ServiceMembers * cpp_service_members)
{
  if (!rcutils_allocator_is_valid(&allocator) || service_name == nullptr || qos == nullptr ||
    ((service_members == nullptr || service_members->request_members_ == nullptr ||
    service_members->response_members_ == nullptr) &&
    (cpp_service_members == nullptr || cpp_service_members->request_members_ == nullptr ||
    cpp_service_members->response_members_ == nullptr)))
  {
    return nullptr;
  }
  void * memory = allocator.allocate(sizeof(FleetQoxServiceData), allocator.state);
  if (memory == nullptr) {
    return nullptr;
  }
  const size_t qos_depth = qos->depth == 0 ? 10 : qos->depth;
  const size_t request_queue_limit = service_resource_limit(
    "FLEETQOX_RMW_SERVICE_REQUEST_QUEUE_LIMIT", qos_depth);
  const size_t per_client_request_queue_limit = service_resource_limit(
    "FLEETQOX_RMW_SERVICE_PER_CLIENT_REQUEST_QUEUE_LIMIT", 65536);
  const size_t response_queue_limit = service_resource_limit(
    "FLEETQOX_RMW_SERVICE_RESPONSE_QUEUE_LIMIT", qos_depth);
  const size_t pending_response_limit = service_resource_limit(
    "FLEETQOX_RMW_SERVICE_PENDING_RESPONSE_LIMIT",
    std::max<size_t>(qos_depth, 1024));
  const size_t dedupe_history_limit = service_resource_limit(
    "FLEETQOX_RMW_SERVICE_DEDUPE_HISTORY_LIMIT",
    std::max<size_t>(qos_depth, 1024));
  const size_t configured_replay_limit = service_resource_limit(
    "FLEETQOX_RMW_SERVICE_RESPONSE_REPLAY_LIMIT",
    std::max<size_t>(qos_depth, 1024));
  const size_t response_replay_limit =
    std::max(configured_replay_limit, dedupe_history_limit);
  const int priority_aging_ms = parse_nonnegative_int_env(
    "FLEETQOX_RMW_SERVICE_PRIORITY_AGING_MS", 100, 60000);
  const int deadline_aging_ms = parse_nonnegative_int_env(
    "FLEETQOX_RMW_SERVICE_DEADLINE_AGING_MS", 1000, 60000);
  const int client_priority = parse_nonnegative_int_env(
    "FLEETQOX_RMW_SERVICE_CLIENT_PRIORITY", 0, 255);
  const int client_weight = std::max(
    1, parse_nonnegative_int_env(
      "FLEETQOX_RMW_SERVICE_CLIENT_WEIGHT", 1, 64));
  const int configured_client_deadline_ms = parse_nonnegative_int_env(
    "FLEETQOX_RMW_SERVICE_CLIENT_DEADLINE_MS", -1, 60000);
  const std::int64_t client_deadline_ns =
    configured_client_deadline_ms >= 0 ?
    static_cast<std::int64_t>(configured_client_deadline_ms) * 1000000 :
    qos_duration_ns(qos->deadline);
  auto * data = new (memory) FleetQoxServiceData{
    allocator,
    context,
    owner_node,
    nullptr,
    *qos,
    is_service,
    type_name,
    node_name,
    node_namespace,
    enclave,
    domain_id,
    endpoint_id,
    endpoint_gid,
    service_members,
    service_members == nullptr ? nullptr : service_members->request_members_,
    service_members == nullptr ? nullptr : service_members->response_members_,
    cpp_service_members,
    cpp_service_members == nullptr ? nullptr : cpp_service_members->request_members_,
    cpp_service_members == nullptr ? nullptr : cpp_service_members->response_members_,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
    1,
    std::deque<rmw_fleetqox_cpp::ServiceFrame>{},
    std::deque<rmw_fleetqox_cpp::ServiceFrame>{},
    std::string{},
    std::map<std::string, std::string>{},
    std::map<std::string, std::string>{},
    std::unordered_set<std::string>{},
    std::unordered_set<std::string>{},
    std::deque<std::string>{},
    std::deque<std::string>{},
    std::deque<std::string>{},
    request_queue_limit,
    per_client_request_queue_limit,
    response_queue_limit,
    pending_response_limit,
    dedupe_history_limit,
    response_replay_limit,
    static_cast<std::int64_t>(priority_aging_ms) * 1000000,
    weighted_service_scheduler_enabled(),
    deadline_service_scheduler_enabled(),
    static_cast<std::int64_t>(deadline_aging_ms) * 1000000,
    std::map<std::string, std::int64_t>{},
    static_cast<std::uint64_t>(client_priority),
    static_cast<std::uint64_t>(client_weight),
    static_cast<std::uint64_t>(std::max<std::int64_t>(0, client_deadline_ns)),
    std::string{},
    std::unordered_set<std::string>{}};
  data->service_name = rcutils_strdup(service_name, allocator);
  if (data->service_name == nullptr) {
    data->~FleetQoxServiceData();
    allocator.deallocate(memory, allocator.state);
    return nullptr;
  }
  if (is_service) {
    data->durable_replay_path = durable_service_replay_path(
      service_name, type_name, domain_id);
    (void)load_durable_service_replays(data);
  }
  return data;
}

void deallocate_service_data(FleetQoxServiceData * data)
{
  if (data == nullptr) {
    return;
  }
  rcutils_allocator_t allocator = data->allocator;
  if (data->service_name != nullptr && allocator.deallocate != nullptr) {
    allocator.deallocate(data->service_name, allocator.state);
  }
  data->~FleetQoxServiceData();
  allocator.deallocate(data, allocator.state);
}

bool serialize_service_message(
  const FleetQoxServiceData * data,
  bool request,
  const void * ros_message,
  std::vector<std::uint8_t> * payload)
{
  if (data == nullptr) {
    return false;
  }
  const auto * c_members = request ? data->request_members : data->response_members;
  if (c_members != nullptr) {
    return rmw_fleetqox_cpp_serialize_introspection_message(c_members, ros_message, payload);
  }
  const auto * cpp_members = request ? data->cpp_request_members : data->cpp_response_members;
  return rmw_fleetqox_cpp_serialize_introspection_cpp_message(
    cpp_members, ros_message, payload);
}

bool deserialize_service_message(
  const FleetQoxServiceData * data,
  bool request,
  const std::vector<std::uint8_t> * payload,
  void * ros_message)
{
  if (data == nullptr) {
    return false;
  }
  const auto * c_members = request ? data->request_members : data->response_members;
  if (c_members != nullptr) {
    return rmw_fleetqox_cpp_deserialize_introspection_message(c_members, payload, ros_message);
  }
  const auto * cpp_members = request ? data->cpp_request_members : data->cpp_response_members;
  return rmw_fleetqox_cpp_deserialize_introspection_cpp_message(
    cpp_members, payload, ros_message);
}

std::string ros_type_name_from_service_members(
  const rosidl_typesupport_introspection_c__ServiceMembers * members)
{
  if (members == nullptr || members->service_namespace_ == nullptr ||
    members->service_name_ == nullptr)
  {
    return "unknown";
  }
  std::string namespace_text = members->service_namespace_;
  size_t separator = 0;
  while ((separator = namespace_text.find("__", separator)) != std::string::npos) {
    namespace_text.replace(separator, 2, "/");
    separator += 1;
  }
  return namespace_text + "/" + members->service_name_;
}

const rosidl_typesupport_introspection_c__ServiceMembers * service_introspection_members(
  const rosidl_service_type_support_t * type_support)
{
  if (type_support == nullptr ||
    type_support->typesupport_identifier == nullptr ||
    std::strcmp(type_support->typesupport_identifier, rosidl_typesupport_introspection_c__identifier) != 0 ||
    type_support->data == nullptr)
  {
    return nullptr;
  }
  return static_cast<const rosidl_typesupport_introspection_c__ServiceMembers *>(type_support->data);
}

const rosidl_typesupport_introspection_c__MessageMembers * message_introspection_members(
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

const rosidl_typesupport_introspection_cpp::ServiceMembers * service_cpp_introspection_members(
  const rosidl_service_type_support_t * type_support)
{
  if (type_support == nullptr || type_support->typesupport_identifier == nullptr ||
    std::strcmp(
      type_support->typesupport_identifier,
      rosidl_typesupport_introspection_cpp::typesupport_identifier) != 0 ||
    type_support->data == nullptr)
  {
    return nullptr;
  }
  return static_cast<const rosidl_typesupport_introspection_cpp::ServiceMembers *>(
    type_support->data);
}

const rosidl_typesupport_introspection_cpp::MessageMembers * message_cpp_introspection_members(
  const rosidl_message_type_support_t * type_support)
{
  if (type_support == nullptr || type_support->typesupport_identifier == nullptr ||
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

const rosidl_message_type_support_t * resolve_effective_message_type_support(
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

const rosidl_service_type_support_t * resolve_effective_service_type_support(
  const rosidl_service_type_support_t * type_support)
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
  if (std::strcmp(type_support->typesupport_identifier, rosidl_typesupport_c__typesupport_identifier) == 0) {
    const rosidl_service_type_support_t * resolved =
      rosidl_typesupport_c__get_service_typesupport_handle_function(
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
    const rosidl_service_type_support_t * resolved =
      rosidl_typesupport_cpp::get_service_typesupport_handle_function(
      type_support,
      rosidl_typesupport_introspection_cpp::typesupport_identifier);
    if (resolved != nullptr) {
      return resolved;
    }
  }
  if (type_support->func != nullptr) {
    const rosidl_service_type_support_t * resolved =
      type_support->func(type_support, rosidl_typesupport_introspection_c__identifier);
    if (resolved != nullptr) {
      return resolved;
    }
  }
  return type_support;
}

std::string service_type_name_from_type_support(const rosidl_service_type_support_t * type_support)
{
  const auto * effective = resolve_effective_service_type_support(type_support);
  const auto * members = service_introspection_members(effective);
  if (members != nullptr) {
    return ros_type_name_from_service_members(members);
  }
  const auto * cpp_members = service_cpp_introspection_members(effective);
  if (cpp_members != nullptr && cpp_members->service_namespace_ != nullptr &&
    cpp_members->service_name_ != nullptr)
  {
    std::string namespace_text = cpp_members->service_namespace_;
    size_t separator = 0;
    while ((separator = namespace_text.find("::", separator)) != std::string::npos) {
      namespace_text.replace(separator, 2, "/");
      separator += 1;
    }
    return namespace_text + "/" + cpp_members->service_name_;
  }
  return type_support != nullptr && type_support->typesupport_identifier != nullptr ?
         type_support->typesupport_identifier : "unknown";
}

std::string endpoint_id_for_local_id(const std::string & local_id)
{
  const char * bound_endpoint = rmw_fleetqox_cpp_socket_bound_endpoint();
  if (bound_endpoint != nullptr && bound_endpoint[0] != '\0') {
    return std::string(bound_endpoint) + "|" + local_id;
  }
  return std::string("local|") + local_id;
}

std::string allocate_service_endpoint_id(bool is_service)
{
  const std::uint64_t id = is_service ?
    g_next_service_endpoint_id.fetch_add(1) :
    g_next_client_endpoint_id.fetch_add(1);
  return endpoint_id_for_local_id(std::string(is_service ? "fsvccpp-" : "fclicpp-") + std::to_string(id));
}

void send_service_graph_advertisement(const FleetQoxServiceData * data, const char * action)
{
  if (data == nullptr || action == nullptr) {
    return;
  }
  const rmw_ret_t ret = rmw_fleetqox_cpp_send_graph_advertisement(
    action,
    data->is_service ? "service" : "client",
    data->node_name.c_str(),
    data->node_namespace.c_str(),
    data->service_name,
    data->type_name.c_str(),
    data->endpoint_id.c_str(),
    &data->qos,
    data->domain_id);
  (void)ret;
}

void service_graph_renewal_loop()
{
  constexpr auto kRenewInterval = std::chrono::milliseconds(500);
  while (g_service_graph_renewal_running.load(std::memory_order_acquire)) {
    std::this_thread::sleep_for(kRenewInterval);
    if (!g_service_graph_renewal_running.load(std::memory_order_acquire)) {
      break;
    }
    std::lock_guard<std::mutex> lock(g_service_graph_mutex);
    for (const FleetQoxServiceData * data : g_service_graph_endpoints) {
      send_service_graph_advertisement(data, "add");
    }
  }
}

// Same unstopped-detached-thread-vs-exit()-time-static-destruction hazard as
// remote_graph_lease_monitor_loop in rmw_graph.cpp (see
// rmw_fleetqox_cpp_stop_remote_graph_lease_monitor_thread): this thread is
// started by every process that creates a service endpoint, including
// short-lived CLI processes (e.g. `ros2 lifecycle get/set`), so it must be
// stopped and joined before such a process's exit() runs its global
// destructors.
void stop_service_graph_renewal_thread()
{
  std::lock_guard<std::mutex> lifecycle_lock(g_service_graph_renewal_lifecycle_mutex);
  g_service_graph_renewal_running.store(false, std::memory_order_release);
  if (g_service_graph_renewal_thread.joinable()) {
    g_service_graph_renewal_thread.join();
  }
  g_service_graph_renewal_started.store(false, std::memory_order_release);
}

void ensure_service_graph_renewal_thread()
{
  const char * disable_renewal = std::getenv("FLEETQOX_DISABLE_SERVICE_GRAPH_RENEWAL");
  if (disable_renewal != nullptr && disable_renewal[0] != '\0' &&
    std::strcmp(disable_renewal, "0") != 0)
  {
    return;
  }
  std::lock_guard<std::mutex> lifecycle_lock(g_service_graph_renewal_lifecycle_mutex);
  if (g_service_graph_renewal_started.load(std::memory_order_acquire)) {
    return;
  }
  if (g_service_graph_renewal_thread.joinable()) {
    g_service_graph_renewal_thread.join();
  }
  g_service_graph_renewal_running.store(true, std::memory_order_release);
  g_service_graph_renewal_started.store(true, std::memory_order_release);
  g_service_graph_renewal_thread = std::thread(service_graph_renewal_loop);
  std::call_once(g_service_graph_renewal_atexit_once, []() {
    std::atexit(stop_service_graph_renewal_thread);
  });
}

void add_service_graph_renewal_endpoint(FleetQoxServiceData * data)
{
  if (data == nullptr) {
    return;
  }
  {
    std::lock_guard<std::mutex> lock(g_service_graph_mutex);
    g_service_graph_endpoints.push_back(data);
  }
  ensure_service_graph_renewal_thread();
}

void remove_service_graph_renewal_endpoint(FleetQoxServiceData * data)
{
  std::lock_guard<std::mutex> lock(g_service_graph_mutex);
  g_service_graph_endpoints.erase(
    std::remove(g_service_graph_endpoints.begin(), g_service_graph_endpoints.end(), data),
    g_service_graph_endpoints.end());
}

bool service_has_request_locked(const FleetQoxServiceData * data)
{
  return data != nullptr && data->is_service && !data->request_queue.empty();
}

bool client_has_response_locked(const FleetQoxServiceData * data)
{
  return data != nullptr && !data->is_service && !data->response_queue.empty();
}

FleetQoxServiceData * service_data_from_waitable_locked(const void * waitable)
{
  for (FleetQoxServiceData * data : g_service_bus_endpoints) {
    if (data == waitable && data->is_service) {
      return data;
    }
  }
  for (const rmw_service_t * service : g_service_handles) {
    if (service == waitable && service != nullptr) {
      return service_data(service);
    }
  }
  return nullptr;
}

FleetQoxServiceData * client_data_from_waitable_locked(const void * waitable)
{
  for (FleetQoxServiceData * data : g_service_bus_endpoints) {
    if (data == waitable && !data->is_service) {
      return data;
    }
  }
  for (const rmw_client_t * client : g_client_handles) {
    if (client == waitable && client != nullptr) {
      return client_data(client);
    }
  }
  return nullptr;
}

rmw_ret_t fill_udp_network_flow_endpoint(
  rcutils_allocator_t * allocator,
  rmw_network_flow_endpoint_array_t * endpoints)
{
  if (allocator == nullptr || !rcutils_allocator_is_valid(allocator) || endpoints == nullptr) {
    RMW_SET_ERROR_MSG("invalid allocator or network flow endpoint output");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const char * transport_mode = rmw_fleetqox_cpp_transport_mode();
  if (transport_mode != nullptr && std::strcmp(transport_mode, "shm") == 0) {
    *endpoints = rmw_get_zero_initialized_network_flow_endpoint_array();
    return RMW_RET_OK;
  }
  const char * bound_endpoint = rmw_fleetqox_cpp_socket_bound_endpoint();
  if (bound_endpoint == nullptr || bound_endpoint[0] == '\0') {
    RMW_SET_ERROR_MSG("FleetRMW UDP socket has no bound endpoint");
    return RMW_RET_ERROR;
  }
  const std::string endpoint(bound_endpoint);
  const size_t separator = endpoint.rfind(':');
  if (separator == std::string::npos || separator == 0 || separator + 1 >= endpoint.size()) {
    RMW_SET_ERROR_MSG("FleetRMW UDP bound endpoint is malformed");
    return RMW_RET_ERROR;
  }
  const std::string address = endpoint.substr(0, separator);
  const std::string port_text = endpoint.substr(separator + 1);
  char * port_end = nullptr;
  errno = 0;
  const long port = std::strtol(port_text.c_str(), &port_end, 10);
  if (errno != 0 || port_end == port_text.c_str() || *port_end != '\0' ||
    port < 0 || port > 65535)
  {
    RMW_SET_ERROR_MSG("FleetRMW UDP bound endpoint port is invalid");
    return RMW_RET_ERROR;
  }
  rmw_ret_t ret = rmw_network_flow_endpoint_array_init(endpoints, 1, allocator);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  rmw_network_flow_endpoint_t & flow = endpoints->network_flow_endpoint[0];
  flow.transport_protocol = RMW_TRANSPORT_PROTOCOL_UDP;
  flow.internet_protocol = RMW_INTERNET_PROTOCOL_IPV4;
  flow.transport_port = static_cast<std::uint16_t>(port);
  flow.flow_label = 0;
  flow.dscp = 0;
  ret = rmw_network_flow_endpoint_set_internet_address(
    &flow, address.c_str(), address.size());
  if (ret != RMW_RET_OK) {
    rmw_network_flow_endpoint_array_fini(endpoints);
    return ret;
  }
  return RMW_RET_OK;
}

void fill_pointer_gid(const void * entity, rmw_gid_t * gid)
{
  std::memset(gid, 0, sizeof(*gid));
  gid->implementation_identifier = kIdentifier;
  const auto value = reinterpret_cast<std::uintptr_t>(entity);
  const size_t copy_size = std::min(sizeof(value), static_cast<size_t>(RMW_GID_STORAGE_SIZE));
  std::memcpy(gid->data, &value, copy_size);
}

void clear_reason(char * reason, size_t reason_size)
{
  if (reason != nullptr && reason_size > 0) {
    reason[0] = '\0';
  }
}

bool qos_time_equal(const rmw_time_t & lhs, const rmw_time_t & rhs)
{
  return lhs.sec == rhs.sec && lhs.nsec == rhs.nsec;
}

bool qos_time_less(const rmw_time_t & lhs, const rmw_time_t & rhs)
{
  return lhs.sec < rhs.sec || (lhs.sec == rhs.sec && lhs.nsec < rhs.nsec);
}

void append_qos_reason(char * reason, size_t reason_size, const char * message)
{
  if (reason == nullptr || reason_size == 0 || message == nullptr) {
    return;
  }
  const size_t used = ::strnlen(reason, reason_size);
  if (used >= reason_size - 1) {
    return;
  }
  (void)std::snprintf(reason + used, reason_size - used, "%s", message);
}

bool reliability_unknown(rmw_qos_reliability_policy_t value)
{
  return value == RMW_QOS_POLICY_RELIABILITY_SYSTEM_DEFAULT ||
         value == RMW_QOS_POLICY_RELIABILITY_UNKNOWN;
}

bool durability_unknown(rmw_qos_durability_policy_t value)
{
  return value == RMW_QOS_POLICY_DURABILITY_SYSTEM_DEFAULT ||
         value == RMW_QOS_POLICY_DURABILITY_UNKNOWN;
}

bool liveliness_unknown(rmw_qos_liveliness_policy_t value)
{
  return value == RMW_QOS_POLICY_LIVELINESS_SYSTEM_DEFAULT ||
         value == RMW_QOS_POLICY_LIVELINESS_UNKNOWN;
}

}  // namespace

extern "C"
{

void rmw_fleetqox_cpp_stop_service_graph_renewal_thread()
{
  stop_service_graph_renewal_thread();
}

bool rmw_fleetqox_cpp_handle_service_frame(const char * encoded_frame, size_t size)
{
  if (encoded_frame == nullptr || size == 0) {
    return false;
  }
  const auto frame = rmw_fleetqox_cpp::decode_service_frame(std::string(encoded_frame, size));
  if (!frame) {
    return false;
  }
  g_service_frames_received.fetch_add(1, std::memory_order_relaxed);
  if (drop_if_expired_service_frame(*frame)) {
    return true;
  }
  std::vector<std::pair<rmw_event_callback_t, const void *>> callbacks;
  std::vector<std::pair<std::string, bool>> replay_responses;
  bool matched_response = false;
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    if (frame->role == "request") {
      for (FleetQoxServiceData * data : g_service_bus_endpoints) {
        if (data != nullptr && data->is_service && data->service_name != nullptr &&
          frame->domain_id == data->domain_id &&
          frame->service_name == data->service_name &&
          frame->type_name == data->type_name)
        {
          if (!rmw_fleetqox_cpp_graph_client_matches_service_in_domain(
              frame->client_endpoint_id.c_str(),
              data->service_name,
              data->type_name.c_str(),
              &data->qos,
              data->domain_id))
          {
            trace_service_event("drop_unmatched_request", data, &*frame);
            continue;
          }
          if (!sros2_service_operation_allowed(data, false, true)) {
            trace_service_event("deny_request_subscribe", data, &*frame);
            continue;
          }
          const std::string dedupe_key = service_frame_dedupe_key(*frame);
          if (data->seen_request_keys.find(dedupe_key) != data->seen_request_keys.end()) {
            const std::string replay_key =
              service_response_replay_key(frame->client_endpoint_id, frame->sequence_id);
            const auto replay = data->response_replay_cache.find(replay_key);
            if (replay != data->response_replay_cache.end()) {
              replay_responses.emplace_back(
                replay->second,
                data->durable_response_keys.find(replay_key) !=
                data->durable_response_keys.end());
            }
            trace_service_event("drop_duplicate_request", data, &*frame, data->request_queue.size());
            continue;
          }
          const size_t client_pending = static_cast<size_t>(std::count_if(
              data->request_queue.begin(),
              data->request_queue.end(),
              [&frame](const rmw_fleetqox_cpp::ServiceFrame & queued) {
                return queued.client_endpoint_id == frame->client_endpoint_id;
              }));
          if (client_pending >= data->per_client_request_queue_limit) {
            g_service_request_per_client_resource_drops.fetch_add(
              1, std::memory_order_relaxed);
            trace_service_event(
              "request_client_resource_limit", data, &*frame, data->request_queue.size());
            continue;
          }
          if (data->request_queue.size() >= data->request_queue_limit) {
            g_service_request_queue_resource_drops.fetch_add(1, std::memory_order_relaxed);
            trace_service_event(
              "request_queue_resource_limit", data, &*frame, data->request_queue.size());
            continue;
          }
          remember_bounded_service_key(
            &data->seen_request_keys,
            &data->seen_request_order,
            dedupe_key,
            data->dedupe_history_limit,
            &g_service_request_dedupe_evictions);
          rmw_fleetqox_cpp::ServiceFrame queued_frame = *frame;
          queued_frame.local_enqueue_timestamp_ns = monotonic_timestamp_ns();
          data->request_queue.push_back(std::move(queued_frame));
          update_max_observed(
            &g_service_request_queue_max_observed, data->request_queue.size());
          update_max_observed(
            &g_service_request_per_client_max_observed, client_pending + 1);
          trace_service_event("enqueue_request", data, &*frame, data->request_queue.size());
          if (data->on_new_request_callback != nullptr) {
            callbacks.emplace_back(
              data->on_new_request_callback, data->on_new_request_user_data);
          }
        }
      }
    } else if (frame->role == "response") {
      for (FleetQoxServiceData * data : g_service_bus_endpoints) {
        if (data != nullptr && !data->is_service &&
          frame->domain_id == data->domain_id &&
          frame->client_endpoint_id == data->endpoint_id &&
          frame->service_name == data->service_name &&
          frame->type_name == data->type_name)
        {
          if (!sros2_service_operation_allowed(data, false, false)) {
            trace_service_event("deny_response_subscribe", data, &*frame);
            continue;
          }
          const std::string dedupe_key = service_frame_dedupe_key(*frame);
          if (data->seen_response_keys.find(dedupe_key) != data->seen_response_keys.end()) {
            matched_response = true;
            trace_service_event("drop_duplicate_response", data, &*frame, data->response_queue.size());
            continue;
          }
          if (data->response_queue.size() >= data->response_queue_limit) {
            g_service_response_queue_resource_drops.fetch_add(1, std::memory_order_relaxed);
            trace_service_event(
              "response_queue_resource_limit", data, &*frame, data->response_queue.size());
            continue;
          }
          remember_bounded_service_key(
            &data->seen_response_keys,
            &data->seen_response_order,
            dedupe_key,
            data->dedupe_history_limit,
            &g_service_response_dedupe_evictions);
          data->response_queue.push_back(*frame);
          matched_response = true;
          update_max_observed(
            &g_service_response_queue_max_observed, data->response_queue.size());
          trace_service_event("enqueue_response", data, &*frame, data->response_queue.size());
          if (data->on_new_response_callback != nullptr) {
            callbacks.emplace_back(
              data->on_new_response_callback, data->on_new_response_user_data);
          }
        }
      }
    }
  }
  if (matched_response) {
    cancel_service_request_repair(
      frame->client_endpoint_id, frame->sequence_id, "response_received");
  }
  for (const auto & response : replay_responses) {
    const rmw_ret_t replay_ret = send_service_frame_with_repeats(
      response.first,
      "FLEETQOX_RMW_SERVICE_RESPONSE_REPEATS",
      "FLEETQOX_RMW_SERVICE_RESPONSE_REPEAT_INTERVAL_MS");
    if (replay_ret == RMW_RET_OK && response.second) {
      g_service_durable_replays_sent.fetch_add(1, std::memory_order_relaxed);
    }
  }
  for (const auto & callback : callbacks) {
    callback.first(callback.second, 1);
  }
  return true;
}

bool rmw_fleetqox_cpp_waitable_service_has_request(const void * waitable)
{
  if (waitable == nullptr) {
    return false;
  }
  std::lock_guard<std::mutex> lock(g_service_bus_mutex);
  return service_has_request_locked(service_data_from_waitable_locked(waitable));
}

const rmw_context_t * rmw_fleetqox_cpp_waitable_service_context(const void * waitable)
{
  if (waitable == nullptr) {
    return nullptr;
  }
  std::lock_guard<std::mutex> lock(g_service_bus_mutex);
  FleetQoxServiceData * data = service_data_from_waitable_locked(waitable);
  return data == nullptr ? nullptr : data->context;
}

bool rmw_fleetqox_cpp_waitable_client_has_response(const void * waitable)
{
  if (waitable == nullptr) {
    return false;
  }
  std::lock_guard<std::mutex> lock(g_service_bus_mutex);
  FleetQoxServiceData * matched = client_data_from_waitable_locked(waitable);
  if (matched == nullptr && trace_service_enabled()) {
    for (FleetQoxServiceData * data : g_service_bus_endpoints) {
      if (client_has_response_locked(data)) {
        std::fprintf(
          stderr,
          "fleetqox service event=waitable_client_unmatched waitable=%p pending_data=%p endpoint=%s\n",
          waitable,
          static_cast<void *>(data),
          data->endpoint_id.c_str());
        break;
      }
    }
  }
  return client_has_response_locked(matched);
}

const rmw_context_t * rmw_fleetqox_cpp_waitable_client_context(const void * waitable)
{
  if (waitable == nullptr) {
    return nullptr;
  }
  std::lock_guard<std::mutex> lock(g_service_bus_mutex);
  FleetQoxServiceData * data = client_data_from_waitable_locked(waitable);
  return data == nullptr ? nullptr : data->context;
}

bool rmw_fleetqox_cpp_waitable_event_has_status(const void * waitable)
{
  if (waitable == nullptr) {
    return false;
  }
  FleetQoxEventData snapshot{};
  {
    std::lock_guard<std::mutex> lock(g_event_mutex);
    FleetQoxEventData * matched = event_data_from_waitable_locked(waitable);
    if (matched == nullptr) {
      return false;
    }
    snapshot = *matched;
  }
  if (snapshot.publisher_event) {
    return rmw_fleetqox_cpp_publisher_qos_event_has_status(
      static_cast<const rmw_publisher_t *>(snapshot.owner),
      snapshot.event_type);
  }
  return rmw_fleetqox_cpp_subscription_qos_event_has_status(
    static_cast<const rmw_subscription_t *>(snapshot.owner),
    snapshot.event_type);
}

const rmw_context_t * rmw_fleetqox_cpp_waitable_event_context(const void * waitable)
{
  if (waitable == nullptr) {
    return nullptr;
  }
  std::lock_guard<std::mutex> lock(g_event_mutex);
  FleetQoxEventData * data = event_data_from_waitable_locked(waitable);
  return data == nullptr ? nullptr : data->context;
}

std::uint64_t rmw_fleetqox_cpp_service_expired_frames_dropped()
{
  return g_service_expired_frames_dropped.load();
}

std::uint64_t rmw_fleetqox_cpp_service_frames_received()
{
  return g_service_frames_received.load(std::memory_order_relaxed);
}

const char * rmw_fleetqox_cpp_service_endpoint_id(const rmw_service_t * service)
{
  const FleetQoxServiceData * data = service_data(service);
  return data == nullptr ? "" : data->endpoint_id.c_str();
}

const char * rmw_fleetqox_cpp_client_endpoint_id(const rmw_client_t * client)
{
  const FleetQoxServiceData * data = client_data(client);
  return data == nullptr ? "" : data->endpoint_id.c_str();
}

rmw_ret_t rmw_init_publisher_allocation(
  const rosidl_message_type_support_t * type_support,
  const rosidl_runtime_c__Sequence__bound * message_bounds,
  rmw_publisher_allocation_t * allocation)
{
  (void)message_bounds;
  if (type_support == nullptr || allocation == nullptr) {
    RMW_SET_ERROR_MSG("publisher allocation arguments must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (allocation->implementation_identifier != nullptr || allocation->data != nullptr) {
    RMW_SET_ERROR_MSG("publisher allocation must be zero initialized");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const auto * effective = resolve_effective_message_type_support(type_support);
  auto * data = new (std::nothrow) rmw_fleetqox_cpp::MessageAllocationData(
    rmw_fleetqox_cpp::MessageAllocationKind::Publisher, effective);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate publisher payload scratch");
    return RMW_RET_BAD_ALLOC;
  }
  size_t reserve_size = static_cast<size_t>(
    parse_nonnegative_int_env("FLEETQOX_RMW_ALLOCATION_PAYLOAD_BYTES", 65536, 64 * 1024 * 1024));
  const auto * members = message_introspection_members(effective);
  const auto * cpp_members = message_cpp_introspection_members(effective);
  size_t bounded_size = 0;
  const bool bounded = members != nullptr ?
    rmw_fleetqox_cpp_max_serialized_size_introspection_message(members, &bounded_size) :
    (cpp_members != nullptr &&
    rmw_fleetqox_cpp_max_serialized_size_introspection_cpp_message(cpp_members, &bounded_size));
  if (bounded) {
    reserve_size = std::max(reserve_size, bounded_size);
  }
  try {
    data->payload.reserve(reserve_size);
  } catch (const std::bad_alloc &) {
    delete data;
    RMW_SET_ERROR_MSG("failed to reserve publisher payload scratch");
    return RMW_RET_BAD_ALLOC;
  }
  data->initial_capacity = data->payload.capacity();
  allocation->implementation_identifier = kIdentifier;
  allocation->data = data;
  g_publisher_allocations_initialized.fetch_add(1, std::memory_order_relaxed);
  return RMW_RET_OK;
}

rmw_ret_t rmw_fini_publisher_allocation(rmw_publisher_allocation_t * allocation)
{
  if (allocation == nullptr) {
    RMW_SET_ERROR_MSG("publisher allocation is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!identifier_matches(allocation->implementation_identifier)) {
    RMW_SET_ERROR_MSG("publisher allocation is not from rmw_fleetqox_cpp");
    return RMW_RET_INCORRECT_RMW_IMPLEMENTATION;
  }
  auto * data =
    static_cast<rmw_fleetqox_cpp::MessageAllocationData *>(allocation->data);
  if (data == nullptr || data->magic != rmw_fleetqox_cpp::kMessageAllocationMagic ||
    data->kind != rmw_fleetqox_cpp::MessageAllocationKind::Publisher)
  {
    RMW_SET_ERROR_MSG("publisher allocation data is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  data->magic = 0;
  delete data;
  allocation->implementation_identifier = nullptr;
  allocation->data = nullptr;
  g_publisher_allocations_finalized.fetch_add(1, std::memory_order_relaxed);
  return RMW_RET_OK;
}

rmw_ret_t rmw_borrow_loaned_message(
  const rmw_publisher_t * publisher,
  const rosidl_message_type_support_t * type_support,
  void ** ros_message)
{
  rmw_ret_t ret = validate_publisher(publisher);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (type_support == nullptr || ros_message == nullptr || *ros_message != nullptr) {
    RMW_SET_ERROR_MSG("loaned publisher message arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return rmw_fleetqox_cpp_borrow_publisher_loan(publisher, type_support, ros_message);
}

rmw_ret_t rmw_return_loaned_message_from_publisher(
  const rmw_publisher_t * publisher,
  void * loaned_message)
{
  rmw_ret_t ret = validate_publisher(publisher);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (loaned_message == nullptr) {
    RMW_SET_ERROR_MSG("loaned publisher message is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return rmw_fleetqox_cpp_release_publisher_loan(publisher, loaned_message);
}

rmw_ret_t rmw_publish_loaned_message(
  const rmw_publisher_t * publisher,
  void * ros_message,
  rmw_publisher_allocation_t * allocation)
{
  (void)allocation;
  rmw_ret_t ret = validate_publisher(publisher);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (ros_message == nullptr) {
    RMW_SET_ERROR_MSG("loaned publisher message is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  ret = rmw_publish(publisher, ros_message, allocation);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  return rmw_fleetqox_cpp_release_publisher_loan(publisher, ros_message);
}

rmw_ret_t rmw_publisher_event_init(
  rmw_event_t * rmw_event,
  const rmw_publisher_t * publisher,
  rmw_event_type_t event_type)
{
  if (rmw_event == nullptr) {
    RMW_SET_ERROR_MSG("publisher event is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = validate_publisher(publisher);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (!publisher_event_type_supported(event_type)) {
    return unsupported("publisher QoS event type is not supported by rmw_fleetqox_cpp");
  }
  return init_event(rmw_event, event_type, publisher, true);
}

rmw_ret_t rmw_publisher_assert_liveliness(const rmw_publisher_t * publisher)
{
  rmw_ret_t ret = validate_publisher(publisher);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  return rmw_fleetqox_cpp_assert_publisher_liveliness(publisher);
}

rmw_ret_t rmw_publisher_wait_for_all_acked(
  const rmw_publisher_t * publisher,
  rmw_time_t wait_timeout)
{
  rmw_ret_t ret = validate_publisher(publisher);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  return rmw_fleetqox_cpp_publisher_wait_for_all_acked(publisher, wait_timeout);
}

rmw_ret_t rmw_get_serialized_message_size(
  const rosidl_message_type_support_t * type_support,
  const rosidl_runtime_c__Sequence__bound * message_bounds,
  size_t * size)
{
  (void)message_bounds;
  if (type_support == nullptr || size == nullptr) {
    RMW_SET_ERROR_MSG("serialized message size arguments must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const auto * effective = resolve_effective_message_type_support(type_support);
  const auto * members = message_introspection_members(effective);
  const auto * cpp_members = message_cpp_introspection_members(effective);
  if (members == nullptr && cpp_members == nullptr) {
    return unsupported(
      "standalone serialization sizing requires introspection C or C++ type support");
  }
  const bool computed = members != nullptr ?
    rmw_fleetqox_cpp_max_serialized_size_introspection_message(members, size) :
    rmw_fleetqox_cpp_max_serialized_size_introspection_cpp_message(cpp_members, size);
  if (!computed) {
    return unsupported(
      "standalone serialization sizing requires a statically bounded message type");
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_serialize(
  const void * ros_message,
  const rosidl_message_type_support_t * type_support,
  rmw_serialized_message_t * serialized_message)
{
  if (ros_message == nullptr || type_support == nullptr || serialized_message == nullptr) {
    RMW_SET_ERROR_MSG("serialize arguments must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const auto * effective = resolve_effective_message_type_support(type_support);
  const auto * members = message_introspection_members(effective);
  const auto * cpp_members = message_cpp_introspection_members(effective);
  if (members == nullptr && cpp_members == nullptr) {
    RMW_SET_ERROR_MSG("rmw_serialize requires introspection C or C++ type support");
    return RMW_RET_UNSUPPORTED;
  }
  std::vector<std::uint8_t> payload;
  const bool serialized = members != nullptr ?
    rmw_fleetqox_cpp_serialize_introspection_message(members, ros_message, &payload) :
    rmw_fleetqox_cpp_serialize_introspection_cpp_message(cpp_members, ros_message, &payload);
  if (!serialized) {
    RMW_SET_ERROR_MSG("failed to serialize message with introspection type support");
    return RMW_RET_ERROR;
  }
  if (payload.size() > serialized_message->buffer_capacity) {
    const rmw_ret_t resize_ret = rmw_serialized_message_resize(serialized_message, payload.size());
    if (resize_ret != RMW_RET_OK) {
      RMW_SET_ERROR_MSG("failed to resize standalone serialized message");
      return resize_ret;
    }
  }
  if (!payload.empty()) {
    std::memcpy(serialized_message->buffer, payload.data(), payload.size());
  }
  serialized_message->buffer_length = payload.size();
  return RMW_RET_OK;
}

rmw_ret_t rmw_deserialize(
  const rmw_serialized_message_t * serialized_message,
  const rosidl_message_type_support_t * type_support,
  void * ros_message)
{
  if (serialized_message == nullptr || type_support == nullptr || ros_message == nullptr) {
    RMW_SET_ERROR_MSG("deserialize arguments must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (serialized_message->buffer_length > serialized_message->buffer_capacity ||
    (serialized_message->buffer_length > 0 && serialized_message->buffer == nullptr))
  {
    RMW_SET_ERROR_MSG("serialized message buffer is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const auto * effective = resolve_effective_message_type_support(type_support);
  const auto * members = message_introspection_members(effective);
  const auto * cpp_members = message_cpp_introspection_members(effective);
  if (members == nullptr && cpp_members == nullptr) {
    RMW_SET_ERROR_MSG("rmw_deserialize requires introspection C or C++ type support");
    return RMW_RET_UNSUPPORTED;
  }
  std::vector<std::uint8_t> payload;
  if (serialized_message->buffer_length > 0) {
    payload.assign(
      serialized_message->buffer,
      serialized_message->buffer + serialized_message->buffer_length);
  }
  const bool deserialized = members != nullptr ?
    rmw_fleetqox_cpp_deserialize_introspection_message(members, &payload, ros_message) :
    rmw_fleetqox_cpp_deserialize_introspection_cpp_message(cpp_members, &payload, ros_message);
  if (!deserialized) {
    RMW_SET_ERROR_MSG("failed to deserialize message with introspection type support");
    return RMW_RET_ERROR;
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_init_subscription_allocation(
  const rosidl_message_type_support_t * type_support,
  const rosidl_runtime_c__Sequence__bound * message_bounds,
  rmw_subscription_allocation_t * allocation)
{
  (void)message_bounds;
  if (type_support == nullptr || allocation == nullptr) {
    RMW_SET_ERROR_MSG("subscription allocation arguments must be non-null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (allocation->implementation_identifier != nullptr || allocation->data != nullptr) {
    RMW_SET_ERROR_MSG("subscription allocation must be zero initialized");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const auto * effective = resolve_effective_message_type_support(type_support);
  auto * data = new (std::nothrow) rmw_fleetqox_cpp::MessageAllocationData(
    rmw_fleetqox_cpp::MessageAllocationKind::Subscription, effective);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate subscription payload scratch");
    return RMW_RET_BAD_ALLOC;
  }
  size_t reserve_size = static_cast<size_t>(
    parse_nonnegative_int_env("FLEETQOX_RMW_ALLOCATION_PAYLOAD_BYTES", 65536, 64 * 1024 * 1024));
  const auto * members = message_introspection_members(effective);
  const auto * cpp_members = message_cpp_introspection_members(effective);
  size_t bounded_size = 0;
  const bool bounded = members != nullptr ?
    rmw_fleetqox_cpp_max_serialized_size_introspection_message(members, &bounded_size) :
    (cpp_members != nullptr &&
    rmw_fleetqox_cpp_max_serialized_size_introspection_cpp_message(cpp_members, &bounded_size));
  if (bounded) {
    reserve_size = std::max(reserve_size, bounded_size);
  }
  try {
    data->payload.reserve(reserve_size);
  } catch (const std::bad_alloc &) {
    delete data;
    RMW_SET_ERROR_MSG("failed to reserve subscription payload scratch");
    return RMW_RET_BAD_ALLOC;
  }
  data->initial_capacity = data->payload.capacity();
  allocation->implementation_identifier = kIdentifier;
  allocation->data = data;
  g_subscription_allocations_initialized.fetch_add(1, std::memory_order_relaxed);
  return RMW_RET_OK;
}

rmw_ret_t rmw_fini_subscription_allocation(rmw_subscription_allocation_t * allocation)
{
  if (allocation == nullptr) {
    RMW_SET_ERROR_MSG("subscription allocation is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!identifier_matches(allocation->implementation_identifier)) {
    RMW_SET_ERROR_MSG("subscription allocation is not from rmw_fleetqox_cpp");
    return RMW_RET_INCORRECT_RMW_IMPLEMENTATION;
  }
  auto * data =
    static_cast<rmw_fleetqox_cpp::MessageAllocationData *>(allocation->data);
  if (data == nullptr || data->magic != rmw_fleetqox_cpp::kMessageAllocationMagic ||
    data->kind != rmw_fleetqox_cpp::MessageAllocationKind::Subscription)
  {
    RMW_SET_ERROR_MSG("subscription allocation data is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  data->magic = 0;
  delete data;
  allocation->implementation_identifier = nullptr;
  allocation->data = nullptr;
  g_subscription_allocations_finalized.fetch_add(1, std::memory_order_relaxed);
  return RMW_RET_OK;
}

rmw_ret_t rmw_subscription_event_init(
  rmw_event_t * rmw_event,
  const rmw_subscription_t * subscription,
  rmw_event_type_t event_type)
{
  if (rmw_event == nullptr) {
    RMW_SET_ERROR_MSG("subscription event is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = validate_subscription(subscription);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (!subscription_event_type_supported(event_type)) {
    return unsupported("subscription QoS event type is not supported by rmw_fleetqox_cpp");
  }
  return init_event(rmw_event, event_type, subscription, false);
}

rmw_ret_t rmw_subscription_set_content_filter(
  rmw_subscription_t * subscription,
  const rmw_subscription_content_filter_options_t * options)
{
  rmw_ret_t ret = validate_subscription(subscription);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (options == nullptr) {
    RMW_SET_ERROR_MSG("content filter options are null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return rmw_fleetqox_cpp_subscription_set_content_filter(subscription, options);
}

rmw_ret_t rmw_subscription_get_content_filter(
  const rmw_subscription_t * subscription,
  rcutils_allocator_t * allocator,
  rmw_subscription_content_filter_options_t * options)
{
  rmw_ret_t ret = validate_subscription(subscription);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (allocator == nullptr || !rcutils_allocator_is_valid(allocator) || options == nullptr) {
    RMW_SET_ERROR_MSG("content filter output arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return rmw_fleetqox_cpp_subscription_get_content_filter(subscription, allocator, options);
}

rmw_ret_t rmw_take_loaned_message(
  const rmw_subscription_t * subscription,
  void ** loaned_message,
  bool * taken,
  rmw_subscription_allocation_t * allocation)
{
  (void)allocation;
  rmw_ret_t ret = validate_subscription(subscription);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (loaned_message == nullptr || taken == nullptr) {
    RMW_SET_ERROR_MSG("loaned subscription message arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (*loaned_message != nullptr) {
    RMW_SET_ERROR_MSG("loaned subscription message output must be null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *taken = false;
  ret = rmw_fleetqox_cpp_borrow_subscription_loan(subscription, loaned_message);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  ret = rmw_take(subscription, *loaned_message, taken, allocation);
  if (ret != RMW_RET_OK || !*taken) {
    const rmw_ret_t release_ret =
      rmw_fleetqox_cpp_release_subscription_loan(subscription, *loaned_message);
    *loaned_message = nullptr;
    return ret != RMW_RET_OK ? ret : release_ret;
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_take_loaned_message_with_info(
  const rmw_subscription_t * subscription,
  void ** loaned_message,
  bool * taken,
  rmw_message_info_t * message_info,
  rmw_subscription_allocation_t * allocation)
{
  rmw_ret_t ret = validate_subscription(subscription);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (loaned_message == nullptr || taken == nullptr || message_info == nullptr ||
    *loaned_message != nullptr)
  {
    RMW_SET_ERROR_MSG("loaned subscription message-with-info arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *taken = false;
  ret = rmw_fleetqox_cpp_borrow_subscription_loan(subscription, loaned_message);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  ret = rmw_take_with_info(subscription, *loaned_message, taken, message_info, allocation);
  if (ret != RMW_RET_OK || !*taken) {
    const rmw_ret_t release_ret =
      rmw_fleetqox_cpp_release_subscription_loan(subscription, *loaned_message);
    *loaned_message = nullptr;
    return ret != RMW_RET_OK ? ret : release_ret;
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_return_loaned_message_from_subscription(
  const rmw_subscription_t * subscription,
  void * loaned_message)
{
  rmw_ret_t ret = validate_subscription(subscription);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (loaned_message == nullptr) {
    RMW_SET_ERROR_MSG("loaned subscription message is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  return rmw_fleetqox_cpp_release_subscription_loan(subscription, loaned_message);
}

rmw_client_t * rmw_create_client(
  const rmw_node_t * node,
  const rosidl_service_type_support_t * type_support,
  const char * service_name,
  const rmw_qos_profile_t * qos_policies)
{
  rmw_ret_t ret = validate_node(node);
  if (ret != RMW_RET_OK) {
    return nullptr;
  }
  if (type_support == nullptr || service_name == nullptr || qos_policies == nullptr) {
    RMW_SET_ERROR_MSG("client creation arguments are invalid");
    return nullptr;
  }
  rmw_client_t * client = rmw_client_allocate();
  if (client == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate client handle");
    return nullptr;
  }
  const rosidl_service_type_support_t * effective_type_support =
    resolve_effective_service_type_support(type_support);
  const auto * service_members = service_introspection_members(effective_type_support);
  const auto * cpp_service_members = service_cpp_introspection_members(effective_type_support);
  if (service_members == nullptr && cpp_service_members == nullptr) {
    rmw_client_free(client);
    RMW_SET_ERROR_MSG("client requires introspection C or C++ service type support");
    return nullptr;
  }
  const std::string type_name = service_type_name_from_type_support(effective_type_support);
  const std::string endpoint_id = allocate_service_endpoint_id(false);
  const std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> endpoint_gid =
    endpoint_gid_from_id(endpoint_id);
  FleetQoxServiceData * data =
    allocate_service_data(
    node->context->options.allocator,
    node->context,
    node,
    service_name,
    qos_policies,
    false,
    type_name,
    std::string(node->name != nullptr ? node->name : ""),
    std::string(node->namespace_ != nullptr ? node->namespace_ : ""),
    std::string(node->context->options.enclave != nullptr ? node->context->options.enclave : ""),
    node->context->actual_domain_id,
    endpoint_id,
    endpoint_gid,
    service_members,
    cpp_service_members);
  if (data == nullptr) {
    rmw_client_free(client);
    RMW_SET_ERROR_MSG("failed to allocate client data");
    return nullptr;
  }
  client->implementation_identifier = kIdentifier;
  client->data = data;
  client->service_name = data->service_name;
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    g_service_bus_endpoints.push_back(data);
    g_client_handles.push_back(client);
  }
  rmw_fleetqox_cpp_graph_register_client_endpoint(
    data->node_name.c_str(),
    data->node_namespace.c_str(),
    data->service_name,
    data->type_name.c_str(),
    data->endpoint_id.c_str(),
    &data->qos,
    data->domain_id);
  add_service_graph_renewal_endpoint(data);
  send_service_graph_advertisement(data, "add");
  return client;
}

rmw_ret_t rmw_destroy_client(rmw_node_t * node, rmw_client_t * client)
{
  rmw_ret_t ret = validate_node(node);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  ret = validate_client(client);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxServiceData * data = client_data(client);
  if (data == nullptr || data->owner_node != node) {
    RMW_SET_ERROR_MSG("client was not created by the supplied node");
    return RMW_RET_INVALID_ARGUMENT;
  }
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    g_service_bus_endpoints.erase(
      std::remove(g_service_bus_endpoints.begin(), g_service_bus_endpoints.end(), data),
      g_service_bus_endpoints.end());
    g_client_handles.erase(
      std::remove(g_client_handles.begin(), g_client_handles.end(), client),
      g_client_handles.end());
  }
  remove_service_graph_renewal_endpoint(data);
  rmw_fleetqox_cpp_graph_unregister_client_endpoint(data->endpoint_id.c_str());
  send_service_graph_advertisement(data, "remove");
  cancel_service_request_repairs_for_client(data->endpoint_id);
  deallocate_service_data(data);
  rmw_client_free(client);
  return RMW_RET_OK;
}

rmw_ret_t rmw_send_request(
  const rmw_client_t * client,
  const void * ros_request,
  int64_t * sequence_id)
{
  rmw_ret_t ret = validate_client(client);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (ros_request == nullptr || sequence_id == nullptr) {
    RMW_SET_ERROR_MSG("request arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  FleetQoxServiceData * data = client_data(client);
  if (data == nullptr || data->service_name == nullptr ||
    (data->request_members == nullptr && data->cpp_request_members == nullptr))
  {
    RMW_SET_ERROR_MSG("client data is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!sros2_service_operation_allowed(data, true, true)) {
    trace_service_event("deny_request_publish", data);
    RMW_SET_ERROR_MSG("service request publish denied by SROS2 permissions policy");
    return RMW_RET_ERROR;
  }
  std::vector<std::uint8_t> payload;
  if (!serialize_service_message(data, true, ros_request, &payload)) {
    RMW_SET_ERROR_MSG("failed to serialize service request with introspection type support");
    return RMW_RET_UNSUPPORTED;
  }
  std::int64_t next_sequence = 0;
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    next_sequence = data->next_sequence_id++;
  }
  const rmw_fleetqox_cpp::ServiceFrame frame{
    "request",
    data->service_name,
    data->type_name,
    data->endpoint_id,
    "",
    next_sequence,
    monotonic_timestamp_ns(),
    qos_duration_ns(data->qos.lifespan),
    payload,
    data->domain_id};
  rmw_fleetqox_cpp::ServiceFrame prioritized_frame = frame;
  prioritized_frame.client_priority = data->service_client_priority;
  prioritized_frame.client_weight = data->service_client_weight;
  prioritized_frame.request_deadline_ns = data->service_client_deadline_ns;
  trace_service_event("send_request", data, &prioritized_frame);
  const std::string encoded = rmw_fleetqox_cpp::encode_service_frame(prioritized_frame);
  if (!schedule_service_request_repair(data->endpoint_id, next_sequence, encoded)) {
    trace_service_request_repair_event(
      "schedule_failed", data->endpoint_id, next_sequence, 0);
  }
  ret = rmw_fleetqox_cpp_send_encoded_frame(encoded.data(), encoded.size());
  if (ret != RMW_RET_OK) {
    cancel_service_request_repair(
      data->endpoint_id, next_sequence, "initial_send_failed");
    return ret;
  }
  *sequence_id = next_sequence;
  return RMW_RET_OK;
}

rmw_ret_t rmw_take_response(
  const rmw_client_t * client,
  rmw_service_info_t * request_header,
  void * ros_response,
  bool * taken)
{
  rmw_ret_t ret = validate_client(client);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (request_header == nullptr || ros_response == nullptr || taken == nullptr) {
    RMW_SET_ERROR_MSG("response take arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  FleetQoxServiceData * data = client_data(client);
  if (data == nullptr ||
    (data->response_members == nullptr && data->cpp_response_members == nullptr))
  {
    RMW_SET_ERROR_MSG("client data is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_fleetqox_cpp::ServiceFrame frame{};
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    while (!data->response_queue.empty()) {
      frame = std::move(data->response_queue.front());
      data->response_queue.pop_front();
      if (drop_if_expired_service_frame(frame)) {
        frame = rmw_fleetqox_cpp::ServiceFrame{};
        continue;
      }
      break;
    }
    if (frame.role.empty()) {
      *taken = false;
      return RMW_RET_OK;
    }
  }
  if (!deserialize_service_message(data, false, &frame.serialized_payload, ros_response))
  {
    *taken = false;
    trace_service_event("take_response_deserialize_failed", data, &frame);
    RMW_SET_ERROR_MSG("failed to deserialize service response with introspection type support");
    return RMW_RET_UNSUPPORTED;
  }
  request_header->source_timestamp = frame.source_timestamp_ns;
  request_header->received_timestamp = monotonic_timestamp_ns();
  fill_request_id(data->endpoint_gid, frame.sequence_id, &request_header->request_id);
  *taken = true;
  trace_service_event("take_response", data, &frame);
  return RMW_RET_OK;
}

rmw_ret_t rmw_fleetqox_cpp_send_malformed_response(
  const rmw_service_t * service,
  rmw_request_id_t * request_header)
{
  rmw_ret_t ret = validate_service(service);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (request_header == nullptr) {
    RMW_SET_ERROR_MSG("malformed response request header is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  FleetQoxServiceData * data = service_data(service);
  if (data == nullptr || data->service_name == nullptr) {
    RMW_SET_ERROR_MSG("service data is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  std::string client_endpoint_id;
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    const auto found = data->pending_response_clients.find(request_key(*request_header));
    if (found != data->pending_response_clients.end()) {
      client_endpoint_id = found->second;
      data->pending_response_clients.erase(found);
    }
  }
  if (client_endpoint_id.empty()) {
    trace_service_event("send_malformed_response_unknown_target", data);
    RMW_SET_ERROR_MSG("malformed service response target is unknown");
    return RMW_RET_ERROR;
  }
  const rmw_fleetqox_cpp::ServiceFrame frame{
    "response",
    data->service_name,
    data->type_name,
    client_endpoint_id,
    data->endpoint_id,
    request_header->sequence_number,
    monotonic_timestamp_ns(),
    qos_duration_ns(data->qos.lifespan),
    std::vector<std::uint8_t>{0xff},
    data->domain_id};
  trace_service_event("send_malformed_response", data, &frame);
  const std::string encoded = rmw_fleetqox_cpp::encode_service_frame(frame);
  return rmw_fleetqox_cpp_send_encoded_frame(encoded.data(), encoded.size());
}

rmw_service_t * rmw_create_service(
  const rmw_node_t * node,
  const rosidl_service_type_support_t * type_support,
  const char * service_name,
  const rmw_qos_profile_t * qos_profile)
{
  rmw_ret_t ret = validate_node(node);
  if (ret != RMW_RET_OK) {
    return nullptr;
  }
  if (type_support == nullptr || service_name == nullptr || qos_profile == nullptr) {
    RMW_SET_ERROR_MSG("service creation arguments are invalid");
    return nullptr;
  }
  rmw_service_t * service = rmw_service_allocate();
  if (service == nullptr) {
    RMW_SET_ERROR_MSG("failed to allocate service handle");
    return nullptr;
  }
  const rosidl_service_type_support_t * effective_type_support =
    resolve_effective_service_type_support(type_support);
  const auto * service_members = service_introspection_members(effective_type_support);
  const auto * cpp_service_members = service_cpp_introspection_members(effective_type_support);
  if (service_members == nullptr && cpp_service_members == nullptr) {
    rmw_service_free(service);
    RMW_SET_ERROR_MSG("service requires introspection C or C++ service type support");
    return nullptr;
  }
  const std::string type_name = service_type_name_from_type_support(effective_type_support);
  const std::string endpoint_id = allocate_service_endpoint_id(true);
  const std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> endpoint_gid =
    endpoint_gid_from_id(endpoint_id);
  FleetQoxServiceData * data =
    allocate_service_data(
    node->context->options.allocator,
    node->context,
    node,
    service_name,
    qos_profile,
    true,
    type_name,
    std::string(node->name != nullptr ? node->name : ""),
    std::string(node->namespace_ != nullptr ? node->namespace_ : ""),
    std::string(node->context->options.enclave != nullptr ? node->context->options.enclave : ""),
    node->context->actual_domain_id,
    endpoint_id,
    endpoint_gid,
    service_members,
    cpp_service_members);
  if (data == nullptr) {
    rmw_service_free(service);
    RMW_SET_ERROR_MSG("failed to allocate service data");
    return nullptr;
  }
  service->implementation_identifier = kIdentifier;
  service->data = data;
  service->service_name = data->service_name;
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    g_service_bus_endpoints.push_back(data);
    g_service_handles.push_back(service);
  }
  rmw_fleetqox_cpp_graph_register_service_endpoint(
    data->node_name.c_str(),
    data->node_namespace.c_str(),
    data->service_name,
    data->type_name.c_str(),
    data->endpoint_id.c_str(),
    &data->qos,
    data->domain_id);
  add_service_graph_renewal_endpoint(data);
  send_service_graph_advertisement(data, "add");
  return service;
}

rmw_ret_t rmw_destroy_service(rmw_node_t * node, rmw_service_t * service)
{
  rmw_ret_t ret = validate_node(node);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  ret = validate_service(service);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxServiceData * data = service_data(service);
  if (data == nullptr || data->owner_node != node) {
    RMW_SET_ERROR_MSG("service was not created by the supplied node");
    return RMW_RET_INVALID_ARGUMENT;
  }
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    g_service_bus_endpoints.erase(
      std::remove(g_service_bus_endpoints.begin(), g_service_bus_endpoints.end(), data),
      g_service_bus_endpoints.end());
    g_service_handles.erase(
      std::remove(g_service_handles.begin(), g_service_handles.end(), service),
      g_service_handles.end());
  }
  remove_service_graph_renewal_endpoint(data);
  rmw_fleetqox_cpp_graph_unregister_service_endpoint(data->endpoint_id.c_str());
  send_service_graph_advertisement(data, "remove");
  deallocate_service_data(data);
  rmw_service_free(service);
  return RMW_RET_OK;
}

rmw_ret_t rmw_take_request(
  const rmw_service_t * service,
  rmw_service_info_t * request_header,
  void * ros_request,
  bool * taken)
{
  rmw_ret_t ret = validate_service(service);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (request_header == nullptr || ros_request == nullptr || taken == nullptr) {
    RMW_SET_ERROR_MSG("request take arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  FleetQoxServiceData * data = service_data(service);
  if (data == nullptr ||
    (data->request_members == nullptr && data->cpp_request_members == nullptr))
  {
    RMW_SET_ERROR_MSG("service data is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_fleetqox_cpp::ServiceFrame frame{};
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    if (!data->request_queue.empty() &&
      data->pending_response_clients.size() >= data->pending_response_limit)
    {
      g_service_pending_response_backpressure.fetch_add(1, std::memory_order_relaxed);
      trace_service_event(
        "pending_response_backpressure", data, &data->request_queue.front(),
        data->request_queue.size());
      *taken = false;
      return RMW_RET_OK;
    }
    while (!data->request_queue.empty()) {
      std::map<
        std::string,
        std::deque<rmw_fleetqox_cpp::ServiceFrame>::iterator> client_heads;
      for (auto candidate = data->request_queue.begin();
        candidate != data->request_queue.end(); ++candidate)
      {
        const auto existing = client_heads.find(candidate->client_endpoint_id);
        if (existing == client_heads.end() ||
          candidate->sequence_id < existing->second->sequence_id)
        {
          client_heads[candidate->client_endpoint_id] = candidate;
        }
      }
      auto selected = data->request_queue.end();
      if (data->deadline_service_scheduler) {
        std::uint64_t earliest_deadline = std::numeric_limits<std::uint64_t>::max();
        for (const auto & client_head : client_heads) {
          const auto candidate = client_head.second;
          const std::uint64_t deadline = effective_service_deadline(
            *candidate, data->service_deadline_aging_ns);
          if (selected == data->request_queue.end() || deadline < earliest_deadline) {
            selected = candidate;
            earliest_deadline = deadline;
          } else if (
            deadline == earliest_deadline &&
            !data->last_dequeued_client_endpoint_id.empty() &&
            candidate->client_endpoint_id != data->last_dequeued_client_endpoint_id)
          {
            selected = candidate;
          }
        }
        if (selected != data->request_queue.end()) {
          g_service_deadline_dequeues.fetch_add(1, std::memory_order_relaxed);
          if (selected->request_deadline_ns == 0) {
            g_service_deadline_aged_dequeues.fetch_add(1, std::memory_order_relaxed);
          }
        }
      } else if (data->weighted_service_scheduler) {
        std::int64_t total_weight = 0;
        std::int64_t best_current = std::numeric_limits<std::int64_t>::min();
        for (const auto & client_head : client_heads) {
          const auto candidate = client_head.second;
          const std::int64_t weight = static_cast<std::int64_t>(
            std::max<std::uint64_t>(
              1, std::min<std::uint64_t>(candidate->client_weight, 64)));
          std::int64_t & current =
            data->weighted_service_current[candidate->client_endpoint_id];
          current += weight;
          total_weight += weight;
          if (selected == data->request_queue.end() || current > best_current) {
            selected = candidate;
            best_current = current;
          }
        }
        for (auto state = data->weighted_service_current.begin();
          state != data->weighted_service_current.end();)
        {
          if (client_heads.find(state->first) == client_heads.end()) {
            state = data->weighted_service_current.erase(state);
          } else {
            ++state;
          }
        }
        if (selected != data->request_queue.end()) {
          data->weighted_service_current[selected->client_endpoint_id] -= total_weight;
          g_service_weighted_dequeues.fetch_add(1, std::memory_order_relaxed);
        }
      } else {
        const std::int64_t now_ns = monotonic_timestamp_ns();
        std::uint64_t maximum_priority = 0;
        std::uint64_t minimum_priority = std::numeric_limits<std::uint64_t>::max();
        for (const auto & client_head : client_heads) {
          const auto & candidate = *client_head.second;
          const std::uint64_t priority = effective_service_priority(
            candidate, now_ns, data->service_priority_aging_ns);
          maximum_priority = std::max(maximum_priority, priority);
          minimum_priority = std::min(minimum_priority, priority);
        }
        for (const auto & client_head : client_heads) {
          const auto candidate = client_head.second;
          if (effective_service_priority(
              *candidate, now_ns, data->service_priority_aging_ns) != maximum_priority)
          {
            continue;
          }
          if (selected == data->request_queue.end()) {
            selected = candidate;
          }
          if (!data->last_dequeued_client_endpoint_id.empty() &&
            candidate->client_endpoint_id != data->last_dequeued_client_endpoint_id)
          {
            selected = candidate;
            break;
          }
        }
        const std::uint64_t selected_effective_priority =
          effective_service_priority(*selected, now_ns, data->service_priority_aging_ns);
        if (maximum_priority > minimum_priority) {
          g_service_priority_dequeues.fetch_add(1, std::memory_order_relaxed);
        }
        if (selected_effective_priority > selected->client_priority) {
          g_service_aged_priority_dequeues.fetch_add(1, std::memory_order_relaxed);
        }
      }
      frame = std::move(*selected);
      data->request_queue.erase(selected);
      if (drop_if_expired_service_frame(frame)) {
        frame = rmw_fleetqox_cpp::ServiceFrame{};
        continue;
      }
      data->last_dequeued_client_endpoint_id = frame.client_endpoint_id;
      break;
    }
    if (frame.role.empty()) {
      *taken = false;
      return RMW_RET_OK;
    }
  }
  if (!deserialize_service_message(data, true, &frame.serialized_payload, ros_request))
  {
    *taken = false;
    trace_service_event("take_request_deserialize_failed", data, &frame);
    RMW_SET_ERROR_MSG("failed to deserialize service request with introspection type support");
    return RMW_RET_UNSUPPORTED;
  }
  const std::array<std::uint8_t, RMW_GID_STORAGE_SIZE> client_gid =
    endpoint_gid_from_id(frame.client_endpoint_id);
  request_header->source_timestamp = frame.source_timestamp_ns;
  request_header->received_timestamp = monotonic_timestamp_ns();
  fill_request_id(client_gid, frame.sequence_id, &request_header->request_id);
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    data->pending_response_clients[request_key(request_header->request_id)] = frame.client_endpoint_id;
    update_max_observed(
      &g_service_pending_response_max_observed,
      data->pending_response_clients.size());
  }
  *taken = true;
  trace_service_event("take_request", data, &frame);
  return RMW_RET_OK;
}

rmw_ret_t rmw_send_response(
  const rmw_service_t * service,
  rmw_request_id_t * request_header,
  void * ros_response)
{
  rmw_ret_t ret = validate_service(service);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (request_header == nullptr || ros_response == nullptr) {
    RMW_SET_ERROR_MSG("response send arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  FleetQoxServiceData * data = service_data(service);
  if (data == nullptr || data->service_name == nullptr ||
    (data->response_members == nullptr && data->cpp_response_members == nullptr))
  {
    RMW_SET_ERROR_MSG("service data is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!sros2_service_operation_allowed(data, true, false)) {
    trace_service_event("deny_response_publish", data);
    RMW_SET_ERROR_MSG("service response publish denied by SROS2 permissions policy");
    return RMW_RET_ERROR;
  }
  std::vector<std::uint8_t> payload;
  if (!serialize_service_message(data, false, ros_response, &payload)) {
    trace_service_event("send_response_serialize_failed", data);
    RMW_SET_ERROR_MSG("failed to serialize service response with introspection type support");
    return RMW_RET_UNSUPPORTED;
  }
  std::string client_endpoint_id;
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    const auto found = data->pending_response_clients.find(request_key(*request_header));
    if (found != data->pending_response_clients.end()) {
      client_endpoint_id = found->second;
      data->pending_response_clients.erase(found);
    }
  }
  if (client_endpoint_id.empty()) {
    trace_service_event("send_response_unknown_target", data);
    RMW_SET_ERROR_MSG("service response target is unknown");
    return RMW_RET_ERROR;
  }
  const rmw_fleetqox_cpp::ServiceFrame frame{
    "response",
    data->service_name,
    data->type_name,
    client_endpoint_id,
    data->endpoint_id,
    request_header->sequence_number,
    monotonic_timestamp_ns(),
    qos_duration_ns(data->qos.lifespan),
    payload,
    data->domain_id};
  trace_service_event("send_response", data, &frame);
  const std::string encoded = rmw_fleetqox_cpp::encode_service_frame(frame);
  const std::string replay_key =
    service_response_replay_key(client_endpoint_id, request_header->sequence_number);
  std::string durable_snapshot;
  std::string durable_path;
  std::unique_lock<std::mutex> durable_lock(
    g_service_durable_replay_mutex, std::defer_lock);
  if (!data->durable_replay_path.empty()) {
    durable_lock.lock();
  }
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    store_bounded_service_response_replay(
      data,
      replay_key,
      encoded);
    if (!data->durable_replay_path.empty()) {
      durable_path = data->durable_replay_path;
      durable_snapshot = durable_service_replay_snapshot(data);
    }
  }
  if (!durable_path.empty()) {
    if (persist_durable_service_replay(durable_path, durable_snapshot)) {
      std::lock_guard<std::mutex> lock(g_service_bus_mutex);
      data->durable_response_keys.clear();
      for (const std::string & key : data->response_replay_order) {
        if (data->response_replay_cache.find(key) !=
          data->response_replay_cache.end())
        {
          data->durable_response_keys.insert(key);
        }
      }
      g_service_durable_replays_persisted.fetch_add(1, std::memory_order_relaxed);
      trace_service_event("durable_response_persisted", data, &frame);
    } else {
      g_service_durable_replay_failures.fetch_add(1, std::memory_order_relaxed);
      trace_service_event("durable_response_persist_failed", data, &frame);
    }
  }
  if (durable_lock.owns_lock()) {
    durable_lock.unlock();
  }
  return send_service_frame_with_repeats(
    encoded,
    "FLEETQOX_RMW_SERVICE_RESPONSE_REPEATS",
    "FLEETQOX_RMW_SERVICE_RESPONSE_REPEAT_INTERVAL_MS");
}

rmw_ret_t rmw_take_event(const rmw_event_t * event_handle, void * event_info, bool * taken)
{
  if (event_handle == nullptr || event_info == nullptr || taken == nullptr) {
    RMW_SET_ERROR_MSG("event take arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!identifier_matches(event_handle->implementation_identifier)) {
    RMW_SET_ERROR_MSG("event is not from rmw_fleetqox_cpp");
    return RMW_RET_INCORRECT_RMW_IMPLEMENTATION;
  }
  FleetQoxEventData * data = event_data(event_handle);
  if (data == nullptr || !qos_event_type_supported(event_handle->event_type)) {
    RMW_SET_ERROR_MSG("event data is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (data->publisher_event) {
    return rmw_fleetqox_cpp_take_publisher_qos_event(
      static_cast<const rmw_publisher_t *>(data->owner),
      data->event_type,
      event_info,
      taken);
  }
  return rmw_fleetqox_cpp_take_subscription_qos_event(
    static_cast<const rmw_subscription_t *>(data->owner),
    data->event_type,
    event_info,
    taken);
}

bool rmw_event_type_is_supported(rmw_event_type_t rmw_event_type)
{
  return qos_event_type_supported(rmw_event_type);
}

rmw_ret_t rmw_event_fini(rmw_event_t * event)
{
  if (event == nullptr) {
    RMW_SET_ERROR_MSG("event is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!identifier_matches(event->implementation_identifier)) {
    RMW_SET_ERROR_MSG("event is not from rmw_fleetqox_cpp");
    return RMW_RET_INCORRECT_RMW_IMPLEMENTATION;
  }
  FleetQoxEventData * data = event_data(event);
  if (data != nullptr) {
    const rmw_ret_t clear_ret = data->publisher_event ?
      rmw_fleetqox_cpp_set_publisher_qos_event_callback(
        static_cast<const rmw_publisher_t *>(data->owner),
        data->event_type,
        nullptr,
        nullptr) :
      rmw_fleetqox_cpp_set_subscription_qos_event_callback(
        static_cast<const rmw_subscription_t *>(data->owner),
        data->event_type,
        nullptr,
        nullptr);
    (void)clear_ret;
  }
  {
    std::lock_guard<std::mutex> lock(g_event_mutex);
    g_event_handles.erase(
      std::remove(g_event_handles.begin(), g_event_handles.end(), event),
      g_event_handles.end());
    g_event_data.erase(
      std::remove(g_event_data.begin(), g_event_data.end(), data),
      g_event_data.end());
  }
  if (data != nullptr) {
    rcutils_allocator_t allocator = rcutils_get_default_allocator();
    data->~FleetQoxEventData();
    allocator.deallocate(data, allocator.state);
  }
  event->implementation_identifier = nullptr;
  event->data = nullptr;
  event->event_type = RMW_EVENT_INVALID;
  g_qos_events_finalized.fetch_add(1, std::memory_order_relaxed);
  return RMW_RET_OK;
}

rmw_ret_t rmw_get_gid_for_client(const rmw_client_t * client, rmw_gid_t * gid)
{
  rmw_ret_t ret = validate_client(client);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (gid == nullptr) {
    RMW_SET_ERROR_MSG("client gid output is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  FleetQoxServiceData * data = client_data(client);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("client data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  std::memset(gid, 0, sizeof(*gid));
  gid->implementation_identifier = kIdentifier;
  std::memcpy(gid->data, data->endpoint_gid.data(), data->endpoint_gid.size());
  return RMW_RET_OK;
}

rmw_ret_t rmw_get_gid_for_publisher(const rmw_publisher_t * publisher, rmw_gid_t * gid)
{
  rmw_ret_t ret = validate_publisher(publisher);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (gid == nullptr) {
    RMW_SET_ERROR_MSG("publisher gid output is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!rmw_fleetqox_cpp_publisher_gid(publisher, gid)) {
    fill_pointer_gid(publisher, gid);
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_compare_gids_equal(const rmw_gid_t * gid1, const rmw_gid_t * gid2, bool * result)
{
  if (gid1 == nullptr || gid2 == nullptr || result == nullptr) {
    RMW_SET_ERROR_MSG("gid comparison arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!identifier_matches(gid1->implementation_identifier) ||
    !identifier_matches(gid2->implementation_identifier))
  {
    RMW_SET_ERROR_MSG("gid is not from rmw_fleetqox_cpp");
    return RMW_RET_INCORRECT_RMW_IMPLEMENTATION;
  }
  *result = std::memcmp(gid1->data, gid2->data, RMW_GID_STORAGE_SIZE) == 0;
  return RMW_RET_OK;
}

rmw_ret_t rmw_service_response_publisher_get_actual_qos(
  const rmw_service_t * service,
  rmw_qos_profile_t * qos)
{
  rmw_ret_t ret = validate_service(service);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (qos == nullptr) {
    RMW_SET_ERROR_MSG("service qos output is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  FleetQoxServiceData * data = service_data(service);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("service data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *qos = data->qos;
  return RMW_RET_OK;
}

rmw_ret_t rmw_service_request_subscription_get_actual_qos(
  const rmw_service_t * service,
  rmw_qos_profile_t * qos)
{
  return rmw_service_response_publisher_get_actual_qos(service, qos);
}

rmw_ret_t rmw_service_server_is_available(
  const rmw_node_t * node,
  const rmw_client_t * client,
  bool * is_available)
{
  rmw_ret_t ret = validate_node(node);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  ret = validate_client(client);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (is_available == nullptr) {
    RMW_SET_ERROR_MSG("service availability output is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  FleetQoxServiceData * data = client_data(client);
  if (data == nullptr || data->service_name == nullptr) {
    RMW_SET_ERROR_MSG("client data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (data->owner_node != node) {
    RMW_SET_ERROR_MSG("client was not created by the supplied node");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const size_t service_count = rmw_fleetqox_cpp_graph_matching_service_count_in_domain(
    data->service_name, data->type_name.c_str(), &data->qos, data->domain_id);
  *is_available = service_count > 0;
  if (trace_service_enabled()) {
    std::fprintf(
      stderr,
      "fleetqox service event=server_is_available service=%s endpoint=%s count=%zu available=%s\n",
      data->service_name,
      data->endpoint_id.c_str(),
      service_count,
      *is_available ? "true" : "false");
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_set_log_severity(rmw_log_severity_t severity)
{
  (void)severity;
  return RMW_RET_OK;
}

rmw_ret_t rmw_qos_profile_check_compatible(
  const rmw_qos_profile_t publisher_profile,
  const rmw_qos_profile_t subscription_profile,
  rmw_qos_compatibility_type_t * compatibility,
  char * reason,
  size_t reason_size)
{
  if (compatibility == nullptr || (reason == nullptr && reason_size != 0)) {
    RMW_SET_ERROR_MSG("QoS compatibility output arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *compatibility = RMW_QOS_COMPATIBILITY_OK;
  clear_reason(reason, reason_size);
  if (publisher_profile.reliability == RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT &&
    subscription_profile.reliability == RMW_QOS_POLICY_RELIABILITY_RELIABLE)
  {
    *compatibility = RMW_QOS_COMPATIBILITY_ERROR;
    append_qos_reason(reason, reason_size, "ERROR: best-effort publisher and reliable subscription;");
  }
  if (publisher_profile.durability == RMW_QOS_POLICY_DURABILITY_VOLATILE &&
    subscription_profile.durability == RMW_QOS_POLICY_DURABILITY_TRANSIENT_LOCAL)
  {
    *compatibility = RMW_QOS_COMPATIBILITY_ERROR;
    append_qos_reason(reason, reason_size, "ERROR: volatile publisher and transient-local subscription;");
  }

  const rmw_time_t deadline_default = RMW_QOS_DEADLINE_DEFAULT;
  const bool publisher_deadline_default =
    qos_time_equal(publisher_profile.deadline, deadline_default);
  const bool subscription_deadline_default =
    qos_time_equal(subscription_profile.deadline, deadline_default);
  if (publisher_deadline_default && !subscription_deadline_default) {
    *compatibility = RMW_QOS_COMPATIBILITY_ERROR;
    append_qos_reason(
      reason, reason_size, "ERROR: subscription has a deadline but publisher does not;");
  } else if (!publisher_deadline_default && !subscription_deadline_default &&
    qos_time_less(subscription_profile.deadline, publisher_profile.deadline))
  {
    *compatibility = RMW_QOS_COMPATIBILITY_ERROR;
    append_qos_reason(
      reason, reason_size, "ERROR: subscription deadline is less than publisher deadline;");
  }

  if (publisher_profile.liveliness == RMW_QOS_POLICY_LIVELINESS_AUTOMATIC &&
    subscription_profile.liveliness == RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC)
  {
    *compatibility = RMW_QOS_COMPATIBILITY_ERROR;
    append_qos_reason(
      reason, reason_size,
      "ERROR: automatic publisher liveliness cannot satisfy manual-by-topic subscription;");
  }

  const rmw_time_t lease_default = RMW_QOS_LIVELINESS_LEASE_DURATION_DEFAULT;
  const bool publisher_lease_default =
    qos_time_equal(publisher_profile.liveliness_lease_duration, lease_default);
  const bool subscription_lease_default =
    qos_time_equal(subscription_profile.liveliness_lease_duration, lease_default);
  if (publisher_lease_default && !subscription_lease_default) {
    *compatibility = RMW_QOS_COMPATIBILITY_ERROR;
    append_qos_reason(
      reason, reason_size,
      "ERROR: subscription has a liveliness lease duration but publisher does not;");
  } else if (!publisher_lease_default && !subscription_lease_default &&
    qos_time_less(
      subscription_profile.liveliness_lease_duration,
      publisher_profile.liveliness_lease_duration))
  {
    *compatibility = RMW_QOS_COMPATIBILITY_ERROR;
    append_qos_reason(
      reason, reason_size,
      "ERROR: subscription liveliness lease is less than publisher lease;");
  }

  if (*compatibility == RMW_QOS_COMPATIBILITY_OK) {
    const bool publisher_reliability_unknown =
      reliability_unknown(publisher_profile.reliability);
    const bool subscription_reliability_unknown =
      reliability_unknown(subscription_profile.reliability);
    const bool publisher_durability_unknown =
      durability_unknown(publisher_profile.durability);
    const bool subscription_durability_unknown =
      durability_unknown(subscription_profile.durability);
    const bool publisher_liveliness_unknown =
      liveliness_unknown(publisher_profile.liveliness);
    const bool subscription_liveliness_unknown =
      liveliness_unknown(subscription_profile.liveliness);

    if (publisher_reliability_unknown && subscription_reliability_unknown) {
      *compatibility = RMW_QOS_COMPATIBILITY_WARNING;
      append_qos_reason(reason, reason_size, "WARNING: publisher and subscription reliability are unknown;");
    } else if (publisher_reliability_unknown &&
      subscription_profile.reliability == RMW_QOS_POLICY_RELIABILITY_RELIABLE)
    {
      *compatibility = RMW_QOS_COMPATIBILITY_WARNING;
      append_qos_reason(reason, reason_size, "WARNING: reliable subscription but publisher reliability is unknown;");
    } else if (publisher_profile.reliability == RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT &&
      subscription_reliability_unknown)
    {
      *compatibility = RMW_QOS_COMPATIBILITY_WARNING;
      append_qos_reason(reason, reason_size, "WARNING: best-effort publisher but subscription reliability is unknown;");
    }

    if (publisher_durability_unknown && subscription_durability_unknown) {
      *compatibility = RMW_QOS_COMPATIBILITY_WARNING;
      append_qos_reason(reason, reason_size, "WARNING: publisher and subscription durability are unknown;");
    } else if (publisher_durability_unknown &&
      subscription_profile.durability == RMW_QOS_POLICY_DURABILITY_TRANSIENT_LOCAL)
    {
      *compatibility = RMW_QOS_COMPATIBILITY_WARNING;
      append_qos_reason(reason, reason_size, "WARNING: transient-local subscription but publisher durability is unknown;");
    } else if (publisher_profile.durability == RMW_QOS_POLICY_DURABILITY_VOLATILE &&
      subscription_durability_unknown)
    {
      *compatibility = RMW_QOS_COMPATIBILITY_WARNING;
      append_qos_reason(reason, reason_size, "WARNING: volatile publisher but subscription durability is unknown;");
    }

    if (publisher_liveliness_unknown && subscription_liveliness_unknown) {
      *compatibility = RMW_QOS_COMPATIBILITY_WARNING;
      append_qos_reason(reason, reason_size, "WARNING: publisher and subscription liveliness are unknown;");
    } else if (publisher_liveliness_unknown &&
      subscription_profile.liveliness == RMW_QOS_POLICY_LIVELINESS_MANUAL_BY_TOPIC)
    {
      *compatibility = RMW_QOS_COMPATIBILITY_WARNING;
      append_qos_reason(reason, reason_size, "WARNING: manual subscription liveliness but publisher is unknown;");
    } else if (publisher_profile.liveliness == RMW_QOS_POLICY_LIVELINESS_AUTOMATIC &&
      subscription_liveliness_unknown)
    {
      *compatibility = RMW_QOS_COMPATIBILITY_WARNING;
      append_qos_reason(reason, reason_size, "WARNING: automatic publisher liveliness but subscription is unknown;");
    }
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_publisher_get_network_flow_endpoints(
  const rmw_publisher_t * publisher,
  rcutils_allocator_t * allocator,
  rmw_network_flow_endpoint_array_t * network_flow_endpoint_array)
{
  rmw_ret_t ret = validate_publisher(publisher);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  return fill_udp_network_flow_endpoint(allocator, network_flow_endpoint_array);
}

rmw_ret_t rmw_subscription_get_network_flow_endpoints(
  const rmw_subscription_t * subscription,
  rcutils_allocator_t * allocator,
  rmw_network_flow_endpoint_array_t * network_flow_endpoint_array)
{
  rmw_ret_t ret = validate_subscription(subscription);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  return fill_udp_network_flow_endpoint(allocator, network_flow_endpoint_array);
}

rmw_ret_t rmw_client_request_publisher_get_actual_qos(
  const rmw_client_t * client,
  rmw_qos_profile_t * qos)
{
  rmw_ret_t ret = validate_client(client);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (qos == nullptr) {
    RMW_SET_ERROR_MSG("client qos output is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  FleetQoxServiceData * data = client_data(client);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("client data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *qos = data->qos;
  return RMW_RET_OK;
}

rmw_ret_t rmw_client_response_subscription_get_actual_qos(
  const rmw_client_t * client,
  rmw_qos_profile_t * qos)
{
  return rmw_client_request_publisher_get_actual_qos(client, qos);
}

rmw_ret_t rmw_service_set_on_new_request_callback(
  rmw_service_t * service,
  rmw_event_callback_t callback,
  const void * user_data)
{
  rmw_ret_t ret = validate_service(service);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxServiceData * data = service_data(service);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("service data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  size_t pending = 0;
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    data->on_new_request_callback = callback;
    data->on_new_request_user_data = user_data;
    pending = data->request_queue.size();
  }
  if (callback != nullptr && pending > 0) {
    callback(user_data, pending);
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_client_set_on_new_response_callback(
  rmw_client_t * client,
  rmw_event_callback_t callback,
  const void * user_data)
{
  rmw_ret_t ret = validate_client(client);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  FleetQoxServiceData * data = client_data(client);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("client data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  size_t pending = 0;
  {
    std::lock_guard<std::mutex> lock(g_service_bus_mutex);
    data->on_new_response_callback = callback;
    data->on_new_response_user_data = user_data;
    pending = data->response_queue.size();
  }
  if (callback != nullptr && pending > 0) {
    callback(user_data, pending);
  }
  return RMW_RET_OK;
}

rmw_ret_t rmw_event_set_callback(
  rmw_event_t * event,
  rmw_event_callback_t callback,
  const void * user_data)
{
  if (event == nullptr) {
    RMW_SET_ERROR_MSG("event is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!identifier_matches(event->implementation_identifier)) {
    RMW_SET_ERROR_MSG("event is not from rmw_fleetqox_cpp");
    return RMW_RET_INCORRECT_RMW_IMPLEMENTATION;
  }
  FleetQoxEventData * data = event_data(event);
  if (data == nullptr) {
    RMW_SET_ERROR_MSG("event data is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  data->callback = callback;
  data->user_data = user_data;
  const rmw_ret_t ret = data->publisher_event ?
    rmw_fleetqox_cpp_set_publisher_qos_event_callback(
      static_cast<const rmw_publisher_t *>(data->owner),
      data->event_type,
      callback,
      user_data) :
    rmw_fleetqox_cpp_set_subscription_qos_event_callback(
      static_cast<const rmw_subscription_t *>(data->owner),
      data->event_type,
      callback,
      user_data);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  g_qos_event_callbacks_set.fetch_add(1, std::memory_order_relaxed);
  return RMW_RET_OK;
}

bool rmw_feature_supported(rmw_feature_t feature)
{
  return feature == RMW_FEATURE_MESSAGE_INFO_PUBLICATION_SEQUENCE_NUMBER ||
         feature == RMW_FEATURE_MESSAGE_INFO_RECEPTION_SEQUENCE_NUMBER ||
         feature == RMW_MIDDLEWARE_CAN_TAKE_DYNAMIC_MESSAGE;
}

rmw_ret_t rmw_take_dynamic_message(
  const rmw_subscription_t * subscription,
  rosidl_dynamic_typesupport_dynamic_data_t * dynamic_message,
  bool * taken,
  rmw_subscription_allocation_t * allocation)
{
  (void)allocation;
  rmw_ret_t ret = validate_subscription(subscription);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (taken == nullptr) {
    RMW_SET_ERROR_MSG("dynamic message taken output is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *taken = false;
  if (dynamic_message == nullptr || dynamic_message->serialization_support == nullptr ||
    dynamic_message->impl.handle == nullptr)
  {
    RMW_SET_ERROR_MSG("dynamic message is not initialized");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rcutils_allocator_t allocator = dynamic_message->allocator;
  if (!rcutils_allocator_is_valid(&allocator)) {
    allocator = rcutils_get_default_allocator();
  }
  rmw_serialized_message_t serialized = rmw_get_zero_initialized_serialized_message();
  if (rmw_serialized_message_init(&serialized, 0, &allocator) != RMW_RET_OK) {
    RMW_SET_ERROR_MSG("failed to initialize serialized buffer for dynamic take");
    return RMW_RET_BAD_ALLOC;
  }
  const rmw_ret_t take_ret = rmw_take_serialized_message(
    subscription, &serialized, taken, allocation);
  if (take_ret != RMW_RET_OK || !*taken) {
    const rmw_ret_t fini_ret = rmw_serialized_message_fini(&serialized);
    (void)fini_ret;
    return take_ret;
  }
  const rcutils_ret_t deserialize_ret =
    rosidl_dynamic_typesupport_dynamic_data_deserialize(dynamic_message, &serialized);
  const rmw_ret_t fini_ret = rmw_serialized_message_fini(&serialized);
  if (deserialize_ret != RCUTILS_RET_OK) {
    *taken = false;
    RMW_SET_ERROR_MSG("dynamic message deserialization failed");
    return RMW_RET_ERROR;
  }
  return fini_ret;
}

rmw_ret_t rmw_take_dynamic_message_with_info(
  const rmw_subscription_t * subscription,
  rosidl_dynamic_typesupport_dynamic_data_t * dynamic_message,
  bool * taken,
  rmw_message_info_t * message_info,
  rmw_subscription_allocation_t * allocation)
{
  if (message_info == nullptr) {
    RMW_SET_ERROR_MSG("dynamic message info output is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rmw_ret_t ret = validate_subscription(subscription);
  if (ret != RMW_RET_OK) {
    return ret;
  }
  if (taken == nullptr) {
    RMW_SET_ERROR_MSG("dynamic message taken output is null");
    return RMW_RET_INVALID_ARGUMENT;
  }
  *taken = false;
  if (dynamic_message == nullptr || dynamic_message->serialization_support == nullptr ||
    dynamic_message->impl.handle == nullptr)
  {
    RMW_SET_ERROR_MSG("dynamic message is not initialized");
    return RMW_RET_INVALID_ARGUMENT;
  }
  rcutils_allocator_t allocator = dynamic_message->allocator;
  if (!rcutils_allocator_is_valid(&allocator)) {
    allocator = rcutils_get_default_allocator();
  }
  rmw_serialized_message_t serialized = rmw_get_zero_initialized_serialized_message();
  if (rmw_serialized_message_init(&serialized, 0, &allocator) != RMW_RET_OK) {
    RMW_SET_ERROR_MSG("failed to initialize serialized buffer for dynamic take with info");
    return RMW_RET_BAD_ALLOC;
  }
  ret = rmw_take_serialized_message_with_info(
    subscription, &serialized, taken, message_info, allocation);
  if (ret != RMW_RET_OK || !*taken) {
    const rmw_ret_t fini_ret = rmw_serialized_message_fini(&serialized);
    (void)fini_ret;
    return ret;
  }
  const rcutils_ret_t deserialize_ret =
    rosidl_dynamic_typesupport_dynamic_data_deserialize(dynamic_message, &serialized);
  const rmw_ret_t fini_ret = rmw_serialized_message_fini(&serialized);
  if (deserialize_ret != RCUTILS_RET_OK) {
    *taken = false;
    RMW_SET_ERROR_MSG("dynamic message deserialization with info failed");
    return RMW_RET_ERROR;
  }
  return fini_ret;
}

rmw_ret_t rmw_serialization_support_init(
  const char * serialization_lib_name,
  rcutils_allocator_t * allocator,
  rosidl_dynamic_typesupport_serialization_support_t * serialization_support)
{
  if (serialization_lib_name == nullptr || allocator == nullptr || serialization_support == nullptr) {
    RMW_SET_ERROR_MSG("serialization support init arguments are invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (!rcutils_allocator_is_valid(allocator)) {
    RMW_SET_ERROR_MSG("dynamic serialization support allocator is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  if (serialization_support->serialization_library_identifier != nullptr ||
    serialization_support->impl.handle != nullptr)
  {
    RMW_SET_ERROR_MSG("dynamic serialization support output is not zero initialized");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const std::string library_name(serialization_lib_name);
  if (library_name.empty() ||
    library_name.find('/') != std::string::npos ||
    library_name.find("..") != std::string::npos)
  {
    RMW_SET_ERROR_MSG("dynamic serialization library name is invalid");
    return RMW_RET_INVALID_ARGUMENT;
  }
  const std::string shared_library = "lib" + library_name + ".so";
  void * handle = ::dlopen(shared_library.c_str(), RTLD_NOW | RTLD_LOCAL);
  if (handle == nullptr) {
    const char * error = ::dlerror();
    RMW_SET_ERROR_MSG_WITH_FORMAT_STRING(
      "failed to load dynamic serialization library %s: %s",
      shared_library.c_str(), error == nullptr ? "unknown dlopen error" : error);
    return RMW_RET_UNSUPPORTED;
  }
  using InitImpl = rcutils_ret_t (*)(
    rcutils_allocator_t *, rosidl_dynamic_typesupport_serialization_support_impl_t *);
  using InitInterface = rcutils_ret_t (*)(
    rcutils_allocator_t *, rosidl_dynamic_typesupport_serialization_support_interface_t *);
  const std::string symbol_prefix = library_name == "rosidl_dynamic_typesupport_fastrtps" ?
    "rosidl_dynamic_typesupport_fastrtps" : library_name;
  const std::string impl_symbol = symbol_prefix + "_init_serialization_support_impl";
  const std::string interface_symbol =
    symbol_prefix + "_init_serialization_support_interface";
  auto init_impl = reinterpret_cast<InitImpl>(::dlsym(handle, impl_symbol.c_str()));
  auto init_interface = reinterpret_cast<InitInterface>(
    ::dlsym(handle, interface_symbol.c_str()));
  if (init_impl == nullptr || init_interface == nullptr) {
    ::dlclose(handle);
    RMW_SET_ERROR_MSG("dynamic serialization library does not expose the ROS interface");
    return RMW_RET_UNSUPPORTED;
  }
  rosidl_dynamic_typesupport_serialization_support_impl_t impl =
    rosidl_dynamic_typesupport_get_zero_initialized_serialization_support_impl();
  rosidl_dynamic_typesupport_serialization_support_interface_t methods =
    rosidl_dynamic_typesupport_get_zero_initialized_serialization_support_interface();
  if (init_impl(allocator, &impl) != RCUTILS_RET_OK ||
    init_interface(allocator, &methods) != RCUTILS_RET_OK ||
    rosidl_dynamic_typesupport_serialization_support_init(
      &impl, &methods, allocator, serialization_support) != RCUTILS_RET_OK)
  {
    ::dlclose(handle);
    RMW_SET_ERROR_MSG("failed to initialize dynamic serialization support");
    return RMW_RET_ERROR;
  }
  {
    std::lock_guard<std::mutex> lock(g_dynamic_serialization_library_mutex);
    g_dynamic_serialization_library_handles.push_back(handle);
  }
  return RMW_RET_OK;
}

std::uint64_t rmw_fleetqox_cpp_publisher_allocations_initialized()
{
  return g_publisher_allocations_initialized.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_publisher_allocations_finalized()
{
  return g_publisher_allocations_finalized.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_subscription_allocations_initialized()
{
  return g_subscription_allocations_initialized.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_subscription_allocations_finalized()
{
  return g_subscription_allocations_finalized.load(std::memory_order_relaxed);
}

size_t rmw_fleetqox_cpp_publisher_allocation_payload_capacity(
  const rmw_publisher_allocation_t * allocation)
{
  if (allocation == nullptr || !identifier_matches(allocation->implementation_identifier)) {
    return 0;
  }
  const auto * data =
    static_cast<const rmw_fleetqox_cpp::MessageAllocationData *>(allocation->data);
  return data == nullptr || data->magic != rmw_fleetqox_cpp::kMessageAllocationMagic ||
         data->kind != rmw_fleetqox_cpp::MessageAllocationKind::Publisher ?
         0 : data->payload.capacity();
}

size_t rmw_fleetqox_cpp_subscription_allocation_payload_capacity(
  const rmw_subscription_allocation_t * allocation)
{
  if (allocation == nullptr || !identifier_matches(allocation->implementation_identifier)) {
    return 0;
  }
  const auto * data =
    static_cast<const rmw_fleetqox_cpp::MessageAllocationData *>(allocation->data);
  return data == nullptr || data->magic != rmw_fleetqox_cpp::kMessageAllocationMagic ||
         data->kind != rmw_fleetqox_cpp::MessageAllocationKind::Subscription ?
         0 : data->payload.capacity();
}

std::uint64_t rmw_fleetqox_cpp_publisher_allocation_uses(
  const rmw_publisher_allocation_t * allocation)
{
  if (allocation == nullptr || !identifier_matches(allocation->implementation_identifier)) {
    return 0;
  }
  const auto * data =
    static_cast<const rmw_fleetqox_cpp::MessageAllocationData *>(allocation->data);
  return data == nullptr || data->magic != rmw_fleetqox_cpp::kMessageAllocationMagic ||
         data->kind != rmw_fleetqox_cpp::MessageAllocationKind::Publisher ?
         0 : data->uses.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_subscription_allocation_uses(
  const rmw_subscription_allocation_t * allocation)
{
  if (allocation == nullptr || !identifier_matches(allocation->implementation_identifier)) {
    return 0;
  }
  const auto * data =
    static_cast<const rmw_fleetqox_cpp::MessageAllocationData *>(allocation->data);
  return data == nullptr || data->magic != rmw_fleetqox_cpp::kMessageAllocationMagic ||
         data->kind != rmw_fleetqox_cpp::MessageAllocationKind::Subscription ?
         0 : data->uses.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_publisher_allocation_capacity_growths(
  const rmw_publisher_allocation_t * allocation)
{
  if (allocation == nullptr || !identifier_matches(allocation->implementation_identifier)) {
    return 0;
  }
  const auto * data =
    static_cast<const rmw_fleetqox_cpp::MessageAllocationData *>(allocation->data);
  return data == nullptr || data->magic != rmw_fleetqox_cpp::kMessageAllocationMagic ||
         data->kind != rmw_fleetqox_cpp::MessageAllocationKind::Publisher ?
         0 : data->capacity_growths.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_subscription_allocation_capacity_growths(
  const rmw_subscription_allocation_t * allocation)
{
  if (allocation == nullptr || !identifier_matches(allocation->implementation_identifier)) {
    return 0;
  }
  const auto * data =
    static_cast<const rmw_fleetqox_cpp::MessageAllocationData *>(allocation->data);
  return data == nullptr || data->magic != rmw_fleetqox_cpp::kMessageAllocationMagic ||
         data->kind != rmw_fleetqox_cpp::MessageAllocationKind::Subscription ?
         0 : data->capacity_growths.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_qos_events_initialized()
{
  return g_qos_events_initialized.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_qos_events_finalized()
{
  return g_qos_events_finalized.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_qos_event_callbacks_set()
{
  return g_qos_event_callbacks_set.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_service_request_publish_allowed()
{
  return g_sros2_service_request_publish_allowed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_service_request_publish_denied()
{
  return g_sros2_service_request_publish_denied.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_service_request_subscribe_allowed()
{
  return g_sros2_service_request_subscribe_allowed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_service_request_subscribe_denied()
{
  return g_sros2_service_request_subscribe_denied.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_service_response_publish_allowed()
{
  return g_sros2_service_response_publish_allowed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_service_response_publish_denied()
{
  return g_sros2_service_response_publish_denied.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_service_response_subscribe_allowed()
{
  return g_sros2_service_response_subscribe_allowed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_service_response_subscribe_denied()
{
  return g_sros2_service_response_subscribe_denied.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_sros2_service_authorization_parse_errors()
{
  return g_sros2_service_authorization_parse_errors.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_repairs_scheduled()
{
  return g_service_request_repairs_scheduled.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_retries_sent()
{
  return g_service_request_retries_sent.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_repairs_cancelled()
{
  return g_service_request_repairs_cancelled.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_repairs_exhausted()
{
  return g_service_request_repairs_exhausted.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_repair_global_admission_rejections()
{
  return g_service_request_repair_global_admission_rejections.load(
    std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_repair_client_admission_rejections()
{
  return g_service_request_repair_client_admission_rejections.load(
    std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_repair_pending_max_observed()
{
  return g_service_request_repair_pending_max_observed.load(
    std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_queue_resource_drops()
{
  return g_service_request_queue_resource_drops.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_response_queue_resource_drops()
{
  return g_service_response_queue_resource_drops.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_per_client_resource_drops()
{
  return g_service_request_per_client_resource_drops.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_pending_response_backpressure()
{
  return g_service_pending_response_backpressure.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_dedupe_evictions()
{
  return g_service_request_dedupe_evictions.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_response_dedupe_evictions()
{
  return g_service_response_dedupe_evictions.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_response_replay_evictions()
{
  return g_service_response_replay_evictions.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_queue_max_observed()
{
  return g_service_request_queue_max_observed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_response_queue_max_observed()
{
  return g_service_response_queue_max_observed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_request_per_client_max_observed()
{
  return g_service_request_per_client_max_observed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_pending_response_max_observed()
{
  return g_service_pending_response_max_observed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_response_replay_max_observed()
{
  return g_service_response_replay_max_observed.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_priority_dequeues()
{
  return g_service_priority_dequeues.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_aged_priority_dequeues()
{
  return g_service_aged_priority_dequeues.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_weighted_dequeues()
{
  return g_service_weighted_dequeues.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_deadline_dequeues()
{
  return g_service_deadline_dequeues.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_deadline_aged_dequeues()
{
  return g_service_deadline_aged_dequeues.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_durable_replays_loaded()
{
  return g_service_durable_replays_loaded.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_durable_replays_persisted()
{
  return g_service_durable_replays_persisted.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_durable_replays_sent()
{
  return g_service_durable_replays_sent.load(std::memory_order_relaxed);
}

std::uint64_t rmw_fleetqox_cpp_service_durable_replay_failures()
{
  return g_service_durable_replay_failures.load(std::memory_order_relaxed);
}

}  // extern "C"
