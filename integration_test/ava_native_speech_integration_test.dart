import 'package:ava/services/speech_input_service.dart';
import 'package:ava/services/speech_output_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Apple speech channels accept production stop and TTS commands',
      (tester) async {
    const input = SystemSpeechInputService();
    const output = SystemSpeechOutputService();

    await input.stop();
    await output.speak(
      'Ava voice system is ready.',
      volume: 0.35,
    );
  });
}
