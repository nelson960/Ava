import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_anatomy.dart';
import 'package:ava/ui/avatar/avatar_procedural_motion.dart';
import 'package:ava/ui/avatar/avatar_rig_bindings.dart';
import 'package:ava/ui/avatar/avatar_torso_touch_solver.dart';

void main() {
  const solver = AvatarTorsoTouchSolver();

  test('keeps root, hips, and both legs outside an ordinary torso response',
      () {
    final performance = solver.solve(_intent(AvatarTouchStage.notice));
    final controlled = performance.skeletalMotion.frames.first.bones.keys;

    expect(controlled, isNot(contains(AvatarRigBone.hips)));
    expect(controlled, isNot(contains(AvatarRigBone.leftUpperLeg)));
    expect(controlled, isNot(contains(AvatarRigBone.rightUpperLeg)));
    expect(controlled, isNot(contains(AvatarRigBone.leftFoot)));
    expect(controlled, isNot(contains(AvatarRigBone.rightFoot)));
    expect(
      performance.skeletalMotion.frames.first.bones.values
          .every((pose) => pose.isNeutral),
      isTrue,
    );
    expect(
      performance.skeletalMotion.frames.last.bones.values
          .every((pose) => pose.isNeutral),
      isTrue,
    );
  });

  test('orients head before the contacted torso', () {
    final motion =
        solver.solve(_intent(AvatarTouchStage.notice)).skeletalMotion;

    int peakIndex(
      AvatarRigBone bone,
      double Function(AvatarBonePoseDelta pose) value,
    ) {
      var bestIndex = 0;
      var best = -1.0;
      for (var index = 0; index < motion.frames.length; index += 1) {
        final candidate = value(motion.frames[index].bones[bone]!).abs();
        if (candidate > best) {
          best = candidate;
          bestIndex = index;
        }
      }
      return bestIndex;
    }

    final headPeak = peakIndex(AvatarRigBone.head, (pose) => pose.rotationX);
    final chestPeak = peakIndex(AvatarRigBone.chest, (pose) => pose.rotationX);
    expect(headPeak, lessThan(chestPeak));
  });

  test('notice, acknowledge, and redirect have different activation paths', () {
    final performances = {
      for (final stage in AvatarTouchStage.values)
        stage: solver.solve(_intent(stage)),
    };

    double peakChest(AvatarTouchStage stage) {
      final motion = performances[stage]!.skeletalMotion;
      return motion.frames
          .map((frame) => frame.bones[AvatarRigBone.chest]!.rotationX.abs())
          .reduce((left, right) => left > right ? left : right);
    }

    expect(
      peakChest(AvatarTouchStage.notice),
      lessThan(peakChest(AvatarTouchStage.acknowledge)),
    );
    expect(
      peakChest(AvatarTouchStage.acknowledge),
      lessThan(peakChest(AvatarTouchStage.redirect)),
    );

    final redirectMorph = performances[AvatarTouchStage.redirect]!.morphMotion!;
    final noticeMorph = performances[AvatarTouchStage.notice]!.morphMotion!;
    final surprised = redirectMorph.targetNames.indexOf('Fcl_EYE_Surprised');
    expect(
      redirectMorph.frames.any((frame) => frame[surprised] > 0),
      isTrue,
    );
    expect(
      noticeMorph.frames.every((frame) => frame[surprised] == 0),
      isTrue,
    );
  });

  test('facial blink leads the friendly expression', () {
    final morph = solver.solve(_intent(AvatarTouchStage.notice)).morphMotion!;
    final blinkIndex = morph.targetNames.indexOf('Fcl_EYE_Close');
    final joyIndex = morph.targetNames.indexOf('Fcl_EYE_Joy');

    int peakFrame(int targetIndex) {
      var result = 0;
      var best = -1.0;
      for (var index = 0; index < morph.frames.length; index += 1) {
        if (morph.frames[index][targetIndex] > best) {
          best = morph.frames[index][targetIndex];
          result = index;
        }
      }
      return result;
    }

    expect(peakFrame(blinkIndex), lessThan(peakFrame(joyIndex)));
  });

  test('caps cumulative torso-chain movement at every touch stage', () {
    double cumulativePeakDegrees(AvatarTouchStage stage) {
      final motion = solver.solve(_intent(stage)).skeletalMotion;
      return motion.frames
              .map(
                (frame) => const [
                  AvatarRigBone.spine,
                  AvatarRigBone.chest,
                  AvatarRigBone.upperChest,
                ].fold<double>(
                  0,
                  (sum, bone) => sum + frame.bones[bone]!.rotationX.abs(),
                ),
              )
              .reduce((left, right) => left > right ? left : right) *
          motion.maximumBlend *
          180 /
          3.141592653589793;
    }

    final notice = cumulativePeakDegrees(AvatarTouchStage.notice);
    final acknowledge = cumulativePeakDegrees(AvatarTouchStage.acknowledge);
    final redirect = cumulativePeakDegrees(AvatarTouchStage.redirect);

    expect(notice, lessThan(1.0));
    expect(acknowledge, lessThan(1.5));
    expect(redirect, lessThan(2.0));
    expect(notice, lessThan(acknowledge));
    expect(acknowledge, lessThan(redirect));
  });

  test('a center contact never alternates left and right by touch count', () {
    for (final stage in AvatarTouchStage.values) {
      final motion = solver
          .solve(
            _intent(
              stage,
              modelX: 0,
              modelZ: 0.08,
              authoredRadialAngle: -0.97,
            ),
          )
          .skeletalMotion;
      var hasForwardResponse = false;
      for (final frame in motion.frames) {
        for (final bone in const [
          AvatarRigBone.spine,
          AvatarRigBone.chest,
          AvatarRigBone.upperChest,
          AvatarRigBone.neck,
          AvatarRigBone.head,
        ]) {
          expect(frame.bones[bone]!.rotationZ, 0, reason: '$stage $bone');
        }
        for (final bone in const [
          AvatarRigBone.spine,
          AvatarRigBone.chest,
          AvatarRigBone.upperChest,
        ]) {
          expect(frame.bones[bone]!.rotationY, 0, reason: '$stage $bone');
          hasForwardResponse =
              hasForwardResponse || frame.bones[bone]!.rotationX.abs() > 0;
        }
      }
      expect(hasForwardResponse, isTrue, reason: stage.name);
    }
  });

  test('waist contacts remain gentler than chest contacts', () {
    double peak(AvatarAnatomicalRegion region) {
      final motion = solver
          .solve(_intent(AvatarTouchStage.redirect, region: region))
          .skeletalMotion;
      return motion.frames
          .map((frame) => frame.bones[AvatarRigBone.chest]!.rotationX.abs())
          .reduce((left, right) => left > right ? left : right);
    }

    expect(
      peak(AvatarAnatomicalRegion.waist),
      lessThan(peak(AvatarAnatomicalRegion.chest)),
    );
  });
}

AvatarActionIntent _intent(
  AvatarTouchStage stage, {
  AvatarAnatomicalRegion region = AvatarAnatomicalRegion.chest,
  double modelX = 0.04,
  double modelZ = 0.08,
  double authoredRadialAngle = 0,
}) {
  return AvatarActionIntent(
    id: 'torso-${stage.name}',
    kind: AvatarActionKind.protectiveRedirect,
    source: AvatarActionSource.touch,
    priority: AvatarActionPriority.touch,
    channelClaims: const {
      AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
      AvatarMotionChannel.spine: AvatarMotionBlendMode.override,
    },
    occurredAt: const Duration(seconds: 1),
    blendIn: const Duration(milliseconds: 75),
    blendOut: const Duration(milliseconds: 260),
    interruptionPolicy: AvatarInterruptionPolicy.afterMinimumHold,
    retargetPolicy: AvatarRetargetPolicy.afterMinimumHold,
    expectedDuration: const Duration(milliseconds: 900),
    minimumHold: const Duration(milliseconds: 320),
    retargetGroup: 'touch:torso',
    target: AvatarContactTarget(
      anatomy: AvatarAnatomicalContact(
        region: region,
        boneInfluences: const [
          AvatarBoneInfluence(
            jointIndex: 0,
            jointName: 'J_Bip_C_Chest',
            weight: 1,
          ),
        ],
        longitudinalPosition: 0.54,
        radialAngle: authoredRadialAngle,
        surfaceSector: AvatarSurfaceSector.front,
      ),
      modelPosition: AvatarModelVector3(modelX, 1.25, modelZ),
      modelNormal: const AvatarModelVector3(0, 0, 1),
      spatialContext: AvatarSpatialContext(
        cameraAzimuth: 0,
        cameraElevation: 0.2,
        avatarRootYaw: 0,
      ),
    ),
    numericParameters: {
      'strength': switch (stage) {
        AvatarTouchStage.notice => 0.72,
        AvatarTouchStage.acknowledge => 0.88,
        AvatarTouchStage.redirect => 1,
      },
      'streak': (stage.index + 1).toDouble(),
    },
    textParameters: {'touchStage': stage.name},
  );
}
