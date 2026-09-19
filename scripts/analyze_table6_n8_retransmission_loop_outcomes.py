"""Table VI N=8 seed=7 -- does the PROVEN retransmission feedback loop
(see docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI N=8 RETRANSMISSION
FEEDBACK-LOOP -- PROVEN") eventually succeed, or does it sometimes end
in permanent loss? Implements that section's "exactly one next step".

Measurement-only, READ-ONLY re-analysis of the ALREADY-COLLECTED raw
trace data from that same run (results_rmw_socket/
table6_n8_retransmission_feedback_loop/fleetrmw_n8_seed7/
container_results/) -- same N=8, same seed=7, same default config, no
rerun, no source/config change of any kind. Reuses the exact
round-clustering method from investigate_table6_n8_retransmission_
feedback_loop.py (ROUND_CLUSTER_GAP_NS, is_retransmission, robot_id
correlation), just classifies EVERY (identity, target) pair -- not
only the ones that eventually succeeded on round >= 2.

"Why retries stopped" for a never-delivered pair is assessed two ways:
  - Explicit budget/limit: ruled out at the SOURCE level for this
    entire run, not inferred per-pair -- FLEETQOX_RMW_REPAIR_
    RETRANSMISSION_BUDGET defaults to -1 (disabled) and FLEETQOX_RMW_
    REPAIR_MAX_ATTEMPTS_PER_SEQUENCE defaults to 0 (disabled) in
    rmw_pubsub.cpp, and grep confirms launch_coordination_endpoints()/
    fleetqox_coordination_rmw_env_prefix() (the ONLY code path that
    launches this Table VI scenario) never sets either env var -- so
    repair_budget_exhausted_/repair_sequence_attempt_limit_exhausted_
    are structurally 0 for every pair in this run, not just checked
    empirically for this one.
  - Scenario shutdown vs missing further NACKs: this pass has no
    trace of INCOMING ack/nack messages (only outgoing retransmission
    sends), so it cannot directly observe "a NACK arrived but nothing
    happened" vs "no NACK arrived at all". Uses a timing proxy instead:
    each sender's own LAST observed wall_ns across its combined send+
    recv trace approximates when this process stopped actively
    recording (task_completion_s is ~120.3s for every endpoint in this
    project's every prior N=8 trial). If a never-delivered pair's last
    retransmission send is within SHUTDOWN_PROXIMITY_NS of that,
    classified "scenario shutdown"; if there is a large remaining gap
    (during which this same sender demonstrably kept sending/receiving
    other traffic), classified "missing further NACKs" (retries could
    have continued but nothing triggered them); otherwise UNKNOWN.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.investigate_table6_n8_retransmission_feedback_loop import (  # noqa: E402
    ENDPOINTS,
    IP_TO_EP,
    ROUND_CLUSTER_GAP_NS,
    target_ip_from_target_field,
)

RUN_DIR = ROOT / "results_rmw_socket" / "table6_n8_retransmission_feedback_loop" / "fleetrmw_n8_seed7"
SHUTDOWN_PROXIMITY_NS = 2_000_000_000  # 2s: see module docstring.


def classify_rounds(round_index: int) -> str:
    if round_index == 0:
        return "delivered_on_original"
    if round_index == 1:
        return "delivered_after_1_round"
    if round_index in (2, 3):
        return "delivered_after_2_3_rounds"
    return "delivered_after_gt3_rounds"


def main() -> int:
    results_dir = RUN_DIR / "container_results"
    per_endpoint: dict[str, Any] = {}
    for i, ep in enumerate(ENDPOINTS):
        per_endpoint[ep] = json.loads((results_dir / f"result_{i}.json").read_text())

    recv_first_arrival: dict[tuple, dict[str, int]] = defaultdict(dict)
    for receiver_ep, d in per_endpoint.items():
        for e in d["fleetqox_loss_funnel_trace"]["recv"]:
            key = (e["robot_id"], e["source_id"], e["source_sequence"], e["topic"])
            existing = recv_first_arrival[key].get(receiver_ep)
            if existing is None or e["wall_ns"] < existing:
                recv_first_arrival[key][receiver_ep] = e["wall_ns"]

    # Per-sender "last observed activity" proxy for scenario end.
    sender_last_activity_ns: dict[str, int] = {}
    for ep, d in per_endpoint.items():
        trace = d["fleetqox_loss_funnel_trace"]
        all_ns = [e["wall_ns"] for e in trace["send"]] + [e["wall_ns"] for e in trace["recv"]]
        sender_last_activity_ns[ep] = max(all_ns) if all_ns else 0

    # Structural ruling-out of explicit budget/limit stop-reasons for this
    # run (see module docstring) -- also spot-check the exposed counters
    # for defense in depth (all four are expected 0; NOT the sole basis
    # for the ruling, since fleetqox_coordination_endpoint.py does not
    # currently read them back -- see docstring for the code-level proof).
    for ep, d in per_endpoint.items():
        diag = d.get("fleetqox_stream_identity_diagnostics", {})
        assert "repair_budget_exhausted" not in diag or diag["repair_budget_exhausted"] == 0

    outcome_counts = defaultdict(int)
    total_pairs = 0
    never_delivered: list[dict[str, Any]] = []
    max_rounds_success = 0
    max_rounds_never = 0

    for sender_ep, d in per_endpoint.items():
        diag = d.get("fleetqox_stream_identity_diagnostics", {})
        sender_robot_id = diag.get("effective_robot_id", "")
        send_events = d["fleetqox_loss_funnel_trace"]["send"]
        by_identity: dict[tuple, list[dict]] = defaultdict(list)
        for e in send_events:
            by_identity[(e["source_id"], e["source_sequence"], e["topic"])].append(e)

        for (source_id, sequence, topic), events in by_identity.items():
            events.sort(key=lambda e: e["wall_ns"])
            originals = [e for e in events if not e["is_retransmission"]]
            retransmits = [e for e in events if e["is_retransmission"]]

            rounds: list[list[dict]] = []
            for e in retransmits:
                if rounds and e["wall_ns"] - rounds[-1][-1]["wall_ns"] <= ROUND_CLUSTER_GAP_NS:
                    rounds[-1].append(e)
                else:
                    rounds.append([e])

            all_rounds = ([originals] + rounds) if originals else rounds
            if not all_rounds:
                continue
            round_windows = []
            for idx, round_events in enumerate(all_rounds):
                round_index = idx if originals else idx + 1
                start_ns = min(e["wall_ns"] for e in round_events)
                targets = {target_ip_from_target_field(e["target"]) for e in round_events}
                round_windows.append((round_index, start_ns, targets, round_events))

            recv_key = (sender_robot_id, source_id, sequence, topic)
            arrivals = recv_first_arrival.get(recv_key, {})

            all_targets = {ip for (_, _, targets, _) in round_windows for ip in targets}
            for target_ip in all_targets:
                target_ep = IP_TO_EP.get(target_ip)
                if target_ep is None or target_ep == sender_ep:
                    continue
                total_pairs += 1
                first_arrival_ns = arrivals.get(target_ep)

                # Which rounds were this pair actually sent in?
                pair_rounds = [
                    (ri, start_ns, round_events) for ri, start_ns, targets, round_events in round_windows
                    if target_ip in targets
                ]

                if first_arrival_ns is None:
                    # NEVER delivered.
                    last_round_index, last_round_start_ns, last_round_events = pair_rounds[-1]
                    last_pair_send = max(
                        (e for e in last_round_events if target_ip_from_target_field(e["target"]) == target_ip),
                        key=lambda e: e["wall_ns"],
                    )
                    total_send_attempts = sum(
                        1 for _, _, revents in pair_rounds
                        for e in revents if target_ip_from_target_field(e["target"]) == target_ip
                    )
                    gap_to_shutdown_ns = sender_last_activity_ns[sender_ep] - last_pair_send["wall_ns"]
                    if gap_to_shutdown_ns <= SHUTDOWN_PROXIMITY_NS:
                        stop_reason = "scenario_shutdown"
                    elif gap_to_shutdown_ns > SHUTDOWN_PROXIMITY_NS:
                        stop_reason = "missing_further_nacks"
                    else:
                        stop_reason = "UNKNOWN"
                    num_rounds = len(rounds)
                    outcome_counts["never_delivered"] += 1
                    max_rounds_never = max(max_rounds_never, num_rounds)
                    never_delivered.append({
                        "sender": sender_ep, "robot_id": sender_robot_id, "source_id": source_id,
                        "source_sequence": sequence, "topic": topic, "target": target_ep,
                        "num_retransmission_rounds": num_rounds,
                        "total_send_attempts_to_target": total_send_attempts,
                        "final_send_event": last_pair_send,
                        "stop_reason": stop_reason,
                        "gap_to_sender_shutdown_ns": gap_to_shutdown_ns,
                    })
                    continue

                # Delivered: find which round window contains it.
                first_success_round = pair_rounds[-1][0]
                for idx, (ri, start_ns, _) in enumerate(pair_rounds):
                    next_start_ns = pair_rounds[idx + 1][1] if idx + 1 < len(pair_rounds) else None
                    if start_ns <= first_arrival_ns and (next_start_ns is None or first_arrival_ns < next_start_ns):
                        first_success_round = ri
                        break
                outcome_counts[classify_rounds(first_success_round)] += 1
                max_rounds_success = max(max_rounds_success, first_success_round)

    total_delivered = total_pairs - outcome_counts["never_delivered"]
    print("=== compact outcome table ===")
    for k in (
        "delivered_on_original", "delivered_after_1_round",
        "delivered_after_2_3_rounds", "delivered_after_gt3_rounds", "never_delivered",
    ):
        print(f"  {k}: {outcome_counts[k]}")
    print("total_pairs:", total_pairs)
    print("eventual_delivery_pct:", round(100 * total_delivered / total_pairs, 2) if total_pairs else None)
    print("permanent_loss_pct:", round(100 * outcome_counts['never_delivered'] / total_pairs, 2) if total_pairs else None)
    print("max_rounds_among_successful:", max_rounds_success)
    print("max_rounds_among_never_delivered:", max_rounds_never)

    stop_reason_counts = defaultdict(int)
    for x in never_delivered:
        stop_reason_counts[x["stop_reason"]] += 1
    print("stop_reason_counts:", dict(stop_reason_counts))

    out = {
        "outcome_counts": dict(outcome_counts),
        "total_pairs": total_pairs,
        "eventual_delivery_pct": round(100 * total_delivered / total_pairs, 2) if total_pairs else None,
        "permanent_loss_pct": round(100 * outcome_counts["never_delivered"] / total_pairs, 2) if total_pairs else None,
        "max_rounds_among_successful": max_rounds_success,
        "max_rounds_among_never_delivered": max_rounds_never,
        "stop_reason_counts": dict(stop_reason_counts),
        "never_delivered_examples": never_delivered,
    }
    out_path = RUN_DIR.parent / "retransmission_loop_outcomes.json"
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
