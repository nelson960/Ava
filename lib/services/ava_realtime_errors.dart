final class AvaRealtimeStateException implements Exception {
  const AvaRealtimeStateException(this.message);

  final String message;

  @override
  String toString() => 'AvaRealtimeStateException: $message';
}

final class AvaRealtimeBufferOverflow implements Exception {
  const AvaRealtimeBufferOverflow(this.message);

  final String message;

  @override
  String toString() => 'AvaRealtimeBufferOverflow: $message';
}
