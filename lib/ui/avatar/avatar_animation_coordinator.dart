import 'dart:async';

enum AvatarAnimationPlaybackState {
  loading,
  idle,
  reacting,
  explaining,
  error,
}

enum AvatarBaseAnimation { idle, explaining }

class AvatarReactionAnimation {
  const AvatarReactionAnimation({
    required this.name,
    required this.completionTimeout,
    this.playbackRate = 1,
  });

  final String name;

  /// A failure deadline, not the reaction duration. Normal completion is
  /// driven by the 3D viewer's animation `finished` event.
  final Duration completionTimeout;
  final double playbackRate;
}

typedef AvatarAnimationPlayer = int Function({
  required String animationName,
  required int loopCount,
  required double playbackRate,
});

typedef AvatarAnimationFailureHandler = void Function(
  Object error,
  StackTrace stackTrace,
);

typedef AvatarTimerFactory = Timer Function(
  Duration duration,
  void Function() callback,
);

Timer _defaultTimerFactory(Duration duration, void Function() callback) {
  return Timer(duration, callback);
}

/// Owns Ava's event-driven animation transitions independently of the viewer.
///
/// Reactions return to the latest requested base animation only after the
/// viewer confirms that the finite clip completed. The timer is a watchdog
/// that surfaces a playback failure; it never guesses that a clip finished.
class AvatarAnimationCoordinator {
  AvatarAnimationCoordinator({
    required AvatarAnimationPlayer playAnimation,
    required String idleAnimation,
    required String explainingAnimation,
    required AvatarAnimationFailureHandler onPlaybackFailure,
    AvatarTimerFactory timerFactory = _defaultTimerFactory,
  })  : _playAnimation = playAnimation,
        _idleAnimation = idleAnimation,
        _explainingAnimation = explainingAnimation,
        _onPlaybackFailure = onPlaybackFailure,
        _timerFactory = timerFactory {
    if (idleAnimation.isEmpty || explainingAnimation.isEmpty) {
      throw ArgumentError('Base animation names must not be empty.');
    }
  }

  final AvatarAnimationPlayer _playAnimation;
  final String _idleAnimation;
  final String _explainingAnimation;
  final AvatarAnimationFailureHandler _onPlaybackFailure;
  final AvatarTimerFactory _timerFactory;

  Timer? _completionWatchdog;
  bool _ready = false;
  bool _disposed = false;
  AvatarBaseAnimation _baseAnimation = AvatarBaseAnimation.idle;
  AvatarAnimationPlaybackState _state = AvatarAnimationPlaybackState.loading;
  String? _activeAnimation;
  int? _activePlaybackId;

  AvatarAnimationPlaybackState get state => _state;
  String? get activeAnimation => _activeAnimation;
  int? get activePlaybackId => _activePlaybackId;
  bool get isReady => _ready && !_disposed;

  void markReady() {
    _ensureNotDisposed();
    _ready = true;
    _playBaseAnimation(force: true);
  }

  void setBaseAnimation(AvatarBaseAnimation animation) {
    _ensureNotDisposed();
    final changed = animation != _baseAnimation;
    _baseAnimation = animation;
    if (!_ready || _state == AvatarAnimationPlaybackState.reacting) {
      return;
    }
    if (changed) {
      _playBaseAnimation();
    }
  }

  void playReaction(AvatarReactionAnimation reaction) {
    _ensureNotDisposed();
    if (!_ready) {
      throw StateError(
        'Cannot play an avatar reaction before the model is ready.',
      );
    }
    if (reaction.name.isEmpty) {
      throw ArgumentError.value(
        reaction.name,
        'reaction.name',
        'must not be empty',
      );
    }
    if (reaction.completionTimeout <= Duration.zero) {
      throw ArgumentError.value(
        reaction.completionTimeout,
        'reaction.completionTimeout',
        'must be greater than zero',
      );
    }
    if (!reaction.playbackRate.isFinite || reaction.playbackRate <= 0) {
      throw ArgumentError.value(
        reaction.playbackRate,
        'reaction.playbackRate',
        'must be finite and greater than zero',
      );
    }

    _completionWatchdog?.cancel();
    final playbackId = _sendAnimation(
      animationName: reaction.name,
      loopCount: 1,
      playbackRate: reaction.playbackRate,
      nextState: AvatarAnimationPlaybackState.reacting,
    );
    _completionWatchdog = _timerFactory(reaction.completionTimeout, () {
      if (_disposed ||
          !_ready ||
          _state != AvatarAnimationPlaybackState.reacting ||
          playbackId != _activePlaybackId) {
        return;
      }
      final error = StateError(
        'Animation ${reaction.name} did not report completion within '
        '${reaction.completionTimeout.inMilliseconds} ms.',
      );
      final stackTrace = StackTrace.current;
      markError();
      _onPlaybackFailure(error, stackTrace);
    });
  }

  /// Stops a finite clip and immediately restores the requested base layer.
  ///
  /// Procedural reactions use this before taking over their own bone channels,
  /// preventing an older whole-body clip from fighting the new pose layer.
  bool cancelReactionAndResumeBase() {
    _ensureNotDisposed();
    if (!_ready || _state != AvatarAnimationPlaybackState.reacting) {
      return false;
    }
    _completionWatchdog?.cancel();
    _completionWatchdog = null;
    _playBaseAnimation(force: true);
    return true;
  }

  bool handleAnimationFinished({
    required int playbackId,
    required String animationName,
  }) {
    _ensureNotDisposed();
    if (!_ready ||
        _state != AvatarAnimationPlaybackState.reacting ||
        playbackId != _activePlaybackId) {
      return false;
    }
    if (animationName != _activeAnimation) {
      final error = StateError(
        'Playback $playbackId completed as $animationName, but '
        '$_activeAnimation was active.',
      );
      final stackTrace = StackTrace.current;
      markError();
      _onPlaybackFailure(error, stackTrace);
      return false;
    }

    _completionWatchdog?.cancel();
    _completionWatchdog = null;
    _playBaseAnimation(force: true);
    return true;
  }

  void markError() {
    if (_disposed) return;
    _completionWatchdog?.cancel();
    _completionWatchdog = null;
    _ready = false;
    _activeAnimation = null;
    _activePlaybackId = null;
    _state = AvatarAnimationPlaybackState.error;
  }

  void dispose() {
    if (_disposed) return;
    _disposed = true;
    _ready = false;
    _completionWatchdog?.cancel();
    _completionWatchdog = null;
    _activeAnimation = null;
    _activePlaybackId = null;
  }

  void _playBaseAnimation({bool force = false}) {
    final animationName = switch (_baseAnimation) {
      AvatarBaseAnimation.idle => _idleAnimation,
      AvatarBaseAnimation.explaining => _explainingAnimation,
    };
    final nextState = switch (_baseAnimation) {
      AvatarBaseAnimation.idle => AvatarAnimationPlaybackState.idle,
      AvatarBaseAnimation.explaining => AvatarAnimationPlaybackState.explaining,
    };
    if (!force && _state == nextState && _activeAnimation == animationName) {
      return;
    }
    _sendAnimation(
      animationName: animationName,
      loopCount: 0,
      playbackRate: 1,
      nextState: nextState,
    );
  }

  int _sendAnimation({
    required String animationName,
    required int loopCount,
    required double playbackRate,
    required AvatarAnimationPlaybackState nextState,
  }) {
    try {
      final playbackId = _playAnimation(
        animationName: animationName,
        loopCount: loopCount,
        playbackRate: playbackRate,
      );
      if (playbackId <= 0) {
        throw StateError(
          'Animation driver returned invalid playback id $playbackId.',
        );
      }
      _activeAnimation = animationName;
      _activePlaybackId = playbackId;
      _state = nextState;
      return playbackId;
    } catch (_) {
      markError();
      rethrow;
    }
  }

  void _ensureNotDisposed() {
    if (_disposed) {
      throw StateError('The avatar animation coordinator is disposed.');
    }
  }
}
