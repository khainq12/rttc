#!/usr/bin/env python3
"""Prove the native fleetqox.raft consensus core across two REAL, separate
hosts -- two independent KVM virtual machines, each with its own kernel and
its own Docker daemon, connected over a real (virtio) network link between
them -- rather than five containers sharing one Docker daemon.

This directly closes the gap the administrative audit
(FleetRMW_FleetQoX_Bao_cao_hanh_chinh.docx, NV-09) called out: prior
split-brain/fencing evidence ran entirely inside one Docker daemon on one
machine, so it could not exclude a shared-kernel/shared-daemon confound.
Here, killing "a host" means killing an actual QEMU virtual machine process
from outside it -- the surviving side observes the loss only through the
network, exactly as a real datacenter host failure would look.

Topology: 5-node Raft cluster (same fleetqox/raft.py core used by the
single-host docker_raft_consensus_probe), split 2 nodes on VM1 and 3 nodes
on VM2. Proves, across the real VM boundary:

  1. A single leader is elected across both VMs.
  2. A write on the leader replicates to all 5 nodes on both VMs.
  3. Killing VM1 (the whole QEMU process, not just its containers) leaves
     the 3-node majority on VM2 to elect a new leader at a higher term and
     keep serving writes with no data loss -- genuine cross-host failover.
  4. With VM1 gone, additionally killing one of VM2's 3 remaining nodes
     drops the live set to 2 of the original 5 -- a minority -- and the
     cluster must correctly refuse new writes (fail-closed, no
     split-brain) rather than let the minority elect its own leader.

Requires scripts/run_multihost_kvm_setup.sh to have already provisioned
vm1/vm2 (see .multihost_vm/ for SSH keys and VM connection details).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VM_DIR = ROOT / ".multihost_vm"
SCHEMA_VERSION = "fleetrmw.multihost_kvm_raft_consensus_probe.v1"
SSH_KEY = VM_DIR / "id_multihost"
VM1_SSH_PORT = 12201
VM2_SSH_PORT = 12202
VM1_IP = "10.77.0.11"
VM2_IP = "10.77.0.12"
REMOTE_ROOT = "/home/ubuntu/RTC"
IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"

# 2 nodes on VM1 (minority host), 3 nodes on VM2 (majority host).
VM1_NODES = ("n1", "n2")
VM2_NODES = ("n3", "n4", "n5")
ALL_NODES = VM1_NODES + VM2_NODES
NODE_VM = {**{n: "vm1" for n in VM1_NODES}, **{n: "vm2" for n in VM2_NODES}}
NODE_IP = {**{n: VM1_IP for n in VM1_NODES}, **{n: VM2_IP for n in VM2_NODES}}
NODE_PORT = {"n1": 5001, "n2": 5002, "n3": 5003, "n4": 5004, "n5": 5005}


def ssh_cmd(port: int) -> list[str]:
    return [
        "ssh", "-i", str(SSH_KEY), "-p", str(port),
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=5",
        "ubuntu@127.0.0.1",
    ]


def run_remote(port: int, remote_command: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ssh_cmd(port) + [remote_command],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False,
    )


def run_local(command: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False,
    )


def node_peer_url(node_id: str) -> str:
    return f"http://{NODE_IP[node_id]}:{NODE_PORT[node_id]}"


def container_name(node_id: str) -> str:
    return f"fleetrmw-mh-raft-{node_id}"


def start_node(node_id: str) -> subprocess.CompletedProcess[str]:
    vm_port = VM1_SSH_PORT if NODE_VM[node_id] == "vm1" else VM2_SSH_PORT
    vm_ip = NODE_IP[node_id]
    port = NODE_PORT[node_id]
    peer_args = " ".join(
        f"--peer {peer}={node_peer_url(peer)}" for peer in ALL_NODES if peer != node_id
    )
    # VM1's nodes get a much shorter election timeout than VM2's, so the
    # *initial* leader is deterministically one of VM1's two nodes rather
    # than left to chance. That makes killing VM1 below a genuine,
    # guaranteed failover-with-re-election test -- not a coin flip that
    # might happen to land on a VM2 node and merely prove leader
    # continuity instead of actual re-election.
    if NODE_VM[node_id] == "vm1":
        timeout_args = "--election-timeout-min-ms 400 --election-timeout-max-ms 600"
    else:
        timeout_args = "--election-timeout-min-ms 1500 --election-timeout-max-ms 2000"
    remote_command = (
        f"sudo docker run -d --name {container_name(node_id)} "
        f"-p {vm_ip}:{port}:5000 "
        f"-v {REMOTE_ROOT}:/work -w /work --entrypoint python3 {IMAGE} "
        f"scripts/fleetqox_raft_node_service.py "
        f"--host 0.0.0.0 --port 5000 --node-id {node_id} {timeout_args} {peer_args}"
    )
    return run_remote(vm_port, remote_command)


def prober_query(url: str, method: str = "GET", payload: dict[str, Any] | None = None,
                  timeout_s: float = 8.0) -> tuple[int, dict[str, Any] | None]:
    # Routed through VM2, which stays alive through the planned VM1-kill
    # scenario below and can reach both VM1's and VM2's private IPs over
    # the real inter-VM link -- so this is a genuine cross-host HTTP call
    # whenever the target is on VM1.
    curl = f"curl -s --max-time {int(timeout_s) + 1} -w '\\n%{{http_code}}'"
    if method == "POST":
        curl += f" -X POST -d '{json.dumps(payload or {})}'"
    curl += f" {url}"
    result = run_remote(VM2_SSH_PORT, curl, timeout=timeout_s + 5)
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


def get_status(node_id: str) -> dict[str, Any] | None:
    status, document = prober_query(f"{node_peer_url(node_id)}/status")
    return document if status == 200 else None


def get_value(node_id: str, key: str) -> str | None:
    status, document = prober_query(f"{node_peer_url(node_id)}/kv?key={key}")
    return document.get("value") if status == 200 and document is not None else None


def put_value(node_id: str, key: str, value: str, timeout_s: float = 8.0) -> tuple[int, dict[str, Any] | None]:
    return prober_query(
        f"{node_peer_url(node_id)}/kv", method="POST",
        payload={"key": key, "value": value}, timeout_s=timeout_s,
    )


def wait_ready(nodes: list[str], timeout_s: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if all(get_status(n) is not None for n in nodes):
            return True
        time.sleep(0.5)
    return False


def wait_single_leader(
    alive: list[str], *, min_term: int = 0, timeout_s: float = 20.0,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        statuses = {n: get_status(n) for n in alive}
        leaders = [
            (n, doc) for n, doc in statuses.items()
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


def cleanup_containers() -> None:
    for node_id in VM1_NODES:
        run_remote(VM1_SSH_PORT, f"sudo docker rm -f {container_name(node_id)}", timeout=15.0)
    for node_id in VM2_NODES:
        run_remote(VM2_SSH_PORT, f"sudo docker rm -f {container_name(node_id)}", timeout=15.0)


def vm1_pid() -> int | None:
    pid_file = VM_DIR / "vm1.pid"
    if not pid_file.is_file():
        return None
    try:
        return int(pid_file.read_text().strip())
    except ValueError:
        return None


def vm1_alive() -> bool:
    pid = vm1_pid()
    if pid is None:
        return False
    return run_local(["kill", "-0", str(pid)]).returncode == 0


def run_probe() -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "failed"}
    result["vm1_alive_before"] = vm1_alive()
    if not result["vm1_alive_before"]:
        result["stage"] = "vm1_not_running"
        return result

    cleanup_containers()
    try:
        for node_id in ALL_NODES:
            started = start_node(node_id)
            if started.returncode != 0:
                result["stage"] = f"start_{node_id}"
                result["stderr"] = started.stderr[-2000:]
                return result

        if not wait_ready(list(ALL_NODES)):
            result["stage"] = "wait_ready"
            return result

        alive = list(ALL_NODES)
        leader_1 = wait_single_leader(alive)
        if leader_1 is None:
            result["stage"] = "initial_election"
            return result
        result["leader_1"] = leader_1
        result["leader_1_vm"] = NODE_VM[leader_1["leader_id"]]

        write_status, write_body = put_value(leader_1["leader_id"], "k1", "v1")
        write_1_ok = (
            write_status == 200 and write_body is not None
            and write_body.get("status") == "committed"
        )
        time.sleep(0.5)
        replication_1_ok = all(get_value(n, "k1") == "v1" for n in ALL_NODES)
        result["write_1_ok"] = write_1_ok
        result["replication_1_ok"] = replication_1_ok

        # The real cross-host event: kill the whole VM1 QEMU process from
        # the host side, outside either VM's own OS. VM2's 3 nodes (a
        # genuine majority of the original 5) must notice VM1 is gone only
        # through the network, elect a new leader among themselves, and
        # keep serving writes with the already-committed value intact.
        kill_result = run_local(["kill", "-9", str(vm1_pid())])
        result["vm1_kill_ret"] = kill_result.returncode
        time.sleep(2.0)
        result["vm1_alive_after_kill"] = vm1_alive()

        survivors = list(VM2_NODES)
        leader_2 = wait_single_leader(
            survivors, min_term=leader_1["term"] + 1, timeout_s=30.0,
        )
        failover_ok = (
            leader_2 is not None
            and leader_2["leader_id"] in VM2_NODES
            and NODE_VM[leader_2["leader_id"]] == "vm2"
        )
        result["leader_2"] = leader_2
        result["cross_host_failover_ok"] = failover_ok

        data_survived_ok = failover_ok and get_value(leader_2["leader_id"], "k1") == "v1"
        result["data_survived_host_loss_ok"] = data_survived_ok

        write_2_status, write_2_body = (
            put_value(leader_2["leader_id"], "k2", "v2") if failover_ok else (-1, None)
        )
        write_2_ok = (
            write_2_status == 200 and write_2_body is not None
            and write_2_body.get("status") == "committed"
        )
        time.sleep(0.5)
        replicated_2_ok = failover_ok and all(get_value(n, "k2") == "v2" for n in survivors)
        result["write_2_ok"] = write_2_ok
        result["replicated_2_ok"] = replicated_2_ok

        # Now also kill one of VM2's 3 nodes (a normal container kill --
        # VM2 itself stays up). Combined with VM1 already gone, only 2 of
        # the original 5 remain live: a minority. The invariant under test
        # is that a minority must NOT be able to commit a new write --
        # otherwise a partition could produce two divergent leaders (one
        # here, one on whatever holds the other 3) which is exactly
        # split-brain.
        quorum_ok = False
        if failover_ok:
            demoted = next(n for n in VM2_NODES if n != leader_2["leader_id"])
            run_remote(VM2_SSH_PORT, f"sudo docker kill {container_name(demoted)}", timeout=15.0)
            minority = [n for n in VM2_NODES if n != demoted]
            time.sleep(3.0)
            minority_write_status, _ = put_value(minority[0], "k3", "v3", timeout_s=6.0)
            no_new_leader_at_higher_term = wait_single_leader(
                minority, min_term=leader_2["term"] + 1, timeout_s=8.0,
            ) is None
            quorum_ok = minority_write_status != 200 and no_new_leader_at_higher_term
        result["minority_fail_closed_ok"] = quorum_ok

        ok = (
            result["vm1_alive_before"]
            and write_1_ok and replication_1_ok
            and result["vm1_alive_after_kill"] is False
            and failover_ok and data_survived_ok
            and write_2_ok and replicated_2_ok
            and quorum_ok
        )
        result["status"] = "ok" if ok else "failed"
        result["multi_host_kvm_raft_consensus_claim"] = ok
        result["multi_host_no_split_brain_claim"] = ok
        return result
    finally:
        for node_id in VM2_NODES:
            run_remote(VM2_SSH_PORT, f"sudo docker rm -f {container_name(node_id)}", timeout=15.0)


def main() -> int:
    summary = run_probe()
    output = ROOT / "results_rmw_socket" / "multihost_kvm_raft_consensus_probe_summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
