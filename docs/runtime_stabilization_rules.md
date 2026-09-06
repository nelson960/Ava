# Ava Runtime Stabilization Rules

Status: Authoritative engineering and validation discipline
Audience: Architecture reviews, forensic analysis, coding agents, and verification workflows.

This document defines HOW Ava must be analyzed, changed, reviewed, and
validated. It is not an architecture description, a changelog, or a bug diary.
It establishes the durable engineering discipline required to keep Ava's
realtime conversation, presentation clock, canvas workspace, and persistence
stable, robust, and verifiable.

---

## 1. Architecture Before Implementation

For all non-trivial runtime, protocol, coordinator, or state changes:

1. **Reconstruct the Active Production Path:** Read the running code and trace
   the active execution path across Flutter, backend, and providers. Do not rely
   on comments, outdated docs, or assumptions.
2. **Identify State Owners and Lifetimes:** Enumerate every piece of mutable
   state, who creates it, who consumes it, what cancels it, and when it dies.
3. **Gather Concrete Evidence:** Collect exact trace logs, network events, or
   failing event sequences demonstrating the underlying problem.
4. **Compare Viable Architectural Designs:** Formulate alternative approaches,
   evaluating them against ownership boundaries and lifecycle invariants.
5. **Architectural Decision Precedes Code:** A design decision must be agreed
   upon before implementation begins.
6. **Implement the Agreed Decision:** The implementer executes the agreed
   architecture cleanly across all affected layers.
7. **Independent Adversarial Diff Review:** An independent reviewer (or fresh
   adversarial prompt) inspects the actual code diff, verifying that boundaries
   and invariants were preserved without side effects.
8. **Deterministic and Live Verification:** Automated scenarios prove the fix,
   and live/physical validation proves hardware and transport boundaries.

### Role of Coding Agents
Coding agents are **implementers**, not architecture authorities:
- Agents may perform forensic research, identify structural defects, and propose
  principled options.
- Agents must **never** silently make major architectural decisions, invent
  shadow state, create ad-hoc protocols, or redefine ownership boundaries while
  coding.

---

## 2. Change Scope Discipline

### Principled Scope Over Artificial Limits
- **No Simplistic File Caps:** Never apply artificial restrictions such as "only
  touch these three files" when a correct architectural invariant legitimately
  spans multiple layers (e.g., client protocol, backend coordinator, and tests).
- **Comprehensive Boundary Implementation:** Change every production file and test
  file genuinely required to implement the agreed architecture correctly.
- **Direct Traceability:** Every modified file must have a direct, demonstrable
  relationship to the intended architectural invariant.
- **No Unrelated Churn:** Do not perform drive-by refactoring, cosmetic code
  cleanup, or speculative redesign in the same change.
- **Never Preserve Broken Boundaries:** Never leave a flawed ownership boundary
  or lifecycle hack in place simply to keep a diff artificially small.

---

## 3. State Ownership and Identity Fencing

### Single Authoritative Owner
Every piece of mutable asynchronous state must have exactly one authoritative
creator and owner.

### Lifetime Taxonomy
All state must be explicitly classified into its correct lifetime:
- `SESSION_LIFETIME`: Spans an authenticated user conversation; bounded by expiry
  or explicit reset.
- `WEBRTC_LIFETIME`: Spans the media peer connection and audio track. Muting
  disables the track but preserves the connection.
- `TURN_LIFETIME`: Spans a single child utterance through assistant speech
  completion, interruption, or terminal failure.
- `INPUT_ITEM_LIFETIME`: Spans one committed provider conversation input item
  associated with one child utterance/transcription lifecycle (not a PCM or
  audio chunk lifetime). Preserves provider item ID -> exact owning Ava turn ->
  exact transcription lifecycle.
- `SPEECH_STREAM_LIFETIME`: Spans assistant phrase generation and ElevenLabs TTS
  streaming for one turn.
- `PLAYBACK_CLOCK_LIFETIME`: Monotonically counted PCM sample offsets tied to one
  specific playback stream identity.
- `CANVAS_GENERATION_LIFETIME`: Spans an asynchronous specialist execution;
  tracked by `generationId`.
- `CANVAS_CANDIDATE_LIFETIME`: Spans unpresented draft documents undergoing
  validation, delivery, and preload.
- `CANVAS_SCENE_LIFETIME`: Persistent session state retaining canonical SVG
  documents; incremented via monotonic scene revisions.
- `WORKSPACE_FOCUS_LIFETIME`: Durable client-side selection of the active canvas
  document.
- `INTERACTION_LIFETIME`: Monotonic revision incremented per semantic canvas
  interaction.
- `MEMORY_EPOCH_LIFETIME`: Bounded historical persistence incremented per
  successful authoritative conversation reset.

### Identity Fencing Rules
- **No Shadow Counters:** A consumer must never invent parallel sequence counters,
  shadow IDs, or heuristics to track another subsystem's progress.
- **Fenced Retention:** A longer-lived object may retain shorter-lived state only
  if the shorter-lived identity token is preserved and strictly validated on
  every access.
- **No "Current Turn" Heuristics:** Never infer ownership from "whatever turn is
  currently active" when an explicit provider item ID, stream ID, or generation ID
  exists.
- **Stale Isolation:** Stale callbacks, late task completions, and out-of-order
  network frames from an older identity must be dropped immediately. They must
  never mutate current or subsequent state.

---

## 4. Generalization and Anti-Hardcoding Discipline

### Fix Invariants, Not Incidents
A production bug fix must implement a general invariant, not merely make the
incident that exposed it disappear.

### Prohibited Practices
The following are strictly prohibited:
- **Topic-Specific Branches:** Logic checking for specific words or subjects
  (e.g., `if (subject == "heart")`).
- **Hardcoded Identifiers:** Baking fixture IDs, turn IDs, or generation IDs into
  production code.
- **Keyword-Based Semantic Routers:** Regex or string scanning to infer child intent.
- **Hidden Test-Only Branches:** Production behavior altering its code path via
  `isTest` flags or silent environment bypasses.
- **Unexplained Retry Loops:** Adding loops or sleep delays to mask timing races.
- **Broad Exception Swallowing:** `except Exception: pass` or catching root
  errors without actionable logging.
- **Fake Fallbacks:** Downgrading validation failures to silent defaults or
  unlabeled neutral states.

### Classification of Constants and Thresholds
Every new constant, timeout, queue limit, or retry threshold must be classified
into one of four defensible categories:
1. **PROTOCOL:** Mandated by external protocol specifications (e.g., PCM 24 kHz
   16-bit mono, WebSockets frames).
2. **PRODUCT:** Dictated by an explicit product decision (e.g., maximum 10
   retained canvas scenes, 2-minute warm pause window).
3. **SAFETY/RESOURCE:** Established to protect hardware, token budgets, or
   memory bounds (e.g., 384 KB SVG size limit, 1000-character canvas brief).
4. **ALGORITHMIC:** Derived from a deterministic mathematical model (e.g.,
   corpus-aware continuous ranking weights in candidate retrieval).

If a constant has no defensible category, it is an arbitrary magic number and must
be rejected.

### Continuous Evidence Over Binary Gates
For relevance, ranking, and scoring:
- Prefer continuous evidence ranking (e.g., corpus-aware continuous lexical scoring
  over eligible corpora) rather than arbitrary binary admission gates.
- Do not introduce arbitrary cutoff thresholds unless explicitly mandated by
  protocol or resource limits.

---

## 5. Semantic Ownership and Subsystem Boundaries

### Clear Separation of Concerns
- **OpenAI Realtime:** Owns conversational meaning and intent.
- **Ava Backend:** Owns deterministic lifecycle, session identity, validation,
  security, cancellation, and persistence.
- **Canvas Specialist:** Owns visual composition and SVG markup authorship.
- **Renderer / Client:** Owns local presentation, frame-rate manipulation, and
  hit testing.
- **Memory Store:** Supplies historical evidence; it does not reason about
  conversational meaning.

### Non-Interference Invariant
No subsystem may perform another subsystem's responsibility to satisfy a test or
shortcut implementation.

> Subsystem tests must test the responsibility of that subsystem. A lexical
> retrieval layer must not be forced by tests to perform semantic classification
> owned by the reasoning layer.

---

## 6. Test Discipline and Verification Truthfulness

### Required Test Matrix
Stabilization of any runtime boundary requires deterministic tests covering:
1. **Normal Positive Case:** Successful end-to-end execution.
2. **Stale Identity:** Late-arriving events carrying older turn, stream, or
   generation IDs.
3. **Supersession:** Newer child intent or generation arriving before previous
   work completes.
4. **Interruption and Cancellation:** Abrupt cancellation at every lifecycle
   phase.
5. **Rapid Repeated Actions:** Rapid taps, toggles, or interruptions in quick
   succession.
6. **Network Reconnect / Reset:** Handling reconnect and session reset without
   inheriting transient state.
7. **Adversarial Edge Cases:** Tests deliberately crafted to defeat shallow,
   incident-specific fixes.

### Truth in Reporting
Validation reports must strictly distinguish between:
- **PASSED:** The test suite executed and passed completely.
- **FAILED:** The test suite executed and encountered failures.
- **SKIPPED:** The test was deliberately bypassed by configuration.
- **NOT RUN:** The test was not executed in the reported environment.

### Unit-Green is Not Production Proof
Passing unit tests does not prove:
- WebRTC media negotiation and audio quality;
- Realtime WebSocket sideband communication;
- Native audio hardware playback and click-free interruption;
- Direct SVG rendering performance in native viewports;
- Live provider rate-limiting and error handling;
- Cross-process database persistence and transactions.

When a change touches a physical or network boundary, physical/live validation
must be performed before declaring the boundary stabilized.

---

## 7. Concurrency, Queues, and Cancellation

### Asynchronous Task Ownership and Lifetime Transfer
Every asynchronous task (`asyncio.create_task`, `Future`, timer, stream
subscription) must have an explicit lifetime owner and cancellation fence:
- **Single Authoritative Lifetime Owner:** Every asynchronous task has exactly
  one authoritative lifetime owner at any point in time. There are no orphan or
  unowned background tasks. Task creation scope does not dictate permanent
  lifetime ownership.
- **Termination and Cleanup:** Work still owned by a terminating owner must be
  cancelled and joined or awaited.
- **Explicit Lifetime Transfer:** Work intended to outlive its initiating scope
  (e.g., an accepted canvas generation initiated during a speech turn transferring
  to canvas-workspace or job-coordinator ownership) must first be explicitly and
  atomically transferred to and registered with the longer-lived owner.
- **Post-Transfer Non-Interference:** After ownership transfer, the old owner
  must not cancel, terminate, or mutate the transferred task.
- **Identity Validation on Commit:** Completed tasks must verify that their
  target identity is still active and current before committing any state change.
- **Immutable Identity Binding:** Callbacks and async continuations must never
  capture mutable current-turn state by reference; they must capture explicit
  immutable identity tokens.

### Queue Boundaries
- **Turn-Scoped Inboxes:** Queues carrying turn-scoped controls or audio frames
  must be owned by the turn and discarded upon turn completion or cancellation.
- **No Cross-Turn Leakage:** Messages from Turn $N$ must never linger in a shared
  queue to be consumed by Turn $N+1$.
- **Bounded Capacity:** Every queue must have an explicit capacity bound.
  Backpressure must pause producers or reject work cleanly; memory must never
  grow without limit.

---

## 8. Error Taxonomy and Failure Isolation

### Failure Independence Invariant
The failure of an optional subsystem must never retroactively invalidate or abort
unrelated successful operations:
- **Canvas Generation Failure $\neq$ Conversation Failure:** Visual generation
  rejection or timeout must leave active speech and media intact.
- **Memory Persistence Failure $\neq$ Speech Delivery Failure:** Inability to
  write to Postgres must not prevent the child from hearing Ava's response.
- **Focus Sync Latency $\neq$ Client UI Freeze:** Delays in updating Realtime's
  canvas context must never freeze local workspace navigation.
- **Renderer Failure $\neq$ Session Teardown:** Client SVG rasterization failure
  must not drop the microphone track or close WebSockets.

### Conceptual Failure Categories
All runtime exceptions must be mapped to distinct, typed error categories:
- **Authentication & Session Authorization:** Token expiration, invalid signatures,
  or unauthorized session access.
- **Transport & Media Connectivity:** WebRTC ICE failure, media track loss,
  or event WebSocket disconnection.
- **Conversational Brain Protocol & Parser:** Malformed Realtime frames, schema
  contract violations, or unrecoverable sideband protocol errors.
- **Provider Availability & Capacity:** Upstream rate limits, HTTP 429/503 status,
  or connection timeouts.
- **Speech Synthesis & Streaming:** ElevenLabs WebSocket connection drops,
  synthesis stalls, or alignment parse errors.
- **Canvas Specialist & Validation:** Specialist generation timeout, output token
  exhaustion, SVG schema/security rejection, or quality repair failure.
- **Canvas Client Preparation & Presentation:** Scoped SVG document fetch failure,
  renderer rasterization failure, or presentation cue sample expiration.
- **Canvas State Machine & Synchronization:** Stale candidate transitions, content
  hash mismatches, or out-of-order interaction revisions.
- **Authoritative Persistence:** Database connectivity failure, pool timeouts,
  constraint violations, or reset revision epoch mismatches.

Do not catch generic exceptions and translate them into misleading categories
(e.g., disguising a local JSON parse error as upstream provider unavailability).

---

## 9. System Invariants Register

The following invariants are mandatory across the codebase:

### Input and Transcription
- **INV-001:** A provider input transcription belongs to exactly one provider
  input item and exactly one Ava turn.
- **INV-002:** Provider item identity must not be inferred from the active turn
  when an explicit provider item ID exists.
- **INV-003:** Only an authoritative completed transcription may become
  authoritative child transcript text. Partial transcription deltas must never
  become stable memory evidence.

### Turn Sequencing and Stale State
- **INV-004:** A stale event from Turn $N$ must never mutate Turn $N+1$.
- **INV-014:** A duplicate, stale, or invalid `turnCreated` event must be
  validated and rejected before runtime or presentation state is reset.
- **INV-015:** Turn-scoped queues, cancellation tokens, progress state, and
  transient controls must not leak into a subsequent turn.
- **INV-017:** Expected stale asynchronous input should be dropped cleanly
  without mutating state or killing the session.

### Playback Clock and Presentation
- **INV-005:** Rendered sample positions are comparable only when they belong to
  the same playback clock identity.
- **INV-006:** Rendered samples must be monotonically non-decreasing within one
  valid playback clock.
- **INV-007:** A canvas presentation cue may advance only from the playback clock
  belonging to that cue's source turn/playback identity.
- **INV-013:** Captions, visemes, expressions, and canvas presentation must derive
  timing from one authoritative rendered-audio timeline.

### Canvas Lifecycle and Workspace
- **INV-008:** An active canvas scene is persistent session state and survives turn
  boundaries.
- **INV-009:** Cancelled canvas work cannot later commit, activate, or present.
- **INV-010:** Canvas failure must not fail speech, alter microphone intent, or
  invalidate a successfully delivered conversational turn.
- **INV-021:** Canvas generation lifetime is independent of speech presentation.
  Normal speech barge-in must not cancel accepted canvas work.
- **INV-022:** Flutter is the durable recovery authority and preserves immediate
  local canvas navigation, but the backend owns the canonical ordered workspace
  after authenticated pre-negotiation manifest adoption. Switching retained
  scenes must never block on active speech, TTS synthesis, or model readiness.
- **INV-027:** Backend `workspaceEpoch` changes only for destructive/reset lineage
  changes. `workspaceRevision` increments exactly once per accepted atomic
  create/revise/delete mutation (including capacity eviction) and never for
  focus, presentation, interaction, conversation, or view changes.
- **INV-028:** Provider Canvas-control continuation may begin only after the
  exact physical/backend postcondition is known and the resulting latest desired
  dynamic-context build has been acknowledged by Realtime. Boundary rejection
  requires no impossible state-change ACK.
- **INV-023:** Continuous canvas manipulation is 100% local at display frame
  rate. Network communication occurs only on gesture release, resolving to the
  nearest validated semantic checkpoint and transmitting the discrete binding ID,
  never raw continuous coordinates.
- **INV-026:** Panel dismiss is local client presentation state and must not
  cancel an accepted canvas generation or discard an active scene (`PANEL DISMISS
  != CANVAS SEMANTIC CANCEL`).

### Memory, Intent, and Subsystems
- **INV-011:** Memory persistence failure must not prevent turn completion,
  speech delivery, microphone recovery, or session continuation.
- **INV-012:** User microphone intent is independent of turn phase. Turn state
  may control media track enablement, but must not overwrite the user's intent.
- **INV-016:** Local protocol/parser errors must be distinguished from provider
  availability and network failures.
- **INV-018:** Unknown provider events must not be added to generic ignore lists
  without explicitly defining their lifecycle semantics.
- **INV-019:** No optional subsystem participates in the success condition of
  unrelated critical functionality.
- **INV-020:** Every state transition must have exactly one authoritative owner.
- **INV-024:** For a new visual request, current child intent is authoritative;
  historical memory and past scenes are supporting context only.
- **INV-025:** Subsystem tests must test the responsibility of that subsystem.
  A lexical retrieval layer must not be forced by tests to perform semantic
  classification owned by the reasoning layer.

---

## 10. Agent Engineering Workflow

When working on complex architectural or runtime tasks:

```text
FORENSIC / RESEARCH ANALYSIS
  -> ADVERSARIAL DESIGN REVIEW
  -> HUMAN / OWNER ARCHITECTURAL DECISION
  -> TARGETED IMPLEMENTATION
  -> FRESH ADVERSARIAL DIFF REVIEW
  -> DETERMINISTIC TESTS
  -> LIVE HARDWARE VALIDATION
  -> COMMIT
```

1. **Context Baseline:** Use `docs/production_architecture.md` and
   `docs/runtime_stabilization_rules.md` as the durable context. Do not carry
   stale bug logs or old implementation diaries into agent prompts.
2. **Review the Real Diff:** The author's summary never substitutes for code
   review. Always inspect the actual git diff and all surrounding ownership
   paths before approving changes.
3. **Preserve Documentation Integrity:** Update these authoritative documents
   only when a true architectural invariant or engineering process changes.
   Do not modify them for routine bug fixes.
