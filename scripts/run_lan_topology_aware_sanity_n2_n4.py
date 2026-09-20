"""Phase 4 live sanity for the LAN topology-aware readiness fix (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN READINESS GATE: TOPOLOGY-AWARE
FIX"). Reuses run_lan_n16_fresh_baseline_comparison.py's own run_one()/
MIDDLEWARE (same per-middleware discovery_mode convention, same metric
computation) -- this script adds nothing new except running N=2 AND
N=4 (that script's own --sanity-only only covers N=2) for all four
middlewares, and reporting the readiness classification (READY vs
INVALID_READINESS) explicitly for each, since that is exactly what this
phase needs to verify: the gate must accept a valid star and must not
silently convert a genuine readiness failure into a false "ready".

Does NOT modify run_lan_n16_fresh_baseline_comparison.py or any other
existing script -- run_lan_probe() itself now always computes and
applies the topology-aware required-peer set (see run_lan_probe()'s own
required_peer_ids_by_endpoint line), so every caller of run_lan_probe(),
including the frozen historical scripts, picks this up automatically
with no further change needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import DEFAULT_IMAGE  # noqa: E402
from scripts.run_lan_n16_fresh_baseline_comparison import MIDDLEWARE, run_one  # noqa: E402

OUTPUT_ROOT = ROOT / "results_rmw_socket" / "lan_topology_aware_sanity_n2_n4"


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for num_robots in (2, 4):
        for name, rmw_impl, discovery_mode in MIDDLEWARE:
            print(f"=== SANITY N={num_robots} {name} ===", flush=True)
            r = run_one(
                name, rmw_impl, discovery_mode, num_robots, 13,
                OUTPUT_ROOT, DEFAULT_IMAGE,
            )
            readiness = "INVALID_READINESS" if r["status"] == "invalid_readiness" else (
                "READY" if r["status"] == "ok" else f"OTHER({r['status']})"
            )
            r["readiness_classification"] = readiness
            results.append(r)
            print(json.dumps({
                "num_robots": num_robots,
                "middleware": r["middleware"],
                "readiness_classification": readiness,
                "valid": r["valid"],
                "status": r["status"],
                "error": r["error"],
                "delivery_pct": r["delivery_pct"],
                "endpoint_results_complete": r["endpoint_results_complete"],
            }, indent=2), flush=True)

    out_path = OUTPUT_ROOT / "findings.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)

    print("\n=== SUMMARY ===", flush=True)
    for r in results:
        print(
            f"N={r['num_robots']} "
            f"{r['middleware']:10s} readiness={r['readiness_classification']:18s} "
            f"delivery_pct={r['delivery_pct']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
