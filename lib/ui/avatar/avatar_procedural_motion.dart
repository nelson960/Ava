import 'avatar_rig_bindings.dart';

/// A small local-space change layered over the pose produced by Ava's base
/// performance. Scale is intentionally excluded: touch must never make the
/// character appear to squash, grow, or move as one rigid image.
class AvatarBonePoseDelta {
  const AvatarBonePoseDelta({
    this.rotationX = 0,
    this.rotationY = 0,
    this.rotationZ = 0,
    this.translationX = 0,
    this.translationY = 0,
    this.translationZ = 0,
  });

  final double rotationX;
  final double rotationY;
  final double rotationZ;
  final double translationX;
  final double translationY;
  final double translationZ;

  bool get isNeutral =>
      rotationX == 0 &&
      rotationY == 0 &&
      rotationZ == 0 &&
      translationX == 0 &&
      translationY == 0 &&
      translationZ == 0;

  static AvatarBonePoseDelta interpolate(
    AvatarBonePoseDelta start,
    AvatarBonePoseDelta end,
    double progress,
  ) {
    if (!progress.isFinite || progress < 0 || progress > 1) {
      throw ArgumentError.value(
        progress,
        'progress',
        'must be finite and between zero and one',
      );
    }
    double mix(double left, double right) => left + (right - left) * progress;
    return AvatarBonePoseDelta(
      rotationX: mix(start.rotationX, end.rotationX),
      rotationY: mix(start.rotationY, end.rotationY),
      rotationZ: mix(start.rotationZ, end.rotationZ),
      translationX: mix(start.translationX, end.translationX),
      translationY: mix(start.translationY, end.translationY),
      translationZ: mix(start.translationZ, end.translationZ),
    );
  }
}

class AvatarProceduralPoseFrame {
  const AvatarProceduralPoseFrame(this.bones);

  final Map<AvatarRigBone, AvatarBonePoseDelta> bones;
}

class AvatarProceduralMotion {
  const AvatarProceduralMotion({
    required this.frameLengthMilliseconds,
    required this.frames,
    required this.maximumBlend,
    this.loop = false,
  });

  final double frameLengthMilliseconds;
  final List<AvatarProceduralPoseFrame> frames;
  final double maximumBlend;
  final bool loop;

  Duration get duration => Duration(
        microseconds: ((frames.length - 1) *
                frameLengthMilliseconds *
                Duration.microsecondsPerMillisecond)
            .round(),
      );
}

class AvatarMorphMotion {
  const AvatarMorphMotion({
    required this.frameLengthMilliseconds,
    required this.targetNames,
    required this.frames,
  });

  final double frameLengthMilliseconds;
  final List<String> targetNames;
  final List<List<double>> frames;

  Duration get duration => Duration(
        microseconds: ((frames.length - 1) *
                frameLengthMilliseconds *
                Duration.microsecondsPerMillisecond)
            .round(),
      );
}

class AvatarProceduralPerformance {
  const AvatarProceduralPerformance({
    required this.skeletalMotion,
    this.morphMotion,
  });

  final AvatarProceduralMotion skeletalMotion;
  final AvatarMorphMotion? morphMotion;
}

/// A renderer layer may control bones, morph targets, or both.
///
/// Touch solvers predate morph-only actions and therefore use
/// [AvatarProceduralPerformance]. The production mixer uses this type so a
/// blink or viseme does not need to claim a fake skeletal bone merely to enter
/// the renderer graph.
class AvatarLayerPerformance {
  AvatarLayerPerformance({
    this.skeletalMotion,
    this.morphMotion,
  }) {
    if (skeletalMotion == null && morphMotion == null) {
      throw ArgumentError(
        'An avatar layer must provide skeletal motion, morph motion, or both.',
      );
    }
  }

  factory AvatarLayerPerformance.fromProcedural(
    AvatarProceduralPerformance performance,
  ) =>
      AvatarLayerPerformance(
        skeletalMotion: performance.skeletalMotion,
        morphMotion: performance.morphMotion,
      );

  final AvatarProceduralMotion? skeletalMotion;
  final AvatarMorphMotion? morphMotion;

  bool get loops => skeletalMotion?.loop == true;

  Duration get duration {
    final skeletalDuration = skeletalMotion?.duration ?? Duration.zero;
    final morphDuration = morphMotion?.duration ?? Duration.zero;
    return skeletalDuration >= morphDuration ? skeletalDuration : morphDuration;
  }
}
