"""Build and exercise ephemeral-ECDH forward secrecy for FleetRMW UDP AEAD in Docker.

Proves, over real two-process UDP peers with SROS2 identities (the same
fixture run_rmw_docker_udp_peer_auth_probe.py uses):

  * A mutual ephemeral ECDH handshake completes on both sides once
    FLEETQOX_RMW_UDP_ECDH_ENABLE=1 is set (requires peer auth to already be
    enabled -- the KEX message itself rides the SROS2 signature wrapper).
  * Once the handshake completes, subsequent frames are encrypted with a
    key that mixes in the derived ECDH secret (udp_ecdh_encrypted_frames
    increments), not just the static PSK.
  * Tampering the signature on an ephemeral-pubkey KEX message is rejected
    the same way a tampered data frame is (MITM protection on the
    handshake itself, not just on ordinary traffic).
  * FLEETQOX_RMW_UDP_ECDH_ENABLE is refused (fails closed) without peer
    auth already enabled.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_allocation_probe import DEFAULT_IMAGE, parse_json_rows


SCHEMA_VERSION = "fleetrmw.docker_udp_ecdh_probe.v1"
TEST_KEY_HEX = "9c" * 32


def _subscriber_rows(rows: list[dict[str, Any]], topic_prefix: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("mode") == "subscriber"
        and str(row.get("topic", "")).startswith(topic_prefix)
    ]


def _publisher_rows(rows: list[dict[str, Any]], topic_prefix: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("mode") == "publisher"
        and str(row.get("topic", "")).startswith(topic_prefix)
    ]


def run_probe(*, root: Path, image: str, iterations: int = 1) -> dict[str, Any]:
    run_count = max(iterations, 1)
    executable = (
        "/tmp/fq-ecdh-install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_interprocess_pubsub_probe"
    )
    missing_peer_auth_python = (
        "import subprocess, os, sys; "
        "env = dict(os.environ); "
        "env['RMW_IMPLEMENTATION'] = 'rmw_fleetqox_cpp'; "
        "env['FLEETQOX_RMW_UDP_AEAD_KEY_HEX'] = " + repr(TEST_KEY_HEX) + "; "
        "env['FLEETQOX_RMW_UDP_AEAD_REQUIRE'] = '1'; "
        "env['FLEETQOX_RMW_UDP_ECDH_ENABLE'] = '1'; "
        "env['FLEETQOX_RMW_BIND'] = '127.0.0.1:50999'; "
        "env['FLEETQOX_RMW_PEERS'] = '127.0.0.1:51000'; "
        "proc = subprocess.run(["
        + repr(executable)
        + ", '--mode', 'publisher', '--topic', "
        "'/fleetqox/udp_ecdh/missing_peer_auth'], env=env, "
        "capture_output=True, text=True); "
        "sys.stdout.write(proc.stdout); sys.stdout.write(proc.stderr); "
        "sys.exit(0 if proc.returncode != 0 else 1)"
    )
    command = f"""
source /opt/ros/jazzy/setup.bash
set -eo pipefail
rm -rf /tmp/fq-ecdh-build /tmp/fq-ecdh-install /tmp/fq-ecdh-log \
  /tmp/fq-ecdh-keystore
colcon --log-base /tmp/fq-ecdh-log build --base-paths ros2_ws/src \
  --packages-select rmw_fleetqox_cpp --build-base /tmp/fq-ecdh-build \
  --install-base /tmp/fq-ecdh-install --cmake-args -DCMAKE_BUILD_TYPE=Release \
  >/dev/null
source /tmp/fq-ecdh-install/setup.bash
ros2 security create_keystore /tmp/fq-ecdh-keystore >/dev/null
ros2 security create_enclave /tmp/fq-ecdh-keystore /fleetqox/peer_a >/dev/null
ros2 security create_enclave /tmp/fq-ecdh-keystore /fleetqox/peer_b >/dev/null

echo "=== missing_peer_auth ==="
set +e
python3 -c {shlex.quote(missing_peer_auth_python)}
missing_peer_auth_rc=$?
set -e
test "${{missing_peer_auth_rc}}" -eq 0 && echo missing_peer_auth_fail_closed=1

run_pair() {{
  case_name="$1"
  port_base="$2"
  tamper_kex="$3"
  expect_taken="$4"
  publish_count="$5"
  interval_ms="$6"
  topic="/fleetqox/udp_ecdh/${{case_name}}"
  subscriber_dir="/tmp/fq-ecdh-keystore/enclaves/fleetqox/peer_b"
  publisher_dir="/tmp/fq-ecdh-keystore/enclaves/fleetqox/peer_a"
  set +e
  env \
    RMW_IMPLEMENTATION=rmw_fleetqox_cpp \
    FLEETQOX_RMW_PROBE_ENCLAVE=/fleetqox/peer_b \
    FLEETQOX_RMW_SROS2_IDENTITY_CERT_FILE="${{subscriber_dir}}/cert.pem" \
    FLEETQOX_RMW_SROS2_IDENTITY_KEY_FILE="${{subscriber_dir}}/key.pem" \
    FLEETQOX_RMW_SROS2_IDENTITY_CA_FILE="${{subscriber_dir}}/identity_ca.cert.pem" \
    FLEETQOX_RMW_UDP_AEAD_KEY_HEX={TEST_KEY_HEX} \
    FLEETQOX_RMW_UDP_AEAD_REQUIRE=1 \
    FLEETQOX_RMW_UDP_PEER_AUTH_REQUIRE=1 \
    FLEETQOX_RMW_UDP_PEER_IDENTITIES=/fleetqox/peer_a \
    FLEETQOX_RMW_UDP_ECDH_ENABLE=1 \
    FLEETQOX_RMW_BIND="127.0.0.1:$((port_base + 1))" \
    FLEETQOX_RMW_PEERS="127.0.0.1:${{port_base}}" \
    {executable} --mode subscriber --topic "${{topic}}" \
      --payload fleetqox-ecdh-forward-secrecy --timeout-ms 4000 \
      --expect-taken "${{expect_taken}}" --publish-count "${{publish_count}}" \
      >"/tmp/fq-ecdh-${{case_name}}-subscriber.json" 2>&1 &
  subscriber_pid=$!
  sleep 0.25
  env \
    RMW_IMPLEMENTATION=rmw_fleetqox_cpp \
    FLEETQOX_RMW_PROBE_ENCLAVE=/fleetqox/peer_a \
    FLEETQOX_RMW_SROS2_IDENTITY_CERT_FILE="${{publisher_dir}}/cert.pem" \
    FLEETQOX_RMW_SROS2_IDENTITY_KEY_FILE="${{publisher_dir}}/key.pem" \
    FLEETQOX_RMW_SROS2_IDENTITY_CA_FILE="${{publisher_dir}}/identity_ca.cert.pem" \
    FLEETQOX_RMW_UDP_AEAD_KEY_HEX={TEST_KEY_HEX} \
    FLEETQOX_RMW_UDP_AEAD_REQUIRE=1 \
    FLEETQOX_RMW_UDP_PEER_AUTH_REQUIRE=1 \
    FLEETQOX_RMW_UDP_PEER_IDENTITIES=/fleetqox/peer_b \
    FLEETQOX_RMW_UDP_ECDH_ENABLE=1 \
    FLEETQOX_RMW_UDP_ECDH_TAMPER_KEX_OUTBOUND_ONCE="${{tamper_kex}}" \
    FLEETQOX_RMW_BIND="127.0.0.1:${{port_base}}" \
    FLEETQOX_RMW_PEERS="127.0.0.1:$((port_base + 1))" \
    {executable} --mode publisher --topic "${{topic}}" \
      --payload fleetqox-ecdh-forward-secrecy --pre-publish-ms 100 \
      --publish-count "${{publish_count}}" --publish-interval-ms "${{interval_ms}}" \
      >"/tmp/fq-ecdh-${{case_name}}-publisher.json" 2>&1
  publisher_rc=$?
  wait "${{subscriber_pid}}"
  subscriber_rc=$?
  set -e
  cat "/tmp/fq-ecdh-${{case_name}}-publisher.json"
  cat "/tmp/fq-ecdh-${{case_name}}-subscriber.json"
  echo "case=${{case_name}} publisher_rc=${{publisher_rc}} subscriber_rc=${{subscriber_rc}}"
}}

for i in $(seq 1 {run_count}); do
  run_pair "handshake_${{i}}" "$((50600 + i * 4))" false 1 6 150
done
run_pair kex_signature_tamper 50700 true 1 3 100
"""
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "-v",
            f"{root}:/work",
            "-w",
            "/work",
            image,
            "-lc",
            command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    rows = parse_json_rows(completed.stdout)
    handshake_subscribers = _subscriber_rows(rows, "/fleetqox/udp_ecdh/handshake_")
    handshake_publishers = _publisher_rows(rows, "/fleetqox/udp_ecdh/handshake_")

    handshake_pairs_ok = 0
    for subscriber in handshake_subscribers:
        suffix = str(subscriber.get("topic", "")).rsplit("/", 1)[-1]
        publisher = next(
            (
                row
                for row in handshake_publishers
                if str(row.get("topic", "")).endswith("/" + suffix)
            ),
            {},
        )
        if (
            subscriber.get("status") == "ok"
            and subscriber.get("messages_taken") == subscriber.get("publish_count")
            and subscriber.get("udp_ecdh_enabled") is True
            and int(subscriber.get("udp_ecdh_handshakes_completed", 0)) >= 1
            and int(subscriber.get("udp_ecdh_kex_received", 0)) >= 1
            and int(subscriber.get("udp_ecdh_encrypted_frames", 0)) >= 1
            and int(subscriber.get("udp_ecdh_derive_failures", 0)) == 0
            and publisher.get("status") == "ok"
            and publisher.get("udp_ecdh_enabled") is True
            and int(publisher.get("udp_ecdh_handshakes_completed", 0)) >= 1
            and int(publisher.get("udp_ecdh_kex_sent", 0)) >= 1
            and int(publisher.get("udp_ecdh_encrypted_frames", 0)) >= 1
        ):
            handshake_pairs_ok += 1

    kex_tamper_subscriber = next(
        iter(_subscriber_rows(rows, "/fleetqox/udp_ecdh/kex_signature_tamper")), {}
    )
    kex_tamper_publisher = next(
        iter(_publisher_rows(rows, "/fleetqox/udp_ecdh/kex_signature_tamper")), {}
    )
    # A forged ephemeral key must be rejected by the same SROS2 signature
    # check that protects ordinary data frames -- proven directly by the
    # subscriber counting a signature failure for the tampered KEX message.
    # (Whether a *later*, untampered bootstrap retry -- sent 500ms after the
    # first attempt by ecdh_shared_secret_for_target's own loss-recovery
    # logic -- eventually completes the handshake before this short-lived
    # process exits is not the property under test here and is deliberately
    # not asserted either way: the tamper knob only ever corrupts the one
    # outbound KEX frame, by design, same as the pre-existing data-frame
    # tamper test.) Ordinary data delivery must be unaffected either way,
    # since it always has the PSK-only fallback while ECDH is unestablished.
    kex_tamper_ok = (
        kex_tamper_subscriber.get("status") == "ok"
        and kex_tamper_subscriber.get("messages_taken")
        == kex_tamper_subscriber.get("publish_count")
        and int(kex_tamper_subscriber.get("udp_peer_auth_signature_failures", 0)) >= 1
        and kex_tamper_publisher.get("status") == "ok"
        and int(kex_tamper_publisher.get("udp_ecdh_kex_sent", 0)) >= 1
    )

    missing_peer_auth_fail_closed = (
        "missing_peer_auth_fail_closed=1" in completed.stdout
    )

    handshake_ok = (
        len(handshake_subscribers) == run_count
        and len(handshake_publishers) == run_count
        and handshake_pairs_ok == run_count
    )
    ok = (
        completed.returncode == 0
        and handshake_ok
        and kex_tamper_ok
        and missing_peer_auth_fail_closed
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "image": image,
        "returncode": completed.returncode,
        "run_count": run_count,
        "ok_run_count": handshake_pairs_ok,
        "kex_scheme": "mutual ephemeral EC-P256 ECDH, SROS2-signed key exchange",
        "session_key_derivation": "HKDF(PSK || ECDH-shared-secret, per-peer)",
        "forward_secrecy_claim": handshake_ok,
        "asymmetric_session_key_exchange_claim": handshake_ok,
        "ecdh_kex_signature_tamper_fail_closed_claim": kex_tamper_ok,
        "udp_ecdh_requires_peer_auth_fail_closed_claim": missing_peer_auth_fail_closed,
        "sros2_peer_identity_authentication_claim": handshake_ok,
        "dds_security_interoperability_claim": False,
        "production_security_hardening_claim": False,
        "security_scope": "fleetqox_udp_ephemeral_ecdh_forward_secrecy",
        "handshake_subscribers": handshake_subscribers,
        "handshake_publishers": handshake_publishers,
        "kex_signature_tamper_subscriber": kex_tamper_subscriber,
        "kex_signature_tamper_publisher": kex_tamper_publisher,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_udp_ecdh_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image, iterations=args.iterations)
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} handshakes={summary['ok_run_count']}/"
            f"{summary['run_count']} kex_tamper="
            f"{summary['ecdh_kex_signature_tamper_fail_closed_claim']} "
            f"requires_peer_auth="
            f"{summary['udp_ecdh_requires_peer_auth_fail_closed_claim']}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
