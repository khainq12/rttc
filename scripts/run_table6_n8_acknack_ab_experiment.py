"""Table VI N=8 seed=7 -- CONTROLLED A/B/A EXPERIMENT for the ACK/NACK
redundancy causal hypothesis (see docs/AUDIT_ACCEPTANCE_TRACKING.md,
"TABLE VI N=8 TRAFFIC COMPOSITION AND ACK/NACK CORRELATION", whose own
"exactly one next experiment" this implements).

A = FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT left UNSET (this
    project's own documented default -- see ack_nack_redundant_resend_count()
    in rmw_pubsub.cpp: parse_nonnegative_int_env(..., 10, 20), i.e. 10
    redundant copies on top of the one immediate send = 11 total sends
    per acknowledged/nacked frame).
B = FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=0 -- the minimum valid
    value the existing (already-defined, unmodified) env var accepts
    (parse_nonnegative_int_env rejects negative values by falling back
    to the default; 0 is a genuine, accepted, non-default value): the
    ONE immediate ack/nack send only, zero redundant copies.

Runs A1 -> B -> A2 in that fixed order (not randomized -- this is a
before/after/reversion check, not a blinded trial) to test reversion,
not just a single before/after difference. NOTHING else changes: same
N=8, same seed=7, same num_crossings/timeouts/QoS/broadcast topic/
retransmission-loop settings (FLEETQOX_RMW_RELIABLE_ACK_TIMEOUT_MS
stays at its own default 0, i.e. the transport retry loop remains off
throughout, exactly as in every prior pass), same tcpdump capture
methodology as investigate_table6_n8_network_loss.py.

This is a single already-documented, already-exported env var this
project's own C++ defines and reads (rmw_pubsub.cpp,
ack_nack_redundant_resend_count()) -- no source code is edited or
rebuilt for this pass. Reverted (A2) before this script exits; the
default (A) is not a persistent left-over state anywhere in the repo.

Explicitly OUT OF SCOPE this pass (per the task): the robot_0007
connectivity-break investigation, the post-recvfrom() duplicate-drop
issue, any change to broadcast/QoS/timeouts/Ricart-Agrawala/ns-3/
production FleetRMW.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    READY_DEADLINE_S,
    RMW_PORT,
    ReadinessFailure,
    ReferenceTopologyProbe,
    docker,
    endpoint_list,
)

NUM_ROBOTS = 8
SEED = 7
TCPDUMP_PORTABLE_HOST_DIR = ROOT / ".tcpdump_portable"
ENDPOINTS = endpoint_list(NUM_ROBOTS)
IP_OF = {e: f"10.60.0.{i + 2}" for i, e in enumerate(ENDPOINTS)}
IP_TO_EP = {v: k for k, v in IP_OF.items()}

RUNS = [
    ("A1", None),
    ("B", "0"),
    ("A2", None),
]


def deploy_portable_tcpdump(container_name: str) -> None:
    docker("cp", str(TCPDUMP_PORTABLE_HOST_DIR), f"{container_name}:/tmp/tcpdump_portable")


def start_capture(container_name: str, pcap_path_container: str, pid_file_container: str) -> None:
    cmd = (
        f"LD_LIBRARY_PATH=/tmp/tcpdump_portable/lib "
        f"/tmp/tcpdump_portable/bin/tcpdump -Z root -i eth0 -n "
        f"udp port {RMW_PORT} -w /work/{pcap_path_container} "
        f"> /work/{pcap_path_container}.log 2>&1 & "
        f"echo $! > /work/{pid_file_container}"
    )
    docker("exec", "-d", container_name, "bash", "-lc", cmd)


def stop_capture(container_name: str, pid_file_host: Path) -> None:
    if not pid_file_host.exists():
        return
    pid = pid_file_host.read_text().strip()
    if pid:
        docker("exec", container_name, "bash", "-lc", f"kill -INT {pid} 2>/dev/null || true")


def parse_pcap(path: Path):
    data = path.read_bytes()
    if len(data) < 24:
        return
    magic = data[0:4]
    endian = "<" if magic == b"\xd4\xc3\xb2\xa1" else ">"
    offset = 24
    n = len(data)
    while offset + 16 <= n:
        ts_sec, ts_usec, incl_len, _orig_len = struct.unpack_from(endian + "IIII", data, offset)
        offset += 16
        if offset + incl_len > n:
            break
        frame = data[offset:offset + incl_len]
        offset += incl_len
        if len(frame) < 14:
            continue
        ethertype = struct.unpack_from(">H", frame, 12)[0]
        if ethertype != 0x0800:
            continue
        ip_start = 14
        if len(frame) < ip_start + 20:
            continue
        ihl_words = frame[ip_start] & 0x0F
        ip_header_len = ihl_words * 4
        protocol = frame[ip_start + 9]
        if protocol != 17:
            continue
        src_ip = ".".join(str(b) for b in frame[ip_start + 12:ip_start + 16])
        dst_ip = ".".join(str(b) for b in frame[ip_start + 16:ip_start + 20])
        udp_start = ip_start + ip_header_len
        if len(frame) < udp_start + 8:
            continue
        src_port, dst_port, udp_len, _checksum = struct.unpack_from(">HHHH", frame, udp_start)
        payload = frame[udp_start + 8:udp_start + udp_len] if udp_len >= 8 else b""
        yield {
            "wall_s": ts_sec + ts_usec / 1_000_000.0, "src_ip": src_ip, "dst_ip": dst_ip,
            "src_port": src_port, "dst_port": dst_port, "length": incl_len, "payload": payload,
        }


def classify(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "UNKNOWN"
    if '"kind":"sidecar_packet_frame"' in text:
        return "DATA"
    if '"kind":"source_sequence_ack_nack"' in text:
        return "ACK" if '"missing_sequence_ranges":[]' in text else "NACK"
    if '"kind":"source_sequence_unrecoverable"' in text:
        return "UNRECOVERABLE"
    for other_kind in ("graph_advertisement", "route_advertisement", "service_frame", "action_frame"):
        if f'"kind":"{other_kind}"' in text:
            return "OTHER_CONTROL"
    return "UNKNOWN"


def run_one(label: str, redundant_count_env: str | None, output_root: Path) -> dict[str, Any]:
    output_dir = (output_root / f"fleetrmw_n{NUM_ROBOTS}_seed{SEED}_{label}").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name.lstrip(".")
    results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
    shutil.rmtree(ROOT / results_dir_container, ignore_errors=True)
    (ROOT / results_dir_container).mkdir(parents=True, exist_ok=True)

    extra_rmw_env = {"FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1"}
    if redundant_count_env is not None:
        extra_rmw_env["FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT"] = redundant_count_env

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=DEFAULT_IMAGE, num_robots=NUM_ROBOTS, output_dir=output_dir
    )
    ready_deadline_s = max(READY_DEADLINE_S, int(15.0) + 15)
    start_wait_timeout_s = ready_deadline_s + 30
    status = "ok"
    error_text = ""
    pcap_files: dict[str, Path] = {}
    pid_files: dict[str, Path] = {}

    print(f"=== RUN {label} (ACK_NACK_REDUNDANT_RESEND_COUNT={redundant_count_env!r}) ===", flush=True)
    try:
        probe.start_containers()
        probe.build_ns3_binary()
        probe.wire_network()
        for i, endpoint in enumerate(ENDPOINTS):
            container_name = probe.endpoint_container_names[i]
            deploy_portable_tcpdump(container_name)
            pcap_path_container = f"{results_dir_container}/capture_{i}.pcap"
            pid_file_container = f"{results_dir_container}/tcpdump_{i}.pid"
            pcap_files[endpoint] = ROOT / pcap_path_container
            pid_files[endpoint] = ROOT / pid_file_container
            start_capture(container_name, pcap_path_container, pid_file_container)
        time.sleep(2.0)

        probe.start_ns3(sim_duration_s=60.0, num_aps=1, layout="circle", circle_radius=7.5,
                         path_loss_exponent=2.7, tx_power_dbm=15.0, rx_sensitivity_dbm=-82.0,
                         mobility_speed=0.0, ns3_seed=1, ns3_run=1)
        probe.launch_coordination_endpoints(
            num_crossings=5, crossing_duration_ms=300.0, reply_timeout_s=5.0,
            defer_release_timeout_s=8.0, priority_mode="lamport", seed=SEED,
            start_offset_ms=2000.0, discovery_timeout_s=15.0,
            start_wait_timeout_s=start_wait_timeout_s, scenario_timeout_s=120.0,
            results_dir_container=results_dir_container, rmw_implementation="rmw_fleetqox_cpp",
            discovery_mode="default", extra_rmw_env=extra_rmw_env,
        )
        probe.wait_for_ready_then_start(ready_deadline_s=ready_deadline_s)
        probe.wait_for_completion(timeout_s=2.0 + 120.0 + 60.0, results_dir_container=results_dir_container)
        endpoint_results = probe.collect_results(results_dir_container)
    except ReadinessFailure as exc:
        status = "invalid_readiness"
        error_text = str(exc)
        endpoint_results = {}
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error_text = str(exc)
        endpoint_results = {}
    finally:
        for i, endpoint in enumerate(ENDPOINTS):
            stop_capture(probe.endpoint_container_names[i], pid_files[endpoint])
        time.sleep(2.0)
        probe.teardown()

    print(f"  status={status} error={error_text!r}", flush=True)

    # ---- classify pcaps ----
    all_packets: dict[str, list[dict[str, Any]]] = {}
    for endpoint in ENDPOINTS:
        pkts = []
        for pkt in parse_pcap(pcap_files[endpoint]):
            if pkt["src_port"] != RMW_PORT and pkt["dst_port"] != RMW_PORT:
                continue
            pkt["class"] = classify(pkt["payload"])
            pkt["src_ep"] = IP_TO_EP.get(pkt["src_ip"])
            pkt["dst_ep"] = IP_TO_EP.get(pkt["dst_ip"])
            del pkt["payload"]
            pkts.append(pkt)
        all_packets[endpoint] = pkts

    return {
        "label": label,
        "redundant_count_env": redundant_count_env,
        "status": status,
        "error": error_text,
        "endpoint_results": endpoint_results,
        "packets": all_packets,
        "output_dir": str(output_dir.relative_to(ROOT)),
    }


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    packets = run["packets"]
    endpoint_results = run["endpoint_results"]

    class_count = defaultdict(int)
    class_bytes = defaultdict(int)
    for ep, pkts in packets.items():
        for p in pkts:
            if p["src_ep"] != ep:
                continue  # egress-only, avoid double counting
            class_count[p["class"]] += 1
            class_bytes[p["class"]] += p["length"]
    total_count = sum(class_count.values())
    total_bytes = sum(class_bytes.values())

    def data_delivery(exclude_robot_0007: bool) -> dict[str, Any]:
        left = defaultdict(int)
        arrived = defaultdict(int)
        for ep, pkts in packets.items():
            for p in pkts:
                if p["class"] != "DATA":
                    continue
                se, de = p["src_ep"], p["dst_ep"]
                if se is None or de is None or se == de:
                    continue
                if exclude_robot_0007 and ("robot_0007" in (se, de)):
                    continue
                if se == ep:
                    left["_"] += 1
                if de == ep:
                    arrived["_"] += 1
        l, a = left["_"], arrived["_"]
        return {"left": l, "arrived": a, "loss_pct": round(100 * (l - a) / l, 1) if l else None}

    total_req_sent = total_req_recv = total_reply_sent = total_reply_recv = 0
    dup_sum = 0
    crossings_completed = {}
    any_forced = {}
    task_completion_s = {}
    for ep, d in endpoint_results.items():
        if d is None:
            continue
        total_req_sent += len([x for x in d["sent_log"] if x["type"] == "request"])
        total_reply_sent += len([x for x in d["sent_log"] if x["type"] == "reply"])
        total_req_recv += len([x for x in d["raw_received_log"] if x["type"] == "request"])
        total_reply_recv += len(
            [x for x in d["raw_received_log"] if x["type"] == "reply" and x["to"] == ep]
        )
        dup_sum += d["fleetqox_stream_identity_diagnostics"].get("duplicate_data_frames_deduped", 0)
        crossings_completed[ep] = d["num_crossings_completed"]
        any_forced[ep] = any(c["forced_entry"] for c in d["crossings"])
        task_completion_s[ep] = d["task_completion_s"]

    return {
        "label": run["label"],
        "redundant_count_env": run["redundant_count_env"],
        "status": run["status"],
        "traffic_composition": {
            c: {"count": class_count.get(c, 0), "bytes": class_bytes.get(c, 0)}
            for c in ("DATA", "ACK", "NACK", "UNRECOVERABLE", "OTHER_CONTROL", "UNKNOWN")
        },
        "total_packets": total_count,
        "total_bytes": total_bytes,
        "data_delivery_all": data_delivery(exclude_robot_0007=False),
        "data_delivery_excl_robot_0007": data_delivery(exclude_robot_0007=True),
        "request_sent": total_req_sent,
        "request_received": total_req_recv,
        "reply_sent": total_reply_sent,
        "reply_received": total_reply_recv,
        "duplicate_data_frames_deduped_sum": dup_sum,
        "crossings_completed": crossings_completed,
        "any_forced_entry_by_endpoint": any_forced,
        "task_completion_s": task_completion_s,
    }


def main() -> int:
    output_root = ROOT / "results_rmw_socket" / "table6_n8_acknack_ab_experiment"
    summaries = []
    for label, redundant_count_env in RUNS:
        run = run_one(label, redundant_count_env, output_root)
        summary = summarize(run)
        summaries.append(summary)
        print(json.dumps({k: summary[k] for k in ("label", "status", "total_packets")}), flush=True)

    summary_path = output_root / "ab_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summaries, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
