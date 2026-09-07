#!/usr/bin/env python3
"""Stateful FleetRMW QUIC v1 / HTTP/3 gateway service."""

from __future__ import annotations

import argparse
import asyncio
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import ssl
from typing import Any
from urllib.parse import urlsplit

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import DataReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ConnectionTerminated, ProtocolNegotiated, QuicEvent
from cryptography.x509.oid import NameOID
from cryptography import x509

from fleetqox.aioquic_mtls_adapter import (
    install_aioquic_mtls_adapter,
    require_aioquic_mtls_compatibility,
)
from fleetqox.aioquic_path_observer import (
    AioquicPathObserver,
    install_aioquic_path_observer,
    require_aioquic_path_observer_compatibility,
)
from fleetqox.quic_gateway_lease import acquire_gateway_state_with_lease_wait
from fleetqox.raft_writer_lease import RaftLeaderLease, RaftLeaseError
from fleetqox.quic_gateway_state import (
    APPLICATION_OUTCOME_API_PATH,
    FrameValidationError,
    GATEWAY_API_PATH,
    FleetQoxGatewayState,
    GatewayAdmissionPolicy,
    GatewayResponse,
    parse_data_frame,
)


SCHEMA_VERSION = "fleetrmw.quic_gateway_service.v1"


def load_revoked_client_serials(client_ca: str, client_crl: str) -> frozenset[int]:
    """Load a current, CA-signed client CRL or fail service startup."""

    ca_certificate = x509.load_pem_x509_certificate(Path(client_ca).read_bytes())
    revocation_list = x509.load_pem_x509_crl(Path(client_crl).read_bytes())
    if revocation_list.issuer != ca_certificate.subject:
        raise ValueError("client CRL issuer does not match the configured client CA")
    if not revocation_list.is_signature_valid(ca_certificate.public_key()):
        raise ValueError("client CRL signature validation failed")
    now = datetime.now(timezone.utc)
    last_update = revocation_list.last_update.replace(tzinfo=timezone.utc)
    next_update = revocation_list.next_update
    if next_update is None:
        raise ValueError("client CRL requires nextUpdate")
    next_update = next_update.replace(tzinfo=timezone.utc)
    if now < last_update or now > next_update:
        raise ValueError("client CRL is outside its validity window")
    return frozenset(entry.serial_number for entry in revocation_list)


@dataclass
class ServiceTelemetry:
    connections_created: int = 0
    h3_sessions_negotiated: int = 0
    connections_terminated: int = 0
    client_certificates_accepted: int = 0
    missing_client_certificates_rejected: int = 0
    untrusted_client_certificates_rejected: int = 0
    revoked_client_certificates_rejected: int = 0
    publisher_identity_authorization_rejected: int = 0
    application_outcome_identity_authorization_rejected: int = 0
    malformed_h3_requests_rejected: int = 0
    client_crl_reload_failures: int = 0
    mtls_private_adapter_installs: int = 0
    native_path_observer_installs: int = 0
    native_path_observation_updates: int = 0
    native_path_samples_unavailable: int = 0
    native_path_packets_sent: int = 0
    native_path_packets_lost: int = 0
    native_path_latest_rtt_ms: float = 0.0
    native_path_latest_rtt_variation_ms: float = 0.0
    native_path_latest_loss: float = 0.0
    native_qoe_debt_updates: int = 0
    native_qoe_latest_debt: float = 0.0

    def snapshot(self) -> dict[str, int | float]:
        return {
            "connections_created": self.connections_created,
            "h3_sessions_negotiated": self.h3_sessions_negotiated,
            "connections_terminated": self.connections_terminated,
            "client_certificates_accepted": self.client_certificates_accepted,
            "missing_client_certificates_rejected": self.missing_client_certificates_rejected,
            "untrusted_client_certificates_rejected": self.untrusted_client_certificates_rejected,
            "revoked_client_certificates_rejected": self.revoked_client_certificates_rejected,
            "publisher_identity_authorization_rejected": self.publisher_identity_authorization_rejected,
            "application_outcome_identity_authorization_rejected": (
                self.application_outcome_identity_authorization_rejected
            ),
            "malformed_h3_requests_rejected": self.malformed_h3_requests_rejected,
            "client_crl_reload_failures": self.client_crl_reload_failures,
            "mtls_private_adapter_installs": self.mtls_private_adapter_installs,
            "native_path_observer_installs": self.native_path_observer_installs,
            "native_path_observation_updates": self.native_path_observation_updates,
            "native_path_samples_unavailable": self.native_path_samples_unavailable,
            "native_path_packets_sent": self.native_path_packets_sent,
            "native_path_packets_lost": self.native_path_packets_lost,
            "native_path_latest_rtt_ms": self.native_path_latest_rtt_ms,
            "native_path_latest_rtt_variation_ms": (
                self.native_path_latest_rtt_variation_ms
            ),
            "native_path_latest_loss": self.native_path_latest_loss,
            "native_qoe_debt_updates": self.native_qoe_debt_updates,
            "native_qoe_latest_debt": self.native_qoe_latest_debt,
        }


class FleetQoxGatewayProtocol(QuicConnectionProtocol):
    def __init__(
        self,
        *args: Any,
        gateway_state: FleetQoxGatewayState,
        service_telemetry: ServiceTelemetry,
        require_client_certificate: bool,
        client_ca: str | None,
        revoked_client_serials: frozenset[int],
        client_crl_reload_failed: bool = False,
        bind_client_cn_to_publisher_id: bool,
        publisher_identity_uri_prefix: str | None,
        native_path_observations: bool,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.gateway_state = gateway_state
        self.service_telemetry = service_telemetry
        self.require_client_certificate = require_client_certificate
        self.client_authenticated = not require_client_certificate
        self.client_identity = ""
        self.bind_client_cn_to_publisher_id = bind_client_cn_to_publisher_id
        self.publisher_identity_uri_prefix = publisher_identity_uri_prefix or ""
        self.bind_client_identity_to_publisher_id = bool(
            bind_client_cn_to_publisher_id or publisher_identity_uri_prefix
        )
        self.http: H3Connection | None = None
        self.requests: dict[int, dict[str, Any]] = {}
        self.completed_request_streams: OrderedDict[int, None] = OrderedDict()
        self.h3_negotiated = False
        self.native_path_observer: AioquicPathObserver | None = None
        self._native_packets_sent_recorded = 0
        self._native_packets_lost_recorded = 0
        if require_client_certificate:
            if not client_ca:
                raise ValueError("client CA is required for mutual TLS")
            if client_crl_reload_failed:
                # Fail closed: a CRL that cannot currently be read/validated
                # means revocation status is unknown for this connection, so
                # no client certificate is authenticated. self.client_authenticated
                # already defaults to False above and every request is gated
                # on it, so simply not installing the adapter is sufficient.
                self.service_telemetry.client_crl_reload_failures += 1
            else:
                install_aioquic_mtls_adapter(
                    self._quic,
                    client_ca=client_ca,
                    revoked_client_serials=revoked_client_serials,
                    on_missing_certificate=self._on_missing_client_certificate,
                    on_untrusted_certificate=self._on_untrusted_client_certificate,
                    on_revoked_certificate=self._on_revoked_client_certificate,
                    on_authenticated_certificate=self._on_authenticated_client_certificate,
                )
                self.service_telemetry.mtls_private_adapter_installs += 1
        if native_path_observations:
            self.native_path_observer = install_aioquic_path_observer(self._quic)
            self.service_telemetry.native_path_observer_installs += 1

    def _on_missing_client_certificate(self) -> None:
        self.service_telemetry.missing_client_certificates_rejected += 1

    def _on_untrusted_client_certificate(self) -> None:
        self.service_telemetry.untrusted_client_certificates_rejected += 1

    def _on_revoked_client_certificate(self) -> None:
        self.service_telemetry.revoked_client_certificates_rejected += 1

    def _on_authenticated_client_certificate(self, certificate: x509.Certificate) -> None:
        if self.publisher_identity_uri_prefix:
            try:
                alternative_names = certificate.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value
                uri_names = alternative_names.get_values_for_type(
                    x509.UniformResourceIdentifier
                )
            except x509.ExtensionNotFound:
                uri_names = []
            identities = [
                value[len(self.publisher_identity_uri_prefix) :]
                for value in uri_names
                if value.startswith(self.publisher_identity_uri_prefix)
                and len(value) > len(self.publisher_identity_uri_prefix)
            ]
            self.client_identity = identities[0] if len(identities) == 1 else ""
        else:
            common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            self.client_identity = (
                common_names[0].value
                if len(common_names) == 1 and common_names[0].value
                else ""
            )
        self.service_telemetry.client_certificates_accepted += 1
        self.client_authenticated = True

    def _native_path_snapshot(self) -> dict[str, Any] | None:
        if self.native_path_observer is None:
            return None
        sample = self.native_path_observer.snapshot()
        packets_sent = int(sample["packets_sent"])
        packets_lost = int(sample["packets_lost"])
        self.service_telemetry.native_path_packets_sent += max(
            0, packets_sent - self._native_packets_sent_recorded
        )
        self.service_telemetry.native_path_packets_lost += max(
            0, packets_lost - self._native_packets_lost_recorded
        )
        self._native_packets_sent_recorded = packets_sent
        self._native_packets_lost_recorded = packets_lost
        if sample["rtt_initialized"]:
            self.service_telemetry.native_path_latest_rtt_ms = float(
                sample["measured_rtt_ms"]
            )
            self.service_telemetry.native_path_latest_rtt_variation_ms = float(
                sample["measured_jitter_ms"]
            )
            self.service_telemetry.native_path_latest_loss = float(
                sample["measured_loss"]
            )
        return sample

    def _publish_native_path_observation(self, metadata: Any) -> None:
        sample = self._native_path_snapshot()
        if sample is None or self.gateway_state.admission_policy is None:
            return
        if not sample["rtt_initialized"]:
            self.service_telemetry.native_path_samples_unavailable += 1
            return
        policy = self.gateway_state.admission_policy
        derived_debt = policy.update_native_path_observation(
            metadata=metadata,
            measured_loss=float(sample["measured_loss"]),
            measured_rtt_ms=float(sample["measured_rtt_ms"]),
            measured_jitter_ms=float(sample["measured_jitter_ms"]),
        )
        if policy.native_qoe_debt_enabled:
            self.service_telemetry.native_qoe_debt_updates += 1
            self.service_telemetry.native_qoe_latest_debt = derived_debt
        self.service_telemetry.native_path_observation_updates += 1

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, ProtocolNegotiated) and event.alpn_protocol in H3_ALPN:
            self.http = H3Connection(self._quic)
            if not self.h3_negotiated:
                self.h3_negotiated = True
                self.service_telemetry.h3_sessions_negotiated += 1
        if isinstance(event, ConnectionTerminated):
            self.service_telemetry.connections_terminated += 1
            self._native_path_snapshot()
            if self.native_path_observer is not None:
                self.native_path_observer.close()
        if self.http is None or (
            self.require_client_certificate and not self.client_authenticated
        ):
            return
        for http_event in self.http.handle_event(event):
            self.http_event_received(http_event)

    def http_event_received(self, event: HeadersReceived | DataReceived) -> None:
        if event.stream_id in self.completed_request_streams:
            return
        if isinstance(event, HeadersReceived):
            request = self.requests.setdefault(
                event.stream_id, {"headers": {}, "body": bytearray()}
            )
            request["headers"].update(
                {
                    name.decode("ascii"): value.decode("utf-8", errors="replace")
                    for name, value in event.headers
                }
            )
            if event.stream_ended:
                self.dispatch(event.stream_id)
        elif isinstance(event, DataReceived):
            request = self.requests.setdefault(
                event.stream_id, {"headers": {}, "body": bytearray()}
            )
            request["body"].extend(event.data)
            if len(request["body"]) > self.gateway_state.max_frame_bytes:
                self.respond(
                    event.stream_id,
                    GatewayResponse(
                        status=413,
                        body=b'{"error":"request_body_too_large"}',
                    ),
                )
                self.requests.pop(event.stream_id, None)
                self._mark_request_stream_completed(event.stream_id)
            elif event.stream_ended:
                self.dispatch(event.stream_id)

    def _mark_request_stream_completed(self, stream_id: int) -> None:
        self.completed_request_streams[stream_id] = None
        self.completed_request_streams.move_to_end(stream_id)
        while len(self.completed_request_streams) > 4096:
            self.completed_request_streams.popitem(last=False)

    def dispatch(self, stream_id: int) -> None:
        request = self.requests.pop(stream_id, None)
        if request is None:
            return
        self._mark_request_stream_completed(stream_id)
        method = request["headers"].get(":method", "")
        path = request["headers"].get(":path", "")
        if not method or not path:
            self.service_telemetry.malformed_h3_requests_rejected += 1
            self.respond(
                stream_id,
                GatewayResponse(status=400, body=b'{"error":"missing_pseudo_headers"}'),
            )
            return
        metadata = None
        if method == "POST" and urlsplit(path).path == GATEWAY_API_PATH:
            try:
                metadata = parse_data_frame(
                    bytes(request["body"]),
                    max_frame_bytes=self.gateway_state.max_frame_bytes,
                )
            except FrameValidationError:
                # Preserve the state engine's normal invalid-frame accounting.
                pass
            else:
                if self.bind_client_identity_to_publisher_id and (
                    not self.client_identity
                    or metadata.publisher_id != self.client_identity
                ):
                    self.service_telemetry.publisher_identity_authorization_rejected += 1
                    self.respond(
                        stream_id,
                        GatewayResponse(
                            status=403,
                            body=b'{"error":"publisher_identity_mismatch"}',
                        ),
                    )
                    return
                self._publish_native_path_observation(metadata)
        if method == "POST" and urlsplit(path).path == APPLICATION_OUTCOME_API_PATH:
            try:
                outcome_document = json.loads(bytes(request["body"]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                outcome_document = None
            outcome_publisher = (
                outcome_document.get("publisher_id")
                if isinstance(outcome_document, dict)
                else None
            )
            if self.bind_client_identity_to_publisher_id and (
                not self.client_identity
                or not isinstance(outcome_publisher, str)
                or outcome_publisher != self.client_identity
            ):
                self.service_telemetry.publisher_identity_authorization_rejected += 1
                self.service_telemetry.application_outcome_identity_authorization_rejected += 1
                self.respond(
                    stream_id,
                    GatewayResponse(
                        status=403,
                        body=b'{"error":"application_outcome_identity_mismatch"}',
                    ),
                )
                return
        response = self.gateway_state.handle_request(
            method, path, bytes(request["body"])
        )
        self.respond(stream_id, response)

    def respond(self, stream_id: int, response: GatewayResponse) -> None:
        if self.http is None:
            return
        headers = [
            (b":status", str(response.status).encode("ascii")),
            (b"server", b"fleetrmw-quic-gateway/1"),
            (b"content-length", str(len(response.body)).encode("ascii")),
        ]
        if response.content_type:
            headers.append((b"content-type", response.content_type.encode("ascii")))
        self.http.send_headers(
            stream_id=stream_id,
            headers=headers,
            end_stream=not response.body,
        )
        if response.body:
            self.http.send_data(
                stream_id=stream_id, data=response.body, end_stream=True
            )
        self.transmit()


async def run_service(args: argparse.Namespace) -> int:
    mtls_adapter = (
        require_aioquic_mtls_compatibility()
        if args.require_client_certificate
        else {
            "adapter_mode": "disabled",
            "compatible": None,
            "public_server_client_auth_api": False,
            "production_supported": False,
        }
    )
    path_observer_adapter = (
        require_aioquic_path_observer_compatibility()
        if args.native_path_observations
        else {
            "adapter_mode": "disabled",
            "compatible": None,
            "public_path_metrics_api": False,
            "production_supported": False,
        }
    )
    admission_policy = None
    if args.admission_policy:
        admission_document = json.loads(Path(args.admission_policy).read_text())
        if not isinstance(admission_document, dict):
            raise ValueError("gateway admission policy must be a JSON object")
        admission_policy = GatewayAdmissionPolicy.from_document(admission_document)
        if admission_policy.native_qoe_debt_enabled:
            if not args.native_path_observations:
                raise ValueError(
                    "native QoE debt requires --native-path-observations"
                )
            if not args.require_client_certificate:
                raise ValueError("native QoE debt requires mutual TLS client auth")
            if not (
                args.bind_client_cn_to_publisher_id
                or args.publisher_identity_uri_prefix
            ):
                raise ValueError(
                    "native QoE debt requires certificate publisher identity binding"
                )
        if admission_policy.application_outcome_qoe_debt_enabled:
            if not args.require_client_certificate:
                raise ValueError(
                    "application outcome QoE debt requires mutual TLS client auth"
                )
            if not (
                args.bind_client_cn_to_publisher_id
                or args.publisher_identity_uri_prefix
            ):
                raise ValueError(
                    "application outcome QoE debt requires certificate publisher "
                    "identity binding"
                )
    # When --raft-status-url is set, this gateway's write eligibility comes
    # from winning leadership of its own co-located fleetqox.raft cluster
    # instead of from a fixed, operator-assigned --writer-lease-instance-id.
    # The current Raft term (strictly increasing across every leadership
    # change) becomes part of the holder_id handed to the existing SQL-based
    # durable store below, so a term change still produces a fresh
    # fence_token there exactly the way a new static instance id would --
    # Raft supplies the leadership *decision*, the already-proven SQL store
    # still enforces it at write time. See fleetqox/raft_writer_lease.py.
    raft_lease: RaftLeaderLease | None = None
    raft_term: int | None = None
    writer_lease_instance_id = args.writer_lease_instance_id
    if args.raft_status_url:
        raft_lease = RaftLeaderLease(status_url=args.raft_status_url)

        def report_raft_wait() -> None:
            print(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "status": "raft_leadership_waiting",
                        "raft_node_id": args.raft_node_id,
                        "timeout_ms": args.raft_writer_lease_wait_timeout_ms,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

        try:
            raft_term = await raft_lease.require_leadership(
                wait_timeout_ms=args.raft_writer_lease_wait_timeout_ms,
                retry_ms=args.raft_writer_lease_retry_ms,
                on_wait=report_raft_wait,
            )
        except RaftLeaseError as exc:
            print(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "status": "raft_leadership_failed",
                        "raft_node_id": args.raft_node_id,
                        "error": str(exc),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 1
        writer_lease_instance_id = f"raft-{args.raft_node_id}-term-{raft_term}"

    def create_state() -> FleetQoxGatewayState:
        return FleetQoxGatewayState(
            max_frames_per_topic=args.max_frames_per_topic,
            max_frame_bytes=args.max_frame_bytes,
            dedup_capacity_per_topic=args.dedup_capacity_per_topic,
            admission_policy=admission_policy,
            durable_state_path=args.state_db,
            durable_writer_id=writer_lease_instance_id,
            durable_writer_lease_ms=args.writer_lease_ms,
        )

    def report_lease_wait() -> None:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "writer_lease_waiting",
                    "instance_id": writer_lease_instance_id,
                    "timeout_ms": args.writer_lease_wait_timeout_ms,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    state, lease_acquisition = await acquire_gateway_state_with_lease_wait(
        factory=create_state,
        wait_timeout_ms=args.writer_lease_wait_timeout_ms,
        retry_ms=args.writer_lease_retry_ms,
        on_wait=report_lease_wait,
    )
    service_telemetry = ServiceTelemetry()
    if args.client_crl:
        # Fail fast on an obviously misconfigured CRL/CA pair at startup;
        # each connection also reloads it fresh below so a serial revoked
        # after startup takes effect without a restart.
        load_revoked_client_serials(args.client_ca, args.client_crl)
    configuration = QuicConfiguration(
        is_client=False,
        alpn_protocols=H3_ALPN,
        max_datagram_frame_size=None,
    )
    configuration.load_cert_chain(args.certificate, args.private_key)
    if args.require_client_certificate:
        configuration.verify_mode = ssl.CERT_REQUIRED
        configuration.load_verify_locations(cafile=args.client_ca)
    if args.qlog_dir:
        from aioquic.quic.logger import QuicFileLogger

        configuration.quic_logger = QuicFileLogger(args.qlog_dir)
    def create_protocol(*protocol_args: Any, **protocol_kwargs: Any) -> FleetQoxGatewayProtocol:
        service_telemetry.connections_created += 1
        # Reload the CRL from disk for every new connection rather than once
        # at service startup, so a serial revoked after the service started
        # takes effect on the very next connection without a restart.
        revoked_client_serials: frozenset[int] = frozenset()
        client_crl_reload_failed = False
        if args.client_crl:
            try:
                revoked_client_serials = load_revoked_client_serials(
                    args.client_ca, args.client_crl)
            except Exception:
                client_crl_reload_failed = True
        return FleetQoxGatewayProtocol(
            *protocol_args,
            gateway_state=state,
            service_telemetry=service_telemetry,
            require_client_certificate=args.require_client_certificate,
            client_ca=args.client_ca,
            revoked_client_serials=revoked_client_serials,
            client_crl_reload_failed=client_crl_reload_failed,
            bind_client_cn_to_publisher_id=args.bind_client_cn_to_publisher_id,
            publisher_identity_uri_prefix=args.publisher_identity_uri_prefix,
            native_path_observations=args.native_path_observations,
            **protocol_kwargs,
        )

    server = await serve(
        args.host,
        args.port,
        configuration=configuration,
        create_protocol=create_protocol,
    )
    stopped = asyncio.Event()
    lease_task: asyncio.Task[None] | None = None
    writer_lease_lost = False
    if writer_lease_instance_id:
        async def renew_writer_lease() -> None:
            nonlocal writer_lease_lost
            interval_seconds = max(0.05, args.writer_lease_ms / 3000.0)
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    state.renew_writer_lease()
                    # The SQL-side TTL alone can't see a Raft leadership
                    # change until it actually causes a write failure --
                    # checking Raft directly here means a demoted node
                    # stops on its OWN next renewal tick instead of only
                    # once some other node has already raced it for the
                    # SQL row.
                    if raft_lease is not None and not await raft_lease.is_still_leader_async(
                        expected_term=raft_term
                    ):
                        raise RaftLeaseError(
                            f"no longer the Raft leader for term {raft_term}"
                        )
                except Exception as exc:
                    writer_lease_lost = True
                    print(
                        json.dumps(
                            {
                                "schema_version": SCHEMA_VERSION,
                                "status": "writer_lease_lost",
                                "instance_id": writer_lease_instance_id,
                                "error": str(exc),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    stopped.set()
                    return

        lease_task = asyncio.create_task(renew_writer_lease())
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "ready",
                "host": args.host,
                "port": args.port,
                "alpn": "h3",
                "stateful": True,
                "tls": True,
                "client_certificate_required": args.require_client_certificate,
                "publisher_identity_binding": bool(
                    args.bind_client_cn_to_publisher_id
                    or args.publisher_identity_uri_prefix
                ),
                "publisher_identity_source": (
                    "uri_san"
                    if args.publisher_identity_uri_prefix
                    else "common_name"
                    if args.bind_client_cn_to_publisher_id
                    else "disabled"
                ),
                "client_crl_configured": bool(args.client_crl),
                "mtls_adapter": mtls_adapter,
                "path_observer_adapter": path_observer_adapter,
                "admission_policy_configured": admission_policy is not None,
                "native_path_observations_configured": (
                    args.native_path_observations
                ),
                "native_qoe_debt_configured": bool(
                    admission_policy is not None
                    and admission_policy.native_qoe_debt_enabled
                ),
                "application_outcome_qoe_debt_configured": bool(
                    admission_policy is not None
                    and admission_policy.application_outcome_qoe_debt_enabled
                ),
                "durable_state_configured": bool(args.state_db),
                "writer_lease_configured": bool(writer_lease_instance_id),
                "writer_lease_instance_id": writer_lease_instance_id or "",
                "writer_lease_ms": (
                    args.writer_lease_ms if writer_lease_instance_id else None
                ),
                "raft_leadership_configured": raft_lease is not None,
                "raft_node_id": args.raft_node_id or "",
                "raft_term": raft_term,
                **lease_acquisition,
                "recovered_frame_count": state.snapshot()["recovered_frames"],
                "recovered_consumer_count": state.snapshot()["recovered_consumers"],
                "recovered_admission_state": state.snapshot()[
                    "recovered_admission_state"
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    await stopped.wait()
    if lease_task is not None:
        lease_task.cancel()
        with suppress(asyncio.CancelledError):
            await lease_task
    server.close()
    await asyncio.sleep(0)
    final_metrics = state.snapshot()
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "stopped",
                "clean_teardown": True,
                "client_certificate_required": args.require_client_certificate,
                "publisher_identity_binding": bool(
                    args.bind_client_cn_to_publisher_id
                    or args.publisher_identity_uri_prefix
                ),
                "publisher_identity_source": (
                    "uri_san"
                    if args.publisher_identity_uri_prefix
                    else "common_name"
                    if args.bind_client_cn_to_publisher_id
                    else "disabled"
                ),
                "client_crl_configured": bool(args.client_crl),
                "mtls_adapter": mtls_adapter,
                "path_observer_adapter": path_observer_adapter,
                "admission_policy_configured": admission_policy is not None,
                "native_path_observations_configured": (
                    args.native_path_observations
                ),
                "native_qoe_debt_configured": bool(
                    admission_policy is not None
                    and admission_policy.native_qoe_debt_enabled
                ),
                "application_outcome_qoe_debt_configured": bool(
                    admission_policy is not None
                    and admission_policy.application_outcome_qoe_debt_enabled
                ),
                "durable_state_configured": bool(args.state_db),
                "writer_lease_configured": bool(writer_lease_instance_id),
                "writer_lease_instance_id": writer_lease_instance_id or "",
                "writer_lease_ms": (
                    args.writer_lease_ms if writer_lease_instance_id else None
                ),
                "raft_leadership_configured": raft_lease is not None,
                "raft_node_id": args.raft_node_id or "",
                "raft_term": raft_term,
                "writer_lease_lost": writer_lease_lost,
                **lease_acquisition,
                "metrics": final_metrics,
                "transport_metrics": service_telemetry.snapshot(),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    state.close()
    return 1 if writer_lease_lost else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4495)
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--client-ca")
    parser.add_argument("--client-crl")
    parser.add_argument("--require-client-certificate", action="store_true")
    parser.add_argument("--bind-client-cn-to-publisher-id", action="store_true")
    parser.add_argument("--publisher-identity-uri-prefix")
    parser.add_argument("--qlog-dir")
    parser.add_argument("--max-frames-per-topic", type=int, default=1024)
    parser.add_argument("--max-frame-bytes", type=int, default=1_048_576)
    parser.add_argument("--dedup-capacity-per-topic", type=int)
    parser.add_argument("--admission-policy")
    parser.add_argument("--state-db")
    parser.add_argument("--writer-lease-instance-id")
    parser.add_argument("--writer-lease-ms", type=int, default=5000)
    parser.add_argument("--writer-lease-wait-timeout-ms", type=int, default=0)
    parser.add_argument("--writer-lease-retry-ms", type=int, default=100)
    parser.add_argument(
        "--raft-status-url",
        help=(
            "status URL of this gateway's own co-located fleetqox.raft node "
            "(e.g. http://raft-n1:5000); when set, write eligibility comes "
            "from winning that cluster's leadership instead of a fixed "
            "--writer-lease-instance-id -- see fleetqox/raft_writer_lease.py"
        ),
    )
    parser.add_argument(
        "--raft-node-id",
        help="this gateway's node id within the Raft cluster named by --raft-status-url",
    )
    parser.add_argument("--raft-writer-lease-wait-timeout-ms", type=int, default=0)
    parser.add_argument("--raft-writer-lease-retry-ms", type=int, default=100)
    parser.add_argument("--native-path-observations", action="store_true")
    args = parser.parse_args()
    if args.require_client_certificate and not args.client_ca:
        parser.error("--client-ca is required with --require-client-certificate")
    if args.bind_client_cn_to_publisher_id and not args.require_client_certificate:
        parser.error(
            "--bind-client-cn-to-publisher-id requires --require-client-certificate"
        )
    if args.publisher_identity_uri_prefix and not args.require_client_certificate:
        parser.error(
            "--publisher-identity-uri-prefix requires --require-client-certificate"
        )
    if args.publisher_identity_uri_prefix and args.bind_client_cn_to_publisher_id:
        parser.error("configure only one publisher certificate identity source")
    if args.client_crl and not args.require_client_certificate:
        parser.error("--client-crl requires --require-client-certificate")
    if args.native_path_observations and not args.admission_policy:
        parser.error("--native-path-observations requires --admission-policy")
    if args.writer_lease_instance_id and not args.state_db:
        parser.error("--writer-lease-instance-id requires --state-db")
    if args.writer_lease_ms <= 0:
        parser.error("--writer-lease-ms must be positive")
    if args.writer_lease_wait_timeout_ms < 0:
        parser.error("--writer-lease-wait-timeout-ms must be non-negative")
    if args.writer_lease_retry_ms <= 0:
        parser.error("--writer-lease-retry-ms must be positive")
    if (
        args.writer_lease_wait_timeout_ms
        and not args.writer_lease_instance_id
        and not args.raft_status_url
    ):
        parser.error(
            "--writer-lease-wait-timeout-ms requires --writer-lease-instance-id "
            "or --raft-status-url"
        )
    if args.raft_status_url and args.writer_lease_instance_id:
        parser.error(
            "configure only one of --writer-lease-instance-id or --raft-status-url"
        )
    if args.raft_status_url and not args.raft_node_id:
        parser.error("--raft-status-url requires --raft-node-id")
    if args.raft_node_id and not args.raft_status_url:
        parser.error("--raft-node-id requires --raft-status-url")
    if args.raft_status_url and not args.state_db:
        parser.error("--raft-status-url requires --state-db")
    if args.raft_writer_lease_wait_timeout_ms < 0:
        parser.error("--raft-writer-lease-wait-timeout-ms must be non-negative")
    if args.raft_writer_lease_retry_ms <= 0:
        parser.error("--raft-writer-lease-retry-ms must be positive")
    return args


def main() -> int:
    return asyncio.run(run_service(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
