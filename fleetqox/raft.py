"""A from-scratch Raft consensus core: leader election plus replicated log.

This is the transport- and clock-free "functional core" of Raft (Ongaro &
Ousterhout, "In Search of an Understandable Consensus Algorithm"): every
method is a pure function of (current state, one incoming event) that
returns what to do next (messages to send, entries newly safe to apply) --
it never sleeps, never opens a socket, and never reads the wall clock. That
split is deliberate: it lets every safety property (election safety, log
matching, leader completeness, state machine safety) be exercised by a
deterministic in-process test harness that drives ticks and message
delivery by hand, including dropping/reordering/partitioning messages,
without a single flaky timing-based test. A real networked node (see
scripts/fleetqox_raft_node_service.py) is just a thin driver around this
class: it owns the actual sockets and timers and feeds their events in.

Implements the full RequestVote/AppendEntries RPC pair and every rule from
the paper's Figure 2, including the log-matching and leader-completeness
checks that are the actual source of Raft's safety guarantees (not just
"pick the node with the most votes").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(Enum):
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


@dataclass(frozen=True)
class LogEntry:
    term: int
    command: Any


@dataclass(frozen=True)
class RequestVoteRequest:
    term: int
    candidate_id: str
    last_log_index: int
    last_log_term: int


@dataclass(frozen=True)
class RequestVoteResponse:
    term: int
    vote_granted: bool
    voter_id: str


@dataclass(frozen=True)
class AppendEntriesRequest:
    term: int
    leader_id: str
    prev_log_index: int
    prev_log_term: int
    entries: tuple[LogEntry, ...]
    leader_commit: int


@dataclass(frozen=True)
class AppendEntriesResponse:
    term: int
    success: bool
    follower_id: str
    # The follower's log length after applying this request (or its own
    # current length, on rejection) -- lets the leader jump next_index
    # straight to the follower's real length instead of decrementing one
    # index at a time, which is a standard, safe optimization over the
    # bare-minimum algorithm in the paper.
    match_length_hint: int


@dataclass
class Outbox:
    """Messages a single tick/RPC-handling call produced, for the driver to send."""

    request_votes: list[tuple[str, RequestVoteRequest]] = field(default_factory=list)
    append_entries: list[tuple[str, AppendEntriesRequest]] = field(default_factory=list)


class RaftNode:
    """One replica's Raft state machine. See module docstring for the contract."""

    def __init__(self, node_id: str, peer_ids: tuple[str, ...]) -> None:
        if not node_id or node_id in peer_ids:
            raise ValueError("node_id must be set and absent from peer_ids")
        if not peer_ids:
            raise ValueError("a Raft cluster needs at least one peer")
        self.node_id = node_id
        self.peer_ids = tuple(peer_ids)
        self.cluster_size = len(peer_ids) + 1
        self.majority = self.cluster_size // 2 + 1

        # Persistent state (a real deployment must fsync these before
        # replying to any RPC; this in-memory class leaves persistence to
        # the driver, matching the transport-agnostic split above).
        self.current_term = 0
        self.voted_for: str | None = None
        self.log: list[LogEntry] = []

        # Volatile state, all servers.
        self.commit_index = -1
        self.last_applied = -1

        # Volatile state, leaders only (reset on each election win).
        self.next_index: dict[str, int] = {}
        self.match_index: dict[str, int] = {}
        self._votes_received: set[str] = set()

        self.role = Role.FOLLOWER
        self.leader_id: str | None = None

    # -- log helpers ---------------------------------------------------

    def last_log_index(self) -> int:
        return len(self.log) - 1

    def last_log_term(self) -> int:
        return self.log[-1].term if self.log else 0

    def _log_up_to_date(self, candidate_last_term: int, candidate_last_index: int) -> bool:
        # Section 5.4.1: the log with the later term is more up-to-date;
        # if the terms match, the longer log is more up-to-date. This is
        # what actually prevents leader completeness violations -- a
        # candidate that is missing committed entries can never win.
        my_term = self.last_log_term()
        if candidate_last_term != my_term:
            return candidate_last_term > my_term
        return candidate_last_index >= self.last_log_index()

    def _step_down_if_stale(self, term: int) -> None:
        if term > self.current_term:
            self.current_term = term
            self.voted_for = None
            self.role = Role.FOLLOWER
            self._votes_received = set()

    # -- role transitions ------------------------------------------------

    def on_election_timeout(self) -> Outbox:
        """Convert to candidate and request votes from every peer."""
        if self.role == Role.LEADER:
            return Outbox()
        self.role = Role.CANDIDATE
        self.current_term += 1
        self.voted_for = self.node_id
        self._votes_received = {self.node_id}
        self.leader_id = None
        request = RequestVoteRequest(
            term=self.current_term,
            candidate_id=self.node_id,
            last_log_index=self.last_log_index(),
            last_log_term=self.last_log_term(),
        )
        return Outbox(request_votes=[(peer, request) for peer in self.peer_ids])

    def _become_leader(self) -> Outbox:
        self.role = Role.LEADER
        self.leader_id = self.node_id
        next_len = len(self.log)
        self.next_index = {peer: next_len for peer in self.peer_ids}
        self.match_index = {peer: -1 for peer in self.peer_ids}
        return self.on_heartbeat_due()

    def on_heartbeat_due(self) -> Outbox:
        """Leader only: send (or resend) AppendEntries to every peer."""
        if self.role != Role.LEADER:
            return Outbox()
        outbox = Outbox()
        for peer in self.peer_ids:
            outbox.append_entries.append((peer, self._append_entries_for(peer)))
        return outbox

    def _append_entries_for(self, peer: str) -> AppendEntriesRequest:
        next_idx = self.next_index[peer]
        prev_index = next_idx - 1
        prev_term = self.log[prev_index].term if 0 <= prev_index < len(self.log) else 0
        return AppendEntriesRequest(
            term=self.current_term,
            leader_id=self.node_id,
            prev_log_index=prev_index,
            prev_log_term=prev_term,
            entries=tuple(self.log[next_idx:]),
            leader_commit=self.commit_index,
        )

    # -- RPC handlers (receiver side) ------------------------------------

    def handle_request_vote(self, request: RequestVoteRequest) -> RequestVoteResponse:
        self._step_down_if_stale(request.term)
        if request.term < self.current_term:
            return RequestVoteResponse(self.current_term, False, self.node_id)
        can_vote = self.voted_for in (None, request.candidate_id)
        grant = can_vote and self._log_up_to_date(
            request.last_log_term, request.last_log_index
        )
        if grant:
            self.voted_for = request.candidate_id
            # Granting a vote counts as "heard from a legitimate
            # participant this term" -- the driver should treat this the
            # same as receiving a heartbeat and reset its own election
            # timer, or two honest followers could both time out and
            # contest an election neither needed to start.
        return RequestVoteResponse(self.current_term, grant, self.node_id)

    def handle_append_entries(self, request: AppendEntriesRequest) -> AppendEntriesResponse:
        self._step_down_if_stale(request.term)
        if request.term < self.current_term:
            return AppendEntriesResponse(
                self.current_term, False, self.node_id, len(self.log)
            )
        # A valid AppendEntries from a current-or-newer-term leader means
        # this term has a leader -- revert from candidate (or accept as
        # follower) rather than keep contesting the election.
        self.role = Role.FOLLOWER
        self.leader_id = request.leader_id
        if request.prev_log_index >= 0:
            if request.prev_log_index >= len(self.log):
                return AppendEntriesResponse(
                    self.current_term, False, self.node_id, len(self.log)
                )
            if self.log[request.prev_log_index].term != request.prev_log_term:
                # Log-matching property: truncate the conflicting suffix.
                # Whatever we had at and after this index cannot be part
                # of the leader's log, so it can never be safely applied.
                del self.log[request.prev_log_index:]
                return AppendEntriesResponse(
                    self.current_term, False, self.node_id, len(self.log)
                )
        insert_at = request.prev_log_index + 1
        for offset, entry in enumerate(request.entries):
            index = insert_at + offset
            if index < len(self.log):
                if self.log[index].term != entry.term:
                    del self.log[index:]
                    self.log.append(entry)
            else:
                self.log.append(entry)
        if request.leader_commit > self.commit_index:
            self.commit_index = min(request.leader_commit, len(self.log) - 1)
        return AppendEntriesResponse(
            self.current_term, True, self.node_id, len(self.log)
        )

    # -- RPC handlers (sender side, processing a reply) ------------------

    def handle_request_vote_response(
        self, response: RequestVoteResponse,
    ) -> Outbox:
        self._step_down_if_stale(response.term)
        if self.role != Role.CANDIDATE or response.term != self.current_term:
            return Outbox()
        if response.vote_granted:
            self._votes_received.add(response.voter_id)
            if len(self._votes_received) >= self.majority:
                return self._become_leader()
        return Outbox()

    def handle_append_entries_response(
        self, peer_id: str, response: AppendEntriesResponse,
    ) -> Outbox:
        self._step_down_if_stale(response.term)
        if self.role != Role.LEADER or response.term != self.current_term:
            return Outbox()
        if response.success:
            self.match_index[peer_id] = response.match_length_hint - 1
            self.next_index[peer_id] = response.match_length_hint
            self._advance_commit_index()
            return Outbox()
        # Rejected: back off to what the follower says it actually has and
        # retry immediately rather than waiting for the next heartbeat tick,
        # so a genuinely lagging (not just momentarily unreachable) follower
        # catches up promptly.
        self.next_index[peer_id] = max(0, response.match_length_hint)
        return Outbox(append_entries=[(peer_id, self._append_entries_for(peer_id))])

    def _advance_commit_index(self) -> None:
        # Figure 2's leader rule: commit index N is safe once a majority
        # (including the leader itself) has matched it AND log[N] was
        # written in the leader's own current term -- committing an
        # earlier-term entry just because it's now replicated to a
        # majority is the classic Raft correctness pitfall (Section 5.4.2)
        # that this term check exists specifically to avoid.
        for candidate_index in range(len(self.log) - 1, self.commit_index, -1):
            if self.log[candidate_index].term != self.current_term:
                continue
            replicas = 1 + sum(
                1 for peer in self.peer_ids if self.match_index[peer] >= candidate_index
            )
            if replicas >= self.majority:
                self.commit_index = candidate_index
                return

    # -- client-facing API ------------------------------------------------

    def propose(self, command: Any) -> int | None:
        """Append `command` to the leader's log. Returns its (uncommitted) index."""
        if self.role != Role.LEADER:
            return None
        self.log.append(LogEntry(term=self.current_term, command=command))
        return self.last_log_index()

    def take_newly_committed(self) -> list[Any]:
        """Pop commands that just became committed, in commit order, for applying."""
        if self.commit_index <= self.last_applied:
            return []
        newly_committed = [
            entry.command for entry in self.log[self.last_applied + 1:self.commit_index + 1]
        ]
        self.last_applied = self.commit_index
        return newly_committed

    def is_leader(self) -> bool:
        return self.role == Role.LEADER
