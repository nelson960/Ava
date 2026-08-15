import 'avatar_motion_scheduler.dart';

class AvatarMotionDriverCapabilities {
  const AvatarMotionDriverCapabilities({
    required this.layeredSkeletalAnimation,
    required this.additiveAnimation,
    required this.proceduralBoneControl,
    required this.morphTargetControl,
    required this.skinnedSurfacePicking,
    required this.cameraPoseReporting,
    required this.maximumConcurrentActions,
  }) : assert(maximumConcurrentActions > 0);

  final bool layeredSkeletalAnimation;
  final bool additiveAnimation;
  final bool proceduralBoneControl;
  final bool morphTargetControl;
  final bool skinnedSurfacePicking;
  final bool cameraPoseReporting;
  final int maximumConcurrentActions;

  bool get supportsProductionInteraction =>
      layeredSkeletalAnimation &&
      additiveAnimation &&
      proceduralBoneControl &&
      morphTargetControl &&
      skinnedSurfacePicking &&
      cameraPoseReporting &&
      maximumConcurrentActions >= 8;
}

sealed class AvatarMotionDriverEvent {
  const AvatarMotionDriverEvent();
}

final class AvatarMotionCompleted extends AvatarMotionDriverEvent {
  const AvatarMotionCompleted({
    required this.runtimeId,
    required this.occurredAt,
  });

  final String runtimeId;
  final Duration occurredAt;
}

final class AvatarMotionFailed extends AvatarMotionDriverEvent {
  const AvatarMotionFailed({
    required this.runtimeId,
    required this.message,
  });

  final String runtimeId;
  final String message;
}

/// Boundary between Ava's behavior system and a concrete 3D renderer.
///
/// The current model-viewer adapter does not implement this contract because
/// it cannot satisfy its production capabilities. The Filament spike will be
/// implemented behind this boundary.
abstract interface class AvatarMotionDriver {
  AvatarMotionDriverCapabilities get capabilities;
  Stream<AvatarMotionDriverEvent> get events;

  Future<void> initialize();

  Future<void> start(AvatarScheduledAction action);

  Future<void> retarget(AvatarScheduledAction action);

  Future<void> stop(
    String runtimeId, {
    required Duration blendOut,
  });

  Future<void> dispose();
}
