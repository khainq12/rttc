"""Table VI N=8 seed=7 -- does the RETRANSMISSION FEEDBACK LOOP actually
occur: DATA loss -> NACK -> retransmit -> retransmit loss -> later NACK
-> retransmit again? (See docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI
N=8 ACK/NACK REDUNDANCY VS DATA RETRANSMISSION -- INDEPENDENCE TEST",
whose "exactly one next step" this implements.)

Measurement-only, N=8 seed=7, default config (FLEETQOX_RMW_ACK_NACK_
REDUNDANT_RESEND_COUNT left at its own default -- this pass does not
touch it). No ACK/NACK/timeout/QoS/broadcast/ns-3/protocol change.

Instrumentation (additive only, see rmw_pubsub.cpp diff):
  - LossFunnelSendEvent gained `is_retransmission` (bool): EXACT, set
    from a new thread_local flag armed by send_retransmission_frame()
    immediately before its one send_frame() call and consumed by
    send_frame_with_qos() the moment it sets the send-identity for that
    same call -- distinguishes an original publish's send event from a
    NACK-driven retransmission's send event without any wall_ns
    clustering/inference for THAT part.
  - LossFunnelRecvEvent gained `robot_id`: source_id (publisher_id)
    alone is not guaranteed unique across different senders in a run
    (same reason SubscriptionMatchTraceEvent needed it), so correlating
    a specific sender's send trace against receivers' recv traces
    needs the full (robot_id, source_id, source_sequence, topic)
    identity, not source_id alone.

Method: for each sender endpoint, group its own "send" trace events by
(source_id, source_sequence, topic) -- a DATA identity. Split into
round 0 (is_retransmission == false events, the original broadcast)
and retransmission events; cluster consecutive retransmission events
whose wall_ns gap is under ROUND_CLUSTER_GAP_NS into one logical round
(a single send_retransmission_frame() call broadcasts to multiple
targets in a tight loop -- all such per-target events belong to ONE
round, not one each). For each (round, target) pair, check every OTHER
endpoint's own "recv" trace (filtered by the full identity, including
robot_id) for that target's FIRST arrival timestamp. A target's
first_success_round is the earliest round whose window contains that
first arrival. first_success_round >= 2 for some target is direct,
per-target evidence of the literal feedback loop (round 0 AND round 1
both failed to reach that target; round 2 is what finally did).
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

NUM_ROBOTS = 8
SEED = 7
ENDPOINTS = endpoint_list(NUM_ROBOTS)
IP_OF = {e: f"10.60.0.{i + 2}" for i, e in enumerate(ENDPOINTS)}
IP_TO_EP = {v: k for k, v in IP_OF.items()}
RMW_PORT = 9100
ROUND_CLUSTER_GAP_NS = 100_000_000  # 100ms: a single broadcast loop's own
# per-target sendto() calls land far closer together than this; any gap
# this large between consecutive is_retransmission events means a
# genuinely separate send_retransmission_frame() call (a new round).
ARRIVAL_WINDOW_NS = 3_000_000_000  # 3s: generous upper bound for network
# transit + processing delay attributable to one round's send, well under
# the seconds-to-tens-of-seconds gaps between distinct retransmission
# rounds seen in this scenario throughout this investigation.


def target_ip_from_target_field(target: str) -> str:
    # LossFunnelSendEvent.target stores "ip:port" (see record_loss_funnel_event).
    return target.rsplit(":", 1)[0] if ":" in target else target


def main() -> int:
    output_dir = ROOT / "results_rmw_socket" / "table6_n8_retransmission_feedback_loop" / f"fleetrmw_n{NUM_ROBOTS}_seed{SEED}"
    print(f"=== FleetRMW N={NUM_ROBOTS} seed={SEED} (retransmission feedback-loop trace) ===", flush=True)
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

    # Build robot_id-keyed recv index: recv_index[(robot_id, source_id, sequence, topic)]
    # -> {receiver_endpoint: first_arrival_wall_ns}
    recv_first_arrival: dict[tuple, dict[str, int]] = defaultdict(dict)
    for receiver_ep, d in per_endpoint.items():
        recv_events = d["fleetqox_loss_funnel_trace"]["recv"]
        for e in recv_events:
            key = (e["robot_id"], e["source_id"], e["source_sequence"], e["topic"])
            wall_ns = e["wall_ns"]
            existing = recv_first_arrival[key].get(receiver_ep)
            if existing is None or wall_ns < existing:
                recv_first_arrival[key][receiver_ep] = wall_ns

    round_count_histogram = defaultdict(int)  # num_retransmission_rounds -> count of identities
    max_rounds = 0
    max_rounds_identity = None
    loop_instances = []  # (robot_id, source_id, sequence, topic, target, first_success_round, rounds detail)

    for sender_ep, d in per_endpoint.items():
        diag = d.get("fleetqox_stream_identity_diagnostics", {})
        sender_robot_id = diag.get("effective_robot_id", "")
        send_events = d["fleetqox_loss_funnel_trace"]["send"]
        by_identity: dict[tuple, list[dict]] = defaultdict(list)
        for e in send_events:
            key = (e["source_id"], e["source_sequence"], e["topic"])
            by_identity[key].append(e)

        for (source_id, sequence, topic), events in by_identity.items():
            events.sort(key=lambda e: e["wall_ns"])
            originals = [e for e in events if not e["is_retransmission"]]
            retransmits = [e for e in events if e["is_retransmission"]]

            # Cluster retransmission events into rounds by wall_ns gap.
            rounds: list[list[dict]] = []
            for e in retransmits:
                if rounds and e["wall_ns"] - rounds[-1][-1]["wall_ns"] <= ROUND_CLUSTER_GAP_NS:
                    rounds[-1].append(e)
                else:
                    rounds.append([e])

            num_rounds = len(rounds)
            if num_rounds > 3:
                round_count_histogram[">3"] += 1
            else:
                round_count_histogram[str(num_rounds)] += 1
            if num_rounds > max_rounds:
                max_rounds = num_rounds
                max_rounds_identity = {
                    "robot_id": sender_robot_id, "source_id": source_id,
                    "source_sequence": sequence, "topic": topic, "num_rounds": num_rounds,
                }

            if num_rounds == 0:
                continue

            # All rounds including round 0 (original), each as
            # (round_index, wall_ns_start, wall_ns_end_exclusive, targets_sent_to).
            all_rounds = [originals] + rounds if originals else rounds
            round_index_offset = 0 if originals else 1  # if no original captured, round 1 is first retransmission
            round_windows = []
            for idx, round_events in enumerate(all_rounds):
                round_index = idx if originals else idx + 1
                start_ns = min(e["wall_ns"] for e in round_events)
                end_ns = (
                    min(e["wall_ns"] for e in all_rounds[idx + 1])
                    if idx + 1 < len(all_rounds) else start_ns + ARRIVAL_WINDOW_NS
                )
                targets = {target_ip_from_target_field(e["target"]) for e in round_events}
                round_windows.append((round_index, start_ns, end_ns, targets, round_events))

            recv_key_prefix = (sender_robot_id, source_id, sequence, topic)
            arrivals = recv_first_arrival.get(recv_key_prefix, {})

            for target_ip in {ip for (_, _, _, targets, _) in round_windows for ip in targets}:
                target_ep = IP_TO_EP.get(target_ip)
                if target_ep is None or target_ep == sender_ep:
                    continue
                first_arrival_ns = arrivals.get(target_ep)
                first_success_round = None
                for round_index, start_ns, end_ns, targets, round_events in round_windows:
                    if target_ip not in targets:
                        continue
                    if first_arrival_ns is not None and start_ns <= first_arrival_ns:
                        if first_arrival_ns < end_ns or round_index == round_windows[-1][0]:
                            first_success_round = round_index
                            break
                if first_success_round is not None and first_success_round >= 2:
                    loop_instances.append({
                        "robot_id": sender_robot_id, "source_id": source_id,
                        "source_sequence": sequence, "topic": topic,
                        "target_endpoint": target_ep, "first_success_round": first_success_round,
                        "first_arrival_wall_ns": first_arrival_ns,
                        "rounds": [
                            {"round": ri, "wall_ns_start": s, "wall_ns_end": en}
                            for ri, s, en, _, _ in round_windows
                        ],
                    })

    print("round_count_histogram:", dict(round_count_histogram), flush=True)
    print("max_rounds:", max_rounds, "identity:", max_rounds_identity, flush=True)
    print("loop_instances found:", len(loop_instances), flush=True)

    out = {
        "round_count_histogram": dict(round_count_histogram),
        "max_rounds": max_rounds,
        "max_rounds_identity": max_rounds_identity,
        "loop_instances": loop_instances[:20],
        "loop_instances_total": len(loop_instances),
    }
    out_path = output_dir.parent / "feedback_loop_findings.json"
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
