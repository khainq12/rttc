"""N=8 A/B: ns-3 Simulator event-scheduler implementation (Phase 3 of the
"N=16 SERIOUS PERFORMANCE PASS" investigation, see
docs/AUDIT_ACCEPTANCE_TRACKING.md). Compares 'map' (ns3::MapScheduler --
this program's original, still-default behavior; also the scheduler
profiling caught as a hot spot -- MapScheduler::Insert -- at N=16) vs
'heap' (ns3::HeapScheduler, the primary candidate per ns-3's own general
reputation for better cache locality than a tree/map-based scheduler).

This is a PURE internal event-ordering data-structure swap
(Simulator::SetScheduler()) -- it cannot change which simulated events
fire or their simulated-time order, so it cannot alter network
semantics/results by construction. This script's job is to CONFIRM that
empirically (network-level outputs must match, modulo the run-to-run
wifi/RNG variance any two identically-configured runs already have) and
measure whether it moves sim_lag_s/CPU/RSS at N=8, matching the
established N=8-first-then-N=16 escalation discipline used throughout
this investigation (see run_table6_n8_clean_ab_seed.py).

Frozen: N=8, directed-reply ON, default network profile, default
ACK/NACK redundancy (Table-VI-specific 0, set inside
run_coordination_probe() -- see run_ns3_docker_container_fleet_probe.py),
heavyTracing default (false), realtimeHardLimitS default (0, disabled --
Phase 5 is a separate, later change). ONLY --scheduler varies.

Usage: python3 scripts/run_table6_n8_scheduler_ab.py <seed>
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

NUM_ROBOTS = 8
ENDPOINTS = endpoint_list(NUM_ROBOTS)
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
    output_dir = ROOT / "results_rmw_socket" / "table6_n8_scheduler_ab" / f"n8_seed{seed}_{scheduler}"
    print(f"=== N=8 seed={seed} scheduler={scheduler} ===", flush=True)
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
    for i, ep in enumerate(ENDPOINTS):
        per_endpoint[ep] = json.loads((results_dir / f"result_{i}.json").read_text())

    all_endpoints_set = set(ENDPOINTS)
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
    total_pairs = 0
    delivered_count = 0
    for wall_ns, info in sent_index.items():
        for recipient in info["intended"]:
            total_pairs += 1
            if (wall_ns, recipient) in delivered_pairs:
                delivered_count += 1
    data_delivery_pct = round(100 * delivered_count / total_pairs, 2) if total_pairs else None

    crossings_completed = {ep: d["num_crossings_completed"] for ep, d in per_endpoint.items()}
    forced_entry = {ep: any(c["forced_entry"] for c in d["crossings"]) for ep, d in per_endpoint.items()}
    task_completion_s = {ep: d["task_completion_s"] for ep, d in per_endpoint.items()}

    sim_lag_s = last.get("sim_lag_s")
    out = {
        "seed": seed,
        "scheduler": scheduler,
        "status": "ok",
        "wall_elapsed_s": last.get("wall_elapsed_s"),
        "sim_time_s": last.get("sim_time_s"),
        "sim_lag_s": sim_lag_s,
        "valid": (sim_lag_s is not None and sim_lag_s <= VALIDITY_GATE_S),
        "self_cpu_s": last.get("self_cpu_s"),
        "self_rss_kb": last.get("self_rss_kb"),
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
        "data_delivery_pct": data_delivery_pct,
        "crossings_completed": crossings_completed,
        "any_forced_entry": any(forced_entry.values()),
        "all_5_of_5_crossings": all(v == 5 for v in crossings_completed.values()),
        "task_completion_s_mean": round(sum(task_completion_s.values()) / len(task_completion_s), 2),
    }
    print(json.dumps({k: v for k, v in out.items() if k != "crossings_completed"}, indent=2), flush=True)
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_table6_n8_scheduler_ab.py <seed>", file=sys.stderr)
        return 2
    seed = int(sys.argv[1])
    # Counterbalance by seed parity, same convention as
    # run_table6_n8_clean_ab_seed.py, to cancel any systematic run-order
    # drift within a single invocation.
    schedulers = ["map", "heap"] if seed % 2 else ["heap", "map"]

    results = [run_one(seed, s) for s in schedulers]

    out_path = ROOT / "results_rmw_socket" / "table6_n8_scheduler_ab" / f"seed{seed}_findings.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
