import 'package:flutter/material.dart';

abstract final class AvaColors {
  static const background = Color(0xFFBFC7C7);
  static const backgroundLight = Color(0xFFD8DEDD);
  static const backgroundDeep = Color(0xFFAAB6B5);

  static const ink = Color(0xFF263238);
  static const inkSoft = Color(0xFF5E6B6D);
  static const inkFaint = Color(0xFF879193);

  static const paper = Color(0xFFFFFCF7);
  static const paperMuted = Color(0xFFF3F1EB);
  static const white = Color(0xFFFFFFFF);
  static const line = Color(0xFFDCE2DE);

  static const teal = Color(0xFF4F7D79);
  static const tealSoft = Color(0xFFDCEAE6);
  static const coral = Color(0xFFE9788C);
  static const coralSoft = Color(0xFFFBE3E7);
  static const gold = Color(0xFFE4A84B);
  static const goldSoft = Color(0xFFF9ECD2);
  static const blue = Color(0xFF6D8EBA);
  static const blueSoft = Color(0xFFE1E9F3);
  static const danger = Color(0xFFA74750);
}

abstract final class AvaRadii {
  static const control = 15.0;
  static const card = 22.0;
  static const sheet = 30.0;
}

abstract final class AvaDurations {
  static const quick = Duration(milliseconds: 180);
  static const standard = Duration(milliseconds: 320);
  static const sheet = Duration(milliseconds: 420);
}

ThemeData buildAvaTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: AvaColors.teal,
    brightness: Brightness.light,
    primary: AvaColors.teal,
    secondary: AvaColors.coral,
    surface: AvaColors.paper,
    error: AvaColors.danger,
  );

  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.light,
    colorScheme: scheme,
    scaffoldBackgroundColor: AvaColors.background,
    textTheme: const TextTheme(
      headlineSmall: TextStyle(
        color: AvaColors.ink,
        fontSize: 24,
        height: 1.08,
        letterSpacing: -0.45,
        fontWeight: FontWeight.w800,
      ),
      titleLarge: TextStyle(
        color: AvaColors.ink,
        fontSize: 20,
        height: 1.12,
        letterSpacing: -0.25,
        fontWeight: FontWeight.w800,
      ),
      titleMedium: TextStyle(
        color: AvaColors.ink,
        fontSize: 16,
        height: 1.25,
        fontWeight: FontWeight.w700,
      ),
      bodyLarge: TextStyle(
        color: AvaColors.ink,
        fontSize: 16,
        height: 1.4,
      ),
      bodyMedium: TextStyle(
        color: AvaColors.inkSoft,
        fontSize: 14,
        height: 1.4,
      ),
      labelLarge: TextStyle(
        color: AvaColors.ink,
        fontSize: 14,
        fontWeight: FontWeight.w700,
      ),
    ),
    dividerColor: AvaColors.line,
    splashFactory: InkSparkle.splashFactory,
    materialTapTargetSize: MaterialTapTargetSize.padded,
    visualDensity: VisualDensity.standard,
  );
}
