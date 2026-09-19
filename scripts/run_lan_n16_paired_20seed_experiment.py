"""LAN N=16 -- 20-seed PAIRED corrected-harness experiment: FleetRMW /
Fast DDS / CycloneDDS / Zenoh.

Continuation of the n=3 fresh baseline
(scripts/run_lan_n16_fresh_baseline_comparison.py,
docs/AUDIT_ACCEPTANCE_TRACKING.md "FRESH CORRECTED-HARNESS LAN N=16
BASELINE"). This script does NOT modify that baseline's driver or
overwrite its results -- it imports and reuses run_one()/per_flow
logic from it, and writes to a separate output tree.

MEASUREMENT ONLY: no optimization, no parameter tuning, no production
middleware code changes, and no result-driven configuration changes of
any kind during this pass.

Seed list (20 total, chosen BEFORE running anything in this pass,
NOT selected or pruned based on any result):
  - The 3 seeds already used in the n=3 baseline: 7, 13, 29.
  - Plus the pre-existing 10-seed list already used elsewhere in this
    project for exactly this kind of variance check (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md, "mo rong len 10 seed"): 7, 13,
    29, 41, 53, 67, 79, 89, 97, 101 -- all of which are already primes
    starting at 7.
  - The union of those two lists is {7,13,29,41,53,67,79,89,97,101},
    i.e. only 10 distinct seeds. To reach 20 total without inventing an
    arbitrary new list, this script extends the SAME pre-existing
    prime-sequence convention with the next 10 primes after 101: 103,
    107, 109, 113, 127, 131, 137, 139, 149, 151. This is a deterministic,
    mechanical extension rule (next N primes), not a choice tuned to any
    outcome -- it was fixed before any of these 17 additional seeds were
    ever run.

Counterbalanced execution order: a cyclic rotation of the 4 middleware,
shifted by one position per seed index (seed 0: Fleet,Fast,Cyclone,Zenoh;
seed 1: Fast,Cyclone,Zenoh,Fleet; seed 2: Cyclone,Zenoh,Fleet,Fast; seed
3: Zenoh,Fleet,Fast,Cyclone; then repeats) -- the same scheme already
used for the n=3 baseline and the exact pattern given in this pass's
own instructions.

Validity / retry policy: each run is validated independently
(status=ok, endpoint_results_complete=true, intended>0). An invalid run
is recorded as INVALID (with its exact reason) and the SAME
middleware+seed is retried exactly once, with no benchmark parameter
changed. Both the invalid attempt and the replacement attempt are kept
in the raw output.
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

from scripts.run_lan_n16_fresh_baseline_comparison import (  # noqa: E402
    MIDDLEWARE,
    run_one,
)
from scripts.run_ns3_docker_container_fleet_probe import DEFAULT_IMAGE  # noqa: E402

BASELINE_SEEDS = [7, 13, 29]
PRIOR_10_SEED_LIST = [7, 13, 29, 41, 53, 67, 79, 89, 97, 101]
NEXT_10_PRIMES_AFTER_101 = [103, 107, 109, 113, 127, 131, 137, 139, 149, 151]
SEEDS_20 = PRIOR_10_SEED_LIST + NEXT_10_PRIMES_AFTER_101
assert len(SEEDS_20) == 20
assert len(set(SEEDS_20)) == 20


def counterbalanced_order(seed_idx: int) -> list[tuple[str, str, str]]:
    shift = seed_idx % len(MIDDLEWARE)
    return MIDDLEWARE[shift:] + MIDDLEWARE[:shift]


def run_with_retry(
    name: str,
    rmw_impl: str,
    discovery_mode: str,
    seed: int,
    execution_index: int,
    output_root: Path,
    image: str,
) -> list[dict[str, Any]]:
    """Runs once; if invalid, retries exactly once with a distinct
    output_dir suffix (no parameter changed). Returns a list of 1 or 2
    result dicts (attempt, then replacement if the first was invalid)."""
    attempts: list[dict[str, Any]] = []
    r = run_one(name, rmw_impl, discovery_mode, 16, seed, output_root, image)
    r["execution_index"] = execution_index
    r["attempt"] = 1
    attempts.append(r)
    if not r["valid"]:
        print(
            f"    INVALID ({name} seed={seed}): {r['invalid_reason']} "
            "-- retrying once",
            flush=True,
        )
        retry_root = output_root.parent / (output_root.name + "_retry")
        r2 = run_one(name, rmw_impl, discovery_mode, 16, seed, retry_root, image)
        r2["execution_index"] = execution_index
        r2["attempt"] = 2
        attempts.append(r2)
    return attempts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "results_rmw_socket" / "lan_n16_paired_20seed",
    )
    parser.add_argument("--summary-json", type=Path, default=None)
    parser.add_argument(
        "--start-at", type=int, default=0,
        help="Resume from this execution_index (0-based) if a prior run was interrupted.",
    )
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)

    # Build the full, frozen (seed, middleware) execution plan up front --
    # printed before any run starts, so the plan itself is auditable and
    # was not adjusted after seeing any result.
    plan: list[tuple[int, int, str, str, str]] = []
    execution_index = 0
    for seed_idx, seed in enumerate(SEEDS_20):
        for name, rmw_impl, discovery_mode in counterbalanced_order(seed_idx):
            plan.append((execution_index, seed, name, rmw_impl, discovery_mode))
            execution_index += 1

    print(f"=== FROZEN EXECUTION PLAN ({len(plan)} runs) ===", flush=True)
    for idx, seed, name, _, _ in plan:
        print(f"  [{idx}] seed={seed} middleware={name}", flush=True)

    all_results: list[dict[str, Any]] = []
    for idx, seed, name, rmw_impl, discovery_mode in plan:
        if idx < args.start_at:
            continue
        print(f"=== RUN [{idx}/{len(plan)-1}] N=16 {name} seed={seed} ===", flush=True)
        output_dir = args.output_root / f"{rmw_impl}_{discovery_mode}_n16_seed{seed}"
        attempts = run_with_retry(
            name, rmw_impl, discovery_mode, seed, idx, output_dir, args.image
        )
        all_results.extend(attempts)
        final = attempts[-1]
        print(json.dumps({
            k: final[k] for k in (
                "middleware", "seed", "attempt", "valid", "status", "intended",
                "delivered", "delivery_pct", "fresh_deadline_success_pct",
            )
        }), flush=True)

    report = {
        "seeds_20": SEEDS_20,
        "seed_list_provenance": {
            "baseline_3": BASELINE_SEEDS,
            "prior_10_seed_list": PRIOR_10_SEED_LIST,
            "extension_next_10_primes_after_101": NEXT_10_PRIMES_AFTER_101,
        },
        "execution_plan": [
            {"execution_index": idx, "seed": seed, "middleware": name}
            for idx, seed, name, _, _ in plan
        ],
        "results": all_results,
    }
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
