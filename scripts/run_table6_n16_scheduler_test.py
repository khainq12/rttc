"""N=16 scheduler test (Phase 3 continuation, see
docs/AUDIT_ACCEPTANCE_TRACKING.md "N=16 SERIOUS PERFORMANCE PASS"): the
N=8 A/B (run_table6_n8_scheduler_ab.py, seeds 7/13) found 'map' (ns-3's
original default) and 'heap' produce matching network-level results at
N=8, where both are already realtime-valid (sim_lag_s ~0.02-0.03s) --
too fast to show any scheduler-driven wall-clock difference. This
script re-runs the EXACT SAME frozen Table VI N=16 configuration used
by validate_table6_n16_scale.py (which found sim_lag_s~35.5s, self_cpu_s
~117.7 with the untouched 'map' default), varying ONLY --scheduler, to
see whether the scheduler choice moves sim_lag_s/self_cpu_s at the
scale where ns-3's own CPU is actually the bottleneck.

Usage: python3 scripts/run_table6_n16_scheduler_test.py <seed> <scheduler>
  e.g. python3 scripts/run_table6_n16_scheduler_test.py 7 heap
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

NUM_ROBOTS = 16
VALIDITY_GATE_S = 10.0
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


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


def run_one(seed: int, scheduler: str) -> dict[str, Any]:
    endpoints = endpoint_list(NUM_ROBOTS)
    output_dir = ROOT / "results_rmw_socket" / "table6_n16_scheduler_test" / f"n16_seed{seed}_{scheduler}"
    print(f"=== N={NUM_ROBOTS} seed={seed} scheduler={scheduler} (frozen Table VI config otherwise) ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=NUM_ROBOTS,
        seed=seed,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        directed_reply=True,
        ns3_scheduler=scheduler,
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        return {"seed": seed, "scheduler": scheduler, "status": result["status"], "error": result["error"]}

    ns3_log = result.get("ns3_log", "") or ""
    (output_dir.parent / f"ns3_seed{seed}_{scheduler}.log").write_text(ns3_log, encoding="utf-8")
    stats = parse_wifi_stats(ns3_log)
    last = stats[-1] if stats else {}

    results_dir = output_dir / "container_results"
    per_endpoint: dict[str, Any] = {}
    for i, ep in enumerate(endpoints):
        per_endpoint[ep] = json.loads((results_dir / f"result_{i}.json").read_text())

    all_endpoints_set = set(endpoints)
    sent_index: dict[int, dict[str, Any]] = {}
    for ep, d in per_endpoint.items():
        for s in d.get("sent_log", []):
            wall_ns = s.get("wall_ns")
            if wall_ns is None:
                continue
            msg_type = s.get("type")
            if msg_type == "reply":
                intended = {s["to"]}
            elif msg_type == "request":
                intended = all_endpoints_set - {ep}
            else:
                continue
            sent_index[wall_ns] = {"sender": ep, "type": msg_type, "intended": intended}
    delivered_pairs: set[tuple] = set()
    for ep, d in per_endpoint.items():
        for r in d.get("raw_received_log", []):
            wall_ns = r.get("wall_ns")
            if wall_ns is not None:
                delivered_pairs.add((wall_ns, ep))

    totals = {"request": [0, 0], "reply": [0, 0]}
    for wall_ns, info in sent_index.items():
        for recipient in info["intended"]:
            totals[info["type"]][0] += 1
            if (wall_ns, recipient) in delivered_pairs:
                totals[info["type"]][1] += 1
    request_total, request_delivered = totals["request"]
    reply_total, reply_delivered = totals["reply"]
    total_pairs = request_total + reply_total
    delivered_count = request_delivered + reply_delivered

    crossings_completed = {ep: d["num_crossings_completed"] for ep, d in per_endpoint.items()}
    forced_entry = {ep: any(c["forced_entry"] for c in d["crossings"]) for ep, d in per_endpoint.items()}
    task_completion_s = {ep: d["task_completion_s"] for ep, d in per_endpoint.items()}

    sim_lag_s = last.get("sim_lag_s")
    wall_elapsed_s = last.get("wall_elapsed_s")
    out = {
        "seed": seed,
        "scheduler": scheduler,
        "num_robots": NUM_ROBOTS,
        "status": "ok",
        "wall_elapsed_s": wall_elapsed_s,
        "sim_time_s": last.get("sim_time_s"),
        "sim_lag_s": sim_lag_s,
        "self_cpu_s": last.get("self_cpu_s"),
        "self_rss_kb": last.get("self_rss_kb"),
        "valid": (sim_lag_s is not None and sim_lag_s <= VALIDITY_GATE_S),
        "mac_tx_total": last.get("mac_tx_total"),
        "mac_tx_bytes": last.get("mac_tx_bytes"),
        "mac_rx_total": last.get("mac_rx_total"),
        "mac_rx_bytes": last.get("mac_rx_bytes"),
        "mac_tx_drop_total": last.get("mac_tx_drop_total"),
        "mac_rx_drop_total": last.get("mac_rx_drop_total"),
        "phy_tx_begin_total": last.get("phy_tx_begin_total"),
        "phy_rx_drop_total": last.get("phy_rx_drop_total"),
        "total_pairs": total_pairs,
        "delivered_count": delivered_count,
        "data_delivery_pct": round(100 * delivered_count / total_pairs, 2) if total_pairs else None,
        "request_total": request_total,
        "request_delivered": request_delivered,
        "request_delivery_pct": round(100 * request_delivered / request_total, 2) if request_total else None,
        "reply_total": reply_total,
        "reply_delivered": reply_delivered,
        "reply_delivery_pct": round(100 * reply_delivered / reply_total, 2) if reply_total else None,
        "crossings_completed": crossings_completed,
        "any_forced_entry": any(forced_entry.values()),
        "all_5_of_5_crossings": all(v == 5 for v in crossings_completed.values()),
        "task_completion_s_mean": round(sum(task_completion_s.values()) / len(task_completion_s), 2),
    }
    print(json.dumps({k: v for k, v in out.items() if k != "crossings_completed"}, indent=2), flush=True)
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: run_table6_n16_scheduler_test.py <seed> <scheduler>", file=sys.stderr)
        return 2
    seed = int(sys.argv[1])
    scheduler = sys.argv[2]
    result = run_one(seed, scheduler)
    out_path = ROOT / "results_rmw_socket" / "table6_n16_scheduler_test" / f"seed{seed}_{scheduler}_findings.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
