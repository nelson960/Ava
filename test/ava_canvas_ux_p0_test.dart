import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_controller.dart';
import 'package:ava/canvas/ava_svg_canvas_session_host.dart';
import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_realtime_presentation.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/services/memory_service.dart';
import 'package:ava/state/app_state.dart';
import 'package:ava/ui/dashboard/ava_svg_canvas_panel.dart';
import 'package:ava/ui/dashboard/ava_svg_canvas_view.dart';
import 'package:ava/ui/main_screen.dart';
import 'package:ava/ui/widgets/caption_ticker.dart';
import 'package:crypto/crypto.dart';
import 'package:drift/native.dart';
import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
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
  });

  final AvaValidatedSvgCanvasDocument document;
  final Map<int, Uint8List> draftBytesByIndex;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) async =>
      document;

  @override
  Future<AvaValidatedSvgCanvasDraft> fetchDraft(
    AvaSvgCanvasDraftDescriptorV4 descriptor,
  ) async {
    final bytes = draftBytesByIndex[descriptor.draftIndex];
    if (bytes == null) {
      throw StateError(
        'No deterministic draft fixture for index ${descriptor.draftIndex}.',
      );
    }
    return AvaValidatedSvgCanvasDraft.verify(
      descriptor: descriptor,
      svgBytes: bytes,
    );
  }

  @override
  void close() {}
}

void main() {
  late List<Map<String, Object?>> serverEvents;
  late AvaValidatedSvgCanvasDocument documentA;
  late AvaValidatedSvgCanvasDocument documentB;

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
    documentA = AvaValidatedSvgCanvasDocument.verify(
      descriptor: descriptor,
      svgBytes:
          File('protocol/fixtures/ava_svg_document_v4.svg').readAsBytesSync(),
      manifestBytes: Uint8List.fromList(utf8.encode(_manifestJson)),
    );
    documentB = _documentWithIdentity(
      documentA,
      generationId: 'generation-b',
      sourceTurnId: 'turn-b',
      sceneId: 'scene-b',
      sceneRevision: 1,
    );
  });

  testWidgets(
      '1 & 2. Pending generation with no draft shows intentional generating placeholder above mounted renderer and preserves renderer instance on draft',
      (tester) async {
    final firstBytes = Uint8List.fromList(documentA.svgBytes);
    final controller = AvaSvgCanvasController(
      documents: _DraftLoader(
        document: documentA,
        draftBytesByIndex: {1: firstBytes},
      ),
      commandSink: (_) {},
    );
    final host = AvaSvgCanvasSessionHost()..attach(controller);

    _announceGeneration(controller, documentA);

    expect(host.panelVisible, isTrue);
    expect(controller.hasPendingVisual, isTrue);
    expect(controller.draft, isNull);
    expect(controller.candidate, isNull);

    Widget buildPanel() => MaterialApp(
          home: Scaffold(
            body: SizedBox(
              width: 600,
              height: 400,
              child: AvaSvgCanvasPanel(host: host),
            ),
          ),
        );

    await tester.pumpWidget(buildPanel());

    // 1. Working label is shown at top-left, AvaSvgCanvasView is mounted underneath
    expect(find.text('Drawing…'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.byType(AvaSvgCanvasView), findsOneWidget);
    expect(find.byIcon(CupertinoIcons.xmark_circle_fill), findsOneWidget);

    final viewBefore = tester.widget<AvaSvgCanvasView>(
      find.byType(AvaSvgCanvasView),
    );
    expect(viewBefore.interactionsEnabled, isFalse);

    // Capture the Element and State identity of the persistent renderer
    final viewElementBefore = tester.element(find.byType(AvaSvgCanvasView));
    final viewStateBefore = tester.state(find.byType(AvaSvgCanvasView));

    // Deliver first draft for the same generation
    await controller
        .receiveDraftAvailable(_draftEvent(documentA, 1, firstBytes));
    await tester.pumpWidget(buildPanel());

    // 2. Draft is visible, working label updates to finishing, renderer instance is the SAME
    expect(find.text('Finishing drawing…'), findsOneWidget);
    expect(find.byType(AvaSvgCanvasView), findsOneWidget);

    final viewAfter = tester.widget<AvaSvgCanvasView>(
      find.byType(AvaSvgCanvasView),
    );
    expect(viewAfter.draft?.descriptor.draftIndex, 1);
    expect(viewAfter.interactionsEnabled, isFalse);

    final viewElementAfter = tester.element(find.byType(AvaSvgCanvasView));
    final viewStateAfter = tester.state(find.byType(AvaSvgCanvasView));

    expect(identical(viewElementAfter, viewElementBefore), isTrue);
    expect(identical(viewStateAfter, viewStateBefore), isTrue);

    host.dispose();
    controller.dispose();
  });

  testWidgets(
      '3. User closes Canvas while generation is pending: remains pending, hidden, reopenable',
      (tester) async {
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(documentA)),
      commandSink: (_) {},
    );
    final host = AvaSvgCanvasSessionHost()..attach(controller);

    _announceGeneration(controller, documentA);
    expect(host.panelVisible, isTrue);

    host.dismissPanel();

    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);
    expect(controller.hasPendingVisual, isTrue);

    host.dispose();
    controller.dispose();
  });

  testWidgets(
      '4. Same generation progressive drafts and candidate stages do not reopen dismissed panel',
      (tester) async {
    final firstBytes = Uint8List.fromList(documentA.svgBytes);
    final secondBytes = Uint8List.fromList(documentB.svgBytes);
    final controller = AvaSvgCanvasController(
      documents: _DraftLoader(
        document: documentA,
        draftBytesByIndex: {1: firstBytes, 2: secondBytes},
      ),
      commandSink: (_) {},
    );
    final host = AvaSvgCanvasSessionHost()..attach(controller);

    controller.beginPlaybackClock(documentA.descriptor.sourceTurnId);
    _announceGeneration(controller, documentA);
    host.dismissPanel();
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    // Draft 1 arrives
    await controller
        .receiveDraftAvailable(_draftEvent(documentA, 1, firstBytes));
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    // Draft 2 arrives
    await controller
        .receiveDraftAvailable(_draftEvent(documentA, 2, secondBytes));
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    // Candidate document arrives
    await controller.receiveDocumentAvailable(serverEvents[0]);
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    // Renderer prepared
    controller.rendererPrepared();
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    // Presentation cue
    controller.receivePresentationCue(serverEvents[1]);
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    // Rendered sample clock advances to presentation point
    controller.updateRenderedSample(
      turnId: documentA.descriptor.sourceTurnId,
      renderedSample: 4812,
    );
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    // First frame presented
    controller.firstFramePresented();
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    host.dispose();
    controller.dispose();
  });

  testWidgets(
      '5. Close while generating, finish while hidden, and reopen as ACTIVE',
      (tester) async {
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(documentA)),
      commandSink: (_) {},
    );
    final host = AvaSvgCanvasSessionHost()..attach(controller);

    controller.beginPlaybackClock(documentA.descriptor.sourceTurnId);
    _announceGeneration(controller, documentA);
    expect(host.panelVisible, isTrue);

    // User dismisses while Sol is still generating
    host.dismissPanel();
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    // Generation A proceeds through full lifecycle while hidden
    await controller.receiveDocumentAvailable(serverEvents[0]);
    expect(host.panelVisible, isFalse);
    controller.rendererPrepared();
    expect(host.panelVisible, isFalse);
    controller.receivePresentationCue(serverEvents[1]);
    expect(host.panelVisible, isFalse);
    controller.updateRenderedSample(
      turnId: documentA.descriptor.sourceTurnId,
      renderedSample: 4812,
    );
    expect(host.panelVisible, isFalse);
    controller.firstFramePresented();
    expect(host.panelVisible, isFalse);

    // Activates while hidden
    controller.receiveActivated(serverEvents[2]);
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);
    expect(host.presentedDocument, same(documentA));

    // Child reopens the visual later
    host.reopenPanel();
    expect(host.panelVisible, isTrue);
    expect(host.presentedDocument, same(documentA));

    host.dispose();
    controller.dispose();
  });

  testWidgets(
      '6. Pending A superseded by pending B auto-opens B without prior activation',
      (tester) async {
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([documentA, documentB]),
      commandSink: (_) {},
    );
    host.attach(controller);

    _announceGeneration(controller, documentA);
    expect(host.panelVisible, isTrue);

    // User dismisses generation A while A is still generating
    host.dismissPanel();
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    // WITHOUT activating A: receive genuinely new generationStarted for B
    _announceGeneration(controller, documentB);

    // Generation B owns pending state and automatically opens
    expect(controller.generation?.generationId, documentB.descriptor.generationId);
    expect(host.panelVisible, isTrue);
    expect(host.canReopen, isFalse);

    host.dispose();
    controller.dispose();
  });

  testWidgets(
      '7. Generation fails with no previous active Canvas: pending reopen disappears',
      (tester) async {
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(documentA)),
      commandSink: (_) {},
    );
    final host = AvaSvgCanvasSessionHost()..attach(controller);

    _announceGeneration(controller, documentA);
    host.dismissPanel();
    expect(host.canReopen, isTrue);

    controller.receiveRejected({
      'protocolVersion': 4,
      'event': 'canvasRejected',
      'sessionId': 'session-1234',
      'sequence': 1,
      'revision': 2,
      'generationId': documentA.descriptor.generationId,
      'sourceTurnId': documentA.descriptor.sourceTurnId,
      'rejectionCode': 'provider_timeout',
      'message': 'Sol generation timed out.',
    });

    expect(host.canReopen, isFalse);
    expect(host.panelVisible, isFalse);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 600,
            height: 400,
            child: AvaSvgCanvasPanel(host: host),
          ),
        ),
      ),
    );

    expect(find.byType(AvaSvgCanvasView), findsNothing);
    expect(find.text('Ava is drawing…'), findsNothing);

    host.dispose();
    controller.dispose();
  });

  testWidgets(
      '8. Existing active Canvas + newer generation fails: retains active canvas and remains reopenable',
      (tester) async {
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([documentA, documentB]),
      commandSink: (_) {},
    );
    host.attach(controller);

    await _driveActivation(controller, documentA, serverEvents);
    expect(host.presentedDocument, same(documentA));
    expect(host.panelVisible, isTrue);

    // Announce generation B and dismiss while B is pending
    _announceGeneration(controller, documentB);
    expect(host.panelVisible, isTrue);
    host.dismissPanel();
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    // Generation B fails
    controller.receiveRejected({
      'protocolVersion': 4,
      'event': 'canvasRejected',
      'sessionId': 'session-1234',
      'sequence': 1,
      'revision': 2,
      'generationId': documentB.descriptor.generationId,
      'sourceTurnId': documentB.descriptor.sourceTurnId,
      'rejectionCode': 'provider_error',
      'message': 'Generation B failed.',
    });

    expect(host.presentedDocument, same(documentA));
    expect(host.canReopen, isTrue);

    host.reopenPanel();
    expect(host.panelVisible, isTrue);
    expect(host.presentedDocument, same(documentA));

    host.dispose();
    controller.dispose();
  });

  testWidgets(
      '9. Generation B accepted after dismissed generation A: B automatically opens',
      (tester) async {
    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader.documents([documentA, documentB]),
      commandSink: (_) {},
    );
    host.attach(controller);

    await _driveActivation(controller, documentA, serverEvents);
    expect(host.panelVisible, isTrue);

    host.dismissPanel();
    expect(host.panelVisible, isFalse);
    expect(host.canReopen, isTrue);

    _announceGeneration(controller, documentB);
    expect(host.panelVisible, isTrue);
    expect(host.canReopen, isFalse);

    host.dispose();
    controller.dispose();
  });

  testWidgets(
      '10. Progressive draft is visible but interaction disabled; validated active enables interaction',
      (tester) async {
    final firstBytes = Uint8List.fromList(documentA.svgBytes);
    final controller = AvaSvgCanvasController(
      documents: _DraftLoader(
        document: documentA,
        draftBytesByIndex: {1: firstBytes},
      ),
      commandSink: (_) {},
    );
    final host = AvaSvgCanvasSessionHost()..attach(controller);

    _announceGeneration(controller, documentA);
    await controller
        .receiveDraftAvailable(_draftEvent(documentA, 1, firstBytes));

    Widget buildPanel() => MaterialApp(
          home: Scaffold(
            body: SizedBox(
              width: 600,
              height: 400,
              child: AvaSvgCanvasPanel(host: host),
            ),
          ),
        );

    await tester.pumpWidget(buildPanel());

    final draftView = tester.widget<AvaSvgCanvasView>(
      find.byType(AvaSvgCanvasView),
    );
    expect(draftView.interactionsEnabled, isFalse);

    final draftIgnorePointer = tester.widget<IgnorePointer>(
      find
          .ancestor(
            of: find.byType(AvaSvgCanvasView),
            matching: find.byType(IgnorePointer),
          )
          .first,
    );
    expect(draftIgnorePointer.ignoring, isTrue);

    // Complete activation of document A
    controller.beginPlaybackClock(documentA.descriptor.sourceTurnId);
    await controller.receiveDocumentAvailable(serverEvents[0]);
    controller.rendererPrepared();
    controller.receivePresentationCue(serverEvents[1]);
    controller.updateRenderedSample(
      turnId: documentA.descriptor.sourceTurnId,
      renderedSample: 4812,
    );
    controller.firstFramePresented();
    controller.receiveActivated(serverEvents[2]);

    await tester.pumpWidget(buildPanel());

    final activeView = tester.widget<AvaSvgCanvasView>(
      find.byType(AvaSvgCanvasView),
    );
    expect(activeView.interactionsEnabled, isTrue);

    final activeIgnorePointer = tester.widget<IgnorePointer>(
      find
          .ancestor(
            of: find.byType(AvaSvgCanvasView),
            matching: find.byType(IgnorePointer),
          )
          .first,
    );
    expect(activeIgnorePointer.ignoring, isFalse);

    host.dispose();
    controller.dispose();
  });

  testWidgets(
      '11. Captions position strictly above visible canvas panel and return to bottom when hidden',
      (tester) async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);

    await database.insertOrUpdateProfile(
      StudentProfile(
        id: 'current_child',
        name: 'Test Child',
        dateOfBirth: null,
        captionsEnabled: true,
        mathScore: 0,
        scienceScore: 0,
        lastActive: DateTime.now(),
      ),
    );

    final presentation = AvaRealtimePresentationController();
    presentation.beginTurn(sessionId: 's-1', turnId: 't-1');
    presentation.setPhase(AvaRealtimeTurnPhase.speaking);
    presentation.addSegment(
      0,
      AvaRealtimeTurnSegment.fromJson({
        'id': 'speech',
        'speech': 'Look at this drawing!',
        'delivery': 'neutral',
        'expression': 'neutral',
        'gaze': 'child',
        'motionIntent': 'none',
        'canvasCues': <Object>[],
      }),
    );
    presentation.addAlignmentSpans([
      AvaRealtimeAlignmentSpan.fromJson({
        'segmentId': 'speech',
        'textStart': 0,
        'textEnd': 20,
        'sampleStart': 0,
        'sampleEnd': 24000,
        'phoneme': 'EE',
      }),
    ]);
    presentation.updatePlaybackClock(
      renderedSampleCount: 1000,
      receivedSampleCount: 24000,
    );

    final host = AvaSvgCanvasSessionHost();
    final controller = AvaSvgCanvasController(
      documents: _Loader(Future.value(documentA)),
      commandSink: (_) {},
    );
    host.attach(controller);

    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWithValue(database),
        avaRealtimePresentationProvider.overrideWith((ref) => presentation),
        avaSvgCanvasSessionHostProvider.overrideWith((ref) => host),
      ],
    );
    addTearDown(container.dispose);
    addTearDown(controller.dispose);

    // Initial state: Canvas is hidden.
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: const MaterialApp(
          home: Scaffold(
            body: MainScreen(),
          ),
        ),
      ),
    );
    await tester.runAsync(() async {
      await Future<void>.delayed(const Duration(milliseconds: 50));
    });
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.text('Look at this drawing!'), findsOneWidget);

    final mainScreenSize = tester.getSize(find.byType(MainScreen));
    final expectedDashboardHeight = mainScreenSize.height * 0.5;

    // 1. Verify AnimatedPositioned for CaptionTicker has bottom: 0 when canvas is hidden
    final hiddenCaptionPositioned = tester.widget<AnimatedPositioned>(
      find.ancestor(
        of: find.byType(CaptionTicker),
        matching: find.byType(AnimatedPositioned),
      ),
    );
    expect(hiddenCaptionPositioned.bottom, equals(0.0));

    // 2. Make canvas visible: Announce generation
    _announceGeneration(controller, documentA);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));

    expect(host.panelVisible, isTrue);

    final visibleCaptionPositioned = tester.widget<AnimatedPositioned>(
      find.ancestor(
        of: find.byType(CaptionTicker),
        matching: find.byType(AnimatedPositioned),
      ),
    );
    // When canvas is visible, bottom offset equals expected dashboard height (50% screen height)
    expect(
      visibleCaptionPositioned.bottom,
      moreOrLessEquals(expectedDashboardHeight, epsilon: 0.01),
    );

    // Verify physical layout geometry: bottom of CaptionTicker positioned region <= top of AvaSvgCanvasPanel
    final captionTickerRect = tester.getRect(find.byType(CaptionTicker));
    final canvasPanelRect = tester.getRect(find.byType(AvaSvgCanvasPanel));

    expect(
      captionTickerRect.bottom,
      lessThanOrEqualTo(canvasPanelRect.top + 0.01),
    );
  });
}

void _announceGeneration(
  AvaSvgCanvasController controller,
  AvaValidatedSvgCanvasDocument document,
) {
  controller.receiveGenerationStarted({
    'protocolVersion': 4,
    'event': 'canvasGenerationStarted',
      'focusRevision': 0,
    'sessionId': 'session-1234',
    'sequence': 0,
    'revision': 1,
    'generationId': document.descriptor.generationId,
    'sourceTurnId': document.descriptor.sourceTurnId,
    'operation': 'new',
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
