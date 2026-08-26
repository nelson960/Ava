import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_controller.dart';
import 'package:ava/canvas/ava_svg_canvas_session_host.dart';
import 'package:ava/canvas/ava_svg_canvas_telemetry.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:flutter_test/flutter_test.dart';

const _manifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","action":"select","bindingId":"select-cloud","elementId":"cloud","exclusive":false,"hitTest":"element","pulseIds":[],"revealIds":[],"serverReportable":true,"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","manifestVersion":"ava-svg-manifest-v1","rendererCapabilityVersion":"ava-svg-renderer-v1","sourceTrust":"modelGeneratedData","title":"Café cloud ☁️","validatorVersion":"ava-svg-validator-v1","viewBox":[0.0,0.0,1000.0,600.0]}';

final class _Loader implements AvaSvgDocumentLoader {
  _Loader(this.result);

  final Future<AvaValidatedSvgCanvasDocument> result;
  bool closed = false;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) =>
      result;

  @override
  void close() => closed = true;
}

void main() {
  late List<Map<String, Object?>> serverEvents;
  late AvaValidatedSvgCanvasDocument document;
  late AvaValidatedSvgCanvasDocument replacementDocument;
  late List<Map<String, Object?>> replacementEvents;

  setUpAll(() {
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

  test('turn interruption cancellation preserves active canvas without issue',
      () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = await _activate(document, serverEvents, commands);
    final active = controller.active;

    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': 'session-1234',
      'sequence': 3,
      'revision': 2,
      'generationId': 'generation-interrupted-revision',
      'sourceTurnId': 'turn-interrupted-revision',
    });
    controller.receiveRejected({
      'generationId': 'generation-interrupted-revision',
      'sourceTurnId': 'turn-interrupted-revision',
      'rejectionCode': 'cancelled',
      'message': 'Canvas generation was cancelled by interruption.',
    });

    expect(controller.generation, isNull);
    expect(controller.candidate, isNull);
    expect(controller.active, same(active));
    expect(controller.lastIssue, isNull);
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
      'message': 'Canvas generation was cancelled by interruption.',
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

  test('blank_new_runtime_does_not_erase_retained_canvas', () async {
    final oldController = await _activate(document, serverEvents, []);
    final host = AvaSvgCanvasSessionHost()..attach(oldController);
    host.detach(oldController);
    oldController.dispose();
    final newController = AvaSvgCanvasController(
      documents: _Loader(Future.value(replacementDocument)),
      commandSink: (_) {},
    );

    host.attach(newController);

    expect(host.controller, same(newController));
    expect(newController.active, isNull);
    expect(_documentForDisplay(host), same(document));
    expect(host.panelVisible, isTrue);
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

  test('active renderer failure deactivates locally and reports backend',
      () async {
    final commands = <AvaSvgCanvasClientCommand>[];
    final controller = await _activate(document, serverEvents, commands);

    await controller.rendererFailed('SVG renderer process terminated.');

    expect(controller.active, isNull);
    expect(controller.lastIssue?.code, 'rendererTerminated');
    expect(commands.last.type, AvaSvgCanvasCommandType.canvasRuntimeFailed);
    expect(commands.last.fields['failureCode'], 'rendererTerminated');
    controller.dispose();
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
