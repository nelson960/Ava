import 'dart:io';
import 'package:drift/drift.dart';
import 'package:drift/native.dart';
import 'package:path_provider/path_provider.dart';
import 'package:path/path.dart' as p;

part 'memory_service.g.dart';

@DataClassName('StudentProfile')
class StudentProfiles extends Table {
  TextColumn get id => text()();
  TextColumn get name => text().withLength(min: 1, max: 100)();
  DateTimeColumn get dateOfBirth => dateTime().nullable()();
  BoolColumn get captionsEnabled =>
      boolean().withDefault(const Constant(false))();
  IntColumn get mathScore => integer().withDefault(const Constant(0))();
  IntColumn get scienceScore => integer().withDefault(const Constant(0))();
  DateTimeColumn get lastActive => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

@DataClassName('HistoryMessage')
class SessionHistory extends Table {
  IntColumn get id => integer().autoIncrement()();
  TextColumn get speaker => text()(); // 'child' or 'ava'
  TextColumn get textContent => text()();
  DateTimeColumn get timestamp => dateTime()();
}

@DataClassName('StoredLearningProgressEvent')
class LearningProgressEvents extends Table {
  IntColumn get id => integer().autoIncrement()();
  TextColumn get profileId => text().withLength(min: 1, max: 64)();
  TextColumn get activityId => text().withLength(min: 3, max: 40)();
  TextColumn get kind => text().withLength(min: 1, max: 32)();
  TextColumn get domain => text().withLength(min: 1, max: 16)();
  TextColumn get topic => text().withLength(min: 1, max: 80)();
  TextColumn get diagramKind => text().withLength(min: 1, max: 32)();
  IntColumn get currentValue => integer().nullable()();
  IntColumn get targetValue => integer().nullable()();
  DateTimeColumn get occurredAt => dateTime()();
}

@DataClassName('StoredCanvasScene')
class CanvasScenes extends Table {
  TextColumn get sceneId => text().withLength(min: 1, max: 96)();
  IntColumn get sceneRevision => integer()();
  TextColumn get generationId => text().withLength(min: 1, max: 96)();
  TextColumn get sourceTurnId => text().withLength(min: 1, max: 96)();
  BlobColumn get svgBytes => blob()();
  BlobColumn get manifestBytes => blob()();
  TextColumn get svgSha256 => text().withLength(min: 64, max: 64)();
  TextColumn get manifestSha256 => text().withLength(min: 64, max: 64)();
  IntColumn get svgByteLength => integer()();
  IntColumn get manifestByteLength => integer()();
  TextColumn get rendererCapabilityVersion => text()();
  TextColumn get validatorVersion => text()();
  TextColumn get manifestVersion => text()();
  IntColumn get workspaceOrder => integer()();
  BoolColumn get isFocused => boolean()();
  IntColumn get creationSequence => integer().nullable()();
  IntColumn get modificationSequence => integer().nullable()();
  TextColumn get title => text().nullable()();
  TextColumn get summary => text().nullable()();

  @override
  Set<Column> get primaryKey => {sceneId};
}

@DataClassName('StoredCanvasWorkspace')
class CanvasWorkspaces extends Table {
  IntColumn get id => integer()();
  IntColumn get workspaceEpoch => integer()();
  IntColumn get workspaceRevision => integer()();
  IntColumn get nextCreationSequence => integer()();
  IntColumn get nextModificationSequence => integer()();

  @override
  Set<Column> get primaryKey => {id};
}

@DriftDatabase(
  tables: [
    StudentProfiles,
    SessionHistory,
    LearningProgressEvents,
    CanvasScenes,
    CanvasWorkspaces,
  ],
)
class AppDatabase extends _$AppDatabase {
  AppDatabase() : super(_openConnection());

  AppDatabase.forTesting(super.executor);

  @override
  int get schemaVersion => 5;

  @override
  MigrationStrategy get migration => MigrationStrategy(
        onCreate: (migrator) => migrator.createAll(),
        onUpgrade: (migrator, from, to) async {
          if (from < 2) {
            await migrator.addColumn(
              studentProfiles,
              studentProfiles.dateOfBirth,
            );
            await migrator.addColumn(
              studentProfiles,
              studentProfiles.captionsEnabled,
            );
          }
          if (from < 3) {
            await migrator.createTable(learningProgressEvents);
          }
          if (from < 4) {
            await migrator.createTable(canvasScenes);
          }
          if (from < 5) {
            await migrator.addColumn(
                canvasScenes, canvasScenes.creationSequence);
            await migrator.addColumn(
                canvasScenes, canvasScenes.modificationSequence);
            await migrator.addColumn(canvasScenes, canvasScenes.title);
            await migrator.addColumn(canvasScenes, canvasScenes.summary);
            await migrator.createTable(canvasWorkspaces);
            await customStatement(
              'UPDATE canvas_scenes SET creation_sequence = workspace_order + 1, '
              "title = '', summary = '' WHERE creation_sequence IS NULL",
            );
            await customStatement(
              'INSERT INTO canvas_workspaces '
              '(id, workspace_epoch, workspace_revision, next_creation_sequence, next_modification_sequence) '
              'VALUES (1, 0, 0, '
              'COALESCE((SELECT MAX(creation_sequence) + 1 FROM canvas_scenes), 1), 1)',
            );
          }
        },
      );

  Future<void> clearConversationHistory() => delete(sessionHistory).go();

  // Helper queries
  Future<StudentProfile?> getProfile(String id) {
    return (select(studentProfiles)..where((tbl) => tbl.id.equals(id)))
        .getSingleOrNull();
  }

  Future<int> insertOrUpdateProfile(StudentProfile profile) {
    return into(studentProfiles).insertOnConflictUpdate(profile);
  }

  Future<int> addMessage(String speaker, String message) {
    return into(sessionHistory).insert(
      SessionHistoryCompanion.insert(
        speaker: speaker,
        textContent: message,
        timestamp: DateTime.now(),
      ),
    );
  }

  Future<void> recordConversationTurn({
    required String childMessage,
    required String avaMessage,
  }) async {
    await transaction(() async {
      await addMessage('child', childMessage);
      await addMessage('ava', avaMessage);
    });
  }

  Future<List<HistoryMessage>> getMessages({int limit = 20}) {
    return (select(sessionHistory)
          ..orderBy([
            (t) =>
                OrderingTerm(expression: t.timestamp, mode: OrderingMode.desc)
          ])
          ..limit(limit))
        .get();
  }

  Future<int> addLearningProgressEvent(
    LearningProgressEventsCompanion event,
  ) {
    return into(learningProgressEvents).insert(event);
  }

  Stream<List<StoredLearningProgressEvent>> watchLearningProgressEvents({
    required String profileId,
    int limit = 200,
  }) {
    if (profileId.trim().isEmpty) {
      throw ArgumentError.value(
        profileId,
        'profileId',
        'The progress profile id cannot be empty.',
      );
    }
    if (limit < 1 || limit > 500) {
      throw RangeError.range(limit, 1, 500, 'limit');
    }
    return (select(learningProgressEvents)
          ..where((event) => event.profileId.equals(profileId))
          ..orderBy([
            (event) => OrderingTerm(
                  expression: event.occurredAt,
                  mode: OrderingMode.desc,
                ),
            (event) => OrderingTerm(
                  expression: event.id,
                  mode: OrderingMode.desc,
                ),
          ])
          ..limit(limit))
        .watch();
  }
}

LazyDatabase _openConnection() {
  return LazyDatabase(() async {
    final dbFolder = await getApplicationDocumentsDirectory();
    final file = File(p.join(dbFolder.path, 'ava_memory.sqlite'));
    return NativeDatabase(file);
  });
}
