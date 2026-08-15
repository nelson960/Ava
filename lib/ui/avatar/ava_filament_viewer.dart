import 'dart:async';
import 'dart:math' as math;
import 'dart:ui' show PointerDeviceKind;

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:thermion_flutter/thermion_flutter.dart' as thermion;

import 'ava_3d_viewer.dart';
import 'ava_filament_motion_driver.dart';
import 'avatar_anatomy.dart';
import 'ava_filament_surface.dart';
import 'avatar_action_intent.dart';
import 'avatar_living_idle_solver.dart';
import 'avatar_motion_driver.dart';
import 'avatar_procedural_motion.dart';
import 'avatar_renderer_performance_graph.dart';
import 'avatar_rig_bindings.dart';
import 'avatar_motion_scheduler.dart';
import 'avatar_semantic_motion_solver.dart';
import 'avatar_viewer_controller.dart';

typedef AvaFilamentCameraOrbit = ({
  double theta,
  double phi,
  double radius,
});

class AvaFilamentModelProfile {
  const AvaFilamentModelProfile({
    required this.animationNames,
    required this.boneNames,
    required this.morphTargetNames,
  });

  final Set<String> animationNames;
  final Set<String> boneNames;
  final Set<String> morphTargetNames;
}

class AvaFilamentPointerEvent {
  const AvaFilamentPointerEvent({
    required this.position,
    required this.viewportSize,
    required this.modelPosition,
    required this.modelNormal,
    required this.anatomy,
    required this.contactDuration,
    required this.peakPressure,
    required this.pointerType,
    required this.cameraAzimuth,
    required this.cameraElevation,
    required this.cameraRadius,
    required this.avatarRootYaw,
  });

  final Offset position;
  final Size viewportSize;
  final AvatarModelVector3 modelPosition;
  final AvatarModelVector3 modelNormal;
  final AvatarAnatomicalContact anatomy;
  final Duration contactDuration;
  final double peakPressure;
  final String pointerType;
  final double cameraAzimuth;
  final double cameraElevation;
  final double cameraRadius;
  final double avatarRootYaw;
}

/// Native renderer controller for Ava's layered touch performance graph.
class AvaFilamentController
    implements AvaAvatarViewerController, AvaRendererMotionLayerSink {
  static const _livingIdleRuntimeId = 'ava_internal_living_idle';
  static const _maximumConcurrentActions = 16;
  static const _cameraRecoveryDuration = Duration(milliseconds: 680);
  static const _cameraRecoveryFrameInterval = Duration(milliseconds: 8);
  static const _requiredProductionMorphTargets = {
    'Fcl_ALL_Neutral',
    'Fcl_EYE_Close',
    'Fcl_EYE_Close_L',
    'Fcl_EYE_Close_R',
    'Fcl_EYE_Joy',
    'Fcl_EYE_Surprised',
    'Fcl_BRW_Angry',
    'Fcl_BRW_Fun',
    'Fcl_BRW_Sorrow',
    'Fcl_BRW_Surprised',
    'Fcl_MTH_A',
    'Fcl_MTH_I',
    'Fcl_MTH_U',
    'Fcl_MTH_E',
    'Fcl_MTH_O',
    'Fcl_MTH_Close',
    'Fcl_MTH_Fun',
    'Fcl_MTH_Joy',
    'Fcl_MTH_Neutral',
    'Fcl_MTH_Small',
    'Fcl_MTH_Sorrow',
    'Fcl_MTH_Surprised',
  };

  thermion.ThermionViewer? _viewer;
  thermion.ThermionAsset? _asset;
  thermion.ThermionAsset? _rigAsset;
  AvatarRigBindings? _rigBindings;
  Map<String, thermion.ThermionEntity> _boneEntities = const {};
  Map<String, int> _boneIndices = const {};
  Map<thermion.ThermionEntity, List<String>> _morphTargetsByEntity = const {};
  final Set<thermion.ThermionEntity> _activeMorphEntities = {};
  Map<String, Duration> _animationDurations = const {};
  ValueChanged<AvaAnimationFinishedEvent>? _onAnimationFinished;
  ValueChanged<String>? _onCommandError;
  thermion.Vector3 _cameraTarget = thermion.Vector3(0, 0.90, 0);
  AvaFilamentCameraOrbit? _frontCameraOrbit;
  AvaFilamentCameraOrbit? _currentCameraOrbit;
  bool _cameraUpdateInFlight = false;
  bool _cameraUpdatePending = false;
  Timer? _cameraRecoveryTimer;
  Stopwatch? _cameraRecoveryClock;
  Timer? _completionTimer;
  int _nextPlaybackId = 0;
  int? _activePlaybackId;
  int _attachmentGeneration = 0;
  Future<void> _boneCommandTail = Future<void>.value();
  late final AvatarProceduralMotion _livingIdleMotion =
      AvatarLivingIdleSolver().solve();
  final AvatarSemanticMotionSolver _semanticMotionSolver =
      const AvatarSemanticMotionSolver();
  AvatarRendererPerformanceGraph _performanceGraph =
      AvatarRendererPerformanceGraph();
  final Map<String, int> _scheduledLayerRevisions = {};
  final Map<String, _AvaRendererLayerFeatures> _scheduledLayerFeatures = {};
  Duration _motionTimelineAt = Duration.zero;
  bool _livingIdleEnabled = false;
  bool _disposed = false;

  @override
  bool get isMotionLayerReady =>
      !_disposed &&
      _viewer != null &&
      _asset != null &&
      _rigAsset != null &&
      _rigBindings != null;

  @override
  AvatarMotionDriverCapabilities get motionCapabilities {
    final attached = isMotionLayerReady;
    final availableMorphTargets = <String>{
      for (final names in _morphTargetsByEntity.values) ...names,
    };
    final morphContractSatisfied = attached &&
        availableMorphTargets.containsAll(_requiredProductionMorphTargets);
    return AvatarMotionDriverCapabilities(
      layeredSkeletalAnimation: attached,
      additiveAnimation: attached,
      proceduralBoneControl: attached,
      morphTargetControl: morphContractSatisfied,
      skinnedSurfacePicking: attached,
      cameraPoseReporting: attached,
      maximumConcurrentActions: _maximumConcurrentActions,
    );
  }

  void attach({
    required thermion.ThermionViewer viewer,
    required thermion.ThermionAsset asset,
    required thermion.ThermionAsset rigAsset,
    required AvatarRigBindings rigBindings,
    required Map<String, thermion.ThermionEntity> boneEntities,
    required Map<String, int> boneIndices,
    required Map<thermion.ThermionEntity, List<String>> morphTargetsByEntity,
    required Map<String, Duration> animationDurations,
    required ValueChanged<AvaAnimationFinishedEvent> onAnimationFinished,
    required ValueChanged<String> onCommandError,
  }) {
    _ensureNotDisposed();
    if (_viewer != null || _asset != null) {
      throw StateError('Ava Filament controller is already attached.');
    }
    _viewer = viewer;
    _asset = asset;
    _rigAsset = rigAsset;
    _rigBindings = rigBindings;
    _boneEntities = Map.unmodifiable(boneEntities);
    _boneIndices = Map.unmodifiable(boneIndices);
    _morphTargetsByEntity =
        Map<thermion.ThermionEntity, List<String>>.unmodifiable(
      <thermion.ThermionEntity, List<String>>{
        for (final entry in morphTargetsByEntity.entries)
          entry.key: List<String>.unmodifiable(entry.value),
      },
    );
    _animationDurations = Map.unmodifiable(animationDurations);
    _onAnimationFinished = onAnimationFinished;
    _onCommandError = onCommandError;
    _attachmentGeneration += 1;
  }

  void detach(thermion.ThermionViewer viewer) {
    if (_viewer != viewer) return;
    _cancelCameraRecovery();
    _completionTimer?.cancel();
    _completionTimer = null;
    _activePlaybackId = null;
    _viewer = null;
    _asset = null;
    _rigAsset = null;
    _rigBindings = null;
    _boneEntities = const {};
    _boneIndices = const {};
    _morphTargetsByEntity = const {};
    _activeMorphEntities.clear();
    _performanceGraph.reset();
    _scheduledLayerRevisions.clear();
    _scheduledLayerFeatures.clear();
    _motionTimelineAt = Duration.zero;
    _animationDurations = const {};
    _frontCameraOrbit = null;
    _currentCameraOrbit = null;
    _cameraUpdatePending = false;
    _livingIdleEnabled = false;
    _attachmentGeneration += 1;
  }

  /// Layers an angle-aware procedural balance response over the active idle.
  ///
  /// Commands are serialized so a rapid second touch snapshots and retargets
  /// from the pose produced by the first instead of racing native animation
  /// component mutations.
  Future<void> playLowerBodyAction(AvatarActionIntent intent) {
    return playProceduralTouchAction(intent);
  }

  Future<void> playProceduralTouchAction(AvatarActionIntent intent) {
    return _enqueueBoneCommand(() => _playProceduralTouchActionNow(intent));
  }

  Future<void> startLivingIdle() async {
    if (_livingIdleEnabled) {
      throw StateError('Ava living idle is already active.');
    }
    _livingIdleEnabled = true;
    try {
      await _enqueueBoneCommand(() async {
        final stagedGraph = _performanceGraph.copy();
        final action = _livingIdleAction(_motionTimelineAt);
        final plan = stagedGraph.start(
          action: action,
          performance: AvatarLayerPerformance(
            skeletalMotion: _livingIdleMotion,
          ),
          timelineAt: _motionTimelineAt,
        );
        await _installPerformancePlan(plan);
        _performanceGraph = stagedGraph;
      });
    } catch (_) {
      _livingIdleEnabled = false;
      rethrow;
    }
  }

  Future<void> clearProceduralMotion() {
    return _enqueueBoneCommand(() async {
      await _clearMorphMotions();
      _performanceGraph.reset();
      if (_livingIdleEnabled) {
        final action = _livingIdleAction(Duration.zero);
        final plan = _performanceGraph.start(
          action: action,
          performance: AvatarLayerPerformance(
            skeletalMotion: _livingIdleMotion,
          ),
          timelineAt: Duration.zero,
        );
        await _installPerformancePlan(plan);
      } else {
        await _resetBoneAnimationComponent();
      }
      _scheduledLayerRevisions.clear();
      _scheduledLayerFeatures.clear();
      _motionTimelineAt = Duration.zero;
    });
  }

  Future<T> _enqueueBoneCommand<T>(Future<T> Function() command) {
    _ensureReady();
    final generation = _attachmentGeneration;
    final completion = Completer<T>();
    _boneCommandTail = _boneCommandTail.then((_) async {
      try {
        if (_disposed || generation != _attachmentGeneration) {
          throw StateError(
            'Ava changed renderer state before the procedural response could play.',
          );
        }
        completion.complete(await command());
      } catch (error, stackTrace) {
        completion.completeError(error, stackTrace);
      }
    });
    return completion.future;
  }

  Future<void> _resetBoneAnimationComponent() async {
    final rigAsset = _rigAsset;
    if (rigAsset == null) {
      throw StateError('Ava procedural rig is not attached.');
    }
    final animationManager = thermion.FilamentApp.instance!.animationManager;
    final removed = await animationManager.removeBoneAnimationComponent(
      rigAsset,
    );
    if (!removed) {
      throw StateError(
        'Ava could not clear the previous procedural pose layer.',
      );
    }
    final added = await animationManager.addBoneAnimationComponent(rigAsset);
    if (!added) {
      throw StateError('Ava could not create a procedural pose layer.');
    }
  }

  Future<void> _playProceduralTouchActionNow(
    AvatarActionIntent intent,
  ) async {
    _ensureReady();
    final performance = _semanticMotionSolver.solve(intent);
    final stagedGraph = _performanceGraph.copy();
    final plan = stagedGraph.start(
      action: AvatarScheduledAction.start(intent, intent.occurredAt),
      performance: performance,
      timelineAt: _advanceMotionTimeline(intent.occurredAt),
    );
    await _installPerformancePlan(plan);
    _performanceGraph = stagedGraph;
  }

  @override
  Future<AvaRendererLayerReceipt> startMotionLayer(
    AvatarScheduledAction action,
  ) {
    return _enqueueBoneCommand(() async {
      if (_scheduledLayerRevisions.containsKey(action.runtimeId) ||
          _performanceGraph.containsRuntime(action.runtimeId)) {
        throw StateError(
          'Ava native layer ${action.runtimeId} is already active.',
        );
      }
      return _upsertMotionLayer(action, isRetarget: false);
    });
  }

  @override
  Future<AvaRendererLayerReceipt> retargetMotionLayer(
    AvatarScheduledAction action,
  ) {
    return _enqueueBoneCommand(() async {
      final currentRevision = _scheduledLayerRevisions[action.runtimeId];
      if (currentRevision == null) {
        throw StateError(
          'Cannot retarget unknown Ava native layer ${action.runtimeId}.',
        );
      }
      if (action.revision <= currentRevision) {
        throw StateError(
          'Ava native layer ${action.runtimeId} received stale revision '
          '${action.revision} after $currentRevision.',
        );
      }
      return _upsertMotionLayer(action, isRetarget: true);
    });
  }

  Future<AvaRendererLayerReceipt> _upsertMotionLayer(
    AvatarScheduledAction action, {
    required bool isRetarget,
  }) async {
    final performance = _semanticMotionSolver.solve(action.intent);
    final features = _AvaRendererLayerFeatures.fromPerformance(performance);
    final stagedGraph = _performanceGraph.copy();
    final timelineAt = _advanceMotionTimeline(action.updatedAt);
    final plan = isRetarget
        ? stagedGraph.reconcileRetarget(
            action: action,
            performance: performance,
            timelineAt: timelineAt,
          )
        : stagedGraph.start(
            action: action,
            performance: performance,
            timelineAt: timelineAt,
          );
    await _installPerformancePlan(
      plan,
      transitionDuration: isRetarget ? const Duration(milliseconds: 45) : null,
      updateSkeletal: features.skeletal,
      updateMorph: features.morph,
    );
    _performanceGraph = stagedGraph;
    _scheduledLayerRevisions[action.runtimeId] = action.revision;
    _scheduledLayerFeatures[action.runtimeId] = features;

    return AvaRendererLayerReceipt(
      runtimeId: action.runtimeId,
      revision: action.revision,
      completesAfter: performance.loops
          ? null
          : action.intent.blendIn +
              performance.duration +
              action.intent.blendOut,
    );
  }

  @override
  Future<void> stopMotionLayer(
    String runtimeId, {
    required Duration blendOut,
    required Duration occurredAt,
  }) {
    return _enqueueBoneCommand(
      () => _removeMotionLayer(
        runtimeId,
        occurredAt: occurredAt,
        transitionDuration: blendOut,
      ),
    );
  }

  @override
  Future<void> completeMotionLayer(
    String runtimeId, {
    required int revision,
    required Duration occurredAt,
  }) {
    return _enqueueBoneCommand(() async {
      final currentRevision = _scheduledLayerRevisions[runtimeId];
      if (currentRevision != revision) {
        throw StateError(
          'Cannot complete stale Ava native layer $runtimeId@$revision; '
          'current revision is $currentRevision.',
        );
      }
      await _removeMotionLayer(
        runtimeId,
        occurredAt: occurredAt,
        transitionDuration: const Duration(milliseconds: 90),
      );
    });
  }

  Future<void> _removeMotionLayer(
    String runtimeId, {
    required Duration occurredAt,
    required Duration transitionDuration,
  }) async {
    if (!_scheduledLayerRevisions.containsKey(runtimeId)) {
      throw StateError('Cannot remove unknown Ava native layer $runtimeId.');
    }
    final features = _scheduledLayerFeatures[runtimeId];
    if (features == null) {
      throw StateError('Ava native layer $runtimeId has no feature contract.');
    }
    if (!_performanceGraph.containsRuntime(runtimeId)) {
      // Another renderer update can observe that this finite motion has
      // reached its final frame before the driver's completion command reaches
      // the serialized native queue. Its runtime revision is still required
      // above, so this is completion coalescing rather than a silent fallback.
      _scheduledLayerRevisions.remove(runtimeId);
      _scheduledLayerFeatures.remove(runtimeId);
      return;
    }
    final stagedGraph = _performanceGraph.copy();
    final renderTime = _advanceMotionTimeline(occurredAt);
    final plan = stagedGraph.remove(
      runtimeId: runtimeId,
      timelineAt: renderTime,
      blendOut: transitionDuration,
    );
    await _installOrClearPerformancePlan(
      plan,
      transitionDuration: transitionDuration,
      updateSkeletal: features.skeletal,
      updateMorph: features.morph,
    );
    _performanceGraph = stagedGraph;
    if (transitionDuration > Duration.zero) {
      await Future<void>.delayed(transitionDuration);
      _ensureReady();
      final settledAt = _advanceMotionTimeline(
        renderTime + transitionDuration,
      );
      final settledPlan = stagedGraph.advance(settledAt);
      await _installOrClearPerformancePlan(
        settledPlan,
        updateSkeletal: features.skeletal,
        updateMorph: features.morph,
      );
      _performanceGraph = stagedGraph;
    }
    _scheduledLayerRevisions.remove(runtimeId);
    _scheduledLayerFeatures.remove(runtimeId);
  }

  Duration _advanceMotionTimeline(Duration candidate) {
    if (candidate.isNegative) {
      throw ArgumentError.value(candidate, 'candidate', 'must not be negative');
    }
    if (candidate > _motionTimelineAt) {
      _motionTimelineAt = candidate;
    }
    return _motionTimelineAt;
  }

  @override
  Future<void> clearMotionLayers() => clearProceduralMotion();

  Future<void> _installPerformancePlan(
    AvatarRendererPerformancePlan plan, {
    Duration? transitionDuration,
    bool updateSkeletal = true,
    bool updateMorph = true,
  }) async {
    if (plan.actions.isEmpty) {
      throw StateError('Ava renderer graph produced no active layers.');
    }
    final skeletalMotion = plan.skeletalMotion;
    if (updateSkeletal && skeletalMotion != null) {
      _validateMotion(skeletalMotion);
      await _resetBoneAnimationComponent();
      final capturedLocal = _captureLocalTransforms(plan.controlledBones);
      await _addSkeletalMotion(
        skeletalMotion,
        capturedLocal: capturedLocal,
        fadeIn: transitionDuration ?? const Duration(milliseconds: 45),
        fadeOut: transitionDuration ?? const Duration(milliseconds: 120),
      );
    }
    if (updateMorph) {
      await _clearMorphMotions();
      final morph = plan.morphMotion;
      if (morph != null) await _addMorphMotion(morph);
    }
  }

  Future<void> _installOrClearPerformancePlan(
    AvatarRendererPerformancePlan plan, {
    Duration? transitionDuration,
    required bool updateSkeletal,
    required bool updateMorph,
  }) async {
    if (updateSkeletal && plan.skeletalMotion == null) {
      await _resetBoneAnimationComponent();
    }
    if (updateMorph && plan.morphMotion == null) {
      await _clearMorphMotions();
    }
    if ((updateSkeletal && plan.skeletalMotion != null) ||
        (updateMorph && plan.morphMotion != null)) {
      await _installPerformancePlan(
        plan,
        transitionDuration: transitionDuration,
        updateSkeletal: updateSkeletal && plan.skeletalMotion != null,
        updateMorph: updateMorph && plan.morphMotion != null,
      );
    }
  }

  AvatarScheduledAction _livingIdleAction(Duration now) =>
      AvatarScheduledAction.start(
        AvatarActionIntent(
          id: _livingIdleRuntimeId,
          kind: AvatarActionKind.idle,
          source: AvatarActionSource.idleSystem,
          priority: AvatarActionPriority.idle,
          channelClaims: const {
            AvatarMotionChannel.spine: AvatarMotionBlendMode.additive,
            AvatarMotionChannel.neck: AvatarMotionBlendMode.additive,
            AvatarMotionChannel.head: AvatarMotionBlendMode.additive,
          },
          occurredAt: now,
          blendIn: const Duration(milliseconds: 650),
          blendOut: const Duration(milliseconds: 650),
          interruptionPolicy: AvatarInterruptionPolicy.interruptible,
          retargetPolicy: AvatarRetargetPolicy.never,
        ),
        now,
      );

  Map<AvatarRigBone, thermion.Matrix4> _captureLocalTransforms(
    Iterable<AvatarRigBone> roles,
  ) {
    final bindings = _rigBindings!;
    final transformManager = thermion.FilamentApp.instance!.transformManager;
    final capturedLocal = <AvatarRigBone, thermion.Matrix4>{};
    for (final role in roles) {
      final boneName = bindings.nameOf(role);
      final entity = _boneEntities[boneName];
      if (entity == null || !_boneIndices.containsKey(boneName)) {
        throw StateError(
          'Ava cannot animate ${role.name}; rig data for $boneName is missing.',
        );
      }
      capturedLocal[role] = transformManager.getLocalTransform(entity).clone();
    }
    return capturedLocal;
  }

  void _validateMotion(AvatarProceduralMotion motion) {
    if (motion.frames.isEmpty) {
      throw StateError('Ava procedural solver produced no pose frames.');
    }
    final roles = motion.frames.first.bones.keys.toList(growable: false);
    if (roles.isEmpty) {
      throw StateError('Ava procedural solver produced no controlled bones.');
    }
    for (final frame in motion.frames) {
      if (frame.bones.length != roles.length ||
          !frame.bones.keys.toSet().containsAll(roles)) {
        throw StateError(
          'Ava procedural solver changed its controlled bone set mid-motion.',
        );
      }
    }
  }

  Future<void> _addSkeletalMotion(
    AvatarProceduralMotion motion, {
    required Map<AvatarRigBone, thermion.Matrix4> capturedLocal,
    required Duration fadeIn,
    required Duration fadeOut,
  }) async {
    final rigAsset = _rigAsset!;
    final bindings = _rigBindings!;
    final animationManager = thermion.FilamentApp.instance!.animationManager;
    try {
      for (final role in motion.frames.first.bones.keys) {
        final boneName = bindings.nameOf(role);
        final frameData = <double>[];
        for (final frame in motion.frames) {
          final thermion.Matrix4 target =
              capturedLocal[role]! * _matrixForPoseDelta(frame.bones[role]!);
          if (!target.storage.every((double value) => value.isFinite)) {
            throw StateError(
              'Ava produced a non-finite transform for ${role.name}.',
            );
          }
          frameData.addAll(target.storage);
        }
        final accepted = await animationManager.addBoneAnimation(
          rigAsset,
          0,
          _boneIndices[boneName]!,
          frameData,
          motion.frames.length,
          motion.frameLengthMilliseconds,
          fadeInInSecs: math.max(0.001, fadeIn.inMicroseconds / 1000000),
          fadeOutInSecs: math.max(0.001, fadeOut.inMicroseconds / 1000000),
          maxDelta: motion.maximumBlend,
          loop: motion.loop,
        );
        if (!accepted) {
          throw StateError(
            'Filament rejected Ava\'s ${role.name} procedural motion.',
          );
        }
      }
    } catch (_) {
      rethrow;
    }
  }

  Future<void> _addMorphMotion(AvatarMorphMotion motion) async {
    if (motion.frames.isEmpty || motion.targetNames.isEmpty) {
      throw StateError('Ava facial solver produced an empty morph motion.');
    }
    if (motion.frames.any(
      (frame) => frame.length != motion.targetNames.length,
    )) {
      throw StateError('Ava facial solver changed target count mid-motion.');
    }
    final animationManager = thermion.FilamentApp.instance!.animationManager;
    var installed = 0;
    for (final entry in _morphTargetsByEntity.entries) {
      final indices = <int>[];
      final sourceIndices = <int>[];
      for (var index = 0; index < motion.targetNames.length; index += 1) {
        final targetIndex = entry.value.indexOf(motion.targetNames[index]);
        if (targetIndex < 0) continue;
        indices.add(targetIndex);
        sourceIndices.add(index);
      }
      if (indices.isEmpty) continue;
      final data = <double>[
        for (final frame in motion.frames)
          for (final sourceIndex in sourceIndices) frame[sourceIndex],
      ];
      final accepted = animationManager.setMorphAnimation(
        entry.key,
        data,
        indices,
        indices.length,
        motion.frames.length,
        motion.frameLengthMilliseconds,
      );
      if (!accepted) {
        throw StateError('Filament rejected Ava\'s facial touch motion.');
      }
      _activeMorphEntities.add(entry.key);
      installed += 1;
    }
    if (installed == 0) {
      throw StateError(
        'Ava facial mesh does not expose the required touch morph targets.',
      );
    }
  }

  Future<void> _clearMorphMotions() async {
    if (_activeMorphEntities.isEmpty) return;
    final animationManager = thermion.FilamentApp.instance!.animationManager;
    for (final entity in _activeMorphEntities) {
      final cleared = animationManager.clearMorphAnimation(entity);
      if (!cleared) {
        throw StateError('Filament could not clear Ava\'s facial motion.');
      }
    }
    _activeMorphEntities.clear();
  }

  @override
  Future<List<String>> getAvailableAnimations() async {
    _ensureReady();
    return _animationDurations.keys.toList(growable: false);
  }

  @override
  void setCameraTarget(double x, double y, double z) {
    _ensureReady();
    if (![x, y, z].every((value) => value.isFinite)) {
      throw ArgumentError('Ava camera target values must be finite.');
    }
    _cameraTarget = thermion.Vector3(x, y, z);
    if (_currentCameraOrbit != null) _scheduleCameraUpdate();
  }

  @override
  void setFrontCameraOrbit(double theta, double phi, double radius) {
    _ensureReady();
    if (![theta, phi, radius].every((value) => value.isFinite) || radius <= 0) {
      throw ArgumentError('Ava camera orbit must be finite and positive.');
    }
    _cancelCameraRecovery();
    final orbit = (theta: theta, phi: phi, radius: radius);
    _frontCameraOrbit = orbit;
    _currentCameraOrbit = orbit;
    _scheduleCameraUpdate();
  }

  @override
  void recenterCamera() {
    _ensureReady();
    final front = _frontCameraOrbit;
    final current = _currentCameraOrbit;
    if (front == null) {
      throw StateError('Ava front camera orbit has not been configured.');
    }
    if (current == null) {
      throw StateError('Ava current camera orbit has not been configured.');
    }
    _cancelCameraRecovery();
    final clock = Stopwatch()..start();
    _cameraRecoveryClock = clock;
    _cameraRecoveryTimer = Timer.periodic(
      _cameraRecoveryFrameInterval,
      (timer) {
        if (_disposed || _viewer == null || _cameraRecoveryClock != clock) {
          timer.cancel();
          return;
        }
        final progress = clock.elapsed.inMicroseconds /
            _cameraRecoveryDuration.inMicroseconds;
        _currentCameraOrbit = avaFilamentCameraRecoveryOrbit(
          current,
          front,
          progress.clamp(0.0, 1.0),
        );
        _scheduleCameraUpdate();
        if (progress >= 1) {
          _currentCameraOrbit = front;
          _scheduleCameraUpdate();
          _cancelCameraRecovery();
        }
      },
    );
  }

  void orbitCameraByDrag({
    required double logicalDeltaX,
    required double logicalDeltaY,
    required int sensitivity,
    required double minimumPhiDegrees,
    required double maximumPhiDegrees,
  }) {
    _ensureReady();
    final current = _currentCameraOrbit;
    if (current == null) {
      throw StateError('Ava front camera orbit has not been configured.');
    }
    _cancelCameraRecovery();
    _currentCameraOrbit = (
      theta: avaFilamentOrbitThetaForDrag(
        current.theta,
        logicalDeltaX,
        sensitivity,
      ),
      phi: avaFilamentOrbitPhiForDrag(
        current.phi,
        logicalDeltaY,
        sensitivity,
        minimumPhiDegrees: minimumPhiDegrees,
        maximumPhiDegrees: maximumPhiDegrees,
      ),
      radius: current.radius,
    );
    _scheduleCameraUpdate();
  }

  void _cancelCameraRecovery() {
    _cameraRecoveryTimer?.cancel();
    _cameraRecoveryTimer = null;
    _cameraRecoveryClock?.stop();
    _cameraRecoveryClock = null;
  }

  void _scheduleCameraUpdate() {
    _cameraUpdatePending = true;
    if (_cameraUpdateInFlight) return;
    unawaited(_drainCameraUpdates());
  }

  Future<void> _drainCameraUpdates() async {
    _cameraUpdateInFlight = true;
    final generation = _attachmentGeneration;
    try {
      while (_cameraUpdatePending &&
          !_disposed &&
          generation == _attachmentGeneration) {
        _cameraUpdatePending = false;
        final orbit = _currentCameraOrbit;
        final viewer = _viewer;
        if (orbit == null || viewer == null) return;
        final azimuth = orbit.theta * math.pi / 180;
        final polar = orbit.phi * math.pi / 180;
        final horizontal = orbit.radius * math.sin(polar);
        final position = thermion.Vector3(
          _cameraTarget.x + horizontal * math.sin(azimuth),
          _cameraTarget.y + orbit.radius * math.cos(polar),
          _cameraTarget.z + horizontal * math.cos(azimuth),
        );
        final camera = await viewer.getActiveCamera();
        await camera.lookAt(position, focus: _cameraTarget);
      }
    } catch (error) {
      _reportCommandError('Ava could not update her native camera: $error');
    } finally {
      _cameraUpdateInFlight = false;
      if (_cameraUpdatePending && !_disposed && _viewer != null) {
        _scheduleCameraUpdate();
      }
    }
  }

  @override
  int playAnimation({
    required String animationName,
    required int loopCount,
    double playbackRate = 1,
  }) {
    _ensureReady();
    if (!_animationDurations.containsKey(animationName)) {
      throw StateError('Ava does not contain animation $animationName.');
    }
    if (loopCount != 0 && loopCount != 1) {
      throw UnsupportedError(
        'The Filament migration controller supports infinite or one-shot '
        'embedded clips only; received loopCount=$loopCount.',
      );
    }
    if (!playbackRate.isFinite || playbackRate <= 0) {
      throw ArgumentError.value(
        playbackRate,
        'playbackRate',
        'must be finite and positive',
      );
    }

    final playbackId = ++_nextPlaybackId;
    _activePlaybackId = playbackId;
    _completionTimer?.cancel();
    _completionTimer = null;
    final asset = _asset!;
    unawaited(
      asset
          .playGltfAnimationByName(
        animationName,
        loop: loopCount == 0,
        replaceActive: true,
        crossfade: 0.18,
        speed: playbackRate,
      )
          .then((_) {
        if (_disposed || loopCount == 0 || _activePlaybackId != playbackId) {
          return;
        }
        final clipDuration = _animationDurations[animationName]!;
        final playbackDuration = Duration(
          microseconds: (clipDuration.inMicroseconds / playbackRate).round(),
        );
        _completionTimer = Timer(playbackDuration, () {
          if (_disposed || _activePlaybackId != playbackId) return;
          _activePlaybackId = null;
          _onAnimationFinished?.call(
            AvaAnimationFinishedEvent(
              playbackId: playbackId,
              animationName: animationName,
            ),
          );
        });
      }).catchError((Object error, StackTrace stackTrace) {
        _reportCommandError(
          'Ava could not execute native animation $animationName: $error',
        );
      }),
    );
    return playbackId;
  }

  @override
  void dispose() {
    if (_disposed) return;
    _disposed = true;
    _cancelCameraRecovery();
    _completionTimer?.cancel();
    _completionTimer = null;
    _activePlaybackId = null;
    _viewer = null;
    _asset = null;
    _rigAsset = null;
    _rigBindings = null;
    _boneEntities = const {};
    _boneIndices = const {};
    _morphTargetsByEntity = const {};
    _activeMorphEntities.clear();
    _performanceGraph.reset();
    _scheduledLayerRevisions.clear();
    _scheduledLayerFeatures.clear();
    _motionTimelineAt = Duration.zero;
    _animationDurations = const {};
    _frontCameraOrbit = null;
    _currentCameraOrbit = null;
    _cameraUpdatePending = false;
    _livingIdleEnabled = false;
    _onAnimationFinished = null;
    _onCommandError = null;
    _attachmentGeneration += 1;
  }

  void _reportCommandError(String message) {
    if (_disposed) return;
    _onCommandError?.call(message);
  }

  void _ensureReady() {
    _ensureNotDisposed();
    if (_viewer == null ||
        _asset == null ||
        _rigAsset == null ||
        _rigBindings == null) {
      throw StateError('Ava Filament controller is not attached.');
    }
  }

  void _ensureNotDisposed() {
    if (_disposed) {
      throw StateError('Ava Filament controller is disposed.');
    }
  }
}

class _AvaRendererLayerFeatures {
  const _AvaRendererLayerFeatures({
    required this.skeletal,
    required this.morph,
  });

  factory _AvaRendererLayerFeatures.fromPerformance(
    AvatarLayerPerformance performance,
  ) =>
      _AvaRendererLayerFeatures(
        skeletal: performance.skeletalMotion != null,
        morph: performance.morphMotion != null,
      );

  final bool skeletal;
  final bool morph;
}

class AvaFilamentViewer extends StatefulWidget {
  const AvaFilamentViewer({
    required this.controller,
    required this.src,
    required this.anatomyMapAsset,
    required this.rigBindingsAsset,
    required this.onAnatomicalTap,
    required this.onAnimationFinished,
    this.gestureConfiguration = const AvaViewerGestureConfiguration(),
    this.onProfileReady,
    this.onProgress,
    this.onLoad,
    this.onError,
    super.key,
  });

  final AvaFilamentController controller;
  final String src;
  final String anatomyMapAsset;
  final String rigBindingsAsset;
  final ValueChanged<AvaFilamentPointerEvent> onAnatomicalTap;
  final ValueChanged<AvaAnimationFinishedEvent> onAnimationFinished;
  final AvaViewerGestureConfiguration gestureConfiguration;
  final ValueChanged<AvaFilamentModelProfile>? onProfileReady;
  final ValueChanged<double>? onProgress;
  final ValueChanged<String>? onLoad;
  final ValueChanged<String>? onError;

  @override
  State<AvaFilamentViewer> createState() => _AvaFilamentViewerState();
}

class _AvaFilamentViewerState extends State<AvaFilamentViewer> {
  thermion.ThermionViewer? _viewer;
  thermion.ThermionAsset? _rigAsset;
  AvatarAnatomyMap? _anatomyMap;
  List<String> _boneNames = const [];
  List<thermion.ThermionEntity> _boneEntities = const [];
  Set<thermion.ThermionEntity> _assetEntities = const {};
  _FilamentPointerContact? _pointerContact;
  Timer? _autoCenterTimer;
  bool _disposed = false;

  @override
  void initState() {
    super.initState();
    if (widget.gestureConfiguration.maximumTapDuration <= Duration.zero) {
      throw ArgumentError.value(
        widget.gestureConfiguration.maximumTapDuration,
        'gestureConfiguration.maximumTapDuration',
        'must be greater than zero',
      );
    }
    if (widget.gestureConfiguration.autoCenterDelay <= Duration.zero) {
      throw ArgumentError.value(
        widget.gestureConfiguration.autoCenterDelay,
        'gestureConfiguration.autoCenterDelay',
        'must be greater than zero',
      );
    }
    unawaited(_initialize());
  }

  Future<void> _initialize() async {
    thermion.ThermionViewer? viewer;
    try {
      _notifyProgress(0.05);
      final anatomySourceFuture = rootBundle.loadString(widget.anatomyMapAsset);
      final rigBindingsSourceFuture =
          rootBundle.loadString(widget.rigBindingsAsset);
      final anatomySource = await anatomySourceFuture;
      final anatomyMap = AvatarAnatomyMap.fromJsonString(anatomySource);
      final rigBindings = AvatarRigBindings.fromJsonString(
        await rigBindingsSourceFuture,
      );
      final modelData = await rootBundle.load(widget.src);
      final modelBytes = modelData.buffer.asUint8List(
        modelData.offsetInBytes,
        modelData.lengthInBytes,
      );
      final modelHash = await compute(_sha256HexForFilament, modelBytes);
      if (modelHash != anatomyMap.modelSha256) {
        throw StateError(
          'Ava Anatomy Map v2 does not match the native renderer model.',
        );
      }

      viewer = await thermion.ThermionFlutterPlugin.createViewer();
      if (_disposed) {
        await viewer.dispose();
        return;
      }
      await viewer.setFrameRate(120);
      await viewer.setRendering(true);
      await viewer.setPostProcessing(true);
      await viewer.setAntiAliasing(true, true, false);
      await viewer.view.setRenderQuality(thermion.QualityLevel.HIGH);
      await viewer.view.setDithering(true);
      await viewer.setBackgroundColor(0, 0, 0, 0);
      final colorGrading = await (await viewer.view.createColorGradingBuilder())
          .quality(thermion.QualityLevel.HIGH)
          .toneMapper(await thermion.ToneMapper.pbrNeutral())
          .exposure(-0.18)
          .contrast(1.08)
          .vibrance(1.04)
          .saturation(1.02)
          .luminanceScaling(true)
          .gamutMapping(true)
          .build();
      await viewer.view.setColorGrading(colorGrading);
      await viewer.addDirectLight(
        thermion.DirectLight.sun(
          direction: thermion.Vector3(-0.35, -0.8, -0.45).normalized(),
          intensity: 75000,
          castShadows: false,
        ),
      );
      await viewer.addDirectLight(
        thermion.DirectLight.point(
          position: thermion.Vector3(1.4, 2.2, 2.4),
          intensity: 24000,
          falloffRadius: 5,
          castShadows: false,
        ),
      );
      _notifyProgress(0.35);

      final asset = await viewer.loadGltf(
        'asset://${widget.src}',
        releaseSourceData: true,
      );
      final childEntities = await asset.getChildEntities();
      final renderableManager =
          thermion.FilamentApp.instance!.renderableManager;
      for (final entity in <thermion.ThermionEntity>{
        asset.entity,
        ...childEntities,
      }) {
        if (!renderableManager.isRenderable(entity)) continue;
        await renderableManager.setCastShadows(entity, false);
        await renderableManager.setReceiveShadows(entity, false);
      }
      await asset.addAnimationComponent();
      final rigAsset = asset.isInstance ? asset : await asset.getInstance(0);
      final boneNames = await rigAsset.getBoneNames(skinIndex: 0);
      final boneEntities = await rigAsset.getBones(skinIndex: 0);
      if (boneNames.length != boneEntities.length || boneNames.isEmpty) {
        throw StateError(
          'Filament returned an invalid Ava skeleton: '
          '${boneNames.length} names and ${boneEntities.length} entities.',
        );
      }
      final missingInteractiveJoints = anatomyMap.interactiveJoints
          .map((joint) => joint.name)
          .where((name) => !boneNames.contains(name))
          .toSet();
      if (missingInteractiveJoints.isNotEmpty) {
        throw StateError(
          'Filament did not expose Ava joints required for picking: '
          '${missingInteractiveJoints.join(', ')}.',
        );
      }
      final missingRigJoints = rigBindings.requiredJointNames
          .where((name) => !boneNames.contains(name))
          .toSet();
      if (missingRigJoints.isNotEmpty) {
        throw StateError(
          'Filament did not expose Ava joints required for procedural motion: '
          '${missingRigJoints.join(', ')}.',
        );
      }
      final boneEntityMap = <String, thermion.ThermionEntity>{};
      final boneIndexMap = <String, int>{};
      for (var index = 0; index < boneNames.length; index += 1) {
        boneEntityMap[boneNames[index]] = boneEntities[index];
        boneIndexMap[boneNames[index]] = index;
      }
      final boneAnimationAdded = await thermion
          .FilamentApp.instance!.animationManager
          .addBoneAnimationComponent(rigAsset);
      if (!boneAnimationAdded) {
        throw StateError(
          'Filament could not attach Ava\'s procedural animation component.',
        );
      }

      final animationNames = await asset.getGltfAnimationNames();
      final animationDurations = <String, Duration>{};
      for (var index = 0; index < animationNames.length; index += 1) {
        final seconds = await asset.getGltfAnimationDuration(index);
        if (!seconds.isFinite || seconds <= 0) {
          throw StateError(
            'Ava animation ${animationNames[index]} has invalid duration '
            '$seconds.',
          );
        }
        animationDurations[animationNames[index]] = Duration(
          microseconds: (seconds * Duration.microsecondsPerSecond).round(),
        );
      }

      final morphTargetNames = <String>{};
      final morphTargetsByEntity = <thermion.ThermionEntity, List<String>>{};
      for (final nodeName in anatomyMap.renderableNodeNames) {
        final entity = await asset.getChildEntity(nodeName);
        if (entity == null || !renderableManager.isRenderable(entity)) {
          throw StateError(
            'Filament did not expose Ava renderable node $nodeName.',
          );
        }
        final names = await asset.getMorphTargetNames(entity: entity);
        morphTargetNames.addAll(names);
        if (names.isNotEmpty) {
          morphTargetsByEntity[entity] = List.unmodifiable(names);
        }
      }
      if (morphTargetNames.isEmpty) {
        throw StateError(
          'Filament loaded Ava without exposing any morph targets.',
        );
      }
      final assetEntities = <thermion.ThermionEntity>{
        asset.entity,
        rigAsset.entity,
        ...childEntities,
        ...await rigAsset.getChildEntities(),
      };

      _viewer = viewer;
      _rigAsset = rigAsset;
      _anatomyMap = anatomyMap;
      _boneNames = List.unmodifiable(boneNames);
      _boneEntities = List.unmodifiable(boneEntities);
      _assetEntities = Set.unmodifiable(assetEntities);
      widget.controller.attach(
        viewer: viewer,
        asset: asset,
        rigAsset: rigAsset,
        rigBindings: rigBindings,
        boneEntities: boneEntityMap,
        boneIndices: boneIndexMap,
        morphTargetsByEntity: morphTargetsByEntity,
        animationDurations: animationDurations,
        onAnimationFinished: widget.onAnimationFinished,
        onCommandError: (message) => widget.onError?.call(message),
      );
      final profile = AvaFilamentModelProfile(
        animationNames: Set.unmodifiable(animationNames.toSet()),
        boneNames: Set.unmodifiable(boneNames.toSet()),
        morphTargetNames: Set.unmodifiable(morphTargetNames),
      );
      if (mounted) setState(() {});
      _afterFrame(() {
        widget.onProfileReady?.call(profile);
        widget.onProgress?.call(1);
        widget.onLoad?.call(widget.src);
      });
    } catch (error, stackTrace) {
      if (viewer != null && viewer != _viewer) {
        await viewer.dispose();
      }
      FlutterError.reportError(
        FlutterErrorDetails(
          exception: error,
          stack: stackTrace,
          library: 'ava_filament_renderer',
          context: ErrorDescription('while initializing Ava in Filament'),
        ),
      );
      if (!_disposed) {
        _afterFrame(
          () => widget.onError?.call('Native Ava renderer failed: $error'),
        );
      }
    }
  }

  void _notifyProgress(double progress) {
    _afterFrame(() => widget.onProgress?.call(progress));
  }

  void _afterFrame(VoidCallback callback) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_disposed && mounted) callback();
    });
  }

  @override
  Widget build(BuildContext context) {
    final viewer = _viewer;
    if (viewer == null) return const SizedBox.expand();
    return Listener(
      behavior: HitTestBehavior.opaque,
      onPointerDown: _handlePointerDown,
      onPointerMove: _handlePointerMove,
      onPointerUp: _handlePointerUp,
      onPointerCancel: _handlePointerCancel,
      child: AvaFilamentSurface(
        viewer: viewer,
        onError: (error) {
          if (!_disposed) {
            widget.onError?.call('Ava native surface failed: $error');
          }
        },
      ),
    );
  }

  void _handlePointerDown(PointerDownEvent event) {
    if (_pointerContact != null) return;
    _pointerContact = _FilamentPointerContact(
      pointer: event.pointer,
      position: event.localPosition,
      avatarHit: _isAvatarAt(event.localPosition),
      startedAt: DateTime.now(),
      peakPressure: _normalizedPressure(event.pressure, event.pressureMax),
      kind: event.kind,
    );
  }

  void _handlePointerMove(PointerMoveEvent event) {
    final contact = _pointerContact;
    if (contact == null || contact.pointer != event.pointer) return;
    contact.peakPressure = math.max(
      contact.peakPressure,
      _normalizedPressure(event.pressure, event.pressureMax),
    );
    final delta = event.localPosition - contact.lastPosition;
    contact.lastPosition = event.localPosition;
    contact.accumulatedHorizontalDrag += delta.dx;
    contact.accumulatedVerticalDrag += delta.dy;
    contact.maximumTravel = math.max(
      contact.maximumTravel,
      (event.localPosition - contact.position).distance,
    );
    if (contact.maximumTravel > widget.gestureConfiguration.tapSlop) {
      _activatePendingDrag(contact);
      if (contact.avatarHitResolved && contact.startedOnAvatar) {
        _applyPendingDrag(contact);
      }
    }
  }

  void _handlePointerUp(PointerUpEvent event) {
    final contact = _pointerContact;
    if (contact == null || contact.pointer != event.pointer) return;
    _pointerContact = null;
    contact.ended = true;
    final delta = event.localPosition - contact.lastPosition;
    contact.lastPosition = event.localPosition;
    contact.accumulatedHorizontalDrag += delta.dx;
    contact.accumulatedVerticalDrag += delta.dy;
    contact.maximumTravel = math.max(
      contact.maximumTravel,
      (event.localPosition - contact.position).distance,
    );
    contact.peakPressure = math.max(
      contact.peakPressure,
      _normalizedPressure(event.pressure, event.pressureMax),
    );
    unawaited(_finishPointerContact(contact));
  }

  void _handlePointerCancel(PointerCancelEvent event) {
    final contact = _pointerContact;
    if (contact == null || contact.pointer != event.pointer) return;
    _pointerContact = null;
    contact.ended = true;
    if (contact.didOrbit) _scheduleAutoCenter();
  }

  void _activatePendingDrag(_FilamentPointerContact contact) {
    if (contact.avatarHitRequested) return;
    contact.avatarHitRequested = true;
    unawaited(
      contact.avatarHit.then((startedOnAvatar) {
        contact.avatarHitResolved = true;
        contact.startedOnAvatar = startedOnAvatar;
        if (!startedOnAvatar || contact.ended || _pointerContact != contact) {
          return;
        }
        _autoCenterTimer?.cancel();
        _autoCenterTimer = null;
        _applyPendingDrag(contact);
      }),
    );
  }

  void _applyPendingDrag(_FilamentPointerContact contact) {
    final horizontalDelta = contact.accumulatedHorizontalDrag;
    final verticalDelta = contact.accumulatedVerticalDrag;
    contact.accumulatedHorizontalDrag = 0;
    contact.accumulatedVerticalDrag = 0;
    if (horizontalDelta.abs() < 0.001 && verticalDelta.abs() < 0.001) return;
    try {
      widget.controller.orbitCameraByDrag(
        logicalDeltaX: horizontalDelta,
        logicalDeltaY: verticalDelta,
        sensitivity: widget.gestureConfiguration.orbitSensitivity,
        minimumPhiDegrees: widget.gestureConfiguration.minimumPolarAngleDegrees,
        maximumPhiDegrees: widget.gestureConfiguration.maximumPolarAngleDegrees,
      );
      contact.didOrbit = true;
    } catch (error, stackTrace) {
      _reportInteractionFailure(
        error,
        stackTrace,
        'while orbiting Ava from a mesh drag',
      );
    }
  }

  Future<void> _finishPointerContact(
    _FilamentPointerContact contact,
  ) async {
    final startedOnAvatar = await contact.avatarHit;
    contact.avatarHitResolved = true;
    contact.startedOnAvatar = startedOnAvatar;
    if (!startedOnAvatar || _disposed) return;

    final duration = DateTime.now().difference(contact.startedAt);
    if (contact.maximumTravel > widget.gestureConfiguration.tapSlop) {
      _applyPendingDrag(contact);
      if (contact.didOrbit) _scheduleAutoCenter();
      return;
    }
    if (duration > widget.gestureConfiguration.maximumTapDuration) return;
    await _resolvePick(
      position: contact.lastPosition,
      duration: duration,
      peakPressure: contact.peakPressure,
      kind: contact.kind,
    );
  }

  Future<bool> _isAvatarAt(Offset position) async {
    try {
      final viewer = _viewer;
      if (viewer == null) {
        throw StateError('Ava native drag picking is not initialized.');
      }
      final completer = Completer<thermion.PickResult>();
      final pickPixel = avaFilamentPhysicalPickPixel(
        position,
        MediaQuery.devicePixelRatioOf(context),
      );
      await viewer.view.pick(
        pickPixel.x,
        pickPixel.y,
        completer.complete,
      );
      final pick = await completer.future;
      return pick.entity != 0 && _assetEntities.contains(pick.entity);
    } catch (error, stackTrace) {
      _reportInteractionFailure(
        error,
        stackTrace,
        'while testing the start of an Ava mesh drag',
      );
      return false;
    }
  }

  void _scheduleAutoCenter() {
    _autoCenterTimer?.cancel();
    _autoCenterTimer = Timer(
      widget.gestureConfiguration.autoCenterDelay,
      () {
        if (_disposed) return;
        try {
          widget.controller.recenterCamera();
        } catch (error, stackTrace) {
          _reportInteractionFailure(
            error,
            stackTrace,
            'while returning Ava to the front after inactivity',
          );
        }
      },
    );
  }

  void _reportInteractionFailure(
    Object error,
    StackTrace stackTrace,
    String contextDescription,
  ) {
    FlutterError.reportError(
      FlutterErrorDetails(
        exception: error,
        stack: stackTrace,
        library: 'ava_filament_renderer',
        context: ErrorDescription(contextDescription),
      ),
    );
    if (!_disposed) {
      widget.onError?.call('Ava native interaction failed: $error');
    }
  }

  Future<void> _resolvePick({
    required Offset position,
    required Duration duration,
    required double peakPressure,
    required PointerDeviceKind kind,
  }) async {
    try {
      final viewer = _viewer;
      final rigAsset = _rigAsset;
      final anatomyMap = _anatomyMap;
      if (viewer == null || rigAsset == null || anatomyMap == null) {
        throw StateError('Ava native picking is not initialized.');
      }
      final completer = Completer<thermion.PickResult>();
      final pickPixel = avaFilamentPhysicalPickPixel(
        position,
        MediaQuery.devicePixelRatioOf(context),
      );
      await viewer.view.pick(
        pickPixel.x,
        pickPixel.y,
        completer.complete,
      );
      final pick = await completer.future;
      if (pick.entity == 0 || !_assetEntities.contains(pick.entity)) return;

      final viewport = await viewer.view.getViewport();
      final camera = await viewer.getActiveCamera();
      final projection = await camera.getProjectionMatrix();
      final viewMatrix = await camera.getViewMatrix();
      final ndc = thermion.Vector4(
        (pick.fragX / viewport.width) * 2 - 1,
        1 - (pick.fragY / viewport.height) * 2,
        pick.fragZ * 2 - 1,
        1,
      );
      final inverseViewProjection = (projection * viewMatrix)..invert();
      final worldHomogeneous = inverseViewProjection * ndc;
      if (worldHomogeneous.w.abs() < 0.000001) {
        throw StateError('Ava native pick produced an invalid world point.');
      }
      final worldPosition = worldHomogeneous.xyz / worldHomogeneous.w;
      final resolved = await _nearestJointContact(
        worldPosition: worldPosition,
        anatomyMap: anatomyMap,
      );
      final anatomy = anatomyMap.contactForJointCoordinates(
        jointName: resolved.frame.name,
        longitudinalPosition: resolved.longitudinal,
        radialAngle: resolved.radialAngle,
      );

      final cameraPosition = await camera.getPosition();
      final target = widget.controller._cameraTarget;
      final cameraOffset = cameraPosition - target;
      final horizontal = math.sqrt(
        cameraOffset.x * cameraOffset.x + cameraOffset.z * cameraOffset.z,
      );
      final radius = cameraOffset.length;
      if (!mounted) return;
      final renderBox = context.findRenderObject() as RenderBox?;
      if (renderBox == null || !renderBox.hasSize) {
        throw StateError('Ava native renderer has no viewport size.');
      }
      widget.onAnatomicalTap(
        AvaFilamentPointerEvent(
          position: position,
          viewportSize: renderBox.size,
          modelPosition: _avatarVector(worldPosition),
          modelNormal: _avatarVector(resolved.worldNormal),
          anatomy: anatomy,
          contactDuration: duration,
          peakPressure: peakPressure,
          pointerType: kind.name,
          cameraAzimuth: math.atan2(cameraOffset.x, cameraOffset.z),
          cameraElevation: math.atan2(cameraOffset.y, horizontal),
          cameraRadius: radius,
          avatarRootYaw: 0,
        ),
      );
    } catch (error, stackTrace) {
      FlutterError.reportError(
        FlutterErrorDetails(
          exception: error,
          stack: stackTrace,
          library: 'ava_filament_renderer',
          context: ErrorDescription('while resolving an Ava native pick'),
        ),
      );
      if (!_disposed) widget.onError?.call('Ava native picking failed: $error');
    }
  }

  Future<_ResolvedJointContact> _nearestJointContact({
    required thermion.Vector3 worldPosition,
    required AvatarAnatomyMap anatomyMap,
  }) async {
    _ResolvedJointContact? nearest;
    final interactiveByName = <String, AvatarJointFrame>{
      for (final frame in anatomyMap.interactiveJoints) frame.name: frame,
    };
    final transformManager = thermion.FilamentApp.instance!.transformManager;
    for (var index = 0; index < _boneNames.length; index += 1) {
      final frame = interactiveByName[_boneNames[index]];
      if (frame == null) continue;
      final worldTransform = transformManager.getWorldTransform(
        _boneEntities[index],
      );
      final inverse = worldTransform.clone()..invert();
      final localHomogeneous = inverse *
          thermion.Vector4(
            worldPosition.x,
            worldPosition.y,
            worldPosition.z,
            1,
          );
      final local = localHomogeneous.xyz / localHomogeneous.w;
      final axis = _thermionVector(frame.axis).normalized();
      final front = _thermionVector(frame.front).normalized();
      final tangent = _thermionVector(frame.tangent).normalized();
      final axialDistance = local.dot(axis);
      final clampedAxial = axialDistance.clamp(0.0, frame.length);
      final radial = local - axis * clampedAxial;
      final distance = radial.length + (axialDistance - clampedAxial).abs();
      if (nearest != null && distance >= nearest.distance) continue;
      final localNormal =
          radial.length2 > 0.0000001 ? radial.normalized() : front;
      final worldNormal4 = worldTransform *
          thermion.Vector4(
            localNormal.x,
            localNormal.y,
            localNormal.z,
            0,
          );
      final worldNormal = worldNormal4.xyz.normalized();
      nearest = _ResolvedJointContact(
        frame: frame,
        distance: distance,
        longitudinal: (axialDistance / frame.length).clamp(0.0, 1.0),
        radialAngle: math.atan2(radial.dot(tangent), radial.dot(front)),
        worldNormal: worldNormal,
      );
    }
    if (nearest == null) {
      throw StateError(
          'Ava native pick did not resolve to an interactive bone.');
    }
    return nearest;
  }

  @override
  void dispose() {
    _disposed = true;
    _autoCenterTimer?.cancel();
    _autoCenterTimer = null;
    _pointerContact = null;
    final viewer = _viewer;
    _viewer = null;
    _rigAsset = null;
    _anatomyMap = null;
    _boneNames = const [];
    _boneEntities = const [];
    _assetEntities = const {};
    if (viewer != null) {
      widget.controller.detach(viewer);
      // ThermionWidget releases the Metal texture asynchronously during its
      // own unmount. Destroying the viewer in the same frame can invalidate
      // the swapchain while Filament is still inside endFrame.
      WidgetsBinding.instance.addPostFrameCallback((_) {
        Timer(const Duration(milliseconds: 250), () {
          unawaited(viewer.dispose());
        });
      });
    }
    super.dispose();
  }
}

class _FilamentPointerContact {
  _FilamentPointerContact({
    required this.pointer,
    required this.position,
    required this.avatarHit,
    required this.startedAt,
    required this.peakPressure,
    required this.kind,
  }) : lastPosition = position;

  final int pointer;
  final Offset position;
  final Future<bool> avatarHit;
  final DateTime startedAt;
  Offset lastPosition;
  double accumulatedHorizontalDrag = 0;
  double accumulatedVerticalDrag = 0;
  double maximumTravel = 0;
  bool avatarHitRequested = false;
  bool avatarHitResolved = false;
  bool startedOnAvatar = false;
  bool didOrbit = false;
  bool ended = false;
  double peakPressure;
  final PointerDeviceKind kind;
}

class _ResolvedJointContact {
  const _ResolvedJointContact({
    required this.frame,
    required this.distance,
    required this.longitudinal,
    required this.radialAngle,
    required this.worldNormal,
  });

  final AvatarJointFrame frame;
  final double distance;
  final double longitudinal;
  final double radialAngle;
  final thermion.Vector3 worldNormal;
}

double _normalizedPressure(double pressure, double maximum) {
  if (!pressure.isFinite || !maximum.isFinite || maximum <= 0) return 0;
  return (pressure / maximum).clamp(0.0, 1.0);
}

thermion.Vector3 _thermionVector(AvatarModelVector3 value) {
  return thermion.Vector3(value.x, value.y, value.z);
}

AvatarModelVector3 _avatarVector(thermion.Vector3 value) {
  return AvatarModelVector3(value.x, value.y, value.z);
}

thermion.Matrix4 _matrixForPoseDelta(AvatarBonePoseDelta delta) {
  final rotationX = thermion.Quaternion.axisAngle(
    thermion.Vector3(1, 0, 0),
    delta.rotationX,
  );
  final rotationY = thermion.Quaternion.axisAngle(
    thermion.Vector3(0, 1, 0),
    delta.rotationY,
  );
  final rotationZ = thermion.Quaternion.axisAngle(
    thermion.Vector3(0, 0, 1),
    delta.rotationZ,
  );
  return thermion.Matrix4.compose(
    thermion.Vector3(
      delta.translationX,
      delta.translationY,
      delta.translationZ,
    ),
    (rotationZ * rotationY * rotationX).normalized(),
    thermion.Vector3.all(1),
  );
}

String _sha256HexForFilament(Uint8List bytes) {
  return sha256.convert(bytes).toString();
}

@visibleForTesting
math.Point<int> avaFilamentPhysicalPickPixel(
  Offset logicalPosition,
  double devicePixelRatio,
) {
  if (!logicalPosition.dx.isFinite ||
      !logicalPosition.dy.isFinite ||
      !devicePixelRatio.isFinite ||
      devicePixelRatio <= 0) {
    throw ArgumentError(
      'Ava pick coordinates and device pixel ratio must be finite and the '
      'ratio must be positive.',
    );
  }
  return math.Point<int>(
    (logicalPosition.dx * devicePixelRatio).round(),
    (logicalPosition.dy * devicePixelRatio).round(),
  );
}

@visibleForTesting
double avaFilamentOrbitThetaForDrag(
  double currentThetaDegrees,
  double logicalDeltaX,
  int sensitivity,
) {
  if (!currentThetaDegrees.isFinite ||
      !logicalDeltaX.isFinite ||
      sensitivity <= 0) {
    throw ArgumentError(
      'Ava orbit angle and drag delta must be finite and sensitivity must be '
      'positive.',
    );
  }
  var theta = (currentThetaDegrees - logicalDeltaX * 0.32 * sensitivity) % 360;
  if (theta > 180) theta -= 360;
  if (theta <= -180) theta += 360;
  return theta;
}

@visibleForTesting
double avaFilamentOrbitPhiForDrag(
  double currentPhiDegrees,
  double logicalDeltaY,
  int sensitivity, {
  required double minimumPhiDegrees,
  required double maximumPhiDegrees,
}) {
  if (!currentPhiDegrees.isFinite ||
      !logicalDeltaY.isFinite ||
      !minimumPhiDegrees.isFinite ||
      !maximumPhiDegrees.isFinite ||
      sensitivity <= 0 ||
      minimumPhiDegrees < 0 ||
      maximumPhiDegrees > 180 ||
      minimumPhiDegrees >= maximumPhiDegrees) {
    throw ArgumentError(
      'Ava vertical orbit values must be finite, sensitivity must be positive, '
      'and polar limits must satisfy 0 <= minimum < maximum <= 180.',
    );
  }
  // The child is moving the viewpoint, not dragging Ava as a flat image:
  // dragging upward raises the viewpoint and keeps the top of her head in
  // view; dragging downward lowers it by the smaller configured allowance.
  // Match the object under the finger: dragging Ava upward lowers the camera
  // orbit so her rendered body moves upward, and vice versa.
  return (currentPhiDegrees - logicalDeltaY * 0.22 * sensitivity).clamp(
    minimumPhiDegrees,
    maximumPhiDegrees,
  );
}

@visibleForTesting
AvaFilamentCameraOrbit avaFilamentCameraRecoveryOrbit(
  AvaFilamentCameraOrbit start,
  AvaFilamentCameraOrbit target,
  double progress,
) {
  if (![
        start.theta,
        start.phi,
        start.radius,
        target.theta,
        target.phi,
        target.radius,
        progress,
      ].every((value) => value.isFinite) ||
      start.radius <= 0 ||
      target.radius <= 0 ||
      progress < 0 ||
      progress > 1) {
    throw ArgumentError(
      'Ava camera recovery requires finite positive orbits and progress '
      'between zero and one.',
    );
  }
  final eased = 1 - math.pow(1 - progress, 3).toDouble();
  var thetaDelta = (target.theta - start.theta) % 360;
  if (thetaDelta > 180) thetaDelta -= 360;
  if (thetaDelta <= -180) thetaDelta += 360;
  var theta = (start.theta + thetaDelta * eased) % 360;
  if (theta > 180) theta -= 360;
  if (theta <= -180) theta += 360;
  return (
    theta: theta,
    phi: start.phi + (target.phi - start.phi) * eased,
    radius: start.radius + (target.radius - start.radius) * eased,
  );
}
