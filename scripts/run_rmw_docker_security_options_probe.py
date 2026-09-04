"""Build and run the rmw_fleetqox_cpp security-options lifecycle probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_nav2_navigate_to_pose_probe import DEFAULT_IMAGE  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_security_options_probe.v1"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def parse_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def parse_json_rows(stdout: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def parse_key_value_markers(stdout: str) -> dict[str, str]:
    markers: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        markers[key.strip()] = value.strip()
    return markers


def run_probe(*, root: Path, image: str, iterations: int = 1) -> dict[str, Any]:
    run_count = max(iterations, 1)
    build_base = root / ".tmp_fleetrmw_security_options_v2_build"
    install_base = root / ".tmp_fleetrmw_security_options_v2_install"
    log_base = root / ".tmp_fleetrmw_security_options_v2_log"
    try:
        build = run(
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
                "source /opt/ros/jazzy/setup.bash && "
                f"rm -rf /work/{build_base.relative_to(root)} "
                f"/work/{install_base.relative_to(root)} "
                f"/work/{log_base.relative_to(root)} && "
                f"colcon --log-base /work/{log_base.relative_to(root)} build "
                "--base-paths ros2_ws/src --packages-select rmw_fleetqox_cpp "
                f"--build-base /work/{build_base.relative_to(root)} "
                f"--install-base /work/{install_base.relative_to(root)} "
                "--cmake-args -DCMAKE_BUILD_TYPE=Release",
            ]
        )
        if build.returncode != 0:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "stage": "build",
                "stdout": build.stdout,
                "stderr": build.stderr,
            }
        run_result = run(
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
                "source /opt/ros/jazzy/setup.bash && "
                f"source /work/{install_base.relative_to(root)}/setup.bash && "
                f"for i in $(seq 1 {run_count}); do "
                "ros2 run rmw_fleetqox_cpp fleetrmw_security_options_probe || exit $?; "
                "done",
            ]
        )
        security_env_result = run(
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
                "source /opt/ros/jazzy/setup.bash; "
                "set +e; "
                "command -v ros2 >/dev/null 2>&1; echo ros2_cli_rc=$?; "
                "ros2 security --help >/dev/null 2>&1; echo ros2_security_help_rc=$?; "
                "command -v openssl >/dev/null 2>&1; echo openssl_rc=$?",
            ]
        )
        security_env_markers = parse_key_value_markers(security_env_result.stdout)
        ros2_cli_available = security_env_markers.get("ros2_cli_rc") == "0"
        sros2_cli_available = security_env_markers.get("ros2_security_help_rc") == "0"
        openssl_available = security_env_markers.get("openssl_rc") == "0"
        security_policy_enforcement_executed = False
        security_policy_enforcement_gap_reason = (
            "sros2_cli_missing"
            if not sros2_cli_available
            else "full_sros2_policy_enforcement_not_implemented"
        )
        security_gap_next_step = (
            "install the ROS 2 security command plugin and generate a keystore"
            if not sros2_cli_available
            else (
                "extend scoped signed authorization with remote peer authentication, "
                "transport security, and revocation checks"
            )
        )
        rows = parse_json_rows(run_result.stdout)
        probe = rows[-1] if rows else parse_json(run_result.stdout)
        ok_rows = [
            row
            for row in rows
            if row.get("status") == "ok"
            and row.get("security_options_lifecycle_abi_supported") is True
            and row.get("context_init_copies_security_options") is True
            and row.get("sros2_policy_enforcement_claim") is False
        ]
        ok = (
            build.returncode == 0
            and run_result.returncode == 0
            and len(rows) == run_count
            and len(ok_rows) == run_count
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "run_count": run_count,
            "ok_run_count": len(ok_rows),
            "security_options_lifecycle_abi_supported": bool(
                probe.get("security_options_lifecycle_abi_supported")
            ),
            "default_enclave_initialized": bool(probe.get("default_enclave_initialized")),
            "custom_enclave_configured": bool(probe.get("custom_enclave_configured")),
            "init_options_copy_preserves_enclave": bool(
                probe.get("init_options_copy_preserves_enclave")
            ),
            "init_options_copy_deep_copies_enclave": bool(
                probe.get("init_options_copy_deep_copies_enclave")
            ),
            "context_init_copies_security_options": bool(
                probe.get("context_init_copies_security_options")
            ),
            "context_shutdown_fini_ok": bool(probe.get("context_shutdown_fini_ok")),
            "ros2_cli_available": ros2_cli_available,
            "sros2_cli_available": sros2_cli_available,
            "openssl_available": openssl_available,
            "security_policy_enforcement_executed": security_policy_enforcement_executed,
            "security_policy_enforcement_gap_reason": security_policy_enforcement_gap_reason,
            "security_hardening_blocker": security_policy_enforcement_gap_reason,
            "security_gap_next_step": security_gap_next_step,
            "sros2_policy_enforcement_scope": "not_executed_lifecycle_only",
            "sros2_policy_enforcement_claim": False,
            "production_security_hardening_claim": False,
            "security_options_repeated_lifecycle_claim": ok and run_count >= 5,
            "probe": probe,
            "runs": rows,
            "security_environment_probe": {
                "docker_returncode": security_env_result.returncode,
                "stdout": security_env_result.stdout,
                "stderr": security_env_result.stderr,
            },
            "docker_returncode": run_result.returncode,
            "docker_stdout": run_result.stdout,
            "docker_stderr": run_result.stderr,
        }
    finally:
        for path in (build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_security_options_probe_summary.json",
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
            f"status={summary['status']} "
            f"security_options={summary.get('security_options_lifecycle_abi_supported')} "
            f"sros2={summary.get('sros2_policy_enforcement_claim')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
