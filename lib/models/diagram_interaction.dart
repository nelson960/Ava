enum DiagramInteractionKind {
  valueChanged,
  completed,
  reset,
}

class DiagramInteraction {
  const DiagramInteraction({
    required this.kind,
    required this.currentValue,
    required this.targetValue,
  });

  final DiagramInteractionKind kind;
  final int currentValue;
  final int? targetValue;

  bool get isComplete =>
      kind == DiagramInteractionKind.completed &&
      targetValue != null &&
      currentValue == targetValue;
}
