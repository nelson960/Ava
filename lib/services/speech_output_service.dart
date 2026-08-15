import 'package:flutter/services.dart';

abstract interface class SpeechOutputService {
  Future<void> speak(String text, {required double volume});

  Future<void> stop();
}

class SystemSpeechOutputService implements SpeechOutputService {
  const SystemSpeechOutputService({
    MethodChannel channel = const MethodChannel('ava/speech'),
    this.languageCode = 'en-US',
  }) : _channel = channel;

  final MethodChannel _channel;
  final String languageCode;

  @override
  Future<void> speak(String text, {required double volume}) async {
    final normalizedText = text.trim();
    if (normalizedText.isEmpty) {
      throw ArgumentError.value(text, 'text', 'Speech text cannot be empty.');
    }
    if (volume < 0 || volume > 1) {
      throw RangeError.range(volume, 0, 1, 'volume');
    }

    await _channel.invokeMethod<void>('speak', <String, Object>{
      'text': normalizedText,
      'volume': volume,
      'language': languageCode,
    });
  }

  @override
  Future<void> stop() => _channel.invokeMethod<void>('stop');
}
