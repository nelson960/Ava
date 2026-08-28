from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass

from ..canvas.svg_workspace import RecordedSvgCanvasInteraction
from .delivery import (
    SynthesizedAlignmentBatch,
    SynthesizedAudioChunk,
    SynthesizedSpeechItem,
)
from .harness import RealtimeHarness
from .plan import AvaTurnPlan
from .speech_contract import SpeechAlignment, SpeechPhrase
from .provider_session import (
    NegotiatedRealtimeCall,
    RealtimeProviderInputEvent,
    RealtimeProviderTurnEvent,
    validate_sdp,
)
from .tool_contract import (
    InteractiveCanvasRequest,
    InteractiveCanvasToolOutput,
    InteractiveCanvasToolOutputV4,
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
        wait_for_canvas_interaction_response: bool = False,
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
                    "operation": "new",
                    "brief": canvas_brief,
                    "interactionMode": "explore",
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
        self._wait_for_canvas_interaction_response = (
            wait_for_canvas_interaction_response
        )
        self._chunk_sizes = tuple(chunk_sizes)
        if not self._chunk_sizes or any(size <= 0 for size in self._chunk_sizes):
            raise ValueError("fake Realtime chunk sizes must be positive")
        self.requests: list[DeterministicPlanningRequest] = []
        self.prepared_session_updates: list[dict[str, object]] = []
        self.negotiated_offers: list[str] = []
        self.yield_count = 0
        self.cancelled_turn_ids: list[str] = []
        self.completed_canvas_outputs: list[
            InteractiveCanvasToolOutput | InteractiveCanvasToolOutputV4
        ] = []
        self.canvas_interaction_response_turn_ids: list[str] = []
        self.canvas_interaction_response_interactions: list[
            RecordedSvgCanvasInteraction
        ] = []
        self.canvas_interaction_response_session_updates: list[
            dict[str, object]
        ] = []
        self.turn_stream_started = asyncio.Event()
        self._canvas_interaction_response_requested = asyncio.Event()
        self._interaction_response_readiness_changed = asyncio.Event()
        self._active_turn_id: str | None = None
        self._pending_call_id: str | None = None
        self._input_events: asyncio.Queue[RealtimeProviderInputEvent] = asyncio.Queue()
        self._input_item_turn_ids: dict[str, str | None] = {}
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
        self._canvas_interaction_response_requested.clear()
        self._active_turn_id = turn_id
        self._interaction_response_readiness_changed.set()

        async def _owned_stream() -> AsyncIterator[RealtimeProviderTurnEvent]:
            try:
                async for event in self._stream():
                    yield event
            finally:
                if self._active_turn_id == turn_id:
                    self._active_turn_id = None
                    self._interaction_response_readiness_changed.set()

        return _owned_stream()

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

    async def next_input_event(self) -> RealtimeProviderInputEvent:
        return await self._input_events.get()

    def input_item_owner(self, *, item_id: str) -> str | None:
        if item_id not in self._input_item_turn_ids:
            raise ValueError("fake input item ownership requested before observation")
        return self._input_item_turn_ids[item_id]

    def emit_input_event(
        self,
        event: RealtimeProviderInputEvent,
        *,
        owner_turn_id: str | None = None,
    ) -> None:
        existing = self._input_item_turn_ids.setdefault(event.item_id, owner_turn_id)
        if owner_turn_id is not None and existing != owner_turn_id:
            raise ValueError("fake provider input item changed turn ownership")
        self._input_events.put_nowait(event)

    async def admit_pending_input_items(self, *, turn_id: str) -> tuple[str, ...]:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        pending = tuple(
            item_id
            for item_id, owner in self._input_item_turn_ids.items()
            if owner is None
        )
        for item_id in pending:
            self._input_item_turn_ids[item_id] = turn_id
        return pending

    async def _stream(self) -> AsyncIterator[RealtimeProviderTurnEvent]:
        self.turn_stream_started.set()
        if self._wait_for_canvas_interaction_response:
            await self._canvas_interaction_response_requested.wait()
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
        output: InteractiveCanvasToolOutput | InteractiveCanvasToolOutputV4,
    ) -> None:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        if call_id != self._pending_call_id:
            raise RuntimeError("fake canvas completion does not match pending call")
        self.completed_canvas_outputs.append(output)
        await asyncio.sleep(0)

    async def request_canvas_interaction_response(
        self,
        *,
        turn_id: str,
        interaction: RecordedSvgCanvasInteraction,
    ) -> bool:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        if (
            self._active_turn_id != turn_id
            or self._canvas_interaction_response_requested.is_set()
        ):
            return False
        self.canvas_interaction_response_turn_ids.append(turn_id)
        self.canvas_interaction_response_interactions.append(interaction)
        if not self.prepared_session_updates:
            raise AssertionError(
                "canvas interaction response requires a prepared session update"
            )
        self.canvas_interaction_response_session_updates.append(
            self.prepared_session_updates[-1]
        )
        self._canvas_interaction_response_requested.set()
        self._interaction_response_readiness_changed.set()
        return True

    async def wait_until_canvas_interaction_response_ready(
        self,
        *,
        turn_id: str,
    ) -> bool:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        while not self.closed and turn_id not in self.cancelled_turn_ids:
            if (
                self._active_turn_id == turn_id
                and not self._canvas_interaction_response_requested.is_set()
            ):
                return True
            self._interaction_response_readiness_changed.clear()
            if (
                self._active_turn_id == turn_id
                and not self._canvas_interaction_response_requested.is_set()
            ):
                return True
            await self._interaction_response_readiness_changed.wait()
        return False

    async def cancel(self, *, turn_id: str) -> None:
        if turn_id not in self.cancelled_turn_ids:
            self.cancelled_turn_ids.append(turn_id)
            self._interaction_response_readiness_changed.set()

    async def close(self) -> None:
        self.closed = True
        self._interaction_response_readiness_changed.set()


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
        segment: SpeechPhrase,
        *,
        turn_id: str,
    ) -> AsyncIterator[SynthesizedSpeechItem]:
        if not turn_id.strip():
            raise ValueError("turn_id cannot be empty")
        if self.closed:
            raise RuntimeError("fake streaming speech session is closed")
        self.synthesized_segment_ids.append(segment.id)
        spans = tuple(
            SpeechAlignment.model_validate(
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
