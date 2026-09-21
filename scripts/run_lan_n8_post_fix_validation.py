"""LAN validation ladder, Phase 1: N=8, 3 seeds, all four middleware,
run AFTER commit a2590e6 (beacon-starvation deadlock fix +
LAN_DISCOVERY_WATCHDOG_S=45.0). See
docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN SOURCE + OFFICIAL-DOCUMENTATION
AUDIT" Phases 5-9 for the fixes this validates.

Reuses run_lan_n16_fresh_baseline_comparison.py's own MIDDLEWARE/
run_one() -- same per-middleware discovery_mode convention, same
metric computation, same production run_lan_probe() entry point (now
defaulting to the fixed behavior automatically, no override needed
here). Does not modify that script or its output tree.

MEASUREMENT ONLY: no optimization, no parameter tuning, no production
middleware code changes, no result-driven configuration changes during
this pass. Seeds (7, 13, 29) match the existing N=16 baseline's own
3-seed set -- not chosen for this pass, reused for consistency.

Counterbalanced execution order: same cyclic-rotation scheme already
used by the N=16 baseline and the 20-seed experiment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import DEFAULT_IMAGE  # noqa: E402
from scripts.run_lan_n16_fresh_baseline_comparison import MIDDLEWARE, run_one  # noqa: E402

SEEDS = [7, 13, 29]
OUTPUT_ROOT = ROOT / "results_rmw_socket" / "lan_n8_post_a2590e6_validation"
NUM_ROBOTS = 8


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for seed_idx, seed in enumerate(SEEDS):
        shift = seed_idx % len(MIDDLEWARE)
        order = MIDDLEWARE[shift:] + MIDDLEWARE[:shift]
        for name, rmw_impl, discovery_mode in order:
            print(f"=== N={NUM_ROBOTS} {name} seed={seed} ===", flush=True)
            r = run_one(
                name, rmw_impl, discovery_mode, NUM_ROBOTS, seed,
                OUTPUT_ROOT, DEFAULT_IMAGE,
            )
            readiness = "INVALID_READINESS" if r["status"] == "invalid_readiness" else (
                "READY" if r["status"] == "ok" else f"OTHER({r['status']})"
            )
            r["readiness_classification"] = readiness
            results.append(r)
            print(json.dumps({
                "middleware": r["middleware"],
                "seed": seed,
                "readiness_classification": readiness,
                "status": r["status"],
                "error": r["error"],
                "endpoint_results_complete": r["endpoint_results_complete"],
                "delivery_pct": r["delivery_pct"],
                "fresh_deadline_success_pct": r["fresh_deadline_success_pct"],
                "p50_ms": r["p50_ms"],
                "p99_ms": r["p99_ms"],
                "discovery_convergence_max_s": r["discovery_convergence_max_s"],
            }, indent=2), flush=True)

    out_path = OUTPUT_ROOT / "findings.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)

    print("\n=== READINESS SUMMARY ===", flush=True)
    for name, _, _ in MIDDLEWARE:
        cell = [r for r in results if r["middleware"] == name]
        ready = sum(1 for r in cell if r["readiness_classification"] == "READY")
        print(f"{name:10s}: {ready}/{len(cell)} READY", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
