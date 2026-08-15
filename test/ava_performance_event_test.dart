import 'dart:convert';

import 'package:ava/models/ava_performance_event.dart';
import 'package:ava/models/ava_performance_plan.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('parses strict PCM metadata, timing and audio events', () {
    final metadata = AvaPerformanceEvent.fromJson(
      _event(
        event: 'audioMetadata',
        sequence: 2,
        fields: const {
          'audioFormat': 'pcm_s16le',
          'mediaType': 'audio/x-raw',
          'sampleRateHz': 24000,
          'channels': 1,
          'bitsPerSample': 16,
          'model': 'candidate-model',
          'voiceId': 'candidate-voice',
          'voiceLabel': 'Eve marry',
        },
      ),
    );
    final timing = AvaPerformanceEvent.fromJson(
      _event(
        event: 'characterTimingBatch',
        sequence: 4,
        fields: const {
          'segmentId': 'reply',
          'chunkIndex': 0,
          'timeBase': 'segmentAudio',
          'timings': [
            {
              'character': 'H',
              'startSeconds': 0.0,
              'endSeconds': 0.08,
            },
          ],
        },
      ),
    );
    final audio = AvaPerformanceEvent.fromJson(
      _event(
        event: 'audioChunk',
        sequence: 5,
        fields: {
          'segmentId': 'reply',
          'chunkIndex': 0,
          'audioBase64': base64Encode([0, 0, 1, 0]),
        },
      ),
    );

    expect(metadata, isA<AvaAudioMetadataEvent>());
    expect((metadata as AvaAudioMetadataEvent).sampleRateHz, 24000);
    expect(timing, isA<AvaCharacterTimingBatchEvent>());
    expect(
      (timing as AvaCharacterTimingBatchEvent).timings.single.start,
      Duration.zero,
    );
    expect(audio, isA<AvaAudioChunkEvent>());
    expect((audio as AvaAudioChunkEvent).audio, [0, 0, 1, 0]);
  });

  test('parses terminal failure without allowing fallback fields', () {
    final failed = AvaPerformanceEvent.fromJson(
      _event(
        event: 'turnFailed',
        sequence: 3,
        fields: const {
          'failure': {
            'code': 'speechUnavailable',
            'message': 'Speech is unavailable.',
            'retryable': true,
          },
        },
      ),
    );

    expect(failed, isA<AvaTurnFailedEvent>());
    expect(failed.isTerminal, isTrue);
    expect(
      (failed as AvaTurnFailedEvent).failure.code,
      PerformanceFailureCode.speechUnavailable,
    );

    expect(
      () => AvaPerformanceEvent.fromJson(
        _event(
          event: 'turnFailed',
          sequence: 3,
          fields: const {
            'failure': {
              'code': 'speechUnavailable',
              'message': 'Speech is unavailable.',
              'retryable': true,
              'fallbackVoice': 'system',
            },
          },
        ),
      ),
      throwsFormatException,
    );
  });

  test('rejects unsupported audio and incomplete PCM samples', () {
    expect(
      () => AvaPerformanceEvent.fromJson(
        _event(
          event: 'audioMetadata',
          sequence: 2,
          fields: const {
            'audioFormat': 'mp3',
            'mediaType': 'audio/mpeg',
            'sampleRateHz': 44100,
            'channels': 1,
            'bitsPerSample': 16,
            'model': 'model',
            'voiceId': 'voice',
            'voiceLabel': 'Voice',
          },
        ),
      ),
      throwsFormatException,
    );
    expect(
      () => AvaPerformanceEvent.fromJson(
        _event(
          event: 'audioChunk',
          sequence: 3,
          fields: {
            'segmentId': 'reply',
            'chunkIndex': 0,
            'audioBase64': base64Encode([0]),
          },
        ),
      ),
      throwsFormatException,
    );
  });
}

Map<String, Object?> _event({
  required String event,
  required int sequence,
  required Map<String, Object?> fields,
}) =>
    {
      'protocolVersion': 2,
      'event': event,
      'sequence': sequence,
      'turnId': 'turn-1234',
      ...fields,
    };
