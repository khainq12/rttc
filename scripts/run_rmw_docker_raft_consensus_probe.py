#!/usr/bin/env python3
"""Prove the native fleetqox.raft consensus core over real separate processes.

Runs a genuine 5-node Raft cluster as five separate Docker containers (not
an in-process simulation -- see tests/test_raft.py for the deterministic
algorithm-safety tests) using scripts/fleetqox_raft_node_service.py, and
proves, entirely without etcd or PostgreSQL:

  1. Exactly one leader is elected among real, independently-running
     processes.
  2. A write is only accepted by the leader; a non-leader rejects it and
     names the actual leader.
  3. A committed write replicates to every node (read back from all five).
  4. Killing the leader container (docker kill, matching this project's
     existing STONITH primitive) triggers automatic re-election among the
     survivors, at a strictly higher term, with the previously committed
     value intact on the new leader -- no data loss across the failover.
  5. Disconnecting enough nodes from the Docker network that no side can
     reach a majority of the original five makes the cluster correctly
     unavailable for new writes (fail-closed, mirroring
     quic_gateway_quorum_loss_promotion_fail_closed_claim for the
     etcd/PostgreSQL path) -- and reconnecting them restores normal
     operation with no manual repair.

Every HTTP call this probe makes goes through `docker exec <container> curl
... http://127.0.0.1:5000/...` rather than a host-published port. This was
not a style choice: `docker network disconnect` on a container also breaks
its `-p` host port mapping, and reconnecting it does not reliably restore a
1:1 mapping back to the same host port either (observed directly while
building this probe -- a request to what should have been node A's host
port came back carrying node B's own node_id). `docker exec` reaches a
container directly through the Docker daemon, independent of whatever that
container's network attachment currently is, so it is the only reliable
way to keep talking to a specific node across a disconnect/reconnect cycle.

This is a genuinely independent consensus/replication implementation from
the etcd + PostgreSQL stack used elsewhere in this project (native
leader election, native replicated log, native state machine), not a
relabeling of it.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_raft_consensus_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
INTERNAL_PORT = 5000
NODE_IDS = ("n1", "n2", "n3", "n4", "n5")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )


def http_via_exec(
    container: str, method: str, path: str,
    payload: dict[str, Any] | None = None, timeout_s: float = 8.0,
) -> tuple[int, dict[str, Any] | None]:
    url = f"http://127.0.0.1:{INTERNAL_PORT}{path}"
    command = [
        "docker", "exec", container, "curl", "-s", "--max-time", str(int(timeout_s) + 1),
        "-w", "\n%{http_code}",
    ]
    if method == "POST":
        command += ["-X", "POST", "-d", json.dumps(payload or {})]
    command.append(url)
    result = run(command)
    if result.returncode != 0:
        return -1, None
    body, _, code = result.stdout.rpartition("\n")
    try:
        status = int(code.strip())
    except ValueError:
        return -1, None
    try:
        document = json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        document = None
    return status, document if isinstance(document, dict) else None


def get_status(container: str) -> dict[str, Any] | None:
    status, document = http_via_exec(container, "GET", "/status")
    return document if status == 200 else None


def get_value(container: str, key: str) -> str | None:
    status, document = http_via_exec(container, "GET", f"/kv?key={key}")
    return document.get("value") if status == 200 and document is not None else None


def put_value(
    container: str, key: str, value: str, timeout_s: float = 8.0,
) -> tuple[int, dict[str, Any] | None]:
    return http_via_exec(
        container, "POST", "/kv", {"key": key, "value": value}, timeout_s=timeout_s,
    )


def start_node(
    *, image: str, network: str, container: str, node_id: str, peers: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    peer_args: list[str] = []
    for peer_id, alias in peers.items():
        if peer_id == node_id:
            continue
        peer_args += ["--peer", f"{peer_id}=http://{alias}:{INTERNAL_PORT}"]
    return run([
        "docker", "run", "-d", "--name", container,
        "--network", network, "--network-alias", node_id,
        "-v", f"{ROOT}:/work", "-w", "/work",
        "--entrypoint", "python3", image,
        "scripts/fleetqox_raft_node_service.py",
        "--host", "0.0.0.0", "--port", str(INTERNAL_PORT), "--node-id", node_id,
        *peer_args,
    ])


def wait_ready(containers: dict[str, str], timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if all(get_status(container) is not None for container in containers.values()):
            return True
        time.sleep(0.5)
    return False


def wait_single_leader(
    containers: dict[str, str], *, alive: set[str], min_term: int = 0,
    timeout_s: float = 15.0,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        statuses = {
            node_id: get_status(containers[node_id])
            for node_id in containers if node_id in alive
        }
        leaders = [
            (node_id, doc) for node_id, doc in statuses.items()
            if doc is not None and doc.get("role") == "leader" and doc.get("term", -1) >= min_term
        ]
        if len(leaders) == 1:
            leader_id, leader_doc = leaders[0]
            agree = all(
                doc.get("leader_id") == leader_id and doc.get("term") == leader_doc["term"]
                for doc in statuses.values() if doc is not None
            )
            if agree:
                return {"leader_id": leader_id, **leader_doc}
        time.sleep(0.5)
    return None


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    suffix = str(os.getpid())
    network = f"fleetrmw-raft-net-{suffix}"
    containers = {node_id: f"fleetrmw-raft-{node_id}-{suffix}" for node_id in NODE_IDS}
    aliases = {node_id: node_id for node_id in NODE_IDS}
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "failed"}

    network_result = run(["docker", "network", "create", network])
    try:
        if network_result.returncode != 0:
            result["stage"] = "network_create"
            return result

        for node_id in NODE_IDS:
            started = start_node(
                image=image, network=network, container=containers[node_id],
                node_id=node_id, peers=aliases,
            )
            if started.returncode != 0:
                result["stage"] = f"start_{node_id}"
                result["stderr"] = started.stderr[-2000:]
                return result

        if not wait_ready(containers):
            result["stage"] = "wait_ready"
            return result

        alive = set(NODE_IDS)
        leader_1 = wait_single_leader(containers, alive=alive)
        if leader_1 is None:
            result["stage"] = "initial_election"
            return result

        # Property: only the leader accepts writes; a non-leader names the
        # real leader instead of silently failing.
        non_leader = next(node_id for node_id in alive if node_id != leader_1["leader_id"])
        rejected_status, rejected_body = put_value(containers[non_leader], "k1", "v1")
        non_leader_rejection_ok = (
            rejected_status == 409
            and rejected_body is not None
            and rejected_body.get("status") == "not_leader"
            and rejected_body.get("leader_id") == leader_1["leader_id"]
        )

        write_status, write_body = put_value(containers[leader_1["leader_id"]], "k1", "v1")
        write_1_ok = (
            write_status == 200 and write_body is not None
            and write_body.get("status") == "committed"
        )
        time.sleep(0.5)
        replication_1_ok = all(
            get_value(container, "k1") == "v1" for container in containers.values()
        )

        # Property: killing the leader (this project's real STONITH
        # primitive elsewhere is also a container kill) triggers automatic
        # re-election among the survivors at a strictly higher term, with
        # no loss of the already-committed value.
        killed_leader = leader_1["leader_id"]
        run(["docker", "kill", containers[killed_leader]])
        alive.discard(killed_leader)
        leader_2 = wait_single_leader(
            containers, alive=alive, min_term=leader_1["term"] + 1, timeout_s=20.0,
        )
        failover_ok = leader_2 is not None and leader_2["leader_id"] != killed_leader
        survived_ok = (
            failover_ok
            and get_value(containers[leader_2["leader_id"]], "k1") == "v1"
        )

        write_2_status, write_2_body = (
            put_value(containers[leader_2["leader_id"]], "k2", "v2")
            if failover_ok else (-1, None)
        )
        write_2_ok = (
            write_2_status == 200 and write_2_body is not None
            and write_2_body.get("status") == "committed"
        )
        time.sleep(0.5)
        replicated_2_ok = failover_ok and all(
            get_value(containers[node_id], "k2") == "v2" for node_id in alive
        )

        # Property: disconnect enough of the SURVIVING nodes that no side
        # can reach a majority of the original 5 -- the cluster must
        # become unavailable for new writes rather than let a minority
        # elect its own leader (which would be a split-brain).
        quorum_ok = False
        recovered_ok = False
        if failover_ok:
            # Isolate 2 of the 4 survivors from the network entirely.
            # Whichever side is left holding the (still-standing) leader
            # has 2 of the original 5 -- not a majority, so it must not be
            # able to commit anything new. The isolated pair also has only
            # 2 of 5 and, critically, cannot even complete an election of
            # its own (RequestVote needs a majority of the WHOLE cluster,
            # not just currently-reachable peers) -- a real minority
            # genuinely cannot self-promote, which is what actually rules
            # out a second, competing leader here.
            isolated = set(list(alive - {leader_2["leader_id"]})[:2])
            for node_id in isolated:
                run(["docker", "network", "disconnect", network, containers[node_id]])
            time.sleep(2.0)
            isolated_pair_stayed_leaderless = all(
                (doc := get_status(containers[node_id])) is not None
                and doc.get("role") != "leader"
                for node_id in isolated
            )
            blocked_status, _ = put_value(
                containers[leader_2["leader_id"]], "k3", "v3", timeout_s=8.0,
            )
            quorum_ok = isolated_pair_stayed_leaderless and blocked_status in (503, -1, 409)

            for node_id in isolated:
                # `docker network connect` without --alias does NOT restore
                # the --network-alias set at `docker run` time (confirmed
                # directly while building this probe: the reconnected
                # container's DNS alias comes back empty). Without it,
                # every OTHER node's peer URL for this one (http://n4:5000)
                # stops resolving, so the reconnected node can send RPCs
                # out but never receives any back -- it looks "healed" to
                # a human but is actually a one-way ghost that just spams
                # ever-higher-term elections forever, repeatedly deposing
                # the real leader since an all-empty-log cluster has no
                # other tie-breaker. Re-specifying --alias here is what
                # actually restores two-way reachability.
                run([
                    "docker", "network", "connect", "--alias", node_id,
                    network, containers[node_id],
                ])
            leader_3 = wait_single_leader(
                containers, alive=alive, min_term=leader_2["term"], timeout_s=25.0,
            )
            if leader_3 is not None:
                recovery_status, recovery_body = put_value(
                    containers[leader_3["leader_id"]], "k4", "v4",
                )
                recovered_ok = (
                    recovery_status == 200 and recovery_body is not None
                    and recovery_body.get("status") == "committed"
                )

        ok = (
            non_leader_rejection_ok and write_1_ok and replication_1_ok
            and failover_ok and survived_ok and write_2_ok and replicated_2_ok
            and quorum_ok and recovered_ok
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "consensus_scheme": "native fleetqox.raft (no etcd, no PostgreSQL)",
            "cluster_size": len(NODE_IDS),
            "initial_leader": leader_1,
            "non_leader_write_rejected_with_leader_hint_claim": non_leader_rejection_ok,
            "leader_write_committed_claim": write_1_ok,
            "replicated_to_all_nodes_claim": replication_1_ok,
            "leader_failure_triggers_reelection_claim": failover_ok,
            "post_failover_leader": leader_2,
            "committed_data_survives_leader_failure_claim": survived_ok,
            "post_failover_write_committed_claim": write_2_ok,
            "post_failover_replication_claim": replicated_2_ok,
            "quorum_loss_blocks_new_writes_claim": quorum_ok,
            "recovers_after_partition_heals_claim": recovered_ok,
            "native_automatic_leader_election_claim": ok,
            "native_consensus_backend_claim": ok,
            "native_distributed_database_claim": ok,
        }
        return result
    finally:
        for container in containers.values():
            run(["docker", "rm", "-f", container])
        run(["docker", "network", "rm", network])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_raft_consensus_probe_summary.json",
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
