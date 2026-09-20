"""Phase 4 validity sanity (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE
VI HARNESS DURATION MISMATCH FIX"): N=2 and N=4, lightweight
(heavyTracing default false), directed-reply ON, frozen default config,
AFTER the sim_duration_s/scenario_timeout_s harness fix -- confirms both
remain valid (sim_lag_s <= 10s) and records wall time / Simulator::Now()
/ sim_lag_s for the record.
"""

from __future__ import annotations

import json
import re
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

_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")
SEED = 7
VALIDITY_GATE_S = 10.0


def parse_wifi_stats(ns3_log: str) -> list[dict[str, Any]]:
    out = []
    for line in ns3_log.splitlines():
        if not line.startswith("FLEETQOX_WIFI_STATS "):
            continue
        try:
            out.append(json.loads(_TRAILING_COMMA_RE.sub(r"\1", line[len("FLEETQOX_WIFI_STATS "):])))
        except json.JSONDecodeError:
            continue
    return out


def run_one(num_robots: int) -> dict[str, Any]:
    endpoints = endpoint_list(num_robots)
    output_dir = ROOT / "results_rmw_socket" / "table6_n2_n4_sanity_post_fix" / f"n{num_robots}_seed{SEED}"
    print(f"=== N={num_robots} seed={SEED} (post-fix sanity) ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=num_robots,
        seed=SEED,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        directed_reply=True,
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        return {"num_robots": num_robots, "status": result["status"], "error": result["error"]}

    ns3_log = result.get("ns3_log", "") or ""
    (output_dir.parent / f"ns3_n{num_robots}.log").write_text(ns3_log, encoding="utf-8")
    stats = parse_wifi_stats(ns3_log)
    last = stats[-1] if stats else {}

    results_dir = output_dir / "container_results"
    crossings_completed = {}
    forced_entry = {}
    task_completion_s = {}
    for i, ep in enumerate(endpoints):
        d = json.loads((results_dir / f"result_{i}.json").read_text())
        crossings_completed[ep] = d["num_crossings_completed"]
        forced_entry[ep] = any(c["forced_entry"] for c in d["crossings"])
        task_completion_s[ep] = d["task_completion_s"]

    sim_lag_s = last.get("sim_lag_s")
    out = {
        "num_robots": num_robots,
        "status": "ok",
        "wall_elapsed_s": last.get("wall_elapsed_s"),
        "sim_time_s": last.get("sim_time_s"),
        "sim_lag_s": sim_lag_s,
        "valid": sim_lag_s is not None and sim_lag_s <= VALIDITY_GATE_S,
        "crossings_completed": crossings_completed,
        "any_forced_entry": any(forced_entry.values()),
        "task_completion_s_mean": round(sum(task_completion_s.values()) / len(task_completion_s), 2),
    }
    print(json.dumps(out, indent=2), flush=True)
    return out


def main() -> int:
    results = [run_one(2), run_one(4)]
    out_path = ROOT / "results_rmw_socket" / "table6_n2_n4_sanity_post_fix" / "sanity_findings.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    print("\n=== SUMMARY ===", flush=True)
    for r in results:
        if r.get("status") != "ok":
            print(f"N={r['num_robots']}: FAILED", flush=True)
            continue
        print(
            f"N={r['num_robots']}: wall={r['wall_elapsed_s']:.2f}s sim={r['sim_time_s']:.2f}s "
            f"lag={r['sim_lag_s']:.2f}s valid={r['valid']} "
            f"task_completion_s_mean={r['task_completion_s_mean']} any_forced_entry={r['any_forced_entry']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
