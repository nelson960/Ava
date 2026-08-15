import 'dart:math';

import '../canvas/ava_canvas_engine.dart';
import '../models/ava_realtime_protocol.dart';
import 'ava_realtime_bootstrap_service.dart';
import 'ava_realtime_presentation.dart';
import 'ava_realtime_session.dart';
import 'ava_realtime_trace.dart';
import 'ava_realtime_turn_runtime.dart';
import 'ava_realtime_webrtc.dart';
import 'pcm_speech_output_service.dart';

/// Constructs the production protocol-v3 runtime without exposing either
/// provider credential to Flutter.
final class AvaRealtimeRuntimeFactory {
  AvaRealtimeRuntimeFactory({
    required AvaRealtimeBootstrapService bootstrapService,
    required AvaRealtimeMediaConnector mediaConnector,
    required AvaRealtimeTransportConnector transportConnector,
    required PcmSpeechOutputService speechOutput,
    required AvaCanvasEngine canvasEngine,
    required AvaRealtimePresentationController presentation,
    required String Function() cancelRequestIdFactory,
    AvaRealtimeTrace Function()? traceFactory,
  })  : _bootstrapService = bootstrapService,
        _mediaConnector = mediaConnector,
        _transportConnector = transportConnector,
        _speechOutput = speechOutput,
        _canvasEngine = canvasEngine,
        _presentation = presentation,
        _cancelRequestIdFactory = cancelRequestIdFactory,
        _traceFactory = traceFactory ??
            (() => AvaRealtimeTrace(
                  attemptId: createSecureAvaRealtimeAttemptId(),
                ));

  final AvaRealtimeBootstrapService _bootstrapService;
  final AvaRealtimeMediaConnector _mediaConnector;
  final AvaRealtimeTransportConnector _transportConnector;
  final PcmSpeechOutputService _speechOutput;
  final AvaCanvasEngine _canvasEngine;
  final AvaRealtimePresentationController _presentation;
  final String Function() _cancelRequestIdFactory;
  final AvaRealtimeTrace Function() _traceFactory;

  bool _closed = false;

  Future<AvaRealtimeTurnRuntime> create({
    required AvaRealtimeAgeBand ageBand,
    required double volume,
    void Function(bool enabled)? onMicrophoneTrackChanged,
  }) async {
    _requireOpen();
    if (volume < 0 || volume > 1) {
      throw RangeError.range(volume, 0, 1, 'volume');
    }
    final trace = _traceFactory()..mark('connection.requested');
    final bootstrap = await trace.measure(
      'bootstrap.create',
      () => _bootstrapService.createSession(ageBand: ageBand),
    );
    trace.attachSession(bootstrap.sessionId);
    final media = await trace.measure(
      'webrtc.connect',
      () => _mediaConnector(bootstrap),
    );
    AvaRealtimeTransport? transport;
    try {
      final connectedTransport = await trace.measure(
        'events.connect',
        () => _transportConnector(bootstrap),
      );
      transport = connectedTransport;
      trace.mark('runtime.ready');
      return AvaRealtimeTurnRuntime(
        sessionId: bootstrap.sessionId,
        limits: bootstrap.limits,
        mediaSession: media,
        transport: connectedTransport,
        speechOutput: _speechOutput,
        canvasEngine: _canvasEngine,
        presentation: _presentation,
        volume: volume,
        cancelRequestIdFactory: _cancelRequestIdFactory,
        trace: trace,
        onMicrophoneTrackChanged: onMicrophoneTrackChanged,
        microphoneInitiallyEnabled: false,
      );
    } catch (_) {
      if (transport != null) {
        await transport.close();
      }
      await media.close();
      rethrow;
    }
  }

  Future<void> close() async {
    if (_closed) return;
    _closed = true;
  }

  void _requireOpen() {
    if (_closed) {
      throw StateError('Ava realtime runtime factory is closed.');
    }
  }
}

String createSecureAvaCancelRequestId() {
  final random = Random.secure();
  final buffer = StringBuffer('cancel-');
  for (var index = 0; index < 16; index += 1) {
    buffer.write(random.nextInt(256).toRadixString(16).padLeft(2, '0'));
  }
  return buffer.toString();
}
