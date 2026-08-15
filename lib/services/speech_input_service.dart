import 'package:flutter/services.dart';

abstract interface class SpeechInputService {
  Future<String> listen();

  Future<void> stop();
}

class SystemSpeechInputService implements SpeechInputService {
  const SystemSpeechInputService({
    MethodChannel channel = const MethodChannel('ava/speech_input'),
    this.localeIdentifier = 'en-US',
  }) : _channel = channel;

  final MethodChannel _channel;
  final String localeIdentifier;

  @override
  Future<String> listen() async {
    final locale = localeIdentifier.trim();
    if (locale.isEmpty) {
      throw StateError('Ava speech recognition locale cannot be empty.');
    }
    final transcript = await _channel.invokeMethod<String>(
      'listen',
      <String, Object>{'locale': locale},
    );
    final normalized = transcript?.trim();
    if (normalized == null || normalized.isEmpty) {
      throw const FormatException(
        'Speech recognition completed without a transcript.',
      );
    }
    return normalized;
  }

  @override
  Future<void> stop() => _channel.invokeMethod<void>('stop');
}
