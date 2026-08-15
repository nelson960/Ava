create table if not exists public.ava_conversation_turns (
  id bigint generated always as identity primary key,
  subject text not null
    check (char_length(subject) between 1 and 256),
  child_text text not null
    check (char_length(btrim(child_text)) between 1 and 2000),
  ava_text text not null
    check (char_length(btrim(ava_text)) between 1 and 1800),
  created_at timestamptz not null default now()
);

create index if not exists ava_conversation_turns_subject_recent_idx
  on public.ava_conversation_turns (subject, created_at desc, id desc);

alter table public.ava_conversation_turns enable row level security;

revoke all on table public.ava_conversation_turns from anon, authenticated;
grant select, insert, delete on table public.ava_conversation_turns to service_role;
grant usage, select on sequence public.ava_conversation_turns_id_seq to service_role;
