import 'dart:math' as math;

import 'avatar_action_intent.dart';
import 'avatar_anatomy.dart';
import 'avatar_procedural_motion.dart';
import 'avatar_rig_bindings.dart';

typedef AvatarLowerBodyPoseFrame = AvatarProceduralPoseFrame;

class AvatarLowerBodyMotion extends AvatarProceduralMotion {
  const AvatarLowerBodyMotion({
    required super.frameLengthMilliseconds,
    required super.frames,
    required super.maximumBlend,
    required this.stage,
  });

  final AvatarTouchStage stage;
}

/// Builds a contact-specific balance response in Ava's anatomical frame.
///
/// The picked surface direction determines the physical response. Camera angle
/// only changes how strongly the response is presented; it can never swap the
/// contacted leg or invert the movement. Every response coordinates the
/// contacted chain, planted support chain, pelvis, spine and gaze so the limb
/// never appears to move independently of Ava's balance.
class AvatarLowerBodySolver {
  const AvatarLowerBodySolver({this.framesPerSecond = 120})
      : assert(framesPerSecond > 0);

  final int framesPerSecond;

  AvatarLowerBodyMotion solve(AvatarActionIntent intent) {
    final target = intent.target;
    if (target is! AvatarContactTarget) {
      throw ArgumentError(
        'Lower-body motion requires an anatomical contact target.',
      );
    }
    final side = target.region.side;
    if (side == AvatarBodySide.center || !_isLowerBodyRegion(target.region)) {
      throw ArgumentError(
        'Lower-body motion requires a left or right leg/foot region.',
      );
    }
    if (!_claimsContactedSide(intent, side)) {
      throw ArgumentError(
        'Lower-body motion channels must match the contacted side.',
      );
    }
    final duration = intent.expectedDuration;
    if (duration == null) {
      throw ArgumentError('Lower-body motion requires an expected duration.');
    }
    final rawStrength = intent.numericParameters['strength'];
    final relativeView = intent.numericParameters['relativeViewAzimuth'];
    final rawStage = intent.textParameters['touchStage'];
    if (rawStrength == null ||
        !rawStrength.isFinite ||
        rawStrength <= 0 ||
        relativeView == null ||
        !relativeView.isFinite) {
      throw ArgumentError(
        'Lower-body motion requires finite strength and relative view inputs.',
      );
    }
    final stage = AvatarTouchStage.values.where(
      (candidate) => candidate.name == rawStage,
    );
    if (stage.length != 1) {
      throw ArgumentError(
        'Lower-body motion requires a supported touchStage; received '
        '${rawStage ?? 'null'}.',
      );
    }

    final strength = rawStrength.clamp(0.25, 1.2).toDouble();
    final touchStage = stage.single;
    final trajectory = _trajectoryFor(touchStage);
    final recipe = _recipeFor(target.region);
    final frameLength = 1000 / framesPerSecond;
    final frameCount = math.max(
      2,
      (duration.inMicroseconds /
              Duration.microsecondsPerMillisecond /
              frameLength)
          .round(),
    );
    final sideSign = side == AvatarBodySide.left ? 1.0 : -1.0;
    final contactAngle = _normalizedAngle(target.radialAngle);
    final viewToContact = _normalizedAngle(relativeView - contactAngle);
    final viewPresentation = 0.84 + 0.16 * math.cos(viewToContact).abs();
    final longitudinalLeverage =
        0.9 + 0.1 * ((target.longitudinalPosition - 0.5).abs() * 2);
    final amplitude = _degreesToRadians(
      (2.8 + 2.4 * strength) *
          viewPresentation *
          longitudinalLeverage *
          trajectory.motionScale,
    );
    final sagittalDirection = -math.cos(contactAngle);
    final lateralDirection = -math.sin(contactAngle);

    final contactedUpper = side == AvatarBodySide.left
        ? AvatarRigBone.leftUpperLeg
        : AvatarRigBone.rightUpperLeg;
    final contactedLower = side == AvatarBodySide.left
        ? AvatarRigBone.leftLowerLeg
        : AvatarRigBone.rightLowerLeg;
    final contactedFoot = side == AvatarBodySide.left
        ? AvatarRigBone.leftFoot
        : AvatarRigBone.rightFoot;
    final contactedToe = side == AvatarBodySide.left
        ? AvatarRigBone.leftToe
        : AvatarRigBone.rightToe;
    final supportUpper = side == AvatarBodySide.left
        ? AvatarRigBone.rightUpperLeg
        : AvatarRigBone.leftUpperLeg;
    final supportLower = side == AvatarBodySide.left
        ? AvatarRigBone.rightLowerLeg
        : AvatarRigBone.leftLowerLeg;
    final supportFoot = side == AvatarBodySide.left
        ? AvatarRigBone.rightFoot
        : AvatarRigBone.leftFoot;
    final supportToe = side == AvatarBodySide.left
        ? AvatarRigBone.rightToe
        : AvatarRigBone.leftToe;

    final frames = <AvatarLowerBodyPoseFrame>[];
    for (var frame = 0; frame < frameCount; frame += 1) {
      final progress = frame / (frameCount - 1);
      final contactEnvelope = trajectory.contactEnvelope(progress);
      final balanceEnvelope = trajectory.balanceEnvelope(progress);
      final footEnvelope = trajectory.footEnvelope(progress);
      final gazeEnvelope = trajectory.gazeEnvelope(progress);

      final sagittal =
          amplitude * sagittalDirection * recipe.upperLeg * contactEnvelope;
      final lateral =
          amplitude * lateralDirection * recipe.upperLeg * contactEnvelope;
      final kneeFlex = amplitude *
          recipe.knee *
          (0.7 + 0.3 * sagittalDirection.abs()) *
          contactEnvelope;
      final footAction = amplitude * recipe.ankle * footEnvelope;
      final toeAction = amplitude * recipe.toe * footEnvelope;
      final support = amplitude * recipe.support * balanceEnvelope;
      final gazePitch =
          _degreesToRadians(recipe.gazePitchDegrees) * strength * gazeEnvelope;
      final gazeYaw =
          _degreesToRadians(3.2 + 1.2 * strength) * sideSign * gazeEnvelope;
      final contactedFootCompensation =
          -(sagittal + kneeFlex) * recipe.footLevelCompensation;
      final supportKneeFlex = support * recipe.supportKneeFlex;

      frames.add(
        AvatarLowerBodyPoseFrame(
          Map.unmodifiable({
            AvatarRigBone.hips: AvatarBonePoseDelta(
              rotationX: -sagittal * 0.14,
              rotationY: -sideSign * amplitude * 0.045 * balanceEnvelope,
              rotationZ: -sideSign *
                  amplitude *
                  recipe.hipCounterbalance *
                  balanceEnvelope,
              translationX: -sideSign *
                  recipe.hipShiftMeters *
                  strength *
                  balanceEnvelope,
              translationY: -recipe.hipDropMeters * strength * balanceEnvelope,
              translationZ: sagittalDirection *
                  recipe.hipShiftMeters *
                  0.18 *
                  strength *
                  balanceEnvelope,
            ),
            AvatarRigBone.spine: AvatarBonePoseDelta(
              rotationX: sagittal * 0.1,
              rotationY: sideSign * amplitude * 0.04 * balanceEnvelope,
              rotationZ: sideSign *
                  amplitude *
                  recipe.hipCounterbalance *
                  0.72 *
                  balanceEnvelope,
            ),
            AvatarRigBone.chest: AvatarBonePoseDelta(
              rotationX: -sagittal * 0.04,
              rotationY: sideSign * amplitude * 0.07 * balanceEnvelope,
              rotationZ: sideSign *
                  amplitude *
                  recipe.hipCounterbalance *
                  0.38 *
                  balanceEnvelope,
            ),
            contactedUpper: AvatarBonePoseDelta(
              rotationX: sagittal,
              rotationY: -sideSign * amplitude * 0.045 * contactEnvelope,
              rotationZ:
                  lateral + sideSign * amplitude * 0.055 * contactEnvelope,
            ),
            contactedLower: AvatarBonePoseDelta(rotationX: kneeFlex),
            contactedFoot: AvatarBonePoseDelta(
              rotationX: contactedFootCompensation + footAction * 0.42,
              rotationY: -sideSign * footAction * 0.06,
              rotationZ: -lateral * recipe.footLevelCompensation * 0.36,
            ),
            contactedToe: AvatarBonePoseDelta(rotationX: -toeAction),
            supportUpper: AvatarBonePoseDelta(
              rotationX: -sagittal * 0.08 * balanceEnvelope,
              rotationY: sideSign * support * 0.05,
              rotationZ: -sideSign * support,
            ),
            supportLower: AvatarBonePoseDelta(rotationX: supportKneeFlex),
            supportFoot: AvatarBonePoseDelta(
              rotationX: -supportKneeFlex * 0.72,
              rotationZ: sideSign * support * 0.12,
            ),
            supportToe: const AvatarBonePoseDelta(),
            AvatarRigBone.neck: AvatarBonePoseDelta(
              rotationX: gazePitch * 0.24,
              rotationZ: gazeYaw * 0.2,
            ),
            AvatarRigBone.head: AvatarBonePoseDelta(
              rotationX: gazePitch * 0.4,
              rotationZ: gazeYaw * 0.34,
            ),
          }),
        ),
      );
    }
    return AvatarLowerBodyMotion(
      frameLengthMilliseconds: frameLength,
      frames: List.unmodifiable(frames),
      maximumBlend: (0.72 + strength * 0.12 + trajectory.maximumBlendAdjustment)
          .clamp(0.76, 0.92)
          .toDouble(),
      stage: touchStage,
    );
  }

  bool _claimsContactedSide(AvatarActionIntent intent, AvatarBodySide side) {
    final requiredChannels = side == AvatarBodySide.left
        ? const {
            AvatarMotionChannel.leftLeg,
            AvatarMotionChannel.leftFoot,
          }
        : const {
            AvatarMotionChannel.rightLeg,
            AvatarMotionChannel.rightFoot,
          };
    return intent.channels.any(requiredChannels.contains);
  }
}

class _LowerBodyRecipe {
  const _LowerBodyRecipe({
    required this.upperLeg,
    required this.knee,
    required this.ankle,
    required this.toe,
    required this.support,
    required this.hipCounterbalance,
    required this.hipShiftMeters,
    required this.hipDropMeters,
    required this.gazePitchDegrees,
    required this.footLevelCompensation,
    required this.supportKneeFlex,
  });

  final double upperLeg;
  final double knee;
  final double ankle;
  final double toe;
  final double support;
  final double hipCounterbalance;
  final double hipShiftMeters;
  final double hipDropMeters;
  final double gazePitchDegrees;
  final double footLevelCompensation;
  final double supportKneeFlex;
}

_LowerBodyRecipe _recipeFor(AvatarAnatomicalRegion region) => switch (region) {
      AvatarAnatomicalRegion.leftThigh ||
      AvatarAnatomicalRegion.rightThigh =>
        const _LowerBodyRecipe(
          upperLeg: 1,
          knee: 0.34,
          ankle: 0.16,
          toe: 0.04,
          support: 0.25,
          hipCounterbalance: 0.2,
          hipShiftMeters: 0.011,
          hipDropMeters: 0.0025,
          gazePitchDegrees: 4.8,
          footLevelCompensation: 0.72,
          supportKneeFlex: 0.46,
        ),
      AvatarAnatomicalRegion.leftKnee ||
      AvatarAnatomicalRegion.rightKnee =>
        const _LowerBodyRecipe(
          upperLeg: 0.48,
          knee: 1,
          ankle: 0.36,
          toe: 0.08,
          support: 0.3,
          hipCounterbalance: 0.24,
          hipShiftMeters: 0.012,
          hipDropMeters: 0.0035,
          gazePitchDegrees: 6.8,
          footLevelCompensation: 0.78,
          supportKneeFlex: 0.5,
        ),
      AvatarAnatomicalRegion.leftLowerLeg ||
      AvatarAnatomicalRegion.rightLowerLeg =>
        const _LowerBodyRecipe(
          upperLeg: 0.32,
          knee: 0.68,
          ankle: 0.82,
          toe: 0.18,
          support: 0.31,
          hipCounterbalance: 0.26,
          hipShiftMeters: 0.013,
          hipDropMeters: 0.004,
          gazePitchDegrees: 8.6,
          footLevelCompensation: 0.82,
          supportKneeFlex: 0.52,
        ),
      AvatarAnatomicalRegion.leftShoe ||
      AvatarAnatomicalRegion.rightShoe =>
        const _LowerBodyRecipe(
          upperLeg: 0.25,
          knee: 0.56,
          ankle: 1,
          toe: 0.8,
          support: 0.36,
          hipCounterbalance: 0.29,
          hipShiftMeters: 0.014,
          hipDropMeters: 0.0045,
          gazePitchDegrees: 10.4,
          footLevelCompensation: 0.86,
          supportKneeFlex: 0.56,
        ),
      _ => throw ArgumentError(
          'Ava region ${region.name} is not a lower-body response region.',
        ),
    };

bool _isLowerBodyRegion(AvatarAnatomicalRegion region) => switch (region) {
      AvatarAnatomicalRegion.leftThigh ||
      AvatarAnatomicalRegion.rightThigh ||
      AvatarAnatomicalRegion.leftKnee ||
      AvatarAnatomicalRegion.rightKnee ||
      AvatarAnatomicalRegion.leftLowerLeg ||
      AvatarAnatomicalRegion.rightLowerLeg ||
      AvatarAnatomicalRegion.leftShoe ||
      AvatarAnatomicalRegion.rightShoe =>
        true,
      _ => false,
    };

class _TouchStageTrajectory {
  const _TouchStageTrajectory({
    required this.stage,
    required this.motionScale,
    required this.maximumBlendAdjustment,
  });

  final AvatarTouchStage stage;
  final double motionScale;
  final double maximumBlendAdjustment;

  double contactEnvelope(double progress) => switch (stage) {
        AvatarTouchStage.notice => _pulse(progress, 0, 0.18, 0.78),
        AvatarTouchStage.acknowledge => 0.34 * _pulse(progress, 0, 0.13, 0.38) +
            0.76 * _pulse(progress, 0.16, 0.43, 0.9),
        AvatarTouchStage.redirect => 0.28 * _pulse(progress, 0, 0.11, 0.32) +
            0.88 * _pulse(progress, 0.18, 0.46, 0.92),
      };

  double balanceEnvelope(double progress) => switch (stage) {
        AvatarTouchStage.notice => 0.52 * _pulse(progress, 0.045, 0.25, 0.86),
        AvatarTouchStage.acknowledge => _pulse(progress, 0.08, 0.38, 0.94),
        AvatarTouchStage.redirect => _pulse(progress, 0.06, 0.34, 0.96),
      };

  double footEnvelope(double progress) => switch (stage) {
        AvatarTouchStage.notice => 0.16 * _pulse(progress, 0.12, 0.3, 0.64),
        AvatarTouchStage.acknowledge => 0.5 * _pulse(progress, 0.22, 0.48, 0.8),
        AvatarTouchStage.redirect => 0.74 * _pulse(progress, 0.18, 0.4, 0.67) +
            0.28 * _pulse(progress, 0.6, 0.72, 0.91),
      };

  double gazeEnvelope(double progress) => switch (stage) {
        AvatarTouchStage.notice => _pulse(progress, 0.035, 0.2, 0.72),
        AvatarTouchStage.acknowledge => _pulse(progress, 0.025, 0.24, 0.82),
        AvatarTouchStage.redirect => _pulse(progress, 0.02, 0.2, 0.76),
      };
}

_TouchStageTrajectory _trajectoryFor(AvatarTouchStage stage) => switch (stage) {
      AvatarTouchStage.notice => const _TouchStageTrajectory(
          stage: AvatarTouchStage.notice,
          motionScale: 0.68,
          maximumBlendAdjustment: 0,
        ),
      AvatarTouchStage.acknowledge => const _TouchStageTrajectory(
          stage: AvatarTouchStage.acknowledge,
          motionScale: 0.86,
          maximumBlendAdjustment: 0.025,
        ),
      AvatarTouchStage.redirect => const _TouchStageTrajectory(
          stage: AvatarTouchStage.redirect,
          motionScale: 1,
          maximumBlendAdjustment: 0.045,
        ),
    };

double _pulse(
  double progress,
  double start,
  double peak,
  double end,
) {
  if (!(start >= 0 && start < peak && peak < end && end <= 1)) {
    throw ArgumentError('Motion pulse requires 0 <= start < peak < end <= 1.');
  }
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

double _degreesToRadians(double degrees) => degrees * math.pi / 180;

double _normalizedAngle(double angle) {
  var normalized = angle % (2 * math.pi);
  if (normalized > math.pi) normalized -= 2 * math.pi;
  if (normalized < -math.pi) normalized += 2 * math.pi;
  return normalized;
}
