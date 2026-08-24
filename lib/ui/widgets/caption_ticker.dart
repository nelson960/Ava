import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/app_state.dart';

class CaptionTicker extends ConsumerStatefulWidget {
  const CaptionTicker({
    super.key,
    required this.dashboardVisible,
  });

  final bool dashboardVisible;

  @override
  ConsumerState<CaptionTicker> createState() => _CaptionTickerState();
}

class _CaptionTickerState extends ConsumerState<CaptionTicker> {
  Timer? _lingerTimer;
  String? _displayedCaption;
  String? _sourceCaption;

  @override
  void dispose() {
    _lingerTimer?.cancel();
    super.dispose();
  }

  void _scheduleFadeOut() {
    _lingerTimer?.cancel();
    _lingerTimer = Timer(const Duration(milliseconds: 1400), () {
      if (mounted) {
        setState(() {
          _displayedCaption = null;
        });
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final enabled = ref.watch(
      studentProvider.select((student) => student.captionsEnabled),
    );
    final currentCaption = ref
        .watch(
          avaRealtimePresentationProvider,
        )
        .state
        .caption;

    if (!enabled) {
      _displayedCaption = null;
      _sourceCaption = null;
      _lingerTimer?.cancel();
    } else if (currentCaption != null && currentCaption.isNotEmpty) {
      _sourceCaption = currentCaption;
      _displayedCaption = currentCaption;
      _lingerTimer?.cancel();
    } else if (_sourceCaption != null) {
      _sourceCaption = null;
      if (_displayedCaption != null) {
        _scheduleFadeOut();
      }
    }

    final caption = enabled ? _displayedCaption : null;

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
                      color: widget.dashboardVisible
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
