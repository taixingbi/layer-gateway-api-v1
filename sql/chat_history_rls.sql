-- Row-level security for conversations and messages (run in Supabase SQL editor).
-- Assumes tables already exist per project ERD.

alter table public.conversations enable row level security;
alter table public.messages enable row level security;

-- conversations: owner-only access
drop policy if exists conversations_select_own on public.conversations;
create policy conversations_select_own on public.conversations
  for select using (user_id = auth.uid());

drop policy if exists conversations_insert_own on public.conversations;
create policy conversations_insert_own on public.conversations
  for insert with check (user_id = auth.uid());

drop policy if exists conversations_update_own on public.conversations;
create policy conversations_update_own on public.conversations
  for update using (user_id = auth.uid());

-- messages: only within owned conversations
drop policy if exists messages_select_own on public.messages;
create policy messages_select_own on public.messages
  for select using (
    exists (
      select 1 from public.conversations c
      where c.id = messages.conversation_id and c.user_id = auth.uid()
    )
  );

drop policy if exists messages_insert_own on public.messages;
create policy messages_insert_own on public.messages
  for insert with check (
    exists (
      select 1 from public.conversations c
      where c.id = messages.conversation_id and c.user_id = auth.uid()
    )
  );

-- Bump conversation.updated_at when a message is inserted
create or replace function public.touch_conversation_updated_at()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.conversations
  set updated_at = now()
  where id = new.conversation_id;
  return new;
end;
$$;

drop trigger if exists messages_touch_conversation_updated_at on public.messages;
create trigger messages_touch_conversation_updated_at
  after insert on public.messages
  for each row execute function public.touch_conversation_updated_at();
