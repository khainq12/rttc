// FleetQoX 5G-LENA (nr module) "ghost node" TAP bridge for ns-3.
//
// 5G equivalent of fleetqox_trace_replay_tap.cc (the 802.11g/wifi profile's
// TapBridge program) for Bảng V's "5G" network profile -- see
// docs/AUDIT_ACCEPTANCE_TRACKING.md "viết chương trình mô phỏng 5G" for the
// full design discussion.
//
// WHY THIS CAN'T BE "JUST SWAP WifiHelper FOR NrHelper": confirmed via the
// nr module's own source (contrib/nr/model/nr-net-device.cc):
//   bool NrNetDevice::SupportsSendFrom() const { return false; }
//   void NrNetDevice::SetPromiscReceiveCallback(...) { /* no-op */ }
// TapBridge's UseLocal mode needs SupportsSendFrom()==true (to spoof the
// real tap's source address when injecting a frame into ns-3); its other
// modes need a REAL promiscuous callback. Neither NrUeNetDevice nor
// NrGnbNetDevice support either -- by design, matching real 5G hardware
// (a real UE's application processor doesn't get an L2 tap into its own
// modem either). TapBridge can therefore NEVER attach directly to an NR
// device, on any ns-3 version, however this module evolves.
//
// FIX ("ghost node" architecture): insert one plain ns-3 Node ("ghost")
// per endpoint BETWEEN the real Docker container's tap and its simulated
// UE:
//
//   Docker container --veth--> [ghost: CSMA, TapBridged] --(IP fwd)--
//     --[ghost: P2P]--> [UE: P2P] --(IP fwd)--> [UE: NrUeNetDevice]
//     ===NR radio=== gNB === 5G core (EPC) === (every other UE, same way)
//
// TapBridge attaches ONLY to the ghost's CSMA device -- an ordinary
// NetDevice that DOES support SendFrom (same device type ns-3 core's own
// examples/tap-csma-virtual-machine.cc TapBridges), never touching an NR
// device at all. Both remaining hops (ghost<->UE, UE<->gNB) are then just
// normal ns-3 IP forwarding + the real NR PHY/MAC simulation respectively
// -- no spoofing trick needed anywhere past the first hop.
//
// Every endpoint (control_station AND every robot) gets IDENTICAL
// treatment -- one ghost+UE pair each, all attached to the same one gNB
// -- matching the wifi/LAN profiles' own "every station is symmetric"
// convention (no station singled out as an external, non-radio
// "DN/Server" role), so Bảng V's three profiles stay apples-to-apples.
//
// ADDRESSING: EpcHelper::AssignUeIpv4Address() assigns each UE an address
// from its own internal "overlay" pool (conventionally 7.0.0.0/8, tied to
// the PGW's own GTP-U-encapsulation routing logic -- NOT something this
// program can safely substitute with an arbitrary scheme without also
// reimplementing that internal PGW matching logic). This program prints
// the resulting per-endpoint overlay IP (FLEETQOX_NR_MAPPING, mirroring
// fleetqox_trace_replay_tap.cc's FLEETQOX_TAP_MAPPING) so the Python
// orchestrator can read it back and configure each real container with a
// route that sources its traffic AS that overlay IP (`ip route ... src`),
// while the container's own link-local address to its ghost stays on a
// separate, fixed, non-EPC-pool subnet -- see the orchestrator script for
// the exact Linux-side commands this pairs with.
//
// Ghost's OWN static routing (AddHostRouteTo) sends traffic for exactly
// this endpoint's overlay IP out its tap-facing CSMA device, regardless of
// whether that device's own address happens to share a subnet with the
// container's traffic -- sidesteps needing any NAT module (confirmed not
// present in this image's ns-3 build) or exact subnet-matching tricks.
//
// Copy this file into an ns-3 workspace and run it against the
// jazzy-nr image variant (see external/rmw-netem/Dockerfile.nr) with:
//   g++ -std=c++17 fleetqox_trace_replay_nr.cc -o fleetqox_nr_bridge \
//     $(pkg-config --cflags --libs ns3-core ns3-network ns3-internet \
//       ns3-point-to-point ns3-csma ns3-mobility ns3-tap-bridge ns3-nr)

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/csma-module.h"
#include "ns3/tap-bridge-module.h"
#include "ns3/mobility-module.h"
#include "ns3/antenna-module.h"
#include "ns3/nr-module.h"

#include <cmath>
#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("FleetQoxTraceReplayNr");

namespace
{
// Same periodic-stats-under-kill pattern as fleetqox_trace_replay_tap.cc's
// PrintWifiStats (see that file for why: the orchestrator kills this
// process once every endpoint finishes rather than waiting for
// --simDuration's natural Simulator::Stop, so only a PERIODICALLY
// rescheduled print is guaranteed to have run recently when that kill
// happens).
uint32_t g_totalStations = 0;

void
PrintNrStats()
{
  Simulator::Schedule(Seconds(5.0), &PrintNrStats);
  std::cout << "FLEETQOX_NR_STATS {\"total_stations\":" << g_totalStations << "}" << std::endl;
}
} // namespace

int
main(int argc, char* argv[])
{
  uint32_t numRobots = 8;
  std::string tapPrefix = "ntap";
  double simDuration = 30.0;
  uint32_t seed = 1;
  uint32_t run = 1;
  std::string layout = "circle";
  // NR cells cover a much larger radius than an indoor 802.11g cell --
  // 50m is a modest, single-macro-cell urban scenario, not tied to the
  // wifi profile's --circleRadius default (7.5m) in any way.
  double circleRadius = 50.0;
  // Numerology 1 (30kHz subcarrier spacing, TS 38.211) -- a common
  // mid-band NR default, shorter slots than numerology 0 (LTE-like
  // 15kHz) without needing mmWave-scale numerology 3/4.
  uint16_t numerology = 1;
  // n78 mid-band (3.3-3.8 GHz), the most widely deployed commercial NR
  // band as of this writing -- a representative "typical 5G deployment"
  // choice, not the paper's own specific requirement (it doesn't pin a
  // band), documented here so it's an explicit, changeable default
  // rather than an unexplained magic number.
  double centralFrequency = 3.5e9;
  double bandwidth = 20e6; // 20 MHz, a typical mid-band NR channel width.
  double gnbTxPowerDbm = 35.0;
  double ueTxPowerDbm = 23.0;

  CommandLine cmd(__FILE__);
  cmd.AddValue("numRobots", "Number of robot stations (plus 1 control_station)", numRobots);
  cmd.AddValue(
      "tapPrefix",
      "Base name for pre-created tap devices (ghost-side, same "
      "15-character Linux interface name limit as the wifi profile).",
      tapPrefix);
  cmd.AddValue("simDuration", "How long to run in real wall-clock seconds", simDuration);
  cmd.AddValue("seed", "ns-3 RngSeedManager seed", seed);
  cmd.AddValue("run", "ns-3 RngSeedManager run number", run);
  cmd.AddValue("layout", "Station placement: only 'circle' is supported for this NR profile", layout);
  cmd.AddValue(
      "circleRadius", "Circle radius in meters (UE placement around the gNB)", circleRadius);
  cmd.AddValue(
      "numerology", "NR numerology (subcarrier spacing = 15kHz * 2^numerology)", numerology);
  cmd.AddValue("centralFrequency", "NR band central frequency in Hz", centralFrequency);
  cmd.AddValue("bandwidth", "NR channel bandwidth in Hz", bandwidth);
  cmd.AddValue("gnbTxPowerDbm", "gNB tx power in dBm", gnbTxPowerDbm);
  cmd.AddValue("ueTxPowerDbm", "UE tx power in dBm", ueTxPowerDbm);
  cmd.Parse(argc, argv);

  if (layout != "circle")
  {
    NS_FATAL_ERROR("--layout must be 'circle' (only supported layout for this NR profile)");
  }
  if (numRobots == 0)
  {
    NS_FATAL_ERROR("numRobots must be positive");
  }

  RngSeedManager::SetSeed(seed);
  RngSeedManager::SetRun(run);

  // Same realtime + checksum requirement as the wifi profile -- TapBridge
  // needs real Linux processes' packets to actually traverse wall-clock
  // time, and needs real (not skipped) checksums.
  GlobalValue::Bind("SimulatorImplementationType", StringValue("ns3::RealtimeSimulatorImpl"));
  GlobalValue::Bind("ChecksumEnabled", BooleanValue(true));

  std::vector<std::string> stationEndpointLabels = {"control_station"};
  for (uint32_t i = 0; i < numRobots; ++i)
  {
    char suffix[24];
    std::snprintf(suffix, sizeof(suffix), "robot_%04u", i);
    stationEndpointLabels.push_back(suffix);
  }
  const uint32_t totalStations = static_cast<uint32_t>(stationEndpointLabels.size());
  g_totalStations = totalStations;

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
          "tap device name '" << name << "' exceeds " << kMaxLinuxInterfaceNameLength
                               << " characters -- shorten --tapPrefix");
    }
  }

  // ---- NR core setup (gNB + EPC), following the nr module's own
  // examples/cttc-nr-demo.cc reference pattern -- see that file (cloned
  // from https://gitlab.com/cttc-lena/nr, branch 5g-lena-v3.0.y) for the
  // canonical version of this sequence. ----
  Ptr<NrPointToPointEpcHelper> epcHelper = CreateObject<NrPointToPointEpcHelper>();
  Ptr<IdealBeamformingHelper> idealBeamformingHelper = CreateObject<IdealBeamformingHelper>();
  Ptr<NrHelper> nrHelper = CreateObject<NrHelper>();
  nrHelper->SetBeamformingHelper(idealBeamformingHelper);
  nrHelper->SetEpcHelper(epcHelper);

  CcBwpCreator ccBwpCreator;
  CcBwpCreator::SimpleOperationBandConf bandConf(
      centralFrequency, bandwidth, 1, BandwidthPartInfo::UMa);
  OperationBandInfo band = ccBwpCreator.CreateOperationBandContiguousCc(bandConf);
  Config::SetDefault("ns3::ThreeGppChannelModel::UpdatePeriod", TimeValue(MilliSeconds(0)));
  nrHelper->SetChannelConditionModelAttribute("UpdatePeriod", TimeValue(MilliSeconds(0)));
  nrHelper->SetPathlossAttribute("ShadowingEnabled", BooleanValue(false));
  nrHelper->InitializeOperationBand(&band);
  BandwidthPartInfoPtrVector allBwps = CcBwpCreator::GetAllBwps({band});

  idealBeamformingHelper->SetAttribute(
      "BeamformingMethod", TypeIdValue(DirectPathBeamforming::GetTypeId()));

  nrHelper->SetUeAntennaAttribute("NumRows", UintegerValue(2));
  nrHelper->SetUeAntennaAttribute("NumColumns", UintegerValue(1));
  nrHelper->SetUeAntennaAttribute(
      "AntennaElement", PointerValue(CreateObject<IsotropicAntennaModel>()));
  nrHelper->SetGnbAntennaAttribute("NumRows", UintegerValue(4));
  nrHelper->SetGnbAntennaAttribute("NumColumns", UintegerValue(2));
  nrHelper->SetGnbAntennaAttribute(
      "AntennaElement", PointerValue(CreateObject<IsotropicAntennaModel>()));

  // ---- Node creation: 1 gNB, totalStations UEs, totalStations ghosts ----
  NodeContainer gnbNodes;
  gnbNodes.Create(1);
  NodeContainer ueNodes;
  ueNodes.Create(totalStations);
  NodeContainer ghostNodes;
  ghostNodes.Create(totalStations);

  // ---- Mobility: gNB fixed at origin (macro-cell height), UEs evenly
  // around a circle -- same "reference diagram" circular placement
  // convention as the wifi profile's --layout=circle. ----
  MobilityHelper gnbMobility;
  gnbMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  gnbMobility.Install(gnbNodes);
  gnbNodes.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(0.0, 0.0, 25.0));

  Ptr<ListPositionAllocator> uePositions = CreateObject<ListPositionAllocator>();
  for (uint32_t i = 0; i < totalStations; ++i)
  {
    const double angle = 2.0 * M_PI * static_cast<double>(i) / static_cast<double>(totalStations);
    uePositions->Add(Vector(circleRadius * std::cos(angle), circleRadius * std::sin(angle), 1.5));
  }
  MobilityHelper ueMobility;
  ueMobility.SetPositionAllocator(uePositions);
  ueMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  ueMobility.Install(ueNodes);

  // Ghost nodes carry no radio device, but ns-3's mobility aggregation is
  // assumed present by some helpers regardless -- co-locate with their UE
  // (physical position is otherwise meaningless for a pure Linux-side
  // routing stand-in).
  Ptr<ListPositionAllocator> ghostPositions = CreateObject<ListPositionAllocator>();
  for (uint32_t i = 0; i < totalStations; ++i)
  {
    const double angle = 2.0 * M_PI * static_cast<double>(i) / static_cast<double>(totalStations);
    ghostPositions->Add(Vector(circleRadius * std::cos(angle), circleRadius * std::sin(angle), 1.5));
  }
  MobilityHelper ghostMobility;
  ghostMobility.SetPositionAllocator(ghostPositions);
  ghostMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  ghostMobility.Install(ghostNodes);

  // ---- Install NR devices ----
  NetDeviceContainer gnbNetDev = nrHelper->InstallGnbDevice(gnbNodes, allBwps);
  NetDeviceContainer ueNetDev = nrHelper->InstallUeDevice(ueNodes, allBwps);

  int64_t randomStream = seed * 1000 + run; // deterministic but distinct per seed/run pair
  randomStream += nrHelper->AssignStreams(gnbNetDev, randomStream);
  randomStream += nrHelper->AssignStreams(ueNetDev, randomStream);

  nrHelper->GetGnbPhy(gnbNetDev.Get(0), 0)->SetAttribute("Numerology", UintegerValue(numerology));
  nrHelper->GetGnbPhy(gnbNetDev.Get(0), 0)->SetAttribute("TxPower", DoubleValue(gnbTxPowerDbm));
  for (uint32_t i = 0; i < ueNetDev.GetN(); ++i)
  {
    nrHelper->GetUePhy(ueNetDev.Get(i), 0)->SetAttribute("TxPower", DoubleValue(ueTxPowerDbm));
  }

  DynamicCast<NrGnbNetDevice>(gnbNetDev.Get(0))->UpdateConfig();
  for (auto it = ueNetDev.Begin(); it != ueNetDev.End(); ++it)
  {
    DynamicCast<NrUeNetDevice>(*it)->UpdateConfig();
  }

  // ---- Internet stack + EPC IP addressing on the UEs ----
  InternetStackHelper internet;
  internet.Install(ueNodes);
  internet.Install(ghostNodes);

  Ipv4InterfaceContainer ueIpIface = epcHelper->AssignUeIpv4Address(ueNetDev);
  Ipv4StaticRoutingHelper ipv4RoutingHelper;
  for (uint32_t i = 0; i < ueNodes.GetN(); ++i)
  {
    Ptr<Ipv4StaticRouting> ueStaticRouting =
        ipv4RoutingHelper.GetStaticRouting(ueNodes.Get(i)->GetObject<Ipv4>());
    // Directly-connected destinations (the ghost-facing p2p link, added
    // below) already take precedence over this via ordinary longest-
    // prefix-match -- this default route only ever fires for genuinely
    // remote destinations (every OTHER endpoint's overlay IP, reached
    // via the gNB/EPC), exactly matching cttc-nr-demo.cc's own UE
    // default-route setup.
    ueStaticRouting->SetDefaultRoute(epcHelper->GetUeDefaultGatewayAddress(), 1);
  }

  nrHelper->AttachToClosestEnb(ueNetDev, gnbNetDev);

  std::vector<Ipv4Address> ueOverlayIp(totalStations);
  for (uint32_t i = 0; i < totalStations; ++i)
  {
    ueOverlayIp[i] = ueIpIface.GetAddress(i);
  }

  // ---- Ghost <-> UE internal link (point-to-point, one dedicated link
  // per endpoint) ----
  PointToPointHelper ghostUeLink;
  ghostUeLink.SetDeviceAttribute("DataRate", DataRateValue(DataRate("1Gbps")));
  ghostUeLink.SetChannelAttribute("Delay", TimeValue(MicroSeconds(1)));

  Ipv4AddressHelper ghostUeAddressHelper;
  for (uint32_t i = 0; i < totalStations; ++i)
  {
    NetDeviceContainer link = ghostUeLink.Install(ghostNodes.Get(i), ueNodes.Get(i));
    // Distinct /30 per endpoint (station index i encoded into the third
    // octet) -- guarantees no collision between endpoints' internal
    // links regardless of totalStations, and stays clear of the EPC's
    // own UE overlay pool (conventionally 7.0.0.0/8).
    std::ostringstream base;
    base << "192.168." << i << ".0";
    ghostUeAddressHelper.SetBase(base.str().c_str(), "255.255.255.252");
    Ipv4InterfaceContainer linkIfaces = ghostUeAddressHelper.Assign(link);

    Ptr<Ipv4> ghostIpv4 = ghostNodes.Get(i)->GetObject<Ipv4>();
    uint32_t ghostLinkIfIndex = ghostIpv4->GetInterfaceForDevice(link.Get(0));
    Ptr<Ipv4StaticRouting> ghostStaticRouting =
        ipv4RoutingHelper.GetStaticRouting(ghostIpv4);
    // Everything that isn't this ghost's own tap-side subnet (added
    // below) goes toward the UE over this link -- looked up by the
    // ACTUAL interface index ghostIpv4 just assigned this device,
    // rather than assuming a fixed device-installation-order number
    // (which would silently break if this file's node/device setup
    // order ever changes).
    ghostStaticRouting->SetDefaultRoute(linkIfaces.GetAddress(1), ghostLinkIfIndex);

    // UE-side: a directly-connected route to this p2p link already
    // exists automatically (ns-3, like any IP stack, auto-installs a
    // route for a device's own subnet on address assignment) -- no
    // extra UE-side route needed for ghost<->UE traffic specifically;
    // the UE's default route (set above) only applies to destinations
    // NOT covered by a more specific route, i.e. every OTHER endpoint.
  }

  // ---- Ghost <-> real Docker container (TapBridge on a CSMA device) ----
  // CSMA, not the ghost-UE point-to-point link, because TapBridge's
  // UseLocal mode needs SupportsSendFrom()==true -- confirmed via ns-3
  // core's own tap-csma-virtual-machine.cc example using exactly this
  // device type. PointToPointNetDevice does NOT support it (a strict
  // 2-endpoint pipe has no "spoof the source" concept to support in the
  // first place); only a shared/broadcast-style device like Csma does.
  CsmaHelper ghostTapLink;
  ghostTapLink.SetChannelAttribute("DataRate", DataRateValue(DataRate("1Gbps")));
  ghostTapLink.SetChannelAttribute("Delay", TimeValue(MicroSeconds(1)));

  // Arbitrary fixed subnet for the ghost<->real-container link-local
  // hop, per endpoint -- deliberately OUTSIDE the EPC's own 7.0.0.0/8
  // overlay pool so the two address spaces can never collide. The real
  // container's actual "5G identity" (ueOverlayIp[i]) is layered on TOP
  // of this link via an explicit source-routed default route configured
  // on the Linux side (see the orchestrator script, not this file) --
  // this ns-3 program only needs to know that packets ADDRESSED to
  // ueOverlayIp[i] should egress via this station's own tap-facing CSMA
  // device, which the explicit AddHostRouteTo below guarantees
  // regardless of what subnet that CSMA device's own address is on (no
  // NAT module needed -- confirmed not present in this image's ns-3
  // build via `pkg-config --list-all`).
  Ipv4AddressHelper ghostTapAddressHelper;

  std::cout << "FLEETQOX_NR_MAPPING station_index,endpoint,tap_device,ue_overlay_ip,"
               "ghost_link_local_ip\n";
  TapBridgeHelper tapBridge;
  tapBridge.SetAttribute("Mode", StringValue("UseLocal"));
  for (uint32_t i = 0; i < totalStations; ++i)
  {
    // TWO CsmaNetDevices on the SAME node, on the SAME shared channel --
    // NOT one. A real Ethernet NIC never feeds its own transmissions
    // back into its own receiver (that's what half-duplex CSMA models),
    // so a single device here would be a dead end: TapBridge injects the
    // container's frames via SendFrom() (a TRANSMIT onto the channel),
    // and if that same device were also the one carrying this ghost's
    // own Ipv4/Arp stack, that stack would NEVER see the frame it just
    // "transmitted" -- confirmed by a real run: the ghost's ARP never
    // answered the container's request (`ip neigh` showed FAILED), and
    // the tap's RX byte counter stayed at exactly 0 for the entire test
    // regardless of how much the container retried. Splitting into two
    // peer devices on one channel -- ghostTapDevice (TapBridge, NO IP)
    // and ghostRoutedDevice (the container's actual gateway IP, runs
    // ARP/routing) -- makes them genuine peers that DO see each other's
    // traffic, exactly like a NIC's own port talking to a bridge port
    // sitting on the same physical segment.
    // Built via two explicit single-node Install() calls onto the SAME
    // channel object, NOT NodeContainer(node, node) -- that doubled-up
    // form reproducibly crashed TapBridge's tap-creator helper with
    // SIGSEGV (confirmed by 2 separate runs hitting the exact same
    // crash), most likely because CsmaHelper::Install(NodeContainer) was
    // never exercised against a container listing the same node twice.
    // Install(Ptr<Node>, Ptr<Channel>) is the standard, widely-used
    // pattern for adding an additional device to an already-existing
    // channel, so this sidesteps that untested code path entirely.
    NetDeviceContainer firstTapLinkDevice = ghostTapLink.Install(NodeContainer(ghostNodes.Get(i)));
    Ptr<NetDevice> ghostTapDevice = firstTapLinkDevice.Get(0);
    Ptr<CsmaChannel> ghostTapChannel = DynamicCast<CsmaChannel>(ghostTapDevice->GetChannel());
    Ptr<NetDevice> ghostRoutedDevice =
        ghostTapLink.Install(ghostNodes.Get(i), ghostTapChannel).Get(0);

    std::ostringstream tapBase;
    tapBase << "172.16." << i << ".0";
    ghostTapAddressHelper.SetBase(tapBase.str().c_str(), "255.255.255.0");
    Ipv4InterfaceContainer tapIface =
        ghostTapAddressHelper.Assign(NetDeviceContainer(ghostRoutedDevice));
    const Ipv4Address ghostLinkLocalIp = tapIface.GetAddress(0);
    // ghostTapDevice ALSO needs a valid Ipv4 interface -- not because
    // anything of ours actually uses this address (Mode=UseLocal never
    // applies the tap-creator's discovered IP to anything real; the
    // container's actual identity is configured entirely on the Linux
    // side), but because TapBridge::CreateTap() unconditionally calls
    // ipv4->GetInterfaceForDevice(bridgedDevice) whenever the NODE has
    // an Ipv4 object at all (it does, for ghostRoutedDevice's sake),
    // with NO check that THIS SPECIFIC device has an interface. Without
    // one, GetInterfaceForDevice() returns -1 (0xFFFFFFFF as uint32_t),
    // which then indexes straight into Ipv4L3Protocol's internal
    // interface array -- confirmed via ns-3.41's own
    // src/tap-bridge/model/tap-bridge.cc (the "if (ipv4)" branch around
    // GetNAddresses(index)/GetAddress(index, 0)) as the exact mechanism
    // behind a real, reproduced SIGSEGV in the tap-creator helper
    // process the first time this file split into two devices without
    // giving the tap-facing one an address of its own. Reuses the same
    // helper (SetBase() not called again) so this lands on the next free
    // address in the same /24 -- distinct from ghostRoutedDevice's,
    // never collides, never actually routed anywhere.
    ghostTapAddressHelper.Assign(NetDeviceContainer(ghostTapDevice));

    Ptr<Ipv4> ghostIpv4 = ghostNodes.Get(i)->GetObject<Ipv4>();
    uint32_t ghostRoutedIfIndex = ghostIpv4->GetInterfaceForDevice(ghostRoutedDevice);
    Ptr<Ipv4StaticRouting> ghostStaticRouting = ipv4RoutingHelper.GetStaticRouting(ghostIpv4);
    // The one piece of routing state THIS station's ghost needs beyond
    // its default route (added above, pointing at the UE): explicitly
    // send anything addressed to ITS OWN endpoint's overlay IP back out
    // the tap-side interface, not toward the UE -- relevant when another
    // endpoint's downlink traffic for THIS ip arrives via the UE link
    // and needs to reach the real container, rather than looping back
    // toward the UE.
    ghostStaticRouting->AddHostRouteTo(ueOverlayIp[i], ghostRoutedIfIndex);

    tapBridge.SetAttribute("DeviceName", StringValue(stationTapNames[i]));
    tapBridge.Install(ghostNodes.Get(i), ghostTapDevice);

    std::cout << "FLEETQOX_NR_MAPPING " << i << "," << stationEndpointLabels[i] << ","
              << stationTapNames[i] << "," << ueOverlayIp[i] << "," << ghostLinkLocalIp << "\n";
  }
  std::cout.flush();

  Simulator::Schedule(Seconds(5.0), &PrintNrStats);

  Simulator::Stop(Seconds(simDuration));
  Simulator::Run();
  PrintNrStats();
  Simulator::Destroy();

  return 0;
}
