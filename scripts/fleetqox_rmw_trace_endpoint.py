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
import csv
import json
from pathlib import Path
import time
from typing import Any

# rclpy is only available inside the ROS 2 image this script is meant to
# run in; importing it lazily (inside main()) keeps the pure trace/topic/
# payload helpers below unit-testable on a plain host.


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
        "--drain-s",
        type=float,
        default=10.0,
        help="how long to keep spinning after the last send to let in-flight repair complete",
    )
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, default=None)
    parser.add_argument("--start-file", type=Path, default=None)
    args = parser.parse_args()

    import rclpy
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
    node = rclpy.create_node(node_name)
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
    discovery_deadline = time.monotonic() + args.discovery_timeout_s
    while time.monotonic() < discovery_deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
        if not publishers or all(
            pub.get_subscription_count() > 0 for pub in publishers.values()
        ):
            break

    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.touch()
    if args.start_file:
        start_deadline = time.monotonic() + args.discovery_timeout_s
        while time.monotonic() < start_deadline and not args.start_file.exists():
            rclpy.spin_once(node, timeout_sec=0.05)
        if not args.start_file.exists():
            raise RuntimeError("timed out waiting for data-plane start gate")

    start_wall = time.monotonic()
    sent: list[str] = []
    for row in outgoing:
        target_offset_s = (float(row["timestamp_ms"]) + args.start_offset_ms) / 1000.0
        now_offset = time.monotonic() - start_wall
        if target_offset_s > now_offset:
            time.sleep(target_offset_s - now_offset)
        rclpy.spin_once(node, timeout_sec=0.0)
        target_bytes = max(1, int(row["bytes"]))
        msg = String()
        msg.data = build_payload(row, target_bytes)
        publishers[_topic_for(row["dst"], row["flow_class"])].publish(msg)
        sent.append(row["event_id"])

    drain_deadline = time.monotonic() + args.drain_s
    while time.monotonic() < drain_deadline:
        rclpy.spin_once(node, timeout_sec=0.1)

    result = {
        "schema_version": "fleetqox.rmw_trace_endpoint.v1",
        "endpoint": args.endpoint,
        "policy": args.policy,
        "tx": len(sent),
        "rx": len(received),
        "sent_event_ids": sent,
        "received": received,
    }
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "ok", "endpoint": args.endpoint, "tx": len(sent), "rx": len(received)}))

    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
