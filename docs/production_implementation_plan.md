# Ava production implementation plan

Status: active plan  
Architecture: `docs/production_architecture.md`  
Last reviewed: 2026-08-03

This plan is based on the code currently in the repository. It separates work
that can be completed without provider credentials from live integration that
requires the OpenAI and ElevenLabs keys.

## 1. Current repository audit

### 1.1 Keep and adapt

| Existing area | Why it stays | Required adaptation |
| --- | --- | --- |
| `lib/ui/avatar/` | Production avatar, real picking, touch, camera return, idle and lip-sync boundaries already exist | Drive speaking state from protocol v3 sample-clock events; remove obsolete response-gesture audition paths only after regression coverage |
| `avatar/ava_vroid_source.glb` and rig maps | Existing validated Ava asset | No new avatar copies; verify viseme mapping against normalized phoneme fixtures |
| `PcmSpeechOutputService` | Native sample-counted PCM playback is the correct master clock | Add stream IDs, buffered-sample reporting, underrun/route events, fade-stop and exact final rendered sample |
| `LivingWorld` | Approved local day/night atmosphere and bounded touches | Preserve; do not put weather work on the voice critical path |
| `SettingsDrawer` | Already close to the minimal product | Change reset wording/behavior, remove progress references, add the weekly recap entry after recap storage exists |
| Drift profile storage | Useful local profile/caption cache | Remove score/progress fields through a migration; never send name/DOB to providers |
| Backend `auth.py`, `security.py`, error/request-ID patterns | Correct separation of concerns | Implement production auth/session tokens and WebSocket authentication |
| Backend prompt layers | Personality is already separated from transport | Keep natural assistant text separate from the optional semantic canvas function and remove fixed lesson/diagram vocabulary |
| Backend provider interfaces | Useful test seams | Split into Realtime sideband, ElevenLabs streaming TTS, and strict on-demand canvas-specialist interfaces |
| Supabase memory adapter | A usable first durable-store adapter | Evolve from raw recent turns to delivered ranges, summaries, proposals and reset revisions |

### 1.2 Missing production components

1. Physical-device and app-lifecycle verification of the implemented Flutter
   WebRTC microphone boundary.
2. Production wiring of the implemented live OpenAI adapter behind the backend
   SDP negotiation and provider call-ID interface.
3. Production wiring of the implemented server-side OpenAI Realtime sideband
   session.
4. Production-authenticated bidirectional Ava event WebSocket deployment. The
   protocol-v3 bootstrap, short-lived separately scoped one-use token registry,
   strict
   WebSocket authentication, JSON/binary bridge, and keyless integration tests
   are implemented.
5. Completion of protocol-v3 cancellation and binary-audio framing around the
   implemented session, turn, event, canvas, and state-machine schemas.
6. Live-validate the implemented strict Responses canvas specialist and its
   deterministic compiler/validator. Realtime emits natural assistant text and
   may select one optional semantic `request_interactive_canvas` function.
7. ElevenLabs input-streaming WebSocket TTS.
8. Physical-device verification of the implemented Flutter rendered-sample
   playback progress and delivered-sample-to-delivered-text ledger.
9. Deterministic text/audio alignment to VRM visemes.
10. Physical-device and performance verification of the implemented dynamic
    scene-graph model, validator, renderer and hit-test engine.
11. Barge-in, provider cancellation, stale-event suppression and reconnect.
12. Production authentication, rate limits, App Attest/abuse controls.
13. Supabase deployment verification of the implemented portable PostgreSQL
   delivered-turn persistence, summary compaction, cloud reset and weekly
   recap.
14. Per-turn latency, queue, provider-usage and cost telemetry.
15. Physical-device audio route, interruption, 60/120 Hz and long-session
    testing.

### 1.2.1 Implemented protocol-v3 brain foundation

Completed on 2026-07-30 without provider credentials:

- versioned layered prompt `ava-realtime-v4.1`;
- natural bounded assistant text under `tool_choice=auto`, plus one optional
  semantic canvas call with a complete `function_call_output` lifecycle;
- escaped, count/character-bounded age-band, delivered-turn, summary, and
  stable-memory context;
- a 30-case typed personality, safety, privacy, canvas, and memory evaluation
  suite with deterministic gates and explicit human-review criteria;
- a delivered-prefix memory model, strict allowlisted proposal policy,
  duplicate protection, bounded development store, and reset behavior.

The fake coordinator consumes this harness and commits only the playback
ledger's alignment-derived delivered prefix. The portable private PostgreSQL
store, local PostgreSQL integration gate, cloud reset endpoint, weekly recap
endpoint, strict Flutter client, and Settings UI are implemented. Supabase
deployment and live-provider wiring remain.

### 1.3 Replace

| Current implementation | Replacement |
| --- | --- |
| `POST /v1/lesson-turns` | Delete after protocol-v3 cutover |
| `POST /v2/performance-turns` NDJSON | Persistent protocol-v3 event WebSocket |
| `OpenAIAvaModel` Responses call for every turn | OpenAI Realtime sideband coordinator |
| Entire-plan-before-TTS orchestration | Incremental validated segment pipeline |
| ElevenLabs HTTP `stream/with-timestamps` | ElevenLabs input-streaming WebSocket |
| Base64 audio inside JSON lines | Bounded binary PCM frames |
| `SystemSpeechInputService` | WebRTC microphone track |
| Client-provided recent history | Backend authoritative delivered-turn context |
| `DiagramSpec` fraction/number-line union | `AvaCanvasScene` and transactions |
| `SvgPicture.string` generated markup | Native validated vector renderer |
| Lesson/progress terminology | Conversation, explanation, memory and recap terminology |

### 1.4 Remove after replacement gates pass

The legacy Flutter fixed-SVG rendering path was removed early on 2026-07-30
by explicit product decision. `flutter_svg`, the fixed dashboard widgets and
the fixed SVG asset are gone. Protocol-v2 diagram cue events now fail
explicitly; only validated protocol-v3 canvas transactions may open a visual
panel.

Do not delete these in advance of a verified replacement:

- `lib/services/production_conversation_service.dart`;
- `lib/services/performance_stream_service.dart`;
- `lib/services/speech_input_service.dart`;
- `lib/services/speech_output_service.dart`;
- `lib/models/lesson_response.dart`;
- protocol-v1/v2 Dart models and fixtures;
- backend `/v1` and `/v2` routes, orchestration and stream composer;
- `backend/src/ava_backend/providers/openai_speech_provider.py`;
- `assets/learning_activities.json`;
- activity catalog/model/picker code;
- learning-progress model/service/provider/sheet/tests;
- math/science score fields and old progress database table;
- obsolete response-animation audition UI and code not used by touch or the
  restrained speaking baseline.

Deletion occurs in the same change that removes all references and includes the
data migration where persistence is affected.

## 2. Work before API keys

The first five phases use deterministic fake providers and checked-in fixtures.
They should be completed before buying or configuring live provider usage.

## Phase 0 — architecture freeze

Status: completed on 2026-07-29.

Deliverables:

- this architecture and implementation plan;
- rewritten `AGENTS.md`;
- old planning documents removed;
- README files point only to the new architecture;
- one vocabulary for session, turn, segment, canvas transaction, delivered
  prefix, cancel and terminal state.

Gate:

- `rg` finds no document presenting protocol v1/v2, fixed diagrams, activities,
  progress scores, platform TTS, or full-turn Responses as the production plan.

## Phase 1 — protocol v3 and deterministic state machines

Status: completed on 2026-07-29. The shared fixtures, strict Python/Dart
contracts, incremental backend parser, mirrored turn state machines, bounded
buffers, deterministic fake streams, and native rendered-sample/fade-stop PCM
contract are implemented and verified. No live provider is connected.

Create a transport-independent shared contract before network code.

Backend:

- add strict Pydantic models for session capabilities, assistant text and the
  optional canvas function, normalized alignment, canvas scene/transactions,
  control events and errors;
- add a `RealtimeSessionCoordinator` interface;
- add a bounded `StreamingSpeechParser` that accepts arbitrary text-delta
  fragmentation and releases only complete speakable phrases;
- add a turn state machine with revisioned, idempotent cancellation;
- add a bounded queue abstraction and explicit overflow errors.

Flutter:

- add matching Dart protocol models;
- add an `AvaRealtimeTransport` interface independent of WebRTC;
- add mirrored state/sequence validation;
- extend PCM service contract with sample progress and fade-stop.

Shared fixtures:

- valid one-segment conversation;
- multi-segment explanation with canvas;
- segment fragmented at every byte boundary;
- invalid enum/unknown key/oversized text;
- out-of-order and duplicate events;
- cancel during planning, TTS and playback;
- partial completion and persistence failure;
- malformed binary-audio metadata.

Tests:

- Python and Dart parse/round-trip the same fixtures;
- property/fuzz tests cover fragmentation and event ordering;
- no state after cancelled/terminal can return to speaking;
- queue limits fail explicitly.

Gate:

- the complete question-to-idle state machine runs with fake audio and no
  network or provider key.

## Phase 2 — dynamic local vector canvas

Status: completed on 2026-07-30.

This phase proves that drawings are dynamic without allowing raw model SVG.

Models:

- `AvaCanvasScene`;
- stable node IDs and scene revisions;
- typed node union;
- typed state variables;
- typed transactions and phrase anchors;
- typed local interaction bindings/outcomes;
- provider-free compound explore/reveal/toggle/drag/state/reset execution;
- ID-only `select`/`submitOutcome` event reporting with canonical backend
  resolution;
- complete-scene specialist revisions compiled into stable-ID atomic patches;
- renderer capability/limit manifest.

Validation:

- exact allowed keys;
- all numeric values finite and in bounds;
- referential integrity;
- path command parser and complexity bounds;
- clipped viewport;
- text/font/color/stroke/transform allowlists;
- transaction atomicity;
- interaction target and effect validation;
- accessibility labels for interactive nodes.

Rendering:

- Flutter custom painter/render object;
- local tweening at display refresh rate;
- deterministic hit testing;
- Reduce Motion behavior;
- dashboard open/resize/close transitions that preserve avatar interaction;
- optional scene-to-SVG serializer only for golden verification.

Fixtures should demonstrate variety, not a hardcoded catalog:

- free-form labeled plant cell;
- solar-system relationship;
- triangle construction;
- force arrows;
- timeline;
- bar/line comparison;
- custom curved path;
- interactive drag/toggle/reveal.
- layered multi-stage explore with exclusive detail cards and connector pulses.

Tests:

- parser and validation limits;
- golden images on iPhone and iPad dimensions;
- hit tests and local interactions;
- malformed paths and node explosions rejected;
- avatar touches still win when a pointer begins on the avatar;
- canvas animation does not exceed frame budget.

Gate:

- all fixture scenes are generated from the same general scene grammar and
  render interactively without raw SVG, HTML or special-case diagram code.

## Phase 3 — backend coordinator with fake providers

Refactor the backend around session ownership while keeping current routes
temporarily available for comparison tests.

Backend status: prompt/context/tool construction, personality evaluations, the
incremental turn parser, deterministic Realtime/TTS streams, ordered
control/binary PCM framing, sample-aligned delivered-prefix accounting,
cancellation, and the authoritative memory boundary now run through one tested
coordinator lifecycle. The backend now also owns a bounded expiring session
registry, separately scoped HMAC-authenticated one-use WebRTC and event tokens,
strict session creation, bounded SDP negotiation, provider call-ID ownership,
and an authenticated WebSocket bridge that preserves immediate JSON-descriptor
/ binary-PCM ordering. Provider transcripts are optional: a missing transcript
is stored as an explicitly labeled model summary and cannot support stable
memory evidence. The phase gate remains open for the cross-process and
physical-device runs.

Status updated by the 2026-08-09 connection audit: the production coordinator
now creates repeated isolated turn lifecycles over one negotiated WebRTC,
OpenAI sideband, and Ava event WebSocket. Flutter consumes those turns through
one runtime, sends `clientReady` once, keeps client controls monotonic across
the conversation, and resets server sequence/revision validation per turn. The
microphone sender remains attached and muted/unmuted through track state; only
conversation shutdown destroys it. Deterministic two-turn gates prove one
provider negotiation, two memory commits, and no presentation-state leakage.
App startup now prepares the complete connection through `clientReady` and the
validated listening phase with one attached but disabled microphone track.
The first explicit microphone tap enables the sender without renegotiating;
later taps only change that track state. Resuming
within the bounded two-minute warm window performs no bootstrap, SDP, ICE,
sideband, or event-WebSocket setup; explicit reset, disposal, expiry, and
failures still release the call and physical capture.
The shell now models microphone intent independently from the response phase,
so Listening → Thinking → Speaking leaves the conversation visibly active and
does not invite an accidental stop/restart tap. Rapid pause/resume intent is
coalesced so an older cancellation acknowledgement cannot overwrite the latest
microphone choice.
Physical-device latency, VAD-driven automatic barge-in, route/lifecycle events,
and bounded reconnect remain open release gates.

Canvas session-ownership status (2026-08-12): the canonical scene is now owned
by one persistent coordinator instead of an LRU keyed by child subject.
Semantic canvas controls no longer carry a turn ID. Select/submit outcomes are
resolved from canonical bindings, refresh bounded Realtime context without
starting inference, and remain available between turns. Flutter preserves the
scene across turns but clears it for a new session. Stale scene/revision/binding
controls emit ordered non-terminal `interactionRejected` frames; deterministic
Python and Dart tests prove they do not fail speech, close transport, or block
the following turn. A later canvas request receives the complete active draft
and produces the next atomic scene revision.

Flutter integration status (2026-07-30): the shared fake lifecycle now crosses
the production-shaped Flutter frame boundary, validates an `audioChunk`
descriptor followed immediately by binary PCM, plays through the existing
sample-counted native player, reports periodic and final rendered samples,
schedules captions/visemes/canvas from that clock, and exercises exact
barge-in/cancellation. Invalid optional canvas transactions remain closed while
speech completes. This completes the Flutter half of the Phase 3 gate. The
Flutter bootstrap client now creates the v3 session with age band only,
validates the bounded response, creates an audio-only WebRTC peer and
microphone track, exchanges bounded SDP through the backend, and assembles the
same runtime over the live event socket. Deterministic media and SDP fakes test
the full interface without provider keys. The cross-process and
physical-device network gates remain open; the provider-facing
Realtime/sideband adapter is deliberately not replaced by a silent fake in
normal app startup.

Canvas-quality status (2026-08-14): the v2 specialist owns the complete
composition rather than emitting generic semantic bubbles for Flutter to
rearrange. Its strict document includes visual type, topology, stable semantic
entities, bounded groups/pivots, exact colors, gradients, shadows, authored
paint order, interactions, and optional slow repeating motion. Flutter
preserves that geometry, renders it natively, and expands compact controls to
at least 44 physical points after viewport scaling. Explore remains instant
locally but its validated binding ID is also reported as semantic selection
context, enabling follow-up questions about a touched object. Before
publication the backend checks entity coverage, composition, text collision,
connector attachment, palette quality, and target size, with at most one
strict repair request. The dynamic quality matrix remains a prompt/evaluation
corpus, never a diagram catalog. A guarded live v2 gate and updated
multi-device goldens remain required before release.

New boundaries:

```text
ava_backend/
├── realtime/
│   ├── coordinator.py
│   ├── session.py
│   ├── turn_parser.py
│   ├── cancellation.py
│   └── events.py
├── canvas/
│   ├── protocol.py
│   ├── validation.py
│   └── specialist.py
├── speech/
│   ├── streaming.py
│   ├── alignment.py
│   └── delivery.py
├── memory/
│   ├── protocol.py
│   ├── context.py
│   └── store.py
└── providers/
    ├── realtime.py
    ├── elevenlabs_streaming.py
    └── canvas_specialist.py
```

Responsibilities:

- session creation/expiry;
- authenticated event WebSocket;
- fake Realtime delta stream;
- fake ElevenLabs PCM/alignment stream;
- incremental segment pipeline;
- cue resolution;
- binary audio framing;
- playback progress and delivered-prefix ledger;
- cancellation propagation;
- memory commit only at a correct terminal result;
- structured metrics.

Tests:

- slow producer/slow consumer backpressure;
- client disconnect at every phase;
- provider disconnect/reconnect;
- repeated cancel;
- audio chunk loss/duplicate/order;
- alignment arriving before/after audio;
- canvas invalid while speech remains valid;
- persistence unavailable after playback;
- session expiry and unauthorized socket access.

Gate:

- a local integration test streams fake child-turn events through the backend,
  plays deterministic PCM in Flutter, schedules captions/visemes/canvas, and
  records only the delivered prefix after interruption.

## Phase 4 — Flutter realtime transport

Dependencies:

- add a maintained Flutter WebRTC package after iOS/iPadOS/macOS support and
  license review;
- use `dart:io` WebSocket or a small well-maintained client only if its
  cancellation and binary-frame behavior is testable;
- avoid introducing an additional state-management or networking framework.

Implementation:

- backend session bootstrap and authenticated event socket (implemented);
- backend SDP exchange and provider call ownership (implemented with strict
  live interfaces and deterministic fakes; live OpenAI adapter remains);
- microphone permission and persistent WebRTC track lifecycle (implemented;
  physical route/lifecycle verification remains);
- authenticated `dart:io` event WebSocket with ping/timeout (implemented);
- strict Flutter session-bootstrap client and runtime assembly (implemented);
- bounded reconnect without stale-turn replay (remaining);
- strict JSON/binary PCM demultiplexing (implemented);
- persistent protocol-v3 conversation runtime with isolated turn state
  (implemented);
- lifecycle/background/audio-route handling;
- immediate local speaking-state and touch composition (implemented);
- caption, viseme and canvas scheduling from rendered sample offsets
  (implemented);
- exact native finish/stop sample reporting and keyless deterministic transport
  (implemented).

Keep the existing turn-based session controller behind a development-only
comparison switch until this phase passes. It is not a release fallback.

Gate:

- fake backend works on macOS, physical iPhone and physical iPad;
- microphone, avatar touch, rotation, background, settings and dashboard do not
  intercept each other;
- foreground/background and route changes do not revive stale audio.

## Phase 5 — interruption, sync and resilience

Implement:

- VAD speech-start interruption path;
- short click-free PCM fade;
- exact final rendered sample report;
- delivered text prefix computation;
- OpenAI/ElevenLabs cancellation fan-out;
- provider history reconciliation;
- sequence/revision stale-event rejection;
- reconnect with bounded restored context;
- jitter/underrun metrics;
- canvas preload and cue deadline handling.

Test matrix:

- interrupt before first audio;
- interrupt during every segment;
- interrupt exactly at a canvas cue;
- double interruption;
- network loss while listening/planning/speaking;
- app background during speech;
- Bluetooth/headphone route changes;
- server restart and expired session;
- malformed provider delta after valid spoken prefix.

Gate:

- interruption reaches silence quickly and never stores or later plays the
  unheard suffix.

## 3. Live provider integration

API keys first become useful here.

## Phase 6 — OpenAI Realtime integration

Implementation status on 2026-08-02:

- SDP call creation, provider call-ID retention, sideband WebSocket attachment,
  session updates, cancellation/hangup, bounded events, usage parsing, and the
  guarded live probe are implemented;
- assistant text is the authoritative spoken response, OpenAI audio output is
  disabled, parallel tool calls are disabled, and one semantic canvas function
  is optional under `tool_choice=auto`;
- the capped greeting probe passed at about 727 ms to first argument delta and
  1.17 seconds to completion, using 1,748 input and 120 output text tokens;
- the canvas probe correctly failed closed. This proved that Realtime function
  arguments are unsuitable for authoring the complete scene geometry;
- no scene was accepted or sent to Flutter;
- only `request_interactive_canvas` carries a compact semantic brief and
  optional interaction goal;
- a strict `gpt-5.6-luna` Responses specialist converts that brief into a
  generic node/interaction draft without a scene ID. The backend owns scene
  and transaction identity plus revision data, requires at least one usable
  local interaction, compiles the draft, and applies the strict validator;
- offline parser/compiler/coordinator and failure-isolation gates pass; one
  explicitly authorized live specialist probe remains.

Configuration:

- `AVA_OPENAI_API_KEY`;
- backend model ID;
- session/turn/audio limits;
- provider timeout/retry policy;
- explicit live-test spending flag.

Implement the provider adapter:

- backend-mediated SDP negotiation;
- server sideband attachment;
- `session.update` for prompt, tools, VAD and text-only output;
- natural assistant-text streaming plus at most one optional semantic canvas
  function call;
- a matching bounded `function_call_output` and one tool-disabled continuation;
- usage and provider request IDs;
- response cancellation and provider-item reconciliation.

Live tests begin with:

- one short greeting;
- one interruption;
- one simple canvas turn;
- one unsafe/unsupported request;
- one reconnect.

Do not put canvas geometry back into the Realtime tool schema.

Gate:

- model output passes the same fake-provider contract with measured latency and
  bounded spend.

The text/greeting half of this gate passes. Canvas intent/brief parsing is green
offline. Live canvas validation belongs to the separate strict specialist gate
and does not block live ElevenLabs voice integration.

## Phase 7 — ElevenLabs production voice

Before the key is used, update
`avatar/sounds/ava_voice_selection.v1.json` with:

- exact provider voice ID;
- exact model ID;
- generation settings;
- commercial-use rights evidence/status;
- approved production use.

The current file is not production-ready: it has no voice ID or model ID and
marks commercial rights unverified and use as research-only.

Configuration:

- `AVA_ELEVENLABS_API_KEY`;
- exact voice ID/model ID;
- output format and voice settings;
- audio/character limits;
- live-test spending flag.

Implement:

- input-streaming WebSocket;
- text chunk/flush policy;
- PCM/alignment validation;
- cancellation and connection cleanup;
- provider usage/error telemetry;
- deterministic alignment-to-viseme stage.

Test:

- short/long sentences;
- punctuation, numbers and names;
- rapid cancellation;
- reconnect;
- silence/empty or malformed provider output;
- cue/viseme drift;
- output consistency across repeated sessions.

Gate:

- the selected voice passes user approval on physical devices and all speech
  timing is driven by rendered samples.

## Phase 8 — end-to-end vertical slice

Run:

`child audio -> Realtime -> validated segment -> ElevenLabs -> PCM/alignment -> captions/visemes/canvas -> idle`

Acceptance:

- no platform TTS or canned response;
- no full-turn wait before first TTS segment;
- natural interruption;
- touch and 3D interaction remain responsive;
- dashboard appears only for validated canvas content;
- no stale audio/canvas after cancel;
- typed errors include request/session/turn IDs;
- measured latency/cost captured for every test turn.

Only after this gate is approved should v1/v2 runtime removal begin.

## 4. Memory, recap and reset

## Phase 9 — delivered conversation memory

Status: implemented locally on 2026-08-01. The standard PostgreSQL schema and
async store run unchanged against PostgreSQL 17 and are intended for the
Supabase direct/pooler PostgreSQL URL. Supabase deployment verification remains
before release.

Schema:

- delivered child/Ava turn ledger;
- terminal/interruption state;
- delivered text range and timing;
- current-session summary;
- typed stable preference/interest proposal with source turn;
- weekly topic/event record;
- reset revision/tombstone.

Rules:

- raw audio not stored;
- exact DOB/name remain profile-side only;
- only age band enters model context;
- unplayed generated text never enters memory;
- stable memories require an allowlisted type and evidence;
- bounded context builder reports omitted/compacted material.

Reset:

- authenticated cloud reset first;
- matching local cache reset;
- clear user-visible completion or actionable failure;
- preserve profile, DOB, captions, avatar and app configuration.

Weekly recap:

- aggregate stored typed summaries, not another model call by default;
- concise topics and notable events only;
- no scores, mastery, diagnosis, emotional certainty or hidden inference;
- mark time range and data freshness;
- add a read-only Settings entry only when the data path is complete.

Gate:

- reset and recap behavior are covered across offline, partial failure,
  reconnect and multiple-device cases.

## 5. On-demand canvas specialist

## Phase 6A — strict canvas generation

Build an evaluation set of canvas briefs and score the Responses specialist on:

- schema validity;
- visual correctness;
- layout/readability;
- interaction correctness;
- latency;
- cost;
- child appropriateness.

Enable the live `gpt-5.6-luna` call only after strict parse, compilation,
validation, timeout, cancellation, and spend-limit tests pass. Invoke it only
when Realtime semantically selects `request_interactive_canvas`; no-canvas
conversation never pays this cost. Canvas failure must remain independent of
speech delivery.

The specialist receives only the minimum visual brief, age band and approved
style/capability manifest. It never receives provider keys, raw child audio,
exact profile data, or control of Ava's skeleton.

## 6. Legacy removal and cleanup

After the v3 gate:

1. remove v1/v2 backend routes and old config;
2. remove old Flutter conversation transports/controllers/models;
3. remove platform speech paths and OpenAI audition TTS;
4. remove remaining activity/progress runtime and assets;
5. migrate the local database away from score/progress fields;
6. update tests to v3 terminology;
7. remove other dependencies left unused by the migration;
8. remove obsolete prompt layers and environment variables;
9. prove no release code can select a legacy runtime.

This is one controlled cleanup phase, not a series of half-removed
compatibility paths.

## 7. Optimization plan

### Latency

- direct WebRTC microphone media;
- selected-tool-first streaming function-delta parser;
- validate and synthesize per segment;
- persistent sideband/event channels;
- semantic TTS chunks rather than character-at-a-time writes;
- pre-open TTS connection at turn-planning start if ElevenLabs permits it
  without billable generation;
- preload validated canvas transactions;
- never wait for memory writes before first audio.

### Audio quality and responsiveness

- bounded PCM ring buffer;
- minimum startup buffer determined from device tests;
- underrun detection;
- audio route/sample-clock continuity;
- click-free fade on interruption;
- no resampling in Dart;
- native player owns rendered-sample truth.

### UI and 120 Hz

- canvas and living world in separate repaint boundaries;
- local tweens;
- bounded node/particle pools;
- no SVG reparsing for every value change;
- pause nonessential animation in background/thermal pressure;
- honor Reduce Motion;
- performance overlays only in debug.

### Model cost

- mini Realtime default;
- stable cached prompt/tool prefix;
- bounded short speech;
- specialist absent from no-canvas turns and bounded on visual turns;
- no model for touch, idle, rendering, weekly aggregation or frame motion;
- cancel immediately on disconnect/interruption;
- per-session audio/speech/canvas limits;
- daily/global spend circuit breaker.

### Storage and privacy

- compact typed memory;
- expiry/retention jobs;
- transcript-free normal logs;
- no raw audio persistence;
- encrypted transport and managed encrypted storage;
- data export/delete and consent review before release.

## 8. Production infrastructure still required

The two provider keys are not the whole deployment. Before App Store release,
configure:

- backend hosting with WebSocket and low-latency regional support;
- HTTPS/WSS domain and certificates;
- durable Postgres/Supabase database;
- production authentication;
- server secret management;
- rate limiting and abuse protection;
- telemetry/error reporting with redaction;
- spend alerts and hard budgets;
- backup/restore and retention;
- privacy policy, terms, parental consent and child-safety review;
- App Store microphone/privacy disclosures;
- staging and production environments with separate keys.

## 9. Principal risks and controls

| Risk | Control |
| --- | --- |
| Realtime function arguments do not yield a valid segment soon enough | Small first segment, streaming parser, latency eval, strict timeout |
| External TTS makes model history disagree after interruption | Rendered-sample delivered ledger and provider-history reconciliation |
| Dynamic canvas produces unsafe or huge scenes | Declarative schema, hard complexity limits, atomic validation, no raw markup/code |
| Voice cost dominates | Short responses, cancellation, account budgets, measured model/voice choices |
| Child privacy exposure | Backend-only keys/context, age band only, typed memory, redacted logs, no raw audio |
| WebRTC/native plugin instability | Early physical-device spike and lifecycle/audio-route suite |
| Legacy paths cause divergent behavior | No release fallback; remove all legacy selection after v3 gate |
| Canvas specialist adds latency | First speech remains independent; explicit deadline, cancellation, cache and hard per-turn/daily budgets |
| Memory claims too much | Weekly recap from bounded factual topic/event records only |
| Avatar regressions during transport rewrite | Full touch/picking/camera/speech composition suite at every shared-state change |

## 10. Required verification before release

Backend:

```sh
cd backend
/opt/miniconda3/envs/ml/bin/python -m pytest
/opt/miniconda3/envs/ml/bin/python -m ruff check src tests
/opt/miniconda3/envs/ml/bin/python -m ruff format --check src tests
/opt/miniconda3/envs/ml/bin/python -m mypy src
```

Flutter:

```sh
/opt/homebrew/share/flutter/bin/flutter analyze
/opt/homebrew/share/flutter/bin/flutter test
/opt/homebrew/share/flutter/bin/flutter build macos
/opt/homebrew/share/flutter/bin/flutter build ios --no-codesign
```

Physical-device matrix:

- supported small and large iPhones;
- supported iPads, portrait and landscape;
- built-in speaker/mic, wired, Bluetooth and AirPods;
- Wi-Fi, cellular, latency/loss simulation and offline transition;
- foreground/background, lock/unlock and phone-call interruption;
- 30+ minute conversation session;
- 60/120 Hz devices;
- Reduce Motion and thermal/low-power states.

Release is blocked by any secret in the bundle, unbounded queue, stale-turn
revival, silent provider substitution, or inability to delete conversation
memory.
