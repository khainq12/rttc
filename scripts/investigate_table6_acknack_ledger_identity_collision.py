"""Table VI PHASE 1 -- ACK/NACK ledger identity collision RED/GREEN test
(see docs/AUDIT_ACCEPTANCE_TRACKING.md). Proves and then re-verifies
whether handle_ack_nack_feedback()'s g_retransmit_ledger lookup
(matching only publisher_id + domain_id, never robot_id) lets an
incoming ACK/NACK about one robot's stream act on a DIFFERENT robot's
ledger entry that happens to share the same (coincidentally-collided)
publisher_id text.

Minimal deterministic repro: N=2 (3 endpoints: control_station,
robot_0000, robot_0001), short scenario, every endpoint's OWN first
DATA sequence is force-dropped exactly once via the already-existing
FLEETQOX_RMW_DROP_SOURCE_SEQUENCES test hook -- this deterministically
produces at least one real NACK/retransmit cycle without needing ns-3
packet loss. All three endpoints' control-topic publisher is the same
positional (first) publisher created in its own process, so all three
get textually identical publisher_id ("fpubcpp-0.0.0.0:9100-N" for the
same N) by construction -- exactly the collision precondition.

Metric: count of incoming_ack_nack trace events where found_in_ledger
is true AND event.robot_id (the ORIGINAL PUBLISHER whose data this
feedback concerns -- see rmw_pubsub.cpp/encode_ack_nack) differs from
the PROCESSING endpoint's own robot_id. Nonzero = proven collision.

READ-ONLY except for the one deliberately-scoped FLEETQOX_RMW_DROP_
SOURCE_SEQUENCES test hook (already-existing, opt-in, used exactly as
intended). Does not change ACK/NACK count, retransmission, timeout,
QoS, ns-3, or broadcast config.
"""

from __future__ import annotations

import json
import sys
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

NUM_ROBOTS = 2
SEED = 1
ENDPOINTS = endpoint_list(NUM_ROBOTS)


def main() -> int:
    output_dir = ROOT / "results_rmw_socket" / "table6_acknack_ledger_identity_collision" / f"fleetrmw_n{NUM_ROBOTS}_seed{SEED}"
    print(f"=== FleetRMW N={NUM_ROBOTS} seed={SEED} (ACK/NACK ledger identity collision RED/GREEN check) ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=NUM_ROBOTS,
        seed=SEED,
        num_crossings=5,
        scenario_timeout_s=30.0,
        discovery_timeout_s=5.0,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        extra_rmw_env={
            "FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1",
            "FLEETQOX_RMW_DROP_SOURCE_SEQUENCES": "3",
        },
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        print("run did not complete ok -- stopping", flush=True)
        return 1

    results_dir = output_dir / "container_results"
    per_endpoint: dict[str, Any] = {}
    for i, ep in enumerate(ENDPOINTS):
        per_endpoint[ep] = json.loads((results_dir / f"result_{i}.json").read_text())

    total_incoming = 0
    total_dropped = 0
    collisions = []
    for ep, d in per_endpoint.items():
        diag = d.get("fleetqox_stream_identity_diagnostics", {})
        own_robot_id = diag.get("effective_robot_id", "")
        total_dropped += diag.get("test_dropped_frames", 0)
        inc = d["fleetqox_loss_funnel_trace"]["incoming_ack_nack"]
        total_incoming += len(inc)
        for e in inc:
            if e["found_in_ledger"] and e["robot_id"] != own_robot_id:
                collisions.append({"processing_endpoint": ep, "processing_own_robot_id": own_robot_id, **e})

    print(f"total test_dropped_frames: {total_dropped}", flush=True)
    print(f"total incoming_ack_nack events: {total_incoming}", flush=True)
    print(f"cross-robot found_in_ledger=true collisions: {len(collisions)}", flush=True)
    for c in collisions[:5]:
        print(" ", c, flush=True)

    out = {
        "num_robots": NUM_ROBOTS, "seed": SEED,
        "total_incoming_ack_nack": total_incoming,
        "collision_count": len(collisions),
        "collisions": collisions,
    }
    out_path = output_dir.parent / "collision_findings.json"
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)
    print(f"RESULT: collision_count={len(collisions)} ({'RED -- bug present' if collisions else 'GREEN -- no collision found'})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
