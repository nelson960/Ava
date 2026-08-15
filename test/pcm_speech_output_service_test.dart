import 'package:ava/services/pcm_speech_output_service.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const channel = MethodChannel('test/ava_pcm_speech');

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test('sends validated PCM lifecycle and derives sample-clock position',
      () async {
    final calls = <MethodCall>[];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      calls.add(call);
      if (call.method == 'progress') {
        return {
          'renderedSampleCount': 12000,
          'scheduledSampleCount': 18000,
        };
      }
      if (call.method == 'fadeOutAndStop') {
        return {'renderedSampleCount': 12500};
      }
      if (call.method == 'finish') {
        return {'renderedSampleCount': 18000};
      }
      return null;
    });
    const output = ApplePcmSpeechOutputService(channel: channel);

    await output.start(
      turnId: 'turn-1234',
      sampleRateHz: 24000,
      volume: 0.8,
    );
    await output.append(
      turnId: 'turn-1234',
      audio: Uint8List.fromList([0, 0, 1, 0]),
    );
    expect(
      await output.position(turnId: 'turn-1234'),
      const Duration(milliseconds: 500),
    );
    final progress = await output.progress(turnId: 'turn-1234');
    expect(progress.renderedSampleCount, 12000);
    expect(progress.bufferedSampleCount, 6000);
    expect(
      await output.fadeOutAndStop(
        turnId: 'turn-1234',
        fadeDuration: const Duration(milliseconds: 80),
      ),
      isA<PcmStopResult>().having(
        (result) => result.renderedSampleCount,
        'renderedSampleCount',
        12500,
      ),
    );
    await output.finish(turnId: 'turn-1234');
    await output.stop();

    expect(
      calls.map((call) => call.method),
      [
        'start',
        'append',
        'progress',
        'progress',
        'fadeOutAndStop',
        'finish',
        'stop',
      ],
    );
    expect(calls.first.arguments, {
      'turnId': 'turn-1234',
      'sampleRateHz': 24000,
      'channels': 1,
      'bitsPerSample': 16,
      'volume': 0.8,
    });
  });

  test('rejects invalid rate, volume, turn and incomplete samples', () async {
    const output = ApplePcmSpeechOutputService(channel: channel);

    await expectLater(
      output.start(
        turnId: 'turn-1234',
        sampleRateHz: 44100,
        volume: 0.8,
      ),
      throwsArgumentError,
    );
    await expectLater(
      output.start(
        turnId: 'turn-1234',
        sampleRateHz: 24000,
        volume: 2,
      ),
      throwsRangeError,
    );
    await expectLater(
      output.append(
        turnId: 'short',
        audio: Uint8List.fromList([0, 0]),
      ),
      throwsArgumentError,
    );
    await expectLater(
      output.append(
        turnId: 'turn-1234',
        audio: Uint8List.fromList([0]),
      ),
      throwsArgumentError,
    );
    await expectLater(
      output.fadeOutAndStop(
        turnId: 'turn-1234',
        fadeDuration: const Duration(milliseconds: 250),
      ),
      throwsRangeError,
    );
  });
}
