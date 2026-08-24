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
  return controller;
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
