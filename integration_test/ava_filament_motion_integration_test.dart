import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import 'package:ava/config/ava_assets.dart';
import 'package:ava/ui/avatar/ava_filament_viewer.dart';
import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_anatomy.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
      'Filament accepts living idle and concurrent full-body touch layers',
      (tester) async {
    final controller = AvaFilamentController();
    Object? failure;
    StackTrace? failureStack;
    var motionAccepted = false;

    await tester.pumpWidget(
      MaterialApp(
        home: AvaFilamentViewer(
          controller: controller,
          src: AvaAssets.avatarModel,
          anatomyMapAsset: AvaAssets.avatarTouchMap,
          rigBindingsAsset: AvaAssets.avatarRigBindings,
          onAnatomicalTap: (_) {},
          onAnimationFinished: (_) {},
          onLoad: (_) {
            unawaited(
              (() async {
                await controller.startLivingIdle();
                await controller.playLowerBodyAction(_rightThighIntent());
                await controller.playProceduralTouchAction(_chestIntent());
                await controller.playProceduralTouchAction(
                  _upperIntent(
                    region: AvatarAnatomicalRegion.head,
                    stage: AvatarTouchStage.notice,
                    occurredAt: const Duration(seconds: 3),
                  ),
                );
                await controller.playProceduralTouchAction(
                  _upperIntent(
                    region: AvatarAnatomicalRegion.leftShoulder,
                    stage: AvatarTouchStage.notice,
                    occurredAt: const Duration(milliseconds: 3090),
                  ),
                );
                await controller.playProceduralTouchAction(
                  _upperIntent(
                    region: AvatarAnatomicalRegion.rightShoulder,
                    stage: AvatarTouchStage.notice,
                    occurredAt: const Duration(milliseconds: 3120),
                  ),
                );
                await controller.playProceduralTouchAction(
                  _upperIntent(
                    region: AvatarAnatomicalRegion.leftUpperArm,
                    stage: AvatarTouchStage.notice,
                    occurredAt: const Duration(milliseconds: 3150),
                  ),
                );
                await controller.playProceduralTouchAction(
                  _upperIntent(
                    region: AvatarAnatomicalRegion.leftHand,
                    stage: AvatarTouchStage.notice,
                    occurredAt: const Duration(milliseconds: 3180),
                  ),
                );
                await controller.playProceduralTouchAction(
                  _upperIntent(
                    region: AvatarAnatomicalRegion.rightUpperArm,
                    stage: AvatarTouchStage.notice,
                    occurredAt: const Duration(milliseconds: 3240),
                  ),
                );
                await controller.playProceduralTouchAction(
                  _upperIntent(
                    region: AvatarAnatomicalRegion.leftLowerArm,
                    stage: AvatarTouchStage.notice,
                    occurredAt: const Duration(milliseconds: 3300),
                  ),
                );
                await controller.playProceduralTouchAction(
                  _upperIntent(
                    region: AvatarAnatomicalRegion.rightLowerArm,
                    stage: AvatarTouchStage.notice,
                    occurredAt: const Duration(milliseconds: 3330),
                  ),
                );
                await controller.playProceduralTouchAction(
                  _upperIntent(
                    region: AvatarAnatomicalRegion.leftHand,
                    stage: AvatarTouchStage.acknowledge,
                    occurredAt: const Duration(milliseconds: 3380),
                  ),
                );
                await controller.playProceduralTouchAction(
                  _upperIntent(
                    region: AvatarAnatomicalRegion.rightHand,
                    stage: AvatarTouchStage.notice,
                    occurredAt: const Duration(milliseconds: 3550),
                  ),
                );
                motionAccepted = true;
              })()
                  .catchError((Object error, StackTrace stackTrace) {
                failure = error;
                failureStack = stackTrace;
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
        attempt < 600 && !motionAccepted && failure == null;
        attempt += 1) {
      await tester.pump(const Duration(milliseconds: 50));
    }

    if (failure != null) {
      Error.throwWithStackTrace(failure!, failureStack!);
    }
    expect(
      motionAccepted,
      isTrue,
      reason: 'Filament did not accept Ava\'s procedural motion in 30 seconds.',
    );

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(milliseconds: 350));
    controller.dispose();
  });
}

AvatarActionIntent _upperIntent({
  required AvatarAnatomicalRegion region,
  required AvatarTouchStage stage,
  required Duration occurredAt,
}) {
  final isHand = region == AvatarAnatomicalRegion.leftHand ||
      region == AvatarAnatomicalRegion.rightHand;
  return AvatarActionIntent(
    id: 'integration-${region.name}-${stage.name}',
    kind: isHand ? AvatarActionKind.handPose : AvatarActionKind.recoil,
    source: AvatarActionSource.touch,
    priority: AvatarActionPriority.touch,
    channelClaims: const {
      AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
    },
    occurredAt: occurredAt,
    blendIn: const Duration(milliseconds: 55),
    blendOut: const Duration(milliseconds: 210),
    expectedDuration: const Duration(milliseconds: 760),
    minimumHold: const Duration(milliseconds: 120),
    interruptionPolicy: AvatarInterruptionPolicy.afterMinimumHold,
    retargetPolicy: AvatarRetargetPolicy.afterMinimumHold,
    retargetGroup: 'integration:${region.name}',
    target: AvatarContactTarget(
      anatomy: AvatarAnatomicalContact(
        region: region,
        boneInfluences: const [
          AvatarBoneInfluence(
            jointIndex: 1,
            jointName: 'J_Bip_C_Head',
            weight: 1,
          ),
        ],
        longitudinalPosition: 0.5,
        radialAngle: 0.2,
        surfaceSector: AvatarSurfaceSector.front,
      ),
      modelPosition: AvatarModelVector3(
        region == AvatarAnatomicalRegion.rightHand ? -0.2 : 0.2,
        isHand ? 0.92 : 1.48,
        0.08,
      ),
      modelNormal: const AvatarModelVector3(0, 0, 1),
      spatialContext: AvatarSpatialContext(
        cameraAzimuth: 0,
        cameraElevation: 0.2,
        avatarRootYaw: 0,
      ),
    ),
    numericParameters: const {'strength': 0.88, 'streak': 1},
    textParameters: {'touchStage': stage.name},
  );
}

AvatarActionIntent _chestIntent() {
  return AvatarActionIntent(
    id: 'integration-chest',
    kind: AvatarActionKind.protectiveRedirect,
    source: AvatarActionSource.touch,
    priority: AvatarActionPriority.touch,
    channelClaims: const {
      AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
      AvatarMotionChannel.spine: AvatarMotionBlendMode.override,
    },
    occurredAt: const Duration(seconds: 2),
    blendIn: const Duration(milliseconds: 75),
    blendOut: const Duration(milliseconds: 260),
    expectedDuration: const Duration(milliseconds: 900),
    minimumHold: const Duration(milliseconds: 320),
    interruptionPolicy: AvatarInterruptionPolicy.afterMinimumHold,
    retargetPolicy: AvatarRetargetPolicy.afterMinimumHold,
    retargetGroup: 'integration:torso',
    target: AvatarContactTarget(
      anatomy: const AvatarAnatomicalContact(
        region: AvatarAnatomicalRegion.chest,
        boneInfluences: [
          AvatarBoneInfluence(
            jointIndex: 1,
            jointName: 'J_Bip_C_Chest',
            weight: 1,
          ),
        ],
        longitudinalPosition: 0.5,
        radialAngle: 0,
        surfaceSector: AvatarSurfaceSector.front,
      ),
      modelPosition: const AvatarModelVector3(0.03, 1.2, 0.08),
      modelNormal: const AvatarModelVector3(0, 0, 1),
      spatialContext: AvatarSpatialContext(
        cameraAzimuth: 0,
        cameraElevation: 0.2,
        avatarRootYaw: 0,
      ),
    ),
    numericParameters: const {
      'strength': 0.88,
      'streak': 2,
    },
    textParameters: const {'touchStage': 'acknowledge'},
  );
}

AvatarActionIntent _rightThighIntent() {
  return AvatarActionIntent(
    id: 'integration-right-thigh',
    kind: AvatarActionKind.recoil,
    source: AvatarActionSource.touch,
    priority: AvatarActionPriority.touch,
    channelClaims: const {
      AvatarMotionChannel.rightLeg: AvatarMotionBlendMode.override,
      AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
    },
    occurredAt: const Duration(seconds: 1),
    blendIn: const Duration(milliseconds: 70),
    blendOut: const Duration(milliseconds: 260),
    expectedDuration: const Duration(milliseconds: 900),
    minimumHold: const Duration(milliseconds: 200),
    interruptionPolicy: AvatarInterruptionPolicy.afterMinimumHold,
    retargetPolicy: AvatarRetargetPolicy.immediate,
    retargetGroup: 'integration:rightLeg',
    target: AvatarContactTarget(
      anatomy: const AvatarAnatomicalContact(
        region: AvatarAnatomicalRegion.rightThigh,
        boneInfluences: [
          AvatarBoneInfluence(
            jointIndex: 1,
            jointName: 'J_Bip_R_UpperLeg',
            weight: 1,
          ),
        ],
        longitudinalPosition: 0.58,
        radialAngle: -0.55,
        surfaceSector: AvatarSurfaceSector.front,
      ),
      modelPosition: const AvatarModelVector3(-0.1, 0.7, 0.06),
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
    textParameters: const {'touchStage': 'acknowledge'},
  );
}
