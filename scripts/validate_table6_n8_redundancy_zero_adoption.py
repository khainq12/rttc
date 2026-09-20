"""Post-adoption N=8 sanity (see docs/AUDIT_ACCEPTANCE_TRACKING.md,
"TABLE VI ACK/NACK REDUNDANCY=0 ADOPTION"). Runs run_coordination_probe()
with NO extra_rmw_env override at all -- proving the Table-VI-specific
default (FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=0, now set inside
fleetqox_coordination_rmw_env_prefix()) actually takes effect end-to-end
through the real launcher, not just in the unit tests.
"""

from __future__ import annotations

import json
import re
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

NUM_ROBOTS = 8
SEED = 7
ENDPOINTS = endpoint_list(NUM_ROBOTS)
VALIDITY_GATE_S = 10.0
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


def parse_wifi_stats(ns3_log: str) -> list[dict[str, Any]]:
    out = []
    for line in ns3_log.splitlines():
        if not line.startswith("FLEETQOX_WIFI_STATS "):
            continue
        try:
            out.append(json.loads(_TRAILING_COMMA_RE.sub(r"\1", line[len("FLEETQOX_WIFI_STATS "):])))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    output_dir = ROOT / "results_rmw_socket" / "table6_n8_redundancy_zero_adoption_sanity" / f"n8_seed{SEED}"
    print(f"=== N=8 seed={SEED}, directed-reply ON, NO extra_rmw_env override (adoption sanity) ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=NUM_ROBOTS,
        seed=SEED,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        directed_reply=True,
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        print("SANITY FAILED: run did not complete ok", flush=True)
        return 1

    ns3_log = result.get("ns3_log", "") or ""
    (output_dir.parent / f"ns3_seed{SEED}.log").write_text(ns3_log, encoding="utf-8")
    stats = parse_wifi_stats(ns3_log)
    last = stats[-1] if stats else {}

    results_dir = output_dir / "container_results"
    per_endpoint: dict[str, Any] = {}
    for i, ep in enumerate(ENDPOINTS):
        per_endpoint[ep] = json.loads((results_dir / f"result_{i}.json").read_text())

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
            sent_index[wall_ns] = {"sender": ep, "intended": intended}
    delivered_pairs: set[tuple] = set()
    for ep, d in per_endpoint.items():
        for r in d.get("raw_received_log", []):
            wall_ns = r.get("wall_ns")
            if wall_ns is not None:
                delivered_pairs.add((wall_ns, ep))
    total_pairs = 0
    delivered_count = 0
    for wall_ns, info in sent_index.items():
        for recipient in info["intended"]:
            total_pairs += 1
            if (wall_ns, recipient) in delivered_pairs:
                delivered_count += 1
    data_delivery_pct = round(100 * delivered_count / total_pairs, 2) if total_pairs else None

    crossings_completed = {ep: d["num_crossings_completed"] for ep, d in per_endpoint.items()}
    forced_entry = {ep: any(c["forced_entry"] for c in d["crossings"]) for ep, d in per_endpoint.items()}
    task_completion_s = {ep: d["task_completion_s"] for ep, d in per_endpoint.items()}

    sim_lag_s = last.get("sim_lag_s")
    out = {
        "status": "ok",
        "wall_elapsed_s": last.get("wall_elapsed_s"),
        "sim_time_s": last.get("sim_time_s"),
        "sim_lag_s": sim_lag_s,
        "valid": (sim_lag_s is not None and sim_lag_s <= VALIDITY_GATE_S),
        "data_delivery_pct": data_delivery_pct,
        "crossings_completed": crossings_completed,
        "all_5_of_5_crossings": all(v == 5 for v in crossings_completed.values()),
        "any_forced_entry": any(forced_entry.values()),
        "task_completion_s_mean": round(sum(task_completion_s.values()) / len(task_completion_s), 2),
    }
    out_path = output_dir.parent / "adoption_sanity_findings.json"
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(out, indent=2), flush=True)
    print(f"\nWrote {out_path}", flush=True)

    ok = (
        out["valid"]
        and out["data_delivery_pct"] == 100.0
        and out["all_5_of_5_crossings"]
        and not out["any_forced_entry"]
    )
    print(f"\nADOPTION SANITY: {'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
