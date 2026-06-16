-- Guest / visitor chat audit log (service_role only; not normal conversations).
-- Run in Supabase SQL editor after conversations/messages exist.

create table if not exists public.chat_events (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),

  auth_guest boolean not null default true,
  user_type text not null default 'guest',

  session_id text,
  trace_id text,
  request_id text,
  conversation_id text,

  prompt text not null,
  prompt_chars int4,

  route text,
  answer_preview text,
  latency_ms jsonb,

  client_ip inet,
  user_agent text
);

create index if not exists chat_events_guest_created_idx
  on public.chat_events (auth_guest, created_at desc);

create index if not exists chat_events_trace_id_idx
  on public.chat_events (trace_id)
  where trace_id is not null;

create index if not exists chat_events_session_id_idx
  on public.chat_events (session_id)
  where session_id is not null;

alter table public.chat_events enable row level security;
-- No policies: gateway uses SUPABASE_SERVICE_KEY only.
