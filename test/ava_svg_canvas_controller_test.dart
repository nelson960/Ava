import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_controller.dart';
import 'package:ava/canvas/ava_svg_canvas_session_host.dart';
import 'package:ava/canvas/ava_svg_canvas_telemetry.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/ui/dashboard/ava_svg_canvas_panel.dart';
import 'package:ava/ui/dashboard/ava_svg_canvas_view.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

const _manifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","bindingId":"select-cloud","elementId":"cloud","revealIds":[],"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Café cloud ☁️","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';

final class _TestInAppWebViewPlatform extends InAppWebViewPlatform {
  @override
  PlatformInAppWebViewWidget createPlatformInAppWebViewWidget(
    PlatformInAppWebViewWidgetCreationParams params,
  ) =>
      _TestPlatformInAppWebViewWidget(params);
}

final class _TestPlatformInAppWebViewWidget extends PlatformInAppWebViewWidget {
  _TestPlatformInAppWebViewWidget(super.params) : super.implementation();

  @override
  Widget build(BuildContext context) => const SizedBox.expand();

  @override
  T controllerFromPlatform<T>(PlatformInAppWebViewController controller) =>
      controller as T;

  @override
  void dispose() {}
}

final class _Loader implements AvaSvgDocumentLoader {
  _Loader(this.result) : _documentsByGeneration = null;

  _Loader.documents(Iterable<AvaValidatedSvgCanvasDocument> documents)
      : result = null,
        _documentsByGeneration = {
          for (final document in documents)
            document.descriptor.generationId: document,
        };

  final Future<AvaValidatedSvgCanvasDocument>? result;
  final Map<String, AvaValidatedSvgCanvasDocument>? _documentsByGeneration;
  bool closed = false;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) {
    final documents = _documentsByGeneration;
    if (documents == null) return result!;
    final document = documents[descriptor.generationId];
    if (document == null) {
      throw StateError(
        'No deterministic SVG document for ${descriptor.generationId}.',
      );
    }
    return Future.value(document);
  }

  @override
  Future<AvaValidatedSvgCanvasDraft> fetchDraft(
    AvaSvgCanvasDraftDescriptorV4 descriptor,
  ) =>
      throw StateError('Unexpected SVG draft ${descriptor.draftIndex}.');

  @override
  void close() => closed = true;
}

final class _DraftLoader implements AvaSvgDocumentLoader {
  _DraftLoader({
    required this.document,
    required this.draftBytesByIndex,
    this.draftFetcher,
  });

  final AvaValidatedSvgCanvasDocument document;
  final Map<int, Uint8List> draftBytesByIndex;
  final Future<AvaValidatedSvgCanvasDraft> Function(
    AvaSvgCanvasDraftDescriptorV4 descriptor,
  )? draftFetcher;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) async =>
      document;

  @override
  Future<AvaValidatedSvgCanvasDraft> fetchDraft(
    AvaSvgCanvasDraftDescriptorV4 descriptor,
  ) async {
    final fetcher = draftFetcher;
    if (fetcher != null) return fetcher(descriptor);
    final bytes = draftBytesByIndex[descriptor.draftIndex];
    if (bytes == null) throw StateError('Unexpected draft index.');
    return AvaValidatedSvgCanvasDraft.verify(
      descriptor: descriptor,
      svgBytes: bytes,
    );
  }

  @override
  void close() {}
}

final class _RestoredWorkspacePersistence implements AvaCanvasPersistence {
  const _RestoredWorkspacePersistence(this.document);

  final AvaValidatedSvgCanvasDocument document;

  @override
  Future<AvaRestoredCanvasWorkspace> loadWorkspace() async =>
      AvaRestoredCanvasWorkspace(
        documents: [document],
        focusedSceneId: document.descriptor.sceneId,
      );

  @override
  Future<void> persistActive(AvaValidatedSvgCanvasDocument document) async {}

  @override
  Future<void> reset() async {}
}

final class _FailingCanvasPersistence implements AvaCanvasPersistence {
  @override
  Future<AvaRestoredCanvasWorkspace> loadWorkspace() async =>
      const AvaRestoredCanvasWorkspace(documents: [], focusedSceneId: null);

  @override
  Future<void> persistActive(AvaValidatedSvgCanvasDocument document) =>
      Future<void>.error(StateError('deterministic persistence failure'));

  @override
  Future<void> reset() async {}
}

void main() {
  late List<Map<String, Object?>> serverEvents;
  late AvaValidatedSvgCanvasDocument document;
  late AvaValidatedSvgCanvasDocument replacementDocument;
  late List<Map<String, Object?>> replacementEvents;

  setUpAll(() {
    InAppWebViewPlatform.instance = _TestInAppWebViewPlatform();
    serverEvents = (jsonDecode(
      File('protocol/fixtures/ava_realtime_canvas_events_v4.json')
          .readAsStringSync(),
    ) as List)
        .map((value) => (value as Map).cast<String, Object?>())
        .toList(growable: false);
    final descriptor =
        AvaSvgCanvasDocumentDescriptorV4.fromJson(serverEvents[0]);
    document = AvaValidatedSvgCanvasDocument.verify(
      descriptor: descriptor,
      svgBytes:
          File('protocol/fixtures/ava_svg_document_v4.svg').readAsBytesSync(),
      manifestBytes: Uint8List.fromList(utf8.encode(_manifestJson)),
    );
    replacementDocument = _documentWithIdentity(
      document,
      generationId: 'generation-replacement',
      sourceTurnId: 'turn-replacement',
      sceneId: 'scene-replacement',
      sceneRevision: 1,
    );
    replacementEvents = _eventsForDocument(replacementDocument);
  });

  test('preload, cue, first frame, and activation remain distinct', () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(document)),
      commandSink: commands.add,
    );
    controller.beginPlaybackClock(document.descriptor.sourceTurnId);
    _announceGeneration(controller, document);

    await controller.receiveDocumentAvailable(serverEvents[0]);
    expect(
      controller.candidate?.phase,
      AvaSvgCanvasCandidatePhase.rendererPreparing,
    );
    expect(controller.active, isNull);
    expect(controller.shouldPresentCandidate, isFalse);

    controller.rendererPrepared();
    expect(
      commands.single.toEventBody(),
      {
        'event': 'canvasDocumentPrepared',
        'generationId': document.descriptor.generationId,
        'sceneId': document.descriptor.sceneId,
        'sceneRevision': document.descriptor.sceneRevision,
        'svgSha256': document.descriptor.svgSha256,
        'manifestSha256': document.descriptor.manifestSha256,
        'rendererCapabilityVersion': avaSvgRendererCapabilityVersion,
        'validatorVersion': avaSvgValidatorVersion,
      },
    );
    expect(controller.active, isNull);

    controller.receivePresentationCue(serverEvents[1]);
    controller.updateRenderedSample(
      turnId: document.descriptor.sourceTurnId,
      renderedSample: 4799,
    );
    expect(controller.shouldPresentCandidate, isFalse);
    controller.updateRenderedSample(
      turnId: document.descriptor.sourceTurnId,
      renderedSample: 4812,
    );
    expect(controller.shouldPresentCandidate, isTrue);
    expect(controller.active, isNull);

    controller.firstFramePresented();
    expect(
      controller.candidate?.phase,
      AvaSvgCanvasCandidatePhase.awaitingActivation,
    );
    expect(controller.active, isNull);
    expect(commands.last.type, AvaSvgCanvasCommandType.canvasPresented);
    expect(commands.last.fields['firstRenderedSample'], 4812);

    controller.receiveActivated(serverEvents[2]);
    expect(controller.candidate, isNull);
    expect(controller.active?.document, same(document));
    controller.dispose();
  });

  test('production_order_initial_activation_populates_workspace', () async {
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([document]),
      commandSink: (_) {},
    );
    host.attach(controller);

    await _driveActivation(controller, document, serverEvents);

    expect(host.workspaceDocuments, orderedEquals([document]));
    expect(host.presentedDocument, same(document));
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('distinct revision-one scene is admissible after an active scene',
      () async {
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([document, replacementDocument]),
      commandSink: (_) {},
    );
    await _driveActivation(controller, document, serverEvents);

    controller.beginPlaybackClock(replacementDocument.descriptor.sourceTurnId);
    _announceGeneration(controller, replacementDocument);
    await controller.receiveDocumentAvailable(replacementEvents[0]);

    expect(
      controller.candidate?.descriptor.sceneId,
      replacementDocument.descriptor.sceneId,
    );
    expect(
      controller.candidate?.descriptor.sceneRevision,
      replacementDocument.descriptor.sceneRevision,
    );
    controller.dispose();
  });

  test('canvas presentation telemetry retains generation and turn identity',
      () async {
    final telemetry = <AvaSvgCanvasTelemetryEvent>[];
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(document)),
      commandSink: commands.add,
      telemetry: telemetry.add,
    );
    controller.beginPlaybackClock(document.descriptor.sourceTurnId);
    _announceGeneration(controller, document);

    await controller.receiveDocumentAvailable(serverEvents[0]);
    controller.rendererPrepared();
    controller.receivePresentationCue(serverEvents[1]);
    controller.updateRenderedSample(
      turnId: document.descriptor.sourceTurnId,
      renderedSample: 4812,
    );
    controller.firstFramePresented();
    controller.receiveActivated(serverEvents[2]);

    expect(
      telemetry.map((event) => event.stage),
      containsAllInOrder([
        'canvas.generation.started_received',
        'canvas.document.available_received',
        'canvas.renderer.prepare_started',
        'canvas.renderer.prepared',
        'canvas.client.document_prepared_enqueued',
        'canvas.presentation.cue_received',
        'canvas.presentation.requested',
        'canvas.presentation.first_frame',
        'canvas.client.presented_enqueued',
        'canvas.activation.completed',
      ]),
    );
    for (final event in telemetry) {
      expect(event.fields['generationId'], document.descriptor.generationId);
      expect(event.fields['sourceTurnId'], document.descriptor.sourceTurnId);
      expect(event.stageDuration.isNegative, isFalse);
    }
    final firstFrame = telemetry.firstWhere(
      (event) => event.stage == 'canvas.presentation.first_frame',
    );
    expect(firstFrame.fields['firstRenderedSample'], 4812);
    expect(firstFrame.fields['presentationSampleOffset'], 4800);
    controller.dispose();
  });

  test('accepted generation is visible before a document exists', () {
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(document)),
      commandSink: (_) {},
    );
    final host = AvaSvgCanvasSessionHost()..attach(controller);

    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': 'session-1234',
      'sequence': 0,
      'revision': 1,
      'generationId': document.descriptor.generationId,
      'sourceTurnId': document.descriptor.sourceTurnId,
    });

    expect(host.generating, isTrue);
    expect(controller.candidate, isNull);
    controller.receiveRejected({
      'generationId': document.descriptor.generationId,
      'rejectionCode': 'cancelled',
      'message': 'The drawing was cancelled.',
    });
    expect(host.generating, isFalse);
    expect(controller.lastIssue, isNull);
    host.dispose();
    controller.dispose();
  });

  test('newer complete draft snapshot replaces rather than accumulates',
      () async {
    final firstBytes = Uint8List.fromList(document.svgBytes);
    final secondBytes = Uint8List.fromList(replacementDocument.svgBytes);
    final controller = AvaSvgCanvasController(
      documents: _DraftLoader(
        document: document,
        draftBytesByIndex: {1: firstBytes, 2: secondBytes},
      ),
      commandSink: (_) {},
    );
    final host = AvaSvgCanvasSessionHost()..attach(controller);
    _announceGeneration(controller, document);
    final firstEvent = _draftEvent(document, 1, firstBytes);
    final secondEvent = _draftEvent(document, 2, secondBytes);

    await controller.receiveDraftAvailable(firstEvent);
    controller.draftPresented(controller.draft!.document);
    expect(controller.lifecyclePhase, AvaSvgCanvasLifecyclePhase.draftVisible);
    expect(controller.draft!.document.svgBytes, orderedEquals(firstBytes));

    await controller.receiveDraftAvailable(secondEvent);
    controller.draftPresented(controller.draft!.document);
    expect(controller.draft!.document.descriptor.draftIndex, 2);
    expect(controller.draft!.document.svgBytes, orderedEquals(secondBytes));
    expect(controller.draft!.document.svgBytes.length, secondBytes.length);

    await controller.receiveDraftAvailable(firstEvent);
    expect(controller.draft!.document.descriptor.draftIndex, 2);

    controller.receiveRejected({
      'generationId': document.descriptor.generationId,
      'sourceTurnId': document.descriptor.sourceTurnId,
      'rejectionCode': 'cancelled',
      'message': 'The drawing was cancelled.',
    });
    expect(controller.draft, isNull);
    expect(controller.active, isNull);
    expect(controller.lifecyclePhase, AvaSvgCanvasLifecyclePhase.cancelled);
    expect(host.panelVisible, isFalse);
    host.dispose();
    controller.dispose();
  });

  test('superseded draft fetch cannot replace its successor or active scene',
      () async {
    final oldDraft = Completer<AvaValidatedSvgCanvasDraft>();
    final successor = _documentWithIdentity(
      document,
      generationId: 'generation-draft-successor',
      sourceTurnId: 'turn-draft-successor',
      sceneId: 'scene-draft-successor',
      sceneRevision: 1,
    );
    final oldBytes = Uint8List.fromList(replacementDocument.svgBytes);
    final successorBytes = Uint8List.fromList(document.svgBytes);
    final controller = AvaSvgCanvasController(
      documents: _DraftLoader(
        document: document,
        draftBytesByIndex: const {},
        draftFetcher: (descriptor) {
          if (descriptor.generationId ==
              replacementDocument.descriptor.generationId) {
            return oldDraft.future;
          }
          return Future.value(
            AvaValidatedSvgCanvasDraft.verify(
              descriptor: descriptor,
              svgBytes: successorBytes,
            ),
          );
        },
      ),
      commandSink: (_) {},
    );
    await _driveActivation(controller, document, serverEvents);
    final active = controller.active;

    _announceGeneration(controller, replacementDocument);
    final oldEvent = _draftEvent(replacementDocument, 1, oldBytes);
    final oldLoading = controller.receiveDraftAvailable(oldEvent);
    _announceGeneration(controller, successor);
    final successorEvent = _draftEvent(successor, 1, successorBytes);
    await controller.receiveDraftAvailable(successorEvent);
    controller.draftPresented(controller.draft!.document);

    oldDraft.complete(
      AvaValidatedSvgCanvasDraft.verify(
        descriptor: AvaSvgCanvasDraftDescriptorV4.fromJson(oldEvent),
        svgBytes: oldBytes,
      ),
    );
    await oldLoading;
    await controller.receiveDraftAvailable(oldEvent);
    controller.receiveRejected({
      'generationId': replacementDocument.descriptor.generationId,
      'sourceTurnId': replacementDocument.descriptor.sourceTurnId,
      'rejectionCode': 'staticValidationFailed',
      'message': 'This obsolete generation failed after supersession.',
    });
    await controller.rendererFailed(
      'A stale renderer operation failed.',
      generationId: replacementDocument.descriptor.generationId,
    );

    expect(controller.active, same(active));
    expect(
      controller.draft!.document.descriptor.generationId,
      successor.descriptor.generationId,
    );
    expect(controller.draft!.document.descriptor.draftIndex, 1);
    expect(controller.lifecyclePhase, AvaSvgCanvasLifecyclePhase.draftVisible);
    expect(controller.lastIssue, isNull);

    controller.receiveRejected({
      'generationId': successor.descriptor.generationId,
      'sourceTurnId': successor.descriptor.sourceTurnId,
      'rejectionCode': 'cancelled',
      'message': 'The successor drawing was cancelled.',
    });
    expect(controller.active, same(active));
    expect(controller.draft, isNull);
    expect(controller.lifecyclePhase, AvaSvgCanvasLifecyclePhase.cancelled);
    controller.dispose();
  });

  test('draft transitions through trusted candidate before activation',
      () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final draftBytes = Uint8List.fromList(document.svgBytes);
    final controller = AvaSvgCanvasController(
      documents: _DraftLoader(
        document: document,
        draftBytesByIndex: {1: draftBytes},
      ),
      commandSink: commands.add,
    );
    controller.beginPlaybackClock(document.descriptor.sourceTurnId);
    _announceGeneration(controller, document);
    await controller
        .receiveDraftAvailable(_draftEvent(document, 1, draftBytes));
    controller.draftPresented(controller.draft!.document);
    expect(controller.lifecyclePhase, AvaSvgCanvasLifecyclePhase.draftVisible);

    final candidateLoading =
        controller.receiveDocumentAvailable(serverEvents[0]);
    expect(
      controller.lifecyclePhase,
      AvaSvgCanvasLifecyclePhase.validatedCandidate,
    );
    await candidateLoading;
    expect(
      controller.lifecyclePhase,
      AvaSvgCanvasLifecyclePhase.preparing,
    );
    controller.rendererPrepared();
    expect(commands.last.type, AvaSvgCanvasCommandType.canvasDocumentPrepared);
    expect(controller.lifecyclePhase, AvaSvgCanvasLifecyclePhase.preparing);

    controller.receivePresentationCue(serverEvents[1]);
    controller.updateRenderedSample(
      turnId: document.descriptor.sourceTurnId,
      renderedSample: serverEvents[2]['firstRenderedSample']! as int,
    );
    expect(
      controller.lifecyclePhase,
      AvaSvgCanvasLifecyclePhase.presentationScheduled,
    );
    controller.firstFramePresented();
    expect(controller.draft, isNull);
    expect(controller.lifecyclePhase, AvaSvgCanvasLifecyclePhase.presented);
    controller.reportBinding(document.manifest.bindings['select-cloud']!);
    expect(
      commands.where(
        (command) => command.type == AvaSvgCanvasCommandType.canvasInteraction,
      ),
      isEmpty,
    );

    controller.receiveActivated(serverEvents[2]);
    expect(controller.lifecyclePhase, AvaSvgCanvasLifecyclePhase.active);
    controller.reportBinding(document.manifest.bindings['select-cloud']!);
    expect(commands.last.type, AvaSvgCanvasCommandType.canvasInteraction);
    controller.dispose();
  });

  test('late draft fetch cannot cover a final candidate being presented',
      () async {
    final draftBytes = Uint8List.fromList(document.svgBytes);
    final delayedDraft = Completer<AvaValidatedSvgCanvasDraft>();
    final controller = AvaSvgCanvasController(
      documents: _DraftLoader(
        document: document,
        draftBytesByIndex: const {},
        draftFetcher: (_) => delayedDraft.future,
      ),
      commandSink: (_) {},
    );
    controller.beginPlaybackClock(document.descriptor.sourceTurnId);
    _announceGeneration(controller, document);
    final draftEvent = _draftEvent(document, 1, draftBytes);
    final draftLoading = controller.receiveDraftAvailable(draftEvent);
    await controller.receiveDocumentAvailable(serverEvents[0]);
    controller.rendererPrepared();
    controller.receivePresentationCue(serverEvents[1]);
    controller.updateRenderedSample(
      turnId: document.descriptor.sourceTurnId,
      renderedSample: serverEvents[2]['firstRenderedSample']! as int,
    );

    delayedDraft.complete(
      AvaValidatedSvgCanvasDraft.verify(
        descriptor: AvaSvgCanvasDraftDescriptorV4.fromJson(draftEvent),
        svgBytes: draftBytes,
      ),
    );
    await draftLoading;

    expect(controller.draft, isNull);
    expect(
      controller.lifecyclePhase,
      AvaSvgCanvasLifecyclePhase.presentationScheduled,
    );
    controller.firstFramePresented();
    controller.receiveActivated(serverEvents[2]);
    expect(controller.lifecyclePhase, AvaSvgCanvasLifecyclePhase.active);
    controller.dispose();
  });

  test('draft renderer failure clears pending work and preserves active scene',
      () async {
    final draftBytes = Uint8List.fromList(replacementDocument.svgBytes);
    final controller = AvaSvgCanvasController(
      documents: _DraftLoader(
        document: document,
        draftBytesByIndex: {1: draftBytes},
      ),
      commandSink: (_) {},
    );
    await _driveActivation(controller, document, serverEvents);
    final active = controller.active;
    _announceGeneration(controller, replacementDocument);
    await controller.receiveDraftAvailable(
      _draftEvent(replacementDocument, 1, draftBytes),
    );
    controller.draftPresented(controller.draft!.document);

    await controller.rendererFailed(
      'SVG renderer rejected the draft.',
      generationId: replacementDocument.descriptor.generationId,
    );

    expect(controller.active, same(active));
    expect(controller.generation, isNull);
    expect(controller.draft, isNull);
    expect(controller.lifecyclePhase, AvaSvgCanvasLifecyclePhase.failed);
    expect(controller.lastIssue?.code, 'rendererRejected');
    controller.dispose();
  });

  test('semantic canvas cancellation preserves active canvas without issue',
      () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = await _activate(document, serverEvents, commands);
    final active = controller.active;
    commands.clear();

    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': 'session-1234',
      'sequence': 3,
      'revision': 2,
      'generationId': 'generation-interrupted-revision',
      'sourceTurnId': 'turn-interrupted-revision',
    });
    controller.reportBinding(document.manifest.bindings['select-cloud']!);
    expect(commands, isEmpty);
    controller.receiveRejected({
      'generationId': 'generation-interrupted-revision',
      'sourceTurnId': 'turn-interrupted-revision',
      'rejectionCode': 'cancelled',
      'message': 'The pending canvas was cancelled semantically.',
    });

    expect(controller.generation, isNull);
    expect(controller.candidate, isNull);
    expect(controller.active, same(active));
    expect(controller.lastIssue, isNull);
    controller.reportBinding(document.manifest.bindings['select-cloud']!);
    expect(commands.single.type, AvaSvgCanvasCommandType.canvasInteraction);
    controller.dispose();
  });

  test('provider and validation rejections remain user-visible issues', () {
    for (final code in const [
      'providerUnavailable',
      'providerInvalid',
      'staticValidationFailed',
      'preparationFailed',
    ]) {
      final controller = AvaSvgCanvasController(
        documents: _Loader(Future.value(document)),
        commandSink: (_) {},
      );
      _announceGeneration(controller, document);
      controller.receiveRejected({
        'generationId': document.descriptor.generationId,
        'sourceTurnId': document.descriptor.sourceTurnId,
        'rejectionCode': code,
        'message': 'The optional visual could not be prepared.',
      });

      expect(controller.lastIssue?.code, code);
      controller.dispose();
    }
  });

  test('cancelled generation cannot later present or activate', () {
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(document)),
      commandSink: commands.add,
    );
    controller.beginPlaybackClock(document.descriptor.sourceTurnId);
    _announceGeneration(controller, document);
    controller.receiveRejected({
      'generationId': document.descriptor.generationId,
      'sourceTurnId': document.descriptor.sourceTurnId,
      'rejectionCode': 'cancelled',
      'message': 'The pending canvas was cancelled semantically.',
    });

    expect(
      () => controller.receivePresentationCue(serverEvents[1]),
      throwsStateError,
    );
    expect(
      () => controller.receiveActivated(serverEvents[2]),
      throwsStateError,
    );
    expect(controller.active, isNull);
    expect(commands, isEmpty);
    expect(controller.lastIssue, isNull);
    controller.dispose();
  });

  test('document cannot bypass accepted generation identity', () async {
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(document)),
      commandSink: (_) {},
    );

    await expectLater(
      controller.receiveDocumentAvailable(serverEvents[0]),
      throwsStateError,
    );
    expect(controller.candidate, isNull);
    controller.dispose();
  });

  test('interaction reports only canonical binding and content identity',
      () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = await _activate(document, serverEvents, commands);

    controller.reportBinding(
      document.manifest.bindings['select-cloud']!,
    );

    final payload = commands.last.toEventBody();
    expect(payload['event'], 'canvasInteraction');
    expect(payload['bindingId'], 'select-cloud');
    expect(payload['interactionRevision'], 1);
    expect(payload['svgSha256'], document.descriptor.svgSha256);
    expect(payload, isNot(contains('label')));
    expect(payload, isNot(contains('value')));
    controller.dispose();
  });

  test('explore A then A emits two semantic interactions', () async {
    final commands = await _interactionCommands(
      mode: AvaSvgInteractionMode.explore,
      declaredBindings: const ['A'],
      selections: const ['A', 'A'],
    );

    expect(commands, hasLength(2));
    expect(
      commands.map((command) => command.fields['bindingId']),
      orderedEquals(['binding-A', 'binding-A']),
    );
    expect(
      commands.map((command) => command.fields['interactionRevision']),
      orderedEquals([1, 2]),
    );
  });

  test('explore A then B then A emits three semantic interactions', () async {
    final commands = await _interactionCommands(
      mode: AvaSvgInteractionMode.explore,
      declaredBindings: const ['A', 'B'],
      selections: const ['A', 'B', 'A'],
    );

    expect(
      commands.map((command) => command.fields['bindingId']),
      orderedEquals(['binding-A', 'binding-B', 'binding-A']),
    );
    expect(
      commands.map((command) => command.fields['interactionRevision']),
      orderedEquals([1, 2, 3]),
    );
  });

  test('select A then A emits one semantic interaction', () async {
    final commands = await _interactionCommands(
      mode: AvaSvgInteractionMode.select,
      declaredBindings: const ['A'],
      selections: const ['A', 'A'],
    );

    expect(commands, hasLength(1));
    expect(commands.single.fields['interactionRevision'], 1);
  });

  test('none mode has no interactive bindings', () async {
    final interactionDocument = _interactionDocument(
      mode: AvaSvgInteractionMode.none,
      bindingNames: const [],
    );
    expect(interactionDocument.manifest.bindings, isEmpty);
  });

  test('steps callbacks remain progressive semantic interactions', () async {
    final commands = await _interactionCommands(
      mode: AvaSvgInteractionMode.steps,
      declaredBindings: const ['A', 'B'],
      selections: const ['A', 'A'],
    );

    expect(commands, hasLength(2));
    expect(
      commands.map((command) => command.fields['interactionRevision']),
      orderedEquals([1, 2]),
    );
  });

  test('rapid genuine A then B then C emits every selection', () async {
    final commands = await _interactionCommands(
      mode: AvaSvgInteractionMode.select,
      declaredBindings: const ['A', 'B', 'C'],
      selections: const ['A', 'B', 'C'],
    );

    expect(
      commands.map((command) => command.fields['bindingId']),
      orderedEquals(['binding-A', 'binding-B', 'binding-C']),
    );
    expect(
      commands.map((command) => command.fields['interactionRevision']),
      orderedEquals([1, 2, 3]),
    );
  });

  test('dismissed active drawing can be reopened from the session host',
      () async {
    final controller = await _activate(document, serverEvents, []);
    final host = AvaSvgCanvasSessionHost()..attach(controller);

    expect(host.panelVisible, isTrue);
    host.dismissPanel();
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);
    host.reopenPanel();
    expect(host.panelVisible, isTrue);

    host.dispose();
    controller.dispose();
  });

  test('active_canvas_survives_runtime_detach', () async {
    final controller = await _activate(document, serverEvents, []);
    final host = AvaSvgCanvasSessionHost()..attach(controller);

    expect(_documentForDisplay(host), same(document));
    expect(host.panelVisible, isTrue);

    host.detach(controller);
    controller.dispose();

    expect(host.controller, isNull);
    expect(_documentForDisplay(host), same(document));
    expect(host.panelVisible, isTrue);
    expect(host.canReopen, isFalse);
    host.dispose();
  });

  test('dismissed_active_canvas_remains_reopenable_after_detach', () async {
    final controller = await _activate(document, serverEvents, []);
    final host = AvaSvgCanvasSessionHost()..attach(controller);

    host.dismissPanel();
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    host.detach(controller);
    controller.dispose();

    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);
    host.reopenPanel();
    expect(host.panelVisible, isTrue);
    expect(_documentForDisplay(host), same(document));
    host.dispose();
  });

  test('fresh runtime hydrates retained canvas without lifecycle commands',
      () async {
    final oldController = await _activate(document, serverEvents, []);
    final host = AvaSvgCanvasSessionHost()..attach(oldController);
    host.detach(oldController);
    oldController.dispose();
    final commands = <AvaSvgCanvasClientCommand>[];
    final newController = AvaSvgCanvasController(
      documents: _Loader(Future.value(replacementDocument)),
      commandSink: commands.add,
    );

    host.attach(newController);

    expect(host.controller, same(newController));
    expect(newController.active?.document, same(document));
    expect(_documentForDisplay(host), same(document));
    expect(host.panelVisible, isTrue);
    expect(commands, isEmpty);
    host.detach(newController);
    newController.dispose();
    host.dispose();
  });

  test('failed_new_candidate_does_not_replace_retained_canvas', () async {
    final oldController = await _activate(document, serverEvents, []);
    final host = AvaSvgCanvasSessionHost()..attach(oldController);
    host.detach(oldController);
    oldController.dispose();
    final fetched = Completer<AvaValidatedSvgCanvasDocument>();
    final commands = <AvaSvgCanvasClientCommand>[];
    final newController = AvaSvgCanvasController(
      documents: _Loader(fetched.future),
      commandSink: commands.add,
    );
    host.attach(newController);

    _announceGeneration(newController, replacementDocument);
    expect(host.generating, isTrue);
    expect(_documentForDisplay(host), same(document));
    final loading = newController.receiveDocumentAvailable(
      replacementEvents[0],
    );
    expect(
      newController.candidate?.phase,
      AvaSvgCanvasCandidatePhase.fetching,
    );
    expect(_documentForDisplay(host), same(document));
    fetched.complete(replacementDocument);
    await loading;
    expect(
      newController.candidate?.phase,
      AvaSvgCanvasCandidatePhase.rendererPreparing,
    );
    expect(_documentForDisplay(host), same(document));

    await newController.rendererFailed(
      'SVG renderer rejected the replacement.',
      generationId: replacementDocument.descriptor.generationId,
    );

    expect(newController.candidate, isNull);
    expect(_documentForDisplay(host), same(document));
    expect(host.panelVisible, isTrue);
    expect(commands.last.type, AvaSvgCanvasCommandType.canvasPreparationFailed);
    host.detach(newController);
    newController.dispose();
    host.dispose();
  });

  test('successful_new_activation_atomically_replaces_retained_canvas',
      () async {
    final oldController = await _activate(document, serverEvents, []);
    final host = AvaSvgCanvasSessionHost()..attach(oldController);
    host.detach(oldController);
    oldController.dispose();
    final newController = AvaSvgCanvasController(
      documents: _Loader(Future.value(replacementDocument)),
      commandSink: (_) {},
    );
    final displayedTransitions = <AvaValidatedSvgCanvasDocument?>[];
    var previous = _documentForDisplay(host);
    host.addListener(() {
      final next = _documentForDisplay(host);
      if (!identical(previous, next)) {
        displayedTransitions.add(next);
        previous = next;
      }
    });

    host.attach(newController);
    expect(_documentForDisplay(host), same(document));
    await _driveActivation(
      newController,
      replacementDocument,
      replacementEvents,
    );

    expect(_documentForDisplay(host), same(replacementDocument));
    expect(displayedTransitions, [replacementDocument]);
    host.detach(newController);
    newController.dispose();
    host.dispose();
  });

  test('persistence failure does not invalidate activated in-memory canvas',
      () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final host = AvaSvgCanvasSessionHost(
      persistence: _FailingCanvasPersistence(),
    );
    await host.restoration;
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([document]),
      commandSink: commands.add,
    );
    host.attach(controller);

    await _driveActivation(controller, document, serverEvents);
    await Future<void>.delayed(Duration.zero);

    expect(controller.active?.document, same(document));
    expect(host.presentedDocument, same(document));
    expect(host.workspaceDocuments, [document]);
    expect(commands.map((command) => command.type), [
      AvaSvgCanvasCommandType.canvasDocumentPrepared,
      AvaSvgCanvasCommandType.canvasPresented,
    ]);
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('runtime_local_pending_state_does_not_survive_detach', () async {
    final oldController = await _activate(document, serverEvents, []);
    final host = AvaSvgCanvasSessionHost()..attach(oldController);
    host.detach(oldController);
    oldController.dispose();
    final fetched = Completer<AvaValidatedSvgCanvasDocument>();
    final commands = <AvaSvgCanvasClientCommand>[];
    final pendingController = AvaSvgCanvasController(
      documents: _Loader(fetched.future),
      commandSink: commands.add,
    );
    host.attach(pendingController);
    _announceGeneration(pendingController, replacementDocument);
    final loading = pendingController.receiveDocumentAvailable(
      replacementEvents[0],
    );
    expect(
      pendingController.candidate?.phase,
      AvaSvgCanvasCandidatePhase.fetching,
    );

    host.detach(pendingController);
    pendingController.dispose();
    fetched.complete(replacementDocument);
    await loading;

    expect(host.controller, isNull);
    expect(host.generating, isFalse);
    expect(_documentForDisplay(host), same(document));
    expect(host.panelVisible, isTrue);
    expect(commands, isEmpty);
    host.dispose();
  });

  test('retained_canvas_does_not_emit_through_disposed_runtime', () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = await _activate(document, serverEvents, commands);
    final host = AvaSvgCanvasSessionHost()..attach(controller);
    commands.clear();

    host.detach(controller);
    controller.dispose();
    host.dismissPanel();
    expect(host.canReopen, isTrue);
    host.reopenPanel();

    expect(_documentForDisplay(host), same(document));
    expect(host.controller, isNull);
    expect(commands, isEmpty);
    host.dispose();
  });

  test('distinct_successful_scenes_accumulate', () async {
    final documents = _sceneDocuments(document, const ['A', 'B', 'C']);
    final host = AvaSvgCanvasSessionHost();
    final controller = await _activateWorkspaceDocuments(host, documents);

    expect(_workspaceSceneIds(host), ['scene-A', 'scene-B', 'scene-C']);
    expect(host.sceneCount, 3);
    expect(host.presentedDocument, same(documents[2]));
    expect(
      () => host.workspaceDocuments.add(documents.first),
      throwsUnsupportedError,
    );
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('revision_replaces_existing_scene_without_new_slot', () async {
    final documents = _sceneDocuments(document, const ['A', 'B', 'C']);
    final host = AvaSvgCanvasSessionHost();
    final initialController = await _activateWorkspaceDocuments(
      host,
      documents,
    );
    host.detach(initialController);
    initialController.dispose();
    final revisedA = _sceneDocument(document, 'A', 2);
    final revisionController = AvaSvgCanvasController(
      documents: _Loader.documents([documents[0], revisedA]),
      commandSink: (_) {},
    );
    await _driveActivation(
      revisionController,
      documents[0],
      _eventsForDocument(documents[0]),
    );
    host.attach(revisionController);

    await _driveActivation(
      revisionController,
      revisedA,
      _eventsForDocument(revisedA),
    );

    expect(_workspaceSceneRevisions(host), [2, 1, 1]);
    expect(_workspaceSceneIds(host), ['scene-A', 'scene-B', 'scene-C']);
    expect(host.sceneCount, 3);
    expect(host.presentedDocument, same(revisedA));
    host.detach(revisionController);
    revisionController.dispose();
    host.dispose();
  });

  test('revision_does_not_refresh_fifo_order', () async {
    final documents = _sceneDocuments(
      document,
      const ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J'],
    );
    final host = AvaSvgCanvasSessionHost();
    final initialController = await _activateWorkspaceDocuments(
      host,
      documents,
    );
    host.detach(initialController);
    initialController.dispose();
    final revisedA = _sceneDocument(document, 'A', 2);
    final sceneK = _sceneDocument(document, 'K', 1);
    final revisionController = AvaSvgCanvasController(
      documents: _Loader.documents([documents[0], revisedA, sceneK]),
      commandSink: (_) {},
    );
    await _driveActivation(
      revisionController,
      documents[0],
      _eventsForDocument(documents[0]),
    );
    host.attach(revisionController);
    await _driveActivation(
      revisionController,
      revisedA,
      _eventsForDocument(revisedA),
    );

    await _driveActivation(
      revisionController,
      sceneK,
      _eventsForDocument(sceneK),
    );

    expect(
      _workspaceSceneIds(host),
      const [
        'scene-B',
        'scene-C',
        'scene-D',
        'scene-E',
        'scene-F',
        'scene-G',
        'scene-H',
        'scene-I',
        'scene-J',
        'scene-K',
      ],
    );
    expect(host.sceneCount, 10);
    expect(host.presentedDocument, same(sceneK));
    host.detach(revisionController);
    revisionController.dispose();
    host.dispose();
  });

  test('eleventh_successful_scene_evicts_oldest', () async {
    final documents = _sceneDocuments(
      document,
      const ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K'],
    );
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents(documents),
      commandSink: (_) {},
    );
    host.attach(controller);
    for (final canvas in documents.take(10)) {
      await _driveActivation(controller, canvas, _eventsForDocument(canvas));
    }
    expect(host.sceneCount, 10);

    await _driveActivation(
      controller,
      documents[10],
      _eventsForDocument(documents[10]),
    );

    expect(host.sceneCount, 10);
    expect(
      _workspaceSceneIds(host),
      const [
        'scene-B',
        'scene-C',
        'scene-D',
        'scene-E',
        'scene-F',
        'scene-G',
        'scene-H',
        'scene-I',
        'scene-J',
        'scene-K',
      ],
    );
    expect(host.presentedDocument, same(documents[10]));
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('failed_eleventh_candidate_does_not_evict', () async {
    final documents = _sceneDocuments(
      document,
      const ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K'],
    );
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents(documents),
      commandSink: (_) {},
    );
    host.attach(controller);
    for (final canvas in documents.take(10)) {
      await _driveActivation(controller, canvas, _eventsForDocument(canvas));
    }
    final retainedBefore = host.workspaceDocuments;
    final presentedBefore = host.presentedDocument;
    final failed = documents[10];
    controller.beginPlaybackClock(failed.descriptor.sourceTurnId);
    _announceGeneration(controller, failed);
    await controller.receiveDocumentAvailable(_eventsForDocument(failed)[0]);

    await controller.rendererFailed(
      'SVG renderer rejected the candidate.',
      generationId: failed.descriptor.generationId,
    );

    expect(host.workspaceDocuments, orderedEquals(retainedBefore));
    expect(host.presentedDocument, same(presentedBefore));
    expect(host.sceneCount, 10);
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('pending_candidate_never_enters_workspace', () async {
    final documents = _sceneDocuments(document, const ['A', 'B']);
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents(documents),
      commandSink: (_) {},
    );
    host.attach(controller);
    await _driveActivation(
      controller,
      documents[0],
      _eventsForDocument(documents[0]),
    );

    final candidate = documents[1];
    final events = _eventsForDocument(candidate);
    controller.beginPlaybackClock(candidate.descriptor.sourceTurnId);
    _announceGeneration(controller, candidate);
    await controller.receiveDocumentAvailable(events[0]);
    controller.rendererPrepared();
    controller.receivePresentationCue(events[1]);
    controller.updateRenderedSample(
      turnId: candidate.descriptor.sourceTurnId,
      renderedSample: 4812,
    );
    controller.firstFramePresented();

    expect(
      controller.candidate?.phase,
      AvaSvgCanvasCandidatePhase.awaitingActivation,
    );
    expect(host.workspaceDocuments, orderedEquals([documents[0]]));
    expect(host.presentedDocument, same(documents[0]));
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('runtime_detach_preserves_entire_workspace', () async {
    final documents = _sceneDocuments(document, const ['A', 'B', 'C']);
    final host = AvaSvgCanvasSessionHost();
    final controller = await _activateWorkspaceDocuments(host, documents);
    host.dismissPanel();

    host.detach(controller);
    controller.dispose();

    expect(host.workspaceDocuments, orderedEquals(documents));
    expect(host.presentedDocument, same(documents[2]));
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);
    host.reopenPanel();
    expect(host.panelVisible, isTrue);
    host.dispose();
  });

  test('blank_new_runtime_preserves_workspace', () async {
    final documents = _sceneDocuments(document, const ['A', 'B', 'C']);
    final host = AvaSvgCanvasSessionHost();
    final oldController = await _activateWorkspaceDocuments(host, documents);
    host.detach(oldController);
    oldController.dispose();
    final blankController = AvaSvgCanvasController(
      documents: _Loader.documents(const []),
      commandSink: (_) {},
    );

    host.attach(blankController);

    expect(host.workspaceDocuments, orderedEquals(documents));
    expect(host.presentedDocument, same(documents[2]));
    expect(host.sceneCount, 3);
    host.detach(blankController);
    blankController.dispose();
    host.dispose();
  });

  test('fresh_controller_accepts_retained_scene_revision', () async {
    final sceneA = _sceneDocument(document, 'A', 1);
    final revisedA = _sceneDocument(document, 'A', 2);
    final host = AvaSvgCanvasSessionHost();
    final firstController = AvaSvgCanvasController(
      documents: _Loader.documents([sceneA]),
      commandSink: (_) {},
      retainedSceneRevision: host.retainedSceneRevision,
    );
    host.attach(firstController);
    await _driveActivation(
      firstController,
      sceneA,
      _eventsForDocument(sceneA),
    );
    host.detach(firstController);
    firstController.dispose();

    final replacementController = AvaSvgCanvasController(
      documents: _Loader.documents([revisedA]),
      commandSink: (_) {},
      retainedSceneRevision: host.retainedSceneRevision,
    );
    host.attach(replacementController);
    await _driveActivation(
      replacementController,
      revisedA,
      _eventsForDocument(revisedA),
    );

    expect(host.sceneCount, 1);
    expect(host.workspaceDocuments, orderedEquals([revisedA]));
    expect(host.presentedDocument, same(revisedA));
    host.detach(replacementController);
    replacementController.dispose();
    host.dispose();
  });

  test('fresh_controller_rejects_unseen_revision_two', () async {
    final unseenRevision = _sceneDocument(document, 'X', 2);
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([unseenRevision]),
      commandSink: (_) {},
      retainedSceneRevision: host.retainedSceneRevision,
    );
    host.attach(controller);
    _announceGeneration(controller, unseenRevision);

    await expectLater(
      controller
          .receiveDocumentAvailable(_eventsForDocument(unseenRevision)[0]),
      throwsFormatException,
    );

    expect(controller.candidate, isNull);
    expect(host.sceneCount, 0);
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('retained_revision_must_be_monotonic', () async {
    final sceneA = _sceneDocument(document, 'A', 1);
    final revisedA = _sceneDocument(document, 'A', 2);
    final host = AvaSvgCanvasSessionHost();
    final owner = AvaSvgCanvasController(
      documents: _Loader.documents([sceneA, revisedA]),
      commandSink: (_) {},
      retainedSceneRevision: host.retainedSceneRevision,
    );
    host.attach(owner);
    await _driveActivation(owner, sceneA, _eventsForDocument(sceneA));
    await _driveActivation(owner, revisedA, _eventsForDocument(revisedA));
    host.detach(owner);
    owner.dispose();

    for (final revision in [1, 2, 4]) {
      final rejected = _sceneDocument(document, 'A', revision);
      final controller = AvaSvgCanvasController(
        documents: _Loader.documents([rejected]),
        commandSink: (_) {},
        retainedSceneRevision: host.retainedSceneRevision,
      );
      host.attach(controller);
      _announceGeneration(controller, rejected);
      await expectLater(
        controller.receiveDocumentAvailable(_eventsForDocument(rejected)[0]),
        throwsFormatException,
      );
      expect(controller.candidate, isNull);
      host.detach(controller);
      controller.dispose();
    }

    final revisionThree = _sceneDocument(document, 'A', 3);
    final accepted = AvaSvgCanvasController(
      documents: _Loader.documents([revisionThree]),
      commandSink: (_) {},
      retainedSceneRevision: host.retainedSceneRevision,
    );
    host.attach(accepted);
    _announceGeneration(accepted, revisionThree);
    await accepted
        .receiveDocumentAvailable(_eventsForDocument(revisionThree)[0]);

    expect(accepted.candidate?.document, same(revisionThree));
    host.detach(accepted);
    accepted.dispose();
    host.dispose();
  });

  test('exact_repeated_active_observation_is_idempotent', () async {
    final documents = _sceneDocuments(
      document,
      const ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J'],
    );
    final host = AvaSvgCanvasSessionHost();
    final controller = await _activateWorkspaceDocuments(host, documents);
    final retainedBefore = host.workspaceDocuments;
    final presentedBefore = host.presentedDocument;
    var presentedTransitions = 0;
    host.addListener(() {
      if (!identical(host.presentedDocument, presentedBefore)) {
        presentedTransitions += 1;
      }
    });
    final pending = _sceneDocument(document, 'K', 1);

    _announceGeneration(controller, pending);
    controller.receiveRejected({
      'generationId': pending.descriptor.generationId,
      'sourceTurnId': pending.descriptor.sourceTurnId,
      'rejectionCode': 'cancelled',
      'message': 'The optional candidate was cancelled.',
    });

    expect(host.workspaceDocuments, orderedEquals(retainedBefore));
    expect(host.presentedDocument, same(presentedBefore));
    expect(host.sceneCount, 10);
    expect(presentedTransitions, 0);
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('invalid_new_scene_revision_is_rejected', () async {
    final sceneA = _sceneDocument(document, 'A', 1);
    final invalidB = _sceneDocument(document, 'B', 2);
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([sceneA, invalidB]),
      commandSink: (_) {},
    );
    host.attach(controller);
    await _driveActivation(controller, sceneA, _eventsForDocument(sceneA));
    controller.beginPlaybackClock(invalidB.descriptor.sourceTurnId);
    _announceGeneration(controller, invalidB);

    await expectLater(
      controller.receiveDocumentAvailable(_eventsForDocument(invalidB)[0]),
      throwsFormatException,
    );

    expect(host.workspaceDocuments, orderedEquals([sceneA]));
    expect(host.presentedDocument, same(sceneA));
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('stale_existing_scene_revision_is_not_committed', () async {
    final sceneA = _sceneDocument(document, 'A', 1);
    final sceneB = _sceneDocument(document, 'B', 1);
    final revisedA = _sceneDocument(document, 'A', 2);
    final host = AvaSvgCanvasSessionHost();
    final initialController = await _activateWorkspaceDocuments(
      host,
      [sceneA, sceneB],
    );
    host.detach(initialController);
    initialController.dispose();
    final revisionController = AvaSvgCanvasController(
      documents: _Loader.documents([sceneA, revisedA]),
      commandSink: (_) {},
    );
    await _driveActivation(
      revisionController,
      sceneA,
      _eventsForDocument(sceneA),
    );
    host.attach(revisionController);
    await _driveActivation(
      revisionController,
      revisedA,
      _eventsForDocument(revisedA),
    );
    host.detach(revisionController);
    revisionController.dispose();
    final staleController = AvaSvgCanvasController(
      documents: _Loader.documents([sceneA]),
      commandSink: (_) {},
    );
    await _driveActivation(
      staleController,
      sceneA,
      _eventsForDocument(sceneA),
    );

    expect(() => host.attach(staleController), throwsStateError);
    expect(_workspaceSceneIds(host), ['scene-A', 'scene-B']);
    expect(_workspaceSceneRevisions(host), [2, 1]);
    expect(host.presentedDocument, same(revisedA));
    expect(host.controller, isNull);
    staleController.dispose();
    host.dispose();
  });

  test('workspace_never_exceeds_ten_scenes', () async {
    final initial = _sceneDocuments(
      document,
      const ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J'],
    );
    final revisedJ = _sceneDocument(document, 'J', 2);
    final additional = _sceneDocuments(document, const ['K', 'L', 'M']);
    final sequence = [...initial, revisedJ, ...additional];
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents(sequence),
      commandSink: (_) {},
    );
    host.attach(controller);

    for (final canvas in sequence) {
      await _driveActivation(controller, canvas, _eventsForDocument(canvas));
      expect(host.sceneCount, lessThanOrEqualTo(10));
    }

    expect(host.sceneCount, 10);
    expect(_workspaceSceneIds(host), isNot(contains('scene-A')));
    expect(_workspaceSceneIds(host), isNot(contains('scene-B')));
    expect(_workspaceSceneIds(host), isNot(contains('scene-C')));
    expect(_workspaceSceneIds(host),
        containsAll(['scene-K', 'scene-L', 'scene-M']));
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('clear_workspace_requires_detached_controller', () async {
    final documents = _sceneDocuments(document, const ['A', 'B', 'C']);
    final host = AvaSvgCanvasSessionHost();
    final controller = await _activateWorkspaceDocuments(host, documents);
    host.dismissPanel();
    final workspaceBefore = host.workspaceDocuments;
    final presentedBefore = host.presentedDocument;

    expect(host.clearWorkspace, throwsStateError);

    expect(host.workspaceDocuments, orderedEquals(workspaceBefore));
    expect(host.presentedDocument, same(presentedBefore));
    expect(host.controller, same(controller));
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);
    expect(host.panelDismissed, isTrue);
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('clear_workspace_after_detach_succeeds', () async {
    final documents = _sceneDocuments(document, const ['A', 'B', 'C']);
    final host = AvaSvgCanvasSessionHost();
    final controller = await _activateWorkspaceDocuments(host, documents);
    host.dismissPanel();
    host.detach(controller);
    var notifications = 0;
    host.addListener(() => notifications += 1);

    host.clearWorkspace();

    expect(host.workspaceDocuments, isEmpty);
    expect(host.sceneCount, 0);
    expect(host.presentedDocument, isNull);
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isFalse);
    expect(host.panelDismissed, isFalse);
    expect(notifications, 1);
    controller.dispose();
    host.dispose();
  });

  test('discard ignores a late fetch result and never presents it', () async {
    final completer = Completer<AvaValidatedSvgCanvasDocument>();
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = AvaSvgCanvasController(
      documents: _Loader(completer.future),
      commandSink: commands.add,
    );
    _announceGeneration(controller, document);
    final loading = controller.receiveDocumentAvailable(serverEvents[0]);
    expect(controller.candidate?.phase, AvaSvgCanvasCandidatePhase.fetching);

    controller.receiveDocumentDiscarded({
      ...serverEvents[3],
      'generationId': document.descriptor.generationId,
    });
    completer.complete(document);
    await loading;

    expect(controller.candidate, isNull);
    expect(controller.active, isNull);
    expect(commands, isEmpty);
    controller.dispose();
  });

  test('failed content fetch emits explicit preparation failure', () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.error(const FormatException('hash mismatch'))),
      commandSink: commands.add,
    );
    _announceGeneration(controller, document);

    await controller.receiveDocumentAvailable(serverEvents[0]);

    expect(controller.candidate, isNull);
    expect(controller.lastIssue?.code, 'contentIdentityMismatch');
    expect(
      commands.single.type,
      AvaSvgCanvasCommandType.canvasPreparationFailed,
    );
    expect(commands.single.fields['failureCode'], 'contentIdentityMismatch');
    controller.dispose();
  });

  test('active_renderer_failure_preserves_flutter_committed_scene', () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([document]),
      commandSink: commands.add,
    );
    host.attach(controller);
    await _driveActivation(controller, document, serverEvents);
    commands.clear();
    final activeBefore = controller.active;
    expect(controller.activeRendererHealthy, isTrue);

    await controller.rendererFailed('SVG renderer process terminated.');
    await controller.rendererFailed('SVG renderer process terminated.');

    expect(controller.active, same(activeBefore));
    expect(controller.active?.document, same(document));
    expect(controller.activeRendererHealthy, isFalse);
    expect(host.workspaceDocuments, orderedEquals([document]));
    expect(host.presentedDocument, same(document));
    expect(host.sceneCount, 1);
    expect(controller.lastIssue?.code, 'rendererTerminated');
    expect(commands, hasLength(1));
    expect(commands.single.type, AvaSvgCanvasCommandType.canvasRuntimeFailed);
    expect(commands.single.fields['failureCode'], 'rendererTerminated');
    controller.reportBinding(document.manifest.bindings['select-cloud']!);
    expect(commands, hasLength(1));
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('revision_after_renderer_failure_remains_valid_in_flutter', () async {
    final revised = _documentWithIdentity(
      document,
      generationId: 'generation-revision-after-renderer-failure',
      sourceTurnId: 'turn-revision-after-renderer-failure',
      sceneId: document.descriptor.sceneId,
      sceneRevision: document.descriptor.sceneRevision + 1,
    );
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([document, revised]),
      commandSink: (_) {},
    );
    host.attach(controller);
    await _driveActivation(controller, document, serverEvents);
    await controller.rendererFailed('SVG renderer process terminated.');

    await _driveActivation(
      controller,
      revised,
      _eventsForDocument(revised),
    );

    expect(controller.active?.document, same(revised));
    expect(controller.active?.document.descriptor.sceneId,
        document.descriptor.sceneId);
    expect(controller.active?.document.descriptor.sceneRevision, 2);
    expect(controller.activeRendererHealthy, isTrue);
    expect(host.workspaceDocuments, orderedEquals([revised]));
    expect(host.presentedDocument, same(revised));
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('candidate_renderer_failure_remains_non_committing_in_flutter',
      () async {
    final candidate = _sceneDocument(document, 'B', 1);
    final commands = <AvaSvgCanvasClientCommand>[];
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([document, candidate]),
      commandSink: commands.add,
    );
    host.attach(controller);
    await _driveActivation(controller, document, serverEvents);
    final activeBefore = controller.active;
    final workspaceBefore = host.workspaceDocuments;
    controller.beginPlaybackClock(candidate.descriptor.sourceTurnId);
    _announceGeneration(controller, candidate);
    await controller.receiveDocumentAvailable(_eventsForDocument(candidate)[0]);

    await controller.rendererFailed(
      'SVG renderer rejected the candidate.',
      generationId: candidate.descriptor.generationId,
    );

    expect(controller.candidate, isNull);
    expect(controller.active, same(activeBefore));
    expect(host.workspaceDocuments, orderedEquals(workspaceBefore));
    expect(host.presentedDocument, same(document));
    expect(commands.last.type, AvaSvgCanvasCommandType.canvasPreparationFailed);
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  testWidgets(
      'detached_presented_canvas_panel_is_renderable_without_controller',
      (tester) async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([document]),
      commandSink: commands.add,
    );
    host.attach(controller);
    await _driveActivation(controller, document, serverEvents);
    host.detach(controller);
    controller.dispose();
    commands.clear();

    await tester.pumpWidget(
      MaterialApp(
        home: SizedBox(
          width: 600,
          height: 400,
          child: AvaSvgCanvasPanel(host: host),
        ),
      ),
    );

    final view = tester.widget<AvaSvgCanvasView>(
      find.byType(AvaSvgCanvasView),
    );
    expect(view.activeDocument, same(document));
    expect(view.draft, isNull);
    expect(view.candidate, isNull);
    expect(view.interactionsEnabled, isFalse);
    view.onBinding(document.manifest.bindings['select-cloud']!);
    view.onError('Detached renderer diagnostic.');
    await tester.pump();
    expect(commands, isEmpty);
    host.dispose();
  });

  testWidgets('locally restored canvas uses active renderer without ACKs',
      (tester) async {
    final descriptor = document.descriptor;
    final restored = AvaValidatedSvgCanvasDocument.verify(
      descriptor: AvaSvgCanvasDocumentDescriptorV4.restored(
        generationId: descriptor.generationId,
        sourceTurnId: descriptor.sourceTurnId,
        sceneId: descriptor.sceneId,
        sceneRevision: descriptor.sceneRevision,
        svgSha256: descriptor.svgSha256,
        manifestSha256: descriptor.manifestSha256,
        svgByteLength: descriptor.svgByteLength,
        manifestByteLength: descriptor.manifestByteLength,
      ),
      svgBytes: document.svgBytes,
      manifestBytes: document.manifestBytes,
    );
    final host = AvaSvgCanvasSessionHost(
      persistence: _RestoredWorkspacePersistence(restored),
    );
    await host.restoration;
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([restored]),
      commandSink: commands.add,
      retainedSceneRevision: host.retainedSceneRevision,
    );
    host.attach(controller);

    await tester.pumpWidget(
      MaterialApp(
        home: SizedBox(
          width: 600,
          height: 400,
          child: AvaSvgCanvasPanel(host: host),
        ),
      ),
    );

    final view = tester.widget<AvaSvgCanvasView>(find.byType(AvaSvgCanvasView));
    expect(view.activeDocument, same(restored));
    expect(view.draft, isNull);
    expect(view.candidate, isNull);
    expect(view.presentCandidate, isFalse);
    expect(view.interactionsEnabled, isTrue);
    expect(controller.active?.document, same(restored));
    expect(commands, isEmpty);

    view.onBinding(restored.manifest.bindings['select-cloud']!);

    expect(commands, hasLength(1));
    expect(commands.single.type, AvaSvgCanvasCommandType.canvasInteraction);
    expect(commands.single.fields, {
      'sceneId': restored.descriptor.sceneId,
      'sceneRevision': restored.descriptor.sceneRevision,
      'interactionRevision': 1,
      'svgSha256': restored.descriptor.svgSha256,
      'manifestSha256': restored.descriptor.manifestSha256,
      'bindingId': 'select-cloud',
    });
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  testWidgets('draft replacement retains one persistent renderer state',
      (tester) async {
    final firstBytes = Uint8List.fromList(document.svgBytes);
    final secondBytes = Uint8List.fromList(replacementDocument.svgBytes);
    final controller = AvaSvgCanvasController(
      documents: _DraftLoader(
        document: document,
        draftBytesByIndex: {1: firstBytes, 2: secondBytes},
      ),
      commandSink: (_) {},
    );
    final host = AvaSvgCanvasSessionHost()..attach(controller);
    _announceGeneration(controller, document);
    await controller
        .receiveDraftAvailable(_draftEvent(document, 1, firstBytes));

    Widget panel() => MaterialApp(
          home: SizedBox(
            width: 600,
            height: 400,
            child: AvaSvgCanvasPanel(host: host),
          ),
        );

    await tester.pumpWidget(panel());
    final firstState = tester.state(find.byType(AvaSvgCanvasView));
    expect(find.byType(AvaSvgCanvasView), findsOneWidget);

    await controller
        .receiveDraftAvailable(_draftEvent(document, 2, secondBytes));
    await tester.pumpWidget(panel());
    final secondState = tester.state(find.byType(AvaSvgCanvasView));

    expect(secondState, same(firstState));
    expect(find.byType(AvaSvgCanvasView), findsOneWidget);
    host.detach(controller);
    controller.dispose();
    host.dispose();
  });

  test('rendered sample clock cannot move backwards', () {
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(document)),
      commandSink: (_) {},
    );
    controller.beginPlaybackClock('turn-clock');
    controller.updateRenderedSample(
      turnId: 'turn-clock',
      renderedSample: 100,
    );

    expect(
      () => controller.updateRenderedSample(
        turnId: 'turn-clock',
        renderedSample: 99,
      ),
      throwsStateError,
    );
    controller.dispose();
  });

  test('new turn owns a fresh clock while the active canvas survives',
      () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = await _activate(document, serverEvents, commands);
    final active = controller.active;

    controller.beginPlaybackClock('turn-next');
    controller.updateRenderedSample(
      turnId: 'turn-next',
      renderedSample: 0,
    );

    expect(controller.active, same(active));
    expect(
      () => controller.updateRenderedSample(
        turnId: document.descriptor.sourceTurnId,
        renderedSample: 5000,
      ),
      throwsStateError,
    );
    controller.dispose();
  });

  test('source presentation cue cannot advance on the next turn clock',
      () async {
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(document)),
      commandSink: (_) {},
    );
    controller.beginPlaybackClock(document.descriptor.sourceTurnId);
    _announceGeneration(controller, document);
    await controller.receiveDocumentAvailable(serverEvents[0]);
    controller.rendererPrepared();
    controller.beginPlaybackClock('turn-next');

    controller.receivePresentationCue(serverEvents[1]);
    controller.updateRenderedSample(
      turnId: 'turn-next',
      renderedSample: 6000,
    );

    expect(
      controller.candidate?.phase,
      AvaSvgCanvasCandidatePhase.presentationScheduled,
    );
    expect(controller.shouldPresentCandidate, isFalse);
    expect(controller.lastIssue, isNull);
    controller.dispose();
  });

  test('late prepared canvas uses its retained source-turn clock', () async {
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(document)),
      commandSink: (_) {},
    );
    controller.beginPlaybackClock(document.descriptor.sourceTurnId);
    controller.updateRenderedSample(
      turnId: document.descriptor.sourceTurnId,
      renderedSample: 5000,
    );
    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': 'session-1234',
      'sequence': 0,
      'revision': 1,
      'generationId': document.descriptor.generationId,
      'sourceTurnId': document.descriptor.sourceTurnId,
    });
    controller.beginPlaybackClock('turn-next');

    await controller.receiveDocumentAvailable(serverEvents[0]);
    controller.rendererPrepared();
    controller.receivePresentationCue(serverEvents[1]);

    expect(controller.shouldPresentCandidate, isTrue);
    expect(controller.lastIssue, isNull);
    controller.firstFramePresented();
    expect(controller.candidate?.firstRenderedSample, 5000);
    controller.dispose();
  });

  test('interruption replaces an unreachable source-turn cue earlier',
      () async {
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(document)),
      commandSink: (_) {},
    );
    controller.beginPlaybackClock(document.descriptor.sourceTurnId);
    controller.updateRenderedSample(
      turnId: document.descriptor.sourceTurnId,
      renderedSample: 5000,
    );
    _announceGeneration(controller, document);
    await controller.receiveDocumentAvailable(serverEvents[0]);
    controller.rendererPrepared();
    controller.receivePresentationCue({
      ...serverEvents[1],
      'presentationSampleOffset': 6000,
    });
    expect(controller.shouldPresentCandidate, isFalse);

    controller.receivePresentationCue({
      ...serverEvents[1],
      'sequence': (serverEvents[1]['sequence']! as int) + 1,
      'presentationSampleOffset': 5000,
    });

    expect(controller.shouldPresentCandidate, isTrue);
    expect(controller.candidate?.presentationSampleOffset, 5000);
    expect(controller.lastIssue, isNull);
    controller.dispose();
  });

  test('duplicate turn identity cannot redefine the playback clock', () {
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(document)),
      commandSink: (_) {},
    );
    controller.beginPlaybackClock('turn-clock');

    expect(
      () => controller.beginPlaybackClock('turn-clock'),
      throwsStateError,
    );
    controller.dispose();
  });
}

Future<List<AvaSvgCanvasClientCommand>> _interactionCommands({
  required AvaSvgInteractionMode mode,
  required List<String> declaredBindings,
  required List<String> selections,
}) async {
  final interactionDocument = _interactionDocument(
    mode: mode,
    bindingNames: declaredBindings,
  );
  final commands = <AvaSvgCanvasClientCommand>[];
  final controller = await _activate(
    interactionDocument,
    _eventsForDocument(interactionDocument),
    commands,
  );
  commands.clear();
  for (final selection in selections) {
    controller.reportBinding(
      interactionDocument.manifest.bindings['binding-$selection']!,
    );
  }
  controller.dispose();
  return commands;
}

AvaValidatedSvgCanvasDocument _interactionDocument({
  required AvaSvgInteractionMode mode,
  required List<String> bindingNames,
}) {
  final svgText =
      '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">
${bindingNames.map((name) => '<g id="element-$name" data-ava-binding="binding-$name"><text id="reveal-$name">$name</text></g>').join()}
</svg>''';
  final bindings = bindingNames.indexed.map((entry) {
    final (index, name) = entry;
    return <String, Object?>{
      'accessibilityLabel': 'Binding $name',
      'bindingId': 'binding-$name',
      'elementId': 'element-$name',
      'revealIds': mode == AvaSvgInteractionMode.explore
          ? <String>['reveal-$name']
          : <String>[],
      'targetId': 'element-$name',
      if (mode == AvaSvgInteractionMode.steps) 'stepIndex': index + 1,
    };
  }).toList(growable: false);
  final manifestText = jsonEncode({
    'background': '#dff5ff',
    'bindings': bindings,
    'description': 'Deterministic interaction admission fixture.',
    'interactionMode': mode.name,
    'manifestVersion': avaSvgManifestVersion,
    'rendererCapabilityVersion': avaSvgRendererCapabilityVersion,
    'sourceTrust': 'modelGeneratedData',
    'title': 'Interaction admission',
    'validatorVersion': avaSvgValidatorVersion,
    'viewBox': [0.0, 0.0, 1000.0, 600.0],
  });
  final svgBytes = Uint8List.fromList(utf8.encode(svgText));
  final manifestBytes = Uint8List.fromList(utf8.encode(manifestText));
  final descriptor = AvaSvgCanvasDocumentDescriptorV4(
    generationId: 'generation-${mode.name}-interaction',
    sourceTurnId: 'turn-${mode.name}-interaction',
    sceneId: 'scene-${mode.name}-interaction',
    sceneRevision: 1,
    svgPath: '/canvas-documents/${mode.name}/svg',
    manifestPath: '/canvas-documents/${mode.name}/manifest',
    svgSha256: sha256.convert(svgBytes).toString(),
    manifestSha256: sha256.convert(manifestBytes).toString(),
    svgByteLength: svgBytes.length,
    manifestByteLength: manifestBytes.length,
  );
  return AvaValidatedSvgCanvasDocument.verify(
    descriptor: descriptor,
    svgBytes: svgBytes,
    manifestBytes: manifestBytes,
  );
}

Future<AvaSvgCanvasController> _activate(
  AvaValidatedSvgCanvasDocument document,
  List<Map<String, Object?>> events,
  List<AvaSvgCanvasClientCommand> commands,
) async {
  final controller = AvaSvgCanvasController(
    documents: _Loader(Future.value(document)),
    commandSink: commands.add,
  );
  await _driveActivation(controller, document, events);
  return controller;
}

Future<AvaSvgCanvasController> _activateWorkspaceDocuments(
  AvaSvgCanvasSessionHost host,
  List<AvaValidatedSvgCanvasDocument> documents,
) async {
  final controller = AvaSvgCanvasController(
    documents: _Loader.documents(documents),
    commandSink: (_) {},
  );
  host.attach(controller);
  for (final document in documents) {
    await _driveActivation(
      controller,
      document,
      _eventsForDocument(document),
    );
  }
  return controller;
}

Future<void> _driveActivation(
  AvaSvgCanvasController controller,
  AvaValidatedSvgCanvasDocument document,
  List<Map<String, Object?>> events,
) async {
  controller.beginPlaybackClock(document.descriptor.sourceTurnId);
  _announceGeneration(controller, document);
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

void _announceGeneration(
  AvaSvgCanvasController controller,
  AvaValidatedSvgCanvasDocument document,
) {
  controller.receiveGenerationStarted({
    'protocolVersion': 4,
    'event': 'canvasGenerationStarted',
    'sessionId': 'session-1234',
    'sequence': 0,
    'revision': 1,
    'generationId': document.descriptor.generationId,
    'sourceTurnId': document.descriptor.sourceTurnId,
  });
}

Map<String, Object?> _draftEvent(
  AvaValidatedSvgCanvasDocument source,
  int draftIndex,
  Uint8List svgBytes,
) =>
    {
      'protocolVersion': 4,
      'event': 'canvasDraftAvailable',
      'sessionId': 'session-1234',
      'sequence': draftIndex,
      'revision': 2,
      'generationId': source.descriptor.generationId,
      'sourceTurnId': source.descriptor.sourceTurnId,
      'draftIndex': draftIndex,
      'svgPath': '/v4/realtime/sessions/session-1234/canvas-drafts/'
          '${source.descriptor.generationId}/$draftIndex/svg',
      'svgSha256': sha256.convert(svgBytes).toString(),
      'svgByteLength': svgBytes.length,
      'authoritative': false,
      'interactive': false,
      'rendererCapabilityVersion': avaSvgRendererCapabilityVersion,
      'validatorVersion': avaSvgValidatorVersion,
    };

AvaValidatedSvgCanvasDocument? _documentForDisplay(
  AvaSvgCanvasSessionHost host,
) =>
    host.presentedDocument;

AvaValidatedSvgCanvasDocument _documentWithIdentity(
  AvaValidatedSvgCanvasDocument source, {
  required String generationId,
  required String sourceTurnId,
  required String sceneId,
  required int sceneRevision,
}) {
  final descriptor = AvaSvgCanvasDocumentDescriptorV4(
    generationId: generationId,
    sourceTurnId: sourceTurnId,
    sceneId: sceneId,
    sceneRevision: sceneRevision,
    svgPath: '/canvas-documents/$generationId/svg',
    manifestPath: '/canvas-documents/$generationId/manifest',
    svgSha256: source.descriptor.svgSha256,
    manifestSha256: source.descriptor.manifestSha256,
    svgByteLength: source.descriptor.svgByteLength,
    manifestByteLength: source.descriptor.manifestByteLength,
  );
  return AvaValidatedSvgCanvasDocument.verify(
    descriptor: descriptor,
    svgBytes: source.svgBytes,
    manifestBytes: source.manifestBytes,
  );
}

AvaValidatedSvgCanvasDocument _sceneDocument(
  AvaValidatedSvgCanvasDocument source,
  String sceneName,
  int sceneRevision,
) =>
    _documentWithIdentity(
      source,
      generationId: 'generation-$sceneName-r$sceneRevision',
      sourceTurnId: 'turn-$sceneName-r$sceneRevision',
      sceneId: 'scene-$sceneName',
      sceneRevision: sceneRevision,
    );

List<AvaValidatedSvgCanvasDocument> _sceneDocuments(
  AvaValidatedSvgCanvasDocument source,
  List<String> sceneNames,
) =>
    sceneNames
        .map((sceneName) => _sceneDocument(source, sceneName, 1))
        .toList(growable: false);

List<String> _workspaceSceneIds(AvaSvgCanvasSessionHost host) =>
    host.workspaceDocuments
        .map((document) => document.descriptor.sceneId)
        .toList(growable: false);

List<int> _workspaceSceneRevisions(AvaSvgCanvasSessionHost host) =>
    host.workspaceDocuments
        .map((document) => document.descriptor.sceneRevision)
        .toList(growable: false);

List<Map<String, Object?>> _eventsForDocument(
  AvaValidatedSvgCanvasDocument document,
) {
  final descriptor = document.descriptor;
  final identity = <String, Object?>{
    'generationId': descriptor.generationId,
    'sourceTurnId': descriptor.sourceTurnId,
    'sceneId': descriptor.sceneId,
    'sceneRevision': descriptor.sceneRevision,
    'svgSha256': descriptor.svgSha256,
    'manifestSha256': descriptor.manifestSha256,
  };
  return [
    {
      'protocolVersion': 4,
      'event': 'canvasDocumentAvailable',
      'sessionId': 'session-replacement',
      'sequence': 0,
      'revision': 1,
      ...identity,
      'svgPath': descriptor.svgPath,
      'manifestPath': descriptor.manifestPath,
      'svgByteLength': descriptor.svgByteLength,
      'manifestByteLength': descriptor.manifestByteLength,
      'rendererCapabilityVersion': avaSvgRendererCapabilityVersion,
      'validatorVersion': avaSvgValidatorVersion,
      'manifestVersion': avaSvgManifestVersion,
    },
    {
      'protocolVersion': 4,
      'event': 'canvasPresentationCue',
      'sessionId': 'session-replacement',
      'sequence': 1,
      'revision': 2,
      ...identity,
      'presentationSampleOffset': 4800,
    },
    {
      'protocolVersion': 4,
      'event': 'canvasActivated',
      'sessionId': 'session-replacement',
      'sequence': 2,
      'revision': 3,
      ...identity,
      'firstRenderedSample': 4812,
    },
  ];
}
