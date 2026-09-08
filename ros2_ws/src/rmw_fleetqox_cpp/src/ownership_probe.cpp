// Proves the FleetQoX OWNERSHIP extension (see qos_extensions.hpp and
// rclcpp_qos_extensions.hpp) through the real, documented rclcpp
// application-facing path, over a real three-container UDP wire.
//
// Arbitrated at TOPIC granularity (not per DDS-keyed-instance: ROS 2
// message types have no key fields exposed to rmw the way native DDS IDL
// does, so there is no instance identifier below the topic itself). An
// EXCLUSIVE-ownership subscription must deliver only from the highest-
// strength publisher seen so far: a low-strength publisher publishing
// FIRST must still lose out once a higher-strength publisher appears, and
// once that higher-strength publisher disappears (destroyed), the
// remaining lower-strength one must take back over -- proving both the
// strength arbitration and the takeover-on-disappearance behavior, not
// just "first writer wins" (which a bug could produce for the wrong
// reason).
#include <chrono>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <thread>

#include "rclcpp/rclcpp.hpp"
#include "rmw_fleetqox_cpp/rclcpp_qos_extensions.hpp"
#include "std_msgs/msg/string.hpp"

using namespace std::chrono_literals;

namespace
{

constexpr const char * kTopic = "/fleetqox/ownership/topic";
constexpr const char * kWeakPayload = "weak-publisher-payload";
constexpr const char * kStrongPayload = "strong-publisher-payload";

struct Config
{
  std::string mode{"observer"};
};

Config parse_args(int argc, char ** argv)
{
  Config config;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--mode" && i + 1 < argc) {
      config.mode = argv[++i];
    }
  }
  return config;
}

rclcpp::NodeOptions no_incidental_traffic_options()
{
  return rclcpp::NodeOptions().enable_rosout(false).start_parameter_services(false)
    .start_parameter_event_publisher(false);
}

rclcpp::PublisherOptions publisher_options_with_strength(std::int32_t strength)
{
  rmw_fleetqox_cpp::FleetQoxExtendedQosPayload qos_ext;
  qos_ext.ownership_kind = rmw_fleetqox_cpp::OwnershipKind::kExclusive;
  qos_ext.ownership_strength = strength;
  auto options = rclcpp::PublisherOptions();
  options.rmw_implementation_payload =
    std::make_shared<rmw_fleetqox_cpp::ExtendedPublisherPayload>(qos_ext);
  return options;
}

// Weak (strength 1) publishes for the whole run. Strong (strength 10)
// only publishes in the middle third, then this process exits (destroying
// its publisher), so the observer sees: weak-only -> strong-only ->
// weak-only again (takeover, then takeover reversed on disappearance).
int run_weak_publisher()
{
  auto node = std::make_shared<rclcpp::Node>(
    "ownership_weak_publisher", no_incidental_traffic_options());
  auto publisher = node->create_publisher<std_msgs::msg::String>(
    kTopic, rclcpp::QoS(4), publisher_options_with_strength(1));
  std::cout << "{\"schema_version\":\"fleetrmw.ownership_probe.v1\","
            << "\"mode\":\"weak_publisher\",\"phase\":\"ready\","
            << "\"created\":" << (publisher != nullptr ? "true" : "false") << "}" << std::endl;
  const auto deadline = std::chrono::steady_clock::now() + 9s;
  while (rclcpp::ok() && std::chrono::steady_clock::now() < deadline) {
    std_msgs::msg::String message;
    message.data = kWeakPayload;
    publisher->publish(message);
    std::this_thread::sleep_for(150ms);
  }
  std::cout << "{\"schema_version\":\"fleetrmw.ownership_probe.v1\","
            << "\"mode\":\"weak_publisher\",\"status\":\"ok\"}" << std::endl;
  return 0;
}

// Strong (strength 10) publishes for a bounded middle window, then this
// whole process exits -- the observer must see the weak publisher regain
// delivery afterward, not stay silently stuck on the now-gone owner.
int run_strong_publisher()
{
  auto node = std::make_shared<rclcpp::Node>(
    "ownership_strong_publisher", no_incidental_traffic_options());
  auto publisher = node->create_publisher<std_msgs::msg::String>(
    kTopic, rclcpp::QoS(4), publisher_options_with_strength(10));
  std::cout << "{\"schema_version\":\"fleetrmw.ownership_probe.v1\","
            << "\"mode\":\"strong_publisher\",\"phase\":\"ready\","
            << "\"created\":" << (publisher != nullptr ? "true" : "false") << "}" << std::endl;
  // Let the weak publisher establish itself as the (only) owner first.
  std::this_thread::sleep_for(2s);
  const auto deadline = std::chrono::steady_clock::now() + 3s;
  while (rclcpp::ok() && std::chrono::steady_clock::now() < deadline) {
    std_msgs::msg::String message;
    message.data = kStrongPayload;
    publisher->publish(message);
    std::this_thread::sleep_for(150ms);
  }
  std::cout << "{\"schema_version\":\"fleetrmw.ownership_probe.v1\","
            << "\"mode\":\"strong_publisher\",\"status\":\"ok\"}" << std::endl;
  return 0;
}

int run_observer()
{
  auto node = std::make_shared<rclcpp::Node>(
    "ownership_observer", no_incidental_traffic_options());

  bool saw_weak_before = false;
  bool saw_strong = false;
  bool saw_only_strong_while_both_active = true;
  bool saw_weak_after_strong_gone = false;
  int strong_seen_count = 0;
  int weak_after_strong_count = 0;

  rmw_fleetqox_cpp::FleetQoxExtendedQosPayload qos_ext;
  qos_ext.ownership_kind = rmw_fleetqox_cpp::OwnershipKind::kExclusive;
  auto options = rclcpp::SubscriptionOptions();
  options.rmw_implementation_payload =
    std::make_shared<rmw_fleetqox_cpp::ExtendedSubscriptionPayload>(qos_ext);
  auto subscription = node->create_subscription<std_msgs::msg::String>(
    kTopic, rclcpp::QoS(4),
    [&](std_msgs::msg::String::ConstSharedPtr message) {
      if (message->data == kWeakPayload) {
        if (strong_seen_count == 0) {
          saw_weak_before = true;
        } else {
          saw_weak_after_strong_gone = true;
          ++weak_after_strong_count;
        }
      } else if (message->data == kStrongPayload) {
        saw_strong = true;
        ++strong_seen_count;
      } else {
        saw_only_strong_while_both_active = false;
      }
    },
    options);

  std::cout << "{\"schema_version\":\"fleetrmw.ownership_probe.v1\","
            << "\"mode\":\"observer\",\"phase\":\"ready\",\"initialized\":"
            << (subscription != nullptr ? "true" : "false") << "}" << std::endl;

  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(node);
  const auto deadline = std::chrono::steady_clock::now() + 10s;
  while (rclcpp::ok() && std::chrono::steady_clock::now() < deadline) {
    executor.spin_once(50ms);
  }

  const bool ok = saw_weak_before && saw_strong && saw_only_strong_while_both_active &&
    saw_weak_after_strong_gone && weak_after_strong_count > 0;
  std::cout << "{\"schema_version\":\"fleetrmw.ownership_probe.v1\","
            << "\"mode\":\"observer\",\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"saw_weak_before_claim\":" << (saw_weak_before ? "true" : "false") << ","
            << "\"saw_strong_takeover_claim\":" << (saw_strong ? "true" : "false") << ","
            << "\"exclusive_arbitration_claim\":"
            << (saw_only_strong_while_both_active ? "true" : "false") << ","
            << "\"reclaimed_after_disappearance_claim\":"
            << (saw_weak_after_strong_gone ? "true" : "false") << ","
            << "\"weak_after_strong_count\":" << weak_after_strong_count << "}" << std::endl;

  executor.remove_node(node);
  return ok ? 0 : 1;
}

}  // namespace

int main(int argc, char ** argv)
{
  const Config config = parse_args(argc, argv);
  int ros_argc = 1;
  rclcpp::init(ros_argc, argv);
  int result = 2;
  if (config.mode == "weak_publisher") {
    result = run_weak_publisher();
  } else if (config.mode == "strong_publisher") {
    result = run_strong_publisher();
  } else if (config.mode == "observer") {
    result = run_observer();
  }
  rclcpp::shutdown();
  return result;
}
