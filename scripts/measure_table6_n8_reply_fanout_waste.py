"""Table VI PHASE 5 -- REQUEST/REPLY broadcast amplification measurement
at N=8 (see docs/AUDIT_ACCEPTANCE_TRACKING.md).

READ-ONLY re-analysis of the ALREADY-COLLECTED, Phase-1-fixed N=8
seed=7 data (no rerun). REQUEST and REPLY currently share one
broadcast topic (`/fleetqox_coordination/control`, see
fleetqox_coordination_endpoint.py: `reply_pub = request_pub`); REPLY
is logically directed (each carries a `"to"` field) but physically
delivered to every peer, filtered at the APPLICATION layer in
on_reply() (`if payload["to"] != args.endpoint: return`) -- i.e. the
full sendto->network->recvfrom->decode wire cost is already paid
before the message is discarded as irrelevant.

Uses raw_received_log (already recorded per endpoint, RMW-level
successful decode events) to count, for every REPLY ever successfully
delivered anywhere in the fleet, whether it was actually addressed to
the endpoint that received it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import endpoint_list  # noqa: E402

NUM_ROBOTS = 8
ENDPOINTS = endpoint_list(NUM_ROBOTS)
RUN_DIR = ROOT / "results_rmw_socket" / "table6_n8_permanent_loss_cause" / "fleetrmw_n8_seed7"


def main() -> int:
    results_dir = RUN_DIR / "container_results"
    useful = 0
    wasted = 0
    request_received = 0
    for i, ep in enumerate(ENDPOINTS):
        d = json.loads((results_dir / f"result_{i}.json").read_text())
        for x in d["raw_received_log"]:
            if x["type"] == "reply":
                if x["to"] == ep:
                    useful += 1
                else:
                    wasted += 1
            elif x["type"] == "request":
                request_received += 1

    total_reply = useful + wasted
    waste_pct = round(100 * wasted / total_reply, 1) if total_reply else None
    expected_waste_pct = round(100 * (NUM_ROBOTS - 1) / NUM_ROBOTS, 1)  # (N-1)/N other peers per fan-out
    print(f"REPLY deliveries useful (addressed to receiver): {useful}")
    print(f"REPLY deliveries wasted (addressed to someone else): {wasted}")
    print(f"wasted fraction of all decoded REPLY deliveries: {waste_pct}%")
    print(f"theoretical fan-out waste for N={NUM_ROBOTS} (7/8 other peers per broadcast reply): {expected_waste_pct}%")
    print(f"REQUEST deliveries (by design, needs full broadcast for Ricart-Agrawala correctness): {request_received}")

    out = {
        "reply_useful": useful, "reply_wasted": wasted, "waste_pct": waste_pct,
        "request_received": request_received,
    }
    out_path = RUN_DIR.parent / "reply_fanout_waste_findings.json"
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
