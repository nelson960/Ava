import 'dart:math' as math;

import 'avatar_action_intent.dart';
import 'avatar_motion_scheduler.dart';
import 'avatar_procedural_motion.dart';
import 'avatar_rig_bindings.dart';

class AvatarRendererPerformancePlan {
  const AvatarRendererPerformancePlan({
    required this.actions,
    required this.skeletalMotion,
    required this.morphMotion,
  });

  final List<AvatarScheduledAction> actions;
  final AvatarProceduralMotion? skeletalMotion;
  final AvatarMorphMotion? morphMotion;

  Set<String> get runtimeIds =>
      actions.map((action) => action.runtimeId).toSet();

  Set<AvatarRigBone> get controlledBones =>
      skeletalMotion?.frames.first.bones.keys.toSet() ?? const {};
}

/// Renderer-owned graph for every active skeletal and facial performance.
///
/// The graph rebases active trajectories whenever membership changes and
/// resolves all bone/morph writers into one deterministic stream. Filament
/// therefore never receives two competing final writers for the same bone or
/// morph target.
class AvatarRendererPerformanceGraph {
  final Map<String, _ActiveRendererPerformance> _active = {};
  Duration? _lastTimelineAt;
  int _nextSequence = 0;

  int get activeCount => _active.length;
  Set<String> get activeRuntimeIds => Set.unmodifiable(_active.keys.toSet());

  bool containsRuntime(String runtimeId) => _active.containsKey(runtimeId);

  AvatarRendererPerformancePlan start({
    required AvatarScheduledAction action,
    required AvatarLayerPerformance performance,
    required Duration timelineAt,
  }) {
    if (_active.containsKey(action.runtimeId)) {
      throw StateError(
        'Renderer performance ${action.runtimeId} is already active.',
      );
    }
    _advanceExisting(timelineAt);
    _active[action.runtimeId] = _ActiveRendererPerformance(
      action: action,
      performance: performance,
      startedAt: timelineAt,
      sequence: ++_nextSequence,
    );
    return _buildPlan();
  }

  AvatarRendererPerformancePlan retarget({
    required AvatarScheduledAction action,
    required AvatarLayerPerformance performance,
    required Duration timelineAt,
  }) {
    final previous = _active[action.runtimeId];
    if (previous == null) {
      throw StateError(
        'Cannot retarget unknown renderer performance ${action.runtimeId}.',
      );
    }
    if (action.revision <= previous.action.revision) {
      throw StateError(
        'Renderer performance ${action.runtimeId} received stale revision '
        '${action.revision} after ${previous.action.revision}.',
      );
    }
    _validateTimeline(timelineAt);
    final elapsed = timelineAt - previous.startedAt;
    final rebasedPrevious = _continuePerformance(
      previous.performance,
      elapsed,
    );
    _advanceExisting(timelineAt, exceptRuntimeId: action.runtimeId);
    _active[action.runtimeId] = _ActiveRendererPerformance(
      action: action,
      performance: rebasedPrevious == null
          ? performance
          : _retargetPerformance(rebasedPrevious, performance),
      startedAt: timelineAt,
      sequence: previous.sequence,
    );
    _lastTimelineAt = timelineAt;
    return _buildPlan();
  }

  /// Applies a scheduler-approved retarget at a segment boundary.
  ///
  /// A finite native trajectory can reach its neutral final frame just before
  /// the driver's completion event traverses the serialized command queue. In
  /// that narrow interval the scheduler still owns the runtime, but this graph
  /// has correctly retired its sampled frames. Continue the same scheduler
  /// runtime as a fresh native trajectory instead of treating it as an unknown
  /// action. Revision validation remains the driver's responsibility.
  AvatarRendererPerformancePlan reconcileRetarget({
    required AvatarScheduledAction action,
    required AvatarLayerPerformance performance,
    required Duration timelineAt,
  }) {
    if (!_active.containsKey(action.runtimeId)) {
      return start(
        action: action,
        performance: performance,
        timelineAt: timelineAt,
      );
    }
    return retarget(
      action: action,
      performance: performance,
      timelineAt: timelineAt,
    );
  }

  AvatarRendererPerformancePlan remove({
    required String runtimeId,
    required Duration timelineAt,
    required Duration blendOut,
  }) {
    if (!_active.containsKey(runtimeId)) {
      throw StateError(
          'Cannot remove unknown renderer performance $runtimeId.');
    }
    if (blendOut.isNegative) {
      throw ArgumentError.value(blendOut, 'blendOut', 'must not be negative');
    }
    _advanceExisting(timelineAt);
    final outgoing = _active.remove(runtimeId);
    if (outgoing == null || blendOut == Duration.zero) return _buildPlan();
    final fade = _fadeOutPerformance(outgoing.performance, blendOut);
    return _buildPlan(
      transients: fade == null
          ? const []
          : [
              _ActiveRendererPerformance(
                action: outgoing.action,
                performance: fade,
                startedAt: timelineAt,
                sequence: outgoing.sequence,
              ),
            ],
    );
  }

  AvatarRendererPerformancePlan advance(Duration timelineAt) {
    _advanceExisting(timelineAt);
    return _buildPlan();
  }

  AvatarRendererPerformanceGraph copy() {
    final copy = AvatarRendererPerformanceGraph();
    copy._active.addAll(_active);
    copy._lastTimelineAt = _lastTimelineAt;
    copy._nextSequence = _nextSequence;
    return copy;
  }

  void reset() {
    _active.clear();
    _lastTimelineAt = null;
    _nextSequence = 0;
  }

  void _advanceExisting(
    Duration timelineAt, {
    String? exceptRuntimeId,
  }) {
    _validateTimeline(timelineAt);
    final rebuilt = <String, _ActiveRendererPerformance>{};
    for (final entry in _active.entries) {
      if (entry.key == exceptRuntimeId) {
        rebuilt[entry.key] = entry.value;
        continue;
      }
      final continued = _continuePerformance(
        entry.value.performance,
        timelineAt - entry.value.startedAt,
      );
      if (continued == null) continue;
      rebuilt[entry.key] = _ActiveRendererPerformance(
        action: entry.value.action,
        performance: continued,
        startedAt: timelineAt,
        sequence: entry.value.sequence,
      );
    }
    _active
      ..clear()
      ..addAll(rebuilt);
    _lastTimelineAt = timelineAt;
  }

  void _validateTimeline(Duration timelineAt) {
    if (timelineAt.isNegative) {
      throw ArgumentError.value(
        timelineAt,
        'timelineAt',
        'must not be negative',
      );
    }
    final previous = _lastTimelineAt;
    if (previous != null && timelineAt < previous) {
      throw StateError(
        'Renderer performance timestamps must be monotonic; received '
        '$timelineAt after $previous.',
      );
    }
  }

  AvatarRendererPerformancePlan _buildPlan({
    List<_ActiveRendererPerformance> transients = const [],
  }) {
    final prioritized = <_ActiveRendererPerformance>[
      ..._active.values,
      ...transients,
    ]..sort((left, right) {
        final byPriority = right.action.intent.priority.value.compareTo(
          left.action.intent.priority.value,
        );
        if (byPriority != 0) return byPriority;
        return right.sequence.compareTo(left.sequence);
      });
    return AvatarRendererPerformancePlan(
      actions: List.unmodifiable([
        for (final active in prioritized) active.action,
      ]),
      skeletalMotion: _composeSkeletal(prioritized),
      morphMotion: _composeMorphs(prioritized),
    );
  }
}

class _ActiveRendererPerformance {
  const _ActiveRendererPerformance({
    required this.action,
    required this.performance,
    required this.startedAt,
    required this.sequence,
  });

  final AvatarScheduledAction action;
  final AvatarLayerPerformance performance;
  final Duration startedAt;
  final int sequence;
}

AvatarLayerPerformance? _continuePerformance(
  AvatarLayerPerformance source,
  Duration elapsed,
) {
  final skeletal = source.skeletalMotion == null
      ? null
      : _continueSkeletal(source.skeletalMotion!, elapsed);
  final morph = source.morphMotion == null
      ? null
      : _continueMorph(source.morphMotion!, elapsed);
  if (skeletal == null && morph == null) return null;
  return AvatarLayerPerformance(
    skeletalMotion: skeletal,
    morphMotion: morph,
  );
}

AvatarLayerPerformance _retargetPerformance(
  AvatarLayerPerformance previous,
  AvatarLayerPerformance next,
) {
  AvatarProceduralMotion? skeletal = next.skeletalMotion;
  AvatarMorphMotion? morph = next.morphMotion;
  if (previous.skeletalMotion != null && next.skeletalMotion != null) {
    skeletal = _retargetSkeletal(
      previous.skeletalMotion!,
      next.skeletalMotion!,
    );
    if (previous.morphMotion != null && next.morphMotion != null) {
      morph = _retargetMorph(previous.morphMotion!, next.morphMotion!);
    }
  } else if (previous.morphMotion != null && next.morphMotion != null) {
    morph = _retargetMorph(previous.morphMotion!, next.morphMotion!);
  }
  return AvatarLayerPerformance(
    skeletalMotion: skeletal,
    morphMotion: morph,
  );
}

AvatarLayerPerformance? _fadeOutPerformance(
  AvatarLayerPerformance source,
  Duration duration,
) {
  final skeletal = source.skeletalMotion == null
      ? null
      : _fadeOutSkeletal(source.skeletalMotion!, duration);
  final morph = source.morphMotion == null
      ? null
      : _fadeOutMorph(source.morphMotion!, duration);
  if (skeletal == null && morph == null) return null;
  return AvatarLayerPerformance(
    skeletalMotion: skeletal,
    morphMotion: morph,
  );
}

AvatarProceduralMotion _fadeOutSkeletal(
  AvatarProceduralMotion source,
  Duration duration,
) {
  _validateSkeletal(source);
  final frameLength = math.min(source.frameLengthMilliseconds, 1000 / 60);
  final frameCount = math.max(
    2,
    (duration.inMicroseconds /
                Duration.microsecondsPerMillisecond /
                frameLength)
            .ceil() +
        1,
  );
  final current = source.frames.first.bones;
  return AvatarProceduralMotion(
    frameLengthMilliseconds: frameLength,
    frames: List.unmodifiable([
      for (var frame = 0; frame < frameCount; frame += 1)
        AvatarProceduralPoseFrame(
          Map.unmodifiable({
            for (final entry in current.entries)
              entry.key: AvatarBonePoseDelta.interpolate(
                entry.value,
                const AvatarBonePoseDelta(),
                _smootherStep(frame / (frameCount - 1)),
              ),
          }),
        ),
    ]),
    maximumBlend: source.maximumBlend,
  );
}

AvatarMorphMotion _fadeOutMorph(AvatarMorphMotion source, Duration duration) {
  _validateMorph(source);
  final frameLength = math.min(source.frameLengthMilliseconds, 1000 / 60);
  final frameCount = math.max(
    2,
    (duration.inMicroseconds /
                Duration.microsecondsPerMillisecond /
                frameLength)
            .ceil() +
        1,
  );
  final current = source.frames.first;
  return AvatarMorphMotion(
    frameLengthMilliseconds: frameLength,
    targetNames: source.targetNames,
    frames: List.unmodifiable([
      for (var frame = 0; frame < frameCount; frame += 1)
        List<double>.unmodifiable([
          for (final value in current)
            value * (1 - _smootherStep(frame / (frameCount - 1))),
        ]),
    ]),
  );
}

AvatarProceduralMotion _retargetSkeletal(
  AvatarProceduralMotion previous,
  AvatarProceduralMotion next,
) {
  _validateSkeletal(previous);
  _validateSkeletal(next);
  final previousNow = previous.frames.first.bones;
  final carryFrames = math.max(
    1,
    (120 / next.frameLengthMilliseconds).ceil(),
  );
  final bones = <AvatarRigBone>{
    ...previousNow.keys,
    ...next.frames.first.bones.keys,
  };
  return AvatarProceduralMotion(
    frameLengthMilliseconds: next.frameLengthMilliseconds,
    frames: List.unmodifiable([
      for (var frame = 0; frame < next.frames.length; frame += 1)
        AvatarProceduralPoseFrame(
          Map.unmodifiable({
            for (final bone in bones)
              bone: _addBoneDelta(
                next.frames[frame].bones[bone] ?? const AvatarBonePoseDelta(),
                _scaleBoneDelta(
                  previousNow[bone] ?? const AvatarBonePoseDelta(),
                  1 -
                      _smootherStep(
                        (frame / carryFrames).clamp(0.0, 1.0),
                      ),
                ),
              ),
          }),
        ),
    ]),
    maximumBlend: math.max(previous.maximumBlend, next.maximumBlend),
    loop: next.loop,
  );
}

AvatarProceduralMotion? _composeSkeletal(
  List<_ActiveRendererPerformance> active,
) {
  final sources = active
      .where((entry) => entry.performance.skeletalMotion != null)
      .toList(growable: false);
  if (sources.isEmpty) return null;
  final frameLength = math.max(
    1000 / 120,
    sources
        .map((entry) =>
            entry.performance.skeletalMotion!.frameLengthMilliseconds)
        .reduce(math.min),
  );
  final finite = sources
      .where((entry) => !entry.performance.skeletalMotion!.loop)
      .toList(growable: false);
  final durationMilliseconds = (finite.isEmpty ? sources : finite)
      .map((entry) => _skeletalDuration(entry.performance.skeletalMotion!))
      .reduce(math.max);
  final frameCount = math.max(
    2,
    (durationMilliseconds / frameLength).ceil() + 1,
  );
  final bones = <AvatarRigBone>{
    for (final entry in sources)
      ...entry.performance.skeletalMotion!.frames.first.bones.keys,
  };
  final frames = <AvatarProceduralPoseFrame>[];
  for (var frame = 0; frame < frameCount; frame += 1) {
    final atMilliseconds = math.min(
      frame * frameLength,
      durationMilliseconds,
    );
    final at = Duration(
      microseconds: (atMilliseconds * 1000).round(),
    );
    final pose = <AvatarRigBone, AvatarBonePoseDelta>{};
    for (final bone in bones) {
      final controlling = sources
          .where(
            (entry) => entry.performance.skeletalMotion!.frames.first.bones
                .containsKey(bone),
          )
          .toList(growable: false);
      final overriding = controlling.where(
        (entry) =>
            _blendModeForBone(entry.action.intent, bone) ==
            AvatarMotionBlendMode.override,
      );
      var value = overriding.isEmpty
          ? const AvatarBonePoseDelta()
          : _sampleSkeletal(
              overriding.first.performance.skeletalMotion!,
              at,
            )[bone]!;
      for (final additive in controlling.where(
        (entry) =>
            _blendModeForBone(entry.action.intent, bone) ==
            AvatarMotionBlendMode.additive,
      )) {
        value = _addBoneDelta(
          value,
          _sampleSkeletal(additive.performance.skeletalMotion!, at)[bone]!,
        );
      }
      pose[bone] = value;
    }
    frames.add(AvatarProceduralPoseFrame(Map.unmodifiable(pose)));
  }
  return AvatarProceduralMotion(
    frameLengthMilliseconds: frameLength,
    frames: List.unmodifiable(frames),
    maximumBlend: sources
        .map((entry) => entry.performance.skeletalMotion!.maximumBlend)
        .reduce(math.max)
        .clamp(0.0, 1.0),
    loop: finite.isEmpty,
  );
}

AvatarMorphMotion? _composeMorphs(
  List<_ActiveRendererPerformance> active,
) {
  final sources = active
      .where((entry) => entry.performance.morphMotion != null)
      .toList(growable: false);
  if (sources.isEmpty) return null;
  final frameLength = math.max(
    1000 / 120,
    sources
        .map((entry) => entry.performance.morphMotion!.frameLengthMilliseconds)
        .reduce(math.min),
  );
  final durationMilliseconds = sources
      .map((entry) => _morphDuration(entry.performance.morphMotion!))
      .reduce(math.max);
  final frameCount = math.max(
    2,
    (durationMilliseconds / frameLength).ceil() + 1,
  );
  final targets = <String>[];
  for (final entry in sources) {
    for (final target in entry.performance.morphMotion!.targetNames) {
      if (!targets.contains(target)) targets.add(target);
    }
  }
  final frames = <List<double>>[];
  for (var frame = 0; frame < frameCount; frame += 1) {
    final at = Duration(
      microseconds:
          (math.min(frame * frameLength, durationMilliseconds) * 1000).round(),
    );
    frames.add(List.unmodifiable([
      for (final target in targets) _composeMorphTarget(sources, target, at),
    ]));
  }
  return AvatarMorphMotion(
    frameLengthMilliseconds: frameLength,
    targetNames: List.unmodifiable(targets),
    frames: List.unmodifiable(frames),
  );
}

double _composeMorphTarget(
  List<_ActiveRendererPerformance> sources,
  String target,
  Duration at,
) {
  final controlling = sources.where(
    (entry) => entry.performance.morphMotion!.targetNames.contains(target),
  );
  final overriding = controlling.where(
    (entry) =>
        _blendModeForMorph(entry.action.intent, target) ==
        AvatarMotionBlendMode.override,
  );
  var value = overriding.isEmpty
      ? 0.0
      : _sampleMorph(
          overriding.first.performance.morphMotion!,
          at,
        )[overriding.first.performance.morphMotion!.targetNames
          .indexOf(target)];
  for (final additive in controlling.where(
    (entry) =>
        _blendModeForMorph(entry.action.intent, target) ==
        AvatarMotionBlendMode.additive,
  )) {
    final motion = additive.performance.morphMotion!;
    value += _sampleMorph(motion, at)[motion.targetNames.indexOf(target)];
  }
  return value.clamp(0.0, 1.0);
}

AvatarMotionBlendMode _blendModeForBone(
  AvatarActionIntent intent,
  AvatarRigBone bone,
) {
  final relevantChannels = _channelsForBone(bone);
  for (final channel in relevantChannels) {
    if (intent.channelClaims[channel] == AvatarMotionBlendMode.override) {
      return AvatarMotionBlendMode.override;
    }
  }
  for (final channel in relevantChannels) {
    if (intent.channelClaims[channel] == AvatarMotionBlendMode.additive) {
      return AvatarMotionBlendMode.additive;
    }
  }
  // Solvers may include small counter-motion outside their primary claim.
  // Such movement is always secondary and can never seize bone ownership.
  return AvatarMotionBlendMode.additive;
}

AvatarMotionBlendMode _blendModeForMorph(
  AvatarActionIntent intent,
  String target,
) {
  final channel = _visemeTargets.contains(target)
      ? AvatarMotionChannel.lipSync
      : AvatarMotionChannel.faceExpression;
  return intent.channelClaims[channel] ?? AvatarMotionBlendMode.additive;
}

const _visemeTargets = {
  'Fcl_MTH_A',
  'Fcl_MTH_I',
  'Fcl_MTH_U',
  'Fcl_MTH_E',
  'Fcl_MTH_O',
  'Fcl_MTH_Close',
};

Set<AvatarMotionChannel> _channelsForBone(AvatarRigBone bone) => switch (bone) {
      AvatarRigBone.hips => const {
          AvatarMotionChannel.basePose,
          AvatarMotionChannel.posture,
        },
      AvatarRigBone.spine ||
      AvatarRigBone.chest ||
      AvatarRigBone.upperChest =>
        const {
          AvatarMotionChannel.basePose,
          AvatarMotionChannel.posture,
          AvatarMotionChannel.spine,
        },
      AvatarRigBone.neck => const {
          AvatarMotionChannel.neck,
          AvatarMotionChannel.gaze,
          AvatarMotionChannel.head,
          AvatarMotionChannel.posture,
        },
      AvatarRigBone.head => const {
          AvatarMotionChannel.head,
          AvatarMotionChannel.gaze,
          AvatarMotionChannel.posture,
        },
      AvatarRigBone.leftEye || AvatarRigBone.rightEye => const {
          AvatarMotionChannel.gaze,
        },
      AvatarRigBone.leftShoulder ||
      AvatarRigBone.leftUpperArm ||
      AvatarRigBone.leftLowerArm =>
        const {AvatarMotionChannel.leftArm},
      AvatarRigBone.leftHand => const {
          AvatarMotionChannel.leftHand,
          AvatarMotionChannel.leftArm,
        },
      AvatarRigBone.leftThumb1 ||
      AvatarRigBone.leftThumb2 ||
      AvatarRigBone.leftThumb3 ||
      AvatarRigBone.leftIndex1 ||
      AvatarRigBone.leftIndex2 ||
      AvatarRigBone.leftIndex3 ||
      AvatarRigBone.leftMiddle1 ||
      AvatarRigBone.leftMiddle2 ||
      AvatarRigBone.leftMiddle3 ||
      AvatarRigBone.leftRing1 ||
      AvatarRigBone.leftRing2 ||
      AvatarRigBone.leftRing3 ||
      AvatarRigBone.leftLittle1 ||
      AvatarRigBone.leftLittle2 ||
      AvatarRigBone.leftLittle3 =>
        const {
          AvatarMotionChannel.leftFingers,
          AvatarMotionChannel.leftHand,
        },
      AvatarRigBone.rightShoulder ||
      AvatarRigBone.rightUpperArm ||
      AvatarRigBone.rightLowerArm =>
        const {AvatarMotionChannel.rightArm},
      AvatarRigBone.rightHand => const {
          AvatarMotionChannel.rightHand,
          AvatarMotionChannel.rightArm,
        },
      AvatarRigBone.rightThumb1 ||
      AvatarRigBone.rightThumb2 ||
      AvatarRigBone.rightThumb3 ||
      AvatarRigBone.rightIndex1 ||
      AvatarRigBone.rightIndex2 ||
      AvatarRigBone.rightIndex3 ||
      AvatarRigBone.rightMiddle1 ||
      AvatarRigBone.rightMiddle2 ||
      AvatarRigBone.rightMiddle3 ||
      AvatarRigBone.rightRing1 ||
      AvatarRigBone.rightRing2 ||
      AvatarRigBone.rightRing3 ||
      AvatarRigBone.rightLittle1 ||
      AvatarRigBone.rightLittle2 ||
      AvatarRigBone.rightLittle3 =>
        const {
          AvatarMotionChannel.rightFingers,
          AvatarMotionChannel.rightHand,
        },
      AvatarRigBone.leftUpperLeg || AvatarRigBone.leftLowerLeg => const {
          AvatarMotionChannel.leftLeg,
          AvatarMotionChannel.posture,
        },
      AvatarRigBone.leftFoot || AvatarRigBone.leftToe => const {
          AvatarMotionChannel.leftFoot,
          AvatarMotionChannel.leftLeg,
          AvatarMotionChannel.posture,
        },
      AvatarRigBone.rightUpperLeg || AvatarRigBone.rightLowerLeg => const {
          AvatarMotionChannel.rightLeg,
          AvatarMotionChannel.posture,
        },
      AvatarRigBone.rightFoot || AvatarRigBone.rightToe => const {
          AvatarMotionChannel.rightFoot,
          AvatarMotionChannel.rightLeg,
          AvatarMotionChannel.posture,
        },
    };

AvatarProceduralMotion? _continueSkeletal(
  AvatarProceduralMotion source,
  Duration elapsed,
) {
  _validateSkeletal(source);
  if (elapsed.isNegative) {
    throw ArgumentError.value(elapsed, 'elapsed', 'must not be negative');
  }
  final duration = _skeletalDuration(source);
  final elapsedMilliseconds =
      elapsed.inMicroseconds / Duration.microsecondsPerMillisecond;
  if (!source.loop && elapsedMilliseconds >= duration) return null;
  final startAt = source.loop && duration > 0
      ? elapsedMilliseconds % duration
      : elapsedMilliseconds;
  final outputDuration = source.loop ? duration : duration - startAt;
  final frameCount = math.max(
    2,
    (outputDuration / source.frameLengthMilliseconds).ceil() + 1,
  );
  final frames = <AvatarProceduralPoseFrame>[];
  for (var frame = 0; frame < frameCount; frame += 1) {
    final offset = math.min(
      frame * source.frameLengthMilliseconds,
      outputDuration,
    );
    final sourceAt =
        source.loop ? (startAt + offset) % duration : startAt + offset;
    final sampled = _sampleSkeletal(
      source,
      Duration(microseconds: (sourceAt * 1000).round()),
    );
    frames.add(
      AvatarProceduralPoseFrame(
        Map.unmodifiable({
          for (final bone in sampled.keys) bone: sampled[bone]!,
        }),
      ),
    );
  }
  return AvatarProceduralMotion(
    frameLengthMilliseconds: source.frameLengthMilliseconds,
    frames: List.unmodifiable(frames),
    maximumBlend: source.maximumBlend,
    loop: source.loop,
  );
}

AvatarMorphMotion? _continueMorph(AvatarMorphMotion source, Duration elapsed) {
  _validateMorph(source);
  final duration = _morphDuration(source);
  final elapsedMilliseconds =
      elapsed.inMicroseconds / Duration.microsecondsPerMillisecond;
  if (elapsedMilliseconds >= duration) return null;
  final frameCount = math.max(
    2,
    ((duration - elapsedMilliseconds) / source.frameLengthMilliseconds).ceil() +
        1,
  );
  return AvatarMorphMotion(
    frameLengthMilliseconds: source.frameLengthMilliseconds,
    targetNames: source.targetNames,
    frames: List.unmodifiable([
      for (var frame = 0; frame < frameCount; frame += 1)
        List<double>.unmodifiable(
          _sampleMorph(
            source,
            Duration(
              microseconds: (math.min(
                        elapsedMilliseconds +
                            frame * source.frameLengthMilliseconds,
                        duration,
                      ) *
                      1000)
                  .round(),
            ),
          ),
        ),
    ]),
  );
}

AvatarMorphMotion _retargetMorph(
  AvatarMorphMotion previous,
  AvatarMorphMotion next,
) {
  _validateMorph(previous);
  _validateMorph(next);
  final targets = <String>[
    ...next.targetNames,
    ...previous.targetNames.where((name) => !next.targetNames.contains(name)),
  ];
  final previousNow = _sampleMorph(previous, Duration.zero);
  final carryFrames = math.max(
    1,
    (120 / next.frameLengthMilliseconds).ceil(),
  );
  return AvatarMorphMotion(
    frameLengthMilliseconds: next.frameLengthMilliseconds,
    targetNames: List.unmodifiable(targets),
    frames: List.unmodifiable([
      for (var frame = 0; frame < next.frames.length; frame += 1)
        List<double>.unmodifiable([
          for (final target in targets)
            (_morphValue(next, target, frame) +
                    _morphValueAt(previous, previousNow, target) *
                        (1 - _smootherStep((frame / carryFrames).clamp(0, 1))))
                .clamp(0.0, 1.0)
                .toDouble(),
        ]),
    ]),
  );
}

double _morphValue(AvatarMorphMotion motion, String target, int frame) {
  final index = motion.targetNames.indexOf(target);
  if (index < 0) return 0;
  return motion.frames[frame][index];
}

double _morphValueAt(
  AvatarMorphMotion motion,
  List<double> values,
  String target,
) {
  final index = motion.targetNames.indexOf(target);
  return index < 0 ? 0 : values[index];
}

Map<AvatarRigBone, AvatarBonePoseDelta> _sampleSkeletal(
  AvatarProceduralMotion motion,
  Duration at,
) {
  final lastIndex = motion.frames.length - 1;
  final duration = _skeletalDuration(motion);
  var atMilliseconds = at.inMicroseconds / Duration.microsecondsPerMillisecond;
  if (motion.loop && duration > 0) atMilliseconds %= duration;
  final rawFrame = atMilliseconds / motion.frameLengthMilliseconds;
  final bounded = rawFrame.clamp(0.0, lastIndex.toDouble());
  final clamped =
      (lastIndex - bounded).abs() < 1e-4 ? lastIndex.toDouble() : bounded;
  final left = clamped.floor();
  final right = math.min(lastIndex, left + 1);
  final mix = clamped - left;
  return Map.unmodifiable({
    for (final bone in motion.frames.first.bones.keys)
      bone: AvatarBonePoseDelta.interpolate(
        motion.frames[left].bones[bone]!,
        motion.frames[right].bones[bone]!,
        mix,
      ),
  });
}

List<double> _sampleMorph(AvatarMorphMotion motion, Duration at) {
  final lastIndex = motion.frames.length - 1;
  final rawFrame = at.inMicroseconds /
      Duration.microsecondsPerMillisecond /
      motion.frameLengthMilliseconds;
  final bounded = rawFrame.clamp(0.0, lastIndex.toDouble());
  final clamped =
      (lastIndex - bounded).abs() < 1e-4 ? lastIndex.toDouble() : bounded;
  final left = clamped.floor();
  final right = math.min(lastIndex, left + 1);
  final mix = clamped - left;
  return List.generate(
    motion.targetNames.length,
    (index) =>
        motion.frames[left][index] * (1 - mix) +
        motion.frames[right][index] * mix,
    growable: false,
  );
}

void _validateSkeletal(AvatarProceduralMotion motion) {
  if (motion.frames.length < 2 ||
      !motion.frameLengthMilliseconds.isFinite ||
      motion.frameLengthMilliseconds <= 0 ||
      !motion.maximumBlend.isFinite ||
      motion.maximumBlend < 0 ||
      motion.maximumBlend > 1) {
    throw ArgumentError('A skeletal layer must contain bounded timed frames.');
  }
  final bones = motion.frames.first.bones.keys.toSet();
  if (bones.isEmpty ||
      motion.frames.any(
        (frame) =>
            frame.bones.length != bones.length ||
            !frame.bones.keys.toSet().containsAll(bones),
      )) {
    throw ArgumentError('A skeletal layer must keep a stable bone set.');
  }
}

void _validateMorph(AvatarMorphMotion motion) {
  if (motion.frames.length < 2) {
    throw ArgumentError('A morph layer must contain at least two frames.');
  }
  if (motion.targetNames.isEmpty ||
      motion.targetNames.toSet().length != motion.targetNames.length) {
    throw ArgumentError('A morph layer must contain unique target names.');
  }
  if (!motion.frameLengthMilliseconds.isFinite ||
      motion.frameLengthMilliseconds <= 0) {
    throw ArgumentError('A morph layer must have a positive frame duration.');
  }
  for (var frameIndex = 0; frameIndex < motion.frames.length; frameIndex += 1) {
    final frame = motion.frames[frameIndex];
    if (frame.length != motion.targetNames.length) {
      throw ArgumentError(
        'Morph frame $frameIndex has ${frame.length} values for '
        '${motion.targetNames.length} targets.',
      );
    }
    for (var valueIndex = 0; valueIndex < frame.length; valueIndex += 1) {
      final value = frame[valueIndex];
      if (!value.isFinite || value < 0 || value > 1) {
        throw ArgumentError(
          'Morph frame $frameIndex target '
          '${motion.targetNames[valueIndex]} has invalid weight $value.',
        );
      }
    }
  }
}

double _skeletalDuration(AvatarProceduralMotion motion) =>
    (motion.frames.length - 1) * motion.frameLengthMilliseconds;

double _morphDuration(AvatarMorphMotion motion) =>
    (motion.frames.length - 1) * motion.frameLengthMilliseconds;

AvatarBonePoseDelta _addBoneDelta(
  AvatarBonePoseDelta left,
  AvatarBonePoseDelta right,
) =>
    AvatarBonePoseDelta(
      rotationX: left.rotationX + right.rotationX,
      rotationY: left.rotationY + right.rotationY,
      rotationZ: left.rotationZ + right.rotationZ,
      translationX: left.translationX + right.translationX,
      translationY: left.translationY + right.translationY,
      translationZ: left.translationZ + right.translationZ,
    );

AvatarBonePoseDelta _scaleBoneDelta(
  AvatarBonePoseDelta value,
  double scale,
) =>
    AvatarBonePoseDelta(
      rotationX: value.rotationX * scale,
      rotationY: value.rotationY * scale,
      rotationZ: value.rotationZ * scale,
      translationX: value.translationX * scale,
      translationY: value.translationY * scale,
      translationZ: value.translationZ * scale,
    );

double _smootherStep(num value) {
  final x = value.clamp(0.0, 1.0);
  return x * x * x * (x * (x * 6 - 15) + 10);
}
