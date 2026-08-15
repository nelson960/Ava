from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass

from ..canvas.specialist import (
    CanvasSpecialistGeneration,
    CanvasSpecialistRequest,
)
from .delivery import (
    SynthesizedAlignmentBatch,
    SynthesizedAudioChunk,
    SynthesizedSpeechItem,
)
from .events import AvaAlignmentSpan
from .events import AvaServerEventUnion
from .harness import RealtimeHarness
from .model_plan import ModelCanvasDraft
from .plan import AvaTurnPlan, AvaTurnSegment
from .provider_session import (
    NegotiatedRealtimeCall,
    RealtimeProviderTurnEvent,
    validate_sdp,
)
from .tool_contract import (
    InteractiveCanvasRequest,
    InteractiveCanvasToolOutput,
    RealtimeCanvasToolCall,
    RealtimeRejectedCanvasToolCall,
    RealtimeSpeechTextDelta,
)


DETERMINISTIC_AUDIO_OFFER_SDP = (
    "v=0\r\n"
    "o=- 1000 2 IN IP4 127.0.0.1\r\n"
    "s=Ava deterministic offer\r\n"
    "t=0 0\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    "a=mid:0\r\n"
    "a=sendonly\r\n"
    "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
    "a=mid:1\r\n"
    "a=sctp-port:5000\r\n"
)

DETERMINISTIC_AUDIO_ANSWER_SDP = (
    "v=0\r\n"
    "o=- 2000 2 IN IP4 127.0.0.1\r\n"
    "s=Ava deterministic answer\r\n"
    "t=0 0\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    "a=mid:0\r\n"
    "a=recvonly\r\n"
    "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
    "a=mid:1\r\n"
    "a=sctp-port:5000\r\n"
)


@dataclass(frozen=True, slots=True)
class DeterministicPlanningRequest:
    turn_id: str
    child_text: str | None
    session_update: dict[str, object]


class DeterministicArgumentDeltaStream:
    """Test provider that fragments one JSON payload at declared boundaries."""

    def __init__(self, payload: str, chunk_sizes: Iterable[int]) -> None:
        sizes = tuple(chunk_sizes)
        if not payload:
            raise ValueError("fake argument payload cannot be empty")
        if not sizes or any(size <= 0 for size in sizes):
            raise ValueError("fake chunk sizes must be positive")
        self._payload = payload
        self._sizes = sizes

    async def stream(self) -> AsyncIterator[str]:
        cursor = 0
        index = 0
        while cursor < len(self._payload):
            size = self._sizes[index % len(self._sizes)]
            yield self._payload[cursor : cursor + size]
            cursor += size
            index += 1
            await asyncio.sleep(0)


class DeterministicRealtimePlanProvider:
    """Fake semantic Realtime stream that records its prompt and tool feedback."""

    def __init__(
        self,
        plan: AvaTurnPlan,
        *,
        child_text: str | None = "Deterministic child turn.",
        canvas_brief: str | None = None,
        rejected_canvas_reason: str | None = None,
        chunk_sizes: Iterable[int] = (17, 5, 43, 2),
    ) -> None:
        normalized_child_text = child_text.strip() if child_text is not None else None
        if normalized_child_text is not None and (
            not normalized_child_text or len(normalized_child_text) > 2_000
        ):
            raise ValueError("fake child_text must contain 1-2000 characters")
        canvas_needed = plan.canvas_decision.complexity == "specialistRequired" or any(
            segment.canvas_cues for segment in plan.segments
        )
        if canvas_needed and canvas_brief is None:
            canvas_brief = plan.canvas_decision.specialist_brief
        if canvas_needed != (canvas_brief is not None):
            raise ValueError("fake canvas brief must match the runtime plan")
        self._speech_segments = tuple(segment.speech for segment in plan.segments)
        self._canvas_request = (
            InteractiveCanvasRequest.model_validate(
                {
                    "brief": canvas_brief,
                    "interactionGoal": (
                        "Let the child reveal or select one useful label."
                    ),
                }
            )
            if canvas_brief is not None
            else None
        )
        self._child_text = normalized_child_text
        self._rejected_canvas_reason = rejected_canvas_reason
        self._chunk_sizes = tuple(chunk_sizes)
        if not self._chunk_sizes or any(size <= 0 for size in self._chunk_sizes):
            raise ValueError("fake Realtime chunk sizes must be positive")
        self.requests: list[DeterministicPlanningRequest] = []
        self.prepared_session_updates: list[dict[str, object]] = []
        self.negotiated_offers: list[str] = []
        self.yield_count = 0
        self.cancelled_turn_ids: list[str] = []
        self.completed_canvas_outputs: list[InteractiveCanvasToolOutput] = []
        self._pending_call_id: str | None = None
        self.closed = False

    async def negotiate(self, *, offer_sdp: str) -> NegotiatedRealtimeCall:
        if self.closed:
            raise RuntimeError("fake Realtime provider session is closed")
        validate_sdp(offer_sdp, description="offer")
        if self.negotiated_offers:
            raise RuntimeError("fake Realtime provider may negotiate only once")
        self.negotiated_offers.append(offer_sdp)
        await asyncio.sleep(0)
        return NegotiatedRealtimeCall(
            answer_sdp=DETERMINISTIC_AUDIO_ANSWER_SDP,
            provider_call_id="rtc_fake_ava_call_0001",
        )

    def turn_events(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.requests.append(
            DeterministicPlanningRequest(
                turn_id=turn_id,
                child_text=self._child_text,
                session_update=harness.session_update(),
            )
        )
        return self._stream()

    async def prepare(self, *, harness: RealtimeHarness) -> None:
        if self.closed:
            raise RuntimeError("fake Realtime provider session is closed")
        update = harness.session_update()
        if (
            not self.prepared_session_updates
            or self.prepared_session_updates[-1] != update
        ):
            self.prepared_session_updates.append(update)
        await asyncio.sleep(0)

    async def child_transcript(self, *, turn_id: str) -> str | None:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        await asyncio.sleep(0)
        return self._child_text

    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        for index, speech in enumerate(self._speech_segments):
            stream = DeterministicArgumentDeltaStream(
                speech + (" " if index < len(self._speech_segments) - 1 else ""),
                self._chunk_sizes,
            )
            async for delta in stream.stream():
                self.yield_count += 1
                yield RealtimeSpeechTextDelta(delta=delta)
            if index == 0 and self._canvas_request is not None:
                self._pending_call_id = "call_canvas_fake_0001"
                if self._rejected_canvas_reason is None:
                    yield RealtimeCanvasToolCall(
                        call_id=self._pending_call_id,
                        request=self._canvas_request,
                    )
                else:
                    yield RealtimeRejectedCanvasToolCall(
                        call_id=self._pending_call_id,
                        reason=self._rejected_canvas_reason,
                    )
                if not self.completed_canvas_outputs:
                    raise RuntimeError("fake canvas tool call was not completed")
                self._pending_call_id = None

    async def complete_canvas_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: InteractiveCanvasToolOutput,
    ) -> None:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        if call_id != self._pending_call_id:
            raise RuntimeError("fake canvas completion does not match pending call")
        self.completed_canvas_outputs.append(output)
        await asyncio.sleep(0)

    async def cancel(self, *, turn_id: str) -> None:
        if turn_id not in self.cancelled_turn_ids:
            self.cancelled_turn_ids.append(turn_id)

    async def close(self) -> None:
        self.closed = True


class DeterministicCanvasSpecialist:
    """Strict fake canvas specialist with observable request/cancel behavior."""

    def __init__(
        self,
        draft: ModelCanvasDraft,
        *,
        error: Exception | None = None,
    ) -> None:
        self._draft = draft
        self._error = error
        self.requests: list[CanvasSpecialistRequest] = []
        self.request_started = asyncio.Event()
        self.cancelled_turn_ids: list[str] = []
        self.closed = False

    async def generate(
        self,
        request: CanvasSpecialistRequest,
    ) -> CanvasSpecialistGeneration:
        if self.closed:
            raise RuntimeError("fake canvas specialist is closed")
        self.requests.append(request)
        self.request_started.set()
        await asyncio.sleep(0)
        if self._error is not None:
            raise self._error
        return CanvasSpecialistGeneration(
            draft=self._draft,
            provider_request_id="resp_fake_canvas_0001",
            input_tokens=120,
            output_tokens=240,
            cached_input_tokens=80,
        )

    async def cancel(self, *, turn_id: str) -> None:
        if turn_id not in self.cancelled_turn_ids:
            self.cancelled_turn_ids.append(turn_id)

    async def close(self) -> None:
        self.closed = True


class DeterministicStreamingSpeechSynthesizer:
    """Fake ElevenLabs stream with word alignment and deterministic PCM."""

    def __init__(
        self,
        *,
        model_id: str = "fake-elevenlabs-model",
        voice_id: str = "fake-ava-voice",
        samples_per_character: int = 4,
        chunk_sample_count: int = 64,
    ) -> None:
        if samples_per_character <= 0:
            raise ValueError("samples_per_character must be positive")
        if not 1 <= chunk_sample_count <= 96_000:
            raise ValueError("chunk_sample_count must be between 1 and 96000")
        self._model_id = model_id
        self._voice_id = voice_id
        self._samples_per_character = samples_per_character
        self._chunk_sample_count = chunk_sample_count
        self.synthesized_segment_ids: list[str] = []
        self.cancelled_turn_ids: list[str] = []
        self.closed = False

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def voice_id(self) -> str:
        return self._voice_id

    async def synthesize(
        self,
        segment: AvaTurnSegment,
    ) -> AsyncIterator[SynthesizedSpeechItem]:
        if self.closed:
            raise RuntimeError("fake streaming speech session is closed")
        self.synthesized_segment_ids.append(segment.id)
        spans = tuple(
            AvaAlignmentSpan.model_validate(
                {
                    "segmentId": segment.id,
                    "textStart": match.start(),
                    "textEnd": match.end(),
                    "sampleStart": match.start() * self._samples_per_character,
                    "sampleEnd": match.end() * self._samples_per_character,
                    "phoneme": None,
                }
            )
            for match in re.finditer(r"\S+", segment.speech)
        )
        if not spans:
            raise ValueError("fake TTS requires non-whitespace segment speech")
        yield SynthesizedAlignmentBatch(spans)

        total_samples = len(segment.speech) * self._samples_per_character
        remaining = total_samples
        while remaining:
            sample_count = min(remaining, self._chunk_sample_count)
            yield SynthesizedAudioChunk(bytes(sample_count * 2))
            remaining -= sample_count
            await asyncio.sleep(0)

    async def cancel(self, *, turn_id: str) -> None:
        if turn_id not in self.cancelled_turn_ids:
            self.cancelled_turn_ids.append(turn_id)

    async def close(self) -> None:
        self.closed = True


class DeterministicServerEventStream:
    def __init__(self, events: Iterable[AvaServerEventUnion]) -> None:
        self._events = tuple(events)
        if not self._events:
            raise ValueError("fake server event stream cannot be empty")

    async def stream(self) -> AsyncIterator[AvaServerEventUnion]:
        for event in self._events:
            yield event
            await asyncio.sleep(0)
