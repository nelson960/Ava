import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:ava/models/lesson_response.dart';
import 'package:ava/state/ava_session_state.dart';
import 'package:ava/ui/avatar/avatar_anatomy.dart';
import 'package:ava/ui/avatar/avatar_action_intent.dart';
import 'package:ava/ui/avatar/avatar_reaction_director.dart';
import 'package:ava/ui/avatar/avatar_reaction_profiles.dart';

void main() {
  late AvatarReactionDirector director;
  late AvatarReactionMotionProfileCatalog profiles;
  const start = Duration(seconds: 10);

  setUpAll(() {
    profiles = AvatarReactionMotionProfileCatalog.fromJsonString(
      File('avatar/ava_reaction_motion_profiles.json').readAsStringSync(),
    );
  });

  setUp(() {
    director = AvatarReactionDirector(profiles: profiles);
  });

  test('first contact is a restrained region-specific reaction', () {
    final decision = director.decide(
      contact: _contact(
        AvatarAnatomicalRegion.hair,
        start,
        modelX: 0.1,
      ),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.neutral,
    );

    expect(decision.family, AvatarReactionFamily.hairLeft);
    expect(decision.intensity, AvatarReactionIntensity.subtle);
    expect(decision.stage, AvatarTouchStage.notice);
    expect(decision.streak, 1);
    expect(decision.animation?.name, 'AvaReactHairLeftSubtle');
    expect(decision.animation?.playbackRate, 0.94);
    expect(
      decision.intents.map((intent) => intent.kind),
      containsAll(const [
        AvatarActionKind.recoil,
        AvatarActionKind.lookAt,
        AvatarActionKind.expression,
      ]),
    );
  });

  test('related repeated touches progress from subtle to normal to repeated',
      () {
    final decisions = <AvatarReactionDecision>[];
    for (var index = 0; index < 3; index += 1) {
      decisions.add(
        director.decide(
          contact: _contact(
            index == 0
                ? AvatarAnatomicalRegion.leftUpperArm
                : AvatarAnatomicalRegion.leftLowerArm,
            start + Duration(milliseconds: index * 300),
          ),
          phase: AvaSessionPhase.idle,
          expression: AvaExpression.neutral,
        ),
      );
    }

    expect(
      decisions.map((decision) => decision.intensity),
      const [
        AvatarReactionIntensity.subtle,
        AvatarReactionIntensity.normal,
        AvatarReactionIntensity.repeated,
      ],
    );
    expect(
      decisions.map((decision) => decision.stage),
      const [
        AvatarTouchStage.notice,
        AvatarTouchStage.acknowledge,
        AvatarTouchStage.redirect,
      ],
    );
    expect(decisions.last.streak, 3);
  });

  test('touch stage persists per body cluster and resets after cooldown', () {
    final firstHead = director.decide(
      contact: _contact(AvatarAnatomicalRegion.head, start),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.neutral,
    );
    director.decide(
      contact: _contact(
        AvatarAnatomicalRegion.leftHand,
        start + const Duration(milliseconds: 600),
      ),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.neutral,
    );
    final secondHead = director.decide(
      contact: _contact(
        AvatarAnatomicalRegion.leftCheek,
        start + const Duration(milliseconds: 1200),
      ),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.neutral,
    );
    final cooledHead = director.decide(
      contact: _contact(
        AvatarAnatomicalRegion.face,
        start + const Duration(milliseconds: 3300),
      ),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.neutral,
    );

    expect(firstHead.stage, AvatarTouchStage.notice);
    expect(secondHead.stage, AvatarTouchStage.acknowledge);
    expect(secondHead.streak, 2);
    expect(cooledHead.stage, AvatarTouchStage.notice);
    expect(cooledHead.streak, 1);
  });

  test('several different body parts form one responsive sequence', () {
    final regions = [
      AvatarAnatomicalRegion.head,
      AvatarAnatomicalRegion.leftHand,
      AvatarAnatomicalRegion.rightShoe,
    ];
    late AvatarReactionDecision decision;
    for (var index = 0; index < regions.length; index += 1) {
      decision = director.decide(
        contact: _contact(
          regions[index],
          start + Duration(milliseconds: index * 250),
        ),
        phase: AvaSessionPhase.idle,
        expression: AvaExpression.happy,
      );
    }

    expect(decision.distinctRecentRegions, 3);
    expect(decision.intensity, AvatarReactionIntensity.repeated);
    expect(decision.stage, AvatarTouchStage.notice);
    expect(decision.animation?.playbackRate, 1.09);
  });

  test('lesson activity keeps every repeated touch reaction subtle', () {
    director.decide(
      contact: _contact(AvatarAnatomicalRegion.leftHand, start),
      phase: AvaSessionPhase.speaking,
      expression: AvaExpression.neutral,
    );
    final acknowledgement = director.decide(
      contact: _contact(
        AvatarAnatomicalRegion.leftHand,
        start + const Duration(milliseconds: 300),
      ),
      phase: AvaSessionPhase.speaking,
      expression: AvaExpression.neutral,
    );
    final redirect = director.decide(
      contact: _contact(
        AvatarAnatomicalRegion.leftHand,
        start + const Duration(milliseconds: 600),
      ),
      phase: AvaSessionPhase.speaking,
      expression: AvaExpression.neutral,
    );

    expect(acknowledgement.intensity, AvatarReactionIntensity.subtle);
    expect(redirect.stage, AvatarTouchStage.redirect);
    expect(redirect.intensity, AvatarReactionIntensity.subtle);
  });

  test('protected contact acknowledges before a friendly redirect', () {
    director.decide(
      contact: _contact(AvatarAnatomicalRegion.chest, start),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.neutral,
    );
    final acknowledgement = director.decide(
      contact: _contact(
        AvatarAnatomicalRegion.chest,
        start + const Duration(milliseconds: 350),
      ),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.neutral,
    );
    final redirect = director.decide(
      contact: _contact(
        AvatarAnatomicalRegion.chest,
        start + const Duration(milliseconds: 700),
      ),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.neutral,
    );

    expect(acknowledgement.intensity, AvatarReactionIntensity.normal);
    expect(acknowledgement.stage, AvatarTouchStage.acknowledge);
    expect(redirect.intensity, AvatarReactionIntensity.repeated);
    expect(redirect.stage, AvatarTouchStage.redirect);
    expect(redirect.animation?.name, 'AvaReactChestRepeated');
  });

  test('impossibly fast completions coalesce skeletal animation only', () {
    final first = director.decide(
      contact: _contact(AvatarAnatomicalRegion.head, start),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.neutral,
    );
    final second = director.decide(
      contact: _contact(
        AvatarAnatomicalRegion.head,
        start + const Duration(milliseconds: 40),
      ),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.neutral,
    );

    expect(first.animation, isNotNull);
    expect(second.animation, isNull);
    expect(second.feedbackStrength, greaterThan(first.feedbackStrength));
  });

  test('catalog declares three authored variants for every family', () {
    expect(
      AvatarReactionCatalog.requiredAnimationNames,
      hasLength(
        AvatarReactionFamily.values.length *
            AvatarReactionIntensity.values.length,
      ),
    );
  });

  test('leg contact produces layered angle-aware semantic intent', () {
    final decision = director.decide(
      contact: _contact(
        AvatarAnatomicalRegion.leftLowerLeg,
        start,
        cameraAzimuth: 2.4,
        rootYaw: 0.4,
      ),
      phase: AvaSessionPhase.idle,
      expression: AvaExpression.encouraging,
    );

    final recoil = decision.intents.singleWhere(
      (intent) => intent.kind == AvatarActionKind.recoil,
    );
    expect(
      recoil.channelClaims,
      const {
        AvatarMotionChannel.leftLeg: AvatarMotionBlendMode.override,
        AvatarMotionChannel.posture: AvatarMotionBlendMode.override,
      },
    );
    expect(recoil.target, isA<AvatarContactTarget>());
    expect(recoil.numericParameters['relativeViewAzimuth'], closeTo(2, 1e-9));
    expect(recoil.textParameters['surfaceSector'], 'front');
    expect(recoil.textParameters['touchStage'], 'notice');
    expect(decision.intents, hasLength(3));
  });
}

AvatarTouchContact _contact(
  AvatarAnatomicalRegion region,
  Duration occurredAt, {
  double modelX = 0,
  double cameraAzimuth = 0,
  double rootYaw = 0,
}) {
  return AvatarTouchContact(
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
    modelPosition: AvatarModelVector3(modelX, 1, 0),
    modelNormal: const AvatarModelVector3(0, 0, 1),
    spatialContext: AvatarSpatialContext(
      cameraAzimuth: cameraAzimuth,
      cameraElevation: 1.43,
      avatarRootYaw: rootYaw,
    ),
    duration: const Duration(milliseconds: 160),
    peakPressure: 0.5,
    pointerType: 'touch',
    occurredAt: occurredAt,
  );
}
