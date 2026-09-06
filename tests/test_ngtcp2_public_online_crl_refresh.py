from __future__ import annotations

import json
import unittest

from scripts.run_rmw_docker_ngtcp2_public_online_crl_refresh_probe import (
    ROOT,
    SCHEMA_VERSION,
)


class Ngtcp2PublicOnlineCrlRefreshTest(unittest.TestCase):
    def test_patch_reloads_crl_with_public_gnutls_apis(self) -> None:
        patch = (
            ROOT / "external/ngtcp2-public-mtls/online-crl-refresh.patch"
        ).read_text(encoding="utf-8")
        # A prior in-place-reload approach (gnutls_credentials_get +
        # gnutls_certificate_free_crls on the existing credentials object)
        # was replaced after empirical proof that GnuTLS leaves stale
        # revocation state behind on a reused credentials object even when
        # the reload itself reports success. The current approach allocates
        # a fresh credentials object on every handshake instead.
        self.assertIn("gnutls_certificate_allocate_credentials", patch)
        self.assertIn("gnutls_certificate_set_x509_trust_file", patch)
        self.assertIn("gnutls_certificate_set_x509_crl_file", patch)
        self.assertIn("gnutls_credentials_set", patch)
        self.assertIn(
            "FLEETQOX_GNUTLS_RELOAD_CLIENT_CRL_EACH_HANDSHAKE",
            patch,
        )
        self.assertIn("FLEETQOX_PUBLIC_MTLS_CRL_RELOADED", patch)
        self.assertIn("FLEETQOX_PUBLIC_MTLS_CRL_RELOAD_FAILED", patch)
        self.assertIn("return GNUTLS_E_CERTIFICATE_ERROR", patch)
        self.assertNotIn("aioquic", patch.lower())

    def test_dockerfile_applies_refresh_after_identity_isolation(self) -> None:
        dockerfile = (
            ROOT / "external/ngtcp2-public-mtls/Dockerfile"
        ).read_text(encoding="utf-8")
        isolation = dockerfile.index("active-worker-isolation.patch")
        refresh = dockerfile.index("online-crl-refresh.patch")
        self.assertLess(isolation, refresh)

    def test_runner_covers_live_revoke_restore_and_invalid_file(self) -> None:
        runner = (
            ROOT
            / "scripts/run_rmw_docker_ngtcp2_public_online_crl_refresh_probe.py"
        ).read_text(encoding="utf-8")
        self.assertIn(SCHEMA_VERSION, runner)
        self.assertIn("same_server_instance", runner)
        self.assertIn("stateful-revoked.crl.pem", runner)
        self.assertIn("initial-client.crl.pem", runner)
        self.assertIn("invalid-client.crl.pem", runner)
        self.assertIn("negative_client_was_rejected", runner)
        self.assertIn('"active_session_revocation_claim": False', runner)
        self.assertIn('"online_client_ca_rotation_claim": False', runner)
        self.assertIn('"production_quic_backend_claim": False', runner)

    def test_canonical_artifact_passes_five_runs(self) -> None:
        artifact = (
            ROOT
            / "results_rmw_socket/"
            "docker_ngtcp2_public_online_crl_refresh_summary.json"
        )
        if not artifact.exists():
            self.skipTest("canonical online-CRL artifact has not been generated")
        summary = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(summary["schema_version"], SCHEMA_VERSION)
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["run_count"], 5)
        self.assertEqual(summary["ok_run_count"], 5)
        self.assertTrue(
            summary["online_client_crl_refresh_new_connections_claim"]
        )
        self.assertTrue(
            summary["new_connection_revocation_without_server_restart_claim"]
        )
        self.assertTrue(summary["invalid_crl_refresh_fail_closed_claim"])
        self.assertFalse(summary["active_session_revocation_claim"])
        self.assertFalse(summary["production_quic_backend_claim"])


if __name__ == "__main__":
    unittest.main()
