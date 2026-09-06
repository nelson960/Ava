import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_controller.dart';
import 'package:ava/canvas/ava_svg_canvas_session_host.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_canvas_persistence_service.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/services/memory_service.dart';
import 'package:crypto/crypto.dart';
import 'package:drift/drift.dart' show OrderingTerm, Value;
import 'package:drift/native.dart';
import 'package:flutter/foundation.dart' show FlutterError, FlutterErrorDetails;
import 'package:flutter_test/flutter_test.dart';

const _manifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","bindingId":"select-cloud","elementId":"cloud","revealIds":[],"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Café cloud ☁️","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';

void main() {
  late AppDatabase database;
  late AvaCanvasPersistenceRepository repository;

  setUp(() {
    database = AppDatabase.forTesting(NativeDatabase.memory());
    repository = AvaCanvasPersistenceRepository(database);
  });

  tearDown(() async {
    await repository.idle;
    await database.close();
  });

  test('only canonical activation persists exact validated bytes', () async {
    expect(database.schemaVersion, 5);
    final document = _document(scene: 1, revision: 1);
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(document),
      commandSink: commands.add,
    );
    final host = AvaSvgCanvasSessionHost(persistence: repository);
    await host.restoration;
    host.attach(controller);

    final events = _events(document);
    controller.beginPlaybackClock(document.descriptor.sourceTurnId);
    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'focusRevision': 0,
      'sessionId': 'session-persistence',
      'sequence': 0,
      'revision': 1,
      'generationId': document.descriptor.generationId,
      'sourceTurnId': document.descriptor.sourceTurnId,
      'operation': 'new',
    });
    await controller.receiveDocumentAvailable(events[0]);
    controller.rendererPrepared();
    controller.receivePresentationCue(events[1]);
    controller.updateRenderedSample(
      turnId: document.descriptor.sourceTurnId,
      renderedSample: 4812,
    );
    controller.firstFramePresented();

    expect(await database.select(database.canvasScenes).get(), isEmpty);

    controller.receiveActivated(events[2]);
    await repository.idle;

    final stored = await database.select(database.canvasScenes).getSingle();
    expect(stored.sceneId, document.descriptor.sceneId);
    expect(stored.svgBytes, document.svgBytes);
    expect(stored.manifestBytes, document.manifestBytes);
    expect(stored.svgSha256, document.descriptor.svgSha256);
    expect(stored.manifestSha256, document.descriptor.manifestSha256);
    expect(commands.map((command) => command.type), [
      AvaSvgCanvasCommandType.canvasDocumentPrepared,
      AvaSvgCanvasCommandType.canvasPresented,
    ]);

    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('schema four migration preserves order without inventing edits',
      () async {
    await database.close();
    database = AppDatabase.forTesting(
      NativeDatabase.memory(
        setup: (raw) {
          raw.execute('''
            CREATE TABLE canvas_scenes (
              scene_id TEXT NOT NULL PRIMARY KEY,
              scene_revision INTEGER NOT NULL,
              generation_id TEXT NOT NULL,
              source_turn_id TEXT NOT NULL,
              svg_bytes BLOB NOT NULL,
              manifest_bytes BLOB NOT NULL,
              svg_sha256 TEXT NOT NULL,
              manifest_sha256 TEXT NOT NULL,
              svg_byte_length INTEGER NOT NULL,
              manifest_byte_length INTEGER NOT NULL,
              renderer_capability_version TEXT NOT NULL,
              validator_version TEXT NOT NULL,
              manifest_version TEXT NOT NULL,
              workspace_order INTEGER NOT NULL,
              is_focused INTEGER NOT NULL
            )
          ''');
          for (var index = 0; index < 3; index++) {
            raw.execute(
              'INSERT INTO canvas_scenes VALUES (?, 1, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?)',
              [
                'legacy-${index + 1}',
                'generation-${index + 1}',
                'turn-${index + 1}',
                Uint8List.fromList([index]),
                Uint8List.fromList([index]),
                '0'.padLeft(64, '0'),
                '1'.padLeft(64, '1'),
                avaSvgRendererCapabilityVersion,
                avaSvgValidatorVersion,
                avaSvgManifestVersion,
                index,
                index == 1 ? 1 : 0,
              ],
            );
          }
          raw.execute('PRAGMA user_version = 4');
        },
      ),
    );
    repository = AvaCanvasPersistenceRepository(database);

    final rows = await (database.select(database.canvasScenes)
          ..orderBy([(row) => OrderingTerm.asc(row.workspaceOrder)]))
        .get();
    final workspace =
        await database.select(database.canvasWorkspaces).getSingle();

    expect(rows.map((row) => row.creationSequence), [1, 2, 3]);
    expect(rows.map((row) => row.modificationSequence), [null, null, null]);
    expect(workspace.workspaceEpoch, 0);
    expect(workspace.workspaceRevision, 0);
    expect(workspace.nextCreationSequence, 4);
    expect(workspace.nextModificationSequence, 1);
  });

  test('progressive draft is never persisted', () async {
    final document = _document(scene: 1, revision: 1);
    final draftBytes = Uint8List.fromList(document.svgBytes);
    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(document, draftBytes: draftBytes),
      commandSink: (_) {},
    );
    final host = AvaSvgCanvasSessionHost(persistence: repository);
    await host.restoration;
    host.attach(controller);
    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'focusRevision': 0,
      'sessionId': 'session-persistence',
      'sequence': 0,
      'revision': 1,
      'generationId': document.descriptor.generationId,
      'sourceTurnId': document.descriptor.sourceTurnId,
      'operation': 'new',
    });
    await controller.receiveDraftAvailable({
      'protocolVersion': 4,
      'event': 'canvasDraftAvailable',
      'sessionId': 'session-persistence',
      'sequence': 1,
      'revision': 2,
      'generationId': document.descriptor.generationId,
      'sourceTurnId': document.descriptor.sourceTurnId,
      'draftIndex': 1,
      'svgPath': '/v4/realtime/sessions/session-persistence/canvas-drafts/'
          '${document.descriptor.generationId}/1/svg',
      'svgSha256': sha256.convert(draftBytes).toString(),
      'svgByteLength': draftBytes.length,
      'authoritative': false,
      'interactive': false,
      'rendererCapabilityVersion': avaSvgRendererCapabilityVersion,
      'validatorVersion': avaSvgValidatorVersion,
    });
    controller.draftPresented(controller.draft!.document);
    await repository.idle;

    expect(controller.draft, isNotNull);
    expect(await database.select(database.canvasScenes).get(), isEmpty);

    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('workspace order, bound, in-place revision, and focus survive restart',
      () async {
    for (var scene = 1; scene <= 11; scene++) {
      await repository.persistActive(_document(scene: scene, revision: 1));
    }

    var restored = await repository.loadWorkspace();
    expect(
      restored.documents.map((document) => document.descriptor.sceneId),
      [for (var scene = 2; scene <= 11; scene++) 'scene-$scene'],
    );
    expect(restored.focusedSceneId, 'scene-11');

    await repository.persistActive(_document(scene: 5, revision: 2));
    await repository.persistActive(_document(scene: 5, revision: 2));
    restored = await repository.loadWorkspace();

    expect(restored.documents, hasLength(10));
    expect(
      restored.documents.map((document) => document.descriptor.sceneId),
      [for (var scene = 2; scene <= 11; scene++) 'scene-$scene'],
    );
    expect(
      restored.documents
          .singleWhere(
            (document) => document.descriptor.sceneId == 'scene-5',
          )
          .descriptor
          .sceneRevision,
      2,
    );
    expect(restored.focusedSceneId, 'scene-5');
    expect(
      () => repository.persistActive(_document(scene: 5, revision: 1)),
      throwsA(isA<AvaCanvasPersistenceConflict>()),
    );
  });

  test('focused middle scene survives restart without becoming visible',
      () async {
    await repository.persistActive(_document(scene: 1, revision: 1));
    await repository.persistActive(_document(scene: 2, revision: 1));
    await repository.persistActive(
      _document(scene: 3, revision: 1),
      focused: false,
    );

    final host = AvaSvgCanvasSessionHost(persistence: repository);
    await host.restoration;
    final manifest = host.workspaceManifestJson();
    final scenes = (manifest['scenes']! as List).cast<Map<String, Object?>>();

    expect(
      host.workspaceDocuments
          .map((document) => document.descriptor.sceneId)
          .toList(),
      ['scene-1', 'scene-2', 'scene-3'],
    );
    expect(host.presentedDocument?.descriptor.sceneId, 'scene-2');
    expect(manifest['focusedSceneId'], 'scene-2');
    expect(
      scenes.map((scene) => scene['sceneId']).toList(),
      ['scene-1', 'scene-2', 'scene-3'],
    );
    expect(host.panelVisible, isFalse);

    host.dispose();
  });

  test('restored revision four activates and persists revision five in place',
      () async {
    final original = _document(scene: 6, revision: 4);
    await repository.persistActive(original);
    final host = AvaSvgCanvasSessionHost(persistence: repository);
    await host.restoration;
    final revised = _document(scene: 6, revision: 5);
    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(revised),
      commandSink: (_) {},
      retainedSceneRevision: host.retainedSceneRevision,
    );
    host.attach(controller);
    expect(controller.active?.document.descriptor.sceneRevision, 4);

    await _activateDocument(controller, revised);
    await repository.idle;
    final restored = await repository.loadWorkspace();

    expect(restored.documents, hasLength(1));
    expect(restored.documents.single.descriptor.sceneId, 'scene-6');
    expect(restored.documents.single.descriptor.sceneRevision, 5);
    expect(host.workspaceDocuments, hasLength(1));
    expect(host.presentedDocument?.descriptor.sceneRevision, 5);
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('corrupt and unsupported records are discarded independently', () async {
    for (var scene = 1; scene <= 4; scene++) {
      await repository.persistActive(_document(scene: scene, revision: 1));
    }
    await (database.update(database.canvasScenes)
          ..where((row) => row.sceneId.equals('scene-1')))
        .write(
      CanvasScenesCompanion(svgBytes: Value(Uint8List.fromList([0xff]))),
    );
    await (database.update(database.canvasScenes)
          ..where((row) => row.sceneId.equals('scene-2')))
        .write(
      CanvasScenesCompanion(manifestBytes: Value(Uint8List.fromList([0xff]))),
    );
    await (database.update(database.canvasScenes)
          ..where((row) => row.sceneId.equals('scene-3')))
        .write(
      const CanvasScenesCompanion(
        rendererCapabilityVersion: Value('unsupported-renderer'),
      ),
    );

    final restored = await repository.loadWorkspace();

    expect(
      restored.documents.map((document) => document.descriptor.sceneId),
      ['scene-4'],
    );
    expect(restored.focusedSceneId, 'scene-4');
    expect(
      (await database.select(database.canvasScenes).get())
          .map((row) => row.sceneId),
      ['scene-4'],
    );
  });

  test('restored workspace enters active host path without protocol commands',
      () async {
    final original = _document(scene: 7, revision: 4);
    await repository.persistActive(original);

    final host = AvaSvgCanvasSessionHost(persistence: repository);
    await host.restoration;

    expect(host.controller, isNull);
    expect(host.generating, isFalse);
    expect(host.workspaceDocuments, hasLength(1));
    expect(host.presentedDocument?.svgBytes, original.svgBytes);
    expect(host.presentedDocument?.manifestBytes, original.manifestBytes);
    expect(host.presentedDocument?.descriptor.isLocallyRestored, isTrue);
    expect(
      () => host.presentedDocument!.descriptor.svgPath,
      throwsStateError,
    );
    expect(
      () => host.presentedDocument!.descriptor.manifestPath,
      throwsStateError,
    );
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);
    host.reopenPanel();
    expect(host.panelVisible, isTrue);
    host.dispose();
  });

  test('reset fences queued persistence and prevents restart resurrection',
      () async {
    final writes = <Future<void>>[
      for (var scene = 1; scene <= 20; scene++)
        repository.persistActive(_document(scene: scene, revision: 1)),
    ];

    await repository.reset();
    await Future.wait(writes);
    await repository.idle;

    expect(await database.select(database.canvasScenes).get(), isEmpty);
    final restarted = AvaCanvasPersistenceRepository(database);
    expect((await restarted.loadWorkspace()).documents, isEmpty);
  });

  test('unexpected structural eviction is rejected without changing order',
      () async {
    for (var scene = 1; scene <= 10; scene++) {
      await repository.persistActive(_document(scene: scene, revision: 1));
    }
    final host = AvaSvgCanvasSessionHost(persistence: repository);
    await host.restoration;
    final created = _document(scene: 11, revision: 1);
    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(created),
      commandSink: (_) {},
      retainedSceneRevision: host.retainedSceneRevision,
    );
    host.attach(controller);
    final events = _events(created);
    controller.beginPlaybackClock(created.descriptor.sourceTurnId);
    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'focusRevision': 0,
      'sessionId': 'session-persistence',
      'sequence': 0,
      'revision': 1,
      'generationId': created.descriptor.generationId,
      'sourceTurnId': created.descriptor.sourceTurnId,
      'operation': 'new',
    });
    await controller.receiveDocumentAvailable(events[0]);
    controller.rendererPrepared();
    controller.receivePresentationCue(events[1]);
    controller.updateRenderedSample(
      turnId: created.descriptor.sourceTurnId,
      renderedSample: 4812,
    );
    controller.firstFramePresented();
    events[2]['evictedSceneId'] = 'scene-2';
    final priorFlutterErrorHandler = FlutterError.onError;
    FlutterErrorDetails? capturedError;
    FlutterError.onError = (details) => capturedError = details;

    controller.receiveActivated(events[2]);
    FlutterError.onError = priorFlutterErrorHandler;
    expect(capturedError?.exception, isA<StateError>());
    expect(
      (capturedError?.exception as StateError).message,
      contains('unexpected eviction'),
    );
    expect(
      host.workspaceDocuments
          .map((document) => document.descriptor.sceneId)
          .toList(),
      [for (var scene = 1; scene <= 10; scene++) 'scene-$scene'],
    );

    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('clear workspace invalidates an in-flight persisted restore', () async {
    await repository.persistActive(_document(scene: 8, revision: 1));
    final restored = await repository.loadWorkspace();
    final persistence = _HeldCanvasPersistence();
    final host = AvaSvgCanvasSessionHost(persistence: persistence);
    var notificationCount = 0;
    host.addListener(() => notificationCount++);

    host.clearWorkspace();
    persistence.complete(restored);
    await host.restoration;

    expect(host.workspaceDocuments, isEmpty);
    expect(host.presentedDocument, isNull);
    expect(notificationCount, 0);
    expect(persistence.persistedDocuments, isEmpty);
    host.dispose();
  });

  test('hung local restore degrades to empty within its startup bound',
      () async {
    final host = AvaSvgCanvasSessionHost(
      persistence: _NeverCanvasPersistence(),
      persistenceRestoreTimeout: const Duration(milliseconds: 1),
    );

    await host.restoration;

    expect(host.workspaceDocuments, isEmpty);
    expect(host.presentedDocument, isNull);
    host.dispose();
  });
}

final class _HeldCanvasPersistence implements AvaCanvasPersistence {
  final Completer<AvaRestoredCanvasWorkspace> _load = Completer();
  final List<AvaValidatedSvgCanvasDocument> persistedDocuments = [];

  void complete(AvaRestoredCanvasWorkspace workspace) {
    _load.complete(workspace);
  }

  @override
  Future<AvaRestoredCanvasWorkspace> loadWorkspace() => _load.future;

  @override
  Future<void> persistActive(AvaValidatedSvgCanvasDocument document,
      {bool focused = true}) async {
    persistedDocuments.add(document);
  }

  @override
  Future<void> persistFocus(String sceneId) async {}

  @override
  Future<void> reset() async {}
}

final class _NeverCanvasPersistence implements AvaCanvasPersistence {
  @override
  Future<AvaRestoredCanvasWorkspace> loadWorkspace() =>
      Completer<AvaRestoredCanvasWorkspace>().future;

  @override
  Future<void> persistActive(AvaValidatedSvgCanvasDocument document,
      {bool focused = true}) async {}

  @override
  Future<void> persistFocus(String sceneId) async {}

  @override
  Future<void> reset() async {}
}

AvaValidatedSvgCanvasDocument _document({
  required int scene,
  required int revision,
}) {
  final fixture = (jsonDecode(
    File('protocol/fixtures/ava_realtime_canvas_events_v4.json')
        .readAsStringSync(),
  ) as List)
      .first as Map;
  final descriptor = AvaSvgCanvasDocumentDescriptorV4.fromJson({
    ...fixture.cast<String, Object?>(),
    'generationId': 'generation-$scene-$revision',
    'sourceTurnId': 'turn-$scene-$revision',
    'sceneId': 'scene-$scene',
    'sceneRevision': revision,
  });
  return AvaValidatedSvgCanvasDocument.verify(
    descriptor: descriptor,
    svgBytes:
        File('protocol/fixtures/ava_svg_document_v4.svg').readAsBytesSync(),
    manifestBytes: Uint8List.fromList(utf8.encode(_manifestJson)),
  );
}

List<Map<String, Object?>> _events(AvaValidatedSvgCanvasDocument document) {
  final descriptor = document.descriptor;
  return (jsonDecode(
    File('protocol/fixtures/ava_realtime_canvas_events_v4.json')
        .readAsStringSync(),
  ) as List)
      .map((value) => {
            ...(value as Map).cast<String, Object?>(),
            'generationId': descriptor.generationId,
            'sourceTurnId': descriptor.sourceTurnId,
            'sceneId': descriptor.sceneId,
            'sceneRevision': descriptor.sceneRevision,
          })
      .toList(growable: false);
}

Future<void> _activateDocument(
  AvaSvgCanvasController controller,
  AvaValidatedSvgCanvasDocument document,
) async {
  final events = _events(document);
  controller.beginPlaybackClock(document.descriptor.sourceTurnId);
  controller.receiveGenerationStarted({
    'protocolVersion': 4,
    'event': 'canvasGenerationStarted',
    'focusRevision': 0,
    'sessionId': 'session-persistence',
    'sequence': 0,
    'revision': 1,
    'generationId': document.descriptor.generationId,
    'sourceTurnId': document.descriptor.sourceTurnId,
    'operation': 'new',
  });
  await controller.receiveDocumentAvailable(events[0]);
  controller.rendererPrepared();
  controller.receivePresentationCue(events[1]);
  controller.updateRenderedSample(
    turnId: document.descriptor.sourceTurnId,
    renderedSample: 4812,
  );
  controller.firstFramePresented();
  controller.receiveActivated(events[2]);
}

final class _DocumentLoader implements AvaSvgDocumentLoader {
  const _DocumentLoader(this.document, {this.draftBytes});

  final AvaValidatedSvgCanvasDocument document;
  final Uint8List? draftBytes;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) async =>
      document;

  @override
  Future<AvaValidatedSvgCanvasDraft> fetchDraft(
    AvaSvgCanvasDraftDescriptorV4 descriptor,
  ) async {
    final bytes = draftBytes;
    if (bytes == null) throw StateError('This test does not fetch drafts.');
    return AvaValidatedSvgCanvasDraft.verify(
      descriptor: descriptor,
      svgBytes: bytes,
    );
  }

  @override
  void close() {}
}
