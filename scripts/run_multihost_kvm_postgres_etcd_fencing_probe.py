#!/usr/bin/env python3
"""Prove the etcd + PostgreSQL + STONITH-fencing HA stack across two REAL,
separate hosts -- two independent KVM VMs, each with its own kernel and its
own Docker daemon -- rather than one Docker daemon on one machine.

This is the etcd/PostgreSQL counterpart to
run_multihost_kvm_raft_consensus_probe.py, closing the same administrative
audit gap (NV-09: "tach failure domain khoi single Docker daemon") for the
project's OTHER, older HA path -- the one actually named in the audit
(etcd DCS + PostgreSQL streaming replication + Docker-socket STONITH).

This path is materially harder to port than the Raft one: the existing
fence agent (fleetqox_postgres_fence_agent.py) fences a container by
calling the Docker Engine API over the LOCAL Unix socket, which cannot
reach a container running on a different host's Docker daemon. Making
cross-host fencing real (not simulated) required teaching docker_call() a
second transport -- a tcp://host:port Docker Engine API URL -- so the fence
agent on VM2 can reach into VM1's Docker daemon over the real network and
kill a container VM1 believes is still healthy. See docker_connection() in
fleetqox_postgres_fence_agent.py.

Topology:
  VM1 ("region A"): etcd1, PostgreSQL PRIMARY.
  VM2 ("region B"): etcd2, etcd3 (etcd majority), PostgreSQL STANDBY,
                    the failover controller, the fence agent.

VM1's Docker Engine API is exposed on its private-link IP only (see
run_multihost_kvm_setup.sh's sibling documentation in .multihost_vm) so the
fence agent's cross-host kill is a genuine remote call, not a shortcut.

The probe proves, over the real inter-VM network link:
  1. A 3-member etcd cluster reaches quorum with members split across two
     real hosts.
  2. PostgreSQL streaming replication works between a primary on one host
     and a standby on the other.
  3. Partitioning VM1 from VM2 at the network layer (iptables on VM1,
     while VM1's primary keeps running -- the classic "still alive but
     unreachable" split-brain hazard STONITH exists for, as opposed to a
     clean host crash) causes the controller on VM2 (majority etcd side)
     to detect the primary as unreachable, acquire the DCS lease, and
     fence the primary -- a REAL cross-host Docker-kill reaching a host
     that is still up and running.
  4. Only after the independent post-fence confirmation does the standby
     get promoted; the promoted standby then accepts new writes.
  5. Healing the partition afterward does not resurrect a second writer:
     the old primary container is dead (fenced), so there is only ever
     one write-accepting node -- no split-brain.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VM_DIR = ROOT / ".multihost_vm"
SCHEMA_VERSION = "fleetrmw.multihost_kvm_postgres_etcd_fencing_probe.v1"
SSH_KEY = VM_DIR / "id_multihost"
VM1_SSH_PORT = 12201
VM2_SSH_PORT = 12202
VM1_IP = "10.77.0.11"
VM2_IP = "10.77.0.12"
REMOTE_ROOT = "/home/ubuntu/RTC"
REMOTE_CERTS = f"{REMOTE_ROOT}/.multihost_pg_certs"
IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
POSTGRES_IMAGE = "postgres:16-alpine"
DATABASE_PASSWORD = "fleetqox-multihost-probe"
REPLICATION_APPLICATION = "fleetqox_multihost_standby"
REPLICATION_SLOT = "fleetqox_multihost_slot"
CONTROLLER_ID = "controller-1"

ETCD_MEMBERS = {
    "etcd1": {"ip": VM1_IP, "vm_port": VM1_SSH_PORT, "client_port": 2379, "peer_port": 2380},
    "etcd2": {"ip": VM2_IP, "vm_port": VM2_SSH_PORT, "client_port": 2379, "peer_port": 2380},
    "etcd3": {"ip": VM2_IP, "vm_port": VM2_SSH_PORT, "client_port": 2381, "peer_port": 2382},
}


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


def scp_to_vm(port: int, local_path: Path, remote_path: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "scp", "-i", str(SSH_KEY), "-P", str(port), "-r",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            str(local_path), f"ubuntu@127.0.0.1:{remote_path}",
        ],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False,
    )


def etcd_endpoints() -> str:
    return ",".join(
        f"https://{m['ip']}:{m['client_port']}" for m in ETCD_MEMBERS.values()
    )


def etcd_initial_cluster() -> str:
    return ",".join(
        f"{name}=https://{m['ip']}:{m['peer_port']}" for name, m in ETCD_MEMBERS.items()
    )


def generate_certs(cert_dir: Path) -> bool:
    cert_dir.mkdir(parents=True, exist_ok=True)

    def openssl(args: list[str]) -> bool:
        return subprocess.run(
            ["openssl", *args], cwd=cert_dir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        ).returncode == 0

    ok = openssl([
        "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", "ca.key", "-out", "ca.crt", "-subj", "/CN=FleetQoX-multihost-CA",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-days", "2",
    ])
    ok = ok and openssl([
        "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-keyout", "etcd-server.key", "-out", "etcd-server.csr", "-subj", "/CN=fleetqox-etcd",
        "-addext", f"subjectAltName=IP:{VM1_IP},IP:{VM2_IP}",
        "-addext", "extendedKeyUsage=serverAuth,clientAuth",
    ])
    ok = ok and openssl([
        "x509", "-req", "-in", "etcd-server.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
        "-CAcreateserial", "-out", "etcd-server.crt", "-days", "2", "-copy_extensions", "copy",
    ])
    ok = ok and openssl([
        "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-keyout", "fence-server.key", "-out", "fence-server.csr",
        "-subj", "/CN=fleetqox-fence-agent",
        "-addext", f"subjectAltName=IP:{VM2_IP}",
        "-addext", "extendedKeyUsage=serverAuth",
    ])
    ok = ok and openssl([
        "x509", "-req", "-in", "fence-server.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
        "-CAcreateserial", "-out", "fence-server.crt", "-days", "2", "-copy_extensions", "copy",
    ])
    ok = ok and openssl([
        "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-keyout", f"{CONTROLLER_ID}.key", "-out", f"{CONTROLLER_ID}.csr",
        "-subj", f"/CN={CONTROLLER_ID}", "-addext", "extendedKeyUsage=clientAuth",
    ])
    ok = ok and openssl([
        "x509", "-req", "-in", f"{CONTROLLER_ID}.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
        "-CAcreateserial", "-out", f"{CONTROLLER_ID}.crt", "-days", "2", "-copy_extensions", "copy",
    ])
    return ok


def sql(vm_port: int, container: str, query: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    remote_command = (
        f"sudo docker exec -e PGPASSWORD={DATABASE_PASSWORD} {container} "
        f"psql -U postgres -d fleetqox -v ON_ERROR_STOP=1 -Atc \"{query}\""
    )
    return run_remote(vm_port, remote_command, timeout=timeout)


def wait_postgres(vm_port: int, container: str, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    consecutive_ready = 0
    while time.monotonic() < deadline:
        checked = run_remote(
            vm_port,
            f"sudo docker exec {container} pg_isready -U postgres -d fleetqox",
            timeout=5.0,
        )
        if checked.returncode == 0:
            consecutive_ready += 1
            if consecutive_ready >= 3:
                return True
            time.sleep(0.2)
            continue
        consecutive_ready = 0
        time.sleep(0.3)
    return False


def start_etcd_members() -> bool:
    # --network host, not -p ip:port:port: two etcd members (etcd2, etcd3)
    # sit on the SAME VM2 host, and dialing a sibling container's
    # published port from another container on that host gets hairpin-NAT
    # rewritten to the docker0 bridge gateway address (172.17.0.1) --
    # confirmed directly in etcd2's own logs ("rejected connection ...
    # certificate is valid for 10.77.0.11, 10.77.0.12, not 172.17.0.1").
    # etcd's peer TLS then rejects it as an unrecognized peer, which is
    # invisible right up until a real election is needed (the existing
    # leader being reachable hides it completely). --network host makes
    # every container bind the VM's real interface directly, so same-host
    # peer traffic never goes through Docker's bridge/NAT at all.
    ok = True
    for name, m in ETCD_MEMBERS.items():
        remote_command = (
            f"sudo docker run -d --name fleetrmw-mh-{name} --network host "
            f"-v {REMOTE_CERTS}:/certs "
            "quay.io/coreos/etcd:v3.5.9 etcd "
            f"--name {name} "
            f"--data-dir /tmp/{name}.etcd "
            f"--initial-advertise-peer-urls https://{m['ip']}:{m['peer_port']} "
            f"--listen-peer-urls https://0.0.0.0:{m['peer_port']} "
            f"--advertise-client-urls https://{m['ip']}:{m['client_port']} "
            f"--listen-client-urls https://0.0.0.0:{m['client_port']} "
            f"--initial-cluster {etcd_initial_cluster()} "
            "--initial-cluster-state new --initial-cluster-token fleetrmw-multihost "
            "--cert-file /certs/etcd-server.crt --key-file /certs/etcd-server.key "
            "--peer-cert-file /certs/etcd-server.crt --peer-key-file /certs/etcd-server.key "
            "--trusted-ca-file /certs/ca.crt --peer-trusted-ca-file /certs/ca.crt "
            "--client-cert-auth --peer-client-cert-auth"
        )
        started = run_remote(m["vm_port"], remote_command)
        ok = ok and started.returncode == 0
    return ok


def etcd_healthy(timeout_s: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = run_remote(
            VM1_SSH_PORT,
            f"sudo docker exec fleetrmw-mh-etcd1 etcdctl "
            f"--cacert=/certs/ca.crt --cert=/certs/etcd-server.crt --key=/certs/etcd-server.key "
            f"--endpoints={etcd_endpoints()} endpoint health --cluster -w json",
            timeout=10.0,
        )
        if result.returncode == 0:
            try:
                rows = json.loads(result.stdout)
                if isinstance(rows, list) and len(rows) == 3 and all(
                    row.get("health") is True for row in rows
                ):
                    return True
            except json.JSONDecodeError:
                pass
        time.sleep(1.0)
    return False


def start_postgres_cluster() -> dict[str, Any]:
    primary_start = run_remote(
        VM1_SSH_PORT,
        f"sudo docker run -d --name fleetrmw-mh-pg-primary --network host "
        f"-e POSTGRES_PASSWORD={DATABASE_PASSWORD} -e POSTGRES_DB=fleetqox "
        f"{POSTGRES_IMAGE} -c wal_level=replica -c max_wal_senders=10 "
        "-c max_replication_slots=10 -c synchronous_commit=on",
    )
    primary_ready = primary_start.returncode == 0 and wait_postgres(VM1_SSH_PORT, "fleetrmw-mh-pg-primary")
    role = subprocess.CompletedProcess([], 1, "", "primary_not_ready")
    hba = subprocess.CompletedProcess([], 1, "", "primary_not_ready")
    standby_start = subprocess.CompletedProcess([], 1, "", "primary_not_ready")
    standby_ready = False
    if primary_ready:
        role = sql(
            VM1_SSH_PORT, "fleetrmw-mh-pg-primary",
            f"CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '{DATABASE_PASSWORD}'",
        )
        hba = run_remote(
            VM1_SSH_PORT,
            "sudo docker exec fleetrmw-mh-pg-primary sh -c "
            # $PGDATA must be escaped here so it survives the remote SSH
            # login shell's parsing unexpanded (that shell has no such
            # variable) and is only expanded by the inner `sh -c` running
            # inside the container, where PGDATA is actually set. Without
            # the escape, the outer shell expands it to an empty string
            # and the append silently lands on the wrong path.
            "\"printf 'host replication replicator all scram-sha-256\\n' >> "
            "\\$PGDATA/pg_hba.conf\"",
        )
        if role.returncode == 0 and hba.returncode == 0:
            sql(VM1_SSH_PORT, "fleetrmw-mh-pg-primary", "SELECT pg_reload_conf()")
            # pg_reload_conf() returns as soon as the reload is requested,
            # not once the postmaster has actually re-read pg_hba.conf and
            # applied it -- on the same Docker host that gap is small
            # enough to never lose the race, but the extra SSH round trip
            # to a real second host was enough to observe pg_basebackup
            # arrive first and get rejected with "no pg_hba.conf entry".
            time.sleep(1.5)
            bootstrap = (
                'mkdir -p "$PGDATA" && '
                "chown -R postgres:postgres /var/lib/postgresql/data && "
                "gosu postgres pg_basebackup "
                f'-d "host={VM1_IP} port=5432 user=replicator '
                f"password={DATABASE_PASSWORD} application_name="
                f'{REPLICATION_APPLICATION}" '
                '-D "$PGDATA" -Fp -Xs -P -R '
                f"-C -S {REPLICATION_SLOT} && "
                'chmod 700 "$PGDATA" && '
                'exec gosu postgres postgres -D "$PGDATA" -c hot_standby=on'
            )
            standby_start = run_remote(
                VM2_SSH_PORT,
                f"sudo docker run -d --name fleetrmw-mh-pg-standby --network host "
                "-e PGDATA=/var/lib/postgresql/data/pgdata "
                f"-e PGPASSWORD={DATABASE_PASSWORD} "
                f"--entrypoint sh {POSTGRES_IMAGE} -c '{bootstrap}'",
            )
            standby_ready = (
                standby_start.returncode == 0
                and wait_postgres(VM2_SSH_PORT, "fleetrmw-mh-pg-standby")
            )
    sync_configured = False
    replication_row = ""
    if standby_ready:
        configured = sql(
            VM1_SSH_PORT, "fleetrmw-mh-pg-primary",
            f"ALTER SYSTEM SET synchronous_standby_names = '{REPLICATION_APPLICATION}'",
        )
        reloaded = sql(VM1_SSH_PORT, "fleetrmw-mh-pg-primary", "SELECT pg_reload_conf()")
        if configured.returncode == 0 and reloaded.returncode == 0:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                status = sql(
                    VM1_SSH_PORT, "fleetrmw-mh-pg-primary",
                    "SELECT application_name || '|' || state || '|' || sync_state "
                    "FROM pg_stat_replication WHERE application_name="
                    f"'{REPLICATION_APPLICATION}'",
                )
                replication_row = status.stdout.strip()
                if replication_row == f"{REPLICATION_APPLICATION}|streaming|sync":
                    sync_configured = True
                    break
                time.sleep(0.3)
    ok = primary_ready and standby_ready and sync_configured
    return {
        "status": "ok" if ok else "failed",
        "primary_ready": primary_ready,
        "standby_ready": standby_ready,
        "sync_configured": sync_configured,
        "replication_row": replication_row,
        "primary_stderr": primary_start.stderr[-1000:],
        "standby_stderr": standby_start.stderr[-1000:],
    }


def start_fence_agent() -> bool:
    remote_command = (
        f"sudo docker run -d --name fleetrmw-mh-fence-agent --network host "
        f"-v {REMOTE_ROOT}:/work -v {REMOTE_CERTS}:/certs -w /work "
        f"--entrypoint python3 {IMAGE} scripts/fleetqox_postgres_fence_agent.py "
        f"--host 0.0.0.0 --port 4510 "
        "--target-container fleetrmw-mh-pg-primary "
        f"--docker-socket tcp://{VM1_IP}:2375 "
        "--tls-ca /certs/ca.crt --tls-cert /certs/fence-server.crt --tls-key /certs/fence-server.key "
        f"--etcd-endpoints {etcd_endpoints()} "
        "--etcd-ca /certs/ca.crt "
        f"--etcd-cert /certs/{CONTROLLER_ID}.crt --etcd-key /certs/{CONTROLLER_ID}.key"
    )
    started = run_remote(VM2_SSH_PORT, remote_command)
    if started.returncode != 0:
        return False
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        logs = run_remote(VM2_SSH_PORT, "sudo docker logs fleetrmw-mh-fence-agent", timeout=5.0)
        if '"status": "ready"' in logs.stdout:
            return True
        time.sleep(0.3)
    return False


def start_controller() -> bool:
    primary_dsn = f"postgresql://postgres:{DATABASE_PASSWORD}@{VM1_IP}:5432/fleetqox"
    standby_dsn = f"postgresql://postgres:{DATABASE_PASSWORD}@{VM2_IP}:5432/fleetqox"
    remote_command = (
        f"sudo docker run -d --name fleetrmw-mh-controller --network host "
        f"-v {REMOTE_ROOT}:/work -v {REMOTE_CERTS}:/certs -w /work "
        f"--entrypoint python3 {IMAGE} scripts/fleetqox_postgres_failover_controller.py "
        f"--controller-id {CONTROLLER_ID} "
        f"--primary-dsn {primary_dsn} --standby-dsn {standby_dsn} "
        f"--etcd-endpoints {etcd_endpoints()} "
        f"--etcd-ca /certs/ca.crt --etcd-cert /certs/{CONTROLLER_ID}.crt --etcd-key /certs/{CONTROLLER_ID}.key "
        f"--fence-url https://{VM2_IP}:4510/fence "
        f"--fence-ca /certs/ca.crt --fence-cert /certs/{CONTROLLER_ID}.crt --fence-key /certs/{CONTROLLER_ID}.key "
        "--failure-threshold 3 --poll-ms 200 --dcs-timeout-ms 800 --max-runtime-ms 40000"
    )
    started = run_remote(VM2_SSH_PORT, remote_command)
    if started.returncode != 0:
        return False
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        logs = run_remote(VM2_SSH_PORT, "sudo docker logs fleetrmw-mh-controller", timeout=5.0)
        if '"status": "monitoring"' in logs.stdout:
            return True
        time.sleep(0.3)
    return False


def controller_final_result(timeout_s: float = 45.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        inspected = run_remote(
            VM2_SSH_PORT, "sudo docker inspect -f {{.State.Running}} fleetrmw-mh-controller",
            timeout=5.0,
        )
        if inspected.returncode == 0 and inspected.stdout.strip() == "false":
            break
        time.sleep(0.5)
    logs = run_remote(VM2_SSH_PORT, "sudo docker logs fleetrmw-mh-controller", timeout=10.0)
    last_line = ""
    for line in logs.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            last_line = line
    if not last_line:
        return None
    try:
        return json.loads(last_line)
    except json.JSONDecodeError:
        return None


def partition_vm1_from_vm2() -> bool:
    # All fenceable services run with --network host (see
    # start_etcd_members's comment: two etcd members on the same VM2 host
    # talking via a docker-published port get hairpin-NAT'd to the bridge
    # gateway address and fail etcd's peer TLS check), so their traffic
    # goes through the VM's own INPUT/OUTPUT chains directly -- no
    # DOCKER-USER/FORWARD involved. An explicit ACCEPT for port 2375
    # (dockerd's own TCP API, listening on the host directly, never a
    # published container port) is inserted ahead of the blanket DROP so
    # the fence agent's cross-host kill keeps working -- the actual
    # scenario STONITH exists for: the application/replication data plane
    # goes dark while a separate management plane (real deployments use a
    # BMC/IPMI path; here, the Docker Engine API) stays reachable.
    result = run_remote(
        VM1_SSH_PORT,
        f"sudo iptables -I INPUT -p tcp -s {VM2_IP} --dport 2375 -j ACCEPT && "
        f"sudo iptables -I OUTPUT -p tcp -d {VM2_IP} --sport 2375 -j ACCEPT && "
        f"sudo iptables -A INPUT -s {VM2_IP} -j DROP && "
        f"sudo iptables -A OUTPUT -d {VM2_IP} -j DROP",
    )
    return result.returncode == 0


def heal_partition() -> bool:
    result = run_remote(
        VM1_SSH_PORT,
        f"sudo iptables -D INPUT -s {VM2_IP} -j DROP; "
        f"sudo iptables -D OUTPUT -d {VM2_IP} -j DROP; "
        f"sudo iptables -D INPUT -p tcp -s {VM2_IP} --dport 2375 -j ACCEPT; "
        f"sudo iptables -D OUTPUT -p tcp -d {VM2_IP} --sport 2375 -j ACCEPT; true",
    )
    return result.returncode == 0


def primary_container_running_on_vm1() -> bool | None:
    result = run_remote(
        VM1_SSH_PORT,
        "sudo docker inspect -f {{.State.Running}} fleetrmw-mh-pg-primary",
        timeout=10.0,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() == "true"


def cleanup() -> None:
    for name in (
        "fleetrmw-mh-controller", "fleetrmw-mh-fence-agent",
        "fleetrmw-mh-pg-primary", "fleetrmw-mh-etcd1",
    ):
        run_remote(VM1_SSH_PORT, f"sudo docker rm -f {name}", timeout=15.0)
    for name in (
        "fleetrmw-mh-controller", "fleetrmw-mh-fence-agent",
        "fleetrmw-mh-pg-standby", "fleetrmw-mh-etcd2", "fleetrmw-mh-etcd3",
    ):
        run_remote(VM2_SSH_PORT, f"sudo docker rm -f {name}", timeout=15.0)
    heal_partition()


def run_probe() -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "failed"}
    cleanup()

    with tempfile.TemporaryDirectory() as tmp:
        cert_dir = Path(tmp) / "certs"
        if not generate_certs(cert_dir):
            result["stage"] = "generate_certs"
            return result
        for port in (VM1_SSH_PORT, VM2_SSH_PORT):
            run_remote(port, f"rm -rf {REMOTE_CERTS} && mkdir -p {REMOTE_CERTS}")
            uploaded = scp_to_vm(port, cert_dir, f"{REMOTE_ROOT}/.multihost_pg_certs_upload")
            if uploaded.returncode != 0:
                result["stage"] = f"scp_certs_{port}"
                result["stderr"] = uploaded.stderr[-2000:]
                return result
            run_remote(
                port,
                f"rm -rf {REMOTE_CERTS} && "
                f"mv {REMOTE_ROOT}/.multihost_pg_certs_upload/certs {REMOTE_CERTS}",
            )

    try:
        if not start_etcd_members():
            result["stage"] = "start_etcd"
            return result
        if not etcd_healthy():
            result["stage"] = "etcd_healthy"
            return result
        result["etcd_multi_host_quorum_ok"] = True

        pg_result = start_postgres_cluster()
        result["postgres_cluster"] = pg_result
        if pg_result["status"] != "ok":
            result["stage"] = "start_postgres"
            return result

        write_result = sql(VM1_SSH_PORT, "fleetrmw-mh-pg-primary", "SELECT 1")
        insert_result = sql(
            VM1_SSH_PORT, "fleetrmw-mh-pg-primary",
            "CREATE TABLE IF NOT EXISTS probe_rows (k text PRIMARY KEY, v text); "
            "INSERT INTO probe_rows VALUES ('k1', 'v1') "
            "ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
        )
        result["baseline_write_ok"] = write_result.returncode == 0 and insert_result.returncode == 0
        time.sleep(1.0)
        replicated = sql(
            VM2_SSH_PORT, "fleetrmw-mh-pg-standby",
            "SELECT v FROM probe_rows WHERE k = 'k1'",
        )
        result["baseline_replication_ok"] = replicated.stdout.strip() == "v1"

        if not start_fence_agent():
            result["stage"] = "start_fence_agent"
            return result
        if not start_controller():
            result["stage"] = "start_controller"
            return result

        # The real cross-host event: VM1 keeps running (its primary is
        # still genuinely alive and could still accept writes) but is
        # network-partitioned from VM2 -- the actual hazard STONITH
        # exists for, distinct from a clean host crash where there is
        # nothing left to fence.
        result["partition_applied"] = partition_vm1_from_vm2()

        controller_result = controller_final_result()
        result["controller_result"] = controller_result
        promoted_ok = (
            controller_result is not None
            and controller_result.get("status") == "promoted"
            and controller_result.get("hard_fence_confirmed") is True
        )
        result["cross_host_fence_and_promote_ok"] = promoted_ok

        # Independent confirmation, NOT taken from the controller/fence
        # agent's own say-so: query VM1 directly (still reachable via SSH
        # even though its Docker-data-plane link to VM2 is cut) to see
        # whether the primary container the fence agent claims to have
        # killed is actually dead.
        result["primary_actually_dead_on_vm1"] = primary_container_running_on_vm1() is False

        new_write_ok = False
        if promoted_ok:
            new_write = sql(
                VM2_SSH_PORT, "fleetrmw-mh-pg-standby",
                "INSERT INTO probe_rows VALUES ('k2', 'v2') "
                "ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
            )
            new_write_ok = new_write.returncode == 0
        result["post_promotion_write_ok"] = new_write_ok

        result["partition_healed"] = heal_partition()

        ok = (
            result.get("etcd_multi_host_quorum_ok") is True
            and pg_result["status"] == "ok"
            and result.get("baseline_write_ok") is True
            and result.get("baseline_replication_ok") is True
            and result.get("cross_host_fence_and_promote_ok") is True
            and result.get("primary_actually_dead_on_vm1") is True
            and result.get("post_promotion_write_ok") is True
        )
        result["status"] = "ok" if ok else "failed"
        result["multi_host_postgres_etcd_fencing_claim"] = ok
        result["multi_host_cross_host_stonith_claim"] = ok
        return result
    finally:
        cleanup()


def main() -> int:
    summary = run_probe()
    output = ROOT / "results_rmw_socket" / "multihost_kvm_postgres_etcd_fencing_probe_summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
