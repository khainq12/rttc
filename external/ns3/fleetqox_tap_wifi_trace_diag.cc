// DIAGNOSTIC ONLY -- not part of the FleetQoX TAP-bridge pipeline.
//
// Continues the investigation in fleetqox_trace_replay_tap.cc /
// fleetqox_tap_csma_diag.cc / fleetqox_tap_adhoc_diag.cc (see
// docs/AUDIT_ACCEPTANCE_TRACKING.md): a real process's ARP reply,
// injected into the simulated 802.11 infrastructure-mode network via
// TapBridge, is relayed correctly from station->AP (confirmed via raw
// packet capture) but never makes it back AP->station, even with fully
// consistent MAC addressing and across ns-3 3.41 through 3.46.
//
// This program hooks ns-3's own trace-source system (MacTxDrop/
// MacRxDrop/Assoc/AssociatedSta/etc, discovered via TypeId runtime
// reflection in list_traces_diag.cc -- no .cc source or NS_LOG needed)
// on both stations and the AP, to see WHY the AP fails to relay the
// reply instead of just observing that it does.
//
// Deliberately reduced to 2 stations (not the full 4-station FleetQoX
// topology) to keep trace output readable for this focused ARP-only
// test, per the request to debug TapBridge<->ApWifiMac in isolation
// before reintroducing UDP/FleetRMW.

#include "ns3/core-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/tap-bridge-module.h"
#include "ns3/wifi-module.h"

#include <iostream>
#include <string>
#include <vector>

using namespace ns3;

static void
StaAssoc(std::string who, Mac48Address apAddr)
{
  std::cout << "[TRACE] " << who << " StaWifiMac::Assoc -> AP " << apAddr << " at "
            << Simulator::Now().GetSeconds() << "s\n";
}

static void
StaDeAssoc(std::string who, Mac48Address apAddr)
{
  std::cout << "[TRACE] " << who << " StaWifiMac::DeAssoc <- AP " << apAddr << " at "
            << Simulator::Now().GetSeconds() << "s\n";
}

static void
ApAssociatedSta(uint16_t aid, Mac48Address addr)
{
  std::cout << "[TRACE] AP ApWifiMac::AssociatedSta aid=" << aid << " addr=" << addr << " at "
            << Simulator::Now().GetSeconds() << "s\n";
}

static void
ApDeAssociatedSta(uint16_t aid, Mac48Address addr)
{
  std::cout << "[TRACE] AP ApWifiMac::DeAssociatedSta aid=" << aid << " addr=" << addr << " at "
            << Simulator::Now().GetSeconds() << "s\n";
}

static void
MacTxDrop(std::string who, Ptr<const Packet> p)
{
  std::cout << "[TRACE] " << who << " WifiMac::MacTxDrop size=" << p->GetSize() << " uid=" << p->GetUid() << " at "
            << Simulator::Now().GetSeconds() << "s\n";
}

static void
MacRxDrop(std::string who, Ptr<const Packet> p)
{
  std::cout << "[TRACE] " << who << " WifiMac::MacRxDrop size=" << p->GetSize() << " uid=" << p->GetUid() << " at "
            << Simulator::Now().GetSeconds() << "s\n";
}

static void
MacTx(std::string who, Ptr<const Packet> p)
{
  std::cout << "[TRACE] " << who << " WifiMac::MacTx size=" << p->GetSize() << " uid=" << p->GetUid() << " at "
            << Simulator::Now().GetSeconds() << "s\n";
}

static void
MacRx(std::string who, Ptr<const Packet> p)
{
  std::cout << "[TRACE] " << who << " WifiMac::MacRx size=" << p->GetSize() << " uid=" << p->GetUid() << " at "
            << Simulator::Now().GetSeconds() << "s\n";
}

static void
MacTxDataFailed(std::string who, Mac48Address addr)
{
  std::cout << "[TRACE] " << who << " WifiRemoteStationManager::MacTxDataFailed dest=" << addr
             << " at " << Simulator::Now().GetSeconds() << "s\n";
}

static void
PhyRxDrop(std::string who, Ptr<const Packet> p, WifiPhyRxfailureReason reason)
{
  std::cout << "[TRACE] " << who << " WifiPhy::PhyRxDrop size=" << p->GetSize() << " uid=" << p->GetUid()
            << " reason=" << reason << " at " << Simulator::Now().GetSeconds() << "s\n";
}

static void
PhyTxBegin(std::string who, Ptr<const Packet> p, double txPowerW)
{
  std::cout << "[TRACE] " << who << " WifiPhy::PhyTxBegin size=" << p->GetSize() << " uid=" << p->GetUid() << " at "
            << Simulator::Now().GetSeconds() << "s";
  WifiMacHeader hdr;
  if (p->PeekHeader(hdr) > 0)
  {
    std::cout << " RA=" << hdr.GetAddr1() << " TA=" << hdr.GetAddr2()
               << " A3=" << hdr.GetAddr3() << " type=" << hdr.GetTypeString()
               << " ToDS=" << hdr.IsToDs() << " FromDS=" << hdr.IsFromDs()
               << " seq=" << hdr.GetSequenceNumber() << " frag=" << hdr.GetFragmentNumber()
               << " retry=" << hdr.IsRetry();
  }
  else
  {
    std::cout << " (no WifiMacHeader present)";
  }
  std::cout << "\n";
}

static void
PhyTxEnd(std::string who, Ptr<const Packet> p)
{
  std::cout << "[TRACE] " << who << " WifiPhy::PhyTxEnd size=" << p->GetSize() << " uid=" << p->GetUid() << " at "
            << Simulator::Now().GetSeconds() << "s\n";
}

static void
PhyRxBegin(std::string who, Ptr<const Packet> p, RxPowerWattPerChannelBand rxPowersW)
{
  std::cout << "[TRACE] " << who << " WifiPhy::PhyRxBegin size=" << p->GetSize() << " uid=" << p->GetUid() << " at "
            << Simulator::Now().GetSeconds() << "s\n";
}

int
main(int argc, char* argv[])
{
  // Trace callbacks below print via std::cout without an explicit
  // flush; when stdout is redirected to a file (fully buffered, unlike
  // a terminal) the last batch of output is lost if the process is
  // killed before an implicit flush happens -- confirmed by the trace
  // log truncating at the exact same line across multiple runs despite
  // waiting far longer before kill. unitbuf flushes after every
  // insertion so nothing is lost regardless of how the process ends.
  std::cout.setf(std::ios::unitbuf);

  std::string tapPrefix = "ftap";
  double simDuration = 30.0;
  std::string wifiMode = "ErpOfdmRate54Mbps";

  CommandLine cmd(__FILE__);
  cmd.AddValue("tapPrefix", "tap prefix", tapPrefix);
  cmd.AddValue("simDuration", "sim duration", simDuration);
  cmd.Parse(argc, argv);

  GlobalValue::Bind("SimulatorImplementationType", StringValue("ns3::RealtimeSimulatorImpl"));
  GlobalValue::Bind("ChecksumEnabled", BooleanValue(true));

  // Index 0 = station0 (real process sends the ARP request), index 1 =
  // station1 (real process replies). tap<prefix>0 / tap<prefix>1.
  std::vector<std::string> tapNames = {tapPrefix + "0", tapPrefix + "1"};

  std::cout << "FLEETQOX_TAP_MAPPING station_index,tap_device\n";
  for (uint32_t i = 0; i < tapNames.size(); ++i)
    std::cout << "FLEETQOX_TAP_MAPPING " << i << "," << tapNames[i] << "\n";
  std::cout.flush();

  NodeContainer stations;
  stations.Create(2);
  NodeContainer ap;
  ap.Create(1);

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
  mac.SetType("ns3::StaWifiMac", "Ssid", SsidValue(ssid), "ActiveProbing", BooleanValue(false));
  NetDeviceContainer stationDevices = wifi.Install(phy, mac, stations);
  mac.SetType("ns3::ApWifiMac", "Ssid", SsidValue(ssid));
  NetDeviceContainer apDevices = wifi.Install(phy, mac, ap);

  MobilityHelper mobility;
  mobility.SetPositionAllocator(
      "ns3::GridPositionAllocator", "MinX", DoubleValue(0.0), "MinY", DoubleValue(0.0), "DeltaX",
      DoubleValue(3.0), "DeltaY", DoubleValue(3.0), "GridWidth", UintegerValue(2), "LayoutType",
      StringValue("RowFirst"));
  mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  mobility.Install(stations);
  MobilityHelper apMobility;
  apMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  apMobility.Install(ap);
  ap.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(3.0, 3.0, 0.0));

  // Deterministic MACs (matches the fix in fleetqox_trace_replay_tap.cc /
  // run_ns3_docker_wifi_tap_rmw_probe.py's _station_mac) so the netns
  // side can be set to the identical address.
  for (uint32_t i = 0; i < stationDevices.GetN(); ++i)
  {
    char macBuf[18];
    std::snprintf(macBuf, sizeof(macBuf), "02:00:00:00:00:%02x", static_cast<unsigned>(i & 0xFF));
    Mac48Address addr(macBuf);
    stationDevices.Get(i)->SetAddress(addr);
    // WifiMac::SetAddress() only updates the MLD/device-level identity
    // (WifiMac::m_address) -- the actual over-the-air frames (including
    // the Association Request's source address) are built by the
    // per-link FrameExchangeManager, which has its OWN separate m_self
    // address that SetAddress() at the device level never touches.
    // Confirmed via trace: without this, AP::AssociatedSta recorded the
    // peer as ns-3's original default address, not this one, even
    // though device->GetAddress() correctly reported it.
    Ptr<StaWifiMac> smac = DynamicCast<StaWifiMac>(
        DynamicCast<WifiNetDevice>(stationDevices.Get(i))->GetMac());
    smac->GetFrameExchangeManager()->SetAddress(addr);
    std::cout << "FLEETQOX_DEBUG_MAC device.GetAddress() station" << i << " = "
              << stationDevices.Get(i)->GetAddress() << "\n";
    std::cout << "FLEETQOX_DEBUG_MAC mac->GetAddress() station" << i << " = " << smac->GetAddress()
              << "\n";
    std::cout << "FLEETQOX_DEBUG_MAC fem->GetAddress() station" << i << " = "
              << smac->GetFrameExchangeManager()->GetAddress() << "\n";
  }
  std::cout.flush();

  // --- trace hooks ---
  std::vector<std::string> labels = {"station0", "station1"};
  for (uint32_t i = 0; i < stations.GetN(); ++i)
  {
    Ptr<WifiNetDevice> dev = DynamicCast<WifiNetDevice>(stationDevices.Get(i));
    Ptr<StaWifiMac> staMac = DynamicCast<StaWifiMac>(dev->GetMac());
    staMac->TraceConnectWithoutContext("Assoc", MakeBoundCallback(&StaAssoc, labels[i]));
    staMac->TraceConnectWithoutContext("DeAssoc", MakeBoundCallback(&StaDeAssoc, labels[i]));
    staMac->TraceConnectWithoutContext("MacTxDrop", MakeBoundCallback(&MacTxDrop, labels[i]));
    staMac->TraceConnectWithoutContext("MacRxDrop", MakeBoundCallback(&MacRxDrop, labels[i]));
    staMac->TraceConnectWithoutContext("MacTx", MakeBoundCallback(&MacTx, labels[i]));
    staMac->TraceConnectWithoutContext("MacRx", MakeBoundCallback(&MacRx, labels[i]));
    Ptr<WifiPhy> wphy = dev->GetPhy();
    wphy->TraceConnectWithoutContext("PhyRxDrop", MakeBoundCallback(&PhyRxDrop, labels[i]));
    wphy->TraceConnectWithoutContext("PhyTxBegin", MakeBoundCallback(&PhyTxBegin, labels[i]));
    wphy->TraceConnectWithoutContext("PhyTxEnd", MakeBoundCallback(&PhyTxEnd, labels[i]));
    wphy->TraceConnectWithoutContext("PhyRxBegin", MakeBoundCallback(&PhyRxBegin, labels[i]));
    Ptr<WifiRemoteStationManager> rsm = dev->GetRemoteStationManager();
    rsm->TraceConnectWithoutContext(
        "MacTxDataFailed", MakeBoundCallback(&MacTxDataFailed, labels[i]));
  }
  {
    Ptr<WifiNetDevice> dev = DynamicCast<WifiNetDevice>(apDevices.Get(0));
    Ptr<ApWifiMac> apMac = DynamicCast<ApWifiMac>(dev->GetMac());
    apMac->TraceConnectWithoutContext("AssociatedSta", MakeCallback(&ApAssociatedSta));
    apMac->TraceConnectWithoutContext("DeAssociatedSta", MakeCallback(&ApDeAssociatedSta));
    apMac->TraceConnectWithoutContext("MacTxDrop", MakeBoundCallback(&MacTxDrop, std::string("AP")));
    apMac->TraceConnectWithoutContext("MacRxDrop", MakeBoundCallback(&MacRxDrop, std::string("AP")));
    apMac->TraceConnectWithoutContext("MacTx", MakeBoundCallback(&MacTx, std::string("AP")));
    apMac->TraceConnectWithoutContext("MacRx", MakeBoundCallback(&MacRx, std::string("AP")));
    Ptr<WifiPhy> wphy = dev->GetPhy();
    wphy->TraceConnectWithoutContext("PhyRxDrop", MakeBoundCallback(&PhyRxDrop, std::string("AP")));
    wphy->TraceConnectWithoutContext("PhyTxBegin", MakeBoundCallback(&PhyTxBegin, std::string("AP")));
    wphy->TraceConnectWithoutContext("PhyTxEnd", MakeBoundCallback(&PhyTxEnd, std::string("AP")));
    wphy->TraceConnectWithoutContext("PhyRxBegin", MakeBoundCallback(&PhyRxBegin, std::string("AP")));
  }

  TapBridgeHelper tapBridge;
  tapBridge.SetAttribute("Mode", StringValue("UseLocal"));
  for (uint32_t i = 0; i < stations.GetN(); ++i)
  {
    tapBridge.SetAttribute("DeviceName", StringValue(tapNames[i]));
    tapBridge.Install(stations.Get(i), stationDevices.Get(i));
  }

  // Scheduled well after association (typically completes by ~0.2s) to
  // directly verify each station's stored BSSID matches the AP's actual
  // address -- ruling in/out a BSSID mismatch as the reason a correctly
  // RA-addressed relay frame still gets MacRxDrop'd.
  Simulator::Schedule(Seconds(1.0), [&stationDevices, &labels, &apDevices]() {
    for (uint32_t i = 0; i < stationDevices.GetN(); ++i)
    {
      Ptr<StaWifiMac> smac =
          DynamicCast<StaWifiMac>(DynamicCast<WifiNetDevice>(stationDevices.Get(i))->GetMac());
      std::cout << "FLEETQOX_DEBUG_BSSID " << labels[i] << " GetBssid()=" << smac->GetBssid(0)
                << "\n";
    }
    Ptr<WifiMac> apMac = DynamicCast<WifiNetDevice>(apDevices.Get(0))->GetMac();
    std::cout << "FLEETQOX_DEBUG_BSSID AP GetAddress()=" << apMac->GetAddress() << "\n";
  });

  std::cout << "FLEETQOX_DEBUG setup complete, running sim\n";
  std::cout.flush();
  Simulator::Stop(Seconds(simDuration));
  Simulator::Run();
  Simulator::Destroy();
  return 0;
}
