from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from ava_backend.config import AvaEnvironment, BackendConfigurationError, Settings
from ava_backend.memory import (
    ConversationTurn,
    DevelopmentConversationMemoryStore,
    MemoryStoreError,
    SupabaseConversationMemoryStore,
)


async def test_development_memory_preserves_complete_recent_turns() -> None:
    store = DevelopmentConversationMemoryStore(max_turns_per_subject=2)
    for index in range(3):
        await store.append_turn(
            subject="child:123",
            turn=ConversationTurn(
                child_text=f"child-{index}",
                ava_text=f"ava-{index}",
            ),
        )

    turns = await store.load_recent_turns(subject="child:123", limit=2)

    assert [(turn.child_text, turn.ava_text) for turn in turns] == [
        ("child-1", "ava-1"),
        ("child-2", "ava-2"),
    ]


async def test_reset_is_scoped_to_conversation_subject() -> None:
    store = DevelopmentConversationMemoryStore()
    turn = ConversationTurn(child_text="Hello", ava_text="Hi!")
    await store.append_turn(subject="child:one", turn=turn)
    await store.append_turn(subject="child:two", turn=turn)

    await store.reset_conversation(subject="child:one")

    assert await store.load_recent_turns(subject="child:one", limit=8) == ()
    assert len(await store.load_recent_turns(subject="child:two", limit=8)) == 1


async def test_memory_contract_rejects_invalid_data_and_bounds() -> None:
    with pytest.raises(ValidationError):
        ConversationTurn(child_text="", ava_text="Hi")

    with pytest.raises(ValueError, match="between 1 and 8"):
        DevelopmentConversationMemoryStore(max_turns_per_subject=9)

    store = DevelopmentConversationMemoryStore(max_turns_per_subject=2)
    with pytest.raises(ValueError, match="non-empty"):
        await store.load_recent_turns(subject=" ", limit=1)
    with pytest.raises(ValueError, match="between 0"):
        await store.load_recent_turns(subject="child:123", limit=3)


def _supabase_settings() -> Settings:
    return Settings(
        environment=AvaEnvironment.TEST,
        supabase_url="https://ava-project.supabase.co",
        supabase_service_role_key=SecretStr("service-role-test-key"),
    )


async def test_supabase_memory_loads_recent_turns_in_conversation_order() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json=[
                {"child_text": "new child", "ava_text": "new ava"},
                {"child_text": "old child", "ava_text": "old ava"},
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = SupabaseConversationMemoryStore(
        _supabase_settings(),
        client=client,
    )

    turns = await store.load_recent_turns(subject="child:123", limit=2)
    await client.aclose()

    assert [(turn.child_text, turn.ava_text) for turn in turns] == [
        ("old child", "old ava"),
        ("new child", "new ava"),
    ]
    assert captured["url"] == (
        "https://ava-project.supabase.co/rest/v1/ava_conversation_turns"
        "?select=child_text%2Cava_text&subject=eq.child%3A123"
        "&order=created_at.desc%2Cid.desc&limit=2"
    )
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["apikey"] == "service-role-test-key"
    assert headers["authorization"] == "Bearer service-role-test-key"


async def test_supabase_memory_appends_and_resets_only_conversation_rows() -> None:
    requests: list[tuple[str, dict[str, str], object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            (
                request.method,
                dict(request.headers),
                json.loads(request.content) if request.content else None,
            )
        )
        return httpx.Response(201 if request.method == "POST" else 204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = SupabaseConversationMemoryStore(
        _supabase_settings(),
        client=client,
    )
    await store.append_turn(
        subject="child:123",
        turn=ConversationTurn(child_text="Hello", ava_text="Hi!"),
    )
    await store.reset_conversation(subject="child:123")
    await client.aclose()

    assert requests[0][0] == "POST"
    assert requests[0][1]["prefer"] == "return=minimal"
    assert requests[0][2] == {
        "subject": "child:123",
        "child_text": "Hello",
        "ava_text": "Hi!",
    }
    assert requests[1][0] == "DELETE"
    assert requests[1][2] is None


async def test_supabase_memory_rejects_invalid_responses() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=[
                    {
                        "child_text": "Hello",
                        "ava_text": "Hi",
                        "raw_transcript": "not allowed",
                    }
                ],
            )
        )
    )
    store = SupabaseConversationMemoryStore(
        _supabase_settings(),
        client=client,
    )

    with pytest.raises(MemoryStoreError, match="invalid turn data"):
        await store.load_recent_turns(subject="child:123", limit=1)
    await client.aclose()


def test_supabase_memory_requires_server_only_credentials() -> None:
    with pytest.raises(BackendConfigurationError, match="AVA_SUPABASE_URL"):
        SupabaseConversationMemoryStore(Settings(environment=AvaEnvironment.TEST))
    with pytest.raises(
        BackendConfigurationError,
        match="AVA_SUPABASE_SERVICE_ROLE_KEY",
    ):
        SupabaseConversationMemoryStore(
            Settings(
                environment=AvaEnvironment.TEST,
                supabase_url="https://ava-project.supabase.co",
            )
        )
