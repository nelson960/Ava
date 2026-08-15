import 'package:ava/services/speech_input_service.dart';
import 'package:ava/services/speech_output_service.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const inputChannel = MethodChannel('test/ava_speech_input');
  const outputChannel = MethodChannel('test/ava_speech_output');

  tearDown(() async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(inputChannel, null);
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(outputChannel, null);
  });

  test('system speech input sends locale and validates the transcript',
      () async {
    MethodCall? received;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(inputChannel, (call) async {
      received = call;
      return '  What is gravity?  ';
    });
    const service = SystemSpeechInputService(
      channel: inputChannel,
      localeIdentifier: 'en-IN',
    );

    expect(await service.listen(), 'What is gravity?');
    expect(received?.method, 'listen');
    expect(received?.arguments, {'locale': 'en-IN'});
  });

  test('system speech input exposes stop and rejects an empty transcript',
      () async {
    final calls = <String>[];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(inputChannel, (call) async {
      calls.add(call.method);
      return call.method == 'listen' ? '   ' : null;
    });
    const service = SystemSpeechInputService(channel: inputChannel);

    await expectLater(service.listen(), throwsFormatException);
    await service.stop();
    expect(calls, ['listen', 'stop']);
  });

  test('system TTS sends validated text, volume, and language', () async {
    MethodCall? received;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(outputChannel, (call) async {
      received = call;
      return null;
    });
    const service = SystemSpeechOutputService(
      channel: outputChannel,
      languageCode: 'en-US',
    );

    await service.speak('  Hello Mia. ', volume: 0.75);
    expect(received?.method, 'speak');
    expect(received?.arguments, {
      'text': 'Hello Mia.',
      'volume': 0.75,
      'language': 'en-US',
    });
  });
}
