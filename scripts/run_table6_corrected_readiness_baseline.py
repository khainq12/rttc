"""Table VI ("Chỉ số điều phối và hoàn thành nhiệm vụ") baseline rerun
under the corrected Table VI readiness gate (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI READINESS CORRECTNESS
FIX"). Same frozen experiment configuration as the previously accepted
Table VI design (docs/BANG_V_VI_KET_QUA.md): N=8/16/32, n=3, 4
middleware, wifi profile (run_coordination_probe()'s own default
network setup, unchanged). No seed was chosen or dropped based on
results -- 7/13/29 is this project's existing canonical 3-seed
convention (already used throughout Table IV/V and the LAN N=16
paired experiment), reused here because no seed list specific to
Table VI's original n=3 runs is checked into this repository.

Discovery timeout is frozen at its existing default (15s) -- NOT
increased to make more runs pass. Runs are classified VALID or
INVALID_READINESS; INVALID_READINESS is never converted into a
delivery/forced-entry/consensus number.

READ-ONLY measurement pass: no coordination protocol change, no REPLY
broadcast fix, no Table IV/V change, no FleetRMW production change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    endpoint_list,
    run_coordination_probe,
)

MIDDLEWARE: list[tuple[str, str, str]] = [
    ("FleetRMW", "rmw_fleetqox_cpp", "default"),
    ("Fast DDS", "rmw_fastrtps_cpp", "discovery_server"),
    ("CycloneDDS", "rmw_cyclonedds_cpp", "static_peers"),
    ("Zenoh", "rmw_zenoh_cpp", "default"),
]
SEEDS = [7, 13, 29]
SCALES = [8, 16, 32]


def run_one(
    name: str, rmw_impl: str, discovery_mode: str, num_robots: int, seed: int,
    output_root: Path, image: str,
) -> dict[str, Any]:
    output_dir = (
        output_root / f"{rmw_impl}_{discovery_mode}_n{num_robots}_seed{seed}"
    ).resolve()
    result = run_coordination_probe(
        image=image,
        output_dir=output_dir,
        num_robots=num_robots,
        seed=seed,
        rmw_implementation=rmw_impl,
        discovery_mode=discovery_mode,
    )
    endpoints = endpoint_list(num_robots)
    valid = (
        result["status"] == "ok"
        and result["endpoint_results_complete"]
    )
    return {
        "middleware": name,
        "rmw_implementation": rmw_impl,
        "discovery_mode": discovery_mode,
        "num_robots": num_robots,
        "seed": seed,
        "valid": valid,
        "status": result["status"],
        "error": result["error"],
        "endpoint_results_complete": result["endpoint_results_complete"],
        "coordination_metrics": result.get("coordination_metrics"),
        "discovery_convergence_max_s": result.get("discovery_convergence_max_s"),
        "graph_join_failures": result.get("graph_join_failures"),
        "num_endpoints": len(endpoints),
        "raw_output_dir": str(output_dir.relative_to(ROOT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "results_rmw_socket" / "table6_corrected_readiness_baseline",
    )
    parser.add_argument("--summary-json", type=Path, default=None)
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for num_robots in SCALES:
        for name, rmw_impl, discovery_mode in MIDDLEWARE:
            for seed in SEEDS:
                print(f"=== TABLE VI N={num_robots} {name} seed={seed} ===", flush=True)
                r = run_one(
                    name, rmw_impl, discovery_mode, num_robots, seed,
                    args.output_root, args.image,
                )
                results.append(r)
                print(json.dumps({
                    k: r[k] for k in ("middleware", "num_robots", "seed", "valid", "status")
                }), flush=True)

    report = {"results": results}
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
