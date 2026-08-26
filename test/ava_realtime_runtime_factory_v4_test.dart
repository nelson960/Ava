import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_controller.dart';
import 'package:ava/canvas/ava_svg_canvas_session_host.dart';
import 'package:ava/config/ava_backend_configuration.dart';
import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_realtime_bootstrap_service.dart';
import 'package:ava/services/ava_realtime_presentation.dart';
import 'package:ava/services/ava_realtime_runtime_factory_v4.dart';
import 'package:ava/services/ava_realtime_session_v4.dart';
import 'package:ava/services/ava_realtime_trace.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/services/pcm_speech_output_service.dart';
import 'package:flutter_test/flutter_test.dart';

const _offerSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava protocol v4 offer\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

void main() {
  test('v4 factory owns separate canvas auth and attaches one session host',
      () async {
    final bootstrapTransport = _BootstrapTransport();
    final media = DeterministicAvaRealtimeMediaSession(offerSdp: _offerSdp);
    final raw = DeterministicAvaRealtimeRawTransportV4(const []);
    final loader = _Loader();
    final host = AvaSvgCanvasSessionHost();
    final traceEvents = <AvaRealtimeTraceEvent>[];
    final factory = AvaRealtimeRuntimeFactoryV4(
      bootstrapService: AvaRealtimeBootstrapService(
        configuration: AvaBackendConfiguration.parse(
          'https://ava.example.test',
        ),
        transport: bootstrapTransport,
      ),
      mediaConnector: (_) async => media,
      transportConnector: (_) async => raw,
      documentLoaderFactory: (bootstrap, telemetry) {
        expect(bootstrap.canvasToken, 'canvas-token-value');
        expect(telemetry, isNotNull);
        return loader;
      },
      canvasHost: host,
      speechOutput: const _NoopPcmOutput(),
      presentation: AvaRealtimePresentationController(),
      cancelRequestIdFactory: () => 'cancel-v4-factory',
      traceFactory: () => AvaRealtimeTrace(
        attemptId: 'attempt-v4-factory',
        observer: traceEvents.add,
      ),
    );
    addTearDown(factory.close);
    addTearDown(host.dispose);

    final runtime = await factory.create(
      ageBand: AvaRealtimeAgeBand.middleChildhood,
      volume: 0.8,
    );

    expect(bootstrapTransport.endpoint.path, '/v4/realtime/sessions');
    expect(bootstrapTransport.protocolHeader, '4');
    expect(host.controller, isNotNull);
    expect(media.microphoneEnabled, isFalse);
    host.controller!.receiveGenerationStarted({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': 'session-v4-factory',
      'sequence': 0,
      'revision': 1,
      'generationId': 'generation-v4-factory',
      'sourceTurnId': 'turn-v4-factory',
    });
    final canvasTrace = traceEvents.firstWhere(
      (event) => event.stage == 'canvas.generation.started_received',
    );
    expect(canvasTrace.sessionId, 'session-v4-factory');
    expect(canvasTrace.fields['generationId'], 'generation-v4-factory');
    expect(canvasTrace.fields['sourceTurnId'], 'turn-v4-factory');

    await runtime.close();
    expect(host.controller, isNull);
    expect(loader.closed, isTrue);
    expect(raw.closed, isTrue);
    expect(media.closed, isTrue);
  });

  test('runtime_factory_uses_application_workspace_lookup', () async {
    final retained = _canvasDocument(
      generationId: 'generation-retained',
      sourceTurnId: 'turn-retained',
      sceneRevision: 1,
    );
    final revised = _canvasDocument(
      generationId: 'generation-revised',
      sourceTurnId: 'turn-revised',
      sceneRevision: 2,
    );
    final host = AvaSvgCanvasSessionHost();
    final owner = AvaSvgCanvasController(
      documents: _Loader(retained),
      commandSink: (_) {},
    );
    host.attach(owner);
    await _activateCanvas(owner, retained);
    host.detach(owner);
    owner.dispose();

    final bootstrapTransport = _BootstrapTransport();
    final media = DeterministicAvaRealtimeMediaSession(offerSdp: _offerSdp);
    final raw = DeterministicAvaRealtimeRawTransportV4(const []);
    final factory = AvaRealtimeRuntimeFactoryV4(
      bootstrapService: AvaRealtimeBootstrapService(
        configuration: AvaBackendConfiguration.parse(
          'https://ava.example.test',
        ),
        transport: bootstrapTransport,
      ),
      mediaConnector: (_) async => media,
      transportConnector: (_) async => raw,
      documentLoaderFactory: (_, __) => _Loader(revised),
      canvasHost: host,
      speechOutput: const _NoopPcmOutput(),
      presentation: AvaRealtimePresentationController(),
      cancelRequestIdFactory: () => 'cancel-v4-factory-replacement',
    );
    addTearDown(factory.close);
    addTearDown(host.dispose);
    final runtime = await factory.create(
      ageBand: AvaRealtimeAgeBand.middleChildhood,
      volume: 0.8,
    );

    await _activateCanvas(runtime.canvasController, revised);

    expect(host.sceneCount, 1);
    expect(host.workspaceDocuments, orderedEquals([revised]));
    expect(host.presentedDocument, same(revised));
    await runtime.close();
  });
}

final class _BootstrapTransport implements AvaRealtimeBootstrapTransport {
  late Uri endpoint;
  late String protocolHeader;

  @override
  Future<AvaRealtimeBootstrapResponse> postJson({
    required Uri endpoint,
    required Map<String, String> headers,
    required String body,
    required Duration timeout,
  }) async {
    this.endpoint = endpoint;
    protocolHeader = headers['x-ava-protocol-version']!;
    expect((jsonDecode(body) as Map)['protocolVersion'], 4);
    final payload = {
      'protocolVersion': 4,
      'sessionId': 'session-v4-factory',
      'eventUrl':
          'wss://ava.example.test/v4/realtime/sessions/session-v4-factory/events',
      'webrtcUrl':
          'https://ava.example.test/v4/realtime/sessions/session-v4-factory/webrtc',
      'canvasDocumentBaseUrl':
          'https://ava.example.test/v4/realtime/sessions/session-v4-factory/canvas-documents',
      'sessionToken': 'session-token-value',
      'webrtcToken': 'webrtc-token-value',
      'canvasToken': 'canvas-token-value',
      'expiresAt': '2026-08-21T12:00:00Z',
      'rendererCapabilityVersion': 'ava-svg-renderer-v1',
      'validatorVersion': 'ava-svg-validator-v1',
      'manifestVersion': 'ava-svg-manifest-v1',
      'limits': {
        'maxTurnSeconds': 90,
        'maxResponseCharacters': 1800,
        'maxSvgBytes': 262144,
        'maxManifestBytes': 65536,
        'maxAudioChunkBytes': 192000,
        'playbackProgressIntervalMs': 250,
      },
    };
    return AvaRealtimeBootstrapResponse(
      statusCode: HttpStatus.created,
      contentType: 'application/json',
      protocolVersion: '4',
      bytes: Stream.value(utf8.encode(jsonEncode(payload))),
    );
  }

  @override
  void close() {}
}

final class _Loader implements AvaSvgDocumentLoader {
  _Loader([this.document]);

  final AvaValidatedSvgCanvasDocument? document;
  bool closed = false;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) async {
    final resolved = document;
    if (resolved == null ||
        resolved.descriptor.generationId != descriptor.generationId) {
      throw StateError('The factory test did not expect this canvas document.');
    }
    return resolved;
  }

  @override
  void close() => closed = true;
}

const _canvasManifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","action":"select","bindingId":"select-cloud","elementId":"cloud","exclusive":false,"hitTest":"element","pulseIds":[],"revealIds":[],"serverReportable":true,"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","manifestVersion":"ava-svg-manifest-v1","rendererCapabilityVersion":"ava-svg-renderer-v1","sourceTrust":"modelGeneratedData","title":"Café cloud ☁️","validatorVersion":"ava-svg-validator-v1","viewBox":[0.0,0.0,1000.0,600.0]}';

AvaValidatedSvgCanvasDocument _canvasDocument({
  required String generationId,
  required String sourceTurnId,
  required int sceneRevision,
}) {
  final fixture = (jsonDecode(
    File('protocol/fixtures/ava_realtime_canvas_events_v4.json')
        .readAsStringSync(),
  ) as List)
      .first as Map;
  final descriptor = AvaSvgCanvasDocumentDescriptorV4.fromJson({
    ...fixture.cast<String, Object?>(),
    'generationId': generationId,
    'sourceTurnId': sourceTurnId,
    'sceneId': 'scene-retained',
    'sceneRevision': sceneRevision,
  });
  return AvaValidatedSvgCanvasDocument.verify(
    descriptor: descriptor,
    svgBytes:
        File('protocol/fixtures/ava_svg_document_v4.svg').readAsBytesSync(),
    manifestBytes: Uint8List.fromList(utf8.encode(_canvasManifestJson)),
  );
}

Future<void> _activateCanvas(
  AvaSvgCanvasController controller,
  AvaValidatedSvgCanvasDocument document,
) async {
  final descriptor = document.descriptor;
  final events = (jsonDecode(
    File('protocol/fixtures/ava_realtime_canvas_events_v4.json')
        .readAsStringSync(),
  ) as List)
      .map((value) => (value as Map).cast<String, Object?>())
      .toList(growable: false);
  Map<String, Object?> withIdentity(Map<String, Object?> event) => {
        ...event,
        'generationId': descriptor.generationId,
        'sourceTurnId': descriptor.sourceTurnId,
        'sceneId': descriptor.sceneId,
        'sceneRevision': descriptor.sceneRevision,
      };
  controller.beginPlaybackClock(descriptor.sourceTurnId);
  controller.receiveGenerationStarted({
    'protocolVersion': 4,
    'event': 'canvasGenerationStarted',
    'sessionId': 'session-v4-factory',
    'sequence': 0,
    'revision': 1,
    'generationId': descriptor.generationId,
    'sourceTurnId': descriptor.sourceTurnId,
  });
  await controller.receiveDocumentAvailable(withIdentity(events[0]));
  controller.rendererPrepared();
  controller.receivePresentationCue(withIdentity(events[1]));
  controller.updateRenderedSample(
    turnId: descriptor.sourceTurnId,
    renderedSample: 4812,
  );
  controller.firstFramePresented();
  controller.receiveActivated(withIdentity(events[2]));
}

final class _NoopPcmOutput implements PcmSpeechOutputService {
  const _NoopPcmOutput();

  @override
  Future<void> append(
      {required String turnId, required Uint8List audio}) async {}

  @override
  Future<PcmStopResult> fadeOutAndStop({
    required String turnId,
    Duration fadeDuration = const Duration(milliseconds: 80),
  }) async =>
      const PcmStopResult(renderedSampleCount: 0);

  @override
  Future<PcmStopResult> finish({required String turnId}) async =>
      const PcmStopResult(renderedSampleCount: 0);

  @override
  Future<Duration> position({required String turnId}) async => Duration.zero;

  @override
  Future<PcmPlaybackProgress> progress({required String turnId}) async =>
      const PcmPlaybackProgress(
        renderedSampleCount: 0,
        scheduledSampleCount: 0,
        sampleRateHz: 24000,
      );

  @override
  Future<void> start({
    required String turnId,
    required int sampleRateHz,
    required double volume,
  }) async {}

  @override
  Future<void> stop() async {}
}
