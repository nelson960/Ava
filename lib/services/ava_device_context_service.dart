import 'package:flutter/services.dart';

abstract interface class AvaDeviceContext {
  Future<String> currentTimezoneId();
}

final class AvaDeviceContextService implements AvaDeviceContext {
  const AvaDeviceContextService({
    MethodChannel channel = const MethodChannel('ava/device_context'),
  }) : _channel = channel;

  final MethodChannel _channel;

  @override
  Future<String> currentTimezoneId() async {
    final value = await _channel.invokeMethod<String>('currentTimezoneId');
    if (value == null || value.isEmpty || value != value.trim()) {
      throw const FormatException(
        'The device did not provide a valid IANA timezone identifier.',
      );
    }
    return value;
  }
}
