-- Row-level security for message_feedback (run in Supabase SQL editor).
-- Requires public.messages and public.conversations with existing RLS.

alter table public.message_feedback enable row level security;

drop policy if exists message_feedback_select_own on public.message_feedback;
create policy message_feedback_select_own on public.message_feedback
  for select using (user_id = auth.uid());

drop policy if exists message_feedback_insert_own on public.message_feedback;
create policy message_feedback_insert_own on public.message_feedback
  for insert with check (
    user_id = auth.uid()
    and exists (
      select 1 from public.conversations c
      where c.id = message_feedback.conversation_id and c.user_id = auth.uid()
    )
    and exists (
      select 1 from public.messages m
      where m.id = message_feedback.message_id
        and m.conversation_id = message_feedback.conversation_id
    )
  );
