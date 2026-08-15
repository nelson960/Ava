from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .coordinator import (
    AvaServerControlFrame,
    AvaServerPcmFrame,
    CoordinatorStateError,
    RealtimeSessionCoordinator,
)
from .events import (
    AVA_CLIENT_EVENT_ADAPTER,
    AudioChunkEvent,
    AvaClientEventUnion,
    ClientReadyEvent,
    serialize_server_event,
)


LOGGER = logging.getLogger("ava_backend.realtime_websocket")


class RealtimeWebSocketProtocolError(RuntimeError):
    """A client or coordinator violated the application WebSocket contract."""


class RealtimeWebSocketSessionTimeout(RuntimeError):
    """The bounded server-side conversation deadline elapsed."""


class AvaRealtimeWebSocketBridge:
    max_client_control_bytes = 64 * 1024

    def __init__(
        self,
        *,
        client_ready_timeout_seconds: float = 10.0,
        turn_timeout_seconds: float = 90.0,
    ) -> None:
        if not 0.1 <= client_ready_timeout_seconds <= 30.0:
            raise ValueError("client-ready timeout must be between 0.1 and 30 seconds")
        if not 0.01 <= turn_timeout_seconds <= 7200.0:
            raise ValueError("session timeout must be between 0.01 and 7200 seconds")
        self._client_ready_timeout_seconds = client_ready_timeout_seconds
        self._turn_timeout_seconds = turn_timeout_seconds

    async def serve(
        self,
        *,
        websocket: WebSocket,
        coordinator: RealtimeSessionCoordinator,
        session_id: str,
    ) -> None:
        try:
            first = await asyncio.wait_for(
                self._receive_client_event(websocket),
                timeout=self._client_ready_timeout_seconds,
            )
        except TimeoutError as error:
            raise RealtimeWebSocketProtocolError(
                "clientReady was not received before the session deadline"
            ) from error
        if not isinstance(first, ClientReadyEvent):
            raise RealtimeWebSocketProtocolError(
                "clientReady must be the first WebSocket control frame"
            )
        self._require_session(first, session_id)
        await self._receive_into_coordinator(coordinator, first)

        sender = asyncio.create_task(
            self._send_frames(websocket=websocket, coordinator=coordinator),
            name="ava-realtime-server-frames",
        )
        receiver = asyncio.create_task(
            self._receive_controls(
                websocket=websocket,
                coordinator=coordinator,
                session_id=session_id,
            ),
            name="ava-realtime-client-controls",
        )
        tasks = {sender, receiver}
        try:
            done, _ = await asyncio.wait_for(
                asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                ),
                timeout=self._turn_timeout_seconds,
            )
            results = await asyncio.gather(*done, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        except TimeoutError as error:
            raise RealtimeWebSocketSessionTimeout(
                "Ava realtime conversation exceeded its server deadline"
            ) from error
        finally:
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _send_frames(
        self,
        *,
        websocket: WebSocket,
        coordinator: RealtimeSessionCoordinator,
    ) -> None:
        pending_audio: AudioChunkEvent | None = None
        async for frame in coordinator.frames():
            if isinstance(frame, AvaServerControlFrame):
                if pending_audio is not None:
                    raise RealtimeWebSocketProtocolError(
                        "audioChunk was not followed immediately by binary PCM"
                    )
                await websocket.send_text(serialize_server_event(frame.event))
                if isinstance(frame.event, AudioChunkEvent):
                    pending_audio = frame.event
                continue

            if not isinstance(frame, AvaServerPcmFrame):
                raise RealtimeWebSocketProtocolError(
                    f"Unsupported coordinator frame {type(frame).__name__}"
                )
            if pending_audio is None or frame.descriptor != pending_audio:
                raise RealtimeWebSocketProtocolError(
                    "Binary PCM does not match the pending audioChunk"
                )
            if len(frame.pcm) != pending_audio.byte_length:
                raise RealtimeWebSocketProtocolError(
                    "Binary PCM length does not match audioChunk byteLength"
                )
            await websocket.send_bytes(frame.pcm)
            pending_audio = None

        if pending_audio is not None:
            raise RealtimeWebSocketProtocolError(
                "Coordinator ended with unresolved binary PCM"
            )

    async def _receive_controls(
        self,
        *,
        websocket: WebSocket,
        coordinator: RealtimeSessionCoordinator,
        session_id: str,
    ) -> None:
        while True:
            event = await self._receive_client_event(websocket)
            self._require_session(event, session_id)
            await self._receive_into_coordinator(coordinator, event)

    async def _receive_client_event(
        self,
        websocket: WebSocket,
    ) -> AvaClientEventUnion:
        message = await websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            raise WebSocketDisconnect(
                code=int(message.get("code") or 1000),
                reason=str(message.get("reason") or ""),
            )
        text = message.get("text")
        if not isinstance(text, str):
            raise RealtimeWebSocketProtocolError(
                "Client WebSocket controls must be JSON text frames"
            )
        if len(text.encode("utf-8")) > self.max_client_control_bytes:
            raise RealtimeWebSocketProtocolError(
                "Client WebSocket control exceeds 65536 bytes"
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise RealtimeWebSocketProtocolError(
                "Client WebSocket control is malformed JSON"
            ) from error
        if not isinstance(payload, dict):
            raise RealtimeWebSocketProtocolError(
                "Client WebSocket control must be a JSON object"
            )
        try:
            return AVA_CLIENT_EVENT_ADAPTER.validate_python(payload)
        except ValidationError as error:
            issues = error.errors(include_url=False, include_context=False)
            if issues:
                first_issue = issues[0]
                location = ".".join(str(part) for part in first_issue["loc"])
                issue_type = first_issue["type"]
            else:
                location = "root"
                issue_type = "unknown"
            raise RealtimeWebSocketProtocolError(
                "Client WebSocket control failed protocol-v3 validation "
                f"(issue_count={len(issues)}, first_path={location or 'root'}, "
                f"first_type={issue_type})"
            ) from error

    async def _receive_into_coordinator(
        self,
        coordinator: RealtimeSessionCoordinator,
        event: AvaClientEventUnion,
    ) -> None:
        try:
            await coordinator.receive(event)
        except CoordinatorStateError as error:
            # Protocol models intentionally materialize enum values as plain
            # strings. Diagnostics must never raise while reporting the
            # original coordinator rejection, otherwise a recoverable state
            # mismatch is misreported as a backend 1011 failure.
            event_name = str(event.event)
            turn_id = getattr(event, "turn_id", None)
            LOGGER.warning(
                "realtime_client_control_rejected event=%s sequence=%d "
                "turn_id=%s coordinator_reason=%s",
                event_name,
                event.sequence,
                turn_id,
                str(error),
            )
            raise RealtimeWebSocketProtocolError(
                "Client WebSocket control violated coordinator state "
                f"(event={event_name}, sequence={event.sequence}, "
                f"turn_id={turn_id}, reason={error})"
            ) from error

    def _require_session(
        self,
        event: AvaClientEventUnion,
        session_id: str,
    ) -> None:
        if event.session_id != session_id:
            raise RealtimeWebSocketProtocolError(
                "Client WebSocket control changed sessionId"
            )
