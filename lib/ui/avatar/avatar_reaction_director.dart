import 'dart:math' as math;

import '../../models/lesson_response.dart';
import '../../state/ava_session_state.dart';
import 'avatar_anatomy.dart';
import 'avatar_action_intent.dart';
import 'avatar_animation_coordinator.dart';
import 'avatar_reaction_profiles.dart';

enum AvatarReactionFamily {
  hairLeft,
  hairRight,
  head,
  face,
  leftCheek,
  rightCheek,
  leftShoulder,
  rightShoulder,
  leftUpperArm,
  rightUpperArm,
  leftLowerArm,
  rightLowerArm,
  leftHand,
  rightHand,
  chest,
  waist,
  leftThigh,
  rightThigh,
  leftKnee,
  rightKnee,
  leftLowerLeg,
  rightLowerLeg,
  leftShoe,
  rightShoe,
}

class AvatarTouchContact {
  const AvatarTouchContact({
    required this.anatomy,
    required this.modelPosition,
    required this.modelNormal,
    required this.spatialContext,
    required this.duration,
    required this.peakPressure,
    required this.pointerType,
    required this.occurredAt,
  });

  final AvatarAnatomicalContact anatomy;
  final AvatarModelVector3 modelPosition;
  final AvatarModelVector3 modelNormal;
  final AvatarSpatialContext spatialContext;
  final Duration duration;
  final double peakPressure;
  final String pointerType;
  final Duration occurredAt;

  AvatarAnatomicalRegion get region => anatomy.region;
}

class AvatarReactionDecision {
  const AvatarReactionDecision({
    required this.contact,
    required this.family,
    required this.intensity,
    required this.stage,
    required this.streak,
    required this.distinctRecentRegions,
    required this.feedbackStrength,
    required this.animation,
    required this.intents,
  });

  final AvatarTouchContact contact;
  final AvatarReactionFamily family;
  final AvatarReactionIntensity intensity;
  final AvatarTouchStage stage;
  final int streak;
  final int distinctRecentRegions;
  final double feedbackStrength;

  /// Null only when several completed taps arrive inside the animation
  /// coalescing interval. Visual feedback still occurs immediately.
  final AvatarReactionAnimation? animation;
  final List<AvatarActionIntent> intents;
}

class AvatarReactionConfiguration {
  const AvatarReactionConfiguration({
    this.repeatWindow = const Duration(milliseconds: 1400),
    this.stageResetWindow = const Duration(seconds: 2),
    this.multiRegionWindow = const Duration(milliseconds: 900),
    this.longContactThreshold = const Duration(milliseconds: 550),
    this.highPressureThreshold = 0.72,
    this.minimumAnimationInterval = const Duration(milliseconds: 90),
    this.completionTimeout = const Duration(seconds: 3),
  })  : assert(highPressureThreshold > 0),
        assert(highPressureThreshold <= 1);

  final Duration repeatWindow;
  final Duration stageResetWindow;
  final Duration multiRegionWindow;
  final Duration longContactThreshold;
  final double highPressureThreshold;
  final Duration minimumAnimationInterval;
  final Duration completionTimeout;
}

class AvatarReactionCatalog {
  const AvatarReactionCatalog._();

  static Set<String> get requiredAnimationNames => {
        for (final family in AvatarReactionFamily.values)
          for (final intensity in AvatarReactionIntensity.values)
            animationName(family, intensity),
      };

  static String animationName(
    AvatarReactionFamily family,
    AvatarReactionIntensity intensity,
  ) {
    return 'AvaReact${family.assetToken}${intensity.assetToken}';
  }
}

class AvatarReactionDirector {
  AvatarReactionDirector({
    required AvatarReactionMotionProfileCatalog profiles,
    this.configuration = const AvatarReactionConfiguration(),
  }) : _profiles = profiles {
    if (configuration.repeatWindow <= Duration.zero ||
        configuration.stageResetWindow <= Duration.zero ||
        configuration.multiRegionWindow <= Duration.zero ||
        configuration.longContactThreshold <= Duration.zero ||
        configuration.minimumAnimationInterval.isNegative ||
        configuration.completionTimeout <= Duration.zero) {
      throw ArgumentError(
          'Avatar reaction durations must be valid and positive.');
    }
  }

  final AvatarReactionConfiguration configuration;
  final AvatarReactionMotionProfileCatalog _profiles;

  final List<AvatarTouchContact> _recentContacts = [];
  final Map<String, _AvatarTouchStageState> _stageStates = {};
  Duration? _lastAnimationAt;
  Duration? _lastDecisionAt;
  int _nextIntentSequence = 0;

  AvatarReactionDecision decide({
    required AvatarTouchContact contact,
    required AvaSessionPhase phase,
    required AvaExpression expression,
  }) {
    final lastDecisionAt = _lastDecisionAt;
    if (lastDecisionAt != null && contact.occurredAt < lastDecisionAt) {
      throw StateError(
        'Avatar touch timestamps must be monotonic; received '
        '${contact.occurredAt} after $lastDecisionAt.',
      );
    }
    _lastDecisionAt = contact.occurredAt;
    _discardExpiredHistory(contact.occurredAt);
    _recentContacts.add(contact);

    final cluster = _clusterFor(contact.region);
    final previousStageState = _stageStates[cluster];
    final continuesTouchConversation = previousStageState != null &&
        contact.occurredAt - previousStageState.lastContactAt <=
            configuration.stageResetWindow;
    final streak =
        continuesTouchConversation ? previousStageState.touchCount + 1 : 1;
    _stageStates[cluster] = _AvatarTouchStageState(
      touchCount: streak,
      lastContactAt: contact.occurredAt,
    );
    final multiRegionCutoff =
        contact.occurredAt - configuration.multiRegionWindow;
    final distinctRecentRegions = _recentContacts
        .where((candidate) => candidate.occurredAt >= multiRegionCutoff)
        .map((candidate) => candidate.region)
        .toSet()
        .length;

    final hasStrongInput =
        contact.duration >= configuration.longContactThreshold ||
            contact.peakPressure >= configuration.highPressureThreshold;
    var intensity = switch ((streak, distinctRecentRegions)) {
      (>= 3, _) => AvatarReactionIntensity.repeated,
      (_, >= 3) => AvatarReactionIntensity.repeated,
      (2, _) => AvatarReactionIntensity.normal,
      (_, 2) => AvatarReactionIntensity.normal,
      _ when hasStrongInput => AvatarReactionIntensity.normal,
      _ => AvatarReactionIntensity.subtle,
    };

    final lessonIsActive = switch (phase) {
      AvaSessionPhase.listening ||
      AvaSessionPhase.thinking ||
      AvaSessionPhase.speaking =>
        true,
      AvaSessionPhase.idle || AvaSessionPhase.failure => false,
    };
    if (lessonIsActive && intensity != AvatarReactionIntensity.subtle) {
      intensity = AvatarReactionIntensity.subtle;
    }
    final stage = switch (streak) {
      1 => AvatarTouchStage.notice,
      2 => AvatarTouchStage.acknowledge,
      _ => AvatarTouchStage.redirect,
    };

    final family = _familyFor(contact);
    final playbackRate = _playbackRateFor(intensity, expression);
    final canAnimate = _lastAnimationAt == null ||
        contact.occurredAt - _lastAnimationAt! >=
            configuration.minimumAnimationInterval;
    AvatarReactionAnimation? animation;
    if (canAnimate) {
      _lastAnimationAt = contact.occurredAt;
      animation = AvatarReactionAnimation(
        name: AvatarReactionCatalog.animationName(family, intensity),
        completionTimeout: configuration.completionTimeout,
        playbackRate: playbackRate,
      );
    }

    final feedbackStrength = switch (intensity) {
      AvatarReactionIntensity.subtle => 0.72,
      AvatarReactionIntensity.normal => 0.88,
      AvatarReactionIntensity.repeated => 1.0,
    };
    final target = AvatarContactTarget(
      anatomy: contact.anatomy,
      modelPosition: contact.modelPosition,
      modelNormal: contact.modelNormal,
      spatialContext: contact.spatialContext,
    );
    final relativeViewAzimuth = _normalizedAngle(
      contact.spatialContext.cameraAzimuth -
          contact.spatialContext.avatarRootYaw,
    );
    final intents = _profiles.composeTouchActions(
      sequence: ++_nextIntentSequence,
      intensity: intensity,
      target: target,
      occurredAt: contact.occurredAt,
      numericParameters: {
        'strength': feedbackStrength,
        'streak': streak.toDouble(),
        'recentRegionCount': distinctRecentRegions.toDouble(),
        'contactSeconds': contact.duration.inMicroseconds / 1000000,
        'pressure': contact.peakPressure,
        'relativeViewAzimuth': relativeViewAzimuth,
      },
      textParameters: {
        'region': contact.region.name,
        'surfaceSector': contact.anatomy.surfaceSector.name,
        'primaryBone': contact.anatomy.primaryBone.jointName,
        'intensity': intensity.name,
        'touchStage': stage.name,
        'sessionPhase': phase.name,
        'expression': expression.name,
        'pointerType': contact.pointerType,
      },
    );
    return AvatarReactionDecision(
      contact: contact,
      family: family,
      intensity: intensity,
      stage: stage,
      streak: streak,
      distinctRecentRegions: distinctRecentRegions,
      feedbackStrength: feedbackStrength,
      animation: animation,
      intents: intents,
    );
  }

  void reset() {
    _recentContacts.clear();
    _stageStates.clear();
    _lastAnimationAt = null;
    _lastDecisionAt = null;
    _nextIntentSequence = 0;
  }

  void _discardExpiredHistory(Duration now) {
    final cutoff = now - configuration.repeatWindow;
    _recentContacts.removeWhere(
      (contact) => contact.occurredAt < cutoff,
    );
    final stageCutoff = now - configuration.stageResetWindow;
    _stageStates.removeWhere(
      (_, state) => state.lastContactAt < stageCutoff,
    );
  }

  AvatarReactionFamily _familyFor(AvatarTouchContact contact) {
    if (contact.region == AvatarAnatomicalRegion.hair) {
      return contact.modelPosition.x >= 0
          ? AvatarReactionFamily.hairLeft
          : AvatarReactionFamily.hairRight;
    }
    return AvatarReactionFamily.values.byName(contact.region.name);
  }

  double _playbackRateFor(
    AvatarReactionIntensity intensity,
    AvaExpression expression,
  ) {
    final baseRate = switch (intensity) {
      AvatarReactionIntensity.subtle => 0.94,
      AvatarReactionIntensity.normal => 1.0,
      AvatarReactionIntensity.repeated => 1.06,
    };
    final moodAdjustment = switch (expression) {
      AvaExpression.happy || AvaExpression.encouraging => 0.03,
      AvaExpression.thinking => -0.03,
      AvaExpression.surprised || AvaExpression.neutral => 0.0,
    };
    return baseRate + moodAdjustment;
  }

  String _clusterFor(AvatarAnatomicalRegion region) => switch (region) {
        AvatarAnatomicalRegion.hair ||
        AvatarAnatomicalRegion.head ||
        AvatarAnatomicalRegion.face ||
        AvatarAnatomicalRegion.leftCheek ||
        AvatarAnatomicalRegion.rightCheek =>
          'head',
        AvatarAnatomicalRegion.leftShoulder ||
        AvatarAnatomicalRegion.leftUpperArm ||
        AvatarAnatomicalRegion.leftLowerArm ||
        AvatarAnatomicalRegion.leftHand =>
          'leftArm',
        AvatarAnatomicalRegion.rightShoulder ||
        AvatarAnatomicalRegion.rightUpperArm ||
        AvatarAnatomicalRegion.rightLowerArm ||
        AvatarAnatomicalRegion.rightHand =>
          'rightArm',
        AvatarAnatomicalRegion.chest || AvatarAnatomicalRegion.waist => 'torso',
        AvatarAnatomicalRegion.leftThigh ||
        AvatarAnatomicalRegion.leftKnee ||
        AvatarAnatomicalRegion.leftLowerLeg ||
        AvatarAnatomicalRegion.leftShoe =>
          'leftLeg',
        AvatarAnatomicalRegion.rightThigh ||
        AvatarAnatomicalRegion.rightKnee ||
        AvatarAnatomicalRegion.rightLowerLeg ||
        AvatarAnatomicalRegion.rightShoe =>
          'rightLeg',
      };
}

class _AvatarTouchStageState {
  const _AvatarTouchStageState({
    required this.touchCount,
    required this.lastContactAt,
  });

  final int touchCount;
  final Duration lastContactAt;
}

double _normalizedAngle(double angle) {
  var normalized = angle % (2 * math.pi);
  if (normalized > math.pi) normalized -= 2 * math.pi;
  if (normalized < -math.pi) normalized += 2 * math.pi;
  return normalized;
}

extension on AvatarReactionFamily {
  String get assetToken => name[0].toUpperCase() + name.substring(1);
}

extension on AvatarReactionIntensity {
  String get assetToken => name[0].toUpperCase() + name.substring(1);
}
