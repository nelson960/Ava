import 'dart:async';

import 'package:ava/config/ava_assets.dart';
import 'package:ava/models/ava_performance_plan.dart';
import 'package:ava/ui/avatar/ava_filament_motion_driver.dart';
import 'package:ava/ui/avatar/ava_filament_viewer.dart';
import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_performance_controller.dart';
import 'package:ava/ui/avatar/avatar_response_animation_director.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
      'native response survives continuous lip sync and an expired retarget',
      (tester) async {
    final filament = AvaFilamentController();
    final clock = Stopwatch()..start();
    Object? failure;
    StackTrace? failureStack;
    var completed = false;

    await tester.pumpWidget(
      MaterialApp(
        home: AvaFilamentViewer(
          controller: filament,
          src: AvaAssets.avatarModel,
          anatomyMapAsset: AvaAssets.avatarTouchMap,
          rigBindingsAsset: AvaAssets.avatarRigBindings,
          onAnatomicalTap: (_) {},
          onAnimationFinished: (_) {},
          onLoad: (_) {
            unawaited(
              (() async {
                final performance = AvatarPerformanceController(
                  driver: AvaFilamentMotionDriver(
                    sink: filament,
                    clock: () => clock.elapsed,
                  ),
                  onFailure: (error, stackTrace) {
                    failure ??= error;
                    failureStack ??= stackTrace;
                  },
                );
                await performance.initialize();
                await filament.startLivingIdle();
                final director = AvatarResponseAnimationDirector(
                  now: () => clock.elapsed,
                  dispatch: (intent, now) async {
                    await performance.dispatch(intent, now: now);
                  },
                  cancelSource: (source, now) async {
                    await performance.cancelSource(source, now: now);
                  },
                );

                director.beginTurn('native_boundary');
                await director.startSegment(
                  _segment(
                    id: 'intro',
                    expression: AvaExpressionIntent.warmSmile,
                    gesture: AvaGestureIntent.acknowledge,
                  ),
                  expectedDuration: const Duration(milliseconds: 700),
                );
                await _playLipSyncTimeline(director, 7);
                await director.startSegment(
                  _segment(
                    id: 'explanation',
                    expression: AvaExpressionIntent.encouraging,
                    gesture: AvaGestureIntent.explainSmall,
                  ),
                  expectedDuration: const Duration(milliseconds: 900),
                );
                await _playLipSyncTimeline(director, 9);
                await director.finishTurn();
                await performance.dispose();
                completed = true;
              })()
                  .catchError((Object error, StackTrace stackTrace) {
                failure ??= error;
                failureStack ??= stackTrace;
              }),
            );
          },
          onError: (message) {
            failure ??= StateError(message);
            failureStack ??= StackTrace.current;
          },
        ),
      ),
    );

    for (var attempt = 0;
        attempt < 600 && !completed && failure == null;
        attempt += 1) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    if (failure != null) {
      Error.throwWithStackTrace(failure!, failureStack!);
    }
    expect(
      completed,
      isTrue,
      reason: 'The native response performance did not finish in 30 seconds.',
    );

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(milliseconds: 350));
    filament.dispose();
  });
}

Future<void> _playLipSyncTimeline(
  AvatarResponseAnimationDirector director,
  int count,
) async {
  final duration = Duration(milliseconds: count * 100);
  await director.synchronizeLipSync(
    AvatarLipSyncTarget(
      audioDuration: duration,
      cues: [
        for (var index = 0; index < count; index += 1)
          AvatarVisemeCue(
            viseme: AvatarViseme.values[index % AvatarViseme.values.length],
            start: Duration(milliseconds: index * 100),
            end: Duration(milliseconds: (index + 1) * 100),
            weight: 0.72,
          ),
      ],
    ),
  );
  await Future<void>.delayed(duration);
  // Let the finite native trajectory retire before the next segment retargets
  // the scheduler-owned runtime. This covers the reconciliation boundary.
  await Future<void>.delayed(const Duration(milliseconds: 170));
}

AvaPerformanceSegment _segment({
  required String id,
  required AvaExpressionIntent expression,
  required AvaGestureIntent gesture,
}) =>
    AvaPerformanceSegment(
      id: id,
      speech: 'A bounded native response integration segment.',
      delivery: AvaDelivery.warm,
      expression: expression,
      gaze: AvaGazeIntent.child,
      gesture: gesture,
      diagramCue: null,
    );
