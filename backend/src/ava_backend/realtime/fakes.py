from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
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
from .memory import (
    SemanticCaptureDisposition,
    SemanticCaptureFunctionArguments,
)
from .provider_session import (
    NegotiatedRealtimeCall,
    RealtimeContextInstallationStatus,
    RealtimeProviderInputEvent,
    RealtimeProviderTurnEvent,
    RealtimeProviderUnavailableError,
    SemanticCaptureEvidence,
    validate_sdp,
)
from .tool_contract import (
    InteractiveCanvasCancellationRequest,
    InteractiveCanvasCancellationToolOutputV4,
    InteractiveCanvasRequest,
    InteractiveCanvasToolOutputV4,
    RealtimeCancelCanvasToolCall,
    RealtimeCanvasToolCall,
    RealtimeRejectedCanvasToolCall,
    RealtimeUtilityToolOutputV4,
    RealtimeSpeechTextDelta,
    ManageCanvasPresentationToolOutputV4,
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
        plan: AvaTurnPlan | None = None,
        *,
        child_text: str | None = "Deterministic child turn.",
        canvas_brief: str | None = None,
        cancel_pending_canvas: bool = False,
        rejected_canvas_reason: str | None = None,
        wait_for_canvas_interaction_response: bool = False,
        chunk_sizes: Iterable[int] = (17, 5, 43, 2),
    ) -> None:
        if plan is None:
            plan = AvaTurnPlan.model_validate(
                {
                    "segments": [
                        {
                            "id": "seg_1",
                            "speech": "Default deterministic response.",
                            "delivery": "warm",
                            "expression": "neutral",
                            "gaze": "child",
                            "motionIntent": "none",
                            "canvasCues": [],
                        }
                    ],
                    "memoryProposal": None,
                    "childTurnSummary": "Default turn summary.",
                    "canvasDecision": {"complexity": "none", "specialistBrief": None},
                }
            )
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
        if cancel_pending_canvas and canvas_brief is not None:
            raise ValueError("fake canvas cancellation cannot request a canvas")
        self._speech_segments = tuple(segment.speech for segment in plan.segments)
        self._canvas_request = (
            InteractiveCanvasRequest.model_validate(
                {
                    "operation": "new",
                    "brief": canvas_brief,
                    "interactionMode": "explore",
                    "continuousControl": "none",
                    "completion": "needsResultReasoning",
                }
            )
            if canvas_brief is not None
            else None
        )
        self._child_text = normalized_child_text
        self._rejected_canvas_reason = rejected_canvas_reason
        self._cancel_pending_canvas = cancel_pending_canvas
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
            (InteractiveCanvasToolOutputV4 | InteractiveCanvasCancellationToolOutputV4)
        ] = []
        self.completed_utility_outputs: list[RealtimeUtilityToolOutputV4] = []
        self.completed_manage_canvas_presentation_outputs: list[
            ManageCanvasPresentationToolOutputV4
        ] = []
        self.canvas_interaction_response_turn_ids: list[str] = []
        self.canvas_interaction_response_interactions: list[
            RecordedSvgCanvasInteraction
        ] = []
        self.canvas_interaction_response_session_updates: list[dict[str, object]] = []
        self.turn_stream_started = asyncio.Event()
        self._canvas_interaction_response_requested = asyncio.Event()
        self._interaction_response_readiness_changed = asyncio.Event()
        self._readiness_transition_lock = asyncio.Lock()
        self._active_turn_id: str | None = None
        self._active_response_turn_id: str | None = None
        self._settled_turn_ids: set[str] = set()
        self._installed_context_build_id: str | None = None
        self._installed_context_build_ids: set[str] = set()
        self._pending_call_id: str | None = None
        self._input_events: asyncio.Queue[RealtimeProviderInputEvent] = asyncio.Queue()
        self._input_item_turn_ids: dict[str, str | None] = {}
        self._desired_context_build_id: str | None = None
        self.defer_acknowledgement = False
        self.context_changed = asyncio.Event()
        self.requested_child_responses: list[tuple[str, str]] = []
        self.captured_evidences: list[SemanticCaptureEvidence] = []
        self.canned_capture_results: list[
            SemanticCaptureFunctionArguments | BaseException
        ] = []
        self.capture_gate: asyncio.Event | None = None
        self.closed = False

    @property
    def desired_context_build_id(self) -> str | None:
        return self._desired_context_build_id

    def is_context_acknowledged(self, build_id: str) -> bool:
        return (
            self._installed_context_build_id == build_id
            or build_id in self._installed_context_build_ids
        )

    def acknowledge_context(self, build_id: str) -> None:
        self._installed_context_build_id = build_id
        self._installed_context_build_ids.add(build_id)
        self.context_changed.set()

    async def wait_for_context(self, *, build_id: str) -> None:
        while not self.is_context_acknowledged(build_id):
            await self.context_changed.wait()
            self.context_changed.clear()

    async def request_child_response(
        self,
        *,
        turn_id: str,
        item_id: str,
        permit_degraded_focus_grounding: bool = False,
    ) -> bool:
        if self._input_item_turn_ids.get(item_id) != turn_id:
            raise RuntimeError("fake child response input owner mismatch")
        desired = self._desired_context_build_id
        if desired is not None and not self.is_context_acknowledged(desired):
            if not permit_degraded_focus_grounding:
                return False
        self.requested_child_responses.append((turn_id, item_id))
        return True

    async def wait_until_response_admissible(self, *, turn_id: str) -> bool:
        return not self.closed and turn_id not in self._settled_turn_ids

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
        self._settled_turn_ids.discard(turn_id)
        self._active_turn_id = turn_id
        self._active_response_turn_id = None
        self._interaction_response_readiness_changed.set()

        async def _owned_stream() -> AsyncIterator[RealtimeProviderTurnEvent]:
            response_completed = False
            try:
                async for event in self._stream():
                    if self._active_response_turn_id is None:
                        # Expose the same idle turn boundary production uses for
                        # a pending session.update before response.created.
                        idle_boundary: asyncio.Future[None] = (
                            asyncio.get_running_loop().create_future()
                        )
                        asyncio.get_running_loop().call_soon(
                            idle_boundary.set_result,
                            None,
                        )
                        await idle_boundary
                        async with self._readiness_transition_lock:
                            self._active_response_turn_id = turn_id
                            self._interaction_response_readiness_changed.set()
                    yield event
                response_completed = True
            finally:
                if response_completed and self._active_turn_id == turn_id:
                    self._settled_turn_ids.add(turn_id)
                    self._active_turn_id = None
                    self._active_response_turn_id = None
                    self._interaction_response_readiness_changed.set()

        return _owned_stream()

    @property
    def active_context_build_id(self) -> str | None:
        return self._installed_context_build_id

    async def prepare(
        self,
        *,
        harness: RealtimeHarness,
    ) -> RealtimeContextInstallationStatus:
        if self.closed:
            raise RuntimeError("fake Realtime provider session is closed")
        update = harness.session_update()
        deduplicated = (
            bool(self.prepared_session_updates)
            and self.prepared_session_updates[-1] == update
        )
        if (
            not self.prepared_session_updates
            or self.prepared_session_updates[-1] != update
        ):
            self.prepared_session_updates.append(update)
        await asyncio.sleep(0)
        self._desired_context_build_id = harness.context.build_id
        if deduplicated:
            self._installed_context_build_id = harness.context.build_id
            self._installed_context_build_ids.add(harness.context.build_id)
            self.context_changed.set()
            return RealtimeContextInstallationStatus.DEDUPLICATED
        if not self.defer_acknowledgement:
            self._installed_context_build_id = harness.context.build_id
            self._installed_context_build_ids.add(harness.context.build_id)
            self.context_changed.set()
            return RealtimeContextInstallationStatus.INSTALLED
        return RealtimeContextInstallationStatus.SUBMITTED

    async def prepare_if_idle(
        self,
        *,
        harness: RealtimeHarness,
        turn_id: str,
    ) -> RealtimeContextInstallationStatus | None:
        async with self._readiness_transition_lock:
            if self._context_installation_readiness(turn_id) is not None:
                return None
            return await self.prepare(harness=harness)

    async def child_transcript(self, *, turn_id: str) -> str | None:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        await asyncio.sleep(0)
        return self._child_text

    def available_child_transcript(self, *, turn_id: str) -> str | None:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        return self._child_text

    def wait_for_child_transcript(
        self,
        *,
        turn_id: str,
    ) -> Awaitable[str | None]:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")

        async def resolved() -> str | None:
            await asyncio.sleep(0)
            return self._child_text

        return resolved()

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
        self._interaction_response_readiness_changed.set()

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
            if index == 0 and (
                self._canvas_request is not None or self._cancel_pending_canvas
            ):
                self._pending_call_id = "call_canvas_fake_0001"
                if self._cancel_pending_canvas:
                    yield RealtimeCancelCanvasToolCall(
                        call_id=self._pending_call_id,
                        request=InteractiveCanvasCancellationRequest.model_validate(
                            {"completion": "needsResultReasoning"}
                        ),
                    )
                elif self._rejected_canvas_reason is None:
                    request = self._canvas_request
                    if request is None:  # guarded by the enclosing condition
                        raise RuntimeError("fake canvas request was not configured")
                    yield RealtimeCanvasToolCall(
                        call_id=self._pending_call_id,
                        request=request,
                    )
                else:
                    yield RealtimeRejectedCanvasToolCall(
                        call_id=self._pending_call_id,
                        reason=self._rejected_canvas_reason,
                    )
                if not self.completed_canvas_outputs:
                    raise RuntimeError("fake canvas tool call was not completed")
                self._pending_call_id = None
                self._interaction_response_readiness_changed.set()

    async def complete_canvas_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: (
            InteractiveCanvasToolOutputV4 | InteractiveCanvasCancellationToolOutputV4
        ),
    ) -> None:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        if call_id != self._pending_call_id:
            raise RuntimeError("fake canvas completion does not match pending call")
        self.completed_canvas_outputs.append(output)
        await asyncio.sleep(0)

    async def complete_manage_canvas_presentation_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: ManageCanvasPresentationToolOutputV4,
    ) -> None:
        if not turn_id or not call_id:
            raise ValueError("presentation completion requires turn and call identity")
        self.completed_manage_canvas_presentation_outputs.append(output)
        await asyncio.sleep(0)

    async def complete_utility_tool(
        self,
        *,
        turn_id: str,
        call_id: str,
        output: RealtimeUtilityToolOutputV4,
        harness: RealtimeHarness,
        on_result_created: Callable[[], None] | None = None,
    ) -> None:
        del harness
        if not turn_id or not call_id:
            raise ValueError("utility completion requires turn and call identity")
        self.completed_utility_outputs.append(output)
        if on_result_created is not None:
            on_result_created()
        await asyncio.sleep(0)

    async def request_canvas_interaction_response(
        self,
        *,
        turn_id: str,
        interaction: RecordedSvgCanvasInteraction,
    ) -> bool:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        if self._canvas_interaction_response_readiness(turn_id) is not None:
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

    def _context_installation_readiness(self, turn_id: str) -> str | None:
        if self.closed:
            return "providerClosed"
        if turn_id in self.cancelled_turn_ids or turn_id in self._settled_turn_ids:
            return "turnSettledOrCancelled"
        if self._active_turn_id != turn_id:
            return "turnMismatch"
        if self._active_response_turn_id == turn_id:
            return "activeResponse"
        return None

    def _canvas_interaction_response_readiness(self, turn_id: str) -> str | None:
        if self.closed:
            return "providerClosed"
        if turn_id in self.cancelled_turn_ids or turn_id in self._settled_turn_ids:
            return "turnSettledOrCancelled"
        if self._active_turn_id != turn_id:
            return "turnMismatch"
        if any(owner is None for owner in self._input_item_turn_ids.values()):
            return "unresolvedInputOwner"
        if any(owner == turn_id for owner in self._input_item_turn_ids.values()):
            return "turnAlreadyOwnsInput"
        if self._active_response_turn_id is not None:
            return "activeResponse"
        if self._pending_call_id is not None:
            return "pendingCanvasCall"
        if self._canvas_interaction_response_requested.is_set():
            return "responseAlreadyRequested"
        return None

    async def wait_until_canvas_interaction_response_ready(
        self,
        *,
        turn_id: str,
    ) -> bool:
        if not turn_id:
            raise ValueError("turn_id cannot be empty")
        while True:
            readiness = self._canvas_interaction_response_readiness(turn_id)
            if readiness is None:
                return True
            if readiness in {"providerClosed", "turnSettledOrCancelled"}:
                return False
            self._interaction_response_readiness_changed.clear()
            readiness = self._canvas_interaction_response_readiness(turn_id)
            if readiness is None:
                return True
            if readiness in {"providerClosed", "turnSettledOrCancelled"}:
                return False
            await self._interaction_response_readiness_changed.wait()

    async def cancel(self, *, turn_id: str) -> None:
        if turn_id not in self.cancelled_turn_ids:
            self.cancelled_turn_ids.append(turn_id)
            self._interaction_response_readiness_changed.set()

    async def capture_semantic_memory(
        self,
        *,
        evidence: SemanticCaptureEvidence,
    ) -> SemanticCaptureFunctionArguments:
        if self.closed:
            raise RealtimeProviderUnavailableError("provider session is closed")
        self.captured_evidences.append(evidence)
        if self.capture_gate is not None:
            await self.capture_gate.wait()
        if self.canned_capture_results:
            result = self.canned_capture_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return SemanticCaptureFunctionArguments(
            disposition=SemanticCaptureDisposition.NO_CHANGE
        )

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
