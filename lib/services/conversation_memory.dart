import '../models/lesson_response.dart';
import 'memory_service.dart';

abstract interface class ConversationMemory {
  Future<List<ConversationMessage>> recentMessages({int limit = 8});

  Future<void> recordTurn({
    required String childMessage,
    required String avaMessage,
  });
}

class DriftConversationMemory implements ConversationMemory {
  const DriftConversationMemory(this._database);

  final AppDatabase _database;

  @override
  Future<List<ConversationMessage>> recentMessages({int limit = 8}) async {
    final storedMessages = await _database.getMessages(limit: limit);
    return storedMessages.reversed.map((message) {
      final role = switch (message.speaker) {
        'child' => ConversationRole.child,
        'ava' => ConversationRole.ava,
        final unsupported => throw FormatException(
            'Unsupported conversation speaker in memory: $unsupported',
          ),
      };
      return ConversationMessage(role: role, text: message.textContent);
    }).toList(growable: false);
  }

  @override
  Future<void> recordTurn({
    required String childMessage,
    required String avaMessage,
  }) async {
    await _database.recordConversationTurn(
      childMessage: childMessage,
      avaMessage: avaMessage,
    );
  }
}
