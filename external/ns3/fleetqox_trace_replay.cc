// FleetQoX trace replay starter for ns-3.
//
// Copy this file into an ns-3 workspace under scratch/ and run it with:
//
//   ./ns3 run "scratch/fleetqox_trace_replay --trace=/path/to/trace.csv"
//
// Supports shared CSMA, a routed point-to-point star, single-AP Wi-Fi, and a
// two-AP bridged roaming topology.

#include "ns3/applications-module.h"
#include "ns3/bridge-module.h"
#include "ns3/core-module.h"
#include "ns3/csma-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/wifi-module.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("FleetQoxTraceReplay");

class EventIdTag : public Tag
{
public:
  EventIdTag() = default;
  explicit EventIdTag(uint32_t id) : m_id(id) {}

  static TypeId GetTypeId()
  {
    static TypeId tid = TypeId("EventIdTag")
                            .SetParent<Tag>()
                            .AddConstructor<EventIdTag>();
    return tid;
  }

  TypeId GetInstanceTypeId() const override { return GetTypeId(); }
  uint32_t GetSerializedSize() const override { return 4; }
  void Serialize(TagBuffer i) const override { i.WriteU32(m_id); }
  void Deserialize(TagBuffer i) override { m_id = i.ReadU32(); }
  void Print(std::ostream& os) const override { os << m_id; }
  uint32_t Get() const { return m_id; }

private:
  uint32_t m_id = 0;
};

struct TraceEvent
{
  uint32_t id = 0;
  double timestampMs = 0.0;
  std::string policy;
  std::string flowId;
  std::string flowClass;
  std::string src;
  std::string dst;
  uint32_t bytes = 0;
  double deadlineMs = 0.0;
  double utility = 0.0;
};

struct PolicyStats
{
  uint64_t tx = 0;
  uint64_t rx = 0;
  uint64_t bytes = 0;
  uint64_t deadlineMiss = 0;
  double utilityDelivered = 0.0;
  std::vector<double> latencyMs;
};

static std::vector<TraceEvent> g_events;
static std::map<std::string, uint32_t> g_endpointToNode;
static std::map<uint32_t, Ipv4Address> g_nodeToAddress;
static std::map<std::string, Ptr<Socket>> g_sourceSockets;
static std::map<std::string, PolicyStats> g_stats;
static uint16_t g_port = 9100;
static double g_startOffsetMs = 0.0;
static uint64_t g_associationEvents = 0;
static uint64_t g_disassociationEvents = 0;
static uint64_t g_handoffEvents = 0;
static std::map<uint32_t, Mac48Address> g_lastAccessPoint;

static void
RecordAssociation(uint32_t nodeIndex, Mac48Address accessPoint)
{
  g_associationEvents++;
  auto previous = g_lastAccessPoint.find(nodeIndex);
  if (previous != g_lastAccessPoint.end() && previous->second != accessPoint)
  {
    g_handoffEvents++;
  }
  g_lastAccessPoint[nodeIndex] = accessPoint;
}

static void
RecordDisassociation(uint32_t nodeIndex, Mac48Address accessPoint)
{
  (void)nodeIndex;
  (void)accessPoint;
  g_disassociationEvents++;
}

static std::vector<std::string>
SplitCsvLine(const std::string& line)
{
  std::vector<std::string> out;
  std::stringstream ss(line);
  std::string cell;
  while (std::getline(ss, cell, ','))
  {
    out.push_back(cell);
  }
  return out;
}

static uint32_t
Column(const std::vector<std::string>& header, const std::string& name)
{
  auto it = std::find(header.begin(), header.end(), name);
  if (it == header.end())
  {
    NS_FATAL_ERROR("missing CSV column: " << name);
  }
  return static_cast<uint32_t>(std::distance(header.begin(), it));
}

static double
ToDouble(const std::string& value)
{
  return std::stod(value);
}

static uint32_t
ToUint(const std::string& value)
{
  return static_cast<uint32_t>(std::stoul(value));
}

static std::vector<TraceEvent>
LoadTrace(const std::string& path)
{
  std::ifstream input(path);
  if (!input)
  {
    NS_FATAL_ERROR("could not open trace: " << path);
  }

  std::string headerLine;
  std::getline(input, headerLine);
  auto header = SplitCsvLine(headerLine);

  const auto eventId = Column(header, "event_id");
  const auto timestamp = Column(header, "timestamp_ms");
  const auto policy = Column(header, "policy");
  const auto flowId = Column(header, "flow_id");
  const auto flowClass = Column(header, "flow_class");
  const auto src = Column(header, "src");
  const auto dst = Column(header, "dst");
  const auto bytes = Column(header, "bytes");
  const auto deadline = Column(header, "deadline_ms");
  const auto utility = Column(header, "semantic_utility");

  std::vector<TraceEvent> events;
  std::string line;
  while (std::getline(input, line))
  {
    if (line.empty())
    {
      continue;
    }
    auto row = SplitCsvLine(line);
    TraceEvent event;
    event.id = ToUint(row[eventId]);
    event.timestampMs = ToDouble(row[timestamp]);
    event.policy = row[policy];
    event.flowId = row[flowId];
    event.flowClass = row[flowClass];
    event.src = row[src];
    event.dst = row[dst];
    event.bytes = ToUint(row[bytes]);
    event.deadlineMs = ToDouble(row[deadline]);
    event.utility = ToDouble(row[utility]);
    events.push_back(event);
  }

  return events;
}

static void
ReceivePacket(Ptr<Socket> socket)
{
  Ptr<Packet> packet;
  Address from;
  while ((packet = socket->RecvFrom(from)))
  {
    EventIdTag tag;
    if (!packet->PeekPacketTag(tag))
    {
      continue;
    }
    const uint32_t id = tag.Get();
    if (id >= g_events.size())
    {
      continue;
    }
    const TraceEvent& event = g_events[id];
    auto& stats = g_stats[event.policy];
    stats.rx++;
    stats.bytes += packet->GetSize();

    const double latencyMs =
      Simulator::Now().GetMilliSeconds() - (event.timestampMs + g_startOffsetMs);
    stats.latencyMs.push_back(latencyMs);
    if (latencyMs > event.deadlineMs)
    {
      stats.deadlineMiss++;
    }
    stats.utilityDelivered += event.utility;
  }
}

// Maps a FleetQoX flow_class to an 802.11e/WMM User Priority (0-7), using
// the standard WMM Access Category mapping (AC_VO=6-7, AC_VI=4-5,
// AC_BE=0/3, AC_BK=1-2). Only takes effect when --wifiQos enables
// QosSupported on the wifi MAC; ns-3's non-QoS Txop ignores the packet's
// SocketPriorityTag entirely. SAFETY/CONTROL (the tightest-deadline,
// highest-priority flows) map to AC_VO, matching how a real WMM-enabled
// deployment would prioritize them over best-effort/background traffic.
static uint8_t
UserPriorityForFlowClass(const std::string& flowClass)
{
  if (flowClass == "safety" || flowClass == "control")
  {
    return 6; // AC_VO
  }
  if (flowClass == "coordination" || flowClass == "state")
  {
    return 5; // AC_VI
  }
  if (flowClass == "human_qoe")
  {
    return 4; // AC_VI
  }
  if (flowClass == "perception")
  {
    return 0; // AC_BE
  }
  return 1; // AC_BK (debug, bulk, unknown)
}

static Ptr<Socket>
SourceSocket(const std::string& src, Ptr<Node> node)
{
  auto it = g_sourceSockets.find(src);
  if (it != g_sourceSockets.end())
  {
    return it->second;
  }
  Ptr<Socket> socket = Socket::CreateSocket(node, UdpSocketFactory::GetTypeId());
  g_sourceSockets[src] = socket;
  return socket;
}

static void
SendEvent(uint32_t eventIndex, NodeContainer nodes)
{
  const TraceEvent& event = g_events[eventIndex];
  Ptr<Node> srcNode = nodes.Get(g_endpointToNode[event.src]);
  Ptr<Socket> socket = SourceSocket(event.src, srcNode);
  const Ipv4Address dstAddress = g_nodeToAddress[g_endpointToNode[event.dst]];

  Ptr<Packet> packet = Create<Packet>(std::max<uint32_t>(event.bytes, 1));
  packet->AddPacketTag(EventIdTag(eventIndex));
  socket->SetPriority(UserPriorityForFlowClass(event.flowClass));
  socket->SendTo(packet, 0, InetSocketAddress(dstAddress, g_port));
  g_stats[event.policy].tx++;
}

static double
Percentile(std::vector<double> values, double pct)
{
  if (values.empty())
  {
    return 0.0;
  }
  std::sort(values.begin(), values.end());
  const auto index = static_cast<size_t>(
      std::min<double>(values.size() - 1, std::ceil((pct / 100.0) * values.size()) - 1));
  return values[index];
}

static void
PrintSummary()
{
  std::cout << "roaming_metrics,associations,disassociations,handoffs\n";
  std::cout << "roaming_metrics," << g_associationEvents << ","
            << g_disassociationEvents << "," << g_handoffEvents << "\n";
  std::cout << "policy,tx,rx,bytes,deadline_miss_ratio,p50_ms,p99_ms,utility\n";
  for (auto& [policy, stats] : g_stats)
  {
    const double missRatio = stats.rx == 0 ? 0.0 : static_cast<double>(stats.deadlineMiss) / stats.rx;
    std::cout << policy << "," << stats.tx << "," << stats.rx << "," << stats.bytes << ","
              << missRatio << "," << Percentile(stats.latencyMs, 50.0) << ","
              << Percentile(stats.latencyMs, 99.0) << "," << stats.utilityDelivered << "\n";
  }
}

int
main(int argc, char* argv[])
{
  std::string tracePath;
  std::string dataRate = "54Mbps";
  std::string delay = "2ms";
  std::string topology = "csma";
  std::string wifiMode = "ErpOfdmRate54Mbps";
  double errorRate = 0.0;
  double mobilitySpeed = 0.0;
  double stationSpacing = 3.0;
  double accessPointSpacing = 20.0;
  double wifiRange = 12.0;
  double warmupMs = 0.0;
  uint32_t seed = 1;
  uint64_t run = 1;
  bool matchedBackoffRng = false;
  // Explicit AssignStreams() values live in a reserved namespace disjoint
  // from ns-3's auto-increment counter (RandomVariableStream::SetStream()
  // maps them to 2^63 + stream internally), so there is no collision risk
  // from keeping this small. Keep it 0 by default so a station's stream
  // index equals its plain g_endpointToNode index -- the OMNeT++ side
  // (MatchedMrg32k3aRng's rngId / physical RNG slot count) mirrors this
  // exact convention and would need updating if this default changes.
  uint64_t matchedBackoffRngBase = 0;
  bool wifiQos = false;

  CommandLine cmd(__FILE__);
  cmd.AddValue("trace", "FleetQoX simulator CSV trace", tracePath);
  cmd.AddValue("dataRate", "CSMA data rate", dataRate);
  cmd.AddValue("delay", "CSMA channel delay", delay);
  cmd.AddValue(
      "topology", "Network topology: csma, p2p_star, wifi, or wifi_roaming", topology);
  cmd.AddValue("wifiMode", "802.11g station data/control mode", wifiMode);
  cmd.AddValue("errorRate", "Independent receive packet error probability", errorRate);
  cmd.AddValue("mobilitySpeed", "Station speed in meters/second for Wi-Fi", mobilitySpeed);
  cmd.AddValue("stationSpacing", "Initial Wi-Fi station grid spacing in meters", stationSpacing);
  cmd.AddValue("accessPointSpacing", "Distance between roaming access points", accessPointSpacing);
  cmd.AddValue("wifiRange", "Maximum radio range for deterministic roaming", wifiRange);
  cmd.AddValue("warmupMs", "Delay trace transmission start for association warmup", warmupMs);
  cmd.AddValue("seed", "ns-3 RNG seed", seed);
  cmd.AddValue("run", "ns-3 RNG run number", run);
  cmd.AddValue(
      "matchedBackoffRng",
      "Assign deterministic per-station Txop backoff RNG streams "
      "(station i -> matchedBackoffRngBase + i, AP j -> "
      "matchedBackoffRngBase + stationCount + j) instead of ns-3's "
      "global auto-increment counter, so the mapping can be replicated "
      "by another simulator",
      matchedBackoffRng);
  cmd.AddValue(
      "matchedBackoffRngBase",
      "Base stream index used when matchedBackoffRng is enabled",
      matchedBackoffRngBase);
  cmd.AddValue(
      "wifiQos",
      "Enable 802.11e/WMM QoS (EDCA) on the wifi MAC and tag each packet's "
      "Access Category from its flow_class (safety/control -> AC_VO, "
      "coordination/state -> AC_VI, human_qoe -> AC_VI, perception -> "
      "AC_BE, debug/bulk -> AC_BK), instead of a single best-effort DCF "
      "queue for all traffic",
      wifiQos);
  cmd.Parse(argc, argv);

  if (tracePath.empty())
  {
    NS_FATAL_ERROR("missing --trace=/path/to/trace.csv");
  }
  if (errorRate < 0.0 || errorRate > 1.0)
  {
    NS_FATAL_ERROR("errorRate must be in [0,1]");
  }
  if (topology != "csma" && topology != "p2p_star" && topology != "wifi" &&
      topology != "wifi_roaming")
  {
    NS_FATAL_ERROR("topology must be csma, p2p_star, wifi, or wifi_roaming");
  }
  if (mobilitySpeed < 0.0 || stationSpacing <= 0.0 || accessPointSpacing <= 0.0 ||
      wifiRange <= 0.0 || warmupMs < 0.0)
  {
    NS_FATAL_ERROR("mobilitySpeed must be nonnegative and stationSpacing positive");
  }
  if (matchedBackoffRng && wifiQos)
  {
    // assignBackoffStream() below assumes the non-QoS single-Txop MAC
    // ("Txop" attribute); QoS mode exposes VO_Txop/VI_Txop/BE_Txop/BK_Txop
    // instead. These two experimental flags haven't been combined/tested
    // together yet -- fail loudly instead of silently mis-assigning streams.
    NS_FATAL_ERROR("matchedBackoffRng and wifiQos are not yet supported together");
  }
  RngSeedManager::SetSeed(seed);
  RngSeedManager::SetRun(run);
  g_startOffsetMs = warmupMs;

  g_events = LoadTrace(tracePath);
  for (const auto& event : g_events)
  {
    if (!g_endpointToNode.count(event.src))
    {
      g_endpointToNode[event.src] = static_cast<uint32_t>(g_endpointToNode.size());
    }
    if (!g_endpointToNode.count(event.dst))
    {
      g_endpointToNode[event.dst] = static_cast<uint32_t>(g_endpointToNode.size());
    }
  }

  NodeContainer nodes;
  nodes.Create(g_endpointToNode.size());
  Ipv4InterfaceContainer interfaces;
  if (topology == "csma")
  {
    CsmaHelper csma;
    csma.SetChannelAttribute("DataRate", StringValue(dataRate));
    csma.SetChannelAttribute("Delay", StringValue(delay));
    NetDeviceContainer devices = csma.Install(nodes);
    for (uint32_t i = 0; i < devices.GetN(); ++i)
    {
      Ptr<RateErrorModel> error = CreateObject<RateErrorModel>();
      error->SetAttribute("ErrorRate", DoubleValue(errorRate));
      error->SetAttribute("ErrorUnit", EnumValue(RateErrorModel::ERROR_UNIT_PACKET));
      devices.Get(i)->SetAttribute("ReceiveErrorModel", PointerValue(error));
    }
    InternetStackHelper internet;
    internet.Install(nodes);
    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.10.0.0", "255.255.0.0");
    interfaces = ipv4.Assign(devices);
  }
  else if (topology == "p2p_star")
  {
    NodeContainer router;
    router.Create(1);
    InternetStackHelper internet;
    internet.Install(nodes);
    internet.Install(router);
    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.30.0.0", "255.255.255.252");
    for (uint32_t i = 0; i < nodes.GetN(); ++i)
    {
      NodeContainer linkNodes;
      linkNodes.Add(nodes.Get(i));
      linkNodes.Add(router.Get(0));
      PointToPointHelper link;
      link.SetDeviceAttribute("DataRate", StringValue(dataRate));
      link.SetChannelAttribute("Delay", StringValue(delay));
      NetDeviceContainer devices = link.Install(linkNodes);
      for (uint32_t deviceIndex = 0; deviceIndex < devices.GetN(); ++deviceIndex)
      {
        Ptr<RateErrorModel> error = CreateObject<RateErrorModel>();
        error->SetAttribute("ErrorRate", DoubleValue(errorRate));
        error->SetAttribute("ErrorUnit", EnumValue(RateErrorModel::ERROR_UNIT_PACKET));
        devices.Get(deviceIndex)->SetAttribute("ReceiveErrorModel", PointerValue(error));
      }
      Ipv4InterfaceContainer linkInterfaces = ipv4.Assign(devices);
      interfaces.Add(linkInterfaces.Get(0));
      ipv4.NewNetwork();
    }
    Ipv4GlobalRoutingHelper::PopulateRoutingTables();
  }
  else
  {
    const bool roaming = topology == "wifi_roaming";
    NodeContainer accessPoints;
    accessPoints.Create(roaming ? 2 : 1);
    YansWifiChannelHelper channel;
    if (roaming)
    {
      channel.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
      channel.AddPropagationLoss(
          "ns3::RangePropagationLossModel", "MaxRange", DoubleValue(wifiRange));
    }
    else
    {
      channel = YansWifiChannelHelper::Default();
    }
    YansWifiPhyHelper phy;
    phy.SetChannel(channel.Create());
    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211g);
    wifi.SetRemoteStationManager(
        "ns3::ConstantRateWifiManager",
        "DataMode", StringValue(wifiMode),
        "ControlMode", StringValue(wifiMode));
    WifiMacHelper mac;
    Ssid ssid = Ssid("fleetqox-wifi");
    mac.SetType(
        "ns3::StaWifiMac",
        "Ssid", SsidValue(ssid),
        "ActiveProbing", BooleanValue(false),
        "QosSupported", BooleanValue(wifiQos));
    NetDeviceContainer stationDevices = wifi.Install(phy, mac, nodes);
    mac.SetType(
        "ns3::ApWifiMac",
        "Ssid", SsidValue(ssid),
        "QosSupported", BooleanValue(wifiQos));
    NetDeviceContainer accessPointDevices = wifi.Install(phy, mac, accessPoints);

    if (matchedBackoffRng)
    {
      // Bypass ns-3's global auto-increment stream counter (order-dependent
      // on unrelated object construction, e.g. unused IPv6 DAD timers) and
      // assign the Txop backoff RNG (the one driving CSMA/CA contention
      // decisions, see Txop::GetBackoffSlots) an explicit, closed-form
      // stream index per device: station i -> base + i, AP j -> base +
      // stationCount + j. This formula is reproduced in the INET side so
      // both simulators consume matching MRG32k3a substreams per station.
      auto assignBackoffStream = [](Ptr<NetDevice> device, int64_t stream) {
        Ptr<WifiMac> wifiMac = DynamicCast<WifiNetDevice>(device)->GetMac();
        PointerValue ptr;
        wifiMac->GetAttribute("Txop", ptr);
        Ptr<Txop> txop = ptr.Get<Txop>();
        txop->AssignStreams(stream);
      };
      for (uint32_t i = 0; i < stationDevices.GetN(); ++i)
      {
        assignBackoffStream(
            stationDevices.Get(i), static_cast<int64_t>(matchedBackoffRngBase + i));
      }
      for (uint32_t i = 0; i < accessPointDevices.GetN(); ++i)
      {
        assignBackoffStream(
            accessPointDevices.Get(i),
            static_cast<int64_t>(matchedBackoffRngBase + stationDevices.GetN() + i));
      }
    }

    for (uint32_t i = 0; i < stationDevices.GetN(); ++i)
    {
      Ptr<StaWifiMac> stationMac = DynamicCast<StaWifiMac>(
          stationDevices.Get(i)->GetObject<WifiNetDevice>()->GetMac());
      stationMac->TraceConnectWithoutContext(
          "Assoc", MakeBoundCallback(&RecordAssociation, i));
      stationMac->TraceConnectWithoutContext(
          "DeAssoc", MakeBoundCallback(&RecordDisassociation, i));
    }

    MobilityHelper stationMobility;
    if (roaming)
    {
      Ptr<ListPositionAllocator> positions = CreateObject<ListPositionAllocator>();
      for (uint32_t i = 0; i < nodes.GetN(); ++i)
      {
        positions->Add(Vector(5.0, static_cast<double>(i % 4), 0.0));
      }
      stationMobility.SetPositionAllocator(positions);
    }
    else
    {
      stationMobility.SetPositionAllocator(
          "ns3::GridPositionAllocator",
          "MinX", DoubleValue(0.0),
          "MinY", DoubleValue(0.0),
          "DeltaX", DoubleValue(stationSpacing),
          "DeltaY", DoubleValue(stationSpacing),
          "GridWidth", UintegerValue(
            static_cast<uint32_t>(std::ceil(std::sqrt(nodes.GetN())))),
          "LayoutType", StringValue("RowFirst"));
    }
    stationMobility.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
    stationMobility.Install(nodes);
    for (uint32_t i = 0; i < nodes.GetN(); ++i)
    {
      Ptr<ConstantVelocityMobilityModel> model =
        nodes.Get(i)->GetObject<ConstantVelocityMobilityModel>();
      const double direction = roaming ? 1.0 : ((i % 2 == 0) ? 1.0 : -1.0);
      model->SetVelocity(Vector(direction * mobilitySpeed, 0.0, 0.0));
    }
    MobilityHelper accessPointMobility;
    accessPointMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    accessPointMobility.Install(accessPoints);
    if (roaming)
    {
      accessPoints.Get(0)->GetObject<MobilityModel>()->SetPosition(Vector(0.0, 1.5, 0.0));
      accessPoints.Get(1)->GetObject<MobilityModel>()->SetPosition(
          Vector(accessPointSpacing, 1.5, 0.0));
      CsmaHelper backbone;
      backbone.SetChannelAttribute("DataRate", StringValue("1Gbps"));
      backbone.SetChannelAttribute("Delay", StringValue("0.1ms"));
      NetDeviceContainer backhaulDevices = backbone.Install(accessPoints);
      BridgeHelper bridge;
      for (uint32_t i = 0; i < accessPoints.GetN(); ++i)
      {
        NetDeviceContainer ports;
        ports.Add(accessPointDevices.Get(i));
        ports.Add(backhaulDevices.Get(i));
        bridge.Install(accessPoints.Get(i), ports);
      }
    }
    else
    {
      const double gridWidth = std::ceil(std::sqrt(nodes.GetN())) * stationSpacing;
      accessPoints.Get(0)->GetObject<MobilityModel>()->SetPosition(
          Vector(gridWidth / 2.0, gridWidth / 2.0, 0.0));
    }

    InternetStackHelper internet;
    internet.Install(nodes);
    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.20.0.0", "255.255.0.0");
    interfaces = ipv4.Assign(stationDevices);
  }

  for (const auto& [endpoint, nodeIndex] : g_endpointToNode)
  {
    g_nodeToAddress[nodeIndex] = interfaces.GetAddress(nodeIndex);
    Ptr<Socket> sink = Socket::CreateSocket(nodes.Get(nodeIndex), UdpSocketFactory::GetTypeId());
    sink->Bind(InetSocketAddress(Ipv4Address::GetAny(), g_port));
    sink->SetRecvCallback(MakeCallback(&ReceivePacket));
  }

  for (uint32_t i = 0; i < g_events.size(); ++i)
  {
    Simulator::Schedule(
        MilliSeconds(g_events[i].timestampMs + g_startOffsetMs), &SendEvent, i, nodes);
  }

  const double stopMs = g_events.empty() ? g_startOffsetMs :
    g_events.back().timestampMs + g_startOffsetMs + 10'000.0;
  Simulator::Stop(MilliSeconds(stopMs));
  Simulator::Run();
  PrintSummary();
  Simulator::Destroy();
  return 0;
}
