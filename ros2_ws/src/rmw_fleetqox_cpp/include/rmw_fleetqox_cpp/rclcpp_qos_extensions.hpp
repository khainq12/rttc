// rclcpp-facing wiring for the FleetQoX QoS extensions declared in
// qos_extensions.hpp. This header depends on rclcpp; qos_extensions.hpp
// itself does not, since rmw_fleetqox_cpp (the RMW) must not depend on
// rclcpp (rclcpp depends on rmw, never the reverse) but application code
// building against rclcpp needs a real, supported way to populate
// rmw_specific_publisher_payload/rmw_specific_subscription_payload.
//
// rclcpp already provides exactly the mechanism this needs:
// `rclcpp::PublisherOptions::rmw_implementation_payload` and
// `rclcpp::SubscriptionOptions::rmw_implementation_payload`, both
// `std::shared_ptr<rclcpp::detail::RMWImplementationSpecific...Payload>`.
// A subclass overrides `get_implementation_identifier()` (checked against
// the actual loaded rmw's identifier at runtime -- this payload has no
// effect at all if a different rmw is loaded) and
// `modify_rmw_publisher_options()`/`modify_rmw_subscription_options()`,
// which rclcpp calls synchronously, just before rcl_publisher_init()/
// rcl_subscription_init(), with a live, mutable reference to the exact
// rmw_publisher_options_t/rmw_subscription_options_t that will reach
// rmw_create_publisher()/rmw_create_subscription().
//
// Usage:
//
//   rmw_fleetqox_cpp::FleetQoxExtendedQosPayload qos_ext;
//   qos_ext.partitions = {"fleet_a"};
//   auto options = rclcpp::PublisherOptions();
//   options.rmw_implementation_payload =
//     std::make_shared<rmw_fleetqox_cpp::ExtendedPublisherPayload>(qos_ext);
//   auto publisher = node->create_publisher<MsgT>(topic, qos, options);
//
// The payload object (and the FleetQoxExtendedQosPayload it owns) must
// stay alive at least until create_publisher()/create_subscription()
// returns -- rmw_fleetqox_cpp reads and copies the fields it needs
// synchronously during that call and never retains the raw pointer past
// it, but rclcpp itself does not guarantee anything beyond that call
// either.
#ifndef RMW_FLEETQOX_CPP__RCLCPP_QOS_EXTENSIONS_HPP_
#define RMW_FLEETQOX_CPP__RCLCPP_QOS_EXTENSIONS_HPP_

#include "rclcpp/detail/rmw_implementation_specific_publisher_payload.hpp"
#include "rclcpp/detail/rmw_implementation_specific_subscription_payload.hpp"
#include "rmw/rmw.h"
#include "rmw_fleetqox_cpp/qos_extensions.hpp"

namespace rmw_fleetqox_cpp
{

class ExtendedPublisherPayload : public rclcpp::detail::RMWImplementationSpecificPublisherPayload
{
public:
  explicit ExtendedPublisherPayload(FleetQoxExtendedQosPayload qos)
  : qos_(std::move(qos))
  {}

  const char * get_implementation_identifier() const override
  {
    return "rmw_fleetqox_cpp";
  }

  void modify_rmw_publisher_options(rmw_publisher_options_t & rmw_publisher_options) const override
  {
    rmw_publisher_options.rmw_specific_publisher_payload =
      const_cast<FleetQoxExtendedQosPayload *>(&qos_);
  }

private:
  FleetQoxExtendedQosPayload qos_;
};

class ExtendedSubscriptionPayload
  : public rclcpp::detail::RMWImplementationSpecificSubscriptionPayload
{
public:
  explicit ExtendedSubscriptionPayload(FleetQoxExtendedQosPayload qos)
  : qos_(std::move(qos))
  {}

  const char * get_implementation_identifier() const override
  {
    return "rmw_fleetqox_cpp";
  }

  void modify_rmw_subscription_options(
    rmw_subscription_options_t & rmw_subscription_options) const override
  {
    rmw_subscription_options.rmw_specific_subscription_payload =
      const_cast<FleetQoxExtendedQosPayload *>(&qos_);
  }

private:
  FleetQoxExtendedQosPayload qos_;
};

}  // namespace rmw_fleetqox_cpp

#endif  // RMW_FLEETQOX_CPP__RCLCPP_QOS_EXTENSIONS_HPP_
