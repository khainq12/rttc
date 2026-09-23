"""Preflight guard against the exact staleness bug documented in
docs/AUDIT_ACCEPTANCE_TRACKING.md, "WIFI GATEWAY BENCHMARK -- N=2 ROOT
CAUSE FULLY PROVEN AND FIXED": the Docker image tag every Wi-Fi/LAN/5G
probe script references (`DEFAULT_IMAGE` in
run_ns3_docker_container_fleet_probe.py) can silently fall behind its
own Dockerfile -- the image running in this repo's history for roughly
three weeks (built 2026-09-02) predated the commit (2026-09-11,
`30bc4160`) that actually put the TapBridge address-autolearn fix into
that Dockerfile, so every Wi-Fi Table V number measured in that window
used a plain, unpatched ns-3 3.41 (from Ubuntu's apt archive) with the
exact bug this fix addresses -- 100% silent, no error, just uniformly
degraded/zero Wi-Fi unicast delivery that read as "genuine network
loss" until traced to source.

This check is cheap (a few `docker inspect`/`git log`/`dpkg` calls, no
container network setup) and is meant to run BEFORE any real probe:
call `assert_image_fresh()` (raises `StaleImageError` on failure) or
run this file directly for a human-readable report.

Deliberately does NOT try to detect the fix by scanning binaries for
patch-specific strings -- the patch only touches a comment and a
removed function call, neither of which survives into compiled output
in an inspectable way (confirmed: an optimized build strips even
NS_LOG component name strings entirely, see the same doc section).
Detects the ACTUAL condition that broke silently instead: the image is
newer than the Dockerfile's last commit that touches the exact lines
building ns-3, AND the apt `ns3` package is genuinely absent (proving
the from-source build -- not the apt package -- is what is actually
installed).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
DOCKERFILE = "external/rmw-netem/Dockerfile"


class StaleImageError(RuntimeError):
    """Raised by assert_image_fresh() when the running Docker image
    predates the Dockerfile commit that builds ns-3 from source with
    the TapBridge fix -- i.e. exactly the silent-staleness condition
    that produced three weeks of invalid Wi-Fi Table V numbers."""


def _run(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError as exc:
        # e.g. no `docker`/`git`/`date` binary on PATH at all -- treat the
        # same as "command failed", not an unhandled crash, so callers'
        # existing `returncode != 0` checks degrade this gracefully.
        return subprocess.CompletedProcess(args, returncode=127, stdout="", stderr=str(exc))


def _image_created_epoch(image: str) -> int | None:
    result = _run("docker", "inspect", image, "--format", "{{.Created}}")
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # Docker's own RFC3339-ish timestamp; let `date` parse it rather than
    # hand-rolling a parser for its fractional-seconds/timezone format.
    parsed = _run("date", "-d", result.stdout.strip(), "+%s")
    if parsed.returncode != 0:
        return None
    return int(parsed.stdout.strip())


def _dockerfile_last_commit_epoch() -> int | None:
    result = _run(
        "git", "-C", str(ROOT), "log", "-1", "--format=%ct", "--", DOCKERFILE,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return int(result.stdout.strip())


def _apt_ns3_package_present(image: str) -> bool | None:
    """True if the apt `ns3` package is still installed inside the image
    (the pre-fix, unpatched state); False if genuinely absent (the
    from-source build the current Dockerfile installs replaces it
    entirely -- confirmed via this exact check post-rebuild); None if
    the check itself could not run (e.g. no local Docker daemon)."""
    result = _run(
        "docker", "run", "--rm", "--entrypoint", "bash", image, "-c",
        "dpkg -l ns3 2>/dev/null | grep -q '^ii' && echo PRESENT || echo ABSENT",
    )
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    if output.endswith("PRESENT"):
        return True
    if output.endswith("ABSENT"):
        return False
    return None


def check_image_freshness(image: str = DEFAULT_IMAGE) -> dict:
    """Returns a diagnostic dict; never raises. See assert_image_fresh()
    for the raising wrapper meant to gate real probe runs."""
    image_created = _image_created_epoch(image)
    dockerfile_commit = _dockerfile_last_commit_epoch()
    apt_present = _apt_ns3_package_present(image)

    stale_by_timestamp = (
        image_created is not None
        and dockerfile_commit is not None
        and image_created < dockerfile_commit
    )
    fresh = (
        image_created is not None
        and dockerfile_commit is not None
        and not stale_by_timestamp
        and apt_present is False
    )
    return {
        "image": image,
        "image_created_epoch": image_created,
        "dockerfile_last_commit_epoch": dockerfile_commit,
        "stale_by_timestamp": stale_by_timestamp,
        "apt_ns3_package_present": apt_present,
        "fresh": fresh,
    }


def assert_image_fresh(image: str = DEFAULT_IMAGE) -> None:
    """Call this at the start of any real Wi-Fi/LAN/5G probe run (or a
    batch of them). Raises StaleImageError with a clear diagnostic if
    the image predates the Dockerfile's own from-source ns-3 build, or
    if the apt ns3 package is still present (the exact pre-fix state)."""
    diag = check_image_freshness(image)
    if diag["image_created_epoch"] is None:
        raise StaleImageError(
            f"could not inspect image {image!r} -- is it built? "
            f"(docker inspect returned no creation timestamp)"
        )
    if diag["dockerfile_last_commit_epoch"] is None:
        raise StaleImageError(
            f"could not read git history for {DOCKERFILE} -- is this running "
            f"inside the RTC git checkout?"
        )
    if diag["stale_by_timestamp"]:
        raise StaleImageError(
            f"STALE IMAGE: {image} was built at epoch {diag['image_created_epoch']}, "
            f"but {DOCKERFILE} has a commit at epoch {diag['dockerfile_last_commit_epoch']} "
            f"(newer) -- the running image does not reflect the current Dockerfile. "
            f"Rebuild with: docker build -f {DOCKERFILE} -t {image} {ROOT}"
        )
    if diag["apt_ns3_package_present"] is True:
        raise StaleImageError(
            f"STALE IMAGE: {image} still has the apt `ns3` package installed -- "
            f"this is the exact pre-fix state (plain, unpatched ns-3.41 via apt) "
            f"documented in AUDIT_ACCEPTANCE_TRACKING.md as producing invalid Wi-Fi "
            f"Table V numbers for ~3 weeks. Rebuild with: "
            f"docker build -f {DOCKERFILE} -t {image} {ROOT}"
        )
    if diag["apt_ns3_package_present"] is None:
        raise StaleImageError(
            f"could not verify apt package state inside {image} -- "
            f"is the local Docker daemon reachable?"
        )


def main() -> int:
    diag = check_image_freshness()
    for key, value in diag.items():
        print(f"{key}: {value}")
    if diag["fresh"]:
        print("VERDICT: FRESH -- image reflects the current Dockerfile, apt ns3 package absent.")
        return 0
    print("VERDICT: STALE OR UNVERIFIABLE -- see fields above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
