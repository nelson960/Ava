import 'package:ava/models/diagram_interaction.dart';
import 'package:ava/models/learning_progress.dart';
import 'package:ava/models/lesson_response.dart';
import 'package:ava/models/local_learning_activity.dart';
import 'package:ava/services/learning_progress_service.dart';
import 'package:ava/services/memory_service.dart';
import 'package:drift/native.dart';
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

  test('records typed evidence and derives restrained progress', () async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);
    var now = DateTime(2026, 7, 26, 10);
    final service = LearningProgressService(
      database,
      profileId: 'current_child',
      now: () => now,
    );

    await service.recordActivityOpened(activity);
    now = now.add(const Duration(minutes: 1));
    await service.recordInteraction(
      activity: activity,
      interaction: const DiagramInteraction(
        kind: DiagramInteractionKind.valueChanged,
        currentValue: 1,
        targetValue: 3,
      ),
    );
    now = now.add(const Duration(minutes: 1));
    await service.recordInteraction(
      activity: activity,
      interaction: const DiagramInteraction(
        kind: DiagramInteractionKind.completed,
        currentValue: 3,
        targetValue: 3,
      ),
    );

    final summary = await service.watchSummary().first;
    expect(summary.activitiesExplored, 1);
    expect(summary.activitiesCompleted, 1);
    expect(summary.subjectActivityCounts[LearningDomain.math], 1);
    expect(summary.recentTopics, ['Build three quarters']);
    expect(
      summary.recentEvents.map((event) => event.kind),
      [
        LearningProgressEventKind.activityCompleted,
        LearningProgressEventKind.activityOpened,
      ],
    );
  });

  test('conversation reset preserves learning evidence', () async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);
    final service = LearningProgressService(
      database,
      profileId: 'current_child',
      now: () => DateTime(2026, 7, 26, 10),
    );
    await database.recordConversationTurn(
      childMessage: 'Hello',
      avaMessage: 'Hello',
    );
    await service.recordActivityOpened(activity);

    await database.clearConversationHistory();

    expect(await database.getMessages(), isEmpty);
    expect((await service.watchSummary().first).activitiesExplored, 1);
  });

  test('rejects completion without matching evidence', () {
    expect(
      () => LearningProgressEvent.fromInteraction(
        profileId: 'current_child',
        activity: activity,
        interaction: const DiagramInteraction(
          kind: DiagramInteractionKind.completed,
          currentValue: 2,
          targetValue: 3,
        ),
        occurredAt: DateTime(2026, 7, 26),
      ),
      throwsFormatException,
    );
  });
}
