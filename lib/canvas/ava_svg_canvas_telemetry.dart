import 'package:flutter/foundation.dart';

typedef AvaSvgCanvasTelemetrySink = void Function(
  AvaSvgCanvasTelemetryEvent event,
);

/// Content-free, monotonic canvas lifecycle measurement.
///
/// Fields are deliberately limited to bounded scalar identity, timing, size,
/// status, and usage values. SVG, prompts, transcripts, credentials, URLs, and
/// child profile data never belong in this event.
@immutable
final class AvaSvgCanvasTelemetryEvent {
  AvaSvgCanvasTelemetryEvent({
    required String stage,
    required Map<String, Object?> fields,
    this.stageDuration = Duration.zero,
  })  : stage = _validatedStage(stage),
        fields = Map.unmodifiable(_validatedFields(fields)) {
    if (stageDuration.isNegative) {
      throw ArgumentError.value(
        stageDuration,
        'stageDuration',
        'must not be negative',
      );
    }
  }

  final String stage;
  final Duration stageDuration;
  final Map<String, Object?> fields;
}

String _validatedStage(String value) {
  if (!RegExp(r'^[a-z][a-z0-9_.-]{1,79}$').hasMatch(value)) {
    throw ArgumentError.value(value, 'stage', 'has an invalid format');
  }
  return value;
}

Map<String, Object?> _validatedFields(Map<String, Object?> source) {
  if (source.length > 32) {
    throw ArgumentError.value(source.length, 'fields', 'exceeds 32 entries');
  }
  final result = <String, Object?>{};
  for (final entry in source.entries) {
    if (!RegExp(r'^[A-Za-z][A-Za-z0-9]{0,47}$').hasMatch(entry.key)) {
      throw ArgumentError.value(
        entry.key,
        'fields',
        'contains an invalid key',
      );
    }
    final value = entry.value;
    if (value is num && !value.isFinite) {
      throw ArgumentError.value(value, entry.key, 'must be finite');
    }
    if (value is String && value.length > 160) {
      throw ArgumentError.value(
        value.length,
        entry.key,
        'string exceeds 160 characters',
      );
    }
    if (value != null && value is! String && value is! num && value is! bool) {
      throw ArgumentError.value(
        value.runtimeType,
        entry.key,
        'must be a scalar telemetry value',
      );
    }
    result[entry.key] = value;
  }
  return result;
}
