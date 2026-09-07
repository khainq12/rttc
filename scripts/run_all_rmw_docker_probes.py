"""Run every scripts/run_rmw_docker_*.py probe sequentially and report totals.

This is the driver behind the "N/N Docker integration probes pass in the
latest full sequential run" claim in README.md/docs/STATUS_AND_ROADMAP.md/
docs/EXPERIMENTAL_RESULTS_V1.md. Each probe script owns its own container
lifecycle (build, run, --rm teardown), so this driver just invokes them one
at a time, in sorted order, and aggregates pass/fail.

Resumable: results are appended to --jsonl-output as they land, and --resume
skips any script name already present in that file, so an interrupted run
can continue without repeating already-passing (or already-failing) probes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.full_docker_probe_suite.v1"


def discover_probe_scripts(only: str | None) -> list[Path]:
    scripts = sorted(ROOT.glob("scripts/run_rmw_docker_*.py"))
    if only:
        scripts = [s for s in scripts if only in s.name]
    return scripts


def already_run_names(jsonl_path: Path) -> set[str]:
    if not jsonl_path.exists():
        return set()
    names: set[str] = set()
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = row.get("script")
        if isinstance(name, str):
            names.add(name)
    return names


def last_json_line(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
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


def docker_is_healthy(timeout_s: float = 10.0) -> bool:
    try:
        completed = subprocess.run(
            ["docker", "ps"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
            check=False,
        )
        return completed.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def run_one(script: Path, *, timeout_s: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, str(script), "--json"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
        )
        elapsed_s = time.monotonic() - started
        summary = last_json_line(completed.stdout)
        ok = completed.returncode == 0
        return {
            "script": script.name,
            "ok": ok,
            "returncode": completed.returncode,
            "elapsed_s": round(elapsed_s, 1),
            "timed_out": False,
            "summary_status": summary.get("status") if summary else None,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        elapsed_s = time.monotonic() - started
        return {
            "script": script.name,
            "ok": False,
            "returncode": None,
            "elapsed_s": round(elapsed_s, 1),
            "timed_out": True,
            "summary_status": None,
            "stdout_tail": _as_text(exc.stdout)[-4000:],
            "stderr_tail": _as_text(exc.stderr)[-4000:],
        }


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=480)
    parser.add_argument("--only", default=None)
    parser.add_argument(
        "--jsonl-output",
        default="results_rmw_socket/full_docker_probe_suite_log.jsonl",
    )
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/full_docker_probe_suite_summary.json",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after running this many NEW (non-skipped) probes, so an outer "
        "loop can restart Docker Desktop between batches. Exit code is still 0 "
        "if every probe run so far passed.",
    )
    args = parser.parse_args()

    jsonl_path = ROOT / args.jsonl_output
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    skip_names = already_run_names(jsonl_path) if args.resume else set()
    if not args.resume and jsonl_path.exists():
        jsonl_path.unlink()

    scripts = discover_probe_scripts(args.only)
    total = len(scripts)
    results: list[dict[str, Any]] = []
    if args.resume:
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                results.append(json.loads(line))

    run_this_invocation = 0
    docker_died = False
    with jsonl_path.open("a", encoding="utf-8") as handle:
        for index, script in enumerate(scripts, start=1):
            if script.name in skip_names:
                print(f"[{index}/{total}] SKIP (resumed) {script.name}", flush=True)
                continue
            if args.limit is not None and run_this_invocation >= args.limit:
                print(f"[{index}/{total}] limit of {args.limit} reached, stopping batch", flush=True)
                break
            if not docker_is_healthy():
                print(
                    f"[{index}/{total}] docker daemon unresponsive before {script.name}, "
                    "stopping batch",
                    flush=True,
                )
                docker_died = True
                break
            print(f"[{index}/{total}] running {script.name} ...", flush=True)
            result = run_one(script, timeout_s=args.timeout)
            results.append(result)
            run_this_invocation += 1
            handle.write(json.dumps(result, sort_keys=True) + "\n")
            handle.flush()
            status = "OK" if result["ok"] else ("TIMEOUT" if result["timed_out"] else "FAIL")
            print(f"[{index}/{total}] {status} {script.name} ({result['elapsed_s']}s)", flush=True)
            if result["timed_out"] and not docker_is_healthy():
                print(
                    f"[{index}/{total}] docker daemon unresponsive after {script.name} "
                    "timed out, stopping batch",
                    flush=True,
                )
                docker_died = True
                break

    covered_names = skip_names | {r["script"] for r in results}
    remaining = [s.name for s in scripts if s.name not in covered_names]
    passed = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if not failed and not remaining else "failed",
        "total_discovered": total,
        "total_run": len(results),
        "passed": len(passed),
        "failed_count": len(failed),
        "failed_scripts": [r["script"] for r in failed],
        "remaining_count": len(remaining),
        "docker_died": docker_died,
    }
    summary_path = ROOT / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
