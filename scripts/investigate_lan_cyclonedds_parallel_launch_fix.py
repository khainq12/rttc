"""LAN Phase 4/6 fix hypothesis test (see docs/AUDIT_ACCEPTANCE_TRACKING.md,
"LAN DISCOVERY-CONVERGENCE ROOT-CAUSE INVESTIGATION"): the causal test
(investigate_lan_cyclonedds_launch_order_causal_test.py) proved
CycloneDDS's LAN readiness failure follows LAUNCH POSITION (whichever
endpoint is issued last), 3/3 independent rotations. The current
harness (launch_endpoints()) issues each endpoint's `docker exec -d`
SEQUENTIALLY in a plain Python for-loop -- no explicit sleep, but each
subprocess.run() call itself takes tens of ms, so the last endpoint's
CycloneDDS process genuinely starts measurably later than the first.

This script tests whether launching all endpoints CONCURRENTLY (a
thread per endpoint, all `docker exec -d` calls dispatched
near-simultaneously) removes the asymmetry -- this is the opposite of
adding a sleep: it REMOVES the harness's own implicit serial launch
delay, which is exactly the "observable dependency" the strict rules
allow addressing (here, the dependency is "peer process has started",
addressed by not creating an artificial ordering in the first place,
rather than waiting out a fixed time).

Read-only diagnostic -- does NOT change run_lan_probe()/launch_endpoints()
or any production code path yet. If this confirms the fix, it will be
implemented as a minimal, tested change to launch_endpoints() itself.
"""

from __future__ import annotations

import json
import shlex
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
REPEATS = 3
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_cyclonedds_parallel_launch_fix"


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


def run_once(rep: int) -> dict:
    run_id = f"lan_cyclonedds_parallel_rep{rep}"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario=f"lan_cyclonedds_parallel_rep{rep}",
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
    result = {"rep": rep}
    try:
        probe.start_containers()
        probe.wire_network_lan()
        docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        probe._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(probe.endpoints))]
        probe._start_file = f"{results_dir_container}/start"

        # Pre-write every XML config FIRST (sequential, cheap, no DDS
        # process started yet) so the only thing happening concurrently
        # below is each endpoint's OWN process actually starting.
        config_paths = {}
        for i, endpoint in enumerate(probe.endpoints):
            cyclonedds_config = build_cyclonedds_config(probe, endpoint)
            path = f"/tmp/cyclonedds_parallel_{i}.xml"
            docker(
                "exec", probe.endpoint_container_names[i], "bash", "-lc",
                f"echo {shlex.quote(cyclonedds_config)} > {path}",
            )
            config_paths[i] = path

        def launch_one(i: int) -> None:
            endpoint = probe.endpoints[i]
            required = required_peer_ids_by_endpoint.get(endpoint, frozenset())
            result_json = f"{results_dir_container}/result_{i}.json"
            log_file = f"{results_dir_container}/endpoint_{i}.log"
            env_prefix = f"RMW_IMPLEMENTATION=rmw_cyclonedds_cpp CYCLONEDDS_URI={config_paths[i]} "
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

        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=len(probe.endpoints)) as pool:
            list(pool.map(launch_one, range(len(probe.endpoints))))
        result["dispatch_wall_s"] = time.monotonic() - t0

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
    for rep in range(1, REPEATS + 1):
        print(f"=== Parallel-launch rep {rep}/{REPEATS} ===", flush=True)
        r = run_once(rep)
        results.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "readiness_by_endpoint"}, indent=2), flush=True)

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    all_clean = all(r["readiness"] == "ALL_READY" for r in results)
    print(f"\nAll {REPEATS} reps fully ready: {all_clean}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
