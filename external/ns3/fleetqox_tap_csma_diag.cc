// DIAGNOSTIC ONLY -- not part of the FleetQoX TAP-bridge pipeline.
//
// Isolates whether the unresolved unicast-relay failure seen in
// fleetqox_trace_replay_tap.cc (a real process's ARP reply never makes
// it back to the originating station over ns-3's simulated 802.11
// infrastructure-mode wifi + AP, despite broadcast working fine and MAC
// addressing being fully consistent -- see
// docs/AUDIT_ACCEPTANCE_TRACKING.md's TAP-bridge section) is specific to
// wifi/AP relay, or a more general TapBridge/orchestration problem.
//
// Replaces the wifi+AP topology with a plain CSMA (wired Ethernet) bus:
// same number of stations, same TapBridge Mode=UseLocal per station, same
// tap naming convention (<tapPrefix><index>), but NO access point and NO
// association step -- a CSMA bus delivers unicast frames by MAC address
// directly, with no AP relay hop to go wrong. If unicast round-trips
// correctly here, the bug is proven wifi/AP-specific. If it still fails,
// the bug is in TapBridge/orchestration generally, not wifi.
//
// Usage: ./ns3 run "scratch/fleetqox_tap_csma_diag --numStations=4 --tapPrefix=ftap"

#include "ns3/core-module.h"
#include "ns3/csma-module.h"
#include "ns3/network-module.h"
#include "ns3/tap-bridge-module.h"

#include <iostream>
#include <string>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("FleetQoxTapCsmaDiag");

int
main(int argc, char* argv[])
{
  uint32_t numStations = 4;
  std::string tapPrefix = "ftap";
  double simDuration = 30.0;

  CommandLine cmd(__FILE__);
  cmd.AddValue("numStations", "Number of CSMA-bus stations", numStations);
  cmd.AddValue("tapPrefix", "Base name for pre-created tap devices", tapPrefix);
  cmd.AddValue("simDuration", "How long to run in real wall-clock seconds", simDuration);
  cmd.Parse(argc, argv);

  if (numStations == 0)
  {
    NS_FATAL_ERROR("numStations must be positive");
  }

  GlobalValue::Bind("SimulatorImplementationType", StringValue("ns3::RealtimeSimulatorImpl"));
  GlobalValue::Bind("ChecksumEnabled", BooleanValue(true));

  std::vector<std::string> tapNames;
  for (uint32_t i = 0; i < numStations; ++i)
  {
    tapNames.push_back(tapPrefix + std::to_string(i));
  }
  constexpr std::size_t kMaxLinuxInterfaceNameLength = 15; // IFNAMSIZ - 1
  for (const auto& name : tapNames)
  {
    if (name.size() > kMaxLinuxInterfaceNameLength)
    {
      NS_FATAL_ERROR("tap device name '" << name << "' exceeds the "
                                          << kMaxLinuxInterfaceNameLength << "-character limit");
    }
  }

  std::cout << "FLEETQOX_TAP_MAPPING station_index,tap_device\n";
  for (uint32_t i = 0; i < numStations; ++i)
  {
    std::cout << "FLEETQOX_TAP_MAPPING " << i << "," << tapNames[i] << "\n";
  }
  std::cout.flush();

  NodeContainer stations;
  stations.Create(numStations);

  CsmaHelper csma;
  csma.SetChannelAttribute("DataRate", StringValue("100Mbps"));
  csma.SetChannelAttribute("Delay", TimeValue(MicroSeconds(1)));
  NetDeviceContainer devices = csma.Install(stations);

  // No explicit SetAddress() here -- CSMA has no AP-association table to
  // desync from, so ns-3's own default sequential allocation is fine
  // and this diagnostic deliberately keeps the setup as minimal as
  // possible to isolate the wifi/AP variable specifically.
  for (uint32_t i = 0; i < devices.GetN(); ++i)
  {
    std::cout << "FLEETQOX_TAP_MAC " << i << "," << devices.Get(i)->GetAddress() << "\n";
  }
  std::cout.flush();

  TapBridgeHelper tapBridge;
  tapBridge.SetAttribute("Mode", StringValue("UseLocal"));
  for (uint32_t i = 0; i < stations.GetN(); ++i)
  {
    tapBridge.SetAttribute("DeviceName", StringValue(tapNames[i]));
    tapBridge.Install(stations.Get(i), devices.Get(i));
  }

  Simulator::Stop(Seconds(simDuration));
  Simulator::Run();
  Simulator::Destroy();

  return 0;
}
