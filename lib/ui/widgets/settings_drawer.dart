import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/app_state.dart';
import '../../services/ava_memory_control_service.dart';

class SettingsDrawer extends ConsumerStatefulWidget {
  const SettingsDrawer({super.key});

  @override
  ConsumerState<SettingsDrawer> createState() => _SettingsDrawerState();
}

class _SettingsDrawerState extends ConsumerState<SettingsDrawer> {
  static const _ink = Color(0xFF263238);
  static const _mutedInk = Color(0xFF6F7B7D);
  static const _accent = Color(0xFF4F7D79);
  static const _surface = Color(0xFFF4F6F4);

  late final TextEditingController _nameController;
  late final FocusNode _nameFocus;
  bool _weeklyRecapLoading = false;

  @override
  void initState() {
    super.initState();
    _nameController = TextEditingController(
      text: ref.read(studentProvider).name,
    );
    _nameFocus = FocusNode();
  }

  @override
  void dispose() {
    _nameController.dispose();
    _nameFocus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final student = ref.watch(studentProvider);
    final drawerWidth = MediaQuery.sizeOf(context).width.clamp(0, 388.0) * 0.9;

    if (!_nameFocus.hasFocus && _nameController.text != student.name) {
      _nameController.value = TextEditingValue(
        text: student.name,
        selection: TextSelection.collapsed(offset: student.name.length),
      );
    }

    return Drawer(
      width: drawerWidth,
      elevation: 0,
      shadowColor: Colors.black26,
      surfaceTintColor: Colors.transparent,
      backgroundColor: _surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.only(
          topLeft: Radius.circular(30),
          bottomLeft: Radius.circular(30),
        ),
      ),
      child: SafeArea(
        child: Column(
          children: [
            _SettingsHeader(
              onClose: () => unawaited(_closeSettings()),
            ),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(22, 10, 22, 28),
                children: [
                  const _SectionLabel('Child profile'),
                  const SizedBox(height: 10),
                  _SettingsCard(
                    children: [
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
                        child: TextField(
                          key: const ValueKey('settings-child-name'),
                          controller: _nameController,
                          focusNode: _nameFocus,
                          textCapitalization: TextCapitalization.words,
                          textInputAction: TextInputAction.done,
                          maxLength: 100,
                          style: const TextStyle(
                            color: _ink,
                            fontSize: 16,
                            fontWeight: FontWeight.w600,
                          ),
                          cursorColor: _accent,
                          decoration: InputDecoration(
                            labelText: 'Name',
                            labelStyle: const TextStyle(color: _mutedInk),
                            counterText: '',
                            errorText: student.errorMessage,
                            prefixIcon: const Icon(
                              CupertinoIcons.person,
                              color: _accent,
                              size: 20,
                            ),
                            filled: true,
                            fillColor: const Color(0xFFF7F9F7),
                            contentPadding: const EdgeInsets.symmetric(
                              horizontal: 14,
                              vertical: 15,
                            ),
                            enabledBorder:
                                _fieldBorder(const Color(0xFFDCE3E0)),
                            focusedBorder: _fieldBorder(_accent, width: 1.5),
                            errorBorder: _fieldBorder(const Color(0xFFB95D64)),
                            focusedErrorBorder: _fieldBorder(
                                const Color(0xFFB95D64),
                                width: 1.5),
                          ),
                          onSubmitted: (value) => unawaited(_saveName(value)),
                          onTapOutside: (_) {
                            _nameFocus.unfocus();
                            unawaited(_saveName(_nameController.text));
                          },
                        ),
                      ),
                      const Divider(
                        height: 1,
                        indent: 16,
                        endIndent: 16,
                        color: Color(0xFFE6EAE8),
                      ),
                      _DateOfBirthTile(
                        dateOfBirth: student.dateOfBirth,
                        onPressed: () => unawaited(_chooseDateOfBirth()),
                      ),
                    ],
                  ),
                  const SizedBox(height: 24),
                  const _SectionLabel('Display'),
                  const SizedBox(height: 10),
                  _SettingsCard(
                    children: [
                      SwitchListTile.adaptive(
                        key: const ValueKey('settings-captions'),
                        contentPadding: const EdgeInsets.fromLTRB(16, 5, 12, 5),
                        secondary: const _LeadingIcon(
                          icon: CupertinoIcons.captions_bubble,
                        ),
                        title: const Text(
                          'Captions',
                          style: TextStyle(
                            color: _ink,
                            fontSize: 15,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        subtitle: const Text(
                          'Show Ava’s words on screen',
                          style: TextStyle(
                            color: _mutedInk,
                            fontSize: 12,
                          ),
                        ),
                        activeTrackColor: _accent,
                        value: student.captionsEnabled,
                        onChanged: (enabled) =>
                            unawaited(_setCaptionsEnabled(enabled)),
                      ),
                    ],
                  ),
                  const SizedBox(height: 24),
                  const _SectionLabel('Conversation'),
                  const SizedBox(height: 10),
                  _SettingsCard(
                    children: [
                      ListTile(
                        key: const ValueKey('settings-weekly-recap'),
                        contentPadding: const EdgeInsets.fromLTRB(16, 5, 12, 5),
                        leading: const _LeadingIcon(
                          icon: CupertinoIcons.calendar_today,
                        ),
                        title: const Text(
                          'Weekly recap',
                          style: TextStyle(
                            color: _ink,
                            fontSize: 15,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        subtitle: const Text(
                          'A short list of recent topics',
                          style: TextStyle(color: _mutedInk, fontSize: 12),
                        ),
                        trailing: _weeklyRecapLoading
                            ? const SizedBox.square(
                                dimension: 18,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: _accent,
                                ),
                              )
                            : const Icon(
                                CupertinoIcons.chevron_forward,
                                color: Color(0xFF95A09E),
                                size: 16,
                              ),
                        onTap: _weeklyRecapLoading
                            ? null
                            : () => unawaited(_showWeeklyRecap()),
                      ),
                    ],
                  ),
                  const SizedBox(height: 28),
                  OutlinedButton.icon(
                    key: const ValueKey('settings-reset-conversations'),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: const Color(0xFFA74750),
                      backgroundColor: const Color(0xFFFFF7F6),
                      side: const BorderSide(color: Color(0xFFE9C8C9)),
                      minimumSize: const Size.fromHeight(52),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(16),
                      ),
                      textStyle: const TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    onPressed: () => unawaited(_confirmResetConversations()),
                    icon: const Icon(CupertinoIcons.refresh, size: 19),
                    label: const Text('Reset conversations'),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  static OutlineInputBorder _fieldBorder(Color color, {double width = 1}) {
    return OutlineInputBorder(
      borderRadius: BorderRadius.circular(14),
      borderSide: BorderSide(color: color, width: width),
    );
  }

  Future<void> _closeSettings() async {
    if (_nameController.text.trim() != ref.read(studentProvider).name) {
      final saved = await _saveName(_nameController.text);
      if (!saved) return;
    }
    if (!mounted) return;
    Navigator.of(context).pop();
  }

  Future<bool> _saveName(String name) async {
    try {
      await ref.read(studentProvider.notifier).updateName(name);
      return true;
    } catch (error) {
      if (mounted) {
        _showMessage('Could not save the name: $error');
      }
      return false;
    }
  }

  Future<void> _chooseDateOfBirth() async {
    _nameFocus.unfocus();
    final student = ref.read(studentProvider);
    final now = DateTime.now();
    final initialDate =
        student.dateOfBirth ?? DateTime(now.year - 8, now.month, now.day);
    final selected = await showDatePicker(
      context: context,
      initialDate: initialDate,
      firstDate: DateTime(1900),
      lastDate: DateTime(now.year, now.month, now.day),
      helpText: 'Date of birth',
      builder: (context, child) => Theme(
        data: ThemeData(
          useMaterial3: true,
          colorScheme: ColorScheme.fromSeed(
            seedColor: _accent,
            brightness: Brightness.light,
          ),
        ),
        child: child!,
      ),
    );
    if (selected == null || !mounted) return;

    try {
      await ref.read(studentProvider.notifier).updateDateOfBirth(selected);
    } catch (error) {
      if (!mounted) return;
      _showMessage('Could not save the date of birth: $error');
    }
  }

  Future<void> _setCaptionsEnabled(bool enabled) async {
    try {
      await ref.read(studentProvider.notifier).updateCaptionsEnabled(enabled);
    } catch (error) {
      if (!mounted) return;
      _showMessage('Could not update captions: $error');
    }
  }

  Future<void> _confirmResetConversations() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: const Color(0xFFF8FAF8),
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(24),
        ),
        title: const Text(
          'Reset conversations?',
          style: TextStyle(color: _ink, fontWeight: FontWeight.w800),
        ),
        content: const Text(
          'Ava will forget previous conversations and recaps. The child profile and app settings will stay unchanged.',
          style: TextStyle(color: _mutedInk, height: 1.45),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: const Color(0xFFA74750),
              foregroundColor: Colors.white,
            ),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Reset'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    try {
      await ref.read(avaSessionProvider.notifier).disconnect();
      await ref.read(avaMemoryControlProvider).resetConversations();
      await ref.read(studentProvider.notifier).clearConversationHistory();
      await ref.read(avaCanvasPersistenceProvider).reset();
      ref.read(avaSvgCanvasSessionHostProvider.notifier).clearWorkspace();
      if (!mounted) return;
      _showMessage('Conversations reset.');
    } catch (error) {
      if (!mounted) return;
      _showMessage('Could not reset conversations: $error');
    }
  }

  Future<void> _showWeeklyRecap() async {
    setState(() => _weeklyRecapLoading = true);
    try {
      final recap = await ref.read(avaMemoryControlProvider).loadWeeklyRecap();
      if (!mounted) return;
      setState(() => _weeklyRecapLoading = false);
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        showDragHandle: true,
        backgroundColor: const Color(0xFFF8FAF8),
        builder: (context) => FractionallySizedBox(
          heightFactor: 0.75,
          child: _WeeklyRecapSheet(recap: recap),
        ),
      );
    } catch (error) {
      if (!mounted) return;
      _showMessage('Could not load the weekly recap: $error');
    } finally {
      if (mounted && _weeklyRecapLoading) {
        setState(() => _weeklyRecapLoading = false);
      }
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }
}

class _SettingsHeader extends StatelessWidget {
  const _SettingsHeader({required this.onClose});

  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 18, 14, 16),
      child: Row(
        children: [
          const Expanded(
            child: Text(
              'Settings',
              style: TextStyle(
                color: _SettingsDrawerState._ink,
                fontSize: 25,
                letterSpacing: -0.5,
                fontWeight: FontWeight.w800,
              ),
            ),
          ),
          IconButton.filledTonal(
            tooltip: 'Close settings',
            style: IconButton.styleFrom(
              foregroundColor: _SettingsDrawerState._ink,
              backgroundColor: const Color(0xFFE7ECE9),
            ),
            onPressed: onClose,
            icon: const Icon(CupertinoIcons.xmark, size: 18),
          ),
        ],
      ),
    );
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text.toUpperCase(),
      style: const TextStyle(
        color: _SettingsDrawerState._mutedInk,
        fontSize: 11,
        letterSpacing: 1.2,
        fontWeight: FontWeight.w800,
      ),
    );
  }
}

class _SettingsCard extends StatelessWidget {
  const _SettingsCard({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white.withValues(alpha: 0.92),
      elevation: 1,
      shadowColor: const Color(0x18263238),
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(20),
        side: const BorderSide(color: Color(0xFFDCE3E0)),
      ),
      child: Column(children: children),
    );
  }
}

class _DateOfBirthTile extends StatelessWidget {
  const _DateOfBirthTile({
    required this.dateOfBirth,
    required this.onPressed,
  });

  final DateTime? dateOfBirth;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final value = dateOfBirth == null
        ? 'Add date of birth'
        : MaterialLocalizations.of(context).formatCompactDate(dateOfBirth!);

    return ListTile(
      key: const ValueKey('settings-date-of-birth'),
      contentPadding: const EdgeInsets.fromLTRB(16, 5, 12, 5),
      leading: const _LeadingIcon(icon: CupertinoIcons.calendar),
      title: const Text(
        'Date of birth',
        style: TextStyle(
          color: _SettingsDrawerState._ink,
          fontSize: 15,
          fontWeight: FontWeight.w700,
        ),
      ),
      subtitle: Text(
        value,
        style: TextStyle(
          color: dateOfBirth == null
              ? _SettingsDrawerState._mutedInk
              : _SettingsDrawerState._accent,
          fontSize: 12,
          fontWeight: dateOfBirth == null ? FontWeight.w400 : FontWeight.w700,
        ),
      ),
      trailing: const Icon(
        CupertinoIcons.chevron_forward,
        color: Color(0xFF95A09E),
        size: 16,
      ),
      onTap: onPressed,
    );
  }
}

class _LeadingIcon extends StatelessWidget {
  const _LeadingIcon({required this.icon});

  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 38,
      height: 38,
      decoration: BoxDecoration(
        color: const Color(0xFFE8F0ED),
        borderRadius: BorderRadius.circular(12),
      ),
      alignment: Alignment.center,
      child: Icon(
        icon,
        color: _SettingsDrawerState._accent,
        size: 19,
      ),
    );
  }
}

class _WeeklyRecapSheet extends StatelessWidget {
  const _WeeklyRecapSheet({required this.recap});

  final AvaWeeklyConversationRecap recap;

  @override
  Widget build(BuildContext context) {
    final localizations = MaterialLocalizations.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 4, 24, 28),
        child: Column(
          mainAxisSize: MainAxisSize.max,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Weekly recap',
              style: TextStyle(
                color: _SettingsDrawerState._ink,
                fontSize: 22,
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              _formatWeeklyRecapRange(localizations, recap),
              style: const TextStyle(
                color: _SettingsDrawerState._mutedInk,
                fontSize: 13,
              ),
            ),
            const SizedBox(height: 18),
            if (recap.records.isEmpty)
              const Expanded(
                child: Align(
                  alignment: Alignment.topLeft,
                  child: Text(
                    'No conversations are recorded for this week yet.',
                    style: TextStyle(
                      color: _SettingsDrawerState._mutedInk,
                      height: 1.4,
                    ),
                  ),
                ),
              )
            else
              Expanded(
                child: ListView.separated(
                  key: const ValueKey('weekly-recap-list'),
                  itemCount: recap.records.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 10),
                  itemBuilder: (context, index) {
                    final record = recap.records[index];
                    return Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Container(
                          width: 7,
                          height: 7,
                          margin: const EdgeInsets.only(top: 7, right: 12),
                          decoration: const BoxDecoration(
                            color: _SettingsDrawerState._accent,
                            shape: BoxShape.circle,
                          ),
                        ),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                record.summary,
                                style: const TextStyle(
                                  color: _SettingsDrawerState._ink,
                                  fontSize: 14,
                                  height: 1.4,
                                ),
                              ),
                              const SizedBox(height: 3),
                              Text(
                                _formatWeeklyRecapTimestamp(
                                  localizations,
                                  record.occurredAt,
                                ),
                                key: ValueKey(
                                  'weekly-recap-time-${record.turnId}',
                                ),
                                style: const TextStyle(
                                  color: _SettingsDrawerState._mutedInk,
                                  fontSize: 11,
                                  fontWeight: FontWeight.w500,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    );
                  },
                ),
              ),
          ],
        ),
      ),
    );
  }
}

String _formatWeeklyRecapRange(
  MaterialLocalizations localizations,
  AvaWeeklyConversationRecap recap,
) {
  final start = recap.periodStart;
  final end = recap.periodEnd.subtract(const Duration(days: 1));
  final startLabel = localizations.formatShortMonthDay(start);
  final endLabel = localizations.formatShortMonthDay(end);
  if (start.year == end.year) {
    return '$startLabel – $endLabel ${localizations.formatYear(end)}';
  }
  return '$startLabel ${localizations.formatYear(start)} – '
      '$endLabel ${localizations.formatYear(end)}';
}

String _formatWeeklyRecapTimestamp(
  MaterialLocalizations localizations,
  DateTime occurredAt,
) {
  final local = occurredAt.toLocal();
  return '${localizations.formatShortDate(local)} · '
      '${localizations.formatTimeOfDay(TimeOfDay.fromDateTime(local))}';
}
