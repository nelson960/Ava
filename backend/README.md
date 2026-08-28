# Ava backend

The backend is Ava's production control plane. Provider keys, prompt harness,
child-safety policy, session state, strict schemas, cancellation, memory,
budgets, and observability belong here and never in the Flutter bundle.

The locked architecture is documented in
[`docs/production_architecture.md`](../docs/production_architecture.md). The
file-by-file delivery and migration sequence is in
[`docs/production_implementation_plan.md`](../docs/production_implementation_plan.md).

## Target runtime

The protocol-v3 backend will:

1. authenticate the Ava client and create a realtime session;
2. negotiate the client's WebRTC offer with OpenAI Realtime;
3. retain the provider call ID and attach a server sideband connection;
4. configure natural text-only Realtime output with one optional semantic
   `request_interactive_canvas` function;
5. incrementally parse bounded assistant-text phrases and independently
   validate any canvas function call;
6. stream valid speech to ElevenLabs over its input-streaming WebSocket;
7. send versioned control events and binary PCM to Flutter;
8. reconcile interruption from the final rendered sample position;
9. persist only delivered speech plus bounded typed memory;
10. invoke the strict Responses canvas specialist only when a visual is needed.

The ordinary path uses `gpt-realtime-2.1-mini` initially and the selected
ElevenLabs Ava voice. The on-demand `gpt-5.6-luna` canvas specialist uses the
same OpenAI API key, but no-canvas conversation never invokes it.

## Current code status

Protocol-v3 foundations now implemented and covered by deterministic tests:

- strict session/turn/event/state-machine schemas and shared fixtures;
- bounded dynamic vector-canvas schema and validation;
- a compact Realtime canvas decision/brief contract with no geometry;
- a strict Responses canvas draft that the backend compiles deterministically
  into a full validated canvas transaction;
- incremental assistant-text phrase parsing independent of tool arguments;
- a versioned, layered conversation-first prompt;
- one optional bounded `request_interactive_canvas` tool under
  `tool_choice=auto`;
- an escaped, count/character-bounded context containing only age band,
  delivered conversation, session summary, and allowlisted stable memory;
- a 30-case personality/safety/privacy/canvas/memory evaluation suite;
- a fingerprinted evaluation report/regression gate with mandatory human
  review before prompt promotion;
- a typed delivered-prefix memory boundary and strict proposal policy;
- a private, portable PostgreSQL/Supabase memory schema and async store with
  deterministic compaction, weekly recap, reset revisions, and readiness;
- authenticated protocol-v3 recap/reset endpoints and a strict Flutter client;
- deterministic fake Realtime and streaming-TTS providers;
- a live OpenAI Realtime SDP/sideband adapter with bounded WebSocket events,
  natural assistant text, complete optional function-call feedback,
  cancellation, cleanup, provider IDs, usage parsing, and an opt-in paid probe;
- a bounded SDP endpoint with separately scoped one-use WebRTC/event tokens,
  provider call-ID ownership, and deterministic negotiation fakes;
- an authenticated event WebSocket with ordered JSON descriptors and binary
  PCM;
- a transport-independent coordinator that incrementally validates speech and
  optional canvas calls, emits ordered control/binary PCM frames, resolves
  alignment and canvas cues against the sample clock, handles
  playback/cancellation, and commits only delivered text;
- production-shaped live composition for OpenAI Realtime, the strict canvas
  specialist, PostgreSQL memory, and an explicitly selected development-only
  silent caption-preview sample clock;
- Flutter's visible app path consuming protocol-v3 WebRTC/event streams with
  microphone gating, captions, visemes, canvas cues, and interruption;
- persistent live conversation ownership: one negotiated WebRTC/provider
  sideband/event socket carries consecutive isolated turns, while microphone
  mute preserves the attached RTP sender and only session close destroys it;
- correlated Flutter/backend lifecycle timings for bootstrap, microphone
  capture, SDP/ICE, sideband/prompt setup, listening, first segment, and first
  PCM without logging child content or credentials.

The older lesson-turn and NDJSON performance-turn HTTP routes, their
orchestrators, and their stream composer have been removed. OpenAI Responses
fixtures and related prompt/voice evaluation code remain available only as
offline regression tooling; they are not release architecture.

Useful seams to retain include:

- strict Pydantic validation;
- request IDs and typed public errors;
- provider interfaces and deterministic fakes;
- prompt-layer separation;
- bounded context construction;
- replaceable authentication;
- the server-only Supabase persistence adapter.

The active plan replaces the old orchestrator with realtime session, speech,
canvas, and memory subpackages. Legacy routes are removed after the complete v3
vertical slice passes.

The protocol-v3 in-memory store remains test-only. Production uses the private
`ava_private` PostgreSQL schema through a standard connection URL. The exact
same migration and store pass against local PostgreSQL 17 and are portable to
the Supabase direct/pooler PostgreSQL URL. Supabase deployment verification and
production authentication remain; the legacy public Supabase row adapter is
not used by protocol v3.

## Local protocol-v3 memory

Start the persistent local PostgreSQL database from the repository root:

```sh
/opt/homebrew/bin/docker compose \
  -f backend/docker-compose.memory.yml up -d --wait
```

Run both the store and complete fake Realtime → streaming TTS → delivered
prefix → PostgreSQL gates:

```sh
cd backend
AVA_TEST_POSTGRES_URL=postgresql://ava:ava-local-only@127.0.0.1:55432/ava \
  /opt/miniconda3/envs/ml/bin/python -m pytest tests/test_postgres_memory.py
```

This local password belongs only to the local compose service. Use
`AVA_DATABASE_URL` with the Supabase PostgreSQL credential in deployment;
never use a service-role key or database URL in Flutter.

The compose service stores data in the named `ava_postgres_data` volume. Use a
different local subject or explicitly reset conversations when a clean memory
history is required; restarting the container does not erase it.

## Development caption-preview vertical slice

With Docker PostgreSQL healthy and `AVA_OPENAI_API_KEY` present only in the
backend `.env`, start the live protocol-v3 backend:

```sh
cd backend
AVA_DATABASE_URL=postgresql://ava:ava-local-only@127.0.0.1:55432/ava \
AVA_REALTIME_DELIVERY_MODE=captionPreview \
AVA_REALTIME_ALLOW_INSECURE_LOOPBACK=true \
/opt/miniconda3/envs/ml/bin/python -m uvicorn ava_backend.asgi:app \
  --app-dir src \
  --host 127.0.0.1 \
  --port 8080
```

`captionPreview` is explicit, development-only, and emits silent PCM with a
24 kHz sample clock. It is not a TTS fallback. It lets the real OpenAI
Realtime, prompt, canvas specialist, captions, visemes, interruption, and
delivered-memory boundaries run before ElevenLabs is configured. The server
refuses this mode in production.

Starting the server does not spend provider credits. A turn begins only when
the app creates and negotiates a realtime session.

## Provider activation status

Do not add live keys merely to continue local development. Protocol, canvas,
fake streams, state machines, cancellation and transport integration are the
first gates.

Live integration later requires:

```text
AVA_OPENAI_API_KEY
AVA_ELEVENLABS_API_KEY
AVA_ELEVENLABS_VOICE_ID
AVA_ELEVENLABS_MODEL_ID
AVA_ELEVENLABS_COMMERCIAL_RIGHTS_CONFIRMED=true
```

The current voice record
`avatar/sounds/ava_voice_selection.v1.json` has a selected display label but no
provider voice/model IDs, and commercial use is unverified. The boolean must not
be enabled until the rights evidence and exact metadata are recorded.

Production also needs database/auth/secrets configuration. Those are
infrastructure credentials, not additional conversational model keys.

### Guarded OpenAI Realtime probe

The probe performs exactly one paid text turn through the production prompt and
tool boundary. It requires both an environment gate and a per-command spending
acknowledgement:

```sh
cd backend
PYTHONPATH=src AVA_OPENAI_LIVE_TESTS_ENABLED=true \
  /opt/miniconda3/envs/ml/bin/python -m ava_backend.realtime.live_probe \
  --allow-spend \
  --expect-canvas no \
  --max-output-tokens 800 \
  --input 'Hi Ava. Say hello in one short, warm sentence.'
```

The successful greeting gate must confirm natural assistant text, no canvas
call for ordinary conversation, and disabled OpenAI audio output. Do not
automate this command in ordinary tests or CI.

After the one-turn probe passes, the semantic matrix checks both correct canvas
selection and false-positive resistance across consecutive turns on one paid
Realtime connection:

```sh
cd backend
PYTHONPATH=src AVA_OPENAI_LIVE_TESTS_ENABLED=true \
  /opt/miniconda3/envs/ml/bin/python \
  -m ava_backend.realtime.live_semantic_matrix \
  --allow-spend \
  --suite evals/realtime_semantic_matrix_v4.json \
  --max-output-tokens 400
```

The matrix is bounded to eight cases and 800 output tokens per turn. It is not
an ordinary test and must never run without the explicit spend gate.

The Realtime live probe does not run from tests or start without both the
environment gate and explicit `--allow-spend` acknowledgement.

Rejected probes report the provider response ID, billed usage, total validation
issue count, and a bounded list of schema paths/error codes. They intentionally
omit generated values and child text. Use those diagnostics to fix the model
boundary before authorizing another paid probe; do not iterate blindly.

The original deep canvas transaction schema remained fail-closed after live
testing: Realtime function arguments did not reliably obey the nested geometry
schema, and no invalid scene reached Flutter. Realtime now either emits speech
alone or calls `request_interactive_canvas` with a compact semantic brief. A
separate strict Responses call authors the generic vector draft; the backend
supplies transaction identity, validates it without exposing partial state,
returns the matching function result, and commits the canonical revision before
sending it. The deterministic compiler/coordinator gate is green. A separately
authorized capped live specialist probe is still required. Never forward
unvalidated model JSON.

## Legacy regression server

The server can still start with protocol-v3 live sessions disabled to exercise
legacy regression routes:

```sh
cd backend
/opt/miniconda3/envs/ml/bin/python -m uvicorn ava_backend.asgi:app \
  --app-dir src \
  --host 127.0.0.1 \
  --port 8080
```

Its loopback authenticator is development-only. Legacy endpoints are not a
production fallback.

## Verify

```sh
cd backend
/opt/miniconda3/envs/ml/bin/python -m pytest
/opt/miniconda3/envs/ml/bin/python -m ruff check src tests
/opt/miniconda3/envs/ml/bin/python -m ruff format --check src tests
/opt/miniconda3/envs/ml/bin/python -m mypy src
```

Paid live tests must require an explicit spend flag and hard limits. Logs may
contain request/session/turn IDs and timing, but never provider keys, raw child
audio, exact birth dates, or unrestricted sensitive transcripts.
