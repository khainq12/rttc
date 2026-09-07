"""Deterministic safety-property tests for the from-scratch Raft core.

No sleeping, no wall clock, no sockets: every test drives a small in-memory
cluster of fleetqox.raft.RaftNode instances by hand -- calling
on_election_timeout()/on_heartbeat_due() and delivering the RPCs those
calls produce (or deliberately not delivering some of them, to simulate a
partition) one message at a time. That is what makes it possible to test
things like "a partitioned leader can never commit" and "an old-term entry
is not committed by replication count alone" exactly, on every run, instead
of hoping a timing-based integration test happens to hit the right window.
"""

from __future__ import annotations

import unittest

from fleetqox.raft import AppendEntriesRequest, LogEntry, RaftNode, RequestVoteRequest


class Cluster:
    """A hand-driven message bus for a fixed set of RaftNode replicas."""

    def __init__(self, node_ids: tuple[str, ...]) -> None:
        self.nodes = {
            node_id: RaftNode(node_id, tuple(peer for peer in node_ids if peer != node_id))
            for node_id in node_ids
        }
        self.node_ids = node_ids
        # Nodes in this set silently drop every message sent to OR from
        # them -- the partition-simulation primitive used below.
        self.partitioned: set[str] = set()

    def _deliver(self, sender: str, outbox) -> None:
        for peer, request in outbox.request_votes:
            self._send_request_vote(sender, peer, request)
        for peer, request in outbox.append_entries:
            self._send_append_entries(sender, peer, request)

    def _reachable(self, a: str, b: str) -> bool:
        return a not in self.partitioned and b not in self.partitioned

    def _send_request_vote(self, sender: str, peer: str, request: RequestVoteRequest) -> None:
        if not self._reachable(sender, peer):
            return
        response = self.nodes[peer].handle_request_vote(request)
        if not self._reachable(sender, peer):
            return
        self._deliver(sender, self.nodes[sender].handle_request_vote_response(response))

    def _send_append_entries(
        self, sender: str, peer: str, request: AppendEntriesRequest,
    ) -> None:
        if not self._reachable(sender, peer):
            return
        response = self.nodes[peer].handle_append_entries(request)
        if not self._reachable(sender, peer):
            return
        self._deliver(
            sender, self.nodes[sender].handle_append_entries_response(peer, response)
        )

    def elect(self, candidate: str) -> None:
        self._deliver(candidate, self.nodes[candidate].on_election_timeout())

    def heartbeat(self, leader: str) -> None:
        self._deliver(leader, self.nodes[leader].on_heartbeat_due())

    def propose(self, leader: str, command: object) -> int | None:
        index = self.nodes[leader].propose(command)
        self.heartbeat(leader)
        return index

    def leaders_in_term(self, term: int) -> list[str]:
        return [
            node_id for node_id, node in self.nodes.items()
            if node.role.value == "leader" and node.current_term == term
        ]


class RaftSafetyTest(unittest.TestCase):
    def test_uncontested_candidate_wins_and_becomes_leader(self) -> None:
        cluster = Cluster(("n1", "n2", "n3"))
        cluster.elect("n1")
        self.assertTrue(cluster.nodes["n1"].is_leader())
        self.assertEqual(cluster.nodes["n2"].role.value, "follower")
        self.assertEqual(cluster.nodes["n3"].role.value, "follower")
        self.assertEqual(cluster.leaders_in_term(1), ["n1"])

    def test_election_safety_at_most_one_leader_per_term(self) -> None:
        # Two candidates race in a 5-node cluster; whichever's RequestVote
        # a given follower sees first wins that follower's vote for the
        # term (votedFor is sticky), so a 2-2 split with one abstention is
        # possible -- but even then, this must never produce two leaders
        # sharing the same term number.
        cluster = Cluster(("n1", "n2", "n3", "n4", "n5"))
        cluster.nodes["n1"].on_election_timeout()
        cluster.nodes["n2"].on_election_timeout()
        # n1 and n2 are now both candidates for term 1 without having
        # broadcast yet; hand-deliver their votes to disjoint follower
        # sets to force a split vote deterministically.
        request_n1 = RequestVoteRequest(
            term=1, candidate_id="n1", last_log_index=-1, last_log_term=0,
        )
        request_n2 = RequestVoteRequest(
            term=1, candidate_id="n2", last_log_index=-1, last_log_term=0,
        )
        for peer in ("n3",):
            response = cluster.nodes[peer].handle_request_vote(request_n1)
            cluster._deliver("n1", cluster.nodes["n1"].handle_request_vote_response(response))
        for peer in ("n4",):
            response = cluster.nodes[peer].handle_request_vote(request_n2)
            cluster._deliver("n2", cluster.nodes["n2"].handle_request_vote_response(response))
        self.assertFalse(cluster.nodes["n1"].is_leader())
        self.assertFalse(cluster.nodes["n2"].is_leader())
        self.assertEqual(cluster.leaders_in_term(1), [])
        # A fresh election round (new term) with full connectivity resolves it.
        cluster.elect("n3")
        self.assertEqual(len(cluster.leaders_in_term(2)), 1)

    def test_stale_candidate_cannot_win_over_more_up_to_date_log(self) -> None:
        cluster = Cluster(("n1", "n2", "n3"))
        cluster.elect("n1")
        cluster.propose("n1", "committed-entry")
        self.assertEqual(cluster.nodes["n1"].commit_index, 0)
        # n2 and n3 both have the entry replicated. A node with an EMPTY
        # log (as if it had been offline and never caught up) must not be
        # able to win an election over them, or leader completeness would
        # break -- a new leader could lose an already-committed entry.
        stale = RaftNode("n4-hypothetical-stale", ("n2",))
        vote = cluster.nodes["n2"].handle_request_vote(
            RequestVoteRequest(
                term=cluster.nodes["n2"].current_term + 1,
                candidate_id="stale",
                last_log_index=-1,
                last_log_term=0,
            )
        )
        self.assertFalse(vote.vote_granted)
        del stale  # constructed only to document the scenario being modeled

    def test_commit_requires_majority_not_just_the_leader(self) -> None:
        cluster = Cluster(("n1", "n2", "n3", "n4", "n5"))
        cluster.elect("n1")
        # Isolate three of the four followers so only n1 (leader) and n2
        # can exchange AppendEntries -- 2 of 5 is not a majority.
        cluster.partitioned = {"n3", "n4", "n5"}
        cluster.propose("n1", "needs-majority")
        self.assertEqual(
            cluster.nodes["n1"].commit_index, -1,
            "an entry replicated to only 2 of 5 nodes must not be committed",
        )
        cluster.partitioned = set()
        cluster.heartbeat("n1")
        self.assertEqual(cluster.nodes["n1"].commit_index, 0)
        self.assertEqual(cluster.nodes["n1"].take_newly_committed(), ["needs-majority"])

    def test_partitioned_leader_can_never_commit_and_steps_down_on_heal(self) -> None:
        cluster = Cluster(("n1", "n2", "n3", "n4", "n5"))
        cluster.elect("n1")
        cluster.propose("n1", "before-partition")
        self.assertEqual(cluster.nodes["n1"].commit_index, 0)

        # n1 (the leader) is isolated from everyone; n3..n5 are the
        # surviving majority and can still make progress on their own.
        cluster.partitioned = {"n1"}
        stuck_index = cluster.propose("n1", "written-while-partitioned")
        self.assertIsNotNone(stuck_index)
        self.assertEqual(
            cluster.nodes["n1"].commit_index, 0,
            "a minority-of-one leader must never advance its commit index",
        )
        cluster.elect("n3")
        self.assertEqual(cluster.leaders_in_term(cluster.nodes["n3"].current_term), ["n3"])
        cluster.propose("n3", "committed-by-new-leader")
        self.assertGreaterEqual(cluster.nodes["n3"].commit_index, 0)

        # Heal the partition: n1's next heartbeat carries the old term and
        # must be rejected, converting it to a follower instead of ever
        # producing two simultaneous leaders.
        cluster.partitioned = set()
        cluster.heartbeat("n1")
        self.assertFalse(cluster.nodes["n1"].is_leader())
        self.assertEqual(cluster.nodes["n1"].current_term, cluster.nodes["n3"].current_term)
        for term in range(1, cluster.nodes["n3"].current_term + 1):
            self.assertLessEqual(len(cluster.leaders_in_term(term)), 1)

    def test_follower_log_conflict_is_truncated_not_merged(self) -> None:
        # A direct, low-level test of the log-matching rule (Figure 2,
        # AppendEntries receiver rule 3): given a term-2 leader's word
        # that index 0 actually holds a different entry than what this
        # follower has, the follower must discard its conflicting entry
        # outright, not keep it or try to merge/append around it.
        follower = RaftNode("n2", ("n1",))
        follower.current_term = 2
        follower.log = [LogEntry(term=1, command="divergent-orphan")]
        request = AppendEntriesRequest(
            term=2, leader_id="n1", prev_log_index=-1, prev_log_term=0,
            entries=(LogEntry(term=2, command="authoritative-entry"),),
            leader_commit=-1,
        )
        response = follower.handle_append_entries(request)
        self.assertTrue(response.success)
        self.assertEqual(
            [entry.command for entry in follower.log], ["authoritative-entry"]
        )

    def test_old_term_entry_is_not_committed_by_replication_count_alone(self) -> None:
        # This is the specific Section 5.4.2 pitfall, reproduced with the
        # paper's own minimum cluster size (5) for it to be observable: a
        # majority-replicated entry from an OLD term must not be committed
        # just because a later leader's AppendEntries pushed it out to
        # everyone -- only actually committing an entry from the new
        # leader's OWN current term is allowed to pull earlier entries
        # along with it.
        cluster = Cluster(("n1", "n2", "n3", "n4", "n5"))
        cluster.elect("n1")
        old_term = cluster.nodes["n1"].current_term
        # Replicate to n2 only (2 of 5 -- not a majority) and keep
        # n3/n4/n5 unreachable, so the entry is nowhere near committed
        # under the old leader.
        cluster.partitioned = {"n3", "n4", "n5"}
        cluster.propose("n1", "old-term-entry")
        self.assertEqual(cluster.nodes["n1"].commit_index, -1)
        cluster.partitioned = set()

        # n2 (which has the entry) contests the next election; its log is
        # strictly longer than n3/n4/n5's empty logs, so it wins even
        # though n1 is still technically "alive." Winning the election and
        # sending its first round of heartbeats naturally replicates the
        # old-term entry out to everyone (n1 already had it; n3/n4/n5
        # catch up via the reject-and-retry path) -- reaching full 5-of-5
        # replication before n2 has proposed anything of its own.
        cluster.elect("n2")
        self.assertTrue(cluster.nodes["n2"].is_leader())
        self.assertGreater(cluster.nodes["n2"].current_term, old_term)
        for peer in ("n1", "n3", "n4", "n5"):
            self.assertEqual(
                cluster.nodes[peer].log[-1].command, "old-term-entry",
                f"{peer} should have caught up to the old-term entry by now",
            )
        # Fully replicated -- but still must not be committed, because
        # log[0]'s term is not the new leader's current term.
        self.assertEqual(cluster.nodes["n2"].commit_index, -1)
        cluster.propose("n2", "new-term-entry")
        self.assertGreaterEqual(cluster.nodes["n2"].commit_index, 1)
        applied = cluster.nodes["n2"].take_newly_committed()
        self.assertEqual(applied, ["old-term-entry", "new-term-entry"])

    def test_take_newly_committed_is_consumed_exactly_once_and_in_order(self) -> None:
        cluster = Cluster(("n1", "n2", "n3"))
        cluster.elect("n1")
        cluster.propose("n1", "first")
        cluster.propose("n1", "second")
        self.assertEqual(cluster.nodes["n1"].take_newly_committed(), ["first", "second"])
        self.assertEqual(cluster.nodes["n1"].take_newly_committed(), [])
        cluster.propose("n1", "third")
        self.assertEqual(cluster.nodes["n1"].take_newly_committed(), ["third"])

    def test_non_leader_cannot_accept_client_proposals(self) -> None:
        cluster = Cluster(("n1", "n2", "n3"))
        cluster.elect("n1")
        self.assertIsNone(cluster.nodes["n2"].propose("should-be-rejected"))
        self.assertIsNone(cluster.nodes["n3"].propose("should-be-rejected"))


if __name__ == "__main__":
    unittest.main()
