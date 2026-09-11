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
// fleetqox/trace.py's naming). The AP gets no tap device at all (see
// below). Printed to stdout at startup so the orchestrator can verify agreement
// instead of relying on two hardcoded copies of the same order staying in
// sync silently.
//
// Copy this file into an ns-3 workspace under scratch/ and run it with:
//   ./ns3 run "scratch/fleetqox_trace_replay_tap --numRobots=8 --tapPrefix=ftap"
//
// ROOT CAUSE + FIX for unicast relay never reaching stations (11/09/2026,
// see docs/AUDIT_ACCEPTANCE_TRACKING.md for the full investigation):
// ns-3's own TapBridge (Mode=UseLocal) sets this device's MAC address
// from the SOURCE address of the FIRST packet it ever forwards from the
// tap into ns-3 (TapBridge::ForwardToBridgedDevice, gated by
// m_ns3AddressRewritten so it fires exactly once, in
// external/tap-bridge/model/tap-bridge.cc). That races against ANY
// traffic reaching the tap first -- spurious background traffic (e.g.
// IPv6 neighbor discovery, sent automatically when the interface comes
// up) or another bridged station's own multicast flooded across the
// shared bridge both count -- and whichever wins becomes this station's
// permanent address for the ns-3 side, silently diverging from what its
// real process actually uses. Confirmed via a custom ns-3 debug build:
// the address explicitly set below is DIFFERENT from what
// StaWifiMac::Receive() sees on the SAME device later in the run,
// causing unicast relay (which requires an exact address match, unlike
// broadcast) to be dropped with WifiMac::MacRxDrop even though the PHY
// layer and ACK exchange both succeed. Fixed at the ns-3 level by
// external/ns3/patches/0001-tap-bridge-disable-use-local-address-autolearn.patch
// (disables that auto-learning entirely) -- REQUIRED for this program to
// work; the SetAddress() calls below are necessary but not sufficient
// without it, since TapBridge's own auto-learning would otherwise
// overwrite them again later. Apply the patch to whatever ns-3 source
// tree the target image builds against before compiling this file.

#include "ns3/core-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/tap-bridge-module.h"
#include "ns3/wifi-module.h"

#include <array>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <iostream>
#include <map>
#include <string>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("FleetQoxTraceReplayTap");

// WIFI-LEVEL DIAGNOSTIC COUNTERS (11/09/2026, see docs/AUDIT_ACCEPTANCE_TRACKING.md
// "đào tiếp bằng trace ns-3"): distinguish genuine 802.11 congestion
// collapse at 16-station scale from the RMW-level EHOSTUNREACH retry loop
// (rmw_pubsub.cpp) amplifying it via repeated ARP broadcasts. Plain
// atomics rather than per-packet printing -- a 16-station/90s run at real
// application data rates would produce far too much output to read, and
// RealtimeSimulatorImpl's callback delivery previously showed interleaved/
// corrupted stdout under concurrent writes during earlier trace-based
// debugging in this same investigation. Small-vs-large size split is a
// cheap proxy for ARP/control frames (~42-60B) vs actual RMW UDP payloads
// (hundreds of bytes, per rmw_pubsub.cpp's observed 700-900B fragments) --
// exact enough for this diagnostic without parsing EtherType.
namespace
{
constexpr std::size_t kSmallFrameThresholdBytes = 100;
constexpr std::size_t kMaxRxDropReasons = 32;

std::atomic<uint64_t> g_macTxTotal{0};
std::atomic<uint64_t> g_macTxSmall{0};
std::atomic<uint64_t> g_macTxLarge{0};
std::atomic<uint64_t> g_macTxDropTotal{0};
std::atomic<uint64_t> g_macRxTotal{0};
std::atomic<uint64_t> g_macRxDropTotal{0};
std::atomic<uint64_t> g_phyTxBeginTotal{0};
std::atomic<uint64_t> g_phyRxDropTotal{0};
std::array<std::atomic<uint64_t>, kMaxRxDropReasons> g_phyRxDropByReason{};
std::atomic<uint64_t> g_associatedStaCount{0};

void
MacTxTrace(Ptr<const Packet> packet)
{
  g_macTxTotal.fetch_add(1, std::memory_order_relaxed);
  if (packet->GetSize() <= kSmallFrameThresholdBytes)
  {
    g_macTxSmall.fetch_add(1, std::memory_order_relaxed);
  }
  else
  {
    g_macTxLarge.fetch_add(1, std::memory_order_relaxed);
  }
}

void
MacTxDropTrace(Ptr<const Packet> /* packet */)
{
  g_macTxDropTotal.fetch_add(1, std::memory_order_relaxed);
}

void
MacRxTrace(Ptr<const Packet> /* packet */)
{
  g_macRxTotal.fetch_add(1, std::memory_order_relaxed);
}

void
MacRxDropTrace(Ptr<const Packet> /* packet */)
{
  g_macRxDropTotal.fetch_add(1, std::memory_order_relaxed);
}

void
PhyTxBeginTrace(Ptr<const Packet> /* packet */, double /* txPowerW */)
{
  g_phyTxBeginTotal.fetch_add(1, std::memory_order_relaxed);
}

void
PhyRxDropTrace(Ptr<const Packet> /* packet */, WifiPhyRxfailureReason reason)
{
  g_phyRxDropTotal.fetch_add(1, std::memory_order_relaxed);
  auto idx = static_cast<std::size_t>(reason);
  if (idx < kMaxRxDropReasons)
  {
    g_phyRxDropByReason[idx].fetch_add(1, std::memory_order_relaxed);
  }
}

void
AssociatedStaTrace(uint16_t /* aid */, Mac48Address /* address */)
{
  g_associatedStaCount.fetch_add(1, std::memory_order_relaxed);
}

// STATIC cross-AP-group relay (11/09/2026, see docs/AUDIT_ACCEPTANCE_TRACKING.md
// "đào sâu fix bridge flooding"): replaces an earlier CsmaHelper +
// BridgeHelper backhaul that made the 16-robot delivery collapse WORSE,
// not better (mac_tx_total ~4.6x higher, matching numAps). Root cause:
// ns-3's BridgeNetDevice is a DYNAMIC LEARNING bridge -- it only learns
// "this MAC lives behind this port" from that MAC's OWN outbound
// traffic. Any station that mostly/only RECEIVES (e.g. fleet_router,
// operator_ui -- tx=0 in this harness's traffic pattern) never sends
// anything to learn FROM, so every frame addressed to it is flooded to
// EVERY AP group forever, not just during an initial convergence
// window; confirmed by ApWifiMac::Receive additionally pushing even
// ordinary SAME-group relay traffic up to the promiscuous bridge
// callback (WifiNetDevice::ForwardUp's PACKET_OTHERHOST branch), which
// the un-converged bridge then ALSO flooded onto the backhaul. Since
// this program already knows the complete station -> AP-group mapping
// at setup time (stationIndexesByGroup), a real learning bridge adds
// nothing -- direct, static, single-hop relay is both simpler and
// strictly better here.
std::map<Mac48Address, uint32_t> g_macToApGroup;
std::vector<Ptr<NetDevice>> g_apDeviceByGroup;

bool
ApCrossGroupRelay(
    Ptr<NetDevice> device, Ptr<const Packet> packet, uint16_t protocol, const Address& src,
    const Address& dst, NetDevice::PacketType type)
{
  Mac48Address from = Mac48Address::ConvertFrom(src);
  if (type == NetDevice::PACKET_BROADCAST || type == NetDevice::PACKET_MULTICAST)
  {
    // ARP requests are Ethernet broadcast -- without relaying these too,
    // a station in one AP group can never even RESOLVE a station in
    // another group's L2 address, so no cross-group unicast could ever
    // form in the first place. Confirmed as a real bug: a first version
    // of this relay handled PACKET_OTHERHOST (unicast) only, and cross-
    // group traffic went completely silent (mac_tx_large pinned near 0
    // for the rest of the run while mac_tx_small/ARP-sized kept climbing
    // -- ARP requests going out and never getting a reply back).
    // ApWifiMac's own ForwardDown already relayed this within `device`'s
    // own group, so flood it to every OTHER group only.
    for (Ptr<NetDevice> apDevice : g_apDeviceByGroup)
    {
      if (apDevice != device)
      {
        apDevice->SendFrom(packet->Copy(), from, Mac48Address::ConvertFrom(dst), protocol);
      }
    }
    return true;
  }
  // PACKET_OTHERHOST is exactly "not addressed to this AP itself, and
  // not broadcast/multicast" -- fires both for genuine cross-group
  // unicast traffic (which needs relaying) and for ordinary same-group
  // unicast traffic ApWifiMac already relayed over the air directly
  // (which does not; the g_apDeviceByGroup lookup below tells the two
  // apart).
  if (type != NetDevice::PACKET_OTHERHOST)
  {
    return false;
  }
  Mac48Address to = Mac48Address::ConvertFrom(dst);
  auto it = g_macToApGroup.find(to);
  if (it == g_macToApGroup.end())
  {
    return false;
  }
  Ptr<NetDevice> targetApDevice = g_apDeviceByGroup[it->second];
  if (targetApDevice == device)
  {
    // Same-group traffic ApWifiMac's own ForwardDown already delivered
    // over the air -- relaying it again here would duplicate it.
    return false;
  }
  targetApDevice->SendFrom(packet->Copy(), from, to, protocol);
  return true;
}

void
PrintWifiStats(uint32_t totalStations, uint32_t numAps)
{
  // The orchestrator kills this process once every endpoint finishes,
  // rather than waiting for --simDuration's natural Simulator::Stop --
  // so a print scheduled to run only AFTER Simulator::Run() returns
  // would never fire. Reschedule this call every few seconds instead, so
  // whatever cumulative snapshot made it to the log right before the
  // kill is the data available (Simulator::Schedule ties this to the
  // realtime simulator's wall clock, so "every 5s" really is every 5
  // real seconds).
  Simulator::Schedule(Seconds(5.0), &PrintWifiStats, totalStations, numAps);
  std::cout << "FLEETQOX_WIFI_STATS {"
            << "\"total_stations\":" << totalStations << ","
            << "\"num_aps\":" << numAps << ","
            << "\"associated_stations\":" << g_associatedStaCount.load() << ","
            << "\"mac_tx_total\":" << g_macTxTotal.load() << ","
            << "\"mac_tx_small\":" << g_macTxSmall.load() << ","
            << "\"mac_tx_large\":" << g_macTxLarge.load() << ","
            << "\"mac_tx_drop_total\":" << g_macTxDropTotal.load() << ","
            << "\"mac_rx_total\":" << g_macRxTotal.load() << ","
            << "\"mac_rx_drop_total\":" << g_macRxDropTotal.load() << ","
            << "\"phy_tx_begin_total\":" << g_phyTxBeginTotal.load() << ","
            << "\"phy_rx_drop_total\":" << g_phyRxDropTotal.load() << ","
            << "\"phy_rx_drop_by_reason\":[";
  for (std::size_t i = 0; i < kMaxRxDropReasons; ++i)
  {
    uint64_t count = g_phyRxDropByReason[i].load();
    if (count > 0)
    {
      std::cout << "[" << i << "," << count << "],";
    }
  }
  std::cout << "]}" << std::endl;
}
} // namespace

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
  uint32_t numAps = 1;

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
  cmd.AddValue(
      "numAps",
      "Split stations across this many APs, each on its OWN, fully "
      "non-interfering channel (a separate YansWifiChannel C++ object per "
      "group -- ns-3's Yans model only computes interference among PHYs "
      "sharing one channel object, so this is the best-case, fully "
      "orthogonal-channel scenario). Stations are assigned round-robin "
      "(station i -> AP i%numAps) so each channel carries an even share "
      "of the fleet. Added for the 16-robot-scale delivery-collapse "
      "investigation (see docs/AUDIT_ACCEPTANCE_TRACKING.md) to test "
      "whether a single 802.11g/single-AP channel's real capacity ceiling "
      "-- not any software-layer bug -- is what collapses delivery at "
      "scale. Default 1 preserves the original single-AP topology "
      "exactly.",
      numAps);
  bool isolateController = false;
  cmd.AddValue(
      "isolateController",
      "Reserve AP group 0 exclusively for station 0 (fleet_controller), "
      "round-robining every OTHER station across the remaining "
      "numAps-1 groups instead. Added after a numAps=4/16-robot run "
      "still barely delivered anything despite splitting all OTHER "
      "stations across channels: fleet_controller alone generates 71 "
      "percent of the fleet's traffic (1626/2289 sends) and, confined "
      "to one "
      "channel like everyone else under plain round-robin, that one "
      "channel stayed the bottleneck regardless of numAps -- see "
      "docs/AUDIT_ACCEPTANCE_TRACKING.md. No-op when numAps == 1 or "
      "false (the default).",
      isolateController);
  cmd.Parse(argc, argv);

  if (numRobots == 0)
  {
    NS_FATAL_ERROR("numRobots must be positive");
  }
  if (mobilitySpeed < 0.0 || stationSpacing <= 0.0 || simDuration <= 0.0)
  {
    NS_FATAL_ERROR("mobilitySpeed must be nonnegative, stationSpacing and simDuration positive");
  }
  if (numAps == 0)
  {
    NS_FATAL_ERROR("numAps must be positive");
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

  // TapBridge's UseLocal mode does NOT copy the pre-existing tap device's
  // real MAC onto the ns-3 WifiNetDevice it bridges -- confirmed by a real
  // run where ftap0's host-side MAC and station 0's WifiNetDevice address
  // (queried right after TapBridge::Install) were completely different.
  // The WifiNetDevice keeps ns-3's own default sequential allocation
  // (00:00:00:00:00:01, 02, ...) for the AP's association table, while
  // anything the REAL process on the tap's far side sends (e.g. an ARP
  // reply's "sender hardware address", populated by Linux from the
  // sending interface's OWN address) carries a DIFFERENT, ns-3-unaware
  // MAC. The AP only relays unicast frames to addresses in its
  // association table, so a reply addressed to that unknown MAC is
  // silently dropped -- broadcast frames still get through (relayed to
  // everyone), which is why ARP requests reached the far station but
  // replies never came back.
  //
  // Fix: give every station an EXPLICIT, deterministic MAC here (rather
  // than relying on ns-3's undocumented default allocation order), and
  // have the orchestrator script set that SAME address on the real
  // process's own netns interface -- so the simulated station and the
  // real process behind its tap share one L2 identity end to end.
  std::vector<Mac48Address> stationMacs;
  for (uint32_t i = 0; i < totalStations; ++i)
  {
    char macBuf[18];
    std::snprintf(macBuf, sizeof(macBuf), "02:00:00:00:%02x:%02x", (i >> 8) & 0xFF, i & 0xFF);
    stationMacs.push_back(Mac48Address(macBuf));
  }

  std::cout << "FLEETQOX_TAP_MAPPING station_index,endpoint,tap_device,mac_address\n";
  for (uint32_t i = 0; i < totalStations; ++i)
  {
    std::cout << "FLEETQOX_TAP_MAPPING " << i << "," << stationEndpointLabels[i] << ","
              << stationTapNames[i] << "," << stationMacs[i] << "\n";
  }
  std::cout.flush();

  NodeContainer stations;
  stations.Create(totalStations);
  NodeContainer accessPoints;
  accessPoints.Create(numAps);

  // Station -> AP-group assignment. Purely an ns-3-internal grouping --
  // the tap device name/index/MAC contract with the orchestrator script
  // (by station index i) is completely unaffected, so this needs no
  // changes on the Linux/orchestration side.
  std::vector<std::vector<uint32_t>> stationIndexesByGroup(numAps);
  if (isolateController && numAps > 1)
  {
    // Station 0 is always fleet_controller (see the file-header ordering
    // comment) -- give it group 0 entirely to itself, round-robining
    // every other station across the REMAINING numAps-1 groups.
    stationIndexesByGroup[0].push_back(0);
    for (uint32_t i = 1; i < totalStations; ++i)
    {
      stationIndexesByGroup[1 + (i - 1) % (numAps - 1)].push_back(i);
    }
  }
  else
  {
    // Plain round-robin: station i joins AP (i % numAps)'s channel.
    for (uint32_t i = 0; i < totalStations; ++i)
    {
      stationIndexesByGroup[i % numAps].push_back(i);
    }
  }

  WifiHelper wifi;
  wifi.SetStandard(WIFI_STANDARD_80211g);
  wifi.SetRemoteStationManager(
      "ns3::ConstantRateWifiManager", "DataMode", StringValue(wifiMode), "ControlMode",
      StringValue(wifiMode));

  // stationDevices must end up index-aligned with `stations`/`stationMacs`/
  // `stationTapNames` (station i -> stationDevices.Get(i)) for every loop
  // below to keep working unchanged -- wifi.Install() only accepts one
  // NodeContainer per call, so each AP group's Install() call runs
  // separately (on that group's own NodeContainer of just its stations)
  // and the resulting per-group NetDeviceContainer devices are scattered
  // back into their ORIGINAL station-index slots here, rather than
  // appended in per-group order.
  std::vector<Ptr<NetDevice>> stationDeviceByIndex(totalStations);
  NetDeviceContainer apDevices;
  for (uint32_t g = 0; g < numAps; ++g)
  {
    // A separate YansWifiChannel C++ object per group -- ns-3's Yans wifi
    // model computes interference/collision only among PHYs sharing ONE
    // channel object (there is no separate RF-frequency model layered on
    // top), so distinct channel objects are exactly "fully
    // non-interfering channels," the best case a real multi-AP/
    // multi-channel deployment can achieve.
    YansWifiChannelHelper channelHelper = YansWifiChannelHelper::Default();
    YansWifiPhyHelper phy;
    phy.SetChannel(channelHelper.Create());

    Ssid ssid = Ssid("fleetqox-wifi-" + std::to_string(g));
    WifiMacHelper mac;

    NodeContainer groupStations;
    for (uint32_t stationIndex : stationIndexesByGroup[g])
    {
      groupStations.Add(stations.Get(stationIndex));
    }
    if (groupStations.GetN() > 0)
    {
      mac.SetType(
          "ns3::StaWifiMac", "Ssid", SsidValue(ssid), "ActiveProbing", BooleanValue(false),
          "QosSupported", BooleanValue(wifiQos));
      NetDeviceContainer groupStationDevices = wifi.Install(phy, mac, groupStations);
      for (uint32_t k = 0; k < stationIndexesByGroup[g].size(); ++k)
      {
        stationDeviceByIndex[stationIndexesByGroup[g][k]] = groupStationDevices.Get(k);
      }
    }

    mac.SetType("ns3::ApWifiMac", "Ssid", SsidValue(ssid), "QosSupported", BooleanValue(wifiQos));
    apDevices.Add(wifi.Install(phy, mac, accessPoints.Get(g)));
  }
  NetDeviceContainer stationDevices;
  for (uint32_t i = 0; i < totalStations; ++i)
  {
    stationDevices.Add(stationDeviceByIndex[i]);
  }

  // Cross-AP-group backhaul, so a station on AP group 0's channel can
  // still reach a station on AP group 2's channel -- without this, the
  // numAps groups are fully isolated islands (each on its own
  // non-interfering YansWifiChannel by construction), which is NOT what
  // "split the fleet across parallel channels" is supposed to mean; a
  // first attempt confirmed this as a real bug (permanently unreachable
  // cross-group peers stalled every endpoint's startup on the
  // ENETUNREACH/EHOSTUNREACH retry budget). A second attempt used a
  // CsmaHelper+BridgeHelper backhaul (the standard ns-3 multi-AP-over-
  // wired-LAN pattern) but made things WORSE, not better -- see the
  // STATIC cross-AP-group relay comment above for why (a dynamic
  // learning bridge never learns receive-only stations' location and
  // floods everything addressed to them forever). This program already
  // knows the exact station -> AP-group mapping, so relay statically:
  // no backhaul device/channel needed when numAps == 1 (nothing to
  // relay across).
  if (numAps > 1)
  {
    for (uint32_t i = 0; i < totalStations; ++i)
    {
      g_macToApGroup[stationMacs[i]] = i % numAps;
    }
    for (uint32_t g = 0; g < apDevices.GetN(); ++g)
    {
      g_apDeviceByGroup.push_back(apDevices.Get(g));
    }
    for (uint32_t g = 0; g < apDevices.GetN(); ++g)
    {
      apDevices.Get(g)->SetPromiscReceiveCallback(MakeCallback(&ApCrossGroupRelay));
    }
  }

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

  // All APs sit at the same physical position -- each is on its OWN
  // non-interfering channel object (see the numAps setup above), so
  // co-location is a realistic dense-deployment pattern here (multiple
  // APs in one room on different channels), and physical placement only
  // matters for propagation loss WITHIN a channel, not across channels.
  MobilityHelper apMobility;
  apMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  apMobility.Install(accessPoints);
  const double gridWidth =
      std::ceil(std::sqrt(static_cast<double>(totalStations))) * stationSpacing;
  for (uint32_t g = 0; g < accessPoints.GetN(); ++g)
  {
    accessPoints.Get(g)->GetObject<MobilityModel>()->SetPosition(
        Vector(gridWidth / 2.0, gridWidth / 2.0, 0.0));
  }

  // The AP deliberately gets NO TapBridge: its only job is relaying
  // frames between associated stations inside the simulation (standard
  // ApWifiMac behavior), and nothing real needs to send/receive through
  // it directly -- every FleetQoX endpoint is a station. Only bridge the
  // stations, each to its own pre-created tap (wifi devices don't support
  // promiscuous mode, so Mode=UseLocal is required rather than letting
  // TapBridge create+configure the device itself with ConfigureLocal;
  // matches ns-3's tap-bridge module reference pattern in
  // examples/tap-wifi-virtual-machine.cc).
  TapBridgeHelper tapBridge;
  tapBridge.SetAttribute("Mode", StringValue("UseLocal"));
  for (uint32_t i = 0; i < stations.GetN(); ++i)
  {
    tapBridge.SetAttribute("DeviceName", StringValue(stationTapNames[i]));
    tapBridge.Install(stations.Get(i), stationDevices.Get(i));
  }

  // Must run AFTER TapBridge::Install(), not before -- without the
  // tap-bridge patch (see the file-header comment above), TapBridge's
  // own address auto-learning fires asynchronously, on a background
  // thread, whenever it happens to process its first packet, which can
  // be BEFORE OR AFTER this point in program order regardless of source
  // ordering; with the patch applied, that auto-learning is disabled
  // entirely, so this explicit assignment is what actually takes effect
  // and there is no race to lose either way. Kept after Install() so the
  // behavior degrades safely (fails loudly via mismatched addresses,
  // not silently) if this file is ever built against an unpatched ns-3.
  for (uint32_t i = 0; i < stationDevices.GetN(); ++i)
  {
    stationDevices.Get(i)->SetAddress(stationMacs[i]);
    // WifiNetDevice::SetAddress() only updates the MLD/device-level
    // identity (WifiMac::m_address, what GetAddress() returns) -- the
    // actual over-the-air frames (association request included) are
    // built by the per-link FrameExchangeManager, which keeps its OWN
    // separate address that SetAddress() at the device level never
    // touches. Confirmed via ns-3's own trace sources (AssociatedSta on
    // ApWifiMac, TypeId-introspected -- no source or NS_LOG needed):
    // without this, the AP's association table recorded each station
    // under ns-3's original default address, not this one, even though
    // device->GetAddress() correctly reported the new value.
    Ptr<StaWifiMac> smac = DynamicCast<StaWifiMac>(
        DynamicCast<WifiNetDevice>(stationDevices.Get(i))->GetMac());
    smac->GetFrameExchangeManager()->SetAddress(stationMacs[i]);
  }

  // Hook every station's + the AP's Phy/Mac trace sources -- see the
  // WIFI-LEVEL DIAGNOSTIC COUNTERS block above for why (distinguishing a
  // genuine 802.11 capacity/collision ceiling at scale from the RMW
  // retry loop's own ARP broadcasts adding to the contention).
  for (uint32_t i = 0; i < stationDevices.GetN(); ++i)
  {
    Ptr<WifiNetDevice> dev = DynamicCast<WifiNetDevice>(stationDevices.Get(i));
    dev->GetMac()->TraceConnectWithoutContext("MacTx", MakeCallback(&MacTxTrace));
    dev->GetMac()->TraceConnectWithoutContext("MacTxDrop", MakeCallback(&MacTxDropTrace));
    dev->GetMac()->TraceConnectWithoutContext("MacRx", MakeCallback(&MacRxTrace));
    dev->GetMac()->TraceConnectWithoutContext("MacRxDrop", MakeCallback(&MacRxDropTrace));
    dev->GetPhy()->TraceConnectWithoutContext("PhyTxBegin", MakeCallback(&PhyTxBeginTrace));
    dev->GetPhy()->TraceConnectWithoutContext("PhyRxDrop", MakeCallback(&PhyRxDropTrace));
  }
  for (uint32_t i = 0; i < apDevices.GetN(); ++i)
  {
    Ptr<WifiNetDevice> dev = DynamicCast<WifiNetDevice>(apDevices.Get(i));
    dev->GetMac()->TraceConnectWithoutContext("MacTx", MakeCallback(&MacTxTrace));
    dev->GetMac()->TraceConnectWithoutContext("MacTxDrop", MakeCallback(&MacTxDropTrace));
    dev->GetMac()->TraceConnectWithoutContext("MacRx", MakeCallback(&MacRxTrace));
    dev->GetMac()->TraceConnectWithoutContext("MacRxDrop", MakeCallback(&MacRxDropTrace));
    dev->GetPhy()->TraceConnectWithoutContext("PhyTxBegin", MakeCallback(&PhyTxBeginTrace));
    dev->GetPhy()->TraceConnectWithoutContext("PhyRxDrop", MakeCallback(&PhyRxDropTrace));
    Ptr<ApWifiMac> apMac = DynamicCast<ApWifiMac>(dev->GetMac());
    apMac->TraceConnectWithoutContext("AssociatedSta", MakeCallback(&AssociatedStaTrace));
  }

  Simulator::Schedule(Seconds(5.0), &PrintWifiStats, totalStations, numAps);

  Simulator::Stop(Seconds(simDuration));
  Simulator::Run();
  PrintWifiStats(totalStations, numAps);
  Simulator::Destroy();

  return 0;
}
