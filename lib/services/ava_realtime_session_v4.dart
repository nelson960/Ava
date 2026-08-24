import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import '../models/ava_realtime_protocol_v4.dart';
import 'ava_realtime_errors.dart';

export 'ava_realtime_errors.dart';

sealed class AvaRealtimeInboundFrameV4 {
  const AvaRealtimeInboundFrameV4();
}

final class AvaRealtimeControlFrameV4 extends AvaRealtimeInboundFrameV4 {
  const AvaRealtimeControlFrameV4(this.event);

  final AvaRealtimeServerEventV4 event;
}

final class AvaRealtimePcmFrameV4 extends AvaRealtimeInboundFrameV4 {
  AvaRealtimePcmFrameV4(Uint8List bytes)
      : bytes = Uint8List.fromList(bytes),
        super();

  final Uint8List bytes;
}

abstract interface class AvaRealtimeRawTransportV4 {
  Stream<AvaRealtimeInboundFrameV4> frames();

  Future<void> send(AvaRealtimeClientEventV4 event);

  Future<void> close();
}

typedef AvaRealtimeRawTransportConnectorV4 = Future<AvaRealtimeRawTransportV4>
    Function(
  AvaSessionBootstrapV4 bootstrap,
);

final class IoAvaRealtimeRawTransportV4 implements AvaRealtimeRawTransportV4 {
  IoAvaRealtimeRawTransportV4._({
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

  static Future<IoAvaRealtimeRawTransportV4> connect(
    AvaSessionBootstrapV4 bootstrap, {
    Duration pingInterval = const Duration(seconds: 15),
    Duration connectionTimeout = const Duration(seconds: 15),
  }) async {
    if (pingInterval < const Duration(seconds: 5) ||
        pingInterval > const Duration(seconds: 60)) {
      throw RangeError.range(pingInterval.inSeconds, 5, 60, 'pingInterval');
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
          'X-Ava-Protocol-Version': '$avaRealtimeProtocolVersionV4',
        },
        compression: CompressionOptions.compressionOff,
        customClient: client,
      );
    } catch (_) {
      client.close(force: true);
      rethrow;
    }
    socket.pingInterval = pingInterval;
    return IoAvaRealtimeRawTransportV4._(
      socket: socket,
      client: client,
      sessionId: bootstrap.sessionId,
      maxAudioChunkBytes: bootstrap.limits.maxAudioChunkBytes,
    );
  }

  @override
  Stream<AvaRealtimeInboundFrameV4> frames() async* {
    if (_framesConsumed) {
      throw StateError('Protocol-v4 frames may be consumed only once.');
    }
    if (_closed) throw StateError('Protocol-v4 transport is closed.');
    _framesConsumed = true;
    var endedByPeer = false;
    try {
      await for (final message in _socket) {
        if (message is String) {
          final length = utf8.encode(message).length;
          if (length > maxControlFrameBytes) {
            throw AvaRealtimeBufferOverflow(
              'Protocol-v4 control frame exceeds $maxControlFrameBytes bytes.',
            );
          }
          final decoded = jsonDecode(message);
          if (decoded is! Map) {
            throw const FormatException(
              'Protocol-v4 control frame must be a JSON object.',
            );
          }
          final event = AvaRealtimeServerEventV4.fromJson(
            decoded.cast<String, Object?>(),
          );
          if (event.sessionId != sessionId) {
            throw const AvaRealtimeStateException(
              'Protocol-v4 control sessionId does not match bootstrap.',
            );
          }
          yield AvaRealtimeControlFrameV4(event);
          continue;
        }
        if (message is List<int>) {
          if (message.isEmpty || message.length > maxAudioChunkBytes) {
            throw AvaRealtimeBufferOverflow(
              'Protocol-v4 binary PCM frame has invalid length '
              '${message.length}.',
            );
          }
          yield AvaRealtimePcmFrameV4(Uint8List.fromList(message));
          continue;
        }
        throw FormatException(
          'Unsupported protocol-v4 WebSocket frame ${message.runtimeType}.',
        );
      }
      if (_closed) return;
      endedByPeer = true;
      if (_socket.closeCode == WebSocketStatus.normalClosure) return;
      throw AvaRealtimeStateException(
        'Protocol-v4 event socket closed unexpectedly '
        '(code=${_socket.closeCode ?? 'unknown'}, '
        'reason=${_socket.closeReason ?? 'none'}).',
      );
    } finally {
      _closed = true;
      try {
        if (!endedByPeer && _socket.readyState != WebSocket.closed) {
          await _socket.close(
            WebSocketStatus.normalClosure,
            'Ava protocol-v4 frame stream ended',
          );
        }
      } finally {
        _client.close(force: true);
      }
    }
  }

  @override
  Future<void> send(AvaRealtimeClientEventV4 event) async {
    if (_closed) throw StateError('Protocol-v4 transport is closed.');
    final json = event.toJson();
    if (json['sessionId'] != sessionId) {
      throw const AvaRealtimeStateException(
        'Protocol-v4 client control changed sessionId.',
      );
    }
    _socket.add(jsonEncode(json));
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    try {
      await _socket.close(
        WebSocketStatus.normalClosure,
        'Ava protocol-v4 session closed',
      );
    } finally {
      _client.close(force: true);
    }
  }
}

/// Explicit keyless protocol-v4 seam for deterministic transport tests.
final class DeterministicAvaRealtimeRawTransportV4
    implements AvaRealtimeRawTransportV4 {
  DeterministicAvaRealtimeRawTransportV4(
    Iterable<AvaRealtimeInboundFrameV4> frames,
  ) : _frames = List.unmodifiable(frames);

  final List<AvaRealtimeInboundFrameV4> _frames;
  final List<AvaRealtimeClientEventV4> sentEvents = [];
  bool _consumed = false;
  bool closed = false;

  @override
  Stream<AvaRealtimeInboundFrameV4> frames() async* {
    if (_consumed) {
      throw StateError('Deterministic v4 frames already consumed.');
    }
    _consumed = true;
    for (final frame in _frames) {
      if (closed) return;
      yield frame;
      await Future<void>.delayed(Duration.zero);
    }
  }

  @override
  Future<void> send(AvaRealtimeClientEventV4 event) async {
    if (closed) throw StateError('Deterministic v4 transport is closed.');
    sentEvents.add(event);
  }

  @override
  Future<void> close() async => closed = true;
}
