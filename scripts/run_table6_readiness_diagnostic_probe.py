"""Table VI ("Chỉ số điều phối và hoàn thành nhiệm vụ") post-readiness
root-cause investigation -- QUESTION A ONLY (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI POST-READINESS ROOT-CAUSE
INVESTIGATION"): why do Fast DDS/CycloneDDS/Zenoh frequently fail to
become READY under Table VI's full-mesh readiness gate?

Deliberately SMALL and reproducible (2 scales x 3 seeds x 3
middleware = 18 runs), NOT a large seed matrix -- this pass exists to
gather per-run IDENTITY-level readiness data (required/observed/missing
peer names, per-peer first-seen timestamps) via the new
--discovery-diag-json instrumentation (build_discovery_diagnostic() in
fleetqox_coordination_endpoint.py), which no prior run in this project
ever captured (every previous INVALID_READINESS run left zero
artifacts). FleetRMW is NOT run here -- Question A is specifically
about the OTHER 3 middleware; FleetRMW's own readiness behavior
(9/9 valid, see table6_corrected_readiness_baseline) is not in
question.

READ-ONLY measurement pass: no coordination protocol change, no
readiness-gate change, no timeout change, no config change.
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
    ("Fast DDS", "rmw_fastrtps_cpp", "discovery_server"),
    ("CycloneDDS", "rmw_cyclonedds_cpp", "static_peers"),
    ("Zenoh", "rmw_zenoh_cpp", "default"),
]
SEEDS = [7, 13, 29]
SCALES = [4, 8]


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
    valid = result["status"] == "ok" and result["endpoint_results_complete"]
    return {
        "middleware": name,
        "rmw_implementation": rmw_impl,
        "discovery_mode": discovery_mode,
        "num_robots": num_robots,
        "seed": seed,
        "valid": valid,
        "status": result["status"],
        "error": result["error"],
        "readiness_diagnostics": result.get("readiness_diagnostics"),
        "discovery_convergence_max_s": result.get("discovery_convergence_max_s"),
        "num_endpoints": len(endpoints),
        "raw_output_dir": str(output_dir.relative_to(ROOT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "results_rmw_socket" / "table6_readiness_diagnostic",
    )
    parser.add_argument("--summary-json", type=Path, default=None)
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for num_robots in SCALES:
        for name, rmw_impl, discovery_mode in MIDDLEWARE:
            for seed in SEEDS:
                print(f"=== N={num_robots} {name} seed={seed} ===", flush=True)
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
