#!/usr/bin/env python3
"""Prove SROS2 identity CRL revocation reloads live for UDP peer
authentication across two REAL, separate hosts -- two independent KVM
virtual machines, each with its own kernel and its own Docker daemon,
connected over a real (virtio) network link -- rather than same-host
Docker containers on a shared bridge network sharing one kernel.

Closes the multi-host gap the administrative audit's NV-09 finding
implied for prior PKI/CRL evidence: the existing single-host proof
(run_rmw_docker_udp_peer_auth_crl_reload_probe.py) is a single self-loopback
process, so it cannot exclude a shared-process/shared-kernel confound, nor
does it exercise cert/CRL material actually crossing a real network
boundary between two machines with independent clocks and filesystems.

Topology: a long-lived "receiver" (verifier) process runs on VM2, bound to
its real private IP. A "sender" process runs on VM1, bound to its own real
private IP, and publishes a batch of samples to the receiver. Round 1: the
sender's certificate is not yet revoked, and all samples are verified and
delivered. The orchestrator then rewrites the on-disk CRL file that VM2's
still-running receiver process reads its trust decisions from -- revoking
the sender's exact certificate -- without restarting that process. Round 2:
a fresh sender process (same identity/certificate) publishes another batch;
the receiver's live-reloaded CRL must now reject every one of them, on
receipt, over the real cross-VM link, with the verifier process never
having restarted between the two rounds.

Requires scripts/run_multihost_kvm_setup.sh (or an equivalent fresh
provision) to have already brought up vm1/vm2 with the fleetrmw/rmw-netem
image loaded and this repo checked out under ~/RTC on each (see
.multihost_vm/ for SSH keys and VM connection details), and a shared SROS2
keystore already generated under .multihost_vm/pki_keystore and copied to
~/RTC/.multihost_vm/pki_keystore on both VMs (both /fleetqox/sender and
/fleetqox/receiver enclaves, same identity CA).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VM_DIR = ROOT / ".multihost_vm"
SCHEMA_VERSION = "fleetrmw.multihost_kvm_udp_peer_auth_crl_reload_probe.v1"
SSH_KEY = VM_DIR / "id_multihost"
VM1_SSH_PORT = 12201
VM2_SSH_PORT = 12202
VM1_IP = "10.77.0.11"
VM2_IP = "10.77.0.12"
REMOTE_ROOT = "/home/ubuntu/RTC"
IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
SENDER_PORT = 49900
# round3/round4 use their own bind ports rather than reusing SENDER_PORT.
# Root-caused via direct debug instrumentation: the CA-rotation crypto path
# itself was verified working correctly (chain_valid=1 for genuinely
# CA-B-signed traffic), but reusing round1/round2's exact source
# address:port for round3/round4 made this RMW's sequence-level dedup
# treat their samples as a continuation of the already-fully-received
# round1 stream from that same source and silently drop them --
# `taken` never advanced past round1's count despite verified_frames
# climbing normally, with none of the peer-auth counters (chain_failures,
# identity_denied, revoked_certificate_drops) showing anything wrong. A
# fresh source port per round sidesteps that unrelated dedup layer
# entirely rather than working around it.
ROUND3_SENDER_PORT = 49902
ROUND4_SENDER_PORT = 49903
RECEIVER_PORT = 49901
ROUND1_COUNT = 5
ROUND2_COUNT = 5
ROUND3_COUNT = 5
ROUND4_COUNT = 5
TEST_KEY_HEX = "4b" * 32

RECEIVER_SCRIPT = r'''
import ctypes
import json
import os
import time
from pathlib import Path

import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

library = ctypes.CDLL("librmw_fleetqox_cpp.so")


def metric(name):
    fn = getattr(library, name)
    fn.restype = ctypes.c_uint64
    return int(fn())


def snapshot():
    return {
        "verified_frames": metric("rmw_fleetqox_cpp_udp_peer_auth_verified_frames"),
        "revoked_certificate_drops": metric(
            "rmw_fleetqox_cpp_udp_peer_auth_revoked_certificate_drops"),
        "identity_denied": metric("rmw_fleetqox_cpp_udp_peer_auth_identity_denied"),
        "crl_reload_successes": metric(
            "rmw_fleetqox_cpp_udp_peer_auth_crl_reload_successes"),
        "crl_reload_failures": metric(
            "rmw_fleetqox_cpp_udp_peer_auth_crl_reload_failures"),
        "chain_failures": metric("rmw_fleetqox_cpp_udp_peer_auth_chain_failures"),
    }


taken = 0


def on_message(_msg):
    global taken
    taken += 1


rclpy.init(args=["--ros-args", "--enclave", os.environ["FLEETQOX_RMW_PROBE_ENCLAVE"]])
node = rclpy.create_node("fleetrmw_multihost_pki_receiver")
qos = QoSProfile(depth=20)
qos.reliability = ReliabilityPolicy.RELIABLE
node.create_subscription(String, "/fleetqox/multihost_pki_rotation_probe", on_message, qos)

round1_count = int(os.environ["ROUND1_COUNT"])
round3_target = round1_count + int(os.environ["ROUND3_COUNT"])
deadline = time.time() + float(os.environ.get("TOTAL_TIMEOUT_S", "90"))
ready_file = Path(os.environ["READY_FILE"])
round1_done_file = Path(os.environ["ROUND1_DONE_FILE"])
round3_done_file = Path(os.environ["ROUND3_DONE_FILE"])
stop_file = Path(os.environ["STOP_FILE"])

ready_file.touch()
round1_reported = False
round3_reported = False
result = {"schema_version": "fleetrmw.multihost_pki_receiver.v1"}
while time.time() < deadline:
    rclpy.spin_once(node, timeout_sec=0.1)
    if not round1_reported and taken >= round1_count:
        result["round1"] = {"taken": taken, **snapshot()}
        print(json.dumps({"checkpoint": "round1", **result["round1"]}), flush=True)
        round1_reported = True
        round1_done_file.touch()
    if round1_reported and not round3_reported and taken >= round3_target:
        result["round3"] = {"taken": taken, **snapshot()}
        print(json.dumps({"checkpoint": "round3", **result["round3"]}), flush=True)
        round3_reported = True
        round3_done_file.touch()
    if stop_file.exists():
        break

result["final"] = {"taken": taken, **snapshot()}
result["status"] = "ok" if round1_reported else "failed"
print(json.dumps(result), flush=True)
node.destroy_node()
rclpy.shutdown()
'''

SENDER_SCRIPT = r'''
import json
import os
import time

import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

rclpy.init(args=["--ros-args", "--enclave", os.environ["FLEETQOX_RMW_PROBE_ENCLAVE"]])
node = rclpy.create_node("fleetrmw_multihost_pki_sender")
qos = QoSProfile(depth=20)
qos.reliability = ReliabilityPolicy.RELIABLE
publisher = node.create_publisher(String, "/fleetqox/multihost_pki_rotation_probe", qos)

deadline = time.time() + 5.0
while time.time() < deadline and publisher.get_subscription_count() == 0:
    rclpy.spin_once(node, timeout_sec=0.1)

sample_count = int(os.environ["SAMPLE_COUNT"])
label = os.environ["ROUND_LABEL"]
sent = 0
for seq in range(sample_count):
    msg = String()
    msg.data = json.dumps({"round": label, "seq": seq})
    publisher.publish(msg)
    sent += 1
    rclpy.spin_once(node, timeout_sec=0.05)
    time.sleep(0.05)

# Linger alive (not just sleep(1)) rather than exiting right after the
# publish loop: this RMW's own reliable-retransmission background thread
# (reliable_retransmit_loop) is a real OS thread tied to *this process's*
# lifetime, not something a receiver can ever trigger on its own -- if a
# send happens to lose the very first-attempt race against a live CA/CRL
# reload on the receiving side, this process must still be alive when the
# retry timer fires or that sample is lost for good. Root-caused via a
# manual reproduction: a short-lived one-shot sender left samples stuck
# exactly at this gap, while running two independent sender processes
# back-to-back (the second one's own initial attempt landing after the
# reload had settled) got the rest through -- proving the mechanism itself
# works, just not within a process lifetime too short for its own retry
# path to ever run.
linger_deadline = time.time() + float(os.environ.get("LINGER_S", "2.0"))
while time.time() < linger_deadline:
    rclpy.spin_once(node, timeout_sec=0.1)
print(json.dumps({"schema_version": "fleetrmw.multihost_pki_sender.v1", "sent": sent}), flush=True)
node.destroy_node()
rclpy.shutdown()
'''

REVOKE_PYTHON = r'''
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
root = Path("{keystore}")
ca = x509.load_pem_x509_certificate((root / "public/identity_ca.cert.pem").read_bytes())
key = serialization.load_pem_private_key(
    (root / "private/identity_ca.key.pem").read_bytes(), password=None)
sender_cert = x509.load_pem_x509_certificate(
    (root / "enclaves/fleetqox/sender/cert.pem").read_bytes())
now = datetime.now(timezone.utc)
revoked = x509.RevokedCertificateBuilder().serial_number(
    sender_cert.serial_number).revocation_date(now).build()
crl = x509.CertificateRevocationListBuilder().issuer_name(ca.subject).last_update(
    now).next_update(now + timedelta(days=1)).add_revoked_certificate(revoked).sign(
    key, hashes.SHA256())
Path("{crl_path}").write_bytes(crl.public_bytes(serialization.Encoding.PEM))
'''

EMPTY_CRL_PYTHON = r'''
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
root = Path("{keystore}")
ca = x509.load_pem_x509_certificate((root / "public/identity_ca.cert.pem").read_bytes())
key = serialization.load_pem_private_key(
    (root / "private/identity_ca.key.pem").read_bytes(), password=None)
now = datetime.now(timezone.utc)
crl = x509.CertificateRevocationListBuilder().issuer_name(ca.subject).last_update(
    now).next_update(now + timedelta(days=1)).sign(key, hashes.SHA256())
Path("{crl_path}").write_bytes(crl.public_bytes(serialization.Encoding.PEM))
'''


def ssh_cmd(port: int) -> list[str]:
    return [
        "ssh", "-i", str(SSH_KEY), "-p", str(port),
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=5",
        "ubuntu@127.0.0.1",
    ]


def run_remote(port: int, remote_command: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ssh_cmd(port) + [remote_command],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False,
    )


def parse_last_json(text: str) -> dict[str, Any]:
    lines = [line for line in text.splitlines() if line.strip().startswith("{")]
    return json.loads(lines[-1]) if lines else {}


def ensure_build(port: int) -> subprocess.CompletedProcess:
    # Each VM has its own separate filesystem (no shared install dir like
    # same-host Docker runs enjoy), so rmw_fleetqox_cpp must be built once
    # per VM. Skip if an install already exists newer than every tracked
    # source file, mirroring ensure_generic_serialized_relay's mtime check
    # in run_ros2_relay_rmw_netem_probe.py.
    install_setup = "/home/ubuntu/RTC/.tmp_multihost_pki_install/setup.bash"
    check_and_build = (
        f"if [ -f {install_setup} ] && "
        "! find /home/ubuntu/RTC/ros2_ws/src/rmw_fleetqox_cpp "
        f"-newer {install_setup} "
        "\\( -name '*.cpp' -o -name '*.hpp' \\) | grep -q .; "
        "then echo FRESH; else echo NEEDS_BUILD; fi"
    )
    probe = run_remote(port, check_and_build)
    if "FRESH" in probe.stdout:
        return probe
    build_command = (
        "docker run --rm --entrypoint bash "
        f"-v {REMOTE_ROOT}:/work -w /work {IMAGE} -lc "
        + shlex.quote(
            "source /opt/ros/jazzy/setup.bash && "
            "export MAKEFLAGS=-j2 && "
            "rm -rf /work/.tmp_multihost_pki_build /work/.tmp_multihost_pki_install "
            "/work/.tmp_multihost_pki_log && "
            "colcon --log-base /work/.tmp_multihost_pki_log build "
            "--base-paths ros2_ws/src "
            "--packages-select fleetrmw_interfaces rmw_fleetqox_cpp "
            "--build-base /work/.tmp_multihost_pki_build "
            "--install-base /work/.tmp_multihost_pki_install "
            "--cmake-args -DCMAKE_BUILD_TYPE=Release"
        )
    )
    return run_remote(port, build_command, timeout=600.0)


def run_probe() -> dict[str, Any]:
    suffix = str(int(time.time()))
    work_dir_name = f".tmp_multihost_pki_{suffix}"
    remote_work = f"{REMOTE_ROOT}/{work_dir_name}"
    keystore = f"{REMOTE_ROOT}/.multihost_vm/pki_keystore"
    keystore_b = f"{REMOTE_ROOT}/.multihost_vm/pki_keystore_b"
    crl_path = f"{remote_work}/live.crl.pem"
    live_ca_path = f"{remote_work}/live-ca.cert.pem"
    ready_file = f"{remote_work}/receiver_ready"
    round1_done_file = f"{remote_work}/round1_done"
    round3_done_file = f"{remote_work}/round3_done"
    stop_file = f"{remote_work}/stop"

    for port in (VM1_SSH_PORT, VM2_SSH_PORT):
        run_remote(port, f"mkdir -p {shlex.quote(remote_work)}")
        build_result = ensure_build(port)
        if build_result.returncode != 0:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "reason": f"build_failed_port_{port}",
                "stderr": build_result.stderr[-4000:],
                "stdout": build_result.stdout[-4000:],
            }

    empty_crl_cmd = (
        "python3 -c " + shlex.quote(
            EMPTY_CRL_PYTHON.format(keystore=keystore, crl_path=crl_path))
    )
    init_result = run_remote(VM2_SSH_PORT, empty_crl_cmd)
    if init_result.returncode != 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "empty_crl_init_failed",
            "stderr": init_result.stderr[-2000:],
        }
    # live_ca_path starts as a copy of CA-A's own cert, since the receiver's
    # own identity (signed by CA-A) must validate against it once at
    # startup; the CA-rotation step below overwrites this file's *content*
    # in place with CA-B's cert to prove the live-reloaded peer trust store
    # (see maybe_reload_udp_peer_auth_crl) picks up a rotated CA, not just
    # a rotated CRL under the same CA.
    ca_copy_result = run_remote(
        VM2_SSH_PORT,
        f"cp {shlex.quote(f'{keystore}/public/identity_ca.cert.pem')} "
        f"{shlex.quote(live_ca_path)}",
    )
    if ca_copy_result.returncode != 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "live_ca_copy_failed",
            "stderr": ca_copy_result.stderr[-2000:],
        }

    receiver_script_path = f"{remote_work}/receiver.py"
    sender_script_path = f"{remote_work}/sender.py"
    run_remote(
        VM2_SSH_PORT,
        f"cat > {shlex.quote(receiver_script_path)} <<'PYEOF'\n{RECEIVER_SCRIPT}\nPYEOF",
    )
    run_remote(
        VM1_SSH_PORT,
        f"cat > {shlex.quote(sender_script_path)} <<'PYEOF'\n{SENDER_SCRIPT}\nPYEOF",
    )

    receiver_container = f"fleetrmw-mh-pki-receiver-{suffix}"
    receiver_base = f"{keystore}/enclaves/fleetqox/receiver"
    receiver_command = (
        f"docker run -d --name {receiver_container} --network host "
        f"--entrypoint bash -v {REMOTE_ROOT}:/work -w /work "
        "-e RMW_IMPLEMENTATION=rmw_fleetqox_cpp "
        f"-e FLEETQOX_RMW_UDP_AEAD_KEY_HEX={TEST_KEY_HEX} "
        "-e FLEETQOX_RMW_UDP_AEAD_REQUIRE=1 "
        "-e FLEETQOX_RMW_UDP_PEER_AUTH_REQUIRE=1 "
        "-e FLEETQOX_RMW_PROBE_ENCLAVE=/fleetqox/receiver "
        f"-e FLEETQOX_RMW_SROS2_IDENTITY_CERT_FILE=/work/.multihost_vm/pki_keystore/enclaves/fleetqox/receiver/cert.pem "
        f"-e FLEETQOX_RMW_SROS2_IDENTITY_KEY_FILE=/work/.multihost_vm/pki_keystore/enclaves/fleetqox/receiver/key.pem "
        f"-e FLEETQOX_RMW_SROS2_IDENTITY_CA_FILE=/work/{work_dir_name}/live-ca.cert.pem "
        f"-e FLEETQOX_RMW_SROS2_IDENTITY_CRL_FILE=/work/{work_dir_name}/live.crl.pem "
        "-e FLEETQOX_RMW_UDP_PEER_IDENTITIES=/fleetqox/sender "
        f"-e FLEETQOX_RMW_BIND={VM2_IP}:{RECEIVER_PORT} "
        f"-e FLEETQOX_RMW_PEERS={VM1_IP}:{SENDER_PORT},{VM1_IP}:{ROUND3_SENDER_PORT},{VM1_IP}:{ROUND4_SENDER_PORT} "
        f"-e ROUND1_COUNT={ROUND1_COUNT} -e ROUND2_COUNT={ROUND2_COUNT} "
        f"-e ROUND3_COUNT={ROUND3_COUNT} -e ROUND4_COUNT={ROUND4_COUNT} "
        "-e TOTAL_TIMEOUT_S=150 "
        f"-e READY_FILE=/work/{work_dir_name}/receiver_ready "
        f"-e ROUND1_DONE_FILE=/work/{work_dir_name}/round1_done "
        f"-e ROUND3_DONE_FILE=/work/{work_dir_name}/round3_done "
        f"-e STOP_FILE=/work/{work_dir_name}/stop "
        f"{IMAGE} -lc "
        + shlex.quote(
            "source /opt/ros/jazzy/setup.bash && "
            "source /work/.tmp_multihost_pki_install/setup.bash && "
            f"python3 /work/{work_dir_name}/receiver.py"
        )
    )
    start_result = run_remote(VM2_SSH_PORT, receiver_command)
    if start_result.returncode != 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "receiver_start_failed",
            "stderr": start_result.stderr[-2000:],
        }

    def wait_for_remote_path(port: int, path: str, timeout_s: float) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            check = run_remote(port, f"test -e {shlex.quote(path)} && echo yes")
            if "yes" in check.stdout:
                return True
            time.sleep(0.5)
        return False

    if not wait_for_remote_path(VM2_SSH_PORT, ready_file, 15.0):
        logs = run_remote(VM2_SSH_PORT, f"docker logs {receiver_container}")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "receiver_not_ready",
            "receiver_logs": logs.stdout[-4000:],
        }

    def run_sender(
        sender_port: int, sample_count: int, label: str, sender_keystore: str = keystore,
        linger_s: float = 2.0,
    ) -> dict[str, Any]:
        sender_container = f"fleetrmw-mh-pki-sender-{suffix}-{label}"
        sender_keystore_rel = sender_keystore[len(f"{REMOTE_ROOT}/"):]
        command = (
            f"docker run --rm --name {sender_container} --network host "
            f"--entrypoint bash -v {REMOTE_ROOT}:/work -w /work "
            "-e RMW_IMPLEMENTATION=rmw_fleetqox_cpp "
            f"-e FLEETQOX_RMW_UDP_AEAD_KEY_HEX={TEST_KEY_HEX} "
            "-e FLEETQOX_RMW_UDP_AEAD_REQUIRE=1 "
            "-e FLEETQOX_RMW_UDP_PEER_AUTH_REQUIRE=1 "
            "-e FLEETQOX_RMW_PROBE_ENCLAVE=/fleetqox/sender "
            f"-e FLEETQOX_RMW_SROS2_IDENTITY_CERT_FILE=/work/{sender_keystore_rel}/enclaves/fleetqox/sender/cert.pem "
            f"-e FLEETQOX_RMW_SROS2_IDENTITY_KEY_FILE=/work/{sender_keystore_rel}/enclaves/fleetqox/sender/key.pem "
            f"-e FLEETQOX_RMW_SROS2_IDENTITY_CA_FILE=/work/{sender_keystore_rel}/enclaves/fleetqox/sender/identity_ca.cert.pem "
            "-e FLEETQOX_RMW_UDP_PEER_IDENTITIES=/fleetqox/receiver "
            f"-e FLEETQOX_RMW_BIND={VM1_IP}:{sender_port} "
            f"-e FLEETQOX_RMW_PEERS={VM2_IP}:{RECEIVER_PORT} "
            f"-e SAMPLE_COUNT={sample_count} -e ROUND_LABEL={label} "
            f"-e LINGER_S={linger_s} "
            f"{IMAGE} -lc "
            + shlex.quote(
                "source /opt/ros/jazzy/setup.bash && "
                "source /work/.tmp_multihost_pki_install/setup.bash && "
                f"python3 /work/{work_dir_name}/sender.py"
            )
        )
        result = run_remote(VM1_SSH_PORT, command, timeout=linger_s + 30.0)
        return {
            "returncode": result.returncode,
            "stdout": parse_last_json(result.stdout),
            "stderr": result.stderr[-2000:] if result.returncode != 0 else "",
        }

    round1_sender = run_sender(SENDER_PORT, ROUND1_COUNT, "round1")

    if not wait_for_remote_path(VM2_SSH_PORT, round1_done_file, 15.0):
        logs = run_remote(VM2_SSH_PORT, f"docker logs {receiver_container}")
        run_remote(VM2_SSH_PORT, f"docker rm -f {receiver_container}")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "round1_not_observed",
            "round1_sender": round1_sender,
            "receiver_logs": logs.stdout[-4000:],
        }

    revoke_cmd = "python3 -c " + shlex.quote(
        REVOKE_PYTHON.format(keystore=keystore, crl_path=crl_path))
    revoke_result = run_remote(VM2_SSH_PORT, revoke_cmd)
    if revoke_result.returncode != 0:
        run_remote(VM2_SSH_PORT, f"docker rm -f {receiver_container}")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "revoke_failed",
            "stderr": revoke_result.stderr[-2000:],
        }

    time.sleep(1.0)
    round2_sender = run_sender(SENDER_PORT, ROUND2_COUNT, "round2")
    time.sleep(1.0)

    # CA rotation: overwrite live_ca_path's *content* with CA-B's cert (the
    # file path never changes, so this exercises the same live-reload path
    # as the CRL rewrite above -- see maybe_reload_udp_peer_auth_crl, which
    # rebuilds the whole trust store, CA included, from live_ca_path/
    # crl_path whenever crl_path's mtime changes). A fresh CRL must be
    # issued *by CA-B* onto the same crl_path to actually trigger that
    # reload and to give OpenSSL a CRL it can validate under the new
    # issuer.
    ca_rotate_result = run_remote(
        VM2_SSH_PORT,
        f"cp {shlex.quote(f'{keystore_b}/public/identity_ca.cert.pem')} "
        f"{shlex.quote(live_ca_path)}",
    )
    if ca_rotate_result.returncode != 0:
        run_remote(VM2_SSH_PORT, f"docker rm -f {receiver_container}")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "ca_rotate_failed",
            "stderr": ca_rotate_result.stderr[-2000:],
        }
    empty_crl_b_cmd = "python3 -c " + shlex.quote(
        EMPTY_CRL_PYTHON.format(keystore=keystore_b, crl_path=crl_path))
    crl_b_result = run_remote(VM2_SSH_PORT, empty_crl_b_cmd)
    if crl_b_result.returncode != 0:
        run_remote(VM2_SSH_PORT, f"docker rm -f {receiver_container}")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "crl_b_init_failed",
            "stderr": crl_b_result.stderr[-2000:],
        }

    time.sleep(1.0)
    round3_sender = run_sender(
        ROUND3_SENDER_PORT, ROUND3_COUNT, "round3", sender_keystore=keystore_b,
        linger_s=5.0)

    if not wait_for_remote_path(VM2_SSH_PORT, round3_done_file, 20.0):
        # Force the receiver's final snapshot out even though round3 never
        # arrived, so the failure carries the actual reload/chain-failure
        # counters instead of just "it never happened" -- this is what
        # actually distinguishes "CA rotation didn't reload" from "reload
        # happened but CA-B still rejected" from "packets never arrived".
        run_remote(VM2_SSH_PORT, f"touch {shlex.quote(stop_file)}")
        time.sleep(1.0)
        logs = run_remote(VM2_SSH_PORT, f"docker logs {receiver_container}")
        run_remote(VM2_SSH_PORT, f"docker rm -f {receiver_container}")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "round3_not_observed",
            "round3_sender": round3_sender,
            "ca_rotate_result": ca_rotate_result.stderr[-500:],
            "crl_b_result": crl_b_result.stderr[-500:],
            "receiver_logs": logs.stdout[-4000:],
        }

    time.sleep(1.0)
    # Same identity (CN /fleetqox/sender), same cert bytes as round1/round2
    # -- unchanged. Only the receiver's trusted CA changed. A rejection
    # here (not a revocation drop, since CA-B's fresh CRL knows nothing of
    # CA-A's certs at all) proves a genuine rotation -- the old CA is gone,
    # not just supplemented by a new one.
    round4_sender = run_sender(ROUND4_SENDER_PORT, ROUND4_COUNT, "round4")
    time.sleep(3.0)

    run_remote(VM2_SSH_PORT, f"touch {shlex.quote(stop_file)}")
    time.sleep(1.0)
    final_logs = run_remote(VM2_SSH_PORT, f"docker logs {receiver_container}")
    run_remote(VM2_SSH_PORT, f"docker rm -f {receiver_container}")
    run_remote(VM1_SSH_PORT, f"rm -rf {shlex.quote(remote_work)}")
    run_remote(VM2_SSH_PORT, f"rm -rf {shlex.quote(remote_work)}")

    lines = [line for line in final_logs.stdout.splitlines() if line.strip().startswith("{")]
    checkpoints = [json.loads(line) for line in lines]
    round1_checkpoint = next((c for c in checkpoints if c.get("checkpoint") == "round1"), {})
    round3_checkpoint = next((c for c in checkpoints if c.get("checkpoint") == "round3"), {})
    final_checkpoint = next((c for c in checkpoints if "final" in c), None)
    final = final_checkpoint.get("final", {}) if final_checkpoint else {}

    round1_ok = (
        round1_sender.get("returncode") == 0
        and round1_sender.get("stdout", {}).get("sent") == ROUND1_COUNT
        and round1_checkpoint.get("taken", 0) >= ROUND1_COUNT
        and round1_checkpoint.get("revoked_certificate_drops", 0) == 0
    )
    round2_ok = (
        round2_sender.get("returncode") == 0
        and round2_sender.get("stdout", {}).get("sent") == ROUND2_COUNT
        and round3_checkpoint.get("revoked_certificate_drops", 0) >= ROUND2_COUNT
        and round3_checkpoint.get("crl_reload_successes", 0) >= 1
        and round3_checkpoint.get("crl_reload_failures", 0) == 0
    )
    round3_ok = (
        round3_sender.get("returncode") == 0
        and round3_sender.get("stdout", {}).get("sent") == ROUND3_COUNT
        and round3_checkpoint.get("taken", 0)
        >= round1_checkpoint.get("taken", 0) + ROUND3_COUNT
        and round3_checkpoint.get("crl_reload_successes", 0)
        >= round1_checkpoint.get("crl_reload_successes", 0) + 1
    )
    round4_ok = (
        round4_sender.get("returncode") == 0
        and round4_sender.get("stdout", {}).get("sent") == ROUND4_COUNT
        and final.get("taken", 0) == round3_checkpoint.get("taken", 0)
        and final.get("chain_failures", 0)
        >= round3_checkpoint.get("chain_failures", 0) + ROUND4_COUNT
    )
    status = "ok" if round1_ok and round2_ok and round3_ok and round4_ok else "failed"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "round1_ok": round1_ok,
        "round2_ok": round2_ok,
        "round3_ok": round3_ok,
        "round4_ok": round4_ok,
        "round1_sender": round1_sender,
        "round2_sender": round2_sender,
        "round3_sender": round3_sender,
        "round4_sender": round4_sender,
        "round1_checkpoint": round1_checkpoint,
        "round3_checkpoint": round3_checkpoint,
        "final_checkpoint": final,
        "receiver_raw_logs": final_logs.stdout[-4000:] if status == "failed" else "",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/multihost_kvm_udp_peer_auth_crl_reload_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_probe()
    summary_path = ROOT / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-multihost-kvm-udp-peer-auth-crl-reload-probe")
        print(f"  status: {summary['status']}")
        print(f"  round1_ok: {summary.get('round1_ok')}")
        print(f"  round2_ok: {summary.get('round2_ok')}")
        print(f"  final_checkpoint: {summary.get('final_checkpoint')}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
