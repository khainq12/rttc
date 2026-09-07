#!/usr/bin/env python3
"""A real networked node running the fleetqox.raft consensus core.

fleetqox/raft.py is the transport-free algorithm; this is the thin driver
that gives it real sockets and real timers, turning it into an actual
distributed key-value store: writes are only accepted by whichever node the
algorithm has elected leader, are only acknowledged once a majority of
nodes have durably replicated them (Raft's commit rule, not just "the
leader wrote it locally"), and every node applies committed entries to its
own copy of the state machine in the same order. Reads are served from each
node's own local state machine (eventually consistent, not linearizable --
a deliberate scope limit, not an oversight; see the probe/docs for what is
and isn't proven here).

RPC wire format is plain JSON over HTTP, no TLS -- the property under test
is the consensus algorithm's correctness over real separate processes, not
re-proving transport security already covered by the etcd/PostgreSQL
paths. The same mTLS pattern used there (see fleetqox_postgres_fence_agent.py)
would layer on directly if this were promoted beyond a proof of concept.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import error, parse, request

from fleetqox.raft import (
    AppendEntriesRequest,
    AppendEntriesResponse,
    LogEntry,
    Outbox,
    RaftNode,
    RequestVoteRequest,
    RequestVoteResponse,
)


SCHEMA_VERSION = "fleetrmw.raft_node_service.v1"


def post_json(url: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any] | None:
    body = json.dumps(payload).encode()
    call = request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with request.urlopen(call, timeout=timeout_s) as response:
            document = json.loads(response.read().decode())
    except (
        error.HTTPError, error.URLError, TimeoutError,
        json.JSONDecodeError, ConnectionError, OSError,
    ):
        return None
    return document if isinstance(document, dict) else None


class RaftService:
    """Owns the sockets/timers around one fleetqox.raft.RaftNode."""

    def __init__(
        self,
        node: RaftNode,
        peer_urls: dict[str, str],
        *,
        election_timeout_range_ms: tuple[float, float],
        heartbeat_interval_ms: float,
        request_timeout_s: float,
    ) -> None:
        self.node = node
        self.peer_urls = peer_urls
        self.election_timeout_range_ms = election_timeout_range_ms
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.request_timeout_s = request_timeout_s
        self.lock = threading.RLock()
        self.state_machine: dict[str, str] = {}
        self._stop = threading.Event()
        self._next_election_deadline = self._new_election_deadline()
        self._verbose = os.environ.get("FLEETQOX_RAFT_VERBOSE") == "1"

    def _log(self, event: str, **fields: Any) -> None:
        if not self._verbose:
            return
        print(
            json.dumps(
                {"ts": time.time(), "node_id": self.node.node_id, "event": event, **fields},
                sort_keys=True,
            ),
            file=sys.stderr, flush=True,
        )

    def _new_election_deadline(self) -> float:
        timeout_ms = random.uniform(*self.election_timeout_range_ms)
        return time.monotonic() + timeout_ms / 1000.0

    def start(self) -> None:
        threading.Thread(target=self._election_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _election_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.02)
            outbox: Outbox | None = None
            with self.lock:
                if self.node.is_leader():
                    self._next_election_deadline = self._new_election_deadline()
                elif time.monotonic() >= self._next_election_deadline:
                    outbox = self.node.on_election_timeout()
                    self._next_election_deadline = self._new_election_deadline()
                    self._log("election_timeout", new_term=self.node.current_term)
            if outbox is not None:
                self._send_outbox(outbox)

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.heartbeat_interval_ms / 1000.0)
            with self.lock:
                if not self.node.is_leader():
                    continue
                outbox = self.node.on_heartbeat_due()
            self._send_outbox(outbox)

    def _send_outbox(self, outbox: Outbox) -> None:
        for peer_id, req in outbox.request_votes:
            threading.Thread(
                target=self._send_request_vote, args=(peer_id, req), daemon=True,
            ).start()
        for peer_id, req in outbox.append_entries:
            threading.Thread(
                target=self._send_append_entries, args=(peer_id, req), daemon=True,
            ).start()

    def _send_request_vote(self, peer_id: str, req: RequestVoteRequest) -> None:
        url = self.peer_urls[peer_id] + "/raft/request_vote"
        document = post_json(
            url,
            {
                "term": req.term, "candidate_id": req.candidate_id,
                "last_log_index": req.last_log_index, "last_log_term": req.last_log_term,
            },
            self.request_timeout_s,
        )
        if document is None:
            self._log("request_vote_send_failed", peer_id=peer_id, term=req.term)
            return
        response = RequestVoteResponse(
            term=document["term"], vote_granted=document["vote_granted"],
            voter_id=document["voter_id"],
        )
        self._log(
            "request_vote_response", peer_id=peer_id, term=req.term,
            granted=response.vote_granted, response_term=response.term,
        )
        with self.lock:
            was_candidate = self.node.role.value == "candidate"
            outbox = self.node.handle_request_vote_response(response)
            became_leader = self.node.is_leader()
        if was_candidate and became_leader:
            self._log("became_leader", term=self.node.current_term)
        self._send_outbox(outbox)

    def _send_append_entries(self, peer_id: str, req: AppendEntriesRequest) -> None:
        url = self.peer_urls[peer_id] + "/raft/append_entries"
        document = post_json(
            url,
            {
                "term": req.term, "leader_id": req.leader_id,
                "prev_log_index": req.prev_log_index, "prev_log_term": req.prev_log_term,
                "entries": [
                    {"term": entry.term, "command": entry.command} for entry in req.entries
                ],
                "leader_commit": req.leader_commit,
            },
            self.request_timeout_s,
        )
        if document is None:
            return
        response = AppendEntriesResponse(
            term=document["term"], success=document["success"],
            follower_id=document["follower_id"],
            match_length_hint=document["match_length_hint"],
        )
        with self.lock:
            outbox = self.node.handle_append_entries_response(peer_id, response)
            self._apply_committed_locked()
        self._send_outbox(outbox)

    def handle_request_vote_rpc(self, document: dict[str, Any]) -> dict[str, Any]:
        req = RequestVoteRequest(
            term=document["term"], candidate_id=document["candidate_id"],
            last_log_index=document["last_log_index"],
            last_log_term=document["last_log_term"],
        )
        with self.lock:
            response = self.node.handle_request_vote(req)
            if response.vote_granted:
                self._next_election_deadline = self._new_election_deadline()
        self._log(
            "request_vote_received", candidate_id=req.candidate_id, term=req.term,
            granted=response.vote_granted,
        )
        return {
            "term": response.term, "vote_granted": response.vote_granted,
            "voter_id": response.voter_id,
        }

    def handle_append_entries_rpc(self, document: dict[str, Any]) -> dict[str, Any]:
        entries = tuple(
            LogEntry(term=entry["term"], command=entry["command"])
            for entry in document.get("entries", [])
        )
        req = AppendEntriesRequest(
            term=document["term"], leader_id=document["leader_id"],
            prev_log_index=document["prev_log_index"],
            prev_log_term=document["prev_log_term"],
            entries=entries, leader_commit=document["leader_commit"],
        )
        with self.lock:
            response = self.node.handle_append_entries(req)
            if response.term >= self.node.current_term:
                # A message from a current-or-newer-term leader means this
                # term is legitimately occupied -- don't contest it.
                self._next_election_deadline = self._new_election_deadline()
            self._apply_committed_locked()
        return {
            "term": response.term, "success": response.success,
            "follower_id": response.follower_id,
            "match_length_hint": response.match_length_hint,
        }

    def _apply_committed_locked(self) -> None:
        for command in self.node.take_newly_committed():
            if isinstance(command, dict) and command.get("kind") == "put":
                self.state_machine[command["key"]] = command["value"]

    def propose_put(self, key: str, value: str) -> int | None:
        outbox: Outbox | None = None
        with self.lock:
            index = self.node.propose({"kind": "put", "key": key, "value": value})
            if index is not None:
                outbox = self.node.on_heartbeat_due()
        if outbox is not None:
            self._send_outbox(outbox)
        return index

    def wait_for_commit(self, index: int, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self.lock:
                if self.node.last_applied >= index:
                    return True
                if not self.node.is_leader():
                    return False
            time.sleep(0.02)
        return False

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "schema_version": SCHEMA_VERSION,
                "node_id": self.node.node_id,
                "role": self.node.role.value,
                "term": self.node.current_term,
                "leader_id": self.node.leader_id,
                "log_length": len(self.node.log),
                "commit_index": self.node.commit_index,
                "last_applied": self.node.last_applied,
                "state_machine_size": len(self.state_machine),
            }


class RaftServer(ThreadingHTTPServer):
    service: RaftService


class RaftHandler(BaseHTTPRequestHandler):
    server: RaftServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _reply(self, status: int, document: dict[str, Any]) -> None:
        body = json.dumps(document, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("content-length", "0"))
            document = json.loads(self.rfile.read(length).decode())
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return document if isinstance(document, dict) else None

    def do_POST(self) -> None:
        service = self.server.service
        if self.path == "/raft/request_vote":
            document = self._read_json()
            if document is None:
                self._reply(400, {"status": "invalid_request"})
                return
            self._reply(200, service.handle_request_vote_rpc(document))
            return
        if self.path == "/raft/append_entries":
            document = self._read_json()
            if document is None:
                self._reply(400, {"status": "invalid_request"})
                return
            self._reply(200, service.handle_append_entries_rpc(document))
            return
        if self.path == "/kv":
            document = self._read_json()
            key = document.get("key") if document else None
            value = document.get("value") if document else None
            if not isinstance(key, str) or not key or not isinstance(value, str):
                self._reply(400, {"status": "invalid_request"})
                return
            with service.lock:
                leader_id = service.node.leader_id
                is_leader = service.node.is_leader()
            if not is_leader:
                self._reply(
                    409,
                    {"status": "not_leader", "leader_id": leader_id},
                )
                return
            index = service.propose_put(key, value)
            if index is None:
                self._reply(409, {"status": "not_leader", "leader_id": leader_id})
                return
            committed = service.wait_for_commit(index, timeout_s=5.0)
            self._reply(
                200 if committed else 503,
                {
                    "status": "committed" if committed else "commit_timed_out",
                    "index": index,
                },
            )
            return
        self._reply(404, {"status": "not_found"})

    def do_GET(self) -> None:
        service = self.server.service
        if self.path == "/status":
            self._reply(200, service.status())
            return
        if self.path.startswith("/kv"):
            query = parse.parse_qs(parse.urlsplit(self.path).query)
            keys = query.get("key", [])
            if not keys:
                self._reply(400, {"status": "missing_key"})
                return
            with service.lock:
                value = service.state_machine.get(keys[0])
            if value is None:
                self._reply(404, {"status": "not_found", "key": keys[0]})
                return
            self._reply(200, {"status": "ok", "key": keys[0], "value": value})
            return
        self._reply(404, {"status": "not_found"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument(
        "--peer", action="append", default=[],
        help="peer as node_id=http://host:port, repeatable",
    )
    # Wider than the Raft paper's canonical 150-300ms range: those numbers
    # assume a compiled implementation with negligible per-RPC overhead.
    # This is a Python ThreadingHTTPServer doing JSON parsing under a
    # single lock per node; under any real CPU contention (a busy Docker
    # host, or this project's own probes exec-polling /status in a tight
    # loop) too-tight timeouts cause a self-reinforcing livelock -- slow
    # RPC turnaround causes spurious re-elections, which multiplies RPC
    # volume, which causes more contention. The wider range gives enough
    # slack to actually converge under those conditions.
    parser.add_argument("--election-timeout-min-ms", type=float, default=500.0)
    parser.add_argument("--election-timeout-max-ms", type=float, default=1000.0)
    parser.add_argument("--heartbeat-interval-ms", type=float, default=100.0)
    parser.add_argument("--request-timeout-s", type=float, default=1.0)
    args = parser.parse_args()

    peer_urls: dict[str, str] = {}
    for entry in args.peer:
        peer_id, _, url = entry.partition("=")
        if not peer_id or not url:
            parser.error(f"--peer must be node_id=http://host:port, got {entry!r}")
        peer_urls[peer_id] = url.rstrip("/")

    node = RaftNode(args.node_id, tuple(peer_urls.keys()))
    service = RaftService(
        node,
        peer_urls,
        election_timeout_range_ms=(args.election_timeout_min_ms, args.election_timeout_max_ms),
        heartbeat_interval_ms=args.heartbeat_interval_ms,
        request_timeout_s=args.request_timeout_s,
    )
    server = RaftServer((args.host, args.port), RaftHandler)
    server.service = service
    service.start()
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "ready",
                "node_id": args.node_id,
                "peer_count": len(peer_urls),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
