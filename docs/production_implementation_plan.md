# Ava production implementation plan

Status: active plan  
Architecture: `docs/production_architecture.md`  
Last reviewed: 2026-08-21

This plan is based on the code currently in the repository. It separates work
that can be completed without provider credentials from live integration that
requires the OpenAI and ElevenLabs keys.

## 1. Current repository audit

### 1.1 Keep and adapt

| Existing area | Why it stays | Required adaptation |
| --- | --- | --- |
| `lib/ui/avatar/` | Production avatar, real picking, touch, camera return, idle and lip-sync boundaries already exist | Drive speaking state from protocol v4 sample-clock events; remove obsolete response-gesture audition paths only after regression coverage |
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
2. Guarded production verification of the wired OpenAI Realtime SDP/sideband
   adapter, provider-item reconciliation, cancellation, and reconnect.
3. Deployment verification of the authenticated protocol-v4 event WebSocket,
   scoped SVG document endpoint, token rotation, limits, and abuse controls.
4. Guarded live Sol corpus evaluation of the implemented exact extraction,
   non-mutating validator, hash-bound manifest, independent document delivery,
   and presentation acknowledgement path.
5. Guarded live and physical-device validation of the implemented ElevenLabs
   input-streaming WebSocket TTS adapter.
6. Physical-device verification of the implemented Flutter rendered-sample
   playback progress and delivered-sample-to-delivered-text ledger.
7. Deterministic text/audio alignment to VRM visemes.
8. Physical-device and performance verification of the direct SVG renderer,
    element hit testing, independent fetch, and candidate activation handshake.
9. Physical-device barge-in, provider cancellation, stale-event suppression,
   route-change, reconnect, and long-session verification.
10. Production authentication, rate limits, App Attest/abuse controls.
11. Supabase deployment verification of the implemented portable PostgreSQL
    delivered-turn persistence, summary compaction, cloud reset and weekly
    recap.
12. Per-turn latency, queue, provider-usage and cost telemetry.

### 1.2.1 Implemented protocol-v3 brain foundation retained by v4

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
| Legacy lesson-turn HTTP route | Removed after protocol-v4 cutover |
| Legacy performance-turn NDJSON route | Persistent protocol-v4 event WebSocket |
| `OpenAIAvaModel` Responses call for every turn | OpenAI Realtime sideband coordinator |
| Entire-plan-before-TTS orchestration | Incremental validated segment pipeline |
| ElevenLabs HTTP `stream/with-timestamps` | ElevenLabs input-streaming WebSocket |
| Base64 audio inside JSON lines | Bounded binary PCM frames |
| `SystemSpeechInputService` | WebRTC microphone track |
| Client-provided recent history | Backend authoritative delivered-turn context |
| Protocol-v3 typed canvas scene/transactions | Exact Sol-authored SVG plus hash-bound semantic manifest |
| Primitive custom-painter reconstruction | Direct validated SVG renderer with element hit testing |
| Lesson/progress terminology | Conversation, explanation, memory and recap terminology |

### 1.4 Remove after replacement gates pass

The legacy fixed diagram catalog and asset were removed early on 2026-07-30.
Protocol-v2 diagram cues still fail explicitly. Protocol-v4 reintroduces
runtime SVG only as a complete Sol-authored, non-mutatingly validated document;
it does not revive the fixed catalog or accept arbitrary client markup.

The backend lesson-turn and performance-turn routes, their orchestration, and
their stream composer were removed after the verified protocol-v4 replacement.
Do not delete these remaining compatibility assets without a separate proof:

- `lib/models/lesson_response.dart`;
- protocol-v1/v2 Dart models and fixtures;
- `backend/src/ava_backend/providers/openai_speech_provider.py`;
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

## Phase 1 — protocol v4 and deterministic state machines

Status: implemented and green under deterministic backend/Dart fixtures.
Protocol-v3 speech, alignment, cancellation, rendered-sample PCM, and
persistent-conversation behavior remain the proven foundation.
Protocol v4 replaces the incompatible canvas wire contract and fixes microphone,
turn-creation, caption, transcript, and session-state defects exposed by the
migration.

Backend additions:

- strict candidate/prepared/presented/active canvas state;
- immediate accepted/rejected Realtime tool result with generation ownership;
- `canvasDocumentAvailable`, `canvasPresent`, `canvasActivated`,
  `canvasRejected`, and `interactionRejected` controls;
- `canvasDocumentPrepared` and `canvasPresented` client acknowledgements;
- independent authenticated document retrieval;
- compare-and-swap base scene/revision checks and bounded timeouts;
- explicit committed-turn transcription attributed by provider item ID.

Flutter additions:

- matching strict Dart protocol models and shared fixtures;
- independent exact document fetch and hash verification;
- renderer preload, first-frame presentation acknowledgement, and activation;
- microphone intent independent of turn phase;
- active-turn rejection before `turnCreated` reset;
- sample-clock-only captions and visible non-terminal interaction errors.

Tests cover malformed, stale, replayed, superseded, interrupted, disconnected,
timed-out, hash-mismatched, and out-of-order document/acknowledgement states in
addition to the retained protocol-v3 audio fixtures.

Gate:

- fake Realtime, Sol, TTS, document transport, and Flutter execute two turns,
  one canvas creation, one revision, interruption before presentation, and a
  semantic interaction without network/provider keys or state divergence.

## Phase 2 — exact Sol SVG canvas

Status: locally implemented and green under exact-byte, validator, state,
transport, Flutter renderer, full test, and native-build gates. Guarded live Sol
corpus evaluation and physical-device visual approval remain. This phase
supersedes the completed protocol-v3 typed canvas. The old implementation
remains only until every replacement gate passes; there is no automatic runtime
fallback.

Provider boundary:

- `gpt-5.6-sol` returns one complete SVG assistant text payload;
- completed Responses status only; reasoning bookkeeping is allowed;
- exact Unicode-to-UTF-8 encoding with no normalization or stripping;
- complete active SVG supplied for revisions;
- explicit model, reasoning, timeout, output, repair, and spend configuration.

Validation and manifests:

- hardened non-mutating XML/SVG inspection;
- active/external content rejection;
- syntax and render-expansion graph limits with cycle rejection;
- explicit CSS and renderer-capability policy;
- stable element/binding IDs and bounded `data-ava-*` metadata;
- deterministic interaction/accessibility manifest;
- exact SVG and canonical-manifest hashes plus validator/capability versions;
- one complete Sol repair at most; no backend artwork repair.

Delivery and activation:

- SVG never enters the PCM WebSocket queue;
- scoped header-authenticated, no-store canvas document fetch;
- candidate fetch/hash/preload acknowledgement;
- coordinator-owned phrase/segment anchor mapped to rendered samples;
- first-rendered-frame `canvasPresented` acknowledgement;
- backend activation only after actual presentation;
- prior active scene preserved through every candidate failure.

Rendering and interaction:

- direct SVG rendering without primitive reconstruction;
- renderer selected from the frozen live Sol corpus;
- element-level hit testing or exact validated hit geometry;
- 44-point physical target expansion without visible geometry changes;
- provider-free local reveal/toggle/drag/focus/reset/selection;
- ID-only semantic actions resolved from the backend manifest;
- Reduce Motion, accessibility, avatar-touch, and frame-budget verification.

Gate:

- accepted Sol bytes hash identically at extraction, storage, fetch, preload,
  and presentation; the representative corpus retains visual fidelity on
  iPhone, iPad, and macOS; maximum accepted canvas delivery cannot underrun PCM.

## Phase 3 — backend coordinator with fake providers

Status: deterministic/keyless implementation complete. The full backend suite
passes; cross-process deployment and live-provider gates remain.

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

Status: protocol-v4 bootstrap, WebRTC reuse, raw event transport, independent
SVG fetch, PCM adapter, persistent controller, and production screen cutover
are implemented. Flutter analysis/tests and unsigned macOS/iOS builds pass;
physical-device gates remain.

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

Status: deterministic microphone-race, active-turn replacement, sample-clock
caption, committed-transcript attribution, cancellation, stale-event, and
delivered-prefix gates pass. Physical audio-route, backgrounding, reconnect,
and latency measurements remain.

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
- the protocol-v3 strict typed canvas experiment demonstrated that Realtime
  arguments must stay semantic, but constrained Luna/primitive output did not
  meet the measured visual-quality baseline;
- protocol v4 therefore keeps Realtime semantic-only and replaces the canvas
  specialist with exact Sol-authored SVG, independent delivery, and
  presentation acknowledgement;
- offline parser/coordinator and failure-isolation gates pass for the retained
  speech foundation; the v4 SVG and activation gates remain active.

Configuration:

- `AVA_OPENAI_API_KEY`;
- backend model ID;
- session/turn/audio limits;
- provider timeout/retry policy;
- explicit live-test spending flag.

Implemented provider adapter:

- backend-mediated SDP negotiation;
- server sideband attachment;
- `session.update` for prompt, tools, VAD and text-only output;
- natural assistant-text streaming plus at most one optional semantic canvas
  function call;
- an immediate matching accepted/rejected `function_call_output` and one
  tool-disabled continuation independent of Sol completion;
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

Status: the phrase-bounded input-streaming WebSocket adapter, strict PCM and
exact character-alignment validation, bounded transport chunking, explicit
concurrency rejection, cancellation, and production-only configuration gate
are implemented with deterministic fakes. Exact approved voice metadata,
guarded live-provider runs, viseme conversion, and physical-device approval
remain open.

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

Implemented locally:

- input-streaming WebSocket;
- text chunk/flush policy;
- PCM/alignment validation;
- cancellation and connection cleanup;

Still required:

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

## Phase 6A — exact Sol SVG generation

Freeze the successful experimental corpus and score the Responses specialist on:

- exact output/status validity;
- visual correctness;
- layout/readability;
- interaction correctness;
- latency;
- cost;
- child appropriateness.

Enable the live `gpt-5.6-sol` call only after exact extraction, non-mutating
validation, renderer capability, independent document delivery, activation,
timeout, cancellation, and spend-limit tests pass. Invoke it only when Realtime
semantically selects `request_interactive_canvas`; no-canvas conversation never
pays this cost. Canvas failure remains independent of speech delivery.

The specialist receives only the minimum visual brief, age band, approved
renderer capability manifest, and complete current accepted SVG when revising.
It never receives provider keys, raw child audio, exact profile data, or control
of Ava's skeleton.

## 6. Legacy removal and cleanup

After the v4 gate:

1. remove v1/v2 backend routes and old config;
2. remove old Flutter conversation transports/controllers/models;
3. remove platform speech paths and OpenAI audition TTS;
4. remove remaining activity/progress runtime and assets;
5. migrate the local database away from score/progress fields;
6. update tests to v4 terminology;
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
- measure whether safe phrase-socket preconnection improves latency without
  weakening cancellation ownership or creating billable/stale generation;
- preload validated SVG candidates over the independent document lane;
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
- no provider call or full document parse for each local interaction frame;
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
| Dynamic canvas produces unsafe or pathological scenes | Non-mutating active-content rejection, syntax and render-graph limits, hash-bound manifests, renderer sandbox |
| Voice cost dominates | Short responses, cancellation, account budgets, measured model/voice choices |
| Child privacy exposure | Backend-only keys/context, age band only, typed memory, redacted logs, no raw audio |
| WebRTC/native plugin instability | Early physical-device spike and lifecycle/audio-route suite |
| Legacy paths cause divergent behavior | No release fallback; remove all legacy selection after v4 gate |
| Canvas document blocks audio | Independent authenticated fetch lane; SVG never enters the ordered PCM socket |
| Client/backend canvas state diverges | Candidate/prepared/presented/active handshake; activation after first rendered frame |
| Canvas specialist adds latency | Immediate accepted tool output, independent first speech, explicit deadline, cancellation, cache and hard per-turn/daily budgets |
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
