#!/usr/bin/env python3
"""Prove the real QUIC gateway's writer lease can be gated by native Raft.

Every existing writer-lease/takeover probe (run_rmw_docker_quic_writer_fencing_probe.py,
run_rmw_docker_quic_automatic_standby_takeover_probe.py) uses a fixed,
operator-assigned --writer-lease-instance-id string, and a standby takes
over purely by retrying until the previous holder's SQL-side lease TTL
naturally expires -- there is no external, consensus-based failure
detector deciding *when* a takeover is legitimate. This probe instead
starts scripts/fleetrmw_quic_gateway_service.py with the new
--raft-status-url/--raft-node-id flags (see fleetqox/raft_writer_lease.py),
so write eligibility comes from actually winning leadership of a real,
separate three-node fleetqox.raft cluster (scripts/fleetqox_raft_node_service.py)
-- no etcd, no PostgreSQL.

Proves, over real separate Docker containers:

  1. A gateway whose Raft node is NOT the cluster's leader refuses to
     become the writer at all (fails closed immediately with
     --raft-writer-lease-wait-timeout-ms 0), naming the real failure.
  2. A gateway whose Raft node IS the leader accepts a durable admission
     write end to end over a real QUIC v1/H3 connection.
  3. Killing the leader's Raft node (and its gateway) triggers a real
     Raft re-election among the two survivors; whichever survivor
     actually wins (determined by polling, not assumed) is where the
     NEXT gateway instance is pointed, and it automatically takes over
     the shared SQLite durable store with a fresh, Raft-term-derived
     fencing token -- a strictly higher SQL fence_token than the original
     holder's, so a stale writer using the old token is still rejected by
     the same, already-proven SQL-side check. The resumed session sees
     the original seed write's state (no data loss across the failover).

This does not touch fleetqox/quic_gateway_state.py's SQL lease/fencing
logic at all -- Raft supplies a different holder_id per elected leader
term; the existing, separately-proven SQL fencing still enforces it at
write time.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any

try:
    from scripts.run_rmw_docker_quic_admission_probe import certificate_command
    from scripts.run_rmw_docker_quic_durable_admission_failover_probe import probe_ok
    from scripts.run_rmw_docker_quic_stateful_gateway_probe import (
        DEFAULT_IMAGE,
        json_rows,
        run,
        wait_service_ready,
    )
    from scripts.run_rmw_docker_quic_writer_fencing_probe import (
        run_client,
        start_service,
        stop_service,
    )
    from scripts.run_rmw_docker_raft_consensus_probe import (
        INTERNAL_PORT,
        get_status,
        start_node,
        wait_ready,
        wait_single_leader,
    )
except ModuleNotFoundError:
    from run_rmw_docker_quic_admission_probe import certificate_command
    from run_rmw_docker_quic_durable_admission_failover_probe import probe_ok
    from run_rmw_docker_quic_stateful_gateway_probe import (
        DEFAULT_IMAGE,
        json_rows,
        run,
        wait_service_ready,
    )
    from run_rmw_docker_quic_writer_fencing_probe import (
        run_client,
        start_service,
        stop_service,
    )
    from run_rmw_docker_raft_consensus_probe import (
        INTERNAL_PORT,
        get_status,
        start_node,
        wait_ready,
        wait_single_leader,
    )


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_quic_gateway_raft_writer_lease_probe.v1"
GATEWAY_ALIAS = "fleetqox-admission-gateway"
RAFT_NODE_IDS = ("raft-a", "raft-b", "raft-c")


def gateway_service_command(
    *, root: Path, temp_root: Path, index: int, raft_alias: str, qlogs: Path,
    raft_wait_timeout_ms: int, sql_wait_timeout_ms: int,
) -> str:
    certs = temp_root / "certs"
    policy = temp_root / "admission-policy.json"
    database = temp_root / f"run-{index}" / "gateway-state.sqlite3"
    return (
        "tc qdisc replace dev eth0 root netem delay 5ms 1ms loss 0.2% && "
        "tc qdisc show dev eth0 && "
        "exec python3 scripts/fleetrmw_quic_gateway_service.py "
        "--host 0.0.0.0 --port 4504 "
        f"--certificate /work/{(certs / 'server.crt').relative_to(root)} "
        f"--private-key /work/{(certs / 'server.key').relative_to(root)} "
        f"--admission-policy /work/{policy.relative_to(root)} "
        f"--state-db /work/{database.relative_to(root)} "
        f"--raft-status-url http://{raft_alias}:{INTERNAL_PORT} "
        f"--raft-node-id {raft_alias} "
        f"--raft-writer-lease-wait-timeout-ms {raft_wait_timeout_ms} "
        "--raft-writer-lease-retry-ms 100 "
        "--writer-lease-ms 2000 "
        f"--writer-lease-wait-timeout-ms {sql_wait_timeout_ms} "
        "--writer-lease-retry-ms 100 "
        f"--qlog-dir /work/{qlogs.relative_to(root)} "
        "--max-frames-per-topic 8 --max-frame-bytes 65536"
    )


def gateway_lease_ok(row: dict[str, Any], *, raft_node_id: str, min_term: int) -> bool:
    metrics = row.get("metrics", {})
    durable = metrics.get("durable_state", {})
    lease = durable.get("writer_lease", {})
    holder = str(lease.get("holder_id", ""))
    return (
        row.get("raft_leadership_configured") is True
        and row.get("raft_node_id") == raft_node_id
        and isinstance(row.get("raft_term"), int)
        and row.get("raft_term") >= min_term
        and holder == f"raft-{raft_node_id}-term-{row.get('raft_term')}"
        and metrics.get("durable_writer_lease_acquires") == 1
        and metrics.get("durable_writer_lease_failures") == 0
    )


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    suffix = str(os.getpid())
    temp_root = root / f".tmp_fleetrmw_quic_raft_lease_{suffix}"
    certs = temp_root / "certs"
    run_dir = temp_root / "run-1"
    for path in (certs, run_dir):
        path.mkdir(parents=True, exist_ok=True)
    policy = {
        "schema_version": "fleetrmw.quic_gateway_admission_policy.v1",
        "default_action": "deny", "max_accepted_frames": 1,
        "rules": [{
            "domain_id": 42, "topic": "/fleetqox/durable_admission",
            "traffic_class": "control", "max_accepted_frames": 1,
            "allowed_publishers": ["durable-admission-publisher"],
        }],
        "repair": {
            "capacity_bytes": 1024, "max_admitted": 1,
            "paths": [{
                "path_id": "private_5g", "latency_ms": 20.0,
                "loss": 0.01, "failure_domain": "private_5g",
            }],
        },
    }
    (temp_root / "admission-policy.json").write_text(
        json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8"
    )
    build_root = "/work/.tmp_fleetrmw_quic_raft_lease_build"
    install = "/work/.tmp_fleetrmw_quic_raft_lease_install"
    log_root = "/work/.tmp_fleetrmw_quic_raft_lease_log"
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "failed"}

    cert_result = run([
        "docker", "run", "--rm", "--entrypoint", "bash",
        "-v", f"{root}:/work", "-w", "/work", image, "-lc",
        certificate_command(certs, root),
    ])
    build = run([
        "docker", "run", "--rm", "--entrypoint", "bash",
        "-v", f"{root}:/work", "-w", "/work", image, "-lc",
        "source /opt/ros/jazzy/setup.bash && "
        f"rm -rf {build_root} {install} {log_root} && "
        f"colcon --log-base {log_root} build --base-paths ros2_ws/src "
        "--packages-select rmw_fleetqox_cpp "
        f"--build-base {build_root} --install-base {install} "
        "--cmake-args -DCMAKE_BUILD_TYPE=Release",
    ])
    network = f"fleetrmw-raft-lease-net-{suffix}"
    network_result = run(["docker", "network", "create", network])
    raft_containers = {
        node_id: f"fleetrmw-raft-lease-{node_id}-{suffix}" for node_id in RAFT_NODE_IDS
    }
    gateway_names: list[str] = []
    try:
        if cert_result.returncode != 0 or build.returncode != 0 or network_result.returncode != 0:
            result["stage"] = "setup"
            result["build_stderr"] = build.stderr[-4000:]
            return result

        raft_aliases = {node_id: node_id for node_id in RAFT_NODE_IDS}
        for node_id in RAFT_NODE_IDS:
            started = start_node(
                image=image, network=network, container=raft_containers[node_id],
                node_id=node_id, peers=raft_aliases,
            )
            if started.returncode != 0:
                result["stage"] = f"start_raft_{node_id}"
                return result
        if not wait_ready(raft_containers):
            result["stage"] = "raft_wait_ready"
            return result
        raft_alive = set(RAFT_NODE_IDS)
        leader_1 = wait_single_leader(raft_containers, alive=raft_alive)
        if leader_1 is None:
            result["stage"] = "raft_initial_election"
            return result
        leader_1_id = leader_1["leader_id"]

        # Property 1: a gateway whose Raft node is NOT the leader fails
        # closed immediately rather than silently becoming a writer.
        non_leader_id = next(node_id for node_id in RAFT_NODE_IDS if node_id != leader_1_id)
        blocked_qlogs = temp_root / "blocked-qlogs"
        blocked_qlogs.mkdir(parents=True, exist_ok=True)
        blocked_name = f"fleetrmw-raft-lease-blocked-{suffix}"
        gateway_names.append(blocked_name)
        blocked_run = run([
            "docker", "run", "--rm", "--name", blocked_name,
            "--network", network, "--cap-add", "NET_ADMIN", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work", image, "-lc",
            gateway_service_command(
                root=root, temp_root=temp_root, index=1, raft_alias=non_leader_id,
                qlogs=blocked_qlogs, raft_wait_timeout_ms=0, sql_wait_timeout_ms=0,
            ),
        ])
        non_leader_fails_closed = (
            blocked_run.returncode != 0
            and '"status": "raft_leadership_failed"' in blocked_run.stdout
            and f'"raft_node_id": "{non_leader_id}"' in blocked_run.stdout
        )

        # Property 2: the Raft-elected leader's gateway accepts a real
        # durable admission write.
        active_qlogs_svc = temp_root / "active-service-qlogs"
        active_qlogs_client = temp_root / "active-client-qlogs"
        for path in (active_qlogs_svc, active_qlogs_client):
            path.mkdir(parents=True, exist_ok=True)
        active_name = f"fleetrmw-raft-lease-active-{suffix}"
        gateway_names.append(active_name)
        active_ready = start_service(
            root=root, image=image, network=network, name=active_name,
            alias=GATEWAY_ALIAS,
            command=gateway_service_command(
                root=root, temp_root=temp_root, index=1, raft_alias=leader_1_id,
                qlogs=active_qlogs_svc, raft_wait_timeout_ms=5000, sql_wait_timeout_ms=5000,
            ),
        )
        seed_client = run_client(
            root=root, image=image, network=network, install=install,
            name=f"fleetrmw-raft-lease-seed-{suffix}", certs=certs,
            qlogs=active_qlogs_client, mode="seed",
        ) if active_ready else None
        active_exit, active_logs, active_service = stop_service(active_name)
        gateway_names.remove(active_name)
        active_ok = (
            active_ready
            and seed_client is not None and seed_client.returncode == 0
            and probe_ok(
                json_rows(seed_client.stdout)[-1] if json_rows(seed_client.stdout) else {},
                "seed",
            )
            and active_exit == 0
            and gateway_lease_ok(active_service, raft_node_id=leader_1_id, min_term=leader_1["term"])
        )

        # Property 3: kill the leader's Raft node (its gateway is already
        # stopped above); the two survivors hold a real election. Whoever
        # actually wins -- determined by polling, not assumed -- is where
        # the next gateway is pointed, and it must take over with a fresh,
        # strictly higher SQL fence_token.
        run(["docker", "kill", raft_containers[leader_1_id]])
        raft_alive.discard(leader_1_id)
        leader_2 = wait_single_leader(
            raft_containers, alive=raft_alive, min_term=leader_1["term"] + 1, timeout_s=25.0,
        )
        failover_ok = leader_2 is not None
        leader_2_id = leader_2["leader_id"] if failover_ok else ""

        resumed_qlogs_svc = temp_root / "resumed-service-qlogs"
        resumed_qlogs_client = temp_root / "resumed-client-qlogs"
        for path in (resumed_qlogs_svc, resumed_qlogs_client):
            path.mkdir(parents=True, exist_ok=True)
        resumed_ready = False
        resume_client = None
        resumed_exit = -1
        resumed_service: dict[str, Any] = {}
        if failover_ok:
            resumed_name = f"fleetrmw-raft-lease-resumed-{suffix}"
            gateway_names.append(resumed_name)
            resumed_ready = start_service(
                root=root, image=image, network=network, name=resumed_name,
                alias=GATEWAY_ALIAS,
                command=gateway_service_command(
                    root=root, temp_root=temp_root, index=1, raft_alias=leader_2_id,
                    qlogs=resumed_qlogs_svc, raft_wait_timeout_ms=10000,
                    sql_wait_timeout_ms=15000,
                ),
            )
            resume_client = run_client(
                root=root, image=image, network=network, install=install,
                name=f"fleetrmw-raft-lease-resume-{suffix}", certs=certs,
                qlogs=resumed_qlogs_client, mode="resume",
            ) if resumed_ready else None
            resumed_exit, _, resumed_service = stop_service(resumed_name)
            gateway_names.remove(resumed_name)

        resumed_ok = (
            failover_ok and resumed_ready
            and resume_client is not None and resume_client.returncode == 0
            and probe_ok(
                json_rows(resume_client.stdout)[-1] if json_rows(resume_client.stdout) else {},
                "resume",
            )
            and resumed_exit == 0
            and gateway_lease_ok(resumed_service, raft_node_id=leader_2_id, min_term=leader_2["term"] if leader_2 else -1)
        )
        old_fence_token = (
            active_service.get("metrics", {}).get("durable_state", {})
            .get("writer_lease", {}).get("fence_token")
        )
        new_fence_token = (
            resumed_service.get("metrics", {}).get("durable_state", {})
            .get("writer_lease", {}).get("fence_token")
        )
        fence_token_advanced = (
            isinstance(old_fence_token, int) and isinstance(new_fence_token, int)
            and new_fence_token > old_fence_token
        )

        ok = (
            non_leader_fails_closed and active_ok and failover_ok
            and resumed_ok and fence_token_advanced
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "consensus_scheme": "native fleetqox.raft (no etcd, no PostgreSQL)",
            "real_quic_v1_h3": True,
            "raft_initial_leader": leader_1_id,
            "raft_post_failover_leader": leader_2_id,
            "non_leader_gateway_fails_closed_claim": non_leader_fails_closed,
            "raft_leader_gateway_accepts_durable_write_claim": active_ok,
            "raft_leader_failure_triggers_gateway_failover_claim": failover_ok,
            "resumed_gateway_recovers_durable_state_claim": resumed_ok,
            "raft_derived_fence_token_strictly_advances_claim": fence_token_advanced,
            "old_fence_token": old_fence_token,
            "new_fence_token": new_fence_token,
            "active_service": active_service,
            "resumed_service": resumed_service,
        }
        return result
    finally:
        for name in gateway_names:
            run(["docker", "rm", "-f", name])
        for container in raft_containers.values():
            run(["docker", "rm", "-f", container])
        run(["docker", "network", "rm", network])
        cleanup = run([
            "docker", "run", "--rm", "--entrypoint", "bash",
            "-v", f"{root}:/work", image, "-lc",
            f"rm -rf {build_root} {install} {log_root}",
        ])
        if cleanup.returncode == 0:
            shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_quic_gateway_raft_writer_lease_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image)
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"status={summary['status']}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
