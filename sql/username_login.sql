-- Run in Supabase SQL Editor (once per project).
-- Lets the gateway resolve username → email for login without the service_role key.

create or replace function public.get_email_for_username(p_username text)
returns text
language sql
security definer
stable
set search_path = public
as $$
  select email
  from public.profiles
  where username = p_username
  limit 1;
$$;

revoke all on function public.get_email_for_username(text) from public;
grant execute on function public.get_email_for_username(text) to anon, authenticated, service_role;
