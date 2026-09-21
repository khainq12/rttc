"""LAN Phase 1 (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN DISCOVERY
FIRST-DIVERGENCE INVESTIGATION"): maps PID -> process -> UDP/TCP
sockets -> local/remote ports, live, during the 15s discovery window,
for Fast DDS, CycloneDDS, and Zenoh at N=4. Snapshots `ss -uapn`/
`ss -tapn` on control_station and robot_0000 at multiple points during
discovery so the ACTUAL metatraffic ports (not just the discovery-
server/router bootstrap port) are identified with a timestamp, before
any packet capture is attempted against them.

Read-only diagnostic -- does not change any production code path.
"""

from __future__ import annotations

import json
import shlex
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    FASTDDS_DISCOVERY_SERVER_PORT,
    ReferenceTopologyProbe,
    docker,
    endpoint_list,
    required_peers_from_trace,
)
from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

NUM_ROBOTS = 4
SEED = 7
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_discovery_socket_mapping"
SNAPSHOT_OFFSETS_S = [1.0, 3.0, 6.0, 10.0, 14.0]


def snapshot_sockets(container_name: str) -> dict:
    r_udp = docker("exec", container_name, "bash", "-lc", "ss -uapn 2>&1", check=False)
    r_tcp = docker("exec", container_name, "bash", "-lc", "ss -tapn 2>&1", check=False)
    r_procs = docker(
        "exec", container_name, "bash", "-lc",
        "for p in /proc/[0-9]*; do pid=$(basename $p); "
        "cmd=$(tr '\\0' ' ' < $p/cmdline 2>/dev/null | cut -c1-120); "
        "[ -n \"$cmd\" ] && echo \"$pid: $cmd\"; done",
        check=False,
    )
    return {"udp": r_udp.stdout, "tcp": r_tcp.stdout, "procs": r_procs.stdout}


def run_for_middleware(name: str, rmw_impl: str, discovery_mode: str, port_hint: int | None) -> dict:
    run_id = f"lan_socket_map_{name}"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario=f"lan_socket_map_{name}",
        robots=NUM_ROBOTS,
        seconds=3,
        seed=SEED,
        capacity_bytes_per_second=max(200_000, NUM_ROBOTS * 6_000),
        capacity_packets_per_second=None,
        capacity_airtime_ns_per_second=None,
        policies=("fifo",),
        include_non_sent=False,
        merge_control_station=True,
    )
    write_simulator_csv(events, trace_path)
    trace_container_path = f"/work/{trace_path.relative_to(ROOT)}"
    endpoints = endpoint_list(NUM_ROBOTS)
    required_peer_ids_by_endpoint = required_peers_from_trace(trace_path, "fifo", endpoints)
    results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
    (ROOT / results_dir_container).mkdir(parents=True, exist_ok=True)

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=DEFAULT_IMAGE, num_robots=NUM_ROBOTS, output_dir=output_dir
    )
    result = {"middleware": name}
    try:
        probe.start_containers()
        probe.wire_network_lan()
        if rmw_impl == "rmw_zenoh_cpp":
            probe.start_zenoh_router()
        if rmw_impl == "rmw_fastrtps_cpp":
            probe.start_fastdds_discovery_server()

        t0 = time.monotonic()
        probe.launch_endpoints(
            trace_container_path=trace_container_path,
            policy="fifo",
            start_offset_ms=2000.0,
            drain_s=10.0,
            discovery_timeout_s=15.0,
            static_mode=False,
            static_subscriptions=None,
            extra_rmw_env=None,
            results_dir_container=results_dir_container,
            start_wait_timeout_s=45.0,
            rmw_implementation=rmw_impl,
            discovery_mode=discovery_mode,
            required_peer_ids_by_endpoint=required_peer_ids_by_endpoint,
        )

        control_station_name = probe.endpoint_container_names[0]
        robot0_name = probe.endpoint_container_names[1]
        snapshots = []
        for offset in SNAPSHOT_OFFSETS_S:
            wait = t0 + offset - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            snapshots.append({
                "t_offset_s": offset,
                "control_station": snapshot_sockets(control_station_name),
                "robot_0000": snapshot_sockets(robot0_name),
            })
        result["snapshots"] = snapshots

        try:
            probe.wait_for_ready_then_start(ready_deadline_s=30.0)
            result["readiness"] = "ALL_READY"
        except Exception as exc:  # noqa: BLE001
            result["readiness"] = f"NOT_READY: {exc}"

        readiness_by_endpoint = {}
        for i, endpoint in enumerate(endpoints):
            ready_file = ROOT / results_dir_container / f"ready_{i}"
            readiness_by_endpoint[endpoint] = (
                ready_file.read_text().strip() if ready_file.exists() else None
            )
        result["readiness_by_endpoint"] = readiness_by_endpoint
    finally:
        probe.teardown()
    return result


def summarize_ports(snapshot_udp_text: str) -> list[str]:
    lines = [l for l in snapshot_udp_text.splitlines() if l.strip() and not l.startswith("State")]
    return lines


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    configs = [
        ("fastdds", "rmw_fastrtps_cpp", "discovery_server", FASTDDS_DISCOVERY_SERVER_PORT),
        ("cyclonedds", "rmw_cyclonedds_cpp", "static_peers", None),
        ("zenoh", "rmw_zenoh_cpp", "default", 7447),
    ]
    all_results = {}
    for name, rmw_impl, discovery_mode, port_hint in configs:
        print(f"=== {name} ===", flush=True)
        r = run_for_middleware(name, rmw_impl, discovery_mode, port_hint)
        all_results[name] = r
        print(f"readiness: {r['readiness']}", flush=True)
        for ep, status in r["readiness_by_endpoint"].items():
            print(f"  {ep}: {status}", flush=True)
        # Print a concise view of the LAST snapshot's UDP sockets for both.
        last = r["snapshots"][-1]
        print(f"  control_station UDP sockets @t={last['t_offset_s']}s:")
        for line in summarize_ports(last["control_station"]["udp"]):
            print(f"    {line}")
        print(f"  robot_0000 UDP sockets @t={last['t_offset_s']}s:")
        for line in summarize_ports(last["robot_0000"]["udp"]):
            print(f"    {line}")

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(all_results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
