"""Table VI post-readiness root-cause investigation -- QUESTION B ONLY
(see docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI POST-READINESS
ROOT-CAUSE INVESTIGATION"): why does FleetRMW pass readiness 9/9 but
still get forced_entry_rate: 1.0 on essentially every crossing?

Reads back the 4 container_results/result_i.json files from a single
small (N=4, one fixed seed) FleetRMW coordination-probe run and joins
them into a full per-message REQUEST/REPLY funnel, using the NEW
sent_log/raw_received_log[wall_ns/recv_wall_ns] fields added to
fleetqox_coordination_endpoint.py for this investigation. For every
forced-entry crossing and every required peer, classifies exactly what
happened to that (sender, peer, req_id) pair using ONLY what the joined
send/receive logs can prove -- "insufficient_evidence" rather than a
guess wherever the logs don't pin it down (e.g. any run with
publish_failures>0 for the sender, since sent_log cannot currently
distinguish a successful hand-off to the RMW layer from an OS-level
send failure -- see send_reply()/the request-send site's comments).

READ-ONLY analysis: does not run anything, does not change the harness.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import endpoint_list  # noqa: E402


def load_run(output_dir: Path, num_robots: int) -> dict[str, Any]:
    endpoints = endpoint_list(num_robots)
    results_dir = output_dir / "container_results"
    out: dict[str, Any] = {}
    for i, endpoint in enumerate(endpoints):
        p = results_dir / f"result_{i}.json"
        out[endpoint] = json.loads(p.read_text()) if p.exists() else None
    return out


def peer_received_request(peer_result: dict, sender: str, req_id: str) -> dict | None:
    for entry in peer_result.get("raw_received_log", []):
        if entry.get("type") == "request" and entry.get("from") == sender and entry.get("req_id") == req_id:
            return entry
    return None


def peer_sent_reply(peer_result: dict, to: str, req_id: str) -> dict | None:
    for entry in peer_result.get("sent_log", []):
        if entry.get("type") == "reply" and entry.get("to") == to and entry.get("req_id") == req_id:
            return entry
    return None


def sender_received_reply(sender_result: dict, frm: str, req_id: str) -> dict | None:
    for entry in sender_result.get("raw_received_log", []):
        if entry.get("type") == "reply" and entry.get("from") == frm and entry.get("req_id") == req_id:
            return entry
    return None


def classify_pair(
    *,
    sender: str,
    peer: str,
    req_id: str,
    crossing: dict,
    all_results: dict[str, Any],
) -> dict[str, Any]:
    sender_result = all_results[sender]
    peer_result = all_results[peer]
    sender_publish_failures = sender_result["debug_counters"]["publish_failures"]
    peer_publish_failures = peer_result["debug_counters"]["publish_failures"]

    req_recv = peer_received_request(peer_result, sender, req_id)
    if req_recv is None:
        if sender_publish_failures > 0:
            return {"category": "insufficient_evidence", "reason": "sender had publish_failures>0; cannot rule out send-side failure for this specific message"}
        return {"category": "request_sent_not_received", "reason": "peer's raw_received_log has no matching request from sender for this req_id (any retry)"}

    reply_sent = peer_sent_reply(peer_result, sender, req_id)
    if reply_sent is None:
        return {
            "category": "received_not_called_back",
            "reason": "peer received the request (recv_wall_ns present) but peer's own sent_log has no reply to sender for this req_id",
            "peer_recv_wall_ns": req_recv.get("recv_wall_ns"),
        }

    if peer_publish_failures > 0:
        reply_received = sender_received_reply(sender_result, peer, req_id)
        if reply_received is None:
            return {"category": "insufficient_evidence", "reason": "peer had publish_failures>0 and sender never saw the reply; cannot rule out send-side failure for this specific reply"}

    reply_received = sender_received_reply(sender_result, peer, req_id)
    if reply_received is None:
        return {
            "category": "reply_sent_not_received",
            "reason": "peer's sent_log shows a reply was sent to sender, but sender's raw_received_log has no matching entry",
            "peer_reply_sent_wall_ns": reply_sent.get("wall_ns"),
        }

    entered_wall_ns = crossing.get("entered_wall_ns")
    recv_wall_ns = reply_received.get("recv_wall_ns")
    if crossing.get("forced_entry") and entered_wall_ns is not None and recv_wall_ns is not None:
        if recv_wall_ns > entered_wall_ns:
            return {
                "category": "reply_received_late",
                "reason": "reply physically arrived at sender AFTER sender had already forced entry for this crossing",
                "late_by_ms": (recv_wall_ns - entered_wall_ns) / 1e6,
            }
        return {
            "category": "reply_received_before_forced_entry_unexplained",
            "reason": "reply arrived before forced entry was declared, yet crossing was still marked forced_entry -- needs on_reply()/debug_counters cross-check, not classifiable from these logs alone",
        }
    return {"category": "reply_received_in_time", "reason": "not a forced-entry crossing for this pair"}


def analyze(output_dir: Path, num_robots: int) -> dict[str, Any]:
    all_results = load_run(output_dir, num_robots)
    endpoints = endpoint_list(num_robots)
    missing = [e for e in endpoints if all_results.get(e) is None]
    if missing:
        return {"error": f"missing result files for endpoints: {missing}"}

    category_counts: dict[str, int] = {}
    per_crossing_detail: list[dict[str, Any]] = []
    total_crossings = 0
    forced_crossings = 0

    for sender in endpoints:
        sender_result = all_results[sender]
        for crossing in sender_result.get("crossings", []):
            total_crossings += 1
            if not crossing.get("forced_entry"):
                continue
            forced_crossings += 1
            req_id = f"{sender}:{crossing['crossing_index']}"
            pair_results = {}
            for peer in endpoints:
                if peer == sender:
                    continue
                verdict = classify_pair(
                    sender=sender, peer=peer, req_id=req_id, crossing=crossing, all_results=all_results,
                )
                pair_results[peer] = verdict
                category_counts[verdict["category"]] = category_counts.get(verdict["category"], 0) + 1
            per_crossing_detail.append(
                {
                    "sender": sender,
                    "crossing_index": crossing["crossing_index"],
                    "req_id": req_id,
                    "retries": crossing["retries"],
                    "per_peer": pair_results,
                }
            )

    debug_counters_by_endpoint = {e: all_results[e]["debug_counters"] for e in endpoints}
    publish_failures_total = sum(dc["publish_failures"] for dc in debug_counters_by_endpoint.values())

    return {
        "num_robots": num_robots,
        "endpoints": endpoints,
        "total_crossings": total_crossings,
        "forced_crossings": forced_crossings,
        "category_counts": category_counts,
        "publish_failures_total": publish_failures_total,
        "debug_counters_by_endpoint": debug_counters_by_endpoint,
        "per_crossing_detail": per_crossing_detail,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--num-robots", type=int, required=True)
    parser.add_argument("--out-json", type=Path, default=None)
    args = parser.parse_args()

    report = analyze(args.output_dir, args.num_robots)
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(text, encoding="utf-8")
