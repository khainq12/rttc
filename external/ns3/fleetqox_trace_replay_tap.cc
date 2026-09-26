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
// mirrored exactly by the orchestration script: 0=control_station,
// 1..numRobots=robot_0000..robot_{numRobots-1} (matching the reference
// topology's 1 control station + N robots, and fleetqox/trace.py's robot
// naming). The AP gets no tap device at all (see below). Printed to
// stdout at startup so the orchestrator can verify agreement instead of
// relying on two hardcoded copies of the same order staying in sync
// silently.
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

#include <algorithm>
#include <array>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <unistd.h>
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

// Added for the "N=8 REALTIME-LAG VALIDATION" investigation (see
// docs/AUDIT_ACCEPTANCE_TRACKING.md): the "N=8 REMAINING WI-FI LOSS
// AFTER DIRECTED-REPLY" pass found ns-3's own Simulator::Now() reaching
// only ~60s of simulated time across a 120s real-wall-clock run, but
// could not tell apart three explanations -- (A) that same pass's OWN
// heavy per-packet instrumentation (LogMacEvent's CopyData + base64
// decode + byte-search, called from MacTx/MacRx/MacTxDrop/MacRxDrop/
// PhyRxDrop/DroppedMpdu/WifiMacQueueExpired -- up to 769,849 times in
// one run per that pass's own mac_rx_drop_total) slowing the simulator
// down enough to CAUSE the lag itself; (B) genuine FleetRMW/network
// event LOAD (independent of this program's own tracing) simply being
// more than a real-time simulator can keep up with at N=8; or (C) a
// more fundamental CPU ceiling. --heavyTracing (default FALSE) gates
// every one of those expensive per-packet LogMacEvent calls and the
// DroppedMpdu/MacTxFinalDataFailed/BackoffTrace/CwTrace/queue-backlog
// hooks entirely -- with it off, this program touches nothing this
// investigation didn't already touch BEFORE that pass (the original,
// pre-existing MacTx/MacTxDrop/MacRx/MacRxDrop/PhyTxBegin/PhyRxDrop
// atomic-only counters), isolating hypothesis A. wall_elapsed_s/
// self_cpu_s/self_rss_kb below are the ONLY new measurement this pass
// adds when --heavyTracing=false -- all three are O(1) per print (a
// wall-clock read plus two small /proc file reads every 5s), not
// per-packet, so enabling them cannot itself be the confound.
bool g_heavyTracing = false;
std::chrono::steady_clock::time_point g_wallClockStart;

// ---- BEGIN diagnostic-only stage-timing probe ("LOCALIZE NS-3 WALL-CLOCK
// LAG" investigation, see docs/AUDIT_ACCEPTANCE_TRACKING.md). TEMPORARY --
// gated behind --stageTiming (default false, so this program's behavior
// and cost with it off are byte-for-byte identical to before this probe).
// Records a wall-clock timestamp (steady_clock, elapsed ns since
// g_wallClockStart) at each of the 5 ALREADY-CONNECTED trace-callback
// firings (MacTx/MacTxDrop/MacRx/MacRxDrop/PhyTxBegin) into ONE merged,
// time-ordered timeline (not 5 separate per-stage vectors): ns-3's
// event-driven model runs one event handler to completion before
// dispatching the next, so the wall-clock GAP from one traced event to
// the NEXT traced event (of any type) is the closest available proxy,
// from OUTSIDE ns-3's library, for "wall-clock cost incurred handling
// that first event and advancing to the next one" -- attributed to the
// FIRST event's own stage. This is an honest approximation, not a clean
// per-stage isolation: the gap also includes ns-3's own event-dispatch/
// scheduling overhead, any TapBridge forward+write() that runs
// synchronously inside the same call chain (WifiMac hands a received
// frame toward the bridge as part of the same synchronous callback that
// fires MacRx), and RealtimeSimulatorImpl's own per-event realtime-sync
// bookkeeping -- none of which have their own trace sources this driver
// can hook without patching ns-3 library source (a fundamentally larger
// change, see the accompanying doc section for why that was not
// attempted). Documented explicitly rather than presenting false
// precision. All 5 callbacks already fire ONLY on the single ns-3
// simulation thread (TapBridge's own read() happens on a separate
// FdReader thread, but the actual frame-forwarding callback that would
// touch Wifi trace sources is marshaled onto the main thread via
// Simulator::ScheduleWithContext before it runs) -- the mutex below
// guards the push itself (defensive correctness, matching the existing
// g_macEventLogMutex precedent a few lines below), not cross-thread
// re-ordering, so timeline entries are trusted to already be in
// chronological (wall-clock) order without re-sorting.
bool g_stageTiming = false;
std::mutex g_stageTimingMutex;
enum StageTag : uint8_t
{
  kStageMacTx = 0,
  kStageMacTxDrop = 1,
  kStageMacRx = 2,
  kStageMacRxDrop = 3,
  kStagePhyTxBegin = 4,
  kStageCount = 5
};
std::vector<std::pair<int64_t, uint8_t>> g_stageTimeline; // (wall_ns, stage), time-ordered
std::vector<std::pair<double, double>> g_fineLagSamples;  // (sim_time_s, wall_elapsed_s)

inline void
RecordStageEvent(StageTag stage)
{
  if (!g_stageTiming)
  {
    return;
  }
  int64_t nowNs = std::chrono::duration_cast<std::chrono::nanoseconds>(
                      std::chrono::steady_clock::now() - g_wallClockStart)
                      .count();
  std::lock_guard<std::mutex> lock(g_stageTimingMutex);
  g_stageTimeline.emplace_back(nowNs, static_cast<uint8_t>(stage));
}

void
FineLagSample()
{
  if (!g_stageTiming)
  {
    return;
  }
  // 0.5s cadence (real AND simulated, tied together by
  // RealtimeSimulatorImpl) -- 10x finer than PrintWifiStats' existing 5s
  // cadence, purely for the "sim_lag_s progression over time" measurement
  // this investigation asked for; does not replace or alter
  // PrintWifiStats in any way.
  Simulator::Schedule(Seconds(0.5), &FineLagSample);
  double wallElapsedS =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - g_wallClockStart).count();
  double simTimeS = Simulator::Now().GetSeconds();
  std::lock_guard<std::mutex> lock(g_stageTimingMutex);
  g_fineLagSamples.emplace_back(simTimeS, wallElapsedS);
}

double
PercentileOfSorted(const std::vector<int64_t>& sortedVals, double p)
{
  if (sortedVals.empty())
  {
    return -1.0;
  }
  std::size_t idx = static_cast<std::size_t>(p * static_cast<double>(sortedVals.size() - 1));
  return static_cast<double>(sortedVals[idx]);
}

void
DumpStageTimingSummary()
{
  if (!g_stageTiming)
  {
    return;
  }
  static const char* kStageNames[kStageCount] = {
      "mac_tx", "mac_tx_drop", "mac_rx", "mac_rx_drop", "phy_tx_begin"};
  std::vector<std::pair<int64_t, uint8_t>> timeline;
  std::vector<std::pair<double, double>> lagSnapshot;
  {
    // Copy under lock (cheap relative to the run's own event-processing
    // cost -- see the overhead-control measurement in the doc write-up),
    // then compute gaps/percentiles OUTSIDE the lock so this periodic
    // dump (called from PrintWifiStats' existing 5s cadence) never blocks
    // the main thread's own future RecordStageEvent() pushes for longer
    // than the copy itself takes.
    std::lock_guard<std::mutex> lock(g_stageTimingMutex);
    timeline = g_stageTimeline;
    lagSnapshot = g_fineLagSamples;
  }
  std::vector<int64_t> gapsByStage[kStageCount];
  int64_t totalByStage[kStageCount] = {0, 0, 0, 0, 0};
  uint64_t countByStage[kStageCount] = {0, 0, 0, 0, 0};
  for (std::size_t k = 0; k + 1 < timeline.size(); ++k)
  {
    uint8_t stage = timeline[k].second;
    if (stage >= kStageCount)
    {
      continue;
    }
    int64_t gap = timeline[k + 1].first - timeline[k].first;
    gapsByStage[stage].push_back(gap);
    totalByStage[stage] += gap;
    ++countByStage[stage];
  }
  // Every timeline entry is one EVENT of that stage (countByStage above
  // only counts entries that have a following event to form a gap with,
  // i.e. total events minus at most 1 per stage at the very end of the
  // recorded window) -- report the TRUE per-stage event count separately
  // so "number of events per stage" (this investigation's own explicit
  // requirement) is exact, not off-by-the-trailing-event.
  uint64_t trueCountByStage[kStageCount] = {0, 0, 0, 0, 0};
  for (const auto& entry : timeline)
  {
    if (entry.second < kStageCount)
    {
      ++trueCountByStage[entry.second];
    }
  }
  std::cout << "FLEETQOX_STAGE_TIMING {\"stages\":[";
  for (std::size_t i = 0; i < kStageCount; ++i)
  {
    std::vector<int64_t> sorted = gapsByStage[i];
    std::sort(sorted.begin(), sorted.end());
    int64_t maxGap = sorted.empty() ? -1 : sorted.back();
    if (i != 0)
    {
      std::cout << ",";
    }
    std::cout << "{\"stage\":\"" << kStageNames[i] << "\","
              << "\"event_count\":" << trueCountByStage[i] << ","
              << "\"gap_sample_count\":" << countByStage[i] << ","
              << "\"total_gap_wall_ns\":" << totalByStage[i] << ","
              << "\"p50_gap_ns\":" << PercentileOfSorted(sorted, 0.50) << ","
              << "\"p95_gap_ns\":" << PercentileOfSorted(sorted, 0.95) << ","
              << "\"p99_gap_ns\":" << PercentileOfSorted(sorted, 0.99) << ","
              << "\"max_gap_ns\":" << maxGap << "}";
  }
  std::cout << "],\"fine_lag_samples\":[";
  for (std::size_t i = 0; i < lagSnapshot.size(); ++i)
  {
    if (i != 0)
    {
      std::cout << ",";
    }
    std::cout << "[" << lagSnapshot[i].first << "," << lagSnapshot[i].second << "]";
  }
  std::cout << "]}" << std::endl;
}
// ---- END diagnostic-only stage-timing probe globals/helpers ----

double
SelfCpuSeconds()
{
  std::ifstream stat("/proc/self/stat");
  if (!stat.is_open())
  {
    return -1.0;
  }
  std::string skip;
  // Fields 1-2 are pid,(comm) -- comm can contain spaces inside parens,
  // so skip past the closing ')' first rather than counting whitespace-
  // delimited tokens naively. After that, field 3 (state) is the very
  // next whitespace-delimited token; utime/stime are fields 14/15
  // (1-indexed), so 11 more reads (fields 3..13: state, ppid, pgrp,
  // session, tty_nr, tpgid, flags, minflt, cminflt, majflt, cmajflt)
  // land exactly before utime. BUG FIXED (see "N=16 SCALE VALIDATION"
  // in docs/AUDIT_ACCEPTANCE_TRACKING.md): this loop previously ran 13
  // times, consuming 2 fields too many -- it silently swallowed utime
  // AND stime themselves, so the actual read below captured fields
  // 16/17 (cutime/cstime, the process's REAPED CHILDREN's CPU time --
  // 0 for this single-process program) instead, producing a
  // plausible-looking but wrong near-zero self_cpu_s on every prior
  // measurement that used it.
  std::getline(stat, skip, ')');
  long utimeTicks = 0;
  long stimeTicks = 0;
  std::string field;
  for (int i = 0; i < 11; ++i)
  {
    stat >> field;
  }
  stat >> utimeTicks >> stimeTicks;
  long ticksPerSec = sysconf(_SC_CLK_TCK);
  if (ticksPerSec <= 0)
  {
    return -1.0;
  }
  return static_cast<double>(utimeTicks + stimeTicks) / static_cast<double>(ticksPerSec);
}

long
SelfRssKb()
{
  std::ifstream status("/proc/self/status");
  std::string line;
  while (std::getline(status, line))
  {
    if (line.rfind("VmRSS:", 0) == 0)
    {
      long kb = 0;
      std::sscanf(line.c_str(), "VmRSS: %ld kB", &kb);
      return kb;
    }
  }
  return -1;
}

std::atomic<uint64_t> g_macTxTotal{0};
std::atomic<uint64_t> g_macTxBytes{0};
std::atomic<uint64_t> g_macTxSmall{0};
std::atomic<uint64_t> g_macTxLarge{0};
std::atomic<uint64_t> g_macTxDropTotal{0};
std::atomic<uint64_t> g_macRxTotal{0};
std::atomic<uint64_t> g_macRxBytes{0};
std::atomic<uint64_t> g_macRxDropTotal{0};
std::atomic<uint64_t> g_phyTxBeginTotal{0};
std::atomic<uint64_t> g_phyRxDropTotal{0};
std::array<std::atomic<uint64_t>, kMaxRxDropReasons> g_phyRxDropByReason{};
std::atomic<uint64_t> g_associatedStaCount{0};

// Added for the "N=8 REMAINING WI-FI LOSS AFTER DIRECTED-REPLY"
// investigation (see docs/AUDIT_ACCEPTANCE_TRACKING.md): the counters
// above cannot distinguish WHY a packet never reached MacRx -- an
// explicit MAC-layer drop, a channel-access/PHY failure, or a packet
// still legitimately in flight when the process was killed. These add
// the exact ns-3 trace sources that answer that, all purely additive
// (no change to any radio/QoS/timing parameter):
//   WifiMac::DroppedMpdu -- fires with the EXACT WifiMacDropReason
//     (FAILED_ENQUEUE = queue was full at enqueue time, i.e. an
//     immediate explicit drop; EXPIRED_LIFETIME = sat in the MAC queue
//     past its lifetime, i.e. classification B "stuck past useful
//     time"; REACHED_RETRY_LIMIT = the 802.11 MAC itself gave up after
//     exhausting its retry budget, i.e. classification C; QOS_OLD_PACKET
//     = superseded by a newer packet under block-ack, not expected here
//     since wifiQos is never enabled for Table VI).
//   WifiRemoteStationManager::MacTxFinalDataFailed -- an independent
//     confirmation of retry-limit exhaustion from the rate-control
//     side, for a same-mechanism cross-check against DroppedMpdu's own
//     REACHED_RETRY_LIMIT count.
//   WifiMacQueue::Expired -- fires exactly when EXPIRED_LIFETIME above
//     is about to happen (the queue's own lifetime-check firing),
//     kept as a second, independent confirmation of the same
//     mechanism from the queue's side rather than the MAC's side.
//   Txop::BackoffTrace / CwTrace -- the DCF's own backoff counter and
//     contention window value every time either changes; a CW that
//     keeps growing past its minimum is ns-3's own signal that this
//     station is experiencing real channel-access contention/collision
//     (CW doubles on every collision in the standard 802.11 DCF model),
//     independent of anything RMW-level.
// All hooks are labeled per-station ("who") unlike the pre-existing
// aggregate-only counters above, which mix every station together and
// cannot show "per sender->receiver differences".
enum : std::size_t
{
  kDropReasonFailedEnqueue = 0,
  kDropReasonExpiredLifetime = 1,
  kDropReasonReachedRetryLimit = 2,
  kDropReasonQosOldPacket = 3,
  kNumDropReasons = 4,
};
std::array<std::atomic<uint64_t>, kNumDropReasons> g_droppedMpduByReason{};
std::atomic<uint64_t> g_macTxFinalDataFailedTotal{0};
std::atomic<uint64_t> g_wifiMacQueueExpiredTotal{0};
std::atomic<uint64_t> g_backoffValueSum{0};
std::atomic<uint64_t> g_backoffValueCount{0};
std::atomic<uint64_t> g_backoffValueMax{0};
std::atomic<uint64_t> g_cwValueSum{0};
std::atomic<uint64_t> g_cwValueCount{0};
std::atomic<uint64_t> g_cwValueMax{0};

// Per-station WifiMacQueue backlog snapshot, sampled on its own faster
// cadence (2s) than PrintWifiStats' 5s -- a queue can fill/drain much
// faster than 5s, and "how deep did the backlog get, not just whether
// it eventually expired" is what the investigation's "queue delay/
// backlog" measurement needs. Populated once at setup time (before
// Simulator::Run()), read-only from then on from the single simulation
// event thread -- no mutex needed (matches how g_macToApGroup/
// g_apDeviceByGroup above are already used read-only after setup).
std::vector<std::pair<std::string, Ptr<WifiMacQueue>>> g_queueBacklogTargets;

// Per-station identity for the existing aggregate atomics' NEW
// per-packet-with-"who" logging below -- reuses g_macEventLogLines'
// existing buffered-line mechanism, just adding a "who" field to every
// line instead of leaving it implicit/mixed.

// Per-packet MAC-layer timeline for the "black-box" decomposition
// investigation (16-17/09/2026, see docs/AUDIT_ACCEPTANCE_TRACKING.md
// "Optimization #2: tách network black box"). Existing counters above are
// aggregate-only and cannot be joined back to a specific application
// message. scripts/fleetqox_rmw_trace_endpoint.py's build_payload()
// embeds `"e":"<event_id>"` as plaintext JSON inside the std_msgs/String
// CDR-serialized wire data -- present verbatim as ASCII bytes at this
// trace point regardless of whatever RTPS/CDR/802.11 header framing
// surrounds it, so a raw byte scan finds it without needing to know the
// exact layout. Logging BOTH Simulator::Now() (simulated time) and a
// wall-clock timestamp for the same MacTx/MacRx event is what lets the
// offline analysis distinguish "packet genuinely had to wait/retry a lot
// in the simulated Wi-Fi model" (sim-time delta is large) from "the
// realtime simulator just processed this event late relative to wall
// clock" (sim-time delta stays small while wall-time delta is large) --
// see PrintWifiStats' file-header comment above for why per-packet
// printing was previously avoided (interleaved/corrupted stdout): this
// buffers lines instead and only flushes in a batch, piggybacking on
// PrintWifiStats' existing 5s Simulator::Schedule cadence, so writes stay
// on the same single simulation thread and at the same controlled rate.
std::mutex g_macEventLogMutex;
std::vector<std::string> g_macEventLogLines;

// Diagnostic-only (17/09/2026): the first live run with this
// instrumentation found MacTx/MacRx firing thousands of times (matching
// the existing g_macTxTotal/g_macRxTotal counters) but a naive plaintext
// scan for `"e":"` matched ZERO of them. A by-packet-size debug dump
// (kept below, now gated on a genuine post-decode failure) found why:
// FleetRMW does NOT put the app's JSON on the wire directly. Every data
// packet is wrapped in FleetRMW's OWN JSON envelope (magic "FRMW1" +
// `"kind":"sidecar_packet_frame"`, with `route`/`sample_envelope`
// metadata), and the app's JSON (with our `"e":"<event_id>"` field) is
// BASE64-ENCODED inside that envelope's `"serialized_payload":{"data":
// "<base64>"}` field -- so the literal marker never appears un-decoded.
// FleetRMW also emits a SEPARATE `"kind":"source_sequence_ack_nack"`
// control frame per data frame with no such field at all -- explains the
// ~12-15x mac_tx_total amplification vs the ~400-500 logical app
// messages seen earlier (ack/nack + retry traffic dominates the wire).
std::atomic<uint64_t> g_macEventAttempts{0};
std::atomic<uint64_t> g_macEventExtracted{0};
// One debug dump per DISTINCT failing packet size, gated on packets that
// DO contain FleetRMW's own `"data":"..."` field but still fail to
// decode/find our marker afterward -- a genuine anomaly worth seeing,
// unlike ack_nack/control frames (expected, no such field, not dumped).
std::mutex g_macEventDebugSeenSizesMutex;
std::set<uint32_t> g_macEventDebugSeenSizes;
constexpr std::size_t kMaxDebugDumpSizes = 20;

std::vector<uint8_t>
Base64Decode(std::vector<uint8_t>::const_iterator begin, std::vector<uint8_t>::const_iterator end)
{
  static const std::array<int8_t, 256> table = []() {
    std::array<int8_t, 256> t{};
    t.fill(-1);
    const char* alphabet =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (int i = 0; i < 64; ++i)
    {
      t[static_cast<uint8_t>(alphabet[i])] = static_cast<int8_t>(i);
    }
    return t;
  }();
  std::vector<uint8_t> out;
  out.reserve(static_cast<std::size_t>(std::distance(begin, end)) / 4 * 3 + 3);
  int val = 0;
  int bits = -8;
  for (auto i = begin; i != end; ++i)
  {
    if (*i == '=')
    {
      break;
    }
    int8_t d = table[*i];
    if (d == -1)
    {
      continue;
    }
    val = (val << 6) + d;
    bits += 6;
    if (bits >= 0)
    {
      out.push_back(static_cast<uint8_t>((val >> bits) & 0xFF));
      bits -= 8;
    }
  }
  return out;
}

void
DumpDebugPayload(uint32_t size, const std::vector<uint8_t>& buf)
{
  bool shouldDump = false;
  {
    std::lock_guard<std::mutex> lock(g_macEventDebugSeenSizesMutex);
    if (g_macEventDebugSeenSizes.size() < kMaxDebugDumpSizes
        && g_macEventDebugSeenSizes.insert(size).second)
    {
      shouldDump = true;
    }
  }
  if (!shouldDump)
  {
    return;
  }
  std::ostringstream printable;
  for (uint8_t byteVal : buf)
  {
    printable << (std::isprint(byteVal) ? static_cast<char>(byteVal) : '.');
  }
  std::lock_guard<std::mutex> lock(g_macEventLogMutex);
  std::ostringstream line;
  line << "{\"size\":" << size << ",\"printable\":\"" << printable.str() << "\"}";
  g_macEventLogLines.push_back(std::string("FLEETQOX_MAC_DEBUG_PAYLOAD ") + line.str());
}

bool
ExtractEventId(Ptr<const Packet> packet, std::string& eventId)
{
  uint32_t size = packet->GetSize();
  if (size == 0 || size > 4096)
  {
    return false;
  }
  std::vector<uint8_t> buf(size);
  packet->CopyData(buf.data(), size);

  static const std::string kDataMarker = "\"data\":\"";
  auto dataIt = std::search(buf.begin(), buf.end(), kDataMarker.begin(), kDataMarker.end());
  if (dataIt == buf.end())
  {
    // Not a sidecar_packet_frame (e.g. source_sequence_ack_nack, or
    // non-Fleet 802.11/ARP traffic) -- nothing to correlate, not an
    // anomaly, don't dump.
    return false;
  }
  auto b64Start = dataIt + static_cast<std::ptrdiff_t>(kDataMarker.size());
  auto b64End = std::find(b64Start, buf.end(), '"');
  if (b64End == buf.end() || b64End == b64Start)
  {
    DumpDebugPayload(size, buf);
    return false;
  }
  std::vector<uint8_t> decoded = Base64Decode(b64Start, b64End);

  // Table IV/V's fleetqox_rmw_trace_endpoint.py marker (unchanged).
  static const std::string kMarker = "\"e\":\"";
  auto it = std::search(decoded.begin(), decoded.end(), kMarker.begin(), kMarker.end());
  if (it != decoded.end())
  {
    auto digitsStart = it + static_cast<std::ptrdiff_t>(kMarker.size());
    auto digitsEnd = digitsStart;
    while (digitsEnd != decoded.end() && std::isdigit(*digitsEnd))
    {
      ++digitsEnd;
    }
    if (digitsEnd != digitsStart && digitsEnd != decoded.end() && *digitsEnd == '"')
    {
      eventId.assign(digitsStart, digitsEnd);
      return true;
    }
  }

  // Added for the "N=8 REMAINING WI-FI LOSS AFTER DIRECTED-REPLY"
  // investigation (see docs/AUDIT_ACCEPTANCE_TRACKING.md): confirmed by
  // source read that scripts/fleetqox_coordination_endpoint.py (Table
  // VI's own endpoint script, a DIFFERENT script from
  // fleetqox_rmw_trace_endpoint.py above) never emits an "e" field at
  // all -- its JSON schema is {"type","from"/"to","req_id","wall_ns",
  // ...} instead, meaning the "e" marker above ALWAYS fails to extract
  // for every Table VI run and ExtractEventId() silently returned false
  // for 100% of packets (confirmed empirically: a smoke-test run showed
  // mac_event_attempts=3763, mac_event_extracted=0). Every Table VI
  // message (request AND reply) DOES carry "wall_ns": a plain JSON
  // integer nanosecond send timestamp, unique enough within one run to
  // serve the exact same correlation purpose. Prefixed "wallns:" so it
  // is visually distinct from an "e"-derived numeric id (the two
  // schemas are mutually exclusive per run in practice, but this keeps
  // the two id spaces unambiguous regardless).
  static const std::string kWallNsMarker = "\"wall_ns\":";
  auto wallIt = std::search(decoded.begin(), decoded.end(), kWallNsMarker.begin(), kWallNsMarker.end());
  if (wallIt != decoded.end())
  {
    auto digitsStart = wallIt + static_cast<std::ptrdiff_t>(kWallNsMarker.size());
    // fleetqox_coordination_endpoint.py's json.dumps() uses Python's
    // default separators, which insert a space after every ':' --
    // "\"wall_ns\": 123..." not "\"wall_ns\":123...". Skip it (and any
    // other incidental whitespace) before scanning for digits.
    while (digitsStart != decoded.end() && std::isspace(*digitsStart))
    {
      ++digitsStart;
    }
    auto digitsEnd = digitsStart;
    while (digitsEnd != decoded.end() && std::isdigit(*digitsEnd))
    {
      ++digitsEnd;
    }
    if (digitsEnd != digitsStart)
    {
      eventId.assign("wallns:");
      eventId.append(digitsStart, digitsEnd);
      return true;
    }
  }

  DumpDebugPayload(size, buf);
  return false;
}

void
LogMacEvent(const std::string& kind, const std::string& who, Ptr<const Packet> packet)
{
  // Single gating point for ALL per-packet extraction/logging overhead
  // (see g_heavyTracing's doc comment) -- every call site below still
  // calls this unconditionally; skipping here (before the CopyData +
  // base64-decode + byte-search ExtractEventId() does) is what makes
  // --heavyTracing=false actually cheap, not just "logs fewer lines".
  if (!g_heavyTracing)
  {
    return;
  }
  g_macEventAttempts.fetch_add(1, std::memory_order_relaxed);
  std::string eventId;
  if (!ExtractEventId(packet, eventId))
  {
    // Not a Fleet trace-replay application packet (ARP, discovery beacon,
    // 802.11 management frame, etc.) -- nothing to correlate, skip.
    return;
  }
  g_macEventExtracted.fetch_add(1, std::memory_order_relaxed);
  int64_t wallNs = std::chrono::duration_cast<std::chrono::nanoseconds>(
                       std::chrono::system_clock::now().time_since_epoch())
                       .count();
  std::ostringstream line;
  line << "FLEETQOX_MAC_EVENT {\"kind\":\"" << kind << "\",\"who\":\"" << who << "\","
       << "\"event_id\":\"" << eventId << "\","
       << "\"sim_time_s\":" << Simulator::Now().GetSeconds() << ","
       << "\"wall_ns\":" << wallNs << "}";
  std::lock_guard<std::mutex> lock(g_macEventLogMutex);
  g_macEventLogLines.push_back(line.str());
}

void
FlushMacEventLog()
{
  std::vector<std::string> lines;
  {
    std::lock_guard<std::mutex> lock(g_macEventLogMutex);
    lines.swap(g_macEventLogLines);
  }
  for (const auto& line : lines)
  {
    std::cout << line << "\n";
  }
  if (!lines.empty())
  {
    std::cout.flush();
  }
}

void
MacTxTrace(std::string who, Ptr<const Packet> packet)
{
  RecordStageEvent(kStageMacTx);
  g_macTxTotal.fetch_add(1, std::memory_order_relaxed);
  // Cheap (O(1) arithmetic, no string/extraction work) byte counter --
  // unconditional, for the "N=8 REALTIME-LAG VALIDATION" investigation's
  // "total packets/bytes if cheaply available" measurement without
  // needing --heavyTracing.
  g_macTxBytes.fetch_add(packet->GetSize(), std::memory_order_relaxed);
  if (packet->GetSize() <= kSmallFrameThresholdBytes)
  {
    g_macTxSmall.fetch_add(1, std::memory_order_relaxed);
  }
  else
  {
    g_macTxLarge.fetch_add(1, std::memory_order_relaxed);
  }
  LogMacEvent("tx", who, packet);
}

void
MacTxDropTrace(std::string who, Ptr<const Packet> packet)
{
  RecordStageEvent(kStageMacTxDrop);
  g_macTxDropTotal.fetch_add(1, std::memory_order_relaxed);
  LogMacEvent("mac_tx_drop", who, packet);
}

void
MacRxTrace(std::string who, Ptr<const Packet> packet)
{
  RecordStageEvent(kStageMacRx);
  g_macRxTotal.fetch_add(1, std::memory_order_relaxed);
  g_macRxBytes.fetch_add(packet->GetSize(), std::memory_order_relaxed);
  LogMacEvent("rx", who, packet);
}

void
MacRxDropTrace(std::string who, Ptr<const Packet> packet)
{
  RecordStageEvent(kStageMacRxDrop);
  g_macRxDropTotal.fetch_add(1, std::memory_order_relaxed);
  LogMacEvent("mac_rx_drop", who, packet);
}

void
PhyTxBeginTrace(std::string /* who */, Ptr<const Packet> /* packet */, double /* txPowerW */)
{
  RecordStageEvent(kStagePhyTxBegin);
  g_phyTxBeginTotal.fetch_add(1, std::memory_order_relaxed);
}

void
PhyRxDropTrace(std::string who, Ptr<const Packet> packet, WifiPhyRxfailureReason reason)
{
  g_phyRxDropTotal.fetch_add(1, std::memory_order_relaxed);
  auto idx = static_cast<std::size_t>(reason);
  if (idx < kMaxRxDropReasons)
  {
    g_phyRxDropByReason[idx].fetch_add(1, std::memory_order_relaxed);
  }
  LogMacEvent("phy_rx_drop:" + std::to_string(idx), who, packet);
}

void
AssociatedStaTrace(uint16_t /* aid */, Mac48Address /* address */)
{
  g_associatedStaCount.fetch_add(1, std::memory_order_relaxed);
}

// New MAC/PHY-adjacent trace callbacks (see the "N=8 REMAINING WI-FI
// LOSS AFTER DIRECTED-REPLY" doc comment above g_droppedMpduByReason
// for what each measures and why).
void
DroppedMpduTrace(std::string who, WifiMacDropReason reason, Ptr<const WifiMpdu> mpdu)
{
  auto idx = static_cast<std::size_t>(reason);
  if (idx < kNumDropReasons)
  {
    g_droppedMpduByReason[idx].fetch_add(1, std::memory_order_relaxed);
  }
  static const char* kReasonNames[kNumDropReasons] = {
      "failed_enqueue", "expired_lifetime", "reached_retry_limit", "qos_old_packet"};
  const char* reasonName = (idx < kNumDropReasons) ? kReasonNames[idx] : "unknown";
  LogMacEvent(std::string("dropped_mpdu:") + reasonName, who, mpdu->GetPacket());
}

void
MacTxFinalDataFailedTrace(std::string /* who */, Mac48Address /* address */)
{
  g_macTxFinalDataFailedTotal.fetch_add(1, std::memory_order_relaxed);
}

void
WifiMacQueueExpiredTrace(std::string who, Ptr<const WifiMpdu> mpdu)
{
  g_wifiMacQueueExpiredTotal.fetch_add(1, std::memory_order_relaxed);
  LogMacEvent("queue_expired", who, mpdu->GetPacket());
}

void
BackoffValueTrace(std::string /* who */, uint32_t value, uint8_t /* linkId */)
{
  g_backoffValueSum.fetch_add(value, std::memory_order_relaxed);
  g_backoffValueCount.fetch_add(1, std::memory_order_relaxed);
  uint64_t prevMax = g_backoffValueMax.load(std::memory_order_relaxed);
  while (value > prevMax && !g_backoffValueMax.compare_exchange_weak(prevMax, value))
  {
  }
}

void
CwValueTrace(std::string /* who */, uint32_t value, uint8_t /* linkId */)
{
  g_cwValueSum.fetch_add(value, std::memory_order_relaxed);
  g_cwValueCount.fetch_add(1, std::memory_order_relaxed);
  uint64_t prevMax = g_cwValueMax.load(std::memory_order_relaxed);
  while (value > prevMax && !g_cwValueMax.compare_exchange_weak(prevMax, value))
  {
  }
}

void
PrintQueueBacklog()
{
  Simulator::Schedule(Seconds(2.0), &PrintQueueBacklog);
  std::ostringstream line;
  line << "FLEETQOX_QUEUE_BACKLOG {\"sim_time_s\":" << Simulator::Now().GetSeconds()
       << ",\"depths\":[";
  bool first = true;
  for (const auto& target : g_queueBacklogTargets)
  {
    if (!first)
    {
      line << ",";
    }
    first = false;
    line << "[\"" << target.first << "\"," << target.second->GetNPackets() << "]";
  }
  line << "]}";
  std::cout << line.str() << std::endl;
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
  // Flush any buffered per-packet MAC timeline lines on the same safe
  // cadence/thread as this function -- see g_macEventLogLines' comment.
  FlushMacEventLog();
  // Same kill-safe periodic-dump cadence, for the stage-timing probe (see
  // DumpStageTimingSummary's own comment) -- no-op unless --stageTiming.
  DumpStageTimingSummary();
  // sim_time_s lets the orchestrator pick a snapshot by SIMULATED elapsed
  // time instead of blindly taking "whichever line happened to be last
  // before the process got killed" -- the latter varies run-to-run purely
  // from real wall-clock scheduling jitter around when the orchestrator
  // notices completion (see docs/AUDIT_ACCEPTANCE_TRACKING.md 15/09/2026
  // "XÁC ĐỊNH ĐƯỢC NGUỒN GỐC nhiễu nền").
  // Added for the "N=8 REALTIME-LAG VALIDATION" investigation (see
  // g_heavyTracing's doc comment): O(1) per print (one steady_clock
  // read plus two small /proc file reads) regardless of --heavyTracing,
  // so these fields alone cannot be the confound they're measuring.
  double wallElapsedS = std::chrono::duration<double>(
                            std::chrono::steady_clock::now() - g_wallClockStart)
                            .count();
  double simTimeS = Simulator::Now().GetSeconds();
  std::cout << "FLEETQOX_WIFI_STATS {"
            << "\"sim_time_s\":" << simTimeS << ","
            << "\"wall_elapsed_s\":" << wallElapsedS << ","
            << "\"sim_lag_s\":" << (wallElapsedS - simTimeS) << ","
            << "\"self_cpu_s\":" << SelfCpuSeconds() << ","
            << "\"self_rss_kb\":" << SelfRssKb() << ","
            << "\"heavy_tracing\":" << (g_heavyTracing ? "true" : "false") << ","
            << "\"total_stations\":" << totalStations << ","
            << "\"num_aps\":" << numAps << ","
            << "\"associated_stations\":" << g_associatedStaCount.load() << ","
            << "\"mac_tx_total\":" << g_macTxTotal.load() << ","
            << "\"mac_tx_bytes\":" << g_macTxBytes.load() << ","
            << "\"mac_tx_small\":" << g_macTxSmall.load() << ","
            << "\"mac_tx_large\":" << g_macTxLarge.load() << ","
            << "\"mac_tx_drop_total\":" << g_macTxDropTotal.load() << ","
            << "\"mac_rx_total\":" << g_macRxTotal.load() << ","
            << "\"mac_rx_bytes\":" << g_macRxBytes.load() << ","
            << "\"mac_rx_drop_total\":" << g_macRxDropTotal.load() << ","
            << "\"phy_tx_begin_total\":" << g_phyTxBeginTotal.load() << ","
            << "\"phy_rx_drop_total\":" << g_phyRxDropTotal.load() << ","
            << "\"mac_event_attempts\":" << g_macEventAttempts.load() << ","
            << "\"mac_event_extracted\":" << g_macEventExtracted.load() << ","
            << "\"dropped_mpdu_failed_enqueue\":" << g_droppedMpduByReason[kDropReasonFailedEnqueue].load() << ","
            << "\"dropped_mpdu_expired_lifetime\":" << g_droppedMpduByReason[kDropReasonExpiredLifetime].load() << ","
            << "\"dropped_mpdu_reached_retry_limit\":" << g_droppedMpduByReason[kDropReasonReachedRetryLimit].load() << ","
            << "\"dropped_mpdu_qos_old_packet\":" << g_droppedMpduByReason[kDropReasonQosOldPacket].load() << ","
            << "\"mac_tx_final_data_failed_total\":" << g_macTxFinalDataFailedTotal.load() << ","
            << "\"wifi_mac_queue_expired_total\":" << g_wifiMacQueueExpiredTotal.load() << ","
            << "\"backoff_value_count\":" << g_backoffValueCount.load() << ","
            << "\"backoff_value_sum\":" << g_backoffValueSum.load() << ","
            << "\"backoff_value_max\":" << g_backoffValueMax.load() << ","
            << "\"cw_value_count\":" << g_cwValueCount.load() << ","
            << "\"cw_value_sum\":" << g_cwValueSum.load() << ","
            << "\"cw_value_max\":" << g_cwValueMax.load() << ","
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
  g_wallClockStart = std::chrono::steady_clock::now();
  uint32_t numRobots = 8;
  std::string wifiMode = "ErpOfdmRate54Mbps";
  double mobilitySpeed = 0.0;
  double stationSpacing = 3.0;
  std::string tapPrefix = "ftap";
  double simDuration = 30.0;
  bool wifiQos = false;
  uint32_t numAps = 1;
  uint32_t seed = 1;
  uint32_t run = 1;
  // Reference-topology PHY/layout parameters (see docs/AUDIT_ACCEPTANCE_TRACKING.md
  // "kịch bản mô phỏng theo sơ đồ tham chiếu") -- defaults match the
  // reference diagram's own Simulation Parameters table exactly rather
  // than ns-3's own WifiPhy defaults (which are a different, unrelated
  // set of values), so a plain invocation with no PHY flags already
  // reproduces the documented scenario.
  std::string layout = "circle";
  double circleRadius = 7.5;
  double pathLossExponent = 2.7;
  double txPowerDbm = 15.0;
  double rxSensitivityDbm = -82.0;

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
  cmd.AddValue(
      "seed",
      "ns-3 RngSeedManager seed (see docs/AUDIT_ACCEPTANCE_TRACKING.md "
      "'compact data-frame encoding' -- this program previously had NO "
      "RNG seed control at all, unlike fleetqox_trace_replay.cc's "
      "--seed/--run/AssignStreams, meaning repeated runs of the exact "
      "same config were never actually comparable: rerunning one "
      "unchanged JSON --static-mode config 4 times measured 29.4%, "
      "10.3%, 22.7%, 18.3% delivery. ns-3's own guidance is a FIXED seed "
      "with a VARYING --run for independent statistical replications, "
      "not a fresh seed per run -- match that convention rather than "
      "picking a new --seed value per invocation.",
      seed);
  cmd.AddValue(
      "run",
      "ns-3 RngSeedManager run number -- vary this (keeping --seed "
      "fixed) across repeated invocations of the same configuration to "
      "get independent statistical replications, per ns-3's own RNG "
      "documentation.",
      run);
  cmd.AddValue(
      "layout",
      "Station placement: 'grid' (original stationSpacing-based grid) or "
      "'circle' (reference-diagram layout: every station, including the "
      "control station, placed evenly around a circle of radius "
      "--circleRadius centered on the AP -- see 'Node Placement' in the "
      "reference topology diagram).",
      layout);
  cmd.AddValue(
      "circleRadius",
      "Circle radius in meters when --layout=circle (reference diagram "
      "specifies 5-10m; only meaningful with --layout=circle).",
      circleRadius);
  cmd.AddValue(
      "pathLossExponent",
      "LogDistancePropagationLossModel exponent (reference diagram: 2.7 "
      "for an indoor environment) -- explicitly set rather than left at "
      "ns-3's own YansWifiChannelHelper::Default() propagation model, "
      "which is a different model entirely (RangePropagationLossModel), "
      "not just a different exponent value.",
      pathLossExponent);
  cmd.AddValue(
      "txPowerDbm",
      "WifiPhy TxPowerStart/TxPowerEnd in dBm (reference diagram: 15 dBm "
      "for both STA and AP) -- set to a single fixed value on both ends "
      "of the range since this program never uses ns-3's power-control "
      "rate-adaptation feature.",
      txPowerDbm);
  cmd.AddValue(
      "rxSensitivityDbm",
      "WifiPhy RxSensitivity in dBm (reference diagram: -82 dBm).",
      rxSensitivityDbm);
  cmd.AddValue(
      "heavyTracing",
      "Enable the 'N=8 REMAINING WI-FI LOSS AFTER DIRECTED-REPLY' pass's "
      "expensive per-packet MAC-event extraction/logging and "
      "DroppedMpdu/MacTxFinalDataFailed/BackoffTrace/CwTrace/queue-"
      "backlog hooks. Default false: with this off, the program's trace "
      "connections and per-event work are identical to what this "
      "investigation used BEFORE that pass -- see g_heavyTracing's own "
      "doc comment for why (isolating whether that pass's OWN "
      "instrumentation overhead, not FleetRMW/network load, caused the "
      "measured ns-3 realtime lag).",
      g_heavyTracing);
  std::string schedulerType = "map";
  cmd.AddValue(
      "scheduler",
      "ns-3 Simulator event-scheduler implementation: 'map' (ns3::MapScheduler, "
      "std::map/red-black-tree based -- this program's ORIGINAL, still-default "
      "behavior, byte-for-byte unchanged if this flag is never passed), 'heap' "
      "(ns3::HeapScheduler, binary heap), 'list' (ns3::ListScheduler, O(n) "
      "insert -- reference/slow baseline only), 'calendar' "
      "(ns3::CalendarScheduler), or 'priority' (ns3::PriorityQueueScheduler). "
      "Added for the \"N=16 SERIOUS PERFORMANCE PASS\" investigation (see "
      "docs/AUDIT_ACCEPTANCE_TRACKING.md) after profiling found "
      "ns3::MapScheduler::Insert as a hot function at N=16 -- purely an "
      "internal event-ordering data structure, does not change WHICH events "
      "fire or in what simulated-time order, so switching it can never alter "
      "simulated network semantics/results, only wall-clock speed.",
      schedulerType);
  double realtimeHardLimitS = 0.0;
  cmd.AddValue(
      "realtimeHardLimitS",
      "RealtimeSimulatorImpl SynchronizationMode: 0 (default, unchanged "
      "behavior) keeps SYNC_BEST_EFFORT (silently fall behind and keep "
      "going, this program's original behavior). A positive value switches "
      "to SYNC_HARD_LIMIT with that many seconds of tolerance -- ns-3 itself "
      "raises a fatal error (hard stop, not a silent bad measurement) if "
      "wall-clock lag ever exceeds this threshold. Added for the \"N=16 "
      "SERIOUS PERFORMANCE PASS\" investigation's Phase 5 (measurement "
      "safety, not a performance change) -- see "
      "docs/AUDIT_ACCEPTANCE_TRACKING.md.",
      realtimeHardLimitS);
  cmd.AddValue(
      "stageTiming",
      "TEMPORARY, diagnostic-only (see DumpStageTimingSummary's own doc "
      "comment and docs/AUDIT_ACCEPTANCE_TRACKING.md's \"LOCALIZE NS-3 "
      "WALL-CLOCK LAG\" investigation): record a wall-clock timestamp at "
      "every MacTx/MacTxDrop/MacRx/MacRxDrop/PhyTxBegin trace firing into "
      "an in-memory timeline, and sample sim_lag_s every 0.5s instead of "
      "the default 5s. Default false: with this off, behavior and cost "
      "are byte-for-byte identical to before this probe existed.",
      g_stageTiming);
  cmd.Parse(argc, argv);
  if (layout != "grid" && layout != "circle")
  {
    NS_FATAL_ERROR("--layout must be 'grid' or 'circle', got '" << layout << "'");
  }
  if (layout == "circle" && circleRadius <= 0.0)
  {
    NS_FATAL_ERROR("--circleRadius must be positive when --layout=circle");
  }
  static const std::map<std::string, std::string> kSchedulerTypeIds = {
      {"map", "ns3::MapScheduler"},
      {"heap", "ns3::HeapScheduler"},
      {"list", "ns3::ListScheduler"},
      {"calendar", "ns3::CalendarScheduler"},
      {"priority", "ns3::PriorityQueueScheduler"},
  };
  auto schedulerTypeIdIt = kSchedulerTypeIds.find(schedulerType);
  if (schedulerTypeIdIt == kSchedulerTypeIds.end())
  {
    NS_FATAL_ERROR(
        "--scheduler must be one of map/heap/list/calendar/priority, got '" << schedulerType
                                                                             << "'");
  }
  if (realtimeHardLimitS < 0.0)
  {
    NS_FATAL_ERROR("--realtimeHardLimitS must be non-negative");
  }

  // Must happen before ANY ns-3 random variable is constructed (every
  // Wifi PHY/MAC backoff/collision random draw included) -- ns-3's own
  // documentation requires SetSeed()/SetRun() to run first. This does
  // NOT eliminate this harness's full run-to-run variance by itself: it
  // only controls ns-3's own RNG stream, not the real-Linux-process/
  // RealtimeSimulatorImpl wall-clock scheduling jitter this TapBridge
  // pipeline is also subject to (see fleetqox_trace_replay.cc's
  // --realtime flag and the causal-isolation tests using it) -- see the
  // tracking doc for the same-seed/same-run repeatability sanity check
  // this was added to enable.
  RngSeedManager::SetSeed(seed);
  RngSeedManager::SetRun(run);

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

  // Event-scheduler choice: a pure internal event-ordering data-structure
  // swap, never changes which events fire or their simulated-time order,
  // so this cannot alter simulated network semantics/results (see
  // --scheduler's own doc comment above and docs/AUDIT_ACCEPTANCE_TRACKING.md,
  // "N=16 SERIOUS PERFORMANCE PASS"). Must be called before Simulator::Run()
  // -- doing it here, right after the SimulatorImplementationType bind and
  // before any topology/device/application object is built (hence before
  // any event is scheduled), satisfies that ordering requirement.
  {
    ObjectFactory schedulerFactory;
    schedulerFactory.SetTypeId(schedulerTypeIdIt->second);
    Simulator::SetScheduler(schedulerFactory);
  }

  // Realtime hard-limit safety gate (measurement safety, not a performance
  // change -- see --realtimeHardLimitS's own doc comment above). Left at
  // ns-3's default SYNC_BEST_EFFORT when the flag is 0 (unset), matching
  // this program's original, always-silently-falls-behind behavior.
  if (realtimeHardLimitS > 0.0)
  {
    Ptr<RealtimeSimulatorImpl> realtimeImpl =
        DynamicCast<RealtimeSimulatorImpl>(Simulator::GetImplementation());
    if (!realtimeImpl)
    {
      NS_FATAL_ERROR(
          "--realtimeHardLimitS requires ns3::RealtimeSimulatorImpl, but "
          "Simulator::GetImplementation() did not return one");
    }
    realtimeImpl->SetHardLimit(Seconds(realtimeHardLimitS));
    realtimeImpl->SetSynchronizationMode(RealtimeSimulatorImpl::SYNC_HARD_LIMIT);
  }

  // Reference-topology endpoint set: ONE control_station (merges the old
  // fleet_controller/operator_ui roles -- see docs/AUDIT_ACCEPTANCE_TRACKING.md
  // "kịch bản mô phỏng theo sơ đồ tham chiếu") plus numRobots robots,
  // matching the diagram's "16 robots + 1 control station, 17 total
  // endpoints" exactly, instead of the old 3-fixed-role+robots layout.
  std::vector<std::string> stationEndpointLabels = {"control_station"};
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
    // Explicit LogDistancePropagationLossModel rather than
    // YansWifiChannelHelper::Default()'s own propagation model (a
    // different model entirely, RangePropagationLossModel -- Default()
    // only supplies a ConstantSpeedPropagationDelayModel, no loss model
    // suitable for this scenario), matching the reference diagram's
    // indoor path-loss exponent exactly.
    // LogDistancePropagationLossModel's own default ReferenceLoss
    // (46.6777 dB) is a Friis free-space loss AT 1m calculated for
    // ~5.15 GHz, not 802.11g's actual 2.4 GHz carrier -- confirmed as a
    // real bug via a 2-station link-budget test: circleRadius=7.5m (15m
    // worst-case station separation) delivered ZERO packets with the
    // default ReferenceLoss, but worked once this was corrected.
    // Friis reference loss at 1m for 2.4 GHz: 20*log10(4*pi*f/c) =
    // 20*log10(4*pi*2.4e9/3e8) ~= 40.05 dB -- about 6.6 dB LOWER than
    // the 5.15 GHz default, meaning every link in this scenario was
    // suffering ~6.6 dB of unrealistic extra loss versus a physically
    // accurate 2.4 GHz deployment.
    constexpr double kReferenceLoss2_4GhzDb = 40.05;
    YansWifiChannelHelper channelHelper;
    channelHelper.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
    channelHelper.AddPropagationLoss(
        "ns3::LogDistancePropagationLossModel", "Exponent", DoubleValue(pathLossExponent),
        "ReferenceLoss", DoubleValue(kReferenceLoss2_4GhzDb));
    YansWifiPhyHelper phy;
    phy.SetChannel(channelHelper.Create());
    // Reference diagram: Tx power 15 dBm (STA and AP), Rx sensitivity
    // -82 dBm -- explicit rather than ns-3's own WifiPhy defaults (a
    // different, unrelated set of values with no connection to this
    // scenario's spec).
    phy.Set("TxPowerStart", DoubleValue(txPowerDbm));
    phy.Set("TxPowerEnd", DoubleValue(txPowerDbm));
    phy.Set("RxSensitivity", DoubleValue(rxSensitivityDbm));

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

  MobilityHelper stationMobility;
  double apX = 0.0;
  double apY = 0.0;
  if (layout == "circle")
  {
    // Reference-diagram layout: every station (control_station AND every
    // robot) placed evenly around a circle of --circleRadius centered on
    // the AP at the origin -- "all nodes within Wi-Fi coverage, robots
    // placed on a circle" per the diagram's Node Placement panel. Unlike
    // the grid layout, position here doesn't depend on totalStations'
    // square root, so density stays constant (same radius) as the fleet
    // scales, matching a fixed physical deployment radius rather than a
    // layout that spreads out further for a bigger fleet.
    Ptr<ListPositionAllocator> circlePositions = CreateObject<ListPositionAllocator>();
    for (uint32_t i = 0; i < totalStations; ++i)
    {
      const double angle = 2.0 * M_PI * static_cast<double>(i) / static_cast<double>(totalStations);
      circlePositions->Add(
          Vector(circleRadius * std::cos(angle), circleRadius * std::sin(angle), 0.0));
    }
    stationMobility.SetPositionAllocator(circlePositions);
    stationMobility.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
    stationMobility.Install(stations);
    for (uint32_t i = 0; i < stations.GetN(); ++i)
    {
      Ptr<ConstantVelocityMobilityModel> model =
          stations.Get(i)->GetObject<ConstantVelocityMobilityModel>();
      // Tangential velocity (perpendicular to the station's own radius
      // vector) rather than grid layout's fixed +/-X direction -- an
      // outward/inward radial direction would immediately change every
      // station's distance-to-AP in lockstep, which isn't a meaningful
      // "stations moving" scenario; tangential motion is the natural
      // choice for a circular deployment (see kịch bản S3 -- this is a
      // simple placeholder motion model, not yet the diagram's own
      // waypoint mobility, which is separate follow-up work).
      const double angle = 2.0 * M_PI * static_cast<double>(i) / static_cast<double>(totalStations);
      const double direction = (i % 2 == 0) ? 1.0 : -1.0;
      model->SetVelocity(Vector(
          -std::sin(angle) * direction * mobilitySpeed, std::cos(angle) * direction * mobilitySpeed,
          0.0));
    }
  }
  else
  {
    // Original grid-position formula (see fleetqox_trace_replay.cc's
    // non-roaming wifi branch), kept as an opt-in fallback via
    // --layout=grid for anything that depended on the old topology.
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
    apX = std::ceil(std::sqrt(static_cast<double>(totalStations))) * stationSpacing / 2.0;
    apY = apX;
  }

  // All APs sit at the same physical position -- each is on its OWN
  // non-interfering channel object (see the numAps setup above), so
  // co-location is a realistic dense-deployment pattern here (multiple
  // APs in one room on different channels), and physical placement only
  // matters for propagation loss WITHIN a channel, not across channels.
  // Circle layout centers the AP at the origin (apX=apY=0.0, set above);
  // grid layout keeps its original grid-center placement.
  MobilityHelper apMobility;
  apMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  apMobility.Install(accessPoints);
  for (uint32_t g = 0; g < accessPoints.GetN(); ++g)
  {
    accessPoints.Get(g)->GetObject<MobilityModel>()->SetPosition(Vector(apX, apY, 0.0));
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
  // retry loop's own ARP broadcasts adding to the contention). Every
  // hook is now bound to that device's own label ("who") -- see the
  // "N=8 REMAINING WI-FI LOSS AFTER DIRECTED-REPLY" doc comment above
  // g_droppedMpduByReason for why per-station identity is needed here
  // (it wasn't before: every station shared the same unlabeled global
  // callback).
  for (uint32_t i = 0; i < stationDevices.GetN(); ++i)
  {
    Ptr<WifiNetDevice> dev = DynamicCast<WifiNetDevice>(stationDevices.Get(i));
    const std::string& who = stationEndpointLabels[i];
    Ptr<WifiMac> wmac = dev->GetMac();
    // These 6 are the ORIGINAL, pre-"N=8 REMAINING WI-FI LOSS" hooks --
    // cheap atomic-only counters even before this pass, connected
    // unconditionally (matches this program's behavior for every prior
    // investigation that used it).
    wmac->TraceConnectWithoutContext("MacTx", MakeBoundCallback(&MacTxTrace, who));
    wmac->TraceConnectWithoutContext("MacTxDrop", MakeBoundCallback(&MacTxDropTrace, who));
    wmac->TraceConnectWithoutContext("MacRx", MakeBoundCallback(&MacRxTrace, who));
    wmac->TraceConnectWithoutContext("MacRxDrop", MakeBoundCallback(&MacRxDropTrace, who));
    dev->GetPhy()->TraceConnectWithoutContext("PhyTxBegin", MakeBoundCallback(&PhyTxBeginTrace, who));
    dev->GetPhy()->TraceConnectWithoutContext("PhyRxDrop", MakeBoundCallback(&PhyRxDropTrace, who));
    // Everything below is new in the "N=8 REMAINING WI-FI LOSS AFTER
    // DIRECTED-REPLY" pass -- gated behind --heavyTracing (default
    // false) for the "N=8 REALTIME-LAG VALIDATION" investigation (see
    // g_heavyTracing's doc comment): not connecting these trace sources
    // at all when false, not just skipping their callback bodies, so
    // --heavyTracing=false reproduces exactly this program's PRE-that-
    // pass behavior.
    if (g_heavyTracing)
    {
      wmac->TraceConnectWithoutContext("DroppedMpdu", MakeBoundCallback(&DroppedMpduTrace, who));
      wmac->GetWifiRemoteStationManager()->TraceConnectWithoutContext(
          "MacTxFinalDataFailed", MakeBoundCallback(&MacTxFinalDataFailedTrace, who));
      Ptr<Txop> txop = wmac->GetTxop();
      txop->TraceConnectWithoutContext("BackoffTrace", MakeBoundCallback(&BackoffValueTrace, who));
      txop->TraceConnectWithoutContext("CwTrace", MakeBoundCallback(&CwValueTrace, who));
      txop->GetWifiMacQueue()->TraceConnectWithoutContext(
          "Expired", MakeBoundCallback(&WifiMacQueueExpiredTrace, who));
      g_queueBacklogTargets.emplace_back(who, txop->GetWifiMacQueue());
    }
  }
  for (uint32_t i = 0; i < apDevices.GetN(); ++i)
  {
    Ptr<WifiNetDevice> dev = DynamicCast<WifiNetDevice>(apDevices.Get(i));
    const std::string who = "AP" + std::to_string(i);
    Ptr<WifiMac> wmac = dev->GetMac();
    wmac->TraceConnectWithoutContext("MacTx", MakeBoundCallback(&MacTxTrace, who));
    wmac->TraceConnectWithoutContext("MacTxDrop", MakeBoundCallback(&MacTxDropTrace, who));
    wmac->TraceConnectWithoutContext("MacRx", MakeBoundCallback(&MacRxTrace, who));
    wmac->TraceConnectWithoutContext("MacRxDrop", MakeBoundCallback(&MacRxDropTrace, who));
    dev->GetPhy()->TraceConnectWithoutContext("PhyTxBegin", MakeBoundCallback(&PhyTxBeginTrace, who));
    dev->GetPhy()->TraceConnectWithoutContext("PhyRxDrop", MakeBoundCallback(&PhyRxDropTrace, who));
    if (g_heavyTracing)
    {
      wmac->TraceConnectWithoutContext("DroppedMpdu", MakeBoundCallback(&DroppedMpduTrace, who));
      wmac->GetWifiRemoteStationManager()->TraceConnectWithoutContext(
          "MacTxFinalDataFailed", MakeBoundCallback(&MacTxFinalDataFailedTrace, who));
      Ptr<Txop> txop = wmac->GetTxop();
      txop->TraceConnectWithoutContext("BackoffTrace", MakeBoundCallback(&BackoffValueTrace, who));
      txop->TraceConnectWithoutContext("CwTrace", MakeBoundCallback(&CwValueTrace, who));
      txop->GetWifiMacQueue()->TraceConnectWithoutContext(
          "Expired", MakeBoundCallback(&WifiMacQueueExpiredTrace, who));
      g_queueBacklogTargets.emplace_back(who, txop->GetWifiMacQueue());
    }
    Ptr<ApWifiMac> apMac = DynamicCast<ApWifiMac>(wmac);
    apMac->TraceConnectWithoutContext("AssociatedSta", MakeCallback(&AssociatedStaTrace));
  }

  Simulator::Schedule(Seconds(5.0), &PrintWifiStats, totalStations, numAps);
  if (g_heavyTracing)
  {
    Simulator::Schedule(Seconds(2.0), &PrintQueueBacklog);
  }
  if (g_stageTiming)
  {
    // Reserve upfront (see DumpStageTimingSummary's own comment on
    // expected event volume) so normal operation never pays a
    // reallocation cost mid-run -- one-time, at startup, not per-event.
    g_stageTimeline.reserve(400000);
    g_fineLagSamples.reserve(400);
    Simulator::Schedule(Seconds(0.5), &FineLagSample);
  }

  Simulator::Stop(Seconds(simDuration));
  Simulator::Run();
  PrintWifiStats(totalStations, numAps);
  Simulator::Destroy();

  return 0;
}
