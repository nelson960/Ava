import 'dart:async';

import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_motion_driver.dart';
import 'package:ava/ui/avatar/avatar_motion_scheduler.dart';
import 'package:ava/ui/avatar/avatar_performance_controller.dart';

void main() {
  late _FakeMotionDriver driver;
  late List<Object> failures;
  late AvatarPerformanceController controller;

  setUp(() {
    driver = _FakeMotionDriver();
    failures = [];
    controller = AvatarPerformanceController(
      driver: driver,
      onFailure: (error, _) => failures.add(error),
    );
  });

  tearDown(() async {
    await controller.dispose();
  });

  test('rejects a renderer that cannot satisfy production interaction', () {
    driver.capabilitiesOverride = const AvatarMotionDriverCapabilities(
      layeredSkeletalAnimation: false,
      additiveAnimation: false,
      proceduralBoneControl: false,
      morphTargetControl: true,
      skinnedSurfacePicking: true,
      cameraPoseReporting: false,
      maximumConcurrentActions: 1,
    );

    expect(controller.initialize, throwsUnsupportedError);
  });

  test('applies starts, retargets, and queued promotion to the driver',
      () async {
    await controller.initialize();
    await controller.dispatch(
      _intent(
        id: 'leg-1',
        retargetGroup: 'touch:leftLeg',
        retargetPolicy: AvatarRetargetPolicy.immediate,
      ),
      now: Duration.zero,
    );
    await controller.dispatch(
      _intent(
        id: 'leg-2',
        retargetGroup: 'touch:leftLeg',
        retargetPolicy: AvatarRetargetPolicy.immediate,
      ),
      now: const Duration(milliseconds: 80),
    );
    await controller.dispatch(
      _intent(
        id: 'leg-command',
        source: AvatarActionSource.command,
        priority: AvatarActionPriority.command,
      ),
      now: const Duration(milliseconds: 100),
    );

    expect(driver.commands, const ['start:leg-1', 'retarget:leg-1@1']);
    expect(controller.queuedIntents.single.id, 'leg-command');

    driver.complete('leg-1', const Duration(milliseconds: 600));
    await Future<void>.delayed(Duration.zero);

    expect(driver.commands.last, 'start:leg-command');
    expect(controller.activeActions.single.runtimeId, 'leg-command');
    expect(failures, isEmpty);
  });

  test('a driver failure is surfaced and stops new dispatches', () async {
    await controller.initialize();
    driver.fail('leg', 'IK solver diverged');
    await Future<void>.delayed(Duration.zero);

    expect(failures.single, isA<StateError>());
    await expectLater(
      controller.dispatch(_intent(id: 'next'), now: Duration.zero),
      throwsStateError,
    );
  });

  test('completion arriving during cancellation is one terminal transition',
      () async {
    await controller.initialize();
    await controller.dispatch(
      _intent(id: 'speech-ending'),
      now: Duration.zero,
    );
    final stopStarted = Completer<void>();
    final stopGate = Completer<void>();
    driver
      ..stopStarted = stopStarted
      ..stopGate = stopGate;

    final cancellation = controller.cancelSource(
      AvatarActionSource.touch,
      now: const Duration(milliseconds: 600),
    );
    await stopStarted.future;
    driver.complete('speech-ending', const Duration(milliseconds: 600));
    await Future<void>.delayed(Duration.zero);
    stopGate.complete();
    await cancellation;

    expect(controller.activeActions, isEmpty);
    expect(failures, isEmpty);
  });
}

AvatarActionIntent _intent({
  required String id,
  AvatarActionSource source = AvatarActionSource.touch,
  AvatarActionPriority priority = AvatarActionPriority.touch,
  AvatarRetargetPolicy retargetPolicy = AvatarRetargetPolicy.never,
  String? retargetGroup,
}) {
  return AvatarActionIntent(
    id: id,
    kind: AvatarActionKind.recoil,
    source: source,
    priority: priority,
    channelClaims: const {
      AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
    },
    occurredAt: Duration.zero,
    blendIn: const Duration(milliseconds: 60),
    blendOut: const Duration(milliseconds: 120),
    expectedDuration: const Duration(milliseconds: 600),
    interruptionPolicy: AvatarInterruptionPolicy.mustComplete,
    retargetPolicy: retargetPolicy,
    retargetGroup: retargetGroup,
  );
}

class _FakeMotionDriver implements AvatarMotionDriver {
  final StreamController<AvatarMotionDriverEvent> _events =
      StreamController.broadcast();
  final List<String> commands = [];

  AvatarMotionDriverCapabilities? capabilitiesOverride;
  Completer<void>? stopStarted;
  Completer<void>? stopGate;

  @override
  AvatarMotionDriverCapabilities get capabilities =>
      capabilitiesOverride ??
      const AvatarMotionDriverCapabilities(
        layeredSkeletalAnimation: true,
        additiveAnimation: true,
        proceduralBoneControl: true,
        morphTargetControl: true,
        skinnedSurfacePicking: true,
        cameraPoseReporting: true,
        maximumConcurrentActions: 16,
      );

  @override
  Stream<AvatarMotionDriverEvent> get events => _events.stream;

  @override
  Future<void> initialize() async {}

  @override
  Future<void> start(AvatarScheduledAction action) async {
    commands.add('start:${action.runtimeId}');
  }

  @override
  Future<void> retarget(AvatarScheduledAction action) async {
    commands.add('retarget:${action.runtimeId}@${action.revision}');
  }

  @override
  Future<void> stop(String runtimeId, {required Duration blendOut}) async {
    commands.add('stop:$runtimeId');
    stopStarted?.complete();
    await stopGate?.future;
  }

  void complete(String runtimeId, Duration occurredAt) {
    _events.add(
      AvatarMotionCompleted(runtimeId: runtimeId, occurredAt: occurredAt),
    );
  }

  void fail(String runtimeId, String message) {
    _events.add(AvatarMotionFailed(runtimeId: runtimeId, message: message));
  }

  @override
  Future<void> dispose() => _events.close();
}
