"""Reproducible two-phase GCC PGO build pipeline for the accepted ns-3
configuration (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "N16 REALTIME: GCC
PGO"). Builds a profile-generate image, trains it on a real N16 seed=7
run, verifies non-trivial .gcda data exists for the hot Wi-Fi/simulator
objects, builds a profile-use image, verifies the build log shows clean
profile consumption for those same hot objects (fails loudly on any
missing-profile/mismatch warning for them), and tags the final image.

Usage:
    python3 scripts/build_pgo_ns3_image.py --final-tag localhost/fleetrmw/rmw-netem:jazzy-pgo

Fails loudly (non-zero exit, explicit error) rather than silently
producing a non-PGO or partially-covered image.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "external" / "rmw-netem" / "Dockerfile.ns3-pgo"
PROFILE_DIR = ROOT / "pgo_profile_data"

# Hot objects this PGO pass must demonstrably cover (see the fan-out
# source audit and event-growth accounting in
# docs/AUDIT_ACCEPTANCE_TRACKING.md for why these specific files are the
# ones that matter).
HOT_OBJECT_STEMS = [
    "realtime-simulator-impl.cc",
    "heap-scheduler.cc",
    "yans-wifi-channel.cc",
    "wifi-phy.cc",
    "phy-entity.cc",
    "interference-helper.cc",
    "error-rate-model.cc",
    "table-based-error-rate-model.cc",
]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"+ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=ROOT, **kwargs)


def docker_build(tag: str, pgo_mode: str, build_jobs: int, log_path: Path) -> None:
    cmd = [
        "docker", "build",
        "-f", str(DOCKERFILE),
        "-t", tag,
        "--build-arg", f"NS3_BUILD_JOBS={build_jobs}",
        "--build-arg", f"NS3_PGO_MODE={pgo_mode}",
        ".",
    ]
    with open(log_path, "w") as f:
        result = run(cmd, stdout=f, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        raise SystemExit(
            f"FATAL: docker build failed for NS3_PGO_MODE={pgo_mode} "
            f"(tag={tag}); see {log_path}"
        )


def verify_profile_data_exists() -> None:
    if not PROFILE_DIR.is_dir():
        raise SystemExit(
            f"FATAL: {PROFILE_DIR} does not exist -- the training run did not "
            "produce any profile data (or was never run). Refusing to proceed "
            "to a profile-use build that would silently fall back to "
            "non-PGO/stale behavior."
        )
    gcda_files = list(PROFILE_DIR.rglob("*.gcda"))
    if not gcda_files:
        raise SystemExit(f"FATAL: {PROFILE_DIR} exists but contains zero .gcda files.")
    missing = []
    for stem in HOT_OBJECT_STEMS:
        matches = [f for f in gcda_files if stem in f.name]
        if not matches:
            missing.append(stem)
            continue
        total_size = sum(f.stat().st_size for f in matches)
        if total_size < 256:
            missing.append(f"{stem} (found but suspiciously small: {total_size} bytes)")
    if missing:
        raise SystemExit(
            "FATAL: profile data missing or trivially small for hot object(s): "
            f"{missing}. Found {len(gcda_files)} .gcda files total under {PROFILE_DIR}, "
            "but not for all required hot objects. Refusing to proceed."
        )
    print(
        f"OK: {len(gcda_files)} .gcda files found, all {len(HOT_OBJECT_STEMS)} hot "
        "objects have non-trivial profile data.",
        flush=True,
    )


def verify_clean_profile_consumption(log_path: Path) -> None:
    text = log_path.read_text(errors="replace")
    bad_patterns = ["profile count data file not found", "profile mismatch", "coverage mismatch"]
    offending_lines = [
        line for line in text.splitlines()
        if any(p in line.lower() for p in bad_patterns)
    ]
    hot_object_offenses = [
        line for line in offending_lines
        if any(stem in line for stem in HOT_OBJECT_STEMS)
    ]
    if hot_object_offenses:
        raise SystemExit(
            "FATAL: profile-use build reported missing-profile/mismatch warnings "
            f"for hot object(s):\n" + "\n".join(hot_object_offenses)
        )
    print(
        f"OK: zero missing-profile/mismatch warnings for any of the "
        f"{len(HOT_OBJECT_STEMS)} hot objects "
        f"({len(offending_lines)} harmless warning(s) for unrelated files, if any).",
        flush=True,
    )


def run_training(image: str, build_jobs: int) -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.run_ns3_docker_container_fleet_probe import run_probe

    print(f"Training NS3_PGO_MODE=generate image ({image}) on N16 seed=7...", flush=True)
    result = run_probe(
        image=image,
        output_dir=ROOT / "results_rmw_socket" / "_pgo_training" / "n16_seed7",
        num_robots=16, policy="fifo", seconds=3, seed=7, sim_duration_s=20.0,
        rmw_implementation="rmw_fleetqox_cpp", ns3_scheduler="heap", ns3_use_monolib=True,
        ns3_build_profile="release",
    )
    # A profile-generate binary is expected to be much slower than normal
    # (instrumentation overhead) and may miss the harness's own
    # endpoint-completeness gate as a result -- that is NOT a failure of
    # the profile collection itself (libgcov flushes .gcda at normal
    # process exit, which still happens). Only the actual presence of
    # profile data (checked separately, below) determines success.
    print(f"Training run status={result.get('status')} sim_lag_s={result.get('sim_lag_s')} "
          "(a 'failed'/high-lag status here is expected for an instrumented build and is "
          "not itself a training failure -- see verify_profile_data_exists()).", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--final-tag", default="localhost/fleetrmw/rmw-netem:jazzy-pgo")
    parser.add_argument("--build-jobs", type=int, default=8)
    parser.add_argument("--clean", action="store_true",
                         help="Remove any existing pgo_profile_data/ and generate/use images "
                              "before starting, to prove reproducibility from a clean state.")
    args = parser.parse_args()

    generate_tag = "localhost/fleetrmw/rmw-netem:jazzy-pgo-generate"
    use_tag = "localhost/fleetrmw/rmw-netem:jazzy-pgo-use-build"
    gen_log = ROOT / "pgo_generate_build.log"
    use_log = ROOT / "pgo_use_build.log"

    if args.clean:
        print("--clean: removing prior profile data and images", flush=True)
        if PROFILE_DIR.exists():
            shutil.rmtree(PROFILE_DIR)
        for tag in (generate_tag, use_tag, args.final_tag):
            subprocess.run(["docker", "rmi", "-f", tag], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    print("=== PHASE 1: profile-generate build ===", flush=True)
    docker_build(generate_tag, "generate", args.build_jobs, gen_log)

    print("=== PHASE 1b: N16 seed=7 training run ===", flush=True)
    run_training(generate_tag, args.build_jobs)
    verify_profile_data_exists()

    print("=== PHASE 2: profile-use build ===", flush=True)
    docker_build(use_tag, "use", args.build_jobs, use_log)
    verify_clean_profile_consumption(use_log)

    print(f"=== PHASE 3: tagging final image {args.final_tag} ===", flush=True)
    run(["docker", "tag", use_tag, args.final_tag], check=True)

    digest = subprocess.run(
        ["docker", "image", "inspect", args.final_tag, "--format", "{{.Id}}"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.strip()
    print(f"DONE: {args.final_tag} ({digest})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
