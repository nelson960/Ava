import 'dart:collection';

import 'avatar_anatomy.dart';

/// The system that asked Ava to perform an action.
///
/// Sources are kept separate from priorities. A touch can be promoted to a
/// safety response, and a lesson can deliberately schedule a low-priority
/// background action without pretending to be the idle system.
enum AvatarActionSource {
  idleSystem,
  touch,
  lesson,
  speech,
  command,
  safety,
}

/// A semantic request. These values describe intent, never an asset filename
/// or a renderer-specific animation state.
enum AvatarActionKind {
  idle,
  listen,
  think,
  explain,
  recoil,
  weightShift,
  step,
  lookAt,
  blink,
  expression,
  handPose,
  fingerPose,
  point,
  dance,
  lipSync,
  secondaryMotion,
  protectiveRedirect,
}

enum AvatarReactionIntensity { subtle, normal, repeated }

/// The persistent step in a touch conversation with one body region.
///
/// Intensity answers "how much motion is safe right now" while stage answers
/// "what should Ava do next". Keeping them independent prevents a repeated
/// touch during a lesson from replaying the first pose at a different scale.
enum AvatarTouchStage { notice, acknowledge, redirect }

/// Canonical mouth shapes accepted by Ava's local renderer.
///
/// Provider-specific phonemes must be mapped to this bounded vocabulary before
/// they enter the motion scheduler.
enum AvatarViseme { aa, ih, ou, ee, oh, closed }

/// Independently blendable parts of Ava's performance graph.
///
/// Left and right limbs are intentionally independent. Facial expression and
/// lip sync are also separate so speech does not stop when another reaction
/// starts.
enum AvatarMotionChannel {
  basePose,
  posture,
  spine,
  leftLeg,
  rightLeg,
  leftFoot,
  rightFoot,
  leftArm,
  rightArm,
  leftHand,
  rightHand,
  leftFingers,
  rightFingers,
  neck,
  head,
  gaze,
  faceExpression,
  lipSync,
  hair,
  clothing,
}

enum AvatarMotionBlendMode {
  /// Owns the channel while the action is active.
  override,

  /// Is composed on top of other actions claiming the same channel.
  additive,
}

enum AvatarActionPriority {
  idle,
  ambient,
  touch,
  lesson,
  command,
  safety,
}

extension AvatarActionPriorityValue on AvatarActionPriority {
  int get value => switch (this) {
        AvatarActionPriority.idle => 0,
        AvatarActionPriority.ambient => 10,
        AvatarActionPriority.touch => 20,
        AvatarActionPriority.lesson => 30,
        AvatarActionPriority.command => 40,
        AvatarActionPriority.safety => 50,
      };
}

/// Controls whether a conflicting action may replace this one.
enum AvatarInterruptionPolicy {
  interruptible,
  afterMinimumHold,
  mustComplete,
}

/// Controls whether updated input can steer an active action without
/// restarting its timeline.
enum AvatarRetargetPolicy {
  never,
  immediate,
  afterMinimumHold,
}

sealed class AvatarActionTarget {
  const AvatarActionTarget();
}

/// A named lesson or scene target such as a diagram element.
final class AvatarNamedTarget extends AvatarActionTarget {
  AvatarNamedTarget(this.name) {
    if (name.trim().isEmpty) {
      throw ArgumentError.value(name, 'name', 'must not be empty');
    }
  }

  final String name;
}

/// The spatial context captured at the same instant as a 3D contact.
///
/// Angles are radians. Camera orientation, avatar orientation, and anatomical
/// surface orientation are deliberately independent inputs.
class AvatarSpatialContext {
  AvatarSpatialContext({
    required this.cameraAzimuth,
    required this.cameraElevation,
    required this.avatarRootYaw,
  }) {
    for (final entry in <String, double>{
      'cameraAzimuth': cameraAzimuth,
      'cameraElevation': cameraElevation,
      'avatarRootYaw': avatarRootYaw,
    }.entries) {
      if (!entry.value.isFinite) {
        throw ArgumentError.value(
          entry.value,
          entry.key,
          'must be finite',
        );
      }
    }
  }

  final double cameraAzimuth;
  final double cameraElevation;
  final double avatarRootYaw;
}

/// A renderer-independent 3D touch target.
///
/// [longitudinalPosition] and [radialAngle] are populated by Anatomy Map v2.
/// Leaving them null is explicit support for the current v1 map during the
/// migration; consumers that require continuous anatomy must reject null.
final class AvatarContactTarget extends AvatarActionTarget {
  AvatarContactTarget({
    required this.anatomy,
    required this.modelPosition,
    required this.modelNormal,
    required this.spatialContext,
  });

  final AvatarAnatomicalContact anatomy;
  final AvatarModelVector3 modelPosition;
  final AvatarModelVector3 modelNormal;
  final AvatarSpatialContext spatialContext;

  AvatarAnatomicalRegion get region => anatomy.region;
  double get longitudinalPosition => anatomy.longitudinalPosition;
  double get radialAngle => anatomy.radialAngle;
}

/// One validated mouth-shape interval on the TTS audio timeline.
class AvatarVisemeCue {
  AvatarVisemeCue({
    required this.viseme,
    required this.start,
    required this.end,
    required this.weight,
  }) {
    if (start.isNegative) {
      throw ArgumentError.value(start, 'start', 'must not be negative');
    }
    if (end <= start) {
      throw ArgumentError.value(end, 'end', 'must be later than start');
    }
    if (!weight.isFinite || weight < 0.05 || weight > 1) {
      throw ArgumentError.value(
        weight,
        'weight',
        'must be finite and between 0.05 and 1',
      );
    }
  }

  final AvatarViseme viseme;
  final Duration start;
  final Duration end;
  final double weight;
}

/// Complete canonical lip-sync timing for one audio segment.
///
/// [audioPosition] is the playback sample-clock position captured when this
/// target is dispatched. It allows a renderer retarget after a seek, resume or
/// timing correction without replaying the beginning of the utterance.
final class AvatarLipSyncTarget extends AvatarActionTarget {
  AvatarLipSyncTarget({
    required this.audioDuration,
    required List<AvatarVisemeCue> cues,
    this.audioPosition = Duration.zero,
  }) : cues = List.unmodifiable(cues) {
    if (audioDuration <= Duration.zero) {
      throw ArgumentError.value(
        audioDuration,
        'audioDuration',
        'must be greater than zero',
      );
    }
    if (audioPosition.isNegative || audioPosition >= audioDuration) {
      throw ArgumentError.value(
        audioPosition,
        'audioPosition',
        'must be within the audio duration',
      );
    }
    if (this.cues.isEmpty) {
      throw ArgumentError.value(cues, 'cues', 'must not be empty');
    }
    AvatarVisemeCue? previous;
    for (final cue in this.cues) {
      if (cue.end > audioDuration) {
        throw ArgumentError.value(
          cue.end,
          'cues',
          'must not extend beyond the audio duration',
        );
      }
      if (previous != null && cue.start < previous.end) {
        throw ArgumentError.value(
          cues,
          'cues',
          'must be ordered and must not overlap',
        );
      }
      previous = cue;
    }
    if (!this.cues.any((cue) => cue.end > audioPosition)) {
      throw ArgumentError.value(
        audioPosition,
        'audioPosition',
        'must precede at least one viseme cue',
      );
    }
  }

  final Duration audioDuration;
  final Duration audioPosition;
  final List<AvatarVisemeCue> cues;

  Duration get remainingDuration => audioDuration - audioPosition;
}

/// A renderer-independent request for Ava to perform one semantic action.
class AvatarActionIntent {
  AvatarActionIntent({
    required this.id,
    required this.kind,
    required this.source,
    required this.priority,
    required Map<AvatarMotionChannel, AvatarMotionBlendMode> channelClaims,
    required this.occurredAt,
    required this.blendIn,
    required this.blendOut,
    required this.interruptionPolicy,
    required this.retargetPolicy,
    this.expectedDuration,
    this.minimumHold = Duration.zero,
    this.retargetGroup,
    this.target,
    Map<String, double> numericParameters = const {},
    Map<String, String> textParameters = const {},
  })  : channelClaims = UnmodifiableMapView(
          Map<AvatarMotionChannel, AvatarMotionBlendMode>.of(channelClaims),
        ),
        numericParameters = UnmodifiableMapView(
          Map<String, double>.of(numericParameters),
        ),
        textParameters = UnmodifiableMapView(
          Map<String, String>.of(textParameters),
        ) {
    if (id.trim().isEmpty) {
      throw ArgumentError.value(id, 'id', 'must not be empty');
    }
    if (this.channelClaims.isEmpty) {
      throw ArgumentError.value(
        channelClaims,
        'channelClaims',
        'must claim at least one motion channel',
      );
    }
    if (occurredAt.isNegative) {
      throw ArgumentError.value(
        occurredAt,
        'occurredAt',
        'must not be negative',
      );
    }
    if (blendIn.isNegative || blendOut.isNegative) {
      throw ArgumentError('Action blend durations must not be negative.');
    }
    final duration = expectedDuration;
    if (duration != null && duration <= Duration.zero) {
      throw ArgumentError.value(
        duration,
        'expectedDuration',
        'must be greater than zero when provided',
      );
    }
    if (minimumHold.isNegative) {
      throw ArgumentError.value(
        minimumHold,
        'minimumHold',
        'must not be negative',
      );
    }
    if (duration != null && minimumHold > duration) {
      throw ArgumentError(
        'minimumHold cannot be longer than expectedDuration.',
      );
    }
    final group = retargetGroup;
    if (group != null && group.trim().isEmpty) {
      throw ArgumentError.value(
        group,
        'retargetGroup',
        'must not be blank',
      );
    }
    if (retargetPolicy != AvatarRetargetPolicy.never && group == null) {
      throw ArgumentError(
        'A retargetable action must declare a retargetGroup.',
      );
    }
    for (final entry in this.numericParameters.entries) {
      if (entry.key.trim().isEmpty || !entry.value.isFinite) {
        throw ArgumentError.value(
          entry,
          'numericParameters',
          'keys must be non-empty and values must be finite',
        );
      }
    }
    for (final entry in this.textParameters.entries) {
      if (entry.key.trim().isEmpty || entry.value.trim().isEmpty) {
        throw ArgumentError.value(
          entry,
          'textParameters',
          'keys and values must be non-empty',
        );
      }
    }
  }

  final String id;
  final AvatarActionKind kind;
  final AvatarActionSource source;
  final AvatarActionPriority priority;
  final Map<AvatarMotionChannel, AvatarMotionBlendMode> channelClaims;
  final Duration occurredAt;
  final Duration blendIn;
  final Duration blendOut;
  final Duration? expectedDuration;
  final Duration minimumHold;
  final AvatarInterruptionPolicy interruptionPolicy;
  final AvatarRetargetPolicy retargetPolicy;
  final String? retargetGroup;
  final AvatarActionTarget? target;
  final Map<String, double> numericParameters;
  final Map<String, String> textParameters;

  Set<AvatarMotionChannel> get channels => channelClaims.keys.toSet();

  bool conflictsWith(AvatarActionIntent other) {
    for (final entry in channelClaims.entries) {
      final otherMode = other.channelClaims[entry.key];
      if (otherMode == null) continue;
      if (entry.value == AvatarMotionBlendMode.override &&
          otherMode == AvatarMotionBlendMode.override) {
        return true;
      }
    }
    return false;
  }

  bool canRetarget(AvatarActionIntent other) {
    final group = retargetGroup;
    return group != null &&
        group == other.retargetGroup &&
        _sameClaims(channelClaims, other.channelClaims);
  }
}

bool _sameClaims(
  Map<AvatarMotionChannel, AvatarMotionBlendMode> left,
  Map<AvatarMotionChannel, AvatarMotionBlendMode> right,
) {
  if (left.length != right.length) return false;
  for (final entry in left.entries) {
    if (right[entry.key] != entry.value) return false;
  }
  return true;
}
