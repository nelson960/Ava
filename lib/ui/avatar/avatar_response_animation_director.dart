import '../../models/ava_performance_plan.dart';
import 'avatar_action_intent.dart';

typedef AvatarResponseActionDispatch = Future<void> Function(
  AvatarActionIntent intent,
  Duration now,
);

typedef AvatarResponseSourceCancellation = Future<void> Function(
  AvatarActionSource source,
  Duration now,
);

/// Keeps spoken responses on Ava's living idle and owns sample-clock lip sync.
///
/// Semantic expression, gaze, and gesture values remain validated protocol
/// data, but they do not currently drive response motion. Touch reactions stay
/// independent and continue to compose with the lip-sync channel.
class AvatarResponseAnimationDirector {
  AvatarResponseAnimationDirector({
    required Duration Function() now,
    required AvatarResponseActionDispatch dispatch,
    required AvatarResponseSourceCancellation cancelSource,
  })  : _now = now,
        _dispatch = dispatch,
        _cancelSource = cancelSource;

  final Duration Function() _now;
  final AvatarResponseActionDispatch _dispatch;
  final AvatarResponseSourceCancellation _cancelSource;

  String? _activeTurnId;
  int _intentSequence = 0;

  bool get isActive => _activeTurnId != null;

  void beginTurn(String turnId) {
    final normalized = turnId.trim();
    if (normalized.isEmpty) {
      throw ArgumentError.value(turnId, 'turnId', 'must not be empty');
    }
    if (_activeTurnId != null) {
      throw StateError(
        'Ava response turn $_activeTurnId is already controlling motion.',
      );
    }
    _activeTurnId = normalized;
    _intentSequence = 0;
  }

  Future<void> startSegment(
    AvaPerformanceSegment segment, {
    required Duration expectedDuration,
  }) async {
    _requireActiveTurn();
    segment.validate();
    if (expectedDuration < const Duration(milliseconds: 350)) {
      throw ArgumentError.value(
        expectedDuration,
        'expectedDuration',
        'must be at least 350ms',
      );
    }
  }

  /// Starts or corrects one continuous lip-sync trajectory from the audio
  /// playback sample clock.
  ///
  /// A correction uses [AvatarLipSyncTarget.audioPosition] and retargets the
  /// active layer. Individual phonemes are never installed as independent
  /// native animations.
  Future<void> synchronizeLipSync(AvatarLipSyncTarget target) {
    final turnId = _requireActiveTurn();
    return _dispatch(
      _intent(
        turnId: turnId,
        segmentId: 'speech',
        kind: AvatarActionKind.lipSync,
        source: AvatarActionSource.speech,
        claims: const {
          AvatarMotionChannel.lipSync: AvatarMotionBlendMode.override,
        },
        expectedDuration: target.remainingDuration,
        blendIn: const Duration(milliseconds: 45),
        blendOut: const Duration(milliseconds: 70),
        retargetGroup: 'response:$turnId:lipSync',
        target: target,
      ),
      _now(),
    );
  }

  /// Adds restrained visual attention only after the local canvas accepted a
  /// specialist-authored scene. Realtime cannot request this renderer action.
  Future<void> presentValidatedCanvas() {
    final turnId = _requireActiveTurn();
    return _dispatch(
      _intent(
        turnId: turnId,
        segmentId: 'validated_canvas',
        kind: AvatarActionKind.lookAt,
        source: AvatarActionSource.speech,
        claims: const {
          AvatarMotionChannel.gaze: AvatarMotionBlendMode.override,
        },
        expectedDuration: const Duration(milliseconds: 1200),
        blendIn: const Duration(milliseconds: 180),
        blendOut: const Duration(milliseconds: 260),
        retargetGroup: 'response:$turnId:canvasAttention',
        textParameters: const {'gaze': 'diagram'},
      ),
      _now(),
    );
  }

  Future<void> finishTurn() => _endTurn();

  Future<void> cancelTurn() => _endTurn();

  Future<void> _endTurn() async {
    _requireActiveTurn();
    await _cancelSource(AvatarActionSource.speech, _now());
    _activeTurnId = null;
  }

  AvatarActionIntent _intent({
    required String turnId,
    required String segmentId,
    required AvatarActionKind kind,
    required AvatarActionSource source,
    required Map<AvatarMotionChannel, AvatarMotionBlendMode> claims,
    required Duration expectedDuration,
    required Duration blendIn,
    required Duration blendOut,
    String? retargetGroup,
    Map<String, String> textParameters = const {},
    Map<String, double> numericParameters = const {},
    AvatarActionTarget? target,
  }) {
    _intentSequence += 1;
    return AvatarActionIntent(
      id: 'response:$turnId:$segmentId:${kind.name}:$_intentSequence',
      kind: kind,
      source: source,
      priority: AvatarActionPriority.lesson,
      channelClaims: claims,
      occurredAt: _now(),
      blendIn: blendIn,
      blendOut: blendOut,
      expectedDuration: expectedDuration,
      interruptionPolicy: AvatarInterruptionPolicy.interruptible,
      retargetPolicy: retargetGroup == null
          ? AvatarRetargetPolicy.never
          : AvatarRetargetPolicy.immediate,
      retargetGroup: retargetGroup,
      target: target,
      textParameters: textParameters,
      numericParameters: numericParameters,
    );
  }

  String _requireActiveTurn() {
    final turnId = _activeTurnId;
    if (turnId == null) {
      throw StateError('Ava has no active response animation turn.');
    }
    return turnId;
  }
}
