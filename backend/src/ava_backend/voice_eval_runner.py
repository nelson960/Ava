from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .config import BackendConfigurationError, Settings
from .errors import AvaBackendError
from .providers.openai_speech_provider import OpenAIAvaSpeech
from .voice_evaluation import (
    VoiceEvaluationError,
    generate_voice_evaluation_batch,
    record_voice_review,
)


def _default_suite_path() -> Path:
    return Path(__file__).resolve().parents[2] / "evals" / "voice_motion_cases.json"


def _parse_arguments(arguments: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and review paid Ava voice-motion evaluation audio.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser(
        "generate",
        help="Make explicit paid TTS calls for selected evaluation cases.",
    )
    selection = generate.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Generate one case. Repeat to select several.",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="Explicitly authorize generating every case.",
    )
    generate.add_argument("--suite", type=Path, default=_default_suite_path())
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly authorize another paid generation for existing cases.",
    )

    review = commands.add_parser(
        "review",
        help="Record a human voice review without making an API call.",
    )
    review.add_argument("--batch", type=Path, required=True)
    review.add_argument("--case", required=True, dest="case_id")
    review.add_argument(
        "--verdict",
        choices=("approved", "rejected"),
        required=True,
    )
    review.add_argument("--reviewer", required=True)
    review.add_argument("--notes", required=True)
    return parser.parse_args(arguments)


async def _generate(arguments: argparse.Namespace) -> dict[str, Any]:
    settings = Settings()
    speech = OpenAIAvaSpeech(settings)
    return await generate_voice_evaluation_batch(
        speech=speech,
        suite_path=arguments.suite,
        output_directory=arguments.output,
        model=settings.openai_tts_model,
        voice=settings.openai_tts_voice,
        requested_ids=arguments.case_ids,
        replace=arguments.replace,
    )


def main(arguments: list[str] | None = None) -> int:
    parsed = _parse_arguments(arguments)
    try:
        if parsed.command == "generate":
            result = asyncio.run(_generate(parsed))
        elif parsed.command == "review":
            result = record_voice_review(
                parsed.batch,
                parsed.case_id,
                verdict=parsed.verdict,
                reviewer=parsed.reviewer,
                notes=parsed.notes,
            )
        else:
            raise AssertionError(f"unhandled command {parsed.command}")
    except (BackendConfigurationError, VoiceEvaluationError, ValueError) as error:
        print(f"Ava voice evaluation configuration error: {error}", file=sys.stderr)
        return 2
    except AvaBackendError as error:
        print(
            "Ava voice evaluation provider error "
            f"({error.code}): {error.public_message}",
            file=sys.stderr,
        )
        return 3
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
