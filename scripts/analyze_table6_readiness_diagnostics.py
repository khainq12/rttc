"""Table VI post-readiness root-cause investigation -- QUESTION A
analysis (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI
POST-READINESS ROOT-CAUSE INVESTIGATION"). Reads back
run_table6_readiness_diagnostic_probe.py's summary.json (Fast DDS/
CycloneDDS/Zenoh at N=4/N=8, 3 seeds each) and, per middleware,
classifies the readiness-failure SHAPE using only what the per-run
readiness_diagnostics (required/observed/missing PEER IDENTITIES,
per-peer first-seen timestamps) can prove:

  - NEVER-CONVERGES: every endpoint that failed shows the SAME missing
    peer(s) across all seeds/scales it failed at (a structural gap, not
    noise).
  - RANDOM: which endpoint(s)/peer(s) go missing varies run-to-run with
    no consistent pattern.
  - SYSTEMATIC: one particular endpoint (e.g. always index 0, or always
    the last-launched) is disproportionately the one missing peers.
  - ASYMMETRIC: A sees B but B does not see A, for the same pair, in
    the same run (checked directly from peer_first_seen_s / missing on
    both sides -- proves the beacon channel itself is not symmetric,
    which a single "peers_seen count" per endpoint could never show).
  - LATE: peers that WERE eventually seen but only very close to (or
    past, per peer_first_seen_s vs discovery_timeout_s) the deadline --
    suggests a slow-convergence, not a lost-forever, failure mode.

READ-ONLY analysis: does not run anything, does not change the harness.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def analyze(summary_path: Path) -> dict[str, Any]:
    report = json.loads(summary_path.read_text())
    results = report["results"]

    by_middleware: dict[str, list[dict]] = {}
    for r in results:
        by_middleware.setdefault(r["middleware"], []).append(r)

    out: dict[str, Any] = {}
    for mw, runs in by_middleware.items():
        valid_count = sum(1 for r in runs if r["valid"])
        invalid_runs = [r for r in runs if not r["valid"]]

        missing_peer_signatures: list[str] = []
        asymmetric_pairs: list[dict[str, Any]] = []
        late_arrivals: list[dict[str, Any]] = []
        endpoint_failure_counts: Counter = Counter()
        per_run_summaries = []

        for r in invalid_runs:
            diag = r.get("readiness_diagnostics") or {}
            failing_endpoints = {
                ep: d for ep, d in diag.items() if d is not None and not d.get("converged", True)
            }
            for ep in failing_endpoints:
                endpoint_failure_counts[ep] += 1
            missing_sig = sorted(
                (ep, tuple(sorted(d["missing_peers"]))) for ep, d in failing_endpoints.items()
            )
            missing_peer_signatures.append(json.dumps(missing_sig))

            for ep, d in failing_endpoints.items():
                for missing_peer in d.get("missing_peers", []):
                    peer_diag = diag.get(missing_peer)
                    if peer_diag is not None and ep in peer_diag.get("peers_seen", []):
                        asymmetric_pairs.append(
                            {
                                "seed": r["seed"],
                                "num_robots": r["num_robots"],
                                "a_missing_b": ep,
                                "b_missing_from_a": missing_peer,
                                "note": f"{missing_peer} saw {ep}, but {ep} never saw {missing_peer}",
                            }
                        )

            timeout = None
            for d in diag.values():
                if d is not None:
                    timeout = d.get("discovery_timeout_s")
                    break
            if timeout:
                for ep, d in diag.items():
                    if d is None:
                        continue
                    for peer, t in d.get("peer_first_seen_s", {}).items():
                        if t is not None and t > timeout * 0.8:
                            late_arrivals.append(
                                {
                                    "seed": r["seed"],
                                    "num_robots": r["num_robots"],
                                    "endpoint": ep,
                                    "saw_peer": peer,
                                    "first_seen_s": t,
                                    "discovery_timeout_s": timeout,
                                }
                            )

            per_run_summaries.append(
                {
                    "seed": r["seed"],
                    "num_robots": r["num_robots"],
                    "failing_endpoints": {ep: d["missing_peers"] for ep, d in failing_endpoints.items()},
                }
            )

        unique_signatures = set(missing_peer_signatures)
        if not invalid_runs:
            shape = "N/A (0 failures observed in this small sample)"
        elif len(unique_signatures) == 1:
            shape = "SYSTEMATIC/NEVER-CONVERGES (identical missing-endpoint/peer signature every failing run)"
        elif len(endpoint_failure_counts) <= 2 and max(endpoint_failure_counts.values(), default=0) >= len(invalid_runs) * 0.7:
            shape = "SYSTEMATIC (failures concentrated on a small, consistent subset of endpoints)"
        else:
            shape = "RANDOM (missing-peer signature varies run to run, no consistent endpoint/peer pattern)"

        out[mw] = {
            "runs_total": len(runs),
            "valid_count": valid_count,
            "invalid_count": len(invalid_runs),
            "failure_shape": shape,
            "endpoint_failure_counts": dict(endpoint_failure_counts),
            "asymmetric_pairs_found": asymmetric_pairs,
            "late_arrivals_near_or_past_deadline": late_arrivals,
            "per_run_summaries": per_run_summaries,
        }

    return out


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, default=None)
    args = parser.parse_args()

    result = analyze(args.summary_json)
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(text, encoding="utf-8")
