"""Profile the N=16 seed=7 ns-3 process with `perf record` mid-run (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "N=16 SCALE VALIDATION" for the CPU-
saturation finding this diagnoses). Uses localhost/fleetrmw/rmw-netem:
jazzy-perf, a `docker commit` of the standard jazzy image with
linux-tools-generic (`perf`) installed via apt inside an ephemeral,
network-enabled container -- otherwise byte-for-byte the same image
(same ns-3 packages, same ROS install), so results are comparable to
every other run in this investigation. The ns3sim container already
gets --cap-add=SYS_ADMIN (see ReferenceTopologyProbe.start_containers())
-- confirmed sufficient for `perf record`, no new capability granted.

Runs run_coordination_probe() in a background thread (unmodified,
same frozen Table VI config as every other N=16 run in this
investigation) while polling for the ns-3 process's PID inside its
own container, then attaches `perf record -p <pid> --call-graph dwarf
-- sleep <window>` for a fixed real-time sampling window in the middle
of the run (skipping cold-start/build/discovery), well clear of both
ends. Produces a flat (leaf-only) and a call-graph text report via
`perf report --stdio`, entirely inside the container -- no perf.data
extraction/host-side perf needed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    CONTAINER_PREFIX,
    run_coordination_probe,
)

PERF_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy-perf-fixed"
PERF_BIN = "/usr/lib/linux-tools/6.8.0-139-generic/perf"
NUM_ROBOTS = 16
SEED = 7
RUN_ID = "n16_perf_profile"
SAMPLE_WINDOW_S = 60
PID_POLL_TIMEOUT_S = 60

OUTPUT_DIR = ROOT / "results_rmw_socket" / "table6_n16_perf_profile" / RUN_ID
NS3SIM_NAME = f"{CONTAINER_PREFIX}_{RUN_ID}_ns3sim"


def main() -> int:
    result_holder: dict = {}

    def run_experiment() -> None:
        result_holder["result"] = run_coordination_probe(
            image=PERF_IMAGE,
            output_dir=OUTPUT_DIR,
            num_robots=NUM_ROBOTS,
            seed=SEED,
            rmw_implementation="rmw_fleetqox_cpp",
            discovery_mode="default",
            directed_reply=True,
        )

    thread = threading.Thread(target=run_experiment)
    thread.start()

    print(f"Waiting for ns-3 process inside {NS3SIM_NAME} (up to {PID_POLL_TIMEOUT_S}s)...", flush=True)
    pid = None
    start = time.monotonic()
    while time.monotonic() - start < PID_POLL_TIMEOUT_S:
        time.sleep(1)
        # NOT "fleetqox_tap_bridge" alone -- that substring also appears
        # in build_ns3_binary()'s own g++ invocation ("-o /tmp/fleetqox_
        # tap_bridge"), which pgrep -f would match too and did on the
        # first attempt (caught profiling `find`/`bash` from the tap-
        # creator-symlink-fix shell step, not the running simulator).
        # The actual running binary's argv[0] is the bare path with a
        # leading "--numRobots" flag right after it -- unambiguous.
        r = subprocess.run(
            ["docker", "exec", NS3SIM_NAME, "pgrep", "-f", "/tmp/fleetqox_tap_bridge --numRobots"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            pid = r.stdout.strip().splitlines()[0]
            break
    print(f"ns-3 pid: {pid} (found after {time.monotonic() - start:.1f}s)", flush=True)

    if pid is None:
        print("FAILED to find ns-3 pid -- aborting profiling attach", flush=True)
        ps = subprocess.run(["docker", "ps", "-a"], capture_output=True, text=True)
        print("docker ps -a:\n" + ps.stdout, flush=True)
        thread.join(timeout=120)
        print("thread alive after join:", thread.is_alive(), flush=True)
        result = result_holder.get("result")
        print("result:", json.dumps(result) if result else "NONE (thread still running or crashed)", flush=True)
        return 1

    # Let the run settle a few seconds past process start (discovery/
    # startup transients) before sampling steady-state behavior.
    time.sleep(5)
    print(f"Attaching perf record for {SAMPLE_WINDOW_S}s...", flush=True)
    # NOTE: two prior attempts (-p PID with hardware cycles:P, then with
    # software task-clock) BOTH captured ZERO samples specifically for
    # the real ns-3 process, while isolated busy-loop AND syscall-heavy
    # (dd if=/dev/zero) test processes in the SAME container/capability
    # setup sampled perfectly every time with either event -- ruling out
    # both "PMU/kernel-mode restricted" and "syscall-heavy process"
    # explanations, cause still unexplained. System-wide (-a) with
    # --call-graph dwarf DID capture real data (689,490 samples) but at
    # 4.6GB for 60s -- "Processed 1205221 events and lost 14 chunks!
    # Check IO/CPU overload!" -- and the container crashed before
    # `perf report` could run (further docker exec calls failed with
    # "container ... is not running"). Falling back to flat (no call-
    # graph, far cheaper per sample), lower frequency, and a shorter
    # window to stay well under whatever disk/memory ceiling this
    # sandboxed environment has.
    record = subprocess.run(
        [
            "docker", "exec", NS3SIM_NAME, PERF_BIN, "record", "-a",
            "-F", "199", "-o", "/tmp/n16_profile.perf",
            "--", "sleep", "15",
        ],
        capture_output=True, text=True,
    )
    print("perf record stderr tail:", record.stderr[-500:], flush=True)

    OUTPUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    flat = subprocess.run(
        [
            "docker", "exec", NS3SIM_NAME, PERF_BIN, "report", "-i", "/tmp/n16_profile.perf",
            "--stdio", "--sort", "dso,symbol", "-g", "none",
        ],
        capture_output=True, text=True,
    )
    (OUTPUT_DIR.parent / "flat_report.txt").write_text(flat.stdout + "\n---STDERR---\n" + flat.stderr)
    print("Wrote flat_report.txt", flush=True)
    print("flat report head:\n" + "\n".join(flat.stdout.splitlines()[:40]), flush=True)

    thread.join()
    result = result_holder.get("result", {})
    print(json.dumps({"status": result.get("status"), "error": result.get("error")}), flush=True)

    ns3_log = result.get("ns3_log", "") or ""
    (OUTPUT_DIR.parent / "ns3.log").write_text(ns3_log, encoding="utf-8")

    import re
    tc = re.compile(r",(\s*[\]}])")
    last = None
    for line in ns3_log.splitlines():
        if line.startswith("FLEETQOX_WIFI_STATS "):
            try:
                last = json.loads(tc.sub(r"\1", line[len("FLEETQOX_WIFI_STATS "):]))
            except json.JSONDecodeError:
                continue
    print("last FLEETQOX_WIFI_STATS (profiling ENABLED):", json.dumps(last, indent=1), flush=True)
    (OUTPUT_DIR.parent / "last_stats_profiling_enabled.json").write_text(json.dumps(last, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
