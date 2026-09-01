"""Run real ROS 2 action with repaired control/state flows on one router."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts.run_rmw_docker_router_rclpy_action_probe import (
        DEFAULT_ACTION,
        run_probe,
    )
except ModuleNotFoundError:
    from run_rmw_docker_router_rclpy_action_probe import (
        DEFAULT_ACTION,
        run_probe,
    )


SCHEMA_VERSION = "fleetrmw.rmw_router_mixed_action_control_state_probe.v1"
# This probe requires --netem-profile support (default "roaming"), which
# needs the `tc` binary. The base router_rclpy_action_probe's DEFAULT_IMAGE
# (ros:jazzy-ros-base) doesn't have it, so this probe needs its own default.
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--action", default=DEFAULT_ACTION)
    parser.add_argument("--robot-count", type=int, default=2)
    parser.add_argument("--netem-profile", default="roaming")
    parser.add_argument("--netem-loss-percent", type=float, default=0.02)
    parser.add_argument("--scheduler-window-ms", type=int, default=150)
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_router_mixed_action_control_state_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    result = run_probe(
        root=root,
        image=args.image,
        action=args.action,
        feedback_deadline_ms=5,
        status_deadline_ms=100,
        scheduler_window_ms=max(args.scheduler_window_ms, 1),
        expected_data_frames=3,
        expect_observation_delivery=True,
        mixed_robot_count=max(args.robot_count, 1),
        netem_profile=args.netem_profile,
        netem_loss_percent=max(args.netem_loss_percent, 0.0),
    )
    summary = summarize(result)
    output = root / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-mixed-action-control-state-probe")
        print(f"  status: {summary['status']}")
        print(f"  action_ok: {summary['action_ok']}")
        print(f"  mixed_flows_ok: {summary['mixed_flows_ok']}/{summary['mixed_flow_count']}")
        print(f"  scheduler_urgent_frames: {summary['scheduler_urgent_frames']}")
        print(f"  scheduler_queued_frames: {summary['scheduler_queued_frames']}")
    return 0 if summary["status"] == "ok" else 1


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    client = result.get("client", {})
    server = result.get("server", {})
    router = result.get("router", {})
    mixed_rows = result.get("mixed_rows", [])
    action_ok = (
        result.get("status") == "ok"
        and client.get("success_result_status") == 4
        and client.get("cancel_result_status") == 5
        and "success" in client.get("feedback_callbacks", [])
        and "cancel" in client.get("feedback_callbacks", [])
        and server.get("cancel_callbacks", 0) >= 1
    )
    mixed_flows_ok = sum(row.get("status") == "ok" for row in mixed_rows)
    kinds = {row.get("kind") for row in mixed_rows if row.get("status") == "ok"}
    qdisc = str(result.get("netem_qdisc", ""))
    evidence_ok = (
        action_ok
        and mixed_rows
        and mixed_flows_ok == len(mixed_rows)
        and kinds == {"control", "state"}
        and "netem" in qdisc
        and router.get("drop_topic_prefix") == "/fleetqox/mixed/"
        and router.get("test_dropped_frames", 0) >= len(mixed_rows)
        and router.get("ack_nack_forwarded", 0) >= len(mixed_rows) * 3
        and router.get("scheduler_urgent_frames", 0) > 0
        and router.get("scheduler_queued_frames", 0) > 0
        and router.get("service_forwarded", 0) >= 10
        and router.get("scheduler_fresh_deadline_misses", 0) == 0
        and router.get("scheduler_repair_frames", 0) >= len(mixed_rows)
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if evidence_ok else "failed",
        "action_ok": action_ok,
        "mixed_flow_count": len(mixed_rows),
        "mixed_flows_ok": mixed_flows_ok,
        "mixed_kinds_ok": sorted(kind for kind in kinds if kind),
        "netem_qdisc": qdisc,
        "router_drop_topic_prefix": router.get("drop_topic_prefix"),
        "router_test_dropped_frames": router.get("test_dropped_frames", 0),
        "router_ack_nack_forwarded": router.get("ack_nack_forwarded", 0),
        "scheduler_urgent_frames": router.get("scheduler_urgent_frames", 0),
        "scheduler_queued_frames": router.get("scheduler_queued_frames", 0),
        "scheduler_forwarded_frames": router.get("scheduler_forwarded_frames", 0),
        "scheduler_deadline_misses": router.get("scheduler_deadline_misses", 0),
        "scheduler_fresh_deadline_misses": router.get(
            "scheduler_fresh_deadline_misses", 0
        ),
        "scheduler_repair_frames": router.get("scheduler_repair_frames", 0),
        "scheduler_repair_deadline_misses": router.get(
            "scheduler_repair_deadline_misses", 0
        ),
        "scheduler_deadline_success_jain_index": router.get(
            "scheduler_deadline_success_jain_index", 0.0
        ),
        "result": result,
    }


if __name__ == "__main__":
    raise SystemExit(main())
