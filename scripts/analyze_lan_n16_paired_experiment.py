"""Deterministic analysis for the LAN N=16 20-seed paired experiment
(scripts/run_lan_n16_paired_20seed_experiment.py raw output).

Reads the raw --summary-json produced by the experiment driver and
computes, without any hand-copied numbers:

  - per-seed paired delivery / fresh-deadline-success table
  - per-middleware delivery and fresh-deadline-success summaries
  - primary analysis: FleetRMW minus per-seed best-of-{FastDDS,
    CycloneDDS, Zenoh} fresh-deadline success, with a paired bootstrap
    over seeds (resampling whole seed-rows, keeping all 4 middleware
    results for a resampled seed together)
  - ceiling-effect check
  - superiority-gate verdict (+15pp mean AND 95% CI lower bound > 0)
  - per-flow aggregate delivery
  - FleetRMW regression check across all 20 seeds

Uses only the Python standard library (random.Random with a fixed seed
for the bootstrap -- no numpy dependency).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIDDLEWARE_ORDER = ["FleetRMW", "Fast DDS", "CycloneDDS", "Zenoh"]
BASELINE_MIDDLEWARE = ["Fast DDS", "CycloneDDS", "Zenoh"]
FLOW_CLASSES = ["control", "state", "perception", "coordination", "debug", "human_qoe"]
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_ANALYSIS_SEED = 20260919  # fixed, documented, chosen before analysis
SUPERIORITY_MIN_DELTA_PP = 15.0
CEILING_THRESHOLD_PCT = 99.5


def final_result_per_seed_middleware(
    results: list[dict[str, Any]],
) -> dict[tuple[int, str], dict[str, Any]]:
    """Keeps the LAST attempt (highest 'attempt' number) for each
    (seed, middleware) pair -- i.e. the replacement run if a retry
    happened, otherwise the sole attempt."""
    best: dict[tuple[int, str], dict[str, Any]] = {}
    for r in results:
        key = (r["seed"], r["middleware"])
        if key not in best or r["attempt"] > best[key]["attempt"]:
            best[key] = r
    return best


def build_paired_table(
    seeds: list[int], final: dict[tuple[int, str], dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for seed in seeds:
        row: dict[str, Any] = {"seed": seed}
        for mw in MIDDLEWARE_ORDER:
            r = final.get((seed, mw))
            row[mw] = r
        rows.append(row)
    return rows


def paired_bootstrap(
    diffs: list[float], resamples: int, seed: int
) -> dict[str, float]:
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(resamples):
        sample = [diffs[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.mean(sample))
    means.sort()
    lo_idx = int(0.025 * resamples)
    hi_idx = int(0.975 * resamples) - 1
    return {
        "bootstrap_mean_of_means": statistics.mean(means),
        "ci95_lower": means[lo_idx],
        "ci95_upper": means[hi_idx],
    }


def mean_median_min_max(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, default=None)
    args = parser.parse_args()

    raw = json.loads(args.summary_json.read_text(encoding="utf-8"))
    seeds: list[int] = raw["seeds_20"]
    results: list[dict[str, Any]] = raw["results"]
    final = final_result_per_seed_middleware(results)

    # Validity summary
    validity: dict[str, dict[str, Any]] = {}
    for mw in MIDDLEWARE_ORDER:
        valid_seeds = [s for s in seeds if final.get((s, mw), {}).get("valid")]
        invalid_first_attempts = [
            r for r in results
            if r["middleware"] == mw and r["attempt"] == 1 and not r["valid"]
        ]
        replacement_runs = [
            r for r in results if r["middleware"] == mw and r["attempt"] == 2
        ]
        validity[mw] = {
            "valid_runs": len(valid_seeds),
            "invalid_first_attempts": len(invalid_first_attempts),
            "invalid_reasons": [
                {"seed": r["seed"], "reason": r["invalid_reason"]}
                for r in invalid_first_attempts
            ],
            "replacement_runs": len(replacement_runs),
            "replacement_still_invalid": [
                r["seed"] for r in replacement_runs if not r["valid"]
            ],
        }

    paired_table = build_paired_table(seeds, final)

    # Delivery / fresh-deadline summaries (valid seeds only, per middleware)
    delivery_summary = {}
    fresh_deadline_summary = {}
    for mw in MIDDLEWARE_ORDER:
        deliv = [
            final[(s, mw)]["delivery_pct"] for s in seeds
            if final.get((s, mw), {}).get("valid")
        ]
        fresh = [
            final[(s, mw)]["fresh_deadline_success_pct"] for s in seeds
            if final.get((s, mw), {}).get("valid")
        ]
        delivery_summary[mw] = mean_median_min_max(deliv) if deliv else None
        fresh_deadline_summary[mw] = mean_median_min_max(fresh) if fresh else None

    # Primary analysis: per-seed best-baseline reselection, only over
    # seeds where ALL 4 middleware have a valid result.
    complete_seeds = [
        s for s in seeds
        if all(final.get((s, mw), {}).get("valid") for mw in MIDDLEWARE_ORDER)
    ]
    per_seed_diff = []
    per_seed_best_baseline = {}
    for s in complete_seeds:
        baseline_vals = {
            mw: final[(s, mw)]["fresh_deadline_success_pct"]
            for mw in BASELINE_MIDDLEWARE
        }
        best_mw = max(baseline_vals, key=baseline_vals.get)
        best_val = baseline_vals[best_mw]
        fleet_val = final[(s, "FleetRMW")]["fresh_deadline_success_pct"]
        per_seed_best_baseline[s] = {"middleware": best_mw, "value": best_val}
        per_seed_diff.append(fleet_val - best_val)

    primary_analysis = None
    superiority_gate = "SUPERIORITY GATE NOT MET"
    superiority_reasons = []
    ceiling_effect = False
    ceiling_explanation = ""
    if per_seed_diff:
        mean_diff = statistics.mean(per_seed_diff)
        median_diff = statistics.median(per_seed_diff)
        bootstrap = paired_bootstrap(
            per_seed_diff, BOOTSTRAP_RESAMPLES, BOOTSTRAP_ANALYSIS_SEED
        )
        primary_analysis = {
            "n_complete_seeds": len(complete_seeds),
            "mean_diff_pp": mean_diff,
            "median_diff_pp": median_diff,
            "per_seed_diff_pp": dict(zip(complete_seeds, per_seed_diff)),
            "per_seed_best_baseline": per_seed_best_baseline,
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "bootstrap_analysis_seed": BOOTSTRAP_ANALYSIS_SEED,
            **bootstrap,
        }
        gate1 = mean_diff >= SUPERIORITY_MIN_DELTA_PP
        gate2 = bootstrap["ci95_lower"] > 0
        if not gate1:
            superiority_reasons.append(
                f"mean diff {mean_diff:.2f}pp < required +{SUPERIORITY_MIN_DELTA_PP}pp"
            )
        if not gate2:
            superiority_reasons.append(
                f"bootstrap 95% CI lower bound {bootstrap['ci95_lower']:.2f}pp <= 0"
            )
        if gate1 and gate2:
            superiority_gate = "SUPERIORITY GATE MET"

        # Ceiling effect: are FleetRMW/FastDDS/CycloneDDS all saturated?
        non_zenoh = ["FleetRMW", "Fast DDS", "CycloneDDS"]
        saturated = all(
            (fresh_deadline_summary[mw] is not None)
            and (fresh_deadline_summary[mw]["mean"] >= CEILING_THRESHOLD_PCT)
            for mw in non_zenoh
        )
        if saturated:
            ceiling_effect = True
            ceiling_explanation = (
                "FleetRMW, Fast DDS, and CycloneDDS all have mean "
                f"fresh-deadline success >= {CEILING_THRESHOLD_PCT}% across all "
                f"{len(complete_seeds)} complete seeds -- the LAN N=16/3s/fifo "
                "workload is too easy to discriminate among these three; the "
                "primary comparison's near-zero variance among them is the "
                "expected consequence of saturation, not evidence of parity "
                "under harder conditions."
            )
        else:
            ceiling_explanation = (
                "Not all of FleetRMW/Fast DDS/CycloneDDS saturated at "
                f">={CEILING_THRESHOLD_PCT}% mean fresh-deadline success."
            )

    # Per-flow aggregate delivery (mean over valid seeds per middleware)
    per_flow_summary: dict[str, dict[str, float | None]] = {}
    for mw in MIDDLEWARE_ORDER:
        per_flow_summary[mw] = {}
        for fc in FLOW_CLASSES:
            vals = [
                final[(s, mw)]["per_flow"][fc]["delivery_pct"]
                for s in seeds
                if final.get((s, mw), {}).get("valid")
                and final[(s, mw)]["per_flow"][fc]["delivery_pct"] is not None
            ]
            per_flow_summary[mw][fc] = statistics.mean(vals) if vals else None

    # Latency summaries
    latency_summary: dict[str, dict[str, dict[str, float]]] = {}
    for mw in MIDDLEWARE_ORDER:
        p50s = [final[(s, mw)]["p50_ms"] for s in seeds if final.get((s, mw), {}).get("valid")]
        p95s = [final[(s, mw)]["p95_ms"] for s in seeds if final.get((s, mw), {}).get("valid")]
        p99s = [final[(s, mw)]["p99_ms"] for s in seeds if final.get((s, mw), {}).get("valid")]
        latency_summary[mw] = {
            "p50": mean_median_min_max(p50s) if p50s else None,
            "p95": mean_median_min_max(p95s) if p95s else None,
            "p99": mean_median_min_max(p99s) if p99s else None,
        }

    # FleetRMW regression check: is delivery 100.0 on every valid seed?
    fleet_deliveries = [
        final[(s, "FleetRMW")]["delivery_pct"] for s in seeds
        if final.get((s, "FleetRMW"), {}).get("valid")
    ]
    fleet_regression_check = {
        "all_100pct": all(d == 100.0 for d in fleet_deliveries),
        "min_delivery_pct": min(fleet_deliveries) if fleet_deliveries else None,
        "seeds_below_100pct": [
            s for s in seeds
            if final.get((s, "FleetRMW"), {}).get("valid")
            and final[(s, "FleetRMW")]["delivery_pct"] != 100.0
        ],
    }

    # Zenoh variance quantification
    zenoh_deliveries = [
        final[(s, "Zenoh")]["delivery_pct"] for s in seeds
        if final.get((s, "Zenoh"), {}).get("valid")
    ]
    zenoh_variance = {
        "n": len(zenoh_deliveries),
        "mean": statistics.mean(zenoh_deliveries) if zenoh_deliveries else None,
        "median": statistics.median(zenoh_deliveries) if zenoh_deliveries else None,
        "min": min(zenoh_deliveries) if zenoh_deliveries else None,
        "max": max(zenoh_deliveries) if zenoh_deliveries else None,
        "stdev": statistics.stdev(zenoh_deliveries) if len(zenoh_deliveries) > 1 else None,
    }

    report = {
        "seeds_20": seeds,
        "validity": validity,
        "paired_table": [
            {
                "seed": row["seed"],
                **{
                    mw: (
                        {
                            "delivery_pct": row[mw]["delivery_pct"],
                            "fresh_deadline_success_pct": row[mw]["fresh_deadline_success_pct"],
                            "valid": row[mw]["valid"],
                        }
                        if row[mw] is not None else None
                    )
                    for mw in MIDDLEWARE_ORDER
                },
            }
            for row in paired_table
        ],
        "delivery_summary": delivery_summary,
        "fresh_deadline_summary": fresh_deadline_summary,
        "primary_analysis": primary_analysis,
        "superiority_gate": superiority_gate,
        "superiority_gate_reasons_not_met": superiority_reasons,
        "ceiling_effect": ceiling_effect,
        "ceiling_explanation": ceiling_explanation,
        "per_flow_summary": per_flow_summary,
        "latency_summary": latency_summary,
        "fleet_regression_check": fleet_regression_check,
        "zenoh_variance": zenoh_variance,
    }

    output = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(output, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
