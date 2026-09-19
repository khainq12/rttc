"""Table VI PHASE 4 -- small ACK/NACK redundancy sweep, on top of the
Phase 1 ledger-identity fix (see docs/AUDIT_ACCEPTANCE_TRACKING.md).

Values swept: 0, 1, 2, 4, 10 (default/unset). Pilot seed set: 11, 17
(disjoint from the seeds already used extensively in the earlier,
pre-Phase-1-fix ACK/NACK causal-replication investigation: 7, 13, 29,
41, 53). Single repeat per (value, seed) -- 10 runs total. Reuses
run_table6_n8_acknack_ab_experiment.py's run_one()/summarize()
verbatim (same tcpdump capture + loss-funnel trace methodology, same
N=8, same probe lifecycle) -- no changes to that script.

Not a huge matrix: 5 values x 2 seeds x 1 repeat, chosen per the
overnight instruction to keep the pilot small and targeted. Select a
candidate only if repeated evidence across both seeds supports it; if
no value reliably improves the END outcome (crossings/forced_entry/
task_completion_s, not just wire traffic), make NO config change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_table6_n8_acknack_ab_experiment import run_one, summarize  # noqa: E402

VALUES = [("0", "0"), ("1", "1"), ("2", "2"), ("4", "4"), ("default_10", None)]
SEEDS = [11, 17]


def main() -> int:
    output_root = ROOT / "results_rmw_socket" / "table6_n8_acknack_redundancy_sweep_phase4"
    summaries: list[dict[str, Any]] = []
    for seed in SEEDS:
        for label, value in VALUES:
            run = run_one(label, value, output_root, seed=seed)
            summary = summarize(run)
            summaries.append(summary)
            print(
                json.dumps({
                    "label": label, "seed": seed, "status": summary["status"],
                    "data_delivery_all": summary["data_delivery_all"],
                    "request_sent": summary["request_sent"], "request_received": summary["request_received"],
                    "reply_sent": summary["reply_sent"], "reply_received": summary["reply_received"],
                    "traffic_composition": {
                        k: v["count"] for k, v in summary["traffic_composition"].items()
                    },
                    "duplicate_data_frames_deduped_sum": summary["duplicate_data_frames_deduped_sum"],
                    "crossings_completed": summary["crossings_completed"],
                    "any_forced_entry_by_endpoint": summary["any_forced_entry_by_endpoint"],
                    "task_completion_s": summary["task_completion_s"],
                }),
                flush=True,
            )

    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
