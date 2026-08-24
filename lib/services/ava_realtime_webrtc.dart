import 'dart:async';
import 'dart:convert';
import 'dart:developer' as developer;
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';

import '../models/ava_realtime_protocol_v4.dart';
import 'ava_realtime_trace.dart';

const int avaMaximumSdpBytes = 256 * 1024;

const Map<String, Object> avaRealtimeMicrophoneConstraints = {
  'audio': <String, Object>{
    'echoCancellation': true,
    'noiseSuppression': true,
    'autoGainControl': true,
    // flutter_webrtc 1.5.2's Darwin parser reads the legacy optional map,
    // while its other native implementations read the W3C keys above.
    // Request both shapes so Apple capture does not depend on SDK defaults.
    'optional': <Map<String, Object>>[
      <String, Object>{'googEchoCancellation': true},
      <String, Object>{'googNoiseSuppression': true},
      <String, Object>{'googAutoGainControl': true},
    ],
  },
  'video': false,
};

const MethodChannel _audioDiagnosticsChannel =
    MethodChannel('ava/audio_diagnostics');

enum AvaRealtimeMediaState {
  newSession,
  connecting,
  connected,
  disconnected,
  failed,
  closed,
}

abstract interface class AvaRealtimeMediaSession {
  Stream<AvaRealtimeMediaState> get states;

  bool get microphoneEnabled;

  Future<String> createOffer();

  Future<void> applyAnswer(String answerSdp);

  Future<void> setMicrophoneEnabled(bool enabled);

  Future<void> close();
}

final class AvaRealtimeSdpResponse {
  const AvaRealtimeSdpResponse({
    required this.statusCode,
    required this.contentType,
    required this.protocolVersion,
    required this.bytes,
    this.requestId,
  });

  final int statusCode;
  final String? contentType;
  final String? protocolVersion;
  final Stream<List<int>> bytes;
  final String? requestId;
}

abstract interface class AvaRealtimeSdpTransport {
  Future<AvaRealtimeSdpResponse> postSdp({
    required Uri endpoint,
    required Map<String, String> headers,
    required String offerSdp,
    required Duration timeout,
  });

  void close();
}

final class IoAvaRealtimeSdpTransport implements AvaRealtimeSdpTransport {
  IoAvaRealtimeSdpTransport({HttpClient? client})
      : _client = client ?? HttpClient();

  final HttpClient _client;

  @override
  Future<AvaRealtimeSdpResponse> postSdp({
    required Uri endpoint,
    required Map<String, String> headers,
    required String offerSdp,
    required Duration timeout,
  }) async {
    _validateAudioSdp(offerSdp, description: 'offer');
    final request = await _client.postUrl(endpoint).timeout(timeout);
    headers.forEach(request.headers.set);
    final bytes = utf8.encode(offerSdp);
    request.contentLength = bytes.length;
    request.add(bytes);
    final response = await request.close().timeout(timeout);
    return AvaRealtimeSdpResponse(
      statusCode: response.statusCode,
      contentType: response.headers.contentType?.mimeType,
      protocolVersion: response.headers.value('x-ava-protocol-version'),
      bytes: response.timeout(timeout),
      requestId: response.headers.value('x-request-id'),
    );
  }

  @override
  void close() => _client.close(force: true);
}

final class AvaRealtimeSdpException implements Exception {
  const AvaRealtimeSdpException({
    required this.message,
    this.statusCode,
    this.requestId,
  });

  final String message;
  final int? statusCode;
  final String? requestId;

  @override
  String toString() {
    final details = <String>[
      if (statusCode != null) 'status=$statusCode',
      if (requestId != null) 'requestId=$requestId',
    ];
    return details.isEmpty
        ? 'AvaRealtimeSdpException: $message'
        : 'AvaRealtimeSdpException: $message (${details.join(', ')})';
  }
}

final class AvaRealtimeSdpService {
  const AvaRealtimeSdpService({
    required AvaRealtimeSdpTransport transport,
    required Duration requestTimeout,
  })  : _transport = transport,
        _requestTimeout = requestTimeout;

  final AvaRealtimeSdpTransport _transport;
  final Duration _requestTimeout;

  Future<String> negotiateV4({
    required AvaSessionBootstrapV4 bootstrap,
    required String offerSdp,
  }) async {
    _validateAudioSdp(offerSdp, description: 'offer');
    final response = await _transport.postSdp(
      endpoint: Uri.parse(bootstrap.webrtcUrl),
      headers: {
        HttpHeaders.acceptHeader: 'application/sdp',
        HttpHeaders.contentTypeHeader: 'application/sdp',
        HttpHeaders.authorizationHeader: 'Bearer ${bootstrap.webrtcToken}',
        'x-ava-protocol-version': '$avaRealtimeProtocolVersionV4',
      },
      offerSdp: offerSdp,
      timeout: _requestTimeout,
    );
    if (response.statusCode != HttpStatus.ok) {
      await _drainBounded(response.bytes).timeout(_requestTimeout);
      throw AvaRealtimeSdpException(
        message: 'Ava backend rejected protocol-v4 WebRTC negotiation.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    if (response.protocolVersion != '$avaRealtimeProtocolVersionV4') {
      await _drainBounded(response.bytes).timeout(_requestTimeout);
      throw AvaRealtimeSdpException(
        message: 'Ava backend returned a mismatched SDP protocol version.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    if (response.contentType != 'application/sdp') {
      await _drainBounded(response.bytes).timeout(_requestTimeout);
      throw AvaRealtimeSdpException(
        message: 'Ava backend returned a non-SDP WebRTC answer.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    final answerBytes =
        await _readBounded(response.bytes).timeout(_requestTimeout);
    final String answer;
    try {
      answer = utf8.decode(answerBytes, allowMalformed: false);
    } on FormatException catch (error) {
      throw AvaRealtimeSdpException(
        message: 'Ava backend returned non-UTF-8 SDP: ${error.message}',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    _validateAudioSdp(answer, description: 'answer');
    return answer;
  }

  static Future<List<int>> _readBounded(Stream<List<int>> source) async {
    final result = await _drainBounded(source);
    if (result.isEmpty) {
      throw const FormatException('Realtime SDP response cannot be empty.');
    }
    return result;
  }

  static Future<List<int>> _drainBounded(Stream<List<int>> source) async {
    final result = <int>[];
    await for (final chunk in source) {
      if (result.length + chunk.length > avaMaximumSdpBytes) {
        throw const FormatException(
          'Realtime SDP response exceeds the 256 KiB limit.',
        );
      }
      result.addAll(chunk);
    }
    return result;
  }
}

final class FlutterAvaRealtimeMediaSession implements AvaRealtimeMediaSession {
  static const Duration _connectionTimeout = Duration(seconds: 15);

  FlutterAvaRealtimeMediaSession._({
    required RTCPeerConnection peerConnection,
    required RTCDataChannel providerEventsChannel,
    required MediaStream microphoneStream,
    required RTCRtpSender microphoneSender,
  })  : _peerConnection = peerConnection,
        _providerEventsChannel = providerEventsChannel,
        _microphoneStream = microphoneStream,
        _microphoneSender = microphoneSender {
    _peerConnection.onConnectionState = _handleConnectionState;
    // Ava's backend sideband owns provider events. The WebRTC data channel is
    // still required in the SDP contract but never becomes app authority.
    _providerEventsChannel.onMessage = (_) {};
  }

  final RTCPeerConnection _peerConnection;
  final RTCDataChannel _providerEventsChannel;
  final StreamController<AvaRealtimeMediaState> _states =
      StreamController<AvaRealtimeMediaState>.broadcast();

  bool _offerCreated = false;
  bool _answerApplied = false;
  bool _closed = false;
  MediaStream? _microphoneStream;
  RTCRtpSender? _microphoneSender;

  static Future<FlutterAvaRealtimeMediaSession> create() async {
    final trace = AvaRealtimeTrace.current;
    Future<RTCPeerConnection> createPeer() => createPeerConnection(
          const {
            'sdpSemantics': 'unified-plan',
            'bundlePolicy': 'max-bundle',
          },
          const {
            'mandatory': <String, Object>{},
            'optional': <Map<String, Object>>[],
          },
        );
    final peer = trace == null
        ? await createPeer()
        : await trace.measure('webrtc.peer_create', createPeer);
    RTCDataChannel? providerEventsChannel;
    MediaStream? microphoneStream;
    try {
      Future<MediaStream> captureMicrophone() =>
          navigator.mediaDevices.getUserMedia(
            avaRealtimeMicrophoneConstraints,
          );
      final capturedMicrophone = trace == null
          ? await captureMicrophone()
          : await trace.measure('microphone.capture', captureMicrophone);
      microphoneStream = capturedMicrophone;
      final microphoneTracks = capturedMicrophone.getAudioTracks();
      if (microphoneTracks.length != 1) {
        throw StateError(
          'Ava requires exactly one microphone audio track, '
          'received ${microphoneTracks.length}.',
        );
      }
      final microphoneTrack = microphoneTracks.single..enabled = false;
      if (trace != null) {
        await recordAvaRealtimeAudioDiagnostics(
          trace: trace,
          trigger: 'captureCreated',
          trackSettings: microphoneTrack.getSettings(),
        );
      }
      final microphoneSender = await peer.addTrack(
        microphoneTrack,
        capturedMicrophone,
      );
      providerEventsChannel = await peer.createDataChannel(
        'oai-events',
        RTCDataChannelInit(),
      );
      return FlutterAvaRealtimeMediaSession._(
        peerConnection: peer,
        providerEventsChannel: providerEventsChannel,
        microphoneStream: capturedMicrophone,
        microphoneSender: microphoneSender,
      );
    } catch (_) {
      if (microphoneStream != null) {
        await _disposeCapturedMicrophone(microphoneStream);
      }
      await providerEventsChannel?.close();
      await peer.close();
      await peer.dispose();
      rethrow;
    }
  }

  @override
  Stream<AvaRealtimeMediaState> get states => _states.stream;

  @override
  bool get microphoneEnabled {
    final microphone = _microphoneStream;
    if (_closed || microphone == null) return false;
    final tracks = microphone.getAudioTracks();
    if (tracks.length != 1) return false;
    return tracks.single.enabled;
  }

  @override
  Future<String> createOffer() async {
    _requireOpen();
    if (_offerCreated) {
      throw StateError('Ava WebRTC may create only one offer per session.');
    }
    _offerCreated = true;
    _emit(AvaRealtimeMediaState.connecting);
    final offer = await _peerConnection.createOffer(
      const {
        'offerToReceiveAudio': false,
        'offerToReceiveVideo': false,
      },
    );
    final offerSdp = offer.sdp;
    if (offerSdp == null) {
      throw StateError('WebRTC created an offer without SDP.');
    }
    _validateAudioSdp(offerSdp, description: 'offer');
    await _peerConnection.setLocalDescription(offer);
    return offerSdp;
  }

  @override
  Future<void> applyAnswer(String answerSdp) async {
    _requireOpen();
    if (!_offerCreated) {
      throw StateError('Ava WebRTC answer requires a local offer.');
    }
    if (_answerApplied) {
      throw StateError('Ava WebRTC may apply only one answer per session.');
    }
    _validateAudioSdp(answerSdp, description: 'answer');
    final connectedState = states.firstWhere(
      (state) =>
          state == AvaRealtimeMediaState.connected ||
          state == AvaRealtimeMediaState.failed ||
          state == AvaRealtimeMediaState.closed,
    );
    await _peerConnection.setRemoteDescription(
      RTCSessionDescription(answerSdp, 'answer'),
    );
    // The offer already owns the microphone sender, but the track remains
    // muted until the child explicitly enables it.
    await setMicrophoneEnabled(false);
    _answerApplied = true;
    final state = await connectedState.timeout(_connectionTimeout);
    if (state != AvaRealtimeMediaState.connected) {
      throw StateError(
        'Ava WebRTC entered ${state.name} before the media connection was ready.',
      );
    }
    await _waitForProviderDataChannel();
    await setMicrophoneEnabled(false);
  }

  Future<void> _waitForProviderDataChannel() async {
    if (_providerEventsChannel.state ==
        RTCDataChannelState.RTCDataChannelOpen) {
      return;
    }
    final state = await _providerEventsChannel.stateChangeStream
        .firstWhere(
          (state) =>
              state == RTCDataChannelState.RTCDataChannelOpen ||
              state == RTCDataChannelState.RTCDataChannelClosing ||
              state == RTCDataChannelState.RTCDataChannelClosed,
        )
        .timeout(_connectionTimeout);
    if (state != RTCDataChannelState.RTCDataChannelOpen) {
      throw StateError(
        'Ava Realtime data channel entered ${state.name} before it was ready.',
      );
    }
  }

  @override
  Future<void> setMicrophoneEnabled(bool enabled) async {
    if (_closed) return;
    if (_microphoneSender == null) {
      throw StateError(
        'Ava microphone ownership was lost before the WebRTC call closed.',
      );
    }
    final microphone = _microphoneStream;
    if (microphone == null) {
      throw StateError(
        'Ava microphone stream was released before the WebRTC call closed.',
      );
    }
    final tracks = microphone.getAudioTracks();
    if (tracks.length != 1) {
      throw StateError(
        'Ava microphone track ownership changed unexpectedly.',
      );
    }
    final track = tracks.single;
    track.enabled = enabled;
    await Helper.setMicrophoneMute(!enabled, track);
    if (track.enabled != enabled) {
      throw StateError('Ava microphone track did not accept its new state.');
    }
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _disposeMicrophone();
    await _providerEventsChannel.close();
    await _peerConnection.close();
    await _peerConnection.dispose();
    _emit(AvaRealtimeMediaState.closed);
    await _states.close();
  }

  Future<void> _disposeMicrophone() async {
    final microphone = _microphoneStream;
    _microphoneStream = null;
    final sender = _microphoneSender;
    _microphoneSender = null;
    if (microphone == null) return;
    final tracks = microphone.getAudioTracks();
    for (final track in tracks) {
      track.enabled = false;
    }
    try {
      if (sender == null) {
        throw StateError('Ava microphone stream exists without its sender.');
      }
      await sender.replaceTrack(null);
    } finally {
      await _disposeCapturedMicrophone(microphone);
    }
  }

  static Future<void> _disposeCapturedMicrophone(MediaStream microphone) async {
    for (final track in microphone.getTracks()) {
      track.enabled = false;
      await track.stop();
    }
    await microphone.dispose();
  }

  void _handleConnectionState(RTCPeerConnectionState state) {
    final mapped = switch (state) {
      RTCPeerConnectionState.RTCPeerConnectionStateNew =>
        AvaRealtimeMediaState.newSession,
      RTCPeerConnectionState.RTCPeerConnectionStateConnecting =>
        AvaRealtimeMediaState.connecting,
      RTCPeerConnectionState.RTCPeerConnectionStateConnected =>
        AvaRealtimeMediaState.connected,
      RTCPeerConnectionState.RTCPeerConnectionStateDisconnected =>
        AvaRealtimeMediaState.disconnected,
      RTCPeerConnectionState.RTCPeerConnectionStateFailed =>
        AvaRealtimeMediaState.failed,
      RTCPeerConnectionState.RTCPeerConnectionStateClosed =>
        AvaRealtimeMediaState.closed,
    };
    _emit(mapped);
  }

  void _emit(AvaRealtimeMediaState state) {
    if (!_states.isClosed) {
      _states.add(state);
    }
  }

  void _requireOpen() {
    if (_closed) {
      throw StateError('Ava WebRTC media session is closed.');
    }
  }
}

/// Records content-free Apple microphone processing and route state.
///
/// Track settings are reported separately from the Audio Device Module's
/// actual platform voice-processing state because flutter_webrtc's Darwin
/// track settings are declarative rather than a native pipeline query.
Future<void> recordAvaRealtimeAudioDiagnostics({
  required AvaRealtimeTrace trace,
  required String trigger,
  Map<String, dynamic> trackSettings = const {},
  String? turnId,
  String? streamId,
  String? interruptionReasonCode,
}) async {
  final identityFields = <String, Object?>{
    'trigger': trigger,
    if (turnId != null) 'turnId': turnId,
    if (streamId != null) 'streamId': streamId,
    if (interruptionReasonCode != null)
      'interruptionReasonCode': interruptionReasonCode,
  };
  if (!Platform.isIOS && !Platform.isMacOS) {
    trace.mark(
      'microphone.processing_settings',
      fields: {
        ...identityFields,
        'platform': Platform.operatingSystem,
        'supported': false,
      },
    );
    return;
  }

  try {
    final platformVoiceProcessingAllowed =
        await NativeAudioManagement.isVoiceProcessingEnabled();
    final voiceProcessingBypassed =
        await NativeAudioManagement.isVoiceProcessingBypassed();
    final route = await _audioDiagnosticsChannel
            .invokeMapMethod<String, Object?>('currentRoute') ??
        const <String, Object?>{};
    final fields = <String, Object?>{
      ...identityFields,
      'platform': Platform.operatingSystem,
      'requestedEchoCancellation': true,
      'requestedNoiseSuppression': true,
      'requestedAutoGainControl': true,
      'reportedEchoCancellation':
          _reportedBool(trackSettings, 'echoCancellation'),
      'reportedNoiseSuppression':
          _reportedBool(trackSettings, 'noiseSuppression'),
      'reportedAutoGainControl':
          _reportedBool(trackSettings, 'autoGainControl'),
      'platformVoiceProcessingAllowed': platformVoiceProcessingAllowed,
      'voiceProcessingBypassed': voiceProcessingBypassed,
    };
    for (final key in const <String>[
      'inputRouteType',
      'outputRouteType',
      'audioCategory',
      'audioMode',
      'inputAvailable',
      'sameDevice',
      'sampleRateHz',
      'inputSampleRateHz',
      'outputSampleRateHz',
    ]) {
      final value = route[key];
      if (_isSafeDiagnosticScalar(value)) {
        fields[key] = value;
      }
    }
    trace.mark('microphone.processing_settings', fields: fields);
  } catch (error, stackTrace) {
    developer.log(
      'Apple microphone diagnostics failed without affecting capture.',
      name: 'ava.realtime.media',
      error: error,
      stackTrace: stackTrace,
    );
    trace.mark(
      'microphone.diagnostics_failed',
      fields: {
        ...identityFields,
        'platform': Platform.operatingSystem,
        'errorType': error.runtimeType.toString(),
      },
    );
  }
}

bool? _reportedBool(Map<String, dynamic> settings, String key) {
  final value = settings[key];
  return value is bool ? value : null;
}

bool _isSafeDiagnosticScalar(Object? value) {
  if (value == null || value is bool) return true;
  if (value is num) return value.isFinite;
  return value is String &&
      value.length <= 80 &&
      RegExp(r'^[A-Za-z0-9.,_:+-]*$').hasMatch(value);
}

final class DeterministicAvaRealtimeMediaSession
    implements AvaRealtimeMediaSession {
  DeterministicAvaRealtimeMediaSession({
    required String offerSdp,
  }) : _offerSdp = _validateAudioSdp(offerSdp, description: 'offer');

  final String _offerSdp;
  final StreamController<AvaRealtimeMediaState> _states =
      StreamController<AvaRealtimeMediaState>.broadcast();

  String? appliedAnswerSdp;
  @override
  bool microphoneEnabled = false;
  int microphoneChangeCount = 0;
  bool closed = false;
  int offerCount = 0;

  @override
  Stream<AvaRealtimeMediaState> get states => _states.stream;

  @override
  Future<String> createOffer() async {
    if (closed) throw StateError('Deterministic media session is closed.');
    if (offerCount != 0) {
      throw StateError('Deterministic media offer may be created only once.');
    }
    offerCount += 1;
    _states.add(AvaRealtimeMediaState.connecting);
    return _offerSdp;
  }

  @override
  Future<void> applyAnswer(String answerSdp) async {
    if (closed) throw StateError('Deterministic media session is closed.');
    if (offerCount != 1 || appliedAnswerSdp != null) {
      throw StateError('Deterministic media answer is out of order.');
    }
    appliedAnswerSdp = _validateAudioSdp(
      answerSdp,
      description: 'answer',
    );
    _states.add(AvaRealtimeMediaState.connected);
  }

  @override
  Future<void> setMicrophoneEnabled(bool enabled) async {
    if (closed) return;
    microphoneChangeCount += 1;
    microphoneEnabled = enabled;
  }

  @override
  Future<void> close() async {
    if (closed) return;
    closed = true;
    microphoneEnabled = false;
    _states.add(AvaRealtimeMediaState.closed);
    await _states.close();
  }
}

typedef AvaRealtimeMediaSessionFactory = Future<AvaRealtimeMediaSession>
    Function();

typedef AvaRealtimeMediaConnectorV4 = Future<AvaRealtimeMediaSession> Function(
  AvaSessionBootstrapV4 bootstrap,
);

AvaRealtimeMediaConnectorV4 createAvaRealtimeMediaConnectorV4({
  required AvaRealtimeMediaSessionFactory mediaSessionFactory,
  required AvaRealtimeSdpService sdpService,
}) {
  return (bootstrap) async {
    final trace = AvaRealtimeTrace.current;
    final media = await mediaSessionFactory();
    try {
      final offer = trace == null
          ? await media.createOffer()
          : await trace.measure('webrtc.offer', media.createOffer);
      final answer = trace == null
          ? await sdpService.negotiateV4(
              bootstrap: bootstrap,
              offerSdp: offer,
            )
          : await trace.measure(
              'webrtc.sdp',
              () => sdpService.negotiateV4(
                bootstrap: bootstrap,
                offerSdp: offer,
              ),
            );
      if (trace == null) {
        await media.applyAnswer(answer);
      } else {
        await trace.measure(
          'webrtc.answer_ice',
          () => media.applyAnswer(answer),
        );
      }
      return media;
    } catch (_) {
      await media.close();
      rethrow;
    }
  };
}

String _validateAudioSdp(
  String value, {
  required String description,
}) {
  final bytes = utf8.encode(value);
  if (bytes.isEmpty || bytes.length > avaMaximumSdpBytes) {
    throw FormatException(
      '$description SDP must contain 1-$avaMaximumSdpBytes bytes.',
    );
  }
  if (value.contains('\u0000')) {
    throw FormatException('$description SDP cannot contain NUL bytes.');
  }
  final lines =
      value.replaceAll('\r\n', '\n').replaceAll('\r', '\n').split('\n');
  if (lines.isEmpty || lines.first.trim() != 'v=0') {
    throw FormatException('$description SDP must begin with v=0.');
  }
  if (!lines.any((line) => line.startsWith('m=audio '))) {
    throw FormatException('$description SDP must contain an audio section.');
  }
  if (!lines.any((line) => line.startsWith('m=application '))) {
    throw FormatException(
      '$description SDP must contain the Realtime data-channel section.',
    );
  }
  return value;
}
