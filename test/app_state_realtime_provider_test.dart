import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/state/app_state.dart';
import 'package:ava/state/ava_session_state.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('response phases do not imply that the microphone turned off', () {
    const listening = AvaSessionState(
      turnState: AvaSessionPhase.listening,
      userMicIntent: true,
    );

    final thinking = listening.copyWith(turnState: AvaSessionPhase.thinking);
    final speaking = thinking.copyWith(turnState: AvaSessionPhase.speaking);
    final betweenTurns = speaking.copyWith(turnState: AvaSessionPhase.idle);

    expect(thinking.userMicIntent, isTrue);
    expect(speaking.userMicIntent, isTrue);
    expect(betweenTurns.userMicIntent, isTrue);
    expect(betweenTurns.isBusy, isTrue);
  });

  test('microphone intent is acknowledged before preparation completes',
      () async {
    final container = ProviderContainer(
      overrides: [
        avaRealtimeAgeBandProvider.overrideWithValue(
          AvaRealtimeAgeBand.middleChildhood,
        ),
      ],
    );
    addTearDown(container.dispose);

    final turn =
        container.read(avaSessionProvider.notifier).setMicrophoneActive(true);

    expect(
      container.read(avaSessionProvider).turnState,
      AvaSessionPhase.listening,
    );
    expect(container.read(avaSessionProvider).userMicIntent, isTrue);
    await expectLater(turn, throwsA(anything));
    expect(
      container.read(avaSessionProvider).turnState,
      AvaSessionPhase.failure,
    );
    expect(container.read(avaSessionProvider).userMicIntent, isFalse);
  });

  test('presentation changes do not replace the realtime session controller',
      () {
    final container = ProviderContainer();
    addTearDown(container.dispose);

    final controller = container.read(avaSessionProvider.notifier);
    final presentation =
        container.read(avaRealtimePresentationProvider.notifier);

    presentation.beginTurn(
      sessionId: 'session-provider-stability',
      turnId: 'turn-provider-stability',
    );

    expect(
      identical(controller, container.read(avaSessionProvider.notifier)),
      isTrue,
    );
    expect(
      container.read(avaSessionProvider).turnState,
      AvaSessionPhase.idle,
    );
  });
}
