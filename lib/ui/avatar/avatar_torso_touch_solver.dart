import 'dart:math' as math;

import 'avatar_action_intent.dart';
import 'avatar_anatomy.dart';
import 'avatar_procedural_motion.dart';
import 'avatar_rig_bindings.dart';

/// Produces a local, layered response for chest and waist contacts.
///
/// Hips, root and legs are deliberately absent. An ordinary center touch is
/// not allowed to translate the silhouette or disturb either planted foot.
/// Eyes orient first, the head follows, and then the contacted torso responds.
/// The recovery overlaps those layers instead of reversing one synchronized
/// full-body pose. Arm ownership remains independent.
class AvatarTorsoTouchSolver {
  const AvatarTorsoTouchSolver({this.framesPerSecond = 120})
      : assert(framesPerSecond > 0);

  final int framesPerSecond;

  AvatarProceduralPerformance solve(AvatarActionIntent intent) {
    final target = intent.target;
    if (target is! AvatarContactTarget ||
        (target.region != AvatarAnatomicalRegion.chest &&
            target.region != AvatarAnatomicalRegion.waist)) {
      throw ArgumentError(
        'Torso touch motion requires a chest or waist contact target.',
      );
    }
    final duration = intent.expectedDuration;
    if (duration == null) {
      throw ArgumentError('Torso touch motion requires an expected duration.');
    }
    final strength = intent.numericParameters['strength'];
    final rawStage = intent.textParameters['touchStage'];
    if (strength == null || !strength.isFinite || strength <= 0) {
      throw ArgumentError(
        'Torso touch motion requires a finite positive strength.',
      );
    }
    final matchingStages = AvatarTouchStage.values.where(
      (candidate) => candidate.name == rawStage,
    );
    if (matchingStages.length != 1) {
      throw ArgumentError(
        'Torso touch motion requires a supported touchStage; received '
        '${rawStage ?? 'null'}.',
      );
    }

    final stage = matchingStages.single;
    final trajectory = _TorsoTouchTrajectory(stage);
    final frameLength = 1000 / framesPerSecond;
    final frameCount = math.max(
      2,
      (duration.inMicroseconds /
              Duration.microsecondsPerMillisecond /
              frameLength)
          .round(),
    );
    final safeStrength = strength.clamp(0.45, 1.0).toDouble();
    final stageScale = switch (stage) {
      AvatarTouchStage.notice => 0.68,
      AvatarTouchStage.acknowledge => 0.78,
      AvatarTouchStage.redirect => 0.86,
    };
    final regionScale =
        target.region == AvatarAnatomicalRegion.chest ? 1.0 : 0.72;
    final responseScale = safeStrength * stageScale * regionScale;
    final horizontalRadiusSquared =
        target.modelPosition.x * target.modelPosition.x +
            target.modelPosition.z * target.modelPosition.z;
    final bodyRadialAngle = horizontalRadiusSquared > 0.0001
        ? math.atan2(target.modelPosition.x, target.modelPosition.z)
        : target.radialAngle;
    final contactFront = math.cos(bodyRadialAngle);
    final contactTangent = math.sin(bodyRadialAngle);
    final sideBias = target.modelPosition.x.abs() > 0.025
        ? (target.modelPosition.x >= 0 ? 1.0 : -1.0)
        : 0.0;
    final pitch = _radians(3.2) * contactFront * responseScale;
    final turn = _radians(2.2) * contactTangent * responseScale;
    final bodyAccent = _radians(1.2) * responseScale;

    final boneFrames = <AvatarProceduralPoseFrame>[];
    final morphFrames = <List<double>>[];
    for (var frame = 0; frame < frameCount; frame += 1) {
      final progress = frame / (frameCount - 1);
      final gaze = trajectory.gaze(progress);
      final torso = trajectory.torso(progress);
      final expression = trajectory.expression(progress);
      final blink = trajectory.blink(progress);
      boneFrames.add(
        AvatarProceduralPoseFrame(
          Map.unmodifiable({
            AvatarRigBone.spine: AvatarBonePoseDelta(
              rotationX: -pitch * 0.12 * torso,
              rotationY: turn * 0.1 * torso,
              rotationZ: -sideBias * bodyAccent * 0.08 * torso,
            ),
            AvatarRigBone.chest: AvatarBonePoseDelta(
              rotationX: -pitch * 0.38 * torso,
              rotationY: turn * 0.3 * torso,
              rotationZ: -sideBias * bodyAccent * 0.1 * torso,
            ),
            AvatarRigBone.upperChest: AvatarBonePoseDelta(
              rotationX: -pitch * 0.18 * torso,
              rotationY: turn * 0.18 * torso,
              rotationZ: sideBias * bodyAccent * 0.08 * torso,
            ),
            AvatarRigBone.neck: AvatarBonePoseDelta(
              rotationX: _radians(0.18) * responseScale * gaze,
              rotationZ: sideBias * _radians(0.14) * responseScale * gaze,
            ),
            AvatarRigBone.head: AvatarBonePoseDelta(
              rotationX: _radians(0.32) * responseScale * gaze,
              rotationZ: sideBias * _radians(0.24) * responseScale * gaze,
            ),
          }),
        ),
      );
      morphFrames.add(
        List.unmodifiable([
          (0.16 * blink).clamp(0.0, 1.0),
          (0.12 * expression).clamp(0.0, 1.0),
          (0.1 * expression).clamp(0.0, 1.0),
          (stage == AvatarTouchStage.redirect ? 0.035 * expression : 0.0)
              .clamp(0.0, 1.0),
          (stage == AvatarTouchStage.redirect ? 0.04 * expression : 0.0)
              .clamp(0.0, 1.0),
        ]),
      );
    }

    return AvatarProceduralPerformance(
      skeletalMotion: AvatarProceduralMotion(
        frameLengthMilliseconds: frameLength,
        frames: List.unmodifiable(boneFrames),
        maximumBlend: switch (stage) {
          AvatarTouchStage.notice => 0.72,
          AvatarTouchStage.acknowledge => 0.78,
          AvatarTouchStage.redirect => 0.82,
        },
      ),
      morphMotion: AvatarMorphMotion(
        frameLengthMilliseconds: frameLength,
        targetNames: const [
          'Fcl_EYE_Close',
          'Fcl_EYE_Joy',
          'Fcl_MTH_Joy',
          'Fcl_EYE_Surprised',
          'Fcl_MTH_Fun',
        ],
        frames: List.unmodifiable(morphFrames),
      ),
    );
  }
}

class _TorsoTouchTrajectory {
  const _TorsoTouchTrajectory(this.stage);

  final AvatarTouchStage stage;

  double gaze(double progress) => switch (stage) {
        AvatarTouchStage.notice => _pulse(progress, 0.025, 0.15, 0.58),
        AvatarTouchStage.acknowledge => _pulse(progress, 0.02, 0.14, 0.66),
        AvatarTouchStage.redirect => _pulse(progress, 0.015, 0.13, 0.72),
      };

  double torso(double progress) => switch (stage) {
        AvatarTouchStage.notice => _pulse(progress, 0.1, 0.32, 0.74),
        AvatarTouchStage.acknowledge => _pulse(progress, 0.08, 0.34, 0.82),
        AvatarTouchStage.redirect => _pulse(progress, 0.06, 0.3, 0.86),
      };

  double expression(double progress) => switch (stage) {
        AvatarTouchStage.notice => _pulse(progress, 0.1, 0.3, 0.72),
        AvatarTouchStage.acknowledge => _pulse(progress, 0.08, 0.32, 0.84),
        AvatarTouchStage.redirect => _pulse(progress, 0.07, 0.28, 0.88),
      };

  double blink(double progress) => switch (stage) {
        AvatarTouchStage.notice => _pulse(progress, 0.035, 0.085, 0.15),
        AvatarTouchStage.acknowledge => _pulse(progress, 0.025, 0.07, 0.13),
        AvatarTouchStage.redirect => _pulse(progress, 0.02, 0.065, 0.13),
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
