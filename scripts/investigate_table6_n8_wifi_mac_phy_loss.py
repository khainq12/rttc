"""Table VI N=8 seed=7, directed-reply ON -- WHY does DATA still get lost
inside the ns-3 Wi-Fi path after REPLY fan-out was fixed? (See
docs/AUDIT_ACCEPTANCE_TRACKING.md, "N=8 REMAINING WI-FI LOSS AFTER
DIRECTED-REPLY", whose "exactly ONE next step" this implements.)

Frozen config (per this investigation's own instruction -- everything
else stays exactly as the directed-REPLY A/B experiment already used
it): --directed-reply enabled, N=8, seed=7, default scenario_timeout_s
=120, default network profile (circle layout, 7.5m radius, path-loss
exponent 2.7, 15dBm tx, -82dBm rx sensitivity), no ns-3/QoS/timeout
changes.

GROUND TRUTH FOR "DELIVERED": the app/RMW-layer sent_log/raw_received_log
(already used throughout this whole investigation, e.g. the directed-
REPLY A/B experiment's own permanent-loss measurement) -- NOT "any MAC
rx observed anywhere" (a first version of this script used that and got
553/553 = 100% "delivered", contradicting the ~38-43% permanent-loss
number from the A/B experiment; root cause: this topology is
STA-AP-STA -- infrastructure-mode Wi-Fi ALWAYS relays inter-station
traffic through the single AP, so an uplink-only MacRx AT THE AP counted
as "delivered" even when the AP never got the frame back out to the
real destination). This version instead:
  1. builds ground truth per (wall_ns, intended_recipient) pair from
     sent_log (what was actually sent, and to whom) x raw_received_log
     (what each endpoint's OWN app layer actually decoded) -- the exact
     same method the A/B experiment's permanent-loss number came from;
  2. for undelivered pairs, walks the ns-3 MAC_EVENT timeline (now
     labeled with "who") along the ONLY possible path in a single-AP
     topology -- sender -> AP (uplink) -> recipient (downlink) -- to
     find the first hop that never completes, and looks for an explicit
     drop/expiry/retry-limit event at that hop for the SAME event_id.

Every DATA identity sent must land in exactly one bucket per intended
recipient: delivered, uplink-lost (A/B/C/D-classified), downlink-lost
(A/B/C/D-classified), or unknown (E) -- printed counts must sum to the
total (wall_ns, recipient) pairs.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# fleetqox_trace_replay_tap.cc's PrintWifiStats() always appends a "," after
# each phy_rx_drop_by_reason entry, including the last one, leaving a
# trailing comma before the closing "]" -- invalid JSON (pre-existing in
# this diagnostic printer, unrelated to this investigation's own new
# fields; confirmed by direct inspection of the captured log). Tolerated
# here on the Python side rather than touching the C++ printer again for
# a purely cosmetic formatting issue.
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")

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


def parse_lines(ns3_log: str, prefix: str) -> list[dict[str, Any]]:
    out = []
    for line in ns3_log.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            out.append(json.loads(_TRAILING_COMMA_RE.sub(r"\1", line[len(prefix):])))
        except json.JSONDecodeError:
            continue
    return out


def is_ap(who: str) -> bool:
    return who.startswith("AP")


def classify_hop_failure(events: list[dict[str, Any]], hop_device: str) -> tuple[str, dict | None]:
    """events: all MAC_EVENT entries for one event_id, already time-sorted.
    hop_device: the "who" whose outbound side is being examined (the
    device that should have transmitted onward but the recipient never
    saw it arrive). Returns (bucket, evidence_event)."""
    at_device = [e for e in events if e["who"] == hop_device]
    for e in at_device:
        kind = e["kind"]
        if kind in ("mac_tx_drop", "dropped_mpdu:failed_enqueue"):
            return "A_explicit_drop", e
        if kind in ("dropped_mpdu:expired_lifetime", "queue_expired"):
            return "B_queue_backlog_expired", e
        if kind == "dropped_mpdu:reached_retry_limit" or kind.startswith("phy_rx_drop:"):
            return "C_phy_mac_tx_failure", e
        if kind == "mac_rx_drop":
            return "A_explicit_drop", e
    return "E_unknown", None


def main() -> int:
    output_dir = ROOT / "results_rmw_socket" / "table6_n8_wifi_mac_phy_loss" / f"fleetrmw_n{NUM_ROBOTS}_seed{SEED}"
    ns3_log_path = output_dir.parent / f"ns3_seed{SEED}.log"
    results_dir = output_dir / "container_results"

    # Reuse the already-captured run from this same investigation's first
    # pass (RED-flagged "any rx anywhere" classification bug fixed below,
    # not a reason to burn another ~3 minutes of N=8/120s simulation --
    # per this investigation's own "no unnecessary reruns when existing
    # captures answer the question" rule) if present on disk; otherwise
    # run fresh.
    if ns3_log_path.exists() and results_dir.exists():
        print(f"=== Reusing existing capture at {output_dir} ===", flush=True)
        ns3_log = ns3_log_path.read_text(encoding="utf-8", errors="replace")
    else:
        print(f"=== FleetRMW N={NUM_ROBOTS} seed={SEED} directed_reply=True (Wi-Fi MAC/PHY loss trace) ===", flush=True)
        result = run_coordination_probe(
            image=DEFAULT_IMAGE,
            output_dir=output_dir,
            num_robots=NUM_ROBOTS,
            seed=SEED,
            rmw_implementation="rmw_fleetqox_cpp",
            discovery_mode="default",
            extra_rmw_env={"FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1"},
            directed_reply=True,
        )
        print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
        if result["status"] != "ok":
            print("run did not complete ok -- stopping", flush=True)
            return 1
        ns3_log = result.get("ns3_log", "") or ""
        ns3_log_path.write_text(ns3_log, encoding="utf-8")
    per_endpoint: dict[str, Any] = {}
    for i, ep in enumerate(ENDPOINTS):
        per_endpoint[ep] = json.loads((results_dir / f"result_{i}.json").read_text())

    mac_events = parse_lines(ns3_log, "FLEETQOX_MAC_EVENT ")
    wifi_stats_lines = parse_lines(ns3_log, "FLEETQOX_WIFI_STATS ")
    backlog_lines = parse_lines(ns3_log, "FLEETQOX_QUEUE_BACKLOG ")
    print(f"total FLEETQOX_MAC_EVENT lines: {len(mac_events)}", flush=True)
    print(f"total FLEETQOX_WIFI_STATS lines: {len(wifi_stats_lines)}", flush=True)
    print(f"total FLEETQOX_QUEUE_BACKLOG lines: {len(backlog_lines)}", flush=True)
    last_stats = wifi_stats_lines[-1] if wifi_stats_lines else {}
    print("last FLEETQOX_WIFI_STATS:", json.dumps(last_stats), flush=True)

    max_backlog_by_who: dict[str, int] = defaultdict(int)
    for line in backlog_lines:
        for who, depth in line.get("depths", []):
            max_backlog_by_who[who] = max(max_backlog_by_who[who], depth)
    print("max observed queue backlog per station:", dict(max_backlog_by_who), flush=True)

    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in mac_events:
        by_event[e["event_id"]].append(e)
    for v in by_event.values():
        v.sort(key=lambda e: e["wall_ns"])

    # ---- Ground truth: (wall_ns, sender, type, intended_recipients) ----
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

    # Ground truth deliveries: (wall_ns, recipient) actually decoded at
    # the RMW/app layer -- from each endpoint's OWN raw_received_log.
    delivered_pairs: set[tuple] = set()
    for ep, d in per_endpoint.items():
        for r in d.get("raw_received_log", []):
            wall_ns = r.get("wall_ns")
            if wall_ns is not None:
                delivered_pairs.add((wall_ns, ep))

    total_pairs = 0
    delivered_count = 0
    classification_counts: dict[str, int] = defaultdict(int)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for wall_ns, info in sent_index.items():
        sender = info["sender"]
        for recipient in info["intended"]:
            total_pairs += 1
            if (wall_ns, recipient) in delivered_pairs:
                delivered_count += 1
                continue

            event_id = f"wallns:{wall_ns}"
            events = by_event.get(event_id, [])
            ap_events = [e for e in events if is_ap(e["who"])]
            sender_tx = [e for e in events if e["who"] == sender and e["kind"] == "tx"]
            ap_rx = [e for e in ap_events if e["kind"] == "rx"]
            ap_tx = [e for e in ap_events if e["kind"] == "tx"]
            recipient_rx = [e for e in events if e["who"] == recipient and e["kind"] == "rx"]

            if not sender_tx:
                # Never even seen leaving the sender's own device at the
                # ns-3 level -- either extraction missed this specific
                # physical copy (unicast fan-out means N-1 near-identical
                # copies share one event_id; ns-3-side correlation can
                # only prove SOME copy was seen, not every one) or a
                # genuinely unaccounted case.
                bucket = "E_unknown_no_sender_tx_observed"
            elif recipient_rx:
                # ns-3 says the recipient's OWN device DID receive this
                # exact frame at the MAC layer, yet the RMW/app layer
                # never recorded it -- loss happens ABOVE the MAC (RMW
                # decode/dedup/queueing), not in ns-3's Wi-Fi model.
                bucket = "above_mac_rmw_layer_loss"
            elif not ap_rx:
                bucket, evidence = classify_hop_failure(events, sender)
                bucket = f"uplink_{bucket}"
            elif not ap_tx:
                bucket, evidence = classify_hop_failure(events, ap_events[0]["who"] if ap_events else "AP0")
                bucket = f"downlink_ap_internal_{bucket}"
            else:
                bucket, evidence = classify_hop_failure(events, recipient)
                bucket = f"downlink_{bucket}"

            classification_counts[bucket] += 1
            if len(examples[bucket]) < 3:
                examples[bucket].append(
                    {
                        "wall_ns": wall_ns, "sender": sender, "recipient": recipient,
                        "type": info["type"], "events": events,
                    }
                )

    print("\n=== FIRST-LOSS-POINT CLASSIFICATION (per intended (message, recipient) pair) ===", flush=True)
    print(f"total (message, intended_recipient) pairs: {total_pairs}", flush=True)
    print(f"delivered (RMW ground truth): {delivered_count} ({round(100*delivered_count/total_pairs, 2)}%)", flush=True)
    undelivered_total = total_pairs - delivered_count
    for k in sorted(classification_counts):
        pct_of_undelivered = round(100 * classification_counts[k] / undelivered_total, 2) if undelivered_total else None
        print(f"  {k}: {classification_counts[k]} ({pct_of_undelivered}% of undelivered)", flush=True)
    accounted = delivered_count + sum(classification_counts.values())
    print(f"accounted: {accounted} / {total_pairs} (should be equal)", flush=True)

    out = {
        "status": "ok",
        "total_mac_events": len(mac_events),
        "total_pairs": total_pairs,
        "delivered_count": delivered_count,
        "classification_counts": dict(classification_counts),
        "accounted_total": accounted,
        "last_wifi_stats": last_stats,
        "max_backlog_by_who": dict(max_backlog_by_who),
        "examples": {k: v for k, v in examples.items()},
        "crossings_completed": {ep: d["num_crossings_completed"] for ep, d in per_endpoint.items()},
        "forced_entry": {ep: any(c["forced_entry"] for c in d["crossings"]) for ep, d in per_endpoint.items()},
        "task_completion_s": {ep: d["task_completion_s"] for ep, d in per_endpoint.items()},
    }
    out_path = output_dir.parent / "wifi_mac_phy_loss_findings.json"
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
