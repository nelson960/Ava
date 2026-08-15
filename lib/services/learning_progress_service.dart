import 'package:drift/drift.dart';

import '../models/diagram_interaction.dart';
import '../models/learning_progress.dart';
import '../models/lesson_response.dart';
import '../models/local_learning_activity.dart';
import 'memory_service.dart';

class LearningProgressService {
  LearningProgressService(
    this._database, {
    required String profileId,
    DateTime Function()? now,
  })  : _profileId = profileId,
        _now = now ?? DateTime.now {
    if (_profileId.trim().isEmpty) {
      throw ArgumentError.value(
        profileId,
        'profileId',
        'The learning progress profile id cannot be empty.',
      );
    }
  }

  final AppDatabase _database;
  final String _profileId;
  final DateTime Function() _now;

  Future<void> recordActivityOpened(LocalLearningActivity activity) {
    return _store(
      LearningProgressEvent.opened(
        profileId: _profileId,
        activity: activity,
        occurredAt: _now(),
      ),
    );
  }

  Future<void> recordInteraction({
    required LocalLearningActivity activity,
    required DiagramInteraction interaction,
  }) {
    return _store(
      LearningProgressEvent.fromInteraction(
        profileId: _profileId,
        activity: activity,
        interaction: interaction,
        occurredAt: _now(),
      ),
    );
  }

  Stream<LearningProgressSummary> watchSummary() {
    return _database
        .watchLearningProgressEvents(profileId: _profileId)
        .map(_summarize);
  }

  Future<void> _store(LearningProgressEvent event) async {
    event.validate();
    await _database.addLearningProgressEvent(
      LearningProgressEventsCompanion.insert(
        profileId: event.profileId,
        activityId: event.activityId,
        kind: event.kind.name,
        domain: event.domain.name,
        topic: event.topic.trim(),
        diagramKind: event.diagramKind.name,
        currentValue: Value(event.currentValue),
        targetValue: Value(event.targetValue),
        occurredAt: event.occurredAt,
      ),
    );
  }

  LearningProgressSummary _summarize(
    List<StoredLearningProgressEvent> stored,
  ) {
    if (stored.isEmpty) {
      return const LearningProgressSummary.empty();
    }
    final events = stored.map(_decode).toList(growable: false);
    final explored = <String>{};
    final completed = <String>{};
    final activitiesByDomain = <LearningDomain, Set<String>>{};
    final recentTopics = <String>[];
    final timeline = <LearningProgressEvent>[];

    for (final event in events) {
      if (event.kind == LearningProgressEventKind.activityOpened) {
        explored.add(event.activityId);
        activitiesByDomain
            .putIfAbsent(event.domain, () => <String>{})
            .add(event.activityId);
      }
      if (event.kind == LearningProgressEventKind.activityCompleted) {
        completed.add(event.activityId);
      }
      if (!recentTopics.contains(event.topic) && recentTopics.length < 4) {
        recentTopics.add(event.topic);
      }
      if (event.kind != LearningProgressEventKind.valueChanged &&
          timeline.length < 8) {
        timeline.add(event);
      }
    }

    return LearningProgressSummary(
      activitiesExplored: explored.length,
      activitiesCompleted: completed.length,
      subjectActivityCounts: {
        for (final entry in activitiesByDomain.entries)
          entry.key: entry.value.length,
      },
      recentEvents: List.unmodifiable(timeline),
      recentTopics: List.unmodifiable(recentTopics),
    );
  }

  LearningProgressEvent _decode(StoredLearningProgressEvent stored) {
    final event = LearningProgressEvent(
      kind: _enumByName(
        LearningProgressEventKind.values,
        stored.kind,
        'stored progress kind',
      ),
      profileId: stored.profileId,
      activityId: stored.activityId,
      domain: _enumByName(
        LearningDomain.values,
        stored.domain,
        'stored progress domain',
      ),
      topic: stored.topic,
      diagramKind: _enumByName(
        DiagramKind.values,
        stored.diagramKind,
        'stored progress diagram kind',
      ),
      occurredAt: stored.occurredAt,
      currentValue: stored.currentValue,
      targetValue: stored.targetValue,
    );
    event.validate();
    return event;
  }

  static T _enumByName<T extends Enum>(
    List<T> values,
    String raw,
    String context,
  ) {
    final matches = values.where((value) => value.name == raw);
    if (matches.length != 1) {
      throw StateError('$context has unsupported value "$raw".');
    }
    return matches.single;
  }
}
