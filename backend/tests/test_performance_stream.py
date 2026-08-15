from __future__ import annotations

from collections.abc import AsyncIterator

from ava_backend.config import AvaEnvironment, Settings
from ava_backend.errors import SpeechProviderTimeoutError
from ava_backend.memory import (
    ConversationTurn,
    DevelopmentConversationMemoryStore,
)
from ava_backend.orchestration import AvaPerformanceOrchestrator
from ava_backend.performance_stream import AvaPerformanceStream
from ava_backend.protocol import (
    AvaPerformancePlan,
    AvaPerformanceSegment,
    DiagramCue,
    FractionCircleDiagram,
    LessonTurnRequest,
)
from ava_backend.providers.base import (
    PerformanceModelResult,
    SpeechCharacterTiming,
    TimedSpeechChunk,
    TimedSpeechMetadata,
)


class _Model:
    def __init__(self) -> None:
        self.arguments: dict[str, object] = {}

    async def generate_performance(
        self,
        **arguments: object,
    ) -> PerformanceModelResult:
        self.arguments = arguments
        return PerformanceModelResult(
            performance=AvaPerformancePlan(
                segments=[
                    AvaPerformanceSegment(
                        id="intro",
                        speech="A quarter is one of four equal parts.",
                        delivery="encouraging",
                        expression="encouraging",
                        gaze="child",
                        gesture="none",
                        diagram_cue=None,
                    ),
                    AvaPerformanceSegment(
                        id="diagram",
                        speech="This highlighted piece is one quarter.",
                        delivery="warm",
                        expression="warmSmile",
                        gaze="diagram",
                        gesture="pointAtDashboard",
                        diagram_cue=DiagramCue(
                            diagram=FractionCircleDiagram(
                                title="One quarter",
                                total_parts=4,
                                highlighted_parts=1,
                            )
                        ),
                    ),
                ]
            )
        )


class _Speech:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str | None, str | None]] = []

    @property
    def metadata(self) -> TimedSpeechMetadata:
        return TimedSpeechMetadata(
            audio_format="pcm_s16le",
            media_type="audio/x-raw",
            sample_rate_hz=24_000,
            channels=1,
            bits_per_sample=16,
            model="candidate-model",
            voice_id="candidate-voice",
            voice_label="Eve marry",
        )

    async def stream(
        self,
        *,
        text: str,
        previous_text: str | None = None,
        next_text: str | None = None,
    ) -> AsyncIterator[TimedSpeechChunk]:
        self.calls.append((text, previous_text, next_text))
        if self.fail:
            raise SpeechProviderTimeoutError()
        yield TimedSpeechChunk(
            audio=f"audio:{text}".encode(),
            alignment=(
                SpeechCharacterTiming(
                    character=text[0],
                    start_seconds=0,
                    end_seconds=0.08,
                ),
            ),
        )


def _stream(
    speech: _Speech,
    *,
    model: _Model | None = None,
    memory: DevelopmentConversationMemoryStore | None = None,
) -> AvaPerformanceStream:
    settings = Settings(environment=AvaEnvironment.TEST)
    return AvaPerformanceStream(
        AvaPerformanceOrchestrator(model or _Model(), settings),
        speech,
        memory=memory,
    )


async def test_stream_composes_plan_segment_audio_timing_and_diagram() -> None:
    speech = _Speech()
    events = [
        event
        async for event in _stream(speech).stream(
            request=LessonTurnRequest(
                child_name="Mia",
                message="What is a quarter?",
            ),
            turn_id="turn-1234",
            request_id="request-1234",
            subject="child:123",
        )
    ]

    assert [event.sequence for event in events] == list(range(len(events)))
    assert [event.event for event in events] == [
        "turnAccepted",
        "performancePlan",
        "audioMetadata",
        "segmentBoundary",
        "characterTimingBatch",
        "audioChunk",
        "segmentBoundary",
        "diagramCue",
        "characterTimingBatch",
        "audioChunk",
        "turnCompleted",
    ]
    assert speech.calls == [
        (
            "A quarter is one of four equal parts.",
            None,
            "This highlighted piece is one quarter.",
        ),
        (
            "This highlighted piece is one quarter.",
            "A quarter is one of four equal parts.",
            None,
        ),
    ]
    diagram_event = events[7].model_dump(mode="json", by_alias=True)
    assert diagram_event["segmentId"] == "diagram"
    assert diagram_event["cue"]["diagram"]["kind"] == "fractionCircle"
    assert events[-1].model_dump(mode="json", by_alias=True)["audioChunkCount"] == 2


async def test_stream_converts_provider_failure_to_terminal_event() -> None:
    events = [
        event
        async for event in _stream(_Speech(fail=True)).stream(
            request=LessonTurnRequest(
                child_name="Mia",
                message="Hello",
            ),
            turn_id="turn-1234",
            request_id="request-1234",
            subject="child:123",
        )
    ]

    assert events[-1].event == "turnFailed"
    failure = events[-1].model_dump(mode="json", by_alias=True)["failure"]
    assert failure == {
        "code": "speechTimeout",
        "message": "Ava's speech provider timed out.",
        "retryable": True,
    }
    assert all(event.event != "turnCompleted" for event in events)


async def test_stream_uses_backend_memory_and_appends_completed_turn() -> None:
    memory = DevelopmentConversationMemoryStore()
    await memory.append_turn(
        subject="child:123",
        turn=ConversationTurn(
            child_text="What did we discuss?",
            ava_text="We discussed shapes.",
        ),
    )
    model = _Model()
    events = [
        event
        async for event in _stream(
            _Speech(),
            model=model,
            memory=memory,
        ).stream(
            request=LessonTurnRequest(
                child_name="Mia",
                message="What is a quarter?",
                recent_messages=[
                    {
                        "role": "child",
                        "text": "This client memory must be ignored.",
                    }
                ],
            ),
            turn_id="turn-1234",
            request_id="request-1234",
            subject="child:123",
        )
    ]

    provider_input = str(model.arguments["model_input"])
    assert "What did we discuss?" in provider_input
    assert "We discussed shapes." in provider_input
    assert "client memory must be ignored" not in provider_input
    turns = await memory.load_recent_turns(subject="child:123", limit=8)
    assert len(turns) == 2
    assert turns[-1].child_text == "What is a quarter?"
    assert turns[-1].ava_text == (
        "A quarter is one of four equal parts. This highlighted piece is one quarter."
    )
    assert events[-1].event == "turnCompleted"
