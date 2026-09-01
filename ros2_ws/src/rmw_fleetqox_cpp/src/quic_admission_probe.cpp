#include "rmw_fleetqox_cpp/data_frame.hpp"
#include "rmw_fleetqox_cpp/quic_gateway_transport.hpp"

#include <cstdint>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

namespace
{

std::string frame(
  const std::string & topic,
  const std::string & publisher,
  std::uint64_t sequence)
{
  const std::string text = publisher + "-" + std::to_string(sequence);
  return rmw_fleetqox_cpp::encode_data_frame(
    rmw_fleetqox_cpp::DataFrame{
      "admission-robot",
      topic,
      publisher,
      sequence,
      static_cast<std::int64_t>(sequence * 1000000),
      std::vector<std::uint8_t>(text.begin(), text.end()),
      42,
      "std_msgs/msg/String"});
}

bool configure(
  rmw_fleetqox_cpp::QuicGatewayTransport * transport,
  const std::string & uri)
{
  return transport != nullptr &&
         ::setenv("FLEETQOX_RMW_QUIC_URI", uri.c_str(), 1) == 0 &&
         transport->configure_from_environment();
}

bool receive_exact(
  rmw_fleetqox_cpp::QuicGatewayTransport * transport,
  const std::string & expected)
{
  std::string received;
  return transport != nullptr && transport->receive(&received) && received == expected;
}

bool status_error(
  const rmw_fleetqox_cpp::QuicGatewayTransport & transport,
  int status)
{
  return transport.error().find(
    "HTTP/3 response status " + std::to_string(status)) != std::string::npos;
}

}  // namespace

int main()
{
  const std::string base = "https://localhost:4498/fleetrmw/v1/frames?domain_id=42";
  const std::string control_uri =
    base + "&topic=%2Ffleetqox%2Fcontrol&consumer_id=control-consumer";
  const std::string bulk_uri =
    base + "&topic=%2Ffleetqox%2Fbulk&consumer_id=bulk-consumer";
  const std::string state_uri =
    base + "&topic=%2Ffleetqox%2Fstate&consumer_id=state-consumer";

  const std::string control_1 = frame("/fleetqox/control", "control-publisher", 1);
  const std::string control_2 = frame("/fleetqox/control", "control-publisher", 2);
  rmw_fleetqox_cpp::QuicGatewayTransport control;
  const bool control_configured = configure(&control, control_uri);
  const bool control_admitted = control_configured &&
    control.send(control_1) && control.send(control_2);
  const bool control_taken = control_admitted &&
    receive_exact(&control, control_1) && receive_exact(&control, control_2);
  const bool control_session =
    control.connections_created() == 1 && control.handshakes_completed() == 1 &&
    control.streams_opened() == 4 && control.connection_reuse_count() == 3;
  control.stop();

  const std::string bulk_1 = frame("/fleetqox/bulk", "bulk-publisher", 1);
  const std::string bulk_2 = frame("/fleetqox/bulk", "bulk-publisher", 2);
  rmw_fleetqox_cpp::QuicGatewayTransport bulk;
  const bool bulk_configured = configure(&bulk, bulk_uri);
  const bool bulk_first_admitted = bulk_configured && bulk.send(bulk_1);
  const bool bulk_taken = bulk_first_admitted && receive_exact(&bulk, bulk_1);
  const bool stream_quota_rejected = bulk_taken && !bulk.send(bulk_2) && status_error(bulk, 429);
  const bool bulk_session =
    bulk.connections_created() == 1 && bulk.handshakes_completed() == 1 &&
    bulk.streams_opened() == 3 && bulk.connection_reuse_count() == 2;
  bulk.stop();

  rmw_fleetqox_cpp::QuicGatewayTransport state;
  const bool state_configured = configure(&state, state_uri);
  const bool fleet_quota_rejected = state_configured &&
    !state.send(frame("/fleetqox/state", "state-publisher", 1)) &&
    status_error(state, 429);

  rmw_fleetqox_cpp::QuicGatewayTransport intruder;
  const bool intruder_configured = configure(&intruder, control_uri);
  const bool publisher_rejected = intruder_configured &&
    !intruder.send(frame("/fleetqox/control", "intruder", 3)) &&
    status_error(intruder, 403);
  intruder.stop();

  // Must exceed the gateway's admission epoch_ms (10000) so the fleet quota
  // has definitely reset before the retry below.
  std::this_thread::sleep_for(std::chrono::milliseconds(10500));
  const std::string state_1 = frame("/fleetqox/state", "state-publisher", 1);
  const bool epoch_replenishment_admitted = fleet_quota_rejected &&
    state.send(state_1) && receive_exact(&state, state_1);
  const bool state_session =
    state.connections_created() == 2 && state.handshakes_completed() == 2 &&
    state.streams_opened() == 3 && state.connection_reuse_count() == 1;
  state.stop();

  const bool ok = control_admitted && control_taken && control_session &&
    bulk_first_admitted && bulk_taken && stream_quota_rejected && bulk_session &&
    fleet_quota_rejected && publisher_rejected && epoch_replenishment_admitted &&
    state_session &&
    control.backend_name() == "inprocess" && bulk.backend_name() == "inprocess" &&
    !control.subprocess_backed() && !bulk.subprocess_backed();
  std::cout << "{\"schema_version\":\"fleetrmw.quic_admission_probe.v1\",";
  std::cout << "\"status\":\"" << (ok ? "ok" : "failed") << "\",";
  std::cout << "\"admitted_frame_count\":" << (control_admitted ? 2 : 0) +
    (bulk_first_admitted ? 1 : 0) + (epoch_replenishment_admitted ? 1 : 0) << ",";
  std::cout << "\"taken_frame_count\":" << (control_taken ? 2 : 0) +
    (bulk_taken ? 1 : 0) + (epoch_replenishment_admitted ? 1 : 0) << ",";
  std::cout << "\"stream_quota_rejected\":" <<
    (stream_quota_rejected ? "true" : "false") << ",";
  std::cout << "\"fleet_quota_rejected\":" <<
    (fleet_quota_rejected ? "true" : "false") << ",";
  std::cout << "\"publisher_rejected\":" <<
    (publisher_rejected ? "true" : "false") << ",";
  std::cout << "\"epoch_replenishment_admitted\":" <<
    (epoch_replenishment_admitted ? "true" : "false") << ",";
  std::cout << "\"connections_created\":" <<
    control.connections_created() + bulk.connections_created() +
    state.connections_created() + intruder.connections_created() << ",";
  std::cout << "\"handshakes_completed\":" <<
    control.handshakes_completed() + bulk.handshakes_completed() +
    state.handshakes_completed() + intruder.handshakes_completed() << ",";
  std::cout << "\"streams_opened\":" <<
    control.streams_opened() + bulk.streams_opened() +
    state.streams_opened() + intruder.streams_opened() << ",";
  std::cout << "\"fleet_admission_policy_claim\":" << (ok ? "true" : "false") << ",";
  std::cout << "\"tls_peer_verification_required\":true,";
  std::cout << "\"subprocess_backed\":false,\"production_readiness\":false}" << std::endl;
  return ok ? 0 : 1;
}
