"""Table VI N=8 seed=7 -- WHY do permanently-lost (DATA identity, target)
pairs stop being retransmitted? (See docs/AUDIT_ACCEPTANCE_TRACKING.md,
"TABLE VI N=8 RETRANSMISSION LOOP OUTCOMES", whose "exactly one next
step" this implements.)

Measurement-only, N=8 seed=7, default config. No ACK/NACK count,
retry/timeout, QoS, ns-3, broadcast, or production behavior change.

New instrumentation (additive only, see rmw_pubsub.cpp diff):
  - OutgoingAckNackTraceEvent: recorded at the RECEIVER/subscriber side,
    once per (missing sequence range), both at the per-received-frame
    site (observe_frame()'s own call site) and the idle-repair site
    (idle_repair_ack_nacks(), the ~75ms-default periodic re-request that
    fires from take() finding an empty queue even with no new frame) --
    "the target's own intent to report this range as missing".
  - IncomingAckNackTraceEvent: recorded at the SENDER/publisher side,
    inside handle_ack_nack_feedback(), reusing that function's own
    already-computed retransmit_frames (found_in_ledger=true) and
    unavailable_ranges (found_in_ledger=false) -- "what the sender saw
    and could/couldn't act on".
  - RetransmitLedgerErasureTraceEvent: recorded at all 3 (of the 4)
    g_retransmit_ledger.erase() call sites reachable in this scenario's
    default config, with the exact reason ("acknowledged" |
    "lifespan_exceeded" | "capacity_evicted" | "publisher_destroyed").

Classification per never-delivered (identity, target) pair, using the
final DATA retransmission's own wall_ns as the cutoff:
  A) an incoming_ack_nack event for this exact (publisher_id, target
     robot_id, sequence) with found_in_ledger=true appears AFTER the
     final send -- the sender saw it, the ledger still had it, so a
     retransmission SHOULD have fired (contradicts this pair being
     "final" -- direct evidence of a stuck/incomplete-retry chain, not
     merely a hypothesis).
  B) an outgoing_ack_nack event from the target for this exact sequence
     appears AFTER the final send, but NO incoming_ack_nack event at the
     sender ever matches it -- the target re-reported it, the report
     never arrived.
  C) NO outgoing_ack_nack event from the target for this sequence
     appears after the final send at all -- the target never asked
     again.
  D) an incoming_ack_nack event at the sender matches (found_in_ledger
     could be either, but specifically the LEDGER ITSELF shows an
     erasure for this exact (publisher_id, sequence) at or before the
     time it would have been needed) -- confirmed via
     retransmit_ledger_erasure directly, not inferred from "not found".
  E) none of the above cleanly applies (ambiguous or contradictory
     evidence).

Priority when multiple could apply: D (a proven ledger erasure) is
checked first since it is the most direct, then A, then B, then C.
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

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    endpoint_list,
    run_coordination_probe,
)
from scripts.investigate_table6_n8_retransmission_feedback_loop import (  # noqa: E402
    IP_TO_EP,
    ROUND_CLUSTER_GAP_NS,
    target_ip_from_target_field,
)

NUM_ROBOTS = 8
SEED = 7
ENDPOINTS = endpoint_list(NUM_ROBOTS)


def main() -> int:
    output_dir = ROOT / "results_rmw_socket" / "table6_n8_permanent_loss_cause" / f"fleetrmw_n{NUM_ROBOTS}_seed{SEED}"
    print(f"=== FleetRMW N={NUM_ROBOTS} seed={SEED} (permanent-loss ACK/NACK cause trace) ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=NUM_ROBOTS,
        seed=SEED,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        extra_rmw_env={"FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1"},
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        print("run did not complete ok -- stopping", flush=True)
        return 1

    results_dir = output_dir / "container_results"
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

    # Index incoming_ack_nack events per SENDER endpoint by (publisher_id, robot_id-of-target).
    incoming_by_sender: dict[str, list[dict]] = {}
    for ep, d in per_endpoint.items():
        incoming_by_sender[ep] = d["fleetqox_loss_funnel_trace"]["incoming_ack_nack"]

    # Index outgoing_ack_nack events per TARGET endpoint.
    outgoing_by_target: dict[str, list[dict]] = {}
    for ep, d in per_endpoint.items():
        outgoing_by_target[ep] = d["fleetqox_loss_funnel_trace"]["outgoing_ack_nack"]

    # Index ledger erasure events per SENDER endpoint by (publisher_id, sequence).
    erasure_by_sender: dict[str, dict[tuple, dict]] = {}
    for ep, d in per_endpoint.items():
        idx = {}
        for e in d["fleetqox_loss_funnel_trace"]["retransmit_ledger_erasure"]:
            key = (e["publisher_id"], e["sequence"])
            if key not in idx or e["wall_ns"] < idx[key]["wall_ns"]:
                idx[key] = e
        erasure_by_sender[ep] = idx

    classification_counts = defaultdict(int)
    examples: dict[str, list[dict]] = defaultdict(list)
    total_never_delivered = 0

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

            recv_key = (sender_robot_id, source_id, sequence, topic)
            arrivals = recv_first_arrival.get(recv_key, {})

            all_targets = {
                target_ip_from_target_field(e["target"])
                for round_events in all_rounds for e in round_events
            }
            for target_ip in all_targets:
                target_ep = IP_TO_EP.get(target_ip)
                if target_ep is None or target_ep == sender_ep:
                    continue
                if arrivals.get(target_ep) is not None:
                    continue  # delivered eventually -- not our concern here.

                total_never_delivered += 1
                pair_send_events = [
                    e for round_events in all_rounds for e in round_events
                    if target_ip_from_target_field(e["target"]) == target_ip
                ]
                final_send = max(pair_send_events, key=lambda e: e["wall_ns"])
                final_wall_ns = final_send["wall_ns"]
                target_robot_id = per_endpoint[target_ep]["fleetqox_stream_identity_diagnostics"]["effective_robot_id"]

                # D) ledger erasure directly proven for this (publisher, sequence).
                erasure = erasure_by_sender.get(sender_ep, {}).get((source_id, sequence))

                # Incoming ack_nack events at the sender, from this exact target,
                # covering this sequence, after the final send.
                matching_incoming = [
                    e for e in incoming_by_sender.get(sender_ep, [])
                    if e["publisher_id"] == source_id and e["robot_id"] == target_robot_id
                    and e["range_start"] <= sequence <= e["range_end"]
                    and e["wall_ns"] > final_wall_ns
                ]
                incoming_found_true = [e for e in matching_incoming if e["found_in_ledger"]]
                incoming_found_false = [e for e in matching_incoming if not e["found_in_ledger"]]

                # Outgoing ack_nack events from the target, covering this sequence,
                # after the final send.
                matching_outgoing = [
                    e for e in outgoing_by_target.get(target_ep, [])
                    if e["publisher_id"] == source_id and e["robot_id"] == target_robot_id
                    and e["range_start"] <= sequence <= e["range_end"]
                    and e["wall_ns"] > final_wall_ns
                ]

                # Priority: direct sender-side evidence (A/D) beats
                # target-side-only evidence (B/C), since A/D are proven from
                # what the sender itself actually saw, not inferred from
                # absence. Ledger erasure info is reported for every example
                # regardless of classification (see "ledger_erasure" field)
                # since it is relevant context even when it isn't the
                # deciding factor (e.g. an entry can be legitimately erased
                # AFTER a target simply stopped asking, which is still C).
                if incoming_found_true:
                    classification = "A"
                    reason = "sender received a further NACK naming this sequence, ledger still had it"
                elif incoming_found_false:
                    classification = "D"
                    reason = "sender received a further NACK naming this sequence, but ledger no longer had it"
                elif matching_outgoing:
                    classification = "B"
                    reason = "target sent a further NACK naming this sequence, sender never received it"
                elif not matching_outgoing and not matching_incoming:
                    classification = "C"
                    reason = "target never sent another NACK naming this sequence"
                else:
                    classification = "E"
                    reason = "ambiguous/contradictory evidence"

                classification_counts[classification] += 1
                example = {
                    "sender": sender_ep, "robot_id": sender_robot_id, "source_id": source_id,
                    "source_sequence": sequence, "topic": topic, "target": target_ep,
                    "target_robot_id": target_robot_id, "final_send_wall_ns": final_wall_ns,
                    "final_send_outcome": final_send["outcome"],
                    "num_retransmission_rounds": len(rounds),
                    "ledger_erasure": erasure,
                    "matching_outgoing_after_final_send": matching_outgoing[:3],
                    "matching_incoming_after_final_send": matching_incoming[:3],
                    "classification": classification,
                    "reason": reason,
                }
                if len(examples[classification]) < 10:
                    examples[classification].append(example)

    print("=== A/B/C/D/E counts ===")
    for k in ("A", "B", "C", "D", "E"):
        pct = round(100 * classification_counts[k] / total_never_delivered, 2) if total_never_delivered else None
        print(f"  {k}: {classification_counts[k]} ({pct}%)")
    print("total_never_delivered_pairs:", total_never_delivered)

    out = {
        "classification_counts": dict(classification_counts),
        "total_never_delivered_pairs": total_never_delivered,
        "examples": {k: v for k, v in examples.items()},
    }
    out_path = output_dir.parent / "permanent_loss_cause_findings.json"
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
