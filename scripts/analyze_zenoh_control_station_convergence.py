"""Correlates Zenoh's control_station beacon-convergence fraction against
overall delivery %, across the existing 20-seed paired experiment's raw
artifacts (results_rmw_socket/lan_n16_paired_20seed/).

Read-only analysis script -- no benchmark runs, no configuration
changes. Produced the r=0.9912 (n=20) figure cited in
docs/AUDIT_ACCEPTANCE_TRACKING.md, "ZENOH LAN N=16 VARIANCE ROOT-CAUSE
INVESTIGATION", so that number is reproducible rather than hand-copied.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEEDS_20 = [7, 13, 29, 41, 53, 67, 79, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151]


def pearson_r(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mean_x, mean_y = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / n
    sx = statistics.pstdev(xs)
    sy = statistics.pstdev(ys)
    return cov / (sx * sy)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--experiment-summary-json",
        type=Path,
        default=ROOT / "results_rmw_socket" / "lan_n16_paired_20seed" / "raw_report.json",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=ROOT / "results_rmw_socket" / "lan_n16_paired_20seed",
    )
    args = parser.parse_args()

    raw = json.loads(args.experiment_summary_json.read_text(encoding="utf-8"))
    delivery_by_seed = {
        r["seed"]: r["delivery_pct"] for r in raw["results"] if r["middleware"] == "Zenoh"
    }

    rows = []
    for seed in SEEDS_20:
        # NOTE: the 20-seed experiment driver's output_dir nesting has a
        # cosmetic double-directory bug (harmless to correctness, not
        # fixed here since this is a read-only analysis pass) -- account
        # for it when locating result_0.json (control_station).
        candidates = [
            args.results_root
            / f"rmw_zenoh_cpp_default_n16_seed{seed}"
            / f"rmw_zenoh_cpp_default_n16_seed{seed}"
            / "container_results"
            / "result_0.json",
            args.results_root
            / f"rmw_zenoh_cpp_default_n16_seed{seed}"
            / "container_results"
            / "result_0.json",
        ]
        path = next((c for c in candidates if c.is_file()), None)
        if path is None:
            print(f"seed {seed}: result_0.json not found, skipping", file=sys.stderr)
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        peers_seen = d["discovery_peers_seen"]
        expected = d["discovery_expected_peers"]
        rows.append({
            "seed": seed,
            "control_station_peers_seen": peers_seen,
            "control_station_expected_peers": expected,
            "control_station_convergence_pct": 100.0 * peers_seen / expected,
            "control_station_discovery_convergence_s": d["discovery_convergence_s"],
            "overall_delivery_pct": delivery_by_seed.get(seed),
        })

    xs = [r["control_station_convergence_pct"] for r in rows]
    ys = [r["overall_delivery_pct"] for r in rows]
    r = pearson_r(xs, ys)

    print(json.dumps({"n": len(rows), "pearson_r": r, "rows": rows}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
