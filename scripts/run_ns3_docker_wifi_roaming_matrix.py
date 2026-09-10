"""Run measured dual-AP FleetQoX roaming transitions in native ns-3."""

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


SCHEMA_VERSION = "fleetqox.ns3_docker_wifi_roaming_matrix.v1"
SCENARIOS = {
    "balanced_handoff": {
        "wifi_mode": "ErpOfdmRate24Mbps",
        "mobility_speed": 5.0,
        "access_point_spacing": 20.0,
        "wifi_range": 12.0,
    },
    "fast_handoff": {
        "wifi_mode": "ErpOfdmRate12Mbps",
        "mobility_speed": 7.0,
        "access_point_spacing": 24.0,
        "wifi_range": 14.0,
    },
    "edge_handoff": {
        "wifi_mode": "ErpOfdmRate6Mbps",
        "mobility_speed": 4.0,
        "access_point_spacing": 18.0,
        "wifi_range": 10.0,
    },
}


def parse_roaming_metrics(stdout: str) -> dict[str, int]:
    for line in stdout.splitlines():
        cells = line.strip().split(",")
        if len(cells) == 4 and cells[0] == "roaming_metrics" and cells[1].isdigit():
            return {
                "associations": int(cells[1]),
                "disassociations": int(cells[2]),
                "handoffs": int(cells[3]),
            }
    return {}


def run_matrix(
    *,
    image: str,
    output_dir: Path,
    robot_counts: list[int],
    seeds: list[int],
    seconds: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    binary = output_dir / ".fleetqox_ns3_wifi_roaming_replay"
    compiled = docker_run(
        image,
        "g++ -std=c++17 external/ns3/fleetqox_trace_replay.cc "
        f"-o {shlex.quote(str(binary))} "
        "$(pkg-config --cflags --libs ns3-applications ns3-bridge ns3-core "
        "ns3-csma ns3-internet ns3-network ns3-point-to-point ns3-wifi ns3-mobility) && "
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
                    scenario=f"ns3_wifi_roaming_{robots}robot",
                    robots=robots,
                    seconds=seconds,
                    seed=seed,
                    capacity_bytes_per_second=max(200_000, robots * 6_000),
                    policies=POLICIES,
                    include_non_sent=False,
                )
                endpoint_count = len(
                    {
                        endpoint
                        for event in events
                        for endpoint in (event["src"], event["dst"])
                    }
                )
                packet_rows = write_simulator_csv(events, trace_path)
                traces.append(
                    {
                        "robots": robots,
                        "seed": seed,
                        "trace": str(trace_path),
                        "packet_rows": packet_rows,
                        "endpoint_count": endpoint_count,
                    }
                )
                for scenario_name, scenario in SCENARIOS.items():
                    completed = docker_run(
                        image,
                        f"{shlex.quote(str(binary))} "
                        f"--trace={shlex.quote(str(trace_path))} --topology=wifi_roaming "
                        f"--wifiMode={scenario['wifi_mode']} "
                        f"--mobilitySpeed={scenario['mobility_speed']} "
                        f"--accessPointSpacing={scenario['access_point_spacing']} "
                        f"--wifiRange={scenario['wifi_range']} "
                        f"--warmupMs=1000 --seed={seed} --run={seed}",
                    )
                    policy_rows = parse_csv_summary(completed.stdout)
                    metrics = parse_roaming_metrics(completed.stdout)
                    seen = {item["policy"] for item in policy_rows}
                    valid = (
                        completed.returncode == 0
                        and seen == set(POLICIES)
                        and metrics.get("handoffs", 0) >= endpoint_count
                        and metrics.get("associations", 0) >= 2 * endpoint_count
                        and metrics.get("disassociations", 0) >= endpoint_count
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
                            "endpoint_count": endpoint_count,
                            "roaming_metrics": metrics,
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
        "topology": "bridged_dual_ap_80211g",
        "association_transition_events_measured": True,
        "station_ip_continuity_across_handoff": True,
        "roaming_handoff_claim_allowed": ok,
        "general_policy_superiority_claim_allowed": False,
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
        default=Path("results_ns3/wifi_roaming_matrix_v1"),
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path("results_ns3/ns3_docker_wifi_roaming_matrix_v1_summary.json"),
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
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"status={summary['status']} rows={len(summary.get('rows', []))}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
