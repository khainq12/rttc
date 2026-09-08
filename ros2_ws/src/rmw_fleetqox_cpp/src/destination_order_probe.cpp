// Proves the FleetQoX DESTINATION_ORDER extension (see qos_extensions.hpp
// and rclcpp_qos_extensions.hpp) through the REAL, documented rclcpp
// application-facing path -- rclcpp::SubscriptionOptions::
// rmw_implementation_payload -- not just the raw rmw C struct, over a
// real three-container UDP wire.
//
// Two publishers, in separate containers with DIFFERENT netem delay,
// publish to the same topic: "slow" (large artificial delay) publishes
// FIRST (so its message has the EARLIER source_timestamp_ns) but arrives
// SECOND; "fast" (minimal delay) publishes SECOND (LATER source_timestamp_
// ns) but arrives FIRST. This constructs a genuine, deterministic
// out-of-arrival-order scenario -- not a hypothetical -- driven by real
// network conditions on real containers, not by manipulating the wire
// protocol directly.
//
// The observer holds two subscriptions to the same topic: one default
// (BY_RECEPTION_TIMESTAMP), one requesting BY_SOURCE_TIMESTAMP via the
// extension. It deliberately does not spin (does not call take) until
// both messages have already arrived and are sitting in each
// subscription's queue, then drains both. The default subscription must
// observe arrival order (fast, then slow); the extended one must observe
// source-timestamp order (slow, then fast) -- proving the reorder-on-
// insert logic actually changes delivery order, not just that it compiles.
#include <chrono>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "rclcpp/rclcpp.hpp"
#include "rmw_fleetqox_cpp/rclcpp_qos_extensions.hpp"
#include "std_msgs/msg/string.hpp"

using namespace std::chrono_literals;

namespace
{

constexpr const char * kTopic = "/fleetqox/destination_order/topic";
constexpr const char * kSlowPayload = "first-published-slow-arrival";
constexpr const char * kFastPayload = "second-published-fast-arrival";

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
  // Neither role in this probe needs /rosout logging or parameter
  // services, and this rmw's transport requires FLEETQOX_RMW_PEERS to
  // already know a destination for anything it sends -- the observer
  // deliberately has no peers configured (it only listens), so leaving
  // these enabled would crash the very first incidental rosout/parameter-
  // event publish with "socket transport has no local or peer target".
  return rclcpp::NodeOptions().enable_rosout(false).start_parameter_services(false)
    .start_parameter_event_publisher(false);
}

int run_publisher(const std::string & payload, std::chrono::milliseconds delay_before_publish)
{
  auto node = std::make_shared<rclcpp::Node>(
    "destination_order_publisher_" + payload.substr(0, 4), no_incidental_traffic_options());
  auto publisher = node->create_publisher<std_msgs::msg::String>(kTopic, rclcpp::QoS(4));
  std::this_thread::sleep_for(delay_before_publish);
  std_msgs::msg::String message;
  message.data = payload;
  publisher->publish(message);
  std::cout << "{\"schema_version\":\"fleetrmw.destination_order_probe.v1\","
            << "\"mode\":\"publisher\",\"status\":\"ok\",\"payload\":\"" << payload << "\"}"
            << std::endl;
  // Stay alive long enough for the frame to actually leave the socket and
  // for the observer (which waits a fixed window before consuming) to see
  // it, before this process's node/context tear down.
  std::this_thread::sleep_for(2s);
  return 0;
}

int run_observer()
{
  auto node = std::make_shared<rclcpp::Node>(
    "destination_order_observer", no_incidental_traffic_options());

  std::vector<std::string> reception_order_payloads;
  auto reception_order_options = rclcpp::SubscriptionOptions();
  auto reception_order_subscription = node->create_subscription<std_msgs::msg::String>(
    kTopic, rclcpp::QoS(4),
    [&](std_msgs::msg::String::ConstSharedPtr message) {
      reception_order_payloads.push_back(message->data);
    },
    reception_order_options);

  std::vector<std::string> source_order_payloads;
  rmw_fleetqox_cpp::FleetQoxExtendedQosPayload source_order_qos;
  source_order_qos.destination_order =
    rmw_fleetqox_cpp::DestinationOrderKind::kBySourceTimestamp;
  auto source_order_options = rclcpp::SubscriptionOptions();
  source_order_options.rmw_implementation_payload =
    std::make_shared<rmw_fleetqox_cpp::ExtendedSubscriptionPayload>(source_order_qos);
  auto source_order_subscription = node->create_subscription<std_msgs::msg::String>(
    kTopic, rclcpp::QoS(4),
    [&](std_msgs::msg::String::ConstSharedPtr message) {
      source_order_payloads.push_back(message->data);
    },
    source_order_options);

  std::cout << "{\"schema_version\":\"fleetrmw.destination_order_probe.v1\","
            << "\"mode\":\"observer\",\"phase\":\"ready\",\"initialized\":true}" << std::endl;

  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(node);

  // Deliberately do not spin (do not take) until both the slow and fast
  // publishers' messages have had time to arrive over the real wire --
  // otherwise the fast message would be taken and delivered before the
  // slow one even exists in the queue, leaving nothing to reorder.
  std::this_thread::sleep_for(3s);

  const auto deadline = std::chrono::steady_clock::now() + 8s;
  while (rclcpp::ok() && std::chrono::steady_clock::now() < deadline &&
    (reception_order_payloads.size() < 2 || source_order_payloads.size() < 2))
  {
    executor.spin_once(50ms);
  }

  const bool reception_order_ok = reception_order_payloads.size() == 2 &&
    reception_order_payloads[0] == kFastPayload &&
    reception_order_payloads[1] == kSlowPayload;
  const bool source_order_ok = source_order_payloads.size() == 2 &&
    source_order_payloads[0] == kSlowPayload &&
    source_order_payloads[1] == kFastPayload;
  const bool ok = reception_order_ok && source_order_ok;

  std::cout << "{\"schema_version\":\"fleetrmw.destination_order_probe.v1\","
            << "\"mode\":\"observer\",\"status\":\"" << (ok ? "ok" : "failed") << "\","
            << "\"reception_order_claim\":" << (reception_order_ok ? "true" : "false") << ","
            << "\"source_order_claim\":" << (source_order_ok ? "true" : "false") << ","
            << "\"reception_order_count\":" << reception_order_payloads.size() << ","
            << "\"source_order_count\":" << source_order_payloads.size() << "}" << std::endl;

  executor.remove_node(node);
  source_order_subscription.reset();
  reception_order_subscription.reset();
  node.reset();
  return ok ? 0 : 1;
}

}  // namespace

int main(int argc, char ** argv)
{
  const Config config = parse_args(argc, argv);
  int ros_argc = 1;
  rclcpp::init(ros_argc, argv);
  int result = 2;
  if (config.mode == "slow_publisher") {
    result = run_publisher(kSlowPayload, 0ms);
  } else if (config.mode == "fast_publisher") {
    result = run_publisher(kFastPayload, 100ms);
  } else if (config.mode == "observer") {
    result = run_observer();
  }
  rclcpp::shutdown();
  return result;
}
