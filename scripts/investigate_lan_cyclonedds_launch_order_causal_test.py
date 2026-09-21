"""LAN Phase 4 causal test (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN
DISCOVERY-CONVERGENCE ROOT-CAUSE INVESTIGATION"): CycloneDDS's LAN Table
V readiness failure at N=2/N=4/N=16 has repeatedly isolated the
numerically-LAST robot (robot_0001 at N=2, robot_0003 at N=4,
robot_0015 at N=16 -- always the last entry in endpoint_list()). This
could mean either (a) something inherent to that robot's IDENTITY/index
(its IP address, its position in the static-peers XML <Peer> list --
BOTH fixed by self.endpoints/self.ips, independent of launch order), or
(b) something about being LAUNCHED LAST in wall-clock time (a genuine
launch-order/lifecycle race).

This script tests N=4, three conditions, with self.endpoints/self.ips
(hence each robot's IP and its position in every OTHER robot's XML
peer list) held IDENTICAL across all three -- ONLY the ORDER in which
`docker exec -d` is issued to launch each endpoint's process changes:

  A: current/default order -- control_station, robot_0000, robot_0001,
     robot_0002, robot_0003 (robot_0003 launched last).
  B: reversed robot order -- control_station, robot_0003, robot_0002,
     robot_0001, robot_0000 (robot_0000 launched last).
  C: a third, different rotation -- control_station, robot_0000,
     robot_0002, robot_0003, robot_0001 (robot_0001 launched last).

If failure follows LAUNCH POSITION: A fails robot_0003, B fails
robot_0000, C fails robot_0001 (three different robots, always
whichever was issued last).
If failure follows IDENTITY: the SAME robot (whichever it is) fails in
all three regardless of launch order.

Read-only diagnostic -- does NOT change run_lan_probe()/launch_endpoints()
or any production code path.
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
    ReferenceTopologyProbe,
    docker,
    endpoint_list,
    required_peers_from_trace,
)
from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

NUM_ROBOTS = 4
SEED = 7
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_cyclonedds_launch_order_causal_test"

CONDITIONS = {
    # launch_order is a list of INDEXES into probe.endpoints (0=control_station).
    "A_default_order": [0, 1, 2, 3, 4],
    "B_reversed_robots": [0, 4, 3, 2, 1],
    "C_third_rotation": [0, 1, 3, 4, 2],
}


def build_cyclonedds_config(probe: ReferenceTopologyProbe, endpoint: str) -> str:
    peer_xml = "".join(
        f'<Peer address="{probe.ips[other]}"/>' for other in probe.endpoints if other != endpoint
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<CycloneDDS xmlns="https://cdds.io/config">'
        "<Domain><General><AllowMulticast>false</AllowMulticast></General>"
        f"<Discovery><Peers>{peer_xml}</Peers>"
        "<ParticipantIndex>0</ParticipantIndex></Discovery>"
        "</Domain></CycloneDDS>"
    )


def run_condition(name: str, launch_order: list[int]) -> dict:
    run_id = f"lan_cyclonedds_causal_{name}"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario=f"lan_cyclonedds_causal_{name}",
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
    result = {"condition": name, "launch_order_indexes": launch_order}
    try:
        probe.start_containers()
        probe.wire_network_lan()

        docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        probe._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(probe.endpoints))]
        probe._start_file = f"{results_dir_container}/start"

        launch_sequence = [probe.endpoints[i] for i in launch_order]
        result["launch_sequence_endpoints"] = launch_sequence

        for i in launch_order:
            endpoint = probe.endpoints[i]
            required = required_peer_ids_by_endpoint.get(endpoint, frozenset())
            cyclonedds_config = build_cyclonedds_config(probe, endpoint)
            cyclonedds_config_path = f"/tmp/cyclonedds_causal_{i}.xml"
            docker(
                "exec", probe.endpoint_container_names[i], "bash", "-lc",
                f"echo {shlex.quote(cyclonedds_config)} > {cyclonedds_config_path}",
            )
            result_json = f"{results_dir_container}/result_{i}.json"
            log_file = f"{results_dir_container}/endpoint_{i}.log"
            env_prefix = (
                f"RMW_IMPLEMENTATION=rmw_cyclonedds_cpp CYCLONEDDS_URI={cyclonedds_config_path} "
            )
            inner = (
                "source /opt/ros/jazzy/setup.bash && "
                f"export {env_prefix}&& "
                f"python3 -B /work/scripts/fleetqox_rmw_trace_endpoint.py "
                f"--trace={shlex.quote(trace_container_path)} "
                f"--endpoint={shlex.quote(endpoint)} "
                f"--policy=fifo "
                f"--start-offset-ms=2000.0 "
                f"--drain-s=10.0 "
                f"--discovery-timeout-s=15.0 "
                f"--start-wait-timeout-s=45.0 "
                f"--expected-peer-count={NUM_ROBOTS}"
                f" --required-peer-ids={shlex.quote(','.join(sorted(required)))} "
                f"--summary-json=/work/{result_json} "
                f"--ready-file=/work/{probe._ready_files[i]} "
                f"--start-file=/work/{probe._start_file}"
            )
            cmd = f"{inner} > /work/{log_file} 2>&1"
            docker("exec", "-d", probe.endpoint_container_names[i], "bash", "-lc", cmd)
            # Small, FIXED, identical stagger between launches (not a
            # readiness wait -- this is just how fast we ISSUE the next
            # docker exec, kept constant across all 3 conditions so
            # "who is issued last" is the only thing that varies).
            time.sleep(0.15)

        try:
            probe.wait_for_ready_then_start(ready_deadline_s=30.0)
            result["readiness"] = "ALL_READY"
        except Exception as exc:  # noqa: BLE001
            result["readiness"] = f"NOT_READY: {exc}"
        time.sleep(1.0)

        readiness_by_endpoint = {}
        for i, endpoint in enumerate(endpoints):
            ready_file = ROOT / results_dir_container / f"ready_{i}"
            readiness_by_endpoint[endpoint] = (
                ready_file.read_text().strip() if ready_file.exists() else None
            )
        result["readiness_by_endpoint"] = readiness_by_endpoint
        result["failing_endpoints"] = [
            ep for ep, status in readiness_by_endpoint.items() if status != "ready"
        ]
    finally:
        probe.teardown()
    return result


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for name, launch_order in CONDITIONS.items():
        print(f"=== Condition {name}: launch order (indexes) = {launch_order} ===", flush=True)
        r = run_condition(name, launch_order)
        results.append(r)
        print(json.dumps({
            "condition": r["condition"],
            "launch_sequence_endpoints": r["launch_sequence_endpoints"],
            "readiness": r["readiness"],
            "failing_endpoints": r["failing_endpoints"],
        }, indent=2), flush=True)

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)

    print("\n=== CAUSAL VERDICT ===", flush=True)
    for r in results:
        last_launched = r["launch_sequence_endpoints"][-1]
        print(
            f"{r['condition']}: last-launched={last_launched}, "
            f"failing={r['failing_endpoints']}, "
            f"matches_last_launched={r['failing_endpoints'] == [last_launched]}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
