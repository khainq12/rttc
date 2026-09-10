"""Run matched FleetQoX traces over ns-3 and OMNeT++/INET single-AP Wi-Fi.

Sibling to run_omnetpp_docker_parity.py (which matches the ns-3 p2p_star
topology against a wired OMNeT++/INET network). This one matches ns-3's
"wifi" topology (external/ns3/fleetqox_trace_replay.cc, single 802.11g
access point + station grid + ConstantVelocityMobilityModel -- see
run_ns3_docker_wifi_mobility_matrix.py for the ns-3-only version of this
matrix) against external/omnetpp/FleetQoxWifiReplay.ned (single AccessPoint +
WirelessHost stations + LinearMobility).

Scope note: this is still bounded-metric parity, not a high-fidelity
wireless-PHY-identical claim -- ns-3's Yans PHY/propagation model and INET's
scalar radio medium implement 802.11 contention, retry, and error behavior
differently in detail. The comparison checks that end-to-end delivery,
deadline-miss, p99 latency, and utility stay within declared bounds for the
same trace/seed/policy/robot-count/bitrate/spacing/speed.

p99 latency is compared as a RATIO (max/min), not an absolute-ms delta like
the wired p2p parity script uses: at high contention both simulators show
heavy-tailed, hundreds-of-ms latency distributions, where an absolute-ms
bound is the wrong instrument (a 15ms bound that suits ~10ms wired latencies
is meaningless once p99s are in the 400-1300ms range) -- a bounded ratio is
the metric that actually reflects whether the two simulators agree.
delivery_ratio_delta/deadline_miss_ratio_delta/normalized_utility_delta are
compared exactly like the p2p script (bounded 0-1 deltas, well-defined at
any scale).

Investigation history (see docs/AUDIT_ACCEPTANCE_TRACKING.md for full
detail): the first real run at 16/32 robots diverged sharply from ns-3.
Direct instrumentation (compiled trace-source counters, not guessing) on
both simulators found total PHY transmission count/channel-occupied time
differed only ~11-22% -- not the smoking gun expected. Checking every
standard 802.11g timing constant (CWmin/CWmax, retry limits, preamble/
header/slot duration, SIFS, TX power, RX sensitivity) found them consistent
between ns-3 and INET. The actual majority cause turned out to be
INET's PendingQueue defaulting to 100 packets vs ns-3's WifiMacQueue
defaulting to 500 -- INET was tail-dropping under load instead of queueing
the way ns-3 does, understating latency for what survived. Matching the
queue capacity (see external/omnetpp/omnetpp.ini) closed most of the gap:
deadline-miss-ratio deltas at 32 robots went from ~22 points to ~1.5 points.
The residual gap is consistent with normal cross-simulator RNG-stream
variance in backoff draws, not a further fixable misconfiguration.
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
from scripts.run_ns3_docker_fleet_matrix import parse_csv_summary  # noqa: E402
from scripts.run_ns3_docker_wifi_mobility_matrix import (  # noqa: E402
    SCENARIOS,
)
from scripts.run_omnetpp_docker_parity import (  # noqa: E402
    DEFAULT_NS3_IMAGE,
    DEFAULT_OMNETPP_IMAGE,
    INET_VERSION,
    OMNETPP_VERSION,
    POLICIES,
    _container_path,
    _sim_time_limit_seconds,
    _valid_policy_rows,
    build_omnetpp_image,
    docker_run,
)


SCHEMA_VERSION = "fleetqox.omnetpp_ns3_docker_wifi_parity.v2"
WIFI_WARMUP_MS = 1000.0
DEFAULT_THRESHOLDS = {
    "delivery_ratio_delta": 0.10,
    "deadline_miss_ratio_delta": 0.10,
    "p99_latency_ratio": 2.5,
    "normalized_utility_delta": 0.20,
}


def compare_policy_rows_wifi(
    ns3_rows: list[dict[str, Any]],
    omnetpp_rows: list[dict[str, Any]],
    *,
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    ns3 = {row["policy"]: row for row in ns3_rows}
    omnetpp = {row["policy"]: row for row in omnetpp_rows}
    comparisons: list[dict[str, Any]] = []
    for policy in POLICIES:
        left = ns3.get(policy)
        right = omnetpp.get(policy)
        if left is None or right is None:
            comparisons.append(
                {"policy": policy, "status": "failed", "reason": "policy_missing"}
            )
            continue
        ns3_delivery = left["rx"] / left["tx"] if left["tx"] else 0.0
        omnetpp_delivery = right["rx"] / right["tx"] if right["tx"] else 0.0
        ns3_utility = left["utility"] / left["tx"] if left["tx"] else 0.0
        omnetpp_utility = right["utility"] / right["tx"] if right["tx"] else 0.0
        utility_scale = max(abs(ns3_utility), abs(omnetpp_utility), 1e-12)
        p99_high = max(left["p99_ms"], right["p99_ms"])
        p99_low = max(min(left["p99_ms"], right["p99_ms"]), 1e-6)
        metrics = {
            "tx_equal": left["tx"] == right["tx"],
            "delivery_ratio_delta": abs(ns3_delivery - omnetpp_delivery),
            "deadline_miss_ratio_delta": abs(
                left["deadline_miss_ratio"] - right["deadline_miss_ratio"]
            ),
            "p99_latency_ratio": p99_high / p99_low,
            "normalized_utility_delta": abs(ns3_utility - omnetpp_utility)
            / utility_scale,
        }
        passed = bool(
            metrics["tx_equal"]
            and metrics["delivery_ratio_delta"]
            <= thresholds["delivery_ratio_delta"]
            and metrics["deadline_miss_ratio_delta"]
            <= thresholds["deadline_miss_ratio_delta"]
            and metrics["p99_latency_ratio"] <= thresholds["p99_latency_ratio"]
            and metrics["normalized_utility_delta"]
            <= thresholds["normalized_utility_delta"]
        )
        comparisons.append(
            {
                "policy": policy,
                "status": "ok" if passed else "failed",
                "ns3": left,
                "omnetpp": right,
                **metrics,
            }
        )
    return comparisons


def _aggregate_comparisons_wifi(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from collections import defaultdict
    import math

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for comparison in row.get("comparisons", []):
            grouped[comparison["policy"]].append(comparison)
    aggregates: list[dict[str, Any]] = []
    for policy, samples in sorted(grouped.items()):
        aggregates.append(
            {
                "policy": policy,
                "samples": len(samples),
                "passed": sum(item["status"] == "ok" for item in samples),
                "max_delivery_ratio_delta": max(
                    float(item.get("delivery_ratio_delta", math.inf)) for item in samples
                ),
                "max_deadline_miss_ratio_delta": max(
                    float(item.get("deadline_miss_ratio_delta", math.inf))
                    for item in samples
                ),
                "max_p99_latency_ratio": max(
                    float(item.get("p99_latency_ratio", math.inf)) for item in samples
                ),
                "max_normalized_utility_delta": max(
                    float(item.get("normalized_utility_delta", math.inf))
                    for item in samples
                ),
            }
        )
    return aggregates


def compile_ns3_wifi(image: str, build_dir: Path) -> subprocess.CompletedProcess[str]:
    binary = _container_path(build_dir / "fleetqox_ns3_wifi_replay")
    command = (
        "g++ -std=c++17 external/ns3/fleetqox_trace_replay.cc "
        f"-o {shlex.quote(binary)} "
        "$(pkg-config --cflags --libs ns3-applications ns3-bridge ns3-core ns3-csma "
        "ns3-internet ns3-network ns3-point-to-point ns3-wifi ns3-mobility) && "
        "printf 'FLEETQOX_NS3_VERSION=' && pkg-config --modversion ns3-core"
    )
    return docker_run(image, command, timeout=900)


def compile_omnetpp_wifi(image: str, build_dir: Path) -> subprocess.CompletedProcess[str]:
    container_dir = _container_path(build_dir)
    source_dir = "/work/external/omnetpp"
    command = " && ".join(
        [
            f"rm -rf {shlex.quote(container_dir)}",
            f"mkdir -p {shlex.quote(container_dir)}",
            f"mkdir -p {shlex.quote(container_dir)}/fleetqox/omnetpp",
            (
                "cp "
                f"{source_dir}/TraceDrivenUdpApp.h "
                f"{source_dir}/TraceDrivenUdpApp.cc "
                f"{source_dir}/omnetpp.ini "
                f"{shlex.quote(container_dir)}/"
            ),
            (
                "cp "
                f"{source_dir}/FleetQoxTraceReplay.ned "
                f"{source_dir}/FleetQoxWifiReplay.ned "
                f"{source_dir}/TraceDrivenUdpApp.ned "
                f"{shlex.quote(container_dir)}/fleetqox/omnetpp/"
            ),
            f"cd {shlex.quote(container_dir)}",
            (
                "opp_makemake --make-so -f --deep -o FleetQoxReplay "
                "-I/opt/inet/src -L/opt/inet/src -lINET"
            ),
            "make MODE=release -j\"$(nproc)\"",
            "printf 'FLEETQOX_OMNETPP_VERSION='",
            "cat /opt/omnetpp/Version",
            "printf 'FLEETQOX_INET_VERSION='",
            "inet_version",
        ]
    )
    return docker_run(image, command, timeout=900)


def run_ns3_case(
    *,
    image: str,
    binary: Path,
    trace: Path,
    scenario: dict[str, Any],
    seed: int,
) -> subprocess.CompletedProcess[str]:
    command = (
        f"{shlex.quote(_container_path(binary))} "
        f"--trace={shlex.quote(_container_path(trace))} --topology=wifi "
        f"--wifiMode={scenario['wifi_mode']} "
        f"--mobilitySpeed={scenario['mobility_speed']} "
        f"--stationSpacing={scenario['station_spacing']} "
        f"--warmupMs={WIFI_WARMUP_MS} --seed={seed} --run={seed}"
    )
    return docker_run(image, command, timeout=600)


_WIFI_MODE_BPS = {
    "ErpOfdmRate54Mbps": 54_000_000,
    "ErpOfdmRate24Mbps": 24_000_000,
    "ErpOfdmRate6Mbps": 6_000_000,
}


def run_omnetpp_case(
    *,
    image: str,
    build_dir: Path,
    trace: Path,
    robots: int,
    scenario: dict[str, Any],
    seed: int,
) -> subprocess.CompletedProcess[str]:
    drain_ms = 10_000.0
    sim_limit = _sim_time_limit_seconds(trace, WIFI_WARMUP_MS, drain_ms)
    exclusions = (
        '"$(if test -f /opt/inet/.nedexclusions; '
        "then tr '\\n' ';' </opt/inet/.nedexclusions; fi)\""
    )
    trace_flag = shlex.quote('--*.traceFile="' + _container_path(trace) + '"')
    bitrate_bps = _WIFI_MODE_BPS[scenario["wifi_mode"]]
    command = (
        f"cd {shlex.quote(_container_path(build_dir))} && "
        "opp_run_release -u Cmdenv "
        "-l /opt/inet/src/INET -l ./FleetQoxReplay "
        f"-n .:/opt/inet/src -x {exclusions} "
        "-f omnetpp.ini -c MatchedWifi "
        f"--*.numRobots={robots} "
        f"{trace_flag} "
        f"--*.wlanBitrate={bitrate_bps}bps "
        f"--*.stationSpacing={scenario['station_spacing']}m "
        f"--*.mobilitySpeed={scenario['mobility_speed']}mps "
        f"--*.startOffset={WIFI_WARMUP_MS / 1000.0:.12g}s "
        f"--sim-time-limit={sim_limit:.12g}s --seed-set={seed}"
    )
    return docker_run(image, command, timeout=600)


def run_parity_matrix(
    *,
    omnetpp_image: str,
    ns3_image: str,
    output_dir: Path,
    robot_counts: list[int],
    seeds: list[int],
    seconds: int,
    thresholds: dict[str, float],
    build_image: bool,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    build_dir = output_dir / ".omnetpp_build"
    ns3_build_dir = output_dir / ".ns3_build"
    ns3_build_dir.mkdir(parents=True, exist_ok=True)

    image_build: dict[str, Any] = {"status": "skipped"}
    if build_image:
        built = build_omnetpp_image(omnetpp_image)
        image_build = {
            "status": "ok" if built.returncode == 0 else "failed",
            "returncode": built.returncode,
            "stdout": built.stdout,
            "stderr": built.stderr,
        }
        if built.returncode != 0:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "stage": "omnetpp_image_build",
                "image_build": image_build,
            }

    ns3_compile = compile_ns3_wifi(ns3_image, ns3_build_dir)
    omnetpp_compile = compile_omnetpp_wifi(omnetpp_image, build_dir)
    compile_evidence = {
        "ns3": {
            "status": "ok" if ns3_compile.returncode == 0 else "failed",
            "returncode": ns3_compile.returncode,
            "stdout": ns3_compile.stdout,
            "stderr": ns3_compile.stderr,
        },
        "omnetpp": {
            "status": "ok" if omnetpp_compile.returncode == 0 else "failed",
            "returncode": omnetpp_compile.returncode,
            "stdout": omnetpp_compile.stdout,
            "stderr": omnetpp_compile.stderr,
        },
    }
    if ns3_compile.returncode != 0 or omnetpp_compile.returncode != 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "stage": "simulator_compile",
            "image_build": image_build,
            "compile": compile_evidence,
        }

    rows: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    ns3_binary = ns3_build_dir / "fleetqox_ns3_wifi_replay"
    for robots in robot_counts:
        for seed in seeds:
            trace_path = output_dir / f"trace_{robots}robot_seed{seed}.csv"
            events = generate_trace_events(
                scenario=f"omnetpp_ns3_wifi_parity_{robots}robot",
                robots=robots,
                seconds=seconds,
                seed=seed,
                capacity_bytes_per_second=max(200_000, robots * 6_000),
                policies=POLICIES,
                include_non_sent=False,
            )
            packet_rows = write_simulator_csv(events, trace_path)
            traces.append(
                {
                    "robots": robots,
                    "seed": seed,
                    "trace": str(trace_path.relative_to(ROOT)),
                    "packet_rows": packet_rows,
                }
            )
            for scenario_name, scenario in SCENARIOS.items():
                ns3_run = run_ns3_case(
                    image=ns3_image,
                    binary=ns3_binary,
                    trace=trace_path,
                    scenario=scenario,
                    seed=seed,
                )
                omnetpp_run = run_omnetpp_case(
                    image=omnetpp_image,
                    build_dir=build_dir,
                    trace=trace_path,
                    robots=robots,
                    scenario=scenario,
                    seed=seed,
                )
                ns3_policies = parse_csv_summary(ns3_run.stdout)
                omnetpp_policies = parse_csv_summary(omnetpp_run.stdout)
                comparisons = compare_policy_rows_wifi(
                    ns3_policies, omnetpp_policies, thresholds=thresholds
                )
                runtime_ok = bool(
                    ns3_run.returncode == 0
                    and omnetpp_run.returncode == 0
                    and _valid_policy_rows(ns3_policies)
                    and _valid_policy_rows(omnetpp_policies)
                )
                parity_ok = runtime_ok and all(
                    item["status"] == "ok" for item in comparisons
                )
                rows.append(
                    {
                        "robots": robots,
                        "seed": seed,
                        "scenario": scenario_name,
                        "scenario_config": scenario,
                        "status": "ok" if parity_ok else "failed",
                        "runtime_status": "ok" if runtime_ok else "failed",
                        "ns3": {
                            "returncode": ns3_run.returncode,
                            "policies": ns3_policies,
                            "stdout": ns3_run.stdout,
                            "stderr": ns3_run.stderr,
                        },
                        "omnetpp": {
                            "returncode": omnetpp_run.returncode,
                            "policies": omnetpp_policies,
                            "stdout": omnetpp_run.stdout,
                            "stderr": omnetpp_run.stderr,
                        },
                        "comparisons": comparisons,
                    }
                )

    expected_rows = len(robot_counts) * len(seeds) * len(SCENARIOS)
    runtime_ok = len(rows) == expected_rows and all(
        row["runtime_status"] == "ok" for row in rows
    )
    parity_ok = runtime_ok and all(row["status"] == "ok" for row in rows)
    ns3_version = "unknown"
    for line in ns3_compile.stdout.splitlines():
        if line.startswith("FLEETQOX_NS3_VERSION="):
            ns3_version = line.split("=", 1)[1].strip()
            break
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if parity_ok else "partial" if runtime_ok else "failed",
        "simulators": {
            "ns3": ns3_version,
            "omnetpp": OMNETPP_VERSION,
            "inet": INET_VERSION,
        },
        "images": {"ns3": ns3_image, "omnetpp": omnetpp_image},
        "image_build": image_build,
        "compile": compile_evidence,
        "topology_scope": "matched_single_ap_802_11g_infrastructure_grid_mobility",
        "trace_seed_scenario_identical": True,
        "robot_counts": robot_counts,
        "seeds": seeds,
        "seconds": seconds,
        "warmup_ms": WIFI_WARMUP_MS,
        "policies": list(POLICIES),
        "scenarios": SCENARIOS,
        "thresholds": thresholds,
        "traces": traces,
        "total_packet_rows": sum(int(trace["packet_rows"]) for trace in traces),
        "rows": rows,
        "aggregates": _aggregate_comparisons_wifi(rows),
        "runtime_case_count": len(rows),
        "runtime_case_pass_count": sum(row["runtime_status"] == "ok" for row in rows),
        "parity_case_pass_count": sum(row["status"] == "ok" for row in rows),
        "omnetpp_runtime_executed": runtime_ok,
        "omnetpp_wifi_runtime_claim": runtime_ok,
        "ns3_omnetpp_wifi_parity_scope": (
            "matched_trace_seed_policy_bitrate_spacing_speed_bounded_metric_parity"
        ),
        "ns3_omnetpp_wifi_parity_claim": parity_ok,
        "roaming_handoff_parity_claim": False,
        "tsn_mesh_parity_claim": False,
    }


def parse_int_list(value: str) -> list[int]:
    values = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--omnetpp-image", default=DEFAULT_OMNETPP_IMAGE)
    parser.add_argument("--ns3-image", default=DEFAULT_NS3_IMAGE)
    parser.add_argument("--skip-image-build", action="store_true")
    parser.add_argument("--robot-counts", type=parse_int_list, default=[8, 16, 32])
    parser.add_argument("--seeds", type=parse_int_list, default=[7, 13, 29])
    parser.add_argument("--seconds", type=int, default=3)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results_omnetpp/ns3_wifi_parity_matrix_v1"),
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path("results_omnetpp/omnetpp_ns3_docker_wifi_parity_v1_summary.json"),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_parity_matrix(
        omnetpp_image=args.omnetpp_image,
        ns3_image=args.ns3_image,
        output_dir=ROOT / args.output_dir,
        robot_counts=args.robot_counts,
        seeds=args.seeds,
        seconds=max(args.seconds, 1),
        thresholds=dict(DEFAULT_THRESHOLDS),
        build_image=not args.skip_image_build,
    )
    summary_path = ROOT / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} "
            f"runtime={summary.get('runtime_case_pass_count', 0)}/"
            f"{summary.get('runtime_case_count', 0)} "
            f"parity={summary.get('parity_case_pass_count', 0)}/"
            f"{summary.get('runtime_case_count', 0)}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
