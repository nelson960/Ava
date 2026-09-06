import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_controller.dart';
import 'package:ava/canvas/ava_svg_canvas_session_host.dart';
import 'package:ava/models/ava_realtime_protocol_v4.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_realtime_presentation.dart';
import 'package:ava/services/ava_realtime_session_v4.dart';
import 'package:ava/services/ava_realtime_turn_runtime.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/services/pcm_speech_output_service.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

const _manifestJson1 =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","bindingId":"select-cloud","elementId":"cloud","revealIds":[],"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Café cloud 1 ☁️","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';

const _manifestJson2 =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Second cloud","bindingId":"select-cloud-2","elementId":"cloud2","revealIds":[],"targetId":"cloud2"}],"description":"A second cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Café cloud 2 ☁️","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';

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
  Widget build(dynamic context) => const SizedBox.expand();

  @override
  T controllerFromPlatform<T>(PlatformInAppWebViewController controller) =>
      controller as T;

  @override
  void dispose() {}
}

final class _FakePersistence implements AvaCanvasPersistence {
  _FakePersistence(this.documents, this.focusedSceneId);

  final List<AvaValidatedSvgCanvasDocument> documents;
  String? focusedSceneId;

  @override
  Future<AvaRestoredCanvasWorkspace> loadWorkspace() async =>
      AvaRestoredCanvasWorkspace(
        documents: documents,
        focusedSceneId: focusedSceneId,
      );

  @override
  Future<void> persistActive(
    AvaValidatedSvgCanvasDocument document, {
    bool focused = true,
  }) async {}

  @override
  Future<void> persistFocus(String sceneId) async {
    focusedSceneId = sceneId;
  }

  @override
  Future<void> reset() async {
    documents.clear();
    focusedSceneId = null;
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

void main() {
  setUpAll(() {
    TestWidgetsFlutterBinding.ensureInitialized();
    InAppWebViewPlatform.instance = _TestInAppWebViewPlatform();
  });

  group('Physical UI presentation control', () {
    test('physical close: synchronous immediate UI hide and async wire event',
        () {
      final doc1 = _document(scene: 1, revision: 1);
      final commands = <AvaSvgCanvasClientCommand>[];
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: commands.add,
      );
      final persistence = _FakePersistence([doc1], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      host.attach(controller);

      // Restore active scene so panel is visible
      controller.restoreRetainedActive(doc1);
      expect(host.panelVisible, isTrue);

      // Child taps close (X)
      host.dismissPanel();

      // UI is hidden immediately
      expect(host.panelVisible, isFalse);
      expect(host.panelDismissed, isTrue);
      expect(host.pendingClientOperationId, isNotNull);

      // Wire command was emitted
      expect(commands.length, 1);
      expect(
        commands.first.type,
        AvaSvgCanvasCommandType.canvasPresentationUpdated,
      );
      expect(commands.first.fields['visible'], isFalse);
      expect(commands.first.fields['clientOperationId'],
          host.pendingClientOperationId);

      host.dispose();
      controller.dispose();
    });

    test(
        'physical close remains hidden even if backend notification drops/fails',
        () {
      final doc1 = _document(scene: 1, revision: 1);
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: (_) => throw Exception('Network down'),
      );
      final persistence = _FakePersistence([doc1], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      host.attach(controller);

      controller.restoreRetainedActive(doc1);
      expect(host.panelVisible, isTrue);

      // Even if network throws, local UI dismiss is immediate and persists (INV-019)
      host.dismissPanel();
      expect(host.panelVisible, isFalse);
      expect(host.panelDismissed, isTrue);

      host.dispose();
      controller.dispose();
    });

    test(
        'physical reopen: selected local scene visible immediately and wire event sent with focus identity',
        () {
      final doc1 = _document(scene: 1, revision: 1);
      final commands = <AvaSvgCanvasClientCommand>[];
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: commands.add,
      );
      final persistence = _FakePersistence([doc1], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      host.attach(controller);

      controller.restoreRetainedActive(doc1);
      host.dismissPanel();
      commands.clear();
      expect(host.panelVisible, isFalse);

      // Child taps reopen chip
      host.reopenPanel();

      // UI shows immediately
      expect(host.panelVisible, isTrue);
      expect(host.panelDismissed, isFalse);

      // Wire event carries scene identity and focus revision
      expect(commands.length, 1);
      expect(
        commands.first.type,
        AvaSvgCanvasCommandType.canvasPresentationUpdated,
      );
      expect(commands.first.fields['visible'], isTrue);
      expect(commands.first.fields['sceneId'], 'scene-1');
      expect(commands.first.fields['focusRevision'], host.focusRevision);

      host.dispose();
      controller.dispose();
    });

    test(
        'physical reopen synchronizes focus before presentation and gates semantic transport',
        () async {
      final doc1 = _document(scene: 1, revision: 1);
      final commands = <AvaSvgCanvasClientCommand>[];
      final focusAccepted = Completer<void>();
      final focusCalls = <String>[];
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: commands.add,
      );
      final host = AvaSvgCanvasSessionHost(
        persistence: _FakePersistence([doc1], 'scene-1'),
      );
      await host.restoration;
      host.attach(
        controller,
        runtimeFocusSink: (document, revision) async {
          focusCalls.add('${document.descriptor.sceneId}:$revision');
          await focusAccepted.future;
        },
      );

      expect(host.panelVisible, isFalse);
      host.reopenPanel();

      expect(host.panelVisible, isTrue);
      expect(focusCalls, ['scene-1:1']);
      expect(commands, isEmpty);
      expect(
        controller.semanticTransportState,
        AvaSvgSemanticTransportState.pending,
      );
      controller.reportBinding(doc1.manifest.bindings.values.first);
      expect(commands, isEmpty);
      expect(controller.lastIssue, isNull);

      focusAccepted.complete();
      await Future<void>.delayed(Duration.zero);
      expect(commands, hasLength(1));
      expect(
        commands.single.type,
        AvaSvgCanvasCommandType.canvasPresentationUpdated,
      );
      expect(commands.single.fields['sceneId'], 'scene-1');
      expect(commands.single.fields['focusRevision'], 1);
      expect(
        controller.semanticTransportState,
        AvaSvgSemanticTransportState.unaccepted,
      );

      controller.receivePresentationChanged({
        'protocolVersion': 4,
        'event': 'canvasPresentationChanged',
        'sessionId': 'session-1',
        'sequence': 1,
        'revision': 1,
        'visible': true,
        'sceneId': 'scene-1',
        'focusRevision': 1,
        'source': 'user',
      });
      expect(controller.semanticTransportState,
          AvaSvgSemanticTransportState.ready);
      expect(host.pendingClientOperationId, isNull);

      commands.clear();
      controller.reportBinding(doc1.manifest.bindings.values.first);
      expect(commands, hasLength(1));
      expect(commands.single.type, AvaSvgCanvasCommandType.canvasInteraction);

      host.dispose();
      controller.dispose();
    });

    test('pre-attach selected scene identity survives into clientReady',
        () async {
      final doc1 = _document(scene: 1, revision: 1);
      final doc2 = _document(
        scene: 2,
        revision: 1,
        manifestJson: _manifestJson2,
      );
      final host = AvaSvgCanvasSessionHost(
        persistence: _FakePersistence([doc1, doc2], 'scene-1'),
      );
      await host.restoration;

      await host.selectSceneByIndex(1);
      host.reopenPanel();
      expect(host.panelVisible, isTrue);
      expect(host.focusRevision, 1);

      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc2),
        commandSink: (_) {},
      );
      final focusCalls = <String>[];
      host.attach(
        controller,
        runtimeFocusSink: (document, revision) async {
          focusCalls.add('${document.descriptor.sceneId}:$revision');
        },
      );
      await Future<void>.delayed(Duration.zero);

      final snapshot = host.presentationSnapshot();
      expect(snapshot.visible, isTrue);
      expect(snapshot.sceneId, 'scene-2');
      expect(snapshot.focusRevision, 1);
      expect(focusCalls, ['scene-2:1']);
      expect(controller.semanticTransportState,
          AvaSvgSemanticTransportState.unaccepted);

      controller.receivePresentationChanged({
        'protocolVersion': 4,
        'event': 'canvasPresentationChanged',
        'sessionId': 'session-1',
        'sequence': 1,
        'revision': 1,
        'visible': true,
        'sceneId': 'scene-2',
        'focusRevision': 1,
        'source': 'restore',
      });
      expect(controller.semanticTransportState,
          AvaSvgSemanticTransportState.ready);

      host.dispose();
      controller.dispose();
    });
  });

  group('Model presentation command handling', () {
    test('model command hide: applies hide and ACKs', () {
      final doc1 = _document(scene: 1, revision: 1);
      final commands = <AvaSvgCanvasClientCommand>[];
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: commands.add,
      );
      final persistence = _FakePersistence([doc1], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      host.attach(controller);

      controller.restoreRetainedActive(doc1);
      expect(host.panelVisible, isTrue);

      // Server sends canvasPresentationCommand(hide)
      controller.receivePresentationCommand({
        'protocolVersion': 4,
        'event': 'canvasPresentationCommand',
        'sessionId': 'session-1',
        'sequence': 1,
        'revision': 1,
        'commandId': 'cmd-hide-1',
        'action': 'hide',
      });

      expect(host.panelVisible, isFalse);
      expect(commands.length, 1);
      expect(
        commands.first.type,
        AvaSvgCanvasCommandType.canvasPresentationAcknowledged,
      );
      expect(commands.first.fields['commandId'], 'cmd-hide-1');
      expect(commands.first.fields['applied'], isTrue);

      host.dispose();
      controller.dispose();
    });

    test(
        'model command show with known scene: selects scene, opens panel, and ACKs with focus identity',
        () async {
      final doc1 =
          _document(scene: 1, revision: 1, manifestJson: _manifestJson1);
      final doc2 =
          _document(scene: 2, revision: 1, manifestJson: _manifestJson2);
      final commands = <AvaSvgCanvasClientCommand>[];
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: commands.add,
      );
      final persistence = _FakePersistence([doc1, doc2], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      await host.restoration;
      host.attach(controller);

      expect(host.panelVisible, isFalse);
      expect(host.pendingClientOperationId, isNull);

      // Server sends canvasPresentationCommand(show scene-2)
      controller.receivePresentationCommand({
        'protocolVersion': 4,
        'event': 'canvasPresentationCommand',
        'sessionId': 'session-1',
        'sequence': 1,
        'revision': 1,
        'commandId': 'cmd-show-2',
        'action': 'show',
        'sceneId': 'scene-2',
      });

      // Pump microtasks to allow async selectSceneByIndex to complete
      await Future<void>.delayed(Duration.zero);

      expect(host.panelVisible, isTrue);
      expect(host.presentedDocument?.descriptor.sceneId, 'scene-2');
      expect(commands.length, 1);
      expect(
        commands.first.type,
        AvaSvgCanvasCommandType.canvasPresentationAcknowledged,
      );
      expect(commands.first.fields['commandId'], 'cmd-show-2');
      expect(commands.first.fields['applied'], isTrue);
      expect(commands.first.fields['sceneId'], 'scene-2');
      expect(commands.first.fields['focusRevision'], host.focusRevision);

      host.dispose();
      controller.dispose();
    });

    test('model command show with unknown scene: rejects with sceneNotFound',
        () {
      final doc1 = _document(scene: 1, revision: 1);
      final commands = <AvaSvgCanvasClientCommand>[];
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: commands.add,
      );
      final persistence = _FakePersistence([doc1], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      host.attach(controller);

      controller.receivePresentationCommand({
        'protocolVersion': 4,
        'event': 'canvasPresentationCommand',
        'sessionId': 'session-1',
        'sequence': 1,
        'revision': 1,
        'commandId': 'cmd-show-unknown',
        'action': 'show',
        'sceneId': 'scene-unknown',
      });

      expect(commands.length, 1);
      expect(
        commands.first.type,
        AvaSvgCanvasCommandType.canvasPresentationAcknowledged,
      );
      expect(commands.first.fields['commandId'], 'cmd-show-unknown');
      expect(commands.first.fields['applied'], isFalse);
      expect(commands.first.fields['reason'], 'sceneNotFound');

      host.dispose();
      controller.dispose();
    });

    test('physical close while model show is pending fences the model command',
        () {
      final doc1 = _document(scene: 1, revision: 1);
      final commands = <AvaSvgCanvasClientCommand>[];
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: commands.add,
      );
      final persistence = _FakePersistence([doc1], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      host.attach(controller);

      controller.restoreRetainedActive(doc1);

      // Child taps close (X) -> client operation pending!
      host.dismissPanel();
      commands.clear();
      expect(host.pendingClientOperationId, isNotNull);

      // Server command arrives afterwards
      controller.receivePresentationCommand({
        'protocolVersion': 4,
        'event': 'canvasPresentationCommand',
        'sessionId': 'session-1',
        'sequence': 1,
        'revision': 1,
        'commandId': 'cmd-show-1',
        'action': 'show',
        'sceneId': 'scene-1',
      });

      // Command is rejected because client operation is pending!
      expect(commands.length, 1);
      expect(
        commands.first.type,
        AvaSvgCanvasCommandType.canvasPresentationAcknowledged,
      );
      expect(commands.first.fields['commandId'], 'cmd-show-1');
      expect(commands.first.fields['applied'], isFalse);
      expect(commands.first.fields['reason'], 'supersededByClient');
      expect(host.panelVisible, isFalse);

      host.dispose();
      controller.dispose();
    });
  });

  group('Presentation command convergence races', () {
    test('timed-out physical operation cannot poison later model commands',
        () async {
      final doc1 = _document(scene: 1, revision: 1);
      final commands = <AvaSvgCanvasClientCommand>[];
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: commands.add,
      );
      final host = AvaSvgCanvasSessionHost(
        clientPresentationTimeout: const Duration(milliseconds: 10),
      );
      host.attach(controller);
      controller.restoreRetainedActive(doc1);

      host.dismissPanel();
      expect(host.pendingClientOperationId, isNotNull);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(host.pendingClientOperationId, isNull);
      expect(host.panelVisible, isFalse);

      commands.clear();
      controller.receivePresentationCommand({
        'protocolVersion': 4,
        'event': 'canvasPresentationCommand',
        'sessionId': 'session-1',
        'sequence': 1,
        'revision': 1,
        'commandId': 'cmd-show-after-timeout',
        'action': 'show',
        'sceneId': 'scene-1',
      });
      await Future<void>.delayed(Duration.zero);

      expect(commands, hasLength(1));
      expect(commands.single.fields['commandId'], 'cmd-show-after-timeout');
      expect(commands.single.fields['applied'], isTrue);

      host.dispose();
      controller.dispose();
    });

    test('newer manual focus fences an awaiting model show target', () async {
      final doc1 = _document(scene: 1, revision: 1);
      final doc2 = _document(
        scene: 2,
        revision: 1,
        manifestJson: _manifestJson2,
      );
      final doc3 = _document(scene: 3, revision: 1);
      final commands = <AvaSvgCanvasClientCommand>[];
      final modelFocus = Completer<void>();
      final focusCalls = <String>[];
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc2),
        commandSink: commands.add,
      );
      final host = AvaSvgCanvasSessionHost(
        persistence: _FakePersistence([doc1, doc2, doc3], 'scene-1'),
      );
      await host.restoration;
      host.attach(
        controller,
        runtimeFocusSink: (document, revision) async {
          focusCalls.add('${document.descriptor.sceneId}:$revision');
          if (document.descriptor.sceneId == 'scene-2') {
            await modelFocus.future;
          }
        },
      );

      controller.receivePresentationCommand({
        'protocolVersion': 4,
        'event': 'canvasPresentationCommand',
        'sessionId': 'session-1',
        'sequence': 1,
        'revision': 1,
        'commandId': 'cmd-show-2',
        'action': 'show',
        'sceneId': 'scene-2',
      });
      await Future<void>.delayed(Duration.zero);
      expect(host.presentedDocument?.descriptor.sceneId, 'scene-2');

      await host.selectSceneByIndex(2);
      expect(host.presentedDocument?.descriptor.sceneId, 'scene-3');
      expect(host.focusRevision, 2);

      modelFocus.complete();
      await Future<void>.delayed(Duration.zero);

      final modelAck = commands.singleWhere(
        (command) =>
            command.type ==
                AvaSvgCanvasCommandType.canvasPresentationAcknowledged &&
            command.fields['commandId'] == 'cmd-show-2',
      );
      expect(modelAck.fields['applied'], isFalse);
      expect(modelAck.fields['reason'], 'supersededByClient');
      expect(modelAck.fields['sceneId'], isNull);
      expect(modelAck.fields['focusRevision'], isNull);
      expect(focusCalls, ['scene-2:1', 'scene-3:2']);
      expect(host.presentedDocument?.descriptor.sceneId, 'scene-3');

      host.dispose();
      controller.dispose();
    });
  });

  group('Presentation changed event synchronization', () {
    test('user-originated presentationChanged clears pendingClientOperationId',
        () {
      final doc1 = _document(scene: 1, revision: 1);
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: (_) {},
      );
      final persistence = _FakePersistence([doc1], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      host.attach(controller);

      controller.restoreRetainedActive(doc1);
      host.dismissPanel();
      expect(host.pendingClientOperationId, isNotNull);

      // Server confirms presentation change originated by user
      controller.receivePresentationChanged({
        'protocolVersion': 4,
        'event': 'canvasPresentationChanged',
        'sessionId': 'session-1',
        'sequence': 1,
        'revision': 2,
        'visible': false,
        'source': 'user',
      });

      expect(host.pendingClientOperationId, isNull);
      expect(host.lastAppliedCanonicalRevision, 2);

      host.dispose();
      controller.dispose();
    });

    test('stale model presentationChanged is ignored', () {
      final doc1 = _document(scene: 1, revision: 1);
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: (_) {},
      );
      final persistence = _FakePersistence([doc1], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      host.attach(controller);
      controller.restoreRetainedActive(doc1);
      expect(host.panelVisible, isTrue);

      // Establish revision 5
      controller.receivePresentationChanged({
        'protocolVersion': 4,
        'event': 'canvasPresentationChanged',
        'sessionId': 'session-1',
        'sequence': 1,
        'revision': 5,
        'visible': true,
        'sceneId': 'scene-1',
        'focusRevision': 1,
        'source': 'model',
      });
      expect(host.lastAppliedCanonicalRevision, 5);
      expect(host.panelVisible, isTrue);

      // Stale revision 3 arrives
      controller.receivePresentationChanged({
        'protocolVersion': 4,
        'event': 'canvasPresentationChanged',
        'sessionId': 'session-1',
        'sequence': 2,
        'revision': 3,
        'visible': false,
        'source': 'model',
      });

      // Still at revision 5 and still visible
      expect(host.lastAppliedCanonicalRevision, 5);
      expect(host.panelVisible, isTrue);

      host.dispose();
      controller.dispose();
    });
  });

  group('Startup clientReady presentation state', () {
    test('clientReady without visible panel omits active visual claims', () {
      final doc1 = _document(scene: 1, revision: 1);
      final persistence = _FakePersistence([doc1], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      final snapshot = host.presentationSnapshot();
      expect(snapshot.visible, isFalse);
      expect(snapshot.sceneId, isNull);
      expect(snapshot.focusRevision, isNull);

      final event = AvaRealtimeClientEventV4.clientReady(
        sessionId: 'session-startup-1',
        sequence: 0,
        canvasPanelVisible: snapshot.visible,
        sceneId: snapshot.sceneId,
        focusRevision: snapshot.focusRevision,
      );
      final json = event.toJson();
      expect(json['canvasPanelVisible'], isNull);
      expect(json['sceneId'], isNull);
      expect(json['focusRevision'], isNull);

      host.dispose();
    });

    test(
        'clientReady with visible panel includes exact scene and focus revision',
        () {
      final doc1 = _document(scene: 1, revision: 1);
      final controller = AvaSvgCanvasController(
        documents: _DocumentLoader(doc1),
        commandSink: (_) {},
      );
      final persistence = _FakePersistence([doc1], 'scene-1');
      final host = AvaSvgCanvasSessionHost(persistence: persistence);
      host.attach(controller);
      controller.restoreRetainedActive(doc1);
      expect(host.panelVisible, isTrue);

      final snapshot = host.presentationSnapshot();
      expect(snapshot.visible, isTrue);
      expect(snapshot.sceneId, 'scene-1');
      expect(snapshot.focusRevision, 1);

      final event = AvaRealtimeClientEventV4.clientReady(
        sessionId: 'session-startup-2',
        sequence: 0,
        canvasPanelVisible: snapshot.visible,
        sceneId: snapshot.sceneId,
        focusRevision: snapshot.focusRevision,
      );
      final json = event.toJson();
      expect(json['canvasPanelVisible'], isTrue);
      expect(json['sceneId'], 'scene-1');
      expect(json['focusRevision'], 1);

      host.dispose();
      controller.dispose();
    });
  });

  group('Part 5 — Bootstrap / Early Emit Sequence Coalescing', () {
    test('pre-clientReady semantic interaction is ephemeral and not replayed',
        () async {
      final doc1 = _document(
        scene: 1,
        revision: 1,
        manifestJson: _manifestJson1.replaceFirst(
          '"interactionMode":"select"',
          '"interactionMode":"explore"',
        ),
      );
      final host = AvaSvgCanvasSessionHost();
      final raw = _ControlledRawTransportV4();
      final media = DeterministicAvaRealtimeMediaSession(
        offerSdp: _deterministicOfferSdp,
      );
      final runtime = AvaRealtimeTurnRuntime(
        sessionId: _sessionId,
        limits: _testLimits,
        mediaSession: media,
        transport: raw,
        documents: _DocumentLoader(doc1),
        speechOutput: _FakeSpeechOutput(),
        presentation: AvaRealtimePresentationController(),
        volume: 0.8,
        cancelRequestIdFactory: () => 'cancel-pre-ready-interaction',
        automaticPlaybackPolling: false,
        microphoneInitiallyEnabled: false,
        canvasPresentationSnapshot: host.presentationSnapshot,
        retainedSceneRevision: host.retainedSceneRevision,
      );
      host.attach(runtime.canvasController);
      runtime.canvasController.restoreRetainedActive(doc1);
      runtime.canvasController.acceptPresentedScene(
        sceneId: 'scene-1',
        focusRevision: 0,
      );

      runtime.canvasController.reportBinding(
        doc1.manifest.bindings.values.first,
      );
      expect(raw.sentEvents, isEmpty);

      final runFuture = runtime.run();
      await raw.waitForSent((event) => event['event'] == 'clientReady');
      expect(raw.sentJson.map((event) => event['event']), ['clientReady']);

      runtime.canvasController.reportBinding(
        doc1.manifest.bindings.values.first,
      );
      await raw.waitForSent((event) => event['event'] == 'canvasInteraction');
      expect(
        raw.sentJson.where((event) => event['event'] == 'canvasInteraction'),
        hasLength(1),
      );

      await runtime.close();
      await raw.close();
      await media.close();
      host.dispose();
      await runFuture;
    });

    test(
        'pre-clientReady dismiss is coalesced into clientReady; next wire sequence is 1',
        () async {
      final doc1 = _document(scene: 1, revision: 1);
      final host = AvaSvgCanvasSessionHost();
      final raw = _ControlledRawTransportV4();
      final media = DeterministicAvaRealtimeMediaSession(
        offerSdp: _deterministicOfferSdp,
      );
      final presentation = AvaRealtimePresentationController();

      final runtime = AvaRealtimeTurnRuntime(
        sessionId: _sessionId,
        limits: _testLimits,
        mediaSession: media,
        transport: raw,
        documents: _DocumentLoader(doc1),
        speechOutput: _FakeSpeechOutput(),
        presentation: presentation,
        volume: 0.8,
        cancelRequestIdFactory: () => 'cancel-seq-1',
        automaticPlaybackPolling: false,
        microphoneInitiallyEnabled: false,
        canvasPresentationSnapshot: host.presentationSnapshot,
        retainedSceneRevision: host.retainedSceneRevision,
      );

      // 1. Retained Canvas restored and host attaches
      host.attach(runtime.canvasController);
      runtime.canvasController.restoreRetainedActive(doc1);
      expect(host.panelVisible, isTrue);

      // 2. Early emit: dismiss before event socket / clientReady is ready
      host.dismissPanel();
      expect(host.panelVisible, isFalse);

      // Verify no unsent transport events yet
      expect(raw.sentEvents, isEmpty);

      // 3. Now start runtime
      final runFuture = runtime.run();
      await raw.waitForSent((event) => event['event'] == 'clientReady');

      // 4. clientReady must be sequence 0 and have canvasPanelVisible false/omitted
      expect(raw.sentJson.length, 1);
      final clientReady = raw.sentJson[0];
      expect(clientReady['event'], 'clientReady');
      expect(clientReady['sequence'], 0);
      expect(clientReady['canvasPanelVisible'], isNull);

      // 5. Subsequent event (e.g. physical reopenPanel) must begin at sequence 1
      host.reopenPanel();
      await raw.waitForSent(
          (event) => event['event'] == 'canvasPresentationUpdated');

      expect(raw.sentJson.length, 2);
      final updated = raw.sentJson[1];
      expect(updated['event'], 'canvasPresentationUpdated');
      expect(updated['sequence'], 1);
      expect(updated['visible'], isTrue);
      expect(updated['sceneId'], 'scene-1');
      expect(updated['focusRevision'], 1);
      expect(updated['clientOperationId'], 'op_2');

      // Sequences on wire are strictly contiguous: [0, 1]
      expect(raw.sentJson.map((e) => e['sequence']), [0, 1]);

      await runtime.close();
      await raw.close();
      await media.close();
      host.dispose();
      await runFuture;
    });
  });

  group('Part 6 — Physical Close/Reopen Runtime Sequence', () {
    test(
        'physical dismiss and reopen at runtime produce contiguous wire sequences [0, 1, 2]',
        () async {
      final doc1 = _document(scene: 1, revision: 1);
      final host = AvaSvgCanvasSessionHost();
      final raw = _ControlledRawTransportV4();
      final media = DeterministicAvaRealtimeMediaSession(
        offerSdp: _deterministicOfferSdp,
      );
      final presentation = AvaRealtimePresentationController();

      final runtime = AvaRealtimeTurnRuntime(
        sessionId: _sessionId,
        limits: _testLimits,
        mediaSession: media,
        transport: raw,
        documents: _DocumentLoader(doc1),
        speechOutput: _FakeSpeechOutput(),
        presentation: presentation,
        volume: 0.8,
        cancelRequestIdFactory: () => 'cancel-seq-2',
        automaticPlaybackPolling: false,
        microphoneInitiallyEnabled: false,
        canvasPresentationSnapshot: host.presentationSnapshot,
        retainedSceneRevision: host.retainedSceneRevision,
      );

      // Attach and restore visible scene
      host.attach(runtime.canvasController);
      runtime.canvasController.restoreRetainedActive(doc1);
      expect(host.panelVisible, isTrue);

      // Start runtime
      final runFuture = runtime.run();
      await raw.waitForSent((event) => event['event'] == 'clientReady');

      // Frame 0: clientReady
      expect(raw.sentJson[0]['event'], 'clientReady');
      expect(raw.sentJson[0]['sequence'], 0);
      expect(raw.sentJson[0]['canvasPanelVisible'], isTrue);
      expect(raw.sentJson[0]['sceneId'], 'scene-1');
      expect(raw.sentJson[0]['focusRevision'], 1);

      // Physical dismiss
      host.dismissPanel();
      await raw.waitForSent(
        (event) =>
            event['event'] == 'canvasPresentationUpdated' &&
            event['visible'] == false,
      );

      // Frame 1: canvasPresentationUpdated(visible=false)
      expect(raw.sentJson[1]['event'], 'canvasPresentationUpdated');
      expect(raw.sentJson[1]['sequence'], 1);
      expect(raw.sentJson[1]['visible'], isFalse);
      expect(raw.sentJson[1]['clientOperationId'], 'op_1');
      expect(
        RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,95}$').hasMatch(
          raw.sentJson[1]['clientOperationId'] as String,
        ),
        isTrue,
      );

      // Physical reopen
      host.reopenPanel();
      await raw.waitForSent(
        (event) =>
            event['event'] == 'canvasPresentationUpdated' &&
            event['visible'] == true,
      );

      // Frame 2: canvasPresentationUpdated(visible=true)
      expect(raw.sentJson[2]['event'], 'canvasPresentationUpdated');
      expect(raw.sentJson[2]['sequence'], 2);
      expect(raw.sentJson[2]['visible'], isTrue);
      expect(raw.sentJson[2]['sceneId'], 'scene-1');
      expect(raw.sentJson[2]['focusRevision'], 1);
      expect(raw.sentJson[2]['clientOperationId'], 'op_2');
      expect(
        RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,95}$').hasMatch(
          raw.sentJson[2]['clientOperationId'] as String,
        ),
        isTrue,
      );

      // Contiguous transport ordering: [0, 1, 2]
      expect(raw.sentJson.map((e) => e['sequence']), [0, 1, 2]);

      await runtime.close();
      await raw.close();
      await media.close();
      host.dispose();
      await runFuture;
    });

    test(
        'event construction failure does not advance client sequence or create sequence hole',
        () async {
      final doc1 = _document(scene: 1, revision: 1);
      final raw = _ControlledRawTransportV4();
      final media = DeterministicAvaRealtimeMediaSession(
        offerSdp: _deterministicOfferSdp,
      );
      final presentation = AvaRealtimePresentationController();

      final runtime = AvaRealtimeTurnRuntime(
        sessionId: _sessionId,
        limits: _testLimits,
        mediaSession: media,
        transport: raw,
        documents: _DocumentLoader(doc1),
        speechOutput: _FakeSpeechOutput(),
        presentation: presentation,
        volume: 0.8,
        cancelRequestIdFactory: () => 'cancel-seq-3',
        automaticPlaybackPolling: false,
        microphoneInitiallyEnabled: false,
      );

      final runFuture = runtime.run();
      await raw.waitForSent((event) => event['event'] == 'clientReady');
      expect(raw.sentJson.length, 1);
      expect(raw.sentJson[0]['sequence'], 0);

      // Attempt to send an invalid event that fails protocol validation
      expect(
        () => runtime.canvasController.emitPresentationUpdated(
          visible: true,
          clientOperationId: '123_invalid_digits_first',
        ),
        returnsNormally,
      );

      // Now emit a valid event
      runtime.canvasController.emitPresentationUpdated(
        visible: false,
        clientOperationId: 'op_valid_1',
      );
      await raw.waitForSent(
          (event) => event['event'] == 'canvasPresentationUpdated');

      // The valid event MUST have sequence 1 (not sequence 2, which would have been a hole)
      expect(raw.sentJson.length, 2);
      expect(raw.sentJson[1]['sequence'], 1);
      expect(raw.sentJson[1]['clientOperationId'], 'op_valid_1');
      expect(raw.sentJson.map((e) => e['sequence']), [0, 1]);

      await runtime.close();
      await raw.close();
      await media.close();
      await runFuture;
    });
  });
}

const _sessionId = 'session-v4-presentation-seq';
const _deterministicOfferSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava V4 sequence test\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

final class _ControlledRawTransportV4 implements AvaRealtimeRawTransportV4 {
  final StreamController<AvaRealtimeInboundFrameV4> _frames =
      StreamController<AvaRealtimeInboundFrameV4>();
  final List<AvaRealtimeClientEventV4> sentEvents = [];
  final List<_SentWaiter> _waiters = [];
  bool closed = false;

  List<Map<String, Object?>> get sentJson => [
        for (final event in sentEvents) event.toJson(),
      ];

  void add(AvaRealtimeInboundFrameV4 frame) {
    if (closed) throw StateError('Controlled V4 transport is closed.');
    _frames.add(frame);
  }

  Future<Map<String, Object?>> waitForSent(
    bool Function(Map<String, Object?> event) predicate,
  ) {
    for (final event in sentJson) {
      if (predicate(event)) return Future<Map<String, Object?>>.value(event);
    }
    final result = Completer<Map<String, Object?>>();
    _waiters.add(_SentWaiter(predicate: predicate, result: result));
    return result.future;
  }

  @override
  Stream<AvaRealtimeInboundFrameV4> frames() => _frames.stream;

  @override
  Future<void> send(AvaRealtimeClientEventV4 event) async {
    if (closed) throw StateError('Controlled V4 transport is closed.');
    sentEvents.add(event);
    final json = event.toJson();
    for (final waiter in List<_SentWaiter>.of(_waiters)) {
      if (!waiter.predicate(json)) continue;
      _waiters.remove(waiter);
      waiter.result.complete(json);
    }
  }

  @override
  Future<void> close() async {
    if (closed) return;
    closed = true;
    await _frames.close();
    for (final waiter in _waiters) {
      if (!waiter.result.isCompleted) {
        waiter.result.completeError(
          StateError('V4 transport closed before the expected client control.'),
        );
      }
    }
    _waiters.clear();
  }
}

final class _SentWaiter {
  const _SentWaiter({
    required this.predicate,
    required this.result,
  });

  final bool Function(Map<String, Object?> event) predicate;
  final Completer<Map<String, Object?>> result;
}

const _testLimits = AvaSessionLimitsV4(
  maxTurnSeconds: 60,
  maxResponseCharacters: 500,
  maxSvgBytes: 200000,
  maxManifestBytes: 50000,
  maxAudioChunkBytes: 32000,
  playbackProgressIntervalMs: 250,
);

final class _FakeSpeechOutput implements PcmSpeechOutputService {
  @override
  Future<void> start({
    required String turnId,
    required int sampleRateHz,
    required double volume,
  }) async {}

  @override
  Future<void> append({
    required String turnId,
    required Uint8List audio,
  }) async {}

  @override
  Future<PcmStopResult> finish({required String turnId}) async =>
      const PcmStopResult(renderedSampleCount: 0);

  @override
  Future<PcmPlaybackProgress> progress({required String turnId}) async =>
      const PcmPlaybackProgress(
        renderedSampleCount: 0,
        scheduledSampleCount: 0,
        sampleRateHz: 24000,
      );

  @override
  Future<Duration> position({required String turnId}) async => Duration.zero;

  @override
  Future<PcmStopResult> fadeOutAndStop({
    required String turnId,
    Duration fadeDuration = const Duration(milliseconds: 80),
  }) async =>
      const PcmStopResult(renderedSampleCount: 0);

  @override
  Future<void> stop() async {}
}
