class AvaBackendConfiguration {
  const AvaBackendConfiguration._({
    required this.baseUri,
    required this.requestTimeout,
    required this.allowsInsecureLoopback,
  });

  factory AvaBackendConfiguration.fromEnvironment() {
    const baseUrl = String.fromEnvironment('AVA_API_BASE_URL');
    const timeoutSeconds = int.fromEnvironment(
      'AVA_API_TIMEOUT_SECONDS',
      defaultValue: 30,
    );
    const allowInsecureLocalhost = bool.fromEnvironment(
      'AVA_ALLOW_INSECURE_LOCALHOST',
    );
    return AvaBackendConfiguration.parse(
      baseUrl,
      requestTimeout: const Duration(seconds: timeoutSeconds),
      allowInsecureLocalhost: allowInsecureLocalhost,
    );
  }

  factory AvaBackendConfiguration.parse(
    String rawBaseUrl, {
    Duration requestTimeout = const Duration(seconds: 30),
    bool allowInsecureLocalhost = false,
  }) {
    if (requestTimeout <= Duration.zero ||
        requestTimeout > const Duration(minutes: 2)) {
      throw ArgumentError.value(
        requestTimeout,
        'requestTimeout',
        'must be greater than zero and no longer than two minutes',
      );
    }
    final normalized = rawBaseUrl.trim();
    if (normalized.isEmpty) {
      return AvaBackendConfiguration._(
        baseUri: null,
        requestTimeout: requestTimeout,
        allowsInsecureLoopback: allowInsecureLocalhost,
      );
    }
    final parsed = Uri.tryParse(normalized);
    if (parsed == null ||
        !parsed.hasScheme ||
        parsed.host.isEmpty ||
        parsed.hasQuery ||
        parsed.hasFragment ||
        parsed.userInfo.isNotEmpty) {
      throw FormatException(
        'AVA_API_BASE_URL must be an absolute server URL without credentials, '
        'query parameters, or fragments.',
      );
    }
    final isLoopback = parsed.host == 'localhost' ||
        parsed.host == '127.0.0.1' ||
        parsed.host == '::1';
    if (parsed.scheme != 'https' &&
        !(allowInsecureLocalhost && parsed.scheme == 'http' && isLoopback)) {
      throw const FormatException(
        'AVA_API_BASE_URL must use HTTPS. HTTP is permitted only for an '
        'explicitly enabled localhost development server.',
      );
    }
    final path = parsed.path.endsWith('/') ? parsed.path : '${parsed.path}/';
    return AvaBackendConfiguration._(
      baseUri: parsed.replace(path: path),
      requestTimeout: requestTimeout,
      allowsInsecureLoopback: allowInsecureLocalhost,
    );
  }

  final Uri? baseUri;
  final Duration requestTimeout;
  final bool allowsInsecureLoopback;

  bool get isConfigured => baseUri != null;

  Uri get realtimeSessionsV4Endpoint =>
      _requireBaseUri().resolve('v4/realtime/sessions');

  Uri canvasRestoreEndpoint(String sessionId) => _requireBaseUri().resolve(
        'v4/realtime/sessions/$sessionId/canvas-restore',
      );

  Uri weeklyRecapEndpoint(String timezoneId) {
    return _requireBaseUri().resolve('v3/memory/weekly-recap').replace(
      queryParameters: {'timezoneId': timezoneId},
    );
  }

  Uri get resetConversationsEndpoint =>
      _requireBaseUri().resolve('v3/memory/conversations');

  Uri _requireBaseUri() {
    final base = baseUri;
    if (base == null) {
      throw StateError(
        'Ava model backend is not configured. Start the app with '
        '--dart-define=AVA_API_BASE_URL=https://your-backend.example/.',
      );
    }
    return base;
  }
}
