from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from ava_backend.realtime.events_v4 import (
    AVA_SERVER_EVENT_ADAPTER_V4,
    AudioChunkEventV4,
)
from ava_backend.realtime.websocket_v4 import (
    AvaRealtimeWebSocketBridgeV4,
    AvaServerControlFrameV4,
    AvaServerOutboundFrameV4,
    AvaServerPcmFrameV4,
    RealtimeWebSocketProtocolErrorV4,
)


class _Socket:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str | bytes]] = []

    async def send_text(self, value: str) -> None:
        self.sent.append(("text", value))

    async def send_bytes(self, value: bytes) -> None:
        self.sent.append(("bytes", value))


class _Coordinator:
    def __init__(self, frames: list[AvaServerOutboundFrameV4]) -> None:
        self._frames = frames

    async def frames(self) -> AsyncIterator[AvaServerOutboundFrameV4]:
        for frame in self._frames:
            yield frame


def _audio_descriptor() -> AudioChunkEventV4:
    return AudioChunkEventV4.model_validate(
        {
            "protocolVersion": 4,
            "event": "audioChunk",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "sequence": 4,
            "revision": 1,
            "streamId": "stream-1",
            "chunkIndex": 0,
            "sampleOffset": 0,
            "sampleCount": 2,
            "byteLength": 4,
        }
    )


def _canvas_available():
    return AVA_SERVER_EVENT_ADAPTER_V4.validate_python(
        {
            "protocolVersion": 4,
            "event": "canvasDocumentAvailable",
            "sessionId": "session-1",
            "sequence": 5,
            "revision": 2,
            "generationId": "generation-1",
            "sourceTurnId": "turn-1",
            "sceneId": "scene-1",
            "sceneRevision": 1,
            "svgPath": "/v4/realtime/sessions/session-1/canvas-documents/generation-1/svg",
            "manifestPath": "/v4/realtime/sessions/session-1/canvas-documents/generation-1/manifest",
            "svgSha256": "a" * 64,
            "manifestSha256": "b" * 64,
            "svgByteLength": 4096,
            "manifestByteLength": 512,
            "rendererCapabilityVersion": "ava-svg-renderer-v2",
            "validatorVersion": "ava-svg-validator-v2",
            "manifestVersion": "ava-svg-manifest-v2",
        }
    )


async def test_socket_carries_pcm_but_only_canvas_document_descriptors() -> None:
    descriptor = _audio_descriptor()
    socket = _Socket()
    coordinator = _Coordinator(
        [
            AvaServerControlFrameV4(event=descriptor),
            AvaServerPcmFrameV4(descriptor=descriptor, pcm=b"\x00\x00\x01\x00"),
            AvaServerControlFrameV4(event=_canvas_available()),
        ]
    )

    await AvaRealtimeWebSocketBridgeV4()._send_frames(  # noqa: SLF001
        websocket=socket,  # type: ignore[arg-type]
        coordinator=coordinator,  # type: ignore[arg-type]
    )

    assert [kind for kind, _ in socket.sent] == ["text", "bytes", "text"]
    canvas_control = socket.sent[2][1]
    assert isinstance(canvas_control, str)
    assert "canvasDocumentAvailable" in canvas_control
    assert "<svg" not in canvas_control


async def test_no_control_can_split_pcm_descriptor_from_binary_frame() -> None:
    descriptor = _audio_descriptor()
    coordinator = _Coordinator(
        [
            AvaServerControlFrameV4(event=descriptor),
            AvaServerControlFrameV4(event=_canvas_available()),
        ]
    )

    with pytest.raises(RealtimeWebSocketProtocolErrorV4, match="immediately"):
        await AvaRealtimeWebSocketBridgeV4()._send_frames(  # noqa: SLF001
            websocket=_Socket(),  # type: ignore[arg-type]
            coordinator=coordinator,  # type: ignore[arg-type]
        )
