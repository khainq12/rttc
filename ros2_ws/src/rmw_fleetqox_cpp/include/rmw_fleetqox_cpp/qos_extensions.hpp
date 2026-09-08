// FleetQoX-specific QoS extensions: OWNERSHIP, PARTITION, DESTINATION_ORDER,
// and PRESENTATION. These four DDS QoS policies have no representation in
// upstream rmw: `rmw_qos_profile_t` (rmw/types.h) has no fields for them,
// and `rmw_qos_policy_kind_t` (rmw/qos_policy_kind.h) has no enum values to
// even report an incompatibility involving them. That is a real, permanent
// limit of the standard, portable rmw_qos_profile_t path -- it cannot be
// closed without forking rmw itself.
//
// This header instead uses an extension point rmw itself already provides
// for exactly this situation: `rmw_publisher_options_t::
// rmw_specific_publisher_payload` and `rmw_subscription_options_t::
// rmw_specific_subscription_payload` (both `void *`, both already present
// upstream, both previously unused by rmw_fleetqox_cpp). rclcpp exposes a
// supported mechanism for application code to populate these fields --
// `rclcpp::PublisherOptions::rmw_implementation_payload` /
// `rclcpp::SubscriptionOptions::rmw_implementation_payload`, both
// `std::shared_ptr<rclcpp::detail::RMWImplementationSpecific...Payload>` --
// see rclcpp_qos_extensions.hpp for the subclasses that wire this struct
// through that mechanism.
//
// This is a genuine, additional capability specific to rmw_fleetqox_cpp,
// not a portable ROS 2 QoS: an application must deliberately construct one
// of the rclcpp payload subclasses (or set the raw rmw field directly) to
// use it, and it has no effect at all when a different rmw implementation
// is loaded. It does not, and cannot, make
// `full_non_deadline_qos_event_production_claim` true -- that claim is
// specifically about the standard, portable rmw_qos_profile_t path, which
// remains permanently bounded by the upstream struct/enum shown above.
#ifndef RMW_FLEETQOX_CPP__QOS_EXTENSIONS_HPP_
#define RMW_FLEETQOX_CPP__QOS_EXTENSIONS_HPP_

#include <cstdint>
#include <string>
#include <vector>

namespace rmw_fleetqox_cpp
{

// Bumped whenever the ABI of FleetQoxExtendedQosPayload changes. Read and
// checked by rmw_create_publisher/rmw_create_subscription before trusting
// the rest of the struct -- rmw_specific_publisher_payload/
// rmw_specific_subscription_payload are raw `void *` with no type
// information at the C API level, so this is the only defense against
// misinterpreting an unrelated non-null pointer as this struct.
constexpr std::uint32_t kFleetQoxExtendedQosPayloadMagic = 0x464c5158u;  // "FLQX"
constexpr std::uint32_t kFleetQoxExtendedQosPayloadVersion = 1u;

enum class OwnershipKind : std::uint8_t
{
  kShared = 0,
  kExclusive = 1,
};

// DDS BY_RECEPTION_TIMESTAMP / BY_SOURCE_TIMESTAMP. Subscription-side only:
// it governs the order a subscriber's take()/callback observes frames in,
// independent of what any publisher on the topic requests.
enum class DestinationOrderKind : std::uint8_t
{
  kByReceptionTimestamp = 0,
  kBySourceTimestamp = 1,
};

// DDS PRESENTATION access_scope. GROUP here means "coherent/ordered across
// every publisher/subscription sharing the same presentation_group_id",
// the closest honest match to real DDS GROUP scope that fits rmw's
// per-topic (not per-Publisher-entity) model -- see
// presentation_group.hpp for the begin/end-coherent-changes API this
// enables.
enum class PresentationAccessScope : std::uint8_t
{
  kInstance = 0,
  kTopic = 1,
  kGroup = 2,
};

// Shared by both publisher and subscription creation; fields that do not
// apply to one side (e.g. ownership_strength for a subscription) are
// simply ignored by that side.
struct FleetQoxExtendedQosPayload
{
  std::uint32_t magic{kFleetQoxExtendedQosPayloadMagic};
  std::uint32_t version{kFleetQoxExtendedQosPayloadVersion};

  // OWNERSHIP. Arbitrated at TOPIC granularity, not per-instance/key: ROS 2
  // message types have no key fields exposed to rmw the way native DDS IDL
  // does, so there is no natural instance identifier to arbitrate on below
  // the topic itself. This is a documented, deliberate scope boundary, not
  // an oversight.
  OwnershipKind ownership_kind{OwnershipKind::kShared};
  std::int32_t ownership_strength{0};

  // PARTITION. Publisher/subscription with disjoint (non-intersecting)
  // partition sets never match, exactly like DDS. An empty list means
  // "default partition", which is its own singleton partition for matching
  // purposes (matches only other endpoints that are also unpartitioned),
  // matching DDS's own default-partition semantics.
  std::vector<std::string> partitions;

  // DESTINATION_ORDER.
  DestinationOrderKind destination_order{DestinationOrderKind::kByReceptionTimestamp};

  // PRESENTATION.
  PresentationAccessScope presentation_access_scope{PresentationAccessScope::kTopic};
  bool presentation_coherent_access{false};
  bool presentation_ordered_access{false};
  // Only meaningful when presentation_access_scope == kGroup: publishers
  // and subscriptions sharing this id are grouped for coherent/ordered
  // delivery across topics. Empty means "not grouped".
  std::string presentation_group_id;
};

}  // namespace rmw_fleetqox_cpp

#endif  // RMW_FLEETQOX_CPP__QOS_EXTENSIONS_HPP_
