#include "TraceDrivenUdpApp.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "inet/applications/base/ApplicationPacket_m.h"
#include "inet/common/InitStages.h"
#include "inet/common/TimeTag_m.h"
#include "inet/common/packet/Packet.h"
#include "inet/linklayer/common/UserPriorityTag_m.h"
#include "inet/networklayer/common/L3AddressResolver.h"

namespace fleetqox_omnetpp {
namespace {

struct TraceEvent
{
  std::uint64_t event_id{0};
  double timestamp_ms{0.0};
  std::string policy;
  std::string flow_class;
  std::string source;
  std::string destination;
  std::uint32_t bytes{0};
  double deadline_ms{0.0};
  double semantic_utility{0.0};
};

// Maps a FleetQoX flow_class to an 802.11e/WMM User Priority (0-7), using
// the standard WMM Access Category mapping (AC_VO=6-7, AC_VI=4-5,
// AC_BE=0/3, AC_BK=1-2). Mirrors
// external/ns3/fleetqox_trace_replay.cc's UserPriorityForFlowClass() so
// both simulators prioritize the same flow classes the same way when QoS
// is enabled (wlan mac.qosStation = true); with qosStation = false this
// tag is simply ignored by INET's non-QoS Dcf.
int userPriorityForFlowClass(const std::string & flowClass)
{
  if (flowClass == "safety" || flowClass == "control") {
    return 6; // AC_VO
  }
  if (flowClass == "coordination" || flowClass == "state") {
    return 5; // AC_VI
  }
  if (flowClass == "human_qoe") {
    return 4; // AC_VI
  }
  if (flowClass == "perception") {
    return 0; // AC_BE
  }
  return 1; // AC_BK (debug, bulk, unknown)
}

struct PolicyStats
{
  std::uint64_t tx{0};
  std::uint64_t rx{0};
  std::uint64_t bytes{0};
  std::uint64_t deadline_misses{0};
  double utility_delivered{0.0};
  std::vector<double> latency_ms;
};

struct TraceStore
{
  std::string path;
  std::vector<TraceEvent> events;
  std::map<std::string, PolicyStats> stats;
  // Keyed by (policy, flow_class): lets --wifi-qos evaluation see whether
  // EDCA prioritization shifted latency/miss-ratio between flow classes
  // within a policy, instead of only the aggregate-across-all-classes view
  // above. Mirrors external/ns3/fleetqox_trace_replay.cc's g_flowClassStats.
  std::map<std::pair<std::string, std::string>, PolicyStats> flow_class_stats;
};

TraceStore & traceStore()
{
  static TraceStore store;
  return store;
}

std::vector<std::string> splitCsvLine(const std::string & line)
{
  std::vector<std::string> cells;
  std::stringstream stream(line);
  std::string cell;
  while (std::getline(stream, cell, ',')) {
    cells.push_back(cell);
  }
  if (!line.empty() && line.back() == ',') {
    cells.emplace_back();
  }
  return cells;
}

std::size_t requiredColumn(
  const std::unordered_map<std::string, std::size_t> & columns,
  const std::string & name)
{
  const auto iterator = columns.find(name);
  if (iterator == columns.end()) {
    throw ::omnetpp::cRuntimeError("missing FleetQoX CSV column: %s", name.c_str());
  }
  return iterator->second;
}

void loadTraceOnce(const std::string & path)
{
  TraceStore & store = traceStore();
  if (!store.events.empty()) {
    if (store.path != path) {
      throw ::omnetpp::cRuntimeError(
              "one OMNeT++ process cannot mix FleetQoX traces '%s' and '%s'",
              store.path.c_str(), path.c_str());
    }
    return;
  }

  std::ifstream input(path);
  if (!input) {
    throw ::omnetpp::cRuntimeError("could not open FleetQoX trace: %s", path.c_str());
  }

  std::string line;
  if (!std::getline(input, line)) {
    throw ::omnetpp::cRuntimeError("FleetQoX trace is empty: %s", path.c_str());
  }
  const auto header = splitCsvLine(line);
  std::unordered_map<std::string, std::size_t> columns;
  for (std::size_t index = 0; index < header.size(); ++index) {
    columns.emplace(header[index], index);
  }

  const auto event_id = requiredColumn(columns, "event_id");
  const auto timestamp = requiredColumn(columns, "timestamp_ms");
  const auto policy = requiredColumn(columns, "policy");
  const auto flow_class = requiredColumn(columns, "flow_class");
  const auto source = requiredColumn(columns, "src");
  const auto destination = requiredColumn(columns, "dst");
  const auto bytes = requiredColumn(columns, "bytes");
  const auto deadline = requiredColumn(columns, "deadline_ms");
  const auto utility = requiredColumn(columns, "semantic_utility");
  const std::size_t last_required = std::max(
    {event_id, timestamp, policy, flow_class, source, destination, bytes, deadline, utility});

  while (std::getline(input, line)) {
    if (line.empty()) {
      continue;
    }
    const auto row = splitCsvLine(line);
    if (row.size() <= last_required) {
      throw ::omnetpp::cRuntimeError("short FleetQoX CSV row in %s", path.c_str());
    }
    TraceEvent event;
    event.event_id = std::stoull(row[event_id]);
    event.timestamp_ms = std::stod(row[timestamp]);
    event.policy = row[policy];
    event.flow_class = row[flow_class];
    event.source = row[source];
    event.destination = row[destination];
    event.bytes = static_cast<std::uint32_t>(std::stoul(row[bytes]));
    event.deadline_ms = std::stod(row[deadline]);
    event.semantic_utility = std::stod(row[utility]);
    if (event.timestamp_ms < 0.0 || event.deadline_ms < 0.0 || event.policy.empty() ||
      event.source.empty() || event.destination.empty())
    {
      throw ::omnetpp::cRuntimeError("invalid FleetQoX CSV event in %s", path.c_str());
    }
    store.stats.try_emplace(event.policy);
    store.events.push_back(std::move(event));
  }
  if (store.events.empty()) {
    throw ::omnetpp::cRuntimeError("FleetQoX trace has no packet rows: %s", path.c_str());
  }
  store.path = path;
}

std::string endpointForModule(const TraceDrivenUdpApp & app, const char * configured)
{
  if (configured != nullptr && configured[0] != '\0') {
    return configured;
  }
  const ::omnetpp::cModule * host = app.getParentModule();
  if (host == nullptr || std::string(host->getName()) != "robot" || host->getIndex() < 0) {
    throw ::omnetpp::cRuntimeError(
            "endpointName may be empty only for a robot[] host: %s", app.getFullPath().c_str());
  }
  std::ostringstream endpoint;
  endpoint << "robot_" << std::setw(4) << std::setfill('0') << host->getIndex();
  return endpoint.str();
}

std::string modulePathForEndpoint(const std::string & endpoint)
{
  if (endpoint == "fleet_controller") {
    return "controller";
  }
  if (endpoint == "fleet_router") {
    return "fleetRouter";
  }
  if (endpoint == "operator_ui") {
    return "operatorUi";
  }
  constexpr const char * robot_prefix = "robot_";
  if (endpoint.rfind(robot_prefix, 0) == 0) {
    const auto suffix = endpoint.substr(std::char_traits<char>::length(robot_prefix));
    if (suffix.empty() ||
      !std::all_of(suffix.begin(), suffix.end(), [](unsigned char value) {return std::isdigit(value);}))
    {
      throw ::omnetpp::cRuntimeError("invalid FleetQoX robot endpoint: %s", endpoint.c_str());
    }
    return "robot[" + std::to_string(std::stoul(suffix)) + "]";
  }
  throw ::omnetpp::cRuntimeError("unknown FleetQoX endpoint: %s", endpoint.c_str());
}

double percentile(std::vector<double> values, double requested)
{
  if (values.empty()) {
    return 0.0;
  }
  std::sort(values.begin(), values.end());
  const auto rank = std::ceil((requested / 100.0) * static_cast<double>(values.size()));
  const auto index = static_cast<std::size_t>(std::max(1.0, rank) - 1.0);
  return values[std::min(index, values.size() - 1)];
}

bool eventIndexFromPacket(const inet::Packet & packet, std::size_t & event_index)
{
  constexpr const char * prefix = "fleetqox-event-";
  const std::string name = packet.getName();
  if (name.rfind(prefix, 0) != 0) {
    return false;
  }
  const std::string suffix = name.substr(std::char_traits<char>::length(prefix));
  if (suffix.empty()) {
    return false;
  }
  char * end = nullptr;
  const auto parsed = std::strtoull(suffix.c_str(), &end, 10);
  if (end == nullptr || *end != '\0' || parsed > std::numeric_limits<std::size_t>::max()) {
    return false;
  }
  event_index = static_cast<std::size_t>(parsed);
  return true;
}

void printSummary()
{
  const TraceStore & store = traceStore();
  std::cout << "fleetqox_omnetpp_metrics,events_loaded,policies\n";
  std::cout << "fleetqox_omnetpp_metrics," << store.events.size() << "," << store.stats.size() << "\n";
  std::cout << "policy,tx,rx,bytes,deadline_miss_ratio,p50_ms,p99_ms,utility\n";
  std::cout << std::setprecision(12);
  for (const auto & [policy, stats] : store.stats) {
    const double miss_ratio = stats.rx == 0 ? 0.0 :
      static_cast<double>(stats.deadline_misses) / static_cast<double>(stats.rx);
    std::cout << policy << ',' << stats.tx << ',' << stats.rx << ',' << stats.bytes << ',' <<
      miss_ratio << ',' << percentile(stats.latency_ms, 50.0) << ',' <<
      percentile(stats.latency_ms, 99.0) << ',' << stats.utility_delivered << '\n';
  }
  std::cout << "policy,flow_class,tx,rx,bytes,deadline_miss_ratio,p50_ms,p99_ms,utility\n";
  for (const auto & [key, stats] : store.flow_class_stats) {
    const auto & [policy, flow_class] = key;
    const double miss_ratio = stats.rx == 0 ? 0.0 :
      static_cast<double>(stats.deadline_misses) / static_cast<double>(stats.rx);
    std::cout << policy << ',' << flow_class << ',' << stats.tx << ',' << stats.rx << ',' <<
      stats.bytes << ',' << miss_ratio << ',' << percentile(stats.latency_ms, 50.0) << ',' <<
      percentile(stats.latency_ms, 99.0) << ',' << stats.utility_delivered << '\n';
  }
  std::cout.flush();
}

}  // namespace

Define_Module(TraceDrivenUdpApp);

TraceDrivenUdpApp::~TraceDrivenUdpApp()
{
  cancelAndDelete(send_timer_);
}

int TraceDrivenUdpApp::numInitStages() const
{
  return inet::NUM_INIT_STAGES;
}

void TraceDrivenUdpApp::initialize(int stage)
{
  if (stage == inet::INITSTAGE_LOCAL) {
    local_port_ = par("localPort");
    destination_port_ = par("destPort");
    start_offset_ms_ = 1000.0 * static_cast<double>(par("startOffset"));
    summary_writer_ = par("summaryWriter");
    endpoint_name_ = endpointForModule(*this, par("endpointName").stringValue());
    const std::string trace_path = par("traceFile").stringValue();
    loadTraceOnce(trace_path);
    const auto & events = traceStore().events;
    for (std::size_t index = 0; index < events.size(); ++index) {
      if (events[index].source == endpoint_name_) {
        outgoing_events_.push_back(index);
      }
    }
    std::stable_sort(
      outgoing_events_.begin(), outgoing_events_.end(),
      [&events](std::size_t left, std::size_t right) {
        if (events[left].timestamp_ms == events[right].timestamp_ms) {
          return left < right;
        }
        return events[left].timestamp_ms < events[right].timestamp_ms;
      });
    send_timer_ = new ::omnetpp::cMessage("fleetqox-trace-send");
  }
  else if (stage == inet::INITSTAGE_APPLICATION_LAYER) {
    socket_.setOutputGate(gate("socketOut"));
    socket_.setCallback(this);
    socket_.bind(local_port_);
    scheduleNextEvent();
  }
}

void TraceDrivenUdpApp::handleMessage(::omnetpp::cMessage * message)
{
  if (message == send_timer_) {
    sendDueEvents();
    scheduleNextEvent();
    return;
  }
  socket_.processMessage(message);
}

void TraceDrivenUdpApp::scheduleNextEvent()
{
  if (next_outgoing_event_ >= outgoing_events_.size()) {
    return;
  }
  const auto & event = traceStore().events[outgoing_events_[next_outgoing_event_]];
  const auto scheduled = ::omnetpp::SimTime((event.timestamp_ms + start_offset_ms_) / 1000.0);
  scheduleAt(std::max(::omnetpp::simTime(), scheduled), send_timer_);
}

void TraceDrivenUdpApp::sendDueEvents()
{
  const auto & events = traceStore().events;
  constexpr double epsilon_ms = 1e-9;
  const double now_ms = ::omnetpp::simTime().dbl() * 1000.0;
  while (next_outgoing_event_ < outgoing_events_.size()) {
    const std::size_t event_index = outgoing_events_[next_outgoing_event_];
    const auto & event = events[event_index];
    if (event.timestamp_ms + start_offset_ms_ > now_ms + epsilon_ms) {
      break;
    }

    const std::string destination_module = modulePathForEndpoint(event.destination);
    const inet::L3Address destination = inet::L3AddressResolver().resolve(destination_module.c_str());
    const std::string packet_name = "fleetqox-event-" + std::to_string(event_index);
    auto * packet = new inet::Packet(packet_name.c_str());
    const auto payload = inet::makeShared<inet::ApplicationPacket>();
    payload->setChunkLength(inet::B(std::max<std::uint32_t>(event.bytes, 1)));
    payload->setSequenceNumber(static_cast<int64_t>(event.event_id));
    payload->addTag<inet::CreationTimeTag>()->setCreationTime(::omnetpp::simTime());
    packet->insertAtBack(payload);
    packet->addTagIfAbsent<inet::UserPriorityReq>()->setUserPriority(
      userPriorityForFlowClass(event.flow_class));
    socket_.sendTo(packet, destination, destination_port_);
    traceStore().stats[event.policy].tx++;
    traceStore().flow_class_stats[{event.policy, event.flow_class}].tx++;
    sent_++;
    next_outgoing_event_++;
  }
}

void TraceDrivenUdpApp::socketDataArrived(inet::UdpSocket *, inet::Packet * packet)
{
  std::size_t event_index = 0;
  if (eventIndexFromPacket(*packet, event_index) && event_index < traceStore().events.size()) {
    const auto & event = traceStore().events[event_index];
    const double latency_ms = ::omnetpp::simTime().dbl() * 1000.0 -
      (event.timestamp_ms + start_offset_ms_);
    const bool missed_deadline = latency_ms > event.deadline_ms;
    for (auto * stats : {&traceStore().stats[event.policy],
           &traceStore().flow_class_stats[{event.policy, event.flow_class}]}) {
      stats->rx++;
      stats->bytes += event.bytes;
      stats->latency_ms.push_back(latency_ms);
      stats->utility_delivered += event.semantic_utility;
      if (missed_deadline) {
        stats->deadline_misses++;
      }
    }
    received_++;
  }
  delete packet;
}

void TraceDrivenUdpApp::socketErrorArrived(inet::UdpSocket *, inet::Indication * indication)
{
  delete indication;
}

void TraceDrivenUdpApp::socketClosed(inet::UdpSocket *)
{
}

void TraceDrivenUdpApp::finish()
{
  recordScalar("fleetqox packets sent", static_cast<double>(sent_));
  recordScalar("fleetqox packets received", static_cast<double>(received_));
  if (summary_writer_) {
    printSummary();
  }
}

}  // namespace fleetqox_omnetpp
