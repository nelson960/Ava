from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi import FastAPI, Header, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth import DevelopmentLoopbackAuthenticator, RequestAuthenticator
from .config import (
    AvaEnvironment,
    BackendConfigurationError,
    RealtimeDeliveryMode,
    Settings,
)
from .canvas.svg_delivery import (
    SvgCanvasDocumentStore,
    SvgDocumentNotFoundError,
)
from .canvas.svg_document import (
    SVG_MANIFEST_VERSION,
    SVG_RENDERER_CAPABILITY_VERSION,
    SVG_VALIDATOR_VERSION,
)
from .canvas.svg_specialist import SVG_CANVAS_SPECIALIST_PROMPT_VERSION
from .diagnostics import (
    RuntimeDiagnostics,
    configure_persistent_development_logging,
)
from .errors import AvaBackendError
from .protocol import (
    AgeBand,
    ErrorCode,
    ErrorDetail,
    ErrorEnvelope,
)
from .providers.openai_realtime_provider import OpenAIRealtimeProviderError
from .realtime.events_v4 import (
    PROTOCOL_V4,
    AvaSessionBootstrapV4,
    AvaSessionLimitsV4,
)
from .realtime.harness import (
    REALTIME_PROMPT_VERSION,
    REALTIME_TOOL_CONTRACT_VERSION,
)
from .realtime.memory import AuthoritativeMemoryStore
from .realtime.live_factory_v4 import LiveRealtimeCoordinatorFactoryV4
from .realtime.postgres_memory import (
    PostgresAuthoritativeMemoryStore,
    PostgresMemoryStoreError,
)
from .realtime.provider_session import (
    MAX_SDP_BYTES,
    RealtimeNegotiationResponseError,
    RealtimeNegotiationUnavailableError,
    validate_sdp,
)
from .realtime.session import (
    AvaSessionCreateRequestV4,
    RealtimeSessionAuthenticationError,
    RealtimeSessionAuthFailure,
    RealtimeSessionCapacityError,
    RealtimeSessionRegistry,
    RealtimeSessionTokenCodec,
    ephemeral_development_token_secret,
)
from .realtime.websocket_v4 import (
    AvaRealtimeWebSocketBridgeV4,
    RealtimeCoordinatorFactoryV4,
    RealtimeWebSocketProtocolErrorV4,
    RealtimeWebSocketSessionTimeoutV4,
)

LOGGER = logging.getLogger("ava_backend")


def _readiness_diagnostics(
    runtime: RuntimeDiagnostics,
) -> dict[str, object]:
    return {
        "build": runtime.readiness_build(),
        "protocolVersion": 3,
        "promptVersion": REALTIME_PROMPT_VERSION,
        "toolContractVersion": REALTIME_TOOL_CONTRACT_VERSION,
        "canvasSpecialistVersion": SVG_CANVAS_SPECIALIST_PROMPT_VERSION,
    }


def create_app(
    settings: Settings | None = None,
    *,
    authoritative_memory: AuthoritativeMemoryStore | None = None,
    authenticator: RequestAuthenticator | None = None,
    realtime_v4_coordinator_factory: RealtimeCoordinatorFactoryV4 | None = None,
    svg_document_store: SvgCanvasDocumentStore | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    runtime_diagnostics = RuntimeDiagnostics.capture()
    development_log_path = None
    if resolved_settings.environment is AvaEnvironment.DEVELOPMENT:
        development_log_path = configure_persistent_development_logging(
            configured_path=resolved_settings.development_log_path,
        )
    if authenticator is None:
        if resolved_settings.environment is AvaEnvironment.PRODUCTION:
            raise BackendConfigurationError(
                "A production RequestAuthenticator must be configured before startup"
            )
        authenticator = DevelopmentLoopbackAuthenticator(
            resolved_settings.allowed_development_hosts
        )

    app = FastAPI(
        title="Ava Backend",
        version="0.1.0",
        docs_url=None
        if resolved_settings.environment is AvaEnvironment.PRODUCTION
        else "/docs",
        redoc_url=None,
    )
    app.state.settings = resolved_settings
    app.state.runtime_diagnostics = runtime_diagnostics
    app.state.development_log_path = development_log_path
    app.state.authenticator = authenticator
    resolved_authoritative_memory = authoritative_memory
    if resolved_authoritative_memory is None and resolved_settings.database_url:
        resolved_authoritative_memory = PostgresAuthoritativeMemoryStore(
            resolved_settings.require_postgres_memory_url(),
            min_pool_size=resolved_settings.memory_pool_min_size,
            max_pool_size=resolved_settings.memory_pool_max_size,
            pool_timeout_seconds=resolved_settings.memory_pool_timeout_seconds,
            weekly_retention_days=resolved_settings.memory_weekly_retention_days,
        )
    elif (
        resolved_authoritative_memory is None
        and resolved_settings.environment is AvaEnvironment.DEVELOPMENT
    ):
        from .realtime.memory import DevelopmentAuthoritativeMemoryStore

        resolved_authoritative_memory = DevelopmentAuthoritativeMemoryStore()

    if (
        resolved_settings.environment is AvaEnvironment.PRODUCTION
        and resolved_authoritative_memory is None
    ):
        raise BackendConfigurationError(
            "Production realtime requires AVA_DATABASE_URL for durable memory"
        )
    app.state.authoritative_memory = resolved_authoritative_memory
    if resolved_authoritative_memory is not None:
        app.router.add_event_handler("startup", resolved_authoritative_memory.open)
        app.router.add_event_handler("shutdown", resolved_authoritative_memory.close)
    if (
        resolved_settings.openai_api_key is not None
        and resolved_settings.realtime_delivery_mode is RealtimeDeliveryMode.DISABLED
        and resolved_settings.environment is AvaEnvironment.DEVELOPMENT
    ):
        resolved_settings = resolved_settings.model_copy(
            update={"realtime_delivery_mode": RealtimeDeliveryMode.CAPTION_PREVIEW}
        )
    resolved_svg_documents = svg_document_store or SvgCanvasDocumentStore(
        max_documents=min(
            10_000,
            max(1, resolved_settings.realtime_max_sessions * 2),
        ),
    )
    if (
        realtime_v4_coordinator_factory is None
        and resolved_settings.openai_api_key is not None
        and resolved_settings.realtime_delivery_mode
        is not RealtimeDeliveryMode.DISABLED
    ):
        if resolved_authoritative_memory is None:
            raise BackendConfigurationError(
                "Protocol-v4 live sessions require authoritative memory"
            )
        realtime_v4_coordinator_factory = LiveRealtimeCoordinatorFactoryV4(
            settings=resolved_settings,
            memory=resolved_authoritative_memory,
            documents=resolved_svg_documents,
        )
    realtime_v4_sessions: RealtimeSessionRegistry | None = None
    if realtime_v4_coordinator_factory is not None:
        configured_token_secret = resolved_settings.realtime_session_token_secret
        token_secret = (
            configured_token_secret.get_secret_value()
            if configured_token_secret is not None
            else ephemeral_development_token_secret()
        )
        realtime_v4_sessions = RealtimeSessionRegistry(
            coordinator_factory=realtime_v4_coordinator_factory,
            token_codec=RealtimeSessionTokenCodec(token_secret),
            ttl=timedelta(seconds=resolved_settings.realtime_session_ttl_seconds),
            max_sessions=resolved_settings.realtime_max_sessions,
        )
        app.router.add_event_handler("shutdown", realtime_v4_sessions.close)
    app.state.realtime_v4_sessions = realtime_v4_sessions
    app.state.svg_canvas_documents = resolved_svg_documents
    app.state.realtime_websocket_bridge_v4 = AvaRealtimeWebSocketBridgeV4(
        session_timeout_seconds=resolved_settings.realtime_max_session_seconds,
    )

    LOGGER.info(
        "ava_backend_started instance_id=%s build_version=%s "
        "source_revision=%s prompt_version=%s tool_contract_version=%s "
        "canvas_prompt_version=%s development_log_path=%s",
        runtime_diagnostics.instance_id,
        runtime_diagnostics.readiness_build()["version"],
        runtime_diagnostics.source_revision,
        REALTIME_PROMPT_VERSION,
        REALTIME_TOOL_CONTRACT_VERSION,
        SVG_CANVAS_SPECIALIST_PROMPT_VERSION,
        str(development_log_path) if development_log_path is not None else None,
        extra={
            "ava_context": {
                "instanceId": runtime_diagnostics.instance_id,
                "sourceRevision": runtime_diagnostics.source_revision,
                "promptVersion": REALTIME_PROMPT_VERSION,
                "toolContractVersion": REALTIME_TOOL_CONTRACT_VERSION,
                "canvasPromptVersion": SVG_CANVAS_SPECIALIST_PROMPT_VERSION,
            }
        },
    )

    _install_http_boundary(app, resolved_settings)
    _install_error_handlers(app)

    @app.get("/health/live", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def readiness() -> Response:
        diagnostics_payload = _readiness_diagnostics(runtime_diagnostics)
        build = diagnostics_payload["build"]
        if not isinstance(build, dict) or build.get("sourceCurrent") is not True:
            return JSONResponse(
                status_code=503,
                content={"status": "stale", **diagnostics_payload},
                headers={"Cache-Control": "no-store"},
            )
        store: AuthoritativeMemoryStore | None = app.state.authoritative_memory
        if store is not None:
            try:
                await store.ping()
            except Exception:
                LOGGER.exception("memory_readiness_failed")
                return JSONResponse(
                    status_code=503,
                    content={"status": "unavailable", **diagnostics_payload},
                    headers={"Cache-Control": "no-store"},
                )
        return JSONResponse(
            status_code=200,
            content={"status": "ready", **diagnostics_payload},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/v4/realtime/sessions")
    async def create_realtime_session_v4(
        request: Request,
        envelope: AvaSessionCreateRequestV4,
        protocol_header: str | None = Header(
            default=None,
            alias="X-Ava-Protocol-Version",
        ),
    ) -> JSONResponse:
        if protocol_header != "4":
            return _error_response(
                request,
                status_code=409,
                code=ErrorCode.PROTOCOL_MISMATCH,
                message="X-Ava-Protocol-Version must be exactly 4.",
            )
        try:
            client = await app.state.authenticator.authenticate(request)
        except PermissionError:
            return _error_response(
                request,
                status_code=401,
                code=ErrorCode.UNAUTHORIZED,
                message="A valid Ava client session is required.",
            )
        registry: RealtimeSessionRegistry | None = app.state.realtime_v4_sessions
        if registry is None:
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Ava protocol-v4 realtime sessions are not configured.",
            )
        try:
            created = await registry.create(
                request_id=request.state.request_id,
                subject=client.subject,
                age_band=AgeBand(envelope.age_band),
            )
        except RealtimeSessionCapacityError:
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Ava realtime session capacity is temporarily unavailable.",
            )
        event_path = str(
            app.url_path_for(
                "realtime_session_events_v4",
                session_id=created.session_id,
            )
        )
        webrtc_path = str(
            app.url_path_for(
                "negotiate_realtime_webrtc_v4",
                session_id=created.session_id,
            )
        )
        canvas_base_path = (
            f"/v4/realtime/sessions/{created.session_id}/canvas-documents"
        )
        bootstrap = AvaSessionBootstrapV4.model_validate(
            {
                "protocolVersion": PROTOCOL_V4,
                "sessionId": created.session_id,
                "eventUrl": _realtime_event_url(
                    request=request,
                    settings=resolved_settings,
                    event_path=event_path,
                ),
                "webrtcUrl": _realtime_http_url(
                    request=request,
                    settings=resolved_settings,
                    path=webrtc_path,
                ),
                "canvasDocumentBaseUrl": _realtime_http_url(
                    request=request,
                    settings=resolved_settings,
                    path=canvas_base_path,
                ),
                "sessionToken": created.token,
                "webrtcToken": created.webrtc_token,
                "canvasToken": created.canvas_token,
                "expiresAt": created.expires_at.isoformat().replace("+00:00", "Z"),
                "rendererCapabilityVersion": SVG_RENDERER_CAPABILITY_VERSION,
                "validatorVersion": SVG_VALIDATOR_VERSION,
                "manifestVersion": SVG_MANIFEST_VERSION,
                "limits": AvaSessionLimitsV4.model_validate(
                    {
                        "maxTurnSeconds": resolved_settings.realtime_max_turn_seconds,
                        "maxResponseCharacters": 1_800,
                        "maxSvgBytes": 262_144,
                        "maxManifestBytes": 65_536,
                        "maxAudioChunkBytes": 192_000,
                        "playbackProgressIntervalMs": (
                            resolved_settings.realtime_playback_progress_interval_ms
                        ),
                    }
                ),
            },
            context={
                "allow_insecure_loopback": (
                    resolved_settings.realtime_allow_insecure_loopback
                    or resolved_settings.environment is AvaEnvironment.DEVELOPMENT
                )
            },
        )
        return JSONResponse(
            status_code=201,
            content=bootstrap.model_dump(mode="json", by_alias=True),
            headers={
                "Cache-Control": "no-store",
                "X-Ava-Protocol-Version": "4",
            },
        )

    @app.get("/v3/memory/weekly-recap")
    async def get_weekly_recap(
        request: Request,
        period_start: date | None = None,
        protocol_header: str | None = Header(
            default=None,
            alias="X-Ava-Protocol-Version",
        ),
    ) -> JSONResponse:
        if protocol_header != "3":
            return _error_response(
                request,
                status_code=409,
                code=ErrorCode.PROTOCOL_MISMATCH,
                message="X-Ava-Protocol-Version must be exactly 3.",
            )
        try:
            client = await app.state.authenticator.authenticate(request)
        except PermissionError:
            return _error_response(
                request,
                status_code=401,
                code=ErrorCode.UNAUTHORIZED,
                message="A valid Ava client session is required.",
            )
        store: AuthoritativeMemoryStore | None = app.state.authoritative_memory
        if store is None:
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.MEMORY_UNAVAILABLE,
                message="Ava's conversation memory is not configured.",
            )
        generated_at = datetime.now(UTC)
        selected_start = period_start or (
            generated_at.date() - timedelta(days=generated_at.weekday())
        )
        try:
            recap = await store.load_weekly_recap(
                subject=client.subject,
                period_start=selected_start,
                generated_at=generated_at,
            )
        except PostgresMemoryStoreError:
            LOGGER.exception(
                "weekly_recap_failed request_id=%s",
                request.state.request_id,
            )
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.MEMORY_UNAVAILABLE,
                message="Ava's conversation memory is temporarily unavailable.",
            )
        return JSONResponse(
            status_code=200,
            content=recap.model_dump(mode="json", by_alias=True),
            headers={
                "Cache-Control": "no-store",
                "X-Ava-Protocol-Version": "3",
            },
        )

    @app.delete("/v3/memory/conversations")
    async def reset_v3_conversations(
        request: Request,
        protocol_header: str | None = Header(
            default=None,
            alias="X-Ava-Protocol-Version",
        ),
    ) -> JSONResponse:
        if protocol_header != "3":
            return _error_response(
                request,
                status_code=409,
                code=ErrorCode.PROTOCOL_MISMATCH,
                message="X-Ava-Protocol-Version must be exactly 3.",
            )
        try:
            client = await app.state.authenticator.authenticate(request)
        except PermissionError:
            return _error_response(
                request,
                status_code=401,
                code=ErrorCode.UNAUTHORIZED,
                message="A valid Ava client session is required.",
            )
        store: AuthoritativeMemoryStore | None = app.state.authoritative_memory
        if store is None:
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.MEMORY_UNAVAILABLE,
                message="Ava's conversation memory is not configured.",
            )
        try:
            receipt = await store.reset_conversation(subject=client.subject)
        except PostgresMemoryStoreError:
            LOGGER.exception(
                "conversation_reset_failed request_id=%s",
                request.state.request_id,
            )
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.MEMORY_UNAVAILABLE,
                message="Ava's conversation memory is temporarily unavailable.",
            )
        return JSONResponse(
            status_code=200,
            content=receipt.model_dump(mode="json", by_alias=True),
            headers={
                "Cache-Control": "no-store",
                "X-Ava-Protocol-Version": "3",
            },
        )

    @app.post(
        "/v4/realtime/sessions/{session_id}/webrtc",
        name="negotiate_realtime_webrtc_v4",
    )
    async def negotiate_realtime_webrtc_v4(
        request: Request,
        session_id: str,
        protocol_header: str | None = Header(
            default=None,
            alias="X-Ava-Protocol-Version",
        ),
    ) -> Response:
        if protocol_header != "4":
            return _error_response(
                request,
                status_code=409,
                code=ErrorCode.PROTOCOL_MISMATCH,
                message="X-Ava-Protocol-Version must be exactly 4.",
            )
        if (
            request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip()
            != "application/sdp"
        ):
            return _error_response(
                request,
                status_code=415,
                code=ErrorCode.INVALID_REQUEST,
                message="Realtime negotiation requires application/sdp.",
            )
        try:
            client = await app.state.authenticator.authenticate(request)
        except PermissionError:
            return _error_response(
                request,
                status_code=401,
                code=ErrorCode.UNAUTHORIZED,
                message="A valid Ava client session is required.",
            )
        token = _bearer_token(request.headers.get("authorization"))
        registry: RealtimeSessionRegistry | None = app.state.realtime_v4_sessions
        if token is None or registry is None:
            return _error_response(
                request,
                status_code=(401 if token is None else 503),
                code=(
                    ErrorCode.UNAUTHORIZED
                    if token is None
                    else ErrorCode.PROVIDER_UNAVAILABLE
                ),
                message=(
                    "A valid realtime session token is required."
                    if token is None
                    else "Ava protocol-v4 realtime sessions are not configured."
                ),
            )
        try:
            raw_offer = await _read_bounded_request_body(request, MAX_SDP_BYTES)
            offer_sdp = raw_offer.decode("utf-8")
            validate_sdp(offer_sdp, description="offer")
        except (UnicodeDecodeError, ValueError):
            return _error_response(
                request,
                status_code=422,
                code=ErrorCode.INVALID_REQUEST,
                message="The WebRTC offer is invalid or exceeds the SDP limit.",
            )
        try:
            negotiated = await registry.negotiate(
                session_id=session_id,
                token=token,
                subject=client.subject,
                offer_sdp=offer_sdp,
            )
        except RealtimeSessionAuthenticationError:
            return _error_response(
                request,
                status_code=401,
                code=ErrorCode.UNAUTHORIZED,
                message="The realtime session cannot be negotiated.",
            )
        except RealtimeNegotiationUnavailableError:
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Realtime media negotiation is temporarily unavailable.",
            )
        except (
            RealtimeNegotiationResponseError,
            OpenAIRealtimeProviderError,
            ValueError,
        ):
            return _error_response(
                request,
                status_code=502,
                code=ErrorCode.PROVIDER_INVALID_RESPONSE,
                message="Realtime media negotiation returned an invalid answer.",
            )
        return Response(
            content=negotiated.answer_sdp,
            status_code=200,
            media_type="application/sdp",
            headers={
                "Cache-Control": "no-store",
                "X-Ava-Protocol-Version": "4",
            },
        )

    async def serve_svg_canvas_document(
        request: Request,
        *,
        session_id: str,
        generation_id: str,
        kind: str,
        canvas_token: str | None,
        protocol_header: str | None,
    ) -> Response:
        if protocol_header != "4":
            return _error_response(
                request,
                status_code=409,
                code=ErrorCode.PROTOCOL_MISMATCH,
                message="X-Ava-Protocol-Version must be exactly 4.",
            )
        try:
            client = await app.state.authenticator.authenticate(request)
        except PermissionError:
            return _error_response(
                request,
                status_code=401,
                code=ErrorCode.UNAUTHORIZED,
                message="A valid Ava client session is required.",
            )
        registry: RealtimeSessionRegistry | None = app.state.realtime_v4_sessions
        if registry is None or canvas_token is None:
            return _error_response(
                request,
                status_code=401 if canvas_token is None else 503,
                code=(
                    ErrorCode.UNAUTHORIZED
                    if canvas_token is None
                    else ErrorCode.PROVIDER_UNAVAILABLE
                ),
                message="Canvas document authorization is unavailable.",
            )
        try:
            await registry.authorize_canvas_document(
                session_id=session_id,
                token=canvas_token,
                subject=client.subject,
            )
        except RealtimeSessionAuthenticationError:
            return _error_response(
                request,
                status_code=401,
                code=ErrorCode.UNAUTHORIZED,
                message="The canvas document token is invalid.",
            )
        store: SvgCanvasDocumentStore = app.state.svg_canvas_documents
        try:
            stored = await store.get(
                session_id=session_id,
                generation_id=generation_id,
            )
        except (SvgDocumentNotFoundError, ValueError):
            return _error_response(
                request,
                status_code=404,
                code=ErrorCode.INVALID_REQUEST,
                message="The canvas document does not exist.",
            )
        if kind == "svg":
            content = stored.document.svg_bytes
            content_hash = stored.document.svg_sha256
            media_type = "image/svg+xml"
        elif kind == "manifest":
            content = stored.document.manifest_bytes
            content_hash = stored.document.manifest_sha256
            media_type = "application/json"
        else:  # pragma: no cover - route invariant
            raise RuntimeError("unsupported SVG canvas document kind")
        return Response(
            content=content,
            status_code=200,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'",
                "ETag": f'"sha256:{content_hash}"',
                "X-Ava-Protocol-Version": "4",
                "X-Ava-Renderer-Capability-Version": (SVG_RENDERER_CAPABILITY_VERSION),
                "X-Ava-Validator-Version": SVG_VALIDATOR_VERSION,
                "X-Content-SHA256": content_hash,
            },
        )

    @app.get("/v4/realtime/sessions/{session_id}/canvas-documents/{generation_id}/svg")
    async def get_svg_canvas_document(
        request: Request,
        session_id: str,
        generation_id: str,
        canvas_token: str | None = Header(default=None, alias="X-Ava-Canvas-Token"),
        protocol_header: str | None = Header(
            default=None,
            alias="X-Ava-Protocol-Version",
        ),
    ) -> Response:
        return await serve_svg_canvas_document(
            request,
            session_id=session_id,
            generation_id=generation_id,
            kind="svg",
            canvas_token=canvas_token,
            protocol_header=protocol_header,
        )

    @app.get(
        "/v4/realtime/sessions/{session_id}/canvas-documents/{generation_id}/manifest"
    )
    async def get_svg_canvas_manifest(
        request: Request,
        session_id: str,
        generation_id: str,
        canvas_token: str | None = Header(default=None, alias="X-Ava-Canvas-Token"),
        protocol_header: str | None = Header(
            default=None,
            alias="X-Ava-Protocol-Version",
        ),
    ) -> Response:
        return await serve_svg_canvas_document(
            request,
            session_id=session_id,
            generation_id=generation_id,
            kind="manifest",
            canvas_token=canvas_token,
            protocol_header=protocol_header,
        )

    @app.websocket(
        "/v4/realtime/sessions/{session_id}/events",
        name="realtime_session_events_v4",
    )
    async def realtime_session_events_v4(
        websocket: WebSocket,
        session_id: str,
    ) -> None:
        registry: RealtimeSessionRegistry | None = app.state.realtime_v4_sessions
        if registry is None:
            await _close_websocket(websocket, code=1013)
            return
        if websocket.headers.get("x-ava-protocol-version") != "4":
            await _close_websocket(websocket, code=4406)
            return
        token = _bearer_token(websocket.headers.get("authorization"))
        if token is None:
            await _close_websocket(websocket, code=4401)
            return
        try:
            coordinator = await registry.claim(session_id=session_id, token=token)
        except RealtimeSessionAuthenticationError as error:
            await _close_websocket(
                websocket,
                code=_websocket_auth_close_code(error.failure),
            )
            return
        await websocket.accept()
        try:
            bridge: AvaRealtimeWebSocketBridgeV4 = (
                app.state.realtime_websocket_bridge_v4
            )
            await bridge.serve(
                websocket=websocket,
                coordinator=coordinator,
                session_id=session_id,
            )
            await _close_websocket(websocket, code=1000)
        except WebSocketDisconnect:
            pass
        except RealtimeWebSocketProtocolErrorV4:
            await _close_websocket(websocket, code=1002)
        except RealtimeWebSocketSessionTimeoutV4:
            await _close_websocket(websocket, code=1013)
        except Exception:
            LOGGER.exception("realtime_v4_socket_failed session_id=%s", session_id)
            await _close_websocket(websocket, code=1011)
        finally:
            await registry.release(session_id)
            store: SvgCanvasDocumentStore = app.state.svg_canvas_documents
            await store.release_session(session_id=session_id)

    return app


def _install_http_boundary(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def request_boundary(
        request: Request,
        call_next: Callable[[Request], Awaitable[JSONResponse]],
    ) -> JSONResponse:
        request.state.request_id = f"request-{uuid4()}"
        raw_content_length = request.headers.get("content-length")
        if raw_content_length is not None:
            try:
                content_length = int(raw_content_length)
            except ValueError:
                return _error_response(
                    request,
                    status_code=400,
                    code=ErrorCode.INVALID_REQUEST,
                    message="Content-Length must be a valid integer.",
                )
            if content_length > settings.request_max_bytes:
                return _error_response(
                    request,
                    status_code=413,
                    code=ErrorCode.PAYLOAD_TOO_LARGE,
                    message="The lesson request exceeds the server size limit.",
                )

        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code=ErrorCode.INVALID_REQUEST,
            message="The request does not match its declared Ava protocol.",
        )

    @app.exception_handler(AvaBackendError)
    async def backend_error(
        request: Request,
        error: AvaBackendError,
    ) -> JSONResponse:
        LOGGER.warning(
            "lesson_turn_failed request_id=%s error_code=%s",
            request.state.request_id,
            error.code,
        )
        return _error_response(
            request,
            status_code=error.status_code,
            code=error.code,
            message=error.public_message,
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, error: Exception) -> JSONResponse:
        LOGGER.exception(
            "unhandled_backend_error request_id=%s",
            request.state.request_id,
            exc_info=error,
        )
        return _error_response(
            request,
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Ava could not complete the request.",
        )


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", f"request-{uuid4()}")
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=request_id,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json", by_alias=True),
        headers={"X-Request-ID": request_id},
    )


def _realtime_event_url(
    *,
    request: Request,
    settings: Settings,
    event_path: str,
) -> str:
    configured = settings.realtime_public_event_origin
    if configured is not None:
        return f"{configured.rstrip('/')}/{event_path.lstrip('/')}"
    parsed = urlsplit(str(request.base_url))
    if not parsed.hostname:
        raise BackendConfigurationError(
            "Ava could not derive a realtime event origin from the request"
        )
    insecure_loopback = (
        (
            settings.realtime_allow_insecure_loopback
            or settings.environment is AvaEnvironment.DEVELOPMENT
        )
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    return urlunsplit(
        ("ws" if insecure_loopback else "wss", parsed.netloc, event_path, "", "")
    )


def _realtime_http_url(
    *,
    request: Request,
    settings: Settings,
    path: str,
) -> str:
    configured = settings.realtime_public_event_origin
    if configured is not None:
        parsed = urlsplit(configured)
        return urlunsplit(("https", parsed.netloc, path, "", ""))
    parsed = urlsplit(str(request.base_url))
    insecure_loopback = (
        (
            settings.realtime_allow_insecure_loopback
            or settings.environment is AvaEnvironment.DEVELOPMENT
        )
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if (parsed.scheme != "https" and not insecure_loopback) or not parsed.hostname:
        raise BackendConfigurationError(
            "Ava could not derive a secure realtime HTTP origin from the request"
        )
    return urlunsplit(
        ("http" if insecure_loopback else "https", parsed.netloc, path, "", "")
    )


async def _read_bounded_request_body(request: Request, maximum_bytes: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum_bytes:
            raise ValueError("request body exceeds its configured limit")
    if not body:
        raise ValueError("request body cannot be empty")
    return bytes(body)


def _bearer_token(value: str | None) -> str | None:
    if value is None or len(value) > 4096:
        return None
    scheme, separator, token = value.partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        return None
    normalized = token.strip()
    return normalized or None


def _websocket_auth_close_code(
    failure: RealtimeSessionAuthFailure,
) -> int:
    return {
        RealtimeSessionAuthFailure.INVALID: 4401,
        RealtimeSessionAuthFailure.EXPIRED: 4408,
        RealtimeSessionAuthFailure.UNKNOWN: 4404,
        RealtimeSessionAuthFailure.CLAIMED: 4409,
        RealtimeSessionAuthFailure.NOT_READY: 4425,
    }[failure]


async def _close_websocket(
    websocket: WebSocket,
    *,
    code: int,
    reason: str = "",
) -> None:
    await websocket.close(code=code, reason=reason)
