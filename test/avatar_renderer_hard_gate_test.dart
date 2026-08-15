import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_anatomy.dart';
import 'package:ava/ui/avatar/avatar_motion_scheduler.dart';
import 'package:ava/ui/avatar/avatar_procedural_motion.dart';
import 'package:ava/ui/avatar/avatar_renderer_performance_graph.dart';
import 'package:ava/ui/avatar/avatar_rig_bindings.dart';
import 'package:ava/ui/avatar/avatar_semantic_motion_solver.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'production renderer composes eight actions including the six-layer gate',
    () {
      const solver = AvatarSemanticMotionSolver();
      final scheduler = AvatarMotionScheduler();
      final graph = AvatarRendererPerformanceGraph();
      final intents = <AvatarActionIntent>[
        _intent(
          id: 'breathing',
          kind: AvatarActionKind.idle,
          source: AvatarActionSource.idleSystem,
          priority: AvatarActionPriority.idle,
          claims: const {
            AvatarMotionChannel.spine: AvatarMotionBlendMode.additive,
          },
          expectedDuration: null,
        ),
        _intent(
          id: 'blink',
          kind: AvatarActionKind.blink,
          source: AvatarActionSource.idleSystem,
          priority: AvatarActionPriority.ambient,
          claims: const {
            AvatarMotionChannel.faceExpression: AvatarMotionBlendMode.override,
          },
          expectedDuration: const Duration(milliseconds: 180),
        ),
        _intent(
          id: 'gaze',
          kind: AvatarActionKind.lookAt,
          source: AvatarActionSource.lesson,
          priority: AvatarActionPriority.lesson,
          claims: const {
            AvatarMotionChannel.gaze: AvatarMotionBlendMode.override,
          },
          expectedDuration: const Duration(milliseconds: 900),
          textParameters: const {'gaze': 'child'},
        ),
        _intent(
          id: 'viseme-aa',
          kind: AvatarActionKind.lipSync,
          source: AvatarActionSource.speech,
          priority: AvatarActionPriority.lesson,
          claims: const {
            AvatarMotionChannel.lipSync: AvatarMotionBlendMode.override,
          },
          expectedDuration: const Duration(milliseconds: 160),
          retargetGroup: 'speech:lipSync',
          retargetPolicy: AvatarRetargetPolicy.immediate,
          target: _lipSyncTarget(AvatarViseme.aa),
        ),
        _intent(
          id: 'arm-gesture',
          kind: AvatarActionKind.explain,
          source: AvatarActionSource.lesson,
          priority: AvatarActionPriority.lesson,
          claims: const {
            AvatarMotionChannel.rightArm: AvatarMotionBlendMode.override,
            AvatarMotionChannel.spine: AvatarMotionBlendMode.additive,
          },
          expectedDuration: const Duration(milliseconds: 900),
          textParameters: const {'gesture': 'explainSmall'},
        ),
        _touchIntent(),
        _intent(
          id: 'encouraging-expression',
          kind: AvatarActionKind.expression,
          source: AvatarActionSource.lesson,
          priority: AvatarActionPriority.lesson,
          claims: const {
            AvatarMotionChannel.faceExpression: AvatarMotionBlendMode.additive,
          },
          expectedDuration: const Duration(milliseconds: 800),
          textParameters: const {'expression': 'encouraging'},
        ),
        _intent(
          id: 'left-fingers',
          kind: AvatarActionKind.fingerPose,
          source: AvatarActionSource.lesson,
          priority: AvatarActionPriority.lesson,
          claims: const {
            AvatarMotionChannel.leftFingers: AvatarMotionBlendMode.override,
          },
          expectedDuration: const Duration(milliseconds: 620),
          textParameters: const {'pose': 'open'},
        ),
      ];

      AvatarRendererPerformancePlan? plan;
      for (var index = 0; index < intents.length; index += 1) {
        final now = Duration(milliseconds: index * 10);
        final scheduled = scheduler.submit(intents[index], now: now);
        expect(scheduled.type, AvatarScheduleResultType.started);
        plan = graph.start(
          action: scheduled.activeAction!,
          performance: solver.solve(intents[index]),
          timelineAt: now,
        );
      }

      expect(scheduler.activeActions, hasLength(8));
      expect(graph.activeCount, 8);
      expect(plan!.actions, hasLength(8));
      expect(
        plan.runtimeIds,
        containsAll(intents.map((intent) => intent.id)),
      );

      final skeletal = plan.skeletalMotion!;
      expect(skeletal.frameLengthMilliseconds, closeTo(1000 / 120, 1e-9));
      expect(
        skeletal.frames.first.bones.keys,
        containsAll(const {
          AvatarRigBone.spine,
          AvatarRigBone.head,
          AvatarRigBone.leftEye,
          AvatarRigBone.rightEye,
          AvatarRigBone.rightUpperArm,
          AvatarRigBone.leftUpperLeg,
          AvatarRigBone.leftIndex1,
        }),
      );
      for (final frame in skeletal.frames) {
        expect(frame.bones.length, skeletal.frames.first.bones.length);
        for (final pose in frame.bones.values) {
          expect(
            [
              pose.rotationX,
              pose.rotationY,
              pose.rotationZ,
              pose.translationX,
              pose.translationY,
              pose.translationZ,
            ].every((value) => value.isFinite),
            isTrue,
          );
        }
      }

      final morph = plan.morphMotion!;
      expect(
        morph.targetNames,
        containsAll(const {
          'Fcl_EYE_Close',
          'Fcl_MTH_A',
          'Fcl_EYE_Joy',
          'Fcl_MTH_Joy',
        }),
      );
      for (final frame in morph.frames) {
        expect(frame, hasLength(morph.targetNames.length));
        expect(
          frame.every((value) => value.isFinite && value >= 0 && value <= 1),
          isTrue,
        );
      }
    },
  );

  test('viseme retarget steers the active layer without restarting runtime',
      () {
    const solver = AvatarSemanticMotionSolver();
    final scheduler = AvatarMotionScheduler();
    final graph = AvatarRendererPerformanceGraph();
    final first = _viseme('viseme-1', AvatarViseme.aa, Duration.zero);
    final started = scheduler.submit(first, now: Duration.zero).activeAction!;
    graph.start(
      action: started,
      performance: solver.solve(first),
      timelineAt: Duration.zero,
    );
    final next = _viseme(
      'viseme-2',
      AvatarViseme.oh,
      const Duration(milliseconds: 70),
    );
    final result = scheduler.submit(
      next,
      now: const Duration(milliseconds: 70),
    );

    expect(result.type, AvatarScheduleResultType.retargeted);
    expect(result.activeAction!.runtimeId, 'viseme-1');
    expect(result.activeAction!.startedAt, Duration.zero);
    expect(result.activeAction!.revision, 1);

    final plan = graph.retarget(
      action: result.activeAction!,
      performance: solver.solve(next),
      timelineAt: const Duration(milliseconds: 70),
    );
    expect(graph.activeCount, 1);
    expect(plan.actions.single.runtimeId, 'viseme-1');
    expect(plan.actions.single.revision, 1);
    expect(plan.morphMotion!.targetNames, contains('Fcl_MTH_O'));
  });

  test('segment retarget reconciles a native trajectory that just expired', () {
    const solver = AvatarSemanticMotionSolver();
    final scheduler = AvatarMotionScheduler();
    final graph = AvatarRendererPerformanceGraph();
    final first = _intent(
      id: 'segment-gaze-1',
      kind: AvatarActionKind.lookAt,
      source: AvatarActionSource.lesson,
      priority: AvatarActionPriority.lesson,
      claims: const {
        AvatarMotionChannel.gaze: AvatarMotionBlendMode.override,
      },
      expectedDuration: const Duration(milliseconds: 100),
      retargetGroup: 'response:gaze',
      retargetPolicy: AvatarRetargetPolicy.immediate,
      textParameters: const {'gaze': 'child'},
    );
    final started = scheduler.submit(first, now: Duration.zero).activeAction!;
    graph.start(
      action: started,
      performance: solver.solve(first),
      timelineAt: Duration.zero,
    );
    graph.advance(const Duration(milliseconds: 150));
    expect(graph.containsRuntime(first.id), isFalse);

    final next = _intent(
      id: 'segment-gaze-2',
      kind: AvatarActionKind.lookAt,
      source: AvatarActionSource.lesson,
      priority: AvatarActionPriority.lesson,
      claims: const {
        AvatarMotionChannel.gaze: AvatarMotionBlendMode.override,
      },
      occurredAt: const Duration(milliseconds: 150),
      expectedDuration: const Duration(milliseconds: 300),
      retargetGroup: 'response:gaze',
      retargetPolicy: AvatarRetargetPolicy.immediate,
      textParameters: const {'gaze': 'diagram'},
    );
    final retargeted = scheduler.submit(
      next,
      now: const Duration(milliseconds: 150),
    );
    expect(retargeted.type, AvatarScheduleResultType.retargeted);

    final plan = graph.reconcileRetarget(
      action: retargeted.activeAction!,
      performance: solver.solve(next),
      timelineAt: const Duration(milliseconds: 150),
    );
    expect(graph.containsRuntime(first.id), isTrue);
    expect(plan.actions.single.runtimeId, first.id);
    expect(plan.actions.single.revision, 1);
  });

  test('response gestures articulate in distinct primary and secondary beats',
      () {
    const solver = AvatarSemanticMotionSolver();
    final acknowledge = solver.solve(_gestureIntent(
      id: 'acknowledge-beat',
      gesture: 'acknowledge',
    ));
    final explain = solver.solve(_gestureIntent(
      id: 'explain-beat',
      gesture: 'explainSmall',
    ));
    final acknowledgeFrames = acknowledge.skeletalMotion!.frames;
    final explainFrames = explain.skeletalMotion!.frames;
    double lowerArmAt(List<AvatarProceduralPoseFrame> frames, double progress) {
      final index = (progress * (frames.length - 1)).round();
      return frames[index].bones[AvatarRigBone.rightLowerArm]!.rotationX;
    }

    expect(lowerArmAt(explainFrames, 0), closeTo(0, 1e-12));
    expect(lowerArmAt(explainFrames, 1), closeTo(0, 1e-12));
    expect(
      lowerArmAt(explainFrames, 0.70).abs(),
      greaterThan(lowerArmAt(explainFrames, 0.61).abs()),
    );
    expect(
      lowerArmAt(explainFrames, 0.35),
      isNot(closeTo(lowerArmAt(acknowledgeFrames, 0.35), 1e-9)),
    );
  });

  test('early cancellation produces a bounded blend-out and settles cleanly',
      () {
    const solver = AvatarSemanticMotionSolver();
    final graph = AvatarRendererPerformanceGraph();
    final gaze = _intent(
      id: 'cancelled-gaze',
      kind: AvatarActionKind.lookAt,
      source: AvatarActionSource.lesson,
      priority: AvatarActionPriority.lesson,
      claims: const {
        AvatarMotionChannel.gaze: AvatarMotionBlendMode.override,
      },
      expectedDuration: const Duration(milliseconds: 900),
      textParameters: const {'gaze': 'thinkingAway'},
    );
    graph.start(
      action: AvatarScheduledAction.start(gaze, Duration.zero),
      performance: solver.solve(gaze),
      timelineAt: Duration.zero,
    );

    final fading = graph.remove(
      runtimeId: gaze.id,
      timelineAt: const Duration(milliseconds: 300),
      blendOut: const Duration(milliseconds: 120),
    );

    expect(graph.activeCount, 0);
    expect(fading.actions.single.runtimeId, gaze.id);
    final headFrames = fading.skeletalMotion!.frames
        .map((frame) => frame.bones[AvatarRigBone.head]!)
        .toList(growable: false);
    expect(headFrames.first.rotationY.abs(), greaterThan(0));
    expect(headFrames.last.rotationX, closeTo(0, 1e-12));
    expect(headFrames.last.rotationY, closeTo(0, 1e-12));
    expect(headFrames.last.rotationZ, closeTo(0, 1e-12));

    final settled = graph.advance(const Duration(milliseconds: 420));
    expect(settled.actions, isEmpty);
    expect(settled.skeletalMotion, isNull);
    expect(settled.morphMotion, isNull);
  });

  test('additive bone layers are numerically composed over one override owner',
      () {
    final graph = AvatarRendererPerformanceGraph();
    final base = _intent(
      id: 'base-spine',
      kind: AvatarActionKind.explain,
      source: AvatarActionSource.lesson,
      priority: AvatarActionPriority.lesson,
      claims: const {
        AvatarMotionChannel.spine: AvatarMotionBlendMode.override,
      },
      expectedDuration: const Duration(milliseconds: 100),
    );
    final breath = _intent(
      id: 'additive-spine',
      kind: AvatarActionKind.secondaryMotion,
      source: AvatarActionSource.idleSystem,
      priority: AvatarActionPriority.idle,
      claims: const {
        AvatarMotionChannel.spine: AvatarMotionBlendMode.additive,
      },
      expectedDuration: const Duration(milliseconds: 100),
    );
    graph.start(
      action: AvatarScheduledAction.start(base, Duration.zero),
      performance: _constantSpinePerformance(0.1),
      timelineAt: Duration.zero,
    );
    final plan = graph.start(
      action: AvatarScheduledAction.start(breath, Duration.zero),
      performance: _constantSpinePerformance(0.2),
      timelineAt: Duration.zero,
    );

    expect(
      plan.skeletalMotion!.frames.first.bones[AvatarRigBone.spine]!.rotationX,
      closeTo(0.3, 1e-12),
    );
    expect(
      plan.skeletalMotion!.frames.first.bones.keys
          .where((bone) => bone == AvatarRigBone.spine),
      hasLength(1),
    );
  });

  test('unregistered unrestricted movement fails loudly', () {
    const solver = AvatarSemanticMotionSolver();
    final dance = _intent(
      id: 'dance',
      kind: AvatarActionKind.dance,
      source: AvatarActionSource.command,
      priority: AvatarActionPriority.command,
      claims: const {
        AvatarMotionChannel.basePose: AvatarMotionBlendMode.override,
      },
      expectedDuration: const Duration(seconds: 2),
    );

    expect(() => solver.solve(dance), throwsUnsupportedError);
  });
}

AvatarLayerPerformance _constantSpinePerformance(double rotationX) =>
    AvatarLayerPerformance(
      skeletalMotion: AvatarProceduralMotion(
        frameLengthMilliseconds: 100,
        frames: [
          for (var index = 0; index < 2; index += 1)
            AvatarProceduralPoseFrame({
              AvatarRigBone.spine: AvatarBonePoseDelta(rotationX: rotationX),
            }),
        ],
        maximumBlend: 1,
      ),
    );

AvatarActionIntent _viseme(
  String id,
  AvatarViseme viseme,
  Duration occurredAt,
) =>
    _intent(
      id: id,
      kind: AvatarActionKind.lipSync,
      source: AvatarActionSource.speech,
      priority: AvatarActionPriority.lesson,
      claims: const {
        AvatarMotionChannel.lipSync: AvatarMotionBlendMode.override,
      },
      occurredAt: occurredAt,
      expectedDuration: const Duration(milliseconds: 180) - occurredAt,
      retargetGroup: 'speech:lipSync',
      retargetPolicy: AvatarRetargetPolicy.immediate,
      target: _lipSyncTarget(viseme, audioPosition: occurredAt),
    );

AvatarLipSyncTarget _lipSyncTarget(
  AvatarViseme viseme, {
  Duration audioPosition = Duration.zero,
}) =>
    AvatarLipSyncTarget(
      audioDuration: const Duration(milliseconds: 180),
      audioPosition: audioPosition,
      cues: [
        AvatarVisemeCue(
          viseme: viseme,
          start: Duration.zero,
          end: const Duration(milliseconds: 165),
          weight: 0.8,
        ),
      ],
    );

AvatarActionIntent _gestureIntent({
  required String id,
  required String gesture,
}) =>
    _intent(
      id: id,
      kind: AvatarActionKind.explain,
      source: AvatarActionSource.lesson,
      priority: AvatarActionPriority.lesson,
      claims: const {
        AvatarMotionChannel.rightArm: AvatarMotionBlendMode.override,
        AvatarMotionChannel.rightHand: AvatarMotionBlendMode.additive,
        AvatarMotionChannel.spine: AvatarMotionBlendMode.additive,
      },
      expectedDuration: const Duration(milliseconds: 1600),
      textParameters: {'gesture': gesture},
    );

AvatarActionIntent _touchIntent() => AvatarActionIntent(
      id: 'left-leg-touch',
      kind: AvatarActionKind.recoil,
      source: AvatarActionSource.touch,
      priority: AvatarActionPriority.touch,
      channelClaims: const {
        AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
      },
      occurredAt: const Duration(milliseconds: 50),
      blendIn: const Duration(milliseconds: 70),
      blendOut: const Duration(milliseconds: 260),
      expectedDuration: const Duration(milliseconds: 900),
      minimumHold: const Duration(milliseconds: 200),
      interruptionPolicy: AvatarInterruptionPolicy.afterMinimumHold,
      retargetPolicy: AvatarRetargetPolicy.immediate,
      retargetGroup: 'touch:leftLeg',
      target: AvatarContactTarget(
        anatomy: const AvatarAnatomicalContact(
          region: AvatarAnatomicalRegion.leftKnee,
          boneInfluences: [
            AvatarBoneInfluence(
              jointIndex: 0,
              jointName: 'test-joint',
              weight: 1,
            ),
          ],
          longitudinalPosition: 0.62,
          radialAngle: 0.4,
          surfaceSector: AvatarSurfaceSector.front,
        ),
        modelPosition: const AvatarModelVector3(0.1, 0.5, 0.05),
        modelNormal: const AvatarModelVector3(0, 0, 1),
        spatialContext: AvatarSpatialContext(
          cameraAzimuth: 0,
          cameraElevation: 0.2,
          avatarRootYaw: 0,
        ),
      ),
      numericParameters: const {
        'strength': 0.88,
        'relativeViewAzimuth': 0,
      },
      textParameters: const {'touchStage': 'notice'},
    );

AvatarActionIntent _intent({
  required String id,
  required AvatarActionKind kind,
  required AvatarActionSource source,
  required AvatarActionPriority priority,
  required Map<AvatarMotionChannel, AvatarMotionBlendMode> claims,
  required Duration? expectedDuration,
  Duration occurredAt = Duration.zero,
  AvatarRetargetPolicy retargetPolicy = AvatarRetargetPolicy.never,
  String? retargetGroup,
  Map<String, double> numericParameters = const {},
  Map<String, String> textParameters = const {},
  AvatarActionTarget? target,
}) =>
    AvatarActionIntent(
      id: id,
      kind: kind,
      source: source,
      priority: priority,
      channelClaims: claims,
      occurredAt: occurredAt,
      blendIn: const Duration(milliseconds: 45),
      blendOut: const Duration(milliseconds: 120),
      expectedDuration: expectedDuration,
      interruptionPolicy: AvatarInterruptionPolicy.interruptible,
      retargetPolicy: retargetPolicy,
      retargetGroup: retargetGroup,
      target: target,
      numericParameters: numericParameters,
      textParameters: textParameters,
    );
