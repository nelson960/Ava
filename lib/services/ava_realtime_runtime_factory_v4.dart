import '../canvas/ava_svg_canvas_telemetry.dart';
import '../canvas/ava_svg_canvas_session_host.dart';
import '../models/ava_realtime_protocol.dart';
import '../models/ava_realtime_protocol_v4.dart';
import 'ava_realtime_bootstrap_service.dart';
import 'ava_canvas_restore_service.dart';
import 'ava_realtime_presentation.dart';
import 'ava_realtime_runtime_factory.dart';
import 'ava_realtime_session_v4.dart';
import 'ava_realtime_trace.dart';
import 'ava_realtime_turn_runtime.dart';
import 'ava_realtime_webrtc.dart';
import 'ava_svg_document_service.dart';
import 'pcm_speech_output_service.dart';

typedef AvaSvgDocumentLoaderFactory = AvaSvgDocumentLoader Function(
  AvaSessionBootstrapV4 bootstrap,
  AvaSvgCanvasTelemetrySink telemetry,
);

/// Constructs the protocol-v4 conversation while reusing the proven speech,
/// interruption, native PCM, caption, viseme, and microphone runtime.
final class AvaRealtimeRuntimeFactoryV4 implements AvaRealtimeRuntimeBuilder {
  AvaRealtimeRuntimeFactoryV4({
    required AvaRealtimeBootstrapService bootstrapService,
    required AvaCanvasRestoreService canvasRestoreService,
    required AvaRealtimeMediaConnectorV4 mediaConnector,
    required AvaRealtimeRawTransportConnectorV4 transportConnector,
    required AvaSvgDocumentLoaderFactory documentLoaderFactory,
    required AvaSvgCanvasSessionHost canvasHost,
    required PcmSpeechOutputService speechOutput,
    required AvaRealtimePresentationController presentation,
    required String Function() cancelRequestIdFactory,
    AvaRealtimeTrace Function()? traceFactory,
  })  : _bootstrapService = bootstrapService,
        _canvasRestoreService = canvasRestoreService,
        _mediaConnector = mediaConnector,
        _transportConnector = transportConnector,
        _documentLoaderFactory = documentLoaderFactory,
        _canvasHost = canvasHost,
        _speechOutput = speechOutput,
        _presentation = presentation,
        _cancelRequestIdFactory = cancelRequestIdFactory,
        _traceFactory = traceFactory ??
            (() => AvaRealtimeTrace(
                  attemptId: createSecureAvaRealtimeAttemptId(),
                ));

  final AvaRealtimeBootstrapService _bootstrapService;
  final AvaCanvasRestoreService _canvasRestoreService;
  final AvaRealtimeMediaConnectorV4 _mediaConnector;
  final AvaRealtimeRawTransportConnectorV4 _transportConnector;
  final AvaSvgDocumentLoaderFactory _documentLoaderFactory;
  final AvaSvgCanvasSessionHost _canvasHost;
  final PcmSpeechOutputService _speechOutput;
  final AvaRealtimePresentationController _presentation;
  final String Function() _cancelRequestIdFactory;
  final AvaRealtimeTrace Function() _traceFactory;
  bool _closed = false;

  @override
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
    await trace.measure('canvas.restore.local', () => _canvasHost.restoration);
    final canvasRenderProfile = _canvasHost.requireRenderProfile();
    final bootstrap = await trace.measure(
      'bootstrap.create',
      () => _bootstrapService.createSessionV4(
        ageBand: ageBand,
        canvasRenderProfile: canvasRenderProfile,
      ),
    );
    trace.attachSession(bootstrap.sessionId);
    final restoredFocused = _canvasHost.presentedDocument;
    if (restoredFocused != null) {
      await trace.measure(
        'canvas.restore.backend',
        () => _canvasRestoreService.restoreFocused(
          bootstrap: bootstrap,
          document: restoredFocused,
        ),
      );
    }
    void canvasTelemetry(AvaSvgCanvasTelemetryEvent event) {
      trace.mark(
        event.stage,
        stageDuration: event.stageDuration,
        fields: event.fields,
      );
    }

    final media = await trace.measure(
      'webrtc.connect',
      () => _mediaConnector(bootstrap),
    );
    AvaRealtimeRawTransportV4? raw;
    AvaSvgDocumentLoader? documents;
    try {
      final connectedRaw = await trace.measure(
        'events.connect',
        () => _transportConnector(bootstrap),
      );
      raw = connectedRaw;
      documents = _documentLoaderFactory(bootstrap, canvasTelemetry);
      final runtime = AvaRealtimeTurnRuntime(
        sessionId: bootstrap.sessionId,
        limits: bootstrap.limits,
        mediaSession: media,
        transport: connectedRaw,
        documents: documents,
        speechOutput: _speechOutput,
        presentation: _presentation,
        volume: volume,
        cancelRequestIdFactory: _cancelRequestIdFactory,
        canvasTelemetry: canvasTelemetry,
        retainedSceneRevision: _canvasHost.retainedSceneRevision,
        onCanvasClosed: _canvasHost.detach,
        trace: trace,
        onMicrophoneTrackChanged: onMicrophoneTrackChanged,
        microphoneInitiallyEnabled: false,
      );
      _canvasHost.attach(
        runtime.canvasController,
        initialRenderProfile: canvasRenderProfile,
        renderProfileUpdateSink: runtime.updateCanvasRenderProfile,
      );
      trace.mark('runtime.ready');
      return runtime;
    } catch (_) {
      documents?.close();
      if (raw != null) await raw.close();
      await media.close();
      rethrow;
    }
  }

  @override
  Future<void> close() async => _closed = true;

  void _requireOpen() {
    if (_closed) {
      throw StateError('Ava protocol-v4 runtime factory is closed.');
    }
  }
}
