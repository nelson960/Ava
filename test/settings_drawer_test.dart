import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_controller.dart';
import 'package:ava/canvas/ava_svg_canvas_session_host.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_memory_control_service.dart';
import 'package:ava/services/memory_service.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/state/app_state.dart';
import 'package:ava/ui/widgets/settings_drawer.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('clearing conversations preserves the complete child profile', () async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);
    final dateOfBirth = DateTime(2018, 4, 12);
    await database.insertOrUpdateProfile(
      StudentProfile(
        id: 'current_child',
        name: 'Mia',
        dateOfBirth: dateOfBirth,
        captionsEnabled: true,
        mathScore: 7,
        scienceScore: 4,
        lastActive: DateTime(2026, 7, 21),
      ),
    );
    await database.recordConversationTurn(
      childMessage: 'What is gravity?',
      avaMessage: 'Gravity pulls objects toward each other.',
    );

    await database.clearConversationHistory();

    expect(await database.getMessages(), isEmpty);
    final profile = await database.getProfile('current_child');
    expect(profile?.name, 'Mia');
    expect(profile?.dateOfBirth, dateOfBirth);
    expect(profile?.captionsEnabled, isTrue);
    expect(profile?.mathScore, 7);
    expect(profile?.scienceScore, 4);
  });

  testWidgets('shows only the minimal settings controls', (tester) async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [databaseProvider.overrideWithValue(database)],
        child: const MaterialApp(
          home: Scaffold(body: SettingsDrawer()),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Settings'), findsOneWidget);
    expect(find.text('CHILD PROFILE'), findsOneWidget);
    expect(find.text('Name'), findsOneWidget);
    expect(find.text('Date of birth'), findsOneWidget);
    expect(find.text('Captions'), findsOneWidget);
    expect(find.text('Weekly recap'), findsOneWidget);
    await tester.drag(find.byType(ListView), const Offset(0, -300));
    await tester.pumpAndSettle();
    expect(find.text('Reset conversations'), findsOneWidget);

    expect(find.text('Master volume'), findsNothing);
    expect(find.textContaining('Production voice'), findsNothing);
    expect(find.text('Reset learning data'), findsNothing);

    await tester.tap(find.byKey(const ValueKey('settings-captions')));
    await tester.pumpAndSettle();
    expect(
      (await database.getProfile('current_child'))?.captionsEnabled,
      isTrue,
    );
  });

  testWidgets('reset button clears conversations without resetting profile',
      (tester) async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);
    await database.insertOrUpdateProfile(
      StudentProfile(
        id: 'current_child',
        name: 'Ari',
        dateOfBirth: DateTime(2019, 2, 3),
        captionsEnabled: false,
        mathScore: 5,
        scienceScore: 6,
        lastActive: DateTime(2026, 7, 21),
      ),
    );
    await database.recordConversationTurn(
      childMessage: 'Hello Ava',
      avaMessage: 'Hello Ari',
    );
    final canvasHost = await _retainedCanvasHost();
    expect(canvasHost.sceneCount, 1);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          databaseProvider.overrideWithValue(database),
          avaMemoryControlProvider.overrideWithValue(_FakeMemoryControl()),
          avaSvgCanvasSessionHostProvider.overrideWith((ref) => canvasHost),
        ],
        child: const MaterialApp(
          home: Scaffold(body: SettingsDrawer()),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.drag(find.byType(ListView), const Offset(0, -300));
    await tester.pumpAndSettle();
    final resetButton =
        find.byKey(const ValueKey('settings-reset-conversations'));
    await tester.tap(resetButton);
    await tester.pumpAndSettle();
    expect(find.text('Reset conversations?'), findsOneWidget);

    await tester.tap(find.widgetWithText(FilledButton, 'Reset'));
    await tester.pumpAndSettle();

    expect(await database.getMessages(), isEmpty);
    final profile = await database.getProfile('current_child');
    expect(profile?.name, 'Ari');
    expect(profile?.mathScore, 5);
    expect(profile?.scienceScore, 6);
    expect(canvasHost.workspaceDocuments, isEmpty);
    expect(canvasHost.presentedDocument, isNull);
    expect(canvasHost.panelVisible, isFalse);
    expect(canvasHost.canReopen, isFalse);
  });

  testWidgets('weekly recap shows bounded topic summaries', (tester) async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          databaseProvider.overrideWithValue(database),
          avaMemoryControlProvider.overrideWithValue(_FakeMemoryControl()),
        ],
        child: const MaterialApp(
          home: Scaffold(body: SettingsDrawer()),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final recap = find.byKey(const ValueKey('settings-weekly-recap'));
    await tester.ensureVisible(recap);
    await tester.tap(recap);
    await tester.pumpAndSettle();

    expect(find.text('Talked about the moon.'), findsOneWidget);
    expect(find.textContaining('diagnos'), findsNothing);
  });

  testWidgets('weekly recap scrolls on a short viewport without overflowing',
      (tester) async {
    tester.view.physicalSize = const Size(640, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          databaseProvider.overrideWithValue(database),
          avaMemoryControlProvider.overrideWithValue(
            _ManyRecordsMemoryControl(),
          ),
        ],
        child: const MaterialApp(
          home: Scaffold(body: SettingsDrawer()),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final recap = find.byKey(const ValueKey('settings-weekly-recap'));
    await tester.ensureVisible(recap);
    await tester.tap(recap);
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    await tester.scrollUntilVisible(
      find.text('Recap topic 8 with a deliberately longer summary.'),
      80,
      scrollable: find.descendant(
        of: find.byKey(const ValueKey('weekly-recap-list')),
        matching: find.byType(Scrollable),
      ),
    );
    expect(
      find.text('Recap topic 8 with a deliberately longer summary.'),
      findsOneWidget,
    );
  });

  testWidgets('cloud reset failure preserves the local conversation',
      (tester) async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);
    await database.recordConversationTurn(
      childMessage: 'Hello Ava',
      avaMessage: 'Hello there',
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          databaseProvider.overrideWithValue(database),
          avaMemoryControlProvider.overrideWithValue(_FailingMemoryControl()),
        ],
        child: const MaterialApp(
          home: Scaffold(body: SettingsDrawer()),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.drag(find.byType(ListView), const Offset(0, -300));
    await tester.pumpAndSettle();
    final resetButton =
        find.byKey(const ValueKey('settings-reset-conversations'));
    await tester.tap(resetButton);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Reset'));
    await tester.pumpAndSettle();

    expect(await database.getMessages(), hasLength(2));
    expect(
        find.textContaining('Could not reset conversations'), findsOneWidget);
  });
}

const _canvasManifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","action":"select","bindingId":"select-cloud","elementId":"cloud","exclusive":false,"hitTest":"element","pulseIds":[],"revealIds":[],"serverReportable":true,"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","manifestVersion":"ava-svg-manifest-v1","rendererCapabilityVersion":"ava-svg-renderer-v1","sourceTrust":"modelGeneratedData","title":"Café cloud ☁️","validatorVersion":"ava-svg-validator-v1","viewBox":[0.0,0.0,1000.0,600.0]}';

final class _CanvasLoader implements AvaSvgDocumentLoader {
  const _CanvasLoader(this.document);

  final AvaValidatedSvgCanvasDocument document;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) async {
    if (descriptor.generationId != document.descriptor.generationId) {
      throw StateError('Unexpected deterministic canvas generation.');
    }
    return document;
  }

  @override
  void close() {}
}

Future<AvaSvgCanvasSessionHost> _retainedCanvasHost() async {
  final events = (jsonDecode(
    File('protocol/fixtures/ava_realtime_canvas_events_v4.json')
        .readAsStringSync(),
  ) as List)
      .map((value) => (value as Map).cast<String, Object?>())
      .toList(growable: false);
  final descriptor = AvaSvgCanvasDocumentDescriptorV4.fromJson(events[0]);
  final document = AvaValidatedSvgCanvasDocument.verify(
    descriptor: descriptor,
    svgBytes:
        File('protocol/fixtures/ava_svg_document_v4.svg').readAsBytesSync(),
    manifestBytes: Uint8List.fromList(utf8.encode(_canvasManifestJson)),
  );
  final controller = AvaSvgCanvasController(
    documents: _CanvasLoader(document),
    commandSink: (_) {},
  );
  final host = AvaSvgCanvasSessionHost()..attach(controller);
  controller.beginPlaybackClock(descriptor.sourceTurnId);
  controller.receiveGenerationStarted({
    'protocolVersion': 4,
    'event': 'canvasGenerationStarted',
    'sessionId': 'session-1234',
    'sequence': 0,
    'revision': 1,
    'generationId': descriptor.generationId,
    'sourceTurnId': descriptor.sourceTurnId,
  });
  await controller.receiveDocumentAvailable(events[0]);
  controller.rendererPrepared();
  controller.receivePresentationCue(events[1]);
  controller.updateRenderedSample(
    turnId: descriptor.sourceTurnId,
    renderedSample: 4812,
  );
  controller.firstFramePresented();
  controller.receiveActivated(events[2]);
  host.detach(controller);
  controller.dispose();
  return host;
}

final class _FakeMemoryControl implements AvaMemoryControl {
  @override
  Future<AvaWeeklyConversationRecap> loadWeeklyRecap(
    DateTime periodStart,
  ) async {
    return AvaWeeklyConversationRecap(
      periodStart: periodStart,
      periodEnd: periodStart.add(const Duration(days: 7)),
      generatedAt: DateTime.utc(2026, 8, 1),
      resetRevision: 0,
      records: [
        AvaWeeklyConversationRecord(
          turnId: 'turn-1',
          summary: 'Talked about the moon.',
          occurredAt: DateTime.utc(2026, 8, 1),
        ),
      ],
    );
  }

  @override
  Future<AvaConversationResetReceipt> resetConversations() async {
    return AvaConversationResetReceipt(
      revision: 1,
      resetAt: DateTime.utc(2026, 8, 1),
    );
  }
}

final class _FailingMemoryControl implements AvaMemoryControl {
  @override
  Future<AvaWeeklyConversationRecap> loadWeeklyRecap(DateTime periodStart) {
    throw UnimplementedError();
  }

  @override
  Future<AvaConversationResetReceipt> resetConversations() async {
    throw const AvaMemoryControlException(
      message: 'Memory unavailable.',
      statusCode: 503,
      requestId: 'request-test',
    );
  }
}

final class _ManyRecordsMemoryControl implements AvaMemoryControl {
  @override
  Future<AvaWeeklyConversationRecap> loadWeeklyRecap(
    DateTime periodStart,
  ) async {
    return AvaWeeklyConversationRecap(
      periodStart: periodStart,
      periodEnd: periodStart.add(const Duration(days: 7)),
      generatedAt: DateTime.utc(2026, 8, 1),
      resetRevision: 0,
      records: [
        for (var index = 1; index <= 8; index += 1)
          AvaWeeklyConversationRecord(
            turnId: 'turn-$index',
            summary: 'Recap topic $index with a deliberately longer summary.',
            occurredAt: DateTime.utc(2026, 8, index),
          ),
      ],
    );
  }

  @override
  Future<AvaConversationResetReceipt> resetConversations() async {
    throw UnimplementedError();
  }
}
