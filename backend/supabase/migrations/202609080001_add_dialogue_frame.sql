alter table ava_private.memory_state
  add column if not exists dialogue_frame_revision bigint not null default 0,
  add column if not exists dialogue_frame_payload jsonb not null default '{}'::jsonb,
  add column if not exists dialogue_frame_last_source_turn_id text;

alter table ava_private.memory_state
  drop constraint if exists memory_state_dialogue_frame_revision_check,
  drop constraint if exists memory_state_dialogue_frame_payload_check,
  drop constraint if exists memory_state_dialogue_frame_state_check,
  add constraint memory_state_dialogue_frame_revision_check
    check (dialogue_frame_revision >= 0),
  add constraint memory_state_dialogue_frame_payload_check
    check (
      jsonb_typeof(dialogue_frame_payload) = 'object'
      and octet_length(dialogue_frame_payload::text) <= 8192
    ),
  add constraint memory_state_dialogue_frame_state_check
    check (
      (dialogue_frame_revision = 0
        and dialogue_frame_payload = '{}'::jsonb
        and dialogue_frame_last_source_turn_id is null)
      or
      (dialogue_frame_revision > 0
        and dialogue_frame_last_source_turn_id is not null)
    );

create table if not exists ava_private.dialogue_frame_receipts (
  subject text not null references ava_private.memory_state(subject)
    on delete cascade,
  source_turn_id text not null,
  base_revision bigint not null check (base_revision >= 0),
  applied_revision bigint not null check (applied_revision = base_revision + 1),
  update_fingerprint text not null
    check (update_fingerprint ~ '^[0-9a-f]{64}$'),
  applied_at timestamptz not null default now(),
  primary key (subject, source_turn_id),
  unique (subject, applied_revision),
  foreign key (subject, source_turn_id)
    references ava_private.turn_receipts(subject, turn_id)
    on delete cascade
);

insert into ava_private.schema_migrations (name, version)
values ('realtime_memory_v7', 1)
on conflict (name) do update
set version = excluded.version,
    applied_at = now();
