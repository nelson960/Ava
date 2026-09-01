from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from ..config import BackendConfigurationError, Settings
from ..protocol import AgeBand
from ..providers.openai_realtime_provider import (
    OpenAIRealtimeLiveProbe,
    OpenAIRealtimeOutputValidationError,
)
from .harness import build_realtime_harness
from .context_assembler import DynamicContextAssembler, DynamicContextBuildReason
from .memory import AuthoritativeMemorySnapshot


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run exactly one explicitly authorized OpenAI Realtime text probe "
            "through Ava's production prompt and optional canvas tool."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--expect-canvas",
        required=True,
        choices=("yes", "no"),
    )
    parser.add_argument(
        "--age-band",
        default=AgeBand.MIDDLE_CHILDHOOD.value,
        choices=tuple(band.value for band in AgeBand),
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--allow-spend",
        action="store_true",
        help="Required acknowledgement that this command makes one paid request.",
    )
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(_run(arguments)))


async def _run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    if not arguments.allow_spend:
        raise BackendConfigurationError(
            "Live provider probe refused: pass --allow-spend explicitly"
        )
    if not settings.openai_live_tests_enabled:
        raise BackendConfigurationError(
            "Live provider probe refused: set "
            "AVA_OPENAI_LIVE_TESTS_ENABLED=true for this command"
        )
    if (
        not 256
        <= arguments.max_output_tokens
        <= (settings.openai_realtime_max_output_tokens)
    ):
        raise BackendConfigurationError(
            "--max-output-tokens must be between 256 and "
            "AVA_OPENAI_REALTIME_MAX_OUTPUT_TOKENS"
        )

    age_band = AgeBand(arguments.age_band)
    snapshot = AuthoritativeMemorySnapshot.model_validate(
        {
            "ageBand": age_band,
            "resetRevision": 0,
            "currentSessionSummary": None,
            "deliveredTurns": [],
            "stableMemories": [],
        }
    )
    harness = build_realtime_harness(
        dynamic_context=DynamicContextAssembler().assemble(
            build_id="ctx-1",
            reason=DynamicContextBuildReason.SESSION_BOOTSTRAP,
            age_band=snapshot.age_band,
            timezone_id="UTC",
            current_time_utc=datetime.now(UTC),
            bootstrap_turns=tuple(snapshot.delivered_turns),
        )
    )
    probe = OpenAIRealtimeLiveProbe(
        api_key=settings.require_openai_api_key(),
        model=settings.openai_realtime_model,
        safety_identifier=hashlib.sha256(b"ava-live-realtime-probe-v1").hexdigest(),
        connect_timeout_seconds=(settings.openai_realtime_connect_timeout_seconds),
        turn_timeout_seconds=settings.realtime_max_turn_seconds,
        max_output_tokens=settings.openai_realtime_max_output_tokens,
        truncation_post_instructions_tokens=(
            settings.openai_realtime_truncation_post_instructions_tokens
        ),
        truncation_retention_ratio=(
            settings.openai_realtime_truncation_retention_ratio
        ),
    )
    try:
        result = await probe.run(
            harness=harness,
            turn_id=f"turn-live-probe-{uuid4()}",
            text_input=arguments.input,
            response_max_output_tokens=arguments.max_output_tokens,
        )
    except OpenAIRealtimeOutputValidationError as error:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "reason": "realtimeOutputInvalid",
                    "providerResponseId": error.response_id,
                    "validationIssueCount": error.validation_issue_count,
                    "validationIssues": [
                        {
                            "path": list(issue.path),
                            "code": issue.code,
                        }
                        for issue in error.validation_issues
                    ],
                    "usage": asdict(error.usage),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    has_canvas = result.canvas_selected
    expected_canvas = arguments.expect_canvas == "yes"
    if has_canvas != expected_canvas:
        expectation = "with" if expected_canvas else "without"
        raise RuntimeError(f"Live probe expected a turn {expectation} a canvas brief")

    print(
        json.dumps(
            {
                "model": result.model,
                "promptVersion": result.prompt_version,
                "providerResponseId": result.response_id,
                "firstDeltaMs": round(result.first_delta_ms, 2),
                "completedMs": round(result.completed_ms, 2),
                "usage": asdict(result.usage),
                "speech": result.speech,
                "canvasRequest": (
                    result.canvas_request.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                    )
                    if result.canvas_request is not None
                    else None
                ),
                "canvasRejection": result.canvas_rejection,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    main()
