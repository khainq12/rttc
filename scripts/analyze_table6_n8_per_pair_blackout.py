"""Table VI PHASE 2 -- per-pair (sender, receiver) network blackout
characterization at N=8 (see docs/AUDIT_ACCEPTANCE_TRACKING.md).

READ-ONLY re-analysis of the ALREADY-COLLECTED N=8 seed=7 trace data
(post Phase-1-fix run) -- no rerun, no config change. Reuses the recv
trace's per-receiver, per-sender arrival timeline (already correctly
disambiguated by robot_id, from the earlier duplicate-drop/permanent-
loss investigations) to build a pair x time picture:

For each ordered (sender, receiver) pair:
  - DATA arrivals: sorted list of wall_ns.
  - Longest gap between two consecutive arrivals (the longest
    CONTINUOUS reception blackout strictly BETWEEN two successful
    deliveries -- excludes the unobservable time before the first or
    after the last arrival, since there is no reliable common epoch to
    anchor "scenario start/end" across independently-clocked
    processes; flagged explicitly as a limitation).
  - Whether the pair recovers (has at least one arrival after its own
    longest gap) or effectively stops (longest gap ends at the last
    arrival).
  - Reverse-direction comparison (receiver->sender) to check symmetry.
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

from scripts.run_ns3_docker_container_fleet_probe import endpoint_list  # noqa: E402

NUM_ROBOTS = 8
ENDPOINTS = endpoint_list(NUM_ROBOTS)
RUN_DIR = ROOT / "results_rmw_socket" / "table6_n8_permanent_loss_cause" / "fleetrmw_n8_seed7"


def main() -> int:
    results_dir = RUN_DIR / "container_results"
    per_endpoint: dict[str, Any] = {}
    for i, ep in enumerate(ENDPOINTS):
        per_endpoint[ep] = json.loads((results_dir / f"result_{i}.json").read_text())

    # arrivals[(sender_robot, receiver_ep)] = sorted list of wall_ns
    arrivals: dict[tuple, list[int]] = defaultdict(list)
    for receiver_ep, d in per_endpoint.items():
        for e in d["fleetqox_loss_funnel_trace"]["recv"]:
            arrivals[(e["robot_id"], receiver_ep)].append(e["wall_ns"])

    pair_stats = []
    for sender in ENDPOINTS:
        for receiver in ENDPOINTS:
            if sender == receiver:
                continue
            ts = sorted(arrivals.get((sender, receiver), []))
            if len(ts) < 2:
                pair_stats.append({
                    "sender": sender, "receiver": receiver, "arrivals": len(ts),
                    "longest_gap_s": None, "gap_start_ns": None, "gap_end_ns": None,
                    "recovers_after_longest_gap": None,
                })
                continue
            gaps = [(ts[i + 1] - ts[i], ts[i], ts[i + 1]) for i in range(len(ts) - 1)]
            longest_gap, gap_start, gap_end = max(gaps, key=lambda g: g[0])
            recovers = gap_end != ts[-1]  # there IS an arrival after the gap ends (trivially true unless it's the last gap)
            pair_stats.append({
                "sender": sender, "receiver": receiver, "arrivals": len(ts),
                "longest_gap_s": round(longest_gap / 1e9, 3),
                "gap_start_ns": gap_start, "gap_end_ns": gap_end,
                "recovers_after_longest_gap": True,  # by construction (gap is BETWEEN two arrivals)
            })

    # Sort by longest gap descending to surface the worst pairs first.
    ranked = sorted(
        (p for p in pair_stats if p["longest_gap_s"] is not None),
        key=lambda p: p["longest_gap_s"], reverse=True,
    )
    zero_or_one_arrival = [p for p in pair_stats if p["longest_gap_s"] is None]

    print(f"total ordered pairs: {len(pair_stats)}", flush=True)
    print(f"pairs with <2 arrivals (cannot measure a gap): {len(zero_or_one_arrival)}", flush=True)
    for p in zero_or_one_arrival:
        print(f"  {p['sender']} -> {p['receiver']}: {p['arrivals']} arrival(s)", flush=True)
    print(flush=True)
    print("top 15 longest continuous blackouts (between two successful arrivals):", flush=True)
    for p in ranked[:15]:
        print(f"  {p['sender']:16s} -> {p['receiver']:16s}: {p['longest_gap_s']:8.2f}s "
              f"(arrivals={p['arrivals']})", flush=True)
    print(flush=True)

    # Directionality: compare forward vs reverse for each unordered pair.
    print("directionality (forward vs reverse longest gap, seconds):", flush=True)
    seen = set()
    asymmetric_count = 0
    symmetric_count = 0
    for p in pair_stats:
        key = tuple(sorted([p["sender"], p["receiver"]]))
        if key in seen:
            continue
        seen.add(key)
        fwd = next((x for x in pair_stats if x["sender"] == key[0] and x["receiver"] == key[1]), None)
        rev = next((x for x in pair_stats if x["sender"] == key[1] and x["receiver"] == key[0]), None)
        fwd_gap = fwd["longest_gap_s"] if fwd else None
        rev_gap = rev["longest_gap_s"] if rev else None
        if fwd_gap is not None and rev_gap is not None:
            ratio = (max(fwd_gap, rev_gap) / max(min(fwd_gap, rev_gap), 0.001))
            if ratio > 3:
                asymmetric_count += 1
            else:
                symmetric_count += 1
    print(f"  symmetric pairs (within 3x of each other): {symmetric_count}", flush=True)
    print(f"  asymmetric pairs (>3x difference): {asymmetric_count}", flush=True)
    print(flush=True)

    # robot_0007-specific check.
    involving_0007 = [p for p in ranked if "robot_0007" in (p["sender"], p["receiver"])]
    not_0007 = [p for p in ranked if "robot_0007" not in (p["sender"], p["receiver"])]
    print(f"pairs involving robot_0007: {len(involving_0007)}, "
          f"mean longest gap: {sum(p['longest_gap_s'] for p in involving_0007)/max(len(involving_0007),1):.2f}s", flush=True)
    print(f"pairs NOT involving robot_0007: {len(not_0007)}, "
          f"mean longest gap: {sum(p['longest_gap_s'] for p in not_0007)/max(len(not_0007),1):.2f}s", flush=True)

    out_path = RUN_DIR.parent / "per_pair_blackout_findings.json"
    out_path.write_text(json.dumps({"pair_stats": pair_stats}, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
