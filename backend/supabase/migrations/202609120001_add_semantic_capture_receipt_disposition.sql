alter table ava_private.semantic_memory_receipts
  add column if not exists disposition text;

update ava_private.semantic_memory_receipts
  set disposition = 'changed'
  where disposition is null;

alter table ava_private.semantic_memory_receipts
  alter column disposition set not null;

alter table ava_private.semantic_memory_receipts
  drop constraint if exists semantic_memory_receipts_disposition_check;

alter table ava_private.semantic_memory_receipts
  add constraint semantic_memory_receipts_disposition_check
    check (disposition in ('changed', 'noChange'));

insert into ava_private.schema_migrations (name, version)
values ('semantic_capture_receipt_disposition', 1)
on conflict (name) do update
set version = excluded.version,
    applied_at = now();
