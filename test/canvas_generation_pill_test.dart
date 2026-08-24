import 'package:ava/ui/dashboard/canvas_generation_pill.dart';
import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('CanvasGenerationPill renders circular indicator and toggles scale/opacity',
      (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: CanvasGenerationPill(visible: false),
        ),
      ),
    );

    expect(find.byIcon(CupertinoIcons.paintbrush_fill), findsOneWidget);
    final hiddenOpacity = tester.widget<AnimatedOpacity>(
      find.byType(AnimatedOpacity),
    );
    expect(hiddenOpacity.opacity, equals(0.0));

    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: CanvasGenerationPill(visible: true),
        ),
      ),
    );
    await tester.pump(const Duration(milliseconds: 400));

    final visibleOpacity = tester.widget<AnimatedOpacity>(
      find.byType(AnimatedOpacity),
    );
    expect(visibleOpacity.opacity, equals(1.0));
  });
}
