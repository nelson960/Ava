import 'diagram_spec.dart';

export 'diagram_spec.dart';

enum LearningDomain {
  general,
  math,
  science,
}

enum AvaExpression {
  neutral,
  happy,
  thinking,
  encouraging,
  surprised,
}

enum AvaGesture {
  idle,
  nod,
  explain,
  pointAtDashboard,
  celebrate,
}

class LessonResponse {
  const LessonResponse({
    required this.speech,
    required this.domain,
    required this.expression,
    required this.gesture,
    this.diagram,
  });

  final String speech;
  final LearningDomain domain;
  final AvaExpression expression;
  final AvaGesture gesture;
  final DiagramSpec? diagram;

  factory LessonResponse.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {'speech', 'domain', 'expression', 'gesture', 'diagram'},
      'lesson response',
      optional: const {'diagram'},
    );
    final rawDiagram = json['diagram'];
    final response = LessonResponse(
      speech: _requiredString(json, 'speech', 'lesson response'),
      domain: _enumByName(
        LearningDomain.values,
        _requiredString(json, 'domain', 'lesson response'),
        'lesson response.domain',
      ),
      expression: _enumByName(
        AvaExpression.values,
        _requiredString(json, 'expression', 'lesson response'),
        'lesson response.expression',
      ),
      gesture: _enumByName(
        AvaGesture.values,
        _requiredString(json, 'gesture', 'lesson response'),
        'lesson response.gesture',
      ),
      diagram: rawDiagram == null
          ? null
          : DiagramSpec.fromJson(
              _requiredObject(rawDiagram, 'lesson response.diagram'),
            ),
    );
    response.validate();
    return response;
  }

  Map<String, Object?> toJson() => {
        'speech': speech,
        'domain': domain.name,
        'expression': expression.name,
        'gesture': gesture.name,
        if (diagram case final diagram?) 'diagram': diagram.toJson(),
      };

  void validate() {
    final normalizedSpeech = speech.trim();
    if (normalizedSpeech.isEmpty) {
      throw const FormatException('Ava cannot return an empty response.');
    }
    if (normalizedSpeech.length > 1800) {
      throw const FormatException(
        'Ava speech exceeds the 1800-character lesson response limit.',
      );
    }
    diagram?.validate();
  }
}

class ConversationMessage {
  const ConversationMessage({
    required this.role,
    required this.text,
  });

  final ConversationRole role;
  final String text;

  Map<String, Object?> toJson() => {
        'role': role.name,
        'text': text,
      };
}

enum ConversationRole {
  child,
  ava,
}

class ConversationRequest {
  const ConversationRequest({
    required this.childName,
    required this.message,
    required this.recentMessages,
  });

  final String childName;
  final String message;
  final List<ConversationMessage> recentMessages;

  void validate() {
    if (childName.trim().isEmpty) {
      throw const FormatException('The child name cannot be empty.');
    }
    if (message.trim().isEmpty) {
      throw const FormatException('The child message cannot be empty.');
    }
    if (message.length > 2000) {
      throw const FormatException(
        'The child message exceeds the 2000-character request limit.',
      );
    }
    if (recentMessages.length > 16) {
      throw const FormatException(
        'A lesson request cannot contain more than 16 memory messages.',
      );
    }
    for (final entry in recentMessages) {
      if (entry.text.trim().isEmpty) {
        throw const FormatException(
          'Conversation memory cannot contain an empty message.',
        );
      }
    }
  }

  Map<String, Object?> toJson() {
    validate();
    return {
      'childName': childName.trim(),
      'message': message.trim(),
      'recentMessages': [
        for (final entry in recentMessages) entry.toJson(),
      ],
    };
  }
}

void _requireExactKeys(
  Map<String, Object?> source,
  Set<String> allowed,
  String context, {
  Set<String> optional = const {},
}) {
  final unknown = source.keys.toSet().difference(allowed);
  if (unknown.isNotEmpty) {
    throw FormatException(
      '$context contains unsupported fields: ${unknown.join(', ')}.',
    );
  }
  final required = allowed.difference(optional);
  final missing = required.difference(source.keys.toSet());
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

Map<String, Object?> _requiredObject(Object? value, String context) {
  if (value is! Map) {
    throw FormatException('$context must be an object.');
  }
  final result = <String, Object?>{};
  for (final entry in value.entries) {
    if (entry.key is! String) {
      throw FormatException('$context contains a non-string field name.');
    }
    result[entry.key as String] = entry.value;
  }
  return result;
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
