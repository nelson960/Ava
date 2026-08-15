import 'package:ava/models/diagram_interaction.dart';
import 'package:ava/models/lesson_response.dart';
import 'package:ava/models/local_learning_activity.dart';
import 'package:ava/services/learning_progress_service.dart';
import 'package:ava/services/memory_service.dart';
import 'package:ava/state/app_state.dart';
import 'package:ava/ui/progress/parent_progress_sheet.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  const activity = LocalLearningActivity(
    id: 'build_three_quarters',
    eyebrow: 'Fractions',
    title: 'Build three quarters',
    instruction: 'Fill three parts.',
    domain: LearningDomain.math,
    tone: ActivityTone.coral,
    targetValue: 3,
    diagram: DiagramSpec.fractionCircle(
      title: 'Make 3/4',
      totalParts: 4,
      highlightedParts: 0,
    ),
  );

  testWidgets('grown-up trail shows evidence without mastery claims',
      (tester) async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);
    final service = LearningProgressService(
      database,
      profileId: StudentStateNotifier.profileId,
      now: () => DateTime(2026, 7, 26, 10),
    );
    await service.recordActivityOpened(activity);
    await service.recordInteraction(
      activity: activity,
      interaction: const DiagramInteraction(
        kind: DiagramInteractionKind.completed,
        currentValue: 3,
        targetValue: 3,
      ),
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [databaseProvider.overrideWithValue(database)],
        child: const MaterialApp(
          home: Scaffold(body: ParentProgressSheet()),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Learning trail'), findsOneWidget);
    expect(
      find.text('Activity evidence—not grades or guessed mastery.'),
      findsOneWidget,
    );
    expect(find.text('Explored'), findsOneWidget);
    expect(find.text('Completed'), findsOneWidget);
    expect(find.text('Build three quarters'), findsWidgets);
    expect(find.textContaining('score'), findsNothing);
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(milliseconds: 1));
  });
}
