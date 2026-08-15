import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_anatomy.dart';
import 'package:ava/ui/avatar/avatar_procedural_motion.dart';
import 'package:ava/ui/avatar/avatar_rig_bindings.dart';
import 'package:ava/ui/avatar/avatar_upper_body_touch_solver.dart';

void main() {
  const solver = AvatarUpperBodyTouchSolver();

  test('supports every head, face, shoulder, arm, and hand region', () {
    const regions = [
      AvatarAnatomicalRegion.hair,
      AvatarAnatomicalRegion.head,
      AvatarAnatomicalRegion.face,
      AvatarAnatomicalRegion.leftCheek,
      AvatarAnatomicalRegion.rightCheek,
      AvatarAnatomicalRegion.leftShoulder,
      AvatarAnatomicalRegion.rightShoulder,
      AvatarAnatomicalRegion.leftUpperArm,
      AvatarAnatomicalRegion.rightUpperArm,
      AvatarAnatomicalRegion.leftLowerArm,
      AvatarAnatomicalRegion.rightLowerArm,
      AvatarAnatomicalRegion.leftHand,
      AvatarAnatomicalRegion.rightHand,
    ];

    for (final region in regions) {
      final performance = solver.solve(_intent(region));
      final motion = performance.skeletalMotion;
      expect(motion.frameLengthMilliseconds, closeTo(1000 / 120, 1e-9));
      expect(motion.frames.length, greaterThan(50));
      expect(
        motion.frames.first.bones.values.every((pose) => pose.isNeutral),
        isTrue,
        reason: '${region.name} must enter from the current rendered pose',
      );
      expect(
        motion.frames.last.bones.values.every((pose) => pose.isNeutral),
        isTrue,
        reason: '${region.name} must release back to AvaIdle',
      );
      expect(performance.morphMotion, isNotNull);
      expect(
        motion.frames.expand((frame) => frame.bones.values).every(_isBounded),
        isTrue,
        reason: '${region.name} exceeded the conservative touch envelope',
      );
    }
  });

  test('eyes orient before the head follows a face contact', () {
    final motion =
        solver.solve(_intent(AvatarAnatomicalRegion.face)).skeletalMotion;

    int firstMovingFrame(AvatarRigBone bone) => motion.frames.indexWhere(
          (frame) => !frame.bones[bone]!.isNeutral,
        );

    expect(
      firstMovingFrame(AvatarRigBone.leftEye),
      lessThan(firstMovingFrame(AvatarRigBone.head)),
    );
    expect(
      firstMovingFrame(AvatarRigBone.rightEye),
      lessThan(firstMovingFrame(AvatarRigBone.head)),
    );
  });

  test('left and right cheek contacts mirror head turn and local blink', () {
    final left = solver.solve(_intent(AvatarAnatomicalRegion.leftCheek));
    final right = solver.solve(_intent(AvatarAnatomicalRegion.rightCheek));
    final leftPeak = _largestPose(left.skeletalMotion, AvatarRigBone.head);
    final rightPeak = _largestPose(right.skeletalMotion, AvatarRigBone.head);

    expect(leftPeak.rotationX, closeTo(rightPeak.rotationX, 1e-8));
    expect(leftPeak.rotationY, closeTo(0, 1e-8));
    expect(rightPeak.rotationY, closeTo(0, 1e-8));
    expect(leftPeak.rotationZ, closeTo(-rightPeak.rotationZ, 1e-8));

    final leftMorph = left.morphMotion!;
    final rightMorph = right.morphMotion!;
    final closeLeft = leftMorph.targetNames.indexOf('Fcl_EYE_Close_L');
    final closeRight = leftMorph.targetNames.indexOf('Fcl_EYE_Close_R');
    expect(
      leftMorph.frames.any((frame) => frame[closeLeft] > 0),
      isTrue,
    );
    expect(
      leftMorph.frames.every((frame) => frame[closeRight] == 0),
      isTrue,
    );
    expect(
      rightMorph.frames.any((frame) => frame[closeRight] > 0),
      isTrue,
    );
  });

  test('both hands animate their own palm and all fifteen finger joints', () {
    final left =
        solver.solve(_intent(AvatarAnatomicalRegion.leftHand)).skeletalMotion;
    final right =
        solver.solve(_intent(AvatarAnatomicalRegion.rightHand)).skeletalMotion;
    const leftFingers = {
      AvatarRigBone.leftThumb1,
      AvatarRigBone.leftThumb2,
      AvatarRigBone.leftThumb3,
      AvatarRigBone.leftIndex1,
      AvatarRigBone.leftIndex2,
      AvatarRigBone.leftIndex3,
      AvatarRigBone.leftMiddle1,
      AvatarRigBone.leftMiddle2,
      AvatarRigBone.leftMiddle3,
      AvatarRigBone.leftRing1,
      AvatarRigBone.leftRing2,
      AvatarRigBone.leftRing3,
      AvatarRigBone.leftLittle1,
      AvatarRigBone.leftLittle2,
      AvatarRigBone.leftLittle3,
    };
    const rightFingers = {
      AvatarRigBone.rightThumb1,
      AvatarRigBone.rightThumb2,
      AvatarRigBone.rightThumb3,
      AvatarRigBone.rightIndex1,
      AvatarRigBone.rightIndex2,
      AvatarRigBone.rightIndex3,
      AvatarRigBone.rightMiddle1,
      AvatarRigBone.rightMiddle2,
      AvatarRigBone.rightMiddle3,
      AvatarRigBone.rightRing1,
      AvatarRigBone.rightRing2,
      AvatarRigBone.rightRing3,
      AvatarRigBone.rightLittle1,
      AvatarRigBone.rightLittle2,
      AvatarRigBone.rightLittle3,
    };

    expect(left.frames.first.bones.keys, containsAll(leftFingers));
    expect(
        left.frames.first.bones.keys, isNot(contains(AvatarRigBone.rightHand)));
    expect(right.frames.first.bones.keys, containsAll(rightFingers));
    expect(
        right.frames.first.bones.keys, isNot(contains(AvatarRigBone.leftHand)));

    final leftIndex = _largestPose(left, AvatarRigBone.leftIndex2);
    final rightIndex = _largestPose(right, AvatarRigBone.rightIndex2);
    expect(leftIndex.rotationZ, closeTo(-rightIndex.rotationZ, 1e-8));
  });

  test('notice, acknowledge, and redirect use distinct temporal paths', () {
    final motions = {
      for (final stage in AvatarTouchStage.values)
        stage: solver
            .solve(_intent(AvatarAnatomicalRegion.leftHand, stage: stage))
            .skeletalMotion,
    };

    double handAt(AvatarTouchStage stage, double progress) {
      final motion = motions[stage]!;
      final index = ((motion.frames.length - 1) * progress).round();
      return motion.frames[index].bones[AvatarRigBone.leftHand]!.rotationX;
    }

    expect(handAt(AvatarTouchStage.notice, 0.7).abs(), lessThan(1e-8));
    expect(handAt(AvatarTouchStage.acknowledge, 0.7).abs(), greaterThan(1e-5));
    expect(handAt(AvatarTouchStage.redirect, 0.7).abs(), greaterThan(1e-5));
    expect(
      handAt(AvatarTouchStage.redirect, 0.7),
      isNot(closeTo(handAt(AvatarTouchStage.acknowledge, 0.7), 1e-7)),
    );
  });

  test('surface angle changes arm response without changing anatomical side',
      () {
    final front = solver
        .solve(_intent(AvatarAnatomicalRegion.leftUpperArm, radialAngle: 0))
        .skeletalMotion;
    final side = solver
        .solve(_intent(
          AvatarAnatomicalRegion.leftUpperArm,
          radialAngle: 3.141592653589793 / 2,
        ))
        .skeletalMotion;
    final frontPose = _largestPose(front, AvatarRigBone.leftUpperArm);
    final sidePose = _largestPose(side, AvatarRigBone.leftUpperArm);

    expect(frontPose.rotationX.abs(), lessThan(1e-8));
    expect(sidePose.rotationX.abs(), greaterThan(1e-4));
    expect(frontPose.rotationY, closeTo(sidePose.rotationY, 1e-8));
    expect(front.frames.first.bones, contains(AvatarRigBone.leftUpperArm));
    expect(
        front.frames.first.bones, isNot(contains(AvatarRigBone.rightUpperArm)));
  });

  test(
      'every arm region visibly moves its contacted local chain, not only head',
      () {
    const cases = <(
      AvatarAnatomicalRegion,
      AvatarRigBone,
      double Function(AvatarBonePoseDelta)
    )>[
      (
        AvatarAnatomicalRegion.leftShoulder,
        AvatarRigBone.leftShoulder,
        _rotationZ,
      ),
      (
        AvatarAnatomicalRegion.rightShoulder,
        AvatarRigBone.rightShoulder,
        _rotationZ,
      ),
      (
        AvatarAnatomicalRegion.leftUpperArm,
        AvatarRigBone.leftUpperArm,
        _rotationY,
      ),
      (
        AvatarAnatomicalRegion.rightUpperArm,
        AvatarRigBone.rightUpperArm,
        _rotationY,
      ),
      (
        AvatarAnatomicalRegion.leftLowerArm,
        AvatarRigBone.leftLowerArm,
        _rotationY,
      ),
      (
        AvatarAnatomicalRegion.rightLowerArm,
        AvatarRigBone.rightLowerArm,
        _rotationY,
      ),
      (
        AvatarAnatomicalRegion.leftHand,
        AvatarRigBone.leftHand,
        _rotationX,
      ),
      (
        AvatarAnatomicalRegion.rightHand,
        AvatarRigBone.rightHand,
        _rotationX,
      ),
    ];

    for (final entry in cases) {
      final motion = solver.solve(_intent(entry.$1)).skeletalMotion;
      final contactedDegrees = _effectivePeakDegrees(
        motion,
        entry.$2,
        entry.$3,
      );
      final headDegrees = _effectivePeakDegrees(
        motion,
        AvatarRigBone.head,
        _rotationZ,
      );

      expect(
        contactedDegrees,
        greaterThan(1.7),
        reason: '${entry.$1.name} must have visible local movement',
      );
      expect(
        contactedDegrees,
        greaterThan(headDegrees * 1.7),
        reason: '${entry.$1.name} must dominate its attention head turn',
      );
    }
  });

  test('a hand touch visibly opens the palm and finger chain', () {
    final motion =
        solver.solve(_intent(AvatarAnatomicalRegion.leftHand)).skeletalMotion;

    expect(
      _effectivePeakDegrees(motion, AvatarRigBone.leftHand, _rotationX),
      greaterThan(2.5),
    );
    expect(
      _effectivePeakDegrees(motion, AvatarRigBone.leftIndex2, _rotationZ),
      greaterThan(4.5),
    );
  });
}

AvatarActionIntent _intent(
  AvatarAnatomicalRegion region, {
  AvatarTouchStage stage = AvatarTouchStage.notice,
  double radialAngle = 0,
}) {
  final isHand = region == AvatarAnatomicalRegion.leftHand ||
      region == AvatarAnatomicalRegion.rightHand;
  return AvatarActionIntent(
    id: '${region.name}-${stage.name}-$radialAngle',
    kind: isHand ? AvatarActionKind.handPose : AvatarActionKind.recoil,
    source: AvatarActionSource.touch,
    priority: AvatarActionPriority.touch,
    channelClaims: const {
      AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
    },
    occurredAt: const Duration(seconds: 1),
    blendIn: const Duration(milliseconds: 55),
    blendOut: const Duration(milliseconds: 210),
    expectedDuration: const Duration(milliseconds: 760),
    minimumHold: const Duration(milliseconds: 120),
    interruptionPolicy: AvatarInterruptionPolicy.afterMinimumHold,
    retargetPolicy: AvatarRetargetPolicy.afterMinimumHold,
    retargetGroup: 'touch:${region.name}',
    target: AvatarContactTarget(
      anatomy: AvatarAnatomicalContact(
        region: region,
        boneInfluences: const [
          AvatarBoneInfluence(
            jointIndex: 0,
            jointName: 'J_Bip_C_Head',
            weight: 1,
          ),
        ],
        longitudinalPosition: 0.5,
        radialAngle: radialAngle,
        surfaceSector: AvatarSurfaceSector.front,
      ),
      modelPosition: AvatarModelVector3(
        region.name.startsWith('right') ? -0.2 : 0.2,
        1.3,
        0.08,
      ),
      modelNormal: const AvatarModelVector3(0, 0, 1),
      spatialContext: AvatarSpatialContext(
        cameraAzimuth: 0,
        cameraElevation: 0.2,
        avatarRootYaw: 0,
      ),
    ),
    numericParameters: const {'strength': 0.88, 'streak': 1},
    textParameters: {'touchStage': stage.name},
  );
}

AvatarBonePoseDelta _largestPose(
  AvatarProceduralMotion motion,
  AvatarRigBone bone,
) {
  return motion.frames.map((frame) => frame.bones[bone]!).reduce(
      (left, right) => _magnitude(left) >= _magnitude(right) ? left : right);
}

double _magnitude(AvatarBonePoseDelta pose) =>
    pose.rotationX.abs() + pose.rotationY.abs() + pose.rotationZ.abs();

double _effectivePeakDegrees(
  AvatarProceduralMotion motion,
  AvatarRigBone bone,
  double Function(AvatarBonePoseDelta pose) component,
) {
  final peakRadians = motion.frames
      .map((frame) => component(frame.bones[bone]!).abs())
      .reduce((left, right) => left > right ? left : right);
  return peakRadians * motion.maximumBlend * 180 / 3.141592653589793;
}

double _rotationX(AvatarBonePoseDelta pose) => pose.rotationX;
double _rotationY(AvatarBonePoseDelta pose) => pose.rotationY;
double _rotationZ(AvatarBonePoseDelta pose) => pose.rotationZ;

bool _isBounded(AvatarBonePoseDelta pose) =>
    pose.rotationX.abs() < 0.25 &&
    pose.rotationY.abs() < 0.25 &&
    pose.rotationZ.abs() < 0.25 &&
    pose.translationX.abs() < 0.02 &&
    pose.translationY.abs() < 0.02 &&
    pose.translationZ.abs() < 0.02;
