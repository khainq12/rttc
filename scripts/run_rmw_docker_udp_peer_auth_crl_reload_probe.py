"""Prove the SROS2 identity CRL used for UDP peer authentication reloads live.

Closes one specific gap listed under the security partial capability:
udp_peer_auth_trust_store_ used to be built exactly once at process
startup from FLEETQOX_RMW_SROS2_IDENTITY_CRL_FILE, so a certificate
revoked after the process started a long-running node stayed trusted
until a restart -- this is the same class of gap already fixed for the
ngtcp2 gateway and the aioquic gateway, applied here to the UDP AEAD
peer-authentication path.

A single process publishes and takes from itself (self-loopback) using
its own SROS2 identity: a first message round-trips normally, the
orchestrating script then rewrites the on-disk CRL to revoke that exact
identity's own certificate, and a second message from the same process
must now be rejected on receipt without any restart.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
from typing import Any


SCHEMA_VERSION = "fleetrmw.docker_udp_peer_auth_crl_reload_probe.v1"
PROBE_SCHEMA_VERSION = "fleetrmw.rmw_udp_peer_auth_crl_reload_probe.v1"
DEFAULT_IMAGE = "ros:jazzy-ros-base"
TEST_KEY_HEX = "4b" * 32


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_udp_peer_auth_crl_reload_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    summary = run_probe(root=root, image=args.image)
    summary_path = root / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-udp-peer-auth-crl-reload-probe")
        print(f"  status: {summary['status']}")
        probe = summary.get("probe", {})
        print(f"  before_taken: {probe.get('before_taken')}")
        print(f"  after_taken: {probe.get('after_taken')}")
        print(
            "  udp_peer_auth_crl_reload_successes: "
            f"{probe.get('udp_peer_auth_crl_reload_successes')}"
        )
        print(
            "  udp_peer_auth_revoked_certificate_drops: "
            f"{probe.get('udp_peer_auth_revoked_certificate_drops')}"
        )
    return 0 if summary["status"] == "ok" else 1


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    executable = (
        "/tmp/fq-peer-crl-install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_peer_auth_crl_reload_probe"
    )
    revoke_python = (
        "from datetime import datetime, timedelta, timezone; "
        "from pathlib import Path; "
        "from cryptography import x509; "
        "from cryptography.hazmat.primitives import hashes, serialization; "
        "root=Path('/tmp/fq-peer-crl-keystore'); "
        "ca=x509.load_pem_x509_certificate("
        "(root/'public/identity_ca.cert.pem').read_bytes()); "
        "key=serialization.load_pem_private_key("
        "(root/'private/identity_ca.key.pem').read_bytes(),password=None); "
        "peer=x509.load_pem_x509_certificate("
        "(root/'enclaves/fleetqox/peer_a/cert.pem').read_bytes()); "
        "now=datetime.now(timezone.utc); "
        "revoked=x509.RevokedCertificateBuilder().serial_number("
        "peer.serial_number).revocation_date(now).build(); "
        "crl=x509.CertificateRevocationListBuilder().issuer_name("
        "ca.subject).last_update(now).next_update(now+timedelta(days=1))."
        "add_revoked_certificate(revoked).sign(key,hashes.SHA256()); "
        "Path('/tmp/fq-peer-crl-live.crl.pem').write_bytes("
        "crl.public_bytes(serialization.Encoding.PEM))"
    )
    empty_crl_python = (
        "from datetime import datetime, timedelta, timezone; "
        "from pathlib import Path; "
        "from cryptography import x509; "
        "from cryptography.hazmat.primitives import hashes, serialization; "
        "root=Path('/tmp/fq-peer-crl-keystore'); "
        "ca=x509.load_pem_x509_certificate("
        "(root/'public/identity_ca.cert.pem').read_bytes()); "
        "key=serialization.load_pem_private_key("
        "(root/'private/identity_ca.key.pem').read_bytes(),password=None); "
        "now=datetime.now(timezone.utc); "
        "crl=x509.CertificateRevocationListBuilder().issuer_name("
        "ca.subject).last_update(now).next_update(now+timedelta(days=1))."
        "sign(key,hashes.SHA256()); "
        "Path('/tmp/fq-peer-crl-live.crl.pem').write_bytes("
        "crl.public_bytes(serialization.Encoding.PEM))"
    )
    command = f"""
source /opt/ros/jazzy/setup.bash
set -eo pipefail
rm -rf /tmp/fq-peer-crl-build /tmp/fq-peer-crl-install /tmp/fq-peer-crl-log \\
  /tmp/fq-peer-crl-keystore /tmp/fq-peer-crl-live.crl.pem \\
  /tmp/fq-peer-crl-ready /tmp/fq-peer-crl-revoked
colcon --log-base /tmp/fq-peer-crl-log build --base-paths ros2_ws/src \\
  --packages-select rmw_fleetqox_cpp --build-base /tmp/fq-peer-crl-build \\
  --install-base /tmp/fq-peer-crl-install --cmake-args -DCMAKE_BUILD_TYPE=Release \\
  >/dev/null
source /tmp/fq-peer-crl-install/setup.bash
ros2 security create_keystore /tmp/fq-peer-crl-keystore >/dev/null
ros2 security create_enclave /tmp/fq-peer-crl-keystore /fleetqox/peer_a >/dev/null
python3 -c {shlex.quote(empty_crl_python)}

# Background revoker: wait for the probe to signal it has finished its
# first, pre-revoke publish/take round trip, then rewrite the same CRL
# file path to revoke this identity's own certificate, and signal done.
(
  deadline=$(($(date +%s) + 10))
  while [ ! -f /tmp/fq-peer-crl-ready ]; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      exit 1
    fi
    sleep 0.05
  done
  python3 -c {shlex.quote(revoke_python)}
  touch /tmp/fq-peer-crl-revoked
) &
revoker_pid=$!

enclave_dir=/tmp/fq-peer-crl-keystore/enclaves/fleetqox/peer_a
set +e
env \\
  RMW_IMPLEMENTATION=rmw_fleetqox_cpp \\
  FLEETQOX_RMW_PROBE_ENCLAVE=/fleetqox/peer_a \\
  FLEETQOX_RMW_SROS2_IDENTITY_CERT_FILE="${{enclave_dir}}/cert.pem" \\
  FLEETQOX_RMW_SROS2_IDENTITY_KEY_FILE="${{enclave_dir}}/key.pem" \\
  FLEETQOX_RMW_SROS2_IDENTITY_CA_FILE="${{enclave_dir}}/identity_ca.cert.pem" \\
  FLEETQOX_RMW_SROS2_IDENTITY_CRL_FILE=/tmp/fq-peer-crl-live.crl.pem \\
  FLEETQOX_RMW_UDP_AEAD_KEY_HEX={TEST_KEY_HEX} \\
  FLEETQOX_RMW_UDP_AEAD_REQUIRE=1 \\
  FLEETQOX_RMW_UDP_PEER_AUTH_REQUIRE=1 \\
  FLEETQOX_RMW_UDP_PEER_IDENTITIES=/fleetqox/peer_a \\
  FLEETQOX_RMW_BIND=127.0.0.1:50450 \\
  FLEETQOX_RMW_PEERS=127.0.0.1:50450 \\
  FLEETQOX_PROBE_READY_FOR_REVOKE_FILE=/tmp/fq-peer-crl-ready \\
  FLEETQOX_PROBE_REVOKE_DONE_FILE=/tmp/fq-peer-crl-revoked \\
  {executable} > /tmp/fq-peer-crl-probe.json 2>/tmp/fq-peer-crl-probe.err
probe_ret=$?
set -e
wait "$revoker_pid" || true
cat /tmp/fq-peer-crl-probe.json
cat /tmp/fq-peer-crl-probe.err >&2
exit "$probe_ret"
"""
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "--entrypoint", "bash",
            "-v", f"{root}:/work",
            "-w", "/work",
            image,
            "-lc", command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
    probe: dict[str, Any] = json.loads(lines[-1]) if lines else {}
    status = (
        "ok"
        if result.returncode == 0
        and probe.get("schema_version") == PROBE_SCHEMA_VERSION
        and probe.get("status") == "ok"
        and probe.get("before_taken") is True
        and probe.get("marker_observed") is True
        and probe.get("after_taken") is False
        and probe.get("udp_peer_auth_crl_reload_successes", 0) >= 1
        and probe.get("udp_peer_auth_crl_reload_failures", 0) == 0
        and probe.get("udp_peer_auth_revoked_certificate_drops", 0) >= 1
        else "failed"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "docker_returncode": result.returncode,
        "probe": probe,
        "docker_stdout": "" if status == "ok" else result.stdout,
        "docker_stderr": "" if status == "ok" else result.stderr,
    }


if __name__ == "__main__":
    raise SystemExit(main())
