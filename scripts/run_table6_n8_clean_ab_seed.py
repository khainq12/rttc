"""Phase 5 clean N=8 A/B, ONE seed per invocation (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI HARNESS DURATION MISMATCH
FIX" / "N=8 REALTIME-LAG VALIDATION"). Run as:
  python3 scripts/run_table6_n8_clean_ab_seed.py <seed>

Frozen throughout: directed-reply ON, N=8, default network profile,
the NOW-FIXED harness (sim_duration_s auto-derived from
scenario_timeout_s, so ns-3's own network can no longer legitimately
exit before the workload's own deadline). Compares ONLY
FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=10 (A, the production
default) vs =0 (B). Counterbalanced by seed parity (even-indexed seeds
run A-then-B, odd-indexed run B-then-A) to cancel any systematic
run-order drift -- this script takes the seed AND its counterbalance
order as args so the caller (one process per seed, to stay under a
single Bash call's time budget) controls it.

heavyTracing stays at its default (false) -- lightweight, per the
"N=8 REALTIME-LAG VALIDATION" pass's own STEP 1 finding that this
program's per-packet extraction/logging is not what causes the
pacing lag. FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING=1 IS enabled --
that is separate, endpoint-container-side (not ns3sim-side)
instrumentation, already used throughout this investigation's N=8 A/B
experiments (see run_table6_n8_directed_reply_ab_experiment.py) to get
ACK/NACK message counts, and does not touch ns-3's own realtime
pacing at all.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
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


def run_one(seed: int, label: str, redundant_count: str) -> dict[str, Any]:
    output_dir = ROOT / "results_rmw_socket" / "table6_n8_clean_ab" / f"n8_seed{seed}_{label}"
    print(f"=== N=8 seed={seed} label={label} redundancy={redundant_count} ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=NUM_ROBOTS,
        seed=seed,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        extra_rmw_env={
            "FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1",
            "FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT": redundant_count,
        },
        directed_reply=True,
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        return {"seed": seed, "label": label, "status": result["status"], "error": result["error"]}

    ns3_log = result.get("ns3_log", "") or ""
    (output_dir.parent / f"ns3_seed{seed}_{label}.log").write_text(ns3_log, encoding="utf-8")
    stats = parse_wifi_stats(ns3_log)
    last = stats[-1] if stats else {}

    results_dir = output_dir / "container_results"
    per_endpoint: dict[str, Any] = {}
    for i, ep in enumerate(ENDPOINTS):
        per_endpoint[ep] = json.loads((results_dir / f"result_{i}.json").read_text())

    # DATA delivery: same (wall_ns, intended_recipient) ground-truth
    # method used throughout this investigation (sent_log x
    # raw_received_log), not "any ns-3 rx anywhere".
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

    # ACK/NACK message-level traffic (endpoint-side loss_funnel_trace,
    # independent of ns-3's own heavyTracing gate).
    ack_nack_events = 0
    for ep, d in per_endpoint.items():
        trace = d.get("fleetqox_loss_funnel_trace", {})
        ack_nack_events += len(trace.get("outgoing_ack_nack", [])) + len(trace.get("incoming_ack_nack", []))

    crossings_completed = {ep: d["num_crossings_completed"] for ep, d in per_endpoint.items()}
    forced_entry = {ep: any(c["forced_entry"] for c in d["crossings"]) for ep, d in per_endpoint.items()}
    task_completion_s = {ep: d["task_completion_s"] for ep, d in per_endpoint.items()}

    sim_lag_s = last.get("sim_lag_s")
    out = {
        "seed": seed,
        "label": label,
        "status": "ok",
        "redundant_count": redundant_count,
        "wall_elapsed_s": last.get("wall_elapsed_s"),
        "sim_time_s": last.get("sim_time_s"),
        "sim_lag_s": sim_lag_s,
        "valid": (sim_lag_s is not None and sim_lag_s <= VALIDITY_GATE_S),
        "mac_tx_total": last.get("mac_tx_total"),
        "mac_tx_bytes": last.get("mac_tx_bytes"),
        "mac_rx_total": last.get("mac_rx_total"),
        "mac_rx_bytes": last.get("mac_rx_bytes"),
        "ack_nack_events": ack_nack_events,
        "total_pairs": total_pairs,
        "delivered_count": delivered_count,
        "data_delivery_pct": data_delivery_pct,
        "crossings_completed": crossings_completed,
        "any_forced_entry": any(forced_entry.values()),
        "all_5_of_5_crossings": all(v == 5 for v in crossings_completed.values()),
        "task_completion_s_mean": round(sum(task_completion_s.values()) / len(task_completion_s), 2),
    }
    print(json.dumps({k: v for k, v in out.items() if k not in ("crossings_completed",)}, indent=2), flush=True)
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_table6_n8_clean_ab_seed.py <seed>", file=sys.stderr)
        return 2
    seed = int(sys.argv[1])
    seed_order = [7, 13, 29, 41, 53]
    idx = seed_order.index(seed) if seed in seed_order else 0
    order = [("A", "10"), ("B", "0")] if idx % 2 == 0 else [("B", "0"), ("A", "10")]

    results = []
    for label, redundant_count in order:
        results.append(run_one(seed, label, redundant_count))

    out_path = ROOT / "results_rmw_socket" / "table6_n8_clean_ab" / f"seed{seed}_findings.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
