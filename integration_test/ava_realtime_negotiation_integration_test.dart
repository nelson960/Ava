import 'package:ava/state/app_state.dart';
import 'package:ava/state/ava_session_state.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('live protocol-v3 WebRTC reaches listening', (tester) async {
    const enabled = bool.fromEnvironment(
      'AVA_LIVE_REALTIME_TESTS_ENABLED',
    );
    if (!enabled) {
      fail(
        'This live integration test requires '
        '--dart-define=AVA_LIVE_REALTIME_TESTS_ENABLED=true.',
      );
    }

    final container = ProviderContainer();
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: const SizedBox.shrink(),
      ),
    );

    await tester.runAsync(() async {
      final observedPhases = <AvaSessionPhase>[];
      Duration? warmStartToListening;
      for (var attempt = 0; attempt < 40; attempt += 1) {
        if (!container.read(studentProvider).isLoading) break;
        await Future<void>.delayed(const Duration(milliseconds: 50));
      }
      expect(container.read(studentProvider).isLoading, isFalse);
      final controller = container.read(avaSessionProvider.notifier);
      await controller.prepare().timeout(const Duration(seconds: 60));
      expect(container.read(avaSessionProvider).userMicIntent, isFalse);
      expect(
        container.read(avaSessionProvider).turnState,
        AvaSessionPhase.idle,
      );

      final warmStartClock = Stopwatch()..start();
      final phaseSubscription = container.listen(
        avaSessionProvider,
        (_, next) {
          observedPhases.add(next.turnState);
          if (next.turnState == AvaSessionPhase.listening &&
              warmStartToListening == null) {
            warmStartToListening = warmStartClock.elapsed;
          }
        },
        fireImmediately: true,
      );

      try {
        await controller
            .setMicrophoneActive(true)
            .timeout(const Duration(seconds: 1));

        expect(
          observedPhases,
          contains(AvaSessionPhase.listening),
          reason: 'current=${container.read(avaSessionProvider).turnState}',
        );
        expect(
          warmStartToListening,
          isNotNull,
          reason: 'The prepared connection never reached listening.',
        );
        expect(
          warmStartToListening!,
          lessThan(const Duration(seconds: 1)),
          reason: 'A prepared mic press must only enable the existing track.',
        );
      } finally {
        await controller.setMicrophoneActive(false);
        phaseSubscription.close();
        container.dispose();
      }
    });
  });
}
