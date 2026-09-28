// Deterministic regression test for the P2.5/P2.6 cross-host clock bug:
// FleetRMW's QoS Lifespan expiry used to compare a publisher's
// CLOCK_MONOTONIC-based source_timestamp_ns against a subscriber's own
// CLOCK_MONOTONIC "now" -- meaningless once publisher and subscriber are
// separate processes (let alone separate physical hosts), since
// CLOCK_MONOTONIC has an arbitrary, unrelated per-boot epoch in each.
//
// The fix (see rmw_pubsub.cpp / rmw_stubs.cpp / data_frame.hpp) switches the
// WIRE-VISIBLE source_timestamp_ns (and the "now" compared against it) to
// wall_clock_timestamp_ns() (CLOCK_REALTIME). This makes correctness
// ACHIEVABLE across hosts, but NOT unconditional: it depends on the hosts'
// CLOCK_REALTIME actually being disciplined (NTP), the same standing
// assumption this repo's own fleetqox_coordination_endpoint.py already
// relies on for its cross-process wall_ns comparisons, and one that
// CLOCK_MONOTONIC could NEVER satisfy even in principle (its skew across
// processes is not just "usually large", it is UNDEFINED by POSIX).
//
// TWO TIERS, DELIBERATELY KEPT SEPARATE:
//
//   TIER 1 (realistic NTP-bounded skew, +-20ms -- generous for real NTP,
//   tiny next to this system's >=1s Lifespan values): every combination of
//   {fresh, genuinely-expired} x {pub/sub, service-request,
//   service-response} must be GREEN. This is what the fix actually claims
//   to achieve, and what it is tested against here.
//
//   TIER 2 (pathological +-100s skew, i.e. NTP has failed outright): the
//   SAME two failure modes as the original bug (false-drop of fresh data
//   when the subscriber's clock leads; silent lifespan-bypass when it lags)
//   are demonstrated to PERSIST. This is not a defect in this fix -- no
//   software-only change can recover "true elapsed wall time between two
//   events on different machines" when those machines' clocks are not
//   synchronized to begin with. That recovery is what NTP/PTP exist to do,
//   external to this RMW. Tier 2 checks are reported separately and are NOT
//   counted as pass/fail against the fix -- they document a known,
//   unavoidable limit of ANY wall-clock-based design, disclosed rather than
//   hidden.
//
// This test exercises:
//   (a) The REAL, unmodified, linked production functions
//       service_frame_expired()/ServiceFrame for the service scenarios --
//       data_frame.cpp has zero ROS/rclcpp dependencies and is directly
//       link-testable.
//   (b) A byte-for-byte formula copy of rmw_pubsub.cpp's post-fix
//       frame_exceeds_lifespan() for the pub/sub scenarios.
//       frame_exceeds_lifespan() itself is NOT extracted here because it
//       lives inside the ~19k-line rmw_pubsub.cpp translation unit
//       alongside the full RMW implementation (rosidl/rclcpp type support,
//       transports, etc.) and is not currently link-isolatable without a
//       full ROS 2 workspace build. If that function's three-line body
//       (`now > ts && now - ts > lifespan_ns`) is ever edited, this copy
//       must be updated to match -- ideally by moving frame_exceeds_
//       lifespan() into data_frame.cpp alongside its Service/Action
//       siblings so it becomes genuinely link-testable, which this change
//       intentionally does NOT attempt (out of scope: "do not change
//       unrelated timing/retransmission logic").
//
// All timestamps are synthetic, hand-picked int64 nanosecond values
// representing hypothetical independent hosts' wall clocks -- the test's
// outcome does not depend on when it is actually run.

#include "rmw_fleetqox_cpp/data_frame.hpp"

#include <cstdio>
#include <cstdlib>

using rmw_fleetqox_cpp::ServiceFrame;
using rmw_fleetqox_cpp::service_frame_expired;

namespace
{

int g_failures = 0;
int g_checks = 0;

void check(bool condition, const char * description)
{
  ++g_checks;
  std::printf("[%s] %s\n", condition ? "PASS" : "FAIL", description);
  if (!condition) {
    ++g_failures;
  }
}

void info(bool matches_known_limitation, const char * description)
{
  std::printf(
    "[%s] %s\n", matches_known_limitation ? "EXPECTED-LIMITATION" : "UNEXPECTED", description);
}

constexpr std::int64_t kSecond = 1'000'000'000LL;
constexpr std::int64_t kMillisecond = 1'000'000LL;

// Byte-for-byte copy of rmw_pubsub.cpp's post-fix frame_exceeds_lifespan()
// body (now takes wall-clock "now" as an explicit parameter purely so this
// test can drive it deterministically -- see file header).
bool data_frame_exceeds_lifespan_formula(
  std::int64_t lifespan_ns, std::int64_t source_timestamp_ns, std::int64_t wall_now_ns)
{
  if (lifespan_ns <= 0 || source_timestamp_ns <= 0) {
    return false;
  }
  return wall_now_ns > source_timestamp_ns && wall_now_ns - source_timestamp_ns > lifespan_ns;
}

bool pubsub_expired(std::int64_t lifespan_ns, std::int64_t sent_ns, std::int64_t recv_ns)
{
  return data_frame_exceeds_lifespan_formula(lifespan_ns, sent_ns, recv_ns);
}

bool service_expired(
  const char * role, std::int64_t lifespan_ns, std::int64_t sent_ns, std::int64_t recv_ns)
{
  ServiceFrame frame{};
  frame.role = role;
  frame.source_timestamp_ns = sent_ns;
  frame.lifespan_ns = lifespan_ns;
  return service_frame_expired(frame, recv_ns);
}

}  // namespace

int main()
{
  const std::int64_t lifespan_ns = 2 * kSecond;

  std::printf(
    "================ TIER 1: realistic NTP-bounded skew (+-20ms) ================\n");
  std::printf("This is what the fix claims to achieve; all checks below must be GREEN.\n\n");

  const std::int64_t skew = 20 * kMillisecond;

  struct Case
  {
    const char * label;
    std::int64_t sent_ns;
    std::int64_t recv_ns;
    bool expect_expired;
  };

  // fresh: ~0 real transit delay. genuinely_expired: 3s real elapsed,
  // 1s past the 2s lifespan -- comfortably outside the +-20ms skew band.
  const Case tier1_cases[] = {
    {"fresh, subscriber clock +20ms ahead", 5 * kSecond, 5 * kSecond + skew, false},
    {"fresh, subscriber clock -20ms behind", 5 * kSecond, 5 * kSecond - skew, false},
    {"genuinely expired (3s old, 2s lifespan), subscriber +20ms ahead",
      5 * kSecond, 5 * kSecond + 3 * kSecond + skew, true},
    {"genuinely expired (3s old, 2s lifespan), subscriber -20ms behind",
      5 * kSecond, 5 * kSecond + 3 * kSecond - skew, true},
  };

  const struct
  {
    const char * name;
    const char * role;
    bool is_pubsub;
  } protocols[] = {
    {"pub/sub", nullptr, true},
    {"service REQUEST", "request", false},
    {"service RESPONSE", "response", false},
  };

  for (const auto & proto : protocols) {
    std::printf("--- %s ---\n", proto.name);
    for (const auto & c : tier1_cases) {
      const bool expired = proto.is_pubsub ?
        pubsub_expired(lifespan_ns, c.sent_ns, c.recv_ns) :
        service_expired(proto.role, lifespan_ns, c.sent_ns, c.recv_ns);
      char desc[256];
      std::snprintf(
        desc, sizeof(desc), "%s: %s -> %s (expected %s)",
        proto.name, c.label, expired ? "EXPIRED" : "kept",
        c.expect_expired ? "EXPIRED" : "kept");
      check(expired == c.expect_expired, desc);
    }
    std::printf("\n");
  }

  std::printf(
    "================ TIER 2: pathological skew (+-100s, i.e. broken NTP) "
    "================\n");
  std::printf(
    "These are NOT claimed fixed -- no wall-clock-only design can survive an\n"
    "unsynchronized clock this badly off. Shown to make the limit explicit,\n"
    "not to hide it.\n\n");

  const std::int64_t bad_skew = 100 * kSecond;
  const Case tier2_cases[] = {
    {"fresh (zero transit delay), subscriber clock +100s ahead",
      5 * kSecond, 5 * kSecond + bad_skew, false},
    {"genuinely expired (10s old, 2s lifespan), subscriber clock -100s behind",
      200 * kSecond, 200 * kSecond + 10 * kSecond - bad_skew, true},
  };

  for (const auto & proto : protocols) {
    std::printf("--- %s ---\n", proto.name);
    for (const auto & c : tier2_cases) {
      const bool expired = proto.is_pubsub ?
        pubsub_expired(lifespan_ns, c.sent_ns, c.recv_ns) :
        service_expired(proto.role, lifespan_ns, c.sent_ns, c.recv_ns);
      char desc[256];
      std::snprintf(
        desc, sizeof(desc), "%s: %s -> %s (true answer: %s)",
        proto.name, c.label, expired ? "EXPIRED" : "kept",
        c.expect_expired ? "EXPIRED" : "kept");
      // A Tier 2 check "matches the known limitation" when the wrong
      // answer comes out -- i.e. when it reproduces the same failure mode
      // as the original bug. That is the expected, disclosed outcome here.
      info(expired != c.expect_expired, desc);
    }
    std::printf("\n");
  }

  std::printf(
    "TIER 1: %d/%d checks passed.\n", g_checks - g_failures, g_checks);
  std::printf(
    g_failures == 0 ?
    "TIER 1 RESULT: GREEN -- fix is correct under realistic NTP-bounded skew, "
    "for both fresh and genuinely-expired frames, across pub/sub and both "
    "service directions.\n" :
    "TIER 1 RESULT: RED -- fix does not hold even under realistic skew "
    "(this is a real defect, unlike Tier 2).\n");
  std::printf(
    "TIER 2 is informational only (not counted above): it documents that "
    "synchronized wall clocks (NTP/PTP) are a hard prerequisite this RMW "
    "cannot substitute for in software.\n");
  return g_failures > 0 ? 1 : 0;
}
