"""Run FleetQoX traces over an ns-3 802.11g infrastructure/mobility model."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import shlex
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleetqox.trace import generate_trace_events, write_simulator_csv
from scripts.run_ns3_docker_fleet_matrix import (
    DEFAULT_IMAGE,
    POLICIES,
    docker_run,
    parse_csv_summary,
    parse_int_list,
)


SCHEMA_VERSION = "fleetqox.ns3_docker_wifi_mobility_matrix.v1"
SCENARIOS = {
    "stationary_near": {
        "wifi_mode": "ErpOfdmRate54Mbps",
        "mobility_speed": 0.0,
        "station_spacing": 2.0,
    },
    "mobile_moderate": {
        "wifi_mode": "ErpOfdmRate24Mbps",
        "mobility_speed": 0.5,
        "station_spacing": 3.0,
    },
    "mobile_edge": {
        "wifi_mode": "ErpOfdmRate6Mbps",
        "mobility_speed": 1.5,
        "station_spacing": 5.0,
    },
}


def run_matrix(
    *,
    image: str,
    output_dir: Path,
    robot_counts: list[int],
    seeds: list[int],
    seconds: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    binary = output_dir / ".fleetqox_ns3_wifi_replay"
    compiled = docker_run(
        image,
        "g++ -std=c++17 external/ns3/fleetqox_trace_replay.cc "
        f"-o {shlex.quote(str(binary))} "
        "$(pkg-config --cflags --libs ns3-applications ns3-bridge ns3-core ns3-csma "
        "ns3-internet ns3-network ns3-point-to-point ns3-wifi ns3-mobility) && "
        "pkg-config --modversion ns3-core",
    )
    if compiled.returncode != 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "stage": "compile",
            "stdout": compiled.stdout,
            "stderr": compiled.stderr,
        }
    rows: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    try:
        for robots in robot_counts:
            for seed in seeds:
                trace_path = output_dir / f"trace_{robots}robot_seed{seed}.csv"
                events = generate_trace_events(
                    scenario=f"ns3_wifi_{robots}robot",
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
                        "trace": str(trace_path),
                        "packet_rows": packet_rows,
                    }
                )
                for scenario_name, scenario in SCENARIOS.items():
                    completed = docker_run(
                        image,
                        f"{shlex.quote(str(binary))} "
                        f"--trace={shlex.quote(str(trace_path))} --topology=wifi "
                        f"--wifiMode={scenario['wifi_mode']} "
                        f"--mobilitySpeed={scenario['mobility_speed']} "
                        f"--stationSpacing={scenario['station_spacing']} "
                        f"--warmupMs=1000 --seed={seed} --run={seed}",
                    )
                    policy_rows = parse_csv_summary(completed.stdout)
                    seen = {item["policy"] for item in policy_rows}
                    valid = (
                        completed.returncode == 0
                        and seen == set(POLICIES)
                        and all(
                            item["tx"] > 0
                            and 0 < item["rx"] <= item["tx"]
                            and 0.0 <= item["deadline_miss_ratio"] <= 1.0
                            for item in policy_rows
                        )
                    )
                    rows.append(
                        {
                            "robots": robots,
                            "seed": seed,
                            "scenario": scenario_name,
                            "scenario_config": scenario,
                            "status": "ok" if valid else "failed",
                            "returncode": completed.returncode,
                            "policies": policy_rows,
                            "stdout": completed.stdout,
                            "stderr": completed.stderr,
                        }
                    )
    finally:
        binary.unlink(missing_ok=True)

    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for policy in row["policies"]:
            grouped[(row["robots"], row["scenario"], policy["policy"])].append(policy)
    aggregates = []
    for (robots, scenario, policy), samples in sorted(grouped.items()):
        total_tx = sum(item["tx"] for item in samples)
        total_rx = sum(item["rx"] for item in samples)
        aggregates.append(
            {
                "robots": robots,
                "scenario": scenario,
                "policy": policy,
                "repetitions": len(samples),
                "delivery_ratio": total_rx / total_tx if total_tx else 0.0,
                "mean_deadline_miss_ratio": sum(
                    item["deadline_miss_ratio"] for item in samples
                ) / len(samples),
                "mean_p99_ms": sum(item["p99_ms"] for item in samples) / len(samples),
                "mean_utility": sum(item["utility"] for item in samples) / len(samples),
            }
        )
    ok = len(rows) == len(robot_counts) * len(seeds) * len(SCENARIOS) and all(
        row["status"] == "ok" for row in rows
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "simulator": "ns-3",
        "ns3_version": compiled.stdout.strip().splitlines()[-1],
        "topology": "single_ap_80211g_infrastructure",
        "wifi_model_claim_allowed": True,
        "mobility_model_claim_allowed": True,
        "roaming_handoff_claim_allowed": False,
        "robot_counts": robot_counts,
        "seeds": seeds,
        "seconds": seconds,
        "policies": list(POLICIES),
        "scenarios": SCENARIOS,
        "traces": traces,
        "rows": rows,
        "aggregates": aggregates,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--robot-counts", type=parse_int_list, default=[8, 16, 32])
    parser.add_argument("--seeds", type=parse_int_list, default=[7, 13, 29])
    parser.add_argument("--seconds", type=int, default=3)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results_ns3/wifi_mobility_matrix_v1"),
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path("results_ns3/ns3_docker_wifi_mobility_matrix_v1_summary.json"),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_matrix(
        image=args.image,
        output_dir=args.output_dir,
        robot_counts=args.robot_counts,
        seeds=args.seeds,
        seconds=max(args.seconds, 1),
    )
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"status={summary['status']} rows={len(summary.get('rows', []))}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
