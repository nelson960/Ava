import 'dart:math' as math;

import 'avatar_procedural_motion.dart';
import 'avatar_rig_bindings.dart';

/// A low-frequency additive performance layered over the imported idle.
///
/// Every curve is continuous at the loop boundary. There is no per-frame
/// randomness, which avoids the vibration produced by noisy pose updates.
class AvatarLivingIdleSolver {
  const AvatarLivingIdleSolver({
    this.framesPerSecond = 60,
    this.period = const Duration(seconds: 12),
  })  : assert(framesPerSecond > 0),
        assert(period > Duration.zero);

  final int framesPerSecond;
  final Duration period;

  AvatarProceduralMotion solve() {
    final frameLength = 1000 / framesPerSecond;
    final frameCount = math.max(
      2,
      (period.inMicroseconds /
              Duration.microsecondsPerMillisecond /
              frameLength)
          .round(),
    );
    final frames = <AvatarProceduralPoseFrame>[];
    for (var frame = 0; frame < frameCount; frame += 1) {
      final progress = frame / (frameCount - 1);
      final breath = 0.78 * math.sin(6 * math.pi * progress) +
          0.16 * math.sin(12 * math.pi * progress - 0.35);
      final slowAsymmetry = math.sin(2 * math.pi * progress - 0.7);
      final headDrift = math.sin(4 * math.pi * progress + 0.9);
      final chestCounter = math.sin(2 * math.pi * progress + 1.4);

      frames.add(
        AvatarProceduralPoseFrame(
          Map.unmodifiable({
            AvatarRigBone.spine: AvatarBonePoseDelta(
              rotationX: _radians(0.1) * breath,
              rotationZ: _radians(0.12) * slowAsymmetry,
            ),
            AvatarRigBone.chest: AvatarBonePoseDelta(
              rotationX: _radians(0.23) * breath,
              rotationY: _radians(0.08) * chestCounter,
              rotationZ: _radians(0.1) * slowAsymmetry,
            ),
            AvatarRigBone.upperChest: AvatarBonePoseDelta(
              rotationX: _radians(0.17) * breath,
              rotationY: -_radians(0.06) * chestCounter,
              rotationZ: _radians(0.08) * slowAsymmetry,
            ),
            AvatarRigBone.neck: AvatarBonePoseDelta(
              rotationY: _radians(0.11) * headDrift,
              rotationZ: -_radians(0.06) * slowAsymmetry,
            ),
            AvatarRigBone.head: AvatarBonePoseDelta(
              rotationX: _radians(0.08) * breath,
              rotationY: _radians(0.2) * headDrift,
              rotationZ: -_radians(0.11) * slowAsymmetry,
            ),
          }),
        ),
      );
    }
    return AvatarProceduralMotion(
      frameLengthMilliseconds: frameLength,
      frames: List.unmodifiable(frames),
      maximumBlend: 0.42,
      loop: true,
    );
  }
}

double _radians(double degrees) => degrees * math.pi / 180;
