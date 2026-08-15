import 'package:ava/models/lesson_response.dart';
import 'package:ava/models/local_learning_activity.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('bundled local activity catalog is strict, varied and offline',
      () async {
    final catalog = await LocalLearningActivityCatalog.load(rootBundle);

    expect(catalog.activities, hasLength(3));
    expect(
      catalog.activities.map((activity) => activity.id).toSet(),
      hasLength(3),
    );
    expect(
      catalog.activities.map((activity) => activity.diagram.kind).toSet(),
      {
        DiagramKind.fractionCircle,
        DiagramKind.fractionBar,
        DiagramKind.numberLine,
      },
    );
    for (final activity in catalog.activities) {
      activity.validate();
    }
  });

  test('activity contracts reject mismatched number-line targets', () {
    final activity = LocalLearningActivity(
      id: 'find_four',
      eyebrow: 'Numbers',
      title: 'Find four',
      instruction: 'Move to four.',
      domain: LearningDomain.math,
      tone: ActivityTone.gold,
      targetValue: 5,
      diagram: const DiagramSpec.numberLine(
        title: 'Find four',
        minimum: 0,
        maximum: 10,
        step: 1,
        start: 0,
        target: 4,
      ),
    );

    expect(activity.validate, throwsFormatException);
  });
}
