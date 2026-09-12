"""Zero-middleware raw-UDP trace replay endpoint.

Test 3 of the ChatGPT-guided causal-isolation experiment (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "causal isolation: middleware vs
TapBridge"). Replays the exact same CSV trace, through the exact same
TapBridge/netns/RealtimeSimulatorImpl pipeline run_ns3_docker_wifi_tap_
rmw_probe.py already sets up for fleetqox_rmw_trace_endpoint.py, but with
NO ROS2/rclpy/RMW/discovery involved at all: plain socket.sendto()/
recvfrom() against a static name->IP peer map handed in on the command
line (no discovery step -- that omission is the entire point of this
test). Distinguishes whether the 16-robot delivery collapse already seen
identically across rmw_fleetqox_cpp, Fast DDS, and Cyclone DDS is caused
by middleware/discovery traffic itself, or by the TapBridge/Linux-netns
integration path every one of those middlewares shares.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import socket
import threading
import time
from typing import Any


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


def build_payload(row: dict[str, str], target_bytes: int) -> bytes:
    """Same wire shape as fleetqox_rmw_trace_endpoint.py's build_payload,
    so both tests' delivery/latency numbers are directly comparable."""
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
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_peers(spec: str) -> dict[str, str]:
    peers: dict[str, str] = {}
    for item in spec.split(","):
        if not item:
            continue
        name, ip = item.split("=", 1)
        peers[name] = ip
    return peers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument(
        "--peers",
        required=True,
        help="comma-separated name=ip pairs for every endpoint in the run "
        "(including this one -- the entry for --endpoint is simply unused)",
    )
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--start-offset-ms", type=float, default=1000.0)
    parser.add_argument("--start-wait-timeout-s", type=float, default=60.0)
    parser.add_argument("--drain-s", type=float, default=10.0)
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, default=None)
    parser.add_argument("--start-file", type=Path, default=None)
    args = parser.parse_args()

    peers = parse_peers(args.peers)

    rows = load_rows(args.trace, args.policy, args.endpoint)
    outgoing = sorted(
        (row for row in rows if row["src"] == args.endpoint),
        key=lambda row: float(row["timestamp_ms"]),
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(0.2)

    received: list[dict[str, Any]] = []
    stop_event = threading.Event()

    def receiver_loop() -> None:
        while not stop_event.is_set():
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            recv_wall_ns = time.time_ns()
            try:
                payload = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            received.append(
                {
                    "event_id": payload["e"],
                    "deadline_ms": payload["d"],
                    "sent_wall_ns": payload["s"],
                    "recv_wall_ns": recv_wall_ns,
                }
            )

    recv_thread = threading.Thread(target=receiver_loop, daemon=True)
    recv_thread.start()

    # No discovery step at all -- every peer's IP is already known
    # statically. Still uses the same ready/start double-gate as
    # fleetqox_rmw_trace_endpoint.py so every endpoint's replay "t=0"
    # lines up despite each one's ns-3/tap attach finishing at a slightly
    # different real time.
    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.touch()
    if args.start_file:
        start_deadline = time.monotonic() + args.start_wait_timeout_s
        while time.monotonic() < start_deadline and not args.start_file.exists():
            time.sleep(0.05)
        if not args.start_file.exists():
            raise RuntimeError("timed out waiting for data-plane start gate")

    start_wall = time.monotonic()
    sent: list[str] = []
    send_timing: list[dict[str, Any]] = []
    for row in outgoing:
        target_offset_s = (float(row["timestamp_ms"]) + args.start_offset_ms) / 1000.0
        now_offset = time.monotonic() - start_wall
        if target_offset_s > now_offset:
            time.sleep(target_offset_s - now_offset)
        target_bytes = max(1, int(row["bytes"]))
        payload = build_payload(row, target_bytes)
        dst_ip = peers[row["dst"]]
        # Timing instrumentation added for the causal-isolation investigation
        # (see docs/AUDIT_ACCEPTANCE_TRACKING.md "send-timing burstiness") --
        # this raw-UDP control's sendto() is the near-immediate, unbuffered
        # baseline fleetqox_rmw_trace_endpoint.py's publish()-call timing is
        # compared against.
        before_wall_ns = time.monotonic_ns()
        sock.sendto(payload, (dst_ip, args.port))
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
        time.sleep(0.1)

    stop_event.set()
    recv_thread.join(timeout=1.0)
    sock.close()

    result = {
        "schema_version": "fleetqox.raw_udp_trace_endpoint.v1",
        "endpoint": args.endpoint,
        "policy": args.policy,
        "tx": len(sent),
        "rx": len(received),
        "sent_event_ids": sent,
        "send_timing": send_timing,
        "received": received,
    }
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "ok", "endpoint": args.endpoint, "tx": len(sent), "rx": len(received)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
