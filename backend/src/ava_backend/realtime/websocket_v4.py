from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..protocol import AgeBand
from .events_v4 import (
    AVA_CLIENT_EVENT_ADAPTER_V4,
    AudioChunkEventV4,
    AvaClientEventUnionV4,
    AvaServerEventUnionV4,
    ClientReadyEventV4,
    serialize_server_event_v4,
)
from .provider_session import NegotiatedRealtimeCall

LOGGER = logging.getLogger("ava_backend.realtime_websocket_v4")


class RealtimeWebSocketProtocolErrorV4(RuntimeError):
    """A client or coordinator violated the protocol-v4 socket contract."""


class RealtimeWebSocketSessionTimeoutV4(RuntimeError):
    """The bounded server-side conversation deadline elapsed."""


@dataclass(frozen=True, slots=True)
class AvaServerControlFrameV4:
    event: AvaServerEventUnionV4


@dataclass(frozen=True, slots=True)
class AvaServerPcmFrameV4:
    descriptor: AudioChunkEventV4
    pcm: bytes

    def __post_init__(self) -> None:
        if len(self.pcm) != self.descriptor.byte_length:
            raise ValueError("binary PCM length does not match audioChunk byteLength")


AvaServerOutboundFrameV4: TypeAlias = AvaServerControlFrameV4 | AvaServerPcmFrameV4


class RealtimeSessionCoordinatorV4(Protocol):
    def frames(self) -> AsyncIterator[AvaServerOutboundFrameV4]: ...

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall: ...

    async def receive(self, event: AvaClientEventUnionV4) -> None: ...

    async def close(self) -> None: ...

    @property
    def turn_id(self) -> str: ...


class RealtimeCoordinatorFactoryV4(Protocol):
    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
    ) -> RealtimeSessionCoordinatorV4: ...


class AvaRealtimeWebSocketBridgeV4:
    max_client_control_bytes = 64 * 1024

    def __init__(
        self,
        *,
        client_ready_timeout_seconds: float = 10.0,
        session_timeout_seconds: float = 1_800.0,
    ) -> None:
        if not 0.1 <= client_ready_timeout_seconds <= 30.0:
            raise ValueError("client-ready timeout must be between 0.1 and 30 seconds")
        if not 0.01 <= session_timeout_seconds <= 7_200.0:
            raise ValueError("session timeout must be between 0.01 and 7200 seconds")
        self._client_ready_timeout_seconds = client_ready_timeout_seconds
        self._session_timeout_seconds = session_timeout_seconds

    async def serve(
        self,
        *,
        websocket: WebSocket,
        coordinator: RealtimeSessionCoordinatorV4,
        session_id: str,
    ) -> None:
        try:
            first = await asyncio.wait_for(
                self._receive_client_event(websocket),
                timeout=self._client_ready_timeout_seconds,
            )
        except TimeoutError as error:
            raise RealtimeWebSocketProtocolErrorV4(
                "clientReady was not received before the session deadline"
            ) from error
        if not isinstance(first, ClientReadyEventV4):
            raise RealtimeWebSocketProtocolErrorV4(
                "clientReady must be the first WebSocket control frame"
            )
        self._require_session(first, session_id)
        await coordinator.receive(first)

        sender = asyncio.create_task(
            self._send_frames(websocket=websocket, coordinator=coordinator),
            name="ava-realtime-v4-server-frames",
        )
        receiver = asyncio.create_task(
            self._receive_controls(
                websocket=websocket,
                coordinator=coordinator,
                session_id=session_id,
            ),
            name="ava-realtime-v4-client-controls",
        )
        tasks = {sender, receiver}
        try:
            done, _ = await asyncio.wait_for(
                asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED),
                timeout=self._session_timeout_seconds,
            )
            results = await asyncio.gather(*done, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        except TimeoutError as error:
            raise RealtimeWebSocketSessionTimeoutV4(
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
        coordinator: RealtimeSessionCoordinatorV4,
    ) -> None:
        pending_audio: AudioChunkEventV4 | None = None
        async for frame in coordinator.frames():
            if isinstance(frame, AvaServerControlFrameV4):
                if pending_audio is not None:
                    raise RealtimeWebSocketProtocolErrorV4(
                        "audioChunk was not followed immediately by binary PCM"
                    )
                await websocket.send_text(serialize_server_event_v4(frame.event))
                if isinstance(frame.event, AudioChunkEventV4):
                    pending_audio = frame.event
                continue
            if not isinstance(frame, AvaServerPcmFrameV4):
                raise RealtimeWebSocketProtocolErrorV4(
                    f"unsupported coordinator frame {type(frame).__name__}"
                )
            if pending_audio is None or frame.descriptor != pending_audio:
                raise RealtimeWebSocketProtocolErrorV4(
                    "binary PCM does not match the pending audioChunk"
                )
            if len(frame.pcm) != pending_audio.byte_length:
                raise RealtimeWebSocketProtocolErrorV4(
                    "binary PCM length does not match audioChunk byteLength"
                )
            await websocket.send_bytes(frame.pcm)
            pending_audio = None
        if pending_audio is not None:
            raise RealtimeWebSocketProtocolErrorV4(
                "coordinator ended with unresolved binary PCM"
            )

    async def _receive_controls(
        self,
        *,
        websocket: WebSocket,
        coordinator: RealtimeSessionCoordinatorV4,
        session_id: str,
    ) -> None:
        while True:
            event = await self._receive_client_event(websocket)
            self._require_session(event, session_id)
            await coordinator.receive(event)

    async def _receive_client_event(
        self,
        websocket: WebSocket,
    ) -> AvaClientEventUnionV4:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(
                code=int(message.get("code") or 1000),
                reason=str(message.get("reason") or ""),
            )
        text = message.get("text")
        if not isinstance(text, str):
            raise RealtimeWebSocketProtocolErrorV4(
                "client WebSocket controls must be JSON text frames"
            )
        if len(text.encode("utf-8")) > self.max_client_control_bytes:
            raise RealtimeWebSocketProtocolErrorV4(
                "client WebSocket control exceeds 65536 bytes"
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise RealtimeWebSocketProtocolErrorV4(
                "client WebSocket control is malformed JSON"
            ) from error
        if not isinstance(payload, dict):
            raise RealtimeWebSocketProtocolErrorV4(
                "client WebSocket control must be a JSON object"
            )
        try:
            return AVA_CLIENT_EVENT_ADAPTER_V4.validate_python(payload)
        except ValidationError as error:
            issues = error.errors(include_url=False, include_context=False)
            if issues:
                first_location = issues[0]["loc"]
                first_type = issues[0]["type"]
            else:  # Defensive: Pydantic validation errors normally have issues.
                first_location = ()
                first_type = "unknown"
            location = ".".join(str(part) for part in first_location)
            raise RealtimeWebSocketProtocolErrorV4(
                "client WebSocket control failed protocol-v4 validation "
                f"(issue_count={len(issues)}, first_path={location or 'root'}, "
                f"first_type={first_type})"
            ) from error

    @staticmethod
    def _require_session(event: AvaClientEventUnionV4, session_id: str) -> None:
        if event.session_id != session_id:
            raise RealtimeWebSocketProtocolErrorV4(
                "client WebSocket control changed sessionId"
            )
