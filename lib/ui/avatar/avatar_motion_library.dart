import 'dart:convert';

class AvatarMotionLibraryClip {
  AvatarMotionLibraryClip._({
    required this.id,
    required this.actionName,
    required this.framesPerSecond,
    required this.startFrame,
    required this.endFrame,
  });

  static final RegExp _validId = RegExp(r'^[a-z][a-z0-9_]{0,63}$');

  final String id;
  final String actionName;
  final int framesPerSecond;
  final int startFrame;
  final int endFrame;

  Duration get duration => Duration(
        microseconds: (((endFrame - startFrame) / framesPerSecond) *
                Duration.microsecondsPerSecond)
            .round(),
      );

  factory AvatarMotionLibraryClip.fromJson(Map<String, Object?> json) {
    _requireKeys(
      json,
      const {
        'id',
        'actionName',
        'fps',
        'frameRange',
        'markers',
        'reviewFrames',
        'claims',
        'returnsToReferencePose',
        'allowsSelfTransition',
      },
      'motion clip',
    );
    final id = _string(json, 'id', 'motion clip');
    final actionName = _string(json, 'actionName', 'motion clip');
    final fps = _integer(json, 'fps', 'motion clip');
    final range = _object(json['frameRange'], 'motion clip.frameRange');
    _requireKeys(
      range,
      const {'startFrame', 'endFrame'},
      'motion clip.frameRange',
    );
    final start = _integer(range, 'startFrame', 'motion clip.frameRange');
    final end = _integer(range, 'endFrame', 'motion clip.frameRange');
    if (!_validId.hasMatch(id)) {
      throw FormatException('Invalid Ava motion clip id "$id".');
    }
    if (fps <= 0 || fps > 120 || start != 1 || end <= start) {
      throw FormatException(
        'Ava motion clip $id has an invalid frame contract.',
      );
    }

    _validateMarkers(
      _object(json['markers'], 'motion clip.markers'),
      clipId: id,
      startFrame: start,
      endFrame: end,
    );
    _validateReviewFrames(json['reviewFrames'], id, start, end);
    _validateClaims(json['claims'], id);
    if (json['returnsToReferencePose'] != true ||
        json['allowsSelfTransition'] != true) {
      throw FormatException(
        'Ava motion clip $id must return to reference and allow retargeting.',
      );
    }
    return AvatarMotionLibraryClip._(
      id: id,
      actionName: actionName,
      framesPerSecond: fps,
      startFrame: start,
      endFrame: end,
    );
  }
}

class AvatarMotionLibrary {
  AvatarMotionLibrary._({
    required this.referenceActionName,
    required List<AvatarMotionLibraryClip> clips,
  })  : clips = List.unmodifiable(clips),
        _clipsById = Map.unmodifiable({
          for (final clip in clips) clip.id: clip,
        });

  final String referenceActionName;
  final List<AvatarMotionLibraryClip> clips;
  final Map<String, AvatarMotionLibraryClip> _clipsById;

  Set<String> get actionNames => clips.map((clip) => clip.actionName).toSet();

  AvatarMotionLibraryClip clip(String id) {
    final result = _clipsById[id];
    if (result == null) {
      throw StateError('Ava motion capability "$id" is not registered.');
    }
    return result;
  }

  factory AvatarMotionLibrary.fromJsonString(String source) {
    final decoded = jsonDecode(source);
    final root = _object(decoded, 'motion manifest');
    _requireKeys(
      root,
      const {
        'schemaVersion',
        'referencePose',
        'transitionPolicy',
        'clips',
      },
      'motion manifest',
    );
    if (root['schemaVersion'] != 1) {
      throw const FormatException(
        'Ava motion manifest schemaVersion must be exactly 1.',
      );
    }
    final reference = _object(
      root['referencePose'],
      'motion manifest.referencePose',
    );
    _requireKeys(
      reference,
      const {'actionName', 'frame'},
      'motion manifest.referencePose',
    );
    final referenceAction = _string(
      reference,
      'actionName',
      'motion manifest.referencePose',
    );
    if (_integer(reference, 'frame', 'motion manifest.referencePose') != 1) {
      throw const FormatException(
        'Ava motion reference pose must use frame 1.',
      );
    }
    _validateTransitionPolicy(root['transitionPolicy']);

    final rawClips = root['clips'];
    if (rawClips is! List || rawClips.isEmpty) {
      throw const FormatException(
        'Ava motion manifest clips must be a non-empty list.',
      );
    }
    final clips = [
      for (var index = 0; index < rawClips.length; index += 1)
        AvatarMotionLibraryClip.fromJson(
          _object(rawClips[index], 'motion manifest.clips[$index]'),
        ),
    ];
    if (clips.map((clip) => clip.id).toSet().length != clips.length ||
        clips.map((clip) => clip.actionName).toSet().length != clips.length) {
      throw const FormatException(
        'Ava motion manifest clip IDs and action names must be unique.',
      );
    }
    return AvatarMotionLibrary._(
      referenceActionName: referenceAction,
      clips: clips,
    );
  }
}

void _validateTransitionPolicy(Object? value) {
  final policy = _object(value, 'motion manifest.transitionPolicy');
  _requireKeys(
    policy,
    const {
      'kind',
      'weightCurve',
      'blendFrameSource',
      'incomingEntryAlignment',
      'incomingVelocityLeadFrames',
      'preserveUnclaimedChannels',
      'requireExactReferenceExit',
    },
    'motion manifest.transitionPolicy',
  );
  if (_string(policy, 'kind', 'motion manifest.transitionPolicy') !=
          'poseVelocityMatchedCrossfade' ||
      _string(policy, 'weightCurve', 'motion manifest.transitionPolicy') !=
          'smootherStep' ||
      policy['preserveUnclaimedChannels'] != true ||
      policy['requireExactReferenceExit'] != true ||
      _integer(
            policy,
            'incomingVelocityLeadFrames',
            'motion manifest.transitionPolicy',
          ) <=
          0) {
    throw const FormatException(
      'Ava motion manifest has an unsupported transition policy.',
    );
  }
  _string(policy, 'blendFrameSource', 'motion manifest.transitionPolicy');
  _string(policy, 'incomingEntryAlignment', 'motion manifest.transitionPolicy');
}

void _validateMarkers(
  Map<String, Object?> markers, {
  required String clipId,
  required int startFrame,
  required int endFrame,
}) {
  _requireKeys(
    markers,
    const {'entry', 'mainBeats', 'handoff', 'settle', 'exit'},
    'motion clip $clipId.markers',
  );
  final entry = _window(markers['entry'], '$clipId.entry');
  final handoff = _window(markers['handoff'], '$clipId.handoff');
  final settle = _window(markers['settle'], '$clipId.settle');
  final exit = _window(markers['exit'], '$clipId.exit');
  final rawBeats = markers['mainBeats'];
  if (rawBeats is! List ||
      rawBeats.isEmpty ||
      rawBeats.any((beat) => beat is! int)) {
    throw FormatException('Ava motion clip $clipId has invalid main beats.');
  }
  final beats = rawBeats.cast<int>();
  final windows = [entry, handoff, settle, exit];
  if (windows.any(
        (window) =>
            window.$1 < startFrame ||
            window.$1 > endFrame ||
            window.$2 < startFrame ||
            window.$2 > endFrame,
      ) ||
      entry.$1 != startFrame ||
      exit.$2 != endFrame ||
      handoff.$2 != settle.$1 ||
      settle.$2 != exit.$1 ||
      beats.any((beat) => beat <= entry.$2 || beat > handoff.$1)) {
    throw FormatException('Ava motion clip $clipId has invalid markers.');
  }
}

(int, int) _window(Object? value, String context) {
  final window = _object(value, context);
  _requireKeys(window, const {'startFrame', 'endFrame'}, context);
  final start = _integer(window, 'startFrame', context);
  final end = _integer(window, 'endFrame', context);
  if (start <= 0 || end < start) {
    throw FormatException('$context is not an ordered frame window.');
  }
  return (start, end);
}

void _validateReviewFrames(
  Object? value,
  String clipId,
  int startFrame,
  int endFrame,
) {
  if (value is! List || value.isEmpty || value.any((frame) => frame is! int)) {
    throw FormatException('Ava motion clip $clipId has invalid review frames.');
  }
  final frames = value.cast<int>();
  if (frames.first != startFrame ||
      frames.last != endFrame ||
      frames.toSet().length != frames.length ||
      frames.any((frame) => frame < startFrame || frame > endFrame) ||
      !_isStrictlyIncreasing(frames)) {
    throw FormatException('Ava motion clip $clipId review frames are stale.');
  }
}

bool _isStrictlyIncreasing(List<int> values) {
  for (var index = 1; index < values.length; index += 1) {
    if (values[index] <= values[index - 1]) {
      return false;
    }
  }
  return true;
}

void _validateClaims(Object? value, String clipId) {
  final claims = _object(value, 'motion clip $clipId.claims');
  if (claims.isEmpty ||
      claims.entries.any(
        (entry) =>
            entry.key.trim().isEmpty ||
            (entry.value != 'additive' && entry.value != 'override'),
      )) {
    throw FormatException('Ava motion clip $clipId has invalid claims.');
  }
}

void _requireKeys(
  Map<String, Object?> source,
  Set<String> expected,
  String context,
) {
  final actual = source.keys.toSet();
  final missing = expected.difference(actual);
  final unknown = actual.difference(expected);
  if (missing.isNotEmpty || unknown.isNotEmpty) {
    throw FormatException(
      '$context fields mismatch: missing=${missing.join(',')}, '
      'unsupported=${unknown.join(',')}.',
    );
  }
}

Map<String, Object?> _object(Object? value, String context) {
  if (value is! Map) {
    throw FormatException('$context must be an object.');
  }
  final result = <String, Object?>{};
  for (final entry in value.entries) {
    final key = entry.key;
    if (key is! String) {
      throw FormatException('$context contains a non-string field name.');
    }
    result[key] = entry.value;
  }
  return result;
}

String _string(Map<String, Object?> source, String key, String context) {
  final value = source[key];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('$context.$key must be a non-empty string.');
  }
  return value;
}

int _integer(Map<String, Object?> source, String key, String context) {
  final value = source[key];
  if (value is! int) {
    throw FormatException('$context.$key must be an integer.');
  }
  return value;
}
