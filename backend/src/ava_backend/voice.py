from __future__ import annotations

from .protocol import AvaDelivery

VOICE_STYLE_VERSION = "ava-voice-v1"

_BASE_VOICE_INSTRUCTIONS = """
Use Ava's stable voice identity: warm, bright, gentle and clearly articulated.
Sound like a friendly young adult speaking naturally with a child, never like
an infant or a cartoon caricature. Keep the pitch comfortable, the pace calm,
and the emotion sincere. Use small changes in emphasis and timing instead of
exaggerated sing-song delivery. Preserve the exact words and punctuation of the
input. Do not add laughs, sound effects, filler words or extra speech.
""".strip()

_DELIVERY_INSTRUCTIONS: dict[AvaDelivery, str] = {
    AvaDelivery.NEUTRAL: (
        "Deliver this conversationally with relaxed warmth and restrained motion."
    ),
    AvaDelivery.WARM: (
        "Let a soft smile be audible. Begin openly and settle without overacting."
    ),
    AvaDelivery.PLAYFUL: (
        "Use light, curious energy and one natural emphasis; avoid a singsong rhythm."
    ),
    AvaDelivery.THOUGHTFUL: (
        "Use a brief reflective cadence, then speak with calm confidence."
    ),
    AvaDelivery.ENCOURAGING: (
        "Sound genuinely supportive and confident in the child, without cheering."
    ),
    AvaDelivery.CONCERNED: (
        "Sound calm, attentive and reassuring. Avoid alarm, sadness or dramatization."
    ),
}


def build_voice_instructions(delivery: AvaDelivery) -> str:
    try:
        delivery_instruction = _DELIVERY_INSTRUCTIONS[delivery]
    except KeyError as exc:
        raise ValueError(f"unsupported Ava voice delivery: {delivery!r}") from exc
    return f"{_BASE_VOICE_INSTRUCTIONS}\n\n{delivery_instruction}"
