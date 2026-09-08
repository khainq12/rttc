#!/usr/bin/env python3
"""Prove hardware STONITH via a real Redfish power-fencing call, in Docker.

There is no real server hardware (no BMC) anywhere this can run -- but the
protocol real hardware fencing depends on is a standard, well-documented
HTTPS/JSON API (DMTF Redfish), not something vendor-specific. This starts
scripts/fleetqox_redfish_bmc_simulator.py (a minimal but protocol-conformant
fake BMC -- the same pattern OpenStack Ironic/Metal3 use in CI to test
bare-metal power management without physical hardware) and points
scripts/fleetqox_hardware_stonith_agent.py's REAL Redfish HTTPS client code
at it, authorized the same way every other fence agent in this codebase is
(a DCS lease check plus mTLS client-identity binding on /fence).

Proves, over real separate Docker containers:

  1. An unauthenticated (no client certificate) fence request is rejected
     at the TLS layer; the BMC's power state is untouched.
  2. An authenticated request carrying a forged/non-existent DCS lease is
     rejected (403); the BMC's power state is untouched.
  3. A request authorized by a real etcd-issued lease results in a genuine
     Redfish `ComputerSystem.Reset` (ResetType=ForceOff) call reaching the
     BMC, which is then independently confirmed by a separate direct query
     to the BMC (not just the fence agent's own self-report) to have
     transitioned from On to Off.

What this does NOT prove -- stated precisely, matching this project's
evidence rules -- is that the same client code also works correctly
against a specific vendor's real BMC firmware, which can carry
implementation quirks no simulator captures. That gap is inherent to
testing without physical hardware and is not claimed closed here.
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
    from scripts.run_rmw_docker_quic_stateful_gateway_probe import DEFAULT_IMAGE, run
except ModuleNotFoundError:
    from run_rmw_docker_quic_stateful_gateway_probe import DEFAULT_IMAGE, run


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_hardware_stonith_redfish_probe.v1"
ETCD_IMAGE = "quay.io/coreos/etcd:v3.5.17"
ETCD_ALIAS = "fleetqox-hw-etcd"
BMC_ALIAS = "fleetqox-hw-bmc"
FENCE_ALIAS = "fleetqox-hw-fence"
LEASE_KEY = "/fleetqox/hardware/failover"


def pki_command(certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    return (
        "openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/ca.key -out {prefix}/ca.crt "
        "-subj /CN=FleetQoX-HW-STONITH-CA "
        "-addext basicConstraints=critical,CA:TRUE "
        "-addext keyUsage=critical,keyCertSign,cRLSign -days 1 >/dev/null 2>&1 && "
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/etcd-server.key -out {prefix}/etcd-server.csr "
        f"-subj /CN={ETCD_ALIAS} "
        f"-addext subjectAltName=DNS:{ETCD_ALIAS} "
        "-addext extendedKeyUsage=serverAuth,clientAuth >/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/etcd-server.csr -CA {prefix}/ca.crt "
        f"-CAkey {prefix}/ca.key -CAcreateserial -out {prefix}/etcd-server.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1 && "
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/controller-1.key -out {prefix}/controller-1.csr "
        "-subj /CN=controller-1 -addext extendedKeyUsage=clientAuth "
        ">/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/controller-1.csr -CA {prefix}/ca.crt "
        f"-CAkey {prefix}/ca.key -CAcreateserial -out {prefix}/controller-1.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1 && "
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/fence-server.key -out {prefix}/fence-server.csr "
        f"-subj /CN={FENCE_ALIAS} -addext subjectAltName=DNS:{FENCE_ALIAS} "
        "-addext extendedKeyUsage=serverAuth >/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/fence-server.csr -CA {prefix}/ca.crt "
        f"-CAkey {prefix}/ca.key -CAcreateserial -out {prefix}/fence-server.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1 && "
        "openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/bmc.key -out {prefix}/bmc.crt "
        f"-subj /CN={BMC_ALIAS} -addext subjectAltName=DNS:{BMC_ALIAS} "
        "-days 1 >/dev/null 2>&1"
    )


def wait_ready(container: str, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        logs = run(["docker", "logs", container]).stdout
        if '"status": "ready"' in logs:
            return True
        state = run(["docker", "inspect", "-f", "{{.State.Running}}", container])
        if state.returncode != 0 or state.stdout.strip() != "true":
            return False
        time.sleep(0.1)
    return False


def bmc_power_state(*, root: Path, image: str, network: str, certs: Path) -> str:
    ca = f"/work/{(certs / 'bmc.crt').relative_to(root)}"
    script = (
        "import json,ssl,urllib.request,base64;"
        f"c=ssl.create_default_context(cafile={ca!r});"
        "req=urllib.request.Request("
        f"'https://{BMC_ALIAS}:4512/redfish/v1/Systems/1');"
        "req.add_header('Authorization','Basic '+base64.b64encode("
        "b'bmc-operator:bmc-secret').decode());"
        "print(json.load(urllib.request.urlopen(req,timeout=3,context=c))"
        "['PowerState'])"
    )
    result = run([
        "docker", "run", "--rm", "--network", network,
        "-v", f"{root}:/work", "-w", "/work", "--entrypoint", "python3",
        image, "-c", script,
    ])
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def call_fence(
    *, root: Path, image: str, network: str, certs: Path,
    with_client_cert: bool, lease_id: str,
) -> tuple[int, dict[str, Any] | None]:
    ca = f"/work/{(certs / 'ca.crt').relative_to(root)}"
    cert = f"/work/{(certs / 'controller-1.crt').relative_to(root)}"
    key = f"/work/{(certs / 'controller-1.key').relative_to(root)}"
    cert_line = f"c.load_cert_chain(certfile={cert!r},keyfile={key!r});" if with_client_cert else ""
    script = (
        "import json,ssl,sys,urllib.error,urllib.request;"
        f"c=ssl.create_default_context(cafile={ca!r});"
        f"{cert_line}"
        f"r=urllib.request.Request('https://{FENCE_ALIAS}:4513/fence',"
        "data=json.dumps({'controller_id':'controller-1',"
        f"'lease_id':{lease_id!r}}}).encode(),"
        "headers={'content-type':'application/json'},method='POST');"
        "\ntry:"
        "\n  resp=urllib.request.urlopen(r,timeout=6,context=c)"
        "\n  print(resp.status); print(resp.read().decode())"
        "\nexcept urllib.error.HTTPError as e:"
        "\n  print(e.code); print(e.read().decode())"
        "\nexcept Exception as e:"
        "\n  print(-1); print(json.dumps({'exception': str(e)}))"
    )
    result = run([
        "docker", "run", "--rm", "--network", network,
        "-v", f"{root}:/work", "-w", "/work", "--entrypoint", "python3",
        image, "-c", script,
    ])
    lines = result.stdout.strip().splitlines()
    if len(lines) < 2:
        return -1, None
    try:
        status = int(lines[0])
        document = json.loads("\n".join(lines[1:]))
    except (ValueError, json.JSONDecodeError):
        return -1, None
    return status, document if isinstance(document, dict) else None


def acquire_real_lease(
    *, root: Path, image: str, network: str, certs: Path,
) -> str:
    ca = f"/work/{(certs / 'ca.crt').relative_to(root)}"
    cert = f"/work/{(certs / 'controller-1.crt').relative_to(root)}"
    key = f"/work/{(certs / 'controller-1.key').relative_to(root)}"
    script = (
        "from fleetqox.postgres_failover_dcs import EtcdQuorumLease;"
        "dcs=EtcdQuorumLease(("
        f"'https://{ETCD_ALIAS}:2379',),timeout_s=2.0,"
        f"ca_file={ca!r},cert_file={cert!r},key_file={key!r});"
        f"result=dcs.acquire(key={LEASE_KEY!r},value='controller-1',ttl_s=30);"
        "print(result.lease_id if result.acquired else '')"
    )
    result = run([
        "docker", "run", "--rm", "--network", network,
        "-v", f"{root}:/work", "-w", "/work", "--entrypoint", "python3",
        image, "-c", script,
    ])
    return result.stdout.strip() if result.returncode == 0 else ""


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    suffix = str(os.getpid())
    temp_root = root / f".tmp_fleetrmw_hw_stonith_{suffix}"
    certs = temp_root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    network = f"fleetrmw-hw-stonith-net-{suffix}"
    etcd_name = f"fleetrmw-hw-etcd-{suffix}"
    bmc_name = f"fleetrmw-hw-bmc-{suffix}"
    fence_name = f"fleetrmw-hw-fence-{suffix}"
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "failed"}

    cert_result = run([
        "docker", "run", "--rm", "--entrypoint", "bash",
        "-v", f"{root}:/work", "-w", "/work", image, "-lc",
        pki_command(certs, root),
    ])
    network_result = run(["docker", "network", "create", network])
    try:
        if cert_result.returncode != 0 or network_result.returncode != 0:
            result["stage"] = "setup"
            result["cert_stderr"] = cert_result.stderr[-2000:]
            return result

        etcd_started = run([
            "docker", "run", "-d", "--name", etcd_name,
            "--network", network, "--network-alias", ETCD_ALIAS,
            "-v", f"{certs}:/certs:ro", ETCD_IMAGE, "/usr/local/bin/etcd",
            "--name", "etcd1", "--data-dir", "/etcd-data",
            "--listen-client-urls", "https://0.0.0.0:2379",
            "--advertise-client-urls", f"https://{ETCD_ALIAS}:2379",
            "--listen-peer-urls", "https://0.0.0.0:2380",
            "--initial-advertise-peer-urls", f"https://{ETCD_ALIAS}:2380",
            "--cert-file", "/certs/etcd-server.crt",
            "--key-file", "/certs/etcd-server.key",
            "--trusted-ca-file", "/certs/ca.crt",
            "--client-cert-auth=true",
            "--peer-cert-file", "/certs/etcd-server.crt",
            "--peer-key-file", "/certs/etcd-server.key",
            "--peer-trusted-ca-file", "/certs/ca.crt",
            "--peer-client-cert-auth=true",
            "--initial-cluster", f"etcd1=https://{ETCD_ALIAS}:2380",
            "--initial-cluster-state", "new",
        ])
        if etcd_started.returncode != 0:
            result["stage"] = "start_etcd"
            return result
        time.sleep(2.0)

        bmc_started = run([
            "docker", "run", "-d", "--name", bmc_name,
            "--network", network, "--network-alias", BMC_ALIAS,
            "-v", f"{root}:/work", "-w", "/work", "--entrypoint", "python3",
            image, "scripts/fleetqox_redfish_bmc_simulator.py",
            "--host", "0.0.0.0", "--port", "4512", "--system-id", "1",
            "--username", "bmc-operator", "--password", "bmc-secret",
            "--tls-cert", f"/work/{(certs / 'bmc.crt').relative_to(root)}",
            "--tls-key", f"/work/{(certs / 'bmc.key').relative_to(root)}",
            "--initial-power-state", "On",
        ])
        if bmc_started.returncode != 0 or not wait_ready(bmc_name):
            result["stage"] = "start_bmc"
            return result

        fence_started = run([
            "docker", "run", "-d", "--name", fence_name,
            "--network", network, "--network-alias", FENCE_ALIAS,
            "-v", f"{root}:/work", "-w", "/work", "--entrypoint", "python3",
            image, "scripts/fleetqox_hardware_stonith_agent.py",
            "--host", "0.0.0.0", "--port", "4513",
            "--bmc-url", f"https://{BMC_ALIAS}:4512", "--system-id", "1",
            "--bmc-username", "bmc-operator", "--bmc-password", "bmc-secret",
            "--bmc-ca", f"/work/{(certs / 'bmc.crt').relative_to(root)}",
            "--tls-ca", f"/work/{(certs / 'ca.crt').relative_to(root)}",
            "--tls-cert", f"/work/{(certs / 'fence-server.crt').relative_to(root)}",
            "--tls-key", f"/work/{(certs / 'fence-server.key').relative_to(root)}",
            "--etcd-endpoints", f"https://{ETCD_ALIAS}:2379",
            "--etcd-ca", f"/work/{(certs / 'ca.crt').relative_to(root)}",
            "--etcd-cert", f"/work/{(certs / 'controller-1.crt').relative_to(root)}",
            "--etcd-key", f"/work/{(certs / 'controller-1.key').relative_to(root)}",
            "--lease-key", LEASE_KEY,
        ])
        if fence_started.returncode != 0 or not wait_ready(fence_name):
            result["stage"] = "start_fence_agent"
            return result

        power_before_any = bmc_power_state(root=root, image=image, network=network, certs=certs)

        unauthenticated_status, _ = call_fence(
            root=root, image=image, network=network, certs=certs,
            with_client_cert=False, lease_id="-1",
        )
        power_after_unauthenticated = bmc_power_state(
            root=root, image=image, network=network, certs=certs,
        )
        unauthenticated_rejected = (
            unauthenticated_status not in (200,)
            and power_after_unauthenticated == "On"
        )

        forged_status, forged_body = call_fence(
            root=root, image=image, network=network, certs=certs,
            with_client_cert=True, lease_id="forged-lease-id",
        )
        power_after_forged = bmc_power_state(
            root=root, image=image, network=network, certs=certs,
        )
        forged_rejected = (
            forged_status == 403
            and forged_body is not None
            and forged_body.get("status") == "dcs_lease_not_authorized"
            and power_after_forged == "On"
        )

        real_lease_id = acquire_real_lease(
            root=root, image=image, network=network, certs=certs,
        )
        authorized_status, authorized_body = call_fence(
            root=root, image=image, network=network, certs=certs,
            with_client_cert=True, lease_id=real_lease_id,
        ) if real_lease_id else (-1, None)
        power_after_authorized = bmc_power_state(
            root=root, image=image, network=network, certs=certs,
        )
        authorized_fenced = (
            bool(real_lease_id)
            and authorized_status == 200
            and authorized_body is not None
            and authorized_body.get("status") == "fenced"
            and authorized_body.get("hard_fence_confirmed") is True
            and authorized_body.get("fence_mechanism") == "redfish_computer_system_reset"
            and power_after_authorized == "Off"
        )

        ok = (
            power_before_any == "On"
            and unauthenticated_rejected
            and forged_rejected
            and authorized_fenced
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "fence_mechanism": "redfish_computer_system_reset",
            "hardware_validated": False,
            "power_state_initial": power_before_any,
            "unauthenticated_fence_rejected_claim": unauthenticated_rejected,
            "forged_lease_fence_rejected_claim": forged_rejected,
            "authorized_redfish_fence_confirmed_claim": authorized_fenced,
            "power_state_after_authorized_fence": power_after_authorized,
            "authorized_fence_response": authorized_body,
        }
        return result
    finally:
        for name in (fence_name, bmc_name, etcd_name):
            run(["docker", "rm", "-f", name])
        run(["docker", "network", "rm", network])
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_hardware_stonith_redfish_probe_summary.json",
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
