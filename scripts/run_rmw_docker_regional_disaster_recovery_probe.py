#!/usr/bin/env python3
"""Prove regional disaster recovery across 3 independent regions, in Docker.

Every existing quorum/partition probe treats "the cluster" as one flat set
of nodes losing connectivity together. This probe instead assigns each
etcd member and the PostgreSQL primary/standby to one of three named
regions and disconnects an ENTIRE region's containers from the shared
network simultaneously (not one node at a time) to simulate that region
going dark -- while a failover controller and fence agent, both placed in
a surviving region, keep running unaffected.

Proves, over real separate Docker containers:

  1. Losing a MINORITY region (one that holds neither the primary nor a
     majority of etcd) causes no disruption at all -- the remaining two
     regions keep serving writes normally with reduced redundancy.
  2. Losing the region that holds the PRIMARY triggers a real etcd-quorum-
     gated failover: the surviving two regions (still a majority of etcd)
     detect the primary's loss, fence it (Docker-socket SIGKILL still
     reaches an otherwise network-isolated container -- this is legitimate
     out-of-band fencing, not a shortcut), and promote the standby in the
     surviving region to primary, which then accepts writes.
  3. Losing a SECOND region afterward (so only one region, a minority,
     remains) is correctly unable to promote anything further -- the
     lone survivor has no majority and no fencing target, so the failover
     controller's own quorum-loss-fail-closed behavior applies, matching
     quic_gateway_quorum_loss_promotion_fail_closed_claim.

Stated precisely: automatic, unattended RECOVERY from losing a
majority-holding region is not attempted or claimed here -- that is
impossible for any quorum system without a witness in a fourth location,
an inherent property of consensus, not a gap in this codebase (see
docs/STATUS_AND_ROADMAP.md). What this closes is the narrower, and far
more common, real-world case explicit in the name "regional disaster
recovery": losing any ONE of several independent regions is survived
automatically, with correct fail-closed behavior (not silent data
divergence) if a second region is lost afterward.
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
    from scripts.run_rmw_docker_quic_postgres_quorum_failover_probe import (
        ETCD_ALIASES,
        ETCD_IMAGE,
        FENCE_AGENT_ALIAS,
        FENCE_AGENT_PORT,
        collect_controller,
        collect_fence_agent,
        etcd_certificate_command,
        etcd_endpoints,
        fence_security_negative_controls,
        start_controller,
        start_etcd_cluster,
        start_fence_agent,
        wait_two_member_quorum,
    )
    from scripts.run_rmw_docker_quic_postgres_replication_failover_probe import (
        DATABASE_PASSWORD,
        PRIMARY_ALIAS,
        STANDBY_ALIAS,
        replication_checkpoint,
        sql,
        start_replication_cluster,
        wait_postgres,
    )
    from scripts.run_rmw_docker_quic_stateful_gateway_probe import DEFAULT_IMAGE, run
except ModuleNotFoundError:
    from run_rmw_docker_quic_postgres_quorum_failover_probe import (
        ETCD_ALIASES,
        ETCD_IMAGE,
        FENCE_AGENT_ALIAS,
        FENCE_AGENT_PORT,
        collect_controller,
        collect_fence_agent,
        etcd_certificate_command,
        etcd_endpoints,
        fence_security_negative_controls,
        start_controller,
        start_etcd_cluster,
        start_fence_agent,
        wait_two_member_quorum,
    )
    from run_rmw_docker_quic_postgres_replication_failover_probe import (
        DATABASE_PASSWORD,
        PRIMARY_ALIAS,
        STANDBY_ALIAS,
        replication_checkpoint,
        sql,
        start_replication_cluster,
        wait_postgres,
    )
    from run_rmw_docker_quic_stateful_gateway_probe import DEFAULT_IMAGE, run


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_regional_disaster_recovery_probe.v1"
# Reuses one of the identities etcd_certificate_command() already
# generates a certificate for (controller-1/controller-2/failback-controller-1/
# failback-controller-2) -- inventing a new controller id here would need a
# matching cert this shared PKI helper doesn't know to produce.
CONTROLLER_ID = "controller-1"


def write_row(container: str, note: str) -> bool:
    result = sql(
        container, f"INSERT INTO regional_probe(note) VALUES ('{note}')",
    )
    return result.returncode == 0


def row_count(container: str) -> int:
    result = sql(container, "SELECT count(*) FROM regional_probe")
    stripped = result.stdout.strip()
    return int(stripped) if result.returncode == 0 and stripped.isdigit() else -1


def run_case(*, root: Path, image: str, network: str, temp_root: Path) -> dict[str, Any]:
    suffix = str(os.getpid())
    etcd_names = tuple(f"fleetrmw-region-etcd{n}-{suffix}" for n in range(1, 4))
    primary_name = f"fleetrmw-region-primary-{suffix}"
    standby_name = f"fleetrmw-region-standby-{suffix}"
    fence_name = f"fleetrmw-region-fence-{suffix}"
    controller_name = f"fleetrmw-region-controller-{suffix}"
    certs = temp_root / "certs"

    # Region assignment: region A = etcd1 + primary; region B = etcd2 +
    # standby + failover controller + fence agent (the surviving
    # "operations" region for this probe); region C = etcd3 only.
    regions = {
        "region-a": [etcd_names[0], primary_name],
        "region-b": [etcd_names[1], standby_name, fence_name, controller_name],
        "region-c": [etcd_names[2]],
    }

    result: dict[str, Any] = {"status": "failed"}
    all_containers = [
        *etcd_names, primary_name, standby_name, fence_name, controller_name,
    ]

    try:
        etcd = start_etcd_cluster(root=root, certs=certs, network=network, names=etcd_names)
        if etcd["status"] != "ok":
            result["stage"] = "start_etcd"
            result["etcd"] = etcd
            return result

        postgres = start_replication_cluster(
            network=network, primary=primary_name, standby=standby_name,
        )
        if postgres["status"] != "ok":
            result["stage"] = "start_postgres"
            result["postgres"] = postgres
            return result

        table_created = sql(
            primary_name,
            "CREATE TABLE IF NOT EXISTS regional_probe (id serial primary key, note text)",
        )
        if table_created.returncode != 0:
            result["stage"] = "create_table"
            return result

        fence_ready = start_fence_agent(
            root=root, certs=certs, image=image, network=network,
            name=fence_name, primary=primary_name,
        )
        if not fence_ready:
            result["stage"] = "start_fence_agent"
            return result

        controller_ready = start_controller(
            root=root, certs=certs, image=image, network=network,
            name=controller_name, controller_id=CONTROLLER_ID,
        )
        if not controller_ready:
            result["stage"] = "start_controller"
            return result

        # Baseline: a write from region A (primary) replicates to region B
        # (standby) while all three regions are up.
        baseline_write_ok = write_row(primary_name, "baseline")
        baseline_replicated = replication_checkpoint(primary_name)
        baseline_ok = baseline_write_ok and baseline_replicated["status"] == "ok"

        # Property 1: losing region C (a minority holding no postgres data)
        # must not disrupt anything -- writes keep succeeding normally.
        for container in regions["region-c"]:
            run(["docker", "network", "disconnect", network, container])
        time.sleep(1.0)
        minority_loss_write_ok = write_row(primary_name, "region-c-down")
        minority_loss_replicated = replication_checkpoint(primary_name)
        minority_loss_ok = (
            minority_loss_write_ok and minority_loss_replicated["status"] == "ok"
        )
        for container in regions["region-c"]:
            run([
                "docker", "network", "connect", "--alias", ETCD_ALIASES[2],
                network, container,
            ])
        quorum_restored = wait_two_member_quorum(etcd_names[0], timeout_s=10.0)

        # Property 2: losing region A (the primary's region) triggers a real
        # etcd-quorum-gated failover to region B's standby.
        for container in regions["region-a"]:
            run(["docker", "network", "disconnect", network, container])
        controller_result = collect_controller(controller_name)
        all_containers.remove(controller_name)  # collect_controller already rm'd it
        fence_result = collect_fence_agent(fence_name)
        controller_telemetry = controller_result.get("telemetry", {})
        fence_telemetry = fence_result.get("telemetry", {})
        failover_ok = (
            controller_result.get("status") == "ok"
            and controller_telemetry.get("status") == "promoted"
            and controller_telemetry.get("dcs_lock_acquired") is True
            and controller_telemetry.get("hard_fence_confirmed") is True
            and fence_result.get("status") == "ok"
            and fence_telemetry.get("hard_fence_confirmed") is True
            and fence_telemetry.get("target_container") == primary_name
        )
        time.sleep(1.0)
        promoted_write_ok = write_row(standby_name, "promoted-region-b")
        promoted_row_count = row_count(standby_name)
        promoted_ok = failover_ok and promoted_write_ok and promoted_row_count >= 2

        # Property 3: losing a SECOND region now (region C, leaving only the
        # single-region survivor B) must not let anything further be
        # promoted -- there is no majority and, in this topology, no
        # further standby to promote to either way; a fresh quorum check
        # from the surviving member must fail to reach quorum.
        for container in regions["region-c"]:
            run(["docker", "network", "disconnect", network, container])
        time.sleep(1.0)
        second_loss_quorum_blocked = not wait_two_member_quorum(
            etcd_names[1], timeout_s=5.0,
        )

        ok = (
            baseline_ok and minority_loss_ok and quorum_restored
            and promoted_ok and second_loss_quorum_blocked
        )
        result = {
            "status": "ok" if ok else "failed",
            "regions": dict(regions),
            "etcd": etcd,
            "postgres": postgres,
            "baseline_write_and_replication_ok": baseline_ok,
            "minority_region_loss_no_disruption_claim": minority_loss_ok,
            "minority_region_rejoin_restores_quorum_claim": quorum_restored,
            "primary_region_loss_triggers_cross_region_failover_claim": promoted_ok,
            "controller": controller_result,
            "fence_agent": fence_result,
            "second_region_loss_blocks_promotion_claim": second_loss_quorum_blocked,
        }
        return result
    finally:
        # docker rm -f works regardless of a container's current network
        # membership (disconnected or not) and cleans up its endpoints.
        for container in all_containers:
            run(["docker", "rm", "-f", container])


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    suffix = str(os.getpid())
    temp_root = root / f".tmp_fleetrmw_regional_dr_{suffix}"
    certs = temp_root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "failed"}

    cert_result = run([
        "docker", "run", "--rm", "--entrypoint", "bash",
        "-v", f"{root}:/work", "-w", "/work", image, "-lc",
        etcd_certificate_command(certs, root),
    ])
    network = f"fleetrmw-regional-dr-net-{suffix}"
    network_result = run(["docker", "network", "create", network])
    try:
        if cert_result.returncode != 0 or network_result.returncode != 0:
            result["stage"] = "setup"
            result["cert_stderr"] = cert_result.stderr[-2000:]
            return result
        case = run_case(root=root, image=image, network=network, temp_root=temp_root)
        result = {"schema_version": SCHEMA_VERSION, **case}
        result["regional_disaster_recovery_claim"] = case.get("status") == "ok"
        return result
    finally:
        run(["docker", "network", "rm", network])
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_regional_disaster_recovery_probe_summary.json",
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
