// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'memory_service.dart';

// ignore_for_file: type=lint
class $StudentProfilesTable extends StudentProfiles
    with TableInfo<$StudentProfilesTable, StudentProfile> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $StudentProfilesTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
      'id', aliasedName, false,
      type: DriftSqlType.string, requiredDuringInsert: true);
  static const VerificationMeta _nameMeta = const VerificationMeta('name');
  @override
  late final GeneratedColumn<String> name = GeneratedColumn<String>(
      'name', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 1, maxTextLength: 100),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _dateOfBirthMeta =
      const VerificationMeta('dateOfBirth');
  @override
  late final GeneratedColumn<DateTime> dateOfBirth = GeneratedColumn<DateTime>(
      'date_of_birth', aliasedName, true,
      type: DriftSqlType.dateTime, requiredDuringInsert: false);
  static const VerificationMeta _captionsEnabledMeta =
      const VerificationMeta('captionsEnabled');
  @override
  late final GeneratedColumn<bool> captionsEnabled = GeneratedColumn<bool>(
      'captions_enabled', aliasedName, false,
      type: DriftSqlType.bool,
      requiredDuringInsert: false,
      defaultConstraints: GeneratedColumn.constraintIsAlways(
          'CHECK ("captions_enabled" IN (0, 1))'),
      defaultValue: const Constant(false));
  static const VerificationMeta _mathScoreMeta =
      const VerificationMeta('mathScore');
  @override
  late final GeneratedColumn<int> mathScore = GeneratedColumn<int>(
      'math_score', aliasedName, false,
      type: DriftSqlType.int,
      requiredDuringInsert: false,
      defaultValue: const Constant(0));
  static const VerificationMeta _scienceScoreMeta =
      const VerificationMeta('scienceScore');
  @override
  late final GeneratedColumn<int> scienceScore = GeneratedColumn<int>(
      'science_score', aliasedName, false,
      type: DriftSqlType.int,
      requiredDuringInsert: false,
      defaultValue: const Constant(0));
  static const VerificationMeta _lastActiveMeta =
      const VerificationMeta('lastActive');
  @override
  late final GeneratedColumn<DateTime> lastActive = GeneratedColumn<DateTime>(
      'last_active', aliasedName, false,
      type: DriftSqlType.dateTime, requiredDuringInsert: true);
  @override
  List<GeneratedColumn> get $columns => [
        id,
        name,
        dateOfBirth,
        captionsEnabled,
        mathScore,
        scienceScore,
        lastActive
      ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'student_profiles';
  @override
  VerificationContext validateIntegrity(Insertable<StudentProfile> instance,
      {bool isInserting = false}) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('name')) {
      context.handle(
          _nameMeta, name.isAcceptableOrUnknown(data['name']!, _nameMeta));
    } else if (isInserting) {
      context.missing(_nameMeta);
    }
    if (data.containsKey('date_of_birth')) {
      context.handle(
          _dateOfBirthMeta,
          dateOfBirth.isAcceptableOrUnknown(
              data['date_of_birth']!, _dateOfBirthMeta));
    }
    if (data.containsKey('captions_enabled')) {
      context.handle(
          _captionsEnabledMeta,
          captionsEnabled.isAcceptableOrUnknown(
              data['captions_enabled']!, _captionsEnabledMeta));
    }
    if (data.containsKey('math_score')) {
      context.handle(_mathScoreMeta,
          mathScore.isAcceptableOrUnknown(data['math_score']!, _mathScoreMeta));
    }
    if (data.containsKey('science_score')) {
      context.handle(
          _scienceScoreMeta,
          scienceScore.isAcceptableOrUnknown(
              data['science_score']!, _scienceScoreMeta));
    }
    if (data.containsKey('last_active')) {
      context.handle(
          _lastActiveMeta,
          lastActive.isAcceptableOrUnknown(
              data['last_active']!, _lastActiveMeta));
    } else if (isInserting) {
      context.missing(_lastActiveMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  StudentProfile map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return StudentProfile(
      id: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}id'])!,
      name: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}name'])!,
      dateOfBirth: attachedDatabase.typeMapping
          .read(DriftSqlType.dateTime, data['${effectivePrefix}date_of_birth']),
      captionsEnabled: attachedDatabase.typeMapping
          .read(DriftSqlType.bool, data['${effectivePrefix}captions_enabled'])!,
      mathScore: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}math_score'])!,
      scienceScore: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}science_score'])!,
      lastActive: attachedDatabase.typeMapping
          .read(DriftSqlType.dateTime, data['${effectivePrefix}last_active'])!,
    );
  }

  @override
  $StudentProfilesTable createAlias(String alias) {
    return $StudentProfilesTable(attachedDatabase, alias);
  }
}

class StudentProfile extends DataClass implements Insertable<StudentProfile> {
  final String id;
  final String name;
  final DateTime? dateOfBirth;
  final bool captionsEnabled;
  final int mathScore;
  final int scienceScore;
  final DateTime lastActive;
  const StudentProfile(
      {required this.id,
      required this.name,
      this.dateOfBirth,
      required this.captionsEnabled,
      required this.mathScore,
      required this.scienceScore,
      required this.lastActive});
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    map['name'] = Variable<String>(name);
    if (!nullToAbsent || dateOfBirth != null) {
      map['date_of_birth'] = Variable<DateTime>(dateOfBirth);
    }
    map['captions_enabled'] = Variable<bool>(captionsEnabled);
    map['math_score'] = Variable<int>(mathScore);
    map['science_score'] = Variable<int>(scienceScore);
    map['last_active'] = Variable<DateTime>(lastActive);
    return map;
  }

  StudentProfilesCompanion toCompanion(bool nullToAbsent) {
    return StudentProfilesCompanion(
      id: Value(id),
      name: Value(name),
      dateOfBirth: dateOfBirth == null && nullToAbsent
          ? const Value.absent()
          : Value(dateOfBirth),
      captionsEnabled: Value(captionsEnabled),
      mathScore: Value(mathScore),
      scienceScore: Value(scienceScore),
      lastActive: Value(lastActive),
    );
  }

  factory StudentProfile.fromJson(Map<String, dynamic> json,
      {ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return StudentProfile(
      id: serializer.fromJson<String>(json['id']),
      name: serializer.fromJson<String>(json['name']),
      dateOfBirth: serializer.fromJson<DateTime?>(json['dateOfBirth']),
      captionsEnabled: serializer.fromJson<bool>(json['captionsEnabled']),
      mathScore: serializer.fromJson<int>(json['mathScore']),
      scienceScore: serializer.fromJson<int>(json['scienceScore']),
      lastActive: serializer.fromJson<DateTime>(json['lastActive']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'name': serializer.toJson<String>(name),
      'dateOfBirth': serializer.toJson<DateTime?>(dateOfBirth),
      'captionsEnabled': serializer.toJson<bool>(captionsEnabled),
      'mathScore': serializer.toJson<int>(mathScore),
      'scienceScore': serializer.toJson<int>(scienceScore),
      'lastActive': serializer.toJson<DateTime>(lastActive),
    };
  }

  StudentProfile copyWith(
          {String? id,
          String? name,
          Value<DateTime?> dateOfBirth = const Value.absent(),
          bool? captionsEnabled,
          int? mathScore,
          int? scienceScore,
          DateTime? lastActive}) =>
      StudentProfile(
        id: id ?? this.id,
        name: name ?? this.name,
        dateOfBirth: dateOfBirth.present ? dateOfBirth.value : this.dateOfBirth,
        captionsEnabled: captionsEnabled ?? this.captionsEnabled,
        mathScore: mathScore ?? this.mathScore,
        scienceScore: scienceScore ?? this.scienceScore,
        lastActive: lastActive ?? this.lastActive,
      );
  StudentProfile copyWithCompanion(StudentProfilesCompanion data) {
    return StudentProfile(
      id: data.id.present ? data.id.value : this.id,
      name: data.name.present ? data.name.value : this.name,
      dateOfBirth:
          data.dateOfBirth.present ? data.dateOfBirth.value : this.dateOfBirth,
      captionsEnabled: data.captionsEnabled.present
          ? data.captionsEnabled.value
          : this.captionsEnabled,
      mathScore: data.mathScore.present ? data.mathScore.value : this.mathScore,
      scienceScore: data.scienceScore.present
          ? data.scienceScore.value
          : this.scienceScore,
      lastActive:
          data.lastActive.present ? data.lastActive.value : this.lastActive,
    );
  }

  @override
  String toString() {
    return (StringBuffer('StudentProfile(')
          ..write('id: $id, ')
          ..write('name: $name, ')
          ..write('dateOfBirth: $dateOfBirth, ')
          ..write('captionsEnabled: $captionsEnabled, ')
          ..write('mathScore: $mathScore, ')
          ..write('scienceScore: $scienceScore, ')
          ..write('lastActive: $lastActive')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(id, name, dateOfBirth, captionsEnabled,
      mathScore, scienceScore, lastActive);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is StudentProfile &&
          other.id == this.id &&
          other.name == this.name &&
          other.dateOfBirth == this.dateOfBirth &&
          other.captionsEnabled == this.captionsEnabled &&
          other.mathScore == this.mathScore &&
          other.scienceScore == this.scienceScore &&
          other.lastActive == this.lastActive);
}

class StudentProfilesCompanion extends UpdateCompanion<StudentProfile> {
  final Value<String> id;
  final Value<String> name;
  final Value<DateTime?> dateOfBirth;
  final Value<bool> captionsEnabled;
  final Value<int> mathScore;
  final Value<int> scienceScore;
  final Value<DateTime> lastActive;
  final Value<int> rowid;
  const StudentProfilesCompanion({
    this.id = const Value.absent(),
    this.name = const Value.absent(),
    this.dateOfBirth = const Value.absent(),
    this.captionsEnabled = const Value.absent(),
    this.mathScore = const Value.absent(),
    this.scienceScore = const Value.absent(),
    this.lastActive = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  StudentProfilesCompanion.insert({
    required String id,
    required String name,
    this.dateOfBirth = const Value.absent(),
    this.captionsEnabled = const Value.absent(),
    this.mathScore = const Value.absent(),
    this.scienceScore = const Value.absent(),
    required DateTime lastActive,
    this.rowid = const Value.absent(),
  })  : id = Value(id),
        name = Value(name),
        lastActive = Value(lastActive);
  static Insertable<StudentProfile> custom({
    Expression<String>? id,
    Expression<String>? name,
    Expression<DateTime>? dateOfBirth,
    Expression<bool>? captionsEnabled,
    Expression<int>? mathScore,
    Expression<int>? scienceScore,
    Expression<DateTime>? lastActive,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (name != null) 'name': name,
      if (dateOfBirth != null) 'date_of_birth': dateOfBirth,
      if (captionsEnabled != null) 'captions_enabled': captionsEnabled,
      if (mathScore != null) 'math_score': mathScore,
      if (scienceScore != null) 'science_score': scienceScore,
      if (lastActive != null) 'last_active': lastActive,
      if (rowid != null) 'rowid': rowid,
    });
  }

  StudentProfilesCompanion copyWith(
      {Value<String>? id,
      Value<String>? name,
      Value<DateTime?>? dateOfBirth,
      Value<bool>? captionsEnabled,
      Value<int>? mathScore,
      Value<int>? scienceScore,
      Value<DateTime>? lastActive,
      Value<int>? rowid}) {
    return StudentProfilesCompanion(
      id: id ?? this.id,
      name: name ?? this.name,
      dateOfBirth: dateOfBirth ?? this.dateOfBirth,
      captionsEnabled: captionsEnabled ?? this.captionsEnabled,
      mathScore: mathScore ?? this.mathScore,
      scienceScore: scienceScore ?? this.scienceScore,
      lastActive: lastActive ?? this.lastActive,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (name.present) {
      map['name'] = Variable<String>(name.value);
    }
    if (dateOfBirth.present) {
      map['date_of_birth'] = Variable<DateTime>(dateOfBirth.value);
    }
    if (captionsEnabled.present) {
      map['captions_enabled'] = Variable<bool>(captionsEnabled.value);
    }
    if (mathScore.present) {
      map['math_score'] = Variable<int>(mathScore.value);
    }
    if (scienceScore.present) {
      map['science_score'] = Variable<int>(scienceScore.value);
    }
    if (lastActive.present) {
      map['last_active'] = Variable<DateTime>(lastActive.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('StudentProfilesCompanion(')
          ..write('id: $id, ')
          ..write('name: $name, ')
          ..write('dateOfBirth: $dateOfBirth, ')
          ..write('captionsEnabled: $captionsEnabled, ')
          ..write('mathScore: $mathScore, ')
          ..write('scienceScore: $scienceScore, ')
          ..write('lastActive: $lastActive, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $SessionHistoryTable extends SessionHistory
    with TableInfo<$SessionHistoryTable, HistoryMessage> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $SessionHistoryTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<int> id = GeneratedColumn<int>(
      'id', aliasedName, false,
      hasAutoIncrement: true,
      type: DriftSqlType.int,
      requiredDuringInsert: false,
      defaultConstraints:
          GeneratedColumn.constraintIsAlways('PRIMARY KEY AUTOINCREMENT'));
  static const VerificationMeta _speakerMeta =
      const VerificationMeta('speaker');
  @override
  late final GeneratedColumn<String> speaker = GeneratedColumn<String>(
      'speaker', aliasedName, false,
      type: DriftSqlType.string, requiredDuringInsert: true);
  static const VerificationMeta _textContentMeta =
      const VerificationMeta('textContent');
  @override
  late final GeneratedColumn<String> textContent = GeneratedColumn<String>(
      'text_content', aliasedName, false,
      type: DriftSqlType.string, requiredDuringInsert: true);
  static const VerificationMeta _timestampMeta =
      const VerificationMeta('timestamp');
  @override
  late final GeneratedColumn<DateTime> timestamp = GeneratedColumn<DateTime>(
      'timestamp', aliasedName, false,
      type: DriftSqlType.dateTime, requiredDuringInsert: true);
  @override
  List<GeneratedColumn> get $columns => [id, speaker, textContent, timestamp];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'session_history';
  @override
  VerificationContext validateIntegrity(Insertable<HistoryMessage> instance,
      {bool isInserting = false}) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    }
    if (data.containsKey('speaker')) {
      context.handle(_speakerMeta,
          speaker.isAcceptableOrUnknown(data['speaker']!, _speakerMeta));
    } else if (isInserting) {
      context.missing(_speakerMeta);
    }
    if (data.containsKey('text_content')) {
      context.handle(
          _textContentMeta,
          textContent.isAcceptableOrUnknown(
              data['text_content']!, _textContentMeta));
    } else if (isInserting) {
      context.missing(_textContentMeta);
    }
    if (data.containsKey('timestamp')) {
      context.handle(_timestampMeta,
          timestamp.isAcceptableOrUnknown(data['timestamp']!, _timestampMeta));
    } else if (isInserting) {
      context.missing(_timestampMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  HistoryMessage map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return HistoryMessage(
      id: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}id'])!,
      speaker: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}speaker'])!,
      textContent: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}text_content'])!,
      timestamp: attachedDatabase.typeMapping
          .read(DriftSqlType.dateTime, data['${effectivePrefix}timestamp'])!,
    );
  }

  @override
  $SessionHistoryTable createAlias(String alias) {
    return $SessionHistoryTable(attachedDatabase, alias);
  }
}

class HistoryMessage extends DataClass implements Insertable<HistoryMessage> {
  final int id;
  final String speaker;
  final String textContent;
  final DateTime timestamp;
  const HistoryMessage(
      {required this.id,
      required this.speaker,
      required this.textContent,
      required this.timestamp});
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<int>(id);
    map['speaker'] = Variable<String>(speaker);
    map['text_content'] = Variable<String>(textContent);
    map['timestamp'] = Variable<DateTime>(timestamp);
    return map;
  }

  SessionHistoryCompanion toCompanion(bool nullToAbsent) {
    return SessionHistoryCompanion(
      id: Value(id),
      speaker: Value(speaker),
      textContent: Value(textContent),
      timestamp: Value(timestamp),
    );
  }

  factory HistoryMessage.fromJson(Map<String, dynamic> json,
      {ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return HistoryMessage(
      id: serializer.fromJson<int>(json['id']),
      speaker: serializer.fromJson<String>(json['speaker']),
      textContent: serializer.fromJson<String>(json['textContent']),
      timestamp: serializer.fromJson<DateTime>(json['timestamp']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<int>(id),
      'speaker': serializer.toJson<String>(speaker),
      'textContent': serializer.toJson<String>(textContent),
      'timestamp': serializer.toJson<DateTime>(timestamp),
    };
  }

  HistoryMessage copyWith(
          {int? id,
          String? speaker,
          String? textContent,
          DateTime? timestamp}) =>
      HistoryMessage(
        id: id ?? this.id,
        speaker: speaker ?? this.speaker,
        textContent: textContent ?? this.textContent,
        timestamp: timestamp ?? this.timestamp,
      );
  HistoryMessage copyWithCompanion(SessionHistoryCompanion data) {
    return HistoryMessage(
      id: data.id.present ? data.id.value : this.id,
      speaker: data.speaker.present ? data.speaker.value : this.speaker,
      textContent:
          data.textContent.present ? data.textContent.value : this.textContent,
      timestamp: data.timestamp.present ? data.timestamp.value : this.timestamp,
    );
  }

  @override
  String toString() {
    return (StringBuffer('HistoryMessage(')
          ..write('id: $id, ')
          ..write('speaker: $speaker, ')
          ..write('textContent: $textContent, ')
          ..write('timestamp: $timestamp')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(id, speaker, textContent, timestamp);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is HistoryMessage &&
          other.id == this.id &&
          other.speaker == this.speaker &&
          other.textContent == this.textContent &&
          other.timestamp == this.timestamp);
}

class SessionHistoryCompanion extends UpdateCompanion<HistoryMessage> {
  final Value<int> id;
  final Value<String> speaker;
  final Value<String> textContent;
  final Value<DateTime> timestamp;
  const SessionHistoryCompanion({
    this.id = const Value.absent(),
    this.speaker = const Value.absent(),
    this.textContent = const Value.absent(),
    this.timestamp = const Value.absent(),
  });
  SessionHistoryCompanion.insert({
    this.id = const Value.absent(),
    required String speaker,
    required String textContent,
    required DateTime timestamp,
  })  : speaker = Value(speaker),
        textContent = Value(textContent),
        timestamp = Value(timestamp);
  static Insertable<HistoryMessage> custom({
    Expression<int>? id,
    Expression<String>? speaker,
    Expression<String>? textContent,
    Expression<DateTime>? timestamp,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (speaker != null) 'speaker': speaker,
      if (textContent != null) 'text_content': textContent,
      if (timestamp != null) 'timestamp': timestamp,
    });
  }

  SessionHistoryCompanion copyWith(
      {Value<int>? id,
      Value<String>? speaker,
      Value<String>? textContent,
      Value<DateTime>? timestamp}) {
    return SessionHistoryCompanion(
      id: id ?? this.id,
      speaker: speaker ?? this.speaker,
      textContent: textContent ?? this.textContent,
      timestamp: timestamp ?? this.timestamp,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<int>(id.value);
    }
    if (speaker.present) {
      map['speaker'] = Variable<String>(speaker.value);
    }
    if (textContent.present) {
      map['text_content'] = Variable<String>(textContent.value);
    }
    if (timestamp.present) {
      map['timestamp'] = Variable<DateTime>(timestamp.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('SessionHistoryCompanion(')
          ..write('id: $id, ')
          ..write('speaker: $speaker, ')
          ..write('textContent: $textContent, ')
          ..write('timestamp: $timestamp')
          ..write(')'))
        .toString();
  }
}

class $LearningProgressEventsTable extends LearningProgressEvents
    with TableInfo<$LearningProgressEventsTable, StoredLearningProgressEvent> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $LearningProgressEventsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<int> id = GeneratedColumn<int>(
      'id', aliasedName, false,
      hasAutoIncrement: true,
      type: DriftSqlType.int,
      requiredDuringInsert: false,
      defaultConstraints:
          GeneratedColumn.constraintIsAlways('PRIMARY KEY AUTOINCREMENT'));
  static const VerificationMeta _profileIdMeta =
      const VerificationMeta('profileId');
  @override
  late final GeneratedColumn<String> profileId = GeneratedColumn<String>(
      'profile_id', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 1, maxTextLength: 64),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _activityIdMeta =
      const VerificationMeta('activityId');
  @override
  late final GeneratedColumn<String> activityId = GeneratedColumn<String>(
      'activity_id', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 3, maxTextLength: 40),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _kindMeta = const VerificationMeta('kind');
  @override
  late final GeneratedColumn<String> kind = GeneratedColumn<String>(
      'kind', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 1, maxTextLength: 32),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _domainMeta = const VerificationMeta('domain');
  @override
  late final GeneratedColumn<String> domain = GeneratedColumn<String>(
      'domain', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 1, maxTextLength: 16),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _topicMeta = const VerificationMeta('topic');
  @override
  late final GeneratedColumn<String> topic = GeneratedColumn<String>(
      'topic', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 1, maxTextLength: 80),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _diagramKindMeta =
      const VerificationMeta('diagramKind');
  @override
  late final GeneratedColumn<String> diagramKind = GeneratedColumn<String>(
      'diagram_kind', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 1, maxTextLength: 32),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _currentValueMeta =
      const VerificationMeta('currentValue');
  @override
  late final GeneratedColumn<int> currentValue = GeneratedColumn<int>(
      'current_value', aliasedName, true,
      type: DriftSqlType.int, requiredDuringInsert: false);
  static const VerificationMeta _targetValueMeta =
      const VerificationMeta('targetValue');
  @override
  late final GeneratedColumn<int> targetValue = GeneratedColumn<int>(
      'target_value', aliasedName, true,
      type: DriftSqlType.int, requiredDuringInsert: false);
  static const VerificationMeta _occurredAtMeta =
      const VerificationMeta('occurredAt');
  @override
  late final GeneratedColumn<DateTime> occurredAt = GeneratedColumn<DateTime>(
      'occurred_at', aliasedName, false,
      type: DriftSqlType.dateTime, requiredDuringInsert: true);
  @override
  List<GeneratedColumn> get $columns => [
        id,
        profileId,
        activityId,
        kind,
        domain,
        topic,
        diagramKind,
        currentValue,
        targetValue,
        occurredAt
      ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'learning_progress_events';
  @override
  VerificationContext validateIntegrity(
      Insertable<StoredLearningProgressEvent> instance,
      {bool isInserting = false}) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    }
    if (data.containsKey('profile_id')) {
      context.handle(_profileIdMeta,
          profileId.isAcceptableOrUnknown(data['profile_id']!, _profileIdMeta));
    } else if (isInserting) {
      context.missing(_profileIdMeta);
    }
    if (data.containsKey('activity_id')) {
      context.handle(
          _activityIdMeta,
          activityId.isAcceptableOrUnknown(
              data['activity_id']!, _activityIdMeta));
    } else if (isInserting) {
      context.missing(_activityIdMeta);
    }
    if (data.containsKey('kind')) {
      context.handle(
          _kindMeta, kind.isAcceptableOrUnknown(data['kind']!, _kindMeta));
    } else if (isInserting) {
      context.missing(_kindMeta);
    }
    if (data.containsKey('domain')) {
      context.handle(_domainMeta,
          domain.isAcceptableOrUnknown(data['domain']!, _domainMeta));
    } else if (isInserting) {
      context.missing(_domainMeta);
    }
    if (data.containsKey('topic')) {
      context.handle(
          _topicMeta, topic.isAcceptableOrUnknown(data['topic']!, _topicMeta));
    } else if (isInserting) {
      context.missing(_topicMeta);
    }
    if (data.containsKey('diagram_kind')) {
      context.handle(
          _diagramKindMeta,
          diagramKind.isAcceptableOrUnknown(
              data['diagram_kind']!, _diagramKindMeta));
    } else if (isInserting) {
      context.missing(_diagramKindMeta);
    }
    if (data.containsKey('current_value')) {
      context.handle(
          _currentValueMeta,
          currentValue.isAcceptableOrUnknown(
              data['current_value']!, _currentValueMeta));
    }
    if (data.containsKey('target_value')) {
      context.handle(
          _targetValueMeta,
          targetValue.isAcceptableOrUnknown(
              data['target_value']!, _targetValueMeta));
    }
    if (data.containsKey('occurred_at')) {
      context.handle(
          _occurredAtMeta,
          occurredAt.isAcceptableOrUnknown(
              data['occurred_at']!, _occurredAtMeta));
    } else if (isInserting) {
      context.missing(_occurredAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  StoredLearningProgressEvent map(Map<String, dynamic> data,
      {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return StoredLearningProgressEvent(
      id: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}id'])!,
      profileId: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}profile_id'])!,
      activityId: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}activity_id'])!,
      kind: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}kind'])!,
      domain: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}domain'])!,
      topic: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}topic'])!,
      diagramKind: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}diagram_kind'])!,
      currentValue: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}current_value']),
      targetValue: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}target_value']),
      occurredAt: attachedDatabase.typeMapping
          .read(DriftSqlType.dateTime, data['${effectivePrefix}occurred_at'])!,
    );
  }

  @override
  $LearningProgressEventsTable createAlias(String alias) {
    return $LearningProgressEventsTable(attachedDatabase, alias);
  }
}

class StoredLearningProgressEvent extends DataClass
    implements Insertable<StoredLearningProgressEvent> {
  final int id;
  final String profileId;
  final String activityId;
  final String kind;
  final String domain;
  final String topic;
  final String diagramKind;
  final int? currentValue;
  final int? targetValue;
  final DateTime occurredAt;
  const StoredLearningProgressEvent(
      {required this.id,
      required this.profileId,
      required this.activityId,
      required this.kind,
      required this.domain,
      required this.topic,
      required this.diagramKind,
      this.currentValue,
      this.targetValue,
      required this.occurredAt});
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<int>(id);
    map['profile_id'] = Variable<String>(profileId);
    map['activity_id'] = Variable<String>(activityId);
    map['kind'] = Variable<String>(kind);
    map['domain'] = Variable<String>(domain);
    map['topic'] = Variable<String>(topic);
    map['diagram_kind'] = Variable<String>(diagramKind);
    if (!nullToAbsent || currentValue != null) {
      map['current_value'] = Variable<int>(currentValue);
    }
    if (!nullToAbsent || targetValue != null) {
      map['target_value'] = Variable<int>(targetValue);
    }
    map['occurred_at'] = Variable<DateTime>(occurredAt);
    return map;
  }

  LearningProgressEventsCompanion toCompanion(bool nullToAbsent) {
    return LearningProgressEventsCompanion(
      id: Value(id),
      profileId: Value(profileId),
      activityId: Value(activityId),
      kind: Value(kind),
      domain: Value(domain),
      topic: Value(topic),
      diagramKind: Value(diagramKind),
      currentValue: currentValue == null && nullToAbsent
          ? const Value.absent()
          : Value(currentValue),
      targetValue: targetValue == null && nullToAbsent
          ? const Value.absent()
          : Value(targetValue),
      occurredAt: Value(occurredAt),
    );
  }

  factory StoredLearningProgressEvent.fromJson(Map<String, dynamic> json,
      {ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return StoredLearningProgressEvent(
      id: serializer.fromJson<int>(json['id']),
      profileId: serializer.fromJson<String>(json['profileId']),
      activityId: serializer.fromJson<String>(json['activityId']),
      kind: serializer.fromJson<String>(json['kind']),
      domain: serializer.fromJson<String>(json['domain']),
      topic: serializer.fromJson<String>(json['topic']),
      diagramKind: serializer.fromJson<String>(json['diagramKind']),
      currentValue: serializer.fromJson<int?>(json['currentValue']),
      targetValue: serializer.fromJson<int?>(json['targetValue']),
      occurredAt: serializer.fromJson<DateTime>(json['occurredAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<int>(id),
      'profileId': serializer.toJson<String>(profileId),
      'activityId': serializer.toJson<String>(activityId),
      'kind': serializer.toJson<String>(kind),
      'domain': serializer.toJson<String>(domain),
      'topic': serializer.toJson<String>(topic),
      'diagramKind': serializer.toJson<String>(diagramKind),
      'currentValue': serializer.toJson<int?>(currentValue),
      'targetValue': serializer.toJson<int?>(targetValue),
      'occurredAt': serializer.toJson<DateTime>(occurredAt),
    };
  }

  StoredLearningProgressEvent copyWith(
          {int? id,
          String? profileId,
          String? activityId,
          String? kind,
          String? domain,
          String? topic,
          String? diagramKind,
          Value<int?> currentValue = const Value.absent(),
          Value<int?> targetValue = const Value.absent(),
          DateTime? occurredAt}) =>
      StoredLearningProgressEvent(
        id: id ?? this.id,
        profileId: profileId ?? this.profileId,
        activityId: activityId ?? this.activityId,
        kind: kind ?? this.kind,
        domain: domain ?? this.domain,
        topic: topic ?? this.topic,
        diagramKind: diagramKind ?? this.diagramKind,
        currentValue:
            currentValue.present ? currentValue.value : this.currentValue,
        targetValue: targetValue.present ? targetValue.value : this.targetValue,
        occurredAt: occurredAt ?? this.occurredAt,
      );
  StoredLearningProgressEvent copyWithCompanion(
      LearningProgressEventsCompanion data) {
    return StoredLearningProgressEvent(
      id: data.id.present ? data.id.value : this.id,
      profileId: data.profileId.present ? data.profileId.value : this.profileId,
      activityId:
          data.activityId.present ? data.activityId.value : this.activityId,
      kind: data.kind.present ? data.kind.value : this.kind,
      domain: data.domain.present ? data.domain.value : this.domain,
      topic: data.topic.present ? data.topic.value : this.topic,
      diagramKind:
          data.diagramKind.present ? data.diagramKind.value : this.diagramKind,
      currentValue: data.currentValue.present
          ? data.currentValue.value
          : this.currentValue,
      targetValue:
          data.targetValue.present ? data.targetValue.value : this.targetValue,
      occurredAt:
          data.occurredAt.present ? data.occurredAt.value : this.occurredAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('StoredLearningProgressEvent(')
          ..write('id: $id, ')
          ..write('profileId: $profileId, ')
          ..write('activityId: $activityId, ')
          ..write('kind: $kind, ')
          ..write('domain: $domain, ')
          ..write('topic: $topic, ')
          ..write('diagramKind: $diagramKind, ')
          ..write('currentValue: $currentValue, ')
          ..write('targetValue: $targetValue, ')
          ..write('occurredAt: $occurredAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(id, profileId, activityId, kind, domain,
      topic, diagramKind, currentValue, targetValue, occurredAt);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is StoredLearningProgressEvent &&
          other.id == this.id &&
          other.profileId == this.profileId &&
          other.activityId == this.activityId &&
          other.kind == this.kind &&
          other.domain == this.domain &&
          other.topic == this.topic &&
          other.diagramKind == this.diagramKind &&
          other.currentValue == this.currentValue &&
          other.targetValue == this.targetValue &&
          other.occurredAt == this.occurredAt);
}

class LearningProgressEventsCompanion
    extends UpdateCompanion<StoredLearningProgressEvent> {
  final Value<int> id;
  final Value<String> profileId;
  final Value<String> activityId;
  final Value<String> kind;
  final Value<String> domain;
  final Value<String> topic;
  final Value<String> diagramKind;
  final Value<int?> currentValue;
  final Value<int?> targetValue;
  final Value<DateTime> occurredAt;
  const LearningProgressEventsCompanion({
    this.id = const Value.absent(),
    this.profileId = const Value.absent(),
    this.activityId = const Value.absent(),
    this.kind = const Value.absent(),
    this.domain = const Value.absent(),
    this.topic = const Value.absent(),
    this.diagramKind = const Value.absent(),
    this.currentValue = const Value.absent(),
    this.targetValue = const Value.absent(),
    this.occurredAt = const Value.absent(),
  });
  LearningProgressEventsCompanion.insert({
    this.id = const Value.absent(),
    required String profileId,
    required String activityId,
    required String kind,
    required String domain,
    required String topic,
    required String diagramKind,
    this.currentValue = const Value.absent(),
    this.targetValue = const Value.absent(),
    required DateTime occurredAt,
  })  : profileId = Value(profileId),
        activityId = Value(activityId),
        kind = Value(kind),
        domain = Value(domain),
        topic = Value(topic),
        diagramKind = Value(diagramKind),
        occurredAt = Value(occurredAt);
  static Insertable<StoredLearningProgressEvent> custom({
    Expression<int>? id,
    Expression<String>? profileId,
    Expression<String>? activityId,
    Expression<String>? kind,
    Expression<String>? domain,
    Expression<String>? topic,
    Expression<String>? diagramKind,
    Expression<int>? currentValue,
    Expression<int>? targetValue,
    Expression<DateTime>? occurredAt,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (profileId != null) 'profile_id': profileId,
      if (activityId != null) 'activity_id': activityId,
      if (kind != null) 'kind': kind,
      if (domain != null) 'domain': domain,
      if (topic != null) 'topic': topic,
      if (diagramKind != null) 'diagram_kind': diagramKind,
      if (currentValue != null) 'current_value': currentValue,
      if (targetValue != null) 'target_value': targetValue,
      if (occurredAt != null) 'occurred_at': occurredAt,
    });
  }

  LearningProgressEventsCompanion copyWith(
      {Value<int>? id,
      Value<String>? profileId,
      Value<String>? activityId,
      Value<String>? kind,
      Value<String>? domain,
      Value<String>? topic,
      Value<String>? diagramKind,
      Value<int?>? currentValue,
      Value<int?>? targetValue,
      Value<DateTime>? occurredAt}) {
    return LearningProgressEventsCompanion(
      id: id ?? this.id,
      profileId: profileId ?? this.profileId,
      activityId: activityId ?? this.activityId,
      kind: kind ?? this.kind,
      domain: domain ?? this.domain,
      topic: topic ?? this.topic,
      diagramKind: diagramKind ?? this.diagramKind,
      currentValue: currentValue ?? this.currentValue,
      targetValue: targetValue ?? this.targetValue,
      occurredAt: occurredAt ?? this.occurredAt,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<int>(id.value);
    }
    if (profileId.present) {
      map['profile_id'] = Variable<String>(profileId.value);
    }
    if (activityId.present) {
      map['activity_id'] = Variable<String>(activityId.value);
    }
    if (kind.present) {
      map['kind'] = Variable<String>(kind.value);
    }
    if (domain.present) {
      map['domain'] = Variable<String>(domain.value);
    }
    if (topic.present) {
      map['topic'] = Variable<String>(topic.value);
    }
    if (diagramKind.present) {
      map['diagram_kind'] = Variable<String>(diagramKind.value);
    }
    if (currentValue.present) {
      map['current_value'] = Variable<int>(currentValue.value);
    }
    if (targetValue.present) {
      map['target_value'] = Variable<int>(targetValue.value);
    }
    if (occurredAt.present) {
      map['occurred_at'] = Variable<DateTime>(occurredAt.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('LearningProgressEventsCompanion(')
          ..write('id: $id, ')
          ..write('profileId: $profileId, ')
          ..write('activityId: $activityId, ')
          ..write('kind: $kind, ')
          ..write('domain: $domain, ')
          ..write('topic: $topic, ')
          ..write('diagramKind: $diagramKind, ')
          ..write('currentValue: $currentValue, ')
          ..write('targetValue: $targetValue, ')
          ..write('occurredAt: $occurredAt')
          ..write(')'))
        .toString();
  }
}

abstract class _$AppDatabase extends GeneratedDatabase {
  _$AppDatabase(QueryExecutor e) : super(e);
  $AppDatabaseManager get managers => $AppDatabaseManager(this);
  late final $StudentProfilesTable studentProfiles =
      $StudentProfilesTable(this);
  late final $SessionHistoryTable sessionHistory = $SessionHistoryTable(this);
  late final $LearningProgressEventsTable learningProgressEvents =
      $LearningProgressEventsTable(this);
  @override
  Iterable<TableInfo<Table, Object?>> get allTables =>
      allSchemaEntities.whereType<TableInfo<Table, Object?>>();
  @override
  List<DatabaseSchemaEntity> get allSchemaEntities =>
      [studentProfiles, sessionHistory, learningProgressEvents];
}

typedef $$StudentProfilesTableCreateCompanionBuilder = StudentProfilesCompanion
    Function({
  required String id,
  required String name,
  Value<DateTime?> dateOfBirth,
  Value<bool> captionsEnabled,
  Value<int> mathScore,
  Value<int> scienceScore,
  required DateTime lastActive,
  Value<int> rowid,
});
typedef $$StudentProfilesTableUpdateCompanionBuilder = StudentProfilesCompanion
    Function({
  Value<String> id,
  Value<String> name,
  Value<DateTime?> dateOfBirth,
  Value<bool> captionsEnabled,
  Value<int> mathScore,
  Value<int> scienceScore,
  Value<DateTime> lastActive,
  Value<int> rowid,
});

class $$StudentProfilesTableFilterComposer
    extends Composer<_$AppDatabase, $StudentProfilesTable> {
  $$StudentProfilesTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
      column: $table.id, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get name => $composableBuilder(
      column: $table.name, builder: (column) => ColumnFilters(column));

  ColumnFilters<DateTime> get dateOfBirth => $composableBuilder(
      column: $table.dateOfBirth, builder: (column) => ColumnFilters(column));

  ColumnFilters<bool> get captionsEnabled => $composableBuilder(
      column: $table.captionsEnabled,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get mathScore => $composableBuilder(
      column: $table.mathScore, builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get scienceScore => $composableBuilder(
      column: $table.scienceScore, builder: (column) => ColumnFilters(column));

  ColumnFilters<DateTime> get lastActive => $composableBuilder(
      column: $table.lastActive, builder: (column) => ColumnFilters(column));
}

class $$StudentProfilesTableOrderingComposer
    extends Composer<_$AppDatabase, $StudentProfilesTable> {
  $$StudentProfilesTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
      column: $table.id, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get name => $composableBuilder(
      column: $table.name, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<DateTime> get dateOfBirth => $composableBuilder(
      column: $table.dateOfBirth, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<bool> get captionsEnabled => $composableBuilder(
      column: $table.captionsEnabled,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get mathScore => $composableBuilder(
      column: $table.mathScore, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get scienceScore => $composableBuilder(
      column: $table.scienceScore,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<DateTime> get lastActive => $composableBuilder(
      column: $table.lastActive, builder: (column) => ColumnOrderings(column));
}

class $$StudentProfilesTableAnnotationComposer
    extends Composer<_$AppDatabase, $StudentProfilesTable> {
  $$StudentProfilesTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get name =>
      $composableBuilder(column: $table.name, builder: (column) => column);

  GeneratedColumn<DateTime> get dateOfBirth => $composableBuilder(
      column: $table.dateOfBirth, builder: (column) => column);

  GeneratedColumn<bool> get captionsEnabled => $composableBuilder(
      column: $table.captionsEnabled, builder: (column) => column);

  GeneratedColumn<int> get mathScore =>
      $composableBuilder(column: $table.mathScore, builder: (column) => column);

  GeneratedColumn<int> get scienceScore => $composableBuilder(
      column: $table.scienceScore, builder: (column) => column);

  GeneratedColumn<DateTime> get lastActive => $composableBuilder(
      column: $table.lastActive, builder: (column) => column);
}

class $$StudentProfilesTableTableManager extends RootTableManager<
    _$AppDatabase,
    $StudentProfilesTable,
    StudentProfile,
    $$StudentProfilesTableFilterComposer,
    $$StudentProfilesTableOrderingComposer,
    $$StudentProfilesTableAnnotationComposer,
    $$StudentProfilesTableCreateCompanionBuilder,
    $$StudentProfilesTableUpdateCompanionBuilder,
    (
      StudentProfile,
      BaseReferences<_$AppDatabase, $StudentProfilesTable, StudentProfile>
    ),
    StudentProfile,
    PrefetchHooks Function()> {
  $$StudentProfilesTableTableManager(
      _$AppDatabase db, $StudentProfilesTable table)
      : super(TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$StudentProfilesTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$StudentProfilesTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$StudentProfilesTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback: ({
            Value<String> id = const Value.absent(),
            Value<String> name = const Value.absent(),
            Value<DateTime?> dateOfBirth = const Value.absent(),
            Value<bool> captionsEnabled = const Value.absent(),
            Value<int> mathScore = const Value.absent(),
            Value<int> scienceScore = const Value.absent(),
            Value<DateTime> lastActive = const Value.absent(),
            Value<int> rowid = const Value.absent(),
          }) =>
              StudentProfilesCompanion(
            id: id,
            name: name,
            dateOfBirth: dateOfBirth,
            captionsEnabled: captionsEnabled,
            mathScore: mathScore,
            scienceScore: scienceScore,
            lastActive: lastActive,
            rowid: rowid,
          ),
          createCompanionCallback: ({
            required String id,
            required String name,
            Value<DateTime?> dateOfBirth = const Value.absent(),
            Value<bool> captionsEnabled = const Value.absent(),
            Value<int> mathScore = const Value.absent(),
            Value<int> scienceScore = const Value.absent(),
            required DateTime lastActive,
            Value<int> rowid = const Value.absent(),
          }) =>
              StudentProfilesCompanion.insert(
            id: id,
            name: name,
            dateOfBirth: dateOfBirth,
            captionsEnabled: captionsEnabled,
            mathScore: mathScore,
            scienceScore: scienceScore,
            lastActive: lastActive,
            rowid: rowid,
          ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ));
}

typedef $$StudentProfilesTableProcessedTableManager = ProcessedTableManager<
    _$AppDatabase,
    $StudentProfilesTable,
    StudentProfile,
    $$StudentProfilesTableFilterComposer,
    $$StudentProfilesTableOrderingComposer,
    $$StudentProfilesTableAnnotationComposer,
    $$StudentProfilesTableCreateCompanionBuilder,
    $$StudentProfilesTableUpdateCompanionBuilder,
    (
      StudentProfile,
      BaseReferences<_$AppDatabase, $StudentProfilesTable, StudentProfile>
    ),
    StudentProfile,
    PrefetchHooks Function()>;
typedef $$SessionHistoryTableCreateCompanionBuilder = SessionHistoryCompanion
    Function({
  Value<int> id,
  required String speaker,
  required String textContent,
  required DateTime timestamp,
});
typedef $$SessionHistoryTableUpdateCompanionBuilder = SessionHistoryCompanion
    Function({
  Value<int> id,
  Value<String> speaker,
  Value<String> textContent,
  Value<DateTime> timestamp,
});

class $$SessionHistoryTableFilterComposer
    extends Composer<_$AppDatabase, $SessionHistoryTable> {
  $$SessionHistoryTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<int> get id => $composableBuilder(
      column: $table.id, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get speaker => $composableBuilder(
      column: $table.speaker, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get textContent => $composableBuilder(
      column: $table.textContent, builder: (column) => ColumnFilters(column));

  ColumnFilters<DateTime> get timestamp => $composableBuilder(
      column: $table.timestamp, builder: (column) => ColumnFilters(column));
}

class $$SessionHistoryTableOrderingComposer
    extends Composer<_$AppDatabase, $SessionHistoryTable> {
  $$SessionHistoryTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<int> get id => $composableBuilder(
      column: $table.id, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get speaker => $composableBuilder(
      column: $table.speaker, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get textContent => $composableBuilder(
      column: $table.textContent, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<DateTime> get timestamp => $composableBuilder(
      column: $table.timestamp, builder: (column) => ColumnOrderings(column));
}

class $$SessionHistoryTableAnnotationComposer
    extends Composer<_$AppDatabase, $SessionHistoryTable> {
  $$SessionHistoryTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<int> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get speaker =>
      $composableBuilder(column: $table.speaker, builder: (column) => column);

  GeneratedColumn<String> get textContent => $composableBuilder(
      column: $table.textContent, builder: (column) => column);

  GeneratedColumn<DateTime> get timestamp =>
      $composableBuilder(column: $table.timestamp, builder: (column) => column);
}

class $$SessionHistoryTableTableManager extends RootTableManager<
    _$AppDatabase,
    $SessionHistoryTable,
    HistoryMessage,
    $$SessionHistoryTableFilterComposer,
    $$SessionHistoryTableOrderingComposer,
    $$SessionHistoryTableAnnotationComposer,
    $$SessionHistoryTableCreateCompanionBuilder,
    $$SessionHistoryTableUpdateCompanionBuilder,
    (
      HistoryMessage,
      BaseReferences<_$AppDatabase, $SessionHistoryTable, HistoryMessage>
    ),
    HistoryMessage,
    PrefetchHooks Function()> {
  $$SessionHistoryTableTableManager(
      _$AppDatabase db, $SessionHistoryTable table)
      : super(TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$SessionHistoryTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$SessionHistoryTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$SessionHistoryTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback: ({
            Value<int> id = const Value.absent(),
            Value<String> speaker = const Value.absent(),
            Value<String> textContent = const Value.absent(),
            Value<DateTime> timestamp = const Value.absent(),
          }) =>
              SessionHistoryCompanion(
            id: id,
            speaker: speaker,
            textContent: textContent,
            timestamp: timestamp,
          ),
          createCompanionCallback: ({
            Value<int> id = const Value.absent(),
            required String speaker,
            required String textContent,
            required DateTime timestamp,
          }) =>
              SessionHistoryCompanion.insert(
            id: id,
            speaker: speaker,
            textContent: textContent,
            timestamp: timestamp,
          ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ));
}

typedef $$SessionHistoryTableProcessedTableManager = ProcessedTableManager<
    _$AppDatabase,
    $SessionHistoryTable,
    HistoryMessage,
    $$SessionHistoryTableFilterComposer,
    $$SessionHistoryTableOrderingComposer,
    $$SessionHistoryTableAnnotationComposer,
    $$SessionHistoryTableCreateCompanionBuilder,
    $$SessionHistoryTableUpdateCompanionBuilder,
    (
      HistoryMessage,
      BaseReferences<_$AppDatabase, $SessionHistoryTable, HistoryMessage>
    ),
    HistoryMessage,
    PrefetchHooks Function()>;
typedef $$LearningProgressEventsTableCreateCompanionBuilder
    = LearningProgressEventsCompanion Function({
  Value<int> id,
  required String profileId,
  required String activityId,
  required String kind,
  required String domain,
  required String topic,
  required String diagramKind,
  Value<int?> currentValue,
  Value<int?> targetValue,
  required DateTime occurredAt,
});
typedef $$LearningProgressEventsTableUpdateCompanionBuilder
    = LearningProgressEventsCompanion Function({
  Value<int> id,
  Value<String> profileId,
  Value<String> activityId,
  Value<String> kind,
  Value<String> domain,
  Value<String> topic,
  Value<String> diagramKind,
  Value<int?> currentValue,
  Value<int?> targetValue,
  Value<DateTime> occurredAt,
});

class $$LearningProgressEventsTableFilterComposer
    extends Composer<_$AppDatabase, $LearningProgressEventsTable> {
  $$LearningProgressEventsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<int> get id => $composableBuilder(
      column: $table.id, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get profileId => $composableBuilder(
      column: $table.profileId, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get activityId => $composableBuilder(
      column: $table.activityId, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get kind => $composableBuilder(
      column: $table.kind, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get domain => $composableBuilder(
      column: $table.domain, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get topic => $composableBuilder(
      column: $table.topic, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get diagramKind => $composableBuilder(
      column: $table.diagramKind, builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get currentValue => $composableBuilder(
      column: $table.currentValue, builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get targetValue => $composableBuilder(
      column: $table.targetValue, builder: (column) => ColumnFilters(column));

  ColumnFilters<DateTime> get occurredAt => $composableBuilder(
      column: $table.occurredAt, builder: (column) => ColumnFilters(column));
}

class $$LearningProgressEventsTableOrderingComposer
    extends Composer<_$AppDatabase, $LearningProgressEventsTable> {
  $$LearningProgressEventsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<int> get id => $composableBuilder(
      column: $table.id, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get profileId => $composableBuilder(
      column: $table.profileId, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get activityId => $composableBuilder(
      column: $table.activityId, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get kind => $composableBuilder(
      column: $table.kind, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get domain => $composableBuilder(
      column: $table.domain, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get topic => $composableBuilder(
      column: $table.topic, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get diagramKind => $composableBuilder(
      column: $table.diagramKind, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get currentValue => $composableBuilder(
      column: $table.currentValue,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get targetValue => $composableBuilder(
      column: $table.targetValue, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<DateTime> get occurredAt => $composableBuilder(
      column: $table.occurredAt, builder: (column) => ColumnOrderings(column));
}

class $$LearningProgressEventsTableAnnotationComposer
    extends Composer<_$AppDatabase, $LearningProgressEventsTable> {
  $$LearningProgressEventsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<int> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get profileId =>
      $composableBuilder(column: $table.profileId, builder: (column) => column);

  GeneratedColumn<String> get activityId => $composableBuilder(
      column: $table.activityId, builder: (column) => column);

  GeneratedColumn<String> get kind =>
      $composableBuilder(column: $table.kind, builder: (column) => column);

  GeneratedColumn<String> get domain =>
      $composableBuilder(column: $table.domain, builder: (column) => column);

  GeneratedColumn<String> get topic =>
      $composableBuilder(column: $table.topic, builder: (column) => column);

  GeneratedColumn<String> get diagramKind => $composableBuilder(
      column: $table.diagramKind, builder: (column) => column);

  GeneratedColumn<int> get currentValue => $composableBuilder(
      column: $table.currentValue, builder: (column) => column);

  GeneratedColumn<int> get targetValue => $composableBuilder(
      column: $table.targetValue, builder: (column) => column);

  GeneratedColumn<DateTime> get occurredAt => $composableBuilder(
      column: $table.occurredAt, builder: (column) => column);
}

class $$LearningProgressEventsTableTableManager extends RootTableManager<
    _$AppDatabase,
    $LearningProgressEventsTable,
    StoredLearningProgressEvent,
    $$LearningProgressEventsTableFilterComposer,
    $$LearningProgressEventsTableOrderingComposer,
    $$LearningProgressEventsTableAnnotationComposer,
    $$LearningProgressEventsTableCreateCompanionBuilder,
    $$LearningProgressEventsTableUpdateCompanionBuilder,
    (
      StoredLearningProgressEvent,
      BaseReferences<_$AppDatabase, $LearningProgressEventsTable,
          StoredLearningProgressEvent>
    ),
    StoredLearningProgressEvent,
    PrefetchHooks Function()> {
  $$LearningProgressEventsTableTableManager(
      _$AppDatabase db, $LearningProgressEventsTable table)
      : super(TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$LearningProgressEventsTableFilterComposer(
                  $db: db, $table: table),
          createOrderingComposer: () =>
              $$LearningProgressEventsTableOrderingComposer(
                  $db: db, $table: table),
          createComputedFieldComposer: () =>
              $$LearningProgressEventsTableAnnotationComposer(
                  $db: db, $table: table),
          updateCompanionCallback: ({
            Value<int> id = const Value.absent(),
            Value<String> profileId = const Value.absent(),
            Value<String> activityId = const Value.absent(),
            Value<String> kind = const Value.absent(),
            Value<String> domain = const Value.absent(),
            Value<String> topic = const Value.absent(),
            Value<String> diagramKind = const Value.absent(),
            Value<int?> currentValue = const Value.absent(),
            Value<int?> targetValue = const Value.absent(),
            Value<DateTime> occurredAt = const Value.absent(),
          }) =>
              LearningProgressEventsCompanion(
            id: id,
            profileId: profileId,
            activityId: activityId,
            kind: kind,
            domain: domain,
            topic: topic,
            diagramKind: diagramKind,
            currentValue: currentValue,
            targetValue: targetValue,
            occurredAt: occurredAt,
          ),
          createCompanionCallback: ({
            Value<int> id = const Value.absent(),
            required String profileId,
            required String activityId,
            required String kind,
            required String domain,
            required String topic,
            required String diagramKind,
            Value<int?> currentValue = const Value.absent(),
            Value<int?> targetValue = const Value.absent(),
            required DateTime occurredAt,
          }) =>
              LearningProgressEventsCompanion.insert(
            id: id,
            profileId: profileId,
            activityId: activityId,
            kind: kind,
            domain: domain,
            topic: topic,
            diagramKind: diagramKind,
            currentValue: currentValue,
            targetValue: targetValue,
            occurredAt: occurredAt,
          ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ));
}

typedef $$LearningProgressEventsTableProcessedTableManager
    = ProcessedTableManager<
        _$AppDatabase,
        $LearningProgressEventsTable,
        StoredLearningProgressEvent,
        $$LearningProgressEventsTableFilterComposer,
        $$LearningProgressEventsTableOrderingComposer,
        $$LearningProgressEventsTableAnnotationComposer,
        $$LearningProgressEventsTableCreateCompanionBuilder,
        $$LearningProgressEventsTableUpdateCompanionBuilder,
        (
          StoredLearningProgressEvent,
          BaseReferences<_$AppDatabase, $LearningProgressEventsTable,
              StoredLearningProgressEvent>
        ),
        StoredLearningProgressEvent,
        PrefetchHooks Function()>;

class $AppDatabaseManager {
  final _$AppDatabase _db;
  $AppDatabaseManager(this._db);
  $$StudentProfilesTableTableManager get studentProfiles =>
      $$StudentProfilesTableTableManager(_db, _db.studentProfiles);
  $$SessionHistoryTableTableManager get sessionHistory =>
      $$SessionHistoryTableTableManager(_db, _db.sessionHistory);
  $$LearningProgressEventsTableTableManager get learningProgressEvents =>
      $$LearningProgressEventsTableTableManager(
          _db, _db.learningProgressEvents);
}
