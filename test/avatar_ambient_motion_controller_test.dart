import 'dart:async';
import 'dart:math' as math;

import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_ambient_motion_controller.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('schedules sparse blinks as independent ambient actions', () async {
    final timers = <_ManualTimer>[];
    final dispatched = <AvatarActionIntent>[];
    var now = const Duration(seconds: 3);
    final controller = AvatarAmbientMotionController(
      now: () => now,
      dispatch: (intent, occurredAt) async {
        expect(occurredAt, now);
        dispatched.add(intent);
      },
      onFailure: (error, stackTrace) {
        fail('Unexpected ambient motion failure: $error');
      },
      random: math.Random(7),
      timerFactory: (duration, callback) {
        final timer = _ManualTimer(duration, callback);
        timers.add(timer);
        return timer;
      },
    );
    addTearDown(controller.dispose);

    controller.start();
    expect(controller.isRunning, isTrue);
    expect(timers, hasLength(1));
    expect(
      timers.single.duration.inMicroseconds,
      inInclusiveRange(
        const Duration(milliseconds: 2800).inMicroseconds,
        const Duration(milliseconds: 6200).inMicroseconds,
      ),
    );

    timers.single.fire();
    await Future<void>.delayed(Duration.zero);
    await Future<void>.delayed(Duration.zero);

    expect(dispatched, hasLength(1));
    expect(dispatched.single.kind, AvatarActionKind.blink);
    expect(dispatched.single.source, AvatarActionSource.idleSystem);
    expect(
      dispatched.single.channelClaims,
      const {
        AvatarMotionChannel.faceExpression: AvatarMotionBlendMode.override,
      },
    );
    expect(timers, hasLength(2));

    now += const Duration(seconds: 4);
    timers.last.fire();
    await Future<void>.delayed(Duration.zero);
    await Future<void>.delayed(Duration.zero);
    expect(dispatched, hasLength(2));
    expect(dispatched.last.id, isNot(dispatched.first.id));
  });

  test('fails loudly and stops scheduling after dispatch failure', () async {
    final timers = <_ManualTimer>[];
    Object? failure;
    final controller = AvatarAmbientMotionController(
      now: () => Duration.zero,
      dispatch: (intent, occurredAt) {
        throw StateError('renderer unavailable');
      },
      onFailure: (error, stackTrace) => failure = error,
      random: math.Random(1),
      timerFactory: (duration, callback) {
        final timer = _ManualTimer(duration, callback);
        timers.add(timer);
        return timer;
      },
    );
    addTearDown(controller.dispose);

    controller.start();
    timers.single.fire();
    await Future<void>.delayed(Duration.zero);
    await Future<void>.delayed(Duration.zero);

    expect(failure, isA<StateError>());
    expect(controller.isRunning, isFalse);
    expect(timers, hasLength(1));
  });
}

class _ManualTimer implements Timer {
  _ManualTimer(this.duration, this._callback);

  final Duration duration;
  final void Function() _callback;
  bool _active = true;

  @override
  bool get isActive => _active;

  @override
  int get tick => 0;

  @override
  void cancel() {
    _active = false;
  }

  void fire() {
    if (!_active) return;
    _active = false;
    _callback();
  }
}
