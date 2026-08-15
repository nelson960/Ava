import 'package:ava/ui/world/living_world.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('local time resolves continuous approved day phases', () {
    expect(
      LivingWorldState.fromLocalTime(DateTime(2026, 7, 27, 6)).phase,
      LivingWorldDayPhase.dawn,
    );
    expect(
      LivingWorldState.fromLocalTime(DateTime(2026, 7, 27, 12)).phase,
      LivingWorldDayPhase.day,
    );
    expect(
      LivingWorldState.fromLocalTime(DateTime(2026, 7, 27, 18)).phase,
      LivingWorldDayPhase.dusk,
    );
    final night = LivingWorldState.fromLocalTime(DateTime(2026, 7, 27, 23, 30));
    expect(night.phase, LivingWorldDayPhase.night);
    expect(night.moonOpacity, greaterThan(0.8));
    expect(night.starOpacity, greaterThan(0.8));
    expect(night.sunOpacity, 0);
  });

  test('celestial reactions are bounded by visibility and cooldown', () {
    var now = DateTime(2026, 7, 27, 12);
    final controller = LivingWorldController(
      vsync: const TestVSync(),
      clock: () => now,
    );
    addTearDown(controller.dispose);

    expect(controller.interact(LivingWorldElement.moon), isFalse);
    expect(controller.interact(LivingWorldElement.sun), isTrue);
    expect(controller.interact(LivingWorldElement.sun), isFalse);

    now = now.add(LivingWorldController.interactionCooldown);
    expect(controller.interact(LivingWorldElement.sun), isTrue);
  });

  testWidgets(
    'only explicit world objects intercept input above the avatar surface',
    (tester) async {
      final controller = LivingWorldController(
        vsync: tester,
        clock: () => DateTime(2026, 7, 27, 12),
      );
      var underlyingTaps = 0;

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SizedBox.expand(
              child: Stack(
                fit: StackFit.expand,
                children: [
                  GestureDetector(
                    key: const ValueKey('avatar-input-surface'),
                    behavior: HitTestBehavior.opaque,
                    onTap: () => underlyingTaps += 1,
                  ),
                  LivingWorldInteractionLayer(controller: controller),
                ],
              ),
            ),
          ),
        ),
      );

      expect(find.byKey(const ValueKey('living-world-sun')), findsOneWidget);
      expect(find.byKey(const ValueKey('living-world-moon')), findsNothing);

      await tester.tapAt(const Offset(400, 480));
      await tester.pump();
      expect(underlyingTaps, 1);

      await tester.tap(find.byKey(const ValueKey('living-world-sun')));
      await tester.pump();
      expect(underlyingTaps, 1);
      expect(controller.reactingElement, LivingWorldElement.sun);
      controller.dispose();
    },
  );

  testWidgets('night exposes moon and star touch targets', (tester) async {
    final controller = LivingWorldController(
      vsync: tester,
      clock: () => DateTime(2026, 7, 27, 23, 30),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: LivingWorldInteractionLayer(controller: controller),
      ),
    );

    expect(find.byKey(const ValueKey('living-world-sun')), findsNothing);
    expect(find.byKey(const ValueKey('living-world-moon')), findsOneWidget);
    expect(
        find.byKey(const ValueKey('living-world-star-left')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('living-world-star-center')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('living-world-star-right')),
      findsOneWidget,
    );
    controller.dispose();
  });
}
