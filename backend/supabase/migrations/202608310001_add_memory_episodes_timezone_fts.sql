alter table ava_private.delivered_turns
  add column if not exists timezone_id text not null default 'UTC';

comment on column ava_private.delivered_turns.timezone_id is
  'Validated device IANA timezone. UTC on rows predating device timezone transport is migration compatibility, not inferred location.';

alter table ava_private.delivered_turns
  drop constraint if exists delivered_turns_timezone_id_check,
  add constraint delivered_turns_timezone_id_check
    check (char_length(btrim(timezone_id)) between 1 and 64);

alter table ava_private.delivered_turns
  alter column timezone_id drop default;

create table if not exists ava_private.conversation_episodes (
  subject text not null references ava_private.memory_state(subject)
    on delete cascade,
  episode_id text not null
    check (episode_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,63}$'),
  started_at timestamptz not null,
  ended_at timestamptz not null,
  timezone_id text not null
    check (char_length(btrim(timezone_id)) between 1 and 64),
  digest text not null
    check (char_length(btrim(digest)) between 1 and 32768),
  source_turn_ids text[] not null,
  source_event_count integer not null check (source_event_count between 1 and 32),
  created_at timestamptz not null default now(),
  primary key (subject, episode_id),
  check (ended_at >= started_at),
  check (cardinality(source_turn_ids) = source_event_count)
);

alter table ava_private.conversation_episodes
  drop constraint if exists conversation_episodes_timezone_id_check,
  add constraint conversation_episodes_timezone_id_check
    check (char_length(btrim(timezone_id)) between 1 and 64);

create index if not exists conversation_episodes_subject_recent_idx
  on ava_private.conversation_episodes
    (subject, ended_at desc, episode_id desc);

create index if not exists conversation_episodes_source_turn_ids_idx
  on ava_private.conversation_episodes using gin (source_turn_ids);

create index if not exists delivered_turns_search_idx
  on ava_private.delivered_turns using gin (
    to_tsvector(
      'simple',
      coalesce(child_text, '') || ' ' ||
      child_turn_summary || ' ' ||
      ava_delivered_text || ' ' ||
      coalesce(canvas_accessibility_label, '')
    )
  );

create index if not exists conversation_episodes_search_idx
  on ava_private.conversation_episodes using gin (
    to_tsvector('simple', digest)
  );

create index if not exists stable_memories_search_idx
  on ava_private.stable_memories using gin (
    to_tsvector('simple', summary || ' ' || evidence)
  );

insert into ava_private.schema_migrations (name, version)
values ('realtime_memory_v5', 1)
on conflict (name) do update
set version = excluded.version,
    applied_at = now();
