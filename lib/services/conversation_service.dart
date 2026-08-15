import '../models/lesson_response.dart';

abstract interface class ConversationService {
  Future<LessonResponse> respond(ConversationRequest request);
}
