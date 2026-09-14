# AVA — Agent Rules

## What is AVA

AVA is a low-latency conversational avatar that combines:

- realtime natural voice conversation;
- interruption and continuous interaction;
- persistent semantic memory;
- dynamic interactive SVG Canvas;
- expressive avatar speech and physical interaction;
- continuity across long sessions and restarts.

The end product should feel like one continuous intelligent agent, not separate speech, memory, Canvas, and model systems.

---

## Architecture

### Realtime

OpenAI Realtime owns semantic intelligence:

- conversation;
- reasoning;
- language understanding;
- references and intent;
- tool decisions;
- memory retrieval decisions;
- post-delivery semantic capture.

Development model:

    gpt-realtime-2.1-mini

### Backend

The backend owns canonical truth:

- state;
- IDs;
- revisions;
- hashes;
- validation;
- persistence;
- memory canonicalization;
- provider lifecycle;
- ResourceGovernor;
- event ownership;
- reset/session fencing;
- delivered-text truth;
- Canvas workspace and presentation state.

The model must not own mechanically derivable backend truth.

### Canvas

The SVG specialist owns deep Canvas generation and revision.

Development model:

    gpt-5.6-terra
    reasoning = medium

Internal Canvas IDs and hashes are backend details and should not become conversational model context unless strictly necessary.

### Flutter

Flutter owns:

- rendering;
- playback;
- sample clock;
- taps;
- drag;
- sliders;
- local physical interaction;
- avatar presentation.

### Speech

ElevenLabs is the production TTS path.

Semantic memory must use what the user actually heard, not generated-but-undelivered text.

---

## Memory

AVA has one evolving memory architecture.

Core state:

- delivered conversation evidence;
- DialogueFrame;
- SemanticJournal;
- SemanticMemoryProposal;
- SemanticMemoryReceipt.

Post-delivery semantic capture is the production semantic-memory producer.

The model owns semantic judgment.

The backend owns canonical representation, bounds, identity, revisions, fingerprints, persistence, supersession, and fencing.

Do not create parallel memory versions or duplicate producers.

---

## Dynamic Context

DynamicContextAssembler is the single bounded projection of AVA-owned state into model-readable context.

It is not durable memory.

It should eventually reconstruct from:

- current DialogueFrame;
- recent delivered conversation in true turn order;
- relevant durable semantic memory;
- current semantic Canvas state;
- current/pending task state.

Do not create another context system beside it.

---

## Implementation Rules

Always evolve the existing owner in place.

Do not create parallel versions, compatibility layers, duplicate coordinators, duplicate routers, duplicate memory systems, or replacement architectures beside the current one.

Before editing:

1. inspect the named production paths and direct callers;
2. verify assumptions against the current dirty working tree;
3. use live evidence when available;
4. stop and report if the architecture differs materially;
5. implement only the stated ownership boundary.

When a change makes directly-related code obsolete, remove it in the same pass.

Also remove directly-related dead, duplicated, compatibility, or unused code discovered while working in that boundary.

Do not broaden into unrelated cleanup.

---

## Tests

Tests must prove architecture invariants, not be changed merely to make implementation pass.

Do not:

- weaken valid assertions;
- change expected values to match broken production behavior;
- create unrealistic fakes that bypass the real production boundary;
- mock away the code being tested;
- use arbitrary sleeps for ordering;
- invent provider behavior, timings, token counts, or outputs.

If a test changes, explain why its old assumption is no longer valid.

Design deterministic tests with explicit:

- setup;
- input/event order;
- production path exercised;
- measured invariant;
- expected result;
- evidence source.

Run only the tests necessary for the changed subsystem and its direct boundaries.

Use live probes only for behavior that deterministic tests cannot establish.

---

## Evidence Priority

Use evidence in this order:

1. real live logs and provider behavior;
2. architecture invariants;
3. provider contracts;
4. deterministic tests;
5. implementation assumptions.

A passing test does not override contradictory live evidence.

---

## Current Architecture Priorities

Work in this order:

1. semantic capture reliability;
2. model-facing Canvas semantic state and deterministic navigation;
3. DynamicContextAssembler reconstruction and ordering;
4. bounded provider working set, compaction, and rollover;
5. async Canvas generation without blocking conversation;
6. Canvas interaction grounding;
7. integrated long-session validation.

Do not implement later architecture inside an earlier pass.

---

## Git Safety

The dirty working tree may contain important uncommitted work.

Never run without explicit permission:

    git add
    git commit
    git reset
    git restore
    git checkout
    git clean
    git push

Do not overwrite unrelated changes.

---

## Final Rule

AVA should become simpler as it evolves.

For every change:

- identify the current owner;
- fix the broken invariant there;
- remove what becomes obsolete;
- prove the behavior with focused evidence;
- avoid creating another version of the same architecture.