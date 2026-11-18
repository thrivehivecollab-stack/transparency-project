-- Transparency Project: core schema
-- Everything collected lands as 'pending'. Nothing is public until an admin approves it.

create extension if not exists pgcrypto with schema extensions;

-- People allowed to review and publish
create table if not exists admins (
  email text primary key
);

create or replace function is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from admins where lower(email) = lower(coalesce(auth.jwt() ->> 'email', '')));
$$;

-- Every record or finding the system produces
create table if not exists items (
  id            bigserial primary key,
  tracker       text not null,                 -- e.g. house_trades, fec_influencer, usaspending_persuasion
  kind          text not null default 'record' check (kind in ('record', 'finding')),
  source_id     text not null,                 -- unique id inside the source system
  statement     text not null,                 -- exact sentence that gets published if approved
  data          jsonb not null default '{}'::jsonb,
  sources       jsonb not null default '[]'::jsonb,  -- [{label, url, archive_url}]
  checks        jsonb not null default '{}'::jsonb,  -- automatic verification results
  content_hash  text not null,
  priority      int not null default 0,
  status        text not null default 'pending'
                check (status in ('pending', 'approved', 'rejected', 'needs_more')),
  pulled_at     timestamptz not null default now(),
  reviewed_at   timestamptz,
  reviewed_by   text,
  review_note   text,
  published_at  timestamptz,
  unique (tracker, source_id)
);
create index if not exists items_status_idx on items (status, priority desc, pulled_at desc);
create index if not exists items_tracker_idx on items (tracker, status);

-- Public log of every change made after publishing
create table if not exists corrections (
  id          bigserial primary key,
  item_id     bigint not null references items(id),
  changed_at  timestamptz not null default now(),
  changed_by  text,
  reason      text not null check (length(reason) >= 5),
  before      text,
  after       text
);

-- Reader polls
create table if not exists polls (
  id          bigserial primary key,
  question    text not null,
  is_open     boolean not null default true,
  created_at  timestamptz not null default now(),
  closes_at   timestamptz
);
create table if not exists poll_options (
  id        bigserial primary key,
  poll_id   bigint not null references polls(id) on delete cascade,
  label     text not null,
  position  int not null default 0
);
create table if not exists votes (
  id          bigserial primary key,
  poll_id     bigint not null references polls(id) on delete cascade,
  option_id   bigint not null references poll_options(id) on delete cascade,
  voter_hash  text not null,
  created_at  timestamptz not null default now(),
  unique (poll_id, voter_hash)
);
create table if not exists suggestions (
  id          bigserial primary key,
  body        text not null check (length(body) between 5 and 500),
  voter_hash  text not null,
  status      text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  created_at  timestamptz not null default now()
);

-- Row level security
alter table admins       enable row level security;
alter table items        enable row level security;
alter table corrections  enable row level security;
alter table polls        enable row level security;
alter table poll_options enable row level security;
alter table votes        enable row level security;
alter table suggestions  enable row level security;

-- Public can read only approved items; admins can read and change everything
drop policy if exists items_public_read on items;
create policy items_public_read on items for select using (status = 'approved' or is_admin());
drop policy if exists items_admin_write on items;
create policy items_admin_write on items for update using (is_admin()) with check (is_admin());

drop policy if exists corrections_public_read on corrections;
create policy corrections_public_read on corrections for select using (true);
drop policy if exists corrections_admin_insert on corrections;
create policy corrections_admin_insert on corrections for insert with check (is_admin());

drop policy if exists polls_public_read on polls;
create policy polls_public_read on polls for select using (true);
drop policy if exists polls_admin_all on polls;
create policy polls_admin_all on polls for all using (is_admin()) with check (is_admin());

drop policy if exists options_public_read on poll_options;
create policy options_public_read on poll_options for select using (true);
drop policy if exists options_admin_all on poll_options;
create policy options_admin_all on poll_options for all using (is_admin()) with check (is_admin());

-- Votes are never readable or writable directly; only through the functions below
drop policy if exists suggestions_read on suggestions;
create policy suggestions_read on suggestions for select using (status = 'approved' or is_admin());
drop policy if exists suggestions_admin_update on suggestions;
create policy suggestions_admin_update on suggestions for update using (is_admin()) with check (is_admin());

drop policy if exists admins_self_read on admins;
create policy admins_self_read on admins for select using (is_admin());

-- Voting: one vote per device per poll. The device id is hashed so raw ids are never stored.
create or replace function cast_vote(p_poll bigint, p_option bigint, p_device text)
returns table (option_id bigint, label text, votes bigint)
language plpgsql security definer set search_path = public, extensions as $$
#variable_conflict use_column
begin
  if p_device is null or length(p_device) < 16 or length(p_device) > 100 then
    raise exception 'invalid device';
  end if;
  if not exists (select 1 from polls where id = p_poll and is_open and (closes_at is null or closes_at > now())) then
    raise exception 'poll closed';
  end if;
  if not exists (select 1 from poll_options where id = p_option and poll_id = p_poll) then
    raise exception 'invalid option';
  end if;
  insert into votes (poll_id, option_id, voter_hash)
  values (p_poll, p_option, encode(digest(p_poll::text || ':' || p_device, 'sha256'), 'hex'))
  on conflict (poll_id, voter_hash) do nothing;
  return query select * from poll_results(p_poll);
end;
$$;

create or replace function poll_results(p_poll bigint)
returns table (option_id bigint, label text, votes bigint)
language sql stable security definer set search_path = public as $$
  select o.id, o.label, count(v.id)
  from poll_options o left join votes v on v.option_id = o.id
  where o.poll_id = p_poll
  group by o.id, o.label, o.position
  order by o.position, o.id;
$$;

-- Suggestions go to the review queue. Max 3 per device per day.
create or replace function suggest(p_body text, p_device text)
returns void
language plpgsql security definer set search_path = public, extensions as $$
declare h text := encode(digest('suggest:' || coalesce(p_device, ''), 'sha256'), 'hex');
begin
  if p_device is null or length(p_device) < 16 then raise exception 'invalid device'; end if;
  if (select count(*) from suggestions where voter_hash = h and created_at > now() - interval '1 day') >= 3 then
    raise exception 'limit reached';
  end if;
  insert into suggestions (body, voter_hash) values (trim(p_body), h);
end;
$$;

-- Admin action: correct a published item with a public reason
create or replace function correct_item(p_item bigint, p_statement text, p_reason text)
returns void
language plpgsql security definer set search_path = public as $$
declare old text;
begin
  if not is_admin() then raise exception 'not allowed'; end if;
  select statement into old from items where id = p_item;
  insert into corrections (item_id, changed_by, reason, before, after)
  values (p_item, auth.jwt() ->> 'email', p_reason, old, p_statement);
  update items set statement = p_statement where id = p_item;
end;
$$;

revoke all on function cast_vote(bigint, bigint, text) from public;
revoke all on function suggest(text, text) from public;
revoke all on function correct_item(bigint, text, text) from public;
grant execute on function cast_vote(bigint, bigint, text) to anon, authenticated;
grant execute on function poll_results(bigint) to anon, authenticated;
grant execute on function suggest(text, text) to anon, authenticated;
grant execute on function correct_item(bigint, text, text) to authenticated;

-- First poll
insert into polls (question)
select 'What should we track next?'
where not exists (select 1 from polls);

insert into poll_options (poll_id, label, position)
select p.id, x.label, x.pos
from polls p,
  (values ('Congress members'' votes vs. their donors', 1),
          ('Bills that stalled despite broad support', 2),
          ('Promises made vs. what happened', 3),
          ('Studies companies kept quiet', 4),
          ('Data centers and utility bills', 5),
          ('Hospital prices near me', 6)) as x(label, pos)
where p.question = 'What should we track next?'
  and not exists (select 1 from poll_options);

-- Hide the hashed device ids from the public even on approved suggestions
revoke select on suggestions from anon, authenticated;
grant select (id, body, status, created_at) on suggestions to anon, authenticated;
grant update (status) on suggestions to authenticated;
