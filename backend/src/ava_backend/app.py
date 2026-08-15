from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi import FastAPI, Header, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import DevelopmentLoopbackAuthenticator, RequestAuthenticator
from .config import (
    AvaEnvironment,
    BackendConfigurationError,
    RealtimeDeliveryMode,
    Settings,
)
from .canvas.specialist import CANVAS_SPECIALIST_PROMPT_VERSION
from .diagnostics import (
    RuntimeDiagnostics,
    configure_persistent_development_logging,
    new_failure_id,
)
from .errors import AvaBackendError
from .memory import (
    ConversationMemoryStore,
    SupabaseConversationMemoryStore,
)
from .orchestration import AvaOrchestrator, AvaPerformanceOrchestrator
from .performance_stream import AvaPerformanceStream
from .protocol import (
    AgeBand,
    ErrorCode,
    ErrorDetail,
    ErrorEnvelope,
    LessonTurnEnvelope,
    PerformanceTurnEnvelope,
)
from .providers.base import AvaModel, AvaPerformanceModel, AvaTimedSpeech
from .providers.elevenlabs_speech_provider import ElevenLabsAvaSpeech
from .providers.openai_provider import OpenAIAvaModel
from .realtime.events import PROTOCOL_V3, AvaSessionBootstrap, AvaSessionLimits
from .realtime.harness import (
    REALTIME_PROMPT_VERSION,
    REALTIME_TOOL_CONTRACT_VERSION,
)
from .realtime.memory import AuthoritativeMemoryStore
from .realtime.live_factory import LiveRealtimeCoordinatorFactory
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
    AvaSessionCreateRequest,
    RealtimeCoordinatorFactory,
    RealtimeSessionAuthenticationError,
    RealtimeSessionAuthFailure,
    RealtimeSessionCapacityError,
    RealtimeSessionRegistry,
    RealtimeSessionTokenCodec,
    ephemeral_development_token_secret,
)
from .realtime.websocket import (
    AvaRealtimeWebSocketBridge,
    RealtimeWebSocketProtocolError,
    RealtimeWebSocketSessionTimeout,
)

LOGGER = logging.getLogger("ava_backend")


def _readiness_diagnostics(
    runtime: RuntimeDiagnostics,
) -> dict[str, object]:
    return {
        "build": runtime.readiness_build(),
        "protocolVersion": PROTOCOL_V3,
        "promptVersion": REALTIME_PROMPT_VERSION,
        "toolContractVersion": REALTIME_TOOL_CONTRACT_VERSION,
        "canvasSpecialistVersion": CANVAS_SPECIALIST_PROMPT_VERSION,
    }


def create_app(
    settings: Settings | None = None,
    *,
    ava_model: AvaModel | None = None,
    ava_performance_model: AvaPerformanceModel | None = None,
    ava_timed_speech: AvaTimedSpeech | None = None,
    conversation_memory: ConversationMemoryStore | None = None,
    authoritative_memory: AuthoritativeMemoryStore | None = None,
    authenticator: RequestAuthenticator | None = None,
    realtime_coordinator_factory: RealtimeCoordinatorFactory | None = None,
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

    resolved_model = ava_model or OpenAIAvaModel(resolved_settings)
    orchestrator = AvaOrchestrator(resolved_model, resolved_settings)
    resolved_performance_model = ava_performance_model or cast(
        AvaPerformanceModel,
        resolved_model,
    )
    performance_orchestrator = AvaPerformanceOrchestrator(
        resolved_performance_model,
        resolved_settings,
    )
    resolved_timed_speech = ava_timed_speech
    if (
        resolved_timed_speech is None
        and resolved_settings.elevenlabs_api_key is not None
    ):
        resolved_timed_speech = ElevenLabsAvaSpeech(resolved_settings)
    resolved_memory = conversation_memory
    if resolved_memory is None and (
        resolved_settings.supabase_url is not None
        or resolved_settings.supabase_service_role_key is not None
    ):
        resolved_memory = SupabaseConversationMemoryStore(resolved_settings)
    if (
        resolved_settings.environment is AvaEnvironment.PRODUCTION
        and resolved_timed_speech is not None
        and resolved_memory is None
    ):
        raise BackendConfigurationError(
            "Production performance turns require a durable ConversationMemoryStore"
        )
    performance_stream = (
        AvaPerformanceStream(
            performance_orchestrator,
            resolved_timed_speech,
            memory=resolved_memory,
            memory_turn_limit=min(
                8,
                resolved_settings.context_history_max_messages // 2,
            ),
        )
        if resolved_timed_speech is not None
        else None
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
    app.state.orchestrator = orchestrator
    app.state.performance_stream = performance_stream
    resolved_authoritative_memory = authoritative_memory
    if resolved_authoritative_memory is None and resolved_settings.database_url:
        resolved_authoritative_memory = PostgresAuthoritativeMemoryStore(
            resolved_settings.require_postgres_memory_url(),
            min_pool_size=resolved_settings.memory_pool_min_size,
            max_pool_size=resolved_settings.memory_pool_max_size,
            pool_timeout_seconds=resolved_settings.memory_pool_timeout_seconds,
            weekly_retention_days=resolved_settings.memory_weekly_retention_days,
        )
    if (
        resolved_settings.environment is AvaEnvironment.PRODUCTION
        and resolved_authoritative_memory is None
    ):
        raise BackendConfigurationError(
            "Production protocol-v3 requires AVA_DATABASE_URL for durable memory"
        )
    app.state.authoritative_memory = resolved_authoritative_memory
    if resolved_authoritative_memory is not None:
        app.router.add_event_handler("startup", resolved_authoritative_memory.open)
        app.router.add_event_handler("shutdown", resolved_authoritative_memory.close)
    if (
        realtime_coordinator_factory is None
        and resolved_settings.realtime_delivery_mode
        is not RealtimeDeliveryMode.DISABLED
    ):
        if resolved_authoritative_memory is None:
            raise BackendConfigurationError(
                "Protocol-v3 live sessions require AVA_DATABASE_URL"
            )
        realtime_coordinator_factory = LiveRealtimeCoordinatorFactory(
            settings=resolved_settings,
            memory=resolved_authoritative_memory,
        )

    realtime_sessions: RealtimeSessionRegistry | None = None
    if realtime_coordinator_factory is not None:
        configured_token_secret = resolved_settings.realtime_session_token_secret
        token_secret = (
            configured_token_secret.get_secret_value()
            if configured_token_secret is not None
            else ephemeral_development_token_secret()
        )
        realtime_sessions = RealtimeSessionRegistry(
            coordinator_factory=realtime_coordinator_factory,
            token_codec=RealtimeSessionTokenCodec(token_secret),
            ttl=timedelta(seconds=resolved_settings.realtime_session_ttl_seconds),
            max_sessions=resolved_settings.realtime_max_sessions,
        )
        app.router.add_event_handler("shutdown", realtime_sessions.close)
    app.state.realtime_sessions = realtime_sessions
    app.state.realtime_websocket_bridge = AvaRealtimeWebSocketBridge(
        turn_timeout_seconds=resolved_settings.realtime_max_session_seconds,
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
        CANVAS_SPECIALIST_PROMPT_VERSION,
        str(development_log_path) if development_log_path is not None else None,
        extra={
            "ava_context": {
                "instanceId": runtime_diagnostics.instance_id,
                "sourceRevision": runtime_diagnostics.source_revision,
                "promptVersion": REALTIME_PROMPT_VERSION,
                "toolContractVersion": REALTIME_TOOL_CONTRACT_VERSION,
                "canvasPromptVersion": CANVAS_SPECIALIST_PROMPT_VERSION,
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

    @app.post("/v1/lesson-turns")
    async def create_lesson_turn(
        request: Request,
        envelope: LessonTurnEnvelope,
        protocol_header: str | None = Header(
            default=None,
            alias="X-Ava-Protocol-Version",
        ),
    ) -> JSONResponse:
        if protocol_header != "1":
            return _error_response(
                request,
                status_code=409,
                code=ErrorCode.PROTOCOL_MISMATCH,
                message="X-Ava-Protocol-Version must be exactly 1.",
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

        turn_request = envelope.request
        if turn_request.client_instance_id is None:
            turn_request = turn_request.model_copy(
                update={"client_instance_id": client.subject}
            )
        outcome = await app.state.orchestrator.create_turn(turn_request)
        LOGGER.info(
            "lesson_turn_completed request_id=%s provider_request_id=%s input_tokens=%s output_tokens=%s context_included=%s context_omitted=%s",
            request.state.request_id,
            outcome.provider_request_id,
            outcome.input_tokens,
            outcome.output_tokens,
            outcome.included_history_messages,
            outcome.omitted_history_messages,
        )
        return JSONResponse(
            status_code=200,
            content=outcome.response.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
            headers={"X-Request-ID": request.state.request_id},
        )

    @app.post("/v2/performance-turns")
    async def create_performance_turn(
        request: Request,
        envelope: PerformanceTurnEnvelope,
        protocol_header: str | None = Header(
            default=None,
            alias="X-Ava-Protocol-Version",
        ),
    ) -> Response:
        if protocol_header != "2":
            return _error_response(
                request,
                status_code=409,
                code=ErrorCode.PROTOCOL_MISMATCH,
                message="X-Ava-Protocol-Version must be exactly 2.",
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

        streamer: AvaPerformanceStream | None = app.state.performance_stream
        if streamer is None:
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message=("Ava's selected timed-speech provider is not configured."),
            )

        turn_request = envelope.request
        if turn_request.client_instance_id is None:
            turn_request = turn_request.model_copy(
                update={"client_instance_id": client.subject}
            )
        turn_id = f"turn-{uuid4()}"

        async def event_lines() -> AsyncIterator[str]:
            async for event in streamer.stream(
                request=turn_request,
                turn_id=turn_id,
                request_id=request.state.request_id,
                subject=client.subject,
            ):
                yield (
                    event.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    )
                    + "\n"
                )

        return StreamingResponse(
            event_lines(),
            status_code=200,
            media_type="application/x-ndjson",
            headers={
                "X-Request-ID": request.state.request_id,
                "X-Ava-Protocol-Version": "2",
                "Cache-Control": "no-store",
            },
        )

    @app.post("/v3/realtime/sessions")
    async def create_realtime_session(
        request: Request,
        envelope: AvaSessionCreateRequest,
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
        registry: RealtimeSessionRegistry | None = app.state.realtime_sessions
        if registry is None:
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Ava realtime sessions are not configured.",
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
                "realtime_session_events",
                session_id=created.session_id,
            )
        )
        webrtc_path = str(
            app.url_path_for(
                "negotiate_realtime_webrtc",
                session_id=created.session_id,
            )
        )
        bootstrap = AvaSessionBootstrap.model_validate(
            {
                "protocolVersion": 3,
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
                "sessionToken": created.token,
                "webrtcToken": created.webrtc_token,
                "expiresAt": created.expires_at.isoformat().replace("+00:00", "Z"),
                "limits": AvaSessionLimits.model_validate(
                    {
                        "maxTurnSeconds": (resolved_settings.realtime_max_turn_seconds),
                        "maxResponseCharacters": 1_800,
                        "maxCanvasNodes": 96,
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
                )
            },
        )
        LOGGER.info(
            "realtime_session_created request_id=%s session_id=%s",
            request.state.request_id,
            created.session_id,
        )
        return JSONResponse(
            status_code=201,
            content=bootstrap.model_dump(mode="json", by_alias=True),
            headers={
                "Cache-Control": "no-store",
                "X-Ava-Protocol-Version": "3",
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
        "/v3/realtime/sessions/{session_id}/webrtc",
        name="negotiate_realtime_webrtc",
    )
    async def negotiate_realtime_webrtc(
        request: Request,
        session_id: str,
        protocol_header: str | None = Header(
            default=None,
            alias="X-Ava-Protocol-Version",
        ),
    ) -> Response:
        if protocol_header != "3":
            return _error_response(
                request,
                status_code=409,
                code=ErrorCode.PROTOCOL_MISMATCH,
                message="X-Ava-Protocol-Version must be exactly 3.",
            )
        content_type = request.headers.get("content-type", "")
        if content_type.split(";", maxsplit=1)[0].strip() != "application/sdp":
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
        if token is None:
            return _error_response(
                request,
                status_code=401,
                code=ErrorCode.UNAUTHORIZED,
                message="A valid realtime session token is required.",
            )
        registry: RealtimeSessionRegistry | None = app.state.realtime_sessions
        if registry is None:
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Ava realtime sessions are not configured.",
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
        except RealtimeNegotiationUnavailableError as error:
            LOGGER.warning(
                "realtime_media_negotiation_unavailable request_id=%s session_id=%s "
                "stage=%s error_type=%s provider_status_code=%s "
                "provider_request_id=%s",
                request.state.request_id,
                session_id,
                error.stage,
                error.error_type,
                error.provider_status_code,
                error.provider_request_id,
            )
            return _error_response(
                request,
                status_code=503,
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Realtime media negotiation is temporarily unavailable.",
            )
        except RealtimeNegotiationResponseError as error:
            LOGGER.warning(
                "realtime_media_negotiation_invalid request_id=%s session_id=%s "
                "reason=%s "
                "provider_status_code=%s provider_error_code=%s "
                "provider_error_parameter=%s provider_request_id=%s",
                request.state.request_id,
                session_id,
                str(error),
                error.provider_status_code,
                error.provider_error_code,
                error.provider_error_parameter,
                error.provider_request_id,
            )
            return _error_response(
                request,
                status_code=502,
                code=ErrorCode.PROVIDER_INVALID_RESPONSE,
                message="Realtime media negotiation returned an invalid answer.",
            )
        except ValueError:
            LOGGER.warning(
                "realtime_media_negotiation_invalid_sdp request_id=%s session_id=%s",
                request.state.request_id,
                session_id,
            )
            return _error_response(
                request,
                status_code=502,
                code=ErrorCode.PROVIDER_INVALID_RESPONSE,
                message="Realtime media negotiation returned an invalid answer.",
            )

        LOGGER.info(
            "realtime_media_negotiated request_id=%s session_id=%s provider_call_id=%s",
            request.state.request_id,
            session_id,
            negotiated.provider_call_id,
        )
        return Response(
            content=negotiated.answer_sdp,
            status_code=200,
            media_type="application/sdp",
            headers={
                "Cache-Control": "no-store",
                "X-Ava-Protocol-Version": "3",
            },
        )

    @app.websocket(
        "/v3/realtime/sessions/{session_id}/events",
        name="realtime_session_events",
    )
    async def realtime_session_events(
        websocket: WebSocket,
        session_id: str,
    ) -> None:
        connection_id = str(uuid4())
        registry: RealtimeSessionRegistry | None = app.state.realtime_sessions
        if registry is None:
            await _close_websocket(websocket, code=1013)
            return
        if websocket.headers.get("x-ava-protocol-version") != "3":
            await _close_websocket(websocket, code=4406)
            return
        token = _bearer_token(websocket.headers.get("authorization"))
        if token is None:
            await _close_websocket(websocket, code=4401)
            return
        try:
            coordinator = await registry.claim(
                session_id=session_id,
                token=token,
            )
        except RealtimeSessionAuthenticationError as error:
            await _close_websocket(
                websocket,
                code=_websocket_auth_close_code(error.failure),
            )
            return

        await websocket.accept()
        LOGGER.info(
            "realtime_socket_connected connection_id=%s session_id=%s",
            connection_id,
            session_id,
        )
        try:
            bridge: AvaRealtimeWebSocketBridge = app.state.realtime_websocket_bridge
            await bridge.serve(
                websocket=websocket,
                coordinator=coordinator,
                session_id=session_id,
            )
            await _close_websocket(websocket, code=1000)
        except WebSocketDisconnect as error:
            LOGGER.info(
                "realtime_socket_disconnected connection_id=%s session_id=%s "
                "close_code=%s",
                connection_id,
                session_id,
                error.code,
            )
        except RealtimeWebSocketProtocolError as error:
            failure_id = new_failure_id()
            turn_id = coordinator.turn_id
            cause = error.__cause__
            LOGGER.warning(
                "realtime_socket_protocol_error failure_id=%s "
                "connection_id=%s session_id=%s turn_id=%s "
                "reason=%s cause_type=%s",
                failure_id,
                connection_id,
                session_id,
                turn_id,
                str(error),
                type(cause).__name__ if cause is not None else None,
                extra={
                    "ava_context": {
                        "failureId": failure_id,
                        "component": "protocol",
                        "connectionId": connection_id,
                        "sessionId": session_id,
                        "turnId": turn_id,
                    }
                },
            )
            await _close_websocket(
                websocket,
                code=1002,
                reason=f"protocolError:protocol:{failure_id}",
            )
        except RealtimeWebSocketSessionTimeout as error:
            failure_id = new_failure_id()
            turn_id = coordinator.turn_id
            LOGGER.warning(
                "realtime_socket_timeout failure_id=%s connection_id=%s "
                "session_id=%s turn_id=%s reason=%s",
                failure_id,
                connection_id,
                session_id,
                turn_id,
                str(error),
                extra={
                    "ava_context": {
                        "failureId": failure_id,
                        "component": "coordinator",
                        "connectionId": connection_id,
                        "sessionId": session_id,
                        "turnId": turn_id,
                    }
                },
            )
            await _close_websocket(
                websocket,
                code=1013,
                reason=f"sessionTimeout:coordinator:{failure_id}",
            )
        except Exception:
            failure_id = new_failure_id()
            turn_id = coordinator.turn_id
            LOGGER.exception(
                "realtime_socket_failed failure_id=%s connection_id=%s "
                "session_id=%s turn_id=%s",
                failure_id,
                connection_id,
                session_id,
                turn_id,
                extra={
                    "ava_context": {
                        "failureId": failure_id,
                        "component": "coordinator",
                        "connectionId": connection_id,
                        "sessionId": session_id,
                        "turnId": turn_id,
                    }
                },
            )
            await _close_websocket(
                websocket,
                code=1011,
                reason=f"serverError:coordinator:{failure_id}",
            )
        finally:
            await registry.release(session_id)

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
        settings.realtime_allow_insecure_loopback
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
        settings.realtime_allow_insecure_loopback
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
