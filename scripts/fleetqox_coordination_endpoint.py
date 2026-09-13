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
  - Navigation recovery count: this harness has no real navigation
    stack to recover a path in -- the closest faithful analogue
    available is a REQUEST retry: if not all N-1 replies arrive within
    --reply-timeout-s (almost always because a reply was lost on a
    congested/lossy network, not because anyone misbehaved), the
    request is re-broadcast with a fresh timestamp. Each such retry is
    counted here -- report this framing explicitly wherever this
    column is used, it is a coordination-layer retry standing in for
    an application-layer recovery, not a measurement of any real
    motion planner.
  - Task completion time: wall-clock time from the shared start gate
    to every participant finishing its assigned --num-crossings.
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
    args = parser.parse_args()

    import rclpy
    import rclpy.publisher
    from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    peers = [p for p in args.peers.split(",") if p]
    rng = random.Random(f"{args.seed}:{args.endpoint}")

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
    deferred: list[str] = []

    coordination_message_ages_ms: list[float] = []
    crossings: list[dict[str, Any]] = []
    navigation_recovery_count = 0

    # TEMPORARY diagnostic counters for the "every crossing forces entry,
    # navigation_recovery_count always maxes out" investigation -- see
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
    }

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
        reply_pub.publish(msg)

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
        their_ts = (payload["lamport_ts"], payload["from"])
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
        elif requesting and current_req_ts is not None and current_req_ts < their_ts:
            i_have_priority = True
        else:
            i_have_priority = False
        if i_have_priority:
            deferred.append((payload["from"], payload["req_id"]))
        else:
            pending_immediate_replies.append((payload["from"], payload["req_id"]))

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
                    beacon_pub.publish(beacon_msg)
                    last_beacon_sent = now
            for _ in range(20):
                rclpy.spin_once(node, timeout_sec=0.0)
            rclpy.spin_once(node, timeout_sec=0.1)
            drain_pending_replies()
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
        # ONE req_id/lamport_ts for the WHOLE crossing, not a fresh one
        # per retry -- confirmed as the actual bug via a real run: with a
        # fresh req_id (and a reset replies_received) every retry, two
        # replies that both arrive EVENTUALLY but NOT within the same
        # --reply-timeout-s window (very plausible given FleetRMW's own
        # already-documented high-variance Wi-Fi latency -- see Bảng V's
        # p50=5.5s/p95=9.9s/p99=10.9s for this exact RMW/profile) get
        # counted against TWO DIFFERENT req_ids and neither retry ever
        # sees both at once, even though both replies genuinely arrived.
        # Keeping the identifying (lamport_ts, req_id) constant and just
        # RE-BROADCASTING the same request (in case the first copy was
        # lost) means a reply from ANY broadcast of this crossing's
        # request still matches and counts -- retrying stops discarding
        # real progress already made.
        lamport_clock += 1
        requesting = True
        current_req_id = f"{args.endpoint}:{crossing_index}"
        current_req_ts = (lamport_clock, args.endpoint)
        replies_received = set()
        declared_wall_ns = time.time_ns()
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
                    "wall_ns": request_wall_ns,
                }
            )
            request_pub.publish(msg)
            debug_counters["requests_sent"] += 1

            attempt_deadline = time.monotonic() + args.reply_timeout_s
            while (
                time.monotonic() < attempt_deadline
                and len(replies_received) < len(peers)
                and time.monotonic() < scenario_deadline
            ):
                rclpy.spin_once(node, timeout_sec=0.05)
                drain_pending_replies()

            if len(replies_received) >= len(peers) or not peers:
                forced_entry = False
                break
            retries_this_crossing += 1
            navigation_recovery_count += 1
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
        for to, req_id in deferred:
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
        for to, req_id in deferred:
            send_reply(to, req_id)
        deferred = []

    result = {
        "schema_version": "fleetqox.coordination_endpoint.v1",
        "endpoint": args.endpoint,
        "num_peers": len(peers),
        "num_crossings_completed": len(crossings),
        "num_crossings_requested": args.num_crossings,
        "crossings": crossings,
        "navigation_recovery_count": navigation_recovery_count,
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
                "navigation_recovery_count": navigation_recovery_count,
            }
        )
    )

    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
