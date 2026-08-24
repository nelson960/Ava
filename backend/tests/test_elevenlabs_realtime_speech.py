from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping

import pytest
from pydantic import SecretStr

from ava_backend.config import AvaEnvironment, Settings
from ava_backend.providers.elevenlabs_realtime_speech import (
    ElevenLabsInputStreamingError,
    ElevenLabsInputStreamingSynthesizer,
)
from ava_backend.realtime.delivery import (
    SynthesizedAlignmentBatch,
    SynthesizedAudioChunk,
)
from ava_backend.realtime.speech_contract import SpeechPhrase


def _settings() -> Settings:
    return Settings(
        environment=AvaEnvironment.TEST,
        elevenlabs_api_key=SecretStr("elevenlabs-test-key"),
        elevenlabs_voice_id="voiceCandidateB123",
        elevenlabs_model_id="eleven_flash_v2_5",
        elevenlabs_commercial_rights_confirmed=True,
    )


def _segment(speech: str = "Hi.") -> SpeechPhrase:
    return SpeechPhrase.model_validate(
        {
            "id": "answer",
            "speech": speech,
            "delivery": "warm",
            "expression": "warmSmile",
            "gaze": "child",
            "motionIntent": "none",
            "canvasCues": [],
        }
    )


def _audio_message(
    *,
    pcm: bytes,
    chars: list[str],
    starts: list[int],
    durations: list[int],
) -> str:
    return json.dumps(
        {
            "audio": base64.b64encode(pcm).decode("ascii"),
            "alignment": {
                "chars": chars,
                "char_start_times_ms": starts,
                "char_durations_ms": durations,
            },
            "normalized_alignment": None,
            "is_final": False,
        }
    )


class _Socket:
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self.close_reasons: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if not self.messages:
            raise RuntimeError("fake ElevenLabs socket ran out of messages")
        return self.messages.pop(0)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        assert code == 1000
        self.closed = True
        self.close_reasons.append(reason)


async def test_streams_phrase_pcm_and_exact_character_alignment() -> None:
    socket = _Socket(
        [
            _audio_message(
                pcm=bytes(96),
                chars=["H", "i"],
                starts=[0, 1],
                durations=[1, 1],
            ),
            _audio_message(
                pcm=bytes(48),
                chars=["."],
                starts=[0],
                durations=[1],
            ),
            '{"is_final":true}',
        ]
    )
    captured: dict[str, object] = {}

    async def connect(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_event_bytes: int,
    ) -> _Socket:
        captured.update(
            url=url,
            headers=dict(headers),
            timeout=timeout_seconds,
            max_event_bytes=max_event_bytes,
        )
        return socket

    provider = ElevenLabsInputStreamingSynthesizer(
        _settings(),
        websocket_connector=connect,
        websocket_origin="wss://elevenlabs.test",
    )
    items = [item async for item in provider.synthesize(_segment(), turn_id="turn-1")]

    assert provider.model_id == "eleven_flash_v2_5"
    assert provider.voice_id == "voiceCandidateB123"
    assert captured["headers"] == {"xi-api-key": "elevenlabs-test-key"}
    assert captured["url"] == (
        "wss://elevenlabs.test/v1/text-to-speech/voiceCandidateB123/"
        "stream-input?model_id=eleven_flash_v2_5&output_format=pcm_24000&"
        "sync_alignment=true&auto_mode=true&apply_text_normalization=auto"
    )
    assert socket.sent[0] == {
        "text": " ",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": 1.0,
        },
    }
    assert socket.sent[1] == {"text": "Hi.", "flush": True}
    assert socket.sent[2] == {"text": ""}
    alignments = [item for item in items if isinstance(item, SynthesizedAlignmentBatch)]
    audio = [item for item in items if isinstance(item, SynthesizedAudioChunk)]
    assert [span.text_start for batch in alignments for span in batch.spans] == [
        0,
        1,
        2,
    ]
    assert [span.sample_start for batch in alignments for span in batch.spans] == [
        0,
        24,
        48,
    ]
    assert [len(item.pcm) for item in audio] == [96, 48]
    assert socket.closed


async def test_rejects_alignment_that_changes_authored_phrase() -> None:
    socket = _Socket(
        [
            _audio_message(
                pcm=bytes(48),
                chars=["N"],
                starts=[0],
                durations=[1],
            )
        ]
    )

    async def connect(
        _url: str,
        _headers: Mapping[str, str],
        _timeout_seconds: float,
        _max_event_bytes: int,
    ) -> _Socket:
        return socket

    provider = ElevenLabsInputStreamingSynthesizer(
        _settings(),
        websocket_connector=connect,
        websocket_origin="wss://elevenlabs.test",
    )
    with pytest.raises(ElevenLabsInputStreamingError, match="authored phrase"):
        _ = [item async for item in provider.synthesize(_segment(), turn_id="turn-1")]
    assert socket.closed


async def test_splits_large_alignment_into_transport_bounded_batches() -> None:
    speech = "a" * 513
    socket = _Socket(
        [
            _audio_message(
                pcm=bytes(513 * 48),
                chars=list(speech),
                starts=list(range(513)),
                durations=[1] * 513,
            ),
            '{"is_final":true}',
        ]
    )

    async def connect(
        _url: str,
        _headers: Mapping[str, str],
        _timeout_seconds: float,
        _max_event_bytes: int,
    ) -> _Socket:
        return socket

    provider = ElevenLabsInputStreamingSynthesizer(
        _settings(),
        websocket_connector=connect,
        websocket_origin="wss://elevenlabs.test",
    )
    items = [
        item
        async for item in provider.synthesize(
            _segment(speech),
            turn_id="turn-1",
        )
    ]

    alignments = [item for item in items if isinstance(item, SynthesizedAlignmentBatch)]
    assert [len(batch.spans) for batch in alignments] == [512, 1]


async def test_rejects_concurrent_phrase_synthesis() -> None:
    entered = asyncio.Event()

    class _BlockedSocket(_Socket):
        async def recv(self) -> str:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    sockets = [_BlockedSocket([]), _Socket([])]

    async def connect(
        _url: str,
        _headers: Mapping[str, str],
        _timeout_seconds: float,
        _max_event_bytes: int,
    ) -> _Socket:
        return sockets.pop(0)

    provider = ElevenLabsInputStreamingSynthesizer(
        _settings(),
        websocket_connector=connect,
        websocket_origin="wss://elevenlabs.test",
    )
    first_stream = provider.synthesize(_segment(), turn_id="turn-1")
    first = asyncio.create_task(first_stream.__anext__())
    await entered.wait()
    with pytest.raises(ElevenLabsInputStreamingError, match="cannot overlap"):
        _ = [
            item
            async for item in provider.synthesize(
                _segment("Bye."),
                turn_id="turn-1",
            )
        ]
    await provider.cancel(turn_id="turn-1")
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    await provider.close()


async def test_stale_previous_turn_cancel_cannot_close_current_turn_socket() -> None:
    entered = asyncio.Event()

    class _BlockedSocket(_Socket):
        async def recv(self) -> str:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    socket = _BlockedSocket([])

    async def connect(
        _url: str,
        _headers: Mapping[str, str],
        _timeout_seconds: float,
        _max_event_bytes: int,
    ) -> _Socket:
        return socket

    provider = ElevenLabsInputStreamingSynthesizer(
        _settings(),
        websocket_connector=connect,
        websocket_origin="wss://elevenlabs.test",
    )
    current_stream = provider.synthesize(_segment(), turn_id="turn-current")
    current = asyncio.create_task(current_stream.__anext__())
    await entered.wait()

    try:
        await provider.cancel(turn_id="turn-previous")
        assert not socket.closed
    finally:
        current.cancel()
        await asyncio.gather(current, return_exceptions=True)
        await provider.close()


async def test_cancel_during_connect_revokes_turn_before_socket_registration() -> None:
    connect_started = asyncio.Event()
    connect_release = asyncio.Event()
    socket = _Socket([])

    async def connect(
        _url: str,
        _headers: Mapping[str, str],
        _timeout_seconds: float,
        _max_event_bytes: int,
    ) -> _Socket:
        connect_started.set()
        await connect_release.wait()
        return socket

    provider = ElevenLabsInputStreamingSynthesizer(
        _settings(),
        websocket_connector=connect,
        websocket_origin="wss://elevenlabs.test",
    )
    stream = provider.synthesize(_segment(), turn_id="turn-cancelled")
    first = asyncio.create_task(stream.__anext__())
    await connect_started.wait()

    await provider.cancel(turn_id="turn-cancelled")
    connect_release.set()

    with pytest.raises(StopAsyncIteration):
        await first
    assert socket.closed
    assert any("no longer owns this turn" in reason for reason in socket.close_reasons)
    await provider.close()
