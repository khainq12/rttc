"""Focused tests for scripts/check_ns3_image_freshness.py's pure logic
(timestamp comparison, apt-package-state interpretation) -- see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "WIFI GATEWAY BENCHMARK -- N=2 ROOT
CAUSE FULLY PROVEN AND FIXED" for why this guard exists. Does not spin
up real Docker containers -- exercises check_image_freshness()'s own
decision logic directly by monkeypatching its three data-gathering
helpers.
"""

import unittest
from unittest import mock

from scripts import check_ns3_image_freshness as guard


class CheckImageFreshnessTest(unittest.TestCase):
    def test_fresh_image_reports_fresh(self):
        with mock.patch.object(guard, "_image_created_epoch", return_value=200), \
             mock.patch.object(guard, "_dockerfile_last_commit_epoch", return_value=100), \
             mock.patch.object(guard, "_apt_ns3_package_present", return_value=False):
            diag = guard.check_image_freshness()
        self.assertFalse(diag["stale_by_timestamp"])
        self.assertTrue(diag["fresh"])

    def test_image_older_than_dockerfile_commit_is_stale(self):
        # This is the EXACT historical bug: image built 2026-09-02,
        # Dockerfile's ns-3-from-source commit landed 2026-09-11.
        with mock.patch.object(guard, "_image_created_epoch", return_value=100), \
             mock.patch.object(guard, "_dockerfile_last_commit_epoch", return_value=200), \
             mock.patch.object(guard, "_apt_ns3_package_present", return_value=False):
            diag = guard.check_image_freshness()
        self.assertTrue(diag["stale_by_timestamp"])
        self.assertFalse(diag["fresh"])

    def test_apt_package_present_is_not_fresh_even_if_timestamp_ok(self):
        # A freshly-rebuilt image that somehow still has the apt ns3
        # package (e.g. a Dockerfile regression) must not be reported
        # fresh just because it's newer than the last commit.
        with mock.patch.object(guard, "_image_created_epoch", return_value=200), \
             mock.patch.object(guard, "_dockerfile_last_commit_epoch", return_value=100), \
             mock.patch.object(guard, "_apt_ns3_package_present", return_value=True):
            diag = guard.check_image_freshness()
        self.assertFalse(diag["stale_by_timestamp"])
        self.assertFalse(diag["fresh"])

    def test_assert_raises_on_stale_timestamp(self):
        with mock.patch.object(guard, "_image_created_epoch", return_value=100), \
             mock.patch.object(guard, "_dockerfile_last_commit_epoch", return_value=200), \
             mock.patch.object(guard, "_apt_ns3_package_present", return_value=False):
            with self.assertRaises(guard.StaleImageError):
                guard.assert_image_fresh()

    def test_assert_raises_when_apt_package_present(self):
        with mock.patch.object(guard, "_image_created_epoch", return_value=200), \
             mock.patch.object(guard, "_dockerfile_last_commit_epoch", return_value=100), \
             mock.patch.object(guard, "_apt_ns3_package_present", return_value=True):
            with self.assertRaises(guard.StaleImageError):
                guard.assert_image_fresh()

    def test_assert_passes_when_fresh(self):
        with mock.patch.object(guard, "_image_created_epoch", return_value=200), \
             mock.patch.object(guard, "_dockerfile_last_commit_epoch", return_value=100), \
             mock.patch.object(guard, "_apt_ns3_package_present", return_value=False):
            guard.assert_image_fresh()  # must not raise

    def test_assert_raises_when_image_uninspectable(self):
        with mock.patch.object(guard, "_image_created_epoch", return_value=None), \
             mock.patch.object(guard, "_dockerfile_last_commit_epoch", return_value=100), \
             mock.patch.object(guard, "_apt_ns3_package_present", return_value=False):
            with self.assertRaises(guard.StaleImageError):
                guard.assert_image_fresh()

    def test_assert_raises_when_apt_check_unverifiable(self):
        with mock.patch.object(guard, "_image_created_epoch", return_value=200), \
             mock.patch.object(guard, "_dockerfile_last_commit_epoch", return_value=100), \
             mock.patch.object(guard, "_apt_ns3_package_present", return_value=None):
            with self.assertRaises(guard.StaleImageError):
                guard.assert_image_fresh()


if __name__ == "__main__":
    unittest.main()
