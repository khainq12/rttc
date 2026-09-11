// FleetQoX wifi TAP bridge for ns-3.
//
// Unlike fleetqox_trace_replay.cc (which reads the trace CSV itself and
// sends/receives raw single-shot UDP entirely inside the simulation, with
// no recovery beyond the 802.11 MAC's own retry limit), this program does
// NOT touch the trace or generate any application traffic at all. Its only
// job is the simulated 802.11g infrastructure topology (same grid
// positions/mobility/AP setup as the wifi branch of
// fleetqox_trace_replay.cc) plus a TapBridge per station, so real Linux
// processes -- each running scripts/fleetqox_rmw_trace_endpoint.py under
// the ACTUAL rmw_fleetqox_cpp transport (real fragment/NACK/repair) --
// have their packets actually traverse this simulated wifi channel instead
// of a plain Docker bridge network.
//
// Runs in ns-3's realtime simulator (required by TapBridge) rather than
// as-fast-as-possible discrete-event time, so a --simDuration=30 run takes
// approximately 30 real wall-clock seconds.
//
// Each station's tap device must already exist (mode "UseLocal": wifi
// devices don't support promiscuous mode, so TapBridge can't create+attach
// a tap itself the way it can for a wired NetDevice) and be reachable from
// wherever the corresponding real process is launched -- see the
// orchestration script (not yet written) for the required `ip tuntap add`
// / bridge / network-namespace setup per station BEFORE this program
// starts.
//
// Linux interface names are capped at IFNAMSIZ-1 = 15 characters, so
// devices are named "<tapPrefix><index>" (short, numeric) rather than
// embedding the endpoint name -- "fleetqox-tap-robot0000" alone would
// already be 22 characters. Index-to-endpoint order is fixed and must be
// mirrored exactly by the orchestration script: 0=controller, 1=fleet_router,
// 2=operator_ui, 3..numRobots+2=robot_0000..robot_{numRobots-1} (matching
// fleetqox/trace.py's naming), and index numRobots+3 for the access point.
// Printed to stdout at startup so the orchestrator can verify agreement
// instead of relying on two hardcoded copies of the same order staying in
// sync silently.
//
// Copy this file into an ns-3 workspace under scratch/ and run it with:
//   ./ns3 run "scratch/fleetqox_trace_replay_tap --numRobots=8 --tapPrefix=ftap"

#include "ns3/core-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/tap-bridge-module.h"
#include "ns3/wifi-module.h"

#include <cmath>
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("FleetQoxTraceReplayTap");

int
main(int argc, char* argv[])
{
  uint32_t numRobots = 8;
  std::string wifiMode = "ErpOfdmRate54Mbps";
  double mobilitySpeed = 0.0;
  double stationSpacing = 3.0;
  std::string tapPrefix = "ftap";
  double simDuration = 30.0;
  bool wifiQos = false;

  CommandLine cmd(__FILE__);
  cmd.AddValue("numRobots", "Number of robot stations (plus 3 fixed endpoints)", numRobots);
  cmd.AddValue("wifiMode", "802.11g station data/control mode", wifiMode);
  cmd.AddValue("mobilitySpeed", "Station speed in meters/second", mobilitySpeed);
  cmd.AddValue("stationSpacing", "Initial Wi-Fi station grid spacing in meters", stationSpacing);
  cmd.AddValue(
      "tapPrefix",
      "Base name for pre-created tap devices: station index i (see the "
      "fixed ordering documented above) uses interface '<prefix><i>', the "
      "access point uses '<prefix>ap'. Keep this short -- Linux interface "
      "names are capped at 15 characters total.",
      tapPrefix);
  cmd.AddValue("simDuration", "How long to run in real wall-clock seconds", simDuration);
  cmd.AddValue(
      "wifiQos",
      "Enable 802.11e/WMM QoS (EDCA) on the wifi MAC, matching "
      "fleetqox_trace_replay.cc's --wifiQos. Real traffic carries its own "
      "User Priority via the RMW/socket layer on the Linux side, not a "
      "SocketPriorityTag set here (this program never touches packet "
      "contents), so this only controls whether the MAC has separate ACs "
      "to place traffic into at all.",
      wifiQos);
  cmd.Parse(argc, argv);

  if (numRobots == 0)
  {
    NS_FATAL_ERROR("numRobots must be positive");
  }
  if (mobilitySpeed < 0.0 || stationSpacing <= 0.0 || simDuration <= 0.0)
  {
    NS_FATAL_ERROR("mobilitySpeed must be nonnegative, stationSpacing and simDuration positive");
  }

  // TapBridge requires the realtime simulator (packets must actually be
  // sent/received on wall-clock time to interoperate with real processes)
  // and real checksums (the simulation normally skips them for speed).
  GlobalValue::Bind("SimulatorImplementationType", StringValue("ns3::RealtimeSimulatorImpl"));
  GlobalValue::Bind("ChecksumEnabled", BooleanValue(true));

  std::vector<std::string> stationEndpointLabels = {"fleet_controller", "fleet_router", "operator_ui"};
  for (uint32_t i = 0; i < numRobots; ++i)
  {
    char suffix[16];
    std::snprintf(suffix, sizeof(suffix), "robot_%04u", i);
    stationEndpointLabels.push_back(suffix);
  }
  const uint32_t totalStations = static_cast<uint32_t>(stationEndpointLabels.size());

  std::vector<std::string> stationTapNames;
  for (uint32_t i = 0; i < totalStations; ++i)
  {
    stationTapNames.push_back(tapPrefix + std::to_string(i));
  }
  const std::string apTapName = tapPrefix + "ap";
  constexpr std::size_t kMaxLinuxInterfaceNameLength = 15; // IFNAMSIZ - 1
  for (const auto& name : stationTapNames)
  {
    if (name.size() > kMaxLinuxInterfaceNameLength)
    {
      NS_FATAL_ERROR(
          "tap device name '" << name << "' exceeds the " << kMaxLinuxInterfaceNameLength
                               << "-character Linux interface name limit -- shorten --tapPrefix");
    }
  }
  if (apTapName.size() > kMaxLinuxInterfaceNameLength)
  {
    NS_FATAL_ERROR(
        "tap device name '" << apTapName << "' exceeds the " << kMaxLinuxInterfaceNameLength
                             << "-character Linux interface name limit -- shorten --tapPrefix");
  }

  std::cout << "FLEETQOX_TAP_MAPPING station_index,endpoint,tap_device\n";
  for (uint32_t i = 0; i < totalStations; ++i)
  {
    std::cout << "FLEETQOX_TAP_MAPPING " << i << "," << stationEndpointLabels[i] << ","
              << stationTapNames[i] << "\n";
  }
  std::cout << "FLEETQOX_TAP_MAPPING ap,ap," << apTapName << "\n";
  std::cout.flush();

  NodeContainer stations;
  stations.Create(totalStations);
  NodeContainer accessPoint;
  accessPoint.Create(1);

  YansWifiChannelHelper channel = YansWifiChannelHelper::Default();
  YansWifiPhyHelper phy;
  phy.SetChannel(channel.Create());

  WifiHelper wifi;
  wifi.SetStandard(WIFI_STANDARD_80211g);
  wifi.SetRemoteStationManager(
      "ns3::ConstantRateWifiManager", "DataMode", StringValue(wifiMode), "ControlMode",
      StringValue(wifiMode));

  Ssid ssid = Ssid("fleetqox-wifi");
  WifiMacHelper mac;
  mac.SetType(
      "ns3::StaWifiMac", "Ssid", SsidValue(ssid), "ActiveProbing", BooleanValue(false),
      "QosSupported", BooleanValue(wifiQos));
  NetDeviceContainer stationDevices = wifi.Install(phy, mac, stations);
  mac.SetType("ns3::ApWifiMac", "Ssid", SsidValue(ssid), "QosSupported", BooleanValue(wifiQos));
  NetDeviceContainer apDevices = wifi.Install(phy, mac, accessPoint);

  // Same grid-position formula as fleetqox_trace_replay.cc's non-roaming
  // wifi branch, so station density stays comparable to the raw-UDP test.
  MobilityHelper stationMobility;
  stationMobility.SetPositionAllocator(
      "ns3::GridPositionAllocator", "MinX", DoubleValue(0.0), "MinY", DoubleValue(0.0), "DeltaX",
      DoubleValue(stationSpacing), "DeltaY", DoubleValue(stationSpacing), "GridWidth",
      UintegerValue(static_cast<uint32_t>(std::ceil(std::sqrt(totalStations)))), "LayoutType",
      StringValue("RowFirst"));
  stationMobility.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
  stationMobility.Install(stations);
  for (uint32_t i = 0; i < stations.GetN(); ++i)
  {
    Ptr<ConstantVelocityMobilityModel> model =
        stations.Get(i)->GetObject<ConstantVelocityMobilityModel>();
    const double direction = (i % 2 == 0) ? 1.0 : -1.0;
    model->SetVelocity(Vector(direction * mobilitySpeed, 0.0, 0.0));
  }

  MobilityHelper apMobility;
  apMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  apMobility.Install(accessPoint);
  const double gridWidth =
      std::ceil(std::sqrt(static_cast<double>(totalStations))) * stationSpacing;
  accessPoint.Get(0)->GetObject<MobilityModel>()->SetPosition(
      Vector(gridWidth / 2.0, gridWidth / 2.0, 0.0));

  // Wifi devices don't support promiscuous mode, so each tap must already
  // exist and be attached with Mode=UseLocal rather than letting TapBridge
  // create+configure it (ConfigureLocal), matching the reference pattern
  // in ns-3's tap-bridge module (examples/tap-wifi-virtual-machine.cc).
  TapBridgeHelper tapBridge;
  tapBridge.SetAttribute("Mode", StringValue("UseLocal"));
  for (uint32_t i = 0; i < stations.GetN(); ++i)
  {
    tapBridge.SetAttribute("DeviceName", StringValue(stationTapNames[i]));
    tapBridge.Install(stations.Get(i), stationDevices.Get(i));
  }
  tapBridge.SetAttribute("DeviceName", StringValue(apTapName));
  tapBridge.Install(accessPoint.Get(0), apDevices.Get(0));

  Simulator::Stop(Seconds(simDuration));
  Simulator::Run();
  Simulator::Destroy();

  return 0;
}
