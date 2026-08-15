import 'dart:async';

import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_animation_coordinator.dart';

void main() {
  late List<_AnimationCommand> commands;
  late List<_ManualTimer> timers;
  late List<Object> failures;
  late AvatarAnimationCoordinator coordinator;
  late int nextPlaybackId;

  setUp(() {
    commands = [];
    timers = [];
    failures = [];
    nextPlaybackId = 0;
    coordinator = AvatarAnimationCoordinator(
      idleAnimation: 'Idle',
      explainingAnimation: 'Explain',
      playAnimation: ({
        required animationName,
        required loopCount,
        required playbackRate,
      }) {
        final playbackId = ++nextPlaybackId;
        commands.add(
          _AnimationCommand(
            playbackId,
            animationName,
            loopCount,
            playbackRate,
          ),
        );
        return playbackId;
      },
      onPlaybackFailure: (error, _) => failures.add(error),
      timerFactory: (duration, callback) {
        final timer = _ManualTimer(duration, callback);
        timers.add(timer);
        return timer;
      },
    );
  });

  tearDown(() {
    coordinator.dispose();
  });

  test('starts the requested looping base animation when marked ready', () {
    coordinator.setBaseAnimation(AvatarBaseAnimation.explaining);
    expect(commands, isEmpty);

    coordinator.markReady();

    expect(commands, const [_AnimationCommand(1, 'Explain', 0, 1)]);
    expect(
      coordinator.state,
      AvatarAnimationPlaybackState.explaining,
    );
  });

  test('returns to base only after the matching finished event', () {
    coordinator.markReady();
    coordinator.playReaction(_reaction('Head'));

    expect(commands.last, const _AnimationCommand(2, 'Head', 1, 1));
    expect(coordinator.state, AvatarAnimationPlaybackState.reacting);

    expect(
      coordinator.handleAnimationFinished(
        playbackId: 99,
        animationName: 'Head',
      ),
      isFalse,
    );
    expect(commands, hasLength(2));

    expect(
      coordinator.handleAnimationFinished(
        playbackId: 2,
        animationName: 'Head',
      ),
      isTrue,
    );
    expect(commands.last, const _AnimationCommand(3, 'Idle', 0, 1));
    expect(coordinator.state, AvatarAnimationPlaybackState.idle);
    expect(timers.single.isActive, isFalse);
  });

  test('latest reaction ignores completion from the replaced playback', () {
    coordinator.markReady();
    coordinator.playReaction(_reaction('Head'));
    coordinator.playReaction(_reaction('Torso'));

    expect(timers.first.isActive, isFalse);
    expect(timers.last.isActive, isTrue);
    expect(
      coordinator.handleAnimationFinished(
        playbackId: 2,
        animationName: 'Head',
      ),
      isFalse,
    );
    expect(commands.last, const _AnimationCommand(3, 'Torso', 1, 1));

    expect(
      coordinator.handleAnimationFinished(
        playbackId: 3,
        animationName: 'Torso',
      ),
      isTrue,
    );
    expect(commands.last, const _AnimationCommand(4, 'Idle', 0, 1));
  });

  test('reaction returns to explain when base changes while reacting', () {
    coordinator.markReady();
    coordinator.playReaction(_reaction('Arm'));

    coordinator.setBaseAnimation(AvatarBaseAnimation.explaining);
    expect(commands.last, const _AnimationCommand(2, 'Arm', 1, 1));

    coordinator.handleAnimationFinished(
      playbackId: 2,
      animationName: 'Arm',
    );
    expect(commands.last, const _AnimationCommand(3, 'Explain', 0, 1));
    expect(
      coordinator.state,
      AvatarAnimationPlaybackState.explaining,
    );
  });

  test('procedural takeover cancels a finite clip and resumes the base', () {
    coordinator.markReady();
    coordinator.playReaction(_reaction('Leg'));

    expect(coordinator.cancelReactionAndResumeBase(), isTrue);
    expect(commands.last, const _AnimationCommand(3, 'Idle', 0, 1));
    expect(coordinator.state, AvatarAnimationPlaybackState.idle);
    expect(timers.single.isActive, isFalse);
    expect(coordinator.cancelReactionAndResumeBase(), isFalse);
  });

  test('accepts thirty consecutive reactions without locking', () {
    coordinator.markReady();

    for (var index = 0; index < 30; index += 1) {
      coordinator.playReaction(_reaction('Reaction$index'));
    }

    expect(commands, hasLength(31));
    expect(commands.last, const _AnimationCommand(31, 'Reaction29', 1, 1));
    expect(timers.where((timer) => timer.isActive), hasLength(1));

    coordinator.handleAnimationFinished(
      playbackId: 31,
      animationName: 'Reaction29',
    );
    expect(commands.last, const _AnimationCommand(32, 'Idle', 0, 1));
    expect(coordinator.state, AvatarAnimationPlaybackState.idle);
  });

  test('watchdog reports a missing completion instead of guessing', () {
    coordinator.markReady();
    coordinator.playReaction(_reaction('Head'));

    timers.single.fire();

    expect(coordinator.state, AvatarAnimationPlaybackState.error);
    expect(coordinator.isReady, isFalse);
    expect(failures, hasLength(1));
    expect(failures.single, isA<StateError>());
    expect(commands.last, const _AnimationCommand(2, 'Head', 1, 1));
  });

  test('fails visibly when the animation driver rejects a command', () {
    coordinator.dispose();
    coordinator = AvatarAnimationCoordinator(
      idleAnimation: 'Idle',
      explainingAnimation: 'Explain',
      playAnimation: ({
        required animationName,
        required loopCount,
        required playbackRate,
      }) {
        throw StateError('viewer unavailable');
      },
      onPlaybackFailure: (error, _) => failures.add(error),
    );

    expect(coordinator.markReady, throwsStateError);
    expect(coordinator.state, AvatarAnimationPlaybackState.error);
    expect(coordinator.isReady, isFalse);
  });
}

AvatarReactionAnimation _reaction(String name) {
  return AvatarReactionAnimation(
    name: name,
    completionTimeout: const Duration(seconds: 3),
  );
}

class _AnimationCommand {
  const _AnimationCommand(
    this.playbackId,
    this.name,
    this.loopCount,
    this.playbackRate,
  );

  final int playbackId;
  final String name;
  final int loopCount;
  final double playbackRate;

  @override
  bool operator ==(Object other) {
    return other is _AnimationCommand &&
        other.playbackId == playbackId &&
        other.name == name &&
        other.loopCount == loopCount &&
        other.playbackRate == playbackRate;
  }

  @override
  int get hashCode => Object.hash(playbackId, name, loopCount, playbackRate);

  @override
  String toString() => '$playbackId:$name($loopCount)@$playbackRate';
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
