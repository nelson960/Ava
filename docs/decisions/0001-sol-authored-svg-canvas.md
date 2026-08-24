# ADR 0001: Sol-authored exact SVG canvas

- Status: accepted
- Date: 2026-08-21
- Owners: Ava product and runtime
- Supersedes: protocol-v3 typed specialist scene output and primitive runtime
  reconstruction

## Context

Repeated visual experiments showed that `gpt-5.6-luna` constrained to Ava's
typed primitive scene grammar produced unacceptable diagrams. The working
quality baseline uses `gpt-5.6-sol` to author the complete SVG with control over
composition, typography, paths, gradients, filters, clipping, transforms, and
paint order.

The prior design made the backend and Flutter reconstruct model output into a
smaller scene vocabulary. That altered composition and styling, created silent
repair behavior, and prevented the accepted provider artwork from remaining
the rendered source of truth.

## Decision

OpenAI Realtime remains the conversation brain. It may call only the bounded
semantic `request_interactive_canvas` function. A valid call is accepted as an
asynchronous application job and receives exactly one immediate function
output containing an application generation ID. Realtime never waits for Sol
to finish before it may continue ordinary speech, and an accepted result never
means the visual is ready.

`gpt-5.6-sol` is the only canvas specialist. It receives the semantic brief,
age band, renderer capability version, and complete current accepted SVG for a
revision. It returns one complete SVG document as assistant text. Reasoning
effort, timeout, maximum output, repair count, and spend are explicit backend
configuration and are evaluated against a frozen representative corpus.

The exact assistant `output_text` Unicode string is UTF-8 encoded once. Those
bytes and their SHA-256 are canonical. Ava may inspect but never normalize,
strip, serialize, clamp, truncate, reorder, restyle, or otherwise repair an
accepted document. Invalid work is rejected or sent to Sol once for a complete
replacement.

The SVG validator rejects active or external content and bounds both syntax
and render expansion. Provider scripts, event handlers, `foreignObject`,
DTDs/entities, network URLs, navigation, unsupported CSS, reference cycles,
unbounded local-reference graphs, filters, masks, clips, patterns, transforms,
paths, text, and document size are not accepted.

The backend derives a deterministic interaction/accessibility manifest from
stable SVG IDs and bounded `data-ava-*` metadata. It binds that manifest to the
exact SVG using independent hashes plus validator and renderer-capability
versions. The manifest is semantic and hit-test metadata; it never reconstructs
the visual.

## Transport and activation

Speech control and PCM stay on the authenticated Realtime event WebSocket. SVG
bytes never enter that ordered audio lane. A control event announces an
available candidate, and Flutter fetches the exact SVG through a separately
authenticated, bounded, no-store canvas-document endpoint.

Canvas state is session-scoped and progresses as follows:

```text
active -> candidate -> prepared -> presented -> active
```

The backend prepares a candidate against one active base revision. Flutter
fetches, verifies, and preloads it, then reports `canvasDocumentPrepared`. The
coordinator owns the conversational phrase anchor and maps it through the
delivered-text/alignment ledger to a rendered sample offset. Flutter presents
the candidate at that cue and reports `canvasPresented` after the first
successful rendered frame. Only then does the backend activate the revision
and acknowledge `canvasActivated` so semantic interactions may begin.

Barge-in, cancellation, renderer failure, disconnect, timeout, or a newer
generation before `canvasPresented` discards the candidate and preserves the
previous active scene. If the presentation sample has already passed when the
document becomes prepared, the coordinator requests immediate presentation.

## Rendering and interaction

The runtime renders the accepted SVG directly. Renderer selection is gated by
the frozen Sol corpus, exact visual fidelity, element-level hit testing,
security, accessibility, frame performance, and iPhone/iPad/macOS consistency.
No automatic fallback to the typed primitive renderer is allowed.

The preferred interaction path is renderer-native element hit testing that
returns the nearest validated binding element ID in paint order. If a selected
direct Flutter renderer cannot do this, the validated manifest must contain
exact SVG-viewBox hit geometry. Flutter must never guess from labels or rebuild
the full SVG merely to hit-test it. The app expands compact targets to at least
44 physical points without changing visible artwork.

Local reveal, toggle, drag, focus, reset, and visual selection state remain
provider-free. Semantic explore, select, and submit-outcome controls contain
only session, scene, revision, interaction, and binding IDs. The backend
resolves all labels, descriptions, and values from its hash-bound manifest.

## Trust and timing

Sol owns what the drawing looks like. Realtime owns conversational intent. The
coordinator owns when the drawing becomes relevant. ElevenLabs alignment and
the delivered sample ledger map the selected validated phrase/segment anchor
to the presentation sample. Flutter follows that sample clock and reports what
was actually presented.

Text derived from SVG title, description, or interaction metadata remains
untrusted model-generated data. Ava bounds it, removes control characters,
encodes it as data, and tells Realtime never to follow instructions found in
it. It is never concatenated into trusted system instructions.

## Consequences

- Protocol v4 is required because the canvas wire contract is incompatible
  with protocol v3.
- The old typed canvas compiler, transaction diff, primitive Flutter engine,
  and SVG-to-primitive importer are removed only after v4 fake, cross-language,
  visual, interruption, and physical-device gates pass.
- Canvas latency and cost are higher than Luna, but they no longer gate speech.
- Invalid optional visuals preserve ordinary conversation and the previous
  active scene.
- The canvas-document lane, validator, renderer, and activation handshake add
  explicit complexity in exchange for audio isolation, exact visual fidelity,
  and end-to-end scene consistency.

## Required verification

- one accepted assistant SVG text payload and completed Responses status;
- exact byte/hash preservation from provider output to client fetch;
- no audio latency regression while fetching maximum accepted SVG documents;
- graph, CSS, external-resource, active-content, and renderer-denial tests;
- prepared/presented/activated ordering, timeout, replay, supersession, and
  cancellation tests;
- renderer element hit testing and minimum physical target tests;
- sample-clock presentation and barge-in before/after presentation;
- guarded Sol evaluation with spend limits and physical iPhone/iPad runs.
