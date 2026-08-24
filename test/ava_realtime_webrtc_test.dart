import 'dart:convert';
import 'dart:io';

import 'package:ava/models/ava_realtime_protocol_v4.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:flutter_test/flutter_test.dart';

const _offerSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava deterministic offer\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

const _answerSdp = 'v=0\r\n'
    'o=- 2000 2 IN IP4 127.0.0.1\r\n'
    's=Ava deterministic answer\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

void main() {
  test('Apple microphone processing is requested in both supported shapes', () {
    final audio =
        avaRealtimeMicrophoneConstraints['audio']! as Map<String, Object>;
    expect(audio['echoCancellation'], isTrue);
    expect(audio['noiseSuppression'], isTrue);
    expect(audio['autoGainControl'], isTrue);
    expect(audio['optional'], [
      {'googEchoCancellation': true},
      {'googNoiseSuppression': true},
      {'googAutoGainControl': true},
    ]);
    expect(avaRealtimeMicrophoneConstraints['video'], isFalse);
  });

  test('posts bounded SDP with session auth and validates the answer',
      () async {
    final transport = _SdpTransportFake(
      AvaRealtimeSdpResponse(
        statusCode: HttpStatus.ok,
        contentType: 'application/sdp',
        protocolVersion: '4',
        bytes: Stream.value(utf8.encode(_answerSdp)),
        requestId: 'request-sdp-1',
      ),
    );
    final service = AvaRealtimeSdpService(
      transport: transport,
      requestTimeout: const Duration(seconds: 5),
    );
    final bootstrap = _bootstrap();

    final answer = await service.negotiateV4(
      bootstrap: bootstrap,
      offerSdp: _offerSdp,
    );

    expect(answer, _answerSdp);
    expect(transport.endpoint, Uri.parse(bootstrap.webrtcUrl));
    expect(transport.offerSdp, _offerSdp);
    expect(transport.headers[HttpHeaders.contentTypeHeader], 'application/sdp');
    expect(transport.headers['x-ava-protocol-version'], '4');
    expect(
      transport.headers[HttpHeaders.authorizationHeader],
      'Bearer ${bootstrap.webrtcToken}',
    );
  });

  test('media connector closes the microphone session when SDP fails',
      () async {
    final media = DeterministicAvaRealtimeMediaSession(offerSdp: _offerSdp);
    final service = AvaRealtimeSdpService(
      transport: _SdpTransportFake(
        AvaRealtimeSdpResponse(
          statusCode: HttpStatus.serviceUnavailable,
          contentType: 'application/json',
          protocolVersion: '4',
          bytes: Stream.value(utf8.encode('{"error":"unavailable"}')),
        ),
      ),
      requestTimeout: const Duration(seconds: 5),
    );
    final connector = createAvaRealtimeMediaConnectorV4(
      mediaSessionFactory: () async => media,
      sdpService: service,
    );

    await expectLater(
      connector(_bootstrap()),
      throwsA(isA<AvaRealtimeSdpException>()),
    );
    expect(media.closed, isTrue);
    expect(media.appliedAnswerSdp, isNull);
  });

  test('empty backend failure remains a typed SDP rejection', () async {
    final service = AvaRealtimeSdpService(
      transport: _SdpTransportFake(
        AvaRealtimeSdpResponse(
          statusCode: HttpStatus.serviceUnavailable,
          contentType: 'application/json',
          protocolVersion: '4',
          bytes: const Stream.empty(),
          requestId: 'request-empty-failure',
        ),
      ),
      requestTimeout: const Duration(seconds: 5),
    );

    await expectLater(
      service.negotiateV4(
        bootstrap: _bootstrap(),
        offerSdp: _offerSdp,
      ),
      throwsA(
        isA<AvaRealtimeSdpException>()
            .having((error) => error.statusCode, 'statusCode', 503)
            .having(
              (error) => error.requestId,
              'requestId',
              'request-empty-failure',
            ),
      ),
    );
  });

  test('media connector completes one ordered offer and answer', () async {
    final media = DeterministicAvaRealtimeMediaSession(offerSdp: _offerSdp);
    final connector = createAvaRealtimeMediaConnectorV4(
      mediaSessionFactory: () async => media,
      sdpService: AvaRealtimeSdpService(
        transport: _SdpTransportFake(
          AvaRealtimeSdpResponse(
            statusCode: HttpStatus.ok,
            contentType: 'application/sdp',
            protocolVersion: '4',
            bytes: Stream.value(utf8.encode(_answerSdp)),
          ),
        ),
        requestTimeout: const Duration(seconds: 5),
      ),
    );

    final connected = await connector(_bootstrap());

    expect(identical(connected, media), isTrue);
    expect(media.offerCount, 1);
    expect(media.appliedAnswerSdp, _answerSdp);
    expect(media.microphoneEnabled, isFalse);
    expect(
      media.microphoneChangeCount,
      0,
      reason: 'Connection preparation must not enable microphone capture.',
    );
    await connected.setMicrophoneEnabled(true);
    expect(media.microphoneEnabled, isTrue);
    expect(media.microphoneChangeCount, 1);
    await connected.close();
    expect(media.closed, isTrue);
  });

  test('rejects malformed, oversized and mismatched SDP responses', () async {
    Future<void> expectFailure(AvaRealtimeSdpResponse response) async {
      final service = AvaRealtimeSdpService(
        transport: _SdpTransportFake(response),
        requestTimeout: const Duration(milliseconds: 100),
      );
      await expectLater(
        service.negotiateV4(
          bootstrap: _bootstrap(),
          offerSdp: _offerSdp,
        ),
        throwsA(anyOf(isA<AvaRealtimeSdpException>(), isA<FormatException>())),
      );
    }

    await expectFailure(
      AvaRealtimeSdpResponse(
        statusCode: HttpStatus.ok,
        contentType: 'text/plain',
        protocolVersion: '4',
        bytes: Stream.value(utf8.encode(_answerSdp)),
      ),
    );
    await expectFailure(
      AvaRealtimeSdpResponse(
        statusCode: HttpStatus.ok,
        contentType: 'application/sdp',
        protocolVersion: '3',
        bytes: Stream.value(utf8.encode(_answerSdp)),
      ),
    );
    await expectFailure(
      AvaRealtimeSdpResponse(
        statusCode: HttpStatus.ok,
        contentType: 'application/sdp',
        protocolVersion: '4',
        bytes: Stream.value(utf8.encode('v=0\r\nm=video 9 RTP/AVP 96\r\n')),
      ),
    );
    await expectFailure(
      AvaRealtimeSdpResponse(
        statusCode: HttpStatus.ok,
        contentType: 'application/sdp',
        protocolVersion: '4',
        bytes: Stream.value(
          List<int>.filled(avaMaximumSdpBytes + 1, 65),
        ),
      ),
    );
  });
}

AvaSessionBootstrapV4 _bootstrap() {
  final raw = jsonDecode(
    File('protocol/fixtures/ava_session_bootstrap_v4.json').readAsStringSync(),
  );
  return AvaSessionBootstrapV4.fromJson(
    (raw as Map).cast<String, Object?>(),
  );
}

final class _SdpTransportFake implements AvaRealtimeSdpTransport {
  _SdpTransportFake(this.response);

  final AvaRealtimeSdpResponse response;
  late Uri endpoint;
  late Map<String, String> headers;
  late String offerSdp;

  @override
  Future<AvaRealtimeSdpResponse> postSdp({
    required Uri endpoint,
    required Map<String, String> headers,
    required String offerSdp,
    required Duration timeout,
  }) async {
    this.endpoint = endpoint;
    this.headers = Map.unmodifiable(headers);
    this.offerSdp = offerSdp;
    return response;
  }

  @override
  void close() {}
}
