import 'dart:convert';

import 'package:flutter/services.dart';

import 'lesson_response.dart';

enum ActivityTone {
  coral,
  blue,
  gold,
  teal,
}

class LocalLearningActivity {
  const LocalLearningActivity({
    required this.id,
    required this.eyebrow,
    required this.title,
    required this.instruction,
    required this.domain,
    required this.tone,
    required this.targetValue,
    required this.diagram,
  });

  static final RegExp _validId = RegExp(r'^[a-z][a-z0-9_]{2,39}$');

  final String id;
  final String eyebrow;
  final String title;
  final String instruction;
  final LearningDomain domain;
  final ActivityTone tone;
  final int targetValue;
  final DiagramSpec diagram;

  factory LocalLearningActivity.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'id',
        'eyebrow',
        'title',
        'instruction',
        'domain',
        'tone',
        'targetValue',
        'diagram',
      },
      'learning activity',
    );
    final activity = LocalLearningActivity(
      id: _requiredString(json, 'id', 'learning activity'),
      eyebrow: _requiredString(json, 'eyebrow', 'learning activity'),
      title: _requiredString(json, 'title', 'learning activity'),
      instruction: _requiredString(json, 'instruction', 'learning activity'),
      domain: _enumByName(
        LearningDomain.values,
        _requiredString(json, 'domain', 'learning activity'),
        'learning activity.domain',
      ),
      tone: _enumByName(
        ActivityTone.values,
        _requiredString(json, 'tone', 'learning activity'),
        'learning activity.tone',
      ),
      targetValue: _requiredInt(json, 'targetValue', 'learning activity'),
      diagram: DiagramSpec.fromJson(
        _requiredObject(json['diagram'], 'learning activity.diagram'),
      ),
    );
    activity.validate();
    return activity;
  }

  void validate() {
    if (!_validId.hasMatch(id)) {
      throw FormatException(
        'Learning activity id "$id" must be 3-40 lowercase ASCII characters.',
      );
    }
    _validateBoundedText(eyebrow, 'eyebrow', 40);
    _validateBoundedText(title, 'title', 80);
    _validateBoundedText(instruction, 'instruction', 180);
    diagram.validate();
    switch (diagram) {
      case FractionDiagramSpec(:final totalParts):
        if (targetValue < 0 || targetValue > totalParts) {
          throw FormatException(
            'Fraction activity target $targetValue must be between 0 and '
            '$totalParts.',
          );
        }
      case NumberLineDiagramSpec(:final target):
        if (targetValue != target) {
          throw const FormatException(
            'Number-line activity targetValue must match diagram.target.',
          );
        }
    }
  }

  static void _validateBoundedText(String value, String name, int maximum) {
    final normalized = value.trim();
    if (normalized.isEmpty || normalized.runes.length > maximum) {
      throw FormatException(
        'Learning activity $name must contain 1-$maximum characters.',
      );
    }
  }
}

class LocalLearningActivityCatalog {
  LocalLearningActivityCatalog._(List<LocalLearningActivity> activities)
      : activities = List.unmodifiable(activities);

  final List<LocalLearningActivity> activities;

  static Future<LocalLearningActivityCatalog> load(
    AssetBundle bundle, {
    String assetPath = 'assets/learning_activities.json',
  }) async {
    final raw = await bundle.loadString(assetPath);
    final decoded = jsonDecode(raw);
    if (decoded is! Map) {
      throw const FormatException(
        'The local learning activity catalog must be a JSON object.',
      );
    }
    final json = decoded.cast<String, Object?>();
    _requireExactKeys(
        json, const {'version', 'activities'}, 'activity catalog');
    if (json['version'] != 1) {
      throw FormatException(
        'Unsupported local learning activity catalog version '
        '${json['version']}.',
      );
    }
    final rawActivities = json['activities'];
    if (rawActivities is! List || rawActivities.isEmpty) {
      throw const FormatException(
        'The local learning activity catalog cannot be empty.',
      );
    }
    if (rawActivities.length > 24) {
      throw const FormatException(
        'The local learning activity catalog cannot exceed 24 activities.',
      );
    }
    final activities = <LocalLearningActivity>[
      for (var index = 0; index < rawActivities.length; index += 1)
        LocalLearningActivity.fromJson(
          _requiredObject(rawActivities[index], 'activities[$index]'),
        ),
    ];
    final ids = activities.map((activity) => activity.id).toSet();
    if (ids.length != activities.length) {
      throw const FormatException(
        'Local learning activity ids must be unique.',
      );
    }
    return LocalLearningActivityCatalog._(activities);
  }
}

void _requireExactKeys(
  Map<String, Object?> source,
  Set<String> allowed,
  String context,
) {
  final unknown = source.keys.toSet().difference(allowed);
  if (unknown.isNotEmpty) {
    throw FormatException(
      '$context contains unsupported fields: ${unknown.join(', ')}.',
    );
  }
  final missing = allowed.difference(source.keys.toSet());
  if (missing.isNotEmpty) {
    throw FormatException(
      '$context is missing required fields: ${missing.join(', ')}.',
    );
  }
}

String _requiredString(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('$context.$key must be a non-empty string.');
  }
  return value;
}

int _requiredInt(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! int) {
    throw FormatException('$context.$key must be an integer.');
  }
  return value;
}

Map<String, Object?> _requiredObject(Object? value, String context) {
  if (value is! Map) {
    throw FormatException('$context must be an object.');
  }
  return value.cast<String, Object?>();
}

T _enumByName<T extends Enum>(
  List<T> values,
  String raw,
  String context,
) {
  final matches = values.where((value) => value.name == raw);
  if (matches.length != 1) {
    throw FormatException('$context has unsupported value "$raw".');
  }
  return matches.single;
}
