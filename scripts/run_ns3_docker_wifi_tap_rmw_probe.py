"""Bridge the REAL rmw_fleetqox_cpp transport through a simulated ns-3
802.11 wifi network, instead of Group 6's existing wifi-parity test (which
sends raw single-shot UDP with no recovery beyond the 802.11 MAC's own
retry limit -- see external/ns3/fleetqox_trace_replay.cc /
external/omnetpp/TraceDrivenUdpApp.cc). This is the ns-3 half of that
effort: external/ns3/fleetqox_trace_replay_tap.cc provides the simulated
topology bridged via TapBridge, and scripts/fleetqox_rmw_trace_endpoint.py
replays one endpoint's trace rows through the real transport in each
per-station network namespace this script sets up.

Everything runs inside ONE Docker container (not N, unlike the existing
netem probes) because the tap/bridge/netns tree ns-3's TapBridge needs to
attach to has to live in the same network-namespace hierarchy as the
process running ns-3 itself.

Per-station networking, so a real process's traffic is FORCED through the
simulated wifi channel instead of bypassing it via ordinary same-host
kernel routing (which would otherwise silently "deliver" same-subnet
traffic locally without ever touching ns-3):

    real process (its own netns, eth0) --veth pair-- bridge brI --- tapI (ns-3 TapBridge, UseLocal mode)

FIRST ATTEMPT -- nothing here has been run yet. Timing constants (how long
to wait for ns-3 to attach to the tap devices before starting real
processes, etc.) are best-effort guesses documented as such; expect to
need to tune them once this actually runs. Defaults to the smallest
possible scenario (1 robot => 4 total stations) rather than
production-scale robot counts, matching the "start minimal, escalate"
methodology already used for the fragment-repair investigation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
RMW_PORT = 9100
BASE_IP_PREFIX = "10.50.0."  # .0/.1 reserved; stations start at .2
# ns-3's TapBridge (Mode=UseLocal) needs the tap device to already exist
# and be up before it tries to attach; this is how long we wait after
# starting the ns-3 process before assuming it has attached to every tap
# and it's safe to start sending real traffic. UNVERIFIED GUESS -- if
# real runs show endpoints failing to reach each other early on, this is
# the first thing to increase.
NS3_ATTACH_WAIT_S = 3


def endpoint_list(num_robots: int) -> list[str]:
    endpoints = ["fleet_controller", "fleet_router", "operator_ui"]
    endpoints.extend(f"robot_{i:04d}" for i in range(num_robots))
    return endpoints


def _container_path(path: Path) -> str:
    return f"/work/{path.relative_to(ROOT)}"


def build_shell_script(
    *,
    trace_container_path: str,
    endpoints: list[str],
    policy: str,
    num_robots: int,
    sim_duration_s: float,
    start_offset_ms: float,
    drain_s: float,
    results_dir_container: str,
) -> str:
    ips = {endpoint: f"{BASE_IP_PREFIX}{i + 2}" for i, endpoint in enumerate(endpoints)}

    lines: list[str] = [
        "set -e",
        f"mkdir -p {shlex.quote(results_dir_container)}",
        # Build the ns-3 tap-bridge program fresh every run, matching the
        # existing wifi-parity scripts' compile-on-each-invocation pattern
        # (ns3-tap-bridge is confirmed present in this image already, no
        # image rebuild needed -- see docs/AUDIT_ACCEPTANCE_TRACKING.md).
        (
            "g++ -std=c++17 external/ns3/fleetqox_trace_replay_tap.cc "
            "-o /tmp/fleetqox_tap_bridge "
            "$(pkg-config --cflags --libs ns3-core ns3-network ns3-mobility "
            "ns3-wifi ns3-tap-bridge)"
        ),
    ]

    for i, endpoint in enumerate(endpoints):
        lines.extend(
            [
                f"# --- station {i}: {endpoint} ---",
                f"ip link add name br{i} type bridge",
                f"ip link set br{i} up",
                f"ip tuntap add dev ftap{i} mode tap",
                f"ip link set ftap{i} up",
                f"ip link set ftap{i} master br{i}",
                f"ip link add v{i}br type veth peer name v{i}ns",
                f"ip link set v{i}br up",
                f"ip link set v{i}br master br{i}",
                f"ip netns add ns{i}",
                f"ip link set v{i}ns netns ns{i}",
                f"ip netns exec ns{i} ip link set v{i}ns name eth0",
                f"ip netns exec ns{i} ip addr add {ips[endpoint]}/24 dev eth0",
                f"ip netns exec ns{i} ip link set eth0 up",
                f"ip netns exec ns{i} ip link set lo up",
            ]
        )

    lines.extend(
        [
            "# --- start the simulated wifi network ---",
            (
                f"/tmp/fleetqox_tap_bridge --numRobots={num_robots} --tapPrefix=ftap "
                f"--simDuration={sim_duration_s:.12g} "
                f"> {results_dir_container}/ns3_tap.log 2>&1 &"
            ),
            "NS3_PID=$!",
            f"sleep {NS3_ATTACH_WAIT_S}",
            "if ! kill -0 $NS3_PID 2>/dev/null; then",
            f"  echo 'ns-3 tap-bridge process exited early, see {results_dir_container}/ns3_tap.log' >&2",
            f"  cat {results_dir_container}/ns3_tap.log >&2",
            "  exit 1",
            "fi",
            "# --- start every station's real RMW process ---",
            "ENDPOINT_PIDS=()",
        ]
    )

    start_file = f"{results_dir_container}/start"
    ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(endpoints))]
    for i, endpoint in enumerate(endpoints):
        peers = ",".join(
            f"{ips[other]}:{RMW_PORT}" for other in endpoints if other != endpoint
        )
        result_json = f"{results_dir_container}/result_{i}.json"
        log_file = f"{results_dir_container}/endpoint_{i}.log"
        cmd = (
            f"ip netns exec ns{i} env "
            f"RMW_IMPLEMENTATION=rmw_fleetqox_cpp "
            f"FLEETQOX_RMW_BIND=0.0.0.0:{RMW_PORT} "
            f"FLEETQOX_RMW_PEERS={peers} "
            f"python3 {_container_path(ROOT / 'scripts' / 'fleetqox_rmw_trace_endpoint.py')} "
            f"--trace={shlex.quote(trace_container_path)} "
            f"--endpoint={shlex.quote(endpoint)} "
            f"--policy={shlex.quote(policy)} "
            f"--start-offset-ms={start_offset_ms:.12g} "
            f"--drain-s={drain_s:.12g} "
            f"--summary-json={shlex.quote(result_json)} "
            f"--ready-file={shlex.quote(ready_files[i])} "
            f"--start-file={shlex.quote(start_file)} "
            f"> {shlex.quote(log_file)} 2>&1 &"
        )
        lines.append(cmd)
        lines.append("ENDPOINT_PIDS+=($!)")

    lines.extend(
        [
            # Every endpoint independently sets its OWN wall-clock "t=0"
            # right after its own discovery finishes -- without a shared
            # gate, endpoints that discover peers at different real times
            # would replay the trace against different time origins,
            # skewing the intended cross-endpoint schedule. Block every
            # process on --start-file until all of them have signalled
            # --ready-file, then release them together.
            "# --- wait for every endpoint to finish discovery, then release them together ---",
            "READY_DEADLINE=$(( $(date +%s) + 30 ))",
            "while true; do",
            "  MISSING=0",
        ]
        + [f"  [ -f {shlex.quote(f)} ] || MISSING=1" for f in ready_files]
        + [
            "  [ $MISSING -eq 0 ] && break",
            "  if [ $(date +%s) -ge $READY_DEADLINE ]; then",
            "    echo 'timed out waiting for every endpoint to become ready' >&2",
            "    break",
            "  fi",
            "  sleep 0.2",
            "done",
            f"touch {shlex.quote(start_file)}",
            "# --- wait for every real process, then tear down the network ---",
            'wait "${ENDPOINT_PIDS[@]}"',
            "kill $NS3_PID 2>/dev/null || true",
            "wait $NS3_PID 2>/dev/null || true",
        ]
    )
    # The container runs with --rm, so its filesystem (including every
    # endpoint's result JSON under results_dir_container) disappears the
    # moment it exits -- print each one to stdout, bracketed by a marker
    # containing its endpoint name, so run_probe() can pull them back out
    # of the captured subprocess output instead of needing `docker cp`
    # before removal.
    for i, endpoint in enumerate(endpoints):
        result_json = f"{results_dir_container}/result_{i}.json"
        lines.append(
            f"echo FLEETQOX_TAP_RESULT_BEGIN:{endpoint} && "
            f"cat {shlex.quote(result_json)} 2>/dev/null && "
            f"echo && echo FLEETQOX_TAP_RESULT_END:{endpoint}"
        )
    lines.append("echo FLEETQOX_TAP_PROBE_DONE")
    return "\n".join(lines)


def parse_endpoint_results(stdout: str, endpoints: list[str]) -> dict[str, Any]:
    """Pull each endpoint's JSON summary back out of the captured stdout
    (see build_shell_script's FLEETQOX_TAP_RESULT_BEGIN/END markers)."""

    results: dict[str, Any] = {}
    for endpoint in endpoints:
        begin = f"FLEETQOX_TAP_RESULT_BEGIN:{endpoint}"
        end = f"FLEETQOX_TAP_RESULT_END:{endpoint}"
        begin_index = stdout.find(begin)
        end_index = stdout.find(end)
        if begin_index == -1 or end_index == -1 or end_index < begin_index:
            results[endpoint] = None
            continue
        blob = stdout[begin_index + len(begin) : end_index].strip()
        try:
            results[endpoint] = json.loads(blob) if blob else None
        except json.JSONDecodeError:
            results[endpoint] = None
    return results


def run_probe(
    *,
    image: str,
    output_dir: Path,
    num_robots: int,
    policy: str,
    seconds: int,
    seed: int,
    sim_duration_s: float,
    start_offset_ms: float,
    drain_s: float,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_tap_{num_robots}robot_seed{seed}.csv"
    events = generate_trace_events(
        scenario=f"ns3_wifi_tap_rmw_probe_{num_robots}robot",
        robots=num_robots,
        seconds=seconds,
        seed=seed,
        capacity_bytes_per_second=max(200_000, num_robots * 6_000),
        policies=(policy,),
        include_non_sent=False,
    )
    packet_rows = write_simulator_csv(events, trace_path)

    endpoints = endpoint_list(num_robots)
    results_dir_container = "/tmp/fleetqox_tap_results"
    script = build_shell_script(
        trace_container_path=_container_path(trace_path),
        endpoints=endpoints,
        policy=policy,
        num_robots=num_robots,
        sim_duration_s=sim_duration_s,
        start_offset_ms=start_offset_ms,
        drain_s=drain_s,
        results_dir_container=results_dir_container,
    )

    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--cap-add",
            "NET_ADMIN",
            "--device",
            "/dev/net/tun",
            "--entrypoint",
            "bash",
            "-v",
            f"{ROOT}:/work",
            "-w",
            "/work",
            image,
            "-lc",
            script,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=max(120.0, sim_duration_s + 60.0),
    )

    endpoint_results = parse_endpoint_results(completed.stdout, endpoints)

    return {
        "schema_version": "fleetqox.ns3_wifi_tap_rmw_probe.v1",
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "trace": str(trace_path.relative_to(ROOT)),
        "packet_rows": packet_rows,
        "num_robots": num_robots,
        "endpoints": endpoints,
        "policy": policy,
        "endpoint_results": endpoint_results,
        "endpoint_results_complete": all(
            endpoint_results.get(endpoint) is not None for endpoint in endpoints
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--num-robots", type=int, default=1)
    parser.add_argument("--policy", default="fifo")
    parser.add_argument("--seconds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sim-duration-s", type=float, default=30.0)
    parser.add_argument("--start-offset-ms", type=float, default=2000.0)
    parser.add_argument("--drain-s", type=float, default=10.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results_rmw_socket/ns3_wifi_tap_rmw_probe"),
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path("results_rmw_socket/ns3_wifi_tap_rmw_probe_summary.json"),
    )
    args = parser.parse_args()

    summary = run_probe(
        image=args.image,
        output_dir=ROOT / args.output_dir,
        num_robots=max(args.num_robots, 1),
        policy=args.policy,
        seconds=max(args.seconds, 1),
        seed=args.seed,
        sim_duration_s=max(args.sim_duration_s, 1.0),
        start_offset_ms=max(args.start_offset_ms, 0.0),
        drain_s=max(args.drain_s, 1.0),
    )
    summary_path = ROOT / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"status={summary['status']} returncode={summary['returncode']}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
