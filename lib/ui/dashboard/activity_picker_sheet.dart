import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

import '../../models/diagram_spec.dart';
import '../../models/local_learning_activity.dart';
import '../design/ava_design.dart';

class ActivityPickerSheet extends StatelessWidget {
  const ActivityPickerSheet({
    super.key,
    required this.activities,
    required this.onSelected,
    required this.onOpenProgress,
  });

  final List<LocalLearningActivity> activities;
  final ValueChanged<LocalLearningActivity> onSelected;
  final VoidCallback onOpenProgress;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AvaColors.paper,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 10, 20, 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Align(
                child: _Handle(),
              ),
              const SizedBox(height: 18),
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Try something together',
                          style: Theme.of(context).textTheme.headlineSmall,
                        ),
                        const SizedBox(height: 5),
                        Text(
                          'Short activities that work right on Ava’s board.',
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: 'Close activities',
                    onPressed: () => Navigator.of(context).pop(),
                    style: IconButton.styleFrom(
                      foregroundColor: AvaColors.inkSoft,
                      backgroundColor: AvaColors.paperMuted,
                    ),
                    icon: const Icon(CupertinoIcons.xmark, size: 17),
                  ),
                ],
              ),
              const SizedBox(height: 18),
              SizedBox(
                height: 210,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: activities.length,
                  separatorBuilder: (_, __) => const SizedBox(width: 12),
                  itemBuilder: (context, index) {
                    final activity = activities[index];
                    return _ActivityCard(
                      activity: activity,
                      onPressed: () => onSelected(activity),
                    );
                  },
                ),
              ),
              const SizedBox(height: 14),
              Center(
                child: TextButton.icon(
                  key: const ValueKey('open-learning-trail'),
                  onPressed: onOpenProgress,
                  style: TextButton.styleFrom(
                    foregroundColor: AvaColors.inkSoft,
                  ),
                  icon: const Icon(CupertinoIcons.chart_bar, size: 16),
                  label: const Text('Grown-up learning trail'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ActivityCard extends StatelessWidget {
  const _ActivityCard({
    required this.activity,
    required this.onPressed,
  });

  final LocalLearningActivity activity;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final palette = _palette(activity.tone);
    return SizedBox(
      width: 184,
      child: Material(
        color: palette.background,
        clipBehavior: Clip.antiAlias,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AvaRadii.card),
          side: BorderSide(color: palette.foreground.withValues(alpha: 0.18)),
        ),
        child: InkWell(
          key: ValueKey<String>('activity-${activity.id}'),
          onTap: onPressed,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 38,
                  height: 38,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: AvaColors.white.withValues(alpha: 0.78),
                    borderRadius: BorderRadius.circular(13),
                  ),
                  child: Icon(
                    _iconFor(activity.diagram.kind),
                    color: palette.foreground,
                    size: 19,
                  ),
                ),
                const Spacer(),
                Text(
                  activity.eyebrow.toUpperCase(),
                  style: TextStyle(
                    color: palette.foreground,
                    fontSize: 10,
                    letterSpacing: 1.05,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 5),
                Text(
                  activity.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: AvaColors.ink,
                    fontSize: 17,
                    height: 1.15,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 9),
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        'Open activity',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: palette.foreground,
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    const SizedBox(width: 5),
                    Icon(
                      CupertinoIcons.arrow_right,
                      color: palette.foreground,
                      size: 13,
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  static IconData _iconFor(DiagramKind kind) => switch (kind) {
        DiagramKind.fractionCircle => CupertinoIcons.circle_grid_hex,
        DiagramKind.fractionBar => CupertinoIcons.rectangle_split_3x1,
        DiagramKind.numberLine => CupertinoIcons.slider_horizontal_3,
      };

  static ({Color background, Color foreground}) _palette(ActivityTone tone) {
    return switch (tone) {
      ActivityTone.coral => (
          background: AvaColors.coralSoft,
          foreground: AvaColors.coral,
        ),
      ActivityTone.blue => (
          background: AvaColors.blueSoft,
          foreground: AvaColors.blue,
        ),
      ActivityTone.gold => (
          background: AvaColors.goldSoft,
          foreground: AvaColors.gold,
        ),
      ActivityTone.teal => (
          background: AvaColors.tealSoft,
          foreground: AvaColors.teal,
        ),
    };
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
