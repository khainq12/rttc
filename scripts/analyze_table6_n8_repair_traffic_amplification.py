"""Table VI PHASE 3 -- repair-traffic feedback/amplification
quantification at N=8 (see docs/AUDIT_ACCEPTANCE_TRACKING.md).

READ-ONLY re-analysis of the ALREADY-COLLECTED, Phase-1-fixed N=8
seed=7 trace data -- no rerun, no new pcap capture (reuses the
already-established byte-level traffic composition from the earlier
"TABLE VI N=8 TRAFFIC COMPOSITION" section as pre-fix context; that
capture is not repeated here per the overnight time-management
priority to reuse existing captures before running anything new).

Computes, using exact per-(identity, target) frame-identity
correlation (not aggregate counts):
  - total unique DATA (identity, target) pairs attempted
  - total retransmission SEND events (per-target, across all rounds)
  - how retransmission volume splits between pairs that were
    eventually delivered vs pairs that were never delivered
  - average retransmission attempts per pair that succeeded WITHOUT
    ever needing a retry (round 0 only)
  - average retransmission attempts per pair that succeeded ONLY via
    retry (>=1 retransmission before delivery)
  - average retransmission attempts per pair that was never delivered
    at all
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
    IP_TO_EP,
    target_ip_from_target_field,
)
from scripts.run_ns3_docker_container_fleet_probe import endpoint_list  # noqa: E402

NUM_ROBOTS = 8
ENDPOINTS = endpoint_list(NUM_ROBOTS)
RUN_DIR = ROOT / "results_rmw_socket" / "table6_n8_permanent_loss_cause" / "fleetrmw_n8_seed7"


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

    buckets = {
        "delivered_round0_only": {"pairs": 0, "retransmits": 0},
        "delivered_via_retry": {"pairs": 0, "retransmits": 0},
        "never_delivered": {"pairs": 0, "retransmits": 0},
    }
    total_pairs = 0

    for sender_ep, d in per_endpoint.items():
        diag = d.get("fleetqox_stream_identity_diagnostics", {})
        sender_robot_id = diag.get("effective_robot_id", "")
        send_events = d["fleetqox_loss_funnel_trace"]["send"]
        by_identity: dict[tuple, list[dict]] = defaultdict(list)
        for e in send_events:
            by_identity[(e["source_id"], e["source_sequence"], e["topic"])].append(e)

        for (source_id, sequence, topic), events in by_identity.items():
            retransmits = [e for e in events if e["is_retransmission"]]
            recv_key = (sender_robot_id, source_id, sequence, topic)
            arrivals = recv_first_arrival.get(recv_key, {})
            all_targets = {target_ip_from_target_field(e["target"]) for e in events}
            for target_ip in all_targets:
                target_ep = IP_TO_EP.get(target_ip)
                if target_ep is None or target_ep == sender_ep:
                    continue
                total_pairs += 1
                pair_retransmits = sum(
                    1 for e in retransmits if target_ip_from_target_field(e["target"]) == target_ip
                )
                delivered = arrivals.get(target_ep) is not None
                if delivered and pair_retransmits == 0:
                    bucket = "delivered_round0_only"
                elif delivered:
                    bucket = "delivered_via_retry"
                else:
                    bucket = "never_delivered"
                buckets[bucket]["pairs"] += 1
                buckets[bucket]["retransmits"] += pair_retransmits

    total_retransmits = sum(b["retransmits"] for b in buckets.values())
    print(f"total (identity, target) pairs: {total_pairs}", flush=True)
    print(f"total retransmission send events: {total_retransmits}", flush=True)
    print(flush=True)
    for name, b in buckets.items():
        avg = round(b["retransmits"] / b["pairs"], 3) if b["pairs"] else None
        pct_of_retransmits = round(100 * b["retransmits"] / total_retransmits, 1) if total_retransmits else None
        print(f"  {name}: pairs={b['pairs']}, retransmits={b['retransmits']} "
              f"({pct_of_retransmits}% of all retransmission volume), avg/pair={avg}", flush=True)

    out = {"buckets": buckets, "total_pairs": total_pairs, "total_retransmits": total_retransmits}
    out_path = RUN_DIR.parent / "repair_traffic_amplification_findings.json"
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
