import 'ava_realtime_protocol.dart';
import 'ava_svg_canvas_protocol.dart';

const avaRealtimeProtocolVersionV4 = 4;

enum AvaRealtimeServerEventTypeV4 {
  turnContextRequested,
  turnCreated,
  turnPhaseChanged,
  segmentReady,
  audioMetadata,
  audioChunk,
  alignmentBatch,
  interruptRequested,
  canvasGenerationStarted,
  canvasDraftAvailable,
  canvasDocumentAvailable,
  canvasPresentationCue,
  canvasActivated,
  canvasDocumentDiscarded,
  canvasRejected,
  canvasPresentationChanged,
  canvasPresentationCommand,
  interactionRejected,
  memoryPersistenceFailed,
  turnCompleted,
  turnCancelled,
  turnFailed,
}

final class AvaCanvasPresentationSnapshot {
  const AvaCanvasPresentationSnapshot({
    required this.visible,
    this.sceneId,
    this.focusRevision,
  });

  final bool visible;
  final String? sceneId;
  final int? focusRevision;
}

enum AvaCanvasOrientationV4 { portrait, landscape, square }

enum AvaCanvasLayoutClassV4 { compact, regular }

enum AvaCanvasTouchEnvironmentV4 { direct, none }

final class AvaCanvasNormalizedInsetsV4 {
  const AvaCanvasNormalizedInsetsV4._({
    required this.top,
    required this.right,
    required this.bottom,
    required this.left,
  });

  final double top;
  final double right;
  final double bottom;
  final double left;

  Map<String, Object?> toJson() => {
        'top': top,
        'right': right,
        'bottom': bottom,
        'left': left,
      };
}

/// Client-measured semantic composition context for the lower canvas panel.
///
/// Pixel dimensions never become SVG coordinates. The 600-point layout-class
/// boundary is Flutter's ordinary compact/regular layout breakpoint and is
/// applied independently to the measured usable width and height.
final class AvaCanvasRenderProfileV4 {
  const AvaCanvasRenderProfileV4._({
    required this.usableAspectRatio,
    required this.orientation,
    required this.widthClass,
    required this.heightClass,
    required this.safeInsets,
    required this.touchEnvironment,
    required this.minimumInteractionTargetPoints,
    required this.textScaleFactor,
  });

  static const double regularLayoutMinimumPoints = 600;
  static const double directTouchMinimumTargetPoints = 44;

  final double usableAspectRatio;
  final AvaCanvasOrientationV4 orientation;
  final AvaCanvasLayoutClassV4 widthClass;
  final AvaCanvasLayoutClassV4 heightClass;
  final AvaCanvasNormalizedInsetsV4 safeInsets;
  final AvaCanvasTouchEnvironmentV4 touchEnvironment;
  final double? minimumInteractionTargetPoints;
  final double textScaleFactor;

  factory AvaCanvasRenderProfileV4.measure({
    required double workspaceWidthPoints,
    required double workspaceHeightPoints,
    required double safeTopPoints,
    required double safeRightPoints,
    required double safeBottomPoints,
    required double safeLeftPoints,
    required bool directTouch,
    required double textScaleFactor,
  }) {
    final values = {
      'workspaceWidthPoints': workspaceWidthPoints,
      'workspaceHeightPoints': workspaceHeightPoints,
      'safeTopPoints': safeTopPoints,
      'safeRightPoints': safeRightPoints,
      'safeBottomPoints': safeBottomPoints,
      'safeLeftPoints': safeLeftPoints,
      'textScaleFactor': textScaleFactor,
    };
    for (final entry in values.entries) {
      if (!entry.value.isFinite) {
        throw ArgumentError.value(entry.value, entry.key, 'must be finite');
      }
    }
    if (workspaceWidthPoints <= 0 || workspaceHeightPoints <= 0) {
      throw const FormatException(
        'Canvas workspace dimensions must be positive.',
      );
    }
    if (safeTopPoints < 0 ||
        safeRightPoints < 0 ||
        safeBottomPoints < 0 ||
        safeLeftPoints < 0) {
      throw const FormatException('Canvas safe insets cannot be negative.');
    }
    final usableWidth = workspaceWidthPoints - safeLeftPoints - safeRightPoints;
    final usableHeight =
        workspaceHeightPoints - safeTopPoints - safeBottomPoints;
    if (usableWidth <= 0 || usableHeight <= 0) {
      throw const FormatException(
        'Canvas safe insets leave no usable presentation area.',
      );
    }
    final normalized = AvaCanvasNormalizedInsetsV4._(
      top: safeTopPoints / workspaceHeightPoints,
      right: safeRightPoints / workspaceWidthPoints,
      bottom: safeBottomPoints / workspaceHeightPoints,
      left: safeLeftPoints / workspaceWidthPoints,
    );
    final normalizedValues = [
      normalized.top,
      normalized.right,
      normalized.bottom,
      normalized.left,
    ];
    if (normalizedValues.any((value) => value > 0.4) ||
        normalized.left + normalized.right >= 0.8 ||
        normalized.top + normalized.bottom >= 0.8) {
      throw const FormatException(
        'Canvas safe insets exceed protocol-v4 bounds.',
      );
    }
    if (textScaleFactor < 0.75 || textScaleFactor > 3.0) {
      throw const FormatException(
        'Canvas text scale factor is outside protocol-v4 bounds.',
      );
    }
    final ratio = usableWidth / usableHeight;
    if (ratio < 0.25 || ratio > 4.0) {
      throw const FormatException(
        'Canvas usable aspect ratio is outside protocol-v4 bounds.',
      );
    }
    final orientation = ratio >= 1.05
        ? AvaCanvasOrientationV4.landscape
        : ratio <= 0.95
            ? AvaCanvasOrientationV4.portrait
            : AvaCanvasOrientationV4.square;
    return AvaCanvasRenderProfileV4._(
      usableAspectRatio: ratio,
      orientation: orientation,
      widthClass: usableWidth < regularLayoutMinimumPoints
          ? AvaCanvasLayoutClassV4.compact
          : AvaCanvasLayoutClassV4.regular,
      heightClass: usableHeight < regularLayoutMinimumPoints
          ? AvaCanvasLayoutClassV4.compact
          : AvaCanvasLayoutClassV4.regular,
      safeInsets: normalized,
      touchEnvironment: directTouch
          ? AvaCanvasTouchEnvironmentV4.direct
          : AvaCanvasTouchEnvironmentV4.none,
      minimumInteractionTargetPoints:
          directTouch ? directTouchMinimumTargetPoints : null,
      textScaleFactor: textScaleFactor,
    );
  }

  factory AvaCanvasRenderProfileV4.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'usableAspectRatio',
        'orientation',
        'widthClass',
        'heightClass',
        'safeInsets',
        'touchEnvironment',
        'minimumInteractionTargetPoints',
        'textScaleFactor',
      },
      'canvas render profile',
    );
    final ratio = _boundedFiniteNumber(
      json,
      'usableAspectRatio',
      'canvas render profile',
      minimum: 0.25,
      maximum: 4,
    );
    final orientation = _enumValue(
      AvaCanvasOrientationV4.values,
      _requiredString(json, 'orientation', 'canvas render profile'),
      'canvas render profile.orientation',
    );
    if ((orientation == AvaCanvasOrientationV4.portrait && ratio >= 1.05) ||
        (orientation == AvaCanvasOrientationV4.landscape && ratio <= 0.95) ||
        (orientation == AvaCanvasOrientationV4.square &&
            (ratio <= 0.95 || ratio >= 1.05))) {
      throw const FormatException(
        'Canvas render profile orientation does not match its aspect ratio.',
      );
    }
    final safeJson = _requiredObject(
      json['safeInsets'],
      'canvas render profile.safeInsets',
    );
    _requireExactKeys(
      safeJson,
      const {'top', 'right', 'bottom', 'left'},
      'canvas render profile.safeInsets',
    );
    double inset(String key) => _boundedFiniteNumber(
          safeJson,
          key,
          'canvas render profile.safeInsets',
          minimum: 0,
          maximum: 0.4,
        );
    final safeInsets = AvaCanvasNormalizedInsetsV4._(
      top: inset('top'),
      right: inset('right'),
      bottom: inset('bottom'),
      left: inset('left'),
    );
    if (safeInsets.left + safeInsets.right >= 0.8 ||
        safeInsets.top + safeInsets.bottom >= 0.8) {
      throw const FormatException(
        'Canvas render profile safe insets leave too little drawable area.',
      );
    }
    final touchEnvironment = _enumValue(
      AvaCanvasTouchEnvironmentV4.values,
      _requiredString(json, 'touchEnvironment', 'canvas render profile'),
      'canvas render profile.touchEnvironment',
    );
    final rawMinimumTarget = json['minimumInteractionTargetPoints'];
    final minimumTarget = rawMinimumTarget == null
        ? null
        : _boundedFiniteNumber(
            json,
            'minimumInteractionTargetPoints',
            'canvas render profile',
            minimum: 24,
            maximum: 96,
          );
    if ((touchEnvironment == AvaCanvasTouchEnvironmentV4.direct &&
            minimumTarget == null) ||
        (touchEnvironment == AvaCanvasTouchEnvironmentV4.none &&
            minimumTarget != null)) {
      throw const FormatException(
        'Canvas render profile touch environment and target size disagree.',
      );
    }
    return AvaCanvasRenderProfileV4._(
      usableAspectRatio: ratio,
      orientation: orientation,
      widthClass: _enumValue(
        AvaCanvasLayoutClassV4.values,
        _requiredString(json, 'widthClass', 'canvas render profile'),
        'canvas render profile.widthClass',
      ),
      heightClass: _enumValue(
        AvaCanvasLayoutClassV4.values,
        _requiredString(json, 'heightClass', 'canvas render profile'),
        'canvas render profile.heightClass',
      ),
      safeInsets: safeInsets,
      touchEnvironment: touchEnvironment,
      minimumInteractionTargetPoints: minimumTarget,
      textScaleFactor: _boundedFiniteNumber(
        json,
        'textScaleFactor',
        'canvas render profile',
        minimum: 0.75,
        maximum: 3,
      ),
    );
  }

  Map<String, Object?> toJson() => {
        'usableAspectRatio': usableAspectRatio,
        'orientation': orientation.name,
        'widthClass': widthClass.name,
        'heightClass': heightClass.name,
        'safeInsets': safeInsets.toJson(),
        'touchEnvironment': touchEnvironment.name,
        'minimumInteractionTargetPoints': minimumInteractionTargetPoints,
        'textScaleFactor': textScaleFactor,
      };
}

final class AvaSessionLimitsV4 {
  const AvaSessionLimitsV4({
    required this.maxTurnSeconds,
    required this.maxResponseCharacters,
    required this.maxSvgBytes,
    required this.maxManifestBytes,
    required this.maxAudioChunkBytes,
    required this.playbackProgressIntervalMs,
  });

  final int maxTurnSeconds;
  final int maxResponseCharacters;
  final int maxSvgBytes;
  final int maxManifestBytes;
  final int maxAudioChunkBytes;
  final int playbackProgressIntervalMs;

  factory AvaSessionLimitsV4.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'maxTurnSeconds',
        'maxResponseCharacters',
        'maxSvgBytes',
        'maxManifestBytes',
        'maxAudioChunkBytes',
        'playbackProgressIntervalMs',
      },
      'protocol-v4 session limits',
    );
    final result = AvaSessionLimitsV4(
      maxTurnSeconds: _requiredInt(json, 'maxTurnSeconds', 'session limits'),
      maxResponseCharacters:
          _requiredInt(json, 'maxResponseCharacters', 'session limits'),
      maxSvgBytes: _requiredInt(json, 'maxSvgBytes', 'session limits'),
      maxManifestBytes:
          _requiredInt(json, 'maxManifestBytes', 'session limits'),
      maxAudioChunkBytes:
          _requiredInt(json, 'maxAudioChunkBytes', 'session limits'),
      playbackProgressIntervalMs:
          _requiredInt(json, 'playbackProgressIntervalMs', 'session limits'),
    );
    if (result.maxTurnSeconds < 5 ||
        result.maxTurnSeconds > 180 ||
        result.maxResponseCharacters < 128 ||
        result.maxResponseCharacters > 1800 ||
        result.maxSvgBytes <= 0 ||
        result.maxSvgBytes > avaSvgMaximumDocumentBytes ||
        result.maxManifestBytes <= 0 ||
        result.maxManifestBytes > avaSvgMaximumManifestBytes ||
        result.maxAudioChunkBytes < 2048 ||
        result.maxAudioChunkBytes > 192000 ||
        result.playbackProgressIntervalMs < 50 ||
        result.playbackProgressIntervalMs > 2000) {
      throw const FormatException('Session limits are outside v4 bounds.');
    }
    return result;
  }
}

final class AvaSessionCreateRequestV4 {
  const AvaSessionCreateRequestV4({
    required this.ageBand,
    required this.timezoneId,
    required this.canvasRenderProfile,
  });

  final AvaRealtimeAgeBand ageBand;
  final String timezoneId;
  final AvaCanvasRenderProfileV4 canvasRenderProfile;

  Map<String, Object?> toJson() => {
        'protocolVersion': avaRealtimeProtocolVersionV4,
        'ageBand': ageBand.name,
        'timezoneId': timezoneId,
        'canvasRenderProfile': canvasRenderProfile.toJson(),
      };
}

final class AvaSessionBootstrapV4 {
  const AvaSessionBootstrapV4({
    required this.sessionId,
    required this.eventUrl,
    required this.webrtcUrl,
    required this.canvasDocumentBaseUrl,
    required this.sessionToken,
    required this.webrtcToken,
    required this.canvasToken,
    required this.expiresAt,
    required this.limits,
  });

  final String sessionId;
  final String eventUrl;
  final String webrtcUrl;
  final String canvasDocumentBaseUrl;
  final String sessionToken;
  final String webrtcToken;
  final String canvasToken;
  final String expiresAt;
  final AvaSessionLimitsV4 limits;

  factory AvaSessionBootstrapV4.fromJson(
    Map<String, Object?> json, {
    bool allowInsecureLoopback = false,
  }) {
    _requireExactKeys(
      json,
      const {
        'protocolVersion',
        'sessionId',
        'eventUrl',
        'webrtcUrl',
        'canvasDocumentBaseUrl',
        'sessionToken',
        'webrtcToken',
        'canvasToken',
        'expiresAt',
        'rendererCapabilityVersion',
        'validatorVersion',
        'manifestVersion',
        'limits',
      },
      'protocol-v4 session bootstrap',
    );
    if (json['protocolVersion'] != avaRealtimeProtocolVersionV4 ||
        json['rendererCapabilityVersion'] != avaSvgRendererCapabilityVersion ||
        json['validatorVersion'] != avaSvgValidatorVersion ||
        json['manifestVersion'] != avaSvgManifestVersion) {
      throw const FormatException(
        'Session bootstrap protocol or SVG capability version is unsupported.',
      );
    }
    final eventUrl =
        _boundedString(json, 'eventUrl', 'session bootstrap', 2048);
    final webrtcUrl =
        _boundedString(json, 'webrtcUrl', 'session bootstrap', 2048);
    final canvasBase = _boundedString(
      json,
      'canvasDocumentBaseUrl',
      'session bootstrap',
      2048,
    );
    _requireChannelUrl(
      eventUrl,
      secureScheme: 'wss',
      loopbackScheme: 'ws',
      allowInsecureLoopback: allowInsecureLoopback,
      label: 'eventUrl',
    );
    _requireChannelUrl(
      webrtcUrl,
      secureScheme: 'https',
      loopbackScheme: 'http',
      allowInsecureLoopback: allowInsecureLoopback,
      label: 'webrtcUrl',
    );
    _requireChannelUrl(
      canvasBase,
      secureScheme: 'https',
      loopbackScheme: 'http',
      allowInsecureLoopback: allowInsecureLoopback,
      label: 'canvasDocumentBaseUrl',
    );
    final expiresAt =
        _boundedString(json, 'expiresAt', 'session bootstrap', 64);
    if (DateTime.tryParse(expiresAt)?.isUtc != true) {
      throw const FormatException(
        'session bootstrap.expiresAt must be an ISO-8601 UTC timestamp.',
      );
    }
    return AvaSessionBootstrapV4(
      sessionId: _identifier(json, 'sessionId', 'session bootstrap'),
      eventUrl: eventUrl,
      webrtcUrl: webrtcUrl,
      canvasDocumentBaseUrl: canvasBase,
      sessionToken:
          _boundedString(json, 'sessionToken', 'session bootstrap', 2048),
      webrtcToken:
          _boundedString(json, 'webrtcToken', 'session bootstrap', 2048),
      canvasToken:
          _boundedString(json, 'canvasToken', 'session bootstrap', 2048),
      expiresAt: expiresAt,
      limits: AvaSessionLimitsV4.fromJson(
        _requiredObject(json['limits'], 'session bootstrap.limits'),
      ),
    );
  }
}

/// Strict, natively parsed protocol-v4 control envelope.
final class AvaRealtimeServerEventV4 {
  AvaRealtimeServerEventV4._({
    required this.type,
    required this.sessionId,
    required this.sequence,
    required this.revision,
    required Map<String, Object?> json,
    this.turnId,
    this.phase,
    this.segmentIndex,
    this.segment,
    this.streamId,
    this.chunkIndex,
    this.sampleOffset,
    this.sampleCount,
    this.byteLength,
    this.alignmentSpans = const [],
    this.interruptReason,
    this.deliveredSampleCount,
    this.deliveredText,
    this.failure,
  }) : _json = Map.unmodifiable(_deepCopyMap(json));

  final AvaRealtimeServerEventTypeV4 type;
  final String sessionId;
  final int sequence;
  final int revision;
  final String? turnId;
  final AvaRealtimeTurnPhase? phase;
  final int? segmentIndex;
  final AvaRealtimeTurnSegment? segment;
  final String? streamId;
  final int? chunkIndex;
  final int? sampleOffset;
  final int? sampleCount;
  final int? byteLength;
  final List<AvaRealtimeAlignmentSpan> alignmentSpans;
  final String? interruptReason;
  final int? deliveredSampleCount;
  final String? deliveredText;
  final AvaRealtimeFailure? failure;
  final Map<String, Object?> _json;

  bool get isCanvas => switch (type) {
        AvaRealtimeServerEventTypeV4.canvasGenerationStarted ||
        AvaRealtimeServerEventTypeV4.canvasDraftAvailable ||
        AvaRealtimeServerEventTypeV4.canvasDocumentAvailable ||
        AvaRealtimeServerEventTypeV4.canvasPresentationCue ||
        AvaRealtimeServerEventTypeV4.canvasActivated ||
        AvaRealtimeServerEventTypeV4.canvasDocumentDiscarded ||
        AvaRealtimeServerEventTypeV4.canvasRejected ||
        AvaRealtimeServerEventTypeV4.canvasPresentationChanged ||
        AvaRealtimeServerEventTypeV4.canvasPresentationCommand ||
        AvaRealtimeServerEventTypeV4.interactionRejected =>
          true,
        _ => false,
      };

  Map<String, Object?> toJson() => _deepCopyMap(_json);

  bool get isTerminal =>
      type == AvaRealtimeServerEventTypeV4.turnCompleted ||
      type == AvaRealtimeServerEventTypeV4.turnCancelled ||
      type == AvaRealtimeServerEventTypeV4.turnFailed;

  factory AvaRealtimeServerEventV4.fromJson(Map<String, Object?> json) {
    if (json['protocolVersion'] != avaRealtimeProtocolVersionV4) {
      throw const FormatException('Realtime event must use protocol v4.');
    }
    final typeName = _requiredString(json, 'event', 'realtime event');
    final type = AvaRealtimeServerEventTypeV4.values
        .where((candidate) => candidate.name == typeName)
        .firstOrNull;
    if (type == null) {
      throw FormatException('Unsupported protocol-v4 event "$typeName".');
    }
    final sessionId = _identifier(json, 'sessionId', 'realtime event');
    final sequence = _boundedInt(
      json,
      'sequence',
      'realtime event',
      minimum: 0,
      maximum: 1000000,
    );
    final revision = _boundedInt(
      json,
      'revision',
      'realtime event',
      minimum: 0,
      maximum: 1000000,
    );

    String? turnId;
    AvaRealtimeTurnPhase? phase;
    int? segmentIndex;
    AvaRealtimeTurnSegment? segment;
    String? streamId;
    int? chunkIndex;
    int? sampleOffset;
    int? sampleCount;
    int? byteLength;
    List<AvaRealtimeAlignmentSpan> alignmentSpans = const [];
    String? interruptReason;
    int? deliveredSampleCount;
    String? deliveredText;
    AvaRealtimeFailure? failure;

    switch (type) {
      case AvaRealtimeServerEventTypeV4.canvasGenerationStarted:
        _requireExactKeys(
          json,
          {
            ..._serverCommon,
            'generationId',
            'sourceTurnId',
            'operation',
            'focusRevision',
          },
          'canvasGenerationStarted',
        );
        _identifier(json, 'generationId', 'canvasGenerationStarted');
        _identifier(json, 'sourceTurnId', 'canvasGenerationStarted');
        _boundedInt(json, 'focusRevision', 'canvasGenerationStarted',
            minimum: 0, maximum: 1000000);
        final operation = json['operation'];
        if (operation != 'new' && operation != 'reviseFocused') {
          throw const FormatException(
            'canvasGenerationStarted requires operation new or reviseFocused.',
          );
        }
        break;
      case AvaRealtimeServerEventTypeV4.canvasDraftAvailable:
        AvaSvgCanvasDraftDescriptorV4.fromJson(json);
        break;
      case AvaRealtimeServerEventTypeV4.canvasDocumentAvailable:
        AvaSvgCanvasDocumentDescriptorV4.fromJson(json);
        break;
      case AvaRealtimeServerEventTypeV4.canvasPresentationCue:
        _validateCanvasIdentityEvent(
          json,
          label: 'canvasPresentationCue',
          additional: const {'presentationSampleOffset'},
        );
        _boundedInt(
          json,
          'presentationSampleOffset',
          'canvasPresentationCue',
          minimum: 0,
          maximum: 2147483647,
        );
        break;
      case AvaRealtimeServerEventTypeV4.canvasActivated:
        _validateCanvasIdentityEvent(
          json,
          label: 'canvasActivated',
          additional: const {
            'firstRenderedSample',
            'workspaceEpoch',
            'workspaceRevision',
            'creationSequence',
            'modificationSequence',
            'evictedSceneId',
          },
        );
        _boundedInt(
          json,
          'firstRenderedSample',
          'canvasActivated',
          minimum: 0,
          maximum: 2147483647,
        );
        _boundedInt(json, 'workspaceEpoch', 'canvasActivated',
            minimum: 0, maximum: 2147483647);
        _boundedInt(json, 'workspaceRevision', 'canvasActivated',
            minimum: 1, maximum: 2147483647);
        _boundedInt(json, 'creationSequence', 'canvasActivated',
            minimum: 1, maximum: 2147483647);
        _boundedInt(json, 'modificationSequence', 'canvasActivated',
            minimum: 1, maximum: 2147483647);
        final evictedSceneId = json['evictedSceneId'];
        if (evictedSceneId != null) {
          _identifier(json, 'evictedSceneId', 'canvasActivated');
        }
        break;
      case AvaRealtimeServerEventTypeV4.canvasDocumentDiscarded:
        _requireExactKeys(
          json,
          {..._serverCommon, ..._canvasSceneIdentity, 'reason'},
          'canvasDocumentDiscarded',
        );
        _validateSceneIdentity(json, 'canvasDocumentDiscarded');
        _allowedString(
          json,
          'reason',
          'canvasDocumentDiscarded',
          const {
            'superseded',
            'sessionClosed',
            'rendererFailure',
            'presentationTimeout',
          },
        );
        break;
      case AvaRealtimeServerEventTypeV4.canvasRejected:
        final generationIdPresent = json.containsKey('generationId');
        final callIdPresent = json.containsKey('callId');
        if (generationIdPresent == callIdPresent) {
          throw const FormatException(
            'canvasRejected requires exactly one call or generation ID.',
          );
        }
        _requireExactKeys(
          json,
          {
            ..._serverCommon,
            if (generationIdPresent) 'generationId',
            if (callIdPresent) 'callId',
            'sourceTurnId',
            'rejectionCode',
            'message',
            'retryable',
          },
          'canvasRejected',
        );
        _identifier(
          json,
          generationIdPresent ? 'generationId' : 'callId',
          'canvasRejected',
        );
        _identifier(json, 'sourceTurnId', 'canvasRejected');
        _allowedString(
          json,
          'rejectionCode',
          'canvasRejected',
          const {
            'providerUnavailable',
            'providerInvalid',
            'generationLimitExceeded',
            'staticValidationFailed',
            'preparationFailed',
            'presentationFailed',
            'cancelled',
            'timeout',
          },
        );
        _boundedString(json, 'message', 'canvasRejected', 240);
        _requiredBool(json, 'retryable', 'canvasRejected');
        break;
      case AvaRealtimeServerEventTypeV4.canvasPresentationChanged:
        _requireExactKeys(
          json,
          {
            ..._serverCommon,
            'visible',
            if (json.containsKey('sceneId')) 'sceneId',
            if (json.containsKey('focusRevision')) 'focusRevision',
            'source',
          },
          'canvasPresentationChanged',
        );
        final visible =
            _requiredBool(json, 'visible', 'canvasPresentationChanged');
        if (visible) {
          _identifier(json, 'sceneId', 'canvasPresentationChanged');
          _boundedInt(json, 'focusRevision', 'canvasPresentationChanged',
              minimum: 1, maximum: 10001);
        }
        _allowedString(
          json,
          'source',
          'canvasPresentationChanged',
          const {'user', 'model', 'restore', 'system'},
        );
        break;
      case AvaRealtimeServerEventTypeV4.canvasPresentationCommand:
        _requireExactKeys(
          json,
          {
            ..._serverCommon,
            'commandId',
            'action',
            if (json.containsKey('sceneId')) 'sceneId',
          },
          'canvasPresentationCommand',
        );
        _identifier(json, 'commandId', 'canvasPresentationCommand');
        final action = _allowedString(
          json,
          'action',
          'canvasPresentationCommand',
          const {'show', 'hide'},
        );
        if (action == 'show') {
          _identifier(json, 'sceneId', 'canvasPresentationCommand');
        } else {
          if (json.containsKey('sceneId') && json['sceneId'] != null) {
            throw const FormatException(
                'hide presentation command cannot include sceneId.');
          }
        }
        break;
      case AvaRealtimeServerEventTypeV4.interactionRejected:
        _requireExactKeys(
          json,
          {
            ..._serverCommon,
            'sceneId',
            'sceneRevision',
            'interactionRevision',
            'bindingId',
            'rejectionCode',
            'message',
          },
          'interactionRejected',
        );
        _identifier(json, 'sceneId', 'interactionRejected');
        _boundedInt(
          json,
          'sceneRevision',
          'interactionRejected',
          minimum: 1,
          maximum: 10001,
        );
        _boundedInt(
          json,
          'interactionRevision',
          'interactionRejected',
          minimum: 1,
          maximum: 1000000,
        );
        _identifier(json, 'bindingId', 'interactionRejected');
        _allowedString(
          json,
          'rejectionCode',
          'interactionRejected',
          const {
            'noActiveScene',
            'sceneMismatch',
            'sceneRevisionMismatch',
            'focusRevisionMismatch',
            'interactionRevisionStale',
            'bindingUnknown',
            'bindingLocalOnly',
            'contentIdentityMismatch',
          },
        );
        _boundedString(json, 'message', 'interactionRejected', 240);
        break;
      default:
        _speechIdentifier(json, 'sessionId', 'realtime event');
        turnId = _speechIdentifier(json, 'turnId', 'realtime event');
        const common = {..._serverCommon, 'turnId'};
        switch (type) {
          case AvaRealtimeServerEventTypeV4.turnContextRequested:
            _requireExactKeys(json, {...common, 'requestId', 'providerItemId'},
                'turnContextRequested');
            _speechIdentifier(json, 'requestId', 'turnContextRequested');
            _speechIdentifier(json, 'providerItemId', 'turnContextRequested');
            break;
          case AvaRealtimeServerEventTypeV4.turnCreated:
            _requireExactKeys(json, {...common, 'requestId'}, 'turnCreated');
            _speechIdentifier(json, 'requestId', 'turnCreated');
            break;
          case AvaRealtimeServerEventTypeV4.turnPhaseChanged:
            _requireExactKeys(
              json,
              {...common, 'phase'},
              'turnPhaseChanged',
            );
            phase = _enumValue(
              AvaRealtimeTurnPhase.values,
              _requiredString(json, 'phase', 'turnPhaseChanged'),
              'turnPhaseChanged.phase',
            );
            break;
          case AvaRealtimeServerEventTypeV4.segmentReady:
            _requireExactKeys(
              json,
              {...common, 'segmentIndex', 'segment'},
              'segmentReady',
            );
            segmentIndex = _boundedInt(
              json,
              'segmentIndex',
              'segmentReady',
              minimum: 0,
              maximum: 900,
            );
            final segmentJson =
                _requiredObject(json['segment'], 'segmentReady.segment');
            final canvasCues = segmentJson['canvasCues'];
            if (canvasCues is! List || canvasCues.isNotEmpty) {
              throw const FormatException(
                'Protocol-v4 speech segments cannot contain canvas cues.',
              );
            }
            segment = AvaRealtimeTurnSegment.fromJson(segmentJson);
            break;
          case AvaRealtimeServerEventTypeV4.audioMetadata:
            _requireExactKeys(
              json,
              {
                ...common,
                'streamId',
                'audioFormat',
                'sampleRateHz',
                'channels',
                'bitsPerSample',
                'modelId',
                'voiceId',
              },
              'audioMetadata',
            );
            streamId = _speechIdentifier(json, 'streamId', 'audioMetadata');
            if (_requiredString(json, 'audioFormat', 'audioMetadata') !=
                    'pcm_s16le' ||
                _requiredInt(json, 'sampleRateHz', 'audioMetadata') != 24000 ||
                _requiredInt(json, 'channels', 'audioMetadata') != 1 ||
                _requiredInt(json, 'bitsPerSample', 'audioMetadata') != 16) {
              throw const FormatException(
                'audioMetadata must describe mono 24 kHz PCM16.',
              );
            }
            _speechIdentifier(json, 'modelId', 'audioMetadata');
            _speechIdentifier(json, 'voiceId', 'audioMetadata');
            break;
          case AvaRealtimeServerEventTypeV4.audioChunk:
            _requireExactKeys(
              json,
              {
                ...common,
                'streamId',
                'chunkIndex',
                'sampleOffset',
                'sampleCount',
                'byteLength',
              },
              'audioChunk',
            );
            streamId = _speechIdentifier(json, 'streamId', 'audioChunk');
            chunkIndex = _requiredInt(json, 'chunkIndex', 'audioChunk');
            sampleOffset = _requiredInt(json, 'sampleOffset', 'audioChunk');
            sampleCount = _requiredInt(json, 'sampleCount', 'audioChunk');
            byteLength = _requiredInt(json, 'byteLength', 'audioChunk');
            if (chunkIndex < 0 ||
                sampleOffset < 0 ||
                sampleCount < 1 ||
                sampleCount > 96000 ||
                byteLength != sampleCount * 2) {
              throw const FormatException('audioChunk fields are invalid.');
            }
            break;
          case AvaRealtimeServerEventTypeV4.alignmentBatch:
            _requireExactKeys(
              json,
              {...common, 'streamId', 'spans'},
              'alignmentBatch',
            );
            streamId = _speechIdentifier(json, 'streamId', 'alignmentBatch');
            alignmentSpans = _alignmentSpansV4(json['spans']);
            break;
          case AvaRealtimeServerEventTypeV4.interruptRequested:
            _requireExactKeys(
              json,
              {...common, 'streamId', 'reason'},
              'interruptRequested',
            );
            streamId =
                _speechIdentifier(json, 'streamId', 'interruptRequested');
            interruptReason =
                _boundedString(json, 'reason', 'interruptRequested', 120);
            break;
          case AvaRealtimeServerEventTypeV4.memoryPersistenceFailed:
            _requireExactKeys(
              json,
              {...common, 'failure'},
              'memoryPersistenceFailed',
            );
            failure = AvaRealtimeFailure.fromJson(
              _requiredObject(
                json['failure'],
                'memoryPersistenceFailed.failure',
              ),
            );
            if (failure.code != 'memoryUnavailable' || !failure.retryable) {
              throw const FormatException(
                'memoryPersistenceFailed requires a retryable '
                'failure.code=memoryUnavailable.',
              );
            }
            break;
          case AvaRealtimeServerEventTypeV4.turnCompleted:
            _requireExactKeys(
              json,
              {...common, 'deliveredSampleCount', 'deliveredText'},
              'turnCompleted',
            );
            deliveredSampleCount = _deliveredFieldsV4(json, 'turnCompleted');
            deliveredText = json['deliveredText']! as String;
            break;
          case AvaRealtimeServerEventTypeV4.turnCancelled:
            _requireExactKeys(
              json,
              {
                ...common,
                'deliveredSampleCount',
                'deliveredText',
                'reason',
              },
              'turnCancelled',
            );
            deliveredSampleCount = _deliveredFieldsV4(json, 'turnCancelled');
            deliveredText = json['deliveredText']! as String;
            _boundedString(json, 'reason', 'turnCancelled', 120);
            break;
          case AvaRealtimeServerEventTypeV4.turnFailed:
            _requireExactKeys(
              json,
              {
                ...common,
                'failure',
                'deliveredSampleCount',
                'deliveredText',
              },
              'turnFailed',
            );
            deliveredSampleCount = _deliveredFieldsV4(json, 'turnFailed');
            deliveredText = json['deliveredText']! as String;
            failure = AvaRealtimeFailure.fromJson(
              _requiredObject(json['failure'], 'turnFailed.failure'),
            );
            break;
          case AvaRealtimeServerEventTypeV4.canvasGenerationStarted:
          case AvaRealtimeServerEventTypeV4.canvasDraftAvailable:
          case AvaRealtimeServerEventTypeV4.canvasDocumentAvailable:
          case AvaRealtimeServerEventTypeV4.canvasPresentationCue:
          case AvaRealtimeServerEventTypeV4.canvasActivated:
          case AvaRealtimeServerEventTypeV4.canvasDocumentDiscarded:
          case AvaRealtimeServerEventTypeV4.canvasRejected:
          case AvaRealtimeServerEventTypeV4.canvasPresentationChanged:
          case AvaRealtimeServerEventTypeV4.canvasPresentationCommand:
          case AvaRealtimeServerEventTypeV4.interactionRejected:
            throw StateError('Canvas event reached the speech parser.');
        }
        break;
    }
    return AvaRealtimeServerEventV4._(
      type: type,
      sessionId: sessionId,
      sequence: sequence,
      revision: revision,
      json: json,
      turnId: turnId,
      phase: phase,
      segmentIndex: segmentIndex,
      segment: segment,
      streamId: streamId,
      chunkIndex: chunkIndex,
      sampleOffset: sampleOffset,
      sampleCount: sampleCount,
      byteLength: byteLength,
      alignmentSpans: List.unmodifiable(alignmentSpans),
      interruptReason: interruptReason,
      deliveredSampleCount: deliveredSampleCount,
      deliveredText: deliveredText,
      failure: failure,
    );
  }
}

final class AvaRealtimeClientEventV4 {
  AvaRealtimeClientEventV4._(Map<String, Object?> json)
      : _json = Map.unmodifiable(_deepCopyMap(json));

  final Map<String, Object?> _json;

  Map<String, Object?> toJson() => _deepCopyMap(_json);

  factory AvaRealtimeClientEventV4.turnContextSnapshot({
    required String sessionId,
    required int sequence,
    required String turnId,
    required String requestId,
    required String providerItemId,
    required Map<String, Object?> context,
  }) =>
      AvaRealtimeClientEventV4._validated({
        'protocolVersion': 4,
        'event': 'turnContextSnapshot',
        'sessionId': sessionId,
        'sequence': sequence,
        'turnId': turnId,
        'requestId': requestId,
        'providerItemId': providerItemId,
        ...context,
      });

  factory AvaRealtimeClientEventV4.clientReady({
    required String sessionId,
    required int sequence,
    bool canvasPanelVisible = false,
    String? sceneId,
    int? focusRevision,
  }) {
    final json = <String, Object?>{
      'protocolVersion': avaRealtimeProtocolVersionV4,
      'event': 'clientReady',
      'sessionId': sessionId,
      'sequence': sequence,
      'rendererCapabilityVersion': avaSvgRendererCapabilityVersion,
      'validatorVersion': avaSvgValidatorVersion,
      if (canvasPanelVisible) 'canvasPanelVisible': true,
      if (canvasPanelVisible && sceneId != null) 'sceneId': sceneId,
      if (canvasPanelVisible && focusRevision != null)
        'focusRevision': focusRevision,
    };
    _validateClientEvent(json);
    return AvaRealtimeClientEventV4._(json);
  }

  factory AvaRealtimeClientEventV4.canvasRenderProfileUpdated({
    required String sessionId,
    required int sequence,
    required int profileRevision,
    required AvaCanvasRenderProfileV4 renderProfile,
  }) =>
      AvaRealtimeClientEventV4._validated({
        'protocolVersion': avaRealtimeProtocolVersionV4,
        'event': 'canvasRenderProfileUpdated',
        'sessionId': sessionId,
        'sequence': sequence,
        'profileRevision': profileRevision,
        'renderProfile': renderProfile.toJson(),
      });

  factory AvaRealtimeClientEventV4.playbackStarted({
    required String sessionId,
    required int sequence,
    required String turnId,
    required String streamId,
  }) =>
      AvaRealtimeClientEventV4._validated({
        'protocolVersion': avaRealtimeProtocolVersionV4,
        'event': 'playbackStarted',
        'sessionId': sessionId,
        'sequence': sequence,
        'turnId': turnId,
        'streamId': streamId,
        'renderedSampleCount': 0,
      });

  factory AvaRealtimeClientEventV4.playbackProgress({
    required String sessionId,
    required int sequence,
    required String turnId,
    required String streamId,
    required int renderedSampleCount,
    required int bufferedSampleCount,
  }) =>
      AvaRealtimeClientEventV4._validated({
        'protocolVersion': avaRealtimeProtocolVersionV4,
        'event': 'playbackProgress',
        'sessionId': sessionId,
        'sequence': sequence,
        'turnId': turnId,
        'streamId': streamId,
        'renderedSampleCount': renderedSampleCount,
        'bufferedSampleCount': bufferedSampleCount,
      });

  factory AvaRealtimeClientEventV4.playbackStopped({
    required String sessionId,
    required int sequence,
    required String turnId,
    required String streamId,
    required int renderedSampleCount,
    required String reason,
  }) =>
      AvaRealtimeClientEventV4._validated({
        'protocolVersion': avaRealtimeProtocolVersionV4,
        'event': 'playbackStopped',
        'sessionId': sessionId,
        'sequence': sequence,
        'turnId': turnId,
        'streamId': streamId,
        'renderedSampleCount': renderedSampleCount,
        'reason': reason,
      });

  factory AvaRealtimeClientEventV4.cancelTurn({
    required String sessionId,
    required int sequence,
    required String turnId,
    required String requestId,
    required String reason,
  }) =>
      AvaRealtimeClientEventV4._validated({
        'protocolVersion': avaRealtimeProtocolVersionV4,
        'event': 'cancelTurn',
        'sessionId': sessionId,
        'sequence': sequence,
        'turnId': turnId,
        'requestId': requestId,
        'reason': reason,
      });

  factory AvaRealtimeClientEventV4.canvasPresentationAcknowledged({
    required String sessionId,
    required int sequence,
    required String commandId,
    required bool applied,
    String? sceneId,
    int? focusRevision,
    String? reason,
  }) =>
      AvaRealtimeClientEventV4._validated({
        'protocolVersion': avaRealtimeProtocolVersionV4,
        'event': 'canvasPresentationAcknowledged',
        'sessionId': sessionId,
        'sequence': sequence,
        'commandId': commandId,
        'applied': applied,
        if (sceneId != null) 'sceneId': sceneId,
        if (focusRevision != null) 'focusRevision': focusRevision,
        if (reason != null) 'reason': reason,
      });

  factory AvaRealtimeClientEventV4._validated(Map<String, Object?> json) {
    _validateClientEvent(json);
    return AvaRealtimeClientEventV4._(json);
  }

  factory AvaRealtimeClientEventV4.canvasCommand({
    required String sessionId,
    required int sequence,
    required Map<String, Object?> body,
  }) {
    final json = <String, Object?>{
      'protocolVersion': avaRealtimeProtocolVersionV4,
      ...body,
      'sessionId': sessionId,
      'sequence': sequence,
    };
    _validateClientEvent(json);
    return AvaRealtimeClientEventV4._(json);
  }
}

const _serverCommon = {
  'protocolVersion',
  'event',
  'sessionId',
  'sequence',
  'revision',
};

const _canvasSceneIdentity = {
  'generationId',
  'sourceTurnId',
  'sceneId',
  'sceneRevision',
};

const _canvasContentIdentity = {
  ..._canvasSceneIdentity,
  'svgSha256',
  'manifestSha256',
};

void _validateCanvasIdentityEvent(
  Map<String, Object?> json, {
  required String label,
  required Set<String> additional,
}) {
  _requireExactKeys(
    json,
    {..._serverCommon, ..._canvasContentIdentity, ...additional},
    label,
  );
  _validateSceneIdentity(json, label);
  _digest(json, 'svgSha256', label);
  _digest(json, 'manifestSha256', label);
}

void _validateSceneIdentity(Map<String, Object?> json, String label) {
  _identifier(json, 'generationId', label);
  _identifier(json, 'sourceTurnId', label);
  _identifier(json, 'sceneId', label);
  _boundedInt(
    json,
    'sceneRevision',
    label,
    minimum: 1,
    maximum: 10001,
  );
}

void _validateClientEvent(Map<String, Object?> json) {
  if (json['protocolVersion'] != avaRealtimeProtocolVersionV4) {
    throw const FormatException('Client event must use protocol v4.');
  }
  final event = _requiredString(json, 'event', 'client event');
  _identifier(json, 'sessionId', 'client event');
  _boundedInt(
    json,
    'sequence',
    'client event',
    minimum: 0,
    maximum: 1000000,
  );
  const common = {'protocolVersion', 'event', 'sessionId', 'sequence'};
  switch (event) {
    case 'clientReady':
      _requireExactKeys(
        json,
        {
          ...common,
          'rendererCapabilityVersion',
          'validatorVersion',
          if (json.containsKey('canvasPanelVisible')) 'canvasPanelVisible',
          if (json.containsKey('sceneId')) 'sceneId',
          if (json.containsKey('focusRevision')) 'focusRevision',
        },
        event,
      );
      if (json['rendererCapabilityVersion'] !=
              avaSvgRendererCapabilityVersion ||
          json['validatorVersion'] != avaSvgValidatorVersion) {
        throw const FormatException(
            'clientReady SVG versions are unsupported.');
      }
      final panelVisible = json['canvasPanelVisible'];
      if (panelVisible != null) {
        if (panelVisible is! bool) {
          throw const FormatException(
              'clientReady.canvasPanelVisible must be boolean.');
        }
        if (panelVisible) {
          _identifier(json, 'sceneId', event);
          _boundedInt(json, 'focusRevision', event, minimum: 1, maximum: 10001);
        }
      }
      _speechIdentifier(json, 'sessionId', event);
      break;
    case 'turnContextSnapshot':
      _requireExactKeys(
          json,
          {
            ...common,
            'turnId',
            'requestId',
            'providerItemId',
            'focusRevision',
            'focusedCanvas'
          },
          event);
      _speechIdentifier(json, 'turnId', event);
      _speechIdentifier(json, 'requestId', event);
      _speechIdentifier(json, 'providerItemId', event);
      _boundedInt(json, 'focusRevision', event, minimum: 0, maximum: 1000000);
      final focus = json['focusedCanvas'];
      if (focus != null) {
        final identity = _requiredObject(focus, 'focusedCanvas');
        _requireExactKeys(
            identity,
            {'sceneId', 'sceneRevision', 'svgSha256', 'manifestSha256'},
            'focusedCanvas');
        _speechIdentifier(identity, 'sceneId', 'focusedCanvas');
        _boundedInt(identity, 'sceneRevision', 'focusedCanvas',
            minimum: 1, maximum: 10001);
        for (final key in ['svgSha256', 'manifestSha256']) {
          if (!RegExp(r'^[a-f0-9]{64}$')
              .hasMatch(_requiredString(identity, key, 'focusedCanvas'))) {
            throw const FormatException('Invalid focused Canvas hash.');
          }
        }
      }
      break;
    case 'canvasRenderProfileUpdated':
      _requireExactKeys(
        json,
        {...common, 'profileRevision', 'renderProfile'},
        event,
      );
      _boundedInt(
        json,
        'profileRevision',
        event,
        minimum: 1,
        maximum: 1000000,
      );
      AvaCanvasRenderProfileV4.fromJson(
        _requiredObject(json['renderProfile'], '$event.renderProfile'),
      );
      break;
    case 'playbackStarted':
      _requireExactKeys(
        json,
        {...common, 'turnId', 'streamId', 'renderedSampleCount'},
        event,
      );
      _speechIdentifier(json, 'sessionId', event);
      _speechIdentifier(json, 'turnId', event);
      _speechIdentifier(json, 'streamId', event);
      if (json['renderedSampleCount'] != 0) {
        throw const FormatException(
            'playbackStarted must begin at sample zero.');
      }
      break;
    case 'playbackProgress':
      _requireExactKeys(
        json,
        {
          ...common,
          'turnId',
          'streamId',
          'renderedSampleCount',
          'bufferedSampleCount',
        },
        event,
      );
      _speechIdentifier(json, 'sessionId', event);
      _speechIdentifier(json, 'turnId', event);
      _speechIdentifier(json, 'streamId', event);
      _sample(json, 'renderedSampleCount', event);
      _sample(json, 'bufferedSampleCount', event);
      break;
    case 'playbackStopped':
      _requireExactKeys(
        json,
        {
          ...common,
          'turnId',
          'streamId',
          'renderedSampleCount',
          'reason',
        },
        event,
      );
      _speechIdentifier(json, 'sessionId', event);
      _speechIdentifier(json, 'turnId', event);
      _speechIdentifier(json, 'streamId', event);
      _sample(json, 'renderedSampleCount', event);
      _boundedString(json, 'reason', event, 120);
      break;
    case 'cancelTurn':
      _requireExactKeys(
        json,
        {...common, 'turnId', 'requestId', 'reason'},
        event,
      );
      _speechIdentifier(json, 'sessionId', event);
      _speechIdentifier(json, 'turnId', event);
      _speechIdentifier(json, 'requestId', event);
      _boundedString(json, 'reason', event, 120);
      break;
    case 'canvasDocumentPrepared':
      _requireExactKeys(
        json,
        {
          ...common,
          ..._canvasClientIdentity,
          'rendererCapabilityVersion',
          'validatorVersion',
        },
        event,
      );
      _validateCanvasClientIdentity(json, event);
      if (json['rendererCapabilityVersion'] !=
              avaSvgRendererCapabilityVersion ||
          json['validatorVersion'] != avaSvgValidatorVersion) {
        throw const FormatException(
          'canvasDocumentPrepared SVG versions are unsupported.',
        );
      }
      break;
    case 'canvasPreparationFailed':
    case 'canvasRuntimeFailed':
      _requireExactKeys(
        json,
        {...common, ..._canvasClientIdentity, 'failureCode', 'message'},
        event,
      );
      _validateCanvasClientIdentity(json, event);
      final allowed = event == 'canvasPreparationFailed'
          ? const {
              'fetchFailed',
              'contentIdentityMismatch',
              'rendererRejected',
              'rendererTerminated',
              'preparationTimeout',
            }
          : const {'rendererRejected', 'rendererTerminated'};
      _allowedString(json, 'failureCode', event, allowed);
      _boundedString(json, 'message', event, 240);
      break;
    case 'canvasRetained':
      _requireExactKeys(json, {...common, ..._canvasClientIdentity}, event);
      _validateCanvasClientIdentity(json, event);
      break;
    case 'canvasPresented':
      _requireExactKeys(
        json,
        {
          ...common,
          ..._canvasClientIdentity,
          'firstRenderedSample',
          'foreground'
        },
        event,
      );
      _validateCanvasClientIdentity(json, event);
      _sample(json, 'firstRenderedSample', event);
      if (json['foreground'] is! bool) {
        throw const FormatException('Canvas foreground must be boolean.');
      }
      break;
    case 'canvasInteraction':
      _requireExactKeys(
        json,
        {
          ...common,
          'sceneId',
          'sceneRevision',
          'focusRevision',
          'interactionRevision',
          'svgSha256',
          'manifestSha256',
          'bindingId',
        },
        event,
      );
      _identifier(json, 'sceneId', event);
      _boundedInt(
        json,
        'sceneRevision',
        event,
        minimum: 1,
        maximum: 10001,
      );
      _boundedInt(
        json,
        'focusRevision',
        event,
        minimum: 0,
        maximum: 1000000,
      );
      _boundedInt(
        json,
        'interactionRevision',
        event,
        minimum: 1,
        maximum: 1000000,
      );
      _digest(json, 'svgSha256', event);
      _digest(json, 'manifestSha256', event);
      _identifier(json, 'bindingId', event);
      break;
    case 'canvasPresentationRequested':
      _requireExactKeys(
        json,
        {
          ...common,
          'operation',
          if (json.containsKey('selector')) 'selector',
          if (json.containsKey('position')) 'position',
        },
        event,
      );
      final operation = _allowedString(
        json,
        'operation',
        event,
        const {'show', 'hide'},
      );
      if (operation == 'hide') {
        if (json.containsKey('selector') || json.containsKey('position')) {
          throw const FormatException(
              'hide presentation request cannot include a selector.');
        }
      } else if (json.containsKey('selector')) {
        final selector = _allowedString(
          json,
          'selector',
          event,
          const {'current', 'next', 'previous', 'first', 'last', 'position'},
        );
        if (selector == 'position') {
          _boundedInt(json, 'position', event, minimum: 1, maximum: 10);
        } else if (json.containsKey('position')) {
          throw const FormatException(
              'position is required only with selector=position.');
        }
      } else if (json.containsKey('position')) {
        throw const FormatException('position requires selector=position.');
      }
      break;
    case 'canvasPresentationAcknowledged':
      _requireExactKeys(
        json,
        {
          ...common,
          'commandId',
          'applied',
          if (json.containsKey('sceneId')) 'sceneId',
          if (json.containsKey('focusRevision')) 'focusRevision',
          if (json.containsKey('reason')) 'reason',
        },
        event,
      );
      _identifier(json, 'commandId', event);
      final applied = _requiredBool(json, 'applied', event);
      if (applied) {
        if (json.containsKey('reason') && json['reason'] != null) {
          throw const FormatException(
              'applied presentation acknowledgement cannot include reason.');
        }
        if (json.containsKey('sceneId') && json['sceneId'] != null) {
          _identifier(json, 'sceneId', event);
        }
        if (json.containsKey('focusRevision') &&
            json['focusRevision'] != null) {
          _boundedInt(json, 'focusRevision', event, minimum: 1, maximum: 10001);
        }
      } else {
        _boundedString(json, 'reason', event, 240);
      }
      break;
    default:
      throw FormatException('Unsupported protocol-v4 client event "$event".');
  }
}

const _canvasClientIdentity = {
  'generationId',
  'sceneId',
  'sceneRevision',
  'svgSha256',
  'manifestSha256',
};

void _validateCanvasClientIdentity(Map<String, Object?> json, String label) {
  _identifier(json, 'generationId', label);
  _identifier(json, 'sceneId', label);
  _boundedInt(
    json,
    'sceneRevision',
    label,
    minimum: 1,
    maximum: 10001,
  );
  _digest(json, 'svgSha256', label);
  _digest(json, 'manifestSha256', label);
}

int _sample(Map<String, Object?> json, String key, String label) => _boundedInt(
      json,
      key,
      label,
      minimum: 0,
      maximum: 2147483647,
    );

int _deliveredFieldsV4(Map<String, Object?> json, String label) {
  final samples = _requiredInt(json, 'deliveredSampleCount', label);
  if (samples < 0) {
    throw FormatException('$label.deliveredSampleCount cannot be negative.');
  }
  final deliveredText = json['deliveredText'];
  if (deliveredText is! String || deliveredText.runes.length > 1800) {
    throw FormatException(
      '$label.deliveredText must contain at most 1800 characters.',
    );
  }
  return samples;
}

List<AvaRealtimeAlignmentSpan> _alignmentSpansV4(Object? value) {
  if (value is! List || value.isEmpty || value.length > 512) {
    throw const FormatException(
      'alignmentBatch.spans requires 1-512 spans.',
    );
  }
  var previousSampleStart = -1;
  final spans = <AvaRealtimeAlignmentSpan>[];
  for (var index = 0; index < value.length; index += 1) {
    final parsed = AvaRealtimeAlignmentSpan.fromJson(
      _requiredObject(value[index], 'alignment span[$index]'),
    );
    if (parsed.sampleStart < previousSampleStart) {
      throw const FormatException('Alignment span ranges are invalid.');
    }
    spans.add(parsed);
    previousSampleStart = parsed.sampleStart;
  }
  return spans;
}

void _digest(Map<String, Object?> json, String key, String label) {
  final value = _requiredString(json, key, label);
  if (!RegExp(r'^[a-f0-9]{64}$').hasMatch(value)) {
    throw FormatException('$label.$key must be a SHA-256 digest.');
  }
}

void _requireChannelUrl(
  String value, {
  required String secureScheme,
  required String loopbackScheme,
  required bool allowInsecureLoopback,
  required String label,
}) {
  final uri = Uri.tryParse(value);
  final allowedLoopback = allowInsecureLoopback &&
      uri?.scheme == loopbackScheme &&
      _isLoopback(uri!.host);
  if (uri == null ||
      (uri.scheme != secureScheme && !allowedLoopback) ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      uri.hasQuery ||
      uri.hasFragment) {
    throw FormatException(
      '$label must be a credential-free absolute $secureScheme URL.',
    );
  }
}

bool _isLoopback(String host) =>
    host == 'localhost' || host == '127.0.0.1' || host == '::1';

Map<String, Object?> _requiredObject(Object? value, String label) {
  if (value is! Map) throw FormatException('$label must be an object.');
  return value.cast<String, Object?>();
}

String _identifier(Map<String, Object?> json, String key, String label) {
  final value = _requiredString(json, key, label);
  if (!RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,95}$').hasMatch(value)) {
    throw FormatException('$label.$key is not a protocol identifier.');
  }
  return value;
}

String _speechIdentifier(
  Map<String, Object?> json,
  String key,
  String label,
) {
  final value = _requiredString(json, key, label);
  if (!RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,63}$').hasMatch(value)) {
    throw FormatException('$label.$key has an invalid identifier.');
  }
  return value;
}

String _boundedString(
  Map<String, Object?> json,
  String key,
  String label,
  int maximum,
) {
  final value = _requiredString(json, key, label);
  if (value.runes.length > maximum) {
    throw FormatException('$label.$key exceeds its character limit.');
  }
  return value;
}

String _requiredString(Map<String, Object?> json, String key, String label) {
  final value = json[key];
  if (value is! String || value.isEmpty) {
    throw FormatException('$label.$key must be a non-empty string.');
  }
  return value;
}

int _requiredInt(Map<String, Object?> json, String key, String label) {
  final value = json[key];
  if (value is! int) throw FormatException('$label.$key must be an integer.');
  return value;
}

int _boundedInt(
  Map<String, Object?> json,
  String key,
  String label, {
  required int minimum,
  required int maximum,
}) {
  final value = _requiredInt(json, key, label);
  if (value < minimum || value > maximum) {
    throw FormatException('$label.$key is outside its bound.');
  }
  return value;
}

double _boundedFiniteNumber(
  Map<String, Object?> json,
  String key,
  String label, {
  required double minimum,
  required double maximum,
}) {
  final value = json[key];
  if (value is! num || !value.isFinite || value < minimum || value > maximum) {
    throw FormatException('$label.$key is outside its finite bound.');
  }
  return value.toDouble();
}

bool _requiredBool(Map<String, Object?> json, String key, String label) {
  final value = json[key];
  if (value is! bool) throw FormatException('$label.$key must be boolean.');
  return value;
}

String _allowedString(
  Map<String, Object?> json,
  String key,
  String label,
  Set<String> allowed,
) {
  final value = _requiredString(json, key, label);
  if (!allowed.contains(value)) {
    throw FormatException('$label.$key has unsupported value "$value".');
  }
  return value;
}

T _enumValue<T extends Enum>(
  List<T> values,
  String name,
  String label,
) {
  for (final value in values) {
    if (value.name == name) return value;
  }
  throw FormatException('$label has unsupported value "$name".');
}

void _requireExactKeys(
  Map<String, Object?> json,
  Set<String> expected,
  String label,
) {
  final actual = json.keys.toSet();
  if (actual.length != expected.length || !actual.containsAll(expected)) {
    throw FormatException('$label contains missing or unknown fields.');
  }
}

Map<String, Object?> _deepCopyMap(Map<String, Object?> source) =>
    source.map((key, value) => MapEntry(key, _deepCopyValue(value)));

Object? _deepCopyValue(Object? value) {
  if (value is Map) {
    return value.map(
      (key, nested) => MapEntry(key.toString(), _deepCopyValue(nested)),
    );
  }
  if (value is List) return value.map(_deepCopyValue).toList(growable: false);
  return value;
}
