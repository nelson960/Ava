from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from time import monotonic

from .config import BackendConfigurationError, Settings
from .context import ContextLimits, build_model_context
from .evaluation import (
    ConversationEvalCase,
    evaluate_deterministic_constraints,
    load_conversation_eval_suite,
    render_eval_record,
)
from .errors import AvaBackendError
from .prompts import PROMPT_VERSION, build_instructions
from .protocol import LessonTurnRequest
from .providers.openai_provider import OpenAIAvaModel


def _default_suite_path() -> Path:
    return Path(__file__).resolve().parents[2] / "evals" / "conversation_cases.json"


def _parse_arguments(arguments: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run explicit, paid Ava conversation evaluations.",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Run one case ID. Repeat to run several cases.",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="Explicitly authorize running every case in the suite.",
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=_default_suite_path(),
        help="Path to a typed conversation evaluation suite.",
    )
    return parser.parse_args(arguments)


def _select_cases(
    cases: list[ConversationEvalCase],
    *,
    requested_ids: list[str] | None,
) -> list[ConversationEvalCase]:
    if requested_ids is None:
        return cases
    if len(requested_ids) != len(set(requested_ids)):
        raise ValueError("duplicate evaluation case IDs would spend credits twice")

    by_id = {case.id: case for case in cases}
    missing = sorted(set(requested_ids) - by_id.keys())
    if missing:
        raise ValueError(f"unknown evaluation case IDs: {', '.join(missing)}")
    return [by_id[case_id] for case_id in requested_ids]


async def _run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    suite = load_conversation_eval_suite(
        arguments.suite,
        expected_prompt_version=PROMPT_VERSION,
    )
    cases = _select_cases(suite.cases, requested_ids=arguments.case_ids)
    model = OpenAIAvaModel(settings)
    instructions = build_instructions()
    limits = ContextLimits(
        max_history_messages=settings.context_history_max_messages,
        max_history_characters=settings.context_history_max_characters,
    )
    failed = False

    for case in cases:
        request = LessonTurnRequest(
            child_name="EvalChild",
            age_band=case.age_band,
            message=case.message,
            recent_messages=case.recent_messages,
        )
        context = build_model_context(request, limits)
        started_at = monotonic()
        result = await model.generate_response(
            instructions=instructions,
            model_input=context.to_provider_input(),
            safety_id="ava-prompt-evaluation",
        )
        latency_milliseconds = round((monotonic() - started_at) * 1000)
        record = render_eval_record(
            case=case,
            response=result.response,
            provider_request_id=result.provider_request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_milliseconds=latency_milliseconds,
        )
        print(record, flush=True)
        deterministic = evaluate_deterministic_constraints(case, result.response)
        failed = failed or not deterministic.passed

    return 1 if failed else 0


def main(arguments: list[str] | None = None) -> int:
    parsed = _parse_arguments(arguments)
    try:
        return asyncio.run(_run(parsed))
    except (BackendConfigurationError, OSError, ValueError) as error:
        print(f"Ava evaluation configuration error: {error}", file=sys.stderr)
        return 2
    except AvaBackendError as error:
        print(
            f"Ava evaluation provider error ({error.code}): {error.public_message}",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
