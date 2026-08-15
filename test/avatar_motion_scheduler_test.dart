import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_motion_scheduler.dart';

void main() {
  late AvatarMotionScheduler scheduler;

  setUp(() {
    scheduler = AvatarMotionScheduler();
  });

  test('different limbs and lip sync remain active concurrently', () {
    scheduler.submit(
      _intent(
        id: 'left-leg',
        claims: const {
          AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        },
      ),
      now: Duration.zero,
    );
    scheduler.submit(
      _intent(
        id: 'right-hand',
        claims: const {
          AvatarMotionChannel.rightHand: AvatarMotionBlendMode.override,
        },
      ),
      now: const Duration(milliseconds: 10),
    );
    scheduler.submit(
      _intent(
        id: 'speech',
        claims: const {
          AvatarMotionChannel.lipSync: AvatarMotionBlendMode.override,
        },
        source: AvatarActionSource.speech,
        priority: AvatarActionPriority.lesson,
      ),
      now: const Duration(milliseconds: 20),
    );

    expect(
      scheduler.activeActions.map((action) => action.runtimeId),
      containsAll(const ['left-leg', 'right-hand', 'speech']),
    );
    expect(scheduler.queuedIntents, isEmpty);
  });

  test('same-limb input retargets without restarting its timeline', () {
    final first = _intent(
      id: 'calf-1',
      claims: const {
        AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
      },
      retargetGroup: 'touch:leftLeg',
      retargetPolicy: AvatarRetargetPolicy.immediate,
    );
    final second = _intent(
      id: 'calf-2',
      claims: const {
        AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
      },
      retargetGroup: 'touch:leftLeg',
      retargetPolicy: AvatarRetargetPolicy.immediate,
    );
    scheduler.submit(first, now: const Duration(seconds: 1));

    final result = scheduler.submit(
      second,
      now: const Duration(milliseconds: 1120),
    );

    expect(result.type, AvatarScheduleResultType.retargeted);
    expect(result.activeAction?.runtimeId, 'calf-1');
    expect(result.activeAction?.intent.id, 'calf-2');
    expect(result.activeAction?.startedAt, const Duration(seconds: 1));
    expect(result.activeAction?.revision, 1);
    expect(scheduler.activeActions, hasLength(1));
  });

  test('a planted lower-body action queues conflicting movement', () {
    scheduler.submit(
      _intent(
        id: 'plant-left',
        claims: const {
          AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
          AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
        },
        interruptionPolicy: AvatarInterruptionPolicy.mustComplete,
      ),
      now: Duration.zero,
    );

    final queued = scheduler.submit(
      _intent(
        id: 'step-right',
        claims: const {
          AvatarMotionChannel.rightLeg: AvatarMotionBlendMode.override,
          AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
        },
        source: AvatarActionSource.command,
        priority: AvatarActionPriority.command,
      ),
      now: const Duration(milliseconds: 120),
    );

    expect(queued.type, AvatarScheduleResultType.queued);
    expect(scheduler.activeActions.single.runtimeId, 'plant-left');
    expect(scheduler.queuedIntents.single.id, 'step-right');

    final completion = scheduler.complete(
      'plant-left',
      now: const Duration(milliseconds: 600),
    );
    expect(completion.startedActions.single.runtimeId, 'step-right');
    expect(scheduler.activeActions.single.runtimeId, 'step-right');
  });

  test('queued repeated input coalesces to the newest target', () {
    scheduler.submit(
      _intent(
        id: 'protected-step',
        claims: const {
          AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
          AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        },
        interruptionPolicy: AvatarInterruptionPolicy.mustComplete,
      ),
      now: Duration.zero,
    );
    scheduler.submit(
      _intent(
        id: 'touch-1',
        claims: const {
          AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
          AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        },
        retargetGroup: 'touch:leftLeg',
        retargetPolicy: AvatarRetargetPolicy.immediate,
      ),
      now: const Duration(milliseconds: 20),
    );

    final result = scheduler.submit(
      _intent(
        id: 'touch-2',
        claims: const {
          AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
          AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        },
        retargetGroup: 'touch:leftLeg',
        retargetPolicy: AvatarRetargetPolicy.immediate,
      ),
      now: const Duration(milliseconds: 40),
    );

    expect(result.type, AvatarScheduleResultType.queueCoalesced);
    expect(result.coalescedIntent?.id, 'touch-1');
    expect(scheduler.queuedIntents.single.id, 'touch-2');
  });

  test('higher-priority action replaces only after the minimum hold', () {
    scheduler.submit(
      _intent(
        id: 'touch-recovery',
        claims: const {
          AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
        },
        interruptionPolicy: AvatarInterruptionPolicy.afterMinimumHold,
        minimumHold: const Duration(milliseconds: 300),
      ),
      now: Duration.zero,
    );
    final early = scheduler.submit(
      _intent(
        id: 'lesson-early',
        claims: const {
          AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
        },
        source: AvatarActionSource.lesson,
        priority: AvatarActionPriority.lesson,
      ),
      now: const Duration(milliseconds: 100),
    );
    expect(early.type, AvatarScheduleResultType.queued);

    scheduler.complete(
      'touch-recovery',
      now: const Duration(milliseconds: 320),
    );
    expect(scheduler.activeActions.single.runtimeId, 'lesson-early');

    final command = scheduler.submit(
      _intent(
        id: 'command',
        claims: const {
          AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
        },
        source: AvatarActionSource.command,
        priority: AvatarActionPriority.command,
      ),
      now: const Duration(milliseconds: 700),
    );
    expect(command.type, AvatarScheduleResultType.replaced);
    expect(command.stoppedActions.single.runtimeId, 'lesson-early');
  });

  test('additive secondary motion does not displace a base action', () {
    scheduler.submit(
      _intent(
        id: 'base',
        claims: const {
          AvatarMotionChannel.spine: AvatarMotionBlendMode.override,
        },
        source: AvatarActionSource.idleSystem,
        priority: AvatarActionPriority.idle,
      ),
      now: Duration.zero,
    );
    final breathing = scheduler.submit(
      _intent(
        id: 'breathing',
        claims: const {
          AvatarMotionChannel.spine: AvatarMotionBlendMode.additive,
        },
        source: AvatarActionSource.idleSystem,
        priority: AvatarActionPriority.ambient,
      ),
      now: const Duration(milliseconds: 10),
    );

    expect(breathing.type, AvatarScheduleResultType.started);
    expect(scheduler.activeActions, hasLength(2));
  });
}

AvatarActionIntent _intent({
  required String id,
  required Map<AvatarMotionChannel, AvatarMotionBlendMode> claims,
  AvatarActionSource source = AvatarActionSource.touch,
  AvatarActionPriority priority = AvatarActionPriority.touch,
  AvatarInterruptionPolicy interruptionPolicy =
      AvatarInterruptionPolicy.interruptible,
  AvatarRetargetPolicy retargetPolicy = AvatarRetargetPolicy.never,
  String? retargetGroup,
  Duration minimumHold = Duration.zero,
}) {
  return AvatarActionIntent(
    id: id,
    kind: AvatarActionKind.recoil,
    source: source,
    priority: priority,
    channelClaims: claims,
    occurredAt: Duration.zero,
    blendIn: const Duration(milliseconds: 80),
    blendOut: const Duration(milliseconds: 160),
    expectedDuration: const Duration(milliseconds: 800),
    minimumHold: minimumHold,
    interruptionPolicy: interruptionPolicy,
    retargetPolicy: retargetPolicy,
    retargetGroup: retargetGroup,
  );
}
