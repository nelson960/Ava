# Ava

Ava is an avatar-first realtime conversational character for children on
iPhone and iPad, with macOS used for development. The experience opens directly
to a living 3D character. There is no navigation feed, activity catalog, score,
or permanent dashboard.

The lower panel is an optional dynamic vector canvas used only when a visual
helps the current conversation. Ava remains visible above it. The app also
preserves local avatar touch/3D interaction and the restrained living
day/night background.

## Production direction

The locked runtime uses:

- OpenAI Realtime for child audio understanding, conversation, performance,
  and the bounded `canvasNeeded`/`canvasBrief` decision;
- strict OpenAI Responses canvas generation only for turns that need a visual;
- ElevenLabs for Ava's selected production voice;
- the Ava backend for prompts, policy, validation, cancellation, memory,
  budgets, and provider keys;
- Flutter/native code for playback, sample-clock synchronization, avatar
  behavior, touch, captions, lip sync, and dynamic vector rendering.

OpenAI audio output is disabled. The Realtime model emits a forced structured
function call; the backend validates complete incremental speech segments and
streams them to ElevenLabs. TTS audio and alignment are sent to Flutter over an
authenticated Ava event channel. The rendered PCM sample clock synchronizes
captions, visemes, expressions, and canvas actions.

Read the current source of truth:

- [`AGENTS.md`](AGENTS.md)
- [`docs/production_architecture.md`](docs/production_architecture.md)
- [`docs/production_implementation_plan.md`](docs/production_implementation_plan.md)

## Repository status

Implemented and retained:

- production Ava GLB and validated rig/touch maps;
- mesh-based avatar touch and 3D camera interaction;
- living idle and restrained speaking baseline;
- sample-counted native PCM playback;
- shared Python/Dart protocol-v3 schemas, fixtures, turn state machines and
  bounded deterministic fake streams;
- native PCM rendered/buffered sample reporting and interruption fade-stop;
- bounded backend SDP negotiation with separately scoped one-use media/event
  credentials;
- Flutter audio-only WebRTC peer and microphone lifecycle behind deterministic
  media/SDP fakes;
- authenticated protocol-v3 event WebSocket with strict JSON/binary PCM
  ordering;
- interruption and alignment-derived delivered-prefix accounting;
- general dynamic canvas scene graph, local renderer, hit testing, and
  interactions;
- strict on-demand canvas specialist, deterministic transaction compiler, and
  non-blocking `canvasRejected` failure path;
- bounded protocol-v3 memory and deterministic coordinator lifecycle;
- portable private PostgreSQL memory, delivered-turn compaction, cloud reset,
  weekly recap, and prompt regression evaluation;
- minimal child profile/captions/recap/reset settings UI;
- local living day/night background;
- backend prompt, validation, provider, auth, and persistence seams.

Live/release work still required:

- guarded end-to-end OpenAI Realtime verification on the visible app path;
- ElevenLabs input-streaming WebSocket;
- physical iPhone/iPad WebRTC, audio-route, interruption, and performance
  verification;
- Supabase deployment verification, production auth, abuse controls, budgets,
  and telemetry.

The legacy lesson-turn and NDJSON performance-turn backend routes have been
removed after the protocol-v4 cutover. Remaining Responses fixtures and local
presentation types are offline evaluation or compatibility tooling, not
shipping conversation paths.

## API keys

No provider key is needed for the first implementation phases: protocol
schemas, deterministic fake streams, state machines, dynamic canvas, local
playback, cancellation tests, and Flutter/backend integration can all be built
offline.

Live integration later requires two provider secrets stored only on the
backend:

- `AVA_OPENAI_API_KEY`;
- `AVA_ELEVENLABS_API_KEY`.

ElevenLabs activation also requires the exact voice ID, exact model ID, and
documented commercial-use rights. The current selection record at
`avatar/sounds/ava_voice_selection.v1.json` is still a research-only candidate
and does not contain those IDs.

No provider key may be added to Flutter assets, Dart defines, `Info.plist`,
Gradle files, or the shipped app bundle.

## Local protocol-v3 caption preview

Caption preview exercises the production protocol-v3 path with live OpenAI
Realtime, native sample-counted playback, captions, lip sync, canvas, and
PostgreSQL memory. It deliberately emits silent PCM in place of ElevenLabs and
cannot be enabled in production. A conversation turn is a paid OpenAI request;
starting the services alone does not contact OpenAI.

Start persistent local PostgreSQL from the repository root:

```sh
/opt/homebrew/bin/docker compose \
  -f backend/docker-compose.memory.yml up -d --wait
```

Start the backend from `backend/`. The OpenAI key remains in the backend-only
`.env`; do not pass it to Flutter:

```sh
AVA_DATABASE_URL=postgresql://ava:ava-local-only@127.0.0.1:55432/ava \
AVA_REALTIME_DELIVERY_MODE=captionPreview \
AVA_REALTIME_ALLOW_INSECURE_LOOPBACK=true \
/opt/miniconda3/envs/ml/bin/python -m uvicorn ava_backend.asgi:app \
  --app-dir src \
  --host 127.0.0.1 \
  --port 8080
```

Then run the macOS app from the repository root:

```sh
/opt/homebrew/share/flutter/bin/flutter run -d macos \
  --dart-define=AVA_API_BASE_URL=http://127.0.0.1:8080/ \
  --dart-define=AVA_ALLOW_INSECURE_LOCALHOST=true
```

Enable captions in Ava's settings, press the microphone, speak once, and
pause. App startup negotiates one attached audio track in the disabled state;
the first explicit tap enables that already prepared track. Silent timed
playback then advances
the visible caption, visemes, canvas cues, delivered-prefix memory, and turn
completion exactly as ElevenLabs PCM will. Avatar touch remains local and
active throughout.

## Avatar asset pipeline

The Blender source, GLB, rig bindings, and anatomical map form one contract.
Rebuild from the repository root with:

```sh
/Applications/Blender.app/Contents/MacOS/Blender \
  --background avatar/ava_vroid_source.blend \
  --python avatar/tools/install_ava_response_motions.py \
  -- avatar/ava_vroid_source.glb

/opt/miniconda3/envs/ml/bin/python avatar/tools/generate_ava_touch_map.py \
  avatar/ava_vroid_source.glb avatar/ava_touch_map.json

/opt/miniconda3/envs/ml/bin/python avatar/tools/validate_ava_export.py \
  avatar/ava_vroid_source.glb avatar/ava_touch_map.json
```

Ordinary speech keeps `AvaIdle` as its visual baseline and adds restrained
sample-clock lip sync. Touch remains enabled during speech. Do not create
another avatar or reactivate broad response-gesture experiments without an
explicit product decision.

## Validation

```sh
/opt/homebrew/share/flutter/bin/flutter analyze
/opt/homebrew/share/flutter/bin/flutter test
/opt/homebrew/share/flutter/bin/flutter build macos
/opt/homebrew/share/flutter/bin/flutter build ios --no-codesign
```

The current 3D viewer depends on `flutter_inappwebview`, whose Apple plugin
still uses CocoaPods. Native builds may report its Swift Package Manager
adoption warning.
