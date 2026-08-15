import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../config/ava_backend_configuration.dart';
import '../models/ava_performance_event.dart';
import '../models/ava_performance_plan.dart';
import '../models/lesson_response.dart';
import 'production_conversation_service.dart';

class AvaPerformanceBackendResponse {
  const AvaPerformanceBackendResponse({
    required this.statusCode,
    required this.contentType,
    required this.bytes,
    this.requestId,
  });

  final int statusCode;
  final String? contentType;
  final Stream<List<int>> bytes;
  final String? requestId;
}

abstract interface class AvaPerformanceBackendTransport {
  Future<AvaPerformanceBackendResponse> open({
    required Uri endpoint,
    required Map<String, String> headers,
    required String body,
    required Duration timeout,
  });

  void close();
}

class IoAvaPerformanceBackendTransport
    implements AvaPerformanceBackendTransport {
  IoAvaPerformanceBackendTransport({HttpClient? client})
      : _client = client ?? HttpClient();

  final HttpClient _client;

  @override
  Future<AvaPerformanceBackendResponse> open({
    required Uri endpoint,
    required Map<String, String> headers,
    required String body,
    required Duration timeout,
  }) async {
    final request = await _client.postUrl(endpoint).timeout(timeout);
    headers.forEach(request.headers.set);
    request.add(utf8.encode(body));
    final response = await request.close().timeout(timeout);
    return AvaPerformanceBackendResponse(
      statusCode: response.statusCode,
      contentType: response.headers.contentType?.mimeType,
      bytes: response.timeout(timeout),
      requestId: response.headers.value('x-request-id'),
    );
  }

  @override
  void close() => _client.close(force: true);
}

abstract interface class PerformanceStreamService {
  Stream<AvaPerformanceEvent> respond(ConversationRequest request);
}

class AvaPerformanceStreamService implements PerformanceStreamService {
  const AvaPerformanceStreamService({
    required AvaBackendConfiguration configuration,
    required AvaPerformanceBackendTransport transport,
  })  : _configuration = configuration,
        _transport = transport;

  static const _maximumErrorBytes = 64 * 1024;
  static const _maximumEventCharacters = 4 * 1024 * 1024;
  static const _maximumEvents = 2048;

  final AvaBackendConfiguration _configuration;
  final AvaPerformanceBackendTransport _transport;

  @override
  Stream<AvaPerformanceEvent> respond(ConversationRequest request) async* {
    request.validate();
    final response = await _transport.open(
      endpoint: _configuration.performanceTurnsEndpoint,
      headers: const {
        HttpHeaders.acceptHeader: 'application/x-ndjson',
        HttpHeaders.contentTypeHeader: 'application/json; charset=utf-8',
        'x-ava-protocol-version': '2',
      },
      body: jsonEncode({
        'protocolVersion': 2,
        'request': request.toJson(),
      }),
      timeout: _configuration.requestTimeout,
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      await _drainBoundedError(response.bytes);
      throw AvaBackendException(
        message: 'Ava backend rejected the performance request.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    if (response.contentType != 'application/x-ndjson') {
      throw AvaBackendException(
        message: 'Ava backend returned a non-NDJSON performance stream.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }

    var expectedSequence = 0;
    String? turnId;
    var terminalReceived = false;
    var eventCount = 0;
    final sequenceValidator = _PerformanceSequenceValidator();
    final lines =
        response.bytes.transform(utf8.decoder).transform(const LineSplitter());
    await for (final line in lines) {
      if (line.trim().isEmpty) {
        continue;
      }
      if (terminalReceived) {
        throw const FormatException(
          'Ava performance stream continued after a terminal event.',
        );
      }
      if (line.length > _maximumEventCharacters) {
        throw const FormatException(
          'Ava performance event exceeded the 4 MiB limit.',
        );
      }
      eventCount += 1;
      if (eventCount > _maximumEvents) {
        throw const FormatException(
          'Ava performance stream exceeded the event limit.',
        );
      }

      final Object? decoded;
      try {
        decoded = jsonDecode(line);
      } on FormatException catch (error) {
        throw FormatException(
          'Ava performance stream returned malformed JSON: ${error.message}',
        );
      }
      if (decoded is! Map) {
        throw const FormatException(
          'Ava performance event must be a JSON object.',
        );
      }
      final event = AvaPerformanceEvent.fromJson(
        decoded.cast<String, Object?>(),
      );
      if (event.sequence != expectedSequence) {
        throw FormatException(
          'Ava performance event sequence mismatch: expected '
          '$expectedSequence, received ${event.sequence}.',
        );
      }
      if (expectedSequence == 0 &&
          event.event != PerformanceEventType.turnAccepted) {
        throw const FormatException(
          'Ava performance stream must begin with turnAccepted.',
        );
      }
      turnId ??= event.turnId;
      if (event.turnId != turnId) {
        throw const FormatException(
          'Ava performance stream changed turnId mid-turn.',
        );
      }
      sequenceValidator.accept(event);
      expectedSequence += 1;
      terminalReceived = event.isTerminal;
      yield event;
    }

    if (!terminalReceived) {
      throw const FormatException(
        'Ava performance stream ended without a terminal event.',
      );
    }
  }

  Future<void> _drainBoundedError(Stream<List<int>> stream) async {
    var received = 0;
    await for (final chunk in stream) {
      received += chunk.length;
      if (received > _maximumErrorBytes) {
        throw const FormatException(
          'Ava backend error response exceeded the 64 KiB limit.',
        );
      }
    }
  }
}

class _PerformanceSequenceValidator {
  AvaPerformancePlan? _plan;
  var _accepted = false;
  var _metadataReceived = false;
  var _nextSegmentIndex = 0;
  String? _activeSegmentId;
  var _nextChunkIndex = 0;
  int? _pendingTimingChunk;
  var _currentSegmentHasAudio = false;
  var _audioChunkCount = 0;
  final Set<String> _diagramCueSegments = {};

  void accept(AvaPerformanceEvent event) {
    if (!_accepted) {
      if (event is! AvaTurnAcceptedEvent) {
        throw const FormatException(
          'Ava performance stream must begin with turnAccepted.',
        );
      }
      _accepted = true;
      return;
    }
    if (event is AvaTurnFailedEvent || event is AvaTurnCancelledEvent) {
      return;
    }
    if (_plan == null) {
      if (event is! AvaPerformancePlanEvent) {
        throw const FormatException(
          'turnAccepted must be followed by performancePlan.',
        );
      }
      _plan = event.performance;
      return;
    }
    if (!_metadataReceived) {
      if (event is! AvaAudioMetadataEvent) {
        throw const FormatException(
          'performancePlan must be followed by audioMetadata.',
        );
      }
      _metadataReceived = true;
      return;
    }

    switch (event) {
      case AvaSegmentBoundaryEvent():
        _acceptBoundary(event);
      case AvaCharacterTimingBatchEvent():
        _requireActiveSegment(event.segmentId, 'characterTimingBatch');
        if (event.chunkIndex != _nextChunkIndex ||
            _pendingTimingChunk != null) {
          throw const FormatException(
            'Character timing must precede its matching audio chunk once.',
          );
        }
        _pendingTimingChunk = event.chunkIndex;
      case AvaAudioChunkEvent():
        _requireActiveSegment(event.segmentId, 'audioChunk');
        if (event.chunkIndex != _nextChunkIndex) {
          throw FormatException(
            'Audio chunk index mismatch: expected $_nextChunkIndex, '
            'received ${event.chunkIndex}.',
          );
        }
        _pendingTimingChunk = null;
        _nextChunkIndex += 1;
        _audioChunkCount += 1;
        _currentSegmentHasAudio = true;
      case AvaDiagramCueEvent():
        _acceptDiagram(event);
      case AvaTurnCompletedEvent():
        _acceptCompleted(event);
      case AvaTurnAcceptedEvent() ||
            AvaPerformancePlanEvent() ||
            AvaAudioMetadataEvent():
        throw FormatException(
          'Duplicate ${event.event.name} performance event.',
        );
      case AvaTurnFailedEvent() || AvaTurnCancelledEvent():
        break;
    }
  }

  void _acceptBoundary(AvaSegmentBoundaryEvent event) {
    final plan = _plan!;
    if (_activeSegmentId != null &&
        (!_currentSegmentHasAudio || _pendingTimingChunk != null)) {
      throw const FormatException(
        'A segment cannot end before its timed audio is complete.',
      );
    }
    if (_nextSegmentIndex >= plan.segments.length ||
        event.segmentIndex != _nextSegmentIndex ||
        event.segmentId != plan.segments[_nextSegmentIndex].id) {
      throw const FormatException(
        'Segment boundaries must follow the validated performance plan.',
      );
    }
    _activeSegmentId = event.segmentId;
    _nextSegmentIndex += 1;
    _nextChunkIndex = 0;
    _pendingTimingChunk = null;
    _currentSegmentHasAudio = false;
  }

  void _acceptDiagram(AvaDiagramCueEvent event) {
    _requireActiveSegment(event.segmentId, 'diagramCue');
    final segment = _plan!.segments[_nextSegmentIndex - 1];
    if (_currentSegmentHasAudio ||
        _diagramCueSegments.contains(event.segmentId) ||
        segment.diagramCue == null ||
        jsonEncode(event.cue.toJson()) !=
            jsonEncode(segment.diagramCue!.toJson())) {
      throw const FormatException(
        'Diagram cue must match its performance segment before audio.',
      );
    }
    _diagramCueSegments.add(event.segmentId);
  }

  void _acceptCompleted(AvaTurnCompletedEvent event) {
    final plan = _plan!;
    if (_nextSegmentIndex != plan.segments.length ||
        !_currentSegmentHasAudio ||
        _pendingTimingChunk != null ||
        event.audioChunkCount != _audioChunkCount) {
      throw const FormatException(
        'turnCompleted does not match the delivered performance audio.',
      );
    }
    for (final segment in plan.segments) {
      if (segment.diagramCue != null &&
          !_diagramCueSegments.contains(segment.id)) {
        throw const FormatException(
          'turnCompleted omitted a required diagram cue.',
        );
      }
    }
  }

  void _requireActiveSegment(String segmentId, String context) {
    if (_activeSegmentId == null || segmentId != _activeSegmentId) {
      throw FormatException(
        '$context must target the active performance segment.',
      );
    }
  }
}
