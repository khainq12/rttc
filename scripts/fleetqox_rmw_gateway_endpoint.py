"""WiFi-Gateway benchmark: the gateway relay process (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "WIFI GATEWAY BENCHMARK").

Topology this process sits in the middle of:

    robot_i (Wi-Fi/ns-3 segment) --> GATEWAY (this process,
    two network interfaces) --> control_station (separate, wired
    control-side segment)

The gateway is a single ROS 2 node with ONE middleware participant
whose process happens to have two network interfaces (one on each
segment) -- it needs NO interface-aware logic of its own. Physical
segmentation alone (robots have no route to the control-side segment
and vice versa) is what enforces "robot and control must not
communicate directly"; this script does not need to (and does not)
implement any bypass-prevention logic itself.

RMW-AGNOSTIC BY DESIGN, same as fleetqox_rmw_trace_endpoint.py: this
file contains no RMW-specific code at all. RMW_IMPLEMENTATION is
supplied purely via environment by the launcher, exactly like every
other endpoint in this benchmark -- this is the ONE common gateway
application shared by FleetRMW/Fast DDS/CycloneDDS/Zenoh, per the
task's own "prefer ONE identical ROS 2 gateway application" rule. It
does only: receive message -> preserve required metadata -> forward
message.

RELAY MECHANISM (why a plain "resubscribe and republish on the same
topic name" does not work, and what this uses instead): a node whose
own publisher and subscription share a topic name would see its own
republish loop back into its own callback on many RMWs (the exact same
self-loop class already handled for the discovery beacon in
fleetqox_rmw_trace_endpoint.py's on_beacon()), forwarding forever. This
script instead SUBSCRIBES on the ORIGINAL (unsuffixed) topic name a
robot or control_station already publishes on -- their own publish
code is completely unchanged -- and REPUBLISHES the identical payload,
byte for byte, on that same name PLUS --relay-topic-suffix. Robots and
control_station, when run in gateway mode, pass the SAME suffix to
fleetqox_rmw_trace_endpoint.py's own --incoming-topic-suffix so their
SUBSCRIPTIONS (never their publications) pick up the relayed name
instead of the original. No workload/trace semantics change: the
payload, event_id, deadline_ms, and original sent_wall_ns are
forwarded completely unmodified (this script never parses the JSON
payload at all -- it relays the raw String.data exactly as received),
which is what makes the primary end-to-end latency measurement
(original robot publish -> final control receive) valid without any
new wire-format field.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fleetqox_rmw_trace_endpoint import (  # noqa: E402
    _topic_for,
    discovery_converged,
)


def load_all_rows(trace_path: Path, policy: str) -> list[dict[str, str]]:
    """Unlike fleetqox_rmw_trace_endpoint.py's load_rows() (filtered to
    one specific endpoint's own src/dst rows), the gateway relays
    EVERY row that crosses the robot<->control_station boundary, so it
    needs the full population for this policy, unfiltered by any
    single endpoint name."""
    rows: list[dict[str, str]] = []
    with trace_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["policy"] != policy:
                continue
            rows.append(row)
    return rows


def uplink_and_downlink_topics(
    rows: list[dict[str, str]], control_station_name: str
) -> tuple[list[str], list[str]]:
    """uplink = topics robots publish TO control_station (dst ==
    control_station_name); downlink = topics control_station publishes
    TO robots (dst == some other endpoint). Derived purely from the
    trace's own dst column -- makes no assumption about how many
    robots there are or what their names are, same principle as
    required_peers_from_trace() in run_ns3_docker_container_fleet_probe.py."""
    uplink = sorted(
        {
            _topic_for(row["dst"], row["flow_class"])
            for row in rows
            if row["dst"] == control_station_name
        }
    )
    downlink = sorted(
        {
            _topic_for(row["dst"], row["flow_class"])
            for row in rows
            if row["dst"] != control_station_name
        }
    )
    return uplink, downlink


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--control-station-name", default="control_station")
    parser.add_argument(
        "--relay-topic-suffix",
        type=str,
        required=True,
        help=(
            "Appended to every topic name this process REPUBLISHES on "
            "(never to what it subscribes to). Must exactly match the "
            "--incoming-topic-suffix passed to every robot and to "
            "control_station in this same run."
        ),
    )
    parser.add_argument("--discovery-timeout-s", type=float, default=15.0)
    parser.add_argument(
        "--skip-discovery-wait",
        action="store_true",
        help=(
            "Matches fleetqox_rmw_trace_endpoint.py's own flag of the "
            "same name and the SAME reason: FleetRMW's static_mode has "
            "no discovery step by design (peers are known at launch via "
            "FLEETQOX_RMW_PEERS/FLEETQOX_RMW_STATIC_SUBSCRIPTIONS,  "
            "nothing to wait for) -- the beacon topic itself is NOT "
            "part of any trace-derived static_subscriptions table (it "
            "is a harness-internal mechanism, not a workload topic), "
            "so under static_mode's subscription-aware routing (no "
            "fallback broadcast) the beacon has no route and can never "
            "converge if this flag is omitted. Confirmed live: applying "
            "static_mode=True with a correct static_subscriptions table "
            "alone was NOT sufficient -- discovery_peers_seen_ids stayed "
            "empty until this flag was also added (see "
            "docs/AUDIT_ACCEPTANCE_TRACKING.md, 'WIFI GATEWAY BENCHMARK' "
            "Phase 3/STEP 3)."
        ),
    )
    parser.add_argument("--required-peer-ids", type=str, required=True)
    parser.add_argument("--start-wait-timeout-s", type=float, default=60.0)
    parser.add_argument("--drain-s", type=float, default=10.0)
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, default=None)
    parser.add_argument("--start-file", type=Path, default=None)
    args = parser.parse_args()

    import rclpy
    import rclpy.publisher
    from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    rows = load_all_rows(args.trace, args.policy)
    uplink_topics, downlink_topics = uplink_and_downlink_topics(rows, args.control_station_name)
    all_relay_topics = uplink_topics + downlink_topics

    required_peer_ids = frozenset(p for p in args.required_peer_ids.split(",") if p)

    rclpy.init()
    _original_publisher_publish = rclpy.publisher.Publisher.publish

    def _publish_suppressing_parameter_events(self, *pub_args, **pub_kwargs):
        if self.topic_name == "/parameter_events":
            return None
        return _original_publisher_publish(self, *pub_args, **pub_kwargs)

    rclpy.publisher.Publisher.publish = _publish_suppressing_parameter_events
    node = rclpy.create_node("fleetqox_gateway_endpoint", start_parameter_services=False)
    qos = QoSProfile(
        history=HistoryPolicy.KEEP_LAST, depth=64, reliability=ReliabilityPolicy.RELIABLE,
    )

    # Diagnostics (Phase 8): received/forwarded/dropped counts and
    # per-message processing time -- no queue is invented; forwarding
    # happens synchronously inside the subscription callback, matching
    # every other endpoint's own on_message() pattern in this harness.
    received_count = 0
    forwarded_count = 0
    dropped_count = 0
    processing_times_ns: list[int] = []

    relay_publishers: dict[str, Any] = {
        topic: node.create_publisher(String, topic + args.relay_topic_suffix, qos)
        for topic in all_relay_topics
    }

    def make_relay_callback(topic: str):
        publisher = relay_publishers[topic]

        def _relay(msg: String) -> None:
            nonlocal received_count, forwarded_count, dropped_count
            received_count += 1
            t0 = time.monotonic_ns()
            try:
                # Relayed byte-for-byte, unparsed -- event_id, deadline_ms,
                # and the ORIGINAL sent_wall_ns (set by the robot/
                # control_station at first publish) all survive
                # unmodified, which is what makes end-to-end (original
                # publish -> final receive) latency valid without any
                # new wire-format field.
                publisher.publish(msg)
                forwarded_count += 1
            except Exception:  # noqa: BLE001
                dropped_count += 1
                raise
            finally:
                processing_times_ns.append(time.monotonic_ns() - t0)

        return _relay

    for topic in all_relay_topics:
        node.create_subscription(String, topic, make_relay_callback(topic), qos)

    # Same shared discovery-probe beacon convention as
    # fleetqox_rmw_trace_endpoint.py, reusing its own pure
    # discovery_converged() so the readiness contract is identical --
    # not reimplemented differently for the gateway.
    beacon_topic = "/fleetqox_trace/_discovery_probe"
    beacon_qos = QoSProfile(
        history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.RELIABLE,
    )
    beacon_pub = node.create_publisher(String, beacon_topic, beacon_qos)
    beacon_msg = String()
    beacon_msg.data = "gateway"
    discovery_peers_seen: set[str] = set()

    def on_beacon(msg: String) -> None:
        if msg.data != "gateway":
            discovery_peers_seen.add(msg.data)

    node.create_subscription(String, beacon_topic, on_beacon, beacon_qos)

    discovery_start = time.monotonic()
    discovery_deadline = discovery_start + args.discovery_timeout_s
    last_beacon_sent = 0.0
    converged_at: float | None = None
    if not args.skip_discovery_wait:
        while time.monotonic() < discovery_deadline:
            now = time.monotonic()
            if now - last_beacon_sent >= 0.1:
                beacon_pub.publish(beacon_msg)
                last_beacon_sent = now
            for _ in range(20):
                rclpy.spin_once(node, timeout_sec=0.0)
            rclpy.spin_once(node, timeout_sec=0.1)
            if required_peer_ids <= discovery_peers_seen and converged_at is None:
                converged_at = time.monotonic()
    discovery_convergence_s = (
        (converged_at - discovery_start) if converged_at is not None
        else (time.monotonic() - discovery_start)
    )
    converged = discovery_converged(
        skip_discovery_wait=args.skip_discovery_wait,
        beacon_active=True,
        peers_seen=len(discovery_peers_seen),
        expected_peer_count=len(required_peer_ids),
        subscription_fallback_ok=False,
        required_peer_ids=required_peer_ids,
        peers_seen_ids=frozenset(discovery_peers_seen),
    )

    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.write_text("ready\n" if converged else "invalid_readiness\n")

    def write_result(start_gate_timed_out: bool) -> None:
        # Factored out so a start-gate timeout (readiness never became
        # valid, see docs/AUDIT_ACCEPTANCE_TRACKING.md, "WIFI GATEWAY
        # BENCHMARK" STEP 2) still records the diagnostic counts
        # gathered SO FAR (received/forwarded/dropped -- all real,
        # nothing invented) instead of losing them to an uncaught
        # exception with no summary written at all. This is diagnostic
        # visibility only -- it does not change whether readiness was
        # valid, does not fabricate a "ready" result, and does not
        # affect any measured Table V metric.
        result = {
            "endpoint": "gateway",
            "discovery_ready": converged,
            "discovery_convergence_s": discovery_convergence_s,
            "discovery_required_peer_ids": sorted(required_peer_ids),
            "discovery_peers_seen_ids": sorted(discovery_peers_seen),
            "uplink_topics": uplink_topics,
            "downlink_topics": downlink_topics,
            "start_gate_timed_out": start_gate_timed_out,
            "gateway_received_count": received_count,
            "gateway_forwarded_count": forwarded_count,
            "gateway_dropped_count": dropped_count,
            "gateway_processing_time_ns_mean": (
                sum(processing_times_ns) / len(processing_times_ns) if processing_times_ns else None
            ),
            "gateway_processing_time_ns_max": (
                max(processing_times_ns) if processing_times_ns else None
            ),
        }
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")

    if args.start_file:
        start_deadline = time.monotonic() + args.start_wait_timeout_s
        while time.monotonic() < start_deadline and not args.start_file.exists():
            rclpy.spin_once(node, timeout_sec=0.05)
        if not args.start_file.exists():
            write_result(start_gate_timed_out=True)
            raise RuntimeError("timed out waiting for data-plane start gate")

    # No fixed replay schedule of its own (the gateway does not know
    # the workload's timing, only relays whatever arrives) -- stays
    # alive and spinning through the whole measurement + drain window
    # so nothing sent late by a robot/control_station is missed. Margin
    # matches robot/control_station's own drain-based lifecycle (a
    # small fixed buffer beyond drain_s, not an arbitrary large number)
    # -- an earlier +60.0 here made the orchestrator's own result
    # collection read this process's summary before it had been
    # written, a harness bug (see docs/AUDIT_ACCEPTANCE_TRACKING.md,
    # "WIFI GATEWAY BENCHMARK" Phase 3/STEP 3), not a real requirement.
    run_deadline = time.monotonic() + args.drain_s + 5.0
    while time.monotonic() < run_deadline:
        rclpy.spin_once(node, timeout_sec=0.1)

    write_result(start_gate_timed_out=False)
    print(json.dumps({"status": "ok", "endpoint": "gateway", "forwarded": forwarded_count}))
    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
