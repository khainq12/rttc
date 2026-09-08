#pragma once

#include <cstdint>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace rmw_fleetqox_cpp
{

constexpr const char * kDataFrameSchemaVersion = "fleetrmw.data_frame.v1";
constexpr const char * kAckNackSchemaVersion = "fleetrmw.ack_nack.v1";
constexpr const char * kUnrecoverableLossNoticeSchemaVersion =
  "fleetrmw.unrecoverable_loss_notice.v1";
constexpr const char * kRouteAdvertisementSchemaVersion = "fleetrmw.route_advertisement.v1";
constexpr const char * kGraphAdvertisementSchemaVersion = "fleetrmw.graph_advertisement.v1";
constexpr const char * kServiceFrameSchemaVersion = "fleetrmw.service_frame.v1";
constexpr const char * kActionFrameSchemaVersion = "fleetrmw.action_frame.v1";
constexpr const char * kDataFrameMagic = "FRMW1\n";

struct DataFrame
{
  DataFrame() = default;

  DataFrame(
    std::string robot_id_value,
    std::string topic_value,
    std::string publisher_id_value,
    std::uint64_t source_sequence_number_value,
    std::int64_t source_timestamp_ns_value,
    std::vector<std::uint8_t> serialized_payload_value,
    std::uint64_t domain_id_value = 0,
    std::string type_name_value = {},
    std::string flow_class_value = {},
    double deadline_ms_value = 0.0,
    double age_ms_value = 0.0,
    double qoe_debt_value = 0.0,
    double task_criticality_value = 0.0,
    bool repair_requested_value = false,
    std::uint64_t prior_repair_attempts_value = 0,
    std::string partitions_csv_value = {},
    std::int32_t ownership_strength_value = 0,
    std::string coherent_set_id_value = {},
    std::uint32_t coherent_set_total_value = 0,
    std::uint32_t coherent_set_index_value = 0)
  : robot_id(std::move(robot_id_value)),
    topic(std::move(topic_value)),
    publisher_id(std::move(publisher_id_value)),
    source_sequence_number(source_sequence_number_value),
    source_timestamp_ns(source_timestamp_ns_value),
    serialized_payload(std::move(serialized_payload_value)),
    domain_id(domain_id_value),
    type_name(std::move(type_name_value)),
    flow_class(std::move(flow_class_value)),
    deadline_ms(deadline_ms_value),
    age_ms(age_ms_value),
    qoe_debt(qoe_debt_value),
    task_criticality(task_criticality_value),
    repair_requested(repair_requested_value),
    prior_repair_attempts(prior_repair_attempts_value),
    partitions_csv(std::move(partitions_csv_value)),
    ownership_strength(ownership_strength_value),
    coherent_set_id(std::move(coherent_set_id_value)),
    coherent_set_total(coherent_set_total_value),
    coherent_set_index(coherent_set_index_value)
  {}

  std::string robot_id;
  std::string topic;
  std::string publisher_id;
  std::uint64_t source_sequence_number = 0;
  std::int64_t source_timestamp_ns = 0;
  std::vector<std::uint8_t> serialized_payload;
  std::uint64_t domain_id = 0;
  std::string type_name;
  std::string flow_class;
  double deadline_ms = 0.0;
  double age_ms = 0.0;
  double qoe_debt = 0.0;
  double task_criticality = 0.0;
  bool repair_requested = false;
  std::uint64_t prior_repair_attempts = 0;
  // FleetQoX PARTITION extension (see qos_extensions.hpp), comma-joined,
  // mirroring GraphAdvertisement::partitions_csv. Carried per-frame (like
  // type_name above) because the actual delivery gate in
  // enqueue_received_frame() only has the frame itself to check against a
  // local subscription's own configured partitions -- it does not consult
  // the separate remote-endpoint registry that GraphAdvertisement feeds.
  std::string partitions_csv;
  // FleetQoX OWNERSHIP extension (see qos_extensions.hpp): the publishing
  // side's declared strength, used by an EXCLUSIVE-ownership subscription
  // to arbitrate among multiple publishers on the same topic. Meaningless
  // (and ignored) for a SHARED-ownership subscription, the default.
  std::int32_t ownership_strength = 0;
  // FleetQoX PRESENTATION extension (see qos_extensions.hpp): non-empty
  // when this frame is one member of a GROUP-scope coherent set flushed by
  // rmw_fleetqox_cpp_end_coherent_changes(). All frames sharing one
  // coherent_set_id (which may span multiple topics, since PRESENTATION at
  // GROUP scope spans an entire Publisher, not one DataWriter) must be
  // buffered by a coherent_access subscriber and released to
  // rmw_take-visible state together, only once coherent_set_total of them
  // have arrived; coherent_set_index gives their original publish order
  // for a coherent+ordered_access release.
  std::string coherent_set_id;
  std::uint32_t coherent_set_total = 0;
  std::uint32_t coherent_set_index = 0;
};

struct TimedMissingSequenceRange
{
  std::uint64_t first = 0;
  std::uint64_t last = 0;
  std::int64_t first_observed_ns = 0;
};

struct SequenceState
{
  bool initialized = false;
  bool reception_sequence_baseline_initialized = false;
  std::uint64_t cumulative_ack_floor = 0;
  std::uint64_t highest_contiguous_sequence = 0;
  std::uint64_t highest_observed_sequence = 0;
  std::int64_t last_repair_request_ns = 0;
  std::set<std::uint64_t> observed_sequences;
  std::vector<TimedMissingSequenceRange> pending_missing_ranges;
  std::vector<std::pair<std::uint64_t, std::uint64_t>> confirmed_lost_ranges;
};

struct AckNackFeedback
{
  std::vector<std::pair<std::uint64_t, std::uint64_t>> missing_sequence_ranges;
  std::uint64_t lowest_observed_sequence = 0;
  std::uint64_t highest_contiguous_sequence = 0;
  std::uint64_t highest_observed_sequence = 0;
  bool duplicate = false;
  bool out_of_order = false;
};

struct AckNackFrame
{
  std::string robot_id;
  std::string topic;
  std::string publisher_id;
  std::string subscriber_id;
  std::uint64_t ack_sequence_number = 0;
  std::int64_t source_timestamp_ns = 0;
  std::vector<std::pair<std::uint64_t, std::uint64_t>> missing_sequence_ranges;
  std::uint64_t lowest_observed_sequence = 0;
  std::uint64_t highest_contiguous_sequence = 0;
  std::uint64_t highest_observed_sequence = 0;
  bool duplicate = false;
  bool out_of_order = false;
  std::uint64_t domain_id = 0;
};

struct UnrecoverableLossNotice
{
  std::string robot_id;
  std::string topic;
  std::string publisher_id;
  std::string subscriber_id;
  std::int64_t source_timestamp_ns = 0;
  std::vector<std::pair<std::uint64_t, std::uint64_t>> lost_sequence_ranges;
  std::uint64_t domain_id = 0;
};

struct RouteAdvertisement
{
  std::string endpoint_id;
  std::string role;
  std::string topic;
  std::string type_name;
  std::uint64_t lease_ms = 0;
  std::uint64_t domain_id = 0;
};

struct GraphQosProfile
{
  std::uint64_t history = 0;
  std::uint64_t depth = 0;
  std::uint64_t reliability = 0;
  std::uint64_t durability = 0;
  std::uint64_t deadline_sec = 0;
  std::uint64_t deadline_nsec = 0;
  std::uint64_t lifespan_sec = 0;
  std::uint64_t lifespan_nsec = 0;
  std::uint64_t liveliness = 0;
  std::uint64_t liveliness_lease_duration_sec = 0;
  std::uint64_t liveliness_lease_duration_nsec = 0;
  std::uint64_t avoid_ros_namespace_conventions = 0;
};

struct GraphAdvertisement
{
  std::string endpoint_id;
  std::string action;
  std::string entity_kind;
  std::string node_name;
  std::string node_namespace;
  std::string topic;
  std::string type_name;
  std::string endpoint_gid;
  GraphQosProfile qos;
  std::uint64_t lease_ms = 0;
  std::uint64_t domain_id = 0;
  // Hex-encoded RIHS rosidl_type_hash_t (1 version byte + 32 hash bytes ==
  // 66 hex chars), empty when the local type support has no
  // get_type_hash_func (e.g. a hand-built probe type support).
  std::string type_hash_hex;
  // FleetQoX PARTITION extension (see qos_extensions.hpp), comma-joined;
  // partition names containing a comma are not supported. Empty means the
  // default partition.
  std::string partitions_csv;
};

struct ServiceFrame
{
  std::string role;
  std::string service_name;
  std::string type_name;
  std::string client_endpoint_id;
  std::string service_endpoint_id;
  std::int64_t sequence_id = 0;
  std::int64_t source_timestamp_ns = 0;
  std::int64_t lifespan_ns = 0;
  std::vector<std::uint8_t> serialized_payload;
  std::uint64_t domain_id = 0;
  std::uint64_t client_priority = 0;
  std::int64_t local_enqueue_timestamp_ns = 0;
  std::uint64_t client_weight = 1;
  std::uint64_t request_deadline_ns = 0;
};

struct ActionFrame
{
  std::string role;
  std::string action_name;
  std::string type_name;
  std::string endpoint_id;
  std::string goal_id;
  std::int64_t sequence_id = 0;
  std::int64_t source_timestamp_ns = 0;
  std::int64_t lifespan_ns = 0;
  std::vector<std::uint8_t> serialized_payload;
  std::uint64_t domain_id = 0;
};

std::string stream_key(const DataFrame & frame);

std::string encode_data_frame(const DataFrame & frame);

// Reuses `base64_scratch` for the payload's base64 text instead of
// allocating a fresh string on every call; pass a persistent buffer owned
// by the caller (e.g. one per publisher) to avoid a new heap allocation
// per publish once the buffer has grown to the steady-state payload size.
std::string encode_data_frame(const DataFrame & frame, std::string & base64_scratch);

std::optional<DataFrame> decode_data_frame(const std::string & payload);

std::string encode_route_advertisement(const RouteAdvertisement & advertisement);

std::optional<RouteAdvertisement> decode_route_advertisement(const std::string & payload);

std::string encode_graph_advertisement(const GraphAdvertisement & advertisement);

std::optional<GraphAdvertisement> decode_graph_advertisement(const std::string & payload);

std::string encode_service_frame(const ServiceFrame & frame);

std::optional<ServiceFrame> decode_service_frame(const std::string & payload);

bool service_frame_expired(const ServiceFrame & frame, std::int64_t now_ns);

std::string encode_action_frame(const ActionFrame & frame);

std::optional<ActionFrame> decode_action_frame(const std::string & payload);

bool action_frame_expired(const ActionFrame & frame, std::int64_t now_ns);

AckNackFeedback observe_frame(SequenceState & state, const DataFrame & frame);

AckNackFeedback establish_reception_sequence_baseline(SequenceState & state);

AckNackFeedback feedback_from_sequence_state(const SequenceState & state);

std::string encode_ack_nack(
  const DataFrame & frame,
  const AckNackFeedback & feedback,
  const std::string & subscriber_id = "");

std::optional<AckNackFrame> decode_ack_nack(const std::string & payload);

bool ack_nack_acknowledges_sequence(
  const AckNackFrame & frame,
  std::uint64_t sequence);

std::string encode_unrecoverable_loss_notice(const UnrecoverableLossNotice & notice);

std::optional<UnrecoverableLossNotice> decode_unrecoverable_loss_notice(
  const std::string & payload);

std::vector<std::uint64_t> missing_sequences_from_ack_nack(const std::string & payload);

bool ack_nack_reports_out_of_order(const std::string & payload);

}  // namespace rmw_fleetqox_cpp
