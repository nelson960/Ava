AVA REALTIME SYSTEM STABILIZATION INSTRUCTION

You are working on Ava, a production-oriented realtime conversational application with a Flutter client and Python backend.

This is NOT a request to patch the currently observed exception.

The current problem is systemic:

- individual fixes frequently reveal another failure during manual realtime testing;
- many existing unit tests pass even though realistic multi-turn sessions fail;
- several bugs have already been traced to state lifetime/identity mismatches, event-order assumptions, cancellation races, stale state crossing turn boundaries, and optional subsystems affecting unrelated critical paths.

Your job is to stabilize the runtime as a SYSTEM.

Do not optimize for making the currently failing manual scenario pass.
Optimize for establishing explicit invariants, correct state ownership, deterministic lifecycle behavior, and tests capable of discovering the next bug before manual testing does.

======================================================================
PRIMARY OBJECTIVE
======================================================================

Bring the existing Ava realtime runtime into a state where:

1. multiple consecutive conversation turns work correctly;
2. stale events from an earlier turn cannot mutate a later turn;
3. interruption/barge-in is deterministic;
4. cancellation cannot allow cancelled work to commit later;
5. reconnect/session reset cannot inherit stale transient state;
6. transcript events are attributed to the correct provider input item and Ava turn;
7. rendered audio timing remains correctly scoped and monotonic;
8. captions, visemes, expressions and canvas presentation use the correct playback clock;
9. canvas generation/rendering failure cannot invalidate speech;
10. memory persistence failure cannot invalidate a delivered turn;
11. provider protocol/parser errors are not confused with provider/network availability failures;
12. expected late/stale asynchronous events do not unnecessarily kill the persistent session;
13. strict validation remains strict, but is applied at the correct ownership boundary;
14. all of these behaviors are covered by deterministic automated tests rather than relying on manual discovery.

Do not add new product features during this work.

Do not perform unrelated refactoring.

Do not rewrite subsystems merely because a cleaner abstraction is possible.

Prefer the smallest architectural changes that establish the required invariants.

======================================================================
IMPORTANT ENGINEERING PRINCIPLE
======================================================================

Treat every piece of mutable state and every asynchronous event as having an explicit identity and lifetime.

Possible lifetimes include:

- application-scoped;
- authenticated-session-scoped;
- WebRTC-connection-scoped;
- Realtime-provider-session-scoped;
- Ava-conversation-session-scoped;
- turn-scoped;
- OpenAI conversation-item-scoped;
- response-scoped;
- TTS-request-scoped;
- PCM-playback-stream-scoped;
- cancellation-scoped;
- canvas-generation-scoped;
- canvas-candidate-scoped;
- canvas-scene-scoped;
- scene-revision-scoped;
- interaction-revision-scoped;
- memory-reconciliation-scoped.

A longer-lived object may store shorter-lived state ONLY if the shorter-lived identity is preserved and validated.

For example:

WRONG:

    session.lastRenderedSample = 48000

if rendered samples reset on each turn.

CORRECT:

    clock.turnId = turn_42
    clock.renderedSample = 48000

Likewise:

WRONG:

    transcript belongs to currentTurn

CORRECT:

    OpenAI item_id
        -> registered input item
        -> owning Ava turnId
        -> transcription lifecycle

The same principle must be applied throughout the codebase.

======================================================================
KNOWN INVARIANTS
======================================================================

Treat the following as initial required invariants.

Add additional invariants when discovered.

INV-001
A provider input transcription belongs to exactly one provider input item
and exactly one Ava turn.

INV-002
Provider item identity must not be inferred merely from "the currently
active turn" when an explicit provider item ID exists.

INV-003
Only an authoritative completed transcription may become authoritative
child transcript text.

Partial transcription deltas or segments must never become stable memory
evidence.

INV-004
A stale event from Turn N must never mutate Turn N+1.

INV-005
Rendered sample positions are comparable only when they belong to the
same playback clock identity.

INV-006
Rendered samples are monotonically non-decreasing within one valid
playback clock.

A new turn or playback identity may legitimately begin at zero.

INV-007
A canvas presentation cue may advance only from the playback clock
belonging to that cue's source turn/playback identity.

INV-008
An already-active canvas scene is session state and may survive a new turn.

An unpresented pending canvas candidate remains bound to its source
generation/turn and must not silently rebase onto another turn.

INV-009
Cancelled canvas work cannot later commit, activate or present.

INV-010
Canvas failure must not fail speech, permanently alter microphone intent,
or invalidate a successfully delivered conversational turn.

INV-011
Memory persistence failure must not prevent turn completion, speech
delivery, microphone recovery or session continuation.

INV-012
User microphone intent is not owned by turn phase.

Turn state may temporarily control media behavior, but must not permanently
overwrite the user's microphone preference.

INV-013
Captions, visemes, expressions and canvas presentation must derive timing
from one authoritative rendered-audio timeline.

Do not mix rendered-sample timing with wall-clock timers for the same
presentation lifecycle.

INV-014
A duplicate, stale or invalid turnCreated event must be validated before
runtime or presentation state is reset.

INV-015
Turn-scoped queues, cancellation tokens, progress state and transient
controls must not leak into a subsequent turn.

INV-016
Provider protocol/parser errors are different from provider availability,
network and authentication failures.

Do not classify a local parser bug as MODEL_UNAVAILABLE or retryable provider
failure.

INV-017
Expected stale asynchronous input should be rejected without mutating state.

Do not automatically make every stale event fatal to the persistent
conversation unless it represents actual protocol corruption.

INV-018
Unknown or unsupported provider events must not simply be added to a generic
ignore list to silence exceptions.

Determine their lifecycle semantics first.

INV-019
No optional subsystem participates in the success condition of unrelated
critical functionality.

Canvas, memory, analytics or UI presentation failure must not retroactively
invalidate successfully delivered speech.

INV-020
Every state transition must have one authoritative owner.

Avoid multiple components independently inferring the same state from
different signals.

======================================================================
PHASE 1 — READ-ONLY SYSTEM AUDIT
======================================================================

DO NOT MODIFY CODE YET.

First reconstruct the existing runtime.

Trace both backend and Flutter.

Produce a state ownership table with at least:

State/value
Current owning class/module
Producer(s)
Consumer(s)
Actual required lifetime
Current stored lifetime
Identity carried with the value
Whether identity is lost
Reset boundary
Cancellation behavior
Reconnect behavior
Potential stale-event behavior

Audit at minimum:

BACKEND

- Realtime session lifecycle;
- turn creation;
- provider response lifecycle;
- OpenAI input item lifecycle;
- input transcription;
- speech phrase generation;
- ElevenLabs/TTS generation;
- playback/control feedback;
- cancellation;
- barge-in;
- coordinator queues;
- post-response drains;
- deferred events;
- canvas generation;
- canvas candidate commit;
- canvas presentation scheduling;
- scene revision;
- interaction revision;
- memory persistence;
- transcript reconciliation;
- session reconnect/teardown;
- provider exception classification.

FLUTTER

- authenticated session lifecycle;
- realtime connection;
- turn runtime;
- turn presentation state;
- microphone intent;
- physical media track state;
- PCM playback;
- rendered sample count;
- captions;
- visemes;
- expressions;
- canvas controller;
- pending canvas candidate;
- active canvas scene;
- canvas presentation cue;
- interaction state;
- cancellation/barge-in;
- reconnect;
- session reset;
- duplicate/stale events.

Do not assume that naming indicates correct lifetime.

For every mutable field, ask:

"What event creates this?"
"What identity owns it?"
"What event invalidates it?"
"What happens if its owner dies but this object survives?"
"What happens if a late event arrives afterward?"

======================================================================
PHASE 2 — EVENT PROTOCOL AUDIT
======================================================================

Before making fixes, enumerate the externally observable event surface.

For OpenAI Realtime, inspect the current provider implementation and current
official protocol documentation where necessary.

Build an event classification table.

For each event type determine:

- source;
- required identifiers;
- whether it is session-scoped;
- item-scoped;
- response-scoped;
- turn-scoped;
- whether it may arrive late;
- whether ordering is guaranteed;
- whether it must be persisted;
- whether it must be ignored after terminal state;
- whether stale delivery is expected;
- whether unknown identity should be fatal or nonterminal;
- which handler owns it.

Do not rely on broad sets such as:

_IN_TURN_IGNORED_EVENT_TYPES

unless every member has intentionally defined semantics.

In particular audit transcription lifecycle events such as:

- committed input item;
- transcription delta;
- transcription segment, if supported/configured;
- transcription completed;
- transcription failed;

and ensure provider item_id ownership is preserved.

Audit equivalent lifecycle behavior for:

- response creation/completion;
- function calls;
- cancellation;
- playback controls;
- canvas events;
- reconnect/session teardown.

======================================================================
PHASE 3 — STATE MACHINE RECONSTRUCTION
======================================================================

Document the actual and desired state machines.

Do not implement a state-machine framework unless required.

Enums plus explicit transitions are sufficient if they establish correctness.

At minimum define:

TURN STATE

created
listening / input-active
processing
speaking / playback
completed
cancelled
interrupted
failed

INPUT TRANSCRIPTION STATE

registered
committed
partial
completed
failed
expired

PLAYBACK CLOCK STATE

unbound
bound to identity
progressing
stopped
terminal

CANVAS GENERATION STATE

requested
accepted
generating
validated
prepared
presented/active
rejected
cancelled
stale

MEMORY STATE

pending
waiting-for-transcript
committed
summary-only
failed/nonterminal

For every transition specify:

- triggering event;
- required current state;
- required identity;
- side effects;
- legal next state;
- whether duplicate event is idempotent;
- behavior for stale event;
- behavior for invalid event.

Find all places where implementation currently performs state changes without
these checks.

======================================================================
PHASE 4 — FIND LIFETIME/IDENTITY MISMATCHES
======================================================================

This is a dedicated bug search.

Find every location where:

- session-scoped object stores turn-scoped value without turnId;
- turn-scoped object stores item-scoped value without item_id;
- session-scoped queue contains turn-scoped controls without ownership;
- canvas state stores generation-specific values without generationId;
- scene state accepts work without base revision validation;
- playback values are passed as naked integers without clock identity;
- stale callbacks/tasks can write after cancellation;
- "current turn" is used where explicit event identity exists;
- mutable singleton/session fields are reused across turns;
- fallback "last_*" values can substitute for missing current data;
- completion of one subsystem implicitly means completion of another;
- exceptions from one subsystem are translated into another subsystem's
failure category.

Produce the full list before implementation.

Rank each finding:

P0 = can corrupt identity/state or fail persistent conversation;
P1 = likely runtime failure/race;
P2 = resilience/observability issue;
P3 = cleanup or maintainability issue.

Do not automatically fix every P2/P3 issue.

Prioritize correctness boundaries.

======================================================================
PHASE 5 — BUILD A DETERMINISTIC REALTIME TEST HARNESS
======================================================================

This is mandatory.

The current problem cannot be considered solved merely because focused unit
tests pass.

Create or strengthen a deterministic integration harness using fake providers:

- Fake OpenAI Realtime;
- Fake ElevenLabs/TTS;
- Fake Sol/canvas specialist;
- real coordinator/session logic where practical;
- simulated Flutter/client event consumer where practical.

The harness must allow explicit event ordering.

It must be possible to describe scenarios conceptually like:

scenario([
    TURN_CREATED(A),
    INPUT_COMMITTED(A),
    TRANSCRIPTION_DELTA(A),
    RESPONSE_STARTED(A),
    SPEECH_SEGMENT(A),
    PLAYBACK_STARTED(A, 0),
    PLAYBACK_PROGRESS(A, 12000),
    BARGE_IN(A),
    CANCEL(A),
    TURN_CREATED(B),
    LATE_TRANSCRIPTION_COMPLETED(A),
    LATE_PLAYBACK_PROGRESS(A),
    LATE_CANVAS_COMPLETED(A),
])

Then assert:

- Turn A events cannot mutate Turn B;
- Turn A transcript remains attributed to A;
- Turn A canvas cannot become Turn B canvas;
- Turn A cancellation cannot cancel B;
- Turn B clock remains valid;
- microphone state remains recoverable;
- no unrelated subsystem failure kills the session.

The harness must support:

- delayed events;
- duplicated events;
- stale events;
- cancellation;
- interruption;
- reconnect;
- provider error;
- TTS error;
- memory error;
- canvas timeout;
- canvas rejection.

Do not use sleeps as the primary correctness mechanism.

Prefer deterministic scheduling and explicit event injection.

======================================================================
PHASE 6 — REQUIRED SCENARIO MATRIX
======================================================================

Before claiming stabilization, create deterministic tests covering at least:

BASIC MULTI-TURN

1. turn 1 completes normally;
2. turn 2 begins from clean turn state;
3. turn 3 follows without reconnect;
4. persistent session state remains valid;
5. turn-scoped state resets correctly.

TRANSCRIPTION

6. committed -> delta -> completed;
7. committed -> multiple deltas -> completed;
8. segment lifecycle if supported;
9. response.done -> late delta -> completed;
10. transcription completed during cancellation;
11. transcription completed after barge-in;
12. stale previous-turn transcript after new turn starts;
13. unknown item_id transcription event;
14. transcription failure;
15. transcript does not fall back to previous-turn transcript;
16. partial transcript never becomes stable memory evidence.

PLAYBACK CLOCK

17. turn 1 samples increase normally;
18. turn 2 starts at zero;
19. active canvas survives turn clock reset;
20. stale Turn 1 progress arrives after Turn 2 begins;
21. duplicate playbackStarted;
22. playbackStopped followed by stale progress;
23. interrupted playback;
24. playback underrun/pause where applicable;
25. backward sample within same valid clock is rejected;
26. zero on new identity is accepted;
27. zero alone cannot silently redefine clock ownership.

CANVAS

28. no-canvas turn never invokes Sol;
29. canvas tool accepted while speech continues;
30. Sol completes normally;
31. Sol timeout does not fail speech;
32. Sol rejection does not fail speech;
33. canvas generation cancelled on barge-in;
34. cancelled generation later completes but cannot commit;
35. stale generation cannot replace newer revision;
36. pending previous-turn candidate does not silently rebase;
37. active canvas persists across turns;
38. presentation cue uses only matching source turn/playback clock;
39. stale cue cannot trigger during later turn;
40. renderer/client preparation failure is nonterminal to speech.

MICROPHONE

41. user mute during listening;
42. unmute after turn completion;
43. mute/unmute during processing;
44. mute/unmute during speaking;
45. failed optional subsystem does not permanently disable microphone;
46. reconnect preserves only intended user preference;
47. fatal session failure behavior remains intentional.

TURN SEQUENCING

48. duplicate turnCreated;
49. stale turnCreated;
50. new turn while incompatible active turn exists;
51. validation occurs before reset;
52. stale cancel event after next turn begins;
53. stale playback control after next turn begins.

MEMORY

54. memory DB failure;
55. delayed transcript;
56. transcript reconciliation after turn completion;
57. no transcript available;
58. model summary fallback is explicitly labeled;
59. summary cannot create stable preference evidence;
60. memory failure cannot delay/kill delivered speech.

PROVIDER/ERROR CLASSIFICATION

61. provider unavailable;
62. network timeout;
63. authentication failure;
64. valid-but-unhandled protocol event;
65. malformed protocol event;
66. local parser invariant failure;
67. each receives correct retryability/failure classification.

SESSION/RECONNECT

68. clean reconnect;
69. reconnect after interrupted turn;
70. reconnect while canvas generation is pending;
71. previous session's transient state cannot enter new session;
72. active session identity is enforced.

======================================================================
PHASE 7 — IMPLEMENTATION ORDER
======================================================================

After the audit and failing tests exist, implement corrections in dependency
order.

Recommended order:

1. event identity and routing;
2. turn/session ownership boundaries;
3. cancellation and stale-event handling;
4. playback clock ownership;
5. microphone lifecycle;
6. transcript ownership and reconciliation;
7. memory decoupling;
8. canvas generation/candidate ownership;
9. canvas presentation timing;
10. exception/error classification;
11. reconnect/session cleanup.

Do not combine unrelated corrections into one large refactor.

For every correction:

A. Show the failing test first.

B. State the violated invariant.

C. State the exact ownership/lifetime mismatch.

D. Trace all producers and consumers.

E. Implement the smallest change that restores the invariant.

F. Run focused tests.

G. Run adjacent subsystem tests.

H. Run the deterministic multi-turn suite.

I. Run the full relevant repository suite.

J. Inspect the diff for new lifetime coupling.

======================================================================
PHASE 8 — STRICTNESS POLICY
======================================================================

Do not make the system "stable" by making it permissive.

Forbidden shortcuts include:

- removing assertions simply because they throw;
- clamping invalid clocks with max(old, new);
- resetting state whenever zero appears;
- adding unexpected provider events to a generic ignore list without
understanding them;
- catching Exception broadly and translating everything into
MODEL_UNAVAILABLE;
- swallowing stale-state bugs silently;
- using last known transcript as fallback for another turn;
- using current turn identity when explicit provider identity is available;
- preserving cancelled tasks because cancellation is difficult;
- retrying local protocol errors as provider failures;
- converting canvas failure into conversation failure;
- converting memory failure into conversation failure.

Strict validation should remain.

But distinguish:

VALID AND CURRENT EVENT
    -> process

VALID BUT STALE EVENT
    -> reject/drop without mutating current state;
       normally nonterminal;
       record metric/log where useful

MALFORMED EVENT
    -> protocol error

VALID EVENT WITH UNKNOWN REQUIRED IDENTITY
    -> explicit ownership/protocol error according to the documented contract

LOCAL IMPLEMENTATION BUG
    -> do not disguise as provider availability

======================================================================
PHASE 9 — CONCURRENCY AND CANCELLATION AUDIT
======================================================================

Inspect every async task that may outlive its creator.

For every create_task / Future / timer / callback / stream subscription / queue:

Determine:

- who owns it;
- what identity it belongs to;
- what cancels it;
- whether cancellation is awaited;
- whether completion after cancellation can commit state;
- whether callback captures mutable current-turn/session state;
- whether it can enqueue into a queue consumed by a later turn;
- whether terminal transition drains or destroys its inbox.

No async task may write to authoritative state merely because it eventually
completed.

Commit must validate that its owner is still current.

Examples:

canvas result:
generationId + baseRevision still current?

transcript:
item_id still known and mapped?

playback:
clock identity still current?

memory reconciliation:
delivered turn record still valid?

======================================================================
PHASE 10 — QUEUE AUDIT
======================================================================

Inspect all queues/channels/streams used across turns.

For every queue ask:

- session-scoped or turn-scoped?
- can a message remain after terminal state?
- does message carry turnId?
- who drains it?
- can Turn B consume Turn A message?
- can a sentinel be lost because queue is full?
- can cancellation interrupt drain?
- can producer finish without consumer receiving terminal signal?

Prefer turn-owned inboxes for turn-owned controls.

Do not retain turn-specific queue contents across turn boundaries.

======================================================================
PHASE 11 — ERROR BOUNDARY AUDIT
======================================================================

Build an explicit error taxonomy.

At minimum distinguish:

- provider authentication error;
- provider network error;
- provider unavailable/rate limited;
- provider timeout;
- provider protocol incompatibility;
- local protocol parser error;
- local invariant violation;
- TTS failure;
- canvas generation failure;
- canvas validation failure;
- canvas presentation failure;
- memory persistence failure;
- client transport failure;
- fatal session corruption.

For each define:

- retryable?
- turn-terminal?
- session-terminal?
- child-visible?
- microphone impact?
- speech impact?
- telemetry classification?

Optional subsystem failures should normally be nonterminal to conversation.

Do not allow a broad generic exception handler to erase this distinction.

======================================================================
PHASE 12 — DO NOT TRUST UNIT-TEST GREEN ALONE
======================================================================

A correction is NOT complete merely because:

- focused tests pass;
- unit tests pass;
- static analysis passes;
- lint passes.

Before completion, execute:

1. focused regression tests;
2. adjacent subsystem tests;
3. deterministic multi-turn integration scenarios;
4. full backend tests;
5. full Flutter tests;
6. type/lint/static analysis;
7. any existing fake-provider end-to-end suite.

If guarded live integration tests already exist and credentials/configuration are
explicitly available, run only the established opt-in suite.

Do not invent uncontrolled live-provider testing.

======================================================================
PHASE 13 — ADVERSARIAL REVIEW AFTER IMPLEMENTATION
======================================================================

After implementation, stop writing code temporarily and attack your own diff.

For every changed behavior ask:

"What if this arrives twice?"

"What if it arrives late?"

"What if the next turn has already started?"

"What if cancellation happened one instruction earlier?"

"What if cancellation happened one instruction later?"

"What if network disconnect occurs between prepare and commit?"

"What if the provider finishes after owner cancellation?"

"What if the client ACK arrives after the candidate is stale?"

"What if playback progress arrives after playbackStopped?"

"What if an old event has a numerically larger sample count than the new turn?"

"What if item_id is valid but belongs to another turn?"

"What if memory fails after speech is delivered?"

"What if canvas fails while speech is playing?"

"What if turnCreated is duplicate?"

"What if the user mutes during this state?"

Add a regression test for every realistic failure discovered during this review.

======================================================================
PHASE 14 — MANUAL-TEST REPLACEMENT GOAL
======================================================================

The goal is NOT to eliminate manual testing.

The goal is that manual testing should primarily discover:

- UX problems;
- provider quality issues;
- device-specific rendering/performance issues;

NOT basic lifecycle/state-machine crashes.

If a deterministic event-ordering scenario can reproduce a manual crash, add it
to the automated harness permanently.

Every manually discovered lifecycle bug becomes a permanent regression scenario.

======================================================================
PHASE 15 — REQUIRED FINAL REPORT
======================================================================

At completion provide a structured report containing:

1. SYSTEM OWNERSHIP MAP

For each major state:
- owner;
- lifetime;
- identity;
- reset/terminal condition.

2. INVARIANT REGISTER

List every invariant used or added.

3. ROOT CAUSES FOUND

Do not list only symptoms.
Group bugs by structural cause, such as:
- identity loss;
- lifetime mismatch;
- stale-event leakage;
- incorrect cancellation ownership;
- error-boundary collapse;
- timing-authority mismatch.

4. CODE CHANGES

For every file changed:
- purpose;
- invariant restored;
- behavior changed.

5. TESTS ADDED

Map every new regression test to the bug/invariant it protects.

6. SCENARIO MATRIX

Report which deterministic multi-turn scenarios pass.

7. TEST RESULTS

Include:
- focused;
- integration;
- backend full suite;
- Flutter full suite;
- lint/type/static analysis.

Do not say "all good" without exact results.

8. REMAINING RISKS

List unresolved risks honestly.

For each:
- evidence;
- severity;
- why not changed;
- what would prove/disprove it.

9. DELIBERATELY UNCHANGED AREAS

List suspected issues that lacked sufficient evidence and were intentionally not
modified.

10. MANUAL VALIDATION CHECKLIST

Provide a short final physical/manual test sequence after automation is green.

======================================================================
STOP CONDITIONS
======================================================================

Do NOT claim the runtime is stabilized if:

- only the original exception is fixed;
- only focused unit tests pass;
- consecutive-turn behavior has not been tested;
- cancellation/barge-in has not been tested;
- stale previous-turn events have not been tested;
- reconnect has not been tested;
- optional subsystem failure isolation has not been tested;
- provider error classification remains broad/ambiguous;
- important mutable state still has unknown ownership/lifetime.

If you encounter a design choice for which product semantics are genuinely
undefined, do not guess silently.

Choose the safest behavior only when it follows existing product invariants.

Otherwise report the ambiguity explicitly while continuing all other work that
does not depend on it.

======================================================================
ANTI-OVERENGINEERING RULE
======================================================================

Do not introduce infrastructure merely because it is theoretically cleaner.

Every new abstraction must satisfy at least one of:

- fixes a demonstrated bug;
- enforces a documented invariant;
- enables a required deterministic test;
- removes duplicated ownership of authoritative state.

Examples of things NOT to introduce without evidence:

- generic event buses;
- distributed state-machine frameworks;
- new microservices;
- generic job orchestration;
- unnecessary transport lanes;
- generalized plugin systems;
- extra identifiers when an existing identity uniquely owns the state.

Prefer:

explicit IDs;
small state objects;
small transition functions;
turn-owned contexts;
typed error categories;
deterministic tests.

======================================================================
SPECIAL INSTRUCTION FOR THE CURRENT AVA CODEBASE
======================================================================

Recent bugs indicate a recurring structural pattern:

NARROW-LIFETIME DATA
stored in
LONGER-LIVED STATE
without preserving identity.

Known examples include:

- provider item-scoped transcription vs active-turn state;
- turn-scoped rendered sample clock vs session-scoped canvas controller;
- turn-scoped control/cancellation events vs persistent queues;
- canvas generation/base-revision state vs persistent scene state.

Perform a repository-wide audit specifically for this pattern before declaring
stabilization complete.

Do not wait for manual testing to reveal each instance individually.

======================================================================
WORKING STYLE
======================================================================

Be evidence-driven.

Read the actual implementation before proposing changes.

Search all producers and consumers before editing a shared field or contract.

Do not assume comments are correct when code disagrees.

Do not assume tests describe the complete provider protocol.

Do not make unrelated cosmetic refactors.

Do not change product behavior merely to simplify implementation.

Do not silently loosen validation.

Do not stop at the first green test.

Your responsibility is not merely:

"make the current error disappear."

Your responsibility is:

"make this class of lifecycle/state error difficult to represent, and make the
remaining cases detectable automatically before manual realtime testing."