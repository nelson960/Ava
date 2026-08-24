import 'dart:math';

import '../models/ava_realtime_protocol.dart';
import 'ava_realtime_turn_runtime.dart';

abstract interface class AvaRealtimeRuntimeBuilder {
  Future<AvaRealtimeTurnRuntime> create({
    required AvaRealtimeAgeBand ageBand,
    required double volume,
    void Function(bool enabled)? onMicrophoneTrackChanged,
  });

  Future<void> close();
}

String createSecureAvaCancelRequestId() {
  final random = Random.secure();
  final buffer = StringBuffer('cancel-');
  for (var index = 0; index < 16; index += 1) {
    buffer.write(random.nextInt(256).toRadixString(16).padLeft(2, '0'));
  }
  return buffer.toString();
}
