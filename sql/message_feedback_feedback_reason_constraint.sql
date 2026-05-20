-- Optional: align or remove feedback_reason CHECK on public.message_feedback.
--
-- If you already dropped the constraint in Supabase, nothing else is required — gateway
-- still normalizes values in app/services/message_feedback_service.py.
--
-- Step 1 only: remove legacy CHECK (safe to re-run)
alter table public.message_feedback
  drop constraint if exists message_feedback_feedback_type_check;

alter table public.message_feedback
  drop constraint if exists message_feedback_feedback_reason_check;

-- Step 2 (OPTIONAL): re-add CHECK matching gateway enums — uncomment to enable
/*
alter table public.message_feedback
  add constraint message_feedback_feedback_reason_check
  check (
    feedback_reason is null
    or feedback_reason = any (
      array[
        'biased'::text,
        'incomplete_instructions'::text,
        'not_factual'::text,
        'not_relevant'::text,
        'other'::text,
        'style_tone'::text,
        'unsafe'::text
      ]
    )
  );
*/
