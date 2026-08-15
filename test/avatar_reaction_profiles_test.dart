import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_anatomy.dart';
import 'package:ava/ui/avatar/avatar_reaction_profiles.dart';

void main() {
  late AvatarReactionMotionProfileCatalog catalog;

  setUpAll(() {
    catalog = AvatarReactionMotionProfileCatalog.fromJsonString(
      File('avatar/ava_reaction_motion_profiles.json').readAsStringSync(),
    );
  });

  test('production profile covers every anatomical region', () {
    for (final region in AvatarAnatomicalRegion.values) {
      final actions = catalog.composeTouchActions(
        sequence: region.index + 1,
        intensity: AvatarReactionIntensity.normal,
        target: _target(region),
        occurredAt: Duration(milliseconds: region.index),
        numericParameters: const {'strength': 0.8},
        textParameters: {'region': region.name},
      );

      expect(actions, isNotEmpty, reason: region.name);
      expect(
        actions.every((action) => action.target is AvatarContactTarget),
        isTrue,
        reason: region.name,
      );
    }
  });

  test('repeated reactions scale time without changing layer claims', () {
    final subtle = catalog.composeTouchActions(
      sequence: 1,
      intensity: AvatarReactionIntensity.subtle,
      target: _target(AvatarAnatomicalRegion.leftLowerLeg),
      occurredAt: Duration.zero,
      numericParameters: const {'strength': 0.7},
      textParameters: const {'region': 'leftLowerLeg'},
    );
    final repeated = catalog.composeTouchActions(
      sequence: 2,
      intensity: AvatarReactionIntensity.repeated,
      target: _target(AvatarAnatomicalRegion.leftLowerLeg),
      occurredAt: const Duration(milliseconds: 200),
      numericParameters: const {'strength': 1},
      textParameters: const {'region': 'leftLowerLeg'},
    );

    expect(
      repeated.first.expectedDuration,
      greaterThan(subtle.first.expectedDuration!),
    );
    expect(repeated.first.channelClaims, subtle.first.channelClaims);
  });

  test('torso reactions use the shortened bounded response window', () {
    final subtle = catalog.composeTouchActions(
      sequence: 1,
      intensity: AvatarReactionIntensity.subtle,
      target: _target(AvatarAnatomicalRegion.chest),
      occurredAt: Duration.zero,
      numericParameters: const {'strength': 0.72},
      textParameters: const {'region': 'chest'},
    );
    final repeated = catalog.composeTouchActions(
      sequence: 2,
      intensity: AvatarReactionIntensity.repeated,
      target: _target(AvatarAnatomicalRegion.chest),
      occurredAt: const Duration(milliseconds: 300),
      numericParameters: const {'strength': 1},
      textParameters: const {'region': 'chest'},
    );

    expect(
      subtle.first.expectedDuration,
      const Duration(milliseconds: 653, microseconds: 600),
    );
    expect(
      repeated.first.expectedDuration,
      const Duration(milliseconds: 881, microseconds: 600),
    );
    expect(repeated.first.blendIn, const Duration(milliseconds: 90));
    expect(repeated.first.blendOut, const Duration(milliseconds: 220));
    expect(
      repeated.first.channelClaims,
      const {
        AvatarMotionChannel.posture: AvatarMotionBlendMode.additive,
        AvatarMotionChannel.spine: AvatarMotionBlendMode.override,
      },
    );
  });

  test('malformed or incomplete profile data fails loudly', () {
    expect(
      () => AvatarReactionMotionProfileCatalog.fromJsonString('{}'),
      throwsFormatException,
    );
  });
}

AvatarContactTarget _target(AvatarAnatomicalRegion region) {
  return AvatarContactTarget(
    anatomy: AvatarAnatomicalContact(
      region: region,
      boneInfluences: const [
        AvatarBoneInfluence(
          jointIndex: 0,
          jointName: 'TestJoint',
          weight: 1,
        ),
      ],
      longitudinalPosition: 0.5,
      radialAngle: 0,
      surfaceSector: AvatarSurfaceSector.front,
    ),
    modelPosition: const AvatarModelVector3(0, 1, 0),
    modelNormal: const AvatarModelVector3(0, 0, 1),
    spatialContext: AvatarSpatialContext(
      cameraAzimuth: 0,
      cameraElevation: 1.43,
      avatarRootYaw: 0,
    ),
  );
}
