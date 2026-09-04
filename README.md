# FleetRMW / FleetQoX

FleetRMW is a research-oriented ROS 2 middleware for large robot fleets. It
keeps the ROS 2 programming model but does not depend on DDS for its native data
plane. FleetQoX is the fleet control plane around it: task-aware QoS/QoE/QoT,
admission, path selection, reliability, and repair under a shared network
budget.

> Project status: advanced research prototype, **not production-ready**. The
> machine-readable capability manifest deliberately sets
> `production_ready=false`.

## Why this project exists

Conventional ROS 2 middleware primarily reasons about endpoints and topic QoS.
A large fleet has a different optimization problem: hundreds or thousands of
flows compete for changing Wi-Fi, WAN, roaming, and edge capacity while their
importance depends on robot state and task risk.

FleetRMW/FleetQoX asks a stricter question:

> Which information should be admitted, delivered, replicated, repaired,
> degraded, or rejected so that the fleet completes its tasks safely under the
> network budget that actually exists?

The intended contribution is not another transport wrapper. It is a ROS
2-native middleware and control plane that makes fleet-level communication
decisions visible, bounded, testable, and reproducible.

## Project goals

The project is complete as a research system when all of the following hold:

1. **ROS 2-native non-DDS RMW** — real lifecycle, graph, wait, pub/sub,
   service, action, QoS-event, dynamic-message, allocation, and content-filter
   behavior on FleetRMW transports.
2. **Fleet reliability** — bounded histories, ACK/NACK, selective fragment
   repair, fair admission, observable exhaustion, and convergent behavior at
   8/16/32+ robot scale.
3. **Fleet QoX control** — task/flow contracts, QoS/QoE/QoT objectives,
   predictive admission, per-robot budgets, path planning, repair allocation,
   and outcome feedback.
4. **Real transport and security** — UDP and shared memory where appropriate;
   real full-duplex QUIC with session reuse, mTLS identity, revocation and
   rotation behavior, failover, and explicit security boundaries.
5. **Real autonomy integration** — representative Nav2 planner/controller and
   Open-RMF workloads, not only synthetic topic traffic.
6. **Fair evidence** — Docker/tc-netem, ns-3, OMNeT++/INET, stress/soak, and a
   common-middle comparison against Fast DDS, Cyclone DDS, and Zenoh.
7. **Reproducible release** — one benchmark schema/report, multi-seed
   statistics, clean CI/build/install instructions, and no unsupported
   production claim.

## System architecture

```text
ROS 2 applications / Nav2 / Open-RMF
                 │
                 ▼
        rmw_fleetqox_cpp ABI
    lifecycle · graph · wait · QoS
   pub/sub · services · actions · events
                 │
        FleetRMW sample contract
 identity · sequence · time · fidelity
                 │
      FleetQoX fleet control plane
 admission · scheduling · path · repair
 robot budget · QoE/QoT · outcomes
                 │
     ┌───────────┼────────────┐
     ▼           ▼            ▼
 UDP/repair  shared memory  QUIC/mTLS
     │           │            │
     └───────────┼────────────┘
                 ▼
 Docker/netem · ns-3 · OMNeT++ · testbeds
```

The principal implementation areas are:

| Area | Location | Purpose |
|---|---|---|
| ROS 2 RMW | `ros2_ws/src/rmw_fleetqox_cpp/` | C++ RMW ABI, transports, graph, reliability, QoS, services/actions |
| ROS interfaces | `ros2_ws/src/fleetrmw_interfaces/` | Fleet task, quality, and bounded service/action interfaces |
| FleetQoX control | `fleetqox/` | Admission, optimization, repair, path control, outcomes, QUIC state |
| Reproducibility | `scripts/` | Docker/netem, baselines, integration, simulation, stress, report runners |
| Verification | `tests/` | Unit, contract, runner, schema, and source-boundary tests |
| Simulation/testbeds | `external/` | Docker images, ns-3, OMNeT++/INET, ngtcp2, ROS 2 baselines |
| Specifications | `docs/` | Architecture, protocol, evidence, security, roadmap, development |

See [Architecture](docs/ARCHITECTURE.md) for the detailed component and data
flow model.

## Current progress

The canonical status is
[`capabilities.json`](ros2_ws/src/rmw_fleetqox_cpp/capabilities.json). At this
checkpoint it reports:

| Measure | Current value |
|---|---:|
| Supported capabilities | 510 |
| Partial capability groups | 22 |
| Explicitly unsupported items | 14 |
| Passing scoped claim boundaries | 607 / 653 |
| Clean-source test suite | 690 discovered: 637 pass, 53 external-artifact skips |
| Production-ready | **No** |

### Implemented and evidenced

- A real `rmw_fleetqox_cpp` package with lifecycle, graph, guard/wait,
  serialized and typed pub/sub, services, actions, introspection C/C++, dynamic
  messages, content filters, QoS events, loan lifecycle, allocation scratch,
  `take_sequence`, and scoped `wait_for_all_acked` behavior.
- A native FleetRMW data frame and sample contract carrying source identity,
  sequence, timestamps, QoX metadata, admission provenance, and fidelity.
- UDP reliability with bounded writer history, ACK/NACK, whole-sample fallback,
  MTU-aware fragmentation, authenticated completion markers, selective missing
  index repair, receiver/reader isolation, bounded admission, and terminal loss
  signaling.
- Fleet-aware fragment scheduling: initial-frame round robin, NACK sweep
  budgets, progress-aware multi-round repair, duplicate/no-progress handling,
  and per-frame/reader repair round robin. The deterministic contention gate
  reaches eight active repair scopes and limits service to one fragment per
  scope while contended.
- Shared-memory local transport plus explicit UDP fallback and hybrid
  local/remote delivery.
- Real QUIC paths including in-process full-duplex/session reuse, public
  ngtcp2-based mTLS gateway work, admission, application outcomes, durable
  gateway state, and failover probes. Production certificate lifecycle and
  cluster semantics remain incomplete.
- FleetQoX admission, causal-semantic scheduling, Lagrangian adaptation,
  per-robot virtual budgets, telemetry-driven path/repair plans, local control
  leases, and task-outcome feedback.
- Dockerized Nav2/RMF-related action and navigation workloads, dynamic-obstacle
  recovery slices, router actuation, and fleet admission windows up to 4096
  tasks. These are integration evidence, not yet a full production autonomy
  workload.
- Docker/tc-netem comparison runners for FleetRMW, Fast DDS, Cyclone DDS, and
  Zenoh using a common serialized middle; profile/scale/payload/offered-load
  matrices; ns-3 and OMNeT++/INET trace-driven parity; unified report tooling;
  and a repeated stress/security campaign.
- Callback-owner teardown quiescence passed 20 fresh processes with 160
  publisher and 160 subscription cases in the latest local stress audit.
- Durable-state QUIC gateway failover no longer races the backing Postgres
  container's own initialization: the probe now waits for Postgres's own
  init-complete log marker instead of `pg_isready` (which can report a
  connectable server before the target database exists), fixing a failover
  probe that previously succeeded only 1 run in 5.
- The relay router scales `--timeout-ms` with flow count instead of a flat
  20s budget, so higher-flow-count scenarios no longer truncate silently
  before every publisher has finished.
- Loss-resilient UDP fragments now carry a routing hint so a relay router
  forwards each fragment to the correct topic's subscribers instead of
  broadcasting every fragment to all known peers; the broadcast fallback
  previously multiplied traffic by the fleet's fan-out size when fragments
  were relayed through a router rather than sent peer-to-peer.
- `DataFrame`/`ServiceFrame`/`ActionFrame` payloads are wire-encoded as
  base64 instead of hex, cutting the encoded-byte overhead of large
  payloads from 2x to ~4/3x; this measurably improves delivery under
  bandwidth-constrained profiles without changing the wire schema's
  structure.

### Current measured frontier

The best retained 16-robot, 32-KiB, roaming-loss, seed-7 row delivers
`155/160`. New fair repair scheduling substantially reduces repair pressure,
but its matched row remains `152/160`; therefore
`fleet_scale_selective_fragment_repair_claim=false` and
`production_large_sample_reliability_claim=false` remain correct.

An intermittent subscriber heap failure (`free(): invalid next size`) has been
observed twice during long lossy 32-KiB runs. Short selective-repair reruns and
the callback teardown campaign pass. A fresh Jazzy ASan/UBSan Docker build also
completed 5,000/5,000 same-process typed 32-KiB publish/take iterations without
a sanitizer report. That narrows the search but does not reproduce the lossy
inter-process path, so memory-safety qualification remains a release blocker.

A focused investigation into large-payload delivery under severe bandwidth
constraints (16 robots, ~30 KiB state payload, 5 Mbit/s roaming profile)
found the dominant failure mode was IP-level fragmentation loss for
oversized UDP datagrams, not a router or scheduler defect. The base64
encoding change and the router's fragment-routing fix above are real,
committed improvements to that path (state delivery under the same stress
scenario went from 0/16 to partial success). Nine further independent
tuning and architectural attempts (chunk sizing, publisher process
topology, advertisement pacing/jitter) were tried and evaluated; none
produced a reliable additional improvement, and this specific combination
of scale, payload size, and bandwidth remains an open reliability gap
consistent with the P0 item below rather than a newly discovered defect.

Detailed evidence and caveats are in
[Experimental Results](docs/EXPERIMENTAL_RESULTS_V1.md).

## What remains before completion

### P0 — release blockers

- Reproduce and eliminate the intermittent typed large-string/subscriber heap
  corruption under ASan/UBSan; add it as a repeated Docker gate.
- Reach complete reliable 32-KiB delivery and ACK convergence at 16/32 robots
  across multiple seeds without unbounded queues, repair amplification, or
  hidden retries.
- Turn the current reliability frontier into a fixed, versioned acceptance
  matrix with CPU, RSS, queue high-water, retransmission, and completion-time
  bounds.

### P1 — transport and security hardening

- Remove remaining private aioquic-path dependencies or replace them with a
  maintained public API/backend.
- Complete online server/client certificate and CA rotation, active-session
  revocation, forward secrecy/asymmetric key exchange, and multi-attacker soak.
- Qualify QUIC gateway election/consensus, split-brain fencing, rejoin,
  failback, regional recovery, and operational observability.
- Document a production threat model and make every security claim traceable
  to a fail-closed test.

### P2 — RMW semantic depth

- Close the remaining full QoS-event, remote graph-event, message-lost,
  liveliness, DDS content-filter dialect, and DDS-equivalent
  `wait_for_all_acked` gaps.
- Extend allocation from reusable payload scratch to deep hot-path
  preallocation; zero-copy remains explicitly unclaimed.
- Complete production-grade dynamic-message and lifecycle concurrency coverage.

### P3 — autonomy and fleet workloads

- Run full Nav2 planner/controller/costmap behavior with sustained dynamic
  obstacles and recovery policy, not only bounded probes.
- Run representative Open-RMF traffic, bidding, dispatch, state, and failure
  workloads across multiple hosts.
- Add hardware-in-the-loop and physical multi-robot evidence.

### P4 — paper/release evidence

- Finish high-fidelity wireless and TSN/mesh simulation parity.
- Repeat all common-middle baselines over enough seeds for confidence intervals
  and effect sizes; do not claim latency or superiority from incomplete rows.
- Publish one canonical benchmark bundle/report and automate it in CI.
- Complete install, upgrade, rollback, PKI operations, and release packaging.

The detailed acceptance gates and ordered work plan live in
[Status and Roadmap](docs/STATUS_AND_ROADMAP.md).

## Estimated time to completion

These are engineering estimates, not guarantees:

| Target | Estimate | Meaning |
|---|---:|---|
| Research-complete release candidate | **2–4 focused weeks** | P0 reliability/memory gates, consolidated benchmark report, reproducible docs/CI |
| Production-oriented qualification | **6–12 additional weeks minimum** | PKI/QUIC hardening, multi-host/HIL, long soak, cluster failure semantics, operations |

The production estimate assumes access to stable multi-host infrastructure,
real network/testbed time, and PKI/operations review. Hardware availability or
a persistent memory/reliability defect can extend it.

## Quick start

### Local tests

```bash
python3 -m unittest discover -s tests
```

### Build the ROS 2 package in a Jazzy environment

```bash
source /opt/ros/jazzy/setup.bash
colcon build \
  --base-paths ros2_ws/src \
  --packages-select fleetrmw_interfaces rmw_fleetqox_cpp
```

### Build the reproducible Docker image

```bash
docker build \
  -t localhost/fleetrmw/rmw-netem:jazzy \
  -f external/rmw-netem/Dockerfile .
```

### Run representative gates

```bash
# Typed/serialized RMW and contract suite
python3 -m unittest discover -s tests

# Selective fragment repair without whole-sample retry
python3 scripts/run_rmw_docker_selective_fragment_repair_probe.py

# Contended per-frame/reader repair fairness
python3 scripts/run_rmw_docker_fragment_repair_round_robin_probe.py

# Common-middle RMW comparison
python3 scripts/run_same_hop_rmw_comparison.py --help

# Unified evidence/capability report
python3 scripts/generate_unified_benchmark_report.py --help
```

Docker/tc-netem requires Linux network capabilities inside the containers.
Generated `results_*`, `traces_*`, build, install, and log directories are
ignored and should not be committed.

In a clean checkout, tests that validate a previously generated Docker evidence
bundle are reported as skips until that bundle is regenerated. Source,
algorithm, contract, schema, and runner tests continue to execute normally.

## Documentation

- [Documentation index](docs/README.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Status and roadmap](docs/STATUS_AND_ROADMAP.md)
- [RMW boundary](docs/RMW_MINIMAL_BOUNDARY_V1.md)
- [Data-frame protocol](docs/FLEETRMW_DATA_FRAME_V1.md)
- [Sample contract](docs/RMW_SAMPLE_CONTRACT_V1.md)
- [Experimental methodology](docs/EXPERIMENTAL_METHODOLOGY.md)
- [Experimental results](docs/EXPERIMENTAL_RESULTS_V1.md)
- [Security](docs/SECURITY.md)
- [Integration and validation](docs/INTEGRATION_AND_VALIDATION.md)
- [Development and reproducibility](docs/DEVELOPMENT.md)

## Claim discipline

FleetRMW is intentionally conservative about evidence:

- a deterministic probe proves only its scoped contract;
- one seed is a frontier observation, not a fleet reliability claim;
- same-hop/common-middle parity is not universal superiority;
- a Docker security control is not production hardening;
- simulator parity is not physical wireless equivalence;
- `production_ready=false` remains authoritative until every release gate is
  repeatedly satisfied.

## Core thesis

ROS 2 middleware for robot fleets should not merely deliver topics. It should
prioritize and repair information according to the amount of task risk it
reduces under real, shared network constraints.
