"""Raft-backed writer-lease gate for the FleetQoX QUIC gateway.

Every other writer-lease mechanism in this codebase asks a SEPARATE
coordination service for permission to write: `EtcdQuorumLease`
(postgres_failover_dcs.py) asks etcd; `GatewayDurableStore.acquire_writer_lease`
(quic_gateway_state.py) asks a row in the shared SQLite/PostgreSQL store
itself, gated purely by TTL expiry with no external failure detector. This
does something different: it ties write eligibility directly to this
gateway's OWN co-located fleetqox.raft node's leadership result. There is
no separate "acquire a lease" step because Raft's leader election already
IS the lease -- exactly one node can be the elected leader for a given
term, and that term is a monotonically increasing integer for the whole
cluster's lifetime, so it doubles as a fencing token for free.

This module does not replace GatewayDurableStore's SQL-based fencing --
it feeds it a *different, Raft-derived* holder_id (see
run_service()'s use of `raft-{node_id}-term-{term}`), so a term change
still produces a fresh fence_token there exactly the way a new
--writer-lease-instance-id string would, and a stale writer whose Raft
term has since moved on is still rejected by that same, already-proven
mechanism. Raft supplies the leadership *decision*; the SQL store still
enforces it at write time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from typing import Any
from urllib import error, request


class RaftLeaseError(RuntimeError):
    """Raised when this node cannot establish (or has lost) Raft leadership."""


class RaftLeaderLease:
    def __init__(self, *, status_url: str, timeout_s: float = 2.0) -> None:
        if not status_url:
            raise ValueError("Raft status URL must be configured")
        if timeout_s <= 0.0:
            raise ValueError("Raft status poll timeout must be positive")
        self.status_url = status_url.rstrip("/") + "/status"
        self.timeout_s = timeout_s

    def current_status(self) -> dict[str, Any] | None:
        try:
            with request.urlopen(self.status_url, timeout=self.timeout_s) as response:
                document = json.loads(response.read().decode())
        except (error.HTTPError, error.URLError, TimeoutError, OSError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    def is_still_leader(self, *, expected_term: int) -> bool:
        status = self.current_status()
        return (
            status is not None
            and status.get("role") == "leader"
            and status.get("term") == expected_term
        )

    async def current_status_async(self) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.current_status)

    async def is_still_leader_async(self, *, expected_term: int) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.is_still_leader(expected_term=expected_term)
        )

    async def require_leadership(
        self, *, wait_timeout_ms: int, retry_ms: int,
        on_wait: Callable[[], None] | None = None,
    ) -> int:
        """Block (without freezing the event loop) until this node leads.

        Returns the current Raft term once this node is confirmed leader --
        callers should use that term as the fencing input to whatever they
        actually durably write with (see module docstring). Raises
        RaftLeaseError if the wait budget elapses first; wait_timeout_ms=0
        means "check once, fail immediately if not already leader," matching
        the sibling acquire_gateway_state_with_lease_wait's convention.
        """

        if retry_ms <= 0:
            raise ValueError("Raft lease retry interval must be positive")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_timeout_ms / 1000.0
        reported_wait = False
        while True:
            status = await self.current_status_async()
            if status is not None and status.get("role") == "leader":
                term = status.get("term")
                if isinstance(term, int):
                    return term
            remaining = deadline - loop.time()
            if wait_timeout_ms <= 0 or remaining <= 0.0:
                raise RaftLeaseError(
                    "timed out waiting to become the Raft-elected leader"
                )
            if not reported_wait:
                if on_wait is not None:
                    on_wait()
                reported_wait = True
            await asyncio.sleep(min(retry_ms / 1000.0, remaining))
