drop table if exists ava_private.weekly_records;

insert into ava_private.schema_migrations (name, version)
values ('realtime_memory_v6', 1)
on conflict (name) do update
set version = excluded.version,
    applied_at = now();
