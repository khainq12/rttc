"""Table VI robot-identity-collision fix -- MULTI-SEED VALIDATION (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI ROBOT IDENTITY COLLISION --
FIXED AND VALIDATED"). Re-runs the exact same FleetRMW N=4 coordination
probe across 5 fixed seeds (the canonical 7/13/29 plus the next two
seeds from this project's own established 20-seed list, 41/53 --
scripts/run_lan_n16_paired_20seed_experiment.py's PRIOR_10_SEED_LIST)
to check whether the fix (commit 977f777) holds cleanly across more
than the single seed it was validated on, and whether the previously
measured ~36.5% network-level loss component reproduces on its own now
that the much larger duplicate-drop effect is gone.

READ-ONLY measurement pass: no harness/production/protocol/QoS/timeout
change of any kind. FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING=1 is the
ONLY non-default env var, and it only enables already-existing,
opt-in, purely observational tracing (see fleetqox_loss_funnel_trace()'s
own docstring) -- it does not change send/retry/QoS/timeout behavior.
"""

from __future__ import annotations

import argparse
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

SEEDS = [7, 13, 29, 41, 53]
NUM_ROBOTS = 4


def analyze_run(output_dir: Path, num_robots: int, status: str, error: str) -> dict[str, Any]:
    endpoints = endpoint_list(num_robots)
    results_dir = output_dir / "container_results"
    per_endpoint: dict[str, Any] = {}
    for i, endpoint in enumerate(endpoints):
        p = results_dir / f"result_{i}.json"
        per_endpoint[endpoint] = json.loads(p.read_text()) if p.exists() else None

    valid = status == "ok" and all(v is not None for v in per_endpoint.values())
    if not valid:
        return {"valid": False, "status": status, "error": error}

    robot_ids = {}
    dup_sum = 0
    ooo_sum = 0
    send_sum = raw_recvfrom_sum = recv_sum = matched_nonzero_sum = matched_zero_sum = 0
    py_recv_sum = 0
    crossings_completed = {}
    forced_entry_by_endpoint = {}
    task_completion_s = {}

    for endpoint, d in per_endpoint.items():
        diag = d["fleetqox_stream_identity_diagnostics"]
        robot_ids[endpoint] = diag.get("effective_robot_id")
        dup_sum += diag.get("duplicate_data_frames_deduped", 0)
        ooo_sum += diag.get("out_of_order_data_frames_observed", 0)

        trace = d["fleetqox_loss_funnel_trace"]
        send_sum += len(trace["send"])
        raw_recvfrom_sum += len(trace["raw_recvfrom"])
        recv_sum += len(trace["recv"])
        sm = trace["subscription_match"]
        matched_nonzero_sum += sum(1 for e in sm if e["matched_subscriptions"] >= 1)
        matched_zero_sum += sum(1 for e in sm if e["matched_subscriptions"] == 0)

        py_recv_sum += len(d["raw_received_log"])
        crossings_completed[endpoint] = d["num_crossings_completed"]
        forced_entry_by_endpoint[endpoint] = [c["forced_entry"] for c in d["crossings"]]
        task_completion_s[endpoint] = d["task_completion_s"]

    unique_ids = len(set(robot_ids.values())) == len(robot_ids)

    total_req_sent = total_req_recv = total_reply_sent = total_reply_recv = 0
    for a in endpoints:
        total_req_sent += len([x for x in per_endpoint[a]["sent_log"] if x["type"] == "request"])
        total_reply_sent += len([x for x in per_endpoint[a]["sent_log"] if x["type"] == "reply"])
    for e in endpoints:
        total_req_recv += len(
            [x for x in per_endpoint[e]["raw_received_log"] if x["type"] == "request"]
        )
        total_reply_recv += len(
            [x for x in per_endpoint[e]["raw_received_log"] if x["type"] == "reply" and x["to"] == e]
        )

    return {
        "valid": True,
        "status": status,
        "robot_ids": robot_ids,
        "unique_robot_ids": unique_ids,
        "duplicate_data_frames_deduped_sum": dup_sum,
        "out_of_order_data_frames_observed_sum": ooo_sum,
        "loss_funnel": {
            "send_attempts": send_sum,
            "raw_recvfrom": raw_recvfrom_sum,
            "recv_decoded": recv_sum,
            "matched_delivered": matched_nonzero_sum,
            "matched_dropped_zero": matched_zero_sum,
        },
        "python_level_received_total": py_recv_sum,
        "request_sent": total_req_sent,
        "request_received": total_req_recv,
        "reply_sent": total_reply_sent,
        "reply_received": total_reply_recv,
        "crossings_completed": crossings_completed,
        "forced_entry_by_endpoint": forced_entry_by_endpoint,
        "task_completion_s": task_completion_s,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--num-robots",
        type=int,
        default=NUM_ROBOTS,
        help="scale knob only for THIS measurement script -- does not touch the "
        "harness/production code being validated. Default (4) matches the "
        "original single-scale validation this script was written for.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="defaults to results_rmw_socket/table6_identity_fix_seed_validation "
        "for N=4 (unchanged path, for exact backward compatibility) or "
        "..._n{N} for any other --num-robots.",
    )
    parser.add_argument("--summary-json", type=Path, default=None)
    args = parser.parse_args()

    num_robots = args.num_robots
    output_root = args.output_root
    if output_root is None:
        suffix = "" if num_robots == NUM_ROBOTS else f"_n{num_robots}"
        output_root = ROOT / "results_rmw_socket" / f"table6_identity_fix_seed_validation{suffix}"
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for seed in SEEDS:
        print(f"=== FleetRMW N={num_robots} seed={seed} ===", flush=True)
        output_dir = output_root / f"fleetrmw_n{num_robots}_seed{seed}"
        result = run_coordination_probe(
            image=args.image,
            output_dir=output_dir,
            num_robots=num_robots,
            seed=seed,
            rmw_implementation="rmw_fleetqox_cpp",
            discovery_mode="default",
            extra_rmw_env={"FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1"},
        )
        analysis = analyze_run(output_dir, num_robots, result["status"], result["error"])
        analysis["seed"] = seed
        results.append(analysis)
        print(json.dumps({k: analysis[k] for k in ("seed", "valid", "status")}), flush=True)

    report = {"seeds": SEEDS, "num_robots": num_robots, "results": results}
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
