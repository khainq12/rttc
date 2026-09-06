"""Run matched FleetQoX traces in ns-3 and OMNeT++/INET inside Docker.

This runner is deliberately narrower than a blanket simulator-equivalence
claim. It uses the same generated packet rows, robot count, policy set, seed,
link rate, end-to-end propagation delay, packet error target, and warm-up in
both runtimes. It then checks declared bounds for delivery, deadline misses,
p99 latency, and normalized delivered utility.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
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


SCHEMA_VERSION = "fleetqox.omnetpp_ns3_docker_parity.v1"
DEFAULT_OMNETPP_IMAGE = "localhost/fleetqox/omnetpp-inet:6.4.0-4.7.0"
DEFAULT_NS3_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
OMNETPP_VERSION = "6.4.0"
INET_VERSION = "4.7.0"
POLICIES = ("fifo", "static_priority", "fleetqox_predictive_guarded")
PROFILES: dict[str, dict[str, Any]] = {
    "lan": {"data_rate": "54Mbps", "delay_ms": 2.0, "error_rate": 0.01},
    "constrained": {"data_rate": "20Mbps", "delay_ms": 30.0, "error_rate": 0.02},
    "degraded": {"data_rate": "6Mbps", "delay_ms": 15.0, "error_rate": 0.08},
}
DEFAULT_THRESHOLDS = {
    "delivery_ratio_delta": 0.05,
    "deadline_miss_ratio_delta": 0.10,
    "p99_latency_delta_ms": 5.0,
    "normalized_utility_delta": 0.10,
}


def docker_run(
    image: str,
    command: str,
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "-v",
            f"{ROOT}:/work",
            "-w",
            "/work",
            image,
            "-lc",
            command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )


def build_omnetpp_image(image: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "build",
            "--progress=plain",
            "-t",
            image,
            "external/omnetpp",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _container_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"path must be inside repository root: {resolved}") from exc
    return str(Path("/work") / relative)


def compile_ns3(image: str, build_dir: Path) -> subprocess.CompletedProcess[str]:
    binary = _container_path(build_dir / "fleetqox_ns3_replay")
    command = (
        "g++ -std=c++17 external/ns3/fleetqox_trace_replay.cc "
        f"-o {shlex.quote(binary)} "
        "$(pkg-config --cflags --libs ns3-applications ns3-bridge ns3-core ns3-csma "
        "ns3-internet ns3-network ns3-wifi ns3-mobility ns3-point-to-point) && "
        "printf 'FLEETQOX_NS3_VERSION=' && pkg-config --modversion ns3-core"
    )
    return docker_run(image, command, timeout=900)


def compile_omnetpp(image: str, build_dir: Path) -> subprocess.CompletedProcess[str]:
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


def _data_rate_bps(value: str) -> int:
    suffixes = {"kbps": 1_000, "mbps": 1_000_000, "gbps": 1_000_000_000}
    lowered = value.strip().lower()
    for suffix, scale in suffixes.items():
        if lowered.endswith(suffix):
            return int(float(lowered[: -len(suffix)]) * scale)
    return int(lowered)


def _sim_time_limit_seconds(trace_path: Path, warmup_ms: float, drain_ms: float) -> float:
    import csv

    maximum = 0.0
    with trace_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            maximum = max(maximum, float(row["timestamp_ms"]))
    return (maximum + warmup_ms + drain_ms) / 1000.0


def run_ns3_case(
    *,
    image: str,
    binary: Path,
    trace: Path,
    profile: dict[str, Any],
    seed: int,
    warmup_ms: float,
) -> subprocess.CompletedProcess[str]:
    target_per = float(profile["error_rate"])
    per_link = 1.0 - math.sqrt(1.0 - target_per)
    per_link_delay_ms = float(profile["delay_ms"]) / 2.0
    command = (
        f"{shlex.quote(_container_path(binary))} "
        f"--trace={shlex.quote(_container_path(trace))} "
        f"--dataRate={shlex.quote(str(profile['data_rate']))} "
        f"--delay={per_link_delay_ms:.12g}ms "
        f"--errorRate={per_link:.12g} "
        f"--warmupMs={warmup_ms} "
        f"--topology=p2p_star --seed={seed} --run={seed}"
    )
    return docker_run(image, command, timeout=600)


def run_omnetpp_case(
    *,
    image: str,
    build_dir: Path,
    trace: Path,
    robots: int,
    profile: dict[str, Any],
    seed: int,
    warmup_ms: float,
) -> subprocess.CompletedProcess[str]:
    target_per = float(profile["error_rate"])
    per_link = 1.0 - math.sqrt(1.0 - target_per)
    per_link_delay_seconds = float(profile["delay_ms"]) / 2000.0
    # The ns-3 replay stops 10 seconds after the final trace timestamp. Match
    # that drain window so delayed/queued packets have the same observation
    # horizon in both simulators.
    drain_ms = 10_000.0
    sim_limit = _sim_time_limit_seconds(trace, warmup_ms, drain_ms)
    exclusions = (
        '"$(if test -f /opt/inet/.nedexclusions; '
        "then tr '\\n' ';' </opt/inet/.nedexclusions; fi)\""
    )
    trace_flag = shlex.quote('--*.traceFile="' + _container_path(trace) + '"')
    command = (
        f"cd {shlex.quote(_container_path(build_dir))} && "
        "opp_run_release -u Cmdenv "
        "-l /opt/inet/src/INET -l ./FleetQoxReplay "
        f"-n .:/opt/inet/src -x {exclusions} "
        "-f omnetpp.ini -c MatchedP2p "
        f"--*.numRobots={robots} "
        f"{trace_flag} "
        f"--*.linkDataRate={_data_rate_bps(str(profile['data_rate']))}bps "
        f"--*.linkDelay={per_link_delay_seconds:.12g}s "
        f"--*.linkPacketErrorRate={per_link:.12g} "
        f"--*.startOffset={warmup_ms / 1000.0:.12g}s "
        f"--sim-time-limit={sim_limit:.12g}s --seed-set={seed}"
    )
    return docker_run(image, command, timeout=600)


def _valid_policy_rows(rows: list[dict[str, Any]]) -> bool:
    return (
        {row["policy"] for row in rows} == set(POLICIES)
        and all(
            row["tx"] > 0
            and 0 <= row["rx"] <= row["tx"]
            and 0.0 <= row["deadline_miss_ratio"] <= 1.0
            and row["p50_ms"] >= 0.0
            and row["p99_ms"] >= row["p50_ms"]
            for row in rows
        )
    )


def compare_policy_rows(
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
        metrics = {
            "tx_equal": left["tx"] == right["tx"],
            "delivery_ratio_delta": abs(ns3_delivery - omnetpp_delivery),
            "deadline_miss_ratio_delta": abs(
                left["deadline_miss_ratio"] - right["deadline_miss_ratio"]
            ),
            "p99_latency_delta_ms": abs(left["p99_ms"] - right["p99_ms"]),
            "normalized_utility_delta": abs(ns3_utility - omnetpp_utility)
            / utility_scale,
        }
        passed = bool(
            metrics["tx_equal"]
            and metrics["delivery_ratio_delta"]
            <= thresholds["delivery_ratio_delta"]
            and metrics["deadline_miss_ratio_delta"]
            <= thresholds["deadline_miss_ratio_delta"]
            and metrics["p99_latency_delta_ms"]
            <= thresholds["p99_latency_delta_ms"]
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


def _aggregate_comparisons(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                "max_p99_latency_delta_ms": max(
                    float(item.get("p99_latency_delta_ms", math.inf)) for item in samples
                ),
                "max_normalized_utility_delta": max(
                    float(item.get("normalized_utility_delta", math.inf))
                    for item in samples
                ),
            }
        )
    return aggregates


def run_parity_matrix(
    *,
    omnetpp_image: str,
    ns3_image: str,
    output_dir: Path,
    robot_counts: list[int],
    seeds: list[int],
    seconds: int,
    warmup_ms: float,
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

    ns3_compile = compile_ns3(ns3_image, ns3_build_dir)
    omnetpp_compile = compile_omnetpp(omnetpp_image, build_dir)
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
    ns3_binary = ns3_build_dir / "fleetqox_ns3_replay"
    for robots in robot_counts:
        for seed in seeds:
            trace_path = output_dir / f"trace_{robots}robot_seed{seed}.csv"
            events = generate_trace_events(
                scenario=f"omnetpp_ns3_parity_{robots}robot",
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
            for profile_name, profile in PROFILES.items():
                ns3_run = run_ns3_case(
                    image=ns3_image,
                    binary=ns3_binary,
                    trace=trace_path,
                    profile=profile,
                    seed=seed,
                    warmup_ms=warmup_ms,
                )
                omnetpp_run = run_omnetpp_case(
                    image=omnetpp_image,
                    build_dir=build_dir,
                    trace=trace_path,
                    robots=robots,
                    profile=profile,
                    seed=seed,
                    warmup_ms=warmup_ms,
                )
                ns3_policies = parse_csv_summary(ns3_run.stdout)
                omnetpp_policies = parse_csv_summary(omnetpp_run.stdout)
                comparisons = compare_policy_rows(
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
                        "profile": profile_name,
                        "profile_config": profile,
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

    expected_rows = len(robot_counts) * len(seeds) * len(PROFILES)
    runtime_ok = len(rows) == expected_rows and all(
        row["runtime_status"] == "ok" for row in rows
    )
    parity_ok = runtime_ok and all(row["status"] == "ok" for row in rows)
    comparison_samples = [
        comparison
        for row in rows
        for comparison in row.get("comparisons", [])
        if "delivery_ratio_delta" in comparison
    ]
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
        "topology_scope": "matched_routed_point_to_point_star_two_access_links",
        "trace_seed_profile_identical": True,
        "robot_counts": robot_counts,
        "seeds": seeds,
        "seconds": seconds,
        "warmup_ms": warmup_ms,
        "policies": list(POLICIES),
        "profiles": PROFILES,
        "thresholds": thresholds,
        "traces": traces,
        "total_packet_rows": sum(int(trace["packet_rows"]) for trace in traces),
        "rows": rows,
        "aggregates": _aggregate_comparisons(rows),
        "runtime_case_count": len(rows),
        "runtime_case_pass_count": sum(
            row["runtime_status"] == "ok" for row in rows
        ),
        "parity_case_pass_count": sum(row["status"] == "ok" for row in rows),
        "max_delivery_ratio_delta": max(
            (float(item["delivery_ratio_delta"]) for item in comparison_samples),
            default=None,
        ),
        "max_deadline_miss_ratio_delta": max(
            (float(item["deadline_miss_ratio_delta"]) for item in comparison_samples),
            default=None,
        ),
        "max_p99_latency_delta_ms": max(
            (float(item["p99_latency_delta_ms"]) for item in comparison_samples),
            default=None,
        ),
        "max_normalized_utility_delta": max(
            (float(item["normalized_utility_delta"]) for item in comparison_samples),
            default=None,
        ),
        "omnetpp_runtime_executed": runtime_ok,
        "omnetpp_inet_runtime_claim": runtime_ok,
        "omnetpp_parity_claim": parity_ok,
        "ns3_omnetpp_parity_scope": (
            "matched_trace_seed_policy_link_profile_bounded_metric_parity"
        ),
        "ns3_omnetpp_parity_claim": parity_ok,
        "full_tsn_mesh_parity_claim": False,
        "high_fidelity_wireless_parity_claim": False,
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
    parser.add_argument("--warmup-ms", type=float, default=100.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results_omnetpp/ns3_parity_matrix_v1"),
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path("results_omnetpp/omnetpp_ns3_docker_parity_v1_summary.json"),
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
        warmup_ms=max(args.warmup_ms, 0.0),
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
