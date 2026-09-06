# Ava Project Contract

This is the compact source of truth for the repository. Follow it unless the
user explicitly changes a product decision.

Read these only when backend, voice, canvas, memory, or release work is in
scope:

- `docs/production_architecture.md`
- `docs/runtime_stabilization_rules.md`

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

A `gpt-5.6-sol` Responses call authors the complete desired SVG only when
Realtime calls `request_interactive_canvas`. It receives that tool's compact
semantic brief, age band, renderer capability version, and the complete current
accepted SVG when revising a scene. It uses the same OpenAI key, never sits on a
no-canvas turn, and has a hard timeout, output limit, validation boundary, and
spend budget. Sol owns visual composition. Ava validates and either preserves
the exact UTF-8 SVG bytes or rejects the candidate; Ava never reconstructs,
clamps, truncates, restyles, or silently repairs accepted artwork.

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
6. When a visual is requested, the backend validates and reserves a bounded
   generation, returns exactly one immediate `accepted` or `rejected`
   `function_call_output`, and runs Sol independently of Realtime continuation.
7. A non-mutating validator inspects the complete SVG and prepares a candidate
   scene. Flutter fetches it over a separately authenticated canvas-document
   lane, preloads it, presents it on the rendered-sample cue, and acknowledges
   actual presentation before the backend makes the revision canonical.
8. An authenticated Ava event WebSocket sends JSON control frames and binary
   PCM to Flutter. SVG bytes never share the ordered PCM lane.

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

Every turn-scoped server event has protocol version, session ID, turn ID,
monotonic sequence, and revision. Session-scoped canvas controls carry session,
generation, scene, revision, and content-hash identity instead of a speaking
turn ID. Unknown values, gaps, stale revisions, mismatched hashes, and invalid
transitions fail visibly. Explicitly non-terminal canvas and interaction
warnings may follow a terminal turn while the persistent conversation remains
connected.

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

The lower panel is a Sol-authored, locally rendered SVG scene—not a fixed
diagram catalog and not a backend-authored primitive reconstruction. Realtime
only requests the visual and describes its conversational purpose. It never
authors markup, coordinates, paths, styles, interactions, or timing.

Sol may use the complete versioned static-SVG capability manifest, including
bounded paths, text, groups, transforms, gradients, clipping, masks, filters,
and document-local references. Visual freedom never includes provider scripts,
event handlers, `foreignObject`, DTDs/entities, remote resources, navigation,
network access, unsupported CSS, reference cycles, or unbounded expansion.
The validator parses a separate inspection representation but never serializes
it back. Accepted provider text is UTF-8 encoded exactly once and is the
canonical SVG byte sequence and hash.

Sol declares stable element and binding IDs plus bounded `data-ava-*` semantic
metadata. The backend derives a hash-bound interaction/accessibility manifest
without changing the visual. The selected renderer must support element-level
hit testing or consume exact validated hit geometry; Flutter must never guess
from labels or rebuild the SVG into the old primitive canvas. Visible geometry
is preserved while compact interactive targets expand to at least 44 physical
points.

Reveal, toggle, drag, state changes, and reset remain local and provider-free.
Declared `explore`, `select`, or `submitOutcome` bindings may cross the control
socket only as session/scene/revision/interaction/binding IDs. The backend
resolves their meaning from its canonical hash-bound manifest and never trusts
client-authored labels or values. Stale, replayed, unknown, or mismatched
controls emit non-terminal `interactionRejected` events and never close speech,
microphone, or transport.

Canvas state progresses through candidate, prepared, presented, and active.
Backend validation alone never makes a revision canonical. Flutter fetches the
document over an independent authenticated lane, verifies its exact hash,
preloads it, presents it at a coordinator-owned rendered-sample cue, and reports
`canvasPresented`. Only then may the backend activate the revision and expose
its semantics to Realtime. Barge-in, disconnect, renderer failure, supersession,
or timeout before presentation discards the candidate and preserves the prior
active scene.

A later visual request receives the complete active SVG and returns the
complete desired replacement. Runtime validation is deterministic safety,
complexity, renderer-compatibility, reference-integrity, interaction, and
accessibility validation—not subjective restyling. At most one bounded Sol
repair may return a complete replacement. Invalid optional canvas work emits
`canvasRejected`, stays closed or preserves the prior scene, and cannot corrupt
valid speech.

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

- the fixed `DiagramSpec` catalog, dashboard UI/assets, and protocol-v2 fixed
  diagram cues;
- protocol-v2 diagram events fail explicitly and cannot reach the UI.
- the legacy lesson-turn and NDJSON performance-turn backend routes and their
  route-only orchestration.

Remove only after protocol v4 passes:

- platform speech input/output and OpenAI audition TTS;
- activity catalog/picker, learning progress/scores/sheet/assets/database
  fields;
- obsolete response-animation audition controls and unused gesture plumbing.

Never remove avatar touch or rotation during this migration.

## Active order

1. Freeze protocol-v4 control, canvas-document, activation, interaction, and
   state-machine fixtures with fake providers.
2. Build the exact SVG extraction, non-mutating validator, hash-bound manifest,
   independent document lane, and candidate-to-active session state.
3. Select and test a direct SVG renderer with element hit testing without live
   provider keys, while preserving avatar touch and native PCM.
4. Decouple Realtime tool acceptance from Sol completion and prove independent
   speech, audio, canvas, cancellation, reconnect, and backpressure behavior.
5. Fix microphone intent, turn creation, sample-clock captions, transcript
   attribution, delivered-prefix memory, and hermetic configuration tests.
6. Configure Sol, the exact ElevenLabs voice metadata/rights, and the two
   provider keys; run guarded live tests with spend limits.
7. Verify voice, visemes, captions, canvas, interactions, touch, interruption,
   and idle on physical iPhone and iPad.
8. Complete bounded memory, cloud reset, and weekly recap.
9. Remove protocol-v3 typed-canvas and legacy lesson paths, then harden auth,
   privacy, safety, cost, telemetry, and release behavior.

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
