# Ava Project Contract

This is the compact source of truth for the repository. Follow it unless the
user explicitly changes a product decision.

Read these only when backend, voice, canvas, memory, or release work is in
scope:

- `docs/production_architecture.md`
- `docs/production_implementation_plan.md`

Do not revive superseded lesson APIs, fixed diagram catalogs,
activity/progress features, platform TTS, or experimental response-animation
plans. The project Python environment is `/opt/miniconda3/envs/ml`.

## Product

Ava is an avatar-first realtime conversational character for children on
iPhone and iPad; macOS is the development target. Natural, gentle,
low-latency conversation and a consistent personality are the primary value.
Ava can explain and draw when helpful, but ordinary conversation is not forced
into a lesson.

Do not add feeds, tabs, activity catalogs, puzzles, scores, streaks,
curriculum modes, or a teacher/chat mode switch.

## Experience

- Open directly to Ava facing the child.
- No navigation bar or permanent dashboard.
- Keep only the small settings control at top right and microphone at bottom
  left.
- Captions are optional and transparent near the lower edge.
- The dynamic canvas is hidden unless a visual helps. It then uses the lower
  half while Ava stays visible above.
- Preserve the implemented local day/night background and bounded sun, moon,
  cloud, and star touches. Real weather is not active scope.
- Preserve the calm base near `#BFC7C7`.
- Target smooth 60/120 Hz; interpolate locally and never drive frames from
  network events.

Settings contain only child name, date of birth, captions, weekly recap, and
reset conversations. Provider/model/motion/developer controls do not belong
there.

## Locked runtime

Production has two provider accounts and two server-side API keys:

1. OpenAI Realtime is the conversation brain and bounded performance planner.
   It decides only whether a visual helps and emits a compact visual brief.
   Start with backend-configured `gpt-realtime-2.1-mini`.
2. ElevenLabs is Ava's only production TTS provider.

OpenAI receives child audio and emits natural assistant text. OpenAI audio
output is disabled because ElevenLabs owns Ava's voice. Assistant text is
released to TTS only at complete bounded phrase boundaries. Realtime has one
optional application-owned function, `request_interactive_canvas`, configured
with `tool_choice=auto`. Tool selection is semantic and contextual; no keyword
router or forced no-op tool is permitted.

A strict `gpt-5.6-luna` Responses call generates a bounded canvas scene only
when Realtime calls `request_interactive_canvas`. It receives that tool's
compact semantic brief and age band, uses the same OpenAI key, never sits on a
no-canvas turn, and has a hard timeout, output limit, validation boundary, and
spend budget.

Do not add a separate cloud STT provider. Local Apple transcription may be a
non-blocking caption/diagnostic aid but never gates conversation.

Provider keys, prompts, tools, policy, memory, and budgets belong to the Ava
backend. Never put secrets in Dart defines, assets, plists, Gradle files,
logs, crash reports, or the app bundle.

## Connection and turn contract

1. Flutter authenticates and sends a WebRTC offer to the Ava backend.
2. The backend negotiates OpenAI Realtime, retains the provider call ID, and
   attaches a server sideband WebSocket.
3. Child microphone media flows directly between Flutter and OpenAI over
   WebRTC.
4. The backend validates assistant text deltas and the optional semantic canvas
   function call independently.
5. Each complete valid speech phrase streams immediately to ElevenLabs over
   its input-streaming WebSocket.
6. When a visual is requested, the backend calls the strict Responses canvas
   specialist and compiles its scene draft into a validated transaction.
7. The backend returns a matching `function_call_output` for every canvas call;
   success or rejection never terminates ordinary speech.
8. An authenticated Ava event WebSocket sends JSON control frames and binary
   PCM to Flutter.

The WebRTC peer, OpenAI sideband, and Ava event WebSocket persist across
consecutive turns in one conversation. Each turn gets fresh IDs, revisions,
state-machine ownership, and delivered-memory commit. Muting toggles the
existing audio track; only closing the conversation removes and stops it.

Realtime never returns canvas nodes, coordinates, paths, interactions, raw
bones, morph weights, animation clips, milliseconds, SVG/XML, HTML, scripts,
or executable code. The canvas function contains only a bounded semantic brief
and optional interaction goal. Memory extraction is outside the latency-critical
response path and can never gate or invalidate speech.

The backend may forward only a complete bounded phrase. It must not wait for
the entire turn before starting TTS.

The rendered audio sample clock is the master timeline for captions, visemes,
expressions, canvas cues, and return to idle. The model emits phrase/text
anchors; ElevenLabs alignment maps them to sample offsets.

Every server event has protocol version, session ID, turn ID, monotonic
sequence, and revision. Turn-scoped client controls carry the turn ID;
session-scoped controls such as `canvasInteraction` do not. Unknown values,
gaps, stale revisions, and invalid transitions fail visibly. The explicitly
non-terminal `interactionRejected` session warning may follow a terminal turn
while the persistent conversation remains connected.

## Interruption

When the child speaks while Ava is talking:

- Flutter immediately fades/stops queued audio and reports the final rendered
  sample;
- the backend cancels OpenAI, ElevenLabs, and pending canvas actions;
- alignment maps the rendered sample to the text prefix actually heard;
- only that delivered prefix enters authoritative memory;
- stale events can never revive the turn.

Cancellation is idempotent. Reconnect never silently resumes stale speech.

## Personality

Ava has one conversation-first personality, not modes. Keep prompt layers
separate and versioned: identity/voice style, natural conversation,
explanation behavior, child safety, derived age context/memory,
capabilities, and strict output contract.

Default responses are short and speakable. Answer first. Do not automatically
quiz, praise, teach, or ask a follow-up. Never encourage secrecy,
exclusivity, dependency, or replacement of real relationships.

## Dynamic canvas

The lower panel is a specialist-authored, locally rendered vector scene—not a
fixed diagram catalog and not arbitrary SVG. Realtime only requests the visual
and describes its conversational purpose.

Allow bounded declarative groups, text, lines/arrows, circles/ellipses,
rectangles, polygons/polylines, and a validated SVG-compatible path grammar.
Allow atomic create/update/delete/reveal/focus/animate/clear transactions and
bounded local tap/explore/select/toggle/reveal/drag/set/reset interactions.
`explore` is the compound production interaction: one tap selects a visible
stage, optionally hides the previous stage's details, reveals a bounded set of
local nodes, and pulses declared connectors. Flutter owns its timing, dimming,
hit feedback, and reset; the provider supplies node IDs only.

Reveal, toggle, drag, state changes, and reset remain local and provider-free.
Declared `explore`, `select`, or `submitOutcome` bindings may cross the event socket,
as scene/revision/binding IDs. The backend resolves their meaning from its
canonical validated scene; it never trusts client-authored labels or values.
The canonical scene belongs to one persistent realtime session, not a child
profile or speaking turn. `canvasInteraction` has no turn ID. Stale scene,
revision, binding, or replay controls emit non-terminal `interactionRejected`
events and never close speech, microphone, or transport.
When a later turn requests a visual change, the specialist receives the
complete current bounded draft and returns the complete desired next scene.
The backend preserves stable IDs and derives the atomic revision patch.

Flutter must validate finite bounds, node/path/action/text limits, allowed
styles/transforms, referential integrity, accessibility labels, and scene
revisions. No remote assets, scripts, arbitrary expressions, network access,
or raw model markup.

The specialist owns complete composition, semantic entities, topology,
back-to-front paint order, bounded exact colors, linear/radial gradients, soft shadows,
groups/pivots, and optional slow declarative motion. The backend runs
deterministic quality checks and permits at most one bounded repair request.
Flutter preserves authored geometry and expands hit areas to a minimum of 44
physical points using the actual viewport scale. It must not silently move
authored labels or replace specialist styling.

Render through a native Flutter vector canvas with local hit testing/tweening.
SVG serialization is allowed for tests/export only. An invalid optional canvas
emits `canvasRejected`, stays closed, and cannot corrupt valid speech.

## Avatar

Touch and 3D interaction are permanent and local:

- only a press/drag beginning on Ava can react to or rotate her;
- real mesh/anatomy picking works from every view;
- repeated touches work indefinitely and compose with speech;
- Ava returns to front after about 30 seconds without camera interaction and
  whenever conversation starts;
- touch never needs a provider call.

Do not create another avatar. Work from the existing GLB, touch map, rig
bindings, reaction profiles, and `lib/ui/avatar/`.

The approved speaking baseline is restrained: living idle, breathing, blink,
eye contact, subtle expression, and sample-clock lip sync. Do not reintroduce
broad hand/shoulder response clips or learned motion unless the user explicitly
reopens that scope. Touch remains enabled during speech.

## Memory and privacy

The backend owns authoritative memory. Keep only a short delivered-turn
window, compact current-session summary, allowlisted stable
interests/preferences with provenance, and bounded weekly topic/event records.

Never persist speculative output, unplayed interrupted speech, hidden
inferences, raw audio by default, precise location, or unnecessary personal
data. Never send child name or exact date of birth to providers; derive and
send only an age band when needed.

Reset conversations deletes cloud and local conversation history, summaries,
and derived memory while preserving profile, DOB, captions, avatar, and app
configuration. The weekly recap is factual and must not claim diagnosis,
ability, mastery, grading, or emotional certainty.

## Current migration

The existing HTTP lesson/NDJSON runtime is legacy migration code, not the
target. Retain and adapt:

- avatar rendering, picking, touch, camera return, idle, and lip-sync seams;
- native sample-counted PCM playback;
- living day/night background;
- profile, captions, and minimal settings UI;
- strict schemas/errors/request IDs, prompt layers, auth seams, and tests;
- the durable persistence adapter.

Replace:

- Apple Speech with Realtime WebRTC audio;
- full-turn Responses planning with incremental Realtime function planning;
- ElevenLabs HTTP synthesis with input-streaming WebSocket;
- NDJSON/base64 audio with event WebSocket plus binary PCM;
- raw recent turns with delivered ledger and bounded summaries.

Already removed by explicit product decision:

- the fixed `DiagramSpec`-to-SVG Flutter renderer, fixed dashboard UI/assets,
  and `flutter_svg` runtime dependency;
- protocol-v2 fixed diagram cues now fail explicitly and cannot reach the UI.

Remove only after protocol v3 passes:

- `/v1/lesson-turns`, `/v2/performance-turns`, clients, models, fixtures, and
  compatibility code;
- platform speech input/output and OpenAI audition TTS;
- activity catalog/picker, learning progress/scores/sheet/assets/database
  fields;
- obsolete response-animation audition controls and unused gesture plumbing.

Never remove avatar touch or rotation during this migration.

## Active order

1. Freeze protocol-v3 schemas/state machines/canvas limits with fake fixtures.
2. Build and test the local dynamic canvas without provider keys.
3. Build the backend realtime coordinator with fake Realtime and TTS streams.
4. Build Flutter WebRTC/event transport while preserving touch and PCM.
5. Add interruption, delivered-prefix, cancellation, reconnect, and
   backpressure tests.
6. Configure the strict Responses canvas specialist, exact ElevenLabs voice
   metadata/rights, and the two provider keys; run guarded live tests with
   spend limits.
7. Verify voice, visemes, captions, canvas, touch, and idle on physical iPhone
   and iPad.
8. Add bounded memory, cloud reset, and weekly recap.
9. Remove legacy paths and harden auth, privacy, safety, cost, telemetry, and
    release behavior.

Do not start with API keys. They first become necessary at step 6.

## Engineering

- Inspect code first; preserve unrelated changes in the dirty worktree.
- Use `rg` for search and `apply_patch` for source edits.
- Do not hardcode secrets, provider responses, paths, body coordinates, or
  test outcomes.
- No silent fallbacks, swallowed errors, or downgraded validation.
- Keep schemas versioned with shared Python/Dart fixtures.
- Bound all audio, delta, event, canvas, and retry queues.
- Log IDs/timing/usage, never secrets, raw audio, exact DOB, or unrestricted
  sensitive transcripts.
- Add deterministic ordering, fragmentation, cancellation, reconnect,
  malformed-frame, and canvas-limit tests before paid tests.
- Run backend lint/type/tests, `flutter analyze`, `flutter test`, and relevant
  native builds before code handoff.
- Run the full avatar interaction suite whenever shared input/session state
  changes.
