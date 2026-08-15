from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import pytest

from ava_backend.providers.base import SpeechAudioResult
from ava_backend.voice_evaluation import (
    VoiceEvaluationError,
    generate_voice_evaluation_batch,
    load_voice_motion_eval_suite,
    record_voice_review,
    select_voice_eval_cases,
)


SUITE_PATH = Path(__file__).resolve().parents[1] / "evals" / "voice_motion_cases.json"


def _wav_bytes(*, duration_seconds: float = 2.0) -> bytes:
    sample_rate = 24000
    frames = round(sample_rate * duration_seconds)
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


class _Speech:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def synthesize(
        self,
        *,
        text: str,
        instructions: str,
    ) -> SpeechAudioResult:
        self.calls.append((text, instructions))
        return SpeechAudioResult(
            audio=_wav_bytes(),
            audio_format="wav",
            media_type="audio/wav",
            model="gpt-4o-mini-tts-2025-12-15",
            voice="marin",
            provider_request_id="request-123",
        )


def test_voice_motion_suite_is_bounded_and_uses_semantic_intents() -> None:
    suite = load_voice_motion_eval_suite(SUITE_PATH)

    assert suite.suite_id == "ava_voice_motion_v1"
    assert len(suite.cases) == 10
    assert {case.delivery for case in suite.cases} >= {
        "neutral",
        "warm",
        "playful",
        "thoughtful",
        "encouraging",
        "concerned",
    }
    assert all(case.motion_review_focus for case in suite.cases)


def test_case_selection_rejects_duplicates_before_a_paid_call() -> None:
    suite = load_voice_motion_eval_suite(SUITE_PATH)

    with pytest.raises(VoiceEvaluationError, match="spend credits twice"):
        select_voice_eval_cases(suite, ["warm_greeting", "warm_greeting"])
    with pytest.raises(VoiceEvaluationError, match="unknown"):
        select_voice_eval_cases(suite, ["missing"])


async def test_generate_and_review_one_auditable_voice_case(
    tmp_path: Path,
) -> None:
    speech = _Speech()
    output = tmp_path / "batch"
    manifest = await generate_voice_evaluation_batch(
        speech=speech,
        suite_path=SUITE_PATH,
        output_directory=output,
        model="gpt-4o-mini-tts-2025-12-15",
        voice="marin",
        requested_ids=["warm_greeting"],
        replace=False,
    )

    assert len(speech.calls) == 1
    assert "stable voice identity" in speech.calls[0][1]
    assert manifest["timingAvailability"] == "audio_sample_clock_only"
    record = manifest["items"][0]
    assert record["id"] == "warm_greeting"
    assert record["durationSeconds"] == 2.0
    assert record["durationCheck"]["status"] == "passed"
    assert record["motionStatus"] == "awaiting_emage"
    assert (output / record["audioPath"]).is_file()
    assert "text" not in record

    with pytest.raises(VoiceEvaluationError, match="already exists"):
        await generate_voice_evaluation_batch(
            speech=speech,
            suite_path=SUITE_PATH,
            output_directory=output,
            model="gpt-4o-mini-tts-2025-12-15",
            voice="marin",
            requested_ids=["warm_greeting"],
            replace=False,
        )
    assert len(speech.calls) == 1

    review = record_voice_review(
        output,
        "warm_greeting",
        verdict="approved",
        reviewer="voice-reviewer",
        notes="Warm and consistent without exaggerated delivery.",
    )
    assert review["status"] == "approved"
    saved = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert saved["items"][0]["voiceReview"] == review
