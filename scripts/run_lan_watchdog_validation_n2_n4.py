"""Phase 9 validation ladder, step 1: N=2 and N=4 repeated launches for
all four middlewares under the NEW evidence-derived LAN setup watchdog
(LAN_DISCOVERY_WATCHDOG_S=45.0, see docs/AUDIT_ACCEPTANCE_TRACKING.md,
"LAN SOURCE + OFFICIAL-DOCUMENTATION AUDIT" Phase 8-9). Reuses
run_lan_n16_fresh_baseline_comparison.py's own MIDDLEWARE/run_one() --
same per-middleware discovery_mode convention, same metric computation,
same production run_lan_probe() entry point (now defaulting to the new
watchdog automatically, no explicit override needed here) -- so this is
a genuine production-path validation, not a diagnostic-only repro.

Same seed (13) held fixed across every repeat within a given
(num_robots, middleware) cell -- isolates pure launch-timing/discovery-
convergence variance (does this configuration converge EVERY time?)
from trace-content variance (a different seed changes what's sent).
REPEATS reduced from the requested 10 to 5 for wall-clock budget, noted
explicitly in the report -- consistent with prior investigation phases
in this same audit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import DEFAULT_IMAGE  # noqa: E402
from scripts.run_lan_n16_fresh_baseline_comparison import MIDDLEWARE, run_one  # noqa: E402

OUTPUT_ROOT = ROOT / "results_rmw_socket" / "lan_watchdog_validation_n2_n4"
SEED = 13
REPEATS = 5


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for num_robots in (2, 4):
        for name, rmw_impl, discovery_mode in MIDDLEWARE:
            for rep in range(1, REPEATS + 1):
                print(f"=== N={num_robots} {name} rep {rep}/{REPEATS} ===", flush=True)
                rep_root = OUTPUT_ROOT / f"rep{rep}"
                r = run_one(
                    name, rmw_impl, discovery_mode, num_robots, SEED,
                    rep_root, DEFAULT_IMAGE,
                )
                readiness = "INVALID_READINESS" if r["status"] == "invalid_readiness" else (
                    "READY" if r["status"] == "ok" else f"OTHER({r['status']})"
                )
                r["readiness_classification"] = readiness
                r["rep"] = rep
                results.append(r)
                print(json.dumps({
                    "num_robots": num_robots,
                    "middleware": r["middleware"],
                    "rep": rep,
                    "readiness_classification": readiness,
                    "status": r["status"],
                    "error": r["error"],
                    "delivery_pct": r["delivery_pct"],
                }, indent=2), flush=True)

    out_path = OUTPUT_ROOT / "findings.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)

    print("\n=== SUMMARY (ready / total) ===", flush=True)
    for num_robots in (2, 4):
        for name, _, _ in MIDDLEWARE:
            cell = [r for r in results if r["num_robots"] == num_robots and r["middleware"] == name]
            ready = sum(1 for r in cell if r["readiness_classification"] == "READY")
            print(f"N={num_robots} {name:10s}: {ready}/{len(cell)} READY", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
