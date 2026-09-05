"""Run a direct ROS 2 RMW pub/sub baseline under Docker tc-netem."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_multi_robot_live_telemetry_plan_probe import (
    CONTROL_TOPIC,
    DEFAULT_IMAGE,
    NETEM_SCHEMA_VERSION,
    NETEM_SEED_SEMANTICS,
    STATE_TOPIC,
    netem_config_for_path,
    netem_shell_prefix,
    profile_by_name,
)


SCHEMA_VERSION = "fleetrmw.ros2_direct_rmw_netem_probe.v1"
DEFAULT_RMWS = "rmw_fastrtps_cpp,rmw_cyclonedds_cpp,rmw_zenoh_cpp"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--rmw", default="rmw_fastrtps_cpp")
    parser.add_argument(
        "--extra-workspace",
        default=None,
        help=(
            "container path to an additional colcon install space to source "
            "before checking rmw availability and launching nodes (e.g. for "
            "a custom RMW not present in the base ROS install)"
        ),
    )
    parser.add_argument("--profile", default="wifi")
    parser.add_argument("--enable-netem", action="store_true")
    parser.add_argument("--require-netem", action="store_true")
    parser.add_argument("--netem-loss-scale", type=float, default=0.0)
    parser.add_argument("--repetition-seed", type=int, default=None)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--robot-count", type=int, default=1)
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=0,
        help="exact UTF-8 message data size; zero preserves the metadata-only payload",
    )
    parser.add_argument(
        "--state-payload-bytes",
        type=int,
        default=0,
        help=(
            "override payload size for 'state' kind topics only (e.g. odom), "
            "for asymmetric control/state contention; zero falls back to "
            "--payload-bytes for state topics too"
        ),
    )
    parser.add_argument("--publish-interval-ms", type=int, default=500)
    parser.add_argument("--timeout-s", type=float, default=15.0)
    parser.add_argument(
        "--publisher-linger-s",
        type=float,
        default=0.5,
        help="keep the publisher alive after the last sample for RELIABLE repair",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path("results_rmw_socket/ros2_direct_rmw_netem_probe_summary.json"),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_probe(
        root=ROOT,
        image=args.image,
        rmw=args.rmw,
        profile=args.profile,
        enable_netem=args.enable_netem,
        require_netem=args.require_netem,
        netem_loss_scale=args.netem_loss_scale,
        repetition_seed=args.repetition_seed,
        samples=args.samples,
        robot_count=args.robot_count,
        payload_bytes=max(args.payload_bytes, 0),
        state_payload_bytes=max(args.state_payload_bytes, 0),
        publish_interval_ms=args.publish_interval_ms,
        timeout_s=args.timeout_s,
        publisher_linger_s=max(args.publisher_linger_s, 0.0),
        extra_workspace=args.extra_workspace,
    )
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("ros2-direct-rmw-netem-probe")
        print(f"  status: {summary['status']}")
        print(f"  rmw: {summary['rmw']}")
        print(f"  profile: {summary['profile']}")
        print(f"  control/state: {summary.get('control_payload_count')}/{summary.get('state_payload_count')}")
    return 0 if summary["status"] in {"ok", "skipped"} else 1


def run_probe(
    *,
    root: Path,
    image: str,
    rmw: str,
    profile: str,
    enable_netem: bool,
    require_netem: bool,
    netem_loss_scale: float,
    repetition_seed: int | None,
    samples: int,
    robot_count: int = 1,
    payload_bytes: int = 0,
    state_payload_bytes: int = 0,
    publish_interval_ms: int,
    timeout_s: float,
    publisher_linger_s: float = 0.5,
    extra_workspace: str | None = None,
) -> dict[str, Any]:
    if samples <= 0:
        raise ValueError("samples must be positive")
    if robot_count <= 0:
        raise ValueError("robot_count must be positive")
    if payload_bytes < 0:
        raise ValueError("payload_bytes must be non-negative")
    if publish_interval_ms < 0:
        raise ValueError("publish_interval_ms must be non-negative")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if publisher_linger_s < 0.0:
        raise ValueError("publisher_linger_s must be non-negative")
    if netem_loss_scale < 0.0:
        raise ValueError("netem_loss_scale must be non-negative")
    telemetry_profile = profile_by_name(profile)
    topic_specs = topic_specs_for_robot_count(robot_count)
    expected_control_count = samples * sum(1 for spec in topic_specs if spec["kind"] == "control")
    expected_state_count = samples * sum(1 for spec in topic_specs if spec["kind"] == "state")
    availability = probe_rmw_available(image, rmw, extra_workspace=extra_workspace)
    if not availability["available"]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "skipped",
            "reason": "rmw_unavailable",
            "image": image,
            "rmw": rmw,
            "profile": profile,
            "robot_count": robot_count,
            "rmw_probe": availability,
        }

    run_nonce = time.time_ns()
    suffix = f"{os.getpid()}-{run_nonce}"
    domain_id = 120 + (run_nonce % 80)
    network = f"fleetrmw-ros2-direct-net-{suffix}"
    subscriber_name = f"fleetrmw-ros2-direct-sub-{suffix}"
    publisher_name = f"fleetrmw-ros2-direct-pub-{suffix}"
    zenoh_router_name = f"fleetrmw-ros2-direct-zenoh-router-{suffix}"
    work_dir = root / f".tmp_fleetrmw_ros2_direct_{suffix}"
    subscriber_script = work_dir / "subscriber.py"
    publisher_script = work_dir / "publisher.py"
    publisher_netem_status = work_dir / "publisher_netem_status.json"
    publisher_netem_status_container = f"/work/{publisher_netem_status.relative_to(root)}"
    publisher_ready_container = "/tmp/fleetrmw_probe_ready"
    publisher_start_container = "/tmp/fleetrmw_probe_start"
    zenoh_session_config = work_dir / "zenoh-session-router.json5"
    zenoh_session_config_container = f"/work/{zenoh_session_config.relative_to(root)}"
    use_zenoh_router = rmw == "rmw_zenoh_cpp"
    use_fleetqox_peers = rmw == "rmw_fleetqox_cpp"
    fleetqox_port = 7400
    netem = netem_config_for_path(
        telemetry_profile,
        path_id="primary_wifi",
        loss_scale=netem_loss_scale,
        repetition_seed=repetition_seed,
    )
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        write_probe_scripts(
            subscriber_script=subscriber_script,
            publisher_script=publisher_script,
            samples=samples,
            topic_specs=topic_specs,
            payload_bytes=payload_bytes,
            state_payload_bytes=state_payload_bytes,
            publish_interval_ms=publish_interval_ms,
            timeout_s=timeout_s,
            publisher_linger_s=publisher_linger_s,
        )
        run(["docker", "network", "create", network])
        if use_zenoh_router:
            write_zenoh_session_config(
                zenoh_session_config,
                router_host=zenoh_router_name,
            )
            start_container(
                root=root,
                image=image,
                name=zenoh_router_name,
                network=network,
                command=(
                    "source /opt/ros/jazzy/setup.bash && "
                    "exec ros2 run rmw_zenoh_cpp rmw_zenohd"
                ),
            )
            wait_for_container_tcp(zenoh_router_name, port=7447, timeout_s=15.0)
        start_container(
            root=root,
            image=image,
            name=subscriber_name,
            network=network,
            command=ros_command(
                rmw=rmw,
                domain_id=domain_id,
                python_path=f"/work/{subscriber_script.relative_to(root)}",
                zenoh_session_config_uri=(
                    zenoh_session_config_container if use_zenoh_router else None
                ),
                extra_workspace=extra_workspace,
                fleetqox_bind=(
                    f"0.0.0.0:{fleetqox_port}" if use_fleetqox_peers else None
                ),
                fleetqox_peers=(
                    f"{publisher_name}:{fleetqox_port}" if use_fleetqox_peers else None
                ),
            ),
        )
        time.sleep(1.0)
        start_container(
            root=root,
            image=image,
            name=publisher_name,
            network=network,
            command=(
                f"export FLEETQOX_PROBE_READY_FILE={publisher_ready_container} "
                f"FLEETQOX_PROBE_START_FILE={publisher_start_container} && "
            )
            + ros_command(
                rmw=rmw,
                domain_id=domain_id,
                python_path=f"/work/{publisher_script.relative_to(root)}",
                zenoh_session_config_uri=(
                    zenoh_session_config_container if use_zenoh_router else None
                ),
                extra_workspace=extra_workspace,
                fleetqox_bind=(
                    f"0.0.0.0:{fleetqox_port}" if use_fleetqox_peers else None
                ),
                fleetqox_peers=(
                    f"{subscriber_name}:{fleetqox_port}" if use_fleetqox_peers else None
                ),
            ),
            extra_args=("--cap-add", "NET_ADMIN") if enable_netem else (),
        )
        wait_for_container_path(
            publisher_name,
            publisher_ready_container,
            timeout_s=12.0,
        )
        if enable_netem:
            run(
                [
                    "docker",
                    "exec",
                    publisher_name,
                    "bash",
                    "-lc",
                    netem_shell_prefix(
                        netem,
                        status_file=publisher_netem_status_container,
                        require=require_netem,
                    ),
                ]
            )
        run(["docker", "exec", publisher_name, "touch", publisher_start_container])
        publisher_returncode = int(run(["docker", "wait", publisher_name]).stdout.strip())
        subscriber_returncode = int(run(["docker", "wait", subscriber_name]).stdout.strip())
        publisher_log = run(["docker", "logs", publisher_name]).stdout.strip()
        subscriber_log = run(["docker", "logs", subscriber_name]).stdout.strip()
        publisher_result = parse_last_json(publisher_log)
        subscriber_result = parse_last_json(subscriber_log)
        netem_status = {
            "direct_pub": read_json(publisher_netem_status),
        }
        netem_ok = netem_status_ok(netem_status, enabled=enable_netem, required=require_netem)
        control_count = int(subscriber_result.get("control_payload_count", 0))
        state_count = int(subscriber_result.get("state_payload_count", 0))
        delivery_ok = control_count >= expected_control_count and state_count >= expected_state_count
        status = (
            publisher_returncode == 0
            and subscriber_returncode == 0
            and publisher_result.get("status") == "ok"
            and subscriber_result.get("status") == "ok"
            and publisher_result.get("payload_size_contract_ok") is True
            and delivery_ok
            and netem_ok
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if status else "failed",
            "image": image,
            "rmw": rmw,
            "profile": profile,
            "profile_config": telemetry_profile.as_dict(),
            "topics": [spec["topic"] for spec in topic_specs],
            "topic_specs": topic_specs,
            "topic_count": len(topic_specs),
            "robot_count": robot_count,
            "samples": samples,
            "samples_per_topic": samples,
            "payload_bytes": payload_bytes,
            "state_payload_bytes": state_payload_bytes,
            "payload_size_contract_ok": (
                publisher_result.get("payload_size_contract_ok") is True
            ),
            "payload_size_min_bytes": int(
                publisher_result.get("payload_size_min_bytes", 0)
            ),
            "payload_size_max_bytes": int(
                publisher_result.get("payload_size_max_bytes", 0)
            ),
            "publisher_linger_s": publisher_linger_s,
            "zenoh_router_enabled": use_zenoh_router,
            "repetition_seed": repetition_seed,
            "netem_enabled": enable_netem,
            "netem_required": require_netem,
            "netem_loss_scale": netem_loss_scale,
            "netem": netem,
            "netem_status": netem_status,
            "netem_schema_version": NETEM_SCHEMA_VERSION,
            "netem_seed_semantics": NETEM_SEED_SEMANTICS if enable_netem else "",
            "rmw_probe": availability,
            "publisher_returncode": publisher_returncode,
            "subscriber_returncode": subscriber_returncode,
            "publisher": publisher_result,
            "subscriber": subscriber_result,
            "control_payload_count": control_count,
            "state_payload_count": state_count,
            "control_expected_count": expected_control_count,
            "state_expected_count": expected_state_count,
            "control_delivery_ratio": control_count / expected_control_count,
            "state_delivery_ratio": state_count / expected_state_count,
            "control_latency_ms_mean": _float(subscriber_result.get("control_latency_ms_mean")),
            "state_latency_ms_mean": _float(subscriber_result.get("state_latency_ms_mean")),
            "control_latency_ms_p95": _float(subscriber_result.get("control_latency_ms_p95")),
            "state_latency_ms_p95": _float(subscriber_result.get("state_latency_ms_p95")),
            "min_topic_delivery_ratio": _float(subscriber_result.get("min_topic_delivery_ratio")),
            "per_topic_payload_count": subscriber_result.get("per_topic_payload_count", {}),
            "per_topic_delivery_ratio": subscriber_result.get("per_topic_delivery_ratio", {}),
        }
    except subprocess.CalledProcessError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "harness_exception",
            "image": image,
            "rmw": rmw,
            "profile": profile,
            "robot_count": robot_count,
            "topic_count": len(topic_specs),
            "repetition_seed": repetition_seed,
            "returncode": exc.returncode,
            "stdout_excerpt": excerpt(exc.stdout),
            "stderr_excerpt": excerpt(exc.stderr),
            "publisher_diagnostics": container_diagnostics(publisher_name),
            "subscriber_diagnostics": container_diagnostics(subscriber_name),
            "zenoh_router_diagnostics": (
                container_diagnostics(zenoh_router_name) if use_zenoh_router else {}
            ),
        }
    finally:
        for name in (publisher_name, subscriber_name, zenoh_router_name):
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True, text=True)
        subprocess.run(["docker", "network", "rm", network], check=False, capture_output=True, text=True)
        shutil.rmtree(work_dir, ignore_errors=True)


def write_probe_scripts(
    *,
    subscriber_script: Path,
    publisher_script: Path,
    samples: int,
    topic_specs: list[dict[str, str]] | None = None,
    payload_bytes: int = 0,
    state_payload_bytes: int = 0,
    publish_interval_ms: int,
    timeout_s: float,
    publisher_linger_s: float = 0.5,
) -> None:
    topic_specs = topic_specs or topic_specs_for_robot_count(1)
    topic_specs_json = json.dumps(topic_specs, sort_keys=True)
    subscriber_script.write_text(
        SUBSCRIBER_SCRIPT.replace("__SAMPLES__", str(samples))
        .replace("__TIMEOUT_S__", repr(timeout_s))
        .replace("__TOPIC_SPECS_JSON__", topic_specs_json),
        encoding="utf-8",
    )
    publisher_script.write_text(
        PUBLISHER_SCRIPT.replace("__SAMPLES__", str(samples)).replace(
            "__PUBLISH_INTERVAL_S__",
            repr(publish_interval_ms / 1000.0),
        ).replace("__PAYLOAD_BYTES__", str(payload_bytes)).replace(
            "__STATE_PAYLOAD_BYTES__", str(state_payload_bytes)
        ).replace(
            "__PUBLISHER_LINGER_S__", repr(max(publisher_linger_s, 0.0))
        ).replace(
            "__TOPIC_SPECS_JSON__", topic_specs_json
        ),
        encoding="utf-8",
    )


def topic_specs_for_robot_count(robot_count: int) -> list[dict[str, str]]:
    if robot_count <= 0:
        raise ValueError("robot_count must be positive")
    if robot_count == 1:
        return [
            {"topic": CONTROL_TOPIC, "kind": "control", "flow": "robot_0000/cmd_vel"},
            {"topic": STATE_TOPIC, "kind": "state", "flow": "robot_0001/odom"},
        ]
    specs = []
    for robot_index in range(robot_count):
        robot = f"robot_{robot_index:04d}"
        specs.append({"topic": f"/{robot}/cmd_vel", "kind": "control", "flow": f"{robot}/cmd_vel"})
        specs.append({"topic": f"/{robot}/odom", "kind": "state", "flow": f"{robot}/odom"})
    return specs


def probe_rmw_available(
    image: str, rmw: str, *, extra_workspace: str | None = None
) -> dict[str, object]:
    docker = shutil.which("docker")
    if not docker:
        return {"available": False, "reason": "docker_not_found", "docker": None}
    extra_source = (
        f"source {extra_workspace}/setup.bash && " if extra_workspace else ""
    )
    completed = subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "-v",
            f"{ROOT}:/work",
            image,
            f"source /opt/ros/jazzy/setup.bash && {extra_source}"
            f"ros2 pkg prefix {rmw}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "available": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": excerpt(completed.stderr),
    }


def ros_command(
    *,
    rmw: str,
    domain_id: int,
    python_path: str,
    zenoh_session_config_uri: str | None = None,
    extra_workspace: str | None = None,
    fleetqox_bind: str | None = None,
    fleetqox_peers: str | None = None,
) -> str:
    command = "source /opt/ros/jazzy/setup.bash && "
    if extra_workspace:
        command += f"source {extra_workspace}/setup.bash && "
    command += f"export RMW_IMPLEMENTATION={rmw} ROS_DOMAIN_ID={domain_id} && "
    if fleetqox_bind:
        command += f"export FLEETQOX_RMW_BIND={fleetqox_bind} && "
    if fleetqox_peers:
        command += f"export FLEETQOX_RMW_PEERS={fleetqox_peers} && "
    if zenoh_session_config_uri:
        command += f"export ZENOH_SESSION_CONFIG_URI={zenoh_session_config_uri} && "
    return command + f"python3 {python_path}"


def write_zenoh_session_config(path: Path, *, router_host: str) -> None:
    path.write_text(
        "{\n"
        '  mode: "client",\n'
        "  connect: {\n"
        "    timeout_ms: { router: -1, peer: -1, client: 0 },\n"
        f'    endpoints: ["tcp/{router_host}:7447"],\n'
        "    exit_on_failure: { router: false, peer: false, client: true },\n"
        "    retry: { period_init_ms: 200, period_max_ms: 1000, "
        "period_increase_factor: 2 },\n"
        "  },\n"
        "  listen: { timeout_ms: 0, endpoints: [\"tcp/localhost:0\"], "
        "exit_on_failure: true },\n"
        "  scouting: {\n"
        "    multicast: { enabled: false },\n"
        "    gossip: { enabled: false, multihop: false, "
        "target: { router: [\"router\", \"peer\"], peer: [\"router\"] }, "
        "autoconnect: { router: [], peer: [\"router\", \"peer\"] }, "
        "autoconnect_strategy: { peer: { to_router: \"always\", "
        "to_peer: \"greater-zid\" } } },\n"
        "  },\n"
        "}\n",
        encoding="utf-8",
    )


def wait_for_container_tcp(name: str, *, port: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    probe = (
        "import socket; "
        f"s=socket.create_connection(('127.0.0.1',{port}),0.5); s.close()"
    )
    while time.monotonic() < deadline:
        completed = subprocess.run(
            ["docker", "exec", name, "python3", "-c", probe],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return
        time.sleep(0.2)
    raise subprocess.CalledProcessError(
        1,
        ["docker", "exec", name, "python3", "-c", probe],
        stderr=f"container {name} did not listen on TCP {port}",
    )


def wait_for_container_path(name: str, path: str, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        completed = subprocess.run(
            ["docker", "exec", name, "test", "-e", path],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return
        if "is not running" in completed.stderr:
            raise subprocess.CalledProcessError(
                completed.returncode,
                completed.args,
                stderr=completed.stderr,
            )
        time.sleep(0.05)
    raise subprocess.CalledProcessError(
        1,
        ["docker", "exec", name, "test", "-e", path],
        stderr=f"timed out waiting for {path} in {name}",
    )


def start_container(
    *,
    root: Path,
    image: str,
    name: str,
    network: str,
    command: str,
    extra_args: tuple[str, ...] = (),
) -> None:
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            *extra_args,
            "-v",
            f"{root}:/work",
            "-w",
            "/work",
            image,
            command,
        ]
    )


def netem_status_ok(
    statuses: dict[str, object],
    *,
    enabled: bool,
    required: bool,
) -> bool:
    if not enabled:
        return True
    status = statuses.get("direct_pub")
    if not isinstance(status, dict):
        return not required
    if required:
        return status.get("status") == "applied"
    return status.get("status") in {"applied", "skipped", "missing_tc", "failed"}


def parse_last_json(text: str) -> dict[str, object]:
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
    return {"status": "parse_failed", "raw_tail": excerpt(text)}


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"status": "missing"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "parse_failed", "error": str(exc)}
    return value if isinstance(value, dict) else {"status": "parse_failed"}


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def excerpt(value: object, *, max_chars: int = 800) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def container_diagnostics(name: str) -> dict[str, object]:
    inspect = subprocess.run(
        ["docker", "inspect", name, "--format", "{{json .State}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    logs = subprocess.run(
        ["docker", "logs", name],
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "inspect_returncode": inspect.returncode,
        "state": excerpt(inspect.stdout, max_chars=2000),
        "logs_returncode": logs.returncode,
        "stdout_excerpt": excerpt(logs.stdout, max_chars=4000),
        "stderr_excerpt": excerpt(logs.stderr, max_chars=4000),
    }


def _float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


SUBSCRIBER_SCRIPT = r'''
import ctypes
import json
import os
import statistics
import time

import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import String

TOPIC_SPECS = __TOPIC_SPECS_JSON__
SAMPLES = __SAMPLES__
TIMEOUT_S = __TIMEOUT_S__


def percentile(values, percentile):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100.0
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


rclpy.init()
node = rclpy.create_node("fleetrmw_direct_baseline_subscriber")
qos = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)
payloads = {spec["topic"]: [] for spec in TOPIC_SPECS}
latencies = {spec["topic"]: [] for spec in TOPIC_SPECS}


def make_callback(topic):
    def callback(msg):
        now = time.time_ns()
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            payload = {"raw": msg.data}
        payloads[topic].append(payload)
        sent_ns = int(payload.get("sent_ns", 0) or 0)
        if sent_ns > 0:
            latencies[topic].append((now - sent_ns) / 1_000_000.0)
    return callback


subscriptions = [
    node.create_subscription(String, spec["topic"], make_callback(spec["topic"]), qos)
    for spec in TOPIC_SPECS
]
deadline = time.time() + TIMEOUT_S
while time.time() < deadline:
    rclpy.spin_once(node, timeout_sec=0.1)
    if all(len(payloads[topic]) >= SAMPLES for topic in payloads):
        break

control_topics = [spec["topic"] for spec in TOPIC_SPECS if spec["kind"] == "control"]
state_topics = [spec["topic"] for spec in TOPIC_SPECS if spec["kind"] == "state"]
control_latencies = [value for topic in control_topics for value in latencies[topic]]
state_latencies = [value for topic in state_topics for value in latencies[topic]]
per_topic_payload_count = {topic: len(values) for topic, values in payloads.items()}
per_topic_delivery_ratio = {
    topic: min(1.0, len(values) / SAMPLES)
    for topic, values in payloads.items()
}


def fleetqox_transport_metrics():
    if os.environ.get("RMW_IMPLEMENTATION") != "rmw_fleetqox_cpp":
        return {}
    try:
        library = ctypes.CDLL("librmw_fleetqox_cpp.so")
    except OSError:
        return {"available": False}
    names = (
        "data_frames_received",
        "fragment_nacks_sent",
        "fragment_nacks_received",
        "fragments_selectively_retransmitted",
        "fragment_repair_requests_coalesced",
        "fragment_repair_cooldown_coalesced",
        "completed_fragment_duplicates_dropped",
        "fragment_duplicate_no_progress_drops",
        "fragment_send_queue_rejections",
        "fragment_send_failures",
        "fragment_send_queue_high_water",
        "fragment_repair_queue_high_water",
        "fragment_repair_round_robin_rotations",
        "fragment_repair_frame_switches",
        "fragment_repair_max_active_frames",
        "fragment_repair_max_consecutive_same_frame_while_contended",
        "udp_datagram_size_high_water",
        "fragment_effective_chunk_bytes_min",
        "fragment_effective_chunk_bytes_max",
        "fragment_chunk_budget_reductions",
        "udp_datagram_budget_failures",
        "fragment_queue_admission_waits",
        "fragment_queue_admission_timeouts",
        "fragment_queue_admission_wait_ns",
        "fragment_repair_queue_deferrals",
        "fragment_repair_pressure_priority_promotions",
        "fragment_completion_markers_sent",
        "fragment_completion_markers_received",
        "fragment_completion_marker_orphans",
        "fragment_completion_marker_failures",
        "fragment_repair_source_denials",
        "fragment_repair_reader_budget_exhausted",
        "fragment_initial_round_robin_rotations",
        "fragment_initial_frame_switches",
        "fragment_initial_max_consecutive_same_frame",
        "fragment_initial_max_consecutive_same_frame_while_contended",
        "fragment_initial_max_active_frames",
        "fragment_async_send_completions",
        "fragment_initial_pending_timeout_suppressions",
        "fragment_whole_fallback_grace_deferrals",
        "fragment_nack_indexes_requested",
        "fragment_nack_index_budget_reductions",
        "fragment_nack_max_sweep_indexes_requested",
        "fragment_nack_sweep_budget_exhaustions",
        "fragment_progressive_nacks_sent",
        "fragment_progress_grace_deferrals",
        "fragment_active_assemblies",
        "fragment_active_missing_indexes",
        "fragment_nack_exhausted_assemblies",
        "fragment_oldest_assembly_age_ms",
        "fragment_history_request_exhausted",
        "fragment_assembly_evictions",
        "fragment_assembly_oversize_drops",
        "fragment_assembly_metadata_mismatch_drops",
        "fragment_assembly_ttl_expirations",
        "fragment_assembly_ttl_expired_missing_indexes",
        "fragment_observed_timeout_retransmissions_suppressed",
        "fragment_whole_fallback_pacing_deferrals",
        "nack_retransmissions",
    )
    metrics = {"available": True}
    for name in names:
        symbol = getattr(library, f"rmw_fleetqox_cpp_socket_{name}")
        symbol.restype = ctypes.c_uint64
        metrics[name] = int(symbol())
    return metrics


result = {
    "status": "ok" if all(len(payloads[topic]) >= SAMPLES for topic in payloads) else "failed",
    "control_payload_count": sum(len(payloads[topic]) for topic in control_topics),
    "state_payload_count": sum(len(payloads[topic]) for topic in state_topics),
    "control_expected_count": SAMPLES * len(control_topics),
    "state_expected_count": SAMPLES * len(state_topics),
    "control_latency_ms_mean": statistics.mean(control_latencies) if control_latencies else 0.0,
    "state_latency_ms_mean": statistics.mean(state_latencies) if state_latencies else 0.0,
    "control_latency_ms_p95": percentile(control_latencies, 95),
    "state_latency_ms_p95": percentile(state_latencies, 95),
    "min_topic_delivery_ratio": min(per_topic_delivery_ratio.values()) if per_topic_delivery_ratio else 0.0,
    "per_topic_payload_count": per_topic_payload_count,
    "per_topic_delivery_ratio": per_topic_delivery_ratio,
    "payloads": payloads,
    "fleetqox_transport_metrics": fleetqox_transport_metrics(),
}
print(json.dumps(result, sort_keys=True))
node.destroy_node()
rclpy.shutdown()
raise SystemExit(0 if result["status"] == "ok" else 1)
'''


PUBLISHER_SCRIPT = r'''
import ctypes
import json
import os
from pathlib import Path
import time

import rclpy
from rclpy.duration import Duration
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import String

TOPIC_SPECS = __TOPIC_SPECS_JSON__
SAMPLES = __SAMPLES__
PUBLISH_INTERVAL_S = __PUBLISH_INTERVAL_S__
PAYLOAD_BYTES = __PAYLOAD_BYTES__
STATE_PAYLOAD_BYTES = __STATE_PAYLOAD_BYTES__
PUBLISHER_ACK_HORIZON_S = __PUBLISHER_LINGER_S__

rclpy.init()
node = rclpy.create_node("fleetrmw_direct_baseline_publisher")
qos = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    reliability=ReliabilityPolicy.RELIABLE,
)
publishers = {
    spec["topic"]: node.create_publisher(String, spec["topic"], qos)
    for spec in TOPIC_SPECS
}
deadline = time.time() + 5.0
while time.time() < deadline:
    rclpy.spin_once(node, timeout_sec=0.1)
    if all(pub.get_subscription_count() > 0 for pub in publishers.values()):
        break

ready_file = os.environ.get("FLEETQOX_PROBE_READY_FILE", "")
start_file = os.environ.get("FLEETQOX_PROBE_START_FILE", "")
if ready_file:
    Path(ready_file).touch()
if start_file:
    start_deadline = time.time() + 15.0
    while time.time() < start_deadline and not Path(start_file).exists():
        rclpy.spin_once(node, timeout_sec=0.05)
    if not Path(start_file).exists():
        raise RuntimeError("timed out waiting for data-plane start gate")

sent = {"control": 0, "state": 0}
sent_by_topic = {spec["topic"]: 0 for spec in TOPIC_SPECS}
payload_sizes = []
for seq in range(1, SAMPLES + 1):
    now = time.time_ns()
    for spec in TOPIC_SPECS:
        msg = String()
        payload = {
            "flow": spec["flow"],
            "kind": spec["kind"],
            "seq": seq,
            "sent_ns": now,
            "topic": spec["topic"],
        }
        target_bytes = (
            STATE_PAYLOAD_BYTES if spec["kind"] == "state" and STATE_PAYLOAD_BYTES > 0
            else PAYLOAD_BYTES
        )
        if target_bytes > 0:
            payload["padding"] = ""
            compact = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            padding_bytes = target_bytes - len(compact.encode("utf-8"))
            if padding_bytes < 0:
                raise RuntimeError(
                    f"payload target {target_bytes} is smaller than metadata "
                    f"for {spec['topic']}"
                )
            payload["padding"] = "x" * padding_bytes
            msg.data = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            msg.data = json.dumps(payload, sort_keys=True)
        payload_sizes.append(len(msg.data.encode("utf-8")))
        publishers[spec["topic"]].publish(msg)
        sent[spec["kind"]] += 1
        sent_by_topic[spec["topic"]] += 1
    rclpy.spin_once(node, timeout_sec=0.05)
    time.sleep(PUBLISH_INTERVAL_S)

ack_wait_started = time.monotonic()
ack_wait_deadline = ack_wait_started + PUBLISHER_ACK_HORIZON_S
pending_ack_topics = list(publishers)
ack_wait_supported = True
while pending_ack_topics and time.monotonic() < ack_wait_deadline:
    next_pending = []
    try:
        for topic in pending_ack_topics:
            if not publishers[topic].wait_for_all_acked(
                timeout=Duration(nanoseconds=0)
            ):
                next_pending.append(topic)
    except (NotImplementedError, RuntimeError):
        ack_wait_supported = False
        break
    pending_ack_topics = next_pending
    if pending_ack_topics:
        rclpy.spin_once(node, timeout_sec=0.02)
if not ack_wait_supported:
    remaining = ack_wait_deadline - time.monotonic()
    if remaining > 0.0:
        time.sleep(remaining)
ack_wait_elapsed_s = time.monotonic() - ack_wait_started
subscription_counts = {
    topic: pub.get_subscription_count()
    for topic, pub in publishers.items()
}


def fleetqox_transport_metrics():
    if os.environ.get("RMW_IMPLEMENTATION") != "rmw_fleetqox_cpp":
        return {}
    try:
        library = ctypes.CDLL("librmw_fleetqox_cpp.so")
    except OSError:
        return {"available": False}
    names = (
        "fragment_nacks_sent",
        "fragment_nacks_received",
        "fragments_selectively_retransmitted",
        "fragment_repair_requests_coalesced",
        "fragment_repair_cooldown_coalesced",
        "completed_fragment_duplicates_dropped",
        "fragment_duplicate_no_progress_drops",
        "test_dropped_fragments",
        "fragment_send_queue_rejections",
        "fragment_send_failures",
        "fragment_send_queue_high_water",
        "fragment_repair_queue_high_water",
        "fragment_repair_round_robin_rotations",
        "fragment_repair_frame_switches",
        "fragment_repair_max_active_frames",
        "fragment_repair_max_consecutive_same_frame_while_contended",
        "udp_datagram_size_high_water",
        "fragment_effective_chunk_bytes_min",
        "fragment_effective_chunk_bytes_max",
        "fragment_chunk_budget_reductions",
        "udp_datagram_budget_failures",
        "fragment_queue_admission_waits",
        "fragment_queue_admission_timeouts",
        "fragment_queue_admission_wait_ns",
        "fragment_repair_queue_deferrals",
        "fragment_repair_pressure_priority_promotions",
        "fragment_completion_markers_sent",
        "fragment_completion_markers_received",
        "fragment_completion_marker_orphans",
        "fragment_completion_marker_failures",
        "fragment_repair_source_denials",
        "fragment_repair_reader_budget_exhausted",
        "fragment_initial_round_robin_rotations",
        "fragment_initial_frame_switches",
        "fragment_initial_max_consecutive_same_frame",
        "fragment_initial_max_consecutive_same_frame_while_contended",
        "fragment_initial_max_active_frames",
        "fragment_async_send_completions",
        "fragment_initial_pending_timeout_suppressions",
        "fragment_whole_fallback_grace_deferrals",
        "fragment_nack_indexes_requested",
        "fragment_nack_index_budget_reductions",
        "fragment_nack_max_sweep_indexes_requested",
        "fragment_nack_sweep_budget_exhaustions",
        "fragment_progressive_nacks_sent",
        "fragment_progress_grace_deferrals",
        "fragment_active_assemblies",
        "fragment_active_missing_indexes",
        "fragment_nack_exhausted_assemblies",
        "fragment_oldest_assembly_age_ms",
        "fragment_history_request_exhausted",
        "fragment_assembly_evictions",
        "fragment_assembly_oversize_drops",
        "fragment_assembly_metadata_mismatch_drops",
        "fragment_assembly_ttl_expirations",
        "fragment_assembly_ttl_expired_missing_indexes",
        "fragment_observed_timeout_retransmissions_suppressed",
        "fragment_whole_fallback_pacing_deferrals",
        "nack_retransmissions",
        "reliable_timeout_retransmissions",
    )
    metrics = {"available": True}
    for name in names:
        symbol = getattr(library, f"rmw_fleetqox_cpp_socket_{name}")
        symbol.restype = ctypes.c_uint64
        metrics[name] = int(symbol())
    return metrics


result = {
    "status": "ok",
    "payload_bytes": PAYLOAD_BYTES,
    "payload_size_min_bytes": min(payload_sizes) if payload_sizes else 0,
    "payload_size_max_bytes": max(payload_sizes) if payload_sizes else 0,
    "payload_size_contract_ok": (
        bool(payload_sizes)
        and (
            PAYLOAD_BYTES == 0
            or all(size == PAYLOAD_BYTES for size in payload_sizes)
        )
    ),
    "control_sent": sent["control"],
    "state_sent": sent["state"],
    "sent_by_topic": sent_by_topic,
    "subscription_counts": subscription_counts,
    "control_subscription_count": sum(
        subscription_counts[spec["topic"]] for spec in TOPIC_SPECS if spec["kind"] == "control"
    ),
    "state_subscription_count": sum(
        subscription_counts[spec["topic"]] for spec in TOPIC_SPECS if spec["kind"] == "state"
    ),
    "min_subscription_count": min(subscription_counts.values()) if subscription_counts else 0,
    "ack_wait_supported": ack_wait_supported,
    "ack_wait_complete": ack_wait_supported and not pending_ack_topics,
    "unacked_topic_count": len(pending_ack_topics),
    "ack_wait_elapsed_s": ack_wait_elapsed_s,
    "fleetqox_transport_metrics": fleetqox_transport_metrics(),
}
print(json.dumps(result, sort_keys=True))
node.destroy_node()
rclpy.shutdown()
'''


if __name__ == "__main__":
    raise SystemExit(main())
