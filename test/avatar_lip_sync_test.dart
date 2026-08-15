import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_semantic_motion_solver.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('rejects ambiguous or out-of-range speech timing', () {
    expect(
      () => AvatarLipSyncTarget(
        audioDuration: const Duration(milliseconds: 400),
        cues: [
          _cue(
            AvatarViseme.aa,
            const Duration(milliseconds: 20),
            const Duration(milliseconds: 220),
          ),
          _cue(
            AvatarViseme.oh,
            const Duration(milliseconds: 180),
            const Duration(milliseconds: 360),
          ),
        ],
      ),
      throwsArgumentError,
    );
    expect(
      () => AvatarLipSyncTarget(
        audioDuration: const Duration(milliseconds: 300),
        cues: [
          _cue(
            AvatarViseme.ee,
            Duration.zero,
            const Duration(milliseconds: 320),
          ),
        ],
      ),
      throwsArgumentError,
    );
    expect(
      () => AvatarLipSyncTarget(
        audioDuration: const Duration(milliseconds: 500),
        audioPosition: const Duration(milliseconds: 460),
        cues: [
          _cue(
            AvatarViseme.aa,
            Duration.zero,
            const Duration(milliseconds: 420),
          ),
        ],
      ),
      throwsArgumentError,
    );
  });

  test('builds one coarticulated multi-target utterance trajectory', () {
    const solver = AvatarSemanticMotionSolver(framesPerSecond: 100);
    final performance = solver.solve(
      _lipSyncIntent(_timeline()),
    );
    final motion = performance.morphMotion!;

    expect(motion.targetNames, const [
      'Fcl_MTH_A',
      'Fcl_MTH_I',
      'Fcl_MTH_U',
      'Fcl_MTH_E',
      'Fcl_MTH_O',
      'Fcl_MTH_Close',
    ]);
    expect(motion.frames.length, greaterThan(40));
    expect(motion.frames.first.every((value) => value == 0), isTrue);
    expect(motion.frames.last.every((value) => value == 0), isTrue);
    expect(
      motion.frames.any((frame) => frame[AvatarViseme.aa.index] > 0.5),
      isTrue,
    );
    expect(
      motion.frames.any(
        (frame) =>
            frame[AvatarViseme.aa.index] > 0.05 &&
            frame[AvatarViseme.ee.index] > 0.05,
      ),
      isTrue,
    );

    var maximumStep = 0.0;
    for (var frame = 1; frame < motion.frames.length; frame += 1) {
      for (var target = 0; target < motion.targetNames.length; target += 1) {
        final step =
            (motion.frames[frame][target] - motion.frames[frame - 1][target])
                .abs();
        if (step > maximumStep) maximumStep = step;
      }
    }
    expect(maximumStep, lessThan(0.24));
  });

  test('sample-clock correction resumes the same mouth pose', () {
    const solver = AvatarSemanticMotionSolver(framesPerSecond: 100);
    final full = solver.solve(_lipSyncIntent(_timeline())).morphMotion!;
    final correctedTarget = _timeline(
      audioPosition: const Duration(milliseconds: 230),
    );
    final corrected =
        solver.solve(_lipSyncIntent(correctedTarget)).morphMotion!;

    final fullFrame = full.frames[23];
    final correctedFrame = corrected.frames.first;
    for (var target = 0; target < full.targetNames.length; target += 1) {
      expect(correctedFrame[target], closeTo(fullFrame[target], 1e-9));
    }
  });

  test('speech micro-motion is subtle and returns to neutral', () {
    const solver = AvatarSemanticMotionSolver(framesPerSecond: 60);
    final performance = solver.solve(
      AvatarActionIntent(
        id: 'speech-secondary',
        kind: AvatarActionKind.secondaryMotion,
        source: AvatarActionSource.speech,
        priority: AvatarActionPriority.lesson,
        channelClaims: const {
          AvatarMotionChannel.spine: AvatarMotionBlendMode.additive,
          AvatarMotionChannel.neck: AvatarMotionBlendMode.additive,
          AvatarMotionChannel.head: AvatarMotionBlendMode.additive,
        },
        occurredAt: Duration.zero,
        blendIn: const Duration(milliseconds: 180),
        blendOut: const Duration(milliseconds: 220),
        expectedDuration: const Duration(seconds: 2),
        interruptionPolicy: AvatarInterruptionPolicy.interruptible,
        retargetPolicy: AvatarRetargetPolicy.immediate,
        retargetGroup: 'speech:secondary',
        textParameters: const {
          'profile': 'speechSubtle',
          'delivery': 'warm',
        },
        numericParameters: const {'energy': 0.42},
      ),
    );
    final motion = performance.skeletalMotion!;

    expect(
      motion.frames.first.bones.values.every((pose) => pose.isNeutral),
      isTrue,
    );
    expect(
      motion.frames.last.bones.values.every(
        (pose) =>
            pose.rotationX.abs() < 1e-12 &&
            pose.rotationY.abs() < 1e-12 &&
            pose.rotationZ.abs() < 1e-12,
      ),
      isTrue,
    );
    for (final frame in motion.frames) {
      for (final pose in frame.bones.values) {
        expect(pose.rotationX.abs(), lessThan(0.01));
        expect(pose.rotationY.abs(), lessThan(0.01));
        expect(pose.rotationZ.abs(), lessThan(0.01));
      }
    }
  });
}

AvatarActionIntent _lipSyncIntent(AvatarLipSyncTarget target) =>
    AvatarActionIntent(
      id: 'speech-lip-sync-${target.audioPosition.inMilliseconds}',
      kind: AvatarActionKind.lipSync,
      source: AvatarActionSource.speech,
      priority: AvatarActionPriority.lesson,
      channelClaims: const {
        AvatarMotionChannel.lipSync: AvatarMotionBlendMode.override,
      },
      occurredAt: Duration.zero,
      blendIn: const Duration(milliseconds: 45),
      blendOut: const Duration(milliseconds: 70),
      expectedDuration: target.remainingDuration,
      interruptionPolicy: AvatarInterruptionPolicy.interruptible,
      retargetPolicy: AvatarRetargetPolicy.immediate,
      retargetGroup: 'speech:lipSync',
      target: target,
    );

AvatarLipSyncTarget _timeline({
  Duration audioPosition = Duration.zero,
}) =>
    AvatarLipSyncTarget(
      audioDuration: const Duration(milliseconds: 600),
      audioPosition: audioPosition,
      cues: [
        _cue(
          AvatarViseme.aa,
          const Duration(milliseconds: 40),
          const Duration(milliseconds: 210),
          weight: 0.82,
        ),
        _cue(
          AvatarViseme.ee,
          const Duration(milliseconds: 210),
          const Duration(milliseconds: 380),
          weight: 0.74,
        ),
        _cue(
          AvatarViseme.closed,
          const Duration(milliseconds: 380),
          const Duration(milliseconds: 520),
          weight: 0.7,
        ),
      ],
    );

AvatarVisemeCue _cue(
  AvatarViseme viseme,
  Duration start,
  Duration end, {
  double weight = 0.8,
}) =>
    AvatarVisemeCue(
      viseme: viseme,
      start: start,
      end: end,
      weight: weight,
    );
