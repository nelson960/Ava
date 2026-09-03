import 'dart:convert';
import 'dart:io';

import 'package:ava/canvas/ava_svg_canvas_controller.dart';
import 'package:ava/canvas/ava_svg_canvas_session_host.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_canvas_save_service.dart';
import 'package:ava/services/ava_canvas_share_service.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/ui/dashboard/ava_svg_canvas_panel.dart';
import 'package:ava/ui/dashboard/ava_svg_canvas_view.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

const _manifestJson1 =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","bindingId":"select-cloud","elementId":"cloud","revealIds":[],"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Café cloud 1 ☁️","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';

const _manifestJson2 =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Second cloud","bindingId":"select-cloud-2","elementId":"cloud2","revealIds":[],"targetId":"cloud2"}],"description":"A second cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Café cloud 2 ☁️","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';

const _manifestJson3 =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Third cloud","bindingId":"select-cloud-3","elementId":"cloud3","revealIds":[],"targetId":"cloud3"}],"description":"A third cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Café cloud 3 ☁️","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';

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

void main() {
  setUpAll(() {
    TestWidgetsFlutterBinding.ensureInitialized();
    InAppWebViewPlatform.instance = _TestInAppWebViewPlatform();
  });

  tearDownAll(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      const MethodChannel('plugins.flutter.io/share_plus'),
      null,
    );
  });

  test('startup restore leaves panel closed but semantically focused', () async {
    final doc1 = _document(scene: 1, revision: 1);
    final doc2 = _document(scene: 2, revision: 1);
    final persistence = _FakePersistence([doc1, doc2], 'scene-2');
    final host = AvaSvgCanvasSessionHost(persistence: persistence);

    await host.restoration;

    // 1. startup restore leaves panel closed
    expect(host.panelVisible, isFalse);
    expect(host.panelDismissed, isTrue);

    // 2. startup restore preserves semantic focus
    expect(host.presentedDocument?.descriptor.sceneId, 'scene-2');
    expect(host.currentSceneIndex, 1);
    expect(host.sceneCount, 2);

    host.dispose();
  });

  test('new generation after restore opens panel automatically', () async {
    final doc1 = _document(scene: 1, revision: 1);
    final persistence = _FakePersistence([doc1], 'scene-1');
    final host = AvaSvgCanvasSessionHost(persistence: persistence);
    await host.restoration;

    expect(host.panelVisible, isFalse);

    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(doc1),
      commandSink: (_) {},
    );
    host.attach(controller);

    // New generation starts
    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': 'session-nav',
      'sequence': 0,
      'revision': 1,
      'generationId': 'generation-2-1',
      'sourceTurnId': 'turn-2-1',
      'operation': 'new',
    });

    // 4. new generation after restore opens panel
    expect(host.panelVisible, isTrue);
    expect(host.panelDismissed, isFalse);

    host.dispose();
  });

  test('previous/next navigation preserves workspace order and bound', () async {
    final doc1 = _document(scene: 1, revision: 1, manifestJson: _manifestJson1);
    final doc2 = _document(scene: 2, revision: 1, manifestJson: _manifestJson2);
    final doc3 = _document(scene: 3, revision: 1, manifestJson: _manifestJson3);
    final persistence = _FakePersistence([doc1, doc2, doc3], 'scene-2');
    final host = AvaSvgCanvasSessionHost(persistence: persistence);
    await host.restoration;

    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(doc2),
      commandSink: (_) {},
    );
    host.attach(controller);

    // Initial state: scene 2 focused
    expect(host.currentSceneIndex, 1);
    expect(host.canSelectPrevious, isTrue);
    expect(host.canSelectNext, isTrue);
    expect(host.presentedDocument?.descriptor.sceneId, 'scene-2');

    // 3. previous selects scene 1
    await host.selectPreviousScene();
    expect(host.currentSceneIndex, 0);
    expect(host.presentedDocument?.descriptor.sceneId, 'scene-1');
    expect(host.canSelectPrevious, isFalse);
    expect(host.canSelectNext, isTrue);

    // 5. next bounds clamped at ends
    await host.selectPreviousScene(); // no-op
    expect(host.currentSceneIndex, 0);

    // Navigate to end
    await host.selectNextScene();
    expect(host.currentSceneIndex, 1);
    await host.selectNextScene();
    expect(host.currentSceneIndex, 2);
    expect(host.presentedDocument?.descriptor.sceneId, 'scene-3');
    expect(host.canSelectNext, isFalse);

    await host.selectNextScene(); // no-op
    expect(host.currentSceneIndex, 2);

    host.dispose();
  });

  test('history switching disabled during hasPendingVisual and focus transition',
      () async {
    final doc1 = _document(scene: 1, revision: 1);
    final doc2 = _document(scene: 2, revision: 1);
    final persistence = _FakePersistence([doc1, doc2], 'scene-2');
    final host = AvaSvgCanvasSessionHost(persistence: persistence);
    await host.restoration;

    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(doc2),
      commandSink: (_) {},
    );
    host.attach(controller);

    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': 'session-nav',
      'sequence': 0,
      'revision': 1,
      'generationId': 'generation-pending',
      'sourceTurnId': 'turn-pending',
      'operation': 'new',
    });

    // 7. history switching disabled during controller.hasPendingVisual
    expect(controller.hasPendingVisual, isTrue);
    expect(host.canSelectPrevious, isFalse);
    expect(host.canSelectNext, isFalse);

    host.dispose();
  });

  test('focus transition: ordering, failure recovery, and persistence',
      () async {
    final doc1 = _document(scene: 1, revision: 1);
    final doc2 = _document(scene: 2, revision: 1);
    final persistence = _FakePersistence([doc1, doc2], 'scene-2');
    final host = AvaSvgCanvasSessionHost(persistence: persistence);
    await host.restoration;

    var focusSuccess = true;
    final commands = <String>[];

    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(doc2),
      commandSink: (cmd) {},
    );
    host.attach(
      controller,
      runtimeFocusSink: (target) async {
        commands.add(target.descriptor.sceneId);
        if (!focusSuccess) {
          throw Exception('Focus switch network error');
        }
      },
    );

    // 8. focus transition ordering
    final switchFuture = host.selectPreviousScene();
    expect(host.focusTransitionPending, isTrue);
    expect(host.canSelectPrevious, isFalse);
    await switchFuture;
    expect(host.focusTransitionPending, isFalse);
    expect(host.presentedDocument?.descriptor.sceneId, 'scene-1');
    expect(commands, ['scene-1']);

    // 9. focus transition failure recovery
    focusSuccess = false;
    await expectLater(host.selectNextScene(), throwsA(isA<Exception>()));
    expect(host.focusTransitionPending, isFalse);
    // Preserves prior focused scene on failure
    expect(host.presentedDocument?.descriptor.sceneId, 'scene-1');

    host.dispose();
  });

  test('share service sanitizes filename, writes exact bytes, and cleans up',
      () async {
    final doc = _document(scene: 1, revision: 1);
    final originalBytes = doc.svgBytes;

    // 11. Share service sanitizes title
    final sanitized = IoAvaCanvasShareService.sanitizeFilename(
      'Cloud / Storm: <Dangerous> & Café ☁️!',
    );
    expect(sanitized, startsWith('ava-Cloud_Storm_Dangerous_Caf'));
    expect(sanitized, endsWith('.svg'));

    String? sharedFilePath;
    final shareService = IoAvaCanvasShareService(
      tempDirectoryProvider: () async =>
          Directory.systemTemp.createTempSync('ava_test_share'),
      shareInvoker: (files, {sharePositionOrigin}) async {
        expect(files.length, 1);
        sharedFilePath = files.first.path;
        final sharedBytes = await File(sharedFilePath!).readAsBytes();
        // 12. exact UTF-8 bytes sent
        expect(sharedBytes, originalBytes);
      },
    );

    await shareService.shareSvg(document: doc);

    // 13. temp file cleanup
    expect(sharedFilePath, isNotNull);
    expect(File(sharedFilePath!).existsSync(), isFalse);
  });

  testWidgets('AvaSvgCanvasPanel compact navbar shows title, counter, download, and nav',
      (tester) async {
    final doc1 = _document(scene: 1, revision: 1, manifestJson: _manifestJson1);
    final doc2 = _document(scene: 2, revision: 1, manifestJson: _manifestJson2);
    final persistence = _FakePersistence([doc1, doc2], 'scene-2');
    final host = AvaSvgCanvasSessionHost(persistence: persistence);
    await host.restoration;

    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(doc2),
      commandSink: (_) {},
    );
    host.attach(controller);

    expect(host.presentedDocument, isNotNull);
    expect(host.sceneCount, 2);
    expect(host.presentedDocument?.manifest.title, 'Café cloud 2 ☁️');

    final fakeSave = _FakeCanvasSaveService();

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 600,
            height: 400,
            child: ListenableBuilder(
              listenable: host,
              builder: (context, _) => AvaSvgCanvasPanel(
                host: host,
                saveService: fakeSave,
              ),
            ),
          ),
        ),
      ),
    );

    // Initial state: scene 2 of 2 focused
    expect(find.text('Café cloud 2 ☁️'), findsOneWidget);
    expect(find.text('2 / 2'), findsOneWidget);

    // Verify Column layout hierarchy (header is in SafeArea, view is in Expanded)
    expect(
      find.descendant(
        of: find.byType(AvaSvgCanvasPanel),
        matching: find.byType(Column),
      ),
      findsOneWidget,
    );

    IconButton buttonWithTooltip(String tooltip) => tester.widget<IconButton>(
          find.ancestor(
            of: find.byTooltip(tooltip),
            matching: find.byType(IconButton),
          ),
        );

    final prevButton = buttonWithTooltip('Previous drawing');
    final nextButton = buttonWithTooltip('Next drawing');
    final saveButton = buttonWithTooltip('Save drawing');
    final closeButton = buttonWithTooltip('Close drawing');

    // Visual layout test: header height <= 34px (33px target derived from 44px * 0.75)
    final headerSize = tester.getSize(find.byKey(const ValueKey('ava_canvas_navbar')));
    expect(headerSize.height, lessThanOrEqualTo(34.0));
    expect(headerSize.height, equals(33.0));

    // Typography and iconography assertions
    final titleWidget = tester.widget<Text>(find.text('Café cloud 2 ☁️'));
    expect(titleWidget.style?.fontSize, 15.0);
    expect(titleWidget.style?.fontWeight, FontWeight.w600);
    expect(titleWidget.maxLines, 1);
    expect(titleWidget.overflow, TextOverflow.ellipsis);

    final counterWidget = tester.widget<Text>(find.text('2 / 2'));
    expect(counterWidget.style?.fontSize, 14.0);
    expect(counterWidget.style?.fontWeight, FontWeight.w600);

    final prevIcon = tester.widget<Icon>(find.descendant(
      of: find.byTooltip('Previous drawing'),
      matching: find.byType(Icon),
    ));
    expect(prevIcon.size, 18.0);

    // Share button was removed from navbar; Download, Prev, Next, Close present
    expect(find.byTooltip('Share drawing'), findsNothing);
    expect(find.byTooltip('Save drawing'), findsOneWidget);
    expect(find.byTooltip('Previous drawing'), findsOneWidget);
    expect(find.byTooltip('Next drawing'), findsOneWidget);
    expect(find.byTooltip('Close drawing'), findsOneWidget);

    // Effective button hit region: constraints and rendered size approximately >= 44px
    expect(prevButton.constraints?.minWidth, greaterThanOrEqualTo(44.0));
    expect(prevButton.constraints?.minHeight, greaterThanOrEqualTo(44.0));
    expect(saveButton.constraints?.minWidth, greaterThanOrEqualTo(44.0));
    expect(saveButton.constraints?.minHeight, greaterThanOrEqualTo(44.0));
    expect(closeButton.constraints?.minWidth, greaterThanOrEqualTo(44.0));
    expect(closeButton.constraints?.minHeight, greaterThanOrEqualTo(44.0));

    final prevFinder = find.ancestor(
      of: find.byTooltip('Previous drawing'),
      matching: find.byType(IconButton),
    );
    final prevRenderSize = tester.getSize(prevFinder);
    expect(prevRenderSize.width, greaterThanOrEqualTo(44.0));
    expect(prevRenderSize.height, greaterThanOrEqualTo(44.0));

    final closeFinder = find.ancestor(
      of: find.byTooltip('Close drawing'),
      matching: find.byType(IconButton),
    );
    final closeRenderSize = tester.getSize(closeFinder);
    expect(closeRenderSize.width, greaterThanOrEqualTo(44.0));
    expect(closeRenderSize.height, greaterThanOrEqualTo(44.0));

    expect(prevButton.onPressed, isNotNull);
    expect(nextButton.onPressed, isNull);
    expect(saveButton.onPressed, isNotNull);
    expect(closeButton.onPressed, isNotNull);

    // Save button works
    saveButton.onPressed!();
    await tester.pump();
    expect(fakeSave.savedDocument?.descriptor.sceneId, 'scene-2');

    // Close button tap in extended vertical hit area (y = 38, beyond 33px visual header)
    final closeTopLeft = tester.getTopLeft(closeFinder);
    await tester.tapAt(closeTopLeft + const Offset(20, 38));
    await tester.pump();
    expect(host.panelDismissed, isTrue);

    await tester.pumpWidget(const SizedBox.shrink());
    host.dispose();
  });

  testWidgets(
      'operation new provides clean transient surface and restores prior scene on failure',
      (tester) async {
    final doc1 = _document(scene: 1, revision: 1, manifestJson: _manifestJson1);
    final doc2 = _document(scene: 2, revision: 1, manifestJson: _manifestJson2);
    final persistence = _FakePersistence([doc1, doc2], 'scene-2');
    final host = AvaSvgCanvasSessionHost(persistence: persistence);
    await host.restoration;

    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(doc2),
      commandSink: (_) {},
    );
    host.attach(controller);

    final fakeSave = _FakeCanvasSaveService();

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 600,
            height: 400,
            child: ListenableBuilder(
              listenable: host,
              builder: (context, _) => AvaSvgCanvasPanel(
                host: host,
                saveService: fakeSave,
              ),
            ),
          ),
        ),
      ),
    );

    // Initially doc2 is active
    expect(find.text('Café cloud 2 ☁️'), findsOneWidget);
    expect(find.text('2 / 2'), findsOneWidget);

    IconButton buttonWithTooltip(String tooltip) => tester.widget<IconButton>(
          find.ancestor(
            of: find.byTooltip(tooltip),
            matching: find.byType(IconButton),
          ),
        );

    expect(buttonWithTooltip('Save drawing').onPressed, isNotNull);

    // 1. operation=new starts: transient surface becomes clean
    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': 'session-nav',
      'sequence': 0,
      'revision': 1,
      'generationId': 'generation-3-1',
      'sourceTurnId': 'turn-3-1',
      'operation': 'new',
    });
    await tester.pump();

    // The old scene (Café cloud 2 ☁️) is NOT visually present in the navbar or canvas
    expect(find.text('Café cloud 2 ☁️'), findsNothing);
    expect(find.text('2 / 2'), findsNothing);
    expect(find.text('Drawing…'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    // Persistent renderer remains alive underneath
    expect(find.byType(AvaSvgCanvasView), findsOneWidget);

    // Save action must be disabled during pending generation
    expect(buttonWithTooltip('Save drawing').onPressed, isNull);

    // 2. Failure discards transient generation and reveals prior active scene (doc2)
    controller.receiveRejected({
      'protocolVersion': 4,
      'event': 'canvasRejected',
      'sessionId': 'session-nav',
      'sequence': 1,
      'revision': 1,
      'generationId': 'generation-3-1',
      'sourceTurnId': 'turn-3-1',
      'rejectionCode': 'generationLimitExceeded',
      'message': 'Failed',
      'retryable': true,
    });
    await tester.pump();

    // Prior accepted Canvas is restored
    expect(find.text('Café cloud 2 ☁️'), findsOneWidget);
    expect(find.text('2 / 2'), findsOneWidget);
    expect(find.text('Drawing…'), findsNothing);
    expect(buttonWithTooltip('Save drawing').onPressed, isNotNull);
    expect(host.sceneCount, 2);

    await tester.pumpWidget(const SizedBox.shrink());
    host.dispose();
  });

  testWidgets(
      'operation reviseFocused keeps active document visible during generation',
      (tester) async {
    final doc1 = _document(scene: 1, revision: 1, manifestJson: _manifestJson1);
    final doc2 = _document(scene: 2, revision: 1, manifestJson: _manifestJson2);
    final persistence = _FakePersistence([doc1, doc2], 'scene-2');
    final host = AvaSvgCanvasSessionHost(persistence: persistence);
    await host.restoration;

    final controller = AvaSvgCanvasController(
      documents: _DocumentLoader(doc2),
      commandSink: (_) {},
    );
    host.attach(controller);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 600,
            height: 400,
            child: ListenableBuilder(
              listenable: host,
              builder: (context, _) => AvaSvgCanvasPanel(host: host),
            ),
          ),
        ),
      ),
    );

    // operation=reviseFocused starts: doc2 remains visible underneath
    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': 'session-nav',
      'sequence': 0,
      'revision': 1,
      'generationId': 'generation-2-2',
      'sourceTurnId': 'turn-2-2',
      'operation': 'reviseFocused',
    });
    await tester.pump();

    final canvasView =
        tester.widget<AvaSvgCanvasView>(find.byType(AvaSvgCanvasView));
    expect(canvasView.activeDocument?.descriptor.sceneId, 'scene-2');
    expect(find.text('Café cloud 2 ☁️'), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    host.dispose();
  });

  test('IoAvaCanvasSaveService saves exact bytes and sanitizes filename',
      () async {
    final tempDir = await Directory.systemTemp.createTemp('ava_nav_save_test_');
    try {
      final doc = _document(scene: 1, revision: 1);
      String? capturedFileName;
      Uint8List? capturedBytes;

      final saveService = IoAvaCanvasSaveService(
        saveFilePicker: ({
          String? dialogTitle,
          String? fileName,
          String? initialDirectory,
          dynamic type,
          List<String>? allowedExtensions,
          Uint8List? bytes,
          bool lockParentWindow = false,
        }) async {
          capturedFileName = fileName;
          capturedBytes = bytes;
          return '${tempDir.path}/$fileName';
        },
      );

      final saved = await saveService.saveSvg(document: doc);
      expect(saved, isTrue);
      expect(capturedFileName, startsWith('ava-Caf_cloud_1'));
      expect(capturedFileName, endsWith('.svg'));
      expect(capturedBytes, doc.svgBytes);

      final savedFile = File('${tempDir.path}/$capturedFileName');
      expect(savedFile.existsSync(), isTrue);
      expect(savedFile.readAsBytesSync(), doc.svgBytes);
    } finally {
      if (tempDir.existsSync()) {
        await tempDir.delete(recursive: true);
      }
    }
  });
}

final class _FakePersistence implements AvaCanvasPersistence {
  const _FakePersistence(this.documents, this.focusedSceneId);

  final List<AvaValidatedSvgCanvasDocument> documents;
  final String? focusedSceneId;

  @override
  Future<AvaRestoredCanvasWorkspace> loadWorkspace() async =>
      AvaRestoredCanvasWorkspace(
        documents: documents,
        focusedSceneId: focusedSceneId,
      );

  @override
  Future<void> persistActive(AvaValidatedSvgCanvasDocument document) async {}

  @override
  Future<void> persistFocus(String sceneId) async {}

  @override
  Future<void> reset() async {}
}


final class _FakeCanvasSaveService implements AvaCanvasSaveService {
  AvaValidatedSvgCanvasDocument? savedDocument;

  @override
  Future<bool> saveSvg({
    required AvaValidatedSvgCanvasDocument document,
  }) async {
    savedDocument = document;
    return true;
  }
}

AvaValidatedSvgCanvasDocument _document({
  required int scene,
  required int revision,
  String manifestJson = _manifestJson1,
}) {
  final svgBytes =
      File('protocol/fixtures/ava_svg_document_v4.svg').readAsBytesSync();
  final manifestBytes = Uint8List.fromList(utf8.encode(manifestJson));
  final descriptor = AvaSvgCanvasDocumentDescriptorV4.restored(
    generationId: 'generation-$scene-$revision',
    sourceTurnId: 'turn-$scene-$revision',
    sceneId: 'scene-$scene',
    sceneRevision: revision,
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

final class _DocumentLoader implements AvaSvgDocumentLoader {
  const _DocumentLoader(this.document);

  final AvaValidatedSvgCanvasDocument document;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) async =>
      document;

  @override
  Future<AvaValidatedSvgCanvasDraft> fetchDraft(
    AvaSvgCanvasDraftDescriptorV4 descriptor,
  ) async =>
      throw StateError('Not needed');

  @override
  void close() {}
}
