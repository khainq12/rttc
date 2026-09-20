"""Table VI DIRECTED REPLY -- RED/GREEN test (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI DIRECTED REPLY").

Deterministic, minimal repro: N=2 (3 endpoints: control_station,
robot_0000, robot_0001) is the smallest topology where a REPLY from A
to B can be "overheard" by a genuinely unrelated third party C.

RUN A (old, --directed-reply NOT passed, i.e. directed_reply=False):
proves the CURRENT default behavior -- REPLY broadcast on the shared
topic -- delivers replies to unintended robots. This is the RED case:
it should show unintended_reply_deliveries > 0.

RUN B (new, directed_reply=True): same scenario, same seed, only the
REPLY wire mechanism changes (per-target topics + subscription_aware
peer policy + static subscriptions, REQUEST topic untouched). This is
the GREEN case: it should show unintended_reply_deliveries == 0.

Uses raw_received_log (already recorded per endpoint) to count, for
every REPLY message actually decoded by a given endpoint, whether it
was addressed to that endpoint (intended) or someone else
(unintended/wasted fan-out).
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

NUM_ROBOTS = 2
SEED = 3
ENDPOINTS = endpoint_list(NUM_ROBOTS)


def run_one(label: str, directed_reply: bool) -> dict[str, Any]:
    output_dir = ROOT / "results_rmw_socket" / "table6_directed_reply_fanout" / f"fleetrmw_n{NUM_ROBOTS}_seed{SEED}_{label}"
    print(f"=== RUN {label} (directed_reply={directed_reply}) ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=NUM_ROBOTS,
        seed=SEED,
        num_crossings=5,
        scenario_timeout_s=30.0,
        discovery_timeout_s=5.0,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        directed_reply=directed_reply,
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        return {"label": label, "status": result["status"], "error": result["error"]}

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

    print(f"  intended REPLY deliveries: {intended}", flush=True)
    print(f"  unintended REPLY deliveries: {unintended}", flush=True)
    print(f"  crossings_completed: {crossings_completed}", flush=True)
    print(f"  forced_entry: {forced_entry}", flush=True)
    return {
        "label": label, "status": "ok", "intended": intended, "unintended": unintended,
        "crossings_completed": crossings_completed, "forced_entry": forced_entry,
    }


def main() -> int:
    old = run_one("OLD_broadcast", directed_reply=False)
    new = run_one("NEW_directed", directed_reply=True)

    out = {"old": old, "new": new}
    out_path = ROOT / "results_rmw_socket" / "table6_directed_reply_fanout" / "red_green_findings.json"
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)

    if old.get("status") == "ok" and new.get("status") == "ok":
        red_ok = old["unintended"] > 0
        green_ok = new["unintended"] == 0
        print(f"RED (old shows unintended fan-out > 0): {'PASS' if red_ok else 'FAIL'} ({old['unintended']})", flush=True)
        print(f"GREEN (new shows zero unintended fan-out): {'PASS' if green_ok else 'FAIL'} ({new['unintended']})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
