from __future__ import annotations

from ava_backend.prompts import (
    PERFORMANCE_PROMPT_VERSION,
    PROMPT_LAYERS,
    PROMPT_VERSION,
    build_instructions,
    build_performance_instructions,
)


def test_harness_is_conversation_first_and_keeps_capabilities_bounded() -> None:
    instructions = build_instructions()
    layer_names = {layer.name for layer in PROMPT_LAYERS}

    assert PROMPT_VERSION == "ava-conversation-v2"
    assert layer_names == {
        "identity",
        "conversation",
        "explanation",
        "safety",
        "performance",
        "response",
    }
    assert "Ordinary conversation is\nnot automatically a lesson" in instructions
    assert "Do not automatically end with\na quiz" in instructions
    assert "Ask a follow-up\nquestion only when it genuinely helps" in instructions
    assert "teacher" not in instructions.lower()
    assert "never\noutput bone names" in instructions
    assert "Never output SVG" in instructions
    assert "pointAtDashboard only when a diagram is present" in instructions
    assert "fractionBar" in instructions
    assert "numberLine" in instructions


def test_harness_has_explicit_child_relationship_and_privacy_boundaries() -> None:
    instructions = build_instructions()

    assert "Never claim to be human" in instructions
    assert "emotional dependence" in instructions
    assert "Never request contact information" in instructions
    assert "trusted adult or emergency" in instructions
    assert "replace real relationships" in instructions


def test_performance_harness_segments_meaning_not_manufactured_motion() -> None:
    instructions = build_performance_instructions()

    assert PERFORMANCE_PROMPT_VERSION == "ava-performance-v1"
    assert f'<ava_prompt version="{PERFORMANCE_PROMPT_VERSION}">' in instructions
    assert "AvaPerformancePlan" in instructions
    assert "Use one segment for a\nshort ordinary reply" in instructions
    assert "never create segments merely to manufacture\nmovement" in instructions
    assert "Prefer gesture=none for\nordinary conversation" in instructions
    assert "AvaResponse" not in instructions
