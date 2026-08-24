import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/models/ava_realtime_protocol_v4.dart';
import 'package:ava/services/ava_realtime_presentation.dart';
import 'package:ava/services/memory_service.dart';
import 'package:ava/state/app_state.dart';
import 'package:ava/ui/widgets/caption_ticker.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('CaptionTicker renders active caption and fades out after timer',
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

    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWithValue(database),
        avaRealtimePresentationProvider.overrideWith((ref) => presentation),
      ],
    );
    addTearDown(container.dispose);

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: const MaterialApp(
          home: Scaffold(
            body: CaptionTicker(dashboardVisible: false),
          ),
        ),
      ),
    );
    await tester.runAsync(() async {
      await Future<void>.delayed(const Duration(milliseconds: 50));
    });
    await tester.pump();

    presentation.addSegment(
      0,
      AvaRealtimeTurnSegment.fromJson({
        'id': 'speech',
        'speech': 'Hello there!',
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
        'textEnd': 12,
        'sampleStart': 0,
        'sampleEnd': 24000,
        'phoneme': 'EE',
      }),
    ]);
    presentation.updatePlaybackClock(
      renderedSampleCount: 1000,
      receivedSampleCount: 24000,
    );

    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.text('Hello there!'), findsOneWidget);

    // The rendered-sample caption remains visible while presentation owns it;
    // UI wall time must never dismiss active speech.
    await tester.pump(const Duration(milliseconds: 1500));
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.text('Hello there!'), findsOneWidget);

    presentation.complete(
      AvaRealtimeServerEventV4.fromJson({
        'protocolVersion': 4,
        'event': 'turnCompleted',
        'sessionId': 's-1',
        'turnId': 't-1',
        'sequence': 1,
        'revision': 1,
        'deliveredSampleCount': 24000,
        'deliveredText': 'Hello there!',
      }),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 1500));
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.text('Hello there!'), findsNothing);
  });
}
