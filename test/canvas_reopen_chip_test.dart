import 'package:ava/ui/dashboard/canvas_reopen_chip.dart';
import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets(
      'CanvasReopenChip renders circular button and fires callback on tap',
      (tester) async {
    var tapped = false;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: CanvasReopenChip(
            visible: true,
            onPressed: () => tapped = true,
          ),
        ),
      ),
    );

    expect(find.byIcon(CupertinoIcons.paintbrush_fill), findsOneWidget);
    expect(
      find.ancestor(
        of: find.byIcon(CupertinoIcons.paintbrush_fill),
        matching: find.byType(RotationTransition),
      ),
      findsAtLeast(1),
    );
    await tester.tap(find.byType(InkWell));
    expect(tapped, isTrue);
  });
}
