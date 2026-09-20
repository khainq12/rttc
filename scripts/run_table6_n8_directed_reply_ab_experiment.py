"""Table VI N=8 -- OLD broadcast-REPLY vs NEW directed-REPLY, paired/
counterbalanced A/B (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI
DIRECTED REPLY").

Reuses the SAME loss-funnel-trace instrumentation
(FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING) already used throughout this
investigation's N=8 ACK/NACK causal experiments
(run_table6_n8_acknack_redundancy_sweep_phase4.py and friends) -- no new
C++, no new tracing mechanism. Only the harness-level toggle changes:
--directed-reply (per-target REPLY topics + subscription_aware peer
policy + static subscriptions) vs the default (shared broadcast topic).

Per condition per seed, measures (all from data already exposed to
Python, nothing inferred):
  - REPLY intended / unintended deliveries (raw_received_log) -- the
    thing directed-reply is DESIGNED to change.
  - DATA send/recv/retransmission counts (loss_funnel_trace send/recv)
    -- "does ACK/NACK load falling (if it does) show up as improved DATA
    delivery" needs this independent of the ACK/NACK counts themselves.
  - ACK/NACK "load" = count of outgoing_ack_nack + incoming_ack_nack
    trace events (a message-level proxy for wire ACK/NACK volume; the
    per-message wire-redundancy multiplier is a constant config value
    unchanged between conditions here, so this proxy is monotonic with
    real wire ACK/NACK packet count). Literal byte-level pcap
    composition (DATA/ACK/NACK/UNRECOVERABLE bytes) is NOT recaptured
    here -- that was already measured once, directly, for the default
    broadcast-REPLY condition in the overnight investigation ("TABLE VI
    N=8 TRAFFIC COMPOSITION AND ACK/NACK CORRELATION"); repeating a full
    tcpdump capture matrix for this A/B was judged out of proportion to
    "start small, expand only if signal is useful" and is left as a
    follow-up if this A/B's signal warrants it.
  - permanent loss: (source_id, sequence, topic, target) pairs that are
    SENT (recorded in an endpoint's send events) but never appear in any
    OTHER endpoint's recv events for that same (robot_id, source_id,
    sequence, topic) key -- same method as
    investigate_table6_n8_permanent_loss_cause.py's own delivery check,
    simplified to a count rather than a root-cause classification (out
    of scope here).
  - crossings_completed / forced_entry / task_completion_s per endpoint.

Counterbalanced by seed: seed 7 runs OLD-then-NEW, seed 13 runs
NEW-then-OLD, to cancel any systematic run-order drift (e.g. host
load/thermal/docker-cache warmup) that a fixed OLD-always-first order
could otherwise confound with the treatment itself.
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
ENDPOINTS = endpoint_list(NUM_ROBOTS)
# (seed, [order of directed_reply values to run]) -- counterbalanced.
SEED_PLAN = [(7, [False, True]), (13, [True, False])]


def run_one(seed: int, directed_reply: bool) -> dict[str, Any]:
    label = "NEW_directed" if directed_reply else "OLD_broadcast"
    output_dir = (
        ROOT / "results_rmw_socket" / "table6_n8_directed_reply_ab"
        / f"fleetrmw_n{NUM_ROBOTS}_seed{seed}_{label}"
    )
    print(f"=== N=8 seed={seed} RUN {label} (directed_reply={directed_reply}) ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=NUM_ROBOTS,
        seed=seed,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        extra_rmw_env={"FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1"},
        directed_reply=directed_reply,
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        return {"seed": seed, "label": label, "status": result["status"], "error": result["error"]}

    results_dir = output_dir / "container_results"
    per_endpoint: dict[str, Any] = {}
    for i, ep in enumerate(ENDPOINTS):
        per_endpoint[ep] = json.loads((results_dir / f"result_{i}.json").read_text())

    # REPLY intended/unintended fan-out.
    reply_intended = 0
    reply_unintended = 0
    for ep, d in per_endpoint.items():
        for x in d["raw_received_log"]:
            if x["type"] == "reply":
                if x["to"] == ep:
                    reply_intended += 1
                else:
                    reply_unintended += 1

    # DATA send/recv/retransmission counts.
    data_sent_original = 0
    data_sent_retransmit = 0
    data_recv = 0
    ack_nack_outgoing_events = 0
    ack_nack_incoming_events = 0
    for ep, d in per_endpoint.items():
        trace = d.get("fleetqox_loss_funnel_trace", {})
        for e in trace.get("send", []):
            if e["is_retransmission"]:
                data_sent_retransmit += 1
            else:
                data_sent_original += 1
        data_recv += len(trace.get("recv", []))
        ack_nack_outgoing_events += len(trace.get("outgoing_ack_nack", []))
        ack_nack_incoming_events += len(trace.get("incoming_ack_nack", []))

    # Permanent loss: (robot_id, source_id, sequence, topic) sent by
    # someone, never seen in ANY other endpoint's recv events.
    sent_identities: set[tuple] = set()
    recv_identities: set[tuple] = set()
    total_send_target_pairs = 0
    for ep, d in per_endpoint.items():
        diag = d.get("fleetqox_stream_identity_diagnostics", {})
        sender_robot_id = diag.get("effective_robot_id", "")
        trace = d.get("fleetqox_loss_funnel_trace", {})
        seen_pairs = set()
        for e in trace.get("send", []):
            seen_pairs.add((sender_robot_id, e["source_id"], e["source_sequence"], e["topic"]))
        sent_identities |= seen_pairs
        total_send_target_pairs += len(seen_pairs)
    for ep, d in per_endpoint.items():
        trace = d.get("fleetqox_loss_funnel_trace", {})
        for e in trace.get("recv", []):
            recv_identities.add((e["robot_id"], e["source_id"], e["source_sequence"], e["topic"]))
    never_delivered = sent_identities - recv_identities
    permanent_loss_count = len(never_delivered)
    permanent_loss_pct = (
        round(100 * permanent_loss_count / len(sent_identities), 2) if sent_identities else None
    )

    crossings_completed = {ep: d["num_crossings_completed"] for ep, d in per_endpoint.items()}
    forced_entry = {ep: any(c["forced_entry"] for c in d["crossings"]) for ep, d in per_endpoint.items()}
    task_completion_s = {ep: d["task_completion_s"] for ep, d in per_endpoint.items()}

    out = {
        "seed": seed, "label": label, "status": "ok", "directed_reply": directed_reply,
        "reply_intended": reply_intended, "reply_unintended": reply_unintended,
        "data_sent_original": data_sent_original, "data_sent_retransmit": data_sent_retransmit,
        "data_recv": data_recv,
        "ack_nack_outgoing_events": ack_nack_outgoing_events,
        "ack_nack_incoming_events": ack_nack_incoming_events,
        "unique_data_identities_sent": len(sent_identities),
        "permanent_loss_count": permanent_loss_count,
        "permanent_loss_pct": permanent_loss_pct,
        "crossings_completed": crossings_completed,
        "forced_entry": forced_entry,
        "any_forced_entry": any(forced_entry.values()),
        "all_5_of_5_crossings": all(v == 5 for v in crossings_completed.values()),
        "task_completion_s_mean": round(sum(task_completion_s.values()) / len(task_completion_s), 2),
        "task_completion_s": task_completion_s,
    }
    print(json.dumps({k: v for k, v in out.items() if k not in ("crossings_completed", "forced_entry", "task_completion_s")}, indent=2), flush=True)
    return out


def main() -> int:
    all_runs: list[dict[str, Any]] = []
    for seed, order in SEED_PLAN:
        for directed_reply in order:
            all_runs.append(run_one(seed, directed_reply))

    out_path = ROOT / "results_rmw_socket" / "table6_n8_directed_reply_ab" / "ab_findings.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_runs, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)

    print("\n=== OLD vs NEW SUMMARY (per seed) ===", flush=True)
    by_seed: dict[int, dict[str, dict]] = defaultdict(dict)
    for r in all_runs:
        by_seed[r["seed"]][r["label"]] = r
    for seed in sorted(by_seed):
        old = by_seed[seed].get("OLD_broadcast")
        new = by_seed[seed].get("NEW_directed")
        if not old or not new or old.get("status") != "ok" or new.get("status") != "ok":
            print(f"seed={seed}: incomplete/failed run(s)", flush=True)
            continue
        print(
            f"seed={seed}: "
            f"reply_unintended {old['reply_unintended']}->{new['reply_unintended']} | "
            f"ack_nack_events {old['ack_nack_outgoing_events']+old['ack_nack_incoming_events']}"
            f"->{new['ack_nack_outgoing_events']+new['ack_nack_incoming_events']} | "
            f"permanent_loss_pct {old['permanent_loss_pct']}->{new['permanent_loss_pct']} | "
            f"forced_entry {old['any_forced_entry']}->{new['any_forced_entry']} | "
            f"task_completion_s_mean {old['task_completion_s_mean']}->{new['task_completion_s_mean']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
