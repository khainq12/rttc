#pragma once

#include <cstdint>
#include <functional>
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
  // Added for the O(N^2) discovery-traffic reduction (ChatGPT-assisted B+
  // redesign, 11/09/2026, see docs/AUDIT_ACCEPTANCE_TRACKING.md).
  // incarnation_id is a random value generated once per process boot
  // (changes on restart -- lets a future receiver tell "peer restarted"
  // apart from "normal churn"). graph_version is a monotonic per-process
  // counter incremented on every real add/remove event, so a lightweight
  // periodic heartbeat carrying just these two fields (entity_kind=="node",
  // action=="heartbeat") can substitute for resending the full endpoint
  // list on every tick -- a receiver noticing its last-seen graph_version
  // for a peer is behind the heartbeat's could trigger an on-demand
  // resync (not yet implemented; this pass only stops the O(N^2) full
  // periodic resend and adds the fields needed for that follow-up).
  // Default-initialized here (not touching the many existing positional
  // aggregate-initializations of this struct throughout rmw_pubsub.cpp,
  // which simply pick up these defaults for the two new trailing members).
  std::uint64_t incarnation_id = 0;
  std::uint64_t graph_version = 0;
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

// Same encoding as encode_data_frame(frame, base64_scratch), but appends
// directly into caller-owned `json_scratch` instead of returning a freshly
// allocated std::string. Pass persistent per-publisher buffers for both
// (e.g. FleetQoxPublisherData::frame_base64_scratch/frame_json_scratch) to
// avoid any heap allocation on the hot publish path once both buffers have
// grown to the steady-state frame size.
void encode_data_frame_append(
  const DataFrame & frame, std::string & base64_scratch, std::string & json_scratch);

std::optional<DataFrame> decode_data_frame(const std::string & payload);

// Opt-in compact binary data-frame wire format (see
// docs/AUDIT_ACCEPTANCE_TRACKING.md "compact data-frame encoding"):
// encode_data_frame_append's JSON+base64 format measured at ~6x wire-size
// amplification for a small (96-byte) sample -- duplicated robot_id/topic
// fields (once under "route", once under "sample_envelope"), JSON
// punctuation/field-name text, and base64's +33% payload expansion all
// contribute. This format keeps the exact same semantic DataFrame fields
// (so routing/QoS/retry/subscription-matching logic downstream is
// unaffected) but as a flat length-prefixed binary layout with the raw
// payload appended directly, no base64. Distinct magic
// (kDataFrameCompactV1Magic, not kDataFrameMagic) so decode_data_frame()
// can dispatch to whichever format a given datagram is actually in
// without needing every one of decode_data_frame's ~15 call sites
// touched -- the JSON path remains byte-for-byte unchanged as the
// default; this is purely additive.
constexpr const char * kDataFrameCompactV1Magic = "FRMWC1\n";

void encode_data_frame_compact_v1_append(const DataFrame & frame, std::string & out);

std::optional<DataFrame> decode_data_frame_compact_v1(const std::string & payload);

// Opt-in static-mode-only minimal data-frame wire format (see
// docs/AUDIT_ACCEPTANCE_TRACKING.md "wire-frame tối giản cho static mode"):
// a pcap capture during this investigation showed FleetRMW's actual
// on-wire packets running 5.9-7.7x bigger than raw UDP's true minimum for
// the same logical payload, dwarfing the ~2x gap the JSON-vs-compact_v1
// comparison covered -- compact_v1 still carries every DataFrame field
// (robot_id, topic, publisher_id, type_name, flow_class, 4 QoS-extension
// doubles, partitions_csv, ownership_strength, coherent_set_* -- all
// length-prefixed strings even when empty) verbatim. This format instead
// carries ONLY what a receiver can't already know: a topic identity, a
// publisher identity, a sequence number, a timestamp, and the raw
// payload -- everything else (topic/type_name strings, robot_id,
// QoS-extension fields, partitions, ownership, coherent-set fields) is
// either reconstructed via the hash resolver below or left at its struct
// default, which is safe ONLY because those extension fields are already
// always their zero/empty default in any frame this harness produces
// (verified by reading publish_payload()'s DataFrame construction --
// flow_class/partitions_csv/coherent_set_id are always {}, the 4 QoS
// doubles and ownership_strength are always 0). This format is NOT a
// general-purpose replacement for compact_v1 -- it is meaningless without
// FLEETQOX_RMW_STATIC_MODE, whose whole premise (every peer already knows
// the full (topic, subscriber) map ahead of time) is what makes omitting
// the topic string from the wire safe to do at all.
constexpr const char * kDataFrameStaticMinV1Magic = "FRMWM1\n";

// A topic/type/domain identity a process already knows locally (as either
// a local subscription's own registration, or a publisher's own static
// routing table entry) is looked up by a 32-bit hash of
// "<domain_id>|<topic>|<type_name>" (the same string LoopbackSocketTransport
// already uses as subscription_topic_key -- computed there, not here, to
// keep this file's fnv1a64-free and dependency-free as before). Returns
// false if the hash isn't recognized (unlike JSON/compact_v1, this format
// can genuinely fail to decode on a process that doesn't already know the
// topic -- there is no string to fall back to).
using StaticMinV1TopicResolver = std::function<
  bool (std::uint32_t topic_key_hash, std::uint64_t & domain_id,
    std::string & topic, std::string & type_name)>;

// Registers the process-wide resolver decode_data_frame() calls internally
// when it recognizes kDataFrameStaticMinV1Magic -- set once at transport
// startup (see LoopbackSocketTransport::start()) rather than threaded
// through all ~15 decode_data_frame() call sites, so every existing call
// site gets static_min_v1 support for free. Passing an empty
// std::function (the default before this is called, and always the case
// in the standalone unit test binary that doesn't link rmw_pubsub.cpp)
// makes decode_data_frame() treat this format as always-undecodable,
// which is the correct safe default.
void set_static_min_v1_topic_resolver(StaticMinV1TopicResolver resolver);

void encode_data_frame_static_min_v1_append(
  std::uint32_t topic_key_hash,
  std::uint32_t publisher_hash,
  std::uint32_t sequence32,
  std::int64_t source_timestamp_ns,
  const std::vector<std::uint8_t> & payload,
  std::string & out);

// Only usable after set_static_min_v1_topic_resolver() has been called
// with a resolver that recognizes this frame's topic_key_hash -- returns
// std::nullopt otherwise (same failure signature as an unparseable
// JSON/compact_v1 payload, so every existing decode_data_frame() call
// site's std::optional-checking code already handles this correctly).
// robot_id/publisher_id are reconstructed as "h<8 hex digits of
// publisher_hash>" rather than the original strings -- they are only ever
// used downstream for opaque per-publisher stream-key uniqueness (gap
// tracking) and ownership arbitration, never compared against a specific
// expected literal, so a stable synthetic identifier is semantically
// equivalent for every consumer of a decoded DataFrame.
std::optional<DataFrame> decode_data_frame_static_min_v1(const std::string & payload);

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
