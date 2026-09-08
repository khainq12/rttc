// Proves the FleetQoX PARTITION extension (see qos_extensions.hpp and
// rclcpp_qos_extensions.hpp) through the real, documented rclcpp
// application-facing path, over a real two-container UDP wire.
//
// A publisher in partition "fleet_a" and a subscription in partition
// "fleet_b" on the SAME topic must NOT match at all -- no message
// delivered, exactly as if they were on different topics, not an
// "incompatible QoS" condition. A second publisher/subscription pair in
// the SAME partition ("fleet_a") on a different topic must match and
// deliver normally, proving partitioning does not just globally break
// everything. A third pair proves the DDS "default partition" convention:
// an explicit empty-string partition matches an unpartitioned (empty
// list) endpoint, since an empty list is itself the default partition.
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

constexpr const char * kMismatchTopic = "/fleetqox/partition/mismatch_topic";
constexpr const char * kMatchTopic = "/fleetqox/partition/match_topic";
constexpr const char * kDefaultPartitionTopic = "/fleetqox/partition/default_topic";

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

rclcpp::PublisherOptions publisher_options_with_partitions(
  const std::vector<std::string> & partitions)
{
  rmw_fleetqox_cpp::FleetQoxExtendedQosPayload qos_ext;
  qos_ext.partitions = partitions;
  auto options = rclcpp::PublisherOptions();
  options.rmw_implementation_payload =
    std::make_shared<rmw_fleetqox_cpp::ExtendedPublisherPayload>(qos_ext);
  return options;
}

rclcpp::SubscriptionOptions subscription_options_with_partitions(
  const std::vector<std::string> & partitions)
{
  rmw_fleetqox_cpp::FleetQoxExtendedQosPayload qos_ext;
  qos_ext.partitions = partitions;
  auto options = rclcpp::SubscriptionOptions();
  options.rmw_implementation_payload =
    std::make_shared<rmw_fleetqox_cpp::ExtendedSubscriptionPayload>(qos_ext);
  return options;
}

int run_advertiser()
{
  auto node = std::make_shared<rclcpp::Node>(
    "partition_probe_advertiser", no_incidental_traffic_options());
  // fleet_a: must NOT reach the observer's fleet_b subscription below.
  auto mismatch_publisher = node->create_publisher<std_msgs::msg::String>(
    kMismatchTopic, rclcpp::QoS(4), publisher_options_with_partitions({"fleet_a"}));
  // fleet_a on a different topic: must reach the observer's fleet_a
  // subscription on that same topic.
  auto match_publisher = node->create_publisher<std_msgs::msg::String>(
    kMatchTopic, rclcpp::QoS(4), publisher_options_with_partitions({"fleet_a"}));
  // Explicit "" (default) partition: must reach the observer's
  // unpartitioned (empty list) subscription below.
  auto default_publisher = node->create_publisher<std_msgs::msg::String>(
    kDefaultPartitionTopic, rclcpp::QoS(4), publisher_options_with_partitions({""}));

  std::cout << "{\"schema_version\":\"fleetrmw.partition_probe.v1\","
            << "\"mode\":\"advertiser\",\"phase\":\"ready\",\"created\":"
            << (mismatch_publisher && match_publisher && default_publisher ? "true" : "false")
            << "}" << std::endl;

  const auto deadline = std::chrono::steady_clock::now() + 6s;
  while (rclcpp::ok() && std::chrono::steady_clock::now() < deadline) {
    std_msgs::msg::String message;
    message.data = "mismatch-payload";
    mismatch_publisher->publish(message);
    message.data = "match-payload";
    match_publisher->publish(message);
    message.data = "default-payload";
    default_publisher->publish(message);
    std::this_thread::sleep_for(200ms);
  }

  std::cout << "{\"schema_version\":\"fleetrmw.partition_probe.v1\","
            << "\"mode\":\"advertiser\",\"status\":\"ok\"}" << std::endl;
  return 0;
}

int run_observer()
{
  auto node = std::make_shared<rclcpp::Node>(
    "partition_probe_observer", no_incidental_traffic_options());

  int mismatch_received = 0;
  int match_received = 0;
  int default_received = 0;

  // fleet_b: deliberately disjoint from the advertiser's fleet_a on this
  // topic -- must receive nothing.
  auto mismatch_subscription = node->create_subscription<std_msgs::msg::String>(
    kMismatchTopic, rclcpp::QoS(4),
    [&](std_msgs::msg::String::ConstSharedPtr) { ++mismatch_received; },
    subscription_options_with_partitions({"fleet_b"}));
  // fleet_a: matches the advertiser's fleet_a on this (different) topic.
  auto match_subscription = node->create_subscription<std_msgs::msg::String>(
    kMatchTopic, rclcpp::QoS(4),
    [&](std_msgs::msg::String::ConstSharedPtr) { ++match_received; },
    subscription_options_with_partitions({"fleet_a"}));
  // Unpartitioned (empty list): matches the advertiser's explicit ""
  // (default) partition, per DDS's own default-partition convention.
  auto default_subscription = node->create_subscription<std_msgs::msg::String>(
    kDefaultPartitionTopic, rclcpp::QoS(4),
    [&](std_msgs::msg::String::ConstSharedPtr) { ++default_received; },
    subscription_options_with_partitions({}));

  std::cout << "{\"schema_version\":\"fleetrmw.partition_probe.v1\","
            << "\"mode\":\"observer\",\"phase\":\"ready\",\"initialized\":"
            << (mismatch_subscription && match_subscription && default_subscription ?
      "true" : "false")
            << "}" << std::endl;

  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(node);
  const auto deadline = std::chrono::steady_clock::now() + 8s;
  while (rclcpp::ok() && std::chrono::steady_clock::now() < deadline &&
    (match_received < 2 || default_received < 2))
  {
    executor.spin_once(50ms);
  }
  // Give any (incorrectly) in-flight mismatch-partition traffic a final
  // chance to arrive before declaring it absent.
  const auto grace_deadline = std::chrono::steady_clock::now() + 500ms;
  while (rclcpp::ok() && std::chrono::steady_clock::now() < grace_deadline) {
    executor.spin_once(50ms);
  }

  const bool ok = mismatch_received == 0 && match_received >= 2 && default_received >= 2;
  std::cout << "{\"schema_version\":\"fleetrmw.partition_probe.v1\","
            << "\"mode\":\"observer\",\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"mismatch_received\":" << mismatch_received << ","
            << "\"match_received\":" << match_received << ","
            << "\"default_received\":" << default_received << "}" << std::endl;

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
  if (config.mode == "advertiser") {
    result = run_advertiser();
  } else if (config.mode == "observer") {
    result = run_observer();
  }
  rclcpp::shutdown();
  return result;
}
