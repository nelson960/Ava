create table if not exists ava_private.semantic_journal_events (
  event_id text primary key
    check (event_id ~ '^sje-[0-9a-f]{60}$'),
  subject text not null references ava_private.memory_state(subject)
    on delete cascade,
  reset_revision bigint not null check (reset_revision >= 0),
  family text not null
    check (family in (
      'userPreference', 'avaCommitment', 'userFact', 'canvasReference'
    )),
  semantic_key text not null
    check (
      char_length(semantic_key) between 1 and 96
      and semantic_key ~ '^[a-z][a-z0-9._:-]{0,95}$'
    ),
  summary text not null
    check (
      char_length(btrim(summary)) between 1 and 240
      and octet_length(summary) <= 960
    ),
  identity_key text not null
    check (
      char_length(identity_key) between 1 and 128
      and identity_key = family || ':' || semantic_key
    ),
  fingerprint text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
  canonical_canvas_references jsonb not null default '[]'::jsonb
    check (
      jsonb_typeof(canonical_canvas_references) = 'array'
      and jsonb_array_length(canonical_canvas_references) <= 4
      and octet_length(canonical_canvas_references::text) <= 4096
    ),
  created_at timestamptz not null default now(),
  unique (subject, reset_revision, event_id)
);

create index if not exists semantic_journal_subject_identity_idx
  on ava_private.semantic_journal_events
    (subject, reset_revision, family, semantic_key, created_at, event_id);

create index if not exists semantic_journal_subject_content_idx
  on ava_private.semantic_journal_events
    (subject, reset_revision, identity_key, fingerprint);

create table if not exists ava_private.semantic_journal_event_sources (
  subject text not null,
  reset_revision bigint not null check (reset_revision >= 0),
  event_id text not null,
  source_turn_id text not null,
  attached_at timestamptz not null default now(),
  primary key (subject, event_id, source_turn_id),
  foreign key (subject, reset_revision, event_id)
    references ava_private.semantic_journal_events(
      subject, reset_revision, event_id
    ) on delete cascade,
  foreign key (subject, source_turn_id)
    references ava_private.turn_receipts(subject, turn_id)
    on delete cascade
);

create index if not exists semantic_journal_sources_turn_idx
  on ava_private.semantic_journal_event_sources (subject, source_turn_id);

create table if not exists ava_private.semantic_journal_supersessions (
  subject text not null,
  reset_revision bigint not null check (reset_revision >= 0),
  superseded_event_id text not null,
  superseding_event_id text not null,
  created_at timestamptz not null default now(),
  primary key (subject, superseded_event_id),
  foreign key (subject, reset_revision, superseded_event_id)
    references ava_private.semantic_journal_events(
      subject, reset_revision, event_id
    ) on delete cascade,
  foreign key (subject, reset_revision, superseding_event_id)
    references ava_private.semantic_journal_events(
      subject, reset_revision, event_id
    ) on delete cascade,
  check (superseded_event_id <> superseding_event_id)
);

create index if not exists semantic_journal_superseding_event_idx
  on ava_private.semantic_journal_supersessions
    (subject, reset_revision, superseding_event_id);

create table if not exists ava_private.semantic_memory_receipts (
  subject text not null references ava_private.memory_state(subject)
    on delete cascade,
  source_turn_id text not null,
  reset_revision bigint not null check (reset_revision >= 0),
  proposal_fingerprint text not null
    check (proposal_fingerprint ~ '^[0-9a-f]{64}$'),
  result_event_ids text[] not null default '{}'
    check (cardinality(result_event_ids) between 0 and 4),
  result_dispositions text[] not null default '{}'
    check (
      cardinality(result_dispositions) = cardinality(result_event_ids)
      and result_dispositions <@ array['created', 'deduplicated']::text[]
    ),
  resulting_frame_revision bigint not null
    check (resulting_frame_revision >= 0),
  applied_at timestamptz not null default now(),
  primary key (subject, source_turn_id),
  foreign key (subject, source_turn_id)
    references ava_private.turn_receipts(subject, turn_id)
    on delete cascade
);

insert into ava_private.schema_migrations (name, version)
values ('realtime_memory_v8', 1)
on conflict (name) do update
set version = excluded.version,
    applied_at = now();
