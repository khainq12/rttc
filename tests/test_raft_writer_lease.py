import unittest

from fleetqox.raft_writer_lease import RaftLeaderLease, RaftLeaseError


class FakeRaftLeaderLease(RaftLeaderLease):
    def __init__(self, *, statuses: list[dict | None]) -> None:
        super().__init__(status_url="http://raft-n1:5000", timeout_s=0.1)
        self._statuses = list(statuses)
        self.calls = 0

    def current_status(self) -> dict | None:
        self.calls += 1
        if not self._statuses:
            return None
        return self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]


class RaftWriterLeaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_require_leadership_returns_term_when_already_leader(self) -> None:
        client = FakeRaftLeaderLease(statuses=[{"role": "leader", "term": 4}])
        term = await client.require_leadership(wait_timeout_ms=0, retry_ms=50)
        self.assertEqual(term, 4)
        self.assertEqual(client.calls, 1)

    async def test_require_leadership_fails_closed_when_not_leader_and_no_wait(self) -> None:
        client = FakeRaftLeaderLease(statuses=[{"role": "follower", "term": 4}])
        with self.assertRaises(RaftLeaseError):
            await client.require_leadership(wait_timeout_ms=0, retry_ms=50)

    async def test_require_leadership_fails_closed_when_cluster_unreachable(self) -> None:
        client = FakeRaftLeaderLease(statuses=[None])
        with self.assertRaises(RaftLeaseError):
            await client.require_leadership(wait_timeout_ms=0, retry_ms=50)

    async def test_require_leadership_waits_then_wins_election(self) -> None:
        waited = []
        client = FakeRaftLeaderLease(
            statuses=[
                {"role": "candidate", "term": 5},
                {"role": "candidate", "term": 6},
                {"role": "leader", "term": 7},
            ]
        )
        term = await client.require_leadership(
            wait_timeout_ms=2000, retry_ms=10, on_wait=lambda: waited.append(True),
        )
        self.assertEqual(term, 7)
        self.assertTrue(waited)

    async def test_require_leadership_times_out_if_never_elected(self) -> None:
        client = FakeRaftLeaderLease(statuses=[{"role": "candidate", "term": 5}])
        with self.assertRaises(RaftLeaseError):
            await client.require_leadership(wait_timeout_ms=50, retry_ms=10)

    async def test_is_still_leader_requires_matching_term_and_role(self) -> None:
        client = FakeRaftLeaderLease(statuses=[{"role": "leader", "term": 9}])
        self.assertTrue(await client.is_still_leader_async(expected_term=9))

        stepped_down = FakeRaftLeaderLease(statuses=[{"role": "follower", "term": 10}])
        self.assertFalse(await stepped_down.is_still_leader_async(expected_term=9))

        unreachable = FakeRaftLeaderLease(statuses=[None])
        self.assertFalse(await unreachable.is_still_leader_async(expected_term=9))

    def test_rejects_missing_status_url(self) -> None:
        with self.assertRaises(ValueError):
            RaftLeaderLease(status_url="")

    def test_rejects_nonpositive_timeout(self) -> None:
        with self.assertRaises(ValueError):
            RaftLeaderLease(status_url="http://raft-n1:5000", timeout_s=0)


if __name__ == "__main__":
    unittest.main()
