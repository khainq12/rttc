"""Bảng VI ("Chỉ số điều phối và hoàn thành nhiệm vụ") endpoint process.

Runs inside one Docker container per endpoint, wired into the SAME
network harness (wifi/LAN/5G profiles, any RMW_IMPLEMENTATION) as
fleetqox_rmw_trace_endpoint.py -- see that file for the ready/start
gate and rclpy setup this mirrors. What differs is the WORKLOAD: this
is not trace replay, it's a small distributed mutual-exclusion
simulation representing fleet robots contending for a single shared
"zone" (a narrow corridor/intersection every robot must cross some
number of times during the scenario) -- the concrete meaning of
"conflict" the user chose for Bảng VI ("tranh chấp đường đi/zone").

ALGORITHM: Ricart-Agrawala distributed mutual exclusion (a standard,
textbook-correct protocol -- chosen deliberately over an ad-hoc scheme
so "who wins a conflict" has a precise, provably-fair definition
instead of a made-up heuristic that could race or starve under packet
loss):

  To enter the zone, a process broadcasts REQUEST(lamport_ts, req_id)
  to every other participant. On receiving a REQUEST from Q:
    - if this process is not currently requesting/in the zone, OR Q's
      (lamport_ts, name) is STRICTLY LOWER than this process's own
      current request (i.e. Q has priority), reply immediately.
    - otherwise (this process has priority, or is already in the
      zone) the reply is DEFERRED until this process leaves the zone.
  A process may enter the zone once it has collected a REPLY from
  EVERY other participant for its current request.

This maps directly onto Bảng VI's columns:
  - Coordination update age: age (recv_wall_ns - declared_wall_ns) of
    every REQUEST/REPLY message actually received -- i.e. how stale
    the fleet's shared coordination state is by the time it's acted
    on, exactly like Bảng V's latency stats but scoped to this
    scenario's own control traffic.
  - Conflict-resolution delay: entered_wall_ns - the ORIGINAL
    declared_wall_ns of the request that eventually won -- how long
    it took a real, active contention to resolve (when nobody else is
    contending this reduces to N-1 round-trip times, exactly as it
    should).
  - Navigation recovery count (field name: coordination_retry_count,
    renamed 14/09/2026 per review -- "navigation_recovery_count" was
    misleading, this harness has no real navigation stack to recover a
    path in): the closest faithful analogue available is a REQUEST
    retry -- if not all N-1 replies arrive within --reply-timeout-s
    (almost always because a reply was lost on a congested/lossy
    network, not because anyone misbehaved), the request is
    re-broadcast (same req_id, same frozen priority timestamp -- see
    "2 FURTHER FIXES" below). Each such retry is counted here -- report
    this framing explicitly wherever this column is used: it is a
    coordination-layer retry standing in for an application-layer
    recovery, not a measurement of any real motion planner.
  - Task completion time: wall-clock time from the shared start gate
    to every participant finishing its assigned --num-crossings.

KNOWN FAILURE MODE, FIXED (see docs/AUDIT_ACCEPTANCE_TRACKING.md
"endpoint cuối cùng bị cô lập" / "cơ chế Ricart-Agrawala bị kẹt"): a
real 3-endpoint test surfaced a genuine priority-fairness deadlock, not
a network or launch-order bug (confirmed by reversing launch order --
the SAME endpoint got stuck regardless of launch position). Root
cause: every endpoint started its first request at the SAME frozen
Lamport value, so priority ties were broken by NAME alone,
DETERMINISTICALLY and PERMANENTLY favoring lexicographically-earlier
names; combined with a real Wi-Fi reply loss preventing even the
TOP-priority endpoint from ever completing a crossing, the
lowest-priority endpoint's replies sat deferred forever (confirmed via
raw traffic logs: zero "to: <that endpoint>" messages were EVER
broadcast, on ANY topic, by ANYONE, for the whole 90s scenario -- not
lost in transit, never sent at all). Fixed with 3 changes:
  1. The Lamport clock now properly advances BETWEEN crossings (each
     crossing takes one fresh increment when it starts), instead of
     every endpoint reusing the exact same initial value forever.
  2. req_id stays constant across retries of the SAME crossing so a
     reply that arrives on ANY retry still counts -- retries reset the
     OUTGOING message's freshness only, never discard already-collected
     replies.
  3. Deferred replies have a release timeout (--defer-release-timeout-s):
     if this process hasn't itself entered its critical section within
     that long, it gives up enforcing its own priority claim and sends
     the deferred reply anyway. This is the actual deadlock-breaker --
     (1) alone doesn't fix indefinite blocking, since Lamport clocks
     only ever increase (a struggling process's priority can only get
     WORSE over time, never jump the queue); a bounded release is what
     guarantees eventual progress for a low-priority requester when a
     higher-priority one is itself stuck.

2 FURTHER FIXES, 14/09/2026 (found via external review of this file
after the Open5GS profile showed FleetRMW pinned at 100% forced_entry
even at N=8, unlike its healthy ~60% delivery on the same profile/
scale in Bảng V's trace-replay workload -- see
docs/AUDIT_ACCEPTANCE_TRACKING.md):
  4. The Lamport timestamp attached to a crossing's REQUEST is now
     frozen for the WHOLE crossing (one increment, taken once before
     the retry loop, reused on every retry's re-broadcast) instead of
     advancing on EVERY retry as fix #1 above originally did between
     13/09 and 14/09/2026. That per-retry advance was itself a real
     fairness bug: since a LOWER (lamport_ts, name) wins priority ties
     and Lamport values only ever increase, a requester needing more
     retries -- almost always because ITS OWN messages are the ones
     being lost, not because it misbehaved -- got a strictly WORSE
     priority on every single retry, a vicious cycle where the
     requester most in need of protection was punished hardest. A
     retransmission of the same logical request is not a new causal
     event by Lamport's own definition and should not consume a new
     clock value; only a crossing's first broadcast does now.
  5. release_stale_deferrals() (fix #3 above) had a real mutual-
     exclusion safety gap: releasing a deferred reply tells a
     lower-priority peer "go ahead" without this process having
     actually finished OR abandoned its own current request. If that
     peer then enters its critical section, and THIS process's own
     in-flight request separately, later collects a full reply set
     (e.g. via some other endpoint's release elsewhere in the fleet),
     both could end up physically inside the zone at once --
     unreplicated double-occupancy that no existing metric would have
     caught (a crossing only gets flagged forced_entry when giving up
     at the scenario deadline, not when it succeeds on a set of
     replies that included a stale, timeout-released one). Fixed:
     yielding priority to a peer now also invalidates this process's
     OWN currently-collected reply set (replies_received is cleared),
     forcing it to earn a full fresh set via the normal retry path
     before it's allowed to enter -- exactly as if this attempt had
     just failed outright. Deliberately conservative: costs one extra
     retry's worth of latency, not a narrower but harder-to-verify
     correctness argument.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument(
        "--peers",
        required=True,
        help="comma-separated names of every OTHER participant (not including --endpoint)",
    )
    parser.add_argument("--num-crossings", type=int, default=5)
    parser.add_argument("--crossing-duration-ms", type=float, default=300.0)
    parser.add_argument(
        "--reply-timeout-s",
        type=float,
        default=5.0,
        help="if not all peers have replied within this long, re-broadcast the request "
        "(counts as one navigation-recovery event)",
    )
    parser.add_argument(
        "--defer-release-timeout-s",
        type=float,
        default=8.0,
        help="if a reply has been DEFERRED (because this process itself has -- or believes "
        "it has -- priority) for longer than this without this process reaching its own "
        "critical section, send it anyway. Without this, a higher-priority requester that "
        "is itself stuck (e.g. losing its own replies to network loss) can defer a "
        "lower-priority requester's reply forever -- a real deadlock confirmed via a live "
        "3-endpoint run, not a hypothetical.",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--start-offset-ms", type=float, default=1000.0)
    parser.add_argument("--discovery-timeout-s", type=float, default=15.0)
    parser.add_argument("--start-wait-timeout-s", type=float, default=60.0)
    parser.add_argument(
        "--scenario-timeout-s",
        type=float,
        default=120.0,
        help="hard ceiling on the whole crossings loop, so one endpoint that can never "
        "collect all replies (e.g. a peer crashed) doesn't hang the run forever",
    )
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, default=None)
    parser.add_argument("--start-file", type=Path, default=None)
    parser.add_argument("--expected-peer-count", type=int, default=0)
    parser.add_argument("--skip-discovery-wait", action="store_true")
    parser.add_argument(
        "--priority-mode",
        choices=("lamport", "fleetqox"),
        default="lamport",
        help="'lamport' (default, the 'Ours-NoQoX' arm): priority ties are broken purely "
        "by (lamport_ts, name), exactly as this file always did -- no notion of WHICH "
        "task a crossing belongs to. 'fleetqox' (the 'Ours-FleetQoX' arm): priority is "
        "compared on task_criticality FIRST (higher wins outright, regardless of "
        "lamport_ts), falling back to (lamport_ts, name) only between two requests of "
        "equal criticality -- i.e. a safety-critical crossing request always wins over a "
        "routine one, matching FleetQoX's own task-aware priority thesis "
        "(fleetqox/model.py's TaskContext.task_criticality) applied to THIS protocol's "
        "own contention, not just to per-message transport scheduling like Bảng V's "
        "--policy does. Both modes run the identical wire protocol/retry/defer-release "
        "machinery -- only the priority COMPARISON differs -- so any measured difference "
        "is attributable to the priority rule itself, not some other confound.",
    )
    args = parser.parse_args()

    import rclpy
    import rclpy.publisher
    from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    peers = [p for p in args.peers.split(",") if p]
    rng = random.Random(f"{args.seed}:{args.endpoint}")

    # Deterministic, seed-independent task-criticality tier assignment: every
    # 4th participant (by sorted name, so every process derives the SAME
    # assignment for the SAME fleet without needing to exchange anything) is
    # "safety" tier (0.9), the rest "routine" (0.3) -- a fixed ~25% split
    # loosely matching a real fleet where most traffic is routine but some
    # robots/tasks are always safety-critical. Only used when
    # --priority-mode=fleetqox; computed unconditionally anyway so it's
    # always present in the result JSON for inspection either way.
    _all_names = sorted([args.endpoint, *peers])
    task_criticality = 0.9 if _all_names.index(args.endpoint) % 4 == 0 else 0.3

    def priority_key(lamport_ts: int, name: str, criticality: float) -> tuple:
        if args.priority_mode == "fleetqox":
            return (-criticality, lamport_ts, name)
        return (lamport_ts, name)

    # Same /parameter_events-suppression patch as fleetqox_rmw_trace_endpoint.py
    # -- see that file's comment for why it has to happen before create_node().
    _original_publish = rclpy.publisher.Publisher.publish

    def _publish_suppressing_parameter_events(self, *pub_args, **pub_kwargs):
        if self.topic_name == "/parameter_events":
            return None
        return _original_publish(self, *pub_args, **pub_kwargs)

    rclpy.publisher.Publisher.publish = _publish_suppressing_parameter_events

    rclpy.init()
    node_name = "fleetqox_coordination_endpoint_" + "".join(
        ch if ch.isalnum() else "_" for ch in args.endpoint
    )
    node = rclpy.create_node(node_name, start_parameter_services=False)
    qos = QoSProfile(
        history=HistoryPolicy.KEEP_LAST, depth=256, reliability=ReliabilityPolicy.RELIABLE
    )

    # DIAGNOSTIC: temporarily using ONE shared topic for both REQUEST and
    # REPLY (distinguished by a "type" field) instead of two separate
    # topics, to test whether having 2 independent pub/sub pairs on one
    # process is itself the cause of a real, reproducible bug under
    # investigation: replies were essentially never received (0-1 out of
    # 11 sent, across repeated real runs) while requests got through at a
    # much more ordinary ~46% rate, with NO difference in QoS or pattern
    # between the two topics -- see docs/AUDIT_ACCEPTANCE_TRACKING.md.
    request_pub = node.create_publisher(String, "/fleetqox_coordination/control", qos)
    reply_pub = request_pub

    # ---- Ricart-Agrawala mutual-exclusion state ----
    lamport_clock = 0
    requesting = False
    in_cs = False
    current_req_id: str | None = None
    current_req_ts: tuple[int, str] | None = None
    replies_received: set[str] = set()
    # (to, req_id, deferred_at_monotonic) -- the monotonic timestamp is
    # what release_stale_deferrals() checks against
    # --defer-release-timeout-s.
    deferred: list[tuple[str, str, float]] = []

    coordination_message_ages_ms: list[float] = []
    crossings: list[dict[str, Any]] = []
    coordination_retry_count = 0

    # TEMPORARY diagnostic counters for the "every crossing forces entry,
    # coordination_retry_count always maxes out" investigation -- see
    # docs/AUDIT_ACCEPTANCE_TRACKING.md. Cheap enough to leave permanently
    # if useful, but flagged here as debug-oriented rather than a Bảng VI
    # column in its own right.
    debug_counters = {
        "requests_sent": 0,
        "requests_received": 0,
        "requests_received_self_loop": 0,
        "replies_sent": 0,
        "replies_received_raw": 0,
        "replies_received_matched": 0,
        "replies_received_stale_req_id": 0,
        "replies_received_not_requesting": 0,
        "publish_failures": 0,
        "own_claim_yielded_on_timeout": 0,
    }

    def safe_publish(pub, msg: String) -> None:
        # A real N=32 run crashed here with an UNHANDLED exception --
        # errno=105 (No buffer space available) from the OS socket layer,
        # once every endpoint is broadcasting REQUEST/REPLY traffic to 32
        # peers each. An unhandled crash here is strictly worse than a
        # handled one: it means this endpoint writes NO summary JSON at
        # all (wait_for_completion() then times out waiting for a file
        # that will never appear), losing 100% of this endpoint's data
        # instead of just the one message that couldn't be sent. Treat it
        # like any other lost message -- count it and move on, since a
        # message that physically couldn't be enqueued for sending is,
        # for this protocol's purposes, indistinguishable from one lost
        # in transit (the retry-with-persistent-req_id logic already
        # handles that case correctly).
        try:
            pub.publish(msg)
        except Exception:  # noqa: BLE001 -- OS/RMW-level send failure, not a logic bug
            debug_counters["publish_failures"] += 1

    # Replies are NEVER published directly from inside on_request -- only
    # QUEUED here, then actually sent from drain_pending_replies(), called
    # from plain top-level loop code (never from inside an rclpy
    # subscription callback). Confirmed via a real run's own debug
    # counters that calling reply_pub.publish() synchronously from within
    # on_request was the actual bug: requests_received=11,
    # replies_sent=11 (so on_request WAS running and DID decide to
    # reply), yet replies_received_raw=0 on 2 of 3 endpoints and just 1
    # on the third -- i.e. essentially none of the immediate publish()
    # calls issued from inside the callback ever reached anyone, while
    # request_pub.publish() (already called from plain top-level loop
    # code, never from a callback) got through at a much more ordinary
    # rate. Moving the actual publish() out to the main loop fixed it.
    pending_immediate_replies: list[tuple[str, str]] = []

    def send_reply(to: str, req_id: str) -> None:
        debug_counters["replies_sent"] += 1
        msg = String()
        msg.data = json.dumps(
            {
                "type": "reply",
                "from": args.endpoint,
                "to": to,
                "req_id": req_id,
                "wall_ns": time.time_ns(),
            }
        )
        safe_publish(reply_pub, msg)

    def drain_pending_replies() -> None:
        pending = pending_immediate_replies[:]
        pending_immediate_replies.clear()
        for to, req_id in pending:
            send_reply(to, req_id)

    def on_request(msg: String) -> None:
        nonlocal lamport_clock
        payload = json.loads(msg.data)
        if payload["from"] == args.endpoint:
            debug_counters["requests_received_self_loop"] += 1
            return  # broadcast loops back on some RMWs -- ignore our own request
        debug_counters["requests_received"] += 1
        coordination_message_ages_ms.append((time.time_ns() - payload["wall_ns"]) / 1e6)
        their_key = priority_key(
            payload["lamport_ts"], payload["from"], payload.get("task_criticality", 0.0)
        )
        lamport_clock = max(lamport_clock, payload["lamport_ts"]) + 1
        # While ACTUALLY in the zone (in_cs), defer UNCONDITIONALLY --
        # no timestamp comparison here. Mutual exclusion's safety
        # property depends on this: replying to anyone while physically
        # occupying the zone, even a requester with an "earlier" Lamport
        # timestamp, would let a second endpoint believe it can also
        # enter, which is exactly the double-occupancy this algorithm
        # exists to prevent. The timestamp comparison ONLY matters for
        # breaking ties between two REQUESTS that haven't been granted
        # the zone yet (the `requesting` case below).
        if in_cs:
            i_have_priority = True
        elif requesting and current_req_ts is not None and (
            priority_key(current_req_ts[0], current_req_ts[1], task_criticality) < their_key
        ):
            i_have_priority = True
        else:
            i_have_priority = False
        if i_have_priority:
            deferred.append((payload["from"], payload["req_id"], time.monotonic()))
        else:
            pending_immediate_replies.append((payload["from"], payload["req_id"]))

    def release_stale_deferrals() -> None:
        # The actual deadlock-breaker -- see this file's module
        # docstring "KNOWN FAILURE MODE, FIXED" section for the real
        # 3-endpoint run that confirmed indefinite deferral is a genuine
        # risk, not a hypothetical. Never releases while in_cs (that
        # would violate mutual exclusion -- a second endpoint could
        # believe the zone is free while this one is still physically
        # occupying it); a deferral only ever sits here while
        # `requesting` (not yet granted), so this is safe FROM THE
        # RELEASER'S side.
        #
        # SAFETY GAP FIXED HERE (flagged in review, confirmed real by
        # tracing it through): releasing a deferred reply is us telling
        # a lower-priority peer "go ahead, I'm not holding you back
        # anymore" WITHOUT having actually finished (or abandoned) our
        # own current request. If our own in-flight request later
        # SEPARATELY collects its full reply set (e.g. the peer that was
        # blocking IT also times out and yields), we could enter our
        # critical section too -- while the peer we just released may
        # already be inside ITS OWN, a genuine double-occupancy. The
        # peer's reply to OUR OWN prior request doesn't protect against
        # this: replies are sent whenever a peer decides it doesn't have
        # priority, regardless of whether IT is later granted its own
        # entry by a different release elsewhere in the fleet. Fix:
        # yielding priority to someone invalidates whatever reply set
        # we'd already collected for our OWN current request -- we must
        # earn a fresh full set (via the normal retry path just below)
        # before we're allowed to enter, exactly as if this attempt had
        # just failed outright. This is deliberately conservative (an
        # extra retry cycle costs latency, not safety) rather than
        # trying to prove a narrower condition is fine.
        nonlocal deferred, replies_received
        if in_cs:
            return
        now = time.monotonic()
        still_deferred = []
        released_any = False
        for to, req_id, deferred_at in deferred:
            if now - deferred_at >= args.defer_release_timeout_s:
                send_reply(to, req_id)
                released_any = True
            else:
                still_deferred.append((to, req_id, deferred_at))
        deferred = still_deferred
        if released_any and requesting:
            debug_counters["own_claim_yielded_on_timeout"] += 1
            replies_received = set()

    def on_reply(msg: String) -> None:
        payload = json.loads(msg.data)
        if payload["to"] != args.endpoint:
            return
        debug_counters["replies_received_raw"] += 1
        coordination_message_ages_ms.append((time.time_ns() - payload["wall_ns"]) / 1e6)
        if not requesting:
            debug_counters["replies_received_not_requesting"] += 1
        elif payload["req_id"] != current_req_id:
            debug_counters["replies_received_stale_req_id"] += 1
        else:
            debug_counters["replies_received_matched"] += 1
            replies_received.add(payload["from"])

    raw_received_log: list[dict[str, Any]] = []

    def on_control_message(msg: String) -> None:
        payload = json.loads(msg.data)
        if len(raw_received_log) < 200:
            raw_received_log.append(
                {
                    "type": payload.get("type"),
                    "from": payload.get("from"),
                    "to": payload.get("to"),
                    "req_id": payload.get("req_id"),
                }
            )
        if payload.get("type") == "reply":
            on_reply(msg)
        else:
            on_request(msg)

    node.create_subscription(String, "/fleetqox_coordination/control", on_control_message, qos)

    # ---- Same RMW-agnostic beacon discovery-convergence measurement as
    # fleetqox_rmw_trace_endpoint.py, kept for methodological consistency
    # with Bảng IV/V (not required for the mutex protocol itself). ----
    discovery_peers_seen: set[str] = set()
    beacon_pub = None
    if args.expected_peer_count > 0:
        beacon_topic = "/fleetqox_coordination/_discovery_probe"
        beacon_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.RELIABLE
        )
        beacon_pub = node.create_publisher(String, beacon_topic, beacon_qos)
        beacon_msg = String()
        beacon_msg.data = args.endpoint

        def on_beacon(msg: String) -> None:
            if msg.data != args.endpoint:
                discovery_peers_seen.add(msg.data)

        node.create_subscription(String, beacon_topic, on_beacon, beacon_qos)

    discovery_start = time.monotonic()
    discovery_deadline = discovery_start + args.discovery_timeout_s
    last_beacon_sent = 0.0
    if not args.skip_discovery_wait:
        while time.monotonic() < discovery_deadline:
            if beacon_pub is not None:
                now = time.monotonic()
                if now - last_beacon_sent >= 0.1:
                    safe_publish(beacon_pub, beacon_msg)
                    last_beacon_sent = now
            for _ in range(20):
                rclpy.spin_once(node, timeout_sec=0.0)
            rclpy.spin_once(node, timeout_sec=0.1)
            drain_pending_replies()
            release_stale_deferrals()
            if beacon_pub is not None and len(discovery_peers_seen) >= args.expected_peer_count:
                break
    discovery_convergence_s = time.monotonic() - discovery_start

    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.touch()
    if args.start_file:
        start_deadline = time.monotonic() + args.start_wait_timeout_s
        while time.monotonic() < start_deadline and not args.start_file.exists():
            rclpy.spin_once(node, timeout_sec=0.05)
            drain_pending_replies()
            release_stale_deferrals()
        if not args.start_file.exists():
            raise RuntimeError("timed out waiting for data-plane start gate")

    time.sleep(args.start_offset_ms / 1000.0)

    scenario_start = time.monotonic()
    scenario_deadline = scenario_start + args.scenario_timeout_s

    for crossing_index in range(args.num_crossings):
        if time.monotonic() >= scenario_deadline:
            break
        time.sleep(rng.uniform(0.0, 0.5))  # stagger requests, not a fully synchronized storm

        retries_this_crossing = 0
        forced_entry = False
        # req_id stays FIXED for the whole crossing -- confirmed as a
        # real bug fix: with a fresh req_id (and a reset
        # replies_received) every retry, two replies that both arrive
        # EVENTUALLY but NOT within the same --reply-timeout-s window
        # (very plausible given FleetRMW's own already-documented
        # high-variance Wi-Fi latency -- see Bảng V's
        # p50=5.5s/p95=9.9s/p99=10.9s for this exact RMW/profile) got
        # counted against TWO DIFFERENT req_ids and neither retry ever
        # saw both at once, discarding real progress on every retry.
        # Re-broadcasting the SAME req_id means a reply from ANY
        # broadcast of this crossing's request still matches and counts.
        #
        # The Lamport TIMESTAMP is now ALSO frozen for the whole
        # crossing (one increment, taken once below, reused on every
        # retry's re-broadcast) -- changed from an earlier version that
        # bumped it on every retry. That earlier behavior was flagged in
        # review as a real fairness bug: since LOWER (lamport_ts, name)
        # wins priority ties, and Lamport values only ever increase, a
        # requester needing more retries (almost always because ITS OWN
        # messages are the ones being lost, not because it misbehaved)
        # got a STRICTLY WORSE priority on every retry -- exactly the
        # requester most likely to need protection was the one being
        # punished hardest, a vicious cycle that plausibly explains
        # forced_entry rates staying pinned near 100% even at small N
        # (see docs/AUDIT_ACCEPTANCE_TRACKING.md, 14/09/2026 entry). A
        # retransmission of the SAME logical request is not a new
        # causal event by Lamport's own definition, so it should not
        # consume a new clock value -- only the FIRST broadcast of a
        # crossing does.
        requesting = True
        current_req_id = f"{args.endpoint}:{crossing_index}"
        replies_received = set()
        declared_wall_ns = time.time_ns()
        lamport_clock += 1
        current_req_ts = (lamport_clock, args.endpoint)
        while True:
            if retries_this_crossing > 0:
                # Jitter every RE-broadcast (not the first) so a
                # collision on one retry doesn't repeat in lockstep on
                # every subsequent one -- a synchronized-retry
                # ("thundering herd") risk, not a transport bug.
                time.sleep(rng.uniform(0.0, 0.3))
            request_wall_ns = time.time_ns()
            msg = String()
            msg.data = json.dumps(
                {
                    "type": "request",
                    "from": args.endpoint,
                    "req_id": current_req_id,
                    "lamport_ts": current_req_ts[0],
                    "task_criticality": task_criticality,
                    "wall_ns": request_wall_ns,
                }
            )
            safe_publish(request_pub, msg)
            debug_counters["requests_sent"] += 1

            attempt_deadline = time.monotonic() + args.reply_timeout_s
            while (
                time.monotonic() < attempt_deadline
                and len(replies_received) < len(peers)
                and time.monotonic() < scenario_deadline
            ):
                rclpy.spin_once(node, timeout_sec=0.05)
                drain_pending_replies()
                release_stale_deferrals()

            if len(replies_received) >= len(peers) or not peers:
                forced_entry = False
                break
            retries_this_crossing += 1
            coordination_retry_count += 1
            if time.monotonic() >= scenario_deadline:
                # Gave up waiting for full consensus and entering anyway --
                # under severe enough network collapse (e.g. the 5G profile
                # at N>=16, see docs/AUDIT_ACCEPTANCE_TRACKING.md) replies
                # may simply never arrive at all. Marking this explicitly
                # rather than letting it look like a normal, clean mutex
                # acquisition matters: a forced entry is NOT a validated
                # mutual-exclusion guarantee (another endpoint may believe
                # it also holds the zone), so downstream analysis must be
                # able to tell the two cases apart rather than averaging
                # them together as if both were equally trustworthy.
                forced_entry = True
                break

        entered_wall_ns = time.time_ns()
        in_cs = True
        requesting = False
        time.sleep(args.crossing_duration_ms / 1000.0)
        exited_wall_ns = time.time_ns()
        in_cs = False
        for to, req_id, _deferred_at in deferred:
            send_reply(to, req_id)
        deferred = []

        crossings.append(
            {
                "crossing_index": crossing_index,
                "declared_wall_ns": declared_wall_ns,
                "entered_wall_ns": entered_wall_ns,
                "exited_wall_ns": exited_wall_ns,
                "retries": retries_this_crossing,
                "conflict_resolution_delay_ms": (entered_wall_ns - declared_wall_ns) / 1e6,
                "forced_entry": forced_entry,
            }
        )

    task_completion_s = time.monotonic() - scenario_start

    # Drain briefly so any still-in-flight deferred replies we owe other
    # endpoints actually get sent/processed before this process exits --
    # otherwise a slow-to-finish peer could be left waiting on a reply
    # this endpoint already decided to send but hadn't flushed yet.
    drain_deadline = time.monotonic() + 3.0
    while time.monotonic() < drain_deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
        drain_pending_replies()
        release_stale_deferrals()
        for to, req_id, _deferred_at in deferred:
            send_reply(to, req_id)
        deferred = []

    result = {
        "schema_version": "fleetqox.coordination_endpoint.v1",
        "endpoint": args.endpoint,
        "priority_mode": args.priority_mode,
        "task_criticality": task_criticality,
        "num_peers": len(peers),
        "num_crossings_completed": len(crossings),
        "num_crossings_requested": args.num_crossings,
        "crossings": crossings,
        "coordination_retry_count": coordination_retry_count,
        "coordination_message_ages_ms": coordination_message_ages_ms,
        "task_completion_s": task_completion_s,
        "discovery_convergence_s": discovery_convergence_s,
        "discovery_peers_seen": len(discovery_peers_seen),
        "discovery_expected_peers": args.expected_peer_count,
        "debug_counters": debug_counters,
        "raw_received_log": raw_received_log,
    }
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "endpoint": args.endpoint,
                "crossings_completed": len(crossings),
                "coordination_retry_count": coordination_retry_count,
            }
        )
    )

    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
