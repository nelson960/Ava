import 'dart:ui';

final class AvaCanvasPathParser {
  const AvaCanvasPathParser._();

  static const int maximumCommands = 128;
  static const int maximumNumbers = 512;

  static Path parse(String data) {
    final scanner = _PathScanner(data);
    final path = Path();
    var current = Offset.zero;
    var subpathStart = Offset.zero;
    Offset? lastCubicControl;
    Offset? lastQuadraticControl;
    String? command;
    var commandCount = 0;
    var opened = false;

    Offset point(double x, double y, bool relative) =>
        relative ? current + Offset(x, y) : Offset(x, y);

    void countCommand() {
      commandCount += 1;
      if (commandCount > maximumCommands) {
        throw const FormatException(
          'canvas path exceeds the 128-command limit.',
        );
      }
    }

    while (scanner.hasMore) {
      if (scanner.nextIsCommand) {
        command = scanner.readCommand();
      } else if (command == null || command.toLowerCase() == 'z') {
        throw FormatException(
          'canvas path expected a command at character ${scanner.index}.',
        );
      }

      final active = command;
      final relative = active == active.toLowerCase();
      switch (active.toUpperCase()) {
        case 'M':
          var first = true;
          do {
            final next = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            countCommand();
            if (first) {
              path.moveTo(next.dx, next.dy);
              subpathStart = next;
              opened = true;
              first = false;
            } else {
              path.lineTo(next.dx, next.dy);
            }
            current = next;
            lastCubicControl = null;
            lastQuadraticControl = null;
          } while (scanner.hasNumber);
          command = relative ? 'l' : 'L';
          break;
        case 'L':
          _requireOpen(opened);
          do {
            final next = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            countCommand();
            path.lineTo(next.dx, next.dy);
            current = next;
          } while (scanner.hasNumber);
          lastCubicControl = null;
          lastQuadraticControl = null;
          break;
        case 'H':
          _requireOpen(opened);
          do {
            final value = scanner.readNumber();
            final next =
                Offset(relative ? current.dx + value : value, current.dy);
            countCommand();
            path.lineTo(next.dx, next.dy);
            current = next;
          } while (scanner.hasNumber);
          lastCubicControl = null;
          lastQuadraticControl = null;
          break;
        case 'V':
          _requireOpen(opened);
          do {
            final value = scanner.readNumber();
            final next =
                Offset(current.dx, relative ? current.dy + value : value);
            countCommand();
            path.lineTo(next.dx, next.dy);
            current = next;
          } while (scanner.hasNumber);
          lastCubicControl = null;
          lastQuadraticControl = null;
          break;
        case 'C':
          _requireOpen(opened);
          do {
            final control1 = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            final control2 = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            final next = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            countCommand();
            path.cubicTo(
              control1.dx,
              control1.dy,
              control2.dx,
              control2.dy,
              next.dx,
              next.dy,
            );
            current = next;
            lastCubicControl = control2;
            lastQuadraticControl = null;
          } while (scanner.hasNumber);
          break;
        case 'S':
          _requireOpen(opened);
          do {
            final control1 = lastCubicControl == null
                ? current
                : current * 2 - lastCubicControl;
            final control2 = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            final next = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            countCommand();
            path.cubicTo(
              control1.dx,
              control1.dy,
              control2.dx,
              control2.dy,
              next.dx,
              next.dy,
            );
            current = next;
            lastCubicControl = control2;
            lastQuadraticControl = null;
          } while (scanner.hasNumber);
          break;
        case 'Q':
          _requireOpen(opened);
          do {
            final control = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            final next = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            countCommand();
            path.quadraticBezierTo(
              control.dx,
              control.dy,
              next.dx,
              next.dy,
            );
            current = next;
            lastQuadraticControl = control;
            lastCubicControl = null;
          } while (scanner.hasNumber);
          break;
        case 'T':
          _requireOpen(opened);
          do {
            final control = lastQuadraticControl == null
                ? current
                : current * 2 - lastQuadraticControl;
            final next = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            countCommand();
            path.quadraticBezierTo(
              control.dx,
              control.dy,
              next.dx,
              next.dy,
            );
            current = next;
            lastQuadraticControl = control;
            lastCubicControl = null;
          } while (scanner.hasNumber);
          break;
        case 'A':
          _requireOpen(opened);
          do {
            final radiusX = scanner.readNumber();
            final radiusY = scanner.readNumber();
            final rotation = scanner.readNumber();
            final largeArc = scanner.readFlag();
            final clockwise = scanner.readFlag();
            final next = point(
              scanner.readNumber(),
              scanner.readNumber(),
              relative,
            );
            if (radiusX < 0 || radiusY < 0) {
              throw const FormatException(
                'canvas path arc radii cannot be negative.',
              );
            }
            countCommand();
            path.arcToPoint(
              next,
              radius: Radius.elliptical(radiusX, radiusY),
              rotation: rotation,
              largeArc: largeArc,
              clockwise: clockwise,
            );
            current = next;
            lastCubicControl = null;
            lastQuadraticControl = null;
          } while (scanner.hasNumber);
          break;
        case 'Z':
          _requireOpen(opened);
          countCommand();
          path.close();
          current = subpathStart;
          lastCubicControl = null;
          lastQuadraticControl = null;
          command = null;
          break;
        default:
          throw FormatException('Unsupported canvas path command "$active".');
      }
    }

    if (!opened || commandCount == 0) {
      throw const FormatException(
          'canvas path must begin with a move command.');
    }
    final bounds = path.getBounds();
    if (!bounds.left.isFinite ||
        !bounds.top.isFinite ||
        !bounds.right.isFinite ||
        !bounds.bottom.isFinite ||
        bounds.left < -2000 ||
        bounds.top < -1200 ||
        bounds.right > 3000 ||
        bounds.bottom > 1800) {
      throw const FormatException(
          'canvas path bounds are outside safe limits.');
    }
    return path;
  }

  static void _requireOpen(bool opened) {
    if (!opened) {
      throw const FormatException(
        'canvas path must begin with a move command.',
      );
    }
  }
}

final class _PathScanner {
  _PathScanner(this.source) {
    if (source.isEmpty || source.length > 2000) {
      throw const FormatException(
        'canvas path requires 1-2000 characters.',
      );
    }
    final trimmed = source.trim();
    if (trimmed.startsWith(',') ||
        trimmed.endsWith(',') ||
        RegExp(r',\s*,').hasMatch(source) ||
        RegExp(r'[MmLlHhVvCcSsQqTtAaZz]\s*,').hasMatch(source)) {
      throw const FormatException('canvas path contains an invalid comma.');
    }
  }

  static final RegExp _number = RegExp(
    r'^[+-]?(?:(?:\d+\.\d*)|(?:\.\d+)|(?:\d+))(?:[eE][+-]?\d+)?',
  );
  static const String _commands = 'MmLlHhVvCcSsQqTtAaZz';

  final String source;
  int index = 0;
  int _numberCount = 0;

  bool get hasMore {
    _skipSeparators();
    return index < source.length;
  }

  bool get nextIsCommand {
    _skipSeparators();
    return index < source.length && _commands.contains(source[index]);
  }

  bool get hasNumber {
    _skipSeparators();
    if (index >= source.length || nextIsCommand) return false;
    return _number.firstMatch(source.substring(index)) != null;
  }

  String readCommand() {
    _skipSeparators();
    if (!nextIsCommand) {
      throw FormatException(
        'canvas path expected a command at character $index.',
      );
    }
    return source[index++];
  }

  double readNumber() {
    _skipSeparators();
    if (index >= source.length) {
      throw const FormatException('canvas path ended inside a command.');
    }
    final match = _number.firstMatch(source.substring(index));
    if (match == null) {
      throw FormatException(
        'canvas path expected a number at character $index.',
      );
    }
    final token = match.group(0)!;
    index += token.length;
    _numberCount += 1;
    if (_numberCount > AvaCanvasPathParser.maximumNumbers) {
      throw const FormatException(
        'canvas path exceeds the 512-number limit.',
      );
    }
    final result = double.parse(token);
    if (!result.isFinite || result.abs() > 100000) {
      throw const FormatException(
        'canvas path number is outside safe finite limits.',
      );
    }
    return result;
  }

  bool readFlag() {
    final value = readNumber();
    if (value != 0 && value != 1) {
      throw const FormatException('canvas path arc flags must be 0 or 1.');
    }
    return value == 1;
  }

  void _skipSeparators() {
    while (index < source.length) {
      final rune = source.codeUnitAt(index);
      if (source[index] == ',' ||
          rune == 0x20 ||
          rune == 0x09 ||
          rune == 0x0A ||
          rune == 0x0D) {
        index += 1;
      } else {
        break;
      }
    }
  }
}
