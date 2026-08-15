import 'package:flutter/services.dart';

class PcmPlaybackProgress {
  const PcmPlaybackProgress({
    required this.renderedSampleCount,
    required this.scheduledSampleCount,
    required this.sampleRateHz,
  });

  final int renderedSampleCount;
  final int scheduledSampleCount;
  final int sampleRateHz;

  int get bufferedSampleCount => scheduledSampleCount - renderedSampleCount < 0
      ? 0
      : scheduledSampleCount - renderedSampleCount;

  Duration get position => Duration(
        microseconds: (renderedSampleCount * Duration.microsecondsPerSecond) ~/
            sampleRateHz,
      );
}

class PcmStopResult {
  const PcmStopResult({required this.renderedSampleCount});

  final int renderedSampleCount;
}

abstract interface class PcmSpeechOutputService {
  Future<void> start({
    required String turnId,
    required int sampleRateHz,
    required double volume,
  });

  Future<void> append({
    required String turnId,
    required Uint8List audio,
  });

  Future<PcmStopResult> finish({required String turnId});

  Future<PcmPlaybackProgress> progress({required String turnId});

  Future<Duration> position({required String turnId});

  Future<PcmStopResult> fadeOutAndStop({
    required String turnId,
    Duration fadeDuration = const Duration(milliseconds: 80),
  });

  Future<void> stop();
}

class ApplePcmSpeechOutputService implements PcmSpeechOutputService {
  const ApplePcmSpeechOutputService({
    MethodChannel channel = const MethodChannel('ava/pcm_speech'),
  }) : _channel = channel;

  static const supportedSampleRateHz = 24000;
  static const _maximumChunkBytes = 2 * 1024 * 1024;

  final MethodChannel _channel;

  @override
  Future<void> start({
    required String turnId,
    required int sampleRateHz,
    required double volume,
  }) async {
    final normalizedTurnId = _validatedTurnId(turnId);
    if (sampleRateHz != supportedSampleRateHz) {
      throw ArgumentError.value(
        sampleRateHz,
        'sampleRateHz',
        'Ava PCM speech requires 24000 Hz audio',
      );
    }
    if (volume < 0 || volume > 1) {
      throw RangeError.range(volume, 0, 1, 'volume');
    }
    await _channel.invokeMethod<void>('start', {
      'turnId': normalizedTurnId,
      'sampleRateHz': sampleRateHz,
      'channels': 1,
      'bitsPerSample': 16,
      'volume': volume,
    });
  }

  @override
  Future<void> append({
    required String turnId,
    required Uint8List audio,
  }) async {
    final normalizedTurnId = _validatedTurnId(turnId);
    if (audio.isEmpty ||
        audio.length > _maximumChunkBytes ||
        audio.length.isOdd) {
      throw ArgumentError.value(
        audio.length,
        'audio',
        'must contain bounded complete PCM16 samples',
      );
    }
    await _channel.invokeMethod<void>('append', {
      'turnId': normalizedTurnId,
      'audio': audio,
    });
  }

  @override
  Future<PcmStopResult> finish({required String turnId}) async {
    final raw = await _channel.invokeMethod<Object?>('finish', {
      'turnId': _validatedTurnId(turnId),
    });
    if (raw is! Map || raw['renderedSampleCount'] is! int) {
      throw StateError('Ava PCM speech returned an invalid finish result.');
    }
    final rendered = raw['renderedSampleCount'] as int;
    if (rendered < 0) {
      throw StateError('Ava PCM speech returned a negative finish position.');
    }
    return PcmStopResult(renderedSampleCount: rendered);
  }

  @override
  Future<PcmPlaybackProgress> progress({required String turnId}) async {
    final raw = await _channel.invokeMethod<Object?>('progress', {
      'turnId': _validatedTurnId(turnId),
    });
    if (raw is! Map) {
      throw StateError('Ava PCM speech returned invalid playback progress.');
    }
    final rendered = raw['renderedSampleCount'];
    final scheduled = raw['scheduledSampleCount'];
    if (rendered is! int ||
        scheduled is! int ||
        rendered < 0 ||
        scheduled < 0 ||
        rendered > scheduled) {
      throw StateError('Ava PCM speech returned invalid sample counts.');
    }
    return PcmPlaybackProgress(
      renderedSampleCount: rendered,
      scheduledSampleCount: scheduled,
      sampleRateHz: supportedSampleRateHz,
    );
  }

  @override
  Future<Duration> position({required String turnId}) async =>
      (await progress(turnId: turnId)).position;

  @override
  Future<PcmStopResult> fadeOutAndStop({
    required String turnId,
    Duration fadeDuration = const Duration(milliseconds: 80),
  }) async {
    if (fadeDuration.isNegative ||
        fadeDuration > const Duration(milliseconds: 200)) {
      throw RangeError.range(
        fadeDuration.inMicroseconds,
        0,
        const Duration(milliseconds: 200).inMicroseconds,
        'fadeDuration',
      );
    }
    final raw = await _channel.invokeMethod<Object?>('fadeOutAndStop', {
      'turnId': _validatedTurnId(turnId),
      'fadeDurationMs': fadeDuration.inMilliseconds,
    });
    if (raw is! Map || raw['renderedSampleCount'] is! int) {
      throw StateError('Ava PCM speech returned an invalid stop result.');
    }
    final rendered = raw['renderedSampleCount'] as int;
    if (rendered < 0) {
      throw StateError('Ava PCM speech returned a negative stop position.');
    }
    return PcmStopResult(renderedSampleCount: rendered);
  }

  @override
  Future<void> stop() => _channel.invokeMethod<void>('stop');

  String _validatedTurnId(String turnId) {
    final normalized = turnId.trim();
    if (!RegExp(r'^[A-Za-z0-9._:-]{8,64}$').hasMatch(normalized)) {
      throw ArgumentError.value(turnId, 'turnId', 'has an invalid format');
    }
    return normalized;
  }
}
