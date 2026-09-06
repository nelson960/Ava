import 'dart:convert';
import 'dart:io';

import 'package:ava/canvas/ava_svg_canvas_controller.dart';
import 'package:ava/canvas/ava_svg_canvas_session_host.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_canvas_save_service.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/ui/dashboard/ava_svg_canvas_panel.dart';
import 'package:crypto/crypto.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

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

AvaValidatedSvgCanvasDocument _sampleDocument({
  String title = 'Water Cycle ☁️',
}) {
  final svgContent =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"><title>$title</title></svg>';
  final svgBytes = Uint8List.fromList(utf8.encode(svgContent));
  final manifestJson = jsonEncode({
    'background': '#dff5ff',
    'bindings': [
      {
        'accessibilityLabel': 'Rain cloud',
        'bindingId': 'select-cloud',
        'elementId': 'cloud',
        'revealIds': <String>[],
        'targetId': 'cloud',
      }
    ],
    'description': 'A cloud turns water vapour into rain.',
    'interactionMode': 'select',
    'manifestVersion': 'ava-svg-manifest-v2',
    'rendererCapabilityVersion': 'ava-svg-renderer-v2',
    'sourceTrust': 'modelGeneratedData',
    'title': title,
    'validatorVersion': 'ava-svg-validator-v2',
    'viewBox': [0.0, 0.0, 1000.0, 600.0],
  });
  final manifestBytes = Uint8List.fromList(utf8.encode(manifestJson));

  final descriptor = AvaSvgCanvasDocumentDescriptorV4.restored(
    generationId: 'gen-1',
    sourceTurnId: 'turn-1',
    sceneId: 'scene-1',
    sceneRevision: 1,
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

class _FakeDocumentLoader implements AvaSvgDocumentLoader {
  const _FakeDocumentLoader(this.document);
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

class _TestPersistence implements AvaCanvasPersistence {
  const _TestPersistence(this.documents, this.focusedSceneId);
  final List<AvaValidatedSvgCanvasDocument> documents;
  final String? focusedSceneId;
  @override
  Future<AvaRestoredCanvasWorkspace> loadWorkspace() async =>
      AvaRestoredCanvasWorkspace(
        documents: documents,
        focusedSceneId: focusedSceneId,
      );
  @override
  Future<void> persistActive(AvaValidatedSvgCanvasDocument document, {bool focused = true}) async {}
  @override
  Future<void> persistFocus(String sceneId) async {}
  @override
  Future<void> reset() async {}
}

void main() {
  setUpAll(() {
    InAppWebViewPlatform.instance = _TestInAppWebViewPlatform();
  });

  group('IoAvaCanvasSaveService Forensic & Semantic Tests', () {
    late Directory tempDir;

    setUp(() async {
      tempDir =
          await Directory.systemTemp.createTemp('ava_save_forensic_test_');
    });

    tearDown(() async {
      if (tempDir.existsSync()) {
        await tempDir.delete(recursive: true);
      }
    });

    test('A & B: picker returns path and AVA writes exact bytes; file matches',
        () async {
      final doc = _sampleDocument(title: 'Solar System');
      String? pickerFileName;
      Uint8List? pickerBytes;

      final saveService = IoAvaCanvasSaveService(
        saveFilePicker: ({
          String? dialogTitle,
          String? fileName,
          String? initialDirectory,
          FileType type = FileType.any,
          List<String>? allowedExtensions,
          Uint8List? bytes,
          bool lockParentWindow = false,
        }) async {
          pickerFileName = fileName;
          pickerBytes = bytes;
          return '${tempDir.path}/$fileName';
        },
      );

      final success = await saveService.saveSvg(document: doc);

      expect(success, isTrue);
      expect(pickerFileName, 'ava-Solar_System.svg');
      expect(pickerBytes, doc.svgBytes);

      final savedFile = File('${tempDir.path}/$pickerFileName');
      expect(savedFile.existsSync(), isTrue);
      expect(savedFile.lengthSync(), doc.svgBytes.length);
      expect(savedFile.readAsBytesSync(), doc.svgBytes);
    });

    test('B (Pre-written): preserves file when already written by platform',
        () async {
      final doc = _sampleDocument(title: 'Water Cycle');
      final targetPath = '${tempDir.path}/ava-Water_Cycle.svg';

      // Simulate native platform writing bytes during picker flow
      File(targetPath).writeAsBytesSync(doc.svgBytes, flush: true);

      final saveService = IoAvaCanvasSaveService(
        saveFilePicker: ({
          String? dialogTitle,
          String? fileName,
          String? initialDirectory,
          FileType type = FileType.any,
          List<String>? allowedExtensions,
          Uint8List? bytes,
          bool lockParentWindow = false,
        }) async {
          return targetPath;
        },
      );

      final success = await saveService.saveSvg(document: doc);

      expect(success, isTrue);
      final file = File(targetPath);
      expect(file.existsSync(), isTrue);
      expect(file.readAsBytesSync(), doc.svgBytes);
    });

    test('C: user cancellation returns false with no error thrown', () async {
      final doc = _sampleDocument(title: 'Cancelled Diagram');

      final saveService = IoAvaCanvasSaveService(
        saveFilePicker: ({
          String? dialogTitle,
          String? fileName,
          String? initialDirectory,
          FileType type = FileType.any,
          List<String>? allowedExtensions,
          Uint8List? bytes,
          bool lockParentWindow = false,
        }) async {
          return null; // User cancelled the save dialog
        },
      );

      final success = await saveService.saveSvg(document: doc);
      expect(success, isFalse);
    });

    test('D: platform exception (e.g. entitlement failure) returns false safely',
        () async {
      final doc = _sampleDocument(title: 'Entitlement Failure');

      final saveService = IoAvaCanvasSaveService(
        saveFilePicker: ({
          String? dialogTitle,
          String? fileName,
          String? initialDirectory,
          FileType type = FileType.any,
          List<String>? allowedExtensions,
          Uint8List? bytes,
          bool lockParentWindow = false,
        }) async {
          throw PlatformException(
            code: 'ENTITLEMENT_REQUIRED_WRITE',
            message: 'The Read-Write entitlement is required for this action.',
          );
        },
      );

      final success = await saveService.saveSvg(document: doc);
      expect(success, isFalse);
    });

    test('D: non-writable path / verification failure returns false safely',
        () async {
      final doc = _sampleDocument(title: 'Invalid Path');

      final saveService = IoAvaCanvasSaveService(
        saveFilePicker: ({
          String? dialogTitle,
          String? fileName,
          String? initialDirectory,
          FileType type = FileType.any,
          List<String>? allowedExtensions,
          Uint8List? bytes,
          bool lockParentWindow = false,
        }) async {
          // A path in a non-existent directory that cannot be created/written
          return '/non_existent_path_for_test/drawing.svg';
        },
      );

      final success = await saveService.saveSvg(document: doc);
      expect(success, isFalse);
    });

    test(
        'E: filename always ends with .svg and path without extension appends .svg',
        () async {
      final doc = _sampleDocument(title: 'Cloud Types');
      String? requestedFileName;

      final saveService = IoAvaCanvasSaveService(
        saveFilePicker: ({
          String? dialogTitle,
          String? fileName,
          String? initialDirectory,
          FileType type = FileType.any,
          List<String>? allowedExtensions,
          Uint8List? bytes,
          bool lockParentWindow = false,
        }) async {
          requestedFileName = fileName;
          // Return user-typed path without .svg extension
          return '${tempDir.path}/custom_name';
        },
      );

      final success = await saveService.saveSvg(document: doc);

      expect(success, isTrue);
      expect(requestedFileName, endsWith('.svg'));

      // Resulting file must have .svg appended
      final resultingFile = File('${tempDir.path}/custom_name.svg');
      expect(resultingFile.existsSync(), isTrue);
      expect(resultingFile.readAsBytesSync(), doc.svgBytes);
    });

    test('G: sanitizeFilename cleans title, trims, bounds length, and formats extension', () {
      final sanitized = IoAvaCanvasSaveService.sanitizeFilename(
        'Cloud / Storm: <Dangerous> & Café ☁️!',
      );
      expect(sanitized, startsWith('ava-Cloud_Storm_Dangerous_Caf'));
      expect(sanitized, endsWith('.svg'));

      expect(IoAvaCanvasSaveService.sanitizeFilename(''), 'ava-drawing.svg');
      expect(
        IoAvaCanvasSaveService.sanitizeFilename('   '),
        'ava-drawing.svg',
      );
      expect(
        IoAvaCanvasSaveService.sanitizeFilename('A' * 100),
        'ava-${'A' * 64}.svg',
      );
    });
  });

  testWidgets(
      'F: Download remains disabled while Canvas generation is pending',
      (tester) async {
    final doc = _sampleDocument(title: 'Active Canvas');
    final persistence = _TestPersistence([doc], 'scene-1');
    final host = AvaSvgCanvasSessionHost(persistence: persistence);
    await host.restoration;

    final controller = AvaSvgCanvasController(
      documents: _FakeDocumentLoader(doc),
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

    AvaCanvasNavbarButton saveButton() => tester.widget<AvaCanvasNavbarButton>(
          find.ancestor(
            of: find.byTooltip('Save drawing'),
            matching: find.byType(AvaCanvasNavbarButton),
          ),
        );

    // Idle state with active document: Download is enabled
    expect(controller.hasPendingVisual, isFalse);
    expect(saveButton().onPressed, isNotNull);

    // Generation starts: Download becomes disabled
    controller.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'focusRevision': 0,
      'sessionId': 'session-1',
      'sequence': 1,
      'revision': 1,
      'generationId': 'gen-2',
      'sourceTurnId': 'turn-2',
      'operation': 'new',
    });
    await tester.pump();

    expect(controller.hasPendingVisual, isTrue);
    expect(saveButton().onPressed, isNull);

    // Generation rejected/finished: Download becomes re-enabled
    controller.receiveRejected({
      'protocolVersion': 4,
      'event': 'canvasRejected',
      'sessionId': 'session-1',
      'sequence': 2,
      'revision': 1,
      'generationId': 'gen-2',
      'sourceTurnId': 'turn-2',
      'rejectionCode': 'timeout',
      'message': 'That drawing took too long. Try again?',
      'retryable': true,
    });
    await tester.pump();

    expect(controller.hasPendingVisual, isFalse);
    expect(saveButton().onPressed, isNotNull);

    await tester.pumpWidget(const SizedBox.shrink());
    host.dispose();
  });
}
