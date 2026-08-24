import 'dart:collection';

import 'package:flutter/foundation.dart';

import '../models/ava_realtime_protocol.dart';
import '../models/ava_realtime_protocol_v4.dart';

enum AvaRealtimeViseme { aa, ih, ou, ee, oh, closed }

enum AvaRealtimePresentationIssueCode {
  canvasRejected,
  interactionRejected,
  memoryUnavailable,
  unknownPhoneme,
}

@immutable
final class AvaRealtimePresentationIssue {
  const AvaRealtimePresentationIssue({
    required this.code,
    required this.message,
    this.failureId,
    this.component,
  });

  final AvaRealtimePresentationIssueCode code;
  final String message;
  final String? failureId;
  final String? component;
}

@immutable
final class AvaRealtimeVisemeCue {
  const AvaRealtimeVisemeCue({
    required this.viseme,
    required this.sampleStart,
    required this.sampleEnd,
    required this.weight,
  });

  final AvaRealtimeViseme viseme;
  final int sampleStart;
  final int sampleEnd;
  final double weight;
}

@immutable
final class AvaRealtimeLipSyncTimeline {
  AvaRealtimeLipSyncTimeline({
    required this.sampleRateHz,
    required this.durationSampleCount,
    required this.renderedSampleCount,
    required List<AvaRealtimeVisemeCue> cues,
  }) : cues = List.unmodifiable(cues);

  final int sampleRateHz;
  final int durationSampleCount;
  final int renderedSampleCount;
  final List<AvaRealtimeVisemeCue> cues;
}

@immutable
final class AvaRealtimePresentationState {
  const AvaRealtimePresentationState({
    required this.revision,
    required this.sessionId,
    required this.turnId,
    required this.phase,
    required this.caption,
    required this.currentSegment,
    required this.renderedSampleCount,
    required this.receivedSampleCount,
    required this.lipSync,
    required this.issues,
    required this.deliveredText,
    required this.failureMessage,
    required this.failureCode,
    required this.failureRetryable,
    required this.failureId,
    required this.failureComponent,
    required this.canvasApplied,
    required this.canvasRejected,
    required this.memoryWarning,
  });

  const AvaRealtimePresentationState.idle()
      : revision = 0,
        sessionId = null,
        turnId = null,
        phase = null,
        caption = null,
        currentSegment = null,
        renderedSampleCount = 0,
        receivedSampleCount = 0,
        lipSync = null,
        issues = const [],
        deliveredText = null,
        failureMessage = null,
        failureCode = null,
        failureRetryable = false,
        failureId = null,
        failureComponent = null,
        canvasApplied = false,
        canvasRejected = false,
        memoryWarning = null;

  final int revision;
  final String? sessionId;
  final String? turnId;
  final AvaRealtimeTurnPhase? phase;
  final String? caption;
  final AvaRealtimeTurnSegment? currentSegment;
  final int renderedSampleCount;
  final int receivedSampleCount;
  final AvaRealtimeLipSyncTimeline? lipSync;
  final List<AvaRealtimePresentationIssue> issues;
  final String? deliveredText;
  final String? failureMessage;
  final String? failureCode;
  final bool failureRetryable;
  final String? failureId;
  final String? failureComponent;
  final bool canvasApplied;
  final bool canvasRejected;
  final String? memoryWarning;

  bool get isActive =>
      turnId != null &&
      phase != AvaRealtimeTurnPhase.completed &&
      phase != AvaRealtimeTurnPhase.cancelled &&
      phase != AvaRealtimeTurnPhase.failed;
}

/// Long-lived Flutter presentation state for one protocol-v4 turn at a time.
///
/// Network events update this controller, but animation frames do not. Caption
/// and viseme positions are derived only from the native rendered-sample clock.
final class AvaRealtimePresentationController extends ChangeNotifier {
  AvaRealtimePresentationState _state =
      const AvaRealtimePresentationState.idle();
  final List<AvaRealtimeTurnSegment> _segments = [];
  final Map<String, AvaRealtimeTurnSegment> _segmentsById = {};
  final List<AvaRealtimeAlignmentSpan> _spans = [];
  final Set<String> _reportedUnknownPhonemes = {};

  AvaRealtimePresentationState get state => _state;
  UnmodifiableListView<AvaRealtimeTurnSegment> get segments =>
      UnmodifiableListView(_segments);
  UnmodifiableListView<AvaRealtimeAlignmentSpan> get alignmentSpans =>
      UnmodifiableListView(_spans);

  void beginTurn({
    required String sessionId,
    required String turnId,
  }) {
    _segments.clear();
    _segmentsById.clear();
    _spans.clear();
    _reportedUnknownPhonemes.clear();
    _replace(
      sessionId: sessionId,
      turnId: turnId,
      phase: AvaRealtimeTurnPhase.created,
      caption: null,
      currentSegment: null,
      renderedSampleCount: 0,
      receivedSampleCount: 0,
      lipSync: null,
      issues: const [],
      deliveredText: null,
      failureMessage: null,
      failureCode: null,
      failureRetryable: false,
      failureId: null,
      failureComponent: null,
      canvasApplied: false,
      canvasRejected: false,
      memoryWarning: null,
    );
  }

  void setPhase(AvaRealtimeTurnPhase phase) {
    _requireActiveTurn();
    _replace(phase: phase);
  }

  void addSegment(int index, AvaRealtimeTurnSegment segment) {
    _requireActiveTurn();
    if (index != _segments.length) {
      throw StateError(
        'Expected realtime presentation segment ${_segments.length}, '
        'received $index.',
      );
    }
    if (_segmentsById.containsKey(segment.id)) {
      throw StateError(
        'Realtime presentation segment ${segment.id} was registered twice.',
      );
    }
    _segments.add(segment);
    _segmentsById[segment.id] = segment;
    _replace(
      currentSegment: _state.currentSegment ?? segment,
    );
  }

  void addAlignmentSpans(List<AvaRealtimeAlignmentSpan> spans) {
    _requireActiveTurn();
    if (_spans.length + spans.length > 3072) {
      throw StateError(
        'Realtime presentation alignment exceeds the protocol-v3 bound.',
      );
    }
    for (final span in spans) {
      final segment = _segmentsById[span.segmentId];
      if (segment == null) {
        throw StateError(
          'Alignment references unknown segment ${span.segmentId}.',
        );
      }
      if (span.textEnd > segment.speech.runes.length) {
        throw StateError(
          'Alignment exceeds the text for segment ${span.segmentId}.',
        );
      }
      if (_spans case [..., final previous]) {
        if (span.sampleStart < previous.sampleEnd) {
          throw StateError('Realtime alignment sample ranges overlap.');
        }
        if (span.segmentId == previous.segmentId &&
            span.textStart < previous.textEnd) {
          throw StateError('Realtime alignment text ranges overlap.');
        }
        final previousIndex =
            _segments.indexOf(_segmentsById[previous.segmentId]!);
        final currentIndex = _segments.indexOf(segment);
        if (currentIndex < previousIndex) {
          throw StateError(
            'Realtime alignment returned to an earlier segment.',
          );
        }
      }
      _spans.add(span);
    }
    updatePlaybackClock(
      renderedSampleCount: _state.renderedSampleCount,
      receivedSampleCount: _state.receivedSampleCount,
    );
  }

  void updatePlaybackClock({
    required int renderedSampleCount,
    required int receivedSampleCount,
  }) {
    _requireActiveTurn();
    if (renderedSampleCount < 0 ||
        receivedSampleCount < 0 ||
        renderedSampleCount > receivedSampleCount) {
      throw StateError(
        'Realtime presentation received invalid playback sample counts.',
      );
    }

    AvaRealtimeAlignmentSpan? deliveredSpan;
    for (final span in _spans) {
      if (span.sampleStart > renderedSampleCount) break;
      deliveredSpan = span;
    }
    final currentSegment = deliveredSpan == null
        ? (_segments.isEmpty ? null : _segments.first)
        : _segmentsById[deliveredSpan.segmentId];
    const captionLingerSamples = 24000 * 3 ~/ 2;
    final caption = deliveredSpan == null ||
            currentSegment == null ||
            (renderedSampleCount >
                deliveredSpan.sampleEnd + captionLingerSamples)
        ? null
        : _captionLineAt(
            currentSegment.speech,
            deliveredSpan.textStart,
          );

    final issues = List<AvaRealtimePresentationIssue>.of(_state.issues);
    final cues = <AvaRealtimeVisemeCue>[];
    for (final span in _spans) {
      final mapping = _mapPhoneme(span.phoneme);
      if (!mapping.recognized &&
          _reportedUnknownPhonemes.add(span.phoneme ?? '<null>')) {
        issues.add(
          AvaRealtimePresentationIssue(
            code: AvaRealtimePresentationIssueCode.unknownPhoneme,
            message: span.phoneme == null
                ? 'A null phoneme was mapped explicitly to a closed mouth.'
                : 'Unsupported phoneme "${span.phoneme}" was mapped '
                    'explicitly to a closed mouth.',
          ),
        );
      }
      cues.add(
        AvaRealtimeVisemeCue(
          viseme: mapping.viseme,
          sampleStart: span.sampleStart,
          sampleEnd: span.sampleEnd,
          weight: mapping.viseme == AvaRealtimeViseme.closed ? 0.22 : 0.78,
        ),
      );
    }
    final durationSamples = cues.isEmpty
        ? receivedSampleCount
        : [
            receivedSampleCount,
            cues.last.sampleEnd,
          ].reduce((left, right) => left > right ? left : right);
    final lipSync = cues.isEmpty || durationSamples <= 0
        ? null
        : AvaRealtimeLipSyncTimeline(
            sampleRateHz: 24000,
            durationSampleCount: durationSamples,
            renderedSampleCount: renderedSampleCount,
            cues: cues,
          );

    _replace(
      caption: caption,
      currentSegment: currentSegment,
      renderedSampleCount: renderedSampleCount,
      receivedSampleCount: receivedSampleCount,
      lipSync: lipSync,
      issues: issues,
    );
  }

  void recordCanvasRejection(
    String message, {
    String? failureId,
    String? component,
  }) {
    _requireActiveTurn();
    _replace(
      canvasRejected: true,
      issues: [
        ..._state.issues,
        AvaRealtimePresentationIssue(
          code: AvaRealtimePresentationIssueCode.canvasRejected,
          message: message,
          failureId: failureId,
          component: component,
        ),
      ],
    );
  }

  void recordCanvasApplied() {
    _requireActiveTurn();
    _replace(canvasApplied: true, canvasRejected: false);
  }

  void recordCanvasInteractionRejection(String message) {
    _replace(
      issues: [
        ..._state.issues,
        AvaRealtimePresentationIssue(
          code: AvaRealtimePresentationIssueCode.interactionRejected,
          message: message,
          component: 'canvasSession',
        ),
      ],
    );
  }

  void recordMemoryPersistenceFailure(AvaRealtimeFailure failure) {
    _requireActiveTurn();
    if (failure.code != 'memoryUnavailable' || !failure.retryable) {
      throw const FormatException(
        'Memory persistence warnings must be retryable memoryUnavailable '
        'failures.',
      );
    }
    _replace(
      memoryWarning: failure.message,
      issues: [
        ..._state.issues,
        AvaRealtimePresentationIssue(
          code: AvaRealtimePresentationIssueCode.memoryUnavailable,
          message: failure.message,
          failureId: failure.failureId,
          component: failure.component,
        ),
      ],
    );
  }

  void complete(AvaRealtimeServerEventV4 event) {
    if (!event.isTerminal) {
      throw ArgumentError.value(
        event.type,
        'event',
        'must be terminal',
      );
    }
    _requireActiveTurn();
    _replace(
      phase: switch (event.type) {
        AvaRealtimeServerEventTypeV4.turnCompleted =>
          AvaRealtimeTurnPhase.completed,
        AvaRealtimeServerEventTypeV4.turnCancelled =>
          AvaRealtimeTurnPhase.cancelled,
        AvaRealtimeServerEventTypeV4.turnFailed => AvaRealtimeTurnPhase.failed,
        _ => throw StateError('Unexpected non-terminal event.'),
      },
      caption: null,
      lipSync: null,
      deliveredText: event.deliveredText,
      failureMessage: event.failure?.message,
      failureCode: event.failure?.code,
      failureRetryable: event.failure?.retryable ?? false,
      failureId: event.failure?.failureId,
      failureComponent: event.failure?.component,
    );
  }

  void failLocally(Object error) {
    _replace(
      phase: AvaRealtimeTurnPhase.failed,
      caption: null,
      lipSync: null,
      failureMessage: error.toString(),
      failureCode: 'localTransportFailure',
      failureRetryable: true,
      failureId: null,
      failureComponent: 'protocol',
    );
  }

  void reset() {
    if (_state.isActive) {
      throw StateError('Cannot reset an active realtime presentation turn.');
    }
    _segments.clear();
    _segmentsById.clear();
    _spans.clear();
    _reportedUnknownPhonemes.clear();
    _state = AvaRealtimePresentationState(
      revision: _state.revision + 1,
      sessionId: null,
      turnId: null,
      phase: null,
      caption: null,
      currentSegment: null,
      renderedSampleCount: 0,
      receivedSampleCount: 0,
      lipSync: null,
      issues: const [],
      deliveredText: null,
      failureMessage: null,
      failureCode: null,
      failureRetryable: false,
      failureId: null,
      failureComponent: null,
      canvasApplied: false,
      canvasRejected: false,
      memoryWarning: null,
    );
    notifyListeners();
  }

  void _replace({
    String? sessionId,
    String? turnId,
    AvaRealtimeTurnPhase? phase,
    Object? caption = _unchanged,
    Object? currentSegment = _unchanged,
    int? renderedSampleCount,
    int? receivedSampleCount,
    Object? lipSync = _unchanged,
    List<AvaRealtimePresentationIssue>? issues,
    Object? deliveredText = _unchanged,
    Object? failureMessage = _unchanged,
    Object? failureCode = _unchanged,
    bool? failureRetryable,
    Object? failureId = _unchanged,
    Object? failureComponent = _unchanged,
    bool? canvasApplied,
    bool? canvasRejected,
    Object? memoryWarning = _unchanged,
  }) {
    _state = AvaRealtimePresentationState(
      revision: _state.revision + 1,
      sessionId: sessionId ?? _state.sessionId,
      turnId: turnId ?? _state.turnId,
      phase: phase ?? _state.phase,
      caption:
          identical(caption, _unchanged) ? _state.caption : caption as String?,
      currentSegment: identical(currentSegment, _unchanged)
          ? _state.currentSegment
          : currentSegment as AvaRealtimeTurnSegment?,
      renderedSampleCount: renderedSampleCount ?? _state.renderedSampleCount,
      receivedSampleCount: receivedSampleCount ?? _state.receivedSampleCount,
      lipSync: identical(lipSync, _unchanged)
          ? _state.lipSync
          : lipSync as AvaRealtimeLipSyncTimeline?,
      issues: List.unmodifiable(issues ?? _state.issues),
      deliveredText: identical(deliveredText, _unchanged)
          ? _state.deliveredText
          : deliveredText as String?,
      failureMessage: identical(failureMessage, _unchanged)
          ? _state.failureMessage
          : failureMessage as String?,
      failureCode: identical(failureCode, _unchanged)
          ? _state.failureCode
          : failureCode as String?,
      failureRetryable: failureRetryable ?? _state.failureRetryable,
      failureId: identical(failureId, _unchanged)
          ? _state.failureId
          : failureId as String?,
      failureComponent: identical(failureComponent, _unchanged)
          ? _state.failureComponent
          : failureComponent as String?,
      canvasApplied: canvasApplied ?? _state.canvasApplied,
      canvasRejected: canvasRejected ?? _state.canvasRejected,
      memoryWarning: identical(memoryWarning, _unchanged)
          ? _state.memoryWarning
          : memoryWarning as String?,
    );
    notifyListeners();
  }

  void _requireActiveTurn() {
    if (!_state.isActive) {
      throw StateError('Ava has no active realtime presentation turn.');
    }
  }
}

const Object _unchanged = Object();

@visibleForTesting
String captionLineAt(
  String speech,
  int scalarOffset, {
  int maximumScalarCount = 38,
}) =>
    _captionLineAt(
      speech,
      scalarOffset,
      maximumScalarCount: maximumScalarCount,
    );

String _captionLineAt(
  String speech,
  int scalarOffset, {
  int maximumScalarCount = 38,
}) {
  final scalars = speech.runes.toList(growable: false);
  if (scalars.isEmpty || scalarOffset < 0 || scalarOffset >= scalars.length) {
    throw RangeError.range(
      scalarOffset,
      0,
      scalars.isEmpty ? 0 : scalars.length - 1,
      'scalarOffset',
    );
  }
  if (maximumScalarCount < 12 || maximumScalarCount > 80) {
    throw RangeError.range(
      maximumScalarCount,
      12,
      80,
      'maximumScalarCount',
    );
  }

  final words = <({int start, int end})>[];
  var cursor = 0;
  while (cursor < scalars.length) {
    while (cursor < scalars.length && _isCaptionWhitespace(scalars[cursor])) {
      cursor += 1;
    }
    if (cursor >= scalars.length) break;
    final start = cursor;
    while (cursor < scalars.length && !_isCaptionWhitespace(scalars[cursor])) {
      cursor += 1;
    }
    words.add((start: start, end: cursor));
  }

  var lineStart = words.first.start;
  var lineEnd = words.first.end;
  for (var index = 0; index < words.length; index += 1) {
    final word = words[index];
    final proposedLength = word.end - lineStart;
    if (index > 0 && proposedLength > maximumScalarCount) {
      if (scalarOffset < word.start) {
        return String.fromCharCodes(scalars.sublist(lineStart, lineEnd)).trim();
      }
      lineStart = word.start;
    }
    lineEnd = word.end;
    final endsSentence = _captionSentenceEnd.contains(scalars[word.end - 1]);
    if (endsSentence || index == words.length - 1) {
      if (scalarOffset < word.end || index == words.length - 1) {
        return String.fromCharCodes(scalars.sublist(lineStart, lineEnd)).trim();
      }
      if (index + 1 < words.length) {
        lineStart = words[index + 1].start;
      }
    }
  }
  throw StateError('Ava could not resolve a caption line for delivered text.');
}

bool _isCaptionWhitespace(int scalar) =>
    scalar == 0x20 || scalar == 0x09 || scalar == 0x0A || scalar == 0x0D;

const Set<int> _captionSentenceEnd = {0x2E, 0x21, 0x3F};

({AvaRealtimeViseme viseme, bool recognized}) _mapPhoneme(
  String? rawPhoneme,
) {
  if (rawPhoneme == null) {
    return (viseme: AvaRealtimeViseme.closed, recognized: false);
  }
  final phoneme = rawPhoneme.trim().toUpperCase().replaceAll(
        RegExp(r'[012]$'),
        '',
      );
  if (_aaPhonemes.contains(phoneme)) {
    return (viseme: AvaRealtimeViseme.aa, recognized: true);
  }
  if (_ihPhonemes.contains(phoneme)) {
    return (viseme: AvaRealtimeViseme.ih, recognized: true);
  }
  if (_ouPhonemes.contains(phoneme)) {
    return (viseme: AvaRealtimeViseme.ou, recognized: true);
  }
  if (_eePhonemes.contains(phoneme)) {
    return (viseme: AvaRealtimeViseme.ee, recognized: true);
  }
  if (_ohPhonemes.contains(phoneme)) {
    return (viseme: AvaRealtimeViseme.oh, recognized: true);
  }
  if (_closedPhonemes.contains(phoneme)) {
    return (viseme: AvaRealtimeViseme.closed, recognized: true);
  }
  return (viseme: AvaRealtimeViseme.closed, recognized: false);
}

const Set<String> _aaPhonemes = {
  'AA',
  'AE',
  'AH',
  'AX',
  'AY',
  'T',
  'D',
  'K',
  'G',
  'N',
  'NG',
  'L',
  'H',
  'HH',
  'ɑ',
  'Æ',
  'ʌ',
  'Ə',
};
const Set<String> _ihPhonemes = {
  'IH',
  'IY',
  'Y',
  'S',
  'Z',
  'SH',
  'ZH',
  'CH',
  'JH',
  'ɪ',
  'Ʊ',
};
const Set<String> _ouPhonemes = {'UW', 'UH', 'W', 'U', 'ʊ'};
const Set<String> _eePhonemes = {
  'EE',
  'EH',
  'EY',
  'F',
  'V',
  'TH',
  'DH',
  'E',
  'Ɛ',
};
const Set<String> _ohPhonemes = {'AO', 'AW', 'OW', 'OY', 'R', 'OH', 'Ɔ', 'O'};
const Set<String> _closedPhonemes = {
  'P',
  'B',
  'M',
  'SIL',
  'SP',
  'PAUSE',
  'CLOSED',
};
