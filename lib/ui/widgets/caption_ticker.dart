import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/app_state.dart';

class CaptionTicker extends ConsumerWidget {
  const CaptionTicker({
    super.key,
    required this.dashboardVisible,
  });

  final bool dashboardVisible;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final enabled = ref.watch(
      studentProvider.select((student) => student.captionsEnabled),
    );
    final realtimePresentation = ref.watch(avaRealtimePresentationProvider);
    final caption = realtimePresentation.state.caption;

    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 180),
      child: !enabled || caption == null
          ? const SizedBox.shrink()
          : Semantics(
              liveRegion: true,
              label: 'Ava says: $caption',
              child: Padding(
                key: ValueKey<String>(caption),
                padding: const EdgeInsets.only(left: 70, right: 22, bottom: 18),
                child: Align(
                  alignment: Alignment.bottomCenter,
                  child: Text(
                    caption,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: dashboardVisible
                          ? const Color(0xFF303440)
                          : const Color(0xFF29313C),
                      fontSize: 15,
                      height: 1.35,
                      fontWeight: FontWeight.w700,
                      shadows: const [
                        Shadow(
                          color: Colors.white,
                          blurRadius: 8,
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
    );
  }
}
