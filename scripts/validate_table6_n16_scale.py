"""Table VI N=16 scale validation (see docs/AUDIT_ACCEPTANCE_TRACKING.md,
"TABLE VI ACK/NACK REDUNDANCY=0 ADOPTION" -- this is its own next
phase). Same FROZEN configuration that made N=8 healthy: harness
duration-contract fix already in place (sim_duration_s auto-derives
from scenario_timeout_s), --directed-reply ON, Table-VI-specific
FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=0 (now the default inside
fleetqox_coordination_rmw_env_prefix(), no override passed here), same
network profile, same QoS, same Ricart-Agrawala. NOTHING is changed for
this scale test -- only num_robots grows from 8 to 16.

Run as:
  python3 scripts/validate_table6_n16_scale.py <seed>

PHASE 1 (this script, single seed=7 invocation first): establishes
simulator validity (sim_lag_s <= 10s) BEFORE any performance claim.
PHASE 2 (this same script, re-invoked per seed): replication across
7/13/29/41/53, ONLY if seed=7 is valid.
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


def run_one(seed: int) -> dict[str, Any]:
    endpoints = endpoint_list(NUM_ROBOTS)
    output_dir = ROOT / "results_rmw_socket" / "table6_n16_scale" / f"n16_seed{seed}"
    print(f"=== N={NUM_ROBOTS} seed={seed} (frozen Table VI config, no changes) ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=NUM_ROBOTS,
        seed=seed,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        extra_rmw_env={"FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1"},
        directed_reply=True,
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        return {"seed": seed, "status": result["status"], "error": result["error"]}

    ns3_log = result.get("ns3_log", "") or ""
    (output_dir.parent / f"ns3_seed{seed}.log").write_text(ns3_log, encoding="utf-8")
    stats = parse_wifi_stats(ns3_log)
    last = stats[-1] if stats else {}

    results_dir = output_dir / "container_results"
    per_endpoint: dict[str, Any] = {}
    for i, ep in enumerate(endpoints):
        per_endpoint[ep] = json.loads((results_dir / f"result_{i}.json").read_text())

    # DATA delivery, split by message type (request vs reply), same
    # (wall_ns, intended_recipient) ground-truth method used throughout
    # this investigation.
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

    totals = {"request": [0, 0], "reply": [0, 0]}  # [total, delivered]
    for wall_ns, info in sent_index.items():
        for recipient in info["intended"]:
            totals[info["type"]][0] += 1
            if (wall_ns, recipient) in delivered_pairs:
                totals[info["type"]][1] += 1
    request_total, request_delivered = totals["request"]
    reply_total, reply_delivered = totals["reply"]
    total_pairs = request_total + reply_total
    delivered_count = request_delivered + reply_delivered

    # Retransmissions / permanent loss (RMW-level loss_funnel_trace, same
    # method as investigate_table6_n8_permanent_loss_cause.py /
    # run_table6_n8_directed_reply_ab_experiment.py).
    data_sent_original = 0
    data_sent_retransmit = 0
    data_recv = 0
    ack_nack_events = 0
    sent_identities: set[tuple] = set()
    recv_identities: set[tuple] = set()
    for ep, d in per_endpoint.items():
        diag = d.get("fleetqox_stream_identity_diagnostics", {})
        sender_robot_id = diag.get("effective_robot_id", "")
        trace = d.get("fleetqox_loss_funnel_trace", {})
        for e in trace.get("send", []):
            if e["is_retransmission"]:
                data_sent_retransmit += 1
            else:
                data_sent_original += 1
            sent_identities.add((sender_robot_id, e["source_id"], e["source_sequence"], e["topic"]))
        data_recv += len(trace.get("recv", []))
        for e in trace.get("recv", []):
            recv_identities.add((e["robot_id"], e["source_id"], e["source_sequence"], e["topic"]))
        ack_nack_events += len(trace.get("outgoing_ack_nack", [])) + len(trace.get("incoming_ack_nack", []))
    never_delivered = sent_identities - recv_identities
    permanent_loss_count = len(never_delivered)
    permanent_loss_pct = (
        round(100 * permanent_loss_count / len(sent_identities), 2) if sent_identities else None
    )

    crossings_completed = {ep: d["num_crossings_completed"] for ep, d in per_endpoint.items()}
    forced_entry = {ep: any(c["forced_entry"] for c in d["crossings"]) for ep, d in per_endpoint.items()}
    task_completion_s = {ep: d["task_completion_s"] for ep, d in per_endpoint.items()}

    sim_lag_s = last.get("sim_lag_s")
    wall_elapsed_s = last.get("wall_elapsed_s")
    out = {
        "seed": seed,
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
        "mac_tx_rate_per_s": (last.get("mac_tx_total") / wall_elapsed_s) if wall_elapsed_s else None,
        "ack_nack_events": ack_nack_events,
        "data_sent_original": data_sent_original,
        "data_sent_retransmit": data_sent_retransmit,
        "data_recv": data_recv,
        "unique_data_identities_sent": len(sent_identities),
        "permanent_loss_count": permanent_loss_count,
        "permanent_loss_pct": permanent_loss_pct,
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
    if len(sys.argv) != 2:
        print("usage: validate_table6_n16_scale.py <seed>", file=sys.stderr)
        return 2
    seed = int(sys.argv[1])
    result = run_one(seed)
    out_path = ROOT / "results_rmw_socket" / "table6_n16_scale" / f"seed{seed}_findings.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
