import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_controller.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/ui/dashboard/ava_svg_canvas_view.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

const _manifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","bindingId":"select-cloud","elementId":"cloud","revealIds":[],"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Cloud","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';
const _svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"
 data-ava-interaction-mode="select">
<title>Cloud</title><desc>A rain cloud.</desc>
<g id="cloud" data-ava-binding="select-cloud" aria-label="Rain cloud">
<ellipse cx="500" cy="300" rx="180" ry="90" fill="#dff5ff"/>
</g></svg>''';

const _svg2 = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"
 data-ava-interaction-mode="select">
<title>Cloud Stage 2</title><desc>Rain drops falling.</desc>
<g id="cloud" data-ava-binding="select-cloud" aria-label="Rain cloud">
<ellipse cx="500" cy="300" rx="200" ry="100" fill="#dff5ff"/>
<circle cx="500" cy="450" r="12" fill="#3080ff"/>
</g></svg>''';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
      'native renderer settles progressive drafts 1 and 2, preparation, presentation, and activation',
      (tester) async {
    final document = _document();
    final draft1 = _draft(document);
    final draft2 = _draft2(document);
    final presentedDrafts = <AvaValidatedSvgCanvasDraft>[];
    final candidatePrepared = Completer<void>();
    final candidatePresented = Completer<void>();
    final commands = <AvaSvgCanvasClientCommand>[];
    final errors = <String>[];
    AvaValidatedSvgCanvasDraft? currentDraft = draft1;
    AvaValidatedSvgCanvasDocument? currentCandidate;
    AvaValidatedSvgCanvasDocument? currentActive;
    AvaSvgCanvasController? canvasController;
    var presentCandidate = false;
    var interactionsEnabled = false;
    late StateSetter updateRenderer;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) {
              updateRenderer = setState;
              return AvaSvgCanvasView(
                activeDocument: currentActive,
                draft: currentDraft,
                candidate: currentCandidate,
                presentCandidate: presentCandidate,
                interactionsEnabled: interactionsEnabled,
                onDraftPresented: (presented) {
                  presentedDrafts.add(presented);
                },
                onCandidatePrepared: (_) {
                  canvasController!.rendererPrepared();
                  if (!candidatePrepared.isCompleted) {
                    candidatePrepared.complete();
                  }
                },
                onCandidateFirstFramePresented: (_) {
                  canvasController!.firstFramePresented();
                  if (!candidatePresented.isCompleted) {
                    candidatePresented.complete();
                  }
                },
                onBinding: (_) {},
                onError: (message, {generationId}) {
                  errors.add('$generationId:$message');
                },
              );
            },
          ),
        ),
      ),
    );

    // 1. First draft presentation
    await _pumpUntil(
      tester,
      () => presentedDrafts.isNotEmpty || errors.isNotEmpty,
    );
    expect(errors, isEmpty);
    expect(presentedDrafts, hasLength(1));
    expect(presentedDrafts.first.descriptor.draftIndex, 1);

    // 2. Progressive replacement: draft 1 -> draft 2
    updateRenderer(() {
      currentDraft = draft2;
    });
    await tester.pump();
    await _pumpUntil(
      tester,
      () => presentedDrafts.length >= 2 || errors.isNotEmpty,
    );
    expect(errors, isEmpty);
    expect(presentedDrafts, hasLength(2));
    expect(presentedDrafts.last.descriptor.draftIndex, 2);

    // 3. Controller setup and candidate preparation
    final controller = AvaSvgCanvasController(
      documents: _Loader(document),
      commandSink: commands.add,
    );
    canvasController = controller;
    controller.beginPlaybackClock(document.descriptor.sourceTurnId);
    controller.receiveGenerationStarted(_generationStarted(document));
    final events = _eventsForDocument(document);
    await controller.receiveDocumentAvailable(events[0]);
    expect(
      controller.candidate?.phase,
      AvaSvgCanvasCandidatePhase.rendererPreparing,
    );

    updateRenderer(() {
      currentDraft = null;
      currentCandidate = controller.candidate!.document;
    });
    await tester.pump();
    await _pumpUntil(
      tester,
      () => candidatePrepared.isCompleted || errors.isNotEmpty,
    );

    expect(errors, isEmpty);
    expect(candidatePrepared.isCompleted, isTrue);
    expect(
      controller.candidate?.phase,
      AvaSvgCanvasCandidatePhase.prepared,
    );
    expect(
      commands.single.type,
      AvaSvgCanvasCommandType.canvasDocumentPrepared,
    );

    // 4. Candidate presentation
    controller.receivePresentationCue(events[1]);
    controller.updateRenderedSample(
      turnId: document.descriptor.sourceTurnId,
      renderedSample: 4812,
    );
    expect(controller.shouldPresentCandidate, isTrue);
    updateRenderer(() {
      presentCandidate = true;
    });
    await tester.pump();
    await _pumpUntil(
      tester,
      () => candidatePresented.isCompleted || errors.isNotEmpty,
    );

    expect(errors, isEmpty);
    expect(candidatePresented.isCompleted, isTrue);
    expect(
      controller.candidate?.phase,
      AvaSvgCanvasCandidatePhase.awaitingActivation,
    );
    expect(
      commands.last.type,
      AvaSvgCanvasCommandType.canvasPresented,
    );

    // 5. Candidate activation
    controller.receiveActivated(events[2]);
    expect(controller.candidate, isNull);
    expect(controller.active?.document, same(document));

    updateRenderer(() {
      currentActive = controller.active?.document;
      currentCandidate = null;
      presentCandidate = false;
      interactionsEnabled = true;
    });
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(errors, isEmpty);
    await tester.pumpWidget(const SizedBox.shrink());
    controller.dispose();
  });

  testWidgets('native renderer reports an invalid draft instead of hanging', (
    tester,
  ) async {
    final document = _document();
    final draft = _draftWithBytes(
      document,
      Uint8List.fromList(utf8.encode('<svg')),
    );
    final presented = <AvaValidatedSvgCanvasDraft>[];
    final errors = <String>[];

    await tester.pumpWidget(
      MaterialApp(
        home: AvaSvgCanvasView(
          activeDocument: null,
          draft: draft,
          candidate: null,
          presentCandidate: false,
          interactionsEnabled: false,
          onDraftPresented: presented.add,
          onCandidatePrepared: (_) {},
          onCandidateFirstFramePresented: (_) {},
          onBinding: (_) {},
          onError: (message, {generationId}) {
            errors.add('$generationId:$message');
          },
        ),
      ),
    );

    await _pumpUntil(tester, () => errors.isNotEmpty);
    expect(presented, isEmpty);
    expect(errors, hasLength(1));
    expect(errors.single, contains('draftReplacementFailed'));
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('renderer operation deadline becomes an explicit failure', (
    tester,
  ) async {
    final document = _document();
    final presented = <AvaValidatedSvgCanvasDraft>[];
    final errors = <String>[];

    await tester.pumpWidget(
      MaterialApp(
        home: AvaSvgCanvasView(
          activeDocument: null,
          draft: _draft(document),
          candidate: null,
          presentCandidate: false,
          interactionsEnabled: false,
          rendererOperationTimeout: Duration.zero,
          onDraftPresented: presented.add,
          onCandidatePrepared: (_) {},
          onCandidateFirstFramePresented: (_) {},
          onBinding: (_) {},
          onError: (message, {generationId}) {
            errors.add('$generationId:$message');
          },
        ),
      ),
    );

    await _pumpUntil(tester, () => errors.isNotEmpty);
    expect(presented, isEmpty);
    expect(errors, hasLength(1));
    expect(errors.single, contains('did not settle in time'));
    await tester.pump(const Duration(milliseconds: 250));
    expect(errors, hasLength(1));
    await tester.pumpWidget(const SizedBox.shrink());
  });
}

Future<void> _pumpUntil(
  WidgetTester tester,
  bool Function() predicate,
) async {
  for (var attempt = 0; attempt < 160 && !predicate(); attempt += 1) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

AvaValidatedSvgCanvasDocument _document() {
  final svgBytes = Uint8List.fromList(utf8.encode(_svg));
  final manifestBytes = Uint8List.fromList(utf8.encode(_manifestJson));
  final descriptor = AvaSvgCanvasDocumentDescriptorV4(
    generationId: 'generation-native-renderer',
    sourceTurnId: 'turn-native-renderer',
    sceneId: 'scene-native-renderer',
    sceneRevision: 1,
    svgPath: '/canvas-documents/generation-native-renderer/svg',
    manifestPath: '/canvas-documents/generation-native-renderer/manifest',
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

AvaValidatedSvgCanvasDraft _draft(
  AvaValidatedSvgCanvasDocument document,
) =>
    _draftWithBytes(document, Uint8List.fromList(document.svgBytes));

AvaValidatedSvgCanvasDraft _draft2(
  AvaValidatedSvgCanvasDocument document,
) =>
    _draftWithIndexAndBytes(
      document,
      2,
      Uint8List.fromList(utf8.encode(_svg2)),
    );

AvaValidatedSvgCanvasDraft _draftWithBytes(
  AvaValidatedSvgCanvasDocument document,
  Uint8List bytes,
) =>
    _draftWithIndexAndBytes(document, 1, bytes);

AvaValidatedSvgCanvasDraft _draftWithIndexAndBytes(
  AvaValidatedSvgCanvasDocument document,
  int draftIndex,
  Uint8List bytes,
) {
  return AvaValidatedSvgCanvasDraft.verify(
    descriptor: AvaSvgCanvasDraftDescriptorV4(
      sessionId: 'session-native-renderer',
      generationId: document.descriptor.generationId,
      sourceTurnId: document.descriptor.sourceTurnId,
      draftIndex: draftIndex,
      svgPath:
          '/canvas-drafts/${document.descriptor.generationId}/$draftIndex/svg',
      svgSha256: sha256.convert(bytes).toString(),
      svgByteLength: bytes.length,
    ),
    svgBytes: bytes,
  );
}

Map<String, Object?> _generationStarted(
  AvaValidatedSvgCanvasDocument document,
) =>
    {
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': 'session-native-renderer',
      'sequence': 0,
      'revision': 1,
      'generationId': document.descriptor.generationId,
      'sourceTurnId': document.descriptor.sourceTurnId,
    };

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
      'sessionId': 'session-native-renderer',
      'sequence': 1,
      'revision': 2,
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
      'sessionId': 'session-native-renderer',
      'sequence': 2,
      'revision': 3,
      ...identity,
      'presentationSampleOffset': 4800,
    },
    {
      'protocolVersion': 4,
      'event': 'canvasActivated',
      'sessionId': 'session-native-renderer',
      'sequence': 3,
      'revision': 4,
      ...identity,
      'firstRenderedSample': 4812,
    },
  ];
}

final class _Loader implements AvaSvgDocumentLoader {
  const _Loader(this.document);

  final AvaValidatedSvgCanvasDocument document;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) async =>
      document;

  @override
  Future<AvaValidatedSvgCanvasDraft> fetchDraft(
    AvaSvgCanvasDraftDescriptorV4 descriptor,
  ) =>
      throw StateError('Unexpected draft fetch.');

  @override
  void close() {}
}
