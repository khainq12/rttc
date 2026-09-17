#!/usr/bin/env python3
"""Replay one FleetQoX endpoint's rows of a CSV trace through the REAL
rmw_fleetqox_cpp transport (actual fragment/NACK/repair reliability),
instead of the raw single-shot UDP that external/ns3/fleetqox_trace_replay.cc
and external/omnetpp/TraceDrivenUdpApp.cc send with no recovery beyond
the 802.11 MAC's own retry limit.

Meant to run once per FleetQoX endpoint (controller/fleet_router/
operator_ui/robot_NNNN), each in its own container or network namespace,
with RMW_IMPLEMENTATION=rmw_fleetqox_cpp and the RMW's peer-discovery env
vars (FLEETQOX_RMW_BIND/FLEETQOX_RMW_PEERS, see
scripts/run_ros2_direct_rmw_netem_probe.py's launch pattern) already set
by whatever launches this process, so its packets flow over whatever real
network path connects the containers -- a plain Docker bridge for a smoke
test, or later a TAP device bridged into an ns-3/INET simulated 802.11
network.

Runs in real wall-clock time (mapped from the trace's timestamp_ms plus
--start-offset-ms), since once a real RMW process is involved there is no
compressed simulation time to exploit -- this is the same constraint the
netem probes already operate under.

One ROS 2 topic per (destination, flow_class) pair
(/fleetqox_trace/<dst>/<flow_class>) gives point-to-point delivery
semantics matching the raw-UDP test's explicit src/dst addressing --
publishing "control" traffic to every robot on a single shared topic
would let every robot receive every other robot's messages too, which
the real network never does.

Output JSON matches TraceEvent-level records (not the aggregate CSV table
the single-process ns-3/INET harnesses print, since this process only
ever sees ITS OWN tx/rx) -- a separate orchestrator combines every
endpoint's output into one system-wide summary comparable to the existing
wifi-parity CSV format.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import csv
import ctypes
import json
import os
from pathlib import Path
import time
from typing import Any

# rclpy is only available inside the ROS 2 image this script is meant to
# run in; importing it lazily (inside main()) keeps the pure trace/topic/
# payload helpers below unit-testable on a plain host.


def fleetqox_transport_metrics() -> dict[str, Any]:
    """Read rmw_fleetqox_cpp's fragment/NACK/repair counters via the same
    ctypes-into-librmw_fleetqox_cpp.so mechanism as
    run_ros2_direct_rmw_netem_probe.py's fleetqox_transport_metrics() --
    added for the 16-robot-scale delivery-collapse investigation (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md), to tell "channel genuinely
    saturated" apart from "the reliability layer's own NACK-driven
    repair retransmissions are amplifying the load that saturates it" --
    a subset of the full counter list there, focused on retransmission
    volume rather than every internal queue/budget signal.
    """
    if os.environ.get("RMW_IMPLEMENTATION") != "rmw_fleetqox_cpp":
        return {}
    try:
        library = ctypes.CDLL("librmw_fleetqox_cpp.so")
    except OSError:
        return {"available": False}
    names = (
        "frames_sent",
        "frames_received",
        "data_frames_received",
        "fragment_nacks_sent",
        "fragment_nacks_received",
        "fragments_selectively_retransmitted",
        "nack_retransmissions",
        "reliable_timeout_retransmissions",
        "fragment_send_failures",
        "udp_datagram_budget_failures",
        "fragment_completion_markers_sent",
        "fragment_completion_markers_received",
        "fragment_active_assemblies",
        "fragment_assembly_ttl_expirations",
        "unreachable_retry_attempts",
        "unreachable_retry_giveups",
        "graph_heartbeats_sent",
        "graph_heartbeats_received",
        "graph_full_resyncs_sent",
        "subscription_aware_frames",
        "subscription_aware_fallback_broadcasts",
        # Loss-funnel instrumentation (Optimization #2 candidate
        # investigation, see docs/AUDIT_ACCEPTANCE_TRACKING.md): localize
        # WHERE FleetRMW messages disappear between publish and app
        # delivery. frames_received_unrecognized = received a payload
        # that matched no known frame type (fell through decode_data_frame
        # silently before this instrumentation). send_datagram_*
        # = measures send_datagram_to_targets()'s per-call fan-out: it
        # aborts the whole call on the first unretryable per-target send
        # failure, so every peer ordered after the failing one in that
        # call's target list is never attempted -- partial_abort_calls
        # counts how often this happens, targets_skipped counts how many
        # peer-sends were never even attempted as a result.
        "frames_received_unrecognized",
        "send_datagram_partial_abort_calls",
        "send_datagram_full_success_calls",
        "send_datagram_targets_attempted",
        "send_datagram_targets_skipped",
    )
    # Global (not per-socket) loss-funnel counters -- different ctypes
    # export naming (no "_socket_" infix), see rmw_pubsub.cpp's
    # g_data_frames_matched_zero_subscriptions/g_frames_enqueued_to_subscriptions.
    global_names = (
        "data_frames_matched_zero_subscriptions",
        "frames_enqueued_to_subscriptions",
    )
    metrics: dict[str, Any] = {"available": True}
    for name in names:
        symbol = getattr(library, f"rmw_fleetqox_cpp_socket_{name}")
        symbol.restype = ctypes.c_uint64
        metrics[name] = int(symbol())
    for name in global_names:
        symbol = getattr(library, f"rmw_fleetqox_cpp_{name}")
        symbol.restype = ctypes.c_uint64
        metrics[name] = int(symbol())
    metrics["publish_stages"] = fleetqox_publish_stage_metrics(library)
    return metrics


def fleetqox_receive_timeline() -> list[dict[str, Any]]:
    """Read rmw_pubsub.cpp's T_RMW_READY timeline (event_id + wall_ns at
    the moment a decoded frame enters a subscription's frame_queue, i.e.
    the point rmw_take()/rmw_wait() would see it as available) via the
    same ctypes-into-librmw_fleetqox_cpp.so mechanism as
    fleetqox_transport_metrics() -- added for the "tách receiver-side
    FleetRMW internal vs dispatch" investigation (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md 17/09/2026). Only populated when the
    caller set FLEETQOX_RMW_RECEIVE_TIMELINE_PROFILING in this process's
    environment (default: empty list, same "opt-in, zero cost otherwise"
    pattern as FLEETQOX_RMW_PUBLISH_STAGE_PROFILING).
    """
    if os.environ.get("RMW_IMPLEMENTATION") != "rmw_fleetqox_cpp":
        return []
    if not os.environ.get("FLEETQOX_RMW_RECEIVE_TIMELINE_PROFILING"):
        return []
    try:
        library = ctypes.CDLL("librmw_fleetqox_cpp.so")
    except OSError:
        return []
    fn = library.rmw_fleetqox_cpp_receive_timeline_json
    fn.restype = ctypes.c_char_p
    raw = fn()
    if not raw:
        return []
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []


def fleetqox_loss_funnel_trace() -> dict[str, list[dict[str, Any]]]:
    """Read rmw_pubsub.cpp's message x target loss-funnel trace (send-side
    ATTEMPT_SUCCESS/ATTEMPT_FAILED/SKIPPED_AFTER_FAILURE per intended
    target, and receive-side DATA frame arrivals), keyed by
    (source_id=publisher_id, source_sequence, topic) -- added for the
    Optimization #2 causal-proof investigation (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md 17-18/09/2026): does
    send_datagram_to_targets()'s abort-on-first-failure actually explain
    a substantial fraction of observed missing deliveries, at the level
    of a specific message never reaching a specific intended recipient
    because an earlier target in the same send call failed? Only
    populated when FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING is set (same
    opt-in, zero-cost-otherwise pattern as the other profiling env vars
    here). Returns {"send": [...], "recv": [...], "raw_recvfrom": [...]}.

    "raw_recvfrom" (added 18/09/2026, see
    docs/AUDIT_ACCEPTANCE_TRACKING.md) is an EARLIER checkpoint than
    "recv": recorded directly in receive_loop() right after recvfrom()
    returns, before any dispatch/reassembly/decode -- lets a message
    already proven (via packet capture) to reach the receiving
    container's own network interface be checked against whether
    recvfrom() itself ever saw those bytes. Best-effort only (can't
    identify AEAD/peer-auth-encrypted or non-fragment-0 fragment
    payloads, which have no plaintext JSON yet at this point) -- absence
    here is not proof of loss for those cases, only for plain DATA
    frames like this benchmark's traffic.
    """
    empty: dict[str, list[dict[str, Any]]] = {"send": [], "recv": [], "raw_recvfrom": []}
    if os.environ.get("RMW_IMPLEMENTATION") != "rmw_fleetqox_cpp":
        return empty
    if not os.environ.get("FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING"):
        return empty
    try:
        library = ctypes.CDLL("librmw_fleetqox_cpp.so")
    except OSError:
        return empty
    result: dict[str, list[dict[str, Any]]] = {}
    for key, symbol_name in (
        ("send", "rmw_fleetqox_cpp_loss_funnel_send_trace_json"),
        ("recv", "rmw_fleetqox_cpp_loss_funnel_recv_trace_json"),
        ("raw_recvfrom", "rmw_fleetqox_cpp_loss_funnel_raw_recvfrom_trace_json"),
    ):
        fn = getattr(library, symbol_name)
        fn.restype = ctypes.c_char_p
        raw = fn()
        if not raw:
            result[key] = []
            continue
        try:
            result[key] = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            result[key] = []
    return result


def fleetqox_publish_stage_metrics(library: "ctypes.CDLL") -> dict[str, Any]:
    """publish_payload()'s stage-resolved micro-profiler (see PublishStage
    in rmw_pubsub.cpp) -- only non-zero when the process was launched with
    FLEETQOX_RMW_PUBLISH_STAGE_PROFILING set, added for the causal-
    isolation investigation into FleetRMW's ~6.4x slower publish() call
    vs raw UDP's sendto() (see docs/AUDIT_ACCEPTANCE_TRACKING.md
    'publish-path latency profiling')."""
    stage_names = (
        "encode",
        "subscription_lookup",
        "mutex_wait",
        "mutex_hold",
        "transport_send",
        "transport_decode",
        "transport_target_lookup",
        "transport_sendto_syscall",
        "udp_send_mutex_wait",
    )
    stages: dict[str, Any] = {}
    for index, name in enumerate(stage_names):
        sum_fn = library.rmw_fleetqox_cpp_publish_stage_sum_ns
        sum_fn.restype = ctypes.c_uint64
        sum_fn.argtypes = [ctypes.c_int]
        count_fn = library.rmw_fleetqox_cpp_publish_stage_count
        count_fn.restype = ctypes.c_uint64
        count_fn.argtypes = [ctypes.c_int]
        max_fn = library.rmw_fleetqox_cpp_publish_stage_max_ns
        max_fn.restype = ctypes.c_uint64
        max_fn.argtypes = [ctypes.c_int]
        sum_ns = int(sum_fn(index))
        count = int(count_fn(index))
        stages[name] = {
            "sum_ns": sum_ns,
            "count": count,
            "max_ns": int(max_fn(index)),
            "mean_ns": sum_ns / count if count else 0.0,
        }
    target_count_sum = library.rmw_fleetqox_cpp_transport_target_count_sum
    target_count_sum.restype = ctypes.c_uint64
    target_count_calls = library.rmw_fleetqox_cpp_transport_target_count_calls
    target_count_calls.restype = ctypes.c_uint64
    peer_addresses_size = library.rmw_fleetqox_cpp_transport_peer_addresses_size
    peer_addresses_size.restype = ctypes.c_uint64
    calls = int(target_count_calls())
    stages["_target_count_mean"] = (int(target_count_sum()) / calls) if calls else 0.0
    stages["_peer_addresses_size"] = int(peer_addresses_size())

    pmtu_drain_calls = library.rmw_fleetqox_cpp_pmtu_drain_calls
    pmtu_drain_calls.restype = ctypes.c_uint64
    pmtu_drain_recvmsg_calls = library.rmw_fleetqox_cpp_pmtu_drain_recvmsg_calls
    pmtu_drain_recvmsg_calls.restype = ctypes.c_uint64
    pmtu_drain_messages_consumed = library.rmw_fleetqox_cpp_pmtu_drain_messages_consumed
    pmtu_drain_messages_consumed.restype = ctypes.c_uint64
    stages["_pmtu_drain_calls"] = int(pmtu_drain_calls())
    stages["_pmtu_drain_recvmsg_calls"] = int(pmtu_drain_recvmsg_calls())
    stages["_pmtu_drain_messages_consumed"] = int(pmtu_drain_messages_consumed())
    return stages


def _topic_for(destination: str, flow_class: str) -> str:
    safe_dst = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in destination)
    safe_class = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in flow_class)
    return f"/fleetqox_trace/{safe_dst}/{safe_class}"


def load_rows(trace_path: Path, policy: str, endpoint: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with trace_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["policy"] != policy:
                continue
            if row["src"] != endpoint and row["dst"] != endpoint:
                continue
            rows.append(row)
    return rows


def build_payload(row: dict[str, str], target_bytes: int) -> str:
    """Wire payload for one trace row.

    Keeps only what the receiver can't otherwise infer: event_id (to
    correlate against the trace) and deadline_ms/sent_wall_ns (to score
    the delivery). src/dst/flow_class/policy are recoverable from which
    topic a message arrived on plus the single --policy this process
    replays, and are deliberately left out of the wire payload -- the
    real system's smallest flow (control, 96 bytes) leaves very little
    room for metadata, and every key here counts. Short key names for the
    same reason.
    """
    payload: dict[str, Any] = {
        "e": row["event_id"],
        "d": float(row["deadline_ms"]),
        "s": time.time_ns(),
        "p": "",
    }
    compact = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    overhead = len(compact.encode("utf-8"))
    if overhead > target_bytes:
        raise ValueError(
            f"payload target {target_bytes} is smaller than the "
            f"{overhead}-byte metadata floor for event {row['event_id']}"
        )
    payload["p"] = "x" * (target_bytes - overhead)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def wait_until_deadline_while_spinning(
    deadline_monotonic: float,
    spin_once_fn: Callable[[float], None],
    *,
    poll_interval_s: float = 0.01,
    now_fn: Callable[[], float] = time.monotonic,
) -> None:
    """Blocks until now_fn() >= deadline_monotonic, servicing ready rclpy
    callbacks the whole time instead of going fully silent the way a
    blind time.sleep() does.

    Root cause this replaces (see docs/AUDIT_ACCEPTANCE_TRACKING.md
    17/09/2026 "SỬA BENCHMARK HARNESS"): the send loop's own inter-send
    wait was a plain time.sleep(target_offset_s - now_offset) that called
    rclpy.spin_once() zero times during the sleep. A message that becomes
    ready to dispatch (T_RMW_READY, per rmw_pubsub.cpp's own
    instrumentation) partway through that wait sat undelivered until this
    process woke up for its OWN next scheduled send -- live A/B measured
    this adding up to ~370ms to control-class message latency, ~99.7% of
    the previously-opaque "receiver-side" gap, while FleetRMW's actual
    receive-side processing (MacRx -> T_RMW_READY) stayed under 0.9ms.
    Same bug class as the drain-burst fix already applied elsewhere in
    this file (spin_once() services only ONE ready entity per call), but
    this is the DIFFERENT gap: no spin_once() call happening AT ALL
    during the wait, not just one call servicing too little per call.

    Design constraints (all directly testable via the fake now_fn/
    spin_once_fn WaitUntilDeadlineWhileSpinningTest uses):
      - never returns before deadline_monotonic (must not shift the
        publish schedule -- the original time.sleep()'s only real job)
      - never busy-loops: each spin_once_fn call is bounded to at most
        poll_interval_s (real rclpy.spin_once(timeout_sec=X) blocks up to
        X seconds inside rmw_wait()'s own 1ms poll loop, so this spends
        that time productively instead of sleeping blind)
      - drains everything already ready in a tight burst each iteration
        (same pattern as the discovery/drain loops elsewhere in this
        file -- spin_once() only services one ready entity per call)
    """
    while True:
        remaining = deadline_monotonic - now_fn()
        if remaining <= 0:
            return
        for _ in range(20):
            spin_once_fn(0.0)
        remaining = deadline_monotonic - now_fn()
        if remaining <= 0:
            return
        spin_once_fn(min(remaining, poll_interval_s))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument(
        "--endpoint",
        required=True,
        help="this process's FleetQoX endpoint name, e.g. fleet_controller or robot_0003",
    )
    parser.add_argument(
        "--policy",
        required=True,
        help="single policy to replay (the trace CSV mixes multiple policies)",
    )
    parser.add_argument("--start-offset-ms", type=float, default=1000.0)
    parser.add_argument(
        "--discovery-timeout-s",
        type=float,
        default=15.0,
        help="max wait for every publisher to see at least one subscriber before sending",
    )
    parser.add_argument(
        "--start-wait-timeout-s",
        type=float,
        default=60.0,
        help=(
            "max wait for --start-file to appear after this endpoint's own "
            "--ready-file is touched. Deliberately a SEPARATE knob from "
            "--discovery-timeout-s: what this endpoint is waiting for here "
            "is the SLOWEST sibling endpoint finishing its own discovery, "
            "not its own -- an endpoint that discovers quickly must not "
            "time itself out before a slower sibling ever gets to release "
            "the shared start gate. Must be set by the caller to at least "
            "the orchestrator's own ready-file poll deadline plus margin."
        ),
    )
    parser.add_argument(
        "--drain-s",
        type=float,
        default=10.0,
        help="how long to keep spinning after the last send to let in-flight repair complete",
    )
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, default=None)
    parser.add_argument("--start-file", type=Path, default=None)
    parser.add_argument(
        "--expected-peer-count",
        type=int,
        default=0,
        help=(
            "Number of OTHER endpoints this one should observe before "
            "declaring discovery converged. 0 (default) preserves the old "
            "behavior (get_subscription_count()-based check only, no "
            "discovery_convergence_s in the summary) -- opt-in because "
            "get_subscription_count() was confirmed unreliable for judging "
            "match state on some RMWs (see docs/AUDIT_ACCEPTANCE_TRACKING.md "
            "'CycloneDDS discovery bug bí ẩn'), so measuring real discovery "
            "convergence time needs an independent, RMW-agnostic signal: a "
            "small beacon each endpoint publishes/subscribes on a shared "
            "topic, counting DISTINCT senders seen rather than trusting any "
            "single RMW's own introspection API."
        ),
    )
    parser.add_argument(
        "--skip-discovery-wait",
        action="store_true",
        help=(
            "Skip the discovery-wait loop entirely and report "
            "discovery_convergence_s as the (near-zero) time to reach that "
            "point -- for rmw_fleetqox_cpp's static mode, which has no "
            "discovery step by design (peers are known at launch via "
            "FLEETQOX_RMW_PEERS, nothing to wait for). Without this flag, "
            "static-mode runs fell through to the get_subscription_count()"
            "-based fallback loop below, which FleetRMW's custom transport "
            "doesn't populate meaningfully (get_subscription_count() never "
            "returns >0 for it), so every static-mode run silently burned "
            "the full --discovery-timeout-s (~15s) before proceeding --"
            "see docs/AUDIT_ACCEPTANCE_TRACKING.md 'FleetRMW N/A' for the "
            "measurement this replaces."
        ),
    )
    parser.add_argument(
        "--discovery-only",
        action="store_true",
        help=(
            "Create every publisher/subscription and go through the "
            "normal discovery-convergence + ready/start gate, but send NO "
            "application data at all -- isolates whether the middleware's "
            "own discovery/control-plane traffic is sufficient by itself "
            "to saturate the wifi medium, independent of any application "
            "workload on top of it. See docs/AUDIT_ACCEPTANCE_TRACKING.md "
            "'causal isolation: discovery-only'."
        ),
    )
    args = parser.parse_args()

    import rclpy
    import rclpy.publisher
    from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    rows = load_rows(args.trace, args.policy, args.endpoint)
    outgoing = sorted(
        (row for row in rows if row["src"] == args.endpoint),
        key=lambda row: float(row["timestamp_ms"]),
    )
    incoming_topics = sorted(
        {
            _topic_for(row["dst"], row["flow_class"])
            for row in rows
            if row["dst"] == args.endpoint
        }
    )
    outgoing_topics = sorted(
        {_topic_for(row["dst"], row["flow_class"]) for row in outgoing}
    )

    rclpy.init()
    node_name = "fleetqox_trace_endpoint_" + "".join(
        ch if ch.isalnum() else "_" for ch in args.endpoint
    )
    # start_parameter_services=False alone does NOT stop this -- confirmed
    # via a temporary debug print in subscription_aware_targets() (see
    # docs/AUDIT_ACCEPTANCE_TRACKING.md "bắt gói tin thật tại biên TAP")
    # that ALL subscription_aware_fallback_broadcasts_ (38 total, exactly
    # 2/endpoint) are rclpy's /parameter_events topic, which isn't in
    # FLEETQOX_RMW_STATIC_SUBSCRIPTIONS -- so every publish to it falls
    # back to broadcasting to all 18 other peers. Node.__init__ creates
    # _parameter_event_publisher unconditionally (rclpy/node.py), and
    # TimeSource.__init__ unconditionally calls
    # node.declare_parameter('use_sim_time', False) regardless of
    # start_parameter_services -- and BOTH of those run to completion
    # inside rclpy.create_node() itself, before it ever returns, so
    # patching the instance's own .publish attribute afterwards is too
    # late (confirmed empirically -- fallback_broadcasts stayed at 38
    # with that approach). There's no public rclpy option to suppress the
    # publish itself, so patch the Publisher CLASS before create_node()
    # runs, filtering on topic name -- this probe has nothing that
    # subscribes to /parameter_events and never declares/sets real
    # parameters, so dropping these publishes changes no observed
    # behavior.
    _original_publisher_publish = rclpy.publisher.Publisher.publish

    def _publish_suppressing_parameter_events(self, *pub_args, **pub_kwargs):
        if self.topic_name == "/parameter_events":
            return None
        return _original_publisher_publish(self, *pub_args, **pub_kwargs)

    rclpy.publisher.Publisher.publish = _publish_suppressing_parameter_events
    node = rclpy.create_node(node_name, start_parameter_services=False)
    qos = QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=64,
        reliability=ReliabilityPolicy.RELIABLE,
    )

    publishers = {topic: node.create_publisher(String, topic, qos) for topic in outgoing_topics}

    # Topic-based addressing already guarantees every message arriving on
    # one of incoming_topics is meant for this endpoint (only its own
    # (dst=self, flow_class) topics are subscribed below), so the wire
    # payload doesn't need to repeat dst/src/flow_class/policy -- the
    # orchestrator recovers those by joining received event_id back
    # against the original trace CSV.
    received: list[dict[str, Any]] = []

    def on_message(msg: String) -> None:
        recv_wall_ns = time.time_ns()
        payload = json.loads(msg.data)
        received.append(
            {
                "event_id": payload["e"],
                "deadline_ms": payload["d"],
                "sent_wall_ns": payload["s"],
                "recv_wall_ns": recv_wall_ns,
            }
        )

    for topic in incoming_topics:
        node.create_subscription(String, topic, on_message, qos)

    # Discovery (pub-sub matching over the RMW's peer transport) happens
    # BEFORE the ready/start gate below, and can legitimately take a
    # different amount of real time on each endpoint. If start_wall were
    # set right after each endpoint's own discovery finished (the naive
    # order), endpoints that discovered faster would end up replaying the
    # trace against an earlier "t=0" than slower ones, skewing the
    # cross-endpoint schedule the trace intends. Finishing discovery FIRST
    # and only touching --ready-file once it's done means every endpoint
    # is already fully discovered by the time --start-file releases them
    # all together, so start_wall can be set immediately at that shared
    # release point with no further per-endpoint variance.
    # Beacon-based discovery detection (opt-in via --expected-peer-count):
    # each endpoint publishes its own name on a shared topic and counts
    # DISTINCT senders seen, independent of any single RMW's own match-state
    # introspection. Needed because pub.get_subscription_count() (used
    # below as the fallback) was confirmed to under-report matches on some
    # RMWs while data was still being delivered correctly (see
    # docs/AUDIT_ACCEPTANCE_TRACKING.md "CycloneDDS discovery bug bí ẩn") --
    # trusting it here would have made "discovery convergence time" either
    # always equal --discovery-timeout-s (if it never fires) or wrong (if
    # it fires late/early), not a real measurement.
    discovery_peers_seen: set[str] = set()
    beacon_pub = None
    if args.expected_peer_count > 0:
        beacon_topic = "/fleetqox_trace/_discovery_probe"
        beacon_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.RELIABLE
        )
        beacon_pub = node.create_publisher(String, beacon_topic, beacon_qos)
        beacon_msg = String()
        beacon_msg.data = args.endpoint

        beacon_raw_seen: list[str] = []

        def on_beacon(msg: String) -> None:
            beacon_raw_seen.append(msg.data)
            if msg.data != args.endpoint:  # a beacon can loop back on some RMWs
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
            # spin_once() services only ONE ready wait-set entity per call, so
            # drain everything ready each iteration rather than relying on one
            # call to eventually get to a specific subscription. Confirmed via
            # a beacon_pub_subscription_count/beacon_raw_seen_count diagnostic
            # (see the DISCOVERY_TIMEOUT_DEBUG print below) that this alone
            # does NOT fully explain the discovery-timeout cases seen in
            # practice -- the deeper cause turned out to be real, timing-
            # dependent CycloneDDS discovery flakiness between launch-order-
            # distant peers (see docs/AUDIT_ACCEPTANCE_TRACKING.md "beacon
            # discovery convergence"), not starvation -- but this is still a
            # correct fix for the starvation class of bug on its own, so kept.
            for _ in range(20):
                rclpy.spin_once(node, timeout_sec=0.0)
            rclpy.spin_once(node, timeout_sec=0.1)
            if beacon_pub is not None:
                if len(discovery_peers_seen) >= args.expected_peer_count:
                    break
            elif not publishers or all(
                pub.get_subscription_count() > 0 for pub in publishers.values()
            ):
                break
    discovery_convergence_s = time.monotonic() - discovery_start
    if beacon_pub is not None and len(discovery_peers_seen) < args.expected_peer_count:
        # TEMPORARY diagnostic for the "last-launched endpoint never sees
        # any beacon" investigation (docs/AUDIT_ACCEPTANCE_TRACKING.md) --
        # tells apart "writer never matched" from "matched but callback
        # never fired" without needing a live container to inspect.
        print(
            json.dumps(
                {
                    "DISCOVERY_TIMEOUT_DEBUG": args.endpoint,
                    "beacon_pub_subscription_count": beacon_pub.get_subscription_count(),
                    "beacon_raw_seen_count": len(beacon_raw_seen),
                    "beacon_raw_seen_sample": beacon_raw_seen[:10],
                    "topic_names_and_types": node.get_topic_names_and_types(),
                }
            ),
            flush=True,
        )

    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.touch()
    if args.start_file:
        start_deadline = time.monotonic() + args.start_wait_timeout_s
        while time.monotonic() < start_deadline and not args.start_file.exists():
            rclpy.spin_once(node, timeout_sec=0.05)
        if not args.start_file.exists():
            raise RuntimeError("timed out waiting for data-plane start gate")

    start_wall = time.monotonic()
    sent: list[str] = []
    send_timing: list[dict[str, Any]] = []
    publish_failures: list[dict[str, Any]] = []
    # --discovery-only: every publisher/subscription above was still
    # created and matched normally (so discovery/control-plane traffic is
    # unaffected), but the send loop itself is skipped entirely -- isolates
    # whether discovery traffic ALONE is enough to saturate the medium,
    # independent of any application data on top of it.
    replay_rows = [] if args.discovery_only else outgoing
    for row in replay_rows:
        target_offset_s = (float(row["timestamp_ms"]) + args.start_offset_ms) / 1000.0
        # Was a blind time.sleep(target_offset_s - now_offset) -- replaced
        # 17/09/2026 (see docs/AUDIT_ACCEPTANCE_TRACKING.md "SỬA BENCHMARK
        # HARNESS"): that slept without ever calling spin_once(), so any
        # message that became ready to dispatch during the wait sat
        # undelivered until this process's own next scheduled send. Same
        # deadline (start_wall + target_offset_s), so the publish schedule
        # itself is unchanged -- this only adds servicing during the wait.
        wait_until_deadline_while_spinning(
            start_wall + target_offset_s,
            lambda timeout_sec: rclpy.spin_once(node, timeout_sec=timeout_sec),
        )
        # Same "drain everything ready, not just one entity" fix already
        # applied to the discovery wait loop above (spin_once() services
        # only ONE ready wait-set entity per call) -- a single
        # spin_once(0.0) here only services one of possibly several
        # already-queued incoming messages, leaving the rest to wait for
        # a LATER iteration (whenever this row's own scheduled send time
        # arrives) or the drain loop below. Candidate fix for the ~250ms
        # N=1 latency floor found 16/09/2026 (see
        # docs/AUDIT_ACCEPTANCE_TRACKING.md "phân rã latency") -- FleetRMW's
        # own publish-path cost was measured directly at ~0.3ms, so this
        # floor has to be harness-side polling, not RMW.
        for _ in range(20):
            rclpy.spin_once(node, timeout_sec=0.0)
        target_bytes = max(1, int(row["bytes"]))
        msg = String()
        msg.data = build_payload(row, target_bytes)
        # Timing instrumentation added for the causal-isolation investigation
        # (see docs/AUDIT_ACCEPTANCE_TRACKING.md "send-timing burstiness"):
        # packet-count amplification was ruled out (1.02x, essentially 1:1
        # vs logical messages), so the remaining hypothesis is that
        # FleetRMW's actual publish()/sendto() calls cluster tighter in
        # wall-clock time than the trace's scheduled timestamps imply,
        # unlike raw_udp_trace_endpoint.py's near-immediate unbuffered
        # sendto(). before_wall_ns/after_wall_ns bracket the actual
        # publish() call (which synchronously performs encode + sendto()
        # inside this RMW), not just the scheduled offset already captured
        # by scheduled_offset_s.
        before_wall_ns = time.monotonic_ns()
        try:
            publishers[_topic_for(row["dst"], row["flow_class"])].publish(msg)
        except Exception as exc:  # noqa: BLE001 -- rclpy raises RCLError (not
            # a plain return code) when rmw_publish() returns non-OK, e.g.
            # errno=111 (ECONNREFUSED) from send_datagram_to_targets():
            # unlike ENOBUFS/EAGAIN/EWOULDBLOCK/ENETUNREACH/EHOSTUNREACH,
            # ECONNREFUSED has NO retry class in that function at all, so
            # it surfaces immediately as an exception here. Previously
            # uncaught -- confirmed live (17/09/2026, see
            # docs/AUDIT_ACCEPTANCE_TRACKING.md) that this silently killed
            # the WHOLE endpoint process on a single transient send
            # failure, losing every subsequent scheduled message from that
            # endpoint for the rest of the run, not just the one that
            # failed. Recording and continuing (harness robustness fix,
            # same category as the earlier dispatch-gap/5G harness fixes)
            # -- does NOT touch send_datagram_to_targets' own retry/error
            # semantics, which stay exactly as-is.
            publish_failures.append(
                {
                    "event_id": row["event_id"],
                    "wall_ns": time.monotonic_ns(),
                    "error": str(exc),
                }
            )
            continue
        after_wall_ns = time.monotonic_ns()
        sent.append(row["event_id"])
        send_timing.append(
            {
                "event_id": row["event_id"],
                "scheduled_offset_s": target_offset_s,
                "publish_before_wall_ns": before_wall_ns,
                "publish_after_wall_ns": after_wall_ns,
            }
        )

    drain_deadline = time.monotonic() + args.drain_s
    while time.monotonic() < drain_deadline:
        # Same fix as the send loop above: drain every already-ready
        # entity in a tight non-blocking burst before falling back to a
        # single bounded wait -- a bare spin_once(timeout_sec=0.1) here
        # only services ONE ready entity per call, so N incoming messages
        # that arrived close together previously needed N*(up to 100ms)
        # of real wall-clock time to all be noticed and processed.
        for _ in range(20):
            rclpy.spin_once(node, timeout_sec=0.0)
        rclpy.spin_once(node, timeout_sec=0.1)

    result = {
        "schema_version": "fleetqox.rmw_trace_endpoint.v1",
        "endpoint": args.endpoint,
        "policy": args.policy,
        "tx": len(sent),
        "rx": len(received),
        "sent_event_ids": sent,
        "send_timing": send_timing,
        "publish_failures": publish_failures,
        "received": received,
        "fleetqox_transport_metrics": fleetqox_transport_metrics(),
        "fleetqox_receive_timeline": fleetqox_receive_timeline(),
        "fleetqox_loss_funnel_trace": fleetqox_loss_funnel_trace(),
        "discovery_convergence_s": discovery_convergence_s,
        "discovery_peers_seen": len(discovery_peers_seen),
        "discovery_expected_peers": args.expected_peer_count,
    }
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "ok", "endpoint": args.endpoint, "tx": len(sent), "rx": len(received)}))

    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
