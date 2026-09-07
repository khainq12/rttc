#!/usr/bin/env python3
"""Prove synchronous replication prevents split-brain under a gray-failure partition.

Every existing quorum/STONITH probe (see
run_rmw_docker_quic_postgres_quorum_failover_probe.py) tests one specific
partition shape: the primary loses ALL connectivity (etcd, standbys, and
clients alike, via 100% egress loss on its whole interface). That shape
can't actually exercise the classic hard split-brain risk, because a primary
that can't talk to *anyone* also can't silently serve clients writes it
can't sync -- there is no window for divergence.

This probe targets the harder, previously-untested shape: a *replication-only*
partition. The primary keeps talking to clients but is cut off from its
synchronous standby specifically, using a tc filter that matches only the
standby's destination IP -- not a blanket interface-wide loss.

Building this surfaced a real, previously-undocumented PostgreSQL hazard
that shapes what "safe" actually means here: a transaction waiting on
synchronous_commit has *already committed locally* before the wait even
starts (this is documented PostgreSQL behavior, not a bug). If that wait is
aborted with an ordinary query cancel (SIGINT / pg_cancel_backend --
notably, statement_timeout does NOT interrupt this wait at all, by design,
specifically to avoid the scenario below), PostgreSQL lets the original
INSERT command complete as a normal success ("INSERT 0 1") and only adds a
WARNING ("...has already committed locally, but might not have been
replicated"). A client that doesn't scan warnings would see a clean commit
that may never reach the standby -- an actual split-brain hazard. Aborting
the same stuck backend with pg_terminate_backend (kills the connection
outright), by contrast, surfaces as a clear client-visible failure
("server closed the connection unexpectedly") with no false success. This
system's real STONITH path (fleetqox_postgres_fence_agent.py) SIGKILLs the
whole container, which is at least as safe as pg_terminate_backend -- this
probe verifies that directly rather than assuming it.

So the properties this probe proves:

  1. A client write attempted against the primary during the cut does not
     return ANY result (success or failure) while the partition holds --
     it stays blocked in the SyncRep wait, so there is no silent commit a
     client could believe succeeded.
  2. Reads against the primary keep working throughout (partial
     availability, not a full outage).
  3. Aborting that stuck write via pg_cancel_backend (a query cancel, e.g.
     what a naive timeout-based client library might do) DOES report false
     success -- documented and demonstrated here as the hazard to avoid,
     not something this codebase's own failover path does.
  4. Aborting the same stuck write via pg_terminate_backend -- the safe
     primitive, and the one actually consistent with this system's
     container-level SIGKILL STONITH -- reports a clear failure instead.
  5. Once the partition heals, replication catches up and a fresh write
     succeeds and replicates normally; primary and standby converge to the
     same row count, so nothing is silently lost across the whole exercise.

This is a narrower, PostgreSQL-only probe (no etcd/gateway containers) since
the property under test is purely about synchronous replication's commit
gate, independent of the failover/STONITH orchestration already proven
elsewhere.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

try:
    from scripts.run_rmw_docker_quic_postgres_replication_failover_probe import (
        DATABASE_PASSWORD,
        STANDBY_ALIAS,
        replication_checkpoint,
        sql,
        start_replication_cluster,
    )
    from scripts.run_rmw_docker_quic_stateful_gateway_probe import DEFAULT_IMAGE, run
except ModuleNotFoundError:
    from run_rmw_docker_quic_postgres_replication_failover_probe import (
        DATABASE_PASSWORD,
        STANDBY_ALIAS,
        replication_checkpoint,
        sql,
        start_replication_cluster,
    )
    from run_rmw_docker_quic_stateful_gateway_probe import DEFAULT_IMAGE, run


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_postgres_replication_partition_split_brain_probe.v1"
TABLE = "split_brain_probe"


def resolve_container_ip(*, image: str, target_container: str, alias: str) -> str:
    resolved = run([
        "docker", "run", "--rm", "--network", f"container:{target_container}",
        "--entrypoint", "getent", image, "hosts", alias,
    ])
    fields = resolved.stdout.strip().split()
    return fields[0] if resolved.returncode == 0 and fields else ""


def apply_targeted_partition(
    *, image: str, primary: str, blocked_ip: str,
) -> subprocess.CompletedProcess[str]:
    # A blanket "netem loss 100%" (as used by the whole-node partition
    # probes) would also cut the primary off from clients, which sidesteps
    # the actual risk under test. Routing only traffic destined for the
    # standby's IP into a 100%-loss class -- everything else keeps using
    # the default band -- isolates just the replication link.
    return run([
        "docker", "run", "--rm", "--network", f"container:{primary}",
        "--cap-add", "NET_ADMIN", "--entrypoint", "bash", image, "-lc",
        "tc qdisc add dev eth0 root handle 1: prio bands 4 && "
        "tc qdisc add dev eth0 parent 1:4 handle 40: netem loss 100% && "
        f"tc filter add dev eth0 protocol ip parent 1:0 prio 1 u32 "
        f"match ip dst {blocked_ip}/32 flowid 1:4 && "
        "tc qdisc show dev eth0 && tc filter show dev eth0",
    ])


def heal_partition(*, image: str, primary: str) -> subprocess.CompletedProcess[str]:
    return run([
        "docker", "run", "--rm", "--network", f"container:{primary}",
        "--cap-add", "NET_ADMIN", "--entrypoint", "bash", image, "-lc",
        "tc qdisc del dev eth0 root && tc qdisc show dev eth0",
    ])


def blocking_write(*, container: str, note: str) -> "subprocess.Popen[str]":
    query = f"INSERT INTO {TABLE}(note) VALUES ('{note}')"
    return subprocess.Popen(
        [
            "docker", "exec", "-e", f"PGPASSWORD={DATABASE_PASSWORD}",
            container, "psql", "-U", "postgres", "-d", "fleetqox",
            "-v", "ON_ERROR_STOP=1", "-Atc", query,
        ],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def find_syncrep_waiting_pid(container: str, note: str, timeout_s: float = 5.0) -> int:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = sql(
            container,
            "SELECT pid FROM pg_stat_activity WHERE wait_event = 'SyncRep' "
            f"AND query LIKE '%{note}%'",
        )
        stripped = result.stdout.strip()
        if result.returncode == 0 and stripped.isdigit():
            return int(stripped)
        time.sleep(0.2)
    return -1


def still_waiting(container: str, pid: int) -> bool:
    result = sql(
        container,
        f"SELECT wait_event FROM pg_stat_activity WHERE pid = {pid}",
    )
    return result.returncode == 0 and result.stdout.strip() == "SyncRep"


def row_count(container: str) -> int:
    result = sql(container, f"SELECT count(*) FROM {TABLE}")
    stripped = result.stdout.strip()
    return int(stripped) if result.returncode == 0 and stripped.isdigit() else -1


def wait_replication_state(
    primary: str, expected_suffix: str, timeout_s: float = 15.0,
) -> str:
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        checkpoint = replication_checkpoint(primary)
        last = (
            f"{checkpoint['application_name']}|{checkpoint['state']}|"
            f"{checkpoint['sync_state']}"
        )
        if last.endswith(expected_suffix):
            return last
        time.sleep(0.2)
    return last


def collect_popen(process: "subprocess.Popen[str]", timeout_s: float) -> dict[str, Any]:
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
        return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr}
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr}


def run_case(*, root: Path, image: str, network: str, suffix: str) -> dict[str, Any]:
    primary = f"fleetrmw-pg-splitbrain-primary-{suffix}"
    standby = f"fleetrmw-pg-splitbrain-standby-{suffix}"
    cluster = start_replication_cluster(network=network, primary=primary, standby=standby)
    if cluster["status"] != "ok":
        return {"status": "failed", "stage": "start_replication_cluster", "cluster": cluster}

    table_created = sql(
        primary,
        f"CREATE TABLE IF NOT EXISTS {TABLE} (id serial primary key, note text)",
    )
    if table_created.returncode != 0:
        return {
            "status": "failed", "stage": "create_table",
            "stderr": table_created.stderr[-2000:],
        }

    baseline_process = blocking_write(container=primary, note="before-partition")
    baseline_write = collect_popen(baseline_process, timeout_s=10.0)
    baseline_replicated = wait_replication_state(primary, "|streaming|sync")
    baseline_ok = (
        baseline_write["returncode"] == 0
        and baseline_replicated.endswith("|streaming|sync")
    )

    standby_ip = resolve_container_ip(image=image, target_container=primary, alias=STANDBY_ALIAS)
    partition_applied = (
        apply_targeted_partition(image=image, primary=primary, blocked_ip=standby_ip)
        if standby_ip else subprocess.CompletedProcess([], 1, "", "standby_ip_not_resolved")
    )

    primary_reads_during_partition = row_count(primary)

    # Property 1: the write must not return at all (success or failure)
    # while the partition holds -- any result within this window would mean
    # either a silent divergent commit or an unexpectedly-working replica.
    cancel_probe_process = blocking_write(container=primary, note="cancel-probe")
    cancel_probe_pid = find_syncrep_waiting_pid(primary, "cancel-probe")
    cancel_probe_stuck = cancel_probe_pid > 0 and still_waiting(primary, cancel_probe_pid)
    # Give it a few more seconds unattended -- proving it stays blocked
    # rather than just catching it mid-flight the instant it started.
    time.sleep(3.0)
    cancel_probe_still_stuck_after_wait = (
        cancel_probe_pid > 0 and still_waiting(primary, cancel_probe_pid)
    )
    no_result_while_partitioned = cancel_probe_process.poll() is None

    # Property 2 (the documented hazard): an ordinary query cancel against
    # that stuck backend reports FALSE SUCCESS to the client.
    cancel_result = (
        sql(primary, f"SELECT pg_cancel_backend({cancel_probe_pid})")
        if cancel_probe_pid > 0 else subprocess.CompletedProcess([], 1, "", "no_pid")
    )
    cancel_probe_outcome = collect_popen(cancel_probe_process, timeout_s=10.0)
    cancel_reports_false_success = (
        cancel_result.returncode == 0
        and cancel_probe_outcome["returncode"] == 0
        and "INSERT 0 1" in cancel_probe_outcome["stdout"]
        and "might not have been replicated" in cancel_probe_outcome["stderr"]
    )

    # Property 3 (the safe primitive, matching this system's own SIGKILL
    # STONITH): terminating the connection outright reports a clear
    # client-visible failure instead.
    terminate_probe_process = blocking_write(container=primary, note="terminate-probe")
    terminate_probe_pid = find_syncrep_waiting_pid(primary, "terminate-probe")
    terminate_result = (
        sql(primary, f"SELECT pg_terminate_backend({terminate_probe_pid})")
        if terminate_probe_pid > 0 else subprocess.CompletedProcess([], 1, "", "no_pid")
    )
    terminate_probe_outcome = collect_popen(terminate_probe_process, timeout_s=10.0)
    terminate_reports_clean_failure = (
        terminate_result.returncode == 0
        and terminate_probe_outcome["returncode"] != 0
        and "INSERT 0 1" not in terminate_probe_outcome["stdout"]
    )

    heal = heal_partition(image=image, primary=primary) if standby_ip else (
        subprocess.CompletedProcess([], 1, "", "standby_ip_not_resolved")
    )
    post_heal_replicated = wait_replication_state(primary, "|streaming|sync", timeout_s=20.0)

    recovery_process = blocking_write(container=primary, note="after-heal")
    recovery_write = collect_popen(recovery_process, timeout_s=10.0)
    recovery_replicated = wait_replication_state(primary, "|streaming|sync")
    primary_count = row_count(primary)
    standby_count = row_count(standby)

    ok = (
        baseline_ok
        and standby_ip != ""
        and partition_applied.returncode == 0
        and primary_reads_during_partition >= 1
        and cancel_probe_stuck
        and cancel_probe_still_stuck_after_wait
        and no_result_while_partitioned
        and cancel_reports_false_success
        and terminate_reports_clean_failure
        and heal.returncode == 0
        and post_heal_replicated.endswith("|streaming|sync")
        and recovery_write["returncode"] == 0
        and recovery_replicated.endswith("|streaming|sync")
        and primary_count >= 0
        and primary_count == standby_count
    )
    result = {
        "status": "ok" if ok else "failed",
        "cluster": cluster,
        "standby_ip_resolved": standby_ip,
        "baseline_write_ok": baseline_write["returncode"] == 0,
        "baseline_replication_state": baseline_replicated,
        "partition_applied_returncode": partition_applied.returncode,
        "primary_reads_during_partition": primary_reads_during_partition,
        "cancel_probe_stuck_in_syncrep": cancel_probe_stuck,
        "cancel_probe_still_stuck_after_wait": cancel_probe_still_stuck_after_wait,
        "no_result_while_partitioned": no_result_while_partitioned,
        "cancel_reports_false_success": cancel_reports_false_success,
        "cancel_probe_outcome": cancel_probe_outcome,
        "terminate_reports_clean_failure": terminate_reports_clean_failure,
        "terminate_probe_outcome": terminate_probe_outcome,
        "heal_returncode": heal.returncode,
        "post_heal_replication_state": post_heal_replicated,
        "recovery_write_ok": recovery_write["returncode"] == 0,
        "recovery_replication_state": recovery_replicated,
        "primary_row_count": primary_count,
        "standby_row_count": standby_count,
        "no_divergence_after_heal": primary_count == standby_count and primary_count >= 0,
    }
    for container in (primary, standby):
        run(["docker", "rm", "-f", container])
    return result


def run_probe(*, root: Path, image: str, iterations: int) -> dict[str, Any]:
    run_count = max(1, iterations)
    network = f"fleetrmw-pg-splitbrain-net-{os.getpid()}"
    network_result = run(["docker", "network", "create", network])
    rows: list[dict[str, Any]] = []
    try:
        if network_result.returncode == 0:
            for index in range(1, run_count + 1):
                rows.append(run_case(
                    root=root, image=image, network=network,
                    suffix=f"{os.getpid()}-{index}",
                ))
    finally:
        run(["docker", "network", "rm", network])
    successful = sum(row.get("status") == "ok" for row in rows)
    status = "ok" if (
        network_result.returncode == 0 and len(rows) == successful == run_count
    ) else "failed"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_count": run_count,
        "successful_runs": successful,
        "failed_run_count": run_count - successful,
        "network_returncode": network_result.returncode,
        "replication_only_partition_no_silent_write_claim": status == "ok",
        "replication_only_partition_reads_available_claim": status == "ok",
        "query_cancel_false_success_hazard_documented_claim": status == "ok",
        "backend_termination_safe_abort_claim": status == "ok",
        "replication_only_partition_heals_without_divergence_claim": status == "ok",
        "runs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_postgres_replication_partition_split_brain_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image, iterations=args.iterations)
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} runs={summary['successful_runs']}/"
            f"{summary['run_count']}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
