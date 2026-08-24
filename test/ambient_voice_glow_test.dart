import 'package:ava/ui/widgets/ambient_voice_glow.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('AmbientVoiceGlow renders child and toggles glow paint',
      (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: AmbientVoiceGlow(
            active: false,
            child: Text('Ava Voice Viewport'),
          ),
        ),
      ),
    );

    expect(find.text('Ava Voice Viewport'), findsOneWidget);

    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: AmbientVoiceGlow(
            active: true,
            child: Text('Ava Voice Viewport'),
          ),
        ),
      ),
    );

    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('Ava Voice Viewport'), findsOneWidget);
    expect(find.byType(CustomPaint), findsWidgets);
  });
}
