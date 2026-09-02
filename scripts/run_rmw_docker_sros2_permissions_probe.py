"""Generate SROS2 artifacts and exercise FleetRMW permissions XML enforcement."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_allocation_probe import DEFAULT_IMAGE, parse_json_rows  # noqa: E402
from scripts.run_rmw_docker_security_options_probe import parse_key_value_markers  # noqa: E402
from scripts.run_rmw_docker_udp_peer_auth_probe import (  # noqa: E402
    run_probe as run_udp_peer_auth_probe,
)


SCHEMA_VERSION = "fleetrmw.docker_sros2_permissions_probe.v1"
DEFAULT_POLICY_FILE = (
    "ros2_ws/src/rmw_fleetqox_cpp/test/security/sros2_policy.xml"
)
DEFAULT_MALFORMED_PERMISSIONS_FILE = (
    "ros2_ws/src/rmw_fleetqox_cpp/test/security/malformed_permissions.xml"
)
DEFAULT_GOVERNANCE_FILE = (
    "ros2_ws/src/rmw_fleetqox_cpp/test/security/"
    "sros2_governance_access_control.xml"
)
DEFAULT_ENCLAVE = "/fleetqox/security_probe"
DEFAULT_DOMAIN_ID = 7


def sros2_permissions_probe_ok(probe: dict[str, Any]) -> bool:
    return (
        probe.get("status") == "ok"
        and probe.get("probe_mode") == "valid_signed_permissions"
        and probe.get("permissions_file_configured") is True
        and probe.get("signed_permissions_file_configured") is True
        and probe.get("permissions_ca_file_configured") is True
        and probe.get("signed_permissions_source") is True
        and probe.get("runtime_signature_verified") is True
        and probe.get("permissions_xml_loaded") is True
        and probe.get("permissions_xml_error") == ""
        and probe.get("enclave") == DEFAULT_ENCLAVE
        and int(probe.get("domain_id", -1)) == DEFAULT_DOMAIN_ID
        and int(probe.get("allowed_publish_returncode", -1)) == 0
        and probe.get("allowed_taken") is True
        and probe.get("allowed_payload_ok") is True
        and int(probe.get("explicit_denied_publish_returncode", 0)) != 0
        and probe.get("explicit_denied_taken") is False
        and int(probe.get("default_denied_publish_returncode", 0)) != 0
        and probe.get("default_denied_taken") is False
        and int(probe.get("subscribe_denied_publish_returncode", -1)) == 0
        and int(probe.get("subscribe_default_denied_publish_returncode", -1)) == 0
        and probe.get("subscribe_decisions_ready") is True
        and probe.get("subscribe_denied_taken") is False
        and probe.get("subscribe_default_denied_taken") is False
        and int(probe.get("security_policy_denied_delta", 0)) == 2
        and int(probe.get("sros2_permissions_xml_allowed_delta", 0)) == 3
        and int(probe.get("sros2_permissions_xml_denied_delta", 0)) == 2
        and int(probe.get("sros2_permissions_xml_parse_errors_delta", -1)) == 0
        and int(probe.get("sros2_permissions_xml_subscribe_allowed_delta", 0)) == 1
        and int(probe.get("sros2_permissions_xml_subscribe_denied_delta", 0)) == 2
        and probe.get("sros2_permissions_xml_publish_enforcement_claim") is True
        and probe.get("sros2_permissions_xml_subscribe_enforcement_claim") is True
        and probe.get("sros2_permissions_xml_pubsub_enforcement_claim") is True
        and probe.get("sros2_policy_enforcement_claim") is False
        and probe.get("runtime_permissions_signature_validation") is True
        and probe.get("runtime_sros2_permissions_signature_validation_claim") is True
        and probe.get("governance_xml_enforcement_claim") is False
        and probe.get("production_security_hardening_claim") is False
    )


def malformed_permissions_fail_closed_ok(probe: dict[str, Any]) -> bool:
    return (
        probe.get("status") == "ok"
        and probe.get("probe_mode") == "malformed_fail_closed"
        and probe.get("permissions_file_configured") is True
        and probe.get("permissions_xml_loaded") is False
        and probe.get("signed_permissions_source") is False
        and probe.get("runtime_signature_verified") is False
        and bool(probe.get("permissions_xml_error"))
        and int(probe.get("allowed_publish_returncode", 0)) != 0
        and probe.get("allowed_taken") is False
        and probe.get("allowed_no_message") is True
        and int(probe.get("explicit_denied_publish_returncode", 0)) != 0
        and probe.get("explicit_denied_taken") is False
        and int(probe.get("default_denied_publish_returncode", 0)) != 0
        and probe.get("default_denied_taken") is False
        and int(probe.get("subscribe_denied_publish_returncode", 0)) != 0
        and int(probe.get("subscribe_default_denied_publish_returncode", 0)) != 0
        and probe.get("subscribe_denied_taken") is False
        and probe.get("subscribe_default_denied_taken") is False
        and int(probe.get("security_policy_denied_delta", 0)) == 5
        and int(probe.get("sros2_permissions_xml_allowed_delta", -1)) == 0
        and int(probe.get("sros2_permissions_xml_denied_delta", 0)) == 5
        and int(probe.get("sros2_permissions_xml_parse_errors_delta", 0)) == 5
        and int(probe.get("sros2_permissions_xml_subscribe_allowed_delta", -1)) == 0
        and int(probe.get("sros2_permissions_xml_subscribe_denied_delta", -1)) == 0
        and probe.get("sros2_permissions_xml_publish_enforcement_claim") is False
        and probe.get("malformed_permissions_fail_closed_claim") is True
    )


def tampered_signature_fail_closed_ok(probe: dict[str, Any]) -> bool:
    return (
        probe.get("status") == "ok"
        and probe.get("probe_mode") == "tampered_signature_fail_closed"
        and probe.get("permissions_file_configured") is True
        and probe.get("signed_permissions_file_configured") is True
        and probe.get("permissions_ca_file_configured") is True
        and probe.get("signed_permissions_source") is True
        and probe.get("runtime_signature_verified") is False
        and probe.get("permissions_xml_loaded") is False
        and "permissions_p7s_verify_failed" in str(probe.get("permissions_xml_error"))
        and int(probe.get("allowed_publish_returncode", 0)) != 0
        and probe.get("allowed_taken") is False
        and probe.get("allowed_no_message") is True
        and int(probe.get("explicit_denied_publish_returncode", 0)) != 0
        and probe.get("explicit_denied_taken") is False
        and int(probe.get("default_denied_publish_returncode", 0)) != 0
        and probe.get("default_denied_taken") is False
        and int(probe.get("subscribe_denied_publish_returncode", 0)) != 0
        and int(probe.get("subscribe_default_denied_publish_returncode", 0)) != 0
        and probe.get("subscribe_denied_taken") is False
        and probe.get("subscribe_default_denied_taken") is False
        and int(probe.get("security_policy_denied_delta", 0)) == 5
        and int(probe.get("sros2_permissions_xml_allowed_delta", -1)) == 0
        and int(probe.get("sros2_permissions_xml_denied_delta", 0)) == 5
        and int(probe.get("sros2_permissions_xml_parse_errors_delta", 0)) == 5
        and int(probe.get("sros2_permissions_xml_subscribe_allowed_delta", -1)) == 0
        and int(probe.get("sros2_permissions_xml_subscribe_denied_delta", -1)) == 0
        and probe.get("sros2_permissions_xml_publish_enforcement_claim") is False
        and probe.get("tampered_signed_permissions_fail_closed_claim") is True
    )


def sros2_service_permissions_probe_ok(probe: dict[str, Any]) -> bool:
    return (
        probe.get("status") == "ok"
        and probe.get("policy_loaded") is True
        and probe.get("runtime_signature_verified") is True
        and int(probe.get("allowed_send_request_returncode", -1)) == 0
        and probe.get("allowed_request_taken") is True
        and int(probe.get("allowed_send_response_returncode", -1)) == 0
        and probe.get("allowed_response_taken") is True
        and probe.get("allowed_response_payload_ok") is True
        and int(probe.get("request_denied_send_returncode", 0)) != 0
        and probe.get("request_denied_queue_empty") is True
        and int(probe.get("default_denied_send_returncode", 0)) != 0
        and probe.get("default_denied_queue_empty") is True
        and int(probe.get("response_denied_send_request_returncode", -1)) == 0
        and probe.get("response_denied_request_taken") is False
        and int(probe.get("response_denied_send_response_returncode", 0)) != 0
        and probe.get("response_denied_queue_empty") is True
        and int(probe.get("service_request_publish_allowed_delta", 0)) == 2
        and int(probe.get("service_request_publish_denied_delta", 0)) == 2
        and int(probe.get("service_request_subscribe_allowed_delta", 0)) == 1
        and int(probe.get("service_request_subscribe_denied_delta", 0)) == 1
        and int(probe.get("service_response_publish_allowed_delta", 0)) == 1
        and int(probe.get("service_response_publish_denied_delta", 0)) == 1
        and int(probe.get("service_response_subscribe_allowed_delta", 0)) == 1
        and int(probe.get("service_response_subscribe_denied_delta", -1)) == 0
        and int(probe.get("service_authorization_parse_errors_delta", -1)) == 0
        and probe.get("sros2_service_request_reply_authorization_claim") is True
        and probe.get("sros2_action_authorization_claim") is False
    )


def sros2_action_permissions_probe_ok(probe: dict[str, Any]) -> bool:
    deltas = probe.get("authorization_metric_deltas", {})
    return (
        probe.get("status") == "ok"
        and probe.get("enclave") == DEFAULT_ENCLAVE
        and int(probe.get("domain_id", -1)) == DEFAULT_DOMAIN_ID
        and probe.get("policy_loaded") is True
        and probe.get("runtime_signature_verified") is True
        and probe.get("permissions_xml_error") == ""
        and probe.get("allowed_server_available") is True
        and probe.get("allowed_goal_accepted") is True
        and probe.get("allowed_result_done") is True
        and int(probe.get("allowed_result_status", -1)) == 4
        and probe.get("allowed_result_frame") == "map"
        and probe.get("allowed_result_child_frame") == "base_link"
        and int(probe.get("allowed_result_error", -1)) == 0
        and probe.get("call_denied_server_available") is True
        and bool(probe.get("call_denied_exception"))
        and int(probe.get("call_denied_request_publish_denied_delta", 0)) >= 1
        and probe.get("execute_denied_server_available") is True
        and probe.get("execute_denied_future_created") is True
        and probe.get("execute_denied_future_done") is False
        and int(probe.get("execute_denied_request_subscribe_denied_delta", 0)) >= 1
        and probe.get("action_call_execute_decision_matrix_claim") is True
        and probe.get("sros2_action_allowed_end_to_end_claim") is True
        and probe.get("sros2_action_call_denied_fail_closed_claim") is True
        and probe.get("sros2_action_execute_denied_fail_closed_claim") is True
        and probe.get("sros2_action_authorization_metrics_claim") is True
        and probe.get("sros2_action_authorization_claim") is True
        and int(deltas.get("sros2_service_request_publish_allowed", 0)) >= 3
        and int(deltas.get("sros2_service_request_publish_denied", 0)) >= 1
        and int(deltas.get("sros2_service_request_subscribe_denied", 0)) >= 1
        and int(deltas.get("sros2_service_authorization_parse_errors", -1)) == 0
    )


def sros2_governance_probe_ok(probe: dict[str, Any]) -> bool:
    return (
        probe.get("status") == "ok"
        and probe.get("probe_mode") == "signed_governance_access_control"
        and probe.get("governance_file_configured") is True
        and probe.get("signed_governance_source") is True
        and probe.get("runtime_signature_verified") is True
        and probe.get("governance_xml_loaded") is True
        and probe.get("governance_xml_error") == ""
        and int(probe.get("allowed_publish_governance_decision", -1)) == 2
        and int(probe.get("allowed_subscribe_governance_decision", -1)) == 2
        and int(probe.get("uncontrolled_publish_governance_decision", -1)) == 1
        and int(probe.get("uncontrolled_subscribe_governance_decision", -1)) == 1
        and int(probe.get("allowed_publish_returncode", -1)) == 0
        and probe.get("allowed_taken") is True
        and int(probe.get("uncontrolled_publish_returncode", -1)) == 0
        and probe.get("uncontrolled_taken") is True
        and int(probe.get("permissions_denied_publish_returncode", 0)) != 0
        and probe.get("permissions_denied_queue_empty") is True
        and probe.get("sros2_governance_access_control_claim") is True
        and probe.get("governance_transport_security_claim") is False
    )


def governance_protection_fail_closed_ok(probe: dict[str, Any]) -> bool:
    return (
        probe.get("status") == "ok"
        and probe.get("probe_mode") == "transport_protection_fail_closed"
        and probe.get("governance_xml_loaded") is True
        and probe.get("runtime_signature_verified") is True
        and int(probe.get("allowed_publish_governance_decision", -1)) == 4
        and int(probe.get("allowed_subscribe_governance_decision", -1)) == 4
        and int(probe.get("allowed_publish_returncode", 0)) != 0
        and probe.get("allowed_taken") is False
        and int(probe.get("uncontrolled_publish_returncode", 0)) != 0
        and probe.get("uncontrolled_taken") is False
        and int(probe.get("permissions_denied_publish_returncode", 0)) != 0
        and probe.get("permissions_denied_queue_empty") is True
        and probe.get(
            "sros2_governance_transport_protection_fail_closed_claim"
        ) is True
        and probe.get("governance_transport_security_claim") is False
    )


def tampered_governance_fail_closed_ok(probe: dict[str, Any]) -> bool:
    return (
        probe.get("status") == "ok"
        and probe.get("probe_mode") == "tampered_governance_fail_closed"
        and probe.get("signed_governance_source") is True
        and probe.get("runtime_signature_verified") is False
        and probe.get("governance_xml_loaded") is False
        and "governance_p7s_verify_failed" in str(probe.get("governance_xml_error"))
        and int(probe.get("allowed_publish_governance_decision", -1)) == 6
        and int(probe.get("allowed_publish_returncode", 0)) != 0
        and int(probe.get("uncontrolled_publish_returncode", 0)) != 0
        and int(probe.get("permissions_denied_publish_returncode", 0)) != 0
        and probe.get("sros2_tampered_signed_governance_fail_closed_claim") is True
    )


def sros2_identity_probe_ok(probe: dict[str, Any]) -> bool:
    return (
        probe.get("status") == "ok"
        and probe.get("probe_mode") == "valid"
        and probe.get("identity_credentials_configured") is True
        and probe.get("identity_certificate_chain_verified") is True
        and probe.get("identity_private_key_matches") is True
        and probe.get("identity_subject_common_name") == DEFAULT_ENCLAVE
        and probe.get("checked_enclave") == DEFAULT_ENCLAVE
        and probe.get("identity_credentials_error") == ""
        and int(probe.get("identity_validation_decision", -1)) == 1
        and int(probe.get("rmw_init_returncode", -1)) == 0
        and probe.get("sros2_local_identity_credentials_validation_claim") is True
        and probe.get("sros2_peer_identity_authentication_claim") is False
    )


def identity_fail_closed_control_ok(probe: dict[str, Any], mode: str) -> bool:
    claim = {
        "tampered_certificate": "sros2_tampered_identity_certificate_fail_closed_claim",
        "private_key_mismatch": "sros2_identity_private_key_mismatch_fail_closed_claim",
        "enclave_mismatch": "sros2_identity_enclave_mismatch_fail_closed_claim",
    }[mode]
    expected_decision = 3 if mode == "enclave_mismatch" else 2
    return (
        probe.get("status") == "ok"
        and probe.get("probe_mode") == mode
        and probe.get("identity_credentials_configured") is True
        and int(probe.get("identity_validation_decision", -1)) == expected_decision
        and int(probe.get("rmw_init_returncode", 0)) != 0
        and probe.get(claim) is True
        and probe.get("sros2_peer_identity_authentication_claim") is False
    )


def run_probe(
    *,
    root: Path,
    image: str,
    iterations: int = 1,
    policy_file: str = DEFAULT_POLICY_FILE,
) -> dict[str, Any]:
    run_count = max(iterations, 1)
    policy_path = root / policy_file
    malformed_permissions_path = root / DEFAULT_MALFORMED_PERMISSIONS_FILE
    governance_path = root / DEFAULT_GOVERNANCE_FILE
    if (
        not policy_path.is_file()
        or not malformed_permissions_path.is_file()
        or not governance_path.is_file()
    ):
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "stage": "policy_file",
            "policy_file": policy_file,
            "error": (
                "SROS2 policy, governance, or malformed-permissions fixture "
                "does not exist"
            ),
        }

    enclave_relative = DEFAULT_ENCLAVE.lstrip("/")
    permissions_dir = f"/tmp/fq-sros2-keystore/enclaves/{enclave_relative}"
    permissions_xml = f"{permissions_dir}/permissions.xml"
    signed_permissions = f"{permissions_dir}/permissions.p7s"
    identity_certificate = f"{permissions_dir}/cert.pem"
    identity_private_key = f"{permissions_dir}/key.pem"
    identity_ca = f"{permissions_dir}/identity_ca.cert.pem"
    tampered_identity_certificate = "/tmp/fq-sros2-tampered-identity-cert.pem"
    mismatched_identity_private_key = "/tmp/fq-sros2-mismatched-identity-key.pem"
    permissions_ca = "/tmp/fq-sros2-keystore/public/permissions_ca.cert.pem"
    verified_smime = "/tmp/fq-sros2-verified-smime.txt"
    verified_permissions = "/tmp/fq-sros2-verified-permissions.xml"
    tampered_signed_permissions = "/tmp/fq-sros2-tampered-permissions.p7s"
    default_signed_governance = "/tmp/fq-sros2-default-governance.p7s"
    signed_governance = "/tmp/fq-sros2-access-governance.p7s"
    tampered_signed_governance = "/tmp/fq-sros2-tampered-governance.p7s"
    permissions_xsd = (
        "/opt/ros/jazzy/lib/python3.12/site-packages/"
        "sros2/policy/schemas/dds/permissions.xsd"
    )
    governance_xsd = (
        "/opt/ros/jazzy/lib/python3.12/site-packages/"
        "sros2/policy/schemas/dds/governance.xsd"
    )
    command = (
        "source /opt/ros/jazzy/setup.bash && set -eo pipefail && "
        "rm -rf /tmp/fq-sros2-build /tmp/fq-sros2-install /tmp/fq-sros2-log "
        "/tmp/fq-sros2-keystore /tmp/fq-sros2-verified-smime.txt "
        "/tmp/fq-sros2-verified-permissions.xml "
        "/tmp/fq-sros2-tampered-permissions.p7s "
        "/tmp/fq-sros2-default-governance.p7s "
        "/tmp/fq-sros2-access-governance.p7s "
        "/tmp/fq-sros2-tampered-governance.p7s "
        "/tmp/fq-sros2-tampered-identity-cert.pem "
        "/tmp/fq-sros2-mismatched-identity-key.pem && "
        "colcon --log-base /tmp/fq-sros2-log build --base-paths ros2_ws/src "
        "--packages-select rmw_fleetqox_cpp --build-base /tmp/fq-sros2-build "
        "--install-base /tmp/fq-sros2-install --cmake-args -DCMAKE_BUILD_TYPE=Release "
        ">/dev/null && source /tmp/fq-sros2-install/setup.bash && "
        f"export ROS_DOMAIN_ID={DEFAULT_DOMAIN_ID} && "
        "ros2 security create_keystore /tmp/fq-sros2-keystore >/dev/null && "
        f"cp /tmp/fq-sros2-keystore/enclaves/governance.p7s "
        f"{shlex.quote(default_signed_governance)} && "
        "python3 -c "
        + shlex.quote(
            "from lxml import etree; "
            f"schema=etree.XMLSchema(etree.parse('{governance_xsd}')); "
            "schema.assertValid(etree.parse("
            f"'/work/{DEFAULT_GOVERNANCE_FILE}'))"
        )
        + " && echo governance_xsd_validated=1 && "
        "python3 -c "
        + shlex.quote(
            "from pathlib import Path; from sros2 import _utilities; "
            "_utilities.create_smime_signed_file("
            "Path('/tmp/fq-sros2-keystore/public/permissions_ca.cert.pem'),"
            "Path('/tmp/fq-sros2-keystore/private/permissions_ca.key.pem'),"
            f"Path('/work/{DEFAULT_GOVERNANCE_FILE}'),"
            f"Path('{signed_governance}'))"
        )
        + " && "
        f"openssl smime -verify -in {shlex.quote(signed_governance)} "
        f"-CAfile {shlex.quote(permissions_ca)} -purpose any "
        ">/dev/null 2>&1 && echo signed_governance_verified=1 && "
        "python3 -c "
        + shlex.quote(
            f"source='{signed_governance}'; target='{tampered_signed_governance}'; "
            "data=bytearray(open(source,'rb').read()); "
            "marker=b'rt/fleetqox/governance_uncontrolled'; index=data.find(marker); "
            "assert index >= 0; data[index]=ord('x'); open(target,'wb').write(data)"
        )
        + " && echo tampered_signed_governance_created=1 && "
        f"ros2 security create_enclave /tmp/fq-sros2-keystore "
        f"{shlex.quote(DEFAULT_ENCLAVE)} >/dev/null && "
        f"test -s {shlex.quote(identity_certificate)} && "
        f"test -s {shlex.quote(identity_private_key)} && "
        f"test -s {shlex.quote(identity_ca)} && "
        "python3 -c "
        + shlex.quote(
            f"source='{identity_certificate}'; target='{tampered_identity_certificate}'; "
            "lines=open(source,'r',encoding='ascii').read().splitlines(); "
            "index=next(i for i,line in enumerate(lines) if not line.startswith('---') "
            "and len(line)>20); line=lines[index]; offset=10; "
            "lines[index]=line[:offset]+('B' if line[offset]!='B' else 'C')+line[offset+1:]; "
            "open(target,'w',encoding='ascii').write('\\n'.join(lines)+'\\n')"
        )
        + " && "
        f"openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:prime256v1 "
        f"-out {shlex.quote(mismatched_identity_private_key)} >/dev/null 2>&1 && "
        "echo identity_negative_controls_created=1 && "
        f"ros2 security create_permission /tmp/fq-sros2-keystore "
        f"{shlex.quote(DEFAULT_ENCLAVE)} {shlex.quote('/work/' + policy_file)} >/dev/null && "
        f"test -s {shlex.quote(permissions_xml)} && "
        f"test -s {shlex.quote(signed_permissions)} && "
        "echo sros2_artifacts_generated=1 && "
        f"openssl smime -verify -in {shlex.quote(signed_permissions)} "
        f"-CAfile {shlex.quote(permissions_ca)} -purpose any "
        f"-out {shlex.quote(verified_smime)} >/dev/null 2>&1 && "
        "echo signed_permissions_verified=1 && "
        "python3 -c "
        + shlex.quote(
            "from email import policy; from email.parser import BytesParser; "
            f"message=BytesParser(policy=policy.default).parse(open('{verified_smime}','rb')); "
            "payload=message.get_payload(decode=True); "
            f"open('{verified_permissions}','wb').write(payload)"
        )
        + " && "
        "python3 -c "
        + shlex.quote(
            "from lxml import etree; "
            f"schema=etree.XMLSchema(etree.parse('{permissions_xsd}')); "
            f"schema.assertValid(etree.parse('{verified_permissions}'))"
        )
        + " && echo permissions_xsd_validated=1 && "
        "python3 -c "
        + shlex.quote(
            f"source='{signed_permissions}'; target='{tampered_signed_permissions}'; "
            "data=bytearray(open(source,'rb').read()); "
            "marker=b'rt/fleetqox/sros2_allowed'; index=data.find(marker); "
            "assert index >= 0; data[index]=ord('x'); open(target,'wb').write(data)"
        )
        + " && echo tampered_signed_permissions_created=1 && "
        "unset FLEETQOX_RMW_SROS2_PERMISSIONS_FILE "
        "FLEETQOX_RMW_SROS2_EXPECT_INVALID FLEETQOX_RMW_SROS2_EXPECT_INVALID_KIND && "
        f"export FLEETQOX_RMW_SROS2_PERMISSIONS_P7S_FILE={shlex.quote(signed_permissions)} && "
        f"export FLEETQOX_RMW_SROS2_PERMISSIONS_CA_FILE={shlex.quote(permissions_ca)} && "
        f"export FLEETQOX_RMW_SROS2_GOVERNANCE_P7S_FILE={shlex.quote(signed_governance)} && "
        f"export FLEETQOX_RMW_SROS2_GOVERNANCE_CA_FILE={shlex.quote(permissions_ca)} && "
        f"export FLEETQOX_RMW_SROS2_IDENTITY_CERT_FILE={shlex.quote(identity_certificate)} && "
        f"export FLEETQOX_RMW_SROS2_IDENTITY_KEY_FILE={shlex.quote(identity_private_key)} && "
        f"export FLEETQOX_RMW_SROS2_IDENTITY_CA_FILE={shlex.quote(identity_ca)} && "
        "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
        f"for i in $(seq 1 {run_count}); do "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_identity_probe || exit $?; "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_permissions_probe || exit $?; "
        # Scoped (not exported) to this one invocation: a denied service
        # request/response never gets acknowledged, so the client-side
        # reliability layer's default retry budget (5 retries) resends it
        # several more times, and each retry is independently evaluated
        # and counted as another "denied" decision by the subscriber side
        # -- inflating e.g. request_subscribe_denied_delta from 1 to 3+
        # depending on retry/timing, when this probe's own counters_ok
        # check expects exactly one decision per logical attempt. Disabling
        # retries here (only here) makes the per-attempt decision counters
        # deterministic without affecting the other probes in this loop,
        # which don't depend on retry behavior for their own assertions.
        "FLEETQOX_RMW_SERVICE_REQUEST_REPEATS=0 "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_service_permissions_probe || exit $?; "
        "python3 /work/scripts/sros2_action_permissions_probe.py || exit $?; "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_governance_probe || exit $?; done && "
        f"export FLEETQOX_RMW_SROS2_IDENTITY_CERT_FILE="
        f"{shlex.quote(tampered_identity_certificate)} && "
        "export FLEETQOX_RMW_SROS2_IDENTITY_PROBE_MODE=tampered_certificate && "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_identity_probe && "
        f"export FLEETQOX_RMW_SROS2_IDENTITY_CERT_FILE="
        f"{shlex.quote(identity_certificate)} && "
        f"export FLEETQOX_RMW_SROS2_IDENTITY_KEY_FILE="
        f"{shlex.quote(mismatched_identity_private_key)} && "
        "export FLEETQOX_RMW_SROS2_IDENTITY_PROBE_MODE=private_key_mismatch && "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_identity_probe && "
        f"export FLEETQOX_RMW_SROS2_IDENTITY_KEY_FILE="
        f"{shlex.quote(identity_private_key)} && "
        "export FLEETQOX_RMW_SROS2_IDENTITY_PROBE_MODE=enclave_mismatch && "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_identity_probe && "
        "unset FLEETQOX_RMW_SROS2_IDENTITY_PROBE_MODE && "
        f"export FLEETQOX_RMW_SROS2_GOVERNANCE_P7S_FILE="
        f"{shlex.quote(default_signed_governance)} && "
        "export FLEETQOX_RMW_SROS2_GOVERNANCE_EXPECT_PROTECTION_DENY=1 && "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_governance_probe && "
        "unset FLEETQOX_RMW_SROS2_GOVERNANCE_EXPECT_PROTECTION_DENY && "
        f"export FLEETQOX_RMW_SROS2_GOVERNANCE_P7S_FILE="
        f"{shlex.quote(tampered_signed_governance)} && "
        "export FLEETQOX_RMW_SROS2_GOVERNANCE_EXPECT_TAMPERED=1 && "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_governance_probe && "
        "unset FLEETQOX_RMW_SROS2_GOVERNANCE_EXPECT_TAMPERED && "
        f"export FLEETQOX_RMW_SROS2_GOVERNANCE_P7S_FILE="
        f"{shlex.quote(signed_governance)} && "
        f"export FLEETQOX_RMW_SROS2_PERMISSIONS_P7S_FILE="
        f"{shlex.quote(tampered_signed_permissions)} && "
        "export FLEETQOX_RMW_SROS2_EXPECT_INVALID=1 && "
        "export FLEETQOX_RMW_SROS2_EXPECT_INVALID_KIND=tampered_signature && "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_permissions_probe && "
        "unset FLEETQOX_RMW_SROS2_PERMISSIONS_P7S_FILE "
        "FLEETQOX_RMW_SROS2_PERMISSIONS_CA_FILE && "
        f"export FLEETQOX_RMW_SROS2_PERMISSIONS_FILE="
        f"{shlex.quote('/work/' + DEFAULT_MALFORMED_PERMISSIONS_FILE)} && "
        "export FLEETQOX_RMW_SROS2_EXPECT_INVALID=1 && "
        "export FLEETQOX_RMW_SROS2_EXPECT_INVALID_KIND=malformed_permissions && "
        "/tmp/fq-sros2-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_sros2_permissions_probe"
    )
    completed = subprocess.run(
        [
            "docker", "run", "--rm", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work", image, "-lc", command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    all_rows = parse_json_rows(completed.stdout)
    rows = [
        row for row in all_rows
        if row.get("probe_mode") == "valid_signed_permissions"
    ]
    fail_closed_rows = [
        row for row in all_rows if row.get("probe_mode") == "malformed_fail_closed"
    ]
    tampered_signature_rows = [
        row for row in all_rows
        if row.get("probe_mode") == "tampered_signature_fail_closed"
    ]
    service_rows = [
        row for row in all_rows
        if row.get("schema_version") == "fleetrmw.sros2_service_permissions_probe.v1"
    ]
    action_rows = [
        row for row in all_rows
        if row.get("schema_version") == "fleetrmw.sros2_action_permissions_probe.v1"
    ]
    governance_rows = [
        row for row in all_rows
        if row.get("schema_version") == "fleetrmw.sros2_governance_probe.v1"
        and row.get("probe_mode") == "signed_governance_access_control"
    ]
    governance_protection_rows = [
        row for row in all_rows
        if row.get("schema_version") == "fleetrmw.sros2_governance_probe.v1"
        and row.get("probe_mode") == "transport_protection_fail_closed"
    ]
    tampered_governance_rows = [
        row for row in all_rows
        if row.get("schema_version") == "fleetrmw.sros2_governance_probe.v1"
        and row.get("probe_mode") == "tampered_governance_fail_closed"
    ]
    identity_rows = [
        row for row in all_rows
        if row.get("schema_version") == "fleetrmw.sros2_identity_probe.v1"
        and row.get("probe_mode") == "valid"
    ]
    tampered_identity_rows = [
        row for row in all_rows
        if row.get("schema_version") == "fleetrmw.sros2_identity_probe.v1"
        and row.get("probe_mode") == "tampered_certificate"
    ]
    mismatched_identity_key_rows = [
        row for row in all_rows
        if row.get("schema_version") == "fleetrmw.sros2_identity_probe.v1"
        and row.get("probe_mode") == "private_key_mismatch"
    ]
    mismatched_identity_enclave_rows = [
        row for row in all_rows
        if row.get("schema_version") == "fleetrmw.sros2_identity_probe.v1"
        and row.get("probe_mode") == "enclave_mismatch"
    ]
    markers = parse_key_value_markers(completed.stdout)
    probe = rows[-1] if rows else {}
    pubsub_ok_run_count = sum(1 for row in rows if sros2_permissions_probe_ok(row))
    service_ok_run_count = sum(
        1 for row in service_rows if sros2_service_permissions_probe_ok(row)
    )
    action_ok_run_count = sum(
        1 for row in action_rows if sros2_action_permissions_probe_ok(row)
    )
    governance_ok_run_count = sum(
        1 for row in governance_rows if sros2_governance_probe_ok(row)
    )
    identity_ok_run_count = sum(
        1 for row in identity_rows if sros2_identity_probe_ok(row)
    )
    service_probe = service_rows[-1] if service_rows else {}
    action_probe = action_rows[-1] if action_rows else {}
    governance_probe = governance_rows[-1] if governance_rows else {}
    identity_probe = identity_rows[-1] if identity_rows else {}
    ok_run_count = min(
        pubsub_ok_run_count,
        service_ok_run_count,
        action_ok_run_count,
        governance_ok_run_count,
        identity_ok_run_count,
    )
    fail_closed_control = fail_closed_rows[-1] if fail_closed_rows else {}
    fail_closed_control_ok = malformed_permissions_fail_closed_ok(fail_closed_control)
    tampered_signature_control = (
        tampered_signature_rows[-1] if tampered_signature_rows else {}
    )
    tampered_signature_control_ok = tampered_signature_fail_closed_ok(
        tampered_signature_control
    )
    governance_protection_control = (
        governance_protection_rows[-1] if governance_protection_rows else {}
    )
    governance_protection_control_ok = governance_protection_fail_closed_ok(
        governance_protection_control
    )
    tampered_governance_control = (
        tampered_governance_rows[-1] if tampered_governance_rows else {}
    )
    tampered_governance_control_ok = tampered_governance_fail_closed_ok(
        tampered_governance_control
    )
    tampered_identity_control = (
        tampered_identity_rows[-1] if tampered_identity_rows else {}
    )
    tampered_identity_control_ok = identity_fail_closed_control_ok(
        tampered_identity_control, "tampered_certificate"
    ) if tampered_identity_control else False
    mismatched_identity_key_control = (
        mismatched_identity_key_rows[-1] if mismatched_identity_key_rows else {}
    )
    mismatched_identity_key_control_ok = identity_fail_closed_control_ok(
        mismatched_identity_key_control, "private_key_mismatch"
    ) if mismatched_identity_key_control else False
    mismatched_identity_enclave_control = (
        mismatched_identity_enclave_rows[-1]
        if mismatched_identity_enclave_rows else {}
    )
    mismatched_identity_enclave_control_ok = identity_fail_closed_control_ok(
        mismatched_identity_enclave_control, "enclave_mismatch"
    ) if mismatched_identity_enclave_control else False
    sros2_artifacts_generated = markers.get("sros2_artifacts_generated") == "1"
    signed_permissions_verified = markers.get("signed_permissions_verified") == "1"
    permissions_xsd_validated = markers.get("permissions_xsd_validated") == "1"
    tampered_signed_permissions_created = (
        markers.get("tampered_signed_permissions_created") == "1"
    )
    governance_xsd_validated = markers.get("governance_xsd_validated") == "1"
    signed_governance_verified = markers.get("signed_governance_verified") == "1"
    tampered_signed_governance_created = (
        markers.get("tampered_signed_governance_created") == "1"
    )
    identity_negative_controls_created = (
        markers.get("identity_negative_controls_created") == "1"
    )
    ok = (
        completed.returncode == 0
        and len(rows) == run_count
        and pubsub_ok_run_count == run_count
        and len(service_rows) == run_count
        and service_ok_run_count == run_count
        and len(action_rows) == run_count
        and action_ok_run_count == run_count
        and len(governance_rows) == run_count
        and governance_ok_run_count == run_count
        and len(identity_rows) == run_count
        and identity_ok_run_count == run_count
        and sros2_artifacts_generated
        and signed_permissions_verified
        and permissions_xsd_validated
        and tampered_signed_permissions_created
        and governance_xsd_validated
        and signed_governance_verified
        and tampered_signed_governance_created
        and identity_negative_controls_created
        and len(fail_closed_rows) == 1
        and fail_closed_control_ok
        and len(tampered_signature_rows) == 1
        and tampered_signature_control_ok
        and len(governance_protection_rows) == 1
        and governance_protection_control_ok
        and len(tampered_governance_rows) == 1
        and tampered_governance_control_ok
        and len(tampered_identity_rows) == 1
        and tampered_identity_control_ok
        and len(mismatched_identity_key_rows) == 1
        and mismatched_identity_key_control_ok
        and len(mismatched_identity_enclave_rows) == 1
        and mismatched_identity_enclave_control_ok
    )
    # sros2_identity_probe/sros2_permissions_probe never authenticate a
    # remote peer -- they validate the local enclave's own credentials and
    # the permissions.xml ACL, nothing more. Real UDP peer authentication
    # (sign/verify against the SROS2 identity CA, including CRL-based
    # revocation) is a separate, already-implemented and already-passing
    # mechanism exercised end-to-end by run_rmw_docker_udp_peer_auth_probe.
    # Run it here and fold its real, computed evidence in rather than
    # leaving these claims hardcoded to False.
    try:
        peer_auth_result = run_udp_peer_auth_probe(root=root, image=image)
    except Exception as exc:  # noqa: BLE001 - report as unmet evidence, don't crash this probe
        peer_auth_result = {"status": "failed", "error": str(exc)}
    peer_auth_ok = peer_auth_result.get("status") == "ok"
    peer_identity_authentication_claim = bool(
        peer_auth_ok
        and peer_auth_result.get("sros2_peer_identity_authentication_claim") is True
    )
    certificate_revocation_claim = bool(
        peer_auth_ok
        and peer_auth_result.get("certificate_revocation_claim") is True
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "image": image,
        "returncode": completed.returncode,
        "run_count": run_count,
        "ok_run_count": ok_run_count,
        "pubsub_ok_run_count": pubsub_ok_run_count,
        "service_ok_run_count": service_ok_run_count,
        "action_ok_run_count": action_ok_run_count,
        "governance_ok_run_count": governance_ok_run_count,
        "identity_ok_run_count": identity_ok_run_count,
        "policy_file": policy_file,
        "enclave": DEFAULT_ENCLAVE,
        "domain_id": DEFAULT_DOMAIN_ID,
        "sros2_cli_generated_artifacts": sros2_artifacts_generated,
        "signed_permissions_verified_preflight": signed_permissions_verified,
        "permissions_xsd_validated": permissions_xsd_validated,
        "governance_xsd_validated": governance_xsd_validated,
        "signed_governance_verified_preflight": signed_governance_verified,
        "tampered_signed_governance_created": tampered_signed_governance_created,
        "identity_negative_controls_created": identity_negative_controls_created,
        "runtime_permissions_signature_validation": ok,
        "runtime_sros2_permissions_signature_validation_claim": ok,
        "tampered_signed_permissions_created": tampered_signed_permissions_created,
        "tampered_signed_permissions_fail_closed_control": (
            tampered_signature_control_ok
        ),
        "malformed_permissions_fail_closed_control": fail_closed_control_ok,
        "permissions_xml_loaded": bool(probe.get("permissions_xml_loaded")),
        "allowed_publish_returncode": probe.get("allowed_publish_returncode"),
        "allowed_taken": probe.get("allowed_taken"),
        "explicit_denied_publish_returncode": probe.get(
            "explicit_denied_publish_returncode"
        ),
        "explicit_denied_taken": probe.get("explicit_denied_taken"),
        "default_denied_publish_returncode": probe.get(
            "default_denied_publish_returncode"
        ),
        "default_denied_taken": probe.get("default_denied_taken"),
        "subscribe_denied_publish_returncode": probe.get(
            "subscribe_denied_publish_returncode"
        ),
        "subscribe_default_denied_publish_returncode": probe.get(
            "subscribe_default_denied_publish_returncode"
        ),
        "subscribe_decisions_ready": probe.get("subscribe_decisions_ready"),
        "subscribe_denied_taken": probe.get("subscribe_denied_taken"),
        "subscribe_default_denied_taken": probe.get(
            "subscribe_default_denied_taken"
        ),
        "security_policy_denied_delta": probe.get("security_policy_denied_delta"),
        "sros2_permissions_xml_allowed_delta": probe.get(
            "sros2_permissions_xml_allowed_delta"
        ),
        "sros2_permissions_xml_denied_delta": probe.get(
            "sros2_permissions_xml_denied_delta"
        ),
        "sros2_permissions_xml_parse_errors_delta": probe.get(
            "sros2_permissions_xml_parse_errors_delta"
        ),
        "sros2_permissions_xml_subscribe_allowed_delta": probe.get(
            "sros2_permissions_xml_subscribe_allowed_delta"
        ),
        "sros2_permissions_xml_subscribe_denied_delta": probe.get(
            "sros2_permissions_xml_subscribe_denied_delta"
        ),
        "security_policy_enforcement_executed": ok,
        "sros2_service_request_reply_authorization_claim": ok,
        "sros2_service_repeated_authorization_claim": ok and run_count >= 5,
        "sros2_action_authorization_claim": ok,
        "sros2_action_repeated_authorization_claim": ok and run_count >= 5,
        "sros2_action_allowed_end_to_end_claim": action_probe.get(
            "sros2_action_allowed_end_to_end_claim"
        ),
        "sros2_action_call_denied_fail_closed_claim": action_probe.get(
            "sros2_action_call_denied_fail_closed_claim"
        ),
        "sros2_action_execute_denied_fail_closed_claim": action_probe.get(
            "sros2_action_execute_denied_fail_closed_claim"
        ),
        "sros2_action_call_execute_decision_matrix_claim": action_probe.get(
            "action_call_execute_decision_matrix_claim"
        ),
        "sros2_action_authorization_metrics_claim": action_probe.get(
            "sros2_action_authorization_metrics_claim"
        ),
        "sros2_governance_access_control_claim": ok,
        "sros2_governance_repeated_access_control_claim": ok and run_count >= 5,
        "sros2_governance_runtime_signature_validation_claim": ok,
        "sros2_governance_transport_protection_fail_closed_claim": (
            governance_protection_control_ok
        ),
        "sros2_tampered_signed_governance_fail_closed_claim": (
            tampered_governance_control_ok
        ),
        "sros2_local_identity_credentials_validation_claim": ok,
        "sros2_local_identity_credentials_repeated_validation_claim": (
            ok and run_count >= 5
        ),
        "sros2_tampered_identity_certificate_fail_closed_claim": (
            tampered_identity_control_ok
        ),
        "sros2_identity_private_key_mismatch_fail_closed_claim": (
            mismatched_identity_key_control_ok
        ),
        "sros2_identity_enclave_mismatch_fail_closed_claim": (
            mismatched_identity_enclave_control_ok
        ),
        "sros2_peer_identity_authentication_claim": (
            peer_identity_authentication_claim
        ),
        "certificate_revocation_claim": certificate_revocation_claim,
        "udp_peer_auth_probe": peer_auth_result,
        "governance_uncontrolled_publish_returncode": governance_probe.get(
            "uncontrolled_publish_returncode"
        ),
        "governance_uncontrolled_taken": governance_probe.get(
            "uncontrolled_taken"
        ),
        "action_call_denied_request_publish_denied_delta": action_probe.get(
            "call_denied_request_publish_denied_delta"
        ),
        "action_execute_denied_request_subscribe_denied_delta": action_probe.get(
            "execute_denied_request_subscribe_denied_delta"
        ),
        "action_authorization_metric_deltas": action_probe.get(
            "authorization_metric_deltas", {}
        ),
        "allowed_service_request_returncode": service_probe.get(
            "allowed_send_request_returncode"
        ),
        "allowed_service_request_taken": service_probe.get("allowed_request_taken"),
        "allowed_service_response_returncode": service_probe.get(
            "allowed_send_response_returncode"
        ),
        "allowed_service_response_taken": service_probe.get("allowed_response_taken"),
        "request_denied_service_returncode": service_probe.get(
            "request_denied_send_returncode"
        ),
        "default_denied_service_returncode": service_probe.get(
            "default_denied_send_returncode"
        ),
        "reply_denied_service_request_taken": service_probe.get(
            "response_denied_request_taken"
        ),
        "reply_denied_service_response_returncode": service_probe.get(
            "response_denied_send_response_returncode"
        ),
        "service_request_publish_allowed_delta": service_probe.get(
            "service_request_publish_allowed_delta"
        ),
        "service_request_publish_denied_delta": service_probe.get(
            "service_request_publish_denied_delta"
        ),
        "service_request_subscribe_allowed_delta": service_probe.get(
            "service_request_subscribe_allowed_delta"
        ),
        "service_request_subscribe_denied_delta": service_probe.get(
            "service_request_subscribe_denied_delta"
        ),
        "service_response_publish_allowed_delta": service_probe.get(
            "service_response_publish_allowed_delta"
        ),
        "service_response_publish_denied_delta": service_probe.get(
            "service_response_publish_denied_delta"
        ),
        "service_response_subscribe_allowed_delta": service_probe.get(
            "service_response_subscribe_allowed_delta"
        ),
        "service_response_subscribe_denied_delta": service_probe.get(
            "service_response_subscribe_denied_delta"
        ),
        "sros2_permissions_xml_publish_enforcement_claim": ok,
        "sros2_permissions_xml_subscribe_enforcement_claim": ok,
        "sros2_permissions_xml_pubsub_enforcement_claim": ok,
        "sros2_permissions_xml_repeated_enforcement_claim": ok and run_count >= 5,
        "sros2_permissions_xml_subscribe_repeated_enforcement_claim": (
            ok and run_count >= 5
        ),
        "malformed_permissions_fail_closed_claim": fail_closed_control_ok,
        "tampered_signed_permissions_fail_closed_claim": (
            tampered_signature_control_ok
        ),
        "sros2_permissions_xml_scope": (
            "sros2_generated_signed_permissions_runtime_ca_validation_then_"
            "grant_enclave_domain_validity_publish_subscribe_service_action_"
            "default_runtime_enforcement"
        ),
        "sros2_policy_enforcement_scope": (
            "signed_permissions_and_governance_access_control_subset"
        ),
        "sros2_policy_enforcement_claim": False,
        "governance_xml_enforcement_claim": ok,
        # Real (not hardcoded) peer authentication + CRL-based revocation
        # evidence is folded in above via udp_peer_auth_probe. What remains
        # unimplemented -- and unrelated to peer auth -- is (a) wiring
        # governance.xml's transport-protection requirement to actually
        # enable AEAD/peer-auth for the affected topics/domains (today it
        # only fail-closed denies the operation) and (b) forward secrecy /
        # session-key establishment, matching the same distinction already
        # drawn in run_rmw_docker_stress_security_campaign.py.
        "governance_transport_security_claim": False,
        "production_security_hardening_claim": False,
        "security_policy_enforcement_gap_reason": (
            "forward_secret_key_exchange_and_transport_hardening_not_implemented"
            if peer_identity_authentication_claim
            else "peer_auth_transport_revocation_hardening_not_implemented"
        ),
        "probe": probe,
        "runs": rows,
        "service_runs": service_rows,
        "service_probe": service_probe,
        "action_runs": action_rows,
        "action_probe": action_probe,
        "governance_runs": governance_rows,
        "governance_probe": governance_probe,
        "governance_protection_control": governance_protection_control,
        "tampered_governance_control": tampered_governance_control,
        "identity_runs": identity_rows,
        "identity_probe": identity_probe,
        "tampered_identity_control": tampered_identity_control,
        "mismatched_identity_key_control": mismatched_identity_key_control,
        "mismatched_identity_enclave_control": mismatched_identity_enclave_control,
        "fail_closed_control": fail_closed_control,
        "tampered_signature_control": tampered_signature_control,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--policy-file", default=DEFAULT_POLICY_FILE)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_sros2_permissions_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        iterations=args.iterations,
        policy_file=args.policy_file,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} ok_runs={summary.get('ok_run_count', 0)}/"
            f"{summary.get('run_count', 0)} generated="
            f"{summary.get('sros2_cli_generated_artifacts', False)} verified="
            f"{summary.get('signed_permissions_verified_preflight', False)}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
