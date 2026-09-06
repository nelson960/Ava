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

class $CanvasScenesTable extends CanvasScenes
    with TableInfo<$CanvasScenesTable, StoredCanvasScene> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $CanvasScenesTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _sceneIdMeta =
      const VerificationMeta('sceneId');
  @override
  late final GeneratedColumn<String> sceneId = GeneratedColumn<String>(
      'scene_id', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 1, maxTextLength: 96),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _sceneRevisionMeta =
      const VerificationMeta('sceneRevision');
  @override
  late final GeneratedColumn<int> sceneRevision = GeneratedColumn<int>(
      'scene_revision', aliasedName, false,
      type: DriftSqlType.int, requiredDuringInsert: true);
  static const VerificationMeta _generationIdMeta =
      const VerificationMeta('generationId');
  @override
  late final GeneratedColumn<String> generationId = GeneratedColumn<String>(
      'generation_id', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 1, maxTextLength: 96),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _sourceTurnIdMeta =
      const VerificationMeta('sourceTurnId');
  @override
  late final GeneratedColumn<String> sourceTurnId = GeneratedColumn<String>(
      'source_turn_id', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 1, maxTextLength: 96),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _svgBytesMeta =
      const VerificationMeta('svgBytes');
  @override
  late final GeneratedColumn<Uint8List> svgBytes = GeneratedColumn<Uint8List>(
      'svg_bytes', aliasedName, false,
      type: DriftSqlType.blob, requiredDuringInsert: true);
  static const VerificationMeta _manifestBytesMeta =
      const VerificationMeta('manifestBytes');
  @override
  late final GeneratedColumn<Uint8List> manifestBytes =
      GeneratedColumn<Uint8List>('manifest_bytes', aliasedName, false,
          type: DriftSqlType.blob, requiredDuringInsert: true);
  static const VerificationMeta _svgSha256Meta =
      const VerificationMeta('svgSha256');
  @override
  late final GeneratedColumn<String> svgSha256 = GeneratedColumn<String>(
      'svg_sha256', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 64, maxTextLength: 64),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _manifestSha256Meta =
      const VerificationMeta('manifestSha256');
  @override
  late final GeneratedColumn<String> manifestSha256 = GeneratedColumn<String>(
      'manifest_sha256', aliasedName, false,
      additionalChecks:
          GeneratedColumn.checkTextLength(minTextLength: 64, maxTextLength: 64),
      type: DriftSqlType.string,
      requiredDuringInsert: true);
  static const VerificationMeta _svgByteLengthMeta =
      const VerificationMeta('svgByteLength');
  @override
  late final GeneratedColumn<int> svgByteLength = GeneratedColumn<int>(
      'svg_byte_length', aliasedName, false,
      type: DriftSqlType.int, requiredDuringInsert: true);
  static const VerificationMeta _manifestByteLengthMeta =
      const VerificationMeta('manifestByteLength');
  @override
  late final GeneratedColumn<int> manifestByteLength = GeneratedColumn<int>(
      'manifest_byte_length', aliasedName, false,
      type: DriftSqlType.int, requiredDuringInsert: true);
  static const VerificationMeta _rendererCapabilityVersionMeta =
      const VerificationMeta('rendererCapabilityVersion');
  @override
  late final GeneratedColumn<String> rendererCapabilityVersion =
      GeneratedColumn<String>('renderer_capability_version', aliasedName, false,
          type: DriftSqlType.string, requiredDuringInsert: true);
  static const VerificationMeta _validatorVersionMeta =
      const VerificationMeta('validatorVersion');
  @override
  late final GeneratedColumn<String> validatorVersion = GeneratedColumn<String>(
      'validator_version', aliasedName, false,
      type: DriftSqlType.string, requiredDuringInsert: true);
  static const VerificationMeta _manifestVersionMeta =
      const VerificationMeta('manifestVersion');
  @override
  late final GeneratedColumn<String> manifestVersion = GeneratedColumn<String>(
      'manifest_version', aliasedName, false,
      type: DriftSqlType.string, requiredDuringInsert: true);
  static const VerificationMeta _workspaceOrderMeta =
      const VerificationMeta('workspaceOrder');
  @override
  late final GeneratedColumn<int> workspaceOrder = GeneratedColumn<int>(
      'workspace_order', aliasedName, false,
      type: DriftSqlType.int, requiredDuringInsert: true);
  static const VerificationMeta _isFocusedMeta =
      const VerificationMeta('isFocused');
  @override
  late final GeneratedColumn<bool> isFocused = GeneratedColumn<bool>(
      'is_focused', aliasedName, false,
      type: DriftSqlType.bool,
      requiredDuringInsert: true,
      defaultConstraints:
          GeneratedColumn.constraintIsAlways('CHECK ("is_focused" IN (0, 1))'));
  static const VerificationMeta _creationSequenceMeta =
      const VerificationMeta('creationSequence');
  @override
  late final GeneratedColumn<int> creationSequence = GeneratedColumn<int>(
      'creation_sequence', aliasedName, true,
      type: DriftSqlType.int, requiredDuringInsert: false);
  static const VerificationMeta _modificationSequenceMeta =
      const VerificationMeta('modificationSequence');
  @override
  late final GeneratedColumn<int> modificationSequence = GeneratedColumn<int>(
      'modification_sequence', aliasedName, true,
      type: DriftSqlType.int, requiredDuringInsert: false);
  static const VerificationMeta _titleMeta = const VerificationMeta('title');
  @override
  late final GeneratedColumn<String> title = GeneratedColumn<String>(
      'title', aliasedName, true,
      type: DriftSqlType.string, requiredDuringInsert: false);
  static const VerificationMeta _summaryMeta =
      const VerificationMeta('summary');
  @override
  late final GeneratedColumn<String> summary = GeneratedColumn<String>(
      'summary', aliasedName, true,
      type: DriftSqlType.string, requiredDuringInsert: false);
  @override
  List<GeneratedColumn> get $columns => [
        sceneId,
        sceneRevision,
        generationId,
        sourceTurnId,
        svgBytes,
        manifestBytes,
        svgSha256,
        manifestSha256,
        svgByteLength,
        manifestByteLength,
        rendererCapabilityVersion,
        validatorVersion,
        manifestVersion,
        workspaceOrder,
        isFocused,
        creationSequence,
        modificationSequence,
        title,
        summary
      ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'canvas_scenes';
  @override
  VerificationContext validateIntegrity(Insertable<StoredCanvasScene> instance,
      {bool isInserting = false}) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('scene_id')) {
      context.handle(_sceneIdMeta,
          sceneId.isAcceptableOrUnknown(data['scene_id']!, _sceneIdMeta));
    } else if (isInserting) {
      context.missing(_sceneIdMeta);
    }
    if (data.containsKey('scene_revision')) {
      context.handle(
          _sceneRevisionMeta,
          sceneRevision.isAcceptableOrUnknown(
              data['scene_revision']!, _sceneRevisionMeta));
    } else if (isInserting) {
      context.missing(_sceneRevisionMeta);
    }
    if (data.containsKey('generation_id')) {
      context.handle(
          _generationIdMeta,
          generationId.isAcceptableOrUnknown(
              data['generation_id']!, _generationIdMeta));
    } else if (isInserting) {
      context.missing(_generationIdMeta);
    }
    if (data.containsKey('source_turn_id')) {
      context.handle(
          _sourceTurnIdMeta,
          sourceTurnId.isAcceptableOrUnknown(
              data['source_turn_id']!, _sourceTurnIdMeta));
    } else if (isInserting) {
      context.missing(_sourceTurnIdMeta);
    }
    if (data.containsKey('svg_bytes')) {
      context.handle(_svgBytesMeta,
          svgBytes.isAcceptableOrUnknown(data['svg_bytes']!, _svgBytesMeta));
    } else if (isInserting) {
      context.missing(_svgBytesMeta);
    }
    if (data.containsKey('manifest_bytes')) {
      context.handle(
          _manifestBytesMeta,
          manifestBytes.isAcceptableOrUnknown(
              data['manifest_bytes']!, _manifestBytesMeta));
    } else if (isInserting) {
      context.missing(_manifestBytesMeta);
    }
    if (data.containsKey('svg_sha256')) {
      context.handle(_svgSha256Meta,
          svgSha256.isAcceptableOrUnknown(data['svg_sha256']!, _svgSha256Meta));
    } else if (isInserting) {
      context.missing(_svgSha256Meta);
    }
    if (data.containsKey('manifest_sha256')) {
      context.handle(
          _manifestSha256Meta,
          manifestSha256.isAcceptableOrUnknown(
              data['manifest_sha256']!, _manifestSha256Meta));
    } else if (isInserting) {
      context.missing(_manifestSha256Meta);
    }
    if (data.containsKey('svg_byte_length')) {
      context.handle(
          _svgByteLengthMeta,
          svgByteLength.isAcceptableOrUnknown(
              data['svg_byte_length']!, _svgByteLengthMeta));
    } else if (isInserting) {
      context.missing(_svgByteLengthMeta);
    }
    if (data.containsKey('manifest_byte_length')) {
      context.handle(
          _manifestByteLengthMeta,
          manifestByteLength.isAcceptableOrUnknown(
              data['manifest_byte_length']!, _manifestByteLengthMeta));
    } else if (isInserting) {
      context.missing(_manifestByteLengthMeta);
    }
    if (data.containsKey('renderer_capability_version')) {
      context.handle(
          _rendererCapabilityVersionMeta,
          rendererCapabilityVersion.isAcceptableOrUnknown(
              data['renderer_capability_version']!,
              _rendererCapabilityVersionMeta));
    } else if (isInserting) {
      context.missing(_rendererCapabilityVersionMeta);
    }
    if (data.containsKey('validator_version')) {
      context.handle(
          _validatorVersionMeta,
          validatorVersion.isAcceptableOrUnknown(
              data['validator_version']!, _validatorVersionMeta));
    } else if (isInserting) {
      context.missing(_validatorVersionMeta);
    }
    if (data.containsKey('manifest_version')) {
      context.handle(
          _manifestVersionMeta,
          manifestVersion.isAcceptableOrUnknown(
              data['manifest_version']!, _manifestVersionMeta));
    } else if (isInserting) {
      context.missing(_manifestVersionMeta);
    }
    if (data.containsKey('workspace_order')) {
      context.handle(
          _workspaceOrderMeta,
          workspaceOrder.isAcceptableOrUnknown(
              data['workspace_order']!, _workspaceOrderMeta));
    } else if (isInserting) {
      context.missing(_workspaceOrderMeta);
    }
    if (data.containsKey('is_focused')) {
      context.handle(_isFocusedMeta,
          isFocused.isAcceptableOrUnknown(data['is_focused']!, _isFocusedMeta));
    } else if (isInserting) {
      context.missing(_isFocusedMeta);
    }
    if (data.containsKey('creation_sequence')) {
      context.handle(
          _creationSequenceMeta,
          creationSequence.isAcceptableOrUnknown(
              data['creation_sequence']!, _creationSequenceMeta));
    }
    if (data.containsKey('modification_sequence')) {
      context.handle(
          _modificationSequenceMeta,
          modificationSequence.isAcceptableOrUnknown(
              data['modification_sequence']!, _modificationSequenceMeta));
    }
    if (data.containsKey('title')) {
      context.handle(
          _titleMeta, title.isAcceptableOrUnknown(data['title']!, _titleMeta));
    }
    if (data.containsKey('summary')) {
      context.handle(_summaryMeta,
          summary.isAcceptableOrUnknown(data['summary']!, _summaryMeta));
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {sceneId};
  @override
  StoredCanvasScene map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return StoredCanvasScene(
      sceneId: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}scene_id'])!,
      sceneRevision: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}scene_revision'])!,
      generationId: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}generation_id'])!,
      sourceTurnId: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}source_turn_id'])!,
      svgBytes: attachedDatabase.typeMapping
          .read(DriftSqlType.blob, data['${effectivePrefix}svg_bytes'])!,
      manifestBytes: attachedDatabase.typeMapping
          .read(DriftSqlType.blob, data['${effectivePrefix}manifest_bytes'])!,
      svgSha256: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}svg_sha256'])!,
      manifestSha256: attachedDatabase.typeMapping.read(
          DriftSqlType.string, data['${effectivePrefix}manifest_sha256'])!,
      svgByteLength: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}svg_byte_length'])!,
      manifestByteLength: attachedDatabase.typeMapping.read(
          DriftSqlType.int, data['${effectivePrefix}manifest_byte_length'])!,
      rendererCapabilityVersion: attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}renderer_capability_version'])!,
      validatorVersion: attachedDatabase.typeMapping.read(
          DriftSqlType.string, data['${effectivePrefix}validator_version'])!,
      manifestVersion: attachedDatabase.typeMapping.read(
          DriftSqlType.string, data['${effectivePrefix}manifest_version'])!,
      workspaceOrder: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}workspace_order'])!,
      isFocused: attachedDatabase.typeMapping
          .read(DriftSqlType.bool, data['${effectivePrefix}is_focused'])!,
      creationSequence: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}creation_sequence']),
      modificationSequence: attachedDatabase.typeMapping.read(
          DriftSqlType.int, data['${effectivePrefix}modification_sequence']),
      title: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}title']),
      summary: attachedDatabase.typeMapping
          .read(DriftSqlType.string, data['${effectivePrefix}summary']),
    );
  }

  @override
  $CanvasScenesTable createAlias(String alias) {
    return $CanvasScenesTable(attachedDatabase, alias);
  }
}

class StoredCanvasScene extends DataClass
    implements Insertable<StoredCanvasScene> {
  final String sceneId;
  final int sceneRevision;
  final String generationId;
  final String sourceTurnId;
  final Uint8List svgBytes;
  final Uint8List manifestBytes;
  final String svgSha256;
  final String manifestSha256;
  final int svgByteLength;
  final int manifestByteLength;
  final String rendererCapabilityVersion;
  final String validatorVersion;
  final String manifestVersion;
  final int workspaceOrder;
  final bool isFocused;
  final int? creationSequence;
  final int? modificationSequence;
  final String? title;
  final String? summary;
  const StoredCanvasScene(
      {required this.sceneId,
      required this.sceneRevision,
      required this.generationId,
      required this.sourceTurnId,
      required this.svgBytes,
      required this.manifestBytes,
      required this.svgSha256,
      required this.manifestSha256,
      required this.svgByteLength,
      required this.manifestByteLength,
      required this.rendererCapabilityVersion,
      required this.validatorVersion,
      required this.manifestVersion,
      required this.workspaceOrder,
      required this.isFocused,
      this.creationSequence,
      this.modificationSequence,
      this.title,
      this.summary});
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['scene_id'] = Variable<String>(sceneId);
    map['scene_revision'] = Variable<int>(sceneRevision);
    map['generation_id'] = Variable<String>(generationId);
    map['source_turn_id'] = Variable<String>(sourceTurnId);
    map['svg_bytes'] = Variable<Uint8List>(svgBytes);
    map['manifest_bytes'] = Variable<Uint8List>(manifestBytes);
    map['svg_sha256'] = Variable<String>(svgSha256);
    map['manifest_sha256'] = Variable<String>(manifestSha256);
    map['svg_byte_length'] = Variable<int>(svgByteLength);
    map['manifest_byte_length'] = Variable<int>(manifestByteLength);
    map['renderer_capability_version'] =
        Variable<String>(rendererCapabilityVersion);
    map['validator_version'] = Variable<String>(validatorVersion);
    map['manifest_version'] = Variable<String>(manifestVersion);
    map['workspace_order'] = Variable<int>(workspaceOrder);
    map['is_focused'] = Variable<bool>(isFocused);
    if (!nullToAbsent || creationSequence != null) {
      map['creation_sequence'] = Variable<int>(creationSequence);
    }
    if (!nullToAbsent || modificationSequence != null) {
      map['modification_sequence'] = Variable<int>(modificationSequence);
    }
    if (!nullToAbsent || title != null) {
      map['title'] = Variable<String>(title);
    }
    if (!nullToAbsent || summary != null) {
      map['summary'] = Variable<String>(summary);
    }
    return map;
  }

  CanvasScenesCompanion toCompanion(bool nullToAbsent) {
    return CanvasScenesCompanion(
      sceneId: Value(sceneId),
      sceneRevision: Value(sceneRevision),
      generationId: Value(generationId),
      sourceTurnId: Value(sourceTurnId),
      svgBytes: Value(svgBytes),
      manifestBytes: Value(manifestBytes),
      svgSha256: Value(svgSha256),
      manifestSha256: Value(manifestSha256),
      svgByteLength: Value(svgByteLength),
      manifestByteLength: Value(manifestByteLength),
      rendererCapabilityVersion: Value(rendererCapabilityVersion),
      validatorVersion: Value(validatorVersion),
      manifestVersion: Value(manifestVersion),
      workspaceOrder: Value(workspaceOrder),
      isFocused: Value(isFocused),
      creationSequence: creationSequence == null && nullToAbsent
          ? const Value.absent()
          : Value(creationSequence),
      modificationSequence: modificationSequence == null && nullToAbsent
          ? const Value.absent()
          : Value(modificationSequence),
      title:
          title == null && nullToAbsent ? const Value.absent() : Value(title),
      summary: summary == null && nullToAbsent
          ? const Value.absent()
          : Value(summary),
    );
  }

  factory StoredCanvasScene.fromJson(Map<String, dynamic> json,
      {ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return StoredCanvasScene(
      sceneId: serializer.fromJson<String>(json['sceneId']),
      sceneRevision: serializer.fromJson<int>(json['sceneRevision']),
      generationId: serializer.fromJson<String>(json['generationId']),
      sourceTurnId: serializer.fromJson<String>(json['sourceTurnId']),
      svgBytes: serializer.fromJson<Uint8List>(json['svgBytes']),
      manifestBytes: serializer.fromJson<Uint8List>(json['manifestBytes']),
      svgSha256: serializer.fromJson<String>(json['svgSha256']),
      manifestSha256: serializer.fromJson<String>(json['manifestSha256']),
      svgByteLength: serializer.fromJson<int>(json['svgByteLength']),
      manifestByteLength: serializer.fromJson<int>(json['manifestByteLength']),
      rendererCapabilityVersion:
          serializer.fromJson<String>(json['rendererCapabilityVersion']),
      validatorVersion: serializer.fromJson<String>(json['validatorVersion']),
      manifestVersion: serializer.fromJson<String>(json['manifestVersion']),
      workspaceOrder: serializer.fromJson<int>(json['workspaceOrder']),
      isFocused: serializer.fromJson<bool>(json['isFocused']),
      creationSequence: serializer.fromJson<int?>(json['creationSequence']),
      modificationSequence:
          serializer.fromJson<int?>(json['modificationSequence']),
      title: serializer.fromJson<String?>(json['title']),
      summary: serializer.fromJson<String?>(json['summary']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'sceneId': serializer.toJson<String>(sceneId),
      'sceneRevision': serializer.toJson<int>(sceneRevision),
      'generationId': serializer.toJson<String>(generationId),
      'sourceTurnId': serializer.toJson<String>(sourceTurnId),
      'svgBytes': serializer.toJson<Uint8List>(svgBytes),
      'manifestBytes': serializer.toJson<Uint8List>(manifestBytes),
      'svgSha256': serializer.toJson<String>(svgSha256),
      'manifestSha256': serializer.toJson<String>(manifestSha256),
      'svgByteLength': serializer.toJson<int>(svgByteLength),
      'manifestByteLength': serializer.toJson<int>(manifestByteLength),
      'rendererCapabilityVersion':
          serializer.toJson<String>(rendererCapabilityVersion),
      'validatorVersion': serializer.toJson<String>(validatorVersion),
      'manifestVersion': serializer.toJson<String>(manifestVersion),
      'workspaceOrder': serializer.toJson<int>(workspaceOrder),
      'isFocused': serializer.toJson<bool>(isFocused),
      'creationSequence': serializer.toJson<int?>(creationSequence),
      'modificationSequence': serializer.toJson<int?>(modificationSequence),
      'title': serializer.toJson<String?>(title),
      'summary': serializer.toJson<String?>(summary),
    };
  }

  StoredCanvasScene copyWith(
          {String? sceneId,
          int? sceneRevision,
          String? generationId,
          String? sourceTurnId,
          Uint8List? svgBytes,
          Uint8List? manifestBytes,
          String? svgSha256,
          String? manifestSha256,
          int? svgByteLength,
          int? manifestByteLength,
          String? rendererCapabilityVersion,
          String? validatorVersion,
          String? manifestVersion,
          int? workspaceOrder,
          bool? isFocused,
          Value<int?> creationSequence = const Value.absent(),
          Value<int?> modificationSequence = const Value.absent(),
          Value<String?> title = const Value.absent(),
          Value<String?> summary = const Value.absent()}) =>
      StoredCanvasScene(
        sceneId: sceneId ?? this.sceneId,
        sceneRevision: sceneRevision ?? this.sceneRevision,
        generationId: generationId ?? this.generationId,
        sourceTurnId: sourceTurnId ?? this.sourceTurnId,
        svgBytes: svgBytes ?? this.svgBytes,
        manifestBytes: manifestBytes ?? this.manifestBytes,
        svgSha256: svgSha256 ?? this.svgSha256,
        manifestSha256: manifestSha256 ?? this.manifestSha256,
        svgByteLength: svgByteLength ?? this.svgByteLength,
        manifestByteLength: manifestByteLength ?? this.manifestByteLength,
        rendererCapabilityVersion:
            rendererCapabilityVersion ?? this.rendererCapabilityVersion,
        validatorVersion: validatorVersion ?? this.validatorVersion,
        manifestVersion: manifestVersion ?? this.manifestVersion,
        workspaceOrder: workspaceOrder ?? this.workspaceOrder,
        isFocused: isFocused ?? this.isFocused,
        creationSequence: creationSequence.present
            ? creationSequence.value
            : this.creationSequence,
        modificationSequence: modificationSequence.present
            ? modificationSequence.value
            : this.modificationSequence,
        title: title.present ? title.value : this.title,
        summary: summary.present ? summary.value : this.summary,
      );
  StoredCanvasScene copyWithCompanion(CanvasScenesCompanion data) {
    return StoredCanvasScene(
      sceneId: data.sceneId.present ? data.sceneId.value : this.sceneId,
      sceneRevision: data.sceneRevision.present
          ? data.sceneRevision.value
          : this.sceneRevision,
      generationId: data.generationId.present
          ? data.generationId.value
          : this.generationId,
      sourceTurnId: data.sourceTurnId.present
          ? data.sourceTurnId.value
          : this.sourceTurnId,
      svgBytes: data.svgBytes.present ? data.svgBytes.value : this.svgBytes,
      manifestBytes: data.manifestBytes.present
          ? data.manifestBytes.value
          : this.manifestBytes,
      svgSha256: data.svgSha256.present ? data.svgSha256.value : this.svgSha256,
      manifestSha256: data.manifestSha256.present
          ? data.manifestSha256.value
          : this.manifestSha256,
      svgByteLength: data.svgByteLength.present
          ? data.svgByteLength.value
          : this.svgByteLength,
      manifestByteLength: data.manifestByteLength.present
          ? data.manifestByteLength.value
          : this.manifestByteLength,
      rendererCapabilityVersion: data.rendererCapabilityVersion.present
          ? data.rendererCapabilityVersion.value
          : this.rendererCapabilityVersion,
      validatorVersion: data.validatorVersion.present
          ? data.validatorVersion.value
          : this.validatorVersion,
      manifestVersion: data.manifestVersion.present
          ? data.manifestVersion.value
          : this.manifestVersion,
      workspaceOrder: data.workspaceOrder.present
          ? data.workspaceOrder.value
          : this.workspaceOrder,
      isFocused: data.isFocused.present ? data.isFocused.value : this.isFocused,
      creationSequence: data.creationSequence.present
          ? data.creationSequence.value
          : this.creationSequence,
      modificationSequence: data.modificationSequence.present
          ? data.modificationSequence.value
          : this.modificationSequence,
      title: data.title.present ? data.title.value : this.title,
      summary: data.summary.present ? data.summary.value : this.summary,
    );
  }

  @override
  String toString() {
    return (StringBuffer('StoredCanvasScene(')
          ..write('sceneId: $sceneId, ')
          ..write('sceneRevision: $sceneRevision, ')
          ..write('generationId: $generationId, ')
          ..write('sourceTurnId: $sourceTurnId, ')
          ..write('svgBytes: $svgBytes, ')
          ..write('manifestBytes: $manifestBytes, ')
          ..write('svgSha256: $svgSha256, ')
          ..write('manifestSha256: $manifestSha256, ')
          ..write('svgByteLength: $svgByteLength, ')
          ..write('manifestByteLength: $manifestByteLength, ')
          ..write('rendererCapabilityVersion: $rendererCapabilityVersion, ')
          ..write('validatorVersion: $validatorVersion, ')
          ..write('manifestVersion: $manifestVersion, ')
          ..write('workspaceOrder: $workspaceOrder, ')
          ..write('isFocused: $isFocused, ')
          ..write('creationSequence: $creationSequence, ')
          ..write('modificationSequence: $modificationSequence, ')
          ..write('title: $title, ')
          ..write('summary: $summary')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
      sceneId,
      sceneRevision,
      generationId,
      sourceTurnId,
      $driftBlobEquality.hash(svgBytes),
      $driftBlobEquality.hash(manifestBytes),
      svgSha256,
      manifestSha256,
      svgByteLength,
      manifestByteLength,
      rendererCapabilityVersion,
      validatorVersion,
      manifestVersion,
      workspaceOrder,
      isFocused,
      creationSequence,
      modificationSequence,
      title,
      summary);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is StoredCanvasScene &&
          other.sceneId == this.sceneId &&
          other.sceneRevision == this.sceneRevision &&
          other.generationId == this.generationId &&
          other.sourceTurnId == this.sourceTurnId &&
          $driftBlobEquality.equals(other.svgBytes, this.svgBytes) &&
          $driftBlobEquality.equals(other.manifestBytes, this.manifestBytes) &&
          other.svgSha256 == this.svgSha256 &&
          other.manifestSha256 == this.manifestSha256 &&
          other.svgByteLength == this.svgByteLength &&
          other.manifestByteLength == this.manifestByteLength &&
          other.rendererCapabilityVersion == this.rendererCapabilityVersion &&
          other.validatorVersion == this.validatorVersion &&
          other.manifestVersion == this.manifestVersion &&
          other.workspaceOrder == this.workspaceOrder &&
          other.isFocused == this.isFocused &&
          other.creationSequence == this.creationSequence &&
          other.modificationSequence == this.modificationSequence &&
          other.title == this.title &&
          other.summary == this.summary);
}

class CanvasScenesCompanion extends UpdateCompanion<StoredCanvasScene> {
  final Value<String> sceneId;
  final Value<int> sceneRevision;
  final Value<String> generationId;
  final Value<String> sourceTurnId;
  final Value<Uint8List> svgBytes;
  final Value<Uint8List> manifestBytes;
  final Value<String> svgSha256;
  final Value<String> manifestSha256;
  final Value<int> svgByteLength;
  final Value<int> manifestByteLength;
  final Value<String> rendererCapabilityVersion;
  final Value<String> validatorVersion;
  final Value<String> manifestVersion;
  final Value<int> workspaceOrder;
  final Value<bool> isFocused;
  final Value<int?> creationSequence;
  final Value<int?> modificationSequence;
  final Value<String?> title;
  final Value<String?> summary;
  final Value<int> rowid;
  const CanvasScenesCompanion({
    this.sceneId = const Value.absent(),
    this.sceneRevision = const Value.absent(),
    this.generationId = const Value.absent(),
    this.sourceTurnId = const Value.absent(),
    this.svgBytes = const Value.absent(),
    this.manifestBytes = const Value.absent(),
    this.svgSha256 = const Value.absent(),
    this.manifestSha256 = const Value.absent(),
    this.svgByteLength = const Value.absent(),
    this.manifestByteLength = const Value.absent(),
    this.rendererCapabilityVersion = const Value.absent(),
    this.validatorVersion = const Value.absent(),
    this.manifestVersion = const Value.absent(),
    this.workspaceOrder = const Value.absent(),
    this.isFocused = const Value.absent(),
    this.creationSequence = const Value.absent(),
    this.modificationSequence = const Value.absent(),
    this.title = const Value.absent(),
    this.summary = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  CanvasScenesCompanion.insert({
    required String sceneId,
    required int sceneRevision,
    required String generationId,
    required String sourceTurnId,
    required Uint8List svgBytes,
    required Uint8List manifestBytes,
    required String svgSha256,
    required String manifestSha256,
    required int svgByteLength,
    required int manifestByteLength,
    required String rendererCapabilityVersion,
    required String validatorVersion,
    required String manifestVersion,
    required int workspaceOrder,
    required bool isFocused,
    this.creationSequence = const Value.absent(),
    this.modificationSequence = const Value.absent(),
    this.title = const Value.absent(),
    this.summary = const Value.absent(),
    this.rowid = const Value.absent(),
  })  : sceneId = Value(sceneId),
        sceneRevision = Value(sceneRevision),
        generationId = Value(generationId),
        sourceTurnId = Value(sourceTurnId),
        svgBytes = Value(svgBytes),
        manifestBytes = Value(manifestBytes),
        svgSha256 = Value(svgSha256),
        manifestSha256 = Value(manifestSha256),
        svgByteLength = Value(svgByteLength),
        manifestByteLength = Value(manifestByteLength),
        rendererCapabilityVersion = Value(rendererCapabilityVersion),
        validatorVersion = Value(validatorVersion),
        manifestVersion = Value(manifestVersion),
        workspaceOrder = Value(workspaceOrder),
        isFocused = Value(isFocused);
  static Insertable<StoredCanvasScene> custom({
    Expression<String>? sceneId,
    Expression<int>? sceneRevision,
    Expression<String>? generationId,
    Expression<String>? sourceTurnId,
    Expression<Uint8List>? svgBytes,
    Expression<Uint8List>? manifestBytes,
    Expression<String>? svgSha256,
    Expression<String>? manifestSha256,
    Expression<int>? svgByteLength,
    Expression<int>? manifestByteLength,
    Expression<String>? rendererCapabilityVersion,
    Expression<String>? validatorVersion,
    Expression<String>? manifestVersion,
    Expression<int>? workspaceOrder,
    Expression<bool>? isFocused,
    Expression<int>? creationSequence,
    Expression<int>? modificationSequence,
    Expression<String>? title,
    Expression<String>? summary,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (sceneId != null) 'scene_id': sceneId,
      if (sceneRevision != null) 'scene_revision': sceneRevision,
      if (generationId != null) 'generation_id': generationId,
      if (sourceTurnId != null) 'source_turn_id': sourceTurnId,
      if (svgBytes != null) 'svg_bytes': svgBytes,
      if (manifestBytes != null) 'manifest_bytes': manifestBytes,
      if (svgSha256 != null) 'svg_sha256': svgSha256,
      if (manifestSha256 != null) 'manifest_sha256': manifestSha256,
      if (svgByteLength != null) 'svg_byte_length': svgByteLength,
      if (manifestByteLength != null)
        'manifest_byte_length': manifestByteLength,
      if (rendererCapabilityVersion != null)
        'renderer_capability_version': rendererCapabilityVersion,
      if (validatorVersion != null) 'validator_version': validatorVersion,
      if (manifestVersion != null) 'manifest_version': manifestVersion,
      if (workspaceOrder != null) 'workspace_order': workspaceOrder,
      if (isFocused != null) 'is_focused': isFocused,
      if (creationSequence != null) 'creation_sequence': creationSequence,
      if (modificationSequence != null)
        'modification_sequence': modificationSequence,
      if (title != null) 'title': title,
      if (summary != null) 'summary': summary,
      if (rowid != null) 'rowid': rowid,
    });
  }

  CanvasScenesCompanion copyWith(
      {Value<String>? sceneId,
      Value<int>? sceneRevision,
      Value<String>? generationId,
      Value<String>? sourceTurnId,
      Value<Uint8List>? svgBytes,
      Value<Uint8List>? manifestBytes,
      Value<String>? svgSha256,
      Value<String>? manifestSha256,
      Value<int>? svgByteLength,
      Value<int>? manifestByteLength,
      Value<String>? rendererCapabilityVersion,
      Value<String>? validatorVersion,
      Value<String>? manifestVersion,
      Value<int>? workspaceOrder,
      Value<bool>? isFocused,
      Value<int?>? creationSequence,
      Value<int?>? modificationSequence,
      Value<String?>? title,
      Value<String?>? summary,
      Value<int>? rowid}) {
    return CanvasScenesCompanion(
      sceneId: sceneId ?? this.sceneId,
      sceneRevision: sceneRevision ?? this.sceneRevision,
      generationId: generationId ?? this.generationId,
      sourceTurnId: sourceTurnId ?? this.sourceTurnId,
      svgBytes: svgBytes ?? this.svgBytes,
      manifestBytes: manifestBytes ?? this.manifestBytes,
      svgSha256: svgSha256 ?? this.svgSha256,
      manifestSha256: manifestSha256 ?? this.manifestSha256,
      svgByteLength: svgByteLength ?? this.svgByteLength,
      manifestByteLength: manifestByteLength ?? this.manifestByteLength,
      rendererCapabilityVersion:
          rendererCapabilityVersion ?? this.rendererCapabilityVersion,
      validatorVersion: validatorVersion ?? this.validatorVersion,
      manifestVersion: manifestVersion ?? this.manifestVersion,
      workspaceOrder: workspaceOrder ?? this.workspaceOrder,
      isFocused: isFocused ?? this.isFocused,
      creationSequence: creationSequence ?? this.creationSequence,
      modificationSequence: modificationSequence ?? this.modificationSequence,
      title: title ?? this.title,
      summary: summary ?? this.summary,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (sceneId.present) {
      map['scene_id'] = Variable<String>(sceneId.value);
    }
    if (sceneRevision.present) {
      map['scene_revision'] = Variable<int>(sceneRevision.value);
    }
    if (generationId.present) {
      map['generation_id'] = Variable<String>(generationId.value);
    }
    if (sourceTurnId.present) {
      map['source_turn_id'] = Variable<String>(sourceTurnId.value);
    }
    if (svgBytes.present) {
      map['svg_bytes'] = Variable<Uint8List>(svgBytes.value);
    }
    if (manifestBytes.present) {
      map['manifest_bytes'] = Variable<Uint8List>(manifestBytes.value);
    }
    if (svgSha256.present) {
      map['svg_sha256'] = Variable<String>(svgSha256.value);
    }
    if (manifestSha256.present) {
      map['manifest_sha256'] = Variable<String>(manifestSha256.value);
    }
    if (svgByteLength.present) {
      map['svg_byte_length'] = Variable<int>(svgByteLength.value);
    }
    if (manifestByteLength.present) {
      map['manifest_byte_length'] = Variable<int>(manifestByteLength.value);
    }
    if (rendererCapabilityVersion.present) {
      map['renderer_capability_version'] =
          Variable<String>(rendererCapabilityVersion.value);
    }
    if (validatorVersion.present) {
      map['validator_version'] = Variable<String>(validatorVersion.value);
    }
    if (manifestVersion.present) {
      map['manifest_version'] = Variable<String>(manifestVersion.value);
    }
    if (workspaceOrder.present) {
      map['workspace_order'] = Variable<int>(workspaceOrder.value);
    }
    if (isFocused.present) {
      map['is_focused'] = Variable<bool>(isFocused.value);
    }
    if (creationSequence.present) {
      map['creation_sequence'] = Variable<int>(creationSequence.value);
    }
    if (modificationSequence.present) {
      map['modification_sequence'] = Variable<int>(modificationSequence.value);
    }
    if (title.present) {
      map['title'] = Variable<String>(title.value);
    }
    if (summary.present) {
      map['summary'] = Variable<String>(summary.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('CanvasScenesCompanion(')
          ..write('sceneId: $sceneId, ')
          ..write('sceneRevision: $sceneRevision, ')
          ..write('generationId: $generationId, ')
          ..write('sourceTurnId: $sourceTurnId, ')
          ..write('svgBytes: $svgBytes, ')
          ..write('manifestBytes: $manifestBytes, ')
          ..write('svgSha256: $svgSha256, ')
          ..write('manifestSha256: $manifestSha256, ')
          ..write('svgByteLength: $svgByteLength, ')
          ..write('manifestByteLength: $manifestByteLength, ')
          ..write('rendererCapabilityVersion: $rendererCapabilityVersion, ')
          ..write('validatorVersion: $validatorVersion, ')
          ..write('manifestVersion: $manifestVersion, ')
          ..write('workspaceOrder: $workspaceOrder, ')
          ..write('isFocused: $isFocused, ')
          ..write('creationSequence: $creationSequence, ')
          ..write('modificationSequence: $modificationSequence, ')
          ..write('title: $title, ')
          ..write('summary: $summary, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $CanvasWorkspacesTable extends CanvasWorkspaces
    with TableInfo<$CanvasWorkspacesTable, StoredCanvasWorkspace> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $CanvasWorkspacesTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<int> id = GeneratedColumn<int>(
      'id', aliasedName, false,
      type: DriftSqlType.int, requiredDuringInsert: false);
  static const VerificationMeta _workspaceEpochMeta =
      const VerificationMeta('workspaceEpoch');
  @override
  late final GeneratedColumn<int> workspaceEpoch = GeneratedColumn<int>(
      'workspace_epoch', aliasedName, false,
      type: DriftSqlType.int, requiredDuringInsert: true);
  static const VerificationMeta _workspaceRevisionMeta =
      const VerificationMeta('workspaceRevision');
  @override
  late final GeneratedColumn<int> workspaceRevision = GeneratedColumn<int>(
      'workspace_revision', aliasedName, false,
      type: DriftSqlType.int, requiredDuringInsert: true);
  static const VerificationMeta _nextCreationSequenceMeta =
      const VerificationMeta('nextCreationSequence');
  @override
  late final GeneratedColumn<int> nextCreationSequence = GeneratedColumn<int>(
      'next_creation_sequence', aliasedName, false,
      type: DriftSqlType.int, requiredDuringInsert: true);
  static const VerificationMeta _nextModificationSequenceMeta =
      const VerificationMeta('nextModificationSequence');
  @override
  late final GeneratedColumn<int> nextModificationSequence =
      GeneratedColumn<int>('next_modification_sequence', aliasedName, false,
          type: DriftSqlType.int, requiredDuringInsert: true);
  @override
  List<GeneratedColumn> get $columns => [
        id,
        workspaceEpoch,
        workspaceRevision,
        nextCreationSequence,
        nextModificationSequence
      ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'canvas_workspaces';
  @override
  VerificationContext validateIntegrity(
      Insertable<StoredCanvasWorkspace> instance,
      {bool isInserting = false}) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    }
    if (data.containsKey('workspace_epoch')) {
      context.handle(
          _workspaceEpochMeta,
          workspaceEpoch.isAcceptableOrUnknown(
              data['workspace_epoch']!, _workspaceEpochMeta));
    } else if (isInserting) {
      context.missing(_workspaceEpochMeta);
    }
    if (data.containsKey('workspace_revision')) {
      context.handle(
          _workspaceRevisionMeta,
          workspaceRevision.isAcceptableOrUnknown(
              data['workspace_revision']!, _workspaceRevisionMeta));
    } else if (isInserting) {
      context.missing(_workspaceRevisionMeta);
    }
    if (data.containsKey('next_creation_sequence')) {
      context.handle(
          _nextCreationSequenceMeta,
          nextCreationSequence.isAcceptableOrUnknown(
              data['next_creation_sequence']!, _nextCreationSequenceMeta));
    } else if (isInserting) {
      context.missing(_nextCreationSequenceMeta);
    }
    if (data.containsKey('next_modification_sequence')) {
      context.handle(
          _nextModificationSequenceMeta,
          nextModificationSequence.isAcceptableOrUnknown(
              data['next_modification_sequence']!,
              _nextModificationSequenceMeta));
    } else if (isInserting) {
      context.missing(_nextModificationSequenceMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  StoredCanvasWorkspace map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return StoredCanvasWorkspace(
      id: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}id'])!,
      workspaceEpoch: attachedDatabase.typeMapping
          .read(DriftSqlType.int, data['${effectivePrefix}workspace_epoch'])!,
      workspaceRevision: attachedDatabase.typeMapping.read(
          DriftSqlType.int, data['${effectivePrefix}workspace_revision'])!,
      nextCreationSequence: attachedDatabase.typeMapping.read(
          DriftSqlType.int, data['${effectivePrefix}next_creation_sequence'])!,
      nextModificationSequence: attachedDatabase.typeMapping.read(
          DriftSqlType.int,
          data['${effectivePrefix}next_modification_sequence'])!,
    );
  }

  @override
  $CanvasWorkspacesTable createAlias(String alias) {
    return $CanvasWorkspacesTable(attachedDatabase, alias);
  }
}

class StoredCanvasWorkspace extends DataClass
    implements Insertable<StoredCanvasWorkspace> {
  final int id;
  final int workspaceEpoch;
  final int workspaceRevision;
  final int nextCreationSequence;
  final int nextModificationSequence;
  const StoredCanvasWorkspace(
      {required this.id,
      required this.workspaceEpoch,
      required this.workspaceRevision,
      required this.nextCreationSequence,
      required this.nextModificationSequence});
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<int>(id);
    map['workspace_epoch'] = Variable<int>(workspaceEpoch);
    map['workspace_revision'] = Variable<int>(workspaceRevision);
    map['next_creation_sequence'] = Variable<int>(nextCreationSequence);
    map['next_modification_sequence'] = Variable<int>(nextModificationSequence);
    return map;
  }

  CanvasWorkspacesCompanion toCompanion(bool nullToAbsent) {
    return CanvasWorkspacesCompanion(
      id: Value(id),
      workspaceEpoch: Value(workspaceEpoch),
      workspaceRevision: Value(workspaceRevision),
      nextCreationSequence: Value(nextCreationSequence),
      nextModificationSequence: Value(nextModificationSequence),
    );
  }

  factory StoredCanvasWorkspace.fromJson(Map<String, dynamic> json,
      {ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return StoredCanvasWorkspace(
      id: serializer.fromJson<int>(json['id']),
      workspaceEpoch: serializer.fromJson<int>(json['workspaceEpoch']),
      workspaceRevision: serializer.fromJson<int>(json['workspaceRevision']),
      nextCreationSequence:
          serializer.fromJson<int>(json['nextCreationSequence']),
      nextModificationSequence:
          serializer.fromJson<int>(json['nextModificationSequence']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<int>(id),
      'workspaceEpoch': serializer.toJson<int>(workspaceEpoch),
      'workspaceRevision': serializer.toJson<int>(workspaceRevision),
      'nextCreationSequence': serializer.toJson<int>(nextCreationSequence),
      'nextModificationSequence':
          serializer.toJson<int>(nextModificationSequence),
    };
  }

  StoredCanvasWorkspace copyWith(
          {int? id,
          int? workspaceEpoch,
          int? workspaceRevision,
          int? nextCreationSequence,
          int? nextModificationSequence}) =>
      StoredCanvasWorkspace(
        id: id ?? this.id,
        workspaceEpoch: workspaceEpoch ?? this.workspaceEpoch,
        workspaceRevision: workspaceRevision ?? this.workspaceRevision,
        nextCreationSequence: nextCreationSequence ?? this.nextCreationSequence,
        nextModificationSequence:
            nextModificationSequence ?? this.nextModificationSequence,
      );
  StoredCanvasWorkspace copyWithCompanion(CanvasWorkspacesCompanion data) {
    return StoredCanvasWorkspace(
      id: data.id.present ? data.id.value : this.id,
      workspaceEpoch: data.workspaceEpoch.present
          ? data.workspaceEpoch.value
          : this.workspaceEpoch,
      workspaceRevision: data.workspaceRevision.present
          ? data.workspaceRevision.value
          : this.workspaceRevision,
      nextCreationSequence: data.nextCreationSequence.present
          ? data.nextCreationSequence.value
          : this.nextCreationSequence,
      nextModificationSequence: data.nextModificationSequence.present
          ? data.nextModificationSequence.value
          : this.nextModificationSequence,
    );
  }

  @override
  String toString() {
    return (StringBuffer('StoredCanvasWorkspace(')
          ..write('id: $id, ')
          ..write('workspaceEpoch: $workspaceEpoch, ')
          ..write('workspaceRevision: $workspaceRevision, ')
          ..write('nextCreationSequence: $nextCreationSequence, ')
          ..write('nextModificationSequence: $nextModificationSequence')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(id, workspaceEpoch, workspaceRevision,
      nextCreationSequence, nextModificationSequence);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is StoredCanvasWorkspace &&
          other.id == this.id &&
          other.workspaceEpoch == this.workspaceEpoch &&
          other.workspaceRevision == this.workspaceRevision &&
          other.nextCreationSequence == this.nextCreationSequence &&
          other.nextModificationSequence == this.nextModificationSequence);
}

class CanvasWorkspacesCompanion extends UpdateCompanion<StoredCanvasWorkspace> {
  final Value<int> id;
  final Value<int> workspaceEpoch;
  final Value<int> workspaceRevision;
  final Value<int> nextCreationSequence;
  final Value<int> nextModificationSequence;
  const CanvasWorkspacesCompanion({
    this.id = const Value.absent(),
    this.workspaceEpoch = const Value.absent(),
    this.workspaceRevision = const Value.absent(),
    this.nextCreationSequence = const Value.absent(),
    this.nextModificationSequence = const Value.absent(),
  });
  CanvasWorkspacesCompanion.insert({
    this.id = const Value.absent(),
    required int workspaceEpoch,
    required int workspaceRevision,
    required int nextCreationSequence,
    required int nextModificationSequence,
  })  : workspaceEpoch = Value(workspaceEpoch),
        workspaceRevision = Value(workspaceRevision),
        nextCreationSequence = Value(nextCreationSequence),
        nextModificationSequence = Value(nextModificationSequence);
  static Insertable<StoredCanvasWorkspace> custom({
    Expression<int>? id,
    Expression<int>? workspaceEpoch,
    Expression<int>? workspaceRevision,
    Expression<int>? nextCreationSequence,
    Expression<int>? nextModificationSequence,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (workspaceEpoch != null) 'workspace_epoch': workspaceEpoch,
      if (workspaceRevision != null) 'workspace_revision': workspaceRevision,
      if (nextCreationSequence != null)
        'next_creation_sequence': nextCreationSequence,
      if (nextModificationSequence != null)
        'next_modification_sequence': nextModificationSequence,
    });
  }

  CanvasWorkspacesCompanion copyWith(
      {Value<int>? id,
      Value<int>? workspaceEpoch,
      Value<int>? workspaceRevision,
      Value<int>? nextCreationSequence,
      Value<int>? nextModificationSequence}) {
    return CanvasWorkspacesCompanion(
      id: id ?? this.id,
      workspaceEpoch: workspaceEpoch ?? this.workspaceEpoch,
      workspaceRevision: workspaceRevision ?? this.workspaceRevision,
      nextCreationSequence: nextCreationSequence ?? this.nextCreationSequence,
      nextModificationSequence:
          nextModificationSequence ?? this.nextModificationSequence,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<int>(id.value);
    }
    if (workspaceEpoch.present) {
      map['workspace_epoch'] = Variable<int>(workspaceEpoch.value);
    }
    if (workspaceRevision.present) {
      map['workspace_revision'] = Variable<int>(workspaceRevision.value);
    }
    if (nextCreationSequence.present) {
      map['next_creation_sequence'] = Variable<int>(nextCreationSequence.value);
    }
    if (nextModificationSequence.present) {
      map['next_modification_sequence'] =
          Variable<int>(nextModificationSequence.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('CanvasWorkspacesCompanion(')
          ..write('id: $id, ')
          ..write('workspaceEpoch: $workspaceEpoch, ')
          ..write('workspaceRevision: $workspaceRevision, ')
          ..write('nextCreationSequence: $nextCreationSequence, ')
          ..write('nextModificationSequence: $nextModificationSequence')
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
  late final $CanvasScenesTable canvasScenes = $CanvasScenesTable(this);
  late final $CanvasWorkspacesTable canvasWorkspaces =
      $CanvasWorkspacesTable(this);
  @override
  Iterable<TableInfo<Table, Object?>> get allTables =>
      allSchemaEntities.whereType<TableInfo<Table, Object?>>();
  @override
  List<DatabaseSchemaEntity> get allSchemaEntities => [
        studentProfiles,
        sessionHistory,
        learningProgressEvents,
        canvasScenes,
        canvasWorkspaces
      ];
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
typedef $$CanvasScenesTableCreateCompanionBuilder = CanvasScenesCompanion
    Function({
  required String sceneId,
  required int sceneRevision,
  required String generationId,
  required String sourceTurnId,
  required Uint8List svgBytes,
  required Uint8List manifestBytes,
  required String svgSha256,
  required String manifestSha256,
  required int svgByteLength,
  required int manifestByteLength,
  required String rendererCapabilityVersion,
  required String validatorVersion,
  required String manifestVersion,
  required int workspaceOrder,
  required bool isFocused,
  Value<int?> creationSequence,
  Value<int?> modificationSequence,
  Value<String?> title,
  Value<String?> summary,
  Value<int> rowid,
});
typedef $$CanvasScenesTableUpdateCompanionBuilder = CanvasScenesCompanion
    Function({
  Value<String> sceneId,
  Value<int> sceneRevision,
  Value<String> generationId,
  Value<String> sourceTurnId,
  Value<Uint8List> svgBytes,
  Value<Uint8List> manifestBytes,
  Value<String> svgSha256,
  Value<String> manifestSha256,
  Value<int> svgByteLength,
  Value<int> manifestByteLength,
  Value<String> rendererCapabilityVersion,
  Value<String> validatorVersion,
  Value<String> manifestVersion,
  Value<int> workspaceOrder,
  Value<bool> isFocused,
  Value<int?> creationSequence,
  Value<int?> modificationSequence,
  Value<String?> title,
  Value<String?> summary,
  Value<int> rowid,
});

class $$CanvasScenesTableFilterComposer
    extends Composer<_$AppDatabase, $CanvasScenesTable> {
  $$CanvasScenesTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get sceneId => $composableBuilder(
      column: $table.sceneId, builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get sceneRevision => $composableBuilder(
      column: $table.sceneRevision, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get generationId => $composableBuilder(
      column: $table.generationId, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get sourceTurnId => $composableBuilder(
      column: $table.sourceTurnId, builder: (column) => ColumnFilters(column));

  ColumnFilters<Uint8List> get svgBytes => $composableBuilder(
      column: $table.svgBytes, builder: (column) => ColumnFilters(column));

  ColumnFilters<Uint8List> get manifestBytes => $composableBuilder(
      column: $table.manifestBytes, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get svgSha256 => $composableBuilder(
      column: $table.svgSha256, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get manifestSha256 => $composableBuilder(
      column: $table.manifestSha256,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get svgByteLength => $composableBuilder(
      column: $table.svgByteLength, builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get manifestByteLength => $composableBuilder(
      column: $table.manifestByteLength,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get rendererCapabilityVersion => $composableBuilder(
      column: $table.rendererCapabilityVersion,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get validatorVersion => $composableBuilder(
      column: $table.validatorVersion,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get manifestVersion => $composableBuilder(
      column: $table.manifestVersion,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get workspaceOrder => $composableBuilder(
      column: $table.workspaceOrder,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<bool> get isFocused => $composableBuilder(
      column: $table.isFocused, builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get creationSequence => $composableBuilder(
      column: $table.creationSequence,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get modificationSequence => $composableBuilder(
      column: $table.modificationSequence,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get title => $composableBuilder(
      column: $table.title, builder: (column) => ColumnFilters(column));

  ColumnFilters<String> get summary => $composableBuilder(
      column: $table.summary, builder: (column) => ColumnFilters(column));
}

class $$CanvasScenesTableOrderingComposer
    extends Composer<_$AppDatabase, $CanvasScenesTable> {
  $$CanvasScenesTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get sceneId => $composableBuilder(
      column: $table.sceneId, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get sceneRevision => $composableBuilder(
      column: $table.sceneRevision,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get generationId => $composableBuilder(
      column: $table.generationId,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get sourceTurnId => $composableBuilder(
      column: $table.sourceTurnId,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<Uint8List> get svgBytes => $composableBuilder(
      column: $table.svgBytes, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<Uint8List> get manifestBytes => $composableBuilder(
      column: $table.manifestBytes,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get svgSha256 => $composableBuilder(
      column: $table.svgSha256, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get manifestSha256 => $composableBuilder(
      column: $table.manifestSha256,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get svgByteLength => $composableBuilder(
      column: $table.svgByteLength,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get manifestByteLength => $composableBuilder(
      column: $table.manifestByteLength,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get rendererCapabilityVersion => $composableBuilder(
      column: $table.rendererCapabilityVersion,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get validatorVersion => $composableBuilder(
      column: $table.validatorVersion,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get manifestVersion => $composableBuilder(
      column: $table.manifestVersion,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get workspaceOrder => $composableBuilder(
      column: $table.workspaceOrder,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<bool> get isFocused => $composableBuilder(
      column: $table.isFocused, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get creationSequence => $composableBuilder(
      column: $table.creationSequence,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get modificationSequence => $composableBuilder(
      column: $table.modificationSequence,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get title => $composableBuilder(
      column: $table.title, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<String> get summary => $composableBuilder(
      column: $table.summary, builder: (column) => ColumnOrderings(column));
}

class $$CanvasScenesTableAnnotationComposer
    extends Composer<_$AppDatabase, $CanvasScenesTable> {
  $$CanvasScenesTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get sceneId =>
      $composableBuilder(column: $table.sceneId, builder: (column) => column);

  GeneratedColumn<int> get sceneRevision => $composableBuilder(
      column: $table.sceneRevision, builder: (column) => column);

  GeneratedColumn<String> get generationId => $composableBuilder(
      column: $table.generationId, builder: (column) => column);

  GeneratedColumn<String> get sourceTurnId => $composableBuilder(
      column: $table.sourceTurnId, builder: (column) => column);

  GeneratedColumn<Uint8List> get svgBytes =>
      $composableBuilder(column: $table.svgBytes, builder: (column) => column);

  GeneratedColumn<Uint8List> get manifestBytes => $composableBuilder(
      column: $table.manifestBytes, builder: (column) => column);

  GeneratedColumn<String> get svgSha256 =>
      $composableBuilder(column: $table.svgSha256, builder: (column) => column);

  GeneratedColumn<String> get manifestSha256 => $composableBuilder(
      column: $table.manifestSha256, builder: (column) => column);

  GeneratedColumn<int> get svgByteLength => $composableBuilder(
      column: $table.svgByteLength, builder: (column) => column);

  GeneratedColumn<int> get manifestByteLength => $composableBuilder(
      column: $table.manifestByteLength, builder: (column) => column);

  GeneratedColumn<String> get rendererCapabilityVersion => $composableBuilder(
      column: $table.rendererCapabilityVersion, builder: (column) => column);

  GeneratedColumn<String> get validatorVersion => $composableBuilder(
      column: $table.validatorVersion, builder: (column) => column);

  GeneratedColumn<String> get manifestVersion => $composableBuilder(
      column: $table.manifestVersion, builder: (column) => column);

  GeneratedColumn<int> get workspaceOrder => $composableBuilder(
      column: $table.workspaceOrder, builder: (column) => column);

  GeneratedColumn<bool> get isFocused =>
      $composableBuilder(column: $table.isFocused, builder: (column) => column);

  GeneratedColumn<int> get creationSequence => $composableBuilder(
      column: $table.creationSequence, builder: (column) => column);

  GeneratedColumn<int> get modificationSequence => $composableBuilder(
      column: $table.modificationSequence, builder: (column) => column);

  GeneratedColumn<String> get title =>
      $composableBuilder(column: $table.title, builder: (column) => column);

  GeneratedColumn<String> get summary =>
      $composableBuilder(column: $table.summary, builder: (column) => column);
}

class $$CanvasScenesTableTableManager extends RootTableManager<
    _$AppDatabase,
    $CanvasScenesTable,
    StoredCanvasScene,
    $$CanvasScenesTableFilterComposer,
    $$CanvasScenesTableOrderingComposer,
    $$CanvasScenesTableAnnotationComposer,
    $$CanvasScenesTableCreateCompanionBuilder,
    $$CanvasScenesTableUpdateCompanionBuilder,
    (
      StoredCanvasScene,
      BaseReferences<_$AppDatabase, $CanvasScenesTable, StoredCanvasScene>
    ),
    StoredCanvasScene,
    PrefetchHooks Function()> {
  $$CanvasScenesTableTableManager(_$AppDatabase db, $CanvasScenesTable table)
      : super(TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$CanvasScenesTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$CanvasScenesTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$CanvasScenesTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback: ({
            Value<String> sceneId = const Value.absent(),
            Value<int> sceneRevision = const Value.absent(),
            Value<String> generationId = const Value.absent(),
            Value<String> sourceTurnId = const Value.absent(),
            Value<Uint8List> svgBytes = const Value.absent(),
            Value<Uint8List> manifestBytes = const Value.absent(),
            Value<String> svgSha256 = const Value.absent(),
            Value<String> manifestSha256 = const Value.absent(),
            Value<int> svgByteLength = const Value.absent(),
            Value<int> manifestByteLength = const Value.absent(),
            Value<String> rendererCapabilityVersion = const Value.absent(),
            Value<String> validatorVersion = const Value.absent(),
            Value<String> manifestVersion = const Value.absent(),
            Value<int> workspaceOrder = const Value.absent(),
            Value<bool> isFocused = const Value.absent(),
            Value<int?> creationSequence = const Value.absent(),
            Value<int?> modificationSequence = const Value.absent(),
            Value<String?> title = const Value.absent(),
            Value<String?> summary = const Value.absent(),
            Value<int> rowid = const Value.absent(),
          }) =>
              CanvasScenesCompanion(
            sceneId: sceneId,
            sceneRevision: sceneRevision,
            generationId: generationId,
            sourceTurnId: sourceTurnId,
            svgBytes: svgBytes,
            manifestBytes: manifestBytes,
            svgSha256: svgSha256,
            manifestSha256: manifestSha256,
            svgByteLength: svgByteLength,
            manifestByteLength: manifestByteLength,
            rendererCapabilityVersion: rendererCapabilityVersion,
            validatorVersion: validatorVersion,
            manifestVersion: manifestVersion,
            workspaceOrder: workspaceOrder,
            isFocused: isFocused,
            creationSequence: creationSequence,
            modificationSequence: modificationSequence,
            title: title,
            summary: summary,
            rowid: rowid,
          ),
          createCompanionCallback: ({
            required String sceneId,
            required int sceneRevision,
            required String generationId,
            required String sourceTurnId,
            required Uint8List svgBytes,
            required Uint8List manifestBytes,
            required String svgSha256,
            required String manifestSha256,
            required int svgByteLength,
            required int manifestByteLength,
            required String rendererCapabilityVersion,
            required String validatorVersion,
            required String manifestVersion,
            required int workspaceOrder,
            required bool isFocused,
            Value<int?> creationSequence = const Value.absent(),
            Value<int?> modificationSequence = const Value.absent(),
            Value<String?> title = const Value.absent(),
            Value<String?> summary = const Value.absent(),
            Value<int> rowid = const Value.absent(),
          }) =>
              CanvasScenesCompanion.insert(
            sceneId: sceneId,
            sceneRevision: sceneRevision,
            generationId: generationId,
            sourceTurnId: sourceTurnId,
            svgBytes: svgBytes,
            manifestBytes: manifestBytes,
            svgSha256: svgSha256,
            manifestSha256: manifestSha256,
            svgByteLength: svgByteLength,
            manifestByteLength: manifestByteLength,
            rendererCapabilityVersion: rendererCapabilityVersion,
            validatorVersion: validatorVersion,
            manifestVersion: manifestVersion,
            workspaceOrder: workspaceOrder,
            isFocused: isFocused,
            creationSequence: creationSequence,
            modificationSequence: modificationSequence,
            title: title,
            summary: summary,
            rowid: rowid,
          ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ));
}

typedef $$CanvasScenesTableProcessedTableManager = ProcessedTableManager<
    _$AppDatabase,
    $CanvasScenesTable,
    StoredCanvasScene,
    $$CanvasScenesTableFilterComposer,
    $$CanvasScenesTableOrderingComposer,
    $$CanvasScenesTableAnnotationComposer,
    $$CanvasScenesTableCreateCompanionBuilder,
    $$CanvasScenesTableUpdateCompanionBuilder,
    (
      StoredCanvasScene,
      BaseReferences<_$AppDatabase, $CanvasScenesTable, StoredCanvasScene>
    ),
    StoredCanvasScene,
    PrefetchHooks Function()>;
typedef $$CanvasWorkspacesTableCreateCompanionBuilder
    = CanvasWorkspacesCompanion Function({
  Value<int> id,
  required int workspaceEpoch,
  required int workspaceRevision,
  required int nextCreationSequence,
  required int nextModificationSequence,
});
typedef $$CanvasWorkspacesTableUpdateCompanionBuilder
    = CanvasWorkspacesCompanion Function({
  Value<int> id,
  Value<int> workspaceEpoch,
  Value<int> workspaceRevision,
  Value<int> nextCreationSequence,
  Value<int> nextModificationSequence,
});

class $$CanvasWorkspacesTableFilterComposer
    extends Composer<_$AppDatabase, $CanvasWorkspacesTable> {
  $$CanvasWorkspacesTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<int> get id => $composableBuilder(
      column: $table.id, builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get workspaceEpoch => $composableBuilder(
      column: $table.workspaceEpoch,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get workspaceRevision => $composableBuilder(
      column: $table.workspaceRevision,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get nextCreationSequence => $composableBuilder(
      column: $table.nextCreationSequence,
      builder: (column) => ColumnFilters(column));

  ColumnFilters<int> get nextModificationSequence => $composableBuilder(
      column: $table.nextModificationSequence,
      builder: (column) => ColumnFilters(column));
}

class $$CanvasWorkspacesTableOrderingComposer
    extends Composer<_$AppDatabase, $CanvasWorkspacesTable> {
  $$CanvasWorkspacesTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<int> get id => $composableBuilder(
      column: $table.id, builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get workspaceEpoch => $composableBuilder(
      column: $table.workspaceEpoch,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get workspaceRevision => $composableBuilder(
      column: $table.workspaceRevision,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get nextCreationSequence => $composableBuilder(
      column: $table.nextCreationSequence,
      builder: (column) => ColumnOrderings(column));

  ColumnOrderings<int> get nextModificationSequence => $composableBuilder(
      column: $table.nextModificationSequence,
      builder: (column) => ColumnOrderings(column));
}

class $$CanvasWorkspacesTableAnnotationComposer
    extends Composer<_$AppDatabase, $CanvasWorkspacesTable> {
  $$CanvasWorkspacesTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<int> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<int> get workspaceEpoch => $composableBuilder(
      column: $table.workspaceEpoch, builder: (column) => column);

  GeneratedColumn<int> get workspaceRevision => $composableBuilder(
      column: $table.workspaceRevision, builder: (column) => column);

  GeneratedColumn<int> get nextCreationSequence => $composableBuilder(
      column: $table.nextCreationSequence, builder: (column) => column);

  GeneratedColumn<int> get nextModificationSequence => $composableBuilder(
      column: $table.nextModificationSequence, builder: (column) => column);
}

class $$CanvasWorkspacesTableTableManager extends RootTableManager<
    _$AppDatabase,
    $CanvasWorkspacesTable,
    StoredCanvasWorkspace,
    $$CanvasWorkspacesTableFilterComposer,
    $$CanvasWorkspacesTableOrderingComposer,
    $$CanvasWorkspacesTableAnnotationComposer,
    $$CanvasWorkspacesTableCreateCompanionBuilder,
    $$CanvasWorkspacesTableUpdateCompanionBuilder,
    (
      StoredCanvasWorkspace,
      BaseReferences<_$AppDatabase, $CanvasWorkspacesTable,
          StoredCanvasWorkspace>
    ),
    StoredCanvasWorkspace,
    PrefetchHooks Function()> {
  $$CanvasWorkspacesTableTableManager(
      _$AppDatabase db, $CanvasWorkspacesTable table)
      : super(TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$CanvasWorkspacesTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$CanvasWorkspacesTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$CanvasWorkspacesTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback: ({
            Value<int> id = const Value.absent(),
            Value<int> workspaceEpoch = const Value.absent(),
            Value<int> workspaceRevision = const Value.absent(),
            Value<int> nextCreationSequence = const Value.absent(),
            Value<int> nextModificationSequence = const Value.absent(),
          }) =>
              CanvasWorkspacesCompanion(
            id: id,
            workspaceEpoch: workspaceEpoch,
            workspaceRevision: workspaceRevision,
            nextCreationSequence: nextCreationSequence,
            nextModificationSequence: nextModificationSequence,
          ),
          createCompanionCallback: ({
            Value<int> id = const Value.absent(),
            required int workspaceEpoch,
            required int workspaceRevision,
            required int nextCreationSequence,
            required int nextModificationSequence,
          }) =>
              CanvasWorkspacesCompanion.insert(
            id: id,
            workspaceEpoch: workspaceEpoch,
            workspaceRevision: workspaceRevision,
            nextCreationSequence: nextCreationSequence,
            nextModificationSequence: nextModificationSequence,
          ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ));
}

typedef $$CanvasWorkspacesTableProcessedTableManager = ProcessedTableManager<
    _$AppDatabase,
    $CanvasWorkspacesTable,
    StoredCanvasWorkspace,
    $$CanvasWorkspacesTableFilterComposer,
    $$CanvasWorkspacesTableOrderingComposer,
    $$CanvasWorkspacesTableAnnotationComposer,
    $$CanvasWorkspacesTableCreateCompanionBuilder,
    $$CanvasWorkspacesTableUpdateCompanionBuilder,
    (
      StoredCanvasWorkspace,
      BaseReferences<_$AppDatabase, $CanvasWorkspacesTable,
          StoredCanvasWorkspace>
    ),
    StoredCanvasWorkspace,
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
  $$CanvasScenesTableTableManager get canvasScenes =>
      $$CanvasScenesTableTableManager(_db, _db.canvasScenes);
  $$CanvasWorkspacesTableTableManager get canvasWorkspaces =>
      $$CanvasWorkspacesTableTableManager(_db, _db.canvasWorkspaces);
}
