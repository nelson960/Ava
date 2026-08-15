import 'dart:math' as math;

import 'avatar_action_intent.dart';
import 'avatar_anatomy.dart';
import 'avatar_procedural_motion.dart';
import 'avatar_rig_bindings.dart';

/// Procedural touch motion for the head, face, hair, cheeks, arms and hands.
///
/// Every output begins and ends at a neutral delta so Ava's imported idle
/// remains the resting authority. The response is built from independently
/// timed attention, posture, palm and finger curves instead of a single pose.
class AvatarUpperBodyTouchSolver {
  const AvatarUpperBodyTouchSolver({this.framesPerSecond = 120})
      : assert(framesPerSecond > 0);

  final int framesPerSecond;

  AvatarProceduralPerformance solve(AvatarActionIntent intent) {
    final target = intent.target;
    if (target is! AvatarContactTarget) {
      throw ArgumentError(
        'Upper-body touch motion requires an anatomical contact target.',
      );
    }
    final stage = _touchStage(intent);
    final duration = intent.expectedDuration;
    if (duration == null) {
      throw ArgumentError(
        'Upper-body touch motion requires an expected duration.',
      );
    }
    final strength = intent.numericParameters['strength'];
    if (strength == null || !strength.isFinite || strength <= 0) {
      throw ArgumentError(
        'Upper-body touch motion requires a finite positive strength.',
      );
    }
    final safeStrength = strength.clamp(0.45, 1.0).toDouble();
    return switch (target.region) {
      AvatarAnatomicalRegion.hair ||
      AvatarAnatomicalRegion.head ||
      AvatarAnatomicalRegion.face ||
      AvatarAnatomicalRegion.leftCheek ||
      AvatarAnatomicalRegion.rightCheek =>
        _solveHead(
          target: target,
          stage: stage,
          duration: duration,
          strength: safeStrength,
        ),
      AvatarAnatomicalRegion.leftShoulder ||
      AvatarAnatomicalRegion.leftUpperArm ||
      AvatarAnatomicalRegion.leftLowerArm ||
      AvatarAnatomicalRegion.leftHand ||
      AvatarAnatomicalRegion.rightShoulder ||
      AvatarAnatomicalRegion.rightUpperArm ||
      AvatarAnatomicalRegion.rightLowerArm ||
      AvatarAnatomicalRegion.rightHand =>
        _solveArm(
          target: target,
          stage: stage,
          duration: duration,
          strength: safeStrength,
        ),
      _ => throw UnsupportedError(
          'Upper-body solver does not support ${target.region.name}.',
        ),
    };
  }

  AvatarProceduralPerformance _solveHead({
    required AvatarContactTarget target,
    required AvatarTouchStage stage,
    required Duration duration,
    required double strength,
  }) {
    final timeline = _TouchTrajectory(stage);
    final frameLength = 1000 / framesPerSecond;
    final frameCount = _frameCount(duration, frameLength);
    final side = switch (target.region) {
      AvatarAnatomicalRegion.leftCheek => 1.0,
      AvatarAnatomicalRegion.rightCheek => -1.0,
      _ when target.modelPosition.x.abs() > 0.012 =>
        target.modelPosition.x >= 0 ? 1.0 : -1.0,
      _ => math.sin(target.radialAngle) >= 0 ? 1.0 : -1.0,
    };
    final contactTangent = math.sin(target.radialAngle);
    final contactFront = math.cos(target.radialAngle);
    final isCheek = target.region == AvatarAnatomicalRegion.leftCheek ||
        target.region == AvatarAnatomicalRegion.rightCheek;
    final isHair = target.region == AvatarAnatomicalRegion.hair;
    final regionScale = isCheek
        ? 0.9
        : isHair
            ? 0.82
            : target.region == AvatarAnatomicalRegion.face
                ? 0.76
                : 1.0;
    final scale = strength * regionScale;
    final pitchDegrees = switch (target.region) {
      AvatarAnatomicalRegion.head => 7.0,
      AvatarAnatomicalRegion.face => 3.0,
      AvatarAnatomicalRegion.hair => 2.0,
      AvatarAnatomicalRegion.leftCheek ||
      AvatarAnatomicalRegion.rightCheek =>
        1.5,
      _ => 0.0,
    };
    final rollDegrees = switch (target.region) {
      AvatarAnatomicalRegion.hair ||
      AvatarAnatomicalRegion.leftCheek ||
      AvatarAnatomicalRegion.rightCheek =>
        5.0,
      AvatarAnatomicalRegion.head => 1.4,
      AvatarAnatomicalRegion.face => 1.0,
      _ => 0.0,
    };
    final pitch = (-contactFront * _radians(pitchDegrees) +
            (isHair ? _radians(0.7) : 0)) *
        scale;
    final roll =
        (side * _radians(rollDegrees) + contactTangent * _radians(1.1)) * scale;
    final boneFrames = <AvatarProceduralPoseFrame>[];
    final morphFrames = <List<double>>[];
    for (var frame = 0; frame < frameCount; frame += 1) {
      final progress = frame / (frameCount - 1);
      final eyes = timeline.attention(progress);
      final head = timeline.posture(progress);
      final expression = timeline.expression(progress);
      final blink = timeline.blink(progress);
      final secondBeat = timeline.secondBeat(progress);
      boneFrames.add(
        AvatarProceduralPoseFrame(
          Map<AvatarRigBone, AvatarBonePoseDelta>.unmodifiable({
            AvatarRigBone.neck: AvatarBonePoseDelta(
              rotationX: pitch * 0.32 * head,
              rotationZ: roll * 0.3 * head,
            ),
            AvatarRigBone.head: AvatarBonePoseDelta(
              rotationX: pitch * (0.68 * head + 0.08 * secondBeat),
              rotationZ: roll * (0.76 * head + 0.08 * secondBeat),
            ),
            AvatarRigBone.leftEye: AvatarBonePoseDelta(
              rotationX: pitch * -0.12 * eyes,
              rotationY: roll * 0.32 * eyes,
            ),
            AvatarRigBone.rightEye: AvatarBonePoseDelta(
              rotationX: pitch * -0.12 * eyes,
              rotationY: roll * 0.32 * eyes,
            ),
          }),
        ),
      );
      final cheekBlink = isCheek ? 0.16 * expression : 0.0;
      morphFrames.add(
        List.unmodifiable([
          (0.2 * blink).clamp(0.0, 1.0),
          (target.region == AvatarAnatomicalRegion.leftCheek ? cheekBlink : 0.0)
              .clamp(0.0, 1.0),
          (target.region == AvatarAnatomicalRegion.rightCheek
                  ? cheekBlink
                  : 0.0)
              .clamp(0.0, 1.0),
          ((stage == AvatarTouchStage.notice ? 0.07 : 0.15) * expression)
              .clamp(0.0, 1.0),
          ((isCheek ? 0.16 : 0.11) * expression).clamp(0.0, 1.0),
          (stage == AvatarTouchStage.redirect ? 0.09 * expression : 0.0)
              .clamp(0.0, 1.0),
          (stage == AvatarTouchStage.redirect ? 0.08 * expression : 0.0)
              .clamp(0.0, 1.0),
        ]),
      );
    }
    return AvatarProceduralPerformance(
      skeletalMotion: AvatarProceduralMotion(
        frameLengthMilliseconds: frameLength,
        frames: List.unmodifiable(boneFrames),
        maximumBlend: 0.72,
      ),
      morphMotion: AvatarMorphMotion(
        frameLengthMilliseconds: frameLength,
        targetNames: const [
          'Fcl_EYE_Close',
          'Fcl_EYE_Close_L',
          'Fcl_EYE_Close_R',
          'Fcl_EYE_Joy',
          'Fcl_MTH_Joy',
          'Fcl_EYE_Surprised',
          'Fcl_MTH_Fun',
        ],
        frames: List.unmodifiable(morphFrames),
      ),
    );
  }

  AvatarProceduralPerformance _solveArm({
    required AvatarContactTarget target,
    required AvatarTouchStage stage,
    required Duration duration,
    required double strength,
  }) {
    final isLeft = switch (target.region) {
      AvatarAnatomicalRegion.leftShoulder ||
      AvatarAnatomicalRegion.leftUpperArm ||
      AvatarAnatomicalRegion.leftLowerArm ||
      AvatarAnatomicalRegion.leftHand =>
        true,
      _ => false,
    };
    final side = isLeft ? -1.0 : 1.0;
    final anatomicalSign = isLeft ? 1.0 : -1.0;
    final roles = _ArmRoles(isLeft);
    final timeline = _TouchTrajectory(stage);
    final frameLength = 1000 / framesPerSecond;
    final frameCount = _frameCount(duration, frameLength);
    final contactFront = math.cos(target.radialAngle);
    final contactTangent = math.sin(target.radialAngle);
    final regionWeights = _ArmRegionWeights.forRegion(target.region);
    final scale = strength *
        switch (stage) {
          AvatarTouchStage.notice => 0.92,
          AvatarTouchStage.acknowledge => 1.0,
          AvatarTouchStage.redirect => 1.08,
        };
    final boneFrames = <AvatarProceduralPoseFrame>[];
    final morphFrames = <List<double>>[];
    for (var frame = 0; frame < frameCount; frame += 1) {
      final progress = frame / (frameCount - 1);
      final attention = timeline.attention(progress);
      final posture = timeline.posture(progress);
      final palm = timeline.palm(progress);
      final fingers = timeline.fingers(progress);
      final expression = timeline.expression(progress);
      final blink = timeline.blink(progress);
      final secondBeat = timeline.secondBeat(progress);
      final bones = <AvatarRigBone, AvatarBonePoseDelta>{
        AvatarRigBone.neck: AvatarBonePoseDelta(
          rotationX: _radians(0.3) * attention * scale,
          rotationZ: anatomicalSign * _radians(0.5) * attention * scale,
        ),
        AvatarRigBone.head: AvatarBonePoseDelta(
          rotationX: _radians(0.72) * attention * scale,
          rotationZ: anatomicalSign * _radians(1.05) * attention * scale,
        ),
        AvatarRigBone.leftEye: AvatarBonePoseDelta(
          rotationX: -_radians(0.18) * attention * scale,
          rotationY: side * _radians(0.4) * attention * scale,
        ),
        AvatarRigBone.rightEye: AvatarBonePoseDelta(
          rotationX: -_radians(0.18) * attention * scale,
          rotationY: side * _radians(0.4) * attention * scale,
        ),
        roles.shoulder: AvatarBonePoseDelta(
          rotationX: _radians(0.8) * regionWeights.shoulder * posture * scale,
          rotationZ: -anatomicalSign *
              _radians(4.6) *
              regionWeights.shoulder *
              posture *
              scale,
        ),
        roles.upperArm: AvatarBonePoseDelta(
          rotationX: contactTangent *
              _radians(1.2) *
              regionWeights.upperArm *
              posture *
              scale,
          rotationY: anatomicalSign *
              _radians(6.5) *
              regionWeights.upperArm *
              (posture + 0.12 * secondBeat) *
              scale,
          rotationZ: -anatomicalSign *
              _radians(2.2) *
              (0.7 + 0.3 * contactFront.abs()) *
              regionWeights.upperArm *
              posture *
              scale,
        ),
        roles.lowerArm: AvatarBonePoseDelta(
          rotationX: -contactFront *
              _radians(1.2) *
              regionWeights.lowerArm *
              posture *
              scale,
          rotationY: anatomicalSign *
              _radians(7.5) *
              regionWeights.lowerArm *
              (posture + 0.18 * secondBeat) *
              scale,
        ),
        roles.hand: AvatarBonePoseDelta(
          rotationX: _radians(7.0) * regionWeights.hand * palm * scale,
          rotationY: contactTangent *
              _radians(2.2) *
              regionWeights.hand *
              palm *
              scale,
          rotationZ: anatomicalSign *
              _radians(2.0) *
              regionWeights.hand *
              (palm + 0.16 * secondBeat) *
              scale,
        ),
      };
      _writeFingerPose(
        bones: bones,
        roles: roles,
        side: side,
        amount: fingers * regionWeights.fingers * scale,
      );
      boneFrames.add(
        AvatarProceduralPoseFrame(
          Map<AvatarRigBone, AvatarBonePoseDelta>.unmodifiable(bones),
        ),
      );
      morphFrames.add(
        List.unmodifiable([
          (0.16 * blink).clamp(0.0, 1.0),
          ((stage == AvatarTouchStage.notice ? 0.06 : 0.11) * expression)
              .clamp(0.0, 1.0),
          ((target.region == AvatarAnatomicalRegion.leftHand ||
                          target.region == AvatarAnatomicalRegion.rightHand
                      ? 0.13
                      : 0.09) *
                  expression)
              .clamp(0.0, 1.0),
          (stage == AvatarTouchStage.redirect ? 0.06 * expression : 0.0)
              .clamp(0.0, 1.0),
        ]),
      );
    }
    return AvatarProceduralPerformance(
      skeletalMotion: AvatarProceduralMotion(
        frameLengthMilliseconds: frameLength,
        frames: List.unmodifiable(boneFrames),
        maximumBlend: 0.88,
      ),
      morphMotion: AvatarMorphMotion(
        frameLengthMilliseconds: frameLength,
        targetNames: const [
          'Fcl_EYE_Close',
          'Fcl_EYE_Joy',
          'Fcl_MTH_Joy',
          'Fcl_MTH_Fun',
        ],
        frames: List.unmodifiable(morphFrames),
      ),
    );
  }

  int _frameCount(Duration duration, double frameLength) => math.max(
        2,
        (duration.inMicroseconds /
                Duration.microsecondsPerMillisecond /
                frameLength)
            .round(),
      );
}

AvatarTouchStage _touchStage(AvatarActionIntent intent) {
  final raw = intent.textParameters['touchStage'];
  final matches = AvatarTouchStage.values.where((stage) => stage.name == raw);
  if (matches.length != 1) {
    throw ArgumentError(
      'Upper-body touch motion requires a supported touchStage; '
      'received ${raw ?? 'null'}.',
    );
  }
  return matches.single;
}

void _writeFingerPose({
  required Map<AvatarRigBone, AvatarBonePoseDelta> bones,
  required _ArmRoles roles,
  required double side,
  required double amount,
}) {
  void open(List<AvatarRigBone> joints, List<double> degrees) {
    for (var index = 0; index < joints.length; index += 1) {
      bones[joints[index]] = AvatarBonePoseDelta(
        rotationZ: -side * _radians(degrees[index]) * amount,
      );
    }
  }

  open(roles.thumb, const [5.0, 8.0, 4.0]);
  open(roles.index, const [8.0, 16.0, 6.0]);
  open(roles.middle, const [10.0, 20.0, 8.0]);
  open(roles.ring, const [11.0, 22.0, 9.0]);
  open(roles.little, const [12.0, 24.0, 10.0]);
}

class _ArmRoles {
  const _ArmRoles(this.isLeft);

  final bool isLeft;

  AvatarRigBone get shoulder =>
      isLeft ? AvatarRigBone.leftShoulder : AvatarRigBone.rightShoulder;
  AvatarRigBone get upperArm =>
      isLeft ? AvatarRigBone.leftUpperArm : AvatarRigBone.rightUpperArm;
  AvatarRigBone get lowerArm =>
      isLeft ? AvatarRigBone.leftLowerArm : AvatarRigBone.rightLowerArm;
  AvatarRigBone get hand =>
      isLeft ? AvatarRigBone.leftHand : AvatarRigBone.rightHand;
  List<AvatarRigBone> get thumb => isLeft
      ? const [
          AvatarRigBone.leftThumb1,
          AvatarRigBone.leftThumb2,
          AvatarRigBone.leftThumb3,
        ]
      : const [
          AvatarRigBone.rightThumb1,
          AvatarRigBone.rightThumb2,
          AvatarRigBone.rightThumb3,
        ];
  List<AvatarRigBone> get index => isLeft
      ? const [
          AvatarRigBone.leftIndex1,
          AvatarRigBone.leftIndex2,
          AvatarRigBone.leftIndex3,
        ]
      : const [
          AvatarRigBone.rightIndex1,
          AvatarRigBone.rightIndex2,
          AvatarRigBone.rightIndex3,
        ];
  List<AvatarRigBone> get middle => isLeft
      ? const [
          AvatarRigBone.leftMiddle1,
          AvatarRigBone.leftMiddle2,
          AvatarRigBone.leftMiddle3,
        ]
      : const [
          AvatarRigBone.rightMiddle1,
          AvatarRigBone.rightMiddle2,
          AvatarRigBone.rightMiddle3,
        ];
  List<AvatarRigBone> get ring => isLeft
      ? const [
          AvatarRigBone.leftRing1,
          AvatarRigBone.leftRing2,
          AvatarRigBone.leftRing3,
        ]
      : const [
          AvatarRigBone.rightRing1,
          AvatarRigBone.rightRing2,
          AvatarRigBone.rightRing3,
        ];
  List<AvatarRigBone> get little => isLeft
      ? const [
          AvatarRigBone.leftLittle1,
          AvatarRigBone.leftLittle2,
          AvatarRigBone.leftLittle3,
        ]
      : const [
          AvatarRigBone.rightLittle1,
          AvatarRigBone.rightLittle2,
          AvatarRigBone.rightLittle3,
        ];
}

class _ArmRegionWeights {
  const _ArmRegionWeights({
    required this.shoulder,
    required this.upperArm,
    required this.lowerArm,
    required this.hand,
    required this.fingers,
  });

  factory _ArmRegionWeights.forRegion(AvatarAnatomicalRegion region) =>
      switch (region) {
        AvatarAnatomicalRegion.leftShoulder ||
        AvatarAnatomicalRegion.rightShoulder =>
          const _ArmRegionWeights(
            shoulder: 1,
            upperArm: 0.42,
            lowerArm: 0.18,
            hand: 0.16,
            fingers: 0.12,
          ),
        AvatarAnatomicalRegion.leftUpperArm ||
        AvatarAnatomicalRegion.rightUpperArm =>
          const _ArmRegionWeights(
            shoulder: 0.48,
            upperArm: 1,
            lowerArm: 0.45,
            hand: 0.25,
            fingers: 0.2,
          ),
        AvatarAnatomicalRegion.leftLowerArm ||
        AvatarAnatomicalRegion.rightLowerArm =>
          const _ArmRegionWeights(
            shoulder: 0.24,
            upperArm: 0.46,
            lowerArm: 1,
            hand: 0.58,
            fingers: 0.36,
          ),
        AvatarAnatomicalRegion.leftHand ||
        AvatarAnatomicalRegion.rightHand =>
          const _ArmRegionWeights(
            shoulder: 0.16,
            upperArm: 0.3,
            lowerArm: 0.62,
            hand: 1,
            fingers: 1,
          ),
        _ => throw UnsupportedError(
            'Arm weights do not support ${region.name}.',
          ),
      };

  final double shoulder;
  final double upperArm;
  final double lowerArm;
  final double hand;
  final double fingers;
}

class _TouchTrajectory {
  const _TouchTrajectory(this.stage);

  final AvatarTouchStage stage;

  double attention(double progress) => switch (stage) {
        AvatarTouchStage.notice => _pulse(progress, 0.015, 0.13, 0.58),
        AvatarTouchStage.acknowledge => _pulse(progress, 0.01, 0.11, 0.76),
        AvatarTouchStage.redirect => 0.82 * _pulse(progress, 0.01, 0.1, 0.52) +
            0.32 * _pulse(progress, 0.5, 0.65, 0.9),
      };

  double posture(double progress) => switch (stage) {
        AvatarTouchStage.notice => _pulse(progress, 0.07, 0.26, 0.72),
        AvatarTouchStage.acknowledge =>
          0.28 * _pulse(progress, 0.045, 0.16, 0.33) +
              0.82 * _pulse(progress, 0.2, 0.42, 0.88),
        AvatarTouchStage.redirect => 0.22 * _pulse(progress, 0.035, 0.15, 0.3) +
            _pulse(progress, 0.22, 0.46, 0.92),
      };

  double palm(double progress) => switch (stage) {
        AvatarTouchStage.notice => 0.55 * _pulse(progress, 0.14, 0.34, 0.7),
        AvatarTouchStage.acknowledge => _pulse(progress, 0.13, 0.38, 0.88),
        AvatarTouchStage.redirect => 0.84 * _pulse(progress, 0.11, 0.32, 0.58) +
            0.52 * _pulse(progress, 0.54, 0.7, 0.93),
      };

  double fingers(double progress) => switch (stage) {
        AvatarTouchStage.notice => 0.46 * _pulse(progress, 0.16, 0.3, 0.65),
        AvatarTouchStage.acknowledge => _pulse(progress, 0.12, 0.36, 0.86),
        AvatarTouchStage.redirect => 0.92 * _pulse(progress, 0.1, 0.3, 0.57) +
            0.6 * _pulse(progress, 0.55, 0.69, 0.91),
      };

  double expression(double progress) => switch (stage) {
        AvatarTouchStage.notice => _pulse(progress, 0.08, 0.28, 0.68),
        AvatarTouchStage.acknowledge => _pulse(progress, 0.065, 0.3, 0.84),
        AvatarTouchStage.redirect => 0.74 * _pulse(progress, 0.05, 0.24, 0.54) +
            0.58 * _pulse(progress, 0.5, 0.69, 0.91),
      };

  double blink(double progress) => switch (stage) {
        AvatarTouchStage.notice => _pulse(progress, 0.025, 0.065, 0.12),
        AvatarTouchStage.acknowledge =>
          0.9 * _pulse(progress, 0.02, 0.055, 0.105),
        AvatarTouchStage.redirect =>
          0.78 * _pulse(progress, 0.018, 0.052, 0.1) +
              0.5 * _pulse(progress, 0.58, 0.62, 0.68),
      };

  double secondBeat(double progress) => switch (stage) {
        AvatarTouchStage.notice => 0.0,
        AvatarTouchStage.acknowledge =>
          0.36 * _pulse(progress, 0.56, 0.68, 0.84),
        AvatarTouchStage.redirect => _pulse(progress, 0.53, 0.68, 0.9),
      };
}

double _pulse(double progress, double start, double peak, double end) {
  if (progress <= start || progress >= end) return 0;
  if (progress < peak) {
    return _smootherStep((progress - start) / (peak - start));
  }
  return 1 - _smootherStep((progress - peak) / (end - peak));
}

double _smootherStep(double value) {
  final x = value.clamp(0.0, 1.0);
  return x * x * x * (x * (x * 6 - 15) + 10);
}

double _radians(double degrees) => degrees * math.pi / 180;
