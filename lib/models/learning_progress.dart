import 'diagram_interaction.dart';
import 'lesson_response.dart';
import 'local_learning_activity.dart';

enum LearningProgressEventKind {
  activityOpened,
  valueChanged,
  activityCompleted,
  activityReset,
}

class LearningProgressEvent {
  const LearningProgressEvent({
    required this.kind,
    required this.profileId,
    required this.activityId,
    required this.domain,
    required this.topic,
    required this.diagramKind,
    required this.occurredAt,
    this.currentValue,
    this.targetValue,
  });

  final LearningProgressEventKind kind;
  final String profileId;
  final String activityId;
  final LearningDomain domain;
  final String topic;
  final DiagramKind diagramKind;
  final DateTime occurredAt;
  final int? currentValue;
  final int? targetValue;

  factory LearningProgressEvent.opened({
    required String profileId,
    required LocalLearningActivity activity,
    required DateTime occurredAt,
  }) {
    return LearningProgressEvent(
      kind: LearningProgressEventKind.activityOpened,
      profileId: profileId,
      activityId: activity.id,
      domain: activity.domain,
      topic: activity.title,
      diagramKind: activity.diagram.kind,
      occurredAt: occurredAt,
      targetValue: activity.targetValue,
    )..validate();
  }

  factory LearningProgressEvent.fromInteraction({
    required String profileId,
    required LocalLearningActivity activity,
    required DiagramInteraction interaction,
    required DateTime occurredAt,
  }) {
    final kind = switch (interaction.kind) {
      DiagramInteractionKind.valueChanged =>
        LearningProgressEventKind.valueChanged,
      DiagramInteractionKind.completed =>
        LearningProgressEventKind.activityCompleted,
      DiagramInteractionKind.reset => LearningProgressEventKind.activityReset,
    };
    return LearningProgressEvent(
      kind: kind,
      profileId: profileId,
      activityId: activity.id,
      domain: activity.domain,
      topic: activity.title,
      diagramKind: activity.diagram.kind,
      occurredAt: occurredAt,
      currentValue: interaction.currentValue,
      targetValue: interaction.targetValue,
    )..validate();
  }

  void validate() {
    _validateId(profileId, 'profileId', 64);
    _validateId(activityId, 'activityId', 40);
    final normalizedTopic = topic.trim();
    if (normalizedTopic.isEmpty || normalizedTopic.runes.length > 80) {
      throw const FormatException(
        'A learning progress topic must contain 1-80 characters.',
      );
    }
    if (occurredAt.isAfter(DateTime.now().add(const Duration(minutes: 1)))) {
      throw const FormatException(
        'A learning progress event cannot occur in the future.',
      );
    }
    if (kind == LearningProgressEventKind.activityOpened &&
        currentValue != null) {
      throw const FormatException(
        'An activityOpened event cannot contain currentValue.',
      );
    }
    if (kind != LearningProgressEventKind.activityOpened &&
        currentValue == null) {
      throw FormatException(
        '${kind.name} requires currentValue.',
      );
    }
    if (kind == LearningProgressEventKind.activityCompleted &&
        (targetValue == null || currentValue != targetValue)) {
      throw const FormatException(
        'An activityCompleted event requires currentValue == targetValue.',
      );
    }
  }

  static void _validateId(String value, String name, int maximum) {
    final valid = RegExp(r'^[A-Za-z0-9._:-]+$');
    if (value.isEmpty || value.length > maximum || !valid.hasMatch(value)) {
      throw FormatException(
        'Learning progress $name has an invalid format.',
      );
    }
  }
}

class LearningProgressSummary {
  const LearningProgressSummary({
    required this.activitiesExplored,
    required this.activitiesCompleted,
    required this.subjectActivityCounts,
    required this.recentEvents,
    required this.recentTopics,
  });

  const LearningProgressSummary.empty()
      : activitiesExplored = 0,
        activitiesCompleted = 0,
        subjectActivityCounts = const {},
        recentEvents = const [],
        recentTopics = const [];

  final int activitiesExplored;
  final int activitiesCompleted;
  final Map<LearningDomain, int> subjectActivityCounts;
  final List<LearningProgressEvent> recentEvents;
  final List<String> recentTopics;

  bool get isEmpty => activitiesExplored == 0 && recentEvents.isEmpty;
}
