import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_living_idle_solver.dart';
import 'package:ava/ui/avatar/avatar_rig_bindings.dart';

void main() {
  test('living idle loops continuously without moving hips or planted legs',
      () {
    final motion = AvatarLivingIdleSolver().solve();

    expect(motion.loop, isTrue);
    expect(motion.frames.length, greaterThan(600));
    expect(motion.frames.first.bones, isNot(contains(AvatarRigBone.hips)));
    expect(
      motion.frames.first.bones,
      isNot(contains(AvatarRigBone.leftUpperLeg)),
    );
    expect(
      motion.frames.first.bones,
      isNot(contains(AvatarRigBone.rightUpperLeg)),
    );
    expect(
      motion.frames.first.bones,
      isNot(contains(AvatarRigBone.leftShoulder)),
    );
    expect(
      motion.frames.first.bones,
      isNot(contains(AvatarRigBone.rightShoulder)),
    );
    expect(
      motion.frames.first.bones,
      isNot(contains(AvatarRigBone.leftUpperArm)),
    );
    expect(
      motion.frames.first.bones,
      isNot(contains(AvatarRigBone.rightUpperArm)),
    );

    for (final bone in motion.frames.first.bones.keys) {
      final first = motion.frames.first.bones[bone]!;
      final last = motion.frames.last.bones[bone]!;
      expect(last.rotationX, closeTo(first.rotationX, 1e-10));
      expect(last.rotationY, closeTo(first.rotationY, 1e-10));
      expect(last.rotationZ, closeTo(first.rotationZ, 1e-10));
    }
  });

  test('living idle is asymmetric and changes several upper-body layers', () {
    final motion = AvatarLivingIdleSolver().solve();
    final quarter = motion.frames[motion.frames.length ~/ 4].bones;

    expect(quarter[AvatarRigBone.chest]!.rotationX.abs(), greaterThan(0));
    expect(quarter[AvatarRigBone.head]!.rotationY.abs(), greaterThan(0));
    expect(
      quarter[AvatarRigBone.chest]!.rotationY,
      isNot(closeTo(quarter[AvatarRigBone.upperChest]!.rotationY, 1e-9)),
    );
  });
}
