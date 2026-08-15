import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'ui/design/ava_design.dart';
import 'ui/main_screen.dart';

void main() {
  // Ensure Flutter engine features are initialised
  WidgetsFlutterBinding.ensureInitialized();

  runApp(
    const ProviderScope(
      child: AvaTeacherApp(),
    ),
  );
}

class AvaTeacherApp extends StatelessWidget {
  const AvaTeacherApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Ava - Interactive AI Character Teacher',
      debugShowCheckedModeBanner: false,
      theme: buildAvaTheme(),
      home: const MainScreen(),
    );
  }
}
