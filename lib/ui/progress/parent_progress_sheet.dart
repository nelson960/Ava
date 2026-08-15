import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/learning_progress.dart';
import '../../models/lesson_response.dart';
import '../../state/app_state.dart';
import '../design/ava_design.dart';

class ParentProgressSheet extends ConsumerWidget {
  const ParentProgressSheet({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final progress = ref.watch(learningProgressSummaryProvider);

    return Material(
      color: AvaColors.paper,
      child: SafeArea(
        top: false,
        child: Column(
          children: [
            const SizedBox(height: 10),
            const _Handle(),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 18, 12, 12),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Learning trail',
                          style: Theme.of(context).textTheme.headlineSmall,
                        ),
                        const SizedBox(height: 5),
                        Text(
                          'Activity evidence—not grades or guessed mastery.',
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: 'Close learning trail',
                    onPressed: () => Navigator.of(context).pop(),
                    style: IconButton.styleFrom(
                      foregroundColor: AvaColors.inkSoft,
                      backgroundColor: AvaColors.paperMuted,
                    ),
                    icon: const Icon(CupertinoIcons.xmark, size: 17),
                  ),
                ],
              ),
            ),
            Expanded(
              child: progress.when(
                data: (summary) => _ProgressContent(summary: summary),
                loading: () => const Center(
                  child: CircularProgressIndicator(color: AvaColors.teal),
                ),
                error: (error, _) => _ProgressError(error: error),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ProgressContent extends StatelessWidget {
  const _ProgressContent({required this.summary});

  final LearningProgressSummary summary;

  @override
  Widget build(BuildContext context) {
    if (summary.isEmpty) {
      return const _EmptyProgress();
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 6, 20, 28),
      children: [
        Row(
          children: [
            Expanded(
              child: _MetricCard(
                label: 'Explored',
                value: '${summary.activitiesExplored}',
                icon: CupertinoIcons.compass,
                color: AvaColors.blue,
                background: AvaColors.blueSoft,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _MetricCard(
                label: 'Completed',
                value: '${summary.activitiesCompleted}',
                icon: CupertinoIcons.check_mark_circled_solid,
                color: AvaColors.teal,
                background: AvaColors.tealSoft,
              ),
            ),
          ],
        ),
        const SizedBox(height: 22),
        const _SectionTitle('Recently explored'),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final topic in summary.recentTopics) _TopicChip(label: topic),
          ],
        ),
        const SizedBox(height: 24),
        const _SectionTitle('Subject activity'),
        const SizedBox(height: 10),
        _SubjectCard(counts: summary.subjectActivityCounts),
        const SizedBox(height: 24),
        const _SectionTitle('Recent activity'),
        const SizedBox(height: 8),
        _Timeline(events: summary.recentEvents),
      ],
    );
  }
}

class _MetricCard extends StatelessWidget {
  const _MetricCard({
    required this.label,
    required this.value,
    required this.icon,
    required this.color,
    required this.background,
  });

  final String label;
  final String value;
  final IconData icon;
  final Color color;
  final Color background;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(AvaRadii.card),
        border: Border.all(color: color.withValues(alpha: 0.16)),
      ),
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: AvaColors.white.withValues(alpha: 0.78),
              borderRadius: BorderRadius.circular(13),
            ),
            child: Icon(icon, color: color, size: 19),
          ),
          const SizedBox(width: 12),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                value,
                style: const TextStyle(
                  color: AvaColors.ink,
                  fontSize: 24,
                  height: 1,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                label,
                style: const TextStyle(
                  color: AvaColors.inkSoft,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _SubjectCard extends StatelessWidget {
  const _SubjectCard({required this.counts});

  final Map<LearningDomain, int> counts;

  @override
  Widget build(BuildContext context) {
    final entries = [
      (
        domain: LearningDomain.math,
        label: 'Math',
        color: AvaColors.coral,
        background: AvaColors.coralSoft,
      ),
      (
        domain: LearningDomain.science,
        label: 'Science',
        color: AvaColors.blue,
        background: AvaColors.blueSoft,
      ),
      (
        domain: LearningDomain.general,
        label: 'Curiosity',
        color: AvaColors.gold,
        background: AvaColors.goldSoft,
      ),
    ];
    final maximum = [
      1,
      ...entries.map((entry) => counts[entry.domain] ?? 0),
    ].reduce((left, right) => left > right ? left : right);

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AvaColors.white,
        borderRadius: BorderRadius.circular(AvaRadii.card),
        border: Border.all(color: AvaColors.line),
      ),
      child: Column(
        children: [
          for (var index = 0; index < entries.length; index += 1) ...[
            _SubjectRow(
              label: entries[index].label,
              count: counts[entries[index].domain] ?? 0,
              maximum: maximum,
              color: entries[index].color,
              background: entries[index].background,
            ),
            if (index != entries.length - 1) const SizedBox(height: 13),
          ],
        ],
      ),
    );
  }
}

class _SubjectRow extends StatelessWidget {
  const _SubjectRow({
    required this.label,
    required this.count,
    required this.maximum,
    required this.color,
    required this.background,
  });

  final String label;
  final int count;
  final int maximum;
  final Color color;
  final Color background;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        SizedBox(
          width: 66,
          child: Text(
            label,
            style: const TextStyle(
              color: AvaColors.ink,
              fontSize: 13,
              fontWeight: FontWeight.w700,
            ),
          ),
        ),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              minHeight: 9,
              value: count / maximum,
              color: color,
              backgroundColor: background,
            ),
          ),
        ),
        const SizedBox(width: 10),
        SizedBox(
          width: 20,
          child: Text(
            '$count',
            textAlign: TextAlign.right,
            style: const TextStyle(
              color: AvaColors.inkSoft,
              fontSize: 12,
              fontWeight: FontWeight.w800,
            ),
          ),
        ),
      ],
    );
  }
}

class _Timeline extends StatelessWidget {
  const _Timeline({required this.events});

  final List<LearningProgressEvent> events;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: AvaColors.white,
        borderRadius: BorderRadius.circular(AvaRadii.card),
        border: Border.all(color: AvaColors.line),
      ),
      child: Column(
        children: [
          for (var index = 0; index < events.length; index += 1) ...[
            _TimelineRow(event: events[index]),
            if (index != events.length - 1)
              const Divider(height: 1, indent: 54),
          ],
        ],
      ),
    );
  }
}

class _TimelineRow extends StatelessWidget {
  const _TimelineRow({required this.event});

  final LearningProgressEvent event;

  @override
  Widget build(BuildContext context) {
    final (icon, label, color, background) = switch (event.kind) {
      LearningProgressEventKind.activityOpened => (
          CupertinoIcons.play_fill,
          'Opened',
          AvaColors.blue,
          AvaColors.blueSoft,
        ),
      LearningProgressEventKind.activityCompleted => (
          CupertinoIcons.check_mark,
          'Completed',
          AvaColors.teal,
          AvaColors.tealSoft,
        ),
      LearningProgressEventKind.activityReset => (
          CupertinoIcons.refresh,
          'Tried again',
          AvaColors.gold,
          AvaColors.goldSoft,
        ),
      LearningProgressEventKind.valueChanged => (
          CupertinoIcons.slider_horizontal_3,
          'Explored',
          AvaColors.coral,
          AvaColors.coralSoft,
        ),
    };

    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 11, 12, 11),
      child: Row(
        children: [
          Container(
            width: 32,
            height: 32,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: background,
              borderRadius: BorderRadius.circular(11),
            ),
            child: Icon(icon, color: color, size: 15),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  event.topic,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: AvaColors.ink,
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  '$label · '
                  '${MaterialLocalizations.of(context).formatCompactDate(event.occurredAt)}',
                  style: const TextStyle(
                    color: AvaColors.inkFaint,
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _TopicChip extends StatelessWidget {
  const _TopicChip({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: AvaColors.paperMuted,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: AvaColors.line),
      ),
      child: Text(
        label,
        style: const TextStyle(
          color: AvaColors.inkSoft,
          fontSize: 12,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle(this.label);

  final String label;

  @override
  Widget build(BuildContext context) {
    return Text(
      label.toUpperCase(),
      style: const TextStyle(
        color: AvaColors.inkFaint,
        fontSize: 10,
        letterSpacing: 1.15,
        fontWeight: FontWeight.w800,
      ),
    );
  }
}

class _EmptyProgress extends StatelessWidget {
  const _EmptyProgress();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 64,
              height: 64,
              alignment: Alignment.center,
              decoration: const BoxDecoration(
                color: AvaColors.tealSoft,
                shape: BoxShape.circle,
              ),
              child: const Icon(
                CupertinoIcons.compass,
                color: AvaColors.teal,
                size: 28,
              ),
            ),
            const SizedBox(height: 16),
            Text(
              'A new learning trail',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 7),
            Text(
              'Activities explored with Ava will appear here. Nothing is '
              'treated as a grade.',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ],
        ),
      ),
    );
  }
}

class _ProgressError extends StatelessWidget {
  const _ProgressError({required this.error});

  final Object error;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Text(
          'Learning progress could not be loaded: $error',
          textAlign: TextAlign.center,
          style: const TextStyle(color: AvaColors.danger),
        ),
      ),
    );
  }
}

class _Handle extends StatelessWidget {
  const _Handle();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 42,
      height: 4,
      decoration: BoxDecoration(
        color: AvaColors.line,
        borderRadius: BorderRadius.circular(999),
      ),
    );
  }
}
