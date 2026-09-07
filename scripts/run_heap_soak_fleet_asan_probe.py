"""B0 heap-corruption reproduction attempt at the exact historical scale:
publisher -> relay -> subscriber (three processes, not two), N robot topics
multiplexed through each, real netem loss on the publisher's outbound link,
matching the documented repro command
`run_ros2_relay_rmw_netem_probe.py --robot-count 16` -- but with all three
processes hand-written in C++ and uniformly ASan/UBSan-compiled, since that
original command's publisher/subscriber are rclpy and LD_PRELOAD-ing ASan
into them hits a real, unrelated ASan/CPython interceptor bug (see
run_heap_soak_asan_probe.py's module docstring).

Prerequisite: the same ASan/UBSan colcon install used by
run_heap_soak_asan_probe.py (fleetrmw_heap_soak_publisher_probe,
fleetrmw_heap_soak_subscriber_probe, and fleetrmw_generic_serialized_relay_probe
all need to exist under <install>/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ros2_relay_rmw_netem_probe import (  # noqa: E402
    fleetqox_static_addresses,
)

DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
DEFAULT_INSTALL = ".tmp_fleetrmw_asan_install"
SCHEMA_VERSION = "fleetrmw.heap_soak_fleet_asan_probe.v2"
PUB_PREFIX = "/fleetqox/heap_soak_pub"
SUB_PREFIX = "/fleetqox/heap_soak_sub"

# The exact "roaming" primary_wifi netem parameters used by
# run_ros2_relay_rmw_netem_probe.py --profile roaming (see
# RouterPathTelemetryProfile "roaming" in
# run_rmw_docker_multi_robot_live_telemetry_plan_probe.py and its
# path_id="primary_wifi" call for the publisher link). At 16 robots x
# 32768 bytes this is genuinely, severely oversubscribed against the
# 5 mbit link (~16.8x), matching the documented historical scenario --
# not a milder approximation of it.
ROAMING_PRIMARY_DELAY_MS = 96.0
ROAMING_PRIMARY_JITTER_MS = 34.0
ROAMING_PRIMARY_LOSS_PERCENT = 28.0
ROAMING_PRIMARY_RATE_MBIT = 5.0


def last_json_object(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def library_is_asan_instrumented(*, image: str, install_root_host: Path) -> bool:
    binary = (
        install_root_host / "rmw_fleetqox_cpp" / "lib" /
        "librmw_fleetqox_cpp.so"
    )
    if not binary.exists():
        return False
    completed = run(
        ["docker", "run", "--rm", "--entrypoint", "bash",
         "-v", f"{binary.parent}:/lib_check", image, "-lc",
         "ldd /lib_check/librmw_fleetqox_cpp.so"],
        timeout=20.0,
    )
    return "libasan.so" in completed.stdout


def run(cmd: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=timeout,
    )


def robot_topic(prefix: str, robot_index: int) -> str:
    return f"{prefix}/robot_{robot_index:04d}/state"


def relay_mappings(robot_count: int) -> list[str]:
    return [
        f"--mapping {robot_topic(PUB_PREFIX, i)}={robot_topic(SUB_PREFIX, i)}"
        for i in range(robot_count)
    ]


def base_env_args(install: str, bind_port: int, peers: str) -> list[str]:
    install_root = f"/work/{install}"
    return [
        "-e", f"FLEETQOX_RMW_BIND=0.0.0.0:{bind_port}",
        "-e", f"FLEETQOX_RMW_PEERS={peers}",
        "-e", (
            f"ASAN_OPTIONS=abort_on_error=1:halt_on_error=1:detect_leaks=0:"
            f"log_path={install_root}/../.tmp_fleetrmw_asan_logs/asan"
        ),
        "-e", (
            f"UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1:"
            f"log_path={install_root}/../.tmp_fleetrmw_asan_logs/ubsan"
        ),
    ]


def with_core_dump_setup(command: str, *, core_dir_container: str, tag: str) -> str:
    # Backup to ASan's own report: if a corruption somehow produces a raw
    # SIGSEGV/SIGABRT that ASan's runtime does not itself intercept and
    # report cleanly, a core file is still evidence, not just a bare exit
    # code. core_pattern is a host-wide (not per-container-namespaceable)
    # kernel setting, so it is deliberately NOT touched here -- only
    # ulimit -c and cwd are set, relying on whatever the image's default
    # core_pattern already does (commonly a plain "core" file in cwd).
    del tag
    return (
        f"mkdir -p {core_dir_container} && "
        "ulimit -c unlimited && "
        f"cd {core_dir_container} && "
        + command
    )


def run_probe(
    *, root: Path, image: str, install: str, robot_count: int, payload_bytes: int,
    samples_per_robot: int, interval_ms: int, linger_s: int, total_wait_s: int,
    relay_timeout_ms: int, loss_percent: float, delay_ms: float, jitter_ms: float,
    rate_mbit: float, gdb_relay: bool = False,
) -> dict[str, Any]:
    pid = os.getpid()
    network = f"fq-heap-fleet-net-{pid}"
    install_root = f"/work/{install}"
    asan_log_dir = root / ".tmp_fleetrmw_asan_logs"
    asan_log_dir.mkdir(parents=True, exist_ok=True)
    for stale in asan_log_dir.glob("*"):
        stale.unlink()

    network_result = run(["docker", "network", "create", network], timeout=20.0)
    addresses = fleetqox_static_addresses(network)
    publisher_ip, relay_ip, subscriber_ip = (
        addresses["publisher"], addresses["relay"], addresses["subscriber"],
    )

    expected_total = robot_count * samples_per_robot
    subscriber_name = f"fq-heap-fleet-sub-{pid}"
    relay_name = f"fq-heap-fleet-relay-{pid}"
    publisher_name = f"fq-heap-fleet-pub-{pid}"

    core_dir_container = f"/work/.tmp_fleetrmw_asan_logs/cores-{pid}"

    subscriber_cmd = with_core_dump_setup(
        f"source {install_root}/setup.bash && "
        f"exec {install_root}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        f"fleetrmw_heap_soak_subscriber_probe "
        f"{payload_bytes} {robot_count} {samples_per_robot} {total_wait_s} {SUB_PREFIX}",
        core_dir_container=core_dir_container, tag="subscriber",
    )
    relay_binary_args = (
        " ".join(relay_mappings(robot_count))
        + f" --samples {samples_per_robot} --timeout-ms {relay_timeout_ms} "
        f"--linger-ms {linger_s * 1000}"
    )
    relay_binary_path = (
        f"{install_root}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        f"fleetrmw_generic_serialized_relay_probe"
    )
    if gdb_relay:
        # Live, full-symbol, all-threads backtrace at the exact moment ASan's
        # abort_on_error=1 fires -- more precise than addr2line on a raw
        # crash address post-mortem, which only resolved to the generic
        # __do_global_dtors_aux teardown loop, not the specific object.
        # Requires gdb already baked into --image (installing it at
        # container-start time delays relay readiness enough to desync the
        # timing that reproduces the crash in the first place).
        relay_exec = (
            "gdb -q -batch -ex run -ex 'thread apply all bt full' -ex quit "
            f"--args {relay_binary_path} {relay_binary_args}"
        )
    else:
        relay_exec = f"exec {relay_binary_path} {relay_binary_args}"
    relay_cmd = with_core_dump_setup(
        f"source {install_root}/setup.bash && " + relay_exec,
        core_dir_container=core_dir_container, tag="relay",
    )
    publisher_cmd = with_core_dump_setup(
        f"tc qdisc replace dev eth0 root netem delay {delay_ms:g}ms {jitter_ms:g}ms "
        f"loss random {loss_percent:g}% rate {rate_mbit:g}mbit && "
        f"source {install_root}/setup.bash && "
        f"exec {install_root}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        f"fleetrmw_heap_soak_publisher_probe "
        f"{payload_bytes} {robot_count} {samples_per_robot} {interval_ms} {linger_s} {PUB_PREFIX}",
        core_dir_container=core_dir_container, tag="publisher",
    )

    subscriber_start = run(
        [
            "docker", "run", "-d", "--name", subscriber_name, "--network", network,
            "--ip", subscriber_ip, "-e", "RMW_IMPLEMENTATION=rmw_fleetqox_cpp",
            *base_env_args(install, 49813, f"{relay_ip}:49812"),
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", subscriber_cmd,
        ],
        timeout=30.0,
    )
    relay_start = run(
        [
            "docker", "run", "-d", "--name", relay_name, "--network", network,
            "--ip", relay_ip, "-e", "RMW_IMPLEMENTATION=rmw_fleetqox_cpp",
            *base_env_args(install, 49812, f"{publisher_ip}:49811,{subscriber_ip}:49813"),
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", relay_cmd,
        ],
        timeout=30.0,
    )
    publisher_start = run(
        [
            "docker", "run", "-d", "--name", publisher_name, "--network", network,
            "--ip", publisher_ip, "--cap-add", "NET_ADMIN",
            "-e", "RMW_IMPLEMENTATION=rmw_fleetqox_cpp",
            *base_env_args(install, 49811, f"{relay_ip}:49812"),
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", publisher_cmd,
        ],
        timeout=30.0,
    )

    started = (
        subscriber_start.returncode == 0
        and relay_start.returncode == 0
        and publisher_start.returncode == 0
    )
    results: dict[str, subprocess.CompletedProcess[str]] = {}
    try:
        if started:
            names = [publisher_name, relay_name, subscriber_name]
            deadline = time.monotonic() + total_wait_s + linger_s + 60
            while time.monotonic() < deadline:
                states = [
                    run(["docker", "inspect", "-f", "{{.State.Running}}", n], timeout=10.0)
                    .stdout.strip()
                    for n in names
                ]
                if all(state == "false" for state in states):
                    break
                time.sleep(2.0)
            for role, name in zip(("publisher", "relay", "subscriber"), names):
                logs = run(["docker", "logs", name], timeout=20.0)
                exit_code = run(
                    ["docker", "inspect", "-f", "{{.State.ExitCode}}", name], timeout=10.0
                )
                results[role] = subprocess.CompletedProcess(
                    [], int(exit_code.stdout.strip() or -1), logs.stdout, logs.stderr,
                )
    finally:
        run(["docker", "rm", "-f", publisher_name], timeout=20.0)
        run(["docker", "rm", "-f", relay_name], timeout=20.0)
        run(["docker", "rm", "-f", subscriber_name], timeout=20.0)
        run(["docker", "network", "rm", network], timeout=20.0)

    sanitizer_reports = []
    for path in sorted(glob.glob(str(asan_log_dir / "*"))):
        if os.path.isdir(path):
            continue
        sanitizer_reports.append({"path": path, "content": Path(path).read_text(errors="replace")})

    core_dir_host = root / ".tmp_fleetrmw_asan_logs" / f"cores-{pid}"
    core_files = sorted(glob.glob(str(core_dir_host / "*"))) if core_dir_host.exists() else []
    if core_dir_host.exists() and not core_files:
        # Nothing to keep as evidence -- avoid leaving an empty directory
        # behind on every round of a long soak.
        try:
            core_dir_host.rmdir()
        except OSError:
            pass

    empty = subprocess.CompletedProcess([], -1, "", "")
    publisher_result = results.get("publisher", empty)
    relay_result = results.get("relay", empty)
    subscriber_result = results.get("subscriber", empty)

    # A role that printed its own trailing JSON summary ran to completion --
    # regardless of whether that JSON's own "status" is "ok" or "failed"
    # (e.g. the relay legitimately reporting incomplete delivery under real
    # 16.8x link oversubscription is not a crash). A role with NO parseable
    # JSON output died before it could report at all: that is the actual
    # crash signal this harness is hunting for, distinct from expected
    # under-delivery.
    publisher_json = last_json_object(publisher_result.stdout)
    relay_json = last_json_object(relay_result.stdout)
    subscriber_json = last_json_object(subscriber_result.stdout)
    any_role_crashed = (
        not started
        or publisher_json is None
        or relay_json is None
        or subscriber_json is None
    )

    relay_metrics = (relay_json or {}).get("fleetqox_transport_metrics", {})
    subscriber_received = (subscriber_json or {}).get("received")
    subscriber_expected = (subscriber_json or {}).get("expected_total", expected_total)
    delivery_complete = subscriber_received == subscriber_expected

    ok = (
        network_result.returncode == 0
        and not any_role_crashed
        and not sanitizer_reports
        and not core_files
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "robot_count": robot_count,
        "payload_bytes": payload_bytes,
        "samples_per_robot": samples_per_robot,
        "expected_total": expected_total,
        "loss_percent": loss_percent,
        "any_role_crashed": any_role_crashed,
        "delivery_complete": delivery_complete,
        "subscriber_received": subscriber_received,
        "subscriber_mismatches": (subscriber_json or {}).get("mismatches"),
        "subscriber_take_errors": (subscriber_json or {}).get("take_errors"),
        "relay_fragment_nacks_sent": relay_metrics.get("fragment_nacks_sent"),
        "relay_fragments_selectively_retransmitted":
            relay_metrics.get("fragments_selectively_retransmitted"),
        "relay_data_frames_received": relay_metrics.get("data_frames_received"),
        "publisher_returncode": publisher_result.returncode,
        "relay_returncode": relay_result.returncode,
        "subscriber_returncode": subscriber_result.returncode,
        "sanitizer_report_count": len(sanitizer_reports),
        "sanitizer_reports": sanitizer_reports,
        "core_file_count": len(core_files),
        "core_files": core_files,
        "publisher_stdout_tail": publisher_result.stdout[-3000:],
        "relay_stdout_tail": relay_result.stdout[-3000:],
        "subscriber_stdout_tail": subscriber_result.stdout[-3000:],
        "publisher_stderr_tail": publisher_result.stderr[-3000:],
        "relay_stderr_tail": relay_result.stderr[-3000:],
        "subscriber_stderr_tail": subscriber_result.stderr[-3000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--install", default=DEFAULT_INSTALL)
    parser.add_argument("--robot-count", type=int, default=16)
    parser.add_argument("--payload-bytes", type=int, default=32768)
    parser.add_argument("--samples-per-robot", type=int, default=10)
    parser.add_argument("--interval-ms", type=int, default=50)
    parser.add_argument("--linger-s", type=int, default=10)
    parser.add_argument("--total-wait-s", type=int, default=120)
    parser.add_argument("--relay-timeout-ms", type=int, default=120000)
    parser.add_argument("--loss-percent", type=float, default=ROAMING_PRIMARY_LOSS_PERCENT)
    parser.add_argument("--delay-ms", type=float, default=ROAMING_PRIMARY_DELAY_MS)
    parser.add_argument("--jitter-ms", type=float, default=ROAMING_PRIMARY_JITTER_MS)
    parser.add_argument("--rate-mbit", type=float, default=ROAMING_PRIMARY_RATE_MBIT)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--skip-asan-check", action="store_true",
        help="skip verifying librmw_fleetqox_cpp.so is actually ASan-instrumented",
    )
    parser.add_argument(
        "--gdb-relay", action="store_true",
        help="run the relay under gdb -batch to capture a live all-threads "
        "backtrace at the moment of an ASan abort, instead of relying on "
        "post-mortem addr2line",
    )
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/heap_soak_fleet_asan_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    install_root_host = ROOT / args.install
    asan_verified = None
    if not args.skip_asan_check:
        asan_verified = library_is_asan_instrumented(
            image=args.image, install_root_host=install_root_host,
        )
        print(f"librmw_fleetqox_cpp.so ASan-instrumented: {asan_verified}", flush=True)
        if not asan_verified:
            print(
                "refusing to run: the library under --install is not linked "
                "against libasan -- this run would prove nothing",
                flush=True,
            )
            return 2

    rounds = []
    for round_index in range(max(1, args.rounds)):
        result = run_probe(
            root=ROOT, image=args.image, install=args.install,
            robot_count=args.robot_count, payload_bytes=args.payload_bytes,
            samples_per_robot=args.samples_per_robot, interval_ms=args.interval_ms,
            linger_s=args.linger_s, total_wait_s=args.total_wait_s,
            relay_timeout_ms=args.relay_timeout_ms, loss_percent=args.loss_percent,
            delay_ms=args.delay_ms, jitter_ms=args.jitter_ms, rate_mbit=args.rate_mbit,
            gdb_relay=args.gdb_relay,
        )
        rounds.append(result)
        print(
            f"round {round_index + 1}/{args.rounds}: status={result['status']} "
            f"crashed={result['any_role_crashed']} "
            f"delivered={result['subscriber_received']}/{result['expected_total']} "
            f"sanitizer_reports={result['sanitizer_report_count']} "
            f"core_files={result['core_file_count']}",
            flush=True,
        )
        if result["sanitizer_report_count"] > 0 or result["core_file_count"] > 0:
            print("STOPPING: evidence found, preserving it instead of continuing", flush=True)
            break

    ok_rounds = sum(1 for r in rounds if r["status"] == "ok")
    crashed_rounds = sum(1 for r in rounds if r["any_role_crashed"])
    incomplete_delivery_rounds = sum(1 for r in rounds if not r["delivery_complete"])
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok_rounds == len(rounds) else "failed",
        "asan_verified": asan_verified,
        "round_count": len(rounds),
        "ok_round_count": ok_rounds,
        "crashed_round_count": crashed_rounds,
        "incomplete_delivery_round_count": incomplete_delivery_rounds,
        "rounds": rounds,
    }
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
