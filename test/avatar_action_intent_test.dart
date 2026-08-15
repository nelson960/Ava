import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_action_intent.dart';

void main() {
  test('override claims conflict but additive claims compose', () {
    final base = _intent(
      id: 'base',
      claims: const {
        AvatarMotionChannel.spine: AvatarMotionBlendMode.override,
      },
    );
    final reaction = _intent(
      id: 'reaction',
      claims: const {
        AvatarMotionChannel.spine: AvatarMotionBlendMode.override,
      },
    );
    final breathing = _intent(
      id: 'breathing',
      claims: const {
        AvatarMotionChannel.spine: AvatarMotionBlendMode.additive,
      },
    );

    expect(base.conflictsWith(reaction), isTrue);
    expect(base.conflictsWith(breathing), isFalse);
    expect(breathing.conflictsWith(reaction), isFalse);
  });

  test('retargeting requires an exact channel claim and group match', () {
    final first = _intent(
      id: 'first',
      retargetGroup: 'touch:leftLeg',
      retargetPolicy: AvatarRetargetPolicy.immediate,
      claims: const {
        AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
      },
    );
    final updated = _intent(
      id: 'updated',
      retargetGroup: 'touch:leftLeg',
      retargetPolicy: AvatarRetargetPolicy.immediate,
      claims: const {
        AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
      },
    );
    final wrongSide = _intent(
      id: 'wrong-side',
      retargetGroup: 'touch:leftLeg',
      retargetPolicy: AvatarRetargetPolicy.immediate,
      claims: const {
        AvatarMotionChannel.rightLeg: AvatarMotionBlendMode.override,
        AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
      },
    );

    expect(first.canRetarget(updated), isTrue);
    expect(first.canRetarget(wrongSide), isFalse);
  });

  test('rejects invalid action contracts loudly', () {
    expect(
      () => _intent(id: '', claims: const {}),
      throwsArgumentError,
    );
    expect(
      () => _intent(
        id: 'missing-group',
        claims: const {
          AvatarMotionChannel.head: AvatarMotionBlendMode.override,
        },
        retargetPolicy: AvatarRetargetPolicy.immediate,
      ),
      throwsArgumentError,
    );
  });
}

AvatarActionIntent _intent({
  required String id,
  required Map<AvatarMotionChannel, AvatarMotionBlendMode> claims,
  AvatarRetargetPolicy retargetPolicy = AvatarRetargetPolicy.never,
  String? retargetGroup,
}) {
  return AvatarActionIntent(
    id: id,
    kind: AvatarActionKind.recoil,
    source: AvatarActionSource.touch,
    priority: AvatarActionPriority.touch,
    channelClaims: claims,
    occurredAt: Duration.zero,
    blendIn: const Duration(milliseconds: 80),
    blendOut: const Duration(milliseconds: 160),
    expectedDuration: const Duration(milliseconds: 700),
    interruptionPolicy: AvatarInterruptionPolicy.interruptible,
    retargetPolicy: retargetPolicy,
    retargetGroup: retargetGroup,
  );
}
