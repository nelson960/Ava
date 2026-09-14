-- Historical timestamp/ID ties have no recoverable causal order. This is a
-- deterministic approximation for legacy rows only; new commits allocate order.
alter table ava_private.memory_state
  add column if not exists last_turn_sequence bigint not null default 0
    check (last_turn_sequence >= 0);

alter table ava_private.delivered_turns
  add column if not exists reset_revision bigint,
  add column if not exists turn_sequence bigint,
  add column if not exists tool_exchanges jsonb not null default '[]'::jsonb;

with ordered as (
  select t.subject, t.turn_id, s.reset_revision,
         row_number() over (partition by t.subject order by t.occurred_at, t.turn_id) as sequence
  from ava_private.delivered_turns t
  join ava_private.memory_state s using (subject)
)
update ava_private.delivered_turns t
set reset_revision = o.reset_revision, turn_sequence = o.sequence
from ordered o
where t.subject = o.subject and t.turn_id = o.turn_id and t.turn_sequence is null;

update ava_private.memory_state s
set last_turn_sequence = greatest(s.last_turn_sequence, latest.sequence)
from (select subject, max(turn_sequence) as sequence
      from ava_private.delivered_turns group by subject) latest
where s.subject = latest.subject;

alter table ava_private.delivered_turns
  alter column reset_revision set not null,
  alter column turn_sequence set not null;

create unique index if not exists delivered_turns_logical_order_idx
  on ava_private.delivered_turns (subject, reset_revision, turn_sequence);

alter table ava_private.delivered_turns
  drop constraint if exists delivered_turns_timeline_check,
  add constraint delivered_turns_timeline_check check (
    reset_revision >= 0 and turn_sequence > 0
    and jsonb_typeof(tool_exchanges) = 'array'
    and jsonb_array_length(tool_exchanges) <= 2
  );

insert into ava_private.schema_migrations (name, version)
values ('durable_turn_timeline', 1)
on conflict (name) do update set version = excluded.version, applied_at = now();
