"""Table VI DIRECTED REPLY -- N=4 sanity validation (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI DIRECTED REPLY").

Confirms the directed-REPLY change (scripts/investigate_table6_directed_reply_fanout.py
already proved the N=2 RED/GREEN fan-out claim) does not regress coordination
correctness at the known-healthy N=4 scale: the identity-collision fix
(TABLE VI ROBOT IDENTITY COLLISION -- FIXED AND VALIDATED /
TABLE VI IDENTITY FIX -- MULTI-SEED VALIDATION) already established that
N=4 seed in {7, 13, 29, 41, 53} completes 5/5 crossings with 0% forced_entry
under the OLD broadcast-REPLY default. This script re-runs seed=7 and
seed=13 under OLD and NEW (directed_reply) and requires identical
crossings_completed/forced_entry, plus confirms unintended REPLY fan-out
drops (N=4 has 4 unintended targets per reply broadcast: 5 endpoints total,
1 intended + 3 unintended per send under OLD, 0 under NEW).
"""

from __future__ import annotations

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

NUM_ROBOTS = 4
SEEDS = [7, 13]
ENDPOINTS = endpoint_list(NUM_ROBOTS)


def run_one(seed: int, label: str, directed_reply: bool) -> dict[str, Any]:
    output_dir = (
        ROOT
        / "results_rmw_socket"
        / "table6_directed_reply_n4_sanity"
        / f"fleetrmw_n{NUM_ROBOTS}_seed{seed}_{label}"
    )
    print(f"=== N=4 seed={seed} RUN {label} (directed_reply={directed_reply}) ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=NUM_ROBOTS,
        seed=seed,
        num_crossings=5,
        scenario_timeout_s=120.0,
        discovery_timeout_s=15.0,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        directed_reply=directed_reply,
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        return {"seed": seed, "label": label, "status": result["status"], "error": result["error"]}

    results_dir = output_dir / "container_results"
    intended = 0
    unintended = 0
    crossings_completed = {}
    forced_entry = {}
    for i, ep in enumerate(ENDPOINTS):
        d = json.loads((results_dir / f"result_{i}.json").read_text())
        crossings_completed[ep] = d["num_crossings_completed"]
        forced_entry[ep] = any(c["forced_entry"] for c in d["crossings"])
        for x in d["raw_received_log"]:
            if x["type"] == "reply":
                if x["to"] == ep:
                    intended += 1
                else:
                    unintended += 1

    out = {
        "seed": seed,
        "label": label,
        "status": "ok",
        "intended": intended,
        "unintended": unintended,
        "crossings_completed": crossings_completed,
        "forced_entry": forced_entry,
        "all_5_of_5": all(v == 5 for v in crossings_completed.values()),
        "any_forced_entry": any(forced_entry.values()),
    }
    print(json.dumps(out, indent=2), flush=True)
    return out


def main() -> int:
    all_results = []
    for seed in SEEDS:
        old = run_one(seed, "OLD_broadcast", directed_reply=False)
        new = run_one(seed, "NEW_directed", directed_reply=True)
        all_results.append({"seed": seed, "old": old, "new": new})

    out_path = (
        ROOT / "results_rmw_socket" / "table6_directed_reply_n4_sanity" / "sanity_findings.json"
    )
    out_path.write_text(json.dumps(all_results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)

    print("\n=== SANITY SUMMARY ===", flush=True)
    ok = True
    for entry in all_results:
        seed = entry["seed"]
        old, new = entry["old"], entry["new"]
        if old.get("status") != "ok" or new.get("status") != "ok":
            print(f"seed={seed}: RUN FAILED (old={old.get('status')}, new={new.get('status')})", flush=True)
            ok = False
            continue
        same_crossings = old["crossings_completed"] == new["crossings_completed"]
        same_forced_entry = old["forced_entry"] == new["forced_entry"]
        healthy = old["all_5_of_5"] and new["all_5_of_5"] and not old["any_forced_entry"] and not new["any_forced_entry"]
        print(
            f"seed={seed}: old_unintended={old['unintended']} new_unintended={new['unintended']} "
            f"same_crossings={same_crossings} same_forced_entry={same_forced_entry} healthy_5_of_5_no_forced_entry={healthy}",
            flush=True,
        )
        if not (same_crossings and same_forced_entry and healthy):
            ok = False
    print(f"N=4 SANITY: {'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
