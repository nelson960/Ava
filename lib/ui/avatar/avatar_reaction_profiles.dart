import 'dart:collection';
import 'dart:convert';

import 'avatar_action_intent.dart';
import 'avatar_anatomy.dart';

/// Data-driven motion composition for anatomical touch reactions.
class AvatarReactionMotionProfileCatalog {
  AvatarReactionMotionProfileCatalog._({
    required Map<AvatarReactionIntensity, double> intensityDurationScales,
    required Map<AvatarAnatomicalRegion, _AvatarReactionMotionProfile>
        regionProfiles,
  })  : _intensityDurationScales = Map.unmodifiable(intensityDurationScales),
        _regionProfiles = Map.unmodifiable(regionProfiles);

  factory AvatarReactionMotionProfileCatalog.fromJsonString(String source) {
    final decoded = jsonDecode(source);
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException(
        'Avatar reaction profiles must be a JSON object.',
      );
    }
    if (decoded['schemaVersion'] != 1) {
      throw FormatException(
        'Unsupported avatar reaction profile schema: '
        '${decoded['schemaVersion']}.',
      );
    }

    final rawScales = decoded['intensityDurationScales'];
    if (rawScales is! Map<String, dynamic>) {
      throw const FormatException(
        'Avatar reaction profiles must declare intensity duration scales.',
      );
    }
    final intensityDurationScales = <AvatarReactionIntensity, double>{};
    for (final intensity in AvatarReactionIntensity.values) {
      final rawScale = rawScales[intensity.name];
      if (rawScale is! num || !rawScale.isFinite || rawScale <= 0) {
        throw FormatException(
          'Avatar reaction intensity ${intensity.name} must have a '
          'finite positive duration scale.',
        );
      }
      intensityDurationScales[intensity] = rawScale.toDouble();
    }
    if (rawScales.length != AvatarReactionIntensity.values.length) {
      throw const FormatException(
        'Avatar reaction profiles contain unknown intensity scales.',
      );
    }

    final rawProfiles = decoded['profiles'];
    if (rawProfiles is! Map<String, dynamic> || rawProfiles.isEmpty) {
      throw const FormatException(
        'Avatar reaction profiles must declare motion profiles.',
      );
    }
    final profiles = <String, _AvatarReactionMotionProfile>{};
    for (final entry in rawProfiles.entries) {
      if (entry.key.trim().isEmpty || entry.value is! Map<String, dynamic>) {
        throw const FormatException(
          'Avatar reaction profile entries must be named objects.',
        );
      }
      profiles[entry.key] = _AvatarReactionMotionProfile.parse(
        entry.value as Map<String, dynamic>,
        entry.key,
      );
    }

    final rawRegions = decoded['regions'];
    if (rawRegions is! Map<String, dynamic>) {
      throw const FormatException(
        'Avatar reaction profiles must map every anatomical region.',
      );
    }
    final regionProfiles =
        <AvatarAnatomicalRegion, _AvatarReactionMotionProfile>{};
    for (final region in AvatarAnatomicalRegion.values) {
      final profileName = rawRegions[region.name];
      if (profileName is! String || !profiles.containsKey(profileName)) {
        throw FormatException(
          'Avatar region ${region.name} references an unknown motion profile.',
        );
      }
      regionProfiles[region] = profiles[profileName]!;
    }
    if (rawRegions.length != AvatarAnatomicalRegion.values.length) {
      throw const FormatException(
        'Avatar reaction profiles contain unknown anatomical regions.',
      );
    }

    return AvatarReactionMotionProfileCatalog._(
      intensityDurationScales: intensityDurationScales,
      regionProfiles: regionProfiles,
    );
  }

  final Map<AvatarReactionIntensity, double> _intensityDurationScales;
  final Map<AvatarAnatomicalRegion, _AvatarReactionMotionProfile>
      _regionProfiles;

  List<AvatarActionIntent> composeTouchActions({
    required int sequence,
    required AvatarReactionIntensity intensity,
    required AvatarContactTarget target,
    required Duration occurredAt,
    required Map<String, double> numericParameters,
    required Map<String, String> textParameters,
  }) {
    if (sequence <= 0) {
      throw ArgumentError.value(sequence, 'sequence', 'must be positive');
    }
    final profile = _regionProfiles[target.region];
    if (profile == null) {
      throw StateError(
        'Ava has no motion profile for ${target.region.name}.',
      );
    }
    final durationScale = _intensityDurationScales[intensity]!;
    return List.unmodifiable(
      profile.actions.indexed.map((entry) {
        final actionIndex = entry.$1;
        final action = entry.$2;
        return action.instantiate(
          id: 'touch-$sequence-${target.region.name}-$actionIndex',
          target: target,
          occurredAt: occurredAt,
          durationScale: durationScale,
          numericParameters: numericParameters,
          textParameters: textParameters,
        );
      }),
    );
  }
}

class _AvatarReactionMotionProfile {
  const _AvatarReactionMotionProfile(this.actions);

  factory _AvatarReactionMotionProfile.parse(
    Map<String, dynamic> source,
    String profileName,
  ) {
    final rawActions = source['actions'];
    if (rawActions is! List || rawActions.isEmpty) {
      throw FormatException(
        'Avatar reaction profile $profileName must contain actions.',
      );
    }
    final actions = rawActions.indexed.map((entry) {
      final rawAction = entry.$2;
      if (rawAction is! Map<String, dynamic>) {
        throw FormatException(
          'Avatar reaction profile $profileName action ${entry.$1} '
          'must be an object.',
        );
      }
      return _AvatarMotionActionProfile.parse(
        rawAction,
        '$profileName action ${entry.$1}',
      );
    }).toList(growable: false);
    return _AvatarReactionMotionProfile(List.unmodifiable(actions));
  }

  final List<_AvatarMotionActionProfile> actions;
}

class _AvatarMotionActionProfile {
  _AvatarMotionActionProfile({
    required this.kind,
    required Map<AvatarMotionChannel, AvatarMotionBlendMode> channelClaims,
    required this.duration,
    required this.blendIn,
    required this.blendOut,
    required this.minimumHold,
    required this.interruptionPolicy,
    required this.retargetPolicy,
    required this.retargetGroup,
  }) : channelClaims = UnmodifiableMapView(channelClaims);

  factory _AvatarMotionActionProfile.parse(
    Map<String, dynamic> source,
    String description,
  ) {
    final kind = _parseEnum(
      AvatarActionKind.values,
      source['kind'],
      '$description kind',
    );
    final rawClaims = source['claims'];
    if (rawClaims is! Map<String, dynamic> || rawClaims.isEmpty) {
      throw FormatException('$description must claim motion channels.');
    }
    final channelClaims = <AvatarMotionChannel, AvatarMotionBlendMode>{};
    for (final entry in rawClaims.entries) {
      final channel = _parseEnum(
        AvatarMotionChannel.values,
        entry.key,
        '$description channel',
      );
      final blendMode = _parseEnum(
        AvatarMotionBlendMode.values,
        entry.value,
        '$description ${entry.key} blend mode',
      );
      channelClaims[channel] = blendMode;
    }
    final duration = _duration(source['durationMs'], '$description durationMs');
    final blendIn = _duration(
      source['blendInMs'],
      '$description blendInMs',
      allowZero: true,
    );
    final blendOut = _duration(
      source['blendOutMs'],
      '$description blendOutMs',
      allowZero: true,
    );
    final minimumHold = _duration(
      source['minimumHoldMs'],
      '$description minimumHoldMs',
      allowZero: true,
    );
    if (minimumHold > duration) {
      throw FormatException(
        '$description minimum hold cannot exceed its duration.',
      );
    }
    final interruption = _parseEnum(
      AvatarInterruptionPolicy.values,
      source['interruption'],
      '$description interruption policy',
    );
    final retarget = _parseEnum(
      AvatarRetargetPolicy.values,
      source['retarget'],
      '$description retarget policy',
    );
    final retargetGroup = source['retargetGroup'];
    if (retargetGroup is! String || retargetGroup.trim().isEmpty) {
      throw FormatException(
        '$description must declare a non-empty retarget group.',
      );
    }
    return _AvatarMotionActionProfile(
      kind: kind,
      channelClaims: channelClaims,
      duration: duration,
      blendIn: blendIn,
      blendOut: blendOut,
      minimumHold: minimumHold,
      interruptionPolicy: interruption,
      retargetPolicy: retarget,
      retargetGroup: retargetGroup,
    );
  }

  final AvatarActionKind kind;
  final Map<AvatarMotionChannel, AvatarMotionBlendMode> channelClaims;
  final Duration duration;
  final Duration blendIn;
  final Duration blendOut;
  final Duration minimumHold;
  final AvatarInterruptionPolicy interruptionPolicy;
  final AvatarRetargetPolicy retargetPolicy;
  final String retargetGroup;

  AvatarActionIntent instantiate({
    required String id,
    required AvatarContactTarget target,
    required Duration occurredAt,
    required double durationScale,
    required Map<String, double> numericParameters,
    required Map<String, String> textParameters,
  }) {
    return AvatarActionIntent(
      id: id,
      kind: kind,
      source: AvatarActionSource.touch,
      priority: AvatarActionPriority.touch,
      channelClaims: channelClaims,
      occurredAt: occurredAt,
      blendIn: blendIn,
      blendOut: blendOut,
      expectedDuration: _scaled(duration, durationScale),
      minimumHold: _scaled(minimumHold, durationScale),
      interruptionPolicy: interruptionPolicy,
      retargetPolicy: retargetPolicy,
      retargetGroup: retargetGroup,
      target: target,
      numericParameters: numericParameters,
      textParameters: textParameters,
    );
  }
}

T _parseEnum<T extends Enum>(
  List<T> values,
  Object? raw,
  String fieldName,
) {
  if (raw is! String) {
    throw FormatException('$fieldName must be a string.');
  }
  for (final value in values) {
    if (value.name == raw) return value;
  }
  throw FormatException('$fieldName has unsupported value $raw.');
}

Duration _duration(
  Object? raw,
  String fieldName, {
  bool allowZero = false,
}) {
  if (raw is! int || raw < 0 || (!allowZero && raw == 0)) {
    throw FormatException(
      '$fieldName must be ${allowZero ? 'non-negative' : 'positive'}.',
    );
  }
  return Duration(milliseconds: raw);
}

Duration _scaled(Duration duration, double scale) {
  return Duration(microseconds: (duration.inMicroseconds * scale).round());
}
