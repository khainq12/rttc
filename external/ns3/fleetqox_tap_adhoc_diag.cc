// DIAGNOSTIC ONLY -- tests whether AD-HOC wifi mode (no AP, direct
// station-to-station) avoids the unresolved infrastructure-mode AP-relay
// failure confirmed in fleetqox_trace_replay_tap.cc, while still
// exercising real 802.11 CSMA/CA contention (unlike the CSMA-bus diag).
//
// RESULT (11/09/2026): segfaults inside ns-3 during Simulator::Run() --
// confirmed via bisection that setup (node/device/mobility/TapBridge
// install) all complete successfully and the crash happens only once the
// event loop starts processing real traffic; a minimal AdhocWifiMac
// program WITHOUT TapBridge does not crash, so this is specific to the
// AdhocWifiMac + TapBridge (UseLocal, RealtimeSimulatorImpl) combination
// in this ns-3 3.41 apt build. Not a viable path forward without a newer
// ns-3 build or its real .cc source to debug (this image has neither --
// see docs/AUDIT_ACCEPTANCE_TRACKING.md). Left in place as a dead-end
// data point, not meant to be run again as-is.
#include "ns3/core-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/tap-bridge-module.h"
#include "ns3/wifi-module.h"

#include <iostream>
#include <string>
#include <vector>

using namespace ns3;

int
main(int argc, char* argv[])
{
  uint32_t numStations = 4;
  std::string tapPrefix = "ftap";
  double simDuration = 30.0;
  std::string wifiMode = "ErpOfdmRate54Mbps";

  CommandLine cmd(__FILE__);
  cmd.AddValue("numStations", "stations", numStations);
  cmd.AddValue("tapPrefix", "tap prefix", tapPrefix);
  cmd.AddValue("simDuration", "sim duration", simDuration);
  cmd.Parse(argc, argv);

  GlobalValue::Bind("SimulatorImplementationType", StringValue("ns3::RealtimeSimulatorImpl"));
  GlobalValue::Bind("ChecksumEnabled", BooleanValue(true));

  std::vector<std::string> tapNames;
  for (uint32_t i = 0; i < numStations; ++i) tapNames.push_back(tapPrefix + std::to_string(i));

  std::cout << "FLEETQOX_TAP_MAPPING station_index,tap_device\n";
  for (uint32_t i = 0; i < numStations; ++i)
    std::cout << "FLEETQOX_TAP_MAPPING " << i << "," << tapNames[i] << "\n";
  std::cout.flush();

  NodeContainer stations;
  stations.Create(numStations);

  YansWifiChannelHelper channel = YansWifiChannelHelper::Default();
  YansWifiPhyHelper phy;
  phy.SetChannel(channel.Create());

  WifiHelper wifi;
  wifi.SetStandard(WIFI_STANDARD_80211g);
  wifi.SetRemoteStationManager(
      "ns3::ConstantRateWifiManager", "DataMode", StringValue(wifiMode), "ControlMode",
      StringValue(wifiMode));

  WifiMacHelper mac;
  mac.SetType("ns3::AdhocWifiMac");
  NetDeviceContainer devices = wifi.Install(phy, mac, stations);

  MobilityHelper mobility;
  mobility.SetPositionAllocator(
      "ns3::GridPositionAllocator", "MinX", DoubleValue(0.0), "MinY", DoubleValue(0.0), "DeltaX",
      DoubleValue(3.0), "DeltaY", DoubleValue(3.0), "GridWidth", UintegerValue(2), "LayoutType",
      StringValue("RowFirst"));
  mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  mobility.Install(stations);

  TapBridgeHelper tapBridge;
  tapBridge.SetAttribute("Mode", StringValue("UseLocal"));
  for (uint32_t i = 0; i < stations.GetN(); ++i)
  {
    tapBridge.SetAttribute("DeviceName", StringValue(tapNames[i]));
    tapBridge.Install(stations.Get(i), devices.Get(i));
    std::cout << "FLEETQOX_DEBUG installed tapbridge " << i << std::endl;
  }

  std::cout << "FLEETQOX_DEBUG about to Simulator::Run" << std::endl;
  Simulator::Stop(Seconds(simDuration));
  Simulator::Run();
  std::cout << "FLEETQOX_DEBUG Simulator::Run returned" << std::endl;
  Simulator::Destroy();
  return 0;
}
