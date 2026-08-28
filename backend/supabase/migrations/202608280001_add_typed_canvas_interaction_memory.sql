alter table ava_private.delivered_turns
  add column if not exists input_kind text not null default 'speech',
  add column if not exists canvas_scene_id text,
  add column if not exists canvas_scene_revision bigint,
  add column if not exists canvas_interaction_revision bigint,
  add column if not exists canvas_interaction_mode text,
  add column if not exists canvas_binding_id text,
  add column if not exists canvas_target_id text,
  add column if not exists canvas_accessibility_label text,
  add column if not exists canvas_step_index integer;

alter table ava_private.delivered_turns
  alter column child_text drop not null,
  alter column child_text_source drop not null;

alter table ava_private.delivered_turns
  drop constraint if exists delivered_turns_input_kind_check,
  drop constraint if exists delivered_turns_input_atomicity_check;

alter table ava_private.delivered_turns
  add constraint delivered_turns_input_kind_check
    check (input_kind in ('speech', 'canvasInteraction')),
  add constraint delivered_turns_input_atomicity_check
    check (
      (
        input_kind = 'speech'
        and child_text is not null
        and child_text_source is not null
        and canvas_scene_id is null
        and canvas_scene_revision is null
        and canvas_interaction_revision is null
        and canvas_interaction_mode is null
        and canvas_binding_id is null
        and canvas_target_id is null
        and canvas_accessibility_label is null
        and canvas_step_index is null
      )
      or
      (
        input_kind = 'canvasInteraction'
        and child_text is null
        and child_text_source is null
        and canvas_scene_id is not null
        and char_length(btrim(canvas_scene_id)) > 0
        and canvas_scene_revision is not null
        and canvas_scene_revision between 1 and 10001
        and canvas_interaction_revision is not null
        and canvas_interaction_revision between 1 and 2147483647
        and canvas_interaction_mode is not null
        and canvas_interaction_mode in ('explore', 'select', 'steps')
        and canvas_binding_id is not null
        and char_length(btrim(canvas_binding_id)) > 0
        and canvas_target_id is not null
        and char_length(btrim(canvas_target_id)) > 0
        and canvas_accessibility_label is not null
        and char_length(btrim(canvas_accessibility_label)) between 1 and 160
        and (
          (
            canvas_interaction_mode = 'steps'
            and canvas_step_index is not null
            and canvas_step_index between 1 and 12
          )
          or (
            canvas_interaction_mode in ('explore', 'select')
            and canvas_step_index is null
          )
        )
      )
    );

insert into ava_private.schema_migrations (name, version)
values ('realtime_memory_v4', 1)
on conflict (name) do update
set version = excluded.version,
    applied_at = now();
