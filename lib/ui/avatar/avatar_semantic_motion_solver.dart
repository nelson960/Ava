import 'dart:math' as math;

import 'avatar_action_intent.dart';
import 'avatar_anatomy.dart';
import 'avatar_living_idle_solver.dart';
import 'avatar_lower_body_solver.dart';
import 'avatar_procedural_motion.dart';
import 'avatar_rig_bindings.dart';
import 'avatar_torso_touch_solver.dart';
import 'avatar_upper_body_touch_solver.dart';

/// Converts validated semantic actions into bounded local performances.
///
/// This is the only production mapping from semantic motion vocabulary to Ava's
/// bones and morph targets. Model output never reaches these transforms.
class AvatarSemanticMotionSolver {
  const AvatarSemanticMotionSolver({this.framesPerSecond = 60})
      : assert(framesPerSecond > 0 && framesPerSecond <= 120);

  final int framesPerSecond;

  AvatarLayerPerformance solve(AvatarActionIntent intent) {
    if (intent.source == AvatarActionSource.touch) {
      return _solveTouch(intent);
    }
    return switch (intent.kind) {
      AvatarActionKind.idle => AvatarLayerPerformance(
          skeletalMotion: AvatarLivingIdleSolver(
            framesPerSecond: framesPerSecond,
          ).solve(),
        ),
      AvatarActionKind.secondaryMotion => _solveSpeechSecondaryMotion(intent),
      AvatarActionKind.blink => _solveBlink(intent),
      AvatarActionKind.lookAt ||
      AvatarActionKind.listen ||
      AvatarActionKind.think =>
        _solveGaze(intent),
      AvatarActionKind.lipSync => _solveLipSync(intent),
      AvatarActionKind.expression => _solveExpression(intent),
      AvatarActionKind.explain ||
      AvatarActionKind.point =>
        _solveGesture(intent),
      AvatarActionKind.handPose ||
      AvatarActionKind.fingerPose =>
        _solveHandPose(intent),
      AvatarActionKind.recoil ||
      AvatarActionKind.weightShift ||
      AvatarActionKind.protectiveRedirect =>
        throw UnsupportedError(
          'A non-touch ${intent.kind.name} action has no registered Ava '
          'motion capability.',
        ),
      AvatarActionKind.step || AvatarActionKind.dance => throw UnsupportedError(
          'Ava motion capability ${intent.kind.name} is not registered.',
        ),
    };
  }

  AvatarLayerPerformance _solveTouch(AvatarActionIntent intent) {
    final target = intent.target;
    if (target is! AvatarContactTarget) {
      throw ArgumentError(
        'Ava procedural touch requires an anatomical contact target.',
      );
    }
    final performance = switch (target.region) {
      AvatarAnatomicalRegion.hair ||
      AvatarAnatomicalRegion.head ||
      AvatarAnatomicalRegion.face ||
      AvatarAnatomicalRegion.leftCheek ||
      AvatarAnatomicalRegion.rightCheek ||
      AvatarAnatomicalRegion.leftShoulder ||
      AvatarAnatomicalRegion.rightShoulder ||
      AvatarAnatomicalRegion.leftUpperArm ||
      AvatarAnatomicalRegion.rightUpperArm ||
      AvatarAnatomicalRegion.leftLowerArm ||
      AvatarAnatomicalRegion.rightLowerArm ||
      AvatarAnatomicalRegion.leftHand ||
      AvatarAnatomicalRegion.rightHand =>
        const AvatarUpperBodyTouchSolver().solve(intent),
      AvatarAnatomicalRegion.chest ||
      AvatarAnatomicalRegion.waist =>
        const AvatarTorsoTouchSolver().solve(intent),
      AvatarAnatomicalRegion.leftThigh ||
      AvatarAnatomicalRegion.rightThigh ||
      AvatarAnatomicalRegion.leftKnee ||
      AvatarAnatomicalRegion.rightKnee ||
      AvatarAnatomicalRegion.leftLowerLeg ||
      AvatarAnatomicalRegion.rightLowerLeg ||
      AvatarAnatomicalRegion.leftShoe ||
      AvatarAnatomicalRegion.rightShoe =>
        AvatarProceduralPerformance(
          skeletalMotion: const AvatarLowerBodySolver().solve(intent),
        ),
    };
    return AvatarLayerPerformance.fromProcedural(performance);
  }

  AvatarLayerPerformance _solveBlink(AvatarActionIntent intent) {
    _requireClaim(intent, AvatarMotionChannel.faceExpression);
    final duration = _duration(intent, const Duration(milliseconds: 180));
    final frameLength = _frameLength;
    final frameCount = _frameCount(duration);
    return AvatarLayerPerformance(
      morphMotion: AvatarMorphMotion(
        frameLengthMilliseconds: frameLength,
        targetNames: const ['Fcl_EYE_Close'],
        frames: List.unmodifiable([
          for (var frame = 0; frame < frameCount; frame += 1)
            <double>[
              _blinkEnvelope(frame / (frameCount - 1)),
            ],
        ]),
      ),
    );
  }

  AvatarLayerPerformance _solveGaze(AvatarActionIntent intent) {
    _requireClaim(intent, AvatarMotionChannel.gaze);
    final target = intent.textParameters['gaze'] ??
        (intent.kind == AvatarActionKind.think ? 'thinkingAway' : 'child');
    final preset = switch (target) {
      'child' => (yaw: 0.0, pitch: 0.0),
      'thinkingAway' => (yaw: 0.16, pitch: -0.06),
      'diagram' => (yaw: 0.0, pitch: 0.18),
      'gestureTarget' => (yaw: -0.13, pitch: 0.04),
      _ => throw ArgumentError.value(
          target,
          'intent.textParameters[gaze]',
          'must be child, thinkingAway, diagram, or gestureTarget',
        ),
    };
    final yaw = intent.numericParameters['yawRadians'] ?? preset.yaw;
    final pitch = intent.numericParameters['pitchRadians'] ?? preset.pitch;
    _requireRange(yaw, 'yawRadians', -0.52, 0.52);
    _requireRange(pitch, 'pitchRadians', -0.35, 0.35);
    final duration = _duration(intent, const Duration(milliseconds: 900));
    final frameCount = _frameCount(duration);
    return AvatarLayerPerformance(
      skeletalMotion: AvatarProceduralMotion(
        frameLengthMilliseconds: _frameLength,
        frames: List.unmodifiable([
          for (var frame = 0; frame < frameCount; frame += 1)
            AvatarProceduralPoseFrame(
              Map.unmodifiable(_gazePose(
                yaw,
                pitch,
                _holdEnvelope(frame / (frameCount - 1)),
              )),
            ),
        ]),
        maximumBlend: 0.82,
      ),
    );
  }

  AvatarLayerPerformance _solveLipSync(AvatarActionIntent intent) {
    _requireClaim(intent, AvatarMotionChannel.lipSync);
    final target = intent.target;
    if (target is! AvatarLipSyncTarget) {
      throw ArgumentError(
        'Ava lip sync requires a validated AvatarLipSyncTarget.',
      );
    }
    final duration = target.remainingDuration;
    final frameCount = _frameCount(duration);
    const targetNames = [
      'Fcl_MTH_A',
      'Fcl_MTH_I',
      'Fcl_MTH_U',
      'Fcl_MTH_E',
      'Fcl_MTH_O',
      'Fcl_MTH_Close',
    ];
    return AvatarLayerPerformance(
      morphMotion: AvatarMorphMotion(
        frameLengthMilliseconds: _frameLength,
        targetNames: targetNames,
        frames: List.unmodifiable([
          for (var frame = 0; frame < frameCount; frame += 1)
            _lipSyncFrame(
              target,
              target.audioPosition +
                  Duration(
                    microseconds: math.min(
                      (frame *
                              _frameLength *
                              Duration.microsecondsPerMillisecond)
                          .round(),
                      duration.inMicroseconds,
                    ),
                  ),
            ),
        ]),
      ),
    );
  }

  AvatarLayerPerformance _solveSpeechSecondaryMotion(
    AvatarActionIntent intent,
  ) {
    final profile = intent.textParameters['profile'];
    if (profile != 'speechSubtle') {
      throw ArgumentError.value(
        profile,
        'intent.textParameters[profile]',
        'must be speechSubtle',
      );
    }
    final duration = _duration(intent, const Duration(milliseconds: 900));
    final energy = intent.numericParameters['energy'] ?? 0.45;
    _requireRange(energy, 'energy', 0.2, 0.8);
    final direction = switch (intent.textParameters['delivery']) {
      'thoughtful' || 'concerned' => -1.0,
      'playful' || 'encouraging' => 1.0,
      'neutral' || 'warm' => 0.45,
      final unsupported => throw ArgumentError.value(
          unsupported,
          'intent.textParameters[delivery]',
          'is not a registered Ava delivery',
        ),
    };
    final frameCount = _frameCount(duration);
    return AvatarLayerPerformance(
      skeletalMotion: AvatarProceduralMotion(
        frameLengthMilliseconds: _frameLength,
        frames: List.unmodifiable([
          for (var frame = 0; frame < frameCount; frame += 1)
            () {
              final progress = frame / (frameCount - 1);
              final phraseEnvelope = math.sin(math.pi * progress);
              final cadence = (0.7 * math.sin(4 * math.pi * progress + 0.35) +
                      0.3 * math.sin(8 * math.pi * progress - 0.2)) *
                  phraseEnvelope *
                  energy;
              final lateral =
                  math.sin(2 * math.pi * progress) * phraseEnvelope * energy;
              return AvatarProceduralPoseFrame(
                Map.unmodifiable({
                  AvatarRigBone.chest: AvatarBonePoseDelta(
                    rotationX: _radians(0.34) * cadence,
                    rotationY: _radians(0.18) * direction * lateral,
                  ),
                  AvatarRigBone.upperChest: AvatarBonePoseDelta(
                    rotationX: -_radians(0.17) * cadence,
                    rotationZ: _radians(0.16) * direction * lateral,
                  ),
                  AvatarRigBone.neck: AvatarBonePoseDelta(
                    rotationX: _radians(0.11) * cadence,
                    rotationY: -_radians(0.12) * direction * lateral,
                  ),
                  AvatarRigBone.head: AvatarBonePoseDelta(
                    rotationX: -_radians(0.09) * cadence,
                    rotationY: _radians(0.08) * direction * lateral,
                  ),
                }),
              );
            }(),
        ]),
        maximumBlend: 0.38,
      ),
    );
  }

  List<double> _lipSyncFrame(
    AvatarLipSyncTarget target,
    Duration audioPosition,
  ) {
    final values = List<double>.filled(AvatarViseme.values.length, 0);
    for (final cue in target.cues) {
      final envelope = _coarticulationEnvelope(
        cue,
        audioPosition,
        target.audioDuration,
      );
      if (envelope <= 0) continue;
      values[cue.viseme.index] += cue.weight * envelope;
    }
    final total = values.fold<double>(0, (sum, value) => sum + value);
    if (total > 1) {
      for (var index = 0; index < values.length; index += 1) {
        values[index] /= total;
      }
    }
    return List.unmodifiable([
      for (final value in values) value.clamp(0.0, 1.0),
    ]);
  }

  AvatarLayerPerformance _solveExpression(AvatarActionIntent intent) {
    _requireClaim(intent, AvatarMotionChannel.faceExpression);
    final expression = intent.textParameters['expression'] ?? 'warmSmile';
    final weights = switch (expression) {
      'neutral' => const <String, double>{'Fcl_ALL_Neutral': 0.12},
      'warmSmile' => const <String, double>{
          'Fcl_EYE_Joy': 0.16,
          'Fcl_MTH_Joy': 0.24,
        },
      'curious' => const <String, double>{
          'Fcl_BRW_Surprised': 0.14,
          'Fcl_MTH_Fun': 0.08,
        },
      'thinking' => const <String, double>{
          'Fcl_BRW_Fun': 0.12,
          'Fcl_MTH_Small': 0.08,
        },
      'encouraging' => const <String, double>{
          'Fcl_EYE_Joy': 0.19,
          'Fcl_MTH_Joy': 0.28,
        },
      'surprised' => const <String, double>{
          'Fcl_EYE_Surprised': 0.18,
          'Fcl_MTH_Surprised': 0.16,
        },
      'concerned' => const <String, double>{
          'Fcl_BRW_Sorrow': 0.16,
          'Fcl_MTH_Sorrow': 0.1,
        },
      'serious' => const <String, double>{
          'Fcl_BRW_Angry': 0.08,
          'Fcl_MTH_Neutral': 0.1,
        },
      _ => throw ArgumentError.value(
          expression,
          'intent.textParameters[expression]',
          'is not a registered Ava expression',
        ),
    };
    final duration = _duration(intent, const Duration(milliseconds: 800));
    final frameCount = _frameCount(duration);
    return AvatarLayerPerformance(
      morphMotion: AvatarMorphMotion(
        frameLengthMilliseconds: _frameLength,
        targetNames: List.unmodifiable(weights.keys),
        frames: List.unmodifiable([
          for (var frame = 0; frame < frameCount; frame += 1)
            List<double>.unmodifiable([
              for (final weight in weights.values)
                weight * _holdEnvelope(frame / (frameCount - 1)),
            ]),
        ]),
      ),
    );
  }

  AvatarLayerPerformance _solveGesture(AvatarActionIntent intent) {
    final gesture = intent.textParameters['gesture'] ??
        (intent.kind == AvatarActionKind.point
            ? 'presentRight'
            : 'explainSmall');
    const registered = {
      'acknowledge',
      'explainSmall',
      'explainWide',
      'count',
      'compare',
      'presentLeft',
      'presentRight',
      'pointAtDashboard',
      'reassure',
      'celebrateSmall',
    };
    if (!registered.contains(gesture)) {
      throw ArgumentError.value(
        gesture,
        'intent.textParameters[gesture]',
        'is not a registered Ava gesture',
      );
    }
    final controlsLeft = intent.channels.contains(AvatarMotionChannel.leftArm);
    final controlsRight =
        intent.channels.contains(AvatarMotionChannel.rightArm);
    if (!controlsLeft && !controlsRight) {
      throw ArgumentError(
        'An Ava gesture must claim leftArm, rightArm, or both.',
      );
    }
    final duration = _duration(intent, const Duration(milliseconds: 900));
    final frameCount = _frameCount(duration);
    final amplitude = switch (gesture) {
      'acknowledge' => 0.32,
      'explainSmall' => 0.48,
      'explainWide' => 0.68,
      'count' => 0.46,
      'compare' => 0.58,
      'presentLeft' || 'presentRight' || 'pointAtDashboard' => 0.62,
      'reassure' => 0.34,
      'celebrateSmall' => 0.68,
      _ => throw StateError('Unreachable Ava gesture $gesture.'),
    };
    return AvatarLayerPerformance(
      skeletalMotion: AvatarProceduralMotion(
        frameLengthMilliseconds: _frameLength,
        frames: List.unmodifiable([
          for (var frame = 0; frame < frameCount; frame += 1)
            () {
              final progress = frame / (frameCount - 1);
              final shoulderAmount =
                  amplitude * _articulatedGestureEnvelope(progress, 0);
              final forearmAmount =
                  amplitude * _articulatedGestureEnvelope(progress, 0.055);
              final handAmount =
                  amplitude * _articulatedGestureEnvelope(progress, 0.11);
              final fingerAmount = _gestureFingerAmount(gesture) * handAmount;
              return AvatarProceduralPoseFrame(
                Map.unmodifiable({
                  AvatarRigBone.chest: AvatarBonePoseDelta(
                    rotationX: -_radians(0.65) * shoulderAmount,
                    rotationY: _radians(1.2) *
                        _gestureDirection(gesture) *
                        shoulderAmount,
                  ),
                  AvatarRigBone.upperChest: AvatarBonePoseDelta(
                    rotationX: -_radians(0.35) * forearmAmount,
                    rotationZ: _radians(0.4) *
                        _gestureDirection(gesture) *
                        forearmAmount,
                  ),
                  AvatarRigBone.neck: AvatarBonePoseDelta(
                    rotationX: _radians(0.3) * handAmount,
                  ),
                  AvatarRigBone.head: AvatarBonePoseDelta(
                    rotationX: -_radians(0.2) * handAmount,
                  ),
                  if (controlsLeft)
                    ..._armGesturePose(
                      isLeft: true,
                      gesture: gesture,
                      shoulderAmount: shoulderAmount,
                      forearmAmount: forearmAmount,
                      handAmount: handAmount,
                    ),
                  if (controlsLeft)
                    ..._fingerPose(isLeft: true, amount: fingerAmount),
                  if (controlsRight)
                    ..._armGesturePose(
                      isLeft: false,
                      gesture: gesture,
                      shoulderAmount: shoulderAmount,
                      forearmAmount: forearmAmount,
                      handAmount: handAmount,
                    ),
                  if (controlsRight)
                    ..._fingerPose(isLeft: false, amount: fingerAmount),
                }),
              );
            }(),
        ]),
        maximumBlend: 0.82,
      ),
    );
  }

  AvatarLayerPerformance _solveHandPose(AvatarActionIntent intent) {
    final left = intent.channels.contains(AvatarMotionChannel.leftHand) ||
        intent.channels.contains(AvatarMotionChannel.leftFingers);
    final right = intent.channels.contains(AvatarMotionChannel.rightHand) ||
        intent.channels.contains(AvatarMotionChannel.rightFingers);
    if (left == right) {
      throw ArgumentError(
        'A hand pose must claim exactly one left or right hand/finger channel.',
      );
    }
    final pose = intent.textParameters['pose'] ?? 'relaxed';
    final amount = switch (pose) {
      'relaxed' => 0.22,
      'open' => -0.35,
      'curl' => 0.45,
      _ => throw ArgumentError.value(
          pose,
          'intent.textParameters[pose]',
          'must be relaxed, open, or curl',
        ),
    };
    final duration = _duration(intent, const Duration(milliseconds: 620));
    final frameCount = _frameCount(duration);
    return AvatarLayerPerformance(
      skeletalMotion: AvatarProceduralMotion(
        frameLengthMilliseconds: _frameLength,
        frames: List.unmodifiable([
          for (var frame = 0; frame < frameCount; frame += 1)
            AvatarProceduralPoseFrame(
              Map.unmodifiable(_fingerPose(
                isLeft: left,
                amount: amount * _holdEnvelope(frame / (frameCount - 1)),
              )),
            ),
        ]),
        maximumBlend: 0.86,
      ),
    );
  }

  Map<AvatarRigBone, AvatarBonePoseDelta> _gazePose(
    double yaw,
    double pitch,
    double amount,
  ) =>
      {
        AvatarRigBone.neck: AvatarBonePoseDelta(
          rotationX: pitch * 0.18 * amount,
          rotationY: yaw * 0.16 * amount,
        ),
        AvatarRigBone.head: AvatarBonePoseDelta(
          rotationX: pitch * 0.32 * amount,
          rotationY: yaw * 0.28 * amount,
        ),
        AvatarRigBone.leftEye: AvatarBonePoseDelta(
          rotationX: pitch * 0.48 * amount,
          rotationY: yaw * 0.56 * amount,
        ),
        AvatarRigBone.rightEye: AvatarBonePoseDelta(
          rotationX: pitch * 0.48 * amount,
          rotationY: yaw * 0.56 * amount,
        ),
      };

  Map<AvatarRigBone, AvatarBonePoseDelta> _armGesturePose({
    required bool isLeft,
    required String gesture,
    required double shoulderAmount,
    required double forearmAmount,
    required double handAmount,
  }) {
    final side = isLeft ? -1.0 : 1.0;
    final shoulder =
        isLeft ? AvatarRigBone.leftShoulder : AvatarRigBone.rightShoulder;
    final upperArm =
        isLeft ? AvatarRigBone.leftUpperArm : AvatarRigBone.rightUpperArm;
    final lowerArm =
        isLeft ? AvatarRigBone.leftLowerArm : AvatarRigBone.rightLowerArm;
    final hand = isLeft ? AvatarRigBone.leftHand : AvatarRigBone.rightHand;
    final pose = _gesturePose(gesture);
    return {
      shoulder: AvatarBonePoseDelta(
        rotationZ: -side * _radians(pose.shoulderZ) * shoulderAmount,
      ),
      upperArm: AvatarBonePoseDelta(
        rotationX: _radians(pose.upperX) * shoulderAmount,
        rotationY: side * _radians(pose.upperY) * shoulderAmount,
        rotationZ: -side * _radians(pose.upperZ) * shoulderAmount,
      ),
      lowerArm: AvatarBonePoseDelta(
        rotationX: _radians(pose.lowerX) * forearmAmount,
        rotationY: side * _radians(pose.lowerY) * forearmAmount,
      ),
      hand: AvatarBonePoseDelta(
        rotationX: _radians(pose.handX) * handAmount,
        rotationY: side * _radians(pose.handY) * handAmount,
        rotationZ: side * _radians(pose.handZ) * handAmount,
      ),
    };
  }

  ({
    double shoulderZ,
    double upperX,
    double upperY,
    double upperZ,
    double lowerX,
    double lowerY,
    double handX,
    double handY,
    double handZ,
  }) _gesturePose(String gesture) => switch (gesture) {
        'acknowledge' => (
            shoulderZ: 2.5,
            upperX: -3.0,
            upperY: 4.0,
            upperZ: 8.0,
            lowerX: 12.0,
            lowerY: 7.0,
            handX: 7.0,
            handY: 2.0,
            handZ: 3.0,
          ),
        'explainSmall' => (
            shoulderZ: 3.0,
            upperX: -5.0,
            upperY: 7.0,
            upperZ: 11.0,
            lowerX: 15.0,
            lowerY: 10.0,
            handX: 10.0,
            handY: 3.0,
            handZ: 5.0,
          ),
        'explainWide' => (
            shoulderZ: 4.0,
            upperX: -6.0,
            upperY: 10.0,
            upperZ: 15.0,
            lowerX: 11.0,
            lowerY: 11.0,
            handX: 8.0,
            handY: 5.0,
            handZ: 7.0,
          ),
        'count' => (
            shoulderZ: 2.5,
            upperX: -7.0,
            upperY: 5.0,
            upperZ: 9.0,
            lowerX: 17.0,
            lowerY: 8.0,
            handX: 5.0,
            handY: 2.0,
            handZ: 2.0,
          ),
        'compare' => (
            shoulderZ: 3.5,
            upperX: -4.0,
            upperY: 9.0,
            upperZ: 13.0,
            lowerX: 13.0,
            lowerY: 12.0,
            handX: 8.0,
            handY: 4.0,
            handZ: 6.0,
          ),
        'presentLeft' || 'presentRight' || 'pointAtDashboard' => (
            shoulderZ: 4.0,
            upperX: -10.0,
            upperY: 10.0,
            upperZ: 18.0,
            lowerX: 8.0,
            lowerY: 15.0,
            handX: 5.0,
            handY: 5.0,
            handZ: 4.0,
          ),
        'reassure' => (
            shoulderZ: 2.0,
            upperX: -2.0,
            upperY: -5.0,
            upperZ: 7.0,
            lowerX: 14.0,
            lowerY: -6.0,
            handX: 7.0,
            handY: -3.0,
            handZ: 2.0,
          ),
        'celebrateSmall' => (
            shoulderZ: 4.0,
            upperX: -9.0,
            upperY: 8.0,
            upperZ: 17.0,
            lowerX: 12.0,
            lowerY: 10.0,
            handX: 8.0,
            handY: 4.0,
            handZ: 6.0,
          ),
        _ => throw StateError('Unreachable Ava gesture $gesture.'),
      };

  double _gestureFingerAmount(String gesture) => switch (gesture) {
        'count' => 0.08,
        'reassure' => -0.08,
        'acknowledge' ||
        'explainSmall' ||
        'explainWide' ||
        'compare' ||
        'presentLeft' ||
        'presentRight' ||
        'pointAtDashboard' ||
        'celebrateSmall' =>
          -0.14,
        _ => throw StateError('Unreachable Ava gesture $gesture.'),
      };

  Map<AvatarRigBone, AvatarBonePoseDelta> _fingerPose({
    required bool isLeft,
    required double amount,
  }) {
    final side = isLeft ? -1.0 : 1.0;
    final groups = isLeft
        ? const [
            [AvatarRigBone.leftThumb1, AvatarRigBone.leftThumb2],
            [AvatarRigBone.leftIndex1, AvatarRigBone.leftIndex2],
            [AvatarRigBone.leftMiddle1, AvatarRigBone.leftMiddle2],
            [AvatarRigBone.leftRing1, AvatarRigBone.leftRing2],
            [AvatarRigBone.leftLittle1, AvatarRigBone.leftLittle2],
          ]
        : const [
            [AvatarRigBone.rightThumb1, AvatarRigBone.rightThumb2],
            [AvatarRigBone.rightIndex1, AvatarRigBone.rightIndex2],
            [AvatarRigBone.rightMiddle1, AvatarRigBone.rightMiddle2],
            [AvatarRigBone.rightRing1, AvatarRigBone.rightRing2],
            [AvatarRigBone.rightLittle1, AvatarRigBone.rightLittle2],
          ];
    return {
      for (var group = 0; group < groups.length; group += 1)
        for (var joint = 0; joint < groups[group].length; joint += 1)
          groups[group][joint]: AvatarBonePoseDelta(
            rotationX: _radians(9 + group * 1.2 + joint * 4) * amount,
            rotationZ: group == 0 ? side * _radians(5) * amount : 0,
          ),
    };
  }

  double _gestureDirection(String gesture) => switch (gesture) {
        'presentLeft' => -1,
        'presentRight' || 'pointAtDashboard' => 1,
        _ => 0,
      };

  Duration _duration(AvatarActionIntent intent, Duration fallback) =>
      intent.expectedDuration ?? fallback;

  double get _frameLength => 1000 / framesPerSecond;

  int _frameCount(Duration duration) => math.max(
        2,
        (duration.inMicroseconds /
                    Duration.microsecondsPerMillisecond /
                    _frameLength)
                .ceil() +
            1,
      );

  void _requireClaim(
    AvatarActionIntent intent,
    AvatarMotionChannel channel,
  ) {
    if (!intent.channelClaims.containsKey(channel)) {
      throw ArgumentError(
        'Ava ${intent.kind.name} action ${intent.id} must claim '
        '${channel.name}.',
      );
    }
  }
}

double _holdEnvelope(double progress) {
  if (progress <= 0.22) return _smootherStep(progress / 0.22);
  if (progress < 0.78) return 1;
  return (1 - _smootherStep((progress - 0.78) / 0.22)).clamp(0.0, 1.0);
}

double _articulatedGestureEnvelope(double progress, double delay) {
  if (progress <= delay) return 0;
  final adjusted = ((progress - delay) / (1 - delay)).clamp(0.0, 1.0);
  if (adjusted <= 0.22) return _smootherStep(adjusted / 0.22);
  if (adjusted < 0.46) return 1;
  if (adjusted < 0.61) {
    return 1 - 0.28 * _smootherStep((adjusted - 0.46) / 0.15);
  }
  if (adjusted < 0.78) {
    return 0.72 + 0.12 * math.sin((adjusted - 0.61) / 0.17 * math.pi);
  }
  return 0.72 * (1 - _smootherStep(((adjusted - 0.78) / 0.22).clamp(0.0, 1.0)));
}

double _blinkEnvelope(double progress) {
  if (progress <= 0.42) return _smootherStep(progress / 0.42);
  return (1 - _smootherStep((progress - 0.42) / 0.58)).clamp(0.0, 1.0);
}

double _coarticulationEnvelope(
  AvatarVisemeCue cue,
  Duration position,
  Duration audioDuration,
) {
  final start = cue.start.inMicroseconds.toDouble();
  final end = cue.end.inMicroseconds.toDouble();
  final duration = end - start;
  final anticipation = (duration * 0.55).clamp(35000.0, 90000.0).toDouble();
  final release = (duration * 0.7).clamp(45000.0, 110000.0).toDouble();
  final attackStart = math.max(0.0, start - anticipation);
  final peak = start + duration * 0.52;
  final releaseEnd = math.min(
    audioDuration.inMicroseconds.toDouble(),
    end + release,
  );
  final at = position.inMicroseconds.toDouble();
  if (at <= attackStart || at >= releaseEnd) return 0;
  if (at <= peak) {
    final span = peak - attackStart;
    return span <= 0 ? 1 : _smootherStep((at - attackStart) / span);
  }
  final span = releaseEnd - peak;
  return span <= 0
      ? 0
      : (1 - _smootherStep((at - peak) / span)).clamp(0.0, 1.0);
}

double _smootherStep(double value) {
  final x = value.clamp(0.0, 1.0);
  return x * x * x * (x * (x * 6 - 15) + 10);
}

double _radians(double degrees) => degrees * math.pi / 180;

void _requireRange(
  double value,
  String name,
  double minimum,
  double maximum,
) {
  if (!value.isFinite || value < minimum || value > maximum) {
    throw ArgumentError.value(
      value,
      name,
      'must be finite and between $minimum and $maximum',
    );
  }
}
