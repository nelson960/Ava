create schema if not exists ava_private;

create table if not exists ava_private.schema_migrations (
  name text primary key,
  version integer not null check (version > 0),
  applied_at timestamptz not null default now()
);

create table if not exists ava_private.memory_state (
  subject text primary key
    check (
      char_length(subject) between 1 and 256
      and subject ~ '^[A-Za-z0-9._:@-]+$'
    ),
  current_session_summary text
    check (
      current_session_summary is null
      or char_length(btrim(current_session_summary)) between 1 and 1200
    ),
  reset_revision bigint not null default 0 check (reset_revision >= 0),
  updated_at timestamptz not null default now()
);

create table if not exists ava_private.turn_receipts (
  subject text not null references ava_private.memory_state(subject)
    on delete cascade,
  turn_id text not null
    check (turn_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,63}$'),
  committed_at timestamptz not null default now(),
  primary key (subject, turn_id)
);

create table if not exists ava_private.delivered_turns (
  subject text not null references ava_private.memory_state(subject)
    on delete cascade,
  turn_id text not null,
  child_text text not null
    check (char_length(btrim(child_text)) between 1 and 2000),
  child_text_source text not null
    check (child_text_source in ('transcript', 'modelSummary')),
  ava_delivered_text text not null
    check (char_length(ava_delivered_text) <= 1800),
  delivered_sample_count bigint not null
    check (delivered_sample_count between 0 and 2147483647),
  child_turn_summary text not null
    check (char_length(btrim(child_turn_summary)) between 1 and 240),
  terminal text not null
    check (terminal in ('completed', 'cancelled', 'failed')),
  occurred_at timestamptz not null default now(),
  primary key (subject, turn_id),
  foreign key (subject, turn_id)
    references ava_private.turn_receipts(subject, turn_id)
    on delete cascade,
  check (
    ava_delivered_text = ''
    or delivered_sample_count > 0
  )
);

create index if not exists delivered_turns_subject_recent_idx
  on ava_private.delivered_turns (subject, occurred_at desc, turn_id desc);

create table if not exists ava_private.stable_memories (
  subject text not null references ava_private.memory_state(subject)
    on delete cascade,
  memory_id text not null
    check (memory_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,63}$'),
  kind text not null check (kind in ('interest', 'preference')),
  summary text not null
    check (char_length(btrim(summary)) between 1 and 160),
  evidence text not null
    check (char_length(btrim(evidence)) between 1 and 160),
  source_turn_id text not null,
  created_at timestamptz not null default now(),
  primary key (subject, memory_id),
  foreign key (subject, source_turn_id)
    references ava_private.turn_receipts(subject, turn_id)
    on delete cascade
);

create unique index if not exists stable_memories_subject_semantic_idx
  on ava_private.stable_memories (subject, kind, lower(summary));

create index if not exists stable_memories_subject_recent_idx
  on ava_private.stable_memories (subject, created_at desc, memory_id desc);

create table if not exists ava_private.weekly_records (
  subject text not null references ava_private.memory_state(subject)
    on delete cascade,
  turn_id text not null,
  summary text not null
    check (char_length(btrim(summary)) between 1 and 240),
  occurred_at timestamptz not null default now(),
  primary key (subject, turn_id),
  foreign key (subject, turn_id)
    references ava_private.turn_receipts(subject, turn_id)
    on delete cascade
);

create index if not exists weekly_records_subject_period_idx
  on ava_private.weekly_records (subject, occurred_at asc, turn_id asc);

create table if not exists ava_private.reset_log (
  subject text not null
    check (
      char_length(subject) between 1 and 256
      and subject ~ '^[A-Za-z0-9._:@-]+$'
    ),
  revision bigint not null check (revision > 0),
  reset_at timestamptz not null default now(),
  primary key (subject, revision)
);

insert into ava_private.schema_migrations (name, version)
values ('realtime_memory_v3', 1)
on conflict (name) do update
set version = excluded.version,
    applied_at = now();
