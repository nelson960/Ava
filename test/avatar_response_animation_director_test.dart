import 'package:ava/models/ava_performance_plan.dart';
import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_motion_scheduler.dart';
import 'package:ava/ui/avatar/avatar_response_animation_director.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('keeps response segments on living idle and dispatches only lip sync',
      () async {
    var now = const Duration(seconds: 4);
    final dispatched = <AvatarActionIntent>[];
    final cancelled = <AvatarActionSource>[];
    final director = AvatarResponseAnimationDirector(
      now: () => now,
      dispatch: (intent, dispatchAt) async {
        expect(dispatchAt, now);
        dispatched.add(intent);
      },
      cancelSource: (source, cancelAt) async {
        expect(cancelAt, now);
        cancelled.add(source);
      },
    );

    director.beginTurn('turn_17');
    await director.startSegment(
      _segment(),
      expectedDuration: const Duration(seconds: 2),
    );

    expect(dispatched, isEmpty);

    now += const Duration(milliseconds: 120);
    await director.synchronizeLipSync(
      _lipSyncTarget(),
    );
    now += const Duration(milliseconds: 90);
    await director.synchronizeLipSync(
      _lipSyncTarget(audioPosition: const Duration(milliseconds: 180)),
    );

    final lipSync = dispatched
        .where((intent) => intent.kind == AvatarActionKind.lipSync)
        .toList(growable: false);
    expect(lipSync, hasLength(2));
    expect(lipSync[0].id, isNot(lipSync[1].id));
    expect(lipSync[0].retargetGroup, lipSync[1].retargetGroup);
    expect(lipSync[0].source, AvatarActionSource.speech);
    expect(
      (lipSync[1].target! as AvatarLipSyncTarget).audioPosition,
      const Duration(milliseconds: 180),
    );

    await director.finishTurn();
    expect(cancelled, [AvatarActionSource.speech]);
    expect(director.isActive, isFalse);
  });

  test('a response keeps non-conflicting touch channels immediately usable',
      () async {
    var now = Duration.zero;
    final scheduler = AvatarMotionScheduler();
    final results = <AvatarScheduleResult>[];
    final director = AvatarResponseAnimationDirector(
      now: () => now,
      dispatch: (intent, dispatchAt) async {
        results.add(scheduler.submit(intent, now: dispatchAt));
      },
      cancelSource: (source, cancelAt) async {
        scheduler.cancelSource(source, now: cancelAt);
      },
    );
    director.beginTurn('touch_composition');
    await director.startSegment(
      _segment(),
      expectedDuration: const Duration(seconds: 2),
    );
    await director.synchronizeLipSync(_lipSyncTarget());
    now = const Duration(milliseconds: 40);

    final touch = AvatarActionIntent(
      id: 'left_leg_touch',
      kind: AvatarActionKind.weightShift,
      source: AvatarActionSource.touch,
      priority: AvatarActionPriority.touch,
      channelClaims: const {
        AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        AvatarMotionChannel.posture: AvatarMotionBlendMode.additive,
      },
      occurredAt: now,
      blendIn: const Duration(milliseconds: 80),
      blendOut: const Duration(milliseconds: 140),
      expectedDuration: const Duration(milliseconds: 600),
      interruptionPolicy: AvatarInterruptionPolicy.interruptible,
      retargetPolicy: AvatarRetargetPolicy.never,
    );
    final touchResult = scheduler.submit(touch, now: now);

    expect(results, hasLength(1));
    expect(
        results
            .every((result) => result.type == AvatarScheduleResultType.started),
        isTrue);
    expect(touchResult.type, AvatarScheduleResultType.started);
    expect(scheduler.activeActions, hasLength(2));
  });

  test('validated canvas attention composes with sample-clock lip sync',
      () async {
    final dispatched = <AvatarActionIntent>[];
    final director = AvatarResponseAnimationDirector(
      now: () => const Duration(seconds: 1),
      dispatch: (intent, now) async => dispatched.add(intent),
      cancelSource: (source, now) async {},
    );

    director.beginTurn('canvas_with_speech');
    await director.synchronizeLipSync(_lipSyncTarget());
    await director.presentValidatedCanvas();

    expect(dispatched, hasLength(2));
    expect(dispatched[0].kind, AvatarActionKind.lipSync);
    expect(dispatched[1].kind, AvatarActionKind.lookAt);
    expect(dispatched[1].textParameters['gaze'], 'diagram');
    expect(
      dispatched[0].channelClaims.keys,
      isNot(contains(AvatarMotionChannel.gaze)),
    );
    expect(
      dispatched[1].channelClaims.keys,
      isNot(contains(AvatarMotionChannel.lipSync)),
    );
  });

  test('semantic gesture fields never dispatch body motion', () async {
    final dispatched = <AvatarActionIntent>[];
    final director = AvatarResponseAnimationDirector(
      now: () => Duration.zero,
      dispatch: (intent, now) async => dispatched.add(intent),
      cancelSource: (source, now) async {},
    );
    director.beginTurn('alternating_gestures');
    await director.startSegment(
      _segment(),
      expectedDuration: const Duration(seconds: 2),
    );
    await director.startSegment(
      const AvaPerformanceSegment(
        id: 'second_explanation',
        speech: 'Now look at the other part.',
        delivery: AvaDelivery.warm,
        expression: AvaExpressionIntent.warmSmile,
        gaze: AvaGazeIntent.child,
        gesture: AvaGestureIntent.explainSmall,
        diagramCue: null,
      ),
      expectedDuration: const Duration(seconds: 2),
    );

    expect(dispatched, isEmpty);
  });

  test('rejects lip sync without an active turn', () async {
    final director = AvatarResponseAnimationDirector(
      now: () => Duration.zero,
      dispatch: (intent, now) async {},
      cancelSource: (source, now) async {},
    );

    expect(
      () => director.startSegment(
        _segment(),
        expectedDuration: const Duration(seconds: 1),
      ),
      throwsStateError,
    );
    expect(
      () => director.synchronizeLipSync(_lipSyncTarget()),
      throwsStateError,
    );
  });
}

AvaPerformanceSegment _segment() => const AvaPerformanceSegment(
      id: 'explanation',
      speech: 'One half means one of two equal parts.',
      delivery: AvaDelivery.encouraging,
      expression: AvaExpressionIntent.encouraging,
      gaze: AvaGazeIntent.child,
      gesture: AvaGestureIntent.explainSmall,
      diagramCue: null,
    );

AvatarLipSyncTarget _lipSyncTarget({
  Duration audioPosition = Duration.zero,
}) =>
    AvatarLipSyncTarget(
      audioDuration: const Duration(milliseconds: 500),
      audioPosition: audioPosition,
      cues: [
        AvatarVisemeCue(
          viseme: AvatarViseme.aa,
          start: Duration.zero,
          end: const Duration(milliseconds: 240),
          weight: 0.8,
        ),
        AvatarVisemeCue(
          viseme: AvatarViseme.oh,
          start: const Duration(milliseconds: 240),
          end: const Duration(milliseconds: 460),
          weight: 0.72,
        ),
      ],
    );
