-- One-time cleanup: move historical thumbs labels out of feedback_type into metadata.rating.
-- Do NOT add thumbs_up/thumbs_down to message_feedback_feedback_type_check.

update public.message_feedback
set
  metadata = coalesce(metadata, '{}'::jsonb)
    || jsonb_build_object('rating', feedback_type),
  feedback_type = null
where feedback_type in ('thumbs_up', 'thumbs_down');
