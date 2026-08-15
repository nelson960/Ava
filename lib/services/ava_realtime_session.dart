import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:developer' as developer;
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../models/ava_realtime_protocol.dart';

sealed class AvaRealtimeInboundFrame {
  const AvaRealtimeInboundFrame();
}

final class AvaRealtimeControlFrame extends AvaRealtimeInboundFrame {
  const AvaRealtimeControlFrame(this.event);

  final AvaRealtimeServerEvent event;
}

final class AvaRealtimePcmFrame extends AvaRealtimeInboundFrame {
  AvaRealtimePcmFrame(Uint8List bytes)
      : bytes = Uint8List.fromList(bytes),
        super();

  final Uint8List bytes;
}

abstract interface class AvaRealtimeTransport {
  Stream<AvaRealtimeInboundFrame> frames();

  Future<void> send(AvaRealtimeClientEvent event);

  Future<void> close();
}

typedef AvaRealtimeTransportConnector = Future<AvaRealtimeTransport> Function(
  AvaSessionBootstrapV3 bootstrap,
);

final class IoAvaRealtimeTransport implements AvaRealtimeTransport {
  IoAvaRealtimeTransport._({
    required WebSocket socket,
    required HttpClient client,
    required this.sessionId,
    required this.maxAudioChunkBytes,
  })  : _socket = socket,
        _client = client;

  static const int maxControlFrameBytes = 1024 * 1024;

  final WebSocket _socket;
  final HttpClient _client;
  final String sessionId;
  final int maxAudioChunkBytes;
  bool _framesConsumed = false;
  bool _closed = false;

  static Future<IoAvaRealtimeTransport> connect(
    AvaSessionBootstrapV3 bootstrap, {
    Duration pingInterval = const Duration(seconds: 15),
    Duration connectionTimeout = const Duration(seconds: 15),
  }) async {
    if (pingInterval < const Duration(seconds: 5) ||
        pingInterval > const Duration(seconds: 60)) {
      throw RangeError.range(
        pingInterval.inSeconds,
        5,
        60,
        'pingInterval',
      );
    }
    if (connectionTimeout < const Duration(seconds: 3) ||
        connectionTimeout > const Duration(seconds: 30)) {
      throw RangeError.range(
        connectionTimeout.inSeconds,
        3,
        30,
        'connectionTimeout',
      );
    }
    final client = HttpClient()..connectionTimeout = connectionTimeout;
    final WebSocket socket;
    try {
      socket = await WebSocket.connect(
        bootstrap.eventUrl,
        headers: {
          HttpHeaders.authorizationHeader: 'Bearer ${bootstrap.sessionToken}',
          'X-Ava-Protocol-Version': '$avaRealtimeProtocolVersion',
        },
        compression: CompressionOptions.compressionOff,
        customClient: client,
      );
    } catch (_) {
      client.close(force: true);
      rethrow;
    }
    socket.pingInterval = pingInterval;
    return IoAvaRealtimeTransport._(
      socket: socket,
      client: client,
      sessionId: bootstrap.sessionId,
      maxAudioChunkBytes: bootstrap.limits.maxAudioChunkBytes,
    );
  }

  @override
  Stream<AvaRealtimeInboundFrame> frames() async* {
    if (_framesConsumed) {
      throw StateError(
        'Ava realtime transport frames may be consumed only once.',
      );
    }
    if (_closed) {
      throw StateError('Ava realtime transport is closed.');
    }
    _framesConsumed = true;
    var endedByPeer = false;
    try {
      await for (final message in _socket) {
        if (message is String) {
          final length = utf8.encode(message).length;
          if (length > maxControlFrameBytes) {
            throw AvaRealtimeBufferOverflow(
              'Realtime control frame exceeds $maxControlFrameBytes bytes.',
            );
          }
          final decoded = jsonDecode(message);
          if (decoded is! Map) {
            throw const FormatException(
              'Realtime control frame must be a JSON object.',
            );
          }
          final event = _parseServerControlEvent(
            decoded.cast<String, Object?>(),
          );
          if (event.sessionId != sessionId) {
            throw const AvaRealtimeStateException(
              'Realtime control frame sessionId does not match bootstrap.',
            );
          }
          yield AvaRealtimeControlFrame(event);
          continue;
        }
        if (message is List<int>) {
          if (message.isEmpty || message.length > maxAudioChunkBytes) {
            throw AvaRealtimeBufferOverflow(
              'Realtime binary PCM frame has invalid length ${message.length}.',
            );
          }
          yield AvaRealtimePcmFrame(Uint8List.fromList(message));
          continue;
        }
        throw FormatException(
          'Unsupported realtime WebSocket frame ${message.runtimeType}.',
        );
      }
      if (_closed) return;
      endedByPeer = true;
      final closeCode = _socket.closeCode;
      final closeReason = _socket.closeReason;
      if (closeCode == WebSocketStatus.normalClosure) {
        return;
      }
      throw AvaRealtimeStateException(
        'Realtime event socket closed unexpectedly '
        '(code=${closeCode ?? 'unknown'}, '
        'reason=${closeReason?.trim().isNotEmpty == true ? closeReason : 'none'}).',
      );
    } finally {
      _closed = true;
      try {
        if (!endedByPeer && _socket.readyState != WebSocket.closed) {
          await _socket.close(
            WebSocketStatus.normalClosure,
            'Ava frame stream ended',
          );
        }
      } finally {
        _client.close(force: true);
      }
    }
  }

  @override
  Future<void> send(AvaRealtimeClientEvent event) async {
    if (_closed) {
      throw StateError('Ava realtime transport is closed.');
    }
    if (event.sessionId != sessionId) {
      throw const AvaRealtimeStateException(
        'Client control sessionId does not match transport.',
      );
    }
    _socket.add(jsonEncode(event.toJson()));
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    try {
      await _socket.close(WebSocketStatus.normalClosure, 'Ava session closed');
    } finally {
      _client.close(force: true);
    }
  }
}

AvaRealtimeServerEvent _parseServerControlEvent(
  Map<String, Object?> payload,
) {
  try {
    return AvaRealtimeServerEvent.fromJson(payload);
  } on AvaRealtimeCanvasPayloadException catch (error) {
    final event = error.rejectionEvent;
    developer.log(
      'canvas_payload_rejected session=${event.sessionId} '
      'turn=${event.turnId} sequence=${event.sequence} '
      'failure_id=${event.failure?.failureId} '
      'component=${event.failure?.component} '
      'detail=${error.detail}',
      name: 'ava.realtime.canvas',
    );
    if (kDebugMode) {
      debugPrint(
        'AVA_CANVAS_PAYLOAD_REJECTED session=${event.sessionId} '
        'turn=${event.turnId} sequence=${event.sequence} '
        'failure_id=${event.failure?.failureId} '
        'component=${event.failure?.component} '
        'detail=${error.detail}',
      );
    }
    return event;
  }
}

/// Explicit keyless seam for protocol and presentation tests.
///
/// This transport never activates automatically and is not a release
/// fallback. Callers must inject every server frame they expect to receive.
final class DeterministicAvaRealtimeTransport implements AvaRealtimeTransport {
  DeterministicAvaRealtimeTransport(Iterable<AvaRealtimeInboundFrame> frames)
      : _frames = List.unmodifiable(frames);

  final List<AvaRealtimeInboundFrame> _frames;
  final List<AvaRealtimeClientEvent> sentEvents = [];
  bool _consumed = false;
  bool _closed = false;

  bool get isClosed => _closed;

  @override
  Stream<AvaRealtimeInboundFrame> frames() async* {
    if (_consumed) {
      throw StateError(
        'Deterministic realtime frames may be consumed only once.',
      );
    }
    if (_closed) {
      throw StateError('Deterministic realtime transport is closed.');
    }
    _consumed = true;
    for (final frame in _frames) {
      if (_closed) return;
      yield frame;
      await Future<void>.delayed(Duration.zero);
    }
  }

  @override
  Future<void> send(AvaRealtimeClientEvent event) async {
    if (_closed) {
      throw StateError('Deterministic realtime transport is closed.');
    }
    sentEvents.add(event);
  }

  @override
  Future<void> close() async {
    _closed = true;
  }
}

final class AvaRealtimeStateException implements Exception {
  const AvaRealtimeStateException(this.message);

  final String message;

  @override
  String toString() => 'AvaRealtimeStateException: $message';
}

final class AvaRealtimeTurnSnapshot {
  const AvaRealtimeTurnSnapshot({
    required this.sessionId,
    required this.turnId,
    required this.phase,
    required this.revision,
    required this.nextSequence,
    required this.segmentCount,
    required this.receivedSampleCount,
    required this.terminal,
  });

  final String? sessionId;
  final String? turnId;
  final AvaRealtimeTurnPhase? phase;
  final int revision;
  final int nextSequence;
  final int segmentCount;
  final int receivedSampleCount;
  final bool terminal;
}

final class AvaRealtimeTurnStateMachine {
  String? _sessionId;
  String? _turnId;
  AvaRealtimeTurnPhase? _phase;
  int _revision = -1;
  int _nextSequence = 0;
  int _nextSegmentIndex = 0;
  String? _audioStreamId;
  int _nextAudioChunkIndex = 0;
  int _receivedSampleCount = 0;
  final Set<String> _cueIds = {};
  String? _cancelRequestId;
  bool _terminal = false;

  AvaRealtimeTurnSnapshot get snapshot => AvaRealtimeTurnSnapshot(
        sessionId: _sessionId,
        turnId: _turnId,
        phase: _phase,
        revision: _revision,
        nextSequence: _nextSequence,
        segmentCount: _nextSegmentIndex,
        receivedSampleCount: _receivedSampleCount,
        terminal: _terminal,
      );

  AvaRealtimeTurnSnapshot accept(AvaRealtimeServerEvent event) {
    if (event.type == AvaRealtimeServerEventType.interactionRejected) {
      return _acceptSessionInteractionRejection(event);
    }
    if (_terminal) {
      throw const AvaRealtimeStateException(
        'Event received after terminal turn state.',
      );
    }
    if (event.sequence != _nextSequence) {
      throw AvaRealtimeStateException(
        'Expected event sequence $_nextSequence, received ${event.sequence}.',
      );
    }

    if (_sessionId == null) {
      if (event.type != AvaRealtimeServerEventType.turnCreated) {
        throw const AvaRealtimeStateException(
          'turnCreated must be the first event.',
        );
      }
      if (event.revision != 0) {
        throw const AvaRealtimeStateException(
          'turnCreated revision must be zero.',
        );
      }
      _sessionId = event.sessionId;
      _turnId = event.turnId;
      _phase = AvaRealtimeTurnPhase.created;
      _revision = 0;
      _nextSequence += 1;
      return snapshot;
    }

    if (event.sessionId != _sessionId || event.turnId != _turnId) {
      throw const AvaRealtimeStateException(
        'sessionId or turnId changed mid-turn.',
      );
    }

    switch (event.type) {
      case AvaRealtimeServerEventType.turnPhaseChanged:
        _acceptPhaseChange(event);
        break;
      case AvaRealtimeServerEventType.turnCompleted:
        _acceptCompleted(event);
        break;
      case AvaRealtimeServerEventType.turnCancelled:
        _acceptCancelled(event);
        break;
      case AvaRealtimeServerEventType.turnFailed:
        _acceptFailed(event);
        break;
      case AvaRealtimeServerEventType.turnCreated:
        throw const AvaRealtimeStateException(
          'turnCreated cannot repeat.',
        );
      case AvaRealtimeServerEventType.segmentReady:
      case AvaRealtimeServerEventType.audioMetadata:
      case AvaRealtimeServerEventType.audioChunk:
      case AvaRealtimeServerEventType.alignmentBatch:
      case AvaRealtimeServerEventType.canvasCue:
      case AvaRealtimeServerEventType.canvasRejected:
      case AvaRealtimeServerEventType.memoryPersistenceFailed:
        if (event.revision != _revision) {
          throw AvaRealtimeStateException(
            'Event revision ${event.revision} does not match active revision '
            '$_revision.',
          );
        }
        _acceptNonPhaseEvent(event);
        break;
      case AvaRealtimeServerEventType.interactionRejected:
        throw const AvaRealtimeStateException(
          'interactionRejected must use session-scoped validation.',
        );
    }
    _nextSequence += 1;
    return snapshot;
  }

  AvaRealtimeTurnSnapshot _acceptSessionInteractionRejection(
    AvaRealtimeServerEvent event,
  ) {
    if (_sessionId == null || _turnId == null) {
      throw const AvaRealtimeStateException(
        'interactionRejected cannot precede turnCreated.',
      );
    }
    if (event.sequence != _nextSequence) {
      throw AvaRealtimeStateException(
        'Expected event sequence $_nextSequence, received ${event.sequence}.',
      );
    }
    if (event.sessionId != _sessionId || event.turnId != _turnId) {
      throw const AvaRealtimeStateException(
        'sessionId or turnId changed mid-turn.',
      );
    }
    if (event.revision != _revision) {
      throw const AvaRealtimeStateException(
        'interactionRejected revision does not match active revision.',
      );
    }
    _nextSequence += 1;
    return snapshot;
  }

  bool registerCancellation(String requestId) {
    if (requestId.isEmpty) {
      throw const AvaRealtimeStateException(
        'Cancel requestId cannot be empty.',
      );
    }
    final registered = _cancelRequestId;
    if (registered != null) {
      if (registered == requestId) return false;
      throw const AvaRealtimeStateException(
        'A different cancellation request is already registered.',
      );
    }
    if (_terminal) {
      throw const AvaRealtimeStateException(
        'Cannot cancel a terminal turn.',
      );
    }
    _requirePhase();
    _cancelRequestId = requestId;
    return true;
  }

  void _acceptPhaseChange(AvaRealtimeServerEvent event) {
    final current = _requirePhase();
    final next = event.phase;
    if (next == null || !_allowedNextPhases(current).contains(next)) {
      throw AvaRealtimeStateException(
        'Invalid turn phase transition $current -> $next.',
      );
    }
    if (event.revision != _revision + 1) {
      throw const AvaRealtimeStateException(
        'Phase changes must increment revision by one.',
      );
    }
    if (next == AvaRealtimeTurnPhase.speaking && _audioStreamId == null) {
      throw const AvaRealtimeStateException(
        'Speaking requires prior audioMetadata.',
      );
    }
    _phase = next;
    _revision = event.revision;
  }

  void _acceptNonPhaseEvent(AvaRealtimeServerEvent event) {
    final phase = _requirePhase();
    switch (event.type) {
      case AvaRealtimeServerEventType.segmentReady:
        if (phase != AvaRealtimeTurnPhase.planning &&
            phase != AvaRealtimeTurnPhase.synthesizing) {
          throw const AvaRealtimeStateException(
            'segmentReady requires planning or synthesizing.',
          );
        }
        if (event.segmentIndex != _nextSegmentIndex) {
          throw AvaRealtimeStateException(
            'Expected segmentIndex $_nextSegmentIndex, '
            'received ${event.segmentIndex}.',
          );
        }
        _nextSegmentIndex += 1;
        break;
      case AvaRealtimeServerEventType.audioMetadata:
        if (phase != AvaRealtimeTurnPhase.synthesizing) {
          throw const AvaRealtimeStateException(
            'audioMetadata requires synthesizing.',
          );
        }
        if (_audioStreamId != null) {
          throw const AvaRealtimeStateException(
            'audioMetadata may occur only once.',
          );
        }
        _audioStreamId = event.streamId;
        break;
      case AvaRealtimeServerEventType.audioChunk:
        if (phase != AvaRealtimeTurnPhase.synthesizing) {
          throw const AvaRealtimeStateException(
            'audioChunk requires synthesizing.',
          );
        }
        _requireAudioStream(event.streamId);
        if (event.chunkIndex != _nextAudioChunkIndex) {
          throw AvaRealtimeStateException(
            'Expected audio chunk $_nextAudioChunkIndex, '
            'received ${event.chunkIndex}.',
          );
        }
        if (event.sampleOffset != _receivedSampleCount) {
          throw AvaRealtimeStateException(
            'Expected sampleOffset $_receivedSampleCount, '
            'received ${event.sampleOffset}.',
          );
        }
        _nextAudioChunkIndex += 1;
        _receivedSampleCount += event.sampleCount!;
        break;
      case AvaRealtimeServerEventType.alignmentBatch:
        if (phase != AvaRealtimeTurnPhase.synthesizing) {
          throw const AvaRealtimeStateException(
            'alignmentBatch requires synthesizing.',
          );
        }
        _requireAudioStream(event.streamId);
        break;
      case AvaRealtimeServerEventType.canvasCue:
        if (phase != AvaRealtimeTurnPhase.synthesizing &&
            phase != AvaRealtimeTurnPhase.speaking) {
          throw const AvaRealtimeStateException(
            'canvasCue requires synthesizing or speaking.',
          );
        }
        if (!_cueIds.add(event.cueId!)) {
          throw const AvaRealtimeStateException(
            'Canvas cue ids must not repeat.',
          );
        }
        if (event.sampleOffset! > _receivedSampleCount) {
          throw const AvaRealtimeStateException(
            'Canvas cue sampleOffset cannot exceed received PCM.',
          );
        }
        break;
      case AvaRealtimeServerEventType.canvasRejected:
        if (phase != AvaRealtimeTurnPhase.synthesizing &&
            phase != AvaRealtimeTurnPhase.speaking) {
          throw const AvaRealtimeStateException(
            'canvasRejected requires synthesizing or speaking.',
          );
        }
        break;
      case AvaRealtimeServerEventType.interactionRejected:
        throw const AvaRealtimeStateException(
          'interactionRejected is not a turn-phase event.',
        );
      case AvaRealtimeServerEventType.memoryPersistenceFailed:
        if (phase != AvaRealtimeTurnPhase.listening &&
            phase != AvaRealtimeTurnPhase.speaking) {
          throw const AvaRealtimeStateException(
            'memoryPersistenceFailed requires listening or speaking.',
          );
        }
        break;
      case AvaRealtimeServerEventType.turnCreated:
      case AvaRealtimeServerEventType.turnPhaseChanged:
      case AvaRealtimeServerEventType.turnCompleted:
      case AvaRealtimeServerEventType.turnCancelled:
      case AvaRealtimeServerEventType.turnFailed:
        throw AvaRealtimeStateException(
          'Unsupported non-phase event ${event.type.name}.',
        );
    }
  }

  void _acceptCompleted(AvaRealtimeServerEvent event) {
    if (_requirePhase() != AvaRealtimeTurnPhase.speaking) {
      throw const AvaRealtimeStateException(
        'turnCompleted requires speaking.',
      );
    }
    _acceptTerminalRevision(event.revision);
    _validateDeliveredSamples(event.deliveredSampleCount!);
    if (_nextAudioChunkIndex == 0) {
      throw const AvaRealtimeStateException(
        'turnCompleted requires received audio.',
      );
    }
    _phase = AvaRealtimeTurnPhase.completed;
    _terminal = true;
  }

  void _acceptCancelled(AvaRealtimeServerEvent event) {
    if (_requirePhase() != AvaRealtimeTurnPhase.cancelling) {
      throw const AvaRealtimeStateException(
        'turnCancelled requires cancelling.',
      );
    }
    _acceptTerminalRevision(event.revision);
    _validateDeliveredSamples(event.deliveredSampleCount!);
    _phase = AvaRealtimeTurnPhase.cancelled;
    _terminal = true;
  }

  void _acceptFailed(AvaRealtimeServerEvent event) {
    _acceptTerminalRevision(event.revision);
    _validateDeliveredSamples(event.deliveredSampleCount!);
    _phase = AvaRealtimeTurnPhase.failed;
    _terminal = true;
  }

  void _acceptTerminalRevision(int revision) {
    if (revision != _revision + 1) {
      throw const AvaRealtimeStateException(
        'Terminal events must increment revision by one.',
      );
    }
    _revision = revision;
  }

  void _validateDeliveredSamples(int samples) {
    if (samples > _receivedSampleCount) {
      throw const AvaRealtimeStateException(
        'Delivered samples cannot exceed received samples.',
      );
    }
  }

  AvaRealtimeTurnPhase _requirePhase() {
    final phase = _phase;
    if (phase == null) {
      throw const AvaRealtimeStateException(
        'Turn state has not been created.',
      );
    }
    return phase;
  }

  void _requireAudioStream(String? streamId) {
    if (_audioStreamId == null) {
      throw const AvaRealtimeStateException(
        'Audio event requires audioMetadata.',
      );
    }
    if (streamId != _audioStreamId) {
      throw const AvaRealtimeStateException(
        'Audio streamId changed mid-turn.',
      );
    }
  }
}

Set<AvaRealtimeTurnPhase> _allowedNextPhases(
  AvaRealtimeTurnPhase phase,
) =>
    switch (phase) {
      AvaRealtimeTurnPhase.created => const {
          AvaRealtimeTurnPhase.connecting,
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.connecting => const {
          AvaRealtimeTurnPhase.listening,
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.listening => const {
          AvaRealtimeTurnPhase.planning,
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.planning => const {
          AvaRealtimeTurnPhase.synthesizing,
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.synthesizing => const {
          AvaRealtimeTurnPhase.speaking,
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.speaking => const {
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.cancelling => const {
          AvaRealtimeTurnPhase.cancelled,
        },
      AvaRealtimeTurnPhase.completed ||
      AvaRealtimeTurnPhase.cancelled ||
      AvaRealtimeTurnPhase.failed =>
        const {},
    };

final class AvaRealtimeBufferOverflow implements Exception {
  const AvaRealtimeBufferOverflow(this.message);

  final String message;

  @override
  String toString() => 'AvaRealtimeBufferOverflow: $message';
}

final class AvaRealtimeBufferClosed implements Exception {
  const AvaRealtimeBufferClosed();

  @override
  String toString() => 'AvaRealtimeBufferClosed';
}

final class BoundedRealtimeBuffer<T> {
  BoundedRealtimeBuffer({
    required this.maxItems,
    required this.maxWeight,
    required int Function(T value) weigh,
  }) : _weigh = weigh {
    if (maxItems <= 0 || maxWeight <= 0) {
      throw ArgumentError('Realtime buffer limits must be positive.');
    }
  }

  final int maxItems;
  final int maxWeight;
  final int Function(T value) _weigh;
  final ListQueue<(T, int)> _items = ListQueue();
  int _weight = 0;
  bool _closed = false;

  int get length => _items.length;
  int get weight => _weight;
  bool get isClosed => _closed;

  void add(T value) {
    if (_closed) throw const AvaRealtimeBufferClosed();
    final weight = _weigh(value);
    if (weight <= 0) {
      throw ArgumentError.value(weight, 'weight', 'must be positive');
    }
    if (_items.length >= maxItems || _weight + weight > maxWeight) {
      throw AvaRealtimeBufferOverflow(
        'Realtime buffer capacity exceeded '
        '(items=${_items.length}, weight=$_weight, incoming=$weight).',
      );
    }
    _items.addLast((value, weight));
    _weight += weight;
  }

  T removeFirst() {
    if (_items.isEmpty) {
      if (_closed) throw const AvaRealtimeBufferClosed();
      throw StateError('Realtime buffer is empty.');
    }
    final (value, weight) = _items.removeFirst();
    _weight -= weight;
    return value;
  }

  void close() {
    _closed = true;
  }
}
