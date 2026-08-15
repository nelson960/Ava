import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_anatomy.dart';
import 'package:ava/ui/avatar/avatar_lower_body_solver.dart';
import 'package:ava/ui/avatar/avatar_rig_bindings.dart';

void main() {
  const solver = AvatarLowerBodySolver();

  test('produces a smooth 120 fps coordinated response over the lower rig', () {
    final motion = solver.solve(
      _intent(AvatarAnatomicalRegion.leftKnee),
    );

    expect(motion.frameLengthMilliseconds, closeTo(1000 / 120, 1e-9));
    expect(motion.duration.inMilliseconds, closeTo(900, 9));
    expect(motion.maximumBlend, inInclusiveRange(0.8, 0.96));
    expect(
      motion.frames.first.bones.keys.toSet(),
      const {
        AvatarRigBone.hips,
        AvatarRigBone.spine,
        AvatarRigBone.chest,
        AvatarRigBone.neck,
        AvatarRigBone.head,
        AvatarRigBone.leftUpperLeg,
        AvatarRigBone.leftLowerLeg,
        AvatarRigBone.leftFoot,
        AvatarRigBone.leftToe,
        AvatarRigBone.rightUpperLeg,
        AvatarRigBone.rightLowerLeg,
        AvatarRigBone.rightFoot,
        AvatarRigBone.rightToe,
      },
    );
    expect(motion.frames.first.bones.values.every((pose) => pose.isNeutral),
        isTrue);
    expect(motion.frames.last.bones.values.every((pose) => pose.isNeutral),
        isTrue);

    final peak = motion.frames[motion.frames.length ~/ 3].bones;
    expect(peak[AvatarRigBone.leftLowerLeg]!.rotationX.abs(), greaterThan(0));
    expect(peak[AvatarRigBone.rightUpperLeg]!.rotationZ.abs(), greaterThan(0));
    expect(peak[AvatarRigBone.hips]!.translationX.abs(), greaterThan(0));
    expect(peak[AvatarRigBone.spine]!.rotationZ.abs(), greaterThan(0));
    expect(peak[AvatarRigBone.head]!.rotationZ.abs(), greaterThan(0));
  });

  test('left and right contacts mirror without changing anatomical identity',
      () {
    final left = solver.solve(
      _intent(AvatarAnatomicalRegion.leftLowerLeg, radialAngle: 0.7),
    );
    final right = solver.solve(
      _intent(AvatarAnatomicalRegion.rightLowerLeg, radialAngle: 0.7),
    );
    final leftPeak = left.frames[left.frames.length ~/ 3].bones;
    final rightPeak = right.frames[right.frames.length ~/ 3].bones;

    expect(
      leftPeak[AvatarRigBone.hips]!.translationX,
      closeTo(-rightPeak[AvatarRigBone.hips]!.translationX, 1e-9),
    );
    expect(
      leftPeak[AvatarRigBone.head]!.rotationZ,
      closeTo(-rightPeak[AvatarRigBone.head]!.rotationZ, 1e-9),
    );
    expect(
      leftPeak[AvatarRigBone.leftLowerLeg]!.rotationX,
      closeTo(rightPeak[AvatarRigBone.rightLowerLeg]!.rotationX, 1e-9),
    );
  });

  test('camera angle changes presentation but never movement side', () {
    final front = solver.solve(
      _intent(
        AvatarAnatomicalRegion.leftThigh,
        radialAngle: 0.35,
        relativeViewAzimuth: 0,
      ),
    );
    final side = solver.solve(
      _intent(
        AvatarAnatomicalRegion.leftThigh,
        radialAngle: 0.35,
        relativeViewAzimuth: math.pi / 2,
      ),
    );
    final frontPeak = front.frames[front.frames.length ~/ 3].bones;
    final sidePeak = side.frames[side.frames.length ~/ 3].bones;

    expect(frontPeak[AvatarRigBone.hips]!.translationX, isNegative);
    expect(sidePeak[AvatarRigBone.hips]!.translationX, isNegative);
    expect(frontPeak[AvatarRigBone.head]!.rotationZ, isPositive);
    expect(sidePeak[AvatarRigBone.head]!.rotationZ, isPositive);
    expect(
      frontPeak[AvatarRigBone.leftUpperLeg]!.rotationX.abs(),
      isNot(
          closeTo(sidePeak[AvatarRigBone.leftUpperLeg]!.rotationX.abs(), 1e-7)),
    );
  });

  test('thigh, knee, lower-leg, and shoe contacts use distinct mechanics', () {
    AvatarLowerBodyPoseFrame peak(AvatarAnatomicalRegion region) {
      final motion = solver.solve(_intent(region, radialAngle: 0));
      return motion.frames[motion.frames.length ~/ 3];
    }

    final thigh = peak(AvatarAnatomicalRegion.leftThigh).bones;
    final knee = peak(AvatarAnatomicalRegion.leftKnee).bones;
    final lowerLeg = peak(AvatarAnatomicalRegion.leftLowerLeg).bones;
    final shoe = peak(AvatarAnatomicalRegion.leftShoe).bones;

    expect(
      thigh[AvatarRigBone.leftUpperLeg]!.rotationX.abs(),
      greaterThan(shoe[AvatarRigBone.leftUpperLeg]!.rotationX.abs()),
    );
    expect(
      knee[AvatarRigBone.leftLowerLeg]!.rotationX.abs(),
      greaterThan(thigh[AvatarRigBone.leftLowerLeg]!.rotationX.abs()),
    );
    expect(
      lowerLeg[AvatarRigBone.leftToe]!.rotationX.abs(),
      greaterThan(thigh[AvatarRigBone.leftToe]!.rotationX.abs()),
    );
    expect(
      shoe[AvatarRigBone.leftToe]!.rotationX.abs(),
      greaterThan(lowerLeg[AvatarRigBone.leftToe]!.rotationX.abs()),
    );
  });

  test(
      'repeated touches use different staged trajectories, not rescaled replay',
      () {
    final notice = solver.solve(
      _intent(
        AvatarAnatomicalRegion.leftShoe,
        stage: AvatarTouchStage.notice,
      ),
    );
    final acknowledge = solver.solve(
      _intent(
        AvatarAnatomicalRegion.leftShoe,
        stage: AvatarTouchStage.acknowledge,
      ),
    );
    final redirect = solver.solve(
      _intent(
        AvatarAnatomicalRegion.leftShoe,
        stage: AvatarTouchStage.redirect,
      ),
    );

    expect(notice.stage, AvatarTouchStage.notice);
    expect(acknowledge.stage, AvatarTouchStage.acknowledge);
    expect(redirect.stage, AvatarTouchStage.redirect);

    double toeAt(AvatarLowerBodyMotion motion, double progress) {
      final index = ((motion.frames.length - 1) * progress).round();
      return motion.frames[index].bones[AvatarRigBone.leftToe]!.rotationX;
    }

    expect(toeAt(notice, 0.72).abs(), lessThan(1e-8));
    expect(toeAt(acknowledge, 0.72).abs(), greaterThan(1e-5));
    expect(toeAt(redirect, 0.72).abs(), greaterThan(1e-5));
    expect(
      toeAt(redirect, 0.72),
      isNot(closeTo(toeAt(acknowledge, 0.72), 1e-5)),
    );
  });

  test('keeps every staged leg pose inside conservative production bounds', () {
    for (final stage in AvatarTouchStage.values) {
      for (final region in const [
        AvatarAnatomicalRegion.leftThigh,
        AvatarAnatomicalRegion.rightKnee,
        AvatarAnatomicalRegion.leftLowerLeg,
        AvatarAnatomicalRegion.rightShoe,
      ]) {
        final motion = solver.solve(_intent(region, stage: stage));
        for (final frame in motion.frames) {
          for (final pose in frame.bones.values) {
            expect(pose.rotationX.abs(), lessThan(_radians(12)));
            expect(pose.rotationY.abs(), lessThan(_radians(12)));
            expect(pose.rotationZ.abs(), lessThan(_radians(12)));
            expect(pose.translationX.abs(), lessThan(0.02));
            expect(pose.translationY.abs(), lessThan(0.01));
            expect(pose.translationZ.abs(), lessThan(0.01));
          }
        }
      }
    }
  });

  test('rejects torso contacts and mismatched side claims', () {
    expect(
      () => solver.solve(_intent(AvatarAnatomicalRegion.waist)),
      throwsArgumentError,
    );
    expect(
      () => solver.solve(
        _intent(
          AvatarAnatomicalRegion.leftKnee,
          claimedSide: AvatarBodySide.right,
        ),
      ),
      throwsArgumentError,
    );
  });
}

double _radians(double degrees) => degrees * math.pi / 180;

AvatarActionIntent _intent(
  AvatarAnatomicalRegion region, {
  double radialAngle = 0.4,
  double relativeViewAzimuth = 0,
  AvatarBodySide? claimedSide,
  AvatarTouchStage stage = AvatarTouchStage.notice,
}) {
  final side = claimedSide ?? region.side;
  final channel = side == AvatarBodySide.left
      ? AvatarMotionChannel.leftLeg
      : AvatarMotionChannel.rightLeg;
  return AvatarActionIntent(
    id: 'test-${region.name}',
    kind: region == AvatarAnatomicalRegion.leftShoe ||
            region == AvatarAnatomicalRegion.rightShoe
        ? AvatarActionKind.weightShift
        : AvatarActionKind.recoil,
    source: AvatarActionSource.touch,
    priority: AvatarActionPriority.touch,
    channelClaims: {
      channel: AvatarMotionBlendMode.override,
      AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
    },
    occurredAt: const Duration(seconds: 1),
    blendIn: const Duration(milliseconds: 70),
    blendOut: const Duration(milliseconds: 260),
    expectedDuration: const Duration(milliseconds: 900),
    minimumHold: const Duration(milliseconds: 200),
    interruptionPolicy: AvatarInterruptionPolicy.afterMinimumHold,
    retargetPolicy: AvatarRetargetPolicy.immediate,
    retargetGroup: 'test:leg',
    target: AvatarContactTarget(
      anatomy: AvatarAnatomicalContact(
        region: region,
        boneInfluences: const [
          AvatarBoneInfluence(
            jointIndex: 0,
            jointName: 'test-joint',
            weight: 1,
          ),
        ],
        longitudinalPosition: 0.62,
        radialAngle: radialAngle,
        surfaceSector: AvatarSurfaceSector.front,
      ),
      modelPosition: const AvatarModelVector3(0.1, 0.5, 0.05),
      modelNormal: const AvatarModelVector3(0, 0, 1),
      spatialContext: AvatarSpatialContext(
        cameraAzimuth: relativeViewAzimuth,
        cameraElevation: 0.2,
        avatarRootYaw: 0,
      ),
    ),
    numericParameters: {
      'strength': 0.88,
      'relativeViewAzimuth': relativeViewAzimuth,
    },
    textParameters: {'touchStage': stage.name},
  );
}
