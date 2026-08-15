import 'dart:async';

import 'package:ava/ui/avatar/ava_filament_motion_driver.dart';
import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_motion_driver.dart';
import 'package:ava/ui/avatar/avatar_motion_scheduler.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('requires an attached validated renderer before initialization', () {
    final sink = _FakeMotionSink()..isMotionLayerReady = false;
    final driver = AvaFilamentMotionDriver(sink: sink);

    expect(driver.initialize, throwsStateError);
  });

  test('reports validated sink capabilities instead of hard-coded support', () {
    final sink = _FakeMotionSink()
      ..capabilitiesOverride = const AvatarMotionDriverCapabilities(
        layeredSkeletalAnimation: true,
        additiveAnimation: true,
        proceduralBoneControl: true,
        morphTargetControl: false,
        skinnedSurfacePicking: true,
        cameraPoseReporting: true,
        maximumConcurrentActions: 12,
      );
    final driver = AvaFilamentMotionDriver(sink: sink);

    expect(driver.capabilities.morphTargetControl, isFalse);
    expect(driver.capabilities.maximumConcurrentActions, 12);
    expect(driver.capabilities.supportsProductionInteraction, isFalse);
    expect(driver.initialize, throwsUnsupportedError);
  });

  test('starts and stops independent renderer-owned layers', () async {
    final sink = _FakeMotionSink(completesAfter: null);
    final driver = AvaFilamentMotionDriver(sink: sink);
    await driver.initialize();
    final head = AvatarScheduledAction.start(
      _intent('head', AvatarMotionChannel.head),
      Duration.zero,
    );
    final hand = AvatarScheduledAction.start(
      _intent('hand', AvatarMotionChannel.leftHand),
      Duration.zero,
    );

    await driver.start(head);
    await driver.start(hand);
    await driver.stop(
      head.runtimeId,
      blendOut: const Duration(milliseconds: 120),
    );

    expect(driver.activeLayerCount, 1);
    expect(sink.active.keys, {'hand'});
    expect(
      sink.commands,
      ['start:head@0', 'start:hand@0', 'stop:head'],
    );
    await driver.dispose();
  });

  test('accepts eight concurrent layers and rejects a ninth', () async {
    final sink = _FakeMotionSink(completesAfter: null);
    final driver = AvaFilamentMotionDriver(
      sink: sink,
      maximumConcurrentActions: 8,
    );
    await driver.initialize();

    for (var index = 0; index < 8; index += 1) {
      await driver.start(
        AvatarScheduledAction.start(
          _intent(
            'layer-$index',
            AvatarMotionChannel.values[index],
          ),
          Duration.zero,
        ),
      );
    }

    expect(driver.activeLayerCount, 8);
    await expectLater(
      driver.start(
        AvatarScheduledAction.start(
          _intent('layer-9', AvatarMotionChannel.rightLeg),
          Duration.zero,
        ),
      ),
      throwsStateError,
    );
    await driver.dispose();
  });

  test('retarget cancels stale completion and completes the latest revision',
      () async {
    final timers = <_FakeTimer>[];
    var now = Duration.zero;
    final sink = _FakeMotionSink(
      completesAfter: const Duration(milliseconds: 500),
    );
    final driver = AvaFilamentMotionDriver(
      sink: sink,
      clock: () => now,
      timerFactory: (duration, callback) {
        final timer = _FakeTimer(duration, callback);
        timers.add(timer);
        return timer;
      },
    );
    final events = <AvatarMotionDriverEvent>[];
    final subscription = driver.events.listen(events.add);
    await driver.initialize();
    final first = AvatarScheduledAction.start(
      _intent(
        'touch-1',
        AvatarMotionChannel.leftHand,
        retargetGroup: 'touch:leftHand',
      ),
      Duration.zero,
    );
    await driver.start(first);
    final revised = first.retarget(
      _intent(
        'touch-2',
        AvatarMotionChannel.leftHand,
        retargetGroup: 'touch:leftHand',
      ),
      const Duration(milliseconds: 80),
    );
    await driver.retarget(revised);

    timers.first.fireIgnoringCancellation();
    await Future<void>.delayed(Duration.zero);
    expect(sink.commands, isNot(contains('complete:touch-1@0')));
    expect(events, isEmpty);

    now = const Duration(milliseconds: 580);
    timers.last.fire();
    await Future<void>.delayed(Duration.zero);

    expect(sink.commands.last, 'complete:touch-1@1');
    expect(driver.activeLayerCount, 0);
    expect(events.single, isA<AvatarMotionCompleted>());
    expect((events.single as AvatarMotionCompleted).occurredAt, now);

    await subscription.cancel();
    await driver.dispose();
  });

  test('end-of-turn stop coalesces with native completion in flight', () async {
    late _FakeTimer timer;
    final completionStarted = Completer<void>();
    final completionGate = Completer<void>();
    final sink = _FakeMotionSink(
      completesAfter: const Duration(milliseconds: 100),
    )
      ..completionStarted = completionStarted
      ..completionGate = completionGate;
    final driver = AvaFilamentMotionDriver(
      sink: sink,
      timerFactory: (duration, callback) {
        timer = _FakeTimer(duration, callback);
        return timer;
      },
    );
    final events = <AvatarMotionDriverEvent>[];
    final subscription = driver.events.listen(events.add);
    await driver.initialize();
    final action = AvatarScheduledAction.start(
      _intent('speech', AvatarMotionChannel.lipSync),
      Duration.zero,
    );
    await driver.start(action);

    timer.fire();
    await completionStarted.future;
    final stop = driver.stop(
      action.runtimeId,
      blendOut: const Duration(milliseconds: 60),
    );
    completionGate.complete();
    await stop;

    expect(driver.activeLayerCount, 0);
    expect(sink.commands, contains('complete:speech@0'));
    expect(sink.commands, isNot(contains('stop:speech')));
    expect(events, isEmpty);
    await subscription.cancel();
    await driver.dispose();
  });

  test('retarget restarts a layer completed natively while revision was queued',
      () async {
    late _FakeTimer timer;
    final completionStarted = Completer<void>();
    final completionGate = Completer<void>();
    final sink = _FakeMotionSink(
      completesAfter: const Duration(milliseconds: 100),
    )
      ..completionStarted = completionStarted
      ..completionGate = completionGate;
    final driver = AvaFilamentMotionDriver(
      sink: sink,
      timerFactory: (duration, callback) {
        timer = _FakeTimer(duration, callback);
        return timer;
      },
    );
    final events = <AvatarMotionDriverEvent>[];
    final subscription = driver.events.listen(events.add);
    await driver.initialize();
    final first = AvatarScheduledAction.start(
      _intent(
        'gaze-1',
        AvatarMotionChannel.gaze,
        retargetGroup: 'response:gaze',
      ),
      Duration.zero,
    );
    await driver.start(first);

    timer.fire();
    await completionStarted.future;
    final revised = first.retarget(
      _intent(
        'gaze-2',
        AvatarMotionChannel.gaze,
        retargetGroup: 'response:gaze',
      ),
      const Duration(milliseconds: 100),
    );
    final retarget = driver.retarget(revised);
    completionGate.complete();
    await retarget;

    expect(driver.activeLayerCount, 1);
    expect(
        sink.commands,
        containsAllInOrder([
          'start:gaze-1@0',
          'complete:gaze-1@0',
          'start:gaze-1@1',
        ]));
    expect(events, isEmpty);
    await subscription.cancel();
    await driver.dispose();
  });

  test('surfaces asynchronous native completion failure', () async {
    late _FakeTimer timer;
    final sink = _FakeMotionSink(
      completesAfter: const Duration(milliseconds: 100),
    )..failCompletion = true;
    final driver = AvaFilamentMotionDriver(
      sink: sink,
      timerFactory: (duration, callback) {
        timer = _FakeTimer(duration, callback);
        return timer;
      },
    );
    final events = <AvatarMotionDriverEvent>[];
    final subscription = driver.events.listen(events.add);
    await driver.initialize();
    await driver.start(
      AvatarScheduledAction.start(
        _intent('face', AvatarMotionChannel.faceExpression),
        Duration.zero,
      ),
    );

    timer.fire();
    await Future<void>.delayed(Duration.zero);

    expect(events.single, isA<AvatarMotionFailed>());
    expect(driver.activeLayerCount, 0);

    await subscription.cancel();
    await driver.dispose();
  });
}

AvatarActionIntent _intent(
  String id,
  AvatarMotionChannel channel, {
  String? retargetGroup,
}) {
  return AvatarActionIntent(
    id: id,
    kind: AvatarActionKind.expression,
    source: AvatarActionSource.touch,
    priority: AvatarActionPriority.touch,
    channelClaims: {channel: AvatarMotionBlendMode.override},
    occurredAt: Duration.zero,
    blendIn: const Duration(milliseconds: 60),
    blendOut: const Duration(milliseconds: 120),
    expectedDuration: const Duration(milliseconds: 500),
    interruptionPolicy: AvatarInterruptionPolicy.interruptible,
    retargetPolicy: retargetGroup == null
        ? AvatarRetargetPolicy.never
        : AvatarRetargetPolicy.immediate,
    retargetGroup: retargetGroup,
  );
}

class _FakeMotionSink implements AvaRendererMotionLayerSink {
  _FakeMotionSink({this.completesAfter});

  @override
  bool isMotionLayerReady = true;

  AvatarMotionDriverCapabilities? capabilitiesOverride;

  @override
  AvatarMotionDriverCapabilities get motionCapabilities =>
      capabilitiesOverride ??
      AvatarMotionDriverCapabilities(
        layeredSkeletalAnimation: isMotionLayerReady,
        additiveAnimation: isMotionLayerReady,
        proceduralBoneControl: isMotionLayerReady,
        morphTargetControl: isMotionLayerReady,
        skinnedSurfacePicking: isMotionLayerReady,
        cameraPoseReporting: isMotionLayerReady,
        maximumConcurrentActions: 16,
      );
  final Duration? completesAfter;
  final Map<String, int> active = {};
  final List<String> commands = [];
  bool failCompletion = false;
  Completer<void>? completionStarted;
  Completer<void>? completionGate;

  @override
  Future<AvaRendererLayerReceipt> startMotionLayer(
    AvatarScheduledAction action,
  ) async {
    active[action.runtimeId] = action.revision;
    commands.add('start:${action.runtimeId}@${action.revision}');
    return _receipt(action);
  }

  @override
  Future<AvaRendererLayerReceipt> retargetMotionLayer(
    AvatarScheduledAction action,
  ) async {
    if (!active.containsKey(action.runtimeId)) {
      throw StateError('missing layer');
    }
    active[action.runtimeId] = action.revision;
    commands.add('retarget:${action.runtimeId}@${action.revision}');
    return _receipt(action);
  }

  AvaRendererLayerReceipt _receipt(AvatarScheduledAction action) {
    return AvaRendererLayerReceipt(
      runtimeId: action.runtimeId,
      revision: action.revision,
      completesAfter: completesAfter,
    );
  }

  @override
  Future<void> stopMotionLayer(
    String runtimeId, {
    required Duration blendOut,
    required Duration occurredAt,
  }) async {
    if (active.remove(runtimeId) == null) throw StateError('missing layer');
    commands.add('stop:$runtimeId');
  }

  @override
  Future<void> completeMotionLayer(
    String runtimeId, {
    required int revision,
    required Duration occurredAt,
  }) async {
    if (failCompletion) throw StateError('native completion failed');
    if (active[runtimeId] != revision) throw StateError('stale layer');
    completionStarted?.complete();
    await completionGate?.future;
    active.remove(runtimeId);
    commands.add('complete:$runtimeId@$revision');
  }

  @override
  Future<void> clearMotionLayers() async {
    active.clear();
    commands.add('clear');
  }
}

class _FakeTimer implements Timer {
  _FakeTimer(this.duration, this.callback);

  final Duration duration;
  final void Function() callback;
  bool _active = true;

  @override
  bool get isActive => _active;

  @override
  int get tick => _active ? 0 : 1;

  @override
  void cancel() {
    _active = false;
  }

  void fire() {
    if (!_active) return;
    _active = false;
    callback();
  }

  void fireIgnoringCancellation() {
    callback();
  }
}
