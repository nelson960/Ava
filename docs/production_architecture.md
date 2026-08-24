# Ava production architecture

Status: locked target architecture  
Protocol target: v4
Last reviewed: 2026-08-21

This document describes the production system Ava is moving toward. It replaces
the old lesson-turn, fixed-diagram, activity/progress, and NDJSON performance
plans.

## 1. Product boundary

Ava is a realtime conversational character for children. Natural voice,
personality continuity, interruption, and the sense that the avatar is present
are the critical path. Explanations and drawings are capabilities inside that
conversation, not separate modes.

The runtime must provide:

- live child audio input;
- one consistent Ava personality;
- the selected ElevenLabs voice;
- synchronized captions and stylized lip sync;
- living idle and local touch/3D interaction;
- an optional dynamic vector canvas;
- bounded private memory and a weekly parent recap.

The runtime does not include an activity browser, lessons feed, games, scores,
curriculum state, a general whiteboard editor, arbitrary code execution, or
model-generated skeletal animation.

## 2. System overview

```mermaid
flowchart LR
    C["Flutter client<br/>avatar, mic, PCM, SVG canvas"] -->|"auth + SDP offer"| B["Ava backend"]
    B -->|"SDP negotiation"| R["OpenAI Realtime<br/>gpt-realtime-2.1-mini"]
    C <-->|"WebRTC child audio + realtime events"| R
    B <-->|"server sideband<br/>prompt, tools, function deltas"| R
    B <-->|"streamed text + audio/alignment"| E["ElevenLabs<br/>selected Ava voice"]
    B <-->|"binary audio + control frames"| C
    B -->|"separately authenticated exact SVG fetch"| C
    B <-->|"delivered turns + bounded memory"| D["Postgres/Supabase adapter"]
    B -->|"validated semantic canvas request"| S["OpenAI Responses canvas specialist<br/>gpt-5.6-sol"]
    S -->|"complete authored SVG"| B
```

There are two AI/voice provider accounts:

- OpenAI for Realtime conversation and strict on-demand canvas planning;
- ElevenLabs for Ava's production voice.

The database and deployment platform require ordinary infrastructure
credentials, but they are not additional conversation models.

## 3. Why this split

OpenAI Realtime consumes speech directly, understands the conversational
context, performs turn detection, and emits function calls. It is not asked to
generate audio because Ava's approved voice is in ElevenLabs.

ElevenLabs receives only validated assistant speech, streams voice audio and
alignment, and has no authority over memory, personality, canvas behavior, or
avatar movement.

The backend is the control plane. It owns policy, session state, validation,
cancellation, provider credentials, persistence, budgets, and the authoritative
record of what the child actually heard.

Flutter is the realtime presentation plane. It owns microphone capture,
sample-counted playback, avatar interpolation, touch, renderer-native SVG hit
testing, lip sync, captions, and presentation acknowledgement. No network event
drives a render frame.

## 4. Connection topology

### 4.1 Session creation

1. Flutter authenticates to the Ava backend.
2. Flutter calls `POST /v4/realtime/sessions`.
3. The backend returns an Ava session ID, protocol version, event-channel URL,
   feature limits, and a short-lived session token.
4. Flutter creates a WebRTC peer connection, attaches a disabled microphone
   track,
   and sends its SDP offer to
   `POST /v4/realtime/sessions/{session_id}/webrtc`.
5. The backend negotiates the offer with OpenAI Realtime using the server API
   key, retains the provider call ID, attaches its sideband WebSocket, and
   returns the SDP answer.
6. During app startup, Flutter applies the answer, opens the authenticated
   bidirectional event WebSocket, sends `clientReady`, and receives the
   validated `listening` phase while the microphone track remains disabled.
7. A microphone tap changes only the existing local track's enabled state.
   Media then flows directly over WebRTC, avoiding bootstrap, SDP, ICE,
   sideband setup, and an extra backend audio hop in the tap path.

The WebRTC peer, OpenAI sideband, and Ava event WebSocket remain open for a
bounded conversation containing consecutive turns. Each turn has a fresh turn
ID, request ID, stream ID, sequence/revision state machine, cancellation scope,
and delivered-memory commit. Muting changes the existing track's enabled state;
only conversation shutdown removes the sender, stops capture, and disposes the
stream. A user pause retains the muted call for at most two minutes so resume
does not renegotiate the provider connection. Expiry, reset, disposal, or a
provider/transport failure releases it and never silently creates a replacement
session.

Flutter tracks microphone intent separately from turn presentation phase.
`planning`, `synthesizing`, `speaking`, and the brief boundary between turns do
not imply that capture was disabled and must never change the microphone button
into a new-session control.

The create request contains only protocol version and the age band derived
locally from the stored DOB. It never contains the child name or exact DOB.
The event token is HMAC-authenticated, short-lived, one-use, scoped to one
opaque Ava session ID, and contains no child data. Canvas-document access uses
a separate short-lived bearer scoped to one session, generation, candidate,
and content hash. It is sent in an authorization header, never a URL. A claimed,
expired, unknown, or mismatched token fails explicitly; reconnect creates a new
Ava session rather than silently replaying the old turn.

The backend configures the Realtime session over the sideband channel:

- model: backend-configured `gpt-realtime-2.1-mini` initially;
- input: child audio;
- output modality: text only;
- server VAD and interruption enabled;
- explicit committed-turn input transcription for asynchronous memory;
- one optional semantic canvas function with `tool_choice=auto` and parallel
  calls off;
- versioned Ava prompt harness and bounded context;
- tool choice, output limits, and safety policy controlled server-side.

### 4.2 Ava event WebSocket and canvas document lane

The Ava event channel is not a provider socket exposed to Flutter. It is a
versioned application protocol.

Server-to-client control frames are JSON. TTS audio frames are binary and carry
an immediately preceding metadata frame containing:

- protocol version;
- Ava session ID;
- turn ID;
- audio stream ID;
- chunk sequence;
- sample offset;
- byte length.

Client-to-server frames include:

- `clientReady` once per conversation;
- `playbackStarted`;
- periodic bounded `playbackProgress`;
- `playbackStopped`;
- `canvasDocumentPrepared`;
- `canvasPresented`;
- `canvasInteraction`;
- `cancelTurn`.

SVG never shares this socket's ordered PCM queue. `canvasGenerationStarted`
announces accepted bounded Sol work so Flutter can show non-blocking drawing
progress before a document exists. `canvasDocumentAvailable` then announces a
bounded candidate and its hashes on the control socket. Flutter
fetches the exact `image/svg+xml` bytes from the independently authenticated
canvas-document endpoint with `Cache-Control: no-store`, verifies length and
hash, and preloads the selected renderer. Lost fetch responses may retry while
the scoped bearer and candidate remain valid; supersession, interruption,
session close, or expiry cancels the document.

App lifecycle, audio-route, and explicit close controls remain required release
work; they are not represented as implemented protocol events yet.

Every frame has a maximum size. Unknown frame types, invalid sequence numbers,
or stale revisions are rejected rather than ignored.

## 5. Realtime turn lifecycle

```mermaid
sequenceDiagram
    participant Child
    participant App as Flutter
    participant RT as OpenAI Realtime
    participant Backend
    participant TTS as ElevenLabs
    participant Canvas as Responses canvas specialist

    Child->>App: speaks
    App->>RT: microphone audio over WebRTC
    RT-->>Backend: assistant text deltas over sideband
    Backend->>Backend: incrementally parse bounded speech phrases
    Backend->>TTS: stream each validated phrase
    opt semantic canvas function selected
        RT-->>Backend: request_interactive_canvas call
        Backend->>Backend: validate brief, budget, and queue capacity
        Backend-->>RT: accepted generation ID or rejected function_call_output
        RT-->>Backend: short continuation text
        Backend->>Canvas: bounded brief + age band + current SVG
        Canvas-->>Backend: complete authored SVG
        Backend->>Backend: exact extraction + non-mutating validation
        Backend-->>App: canvasDocumentAvailable control
        App->>Backend: independent authenticated SVG fetch
        App->>App: verify hash and preload renderer
        App-->>Backend: canvasDocumentPrepared
    end
    TTS-->>Backend: PCM + alignment
    Backend-->>App: control events + binary PCM
    App->>App: sample-clock playback, captions, visemes
    Backend-->>App: sample-anchored canvas/expression cues
    App->>App: present prepared SVG at rendered sample
    App-->>Backend: canvasPresented
    Backend-->>App: canvasActivated
    App-->>Backend: rendered sample progress
    Backend->>Backend: persist delivered prefix and memory decisions
```

### 5.1 Assistant text and the optional canvas function

Realtime emits natural assistant text and may call one application-owned
function, `request_interactive_canvas`. The provider uses `tool_choice=auto`
with parallel calls disabled. A normal turn contains no function call. The
model decides semantically from the full conversation; application code never
routes on words such as “draw”, “show”, or “diagram”.

Assistant text is authoritative speech. A bounded streaming phrase parser
releases complete sentences immediately and splits an unusually long sentence
only at a safe whitespace boundary. Each released phrase is validated before
ElevenLabs receives it. The parser never depends on JSON property order.

The optional function contains only:

```text
request_interactive_canvas
├── brief
└── interactionGoal (optional)
```

The brief describes purpose, objects, relationships, flow, and essential labels.
It is bounded to 600 characters; the optional interaction goal is bounded to
160 characters. These limits align with the downstream specialist input rather
than forcing ordinary semantic descriptions into an unrealistically small
envelope.
It never contains nodes, coordinates, paths, styles, scene IDs, revisions, SVG,
or executable code. The backend validates the request and atomically reserves
queue and spend capacity. It then sends exactly one `function_call_output` with
the original call ID: either `accepted` with an application generation ID or a
bounded rejection. Accepted means scheduled, never ready. Realtime continuation
does not wait for Sol and must not claim the visual exists.

If Realtime emits invalid arguments or the backend cannot reserve bounded work,
the matching function output is rejected and speech continues. Later Sol,
validation, document-fetch, renderer, presentation, interruption, or timeout
failure produces a non-terminal `canvasRejected`, records the generation's
bounded status for later context, and preserves the previous active scene.

A successful or rejected optional canvas cannot invalidate already accepted
speech, mute the microphone, or close the persistent conversation. After the
immediate tool result, Realtime produces one short continuation with tool use
disabled for that continuation, preventing recursive canvas calls.

Memory proposals, summaries, avatar movement, and rendering details are not
part of the live model response. The delivered sample ledger is authoritative;
memory compaction happens independently after delivery and never gates speech.

### 5.2 Turn phases

The authoritative backend state machine is:

```text
created
-> connecting
-> listening
-> planning
-> synthesizing
-> speaking
-> completed

Any non-terminal state
-> cancelling
-> cancelled

Any non-terminal state
-> failed
```

Flutter mirrors these states but does not invent transitions. State changes are
idempotent and revisioned.

Connection, microphone intent, physical track state, turn state, canvas state,
and memory state are independent. A failed turn cannot mute the WebRTC track or
change the child's microphone choice. Only a genuine connection/media failure
forces both off.

Entering `speaking` is also the explicit end-of-synthesis boundary for the
current PCM stream. Every `audioChunk` descriptor and its immediately following
binary frame must arrive while the turn is `synthesizing`. Flutter rejects
late audio after `speaking`; this lets native playback accept `finish` exactly
once without racing a later append.

## 6. Voice, alignment, and lip sync

The first production format is signed 16-bit mono PCM at 24 kHz. It is larger
than compressed audio but keeps playback accounting and lip-sync timing
deterministic. Opus is a later optimization only after sample-accurate timing
is proven across route changes.

The backend opens one bounded ElevenLabs input-streaming WebSocket for each
complete validated speech phrase. Phrase ownership is explicit, so an
interruption closes the exact in-flight provider stream and no stale socket can
revive later audio:

1. authenticate with the selected voice/model/settings;
2. send one complete validated phrase;
3. flush that phrase immediately;
4. decode provider audio;
5. normalize alignment to text offsets and audio sample ranges;
6. send binary PCM and normalized alignment to Flutter;
7. close on phrase completion or cancel immediately on interruption/terminal
   state.

Character timing is not called phoneme timing. A deterministic alignment stage
converts text/audio alignment into the VRM's supported viseme timeline. It may
use a local/server phonemizer or forced aligner, but not another generative
model. Unsupported or low-confidence phonemes resolve to a neutral mouth shape
through an explicit mapping, not an unlabeled fallback.

Flutter schedules:

- audio from the native rendered-sample clock;
- Ava captions from speech text offsets;
- visemes from normalized phoneme/sample ranges;
- expression/gaze/canvas cues from phrase anchors mapped to the same ranges.

The current restrained avatar baseline remains: living idle, blink, eye
contact, subtle expression, and lip sync. Touch remains independent.

## 7. Barge-in and delivered speech

External TTS makes interruption accounting essential.

When Realtime reports that the child started speaking:

1. the backend emits `interruptRequested`;
2. Flutter applies a very short click-free fade and stops queued PCM;
3. Flutter reports the final rendered sample index;
4. the backend cancels the Realtime response and ElevenLabs stream;
5. alignment maps the sample index to the last delivered text offset;
6. pending canvas actions and unsaid segments are discarded;
7. only the delivered Ava prefix is written to the conversation ledger.

Provider conversation state is reconciled so a later response does not assume
the unheard suffix was spoken. The backend deletes/replaces the interrupted
provider item when the Realtime API permits it; otherwise the next model
context explicitly states the delivered prefix and interruption. The backend
ledger remains authoritative in either case.

## 8. Dynamic SVG canvas

### 8.1 Exact representation and validation

Sol authors one complete static SVG document. The application model is a
bounded exact document plus derived sidecars:

```text
SvgCanvasCandidate
├── generationId
├── base active scene/revision
├── candidate scene/revision
├── exact UTF-8 SVG bytes + SHA-256
├── deterministic manifest bytes + SHA-256
├── validator and renderer-capability versions
├── viewBox and presentation metadata
└── interaction/accessibility manifest
```

The backend extracts exactly one completed assistant `output_text` payload.
Reasoning output items may coexist in the Responses object, but a refusal,
additional assistant text payload, Markdown wrapper, incomplete response, or
non-SVG document rejects the generation. The Unicode payload is UTF-8 encoded
once. No whitespace stripping, Unicode/newline normalization, XML reserialization,
attribute ordering, quote rewriting, or pretty printing is permitted.

A hardened parser builds a separate inspection representation. It rejects
DTDs/entities, provider scripts and handlers, `foreignObject`, external URLs,
navigation, network content, unsupported CSS, invalid namespaces and IDs,
non-finite data, unbounded XML/path/text/filter complexity, and cycles or
excessive expansion through `use`, masks, clips, patterns, filters, and local
references. The canonical capability manifest bounds both syntax and effective
render-graph complexity.

Runtime validation is safety, renderer compatibility, reference integrity,
interaction integrity, accessibility, and resource bounds. It never changes
the artwork or applies subjective composition rules. One bounded repair may
send exact diagnostics back to Sol and require a complete replacement.

### 8.2 Rendering and hit testing

Flutter renders the canonical SVG directly. Renderer selection is gated by the
frozen Sol corpus, pixel fidelity, required gradients/filters/clips/text,
element-level hit testing, accessibility, Reduce Motion, frame/memory budgets,
and iPhone/iPad/macOS consistency. Failure never switches automatically to the
typed primitive renderer.

Sol declares stable SVG element IDs and bounded `data-ava-*` binding metadata.
The backend derives a deterministic hash-bound manifest without reconstructing
the visual. The renderer returns the nearest validated binding element in paint
order. If a direct Flutter renderer cannot provide element hit testing, the
manifest must carry exact validated SVG-viewBox hit geometry. Flutter never
guesses from labels or rebuilds the full SVG to recover geometry.

Local presentation state may reveal, toggle, focus, drag, reset, select, or
animate through fixed application-owned behavior. Provider code never runs.
Compact hit areas expand to at least 44 physical points without changing the
visible artwork. Repeated interactions restart their small local response and
remain provider-free.

### 8.3 Independent delivery and end-to-end activation

SVG delivery uses an independently authenticated document lane so it cannot
head-of-line block PCM. A control event announces candidate identity, exact
length, hashes, capability version, and scoped document identity. Flutter
fetches with an authorization header, verifies exact bytes, preloads the
renderer, and returns `canvasDocumentPrepared` with the same generation and
hash tuple.

Prepared does not mean active. The coordinator chooses a validated speech
phrase/segment anchor and maps it through ElevenLabs alignment and the
delivered-text ledger to a rendered sample. Flutter presents the candidate at
that cue, reports `canvasPresented` after a successful rendered frame, and
waits for `canvasActivated` before sending semantic interactions for the new
revision. Only the presented acknowledgement promotes the backend scene.

At most one candidate generation may target a session at a time. Candidate
prepare/activation uses compare-and-swap against its base active revision.
Supersession, stale base, fetch or renderer failure, interruption, disconnect,
timeout, or cancellation before presentation discards the candidate and leaves
the previous active scene unchanged. If the cue sample already passed before
preload completed, presentation is requested immediately.

### 8.4 Session ownership and interactions

Canvas state belongs to one persistent realtime conversation session, not to a
child profile or the turn that requested it. Flutter retains the active SVG
across turns and clears it for a new session. Closing the panel does not delete
the active scene.

Local reveal, toggle, reset, state, focus, and drag actions do not emit provider
events. Semantic `explore`, `select`, and `submitOutcome` controls carry only
session ID, scene ID, active scene revision, interaction revision, binding ID,
and bound content identity. The backend resolves the label, description, and
declared value from its canonical manifest. Client-authored text, coordinates,
and values never enter model context.

Scene and interaction revisions increase independently. A stale scene,
mismatched SVG/manifest hash, unknown binding, or replay emits a non-terminal
`interactionRejected` session event. It cannot fail a speaking turn, close the
event socket, stop audio, or change microphone intent.

Realtime receives only a bounded semantic view of the active presented scene
and last validated semantic interaction. Title, description, and manifest text
remain untrusted model-generated data: control characters are removed, fields
are bounded and data-encoded, and the prompt explicitly forbids following any
instructions found inside them. Realtime never receives SVG geometry.

## 9. Memory and persistence

The existing persistence adapter can be evolved rather than tied permanently
to Supabase. The logical store needs:

- authenticated installation/parent subject;
- Ava session metadata;
- child turn summary or transcript when available;
- generated Ava segments;
- delivered text range and terminal status;
- validated stable-memory proposals with provenance;
- current-session summary;
- weekly topic/event summaries;
- reset tombstone/audit metadata.

Raw audio is not persisted by default. Raw transcripts are retained only for a
defined short window and are excluded from normal logs. Long-term memory uses
compact typed summaries.

The provider receives:

- derived age band;
- short delivered-turn context;
- approved stable interests/preferences;
- current-session summary;
- current canvas state only when needed.

It does not receive the child's name, exact date of birth, database IDs,
location, or parent account details.

Conversation reset deletes conversation turns, summaries, and derived memory
for that profile. It preserves the profile and app settings. Reset must cover
both device cache and backend persistence and return a completion revision;
local success without cloud success is an error.

## 10. Authentication and child privacy

The current loopback authenticator is development-only. Production needs:

- a parent/installation identity flow;
- short-lived backend access tokens;
- refresh/revocation;
- per-subject and per-device rate limits;
- App Attest/DeviceCheck or equivalent abuse protection on Apple platforms;
- deletion/export procedures;
- a documented retention schedule and parental consent flow;
- regional/privacy review before collecting child data.

The existing server-side Supabase adapter is acceptable for the first
deployment if authentication and row ownership are completed. The service-role
key remains backend-only. A public client key, if used by Supabase Auth, is not
a model-provider secret.

## 11. Safety

Safety is layered:

- prompt and relationship policy;
- bounded function schema;
- input/output safety classification available through the same OpenAI
  account when required;
- backend rules for prohibited memory and canvas content;
- provider safety/refusal events;
- child-safe error and redirect language;
- offline red-team and regression suites;
- parent-accessible reset and data controls.

The app must not imply that Ava is human, encourage secrets or exclusivity, or
replace trusted adults. Immediate-danger responses should be calm and direct.
Exact hotline content is locale-sensitive and must come from an approved
locale-aware source when implemented.

## 12. Failure behavior

Failures are typed and visible in telemetry and UI state:

- authentication/authorization failure;
- WebRTC negotiation or sideband failure;
- provider rate limit/timeout/unavailable;
- malformed or unsafe tool arguments;
- ElevenLabs connection/audio/alignment failure;
- protocol sequence/revision failure;
- canvas validation/render failure;
- persistence failure;
- playback/route failure;
- cancellation timeout.

There is no switch to platform TTS, canned model response, fixed diagram, or
different voice. Optional components can fail independently:

- invalid canvas: valid speech may continue, `canvasRejected` is emitted, and
  the panel stays closed;
- memory write failure: emit non-terminal `memoryPersistenceFailed`, then the
  normal spoken terminal event; the app exposes a persistent retry-required
  memory state without failing speech or media;
- weekly recap failure: existing recap remains marked stale.

## 13. Latency and backpressure

Measure at least:

- microphone-to-VAD-end;
- VAD-end-to-first assistant-text delta;
- first delta-to-first valid phrase;
- valid segment-to-first TTS byte;
- first TTS byte-to-first rendered sample;
- speech/canvas cue drift;
- interruption-to-silence;
- total turn time.

Targets are set after device measurements, not guessed in code. The first
auditable goal is a median first-audio latency near natural conversational
turn-taking and a consistently short interruption-to-silence time.

All queues are bounded:

- Realtime text/tool-event buffer;
- TTS text queue;
- decoded PCM ring buffer;
- outbound event queue;
- canvas generation queue;
- canvas preload queue;
- persistence retry queue.

Backpressure pauses upstream text flushing or cancels the turn. It never grows
memory without bound.

## 14. Cost controls

- Default to the mini Realtime model.
- Keep the stable prompt/tool prefix identical to maximize provider caching.
- Keep responses naturally short.
- Do not send avatar frames, touch events, or render state to a model.
- Call the canvas specialist only after Realtime semantically selects
  `request_interactive_canvas`.
- Cap audio duration, silence, response characters, canvas complexity, session
  length, and daily account spend.
- Evaluate Sol reasoning effort against the frozen representative visual corpus;
  do not lower it without measured quality parity.
- Cancel provider work immediately on disconnect or interruption.
- Record per-turn provider usage and cost estimates by model/voice.
- Gate live integration/evaluation tests behind an explicit spend flag.

Current provider prices and model availability must be read from official
documentation at deployment time; do not bake historical prices into product
logic.

## 15. Observability

Structured telemetry uses session/turn/request IDs and phase timings. It may
include:

- model/voice configuration identifiers;
- token/audio usage;
- segment and canvas counts;
- canvas generation state, exact byte count, hashes, validator/capability
  versions, fetch/preload/presentation latency, and rejection codes;
- validation error codes;
- queue depth/high-water marks;
- playback underruns and cue drift;
- cancellation and reconnect results;
- provider request IDs.

It must not include API keys, raw audio, exact birth dates, full child names,
raw SVG, semantic-brief contents, or unrestricted transcript text. Development
content logging is opt-in, redacted, and disabled in release builds.

The development backend also writes rotating JSON Lines diagnostics to
`backend/.logs/ava-backend.jsonl` (or `AVA_DEVELOPMENT_LOG_PATH` when
configured). Every turn failure carries a safe `failureId`, `component`,
session ID, and turn ID so an on-screen failure can be correlated without
showing child data. Canvas rejection and memory persistence warnings are turn
events and must not close or mute the persistent conversation; only a genuine
WebRTC, event-WebSocket, or media failure may tear it down.

`/health/ready` reports the running build version and instance ID, a source
revision/currentness check, protocol version, Realtime prompt version,
Realtime tool-contract version, and canvas-specialist prompt version. A source
tree changed after process startup reports `503 stale`, making an old backend
process explicit instead of silently serving an obsolete prompt or schema.

## 16. Deployment configuration

Two provider secrets are required only for live provider integration:

- `AVA_OPENAI_API_KEY`;
- `AVA_ELEVENLABS_API_KEY`.

Production also needs non-provider configuration:

- exact ElevenLabs voice ID and model ID;
- documented voice commercial-use approval;
- database URL and server credential;
- auth signing/verification configuration;
- safety hash secret;
- allowed origins/hosts;
- rate and spend limits;
- telemetry destination and redaction policy.

No key is needed to build the protocol, fake session coordinator, canvas,
renderer, state machine, cancellation tests, or local playback fixtures.

## 17. Authoritative external references

- [OpenAI Realtime guide](https://developers.openai.com/api/docs/guides/realtime)
- [OpenAI Realtime function calling](https://developers.openai.com/api/docs/guides/realtime-conversations#function-calling)
- [OpenAI Realtime server controls and sideband](https://developers.openai.com/api/docs/guides/realtime-server-controls)
- [OpenAI gpt-realtime-2.1-mini](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini)
- [OpenAI gpt-5.6-sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [OpenAI gpt-transcribe](https://developers.openai.com/api/docs/models/gpt-transcribe)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- [ElevenLabs realtime TTS WebSocket guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/realtime-tts)
- [ElevenLabs input-streaming TTS API](https://elevenlabs.io/docs/api-reference/text-to-speech/v-1-text-to-speech-voice-id-stream-input)
