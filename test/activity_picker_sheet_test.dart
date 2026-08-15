import 'package:ava/models/local_learning_activity.dart';
import 'package:ava/ui/dashboard/activity_picker_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('activity sheet presents varied offline activities cleanly',
      (tester) async {
    final catalog = await LocalLearningActivityCatalog.load(rootBundle);
    LocalLearningActivity? selected;
    var progressOpened = false;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ActivityPickerSheet(
            activities: catalog.activities,
            onSelected: (activity) => selected = activity,
            onOpenProgress: () => progressOpened = true,
          ),
        ),
      ),
    );

    expect(find.text('Try something together'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('activity-build_three_quarters')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('activity-build_two_fifths')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('activity-find_six')), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(
      find.byKey(const ValueKey('activity-build_three_quarters')),
    );
    expect(selected?.id, 'build_three_quarters');

    await tester.tap(find.byKey(const ValueKey('open-learning-trail')));
    expect(progressOpened, isTrue);
  });
}
