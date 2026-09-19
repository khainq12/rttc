"""Validation pass for the shared discovery-readiness false-ready fix
(see docs/AUDIT_ACCEPTANCE_TRACKING.md, "ZENOH FALSE-READY HARNESS FIX
AND VALIDATION").

1. Reruns Zenoh LAN N=16 across the EXACT same 20 seeds used in the
   completed paired experiment (results_rmw_socket/lan_n16_paired_20seed/),
   under the now-fixed harness. Seeds are NOT chosen based on results --
   they are the frozen list already established.
2. Runs a small predetermined regression sanity for Fast DDS,
   CycloneDDS, and FleetRMW at seeds 7, 41, 13 (not their full 20 seeds)
   to confirm the shared readiness-gate change has not regressed them.

No Zenoh configuration change. No timeout increase. No tuning.
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

from scripts.run_lan_n16_fresh_baseline_comparison import run_one  # noqa: E402
from scripts.run_ns3_docker_container_fleet_probe import DEFAULT_IMAGE  # noqa: E402

SEEDS_20 = [7, 13, 29, 41, 53, 67, 79, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151]
SANITY_SEEDS = [7, 41, 13]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "results_rmw_socket" / "zenoh_false_ready_fix_validation",
    )
    parser.add_argument("--summary-json", type=Path, default=None)
    parser.add_argument("--skip-sanity", action="store_true")
    parser.add_argument("--skip-zenoh", action="store_true")
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    report: dict[str, list[dict[str, Any]]] = {"zenoh_20_seed_rerun": [], "cross_middleware_sanity": []}

    if not args.skip_zenoh:
        for seed in SEEDS_20:
            print(f"=== ZENOH RERUN seed={seed} ===", flush=True)
            r = run_one(
                "Zenoh", "rmw_zenoh_cpp", "default", 16, seed,
                args.output_root / "zenoh_rerun", args.image,
            )
            report["zenoh_20_seed_rerun"].append(r)
            print(json.dumps({
                k: r[k] for k in (
                    "seed", "valid", "status", "delivery_pct", "invalid_reason",
                )
            }), flush=True)

    if not args.skip_sanity:
        for name, rmw_impl, discovery_mode in [
            ("Fast DDS", "rmw_fastrtps_cpp", "discovery_server"),
            ("CycloneDDS", "rmw_cyclonedds_cpp", "static_peers"),
            ("FleetRMW", "rmw_fleetqox_cpp", "default"),
        ]:
            for seed in SANITY_SEEDS:
                print(f"=== SANITY {name} seed={seed} ===", flush=True)
                r = run_one(
                    name, rmw_impl, discovery_mode, 16, seed,
                    args.output_root / "sanity", args.image,
                )
                report["cross_middleware_sanity"].append(r)
                print(json.dumps({
                    k: r[k] for k in (
                        "middleware", "seed", "valid", "status", "delivery_pct",
                    )
                }), flush=True)

    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
