"""Table VI N=8 seed=7 POST-RECVFROM DUPLICATE-DROP message-level
correlation (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI N=8
POST-RECVFROM DUPLICATE-DROP LOCALIZATION"). Robot IDs are already
unique (commit 977f777) -- this investigates why frames that DO reach
recvfrom() and DO decode successfully are still being rejected by
observe_frame()'s duplicate check (matched_subscriptions == 0 in the
subscription_match trace, per the already-established finding that for
this scenario matched_subscriptions == 0 is 100% attributable to the
duplicate flag, not a topic/domain/type/partition mismatch).

Uses the SubscriptionMatchTraceEvent.robot_id/payload_text fields added
in this same change (rmw_pubsub.cpp) -- previously the trace only
carried publisher_id (source_id), which is textually IDENTICAL across
every sender in a run by construction, so a dropped event's ACTUAL
sender could not be determined from the trace alone.

READ-ONLY measurement pass: runs the existing, unmodified FleetRMW N=8
coordination probe once (seed=7) with the SAME already-existing,
opt-in FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING=1 env var used by every
prior loss-funnel investigation in this project -- this does not
change send/retry/ACK/NACK/QoS/timeout/duplicate-detection behavior,
it only makes already-computed events visible. Does not touch robot
IDs, broadcast, ns-3, or Ricart-Agrawala.
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
OUTPUT_ROOT = ROOT / "results_rmw_socket" / "table6_n8_duplicate_drop_investigation"


def identity_key(event: dict[str, Any]) -> tuple:
    return (event["robot_id"], event["frame_topic"], event["source_id"], event["source_sequence"])


def classify_receiver(endpoint: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """events: this ONE receiving endpoint's own subscription_match trace,
    in the order the C++ process appended them (== wall_ns order already,
    since g_subscription_match_trace_events is appended-to under a mutex
    in receive order -- confirmed by wall_ns being monotonic non-decreasing
    per endpoint's own trace in every run inspected)."""
    first_accepted: dict[tuple, dict[str, Any]] = {}
    findings = []
    for event in events:
        key = identity_key(event)
        if event["matched_subscriptions"] >= 1:
            if key not in first_accepted:
                first_accepted[key] = event
            continue
        # matched_subscriptions == 0 -> dropped.
        prior = first_accepted.get(key)
        if prior is None:
            findings.append({
                "receiver": endpoint,
                "dropped_event": event,
                "prior_accepted_event": None,
                "classification": "D",
                "reason": "no earlier accepted event with the same "
                          "(robot_id, topic, publisher_id, sequence) identity "
                          "exists in this endpoint's own trace",
            })
            continue
        payload_equal = prior["payload_hex"] == event["payload_hex"]
        inter_arrival_ns = event["wall_ns"] - prior["wall_ns"]
        if payload_equal:
            classification = "A"
            reason = ("same identity, byte-identical payload -- the same "
                      "logical message physically delivered more than once "
                      "(consistent with the already-proven network-level "
                      "physical packet duplication finding); "
                      "observe_frame()'s duplicate check correctly rejected "
                      "a genuine repeat")
        else:
            classification = "C"
            reason = ("same (robot_id, publisher_id, sequence) identity but "
                      "DIFFERENT payload content -- this robot's own "
                      "publisher reused a source_sequence value for two "
                      "logically different messages")
        findings.append({
            "receiver": endpoint,
            "dropped_event": event,
            "prior_accepted_event": prior,
            "payload_equal": payload_equal,
            "inter_arrival_ns": inter_arrival_ns,
            "classification": classification,
            "reason": reason,
        })
    return findings


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    output_dir = OUTPUT_ROOT / f"fleetrmw_n{NUM_ROBOTS}_seed{SEED}"
    print(f"=== FleetRMW N={NUM_ROBOTS} seed={SEED} (loss-funnel trace profiling) ===", flush=True)
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

    endpoints = endpoint_list(NUM_ROBOTS)
    results_dir = output_dir / "container_results"

    all_findings: list[dict[str, Any]] = []
    total_dropped = 0
    for i, endpoint in enumerate(endpoints):
        p = results_dir / f"result_{i}.json"
        d = json.loads(p.read_text())
        sm = d["fleetqox_loss_funnel_trace"]["subscription_match"]
        dropped = [e for e in sm if e["matched_subscriptions"] == 0]
        total_dropped += len(dropped)
        findings = classify_receiver(endpoint, sm)
        all_findings.extend(findings)
        print(f"  {endpoint}: {len(sm)} subscription_match events, {len(dropped)} dropped", flush=True)

    counts = defaultdict(int)
    for f in all_findings:
        counts[f["classification"]] += 1

    out_path = OUTPUT_ROOT / "duplicate_drop_findings.json"
    out_path.write_text(json.dumps({
        "num_robots": NUM_ROBOTS,
        "seed": SEED,
        "total_dropped": total_dropped,
        "classification_counts": dict(counts),
        "findings": all_findings,
    }, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)
    print(f"total_dropped={total_dropped} classification_counts={dict(counts)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
