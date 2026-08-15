enum DiagramKind {
  fractionCircle,
  fractionBar,
  numberLine,
}

sealed class DiagramSpec {
  const DiagramSpec({
    required this.kind,
    required this.title,
  });

  const factory DiagramSpec.fractionCircle({
    required String title,
    required int totalParts,
    required int highlightedParts,
  }) = FractionCircleDiagramSpec;

  const factory DiagramSpec.fractionBar({
    required String title,
    required int totalParts,
    required int highlightedParts,
  }) = FractionBarDiagramSpec;

  const factory DiagramSpec.numberLine({
    required String title,
    required int minimum,
    required int maximum,
    required int step,
    required int start,
    required int target,
  }) = NumberLineDiagramSpec;

  final DiagramKind kind;
  final String title;

  factory DiagramSpec.fromJson(Map<String, Object?> json) {
    final kind = _enumByName(
      DiagramKind.values,
      _requiredString(json, 'kind', 'diagram'),
      'diagram.kind',
    );
    final spec = switch (kind) {
      DiagramKind.fractionCircle => _fractionFromJson(
          json,
          kind: DiagramKind.fractionCircle,
        ),
      DiagramKind.fractionBar => _fractionFromJson(
          json,
          kind: DiagramKind.fractionBar,
        ),
      DiagramKind.numberLine => NumberLineDiagramSpec.fromJson(json),
    };
    spec.validate();
    return spec;
  }

  Map<String, Object?> toJson();

  void validate() {
    final normalizedTitle = title.trim();
    if (normalizedTitle.isEmpty) {
      throw const FormatException('A diagram title cannot be empty.');
    }
    if (normalizedTitle.runes.length > 120) {
      throw const FormatException(
        'A diagram title cannot exceed 120 characters.',
      );
    }
  }

  static FractionDiagramSpec _fractionFromJson(
    Map<String, Object?> json, {
    required DiagramKind kind,
  }) {
    _requireExactKeys(
      json,
      const {'kind', 'title', 'totalParts', 'highlightedParts'},
      'diagram',
    );
    final title = _requiredString(json, 'title', 'diagram');
    final totalParts = _requiredInt(json, 'totalParts', 'diagram');
    final highlightedParts = _requiredInt(
      json,
      'highlightedParts',
      'diagram',
    );
    return switch (kind) {
      DiagramKind.fractionCircle => FractionCircleDiagramSpec(
          title: title,
          totalParts: totalParts,
          highlightedParts: highlightedParts,
        ),
      DiagramKind.fractionBar => FractionBarDiagramSpec(
          title: title,
          totalParts: totalParts,
          highlightedParts: highlightedParts,
        ),
      DiagramKind.numberLine => throw StateError(
          'Number-line diagrams cannot use fraction fields.',
        ),
    };
  }
}

sealed class FractionDiagramSpec extends DiagramSpec {
  const FractionDiagramSpec({
    required super.kind,
    required super.title,
    required this.totalParts,
    required this.highlightedParts,
  });

  final int totalParts;
  final int highlightedParts;

  FractionDiagramSpec withHighlightedParts(int value);

  @override
  Map<String, Object?> toJson() => {
        'kind': kind.name,
        'title': title,
        'totalParts': totalParts,
        'highlightedParts': highlightedParts,
      };

  @override
  void validate() {
    super.validate();
    if (totalParts < 2 || totalParts > 12) {
      throw FormatException(
        'Fraction diagrams support between 2 and 12 parts, got $totalParts.',
      );
    }
    if (highlightedParts < 0 || highlightedParts > totalParts) {
      throw FormatException(
        'Highlighted parts must be between 0 and $totalParts, got '
        '$highlightedParts.',
      );
    }
  }
}

final class FractionCircleDiagramSpec extends FractionDiagramSpec {
  const FractionCircleDiagramSpec({
    required super.title,
    required super.totalParts,
    required super.highlightedParts,
  }) : super(kind: DiagramKind.fractionCircle);

  @override
  FractionCircleDiagramSpec withHighlightedParts(int value) {
    return FractionCircleDiagramSpec(
      title: title,
      totalParts: totalParts,
      highlightedParts: value,
    );
  }
}

final class FractionBarDiagramSpec extends FractionDiagramSpec {
  const FractionBarDiagramSpec({
    required super.title,
    required super.totalParts,
    required super.highlightedParts,
  }) : super(kind: DiagramKind.fractionBar);

  @override
  FractionBarDiagramSpec withHighlightedParts(int value) {
    return FractionBarDiagramSpec(
      title: title,
      totalParts: totalParts,
      highlightedParts: value,
    );
  }
}

final class NumberLineDiagramSpec extends DiagramSpec {
  const NumberLineDiagramSpec({
    required super.title,
    required this.minimum,
    required this.maximum,
    required this.step,
    required this.start,
    required this.target,
  }) : super(kind: DiagramKind.numberLine);

  final int minimum;
  final int maximum;
  final int step;
  final int start;
  final int target;

  NumberLineDiagramSpec withStart(int value) {
    return NumberLineDiagramSpec(
      title: title,
      minimum: minimum,
      maximum: maximum,
      step: step,
      start: value,
      target: target,
    );
  }

  factory NumberLineDiagramSpec.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {'kind', 'title', 'minimum', 'maximum', 'step', 'start', 'target'},
      'diagram',
    );
    return NumberLineDiagramSpec(
      title: _requiredString(json, 'title', 'diagram'),
      minimum: _requiredInt(json, 'minimum', 'diagram'),
      maximum: _requiredInt(json, 'maximum', 'diagram'),
      step: _requiredInt(json, 'step', 'diagram'),
      start: _requiredInt(json, 'start', 'diagram'),
      target: _requiredInt(json, 'target', 'diagram'),
    );
  }

  @override
  Map<String, Object?> toJson() => {
        'kind': kind.name,
        'title': title,
        'minimum': minimum,
        'maximum': maximum,
        'step': step,
        'start': start,
        'target': target,
      };

  @override
  void validate() {
    super.validate();
    if (minimum < -100 || maximum > 100 || minimum >= maximum) {
      throw const FormatException(
        'A number line requires -100 <= minimum < maximum <= 100.',
      );
    }
    if (step < 1 || step > 20) {
      throw const FormatException(
        'A number-line step must be between 1 and 20.',
      );
    }
    final span = maximum - minimum;
    if (span % step != 0 || span ~/ step > 20) {
      throw const FormatException(
        'A number line must contain at most 21 evenly spaced values.',
      );
    }
    _validateValue(start, 'start');
    _validateValue(target, 'target');
  }

  void _validateValue(int value, String name) {
    if (value < minimum || value > maximum || (value - minimum) % step != 0) {
      throw FormatException(
        'Number-line $name must be an aligned value between $minimum and '
        '$maximum, got $value.',
      );
    }
  }
}

void _requireExactKeys(
  Map<String, Object?> source,
  Set<String> allowed,
  String context,
) {
  final unknown = source.keys.toSet().difference(allowed);
  if (unknown.isNotEmpty) {
    throw FormatException(
      '$context contains unsupported fields: ${unknown.join(', ')}.',
    );
  }
  final missing = allowed.difference(source.keys.toSet());
  if (missing.isNotEmpty) {
    throw FormatException(
      '$context is missing required fields: ${missing.join(', ')}.',
    );
  }
}

String _requiredString(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('$context.$key must be a non-empty string.');
  }
  return value;
}

int _requiredInt(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! int) {
    throw FormatException('$context.$key must be an integer.');
  }
  return value;
}

T _enumByName<T extends Enum>(
  List<T> values,
  String raw,
  String context,
) {
  final matches = values.where((value) => value.name == raw);
  if (matches.length != 1) {
    throw FormatException('$context has unsupported value "$raw".');
  }
  return matches.single;
}
