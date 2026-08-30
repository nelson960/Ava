import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/cupertino.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../services/ava_realtime_presentation.dart';
import '../models/ava_realtime_protocol_v4.dart';
import '../state/app_state.dart';
import '../state/ava_session_state.dart';
import 'avatar/avatar_view.dart';
import 'dashboard/ava_svg_canvas_panel.dart';
import 'dashboard/canvas_generation_pill.dart';
import 'dashboard/canvas_reopen_chip.dart';
import 'design/ava_design.dart';
import 'widgets/caption_ticker.dart';
import 'widgets/settings_drawer.dart';
import 'world/living_world.dart';

class MainScreen extends ConsumerStatefulWidget {
  const MainScreen({super.key});

  @override
  ConsumerState<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends ConsumerState<MainScreen>
    with TickerProviderStateMixin {
  final _scaffoldKey = GlobalKey<ScaffoldState>();
  late final LivingWorldController _livingWorldController;

  @override
  void initState() {
    super.initState();
    _livingWorldController = LivingWorldController(vsync: this);
    if (ref.read(avaBackendConfigurationProvider).isConfigured) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        unawaited(
          ref
              .read(avaSessionProvider.notifier)
              .prepare()
              .catchError((Object error, StackTrace stackTrace) {
            FlutterError.reportError(
              FlutterErrorDetails(
                exception: error,
                stack: stackTrace,
                library: 'ava_realtime',
                context: ErrorDescription(
                  'while preparing Ava\'s persistent conversation',
                ),
              ),
            );
          }),
        );
      });
    }
  }

  @override
  void dispose() {
    _livingWorldController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    ref.listen(
      avaRealtimePresentationProvider.select(
        (controller) => controller.state.issues,
      ),
      (previous, next) {
        if (next.length <= (previous?.length ?? 0)) return;
        final issue = next.last;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (!mounted) return;
          if (issue.code ==
              AvaRealtimePresentationIssueCode.memoryUnavailable) {
            return;
          }
          if (issue.code == AvaRealtimePresentationIssueCode.unknownPhoneme) {
            FlutterError.reportError(
              FlutterErrorDetails(
                exception: StateError(issue.message),
                library: 'ava_realtime_presentation',
                context: ErrorDescription(
                  'while mapping speech alignment to Ava visemes',
                ),
              ),
            );
            return;
          }
          ScaffoldMessenger.of(context)
            ..hideCurrentSnackBar()
            ..showSnackBar(SnackBar(content: Text(issue.message)));
        });
      },
    );
    ref.listen(
      avaSvgCanvasSessionHostProvider.select(
        (host) => host.controller?.lastIssue,
      ),
      (previous, next) {
        if (next == null || identical(previous, next)) return;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (!mounted) return;
          ScaffoldMessenger.of(context)
            ..hideCurrentSnackBar()
            ..showSnackBar(SnackBar(content: Text(next.message)));
        });
      },
    );
    final session = ref.watch(avaSessionProvider);
    final canvasHost = ref.watch(avaSvgCanvasSessionHostProvider);
    final dashboardVisible = canvasHost.panelVisible;
    final backendConfigured =
        ref.watch(avaBackendConfigurationProvider).isConfigured;

    return Scaffold(
      key: _scaffoldKey,
      endDrawer: const SettingsDrawer(),
      body: LayoutBuilder(
        builder: (context, constraints) {
          final dashboardHeight = constraints.maxHeight * 0.5;
          final viewPadding = MediaQuery.viewPaddingOf(context);
          final workspaceTop = constraints.maxHeight - dashboardHeight;
          final safeTop = math.max(0.0, viewPadding.top - workspaceTop);
          canvasHost.updateRenderProfile(
            AvaCanvasRenderProfileV4.measure(
              workspaceWidthPoints: constraints.maxWidth,
              workspaceHeightPoints: dashboardHeight,
              safeTopPoints: safeTop,
              safeRightPoints: viewPadding.right,
              safeBottomPoints: viewPadding.bottom,
              safeLeftPoints: viewPadding.left,
              directTouch: !kIsWeb &&
                  (defaultTargetPlatform == TargetPlatform.iOS ||
                      defaultTargetPlatform == TargetPlatform.android),
              textScaleFactor: MediaQuery.textScalerOf(context).scale(1),
            ),
          );
          final canvasGenerating = canvasHost.generating;
          final canReopenCanvas = canvasHost.canReopen;

          return Stack(
            fit: StackFit.expand,
            children: [
              LivingWorldBackdrop(controller: _livingWorldController),
              const RepaintBoundary(child: AvatarView()),
              LivingWorldInteractionLayer(
                controller: _livingWorldController,
              ),
              AnimatedPositioned(
                duration: AvaDurations.sheet,
                curve: Curves.easeOutCubic,
                left: 0,
                right: 0,
                height: dashboardHeight,
                bottom: dashboardVisible ? 0 : -dashboardHeight,
                child: AvaSvgCanvasPanel(host: canvasHost),
              ),
              AnimatedPositioned(
                duration: AvaDurations.sheet,
                curve: Curves.easeOutCubic,
                left: 0,
                right: 0,
                bottom: dashboardVisible ? dashboardHeight : 0,
                child: IgnorePointer(
                  child: CaptionTicker(
                    dashboardVisible: dashboardVisible,
                  ),
                ),
              ),
              if (session.errorMessage case final message?)
                Positioned(
                  left: 72,
                  right: 18,
                  bottom: 18,
                  child: _SessionErrorBanner(
                    message: message,
                    onDismiss: () =>
                        ref.read(avaSessionProvider.notifier).clearError(),
                  ),
                ),
              if (session.errorMessage == null && session.memoryWarning != null)
                Positioned(
                  left: 72,
                  right: 18,
                  bottom: 18,
                  child: _SessionWarningBanner(
                    message: session.memoryWarning!,
                    onDismiss: () => ref
                        .read(avaSessionProvider.notifier)
                        .clearMemoryWarning(),
                  ),
                ),
              Positioned(
                left: 16,
                bottom: 16,
                child: _VoiceButton(
                  microphoneActive: session.userMicIntent,
                  backendConfigured: backendConfigured,
                  onPressed: _handleVoicePressed,
                ),
              ),
              Positioned(
                right: 16,
                bottom: 16,
                child: CanvasGenerationPill(
                  visible: canvasGenerating && !canReopenCanvas,
                ),
              ),
              Positioned(
                right: 16,
                bottom: 16,
                child: CanvasReopenChip(
                  visible: canReopenCanvas,
                  onPressed: canvasHost.reopenPanel,
                ),
              ),
              if (session.turnState != AvaSessionPhase.idle &&
                  session.turnState != AvaSessionPhase.failure)
                SafeArea(
                  child: Align(
                    alignment: Alignment.topCenter,
                    child: Padding(
                      padding: const EdgeInsets.only(top: 10),
                      child: _SessionStatusPill(phase: session.turnState),
                    ),
                  ),
                ),
              SafeArea(
                child: Align(
                  alignment: Alignment.topRight,
                  child: Padding(
                    padding: const EdgeInsets.only(top: 6, right: 10),
                    child: _SettingsButton(
                      onPressed: () =>
                          _scaffoldKey.currentState?.openEndDrawer(),
                    ),
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  Future<void> _handleVoicePressed() async {
    final session = ref.read(avaSessionProvider);
    final controller = ref.read(avaSessionProvider.notifier);
    final configuration = ref.read(avaBackendConfigurationProvider);
    if (!configuration.isConfigured) {
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(
          const SnackBar(
            content: Text(
              'Ava needs AVA_API_BASE_URL before live conversation can start.',
            ),
          ),
        );
      return;
    }
    try {
      await controller.setMicrophoneActive(!session.userMicIntent);
    } catch (_) {
      if (!mounted) return;
      _showSessionError();
    }
  }

  void _showSessionError() {
    final message = ref.read(avaSessionProvider).errorMessage;
    if (message == null) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }
}

class _SessionStatusPill extends StatefulWidget {
  const _SessionStatusPill({required this.phase});

  final AvaSessionPhase phase;

  @override
  State<_SessionStatusPill> createState() => _SessionStatusPillState();
}

class _SessionStatusPillState extends State<_SessionStatusPill> {
  int _transitionRevision = 0;

  @override
  void didUpdateWidget(covariant _SessionStatusPill oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.phase != widget.phase) {
      _transitionRevision += 1;
    }
  }

  @override
  Widget build(BuildContext context) {
    final phase = widget.phase;
    final (icon, label) = switch (phase) {
      AvaSessionPhase.listening => (CupertinoIcons.waveform, 'Listening'),
      AvaSessionPhase.thinking => (CupertinoIcons.sparkles, 'Thinking'),
      AvaSessionPhase.speaking => (CupertinoIcons.speaker_2_fill, 'Speaking'),
      AvaSessionPhase.idle || AvaSessionPhase.failure => (
          CupertinoIcons.circle,
          ''
        ),
    };
    return AnimatedSwitcher(
      duration: AvaDurations.quick,
      child: Container(
        // A phase may recur before its previous transition has faded out
        // (thinking -> speaking -> thinking). Give each transition instance
        // its own identity so AnimatedSwitcher never retains duplicate keys.
        key: ValueKey((phase, _transitionRevision)),
        padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 8),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.86),
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: AvaColors.line),
          boxShadow: const [
            BoxShadow(
              color: Color(0x170F1B1D),
              blurRadius: 12,
              offset: Offset(0, 4),
            ),
          ],
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, color: AvaColors.teal, size: 14),
            const SizedBox(width: 7),
            Text(
              label,
              style: const TextStyle(
                color: AvaColors.ink,
                fontSize: 12,
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _VoiceButton extends StatefulWidget {
  const _VoiceButton({
    required this.microphoneActive,
    required this.backendConfigured,
    required this.onPressed,
  });

  final bool microphoneActive;
  final bool backendConfigured;
  final Future<void> Function() onPressed;

  @override
  State<_VoiceButton> createState() => _VoiceButtonState();
}

class _VoiceButtonState extends State<_VoiceButton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _glowController;
  late final Animation<double> _glowAnimation;

  @override
  void initState() {
    super.initState();
    _glowController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1600),
    );
    _glowAnimation = CurvedAnimation(
      parent: _glowController,
      curve: Curves.easeInOutSine,
    );
    if (widget.microphoneActive) {
      _glowController.repeat(reverse: true);
    }
  }

  @override
  void didUpdateWidget(_VoiceButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.microphoneActive != oldWidget.microphoneActive) {
      if (widget.microphoneActive) {
        _glowController.repeat(reverse: true);
      } else {
        _glowController.animateTo(
          0.0,
          duration: const Duration(milliseconds: 300),
        );
      }
    }
  }

  @override
  void dispose() {
    _glowController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final icon = widget.microphoneActive
        ? CupertinoIcons.mic_fill
        : CupertinoIcons.mic_slash_fill;
    final label =
        widget.microphoneActive ? 'Turn microphone off' : 'Turn microphone on';
    final color = widget.microphoneActive
        ? const Color(0xFFE9788C)
        : widget.backendConfigured
            ? const Color(0xFF44515B)
            : const Color(0xFF778084);

    return Semantics(
      button: true,
      label: label,
      child: Tooltip(
        message: widget.backendConfigured
            ? label
            : 'Model backend configuration required',
        child: AnimatedBuilder(
          animation: _glowAnimation,
          builder: (context, child) {
            final pulse = _glowAnimation.value;
            final isGlow = widget.microphoneActive;
            return Container(
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                boxShadow: isGlow
                    ? [
                        BoxShadow(
                          color: const Color(0xFFFFB38A).withValues(
                            alpha: 0.45 + (pulse * 0.35),
                          ),
                          blurRadius: 16 + (pulse * 10),
                          spreadRadius: 2 + (pulse * 3),
                        ),
                      ]
                    : const [],
              ),
              child: child,
            );
          },
          child: Material(
            color: Colors.white.withValues(alpha: 0.94),
            shape: const CircleBorder(),
            elevation: 5,
            child: InkWell(
              customBorder: const CircleBorder(),
              onTap: () => unawaited(widget.onPressed()),
              child: SizedBox(
                width: 46,
                height: 46,
                child: Icon(icon, color: color, size: 21),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _SessionErrorBanner extends StatelessWidget {
  const _SessionErrorBanner({
    required this.message,
    required this.onDismiss,
  });

  final String message;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: const Color(0xFF7A2E3A).withValues(alpha: 0.94),
      elevation: 5,
      borderRadius: BorderRadius.circular(14),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
        child: Row(
          children: [
            Expanded(
              child: Text(
                message,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            IconButton(
              tooltip: 'Dismiss error',
              onPressed: onDismiss,
              icon: const Icon(
                CupertinoIcons.xmark,
                size: 16,
                color: Colors.white70,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SessionWarningBanner extends StatelessWidget {
  const _SessionWarningBanner({
    required this.message,
    required this.onDismiss,
  });

  final String message;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: const Color(0xFF6D5725).withValues(alpha: 0.94),
      elevation: 5,
      borderRadius: BorderRadius.circular(14),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
        child: Row(
          children: [
            Expanded(
              child: Text(
                '$message Memory retry is required.',
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            IconButton(
              tooltip: 'Dismiss memory warning',
              onPressed: onDismiss,
              icon: const Icon(
                CupertinoIcons.xmark,
                size: 16,
                color: Colors.white70,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SettingsButton extends StatelessWidget {
  const _SettingsButton({required this.onPressed});

  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Open Ava Settings',
      button: true,
      child: Container(
        width: 38,
        height: 38,
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.84),
          shape: BoxShape.circle,
          border: Border.all(
            color: const Color(0xFF525A65).withValues(alpha: 0.1),
          ),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.07),
              blurRadius: 12,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: IconButton(
          onPressed: onPressed,
          padding: EdgeInsets.zero,
          tooltip: 'Ava Settings',
          icon: const _ThreeLineMenuIcon(),
        ),
      ),
    );
  }
}

class _ThreeLineMenuIcon extends StatelessWidget {
  const _ThreeLineMenuIcon();

  @override
  Widget build(BuildContext context) {
    const color = Color(0xFF46505B);
    return SizedBox(
      width: 18,
      height: 15,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: const [
          _MenuLine(width: 18, color: color),
          _MenuLine(width: 13, color: color),
          _MenuLine(width: 18, color: color),
        ],
      ),
    );
  }
}

class _MenuLine extends StatelessWidget {
  const _MenuLine({required this.width, required this.color});

  final double width;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      height: 2,
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(2),
      ),
    );
  }
}
