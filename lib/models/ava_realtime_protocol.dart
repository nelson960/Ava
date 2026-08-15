import 'dart:convert';

import 'ava_canvas_protocol.dart';

const avaRealtimeProtocolVersion = 3;
const avaRealtimeMaxSpeechSegments = 12;

enum AvaRealtimeAgeBand {
  unknown,
  earlyChildhood,
  middleChildhood,
  preteen,
}

enum AvaRealtimeDelivery {
  neutral,
  warm,
  playful,
  thoughtful,
  encouraging,
  concerned,
}

/// Converts a locally stored date of birth into the only age information that
/// may cross Ava's provider boundary.
AvaRealtimeAgeBand deriveAvaRealtimeAgeBand(
  DateTime? dateOfBirth, {
  DateTime? at,
}) {
  if (dateOfBirth == null) return AvaRealtimeAgeBand.unknown;
  final reference = at ?? DateTime.now();
  final referenceDate =
      DateTime(reference.year, reference.month, reference.day);
  final birthDate = DateTime(
    dateOfBirth.year,
    dateOfBirth.month,
    dateOfBirth.day,
  );
  if (birthDate.isAfter(referenceDate)) return AvaRealtimeAgeBand.unknown;
  var age = referenceDate.year - birthDate.year;
  final birthdayThisYear = DateTime(
    referenceDate.year,
    birthDate.month,
    birthDate.day,
  );
  if (referenceDate.isBefore(birthdayThisYear)) age -= 1;
  if (age < 3 || age > 12) return AvaRealtimeAgeBand.unknown;
  if (age <= 5) return AvaRealtimeAgeBand.earlyChildhood;
  if (age <= 9) return AvaRealtimeAgeBand.middleChildhood;
  return AvaRealtimeAgeBand.preteen;
}

enum AvaRealtimeExpression {
  neutral,
  warmSmile,
  curious,
  thinking,
  encouraging,
  concerned,
  serious,
}

enum AvaRealtimeGaze {
  child,
  thinkingAway,
  canvas,
}

enum AvaRealtimeMotionIntent {
  none,
  acknowledge,
  presentCanvas,
  encourage,
}

enum AvaCanvasComplexity {
  none,
  normal,
  specialistRequired,
}

enum AvaMemoryProposalKind {
  interest,
  preference,
}

enum AvaRealtimeTurnPhase {
  created,
  connecting,
  listening,
  planning,
  synthesizing,
  speaking,
  cancelling,
  completed,
  cancelled,
  failed,
}

enum AvaRealtimeServerEventType {
  turnCreated,
  turnPhaseChanged,
  segmentReady,
  audioMetadata,
  audioChunk,
  alignmentBatch,
  canvasCue,
  canvasRejected,
  interactionRejected,
  memoryPersistenceFailed,
  turnCompleted,
  turnCancelled,
  turnFailed,
}

enum AvaCanvasInteractionRejectionCode {
  noActiveScene,
  sceneMismatch,
  sceneRevisionMismatch,
  interactionRevisionStale,
  bindingUnknown,
  bindingLocalOnly,
}

enum AvaRealtimeClientEventType {
  clientReady,
  playbackStarted,
  playbackProgress,
  playbackStopped,
  canvasInteraction,
  cancelTurn,
}

final class AvaPhraseAnchorV3 {
  const AvaPhraseAnchorV3({
    required this.phrase,
    required this.occurrence,
  });

  final String phrase;
  final int occurrence;

  factory AvaPhraseAnchorV3.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {'phrase', 'occurrence'},
      'phrase anchor',
    );
    final phrase = _boundedString(json, 'phrase', 'phrase anchor', 120);
    final occurrence = _requiredInt(json, 'occurrence', 'phrase anchor');
    if (occurrence < 0 || occurrence > 3) {
      throw const FormatException(
        'phrase anchor.occurrence must be between 0 and 3.',
      );
    }
    return AvaPhraseAnchorV3(phrase: phrase, occurrence: occurrence);
  }

  Map<String, Object?> toJson() => {
        'phrase': phrase,
        'occurrence': occurrence,
      };
}

final class AvaCanvasCueV3 {
  const AvaCanvasCueV3({
    required this.id,
    required this.anchor,
    required this.transaction,
  });

  final String id;
  final AvaPhraseAnchorV3 anchor;
  final AvaCanvasTransaction transaction;

  factory AvaCanvasCueV3.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {'id', 'anchor', 'transaction'},
      'canvas cue',
    );
    return AvaCanvasCueV3(
      id: _nodeId(json, 'id', 'canvas cue'),
      anchor: AvaPhraseAnchorV3.fromJson(
        _requiredObject(json['anchor'], 'canvas cue.anchor'),
      ),
      transaction: AvaCanvasTransaction.fromJson(
        _requiredObject(json['transaction'], 'canvas cue.transaction'),
      ),
    );
  }

  Map<String, Object?> toJson() => {
        'id': id,
        'anchor': anchor.toJson(),
        'transaction': transaction.toJson(),
      };
}

final class AvaRealtimeTurnSegment {
  AvaRealtimeTurnSegment({
    required this.id,
    required this.speech,
    required this.delivery,
    required this.expression,
    required this.gaze,
    required this.motionIntent,
    required List<AvaCanvasCueV3> canvasCues,
  }) : canvasCues = List.unmodifiable(canvasCues) {
    _validate();
  }

  final String id;
  final String speech;
  final AvaRealtimeDelivery delivery;
  final AvaRealtimeExpression expression;
  final AvaRealtimeGaze gaze;
  final AvaRealtimeMotionIntent motionIntent;
  final List<AvaCanvasCueV3> canvasCues;

  factory AvaRealtimeTurnSegment.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'id',
        'speech',
        'delivery',
        'expression',
        'gaze',
        'motionIntent',
        'canvasCues',
      },
      'realtime segment',
    );
    final rawCues = json['canvasCues'];
    if (rawCues is! List || rawCues.length > 4) {
      throw const FormatException(
        'realtime segment.canvasCues must contain at most four cues.',
      );
    }
    return AvaRealtimeTurnSegment(
      id: _segmentId(json, 'id', 'realtime segment'),
      speech: _boundedString(json, 'speech', 'realtime segment', 600),
      delivery: _enumByName(
        AvaRealtimeDelivery.values,
        _requiredString(json, 'delivery', 'realtime segment'),
        'realtime segment.delivery',
      ),
      expression: _enumByName(
        AvaRealtimeExpression.values,
        _requiredString(json, 'expression', 'realtime segment'),
        'realtime segment.expression',
      ),
      gaze: _enumByName(
        AvaRealtimeGaze.values,
        _requiredString(json, 'gaze', 'realtime segment'),
        'realtime segment.gaze',
      ),
      motionIntent: _enumByName(
        AvaRealtimeMotionIntent.values,
        _requiredString(json, 'motionIntent', 'realtime segment'),
        'realtime segment.motionIntent',
      ),
      canvasCues: [
        for (var index = 0; index < rawCues.length; index += 1)
          AvaCanvasCueV3.fromJson(
            _requiredObject(
              rawCues[index],
              'realtime segment.canvasCues[$index]',
            ),
          ),
      ],
    );
  }

  void _validate() {
    final hasCanvas = canvasCues.isNotEmpty;
    if ((gaze == AvaRealtimeGaze.canvas) != hasCanvas) {
      throw const FormatException(
        'gaze=canvas requires canvasCues and vice versa.',
      );
    }
    if ((motionIntent == AvaRealtimeMotionIntent.presentCanvas) != hasCanvas) {
      throw const FormatException(
        'motionIntent=presentCanvas requires canvasCues and vice versa.',
      );
    }
    final ids = <String>{};
    for (final cue in canvasCues) {
      if (!ids.add(cue.id)) {
        throw FormatException('Canvas cue id "${cue.id}" is duplicated.');
      }
      final positions = _substringPositions(speech, cue.anchor.phrase);
      if (cue.anchor.occurrence >= positions.length) {
        throw FormatException(
          'Canvas cue anchor "${cue.anchor.phrase}" occurrence '
          '${cue.anchor.occurrence} is absent from segment speech.',
        );
      }
    }
  }

  Map<String, Object?> toJson() => {
        'id': id,
        'speech': speech,
        'delivery': delivery.name,
        'expression': expression.name,
        'gaze': gaze.name,
        'motionIntent': motionIntent.name,
        'canvasCues': [for (final cue in canvasCues) cue.toJson()],
      };
}

final class AvaMemoryProposalV3 {
  const AvaMemoryProposalV3({
    required this.kind,
    required this.summary,
    required this.evidence,
    required this.confidence,
  });

  final AvaMemoryProposalKind kind;
  final String summary;
  final String evidence;
  final double confidence;

  factory AvaMemoryProposalV3.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {'kind', 'summary', 'evidence', 'confidence'},
      'memory proposal',
    );
    final confidence = _requiredFiniteNumber(
      json,
      'confidence',
      'memory proposal',
    );
    if (confidence < 0 || confidence > 1) {
      throw const FormatException(
        'memory proposal.confidence must be between zero and one.',
      );
    }
    return AvaMemoryProposalV3(
      kind: _enumByName(
        AvaMemoryProposalKind.values,
        _requiredString(json, 'kind', 'memory proposal'),
        'memory proposal.kind',
      ),
      summary: _boundedString(json, 'summary', 'memory proposal', 160),
      evidence: _boundedString(json, 'evidence', 'memory proposal', 160),
      confidence: confidence,
    );
  }

  Map<String, Object?> toJson() => {
        'kind': kind.name,
        'summary': summary,
        'evidence': evidence,
        'confidence': confidence,
      };
}

final class AvaCanvasDecisionV3 {
  const AvaCanvasDecisionV3({
    required this.complexity,
    required this.specialistBrief,
  });

  final AvaCanvasComplexity complexity;
  final String? specialistBrief;

  factory AvaCanvasDecisionV3.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {'complexity', 'specialistBrief'},
      'canvas decision',
    );
    final complexity = _enumByName(
      AvaCanvasComplexity.values,
      _requiredString(json, 'complexity', 'canvas decision'),
      'canvas decision.complexity',
    );
    final rawBrief = json['specialistBrief'];
    final brief = rawBrief == null
        ? null
        : _boundedString(
            json,
            'specialistBrief',
            'canvas decision',
            500,
          );
    if ((complexity == AvaCanvasComplexity.specialistRequired) !=
        (brief != null)) {
      throw const FormatException(
        'specialistBrief is required only for specialistRequired.',
      );
    }
    return AvaCanvasDecisionV3(
      complexity: complexity,
      specialistBrief: brief,
    );
  }

  Map<String, Object?> toJson() => {
        'complexity': complexity.name,
        'specialistBrief': specialistBrief,
      };
}

final class AvaRealtimeTurnPlan {
  AvaRealtimeTurnPlan({
    required List<AvaRealtimeTurnSegment> segments,
    required this.memoryProposal,
    required this.childTurnSummary,
    required this.canvasDecision,
  }) : segments = List.unmodifiable(segments) {
    _validate();
  }

  final List<AvaRealtimeTurnSegment> segments;
  final AvaMemoryProposalV3? memoryProposal;
  final String childTurnSummary;
  final AvaCanvasDecisionV3 canvasDecision;

  String get speech => segments.map((segment) => segment.speech).join(' ');

  factory AvaRealtimeTurnPlan.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'segments',
        'memoryProposal',
        'childTurnSummary',
        'canvasDecision',
      },
      'realtime turn plan',
    );
    final rawSegments = json['segments'];
    if (rawSegments is! List ||
        rawSegments.isEmpty ||
        rawSegments.length > avaRealtimeMaxSpeechSegments) {
      throw const FormatException(
        'realtime turn plan requires 1-12 segments.',
      );
    }
    final rawMemory = json['memoryProposal'];
    return AvaRealtimeTurnPlan(
      segments: [
        for (var index = 0; index < rawSegments.length; index += 1)
          AvaRealtimeTurnSegment.fromJson(
            _requiredObject(
              rawSegments[index],
              'realtime turn plan.segments[$index]',
            ),
          ),
      ],
      memoryProposal: rawMemory == null
          ? null
          : AvaMemoryProposalV3.fromJson(
              _requiredObject(
                rawMemory,
                'realtime turn plan.memoryProposal',
              ),
            ),
      childTurnSummary: _boundedString(
        json,
        'childTurnSummary',
        'realtime turn plan',
        240,
      ),
      canvasDecision: AvaCanvasDecisionV3.fromJson(
        _requiredObject(
          json['canvasDecision'],
          'realtime turn plan.canvasDecision',
        ),
      ),
    );
  }

  void _validate() {
    if (segments.isEmpty || segments.length > avaRealtimeMaxSpeechSegments) {
      throw const FormatException(
        'realtime turn plan requires 1-12 segments.',
      );
    }
    if (speech.runes.length > 1800) {
      throw const FormatException(
        'realtime turn speech exceeds the 1800-character limit.',
      );
    }
    final segmentIds = <String>{};
    final cueIds = <String>{};
    final revisions = <String, int>{};
    var transactionCount = 0;
    var createdNodeCount = 0;
    for (final segment in segments) {
      if (!segmentIds.add(segment.id)) {
        throw FormatException(
          'Realtime segment id "${segment.id}" is duplicated.',
        );
      }
      for (final cue in segment.canvasCues) {
        transactionCount += 1;
        if (!cueIds.add(cue.id)) {
          throw FormatException(
            'Canvas cue id "${cue.id}" is duplicated across the turn.',
          );
        }
        final transaction = cue.transaction;
        final expectedBase = revisions[transaction.sceneId] ?? 0;
        if (transaction.baseRevision != expectedBase) {
          throw FormatException(
            'Scene ${transaction.sceneId} expected baseRevision '
            '$expectedBase, got ${transaction.baseRevision}.',
          );
        }
        revisions[transaction.sceneId] = transaction.revision;
        createdNodeCount += transaction.operations
            .where((operation) => operation.createdNodeId != null)
            .length;
      }
    }
    if (transactionCount > avaCanvasMaxTransactionsPerTurn ||
        createdNodeCount > avaCanvasMaxNodesPerTurn) {
      throw const FormatException('Realtime turn exceeds canvas limits.');
    }

    final hasCues = transactionCount > 0;
    switch (canvasDecision.complexity) {
      case AvaCanvasComplexity.none:
        if (hasCues) {
          throw const FormatException(
            'canvasDecision=none cannot contain canvas cues.',
          );
        }
        break;
      case AvaCanvasComplexity.normal:
        if (!hasCues) {
          throw const FormatException(
            'canvasDecision=normal requires canvas cues.',
          );
        }
        break;
      case AvaCanvasComplexity.specialistRequired:
        if (hasCues) {
          throw const FormatException(
            'specialistRequired defers canvas transactions.',
          );
        }
        break;
    }
  }

  Map<String, Object?> toJson() => {
        'segments': [for (final segment in segments) segment.toJson()],
        'memoryProposal': memoryProposal?.toJson(),
        'childTurnSummary': childTurnSummary,
        'canvasDecision': canvasDecision.toJson(),
      };
}

final class AvaSessionLimitsV3 {
  const AvaSessionLimitsV3({
    required this.maxTurnSeconds,
    required this.maxResponseCharacters,
    required this.maxCanvasNodes,
    required this.maxAudioChunkBytes,
    required this.playbackProgressIntervalMs,
  });

  final int maxTurnSeconds;
  final int maxResponseCharacters;
  final int maxCanvasNodes;
  final int maxAudioChunkBytes;
  final int playbackProgressIntervalMs;

  factory AvaSessionLimitsV3.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'maxTurnSeconds',
        'maxResponseCharacters',
        'maxCanvasNodes',
        'maxAudioChunkBytes',
        'playbackProgressIntervalMs',
      },
      'session limits',
    );
    final limits = AvaSessionLimitsV3(
      maxTurnSeconds: _requiredInt(json, 'maxTurnSeconds', 'session limits'),
      maxResponseCharacters:
          _requiredInt(json, 'maxResponseCharacters', 'session limits'),
      maxCanvasNodes: _requiredInt(json, 'maxCanvasNodes', 'session limits'),
      maxAudioChunkBytes:
          _requiredInt(json, 'maxAudioChunkBytes', 'session limits'),
      playbackProgressIntervalMs:
          _requiredInt(json, 'playbackProgressIntervalMs', 'session limits'),
    );
    if (limits.maxTurnSeconds < 5 ||
        limits.maxTurnSeconds > 180 ||
        limits.maxResponseCharacters < 128 ||
        limits.maxResponseCharacters > 1800 ||
        limits.maxCanvasNodes < 1 ||
        limits.maxCanvasNodes > 96 ||
        limits.maxAudioChunkBytes < 2048 ||
        limits.maxAudioChunkBytes > 192000 ||
        limits.playbackProgressIntervalMs < 50 ||
        limits.playbackProgressIntervalMs > 2000) {
      throw const FormatException('Session limits are outside v3 bounds.');
    }
    return limits;
  }

  Map<String, Object?> toJson() => {
        'maxTurnSeconds': maxTurnSeconds,
        'maxResponseCharacters': maxResponseCharacters,
        'maxCanvasNodes': maxCanvasNodes,
        'maxAudioChunkBytes': maxAudioChunkBytes,
        'playbackProgressIntervalMs': playbackProgressIntervalMs,
      };
}

final class AvaSessionCreateRequestV3 {
  const AvaSessionCreateRequestV3({required this.ageBand});

  final AvaRealtimeAgeBand ageBand;

  factory AvaSessionCreateRequestV3.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {'protocolVersion', 'ageBand'},
      'session create request',
    );
    _requireProtocolVersion(json, 'session create request');
    return AvaSessionCreateRequestV3(
      ageBand: _enumByName(
        AvaRealtimeAgeBand.values,
        _requiredString(json, 'ageBand', 'session create request'),
        'session create request.ageBand',
      ),
    );
  }

  Map<String, Object?> toJson() => {
        'protocolVersion': avaRealtimeProtocolVersion,
        'ageBand': ageBand.name,
      };
}

final class AvaSessionBootstrapV3 {
  const AvaSessionBootstrapV3({
    required this.sessionId,
    required this.eventUrl,
    required this.webrtcUrl,
    required this.sessionToken,
    required this.webrtcToken,
    required this.expiresAt,
    required this.limits,
  });

  final String sessionId;
  final String eventUrl;
  final String webrtcUrl;
  final String sessionToken;
  final String webrtcToken;
  final String expiresAt;
  final AvaSessionLimitsV3 limits;

  factory AvaSessionBootstrapV3.fromJson(
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
        'sessionToken',
        'webrtcToken',
        'expiresAt',
        'limits',
      },
      'session bootstrap',
    );
    _requireProtocolVersion(json, 'session bootstrap');
    final eventUrl = _boundedString(
      json,
      'eventUrl',
      'session bootstrap',
      2048,
    );
    final uri = Uri.tryParse(eventUrl);
    final eventIsSecure = uri?.scheme == 'wss';
    final eventIsAllowedLoopback = allowInsecureLoopback &&
        uri?.scheme == 'ws' &&
        _isLoopbackHost(uri!.host);
    if (uri == null ||
        (!eventIsSecure && !eventIsAllowedLoopback) ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment) {
      throw const FormatException(
        'session bootstrap.eventUrl must be a credential-free absolute WSS '
        'URL without a query or fragment.',
      );
    }
    final expiresAt = _boundedString(
      json,
      'expiresAt',
      'session bootstrap',
      64,
    );
    if (DateTime.tryParse(expiresAt)?.isUtc != true) {
      throw const FormatException(
        'session bootstrap.expiresAt must be an ISO-8601 UTC timestamp.',
      );
    }
    final webrtcUrl = _boundedString(
      json,
      'webrtcUrl',
      'session bootstrap',
      2048,
    );
    final webrtcUri = Uri.tryParse(webrtcUrl);
    final webrtcIsSecure = webrtcUri?.scheme == 'https';
    final webrtcIsAllowedLoopback = allowInsecureLoopback &&
        webrtcUri?.scheme == 'http' &&
        _isLoopbackHost(webrtcUri!.host);
    if (webrtcUri == null ||
        (!webrtcIsSecure && !webrtcIsAllowedLoopback) ||
        webrtcUri.host.isEmpty ||
        webrtcUri.userInfo.isNotEmpty ||
        webrtcUri.hasQuery ||
        webrtcUri.hasFragment) {
      throw const FormatException(
        'session bootstrap.webrtcUrl must be a credential-free absolute HTTPS '
        'URL without a query or fragment.',
      );
    }
    return AvaSessionBootstrapV3(
      sessionId: _protocolId(json, 'sessionId', 'session bootstrap'),
      eventUrl: eventUrl,
      webrtcUrl: webrtcUrl,
      sessionToken: _boundedString(
        json,
        'sessionToken',
        'session bootstrap',
        2048,
      ),
      webrtcToken: _boundedString(
        json,
        'webrtcToken',
        'session bootstrap',
        2048,
      ),
      expiresAt: expiresAt,
      limits: AvaSessionLimitsV3.fromJson(
        _requiredObject(json['limits'], 'session bootstrap.limits'),
      ),
    );
  }

  Map<String, Object?> toJson() => {
        'protocolVersion': avaRealtimeProtocolVersion,
        'sessionId': sessionId,
        'eventUrl': eventUrl,
        'webrtcUrl': webrtcUrl,
        'sessionToken': sessionToken,
        'webrtcToken': webrtcToken,
        'expiresAt': expiresAt,
        'limits': limits.toJson(),
      };
}

bool _isLoopbackHost(String host) =>
    host == 'localhost' || host == '127.0.0.1' || host == '::1';

final class AvaRealtimeClientEvent {
  AvaRealtimeClientEvent._({
    required this.type,
    required this.sessionId,
    required this.sequence,
    required Map<String, Object?> json,
    this.turnId,
    this.streamId,
    this.renderedSampleCount,
    this.bufferedSampleCount,
    this.sceneId,
    this.sceneRevision,
    this.interactionRevision,
    this.bindingId,
    this.requestId,
    this.reason,
  }) : _json = _deepCopyMap(json);

  final AvaRealtimeClientEventType type;
  final String sessionId;
  final int sequence;
  final String? turnId;
  final String? streamId;
  final int? renderedSampleCount;
  final int? bufferedSampleCount;
  final String? sceneId;
  final int? sceneRevision;
  final int? interactionRevision;
  final String? bindingId;
  final String? requestId;
  final String? reason;
  final Map<String, Object?> _json;

  factory AvaRealtimeClientEvent.clientReady({
    required String sessionId,
    required int sequence,
  }) =>
      AvaRealtimeClientEvent.fromJson({
        'protocolVersion': avaRealtimeProtocolVersion,
        'event': AvaRealtimeClientEventType.clientReady.name,
        'sessionId': sessionId,
        'sequence': sequence,
      });

  factory AvaRealtimeClientEvent.playbackStarted({
    required String sessionId,
    required int sequence,
    required String turnId,
    required String streamId,
  }) =>
      AvaRealtimeClientEvent.fromJson({
        'protocolVersion': avaRealtimeProtocolVersion,
        'event': AvaRealtimeClientEventType.playbackStarted.name,
        'sessionId': sessionId,
        'sequence': sequence,
        'turnId': turnId,
        'streamId': streamId,
        'renderedSampleCount': 0,
      });

  factory AvaRealtimeClientEvent.playbackProgress({
    required String sessionId,
    required int sequence,
    required String turnId,
    required String streamId,
    required int renderedSampleCount,
    required int bufferedSampleCount,
  }) =>
      AvaRealtimeClientEvent.fromJson({
        'protocolVersion': avaRealtimeProtocolVersion,
        'event': AvaRealtimeClientEventType.playbackProgress.name,
        'sessionId': sessionId,
        'sequence': sequence,
        'turnId': turnId,
        'streamId': streamId,
        'renderedSampleCount': renderedSampleCount,
        'bufferedSampleCount': bufferedSampleCount,
      });

  factory AvaRealtimeClientEvent.playbackStopped({
    required String sessionId,
    required int sequence,
    required String turnId,
    required String streamId,
    required int renderedSampleCount,
    required String reason,
  }) =>
      AvaRealtimeClientEvent.fromJson({
        'protocolVersion': avaRealtimeProtocolVersion,
        'event': AvaRealtimeClientEventType.playbackStopped.name,
        'sessionId': sessionId,
        'sequence': sequence,
        'turnId': turnId,
        'streamId': streamId,
        'renderedSampleCount': renderedSampleCount,
        'reason': reason,
      });

  factory AvaRealtimeClientEvent.cancelTurn({
    required String sessionId,
    required int sequence,
    required String turnId,
    required String requestId,
    required String reason,
  }) =>
      AvaRealtimeClientEvent.fromJson({
        'protocolVersion': avaRealtimeProtocolVersion,
        'event': AvaRealtimeClientEventType.cancelTurn.name,
        'sessionId': sessionId,
        'sequence': sequence,
        'turnId': turnId,
        'requestId': requestId,
        'reason': reason,
      });

  factory AvaRealtimeClientEvent.canvasInteraction({
    required String sessionId,
    required int sequence,
    required String sceneId,
    required int sceneRevision,
    required int interactionRevision,
    required String bindingId,
  }) =>
      AvaRealtimeClientEvent.fromJson({
        'protocolVersion': avaRealtimeProtocolVersion,
        'event': AvaRealtimeClientEventType.canvasInteraction.name,
        'sessionId': sessionId,
        'sequence': sequence,
        'sceneId': sceneId,
        'sceneRevision': sceneRevision,
        'interactionRevision': interactionRevision,
        'bindingId': bindingId,
      });

  factory AvaRealtimeClientEvent.fromJson(Map<String, Object?> json) {
    _requireProtocolVersion(json, 'client event');
    final type = _enumByName(
      AvaRealtimeClientEventType.values,
      _requiredString(json, 'event', 'client event'),
      'client event.event',
    );
    final sessionId = _protocolId(json, 'sessionId', 'client event');
    final sequence = _requiredInt(json, 'sequence', 'client event');
    if (sequence < 0 || sequence > 1000000) {
      throw const FormatException(
        'client event.sequence must be between 0 and 1000000.',
      );
    }
    const common = {
      'protocolVersion',
      'event',
      'sessionId',
      'sequence',
    };

    String? turnId;
    String? streamId;
    int? renderedSampleCount;
    int? bufferedSampleCount;
    String? sceneId;
    int? sceneRevision;
    int? interactionRevision;
    String? bindingId;
    String? requestId;
    String? reason;

    switch (type) {
      case AvaRealtimeClientEventType.clientReady:
        _requireExactKeys(json, common, 'clientReady');
        break;
      case AvaRealtimeClientEventType.playbackStarted:
        _requireExactKeys(
          json,
          {
            ...common,
            'turnId',
            'streamId',
            'renderedSampleCount',
          },
          'playbackStarted',
        );
        turnId = _protocolId(json, 'turnId', 'playbackStarted');
        streamId = _protocolId(json, 'streamId', 'playbackStarted');
        renderedSampleCount = _requiredInt(
          json,
          'renderedSampleCount',
          'playbackStarted',
        );
        if (renderedSampleCount != 0) {
          throw const FormatException(
            'playbackStarted.renderedSampleCount must be zero.',
          );
        }
        break;
      case AvaRealtimeClientEventType.playbackProgress:
        _requireExactKeys(
          json,
          {
            ...common,
            'turnId',
            'streamId',
            'renderedSampleCount',
            'bufferedSampleCount',
          },
          'playbackProgress',
        );
        turnId = _protocolId(json, 'turnId', 'playbackProgress');
        streamId = _protocolId(json, 'streamId', 'playbackProgress');
        renderedSampleCount = _boundedSampleCount(
          json,
          'renderedSampleCount',
          'playbackProgress',
        );
        bufferedSampleCount = _boundedSampleCount(
          json,
          'bufferedSampleCount',
          'playbackProgress',
        );
        break;
      case AvaRealtimeClientEventType.playbackStopped:
        _requireExactKeys(
          json,
          {
            ...common,
            'turnId',
            'streamId',
            'renderedSampleCount',
            'reason',
          },
          'playbackStopped',
        );
        turnId = _protocolId(json, 'turnId', 'playbackStopped');
        streamId = _protocolId(json, 'streamId', 'playbackStopped');
        renderedSampleCount = _boundedSampleCount(
          json,
          'renderedSampleCount',
          'playbackStopped',
        );
        reason = _boundedString(json, 'reason', 'playbackStopped', 120);
        break;
      case AvaRealtimeClientEventType.canvasInteraction:
        _requireExactKeys(
          json,
          {
            ...common,
            'sceneId',
            'sceneRevision',
            'interactionRevision',
            'bindingId',
          },
          'canvasInteraction',
        );
        sceneId = _protocolId(json, 'sceneId', 'canvasInteraction');
        bindingId = _protocolId(json, 'bindingId', 'canvasInteraction');
        sceneRevision = _requiredInt(
          json,
          'sceneRevision',
          'canvasInteraction',
        );
        if (sceneRevision < 1 || sceneRevision > 10001) {
          throw const FormatException(
            'canvasInteraction.sceneRevision must be between 1 and 10001.',
          );
        }
        interactionRevision = _requiredInt(
          json,
          'interactionRevision',
          'canvasInteraction',
        );
        if (interactionRevision < 1 || interactionRevision > 1000000) {
          throw const FormatException(
            'canvasInteraction.interactionRevision must be between 1 and 1000000.',
          );
        }
        break;
      case AvaRealtimeClientEventType.cancelTurn:
        _requireExactKeys(
          json,
          {
            ...common,
            'turnId',
            'requestId',
            'reason',
          },
          'cancelTurn',
        );
        turnId = _protocolId(json, 'turnId', 'cancelTurn');
        requestId = _protocolId(json, 'requestId', 'cancelTurn');
        reason = _boundedString(json, 'reason', 'cancelTurn', 120);
        break;
    }

    return AvaRealtimeClientEvent._(
      type: type,
      sessionId: sessionId,
      sequence: sequence,
      json: json,
      turnId: turnId,
      streamId: streamId,
      renderedSampleCount: renderedSampleCount,
      bufferedSampleCount: bufferedSampleCount,
      sceneId: sceneId,
      sceneRevision: sceneRevision,
      interactionRevision: interactionRevision,
      bindingId: bindingId,
      requestId: requestId,
      reason: reason,
    );
  }

  Map<String, Object?> toJson() => _deepCopyMap(_json);
}

final class AvaRealtimeAlignmentSpan {
  const AvaRealtimeAlignmentSpan({
    required this.segmentId,
    required this.textStart,
    required this.textEnd,
    required this.sampleStart,
    required this.sampleEnd,
    required this.phoneme,
  });

  final String segmentId;
  final int textStart;
  final int textEnd;
  final int sampleStart;
  final int sampleEnd;
  final String? phoneme;

  factory AvaRealtimeAlignmentSpan.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'segmentId',
        'textStart',
        'textEnd',
        'sampleStart',
        'sampleEnd',
        'phoneme',
      },
      'alignment span',
    );
    final textStart = _requiredInt(json, 'textStart', 'alignment span');
    final textEnd = _requiredInt(json, 'textEnd', 'alignment span');
    final sampleStart = _requiredInt(json, 'sampleStart', 'alignment span');
    final sampleEnd = _requiredInt(json, 'sampleEnd', 'alignment span');
    if (textStart < 0 ||
        textEnd <= textStart ||
        textEnd > 1800 ||
        sampleStart < 0 ||
        sampleEnd <= sampleStart) {
      throw const FormatException('Alignment span ranges are invalid.');
    }
    final rawPhoneme = json['phoneme'];
    if (rawPhoneme != null &&
        (rawPhoneme is! String ||
            rawPhoneme.isEmpty ||
            rawPhoneme.runes.length > 16)) {
      throw const FormatException('alignment span.phoneme is invalid.');
    }
    return AvaRealtimeAlignmentSpan(
      segmentId: _segmentId(json, 'segmentId', 'alignment span'),
      textStart: textStart,
      textEnd: textEnd,
      sampleStart: sampleStart,
      sampleEnd: sampleEnd,
      phoneme: rawPhoneme as String?,
    );
  }

  Map<String, Object?> toJson() => {
        'segmentId': segmentId,
        'textStart': textStart,
        'textEnd': textEnd,
        'sampleStart': sampleStart,
        'sampleEnd': sampleEnd,
        'phoneme': phoneme,
      };
}

final class AvaRealtimeFailure {
  const AvaRealtimeFailure({
    required this.failureId,
    required this.component,
    required this.code,
    required this.message,
    required this.retryable,
  });

  final String failureId;
  final String component;
  final String code;
  final String message;
  final bool retryable;

  factory AvaRealtimeFailure.fromJson(Map<String, Object?> json) {
    _validateFailure(json);
    return AvaRealtimeFailure(
      failureId: json['failureId']! as String,
      component: json['component']! as String,
      code: json['code']! as String,
      message: json['message']! as String,
      retryable: json['retryable']! as bool,
    );
  }
}

final class AvaRealtimeServerEvent {
  AvaRealtimeServerEvent._({
    required this.type,
    required this.sessionId,
    required this.turnId,
    required this.sequence,
    required this.revision,
    required Map<String, Object?> json,
    this.phase,
    this.segmentIndex,
    this.segment,
    this.streamId,
    this.chunkIndex,
    this.sampleOffset,
    this.sampleCount,
    this.byteLength,
    this.alignmentSpans = const [],
    this.cueId,
    this.transaction,
    this.sceneId,
    this.sceneRevision,
    this.interactionRevision,
    this.bindingId,
    this.interactionRejectionCode,
    this.interactionRejectionMessage,
    this.deliveredSampleCount,
    this.deliveredText,
    this.terminalReason,
    this.failure,
  }) : _json = _deepCopyMap(json);

  final AvaRealtimeServerEventType type;
  final String sessionId;
  final String turnId;
  final int sequence;
  final int revision;
  final AvaRealtimeTurnPhase? phase;
  final int? segmentIndex;
  final AvaRealtimeTurnSegment? segment;
  final String? streamId;
  final int? chunkIndex;
  final int? sampleOffset;
  final int? sampleCount;
  final int? byteLength;
  final List<AvaRealtimeAlignmentSpan> alignmentSpans;
  final String? cueId;
  final AvaCanvasTransaction? transaction;
  final String? sceneId;
  final int? sceneRevision;
  final int? interactionRevision;
  final String? bindingId;
  final AvaCanvasInteractionRejectionCode? interactionRejectionCode;
  final String? interactionRejectionMessage;
  final int? deliveredSampleCount;
  final String? deliveredText;
  final String? terminalReason;
  final AvaRealtimeFailure? failure;
  final Map<String, Object?> _json;

  bool get isTerminal =>
      type == AvaRealtimeServerEventType.turnCompleted ||
      type == AvaRealtimeServerEventType.turnCancelled ||
      type == AvaRealtimeServerEventType.turnFailed;

  factory AvaRealtimeServerEvent.fromJson(Map<String, Object?> json) {
    _requireProtocolVersion(json, 'realtime event');
    final type = _enumByName(
      AvaRealtimeServerEventType.values,
      _requiredString(json, 'event', 'realtime event'),
      'realtime event.event',
    );
    final sessionId = _protocolId(json, 'sessionId', 'realtime event');
    final turnId = _protocolId(json, 'turnId', 'realtime event');
    final sequence = _requiredInt(json, 'sequence', 'realtime event');
    final revision = _requiredInt(json, 'revision', 'realtime event');
    if (sequence < 0 ||
        sequence > 1000000 ||
        revision < 0 ||
        revision > 1000000) {
      throw const FormatException(
        'realtime event sequence/revision is outside bounds.',
      );
    }
    const common = {
      'protocolVersion',
      'event',
      'sessionId',
      'turnId',
      'sequence',
      'revision',
    };

    AvaRealtimeTurnPhase? phase;
    int? segmentIndex;
    AvaRealtimeTurnSegment? segment;
    String? streamId;
    int? chunkIndex;
    int? sampleOffset;
    int? sampleCount;
    int? byteLength;
    List<AvaRealtimeAlignmentSpan> alignmentSpans = const [];
    String? cueId;
    AvaCanvasTransaction? transaction;
    String? sceneId;
    int? sceneRevision;
    int? interactionRevision;
    String? bindingId;
    AvaCanvasInteractionRejectionCode? interactionRejectionCode;
    String? interactionRejectionMessage;
    int? deliveredSampleCount;
    String? deliveredText;
    String? terminalReason;
    AvaRealtimeFailure? failure;

    switch (type) {
      case AvaRealtimeServerEventType.turnCreated:
        _requireExactKeys(
          json,
          {...common, 'requestId'},
          'turnCreated',
        );
        _protocolId(json, 'requestId', 'turnCreated');
        break;
      case AvaRealtimeServerEventType.turnPhaseChanged:
        _requireExactKeys(json, {...common, 'phase'}, 'turnPhaseChanged');
        phase = _enumByName(
          AvaRealtimeTurnPhase.values,
          _requiredString(json, 'phase', 'turnPhaseChanged'),
          'turnPhaseChanged.phase',
        );
        break;
      case AvaRealtimeServerEventType.segmentReady:
        _requireExactKeys(
          json,
          {...common, 'segmentIndex', 'segment'},
          'segmentReady',
        );
        segmentIndex = _requiredInt(json, 'segmentIndex', 'segmentReady');
        if (segmentIndex < 0 || segmentIndex >= avaRealtimeMaxSpeechSegments) {
          throw const FormatException(
            'segmentReady.segmentIndex must be between 0 and 11.',
          );
        }
        segment = AvaRealtimeTurnSegment.fromJson(
          _requiredObject(json['segment'], 'segmentReady.segment'),
        );
        break;
      case AvaRealtimeServerEventType.audioMetadata:
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
        streamId = _protocolId(json, 'streamId', 'audioMetadata');
        if (_requiredString(json, 'audioFormat', 'audioMetadata') !=
                'pcm_s16le' ||
            _requiredInt(json, 'sampleRateHz', 'audioMetadata') != 24000 ||
            _requiredInt(json, 'channels', 'audioMetadata') != 1 ||
            _requiredInt(json, 'bitsPerSample', 'audioMetadata') != 16) {
          throw const FormatException(
            'audioMetadata must describe mono 24 kHz PCM16.',
          );
        }
        _protocolId(json, 'modelId', 'audioMetadata');
        _protocolId(json, 'voiceId', 'audioMetadata');
        break;
      case AvaRealtimeServerEventType.audioChunk:
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
        streamId = _protocolId(json, 'streamId', 'audioChunk');
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
      case AvaRealtimeServerEventType.alignmentBatch:
        _requireExactKeys(
          json,
          {...common, 'streamId', 'spans'},
          'alignmentBatch',
        );
        streamId = _protocolId(json, 'streamId', 'alignmentBatch');
        alignmentSpans = _alignmentSpans(json['spans']);
        break;
      case AvaRealtimeServerEventType.canvasCue:
        _requireExactKeys(
          json,
          {
            ...common,
            'cueId',
            'sampleOffset',
            'transaction',
          },
          'canvasCue',
        );
        cueId = _protocolId(json, 'cueId', 'canvasCue');
        sampleOffset = _requiredInt(json, 'sampleOffset', 'canvasCue');
        if (sampleOffset < 0) {
          throw const FormatException(
            'canvasCue.sampleOffset cannot be negative.',
          );
        }
        try {
          transaction = AvaCanvasTransaction.fromJson(
            _requiredObject(json['transaction'], 'canvasCue.transaction'),
          );
        } on FormatException catch (error) {
          const rejectionMessage =
              'Ava rejected an invalid optional canvas payload.';
          final rejectionFailure = AvaRealtimeFailure(
            failureId: 'failure-local-canvas-$sequence',
            component: 'protocol',
            code: 'canvasRejected',
            message: rejectionMessage,
            retryable: false,
          );
          final rejectionJson = <String, Object?>{
            'protocolVersion': avaRealtimeProtocolVersion,
            'event': AvaRealtimeServerEventType.canvasRejected.name,
            'sessionId': sessionId,
            'turnId': turnId,
            'sequence': sequence,
            'revision': revision,
            'failure': {
              'failureId': rejectionFailure.failureId,
              'component': rejectionFailure.component,
              'code': rejectionFailure.code,
              'message': rejectionFailure.message,
              'retryable': rejectionFailure.retryable,
            },
          };
          throw AvaRealtimeCanvasPayloadException(
            detail: error.toString(),
            rejectionEvent: AvaRealtimeServerEvent._(
              type: AvaRealtimeServerEventType.canvasRejected,
              sessionId: sessionId,
              turnId: turnId,
              sequence: sequence,
              revision: revision,
              json: rejectionJson,
              failure: rejectionFailure,
            ),
          );
        }
        break;
      case AvaRealtimeServerEventType.canvasRejected:
        _requireExactKeys(
          json,
          {...common, 'failure'},
          'canvasRejected',
        );
        failure = AvaRealtimeFailure.fromJson(
          _requiredObject(json['failure'], 'canvasRejected.failure'),
        );
        if (failure.code != 'canvasRejected') {
          throw const FormatException(
            'canvasRejected requires failure.code=canvasRejected.',
          );
        }
        break;
      case AvaRealtimeServerEventType.interactionRejected:
        _requireExactKeys(
          json,
          {
            ...common,
            'sceneId',
            'sceneRevision',
            'interactionRevision',
            'bindingId',
            'rejectionCode',
            'message',
          },
          'interactionRejected',
        );
        sceneId = _protocolId(json, 'sceneId', 'interactionRejected');
        sceneRevision = _requiredInt(
          json,
          'sceneRevision',
          'interactionRejected',
        );
        interactionRevision = _requiredInt(
          json,
          'interactionRevision',
          'interactionRejected',
        );
        bindingId = _protocolId(json, 'bindingId', 'interactionRejected');
        interactionRejectionCode = _enumByName(
          AvaCanvasInteractionRejectionCode.values,
          _requiredString(json, 'rejectionCode', 'interactionRejected'),
          'interactionRejected.rejectionCode',
        );
        interactionRejectionMessage = _boundedString(
          json,
          'message',
          'interactionRejected',
          240,
        );
        if (sceneRevision < 1 ||
            sceneRevision > 10001 ||
            interactionRevision < 1 ||
            interactionRevision > 1000000) {
          throw const FormatException(
            'interactionRejected revisions are outside protocol bounds.',
          );
        }
        break;
      case AvaRealtimeServerEventType.memoryPersistenceFailed:
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
      case AvaRealtimeServerEventType.turnCompleted:
        _requireExactKeys(
          json,
          {...common, 'deliveredSampleCount', 'deliveredText'},
          'turnCompleted',
        );
        deliveredSampleCount = _deliveredFields(json, 'turnCompleted');
        deliveredText = json['deliveredText']! as String;
        break;
      case AvaRealtimeServerEventType.turnCancelled:
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
        deliveredSampleCount = _deliveredFields(json, 'turnCancelled');
        deliveredText = json['deliveredText']! as String;
        terminalReason = _boundedString(json, 'reason', 'turnCancelled', 120);
        break;
      case AvaRealtimeServerEventType.turnFailed:
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
        deliveredSampleCount = _deliveredFields(json, 'turnFailed');
        deliveredText = json['deliveredText']! as String;
        failure = AvaRealtimeFailure.fromJson(
          _requiredObject(json['failure'], 'turnFailed.failure'),
        );
        break;
    }
    return AvaRealtimeServerEvent._(
      type: type,
      sessionId: sessionId,
      turnId: turnId,
      sequence: sequence,
      revision: revision,
      json: json,
      phase: phase,
      segmentIndex: segmentIndex,
      segment: segment,
      streamId: streamId,
      chunkIndex: chunkIndex,
      sampleOffset: sampleOffset,
      sampleCount: sampleCount,
      byteLength: byteLength,
      alignmentSpans: List.unmodifiable(alignmentSpans),
      cueId: cueId,
      transaction: transaction,
      sceneId: sceneId,
      sceneRevision: sceneRevision,
      interactionRevision: interactionRevision,
      bindingId: bindingId,
      interactionRejectionCode: interactionRejectionCode,
      interactionRejectionMessage: interactionRejectionMessage,
      deliveredSampleCount: deliveredSampleCount,
      deliveredText: deliveredText,
      terminalReason: terminalReason,
      failure: failure,
    );
  }

  Map<String, Object?> toJson() => _deepCopyMap(_json);
}

/// A schema-valid realtime envelope whose optional canvas transaction failed
/// strict local validation.
///
/// The transport converts only this exception into the attached rejection
/// event. Envelope, ordering, audio and all other protocol failures continue
/// to terminate the connection loudly.
final class AvaRealtimeCanvasPayloadException implements Exception {
  const AvaRealtimeCanvasPayloadException({
    required this.detail,
    required this.rejectionEvent,
  });

  final String detail;
  final AvaRealtimeServerEvent rejectionEvent;

  @override
  String toString() => 'AvaRealtimeCanvasPayloadException: $detail';
}

int _boundedSampleCount(
  Map<String, Object?> json,
  String key,
  String context,
) {
  final value = _requiredInt(json, key, context);
  if (value < 0 || value > 2147483647) {
    throw FormatException('$context.$key is outside sample-count bounds.');
  }
  return value;
}

int _deliveredFields(Map<String, Object?> json, String context) {
  final samples = _requiredInt(json, 'deliveredSampleCount', context);
  if (samples < 0) {
    throw FormatException('$context.deliveredSampleCount cannot be negative.');
  }
  final deliveredText = json['deliveredText'];
  if (deliveredText is! String || deliveredText.runes.length > 1800) {
    throw FormatException(
      '$context.deliveredText must contain at most 1800 characters.',
    );
  }
  return samples;
}

void _validateFailure(Map<String, Object?> json) {
  _requireExactKeys(
    json,
    const {'failureId', 'component', 'code', 'message', 'retryable'},
    'realtime failure',
  );
  _protocolId(json, 'failureId', 'realtime failure');
  _allowedString(
    json,
    'component',
    'realtime failure',
    const {
      'realtimeProvider',
      'speechProvider',
      'canvasSpecialist',
      'memoryStore',
      'playback',
      'protocol',
      'coordinator',
    },
  );
  _allowedString(
    json,
    'code',
    'realtime failure',
    const {
      'authentication',
      'realtimeNegotiation',
      'modelInvalid',
      'modelTimeout',
      'modelUnavailable',
      'speechInvalid',
      'speechTimeout',
      'speechUnavailable',
      'protocolSequence',
      'canvasRejected',
      'memoryUnavailable',
      'playbackUnavailable',
      'cancelled',
      'internal',
    },
  );
  _boundedString(json, 'message', 'realtime failure', 240);
  _requiredBool(json, 'retryable', 'realtime failure');
}

List<AvaRealtimeAlignmentSpan> _alignmentSpans(Object? value) {
  if (value is! List || value.isEmpty || value.length > 512) {
    throw const FormatException(
      'alignmentBatch.spans requires 1-512 spans.',
    );
  }
  var previousSampleStart = -1;
  final spans = <AvaRealtimeAlignmentSpan>[];
  for (var index = 0; index < value.length; index += 1) {
    final span = _requiredObject(value[index], 'alignment span[$index]');
    final parsed = AvaRealtimeAlignmentSpan.fromJson(span);
    if (parsed.sampleStart < previousSampleStart) {
      throw const FormatException('Alignment span ranges are invalid.');
    }
    spans.add(parsed);
    previousSampleStart = parsed.sampleStart;
  }
  return spans;
}

List<int> _substringPositions(String text, String phrase) {
  final positions = <int>[];
  var start = 0;
  while (true) {
    final index = text.indexOf(phrase, start);
    if (index < 0) return positions;
    positions.add(index);
    start = index + phrase.length;
  }
}

void _requireProtocolVersion(Map<String, Object?> json, String context) {
  final version = _requiredInt(json, 'protocolVersion', context);
  if (version != avaRealtimeProtocolVersion) {
    throw FormatException(
      '$context.protocolVersion must be $avaRealtimeProtocolVersion.',
    );
  }
}

String _segmentId(Map<String, Object?> json, String key, String context) {
  final value = _requiredString(json, key, context);
  if (!RegExp(r'^[a-z][a-z0-9_]{0,39}$').hasMatch(value)) {
    throw FormatException('$context.$key has an invalid segment identifier.');
  }
  return value;
}

String _nodeId(Map<String, Object?> json, String key, String context) {
  final value = _requiredString(json, key, context);
  if (!RegExp(r'^[a-z][a-z0-9_]{0,47}$').hasMatch(value)) {
    throw FormatException('$context.$key has an invalid node identifier.');
  }
  return value;
}

String _protocolId(Map<String, Object?> json, String key, String context) {
  final value = _requiredString(json, key, context);
  if (!RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,63}$').hasMatch(value)) {
    throw FormatException('$context.$key has an invalid identifier.');
  }
  return value;
}

String _allowedString(
  Map<String, Object?> json,
  String key,
  String context,
  Set<String> allowed,
) {
  final value = _requiredString(json, key, context);
  if (!allowed.contains(value)) {
    throw FormatException('$context.$key has unsupported value "$value".');
  }
  return value;
}

String _boundedString(
  Map<String, Object?> json,
  String key,
  String context,
  int maximum,
) {
  final value = _requiredString(json, key, context);
  if (value.runes.length > maximum) {
    throw FormatException('$context.$key exceeds the character limit.');
  }
  return value;
}

double _requiredFiniteNumber(
  Map<String, Object?> json,
  String key,
  String context,
) {
  final value = json[key];
  if (value is! num || !value.isFinite) {
    throw FormatException('$context.$key must be a finite number.');
  }
  return value.toDouble();
}

T _enumByName<T extends Enum>(
  List<T> values,
  String name,
  String context,
) {
  for (final value in values) {
    if (value.name == name) return value;
  }
  throw FormatException('$context has unsupported value "$name".');
}

void _requireExactKeys(
  Map<String, Object?> source,
  Set<String> allowed,
  String context,
) {
  final unknown = source.keys.toSet().difference(allowed);
  if (unknown.isNotEmpty) {
    throw FormatException(
      '$context contains unsupported fields: ${unknown.join(', ')}.',
    );
  }
  final missing = allowed.difference(source.keys.toSet());
  if (missing.isNotEmpty) {
    throw FormatException(
      '$context is missing required fields: ${missing.join(', ')}.',
    );
  }
}

Map<String, Object?> _requiredObject(Object? value, String context) {
  if (value is! Map) {
    throw FormatException('$context must be an object.');
  }
  return value.cast<String, Object?>();
}

String _requiredString(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('$context.$key must be a non-empty string.');
  }
  return value;
}

int _requiredInt(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! int) {
    throw FormatException('$context.$key must be an integer.');
  }
  return value;
}

bool _requiredBool(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! bool) {
    throw FormatException('$context.$key must be a boolean.');
  }
  return value;
}

Map<String, Object?> _deepCopyMap(Map<String, Object?> source) {
  return (jsonDecode(jsonEncode(source)) as Map).cast<String, Object?>();
}
