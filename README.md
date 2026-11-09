# CS/CIS Beni-Suef Chapter Session Registration

A Django application for registering and approving offline session places for the
**CS/CIS Beni-Suef Chapter**. It replaces informal room-booking messages with a single request
queue, a global conflict check, and a calendar/reporting view.

The application is intentionally a **booking system only**. It does not include public attendee
registration, QR tickets, check-in, or payments.

## What it does

- The seeded entities are the **CS/CIS Beni-Suef Chapter** with its 11 boards, the
  **Beni-Suef Student Branch** with five branch boards/committees, and the
  **WIE Beni-Suef Affinity Group** with its Event Management board. Boards can be parented to
  either a chapter or a branch.
- Each board or committee has exactly one board account. Board accounts can submit requests
  for their own unit and see only their own unit's requests.
- Chapter officers can submit requests for the chapter, see every request, and approve or reject
  any request, including their own.
- Admins can manage units, accounts, places, and closures through Django admin. Chapter officers
  and admins can filter the request register at `/reports/`, download CSV or XLSX, inspect the
  status-change audit log at `/reports/audit/`, and view the utilization heatmap at
  `/reports/utilization/`; board accounts cannot reach reports, analytics, audit, or exports.
- Superusers can soft-delete erroneous or test requests with a reason, restore them from the admin,
  and permanently purge old deleted rows with `manage.py purge_deleted_sessions`. Hard deletion is
  disabled everywhere else.
- Users receive in-app notifications and optional browser Web Push notifications for new
  requests, decisions, cancellations, deletions, and session reminders. Outgoing email is
  disabled. Each user controls the officer digest frequency and can enable push on each device
  from `/settings/notifications/`.
- Approved sessions get 24-hour and 2-hour in-app/browser reminders. Chapter officers and admins
  can also receive a daily or weekly digest of the pending queue and upcoming sessions.
- Requesters can book a recurring series (weekly, biweekly, or monthly). The preview flags
  conflicts and unavailable dates; only the selected occurrences are submitted, and officers can
  approve or reject the whole series or individual occurrences.
- Password-reset email is disabled. Administrators reset passwords from Django admin.
- Pending requests do **not** block a place. Only approved requests block a place.
- Approval checks for overlapping approved requests globally, across every unit.
- All times are handled in `Africa/Cairo`; the UI uses the CS/CIS chapter palette.

## Stack

- Django 5.2 LTS
- PostgreSQL in production; SQLite for local development and tests
- Django templates, Bootstrap 5, HTMX, Alpine.js, FullCalendar
- Open Sans and the CS/CIS chapter colour palette
- `uv` for dependency management
- Vercel for hosting

## Local development

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py seed_units
uv run python manage.py seed_board_accounts --password "<strong-password>"
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open <http://127.0.0.1:8000/> and sign in. Create places and accounts from `/admin/`.

The seed command is idempotent:

```bash
uv run python manage.py seed_units
```

It creates `CS/CIS Beni-Suef Chapter` and its 11 boards:

Agentic AI, Machine Learning, Data Analysis, Red-Team, Network, Mechanical Design, Embedded
Systems, Front-end, Back-end, Flutter, UI/UX.

It also creates `Beni-Suef Student Branch` and its five branch boards:

Human Resources, Entrepreneurship, Visuals, Presentation, Digital Marketing.

It also creates `WIE Beni-Suef Affinity Group` and its Event Management board.

Create or refresh the board accounts with:

```bash
uv run python manage.py seed_board_accounts --password "<strong-password>"
```

The command creates one account per active board. Chapter board emails are generated from the
board name by lowercasing it and replacing spaces or slashes with hyphens, for example
`ui-ux@cics.benisuef.ieee`. Branch boards use the branch email addresses:
`hr@benisuef.ieee`, `entrepreneurship@benisuef.ieee`, `visuals@benisuef.ieee`,
`presentation@benisuef.ieee`, and `digital-marketing@benisuef.ieee`. The WIE Event Management
board uses `em@wie.benisuef.ieee`. Re-running the command updates the existing accounts.

## Tests and linting

```bash
uv run pytest
uv run pytest tests/test_services.py::test_pending_does_not_block_but_approved_does
uv run ruff check .
```

If your `.env` points at PostgreSQL, run the suite against local SQLite so Django does not create
a test database on the shared server:

```bash
DATABASE_URL=sqlite:////tmp/session-registration-test.sqlite3 uv run pytest
```

## Roles and visibility

| Account | Can submit for | Sees | Can approve/reject |
| --- | --- | --- | --- |
| Board account | Its own board or branch committee | Its own unit's requests | No |
| Chapter officer | The chapter | All requests | Yes, including own |
| Admin (`is_superuser`) | The chapter or any board | All requests | Yes |

A treasurer is an officer, not a separate role. There is no public attendee account type.

## Booking rules

- Places are predefined. There is no capacity validation.
- A request has a place, date, start time, duration, requesting unit, title, and purpose.
- Start times align to 30-minute slots; durations are 30–240 minutes in 30-minute increments.
- Requests must be on one date, inside the place's opening hours, and not on a closure date.
- A request cannot start in the past.
- Rejected and cancelled requests are terminal.
- A cancellation of an approved request requires a reason.
- Emails are disabled; in-app notifications and browser pushes are deferred until the database
  transaction commits.

State changes go through `bookings/services.py`:

- `create_session_request`
- `approve_session_request`
- `reject_session_request`
- `cancel_session_request`
- `get_available_slots`

The approval path locks the request and place row, checks for an approved overlap, and then
writes the status change. On PostgreSQL, a `btree_gist` exclusion constraint provides a second
line of defence against overlapping approved rows.

## Recurring series

Use **Book a recurring series** from the request form (or open `/requests/series/new/`). A series
stores the place, title, purpose, frequency, weekday, start time, duration, and date range. The
preview generates one candidate occurrence per date and marks each as available, conflicting with
an approved booking, or unavailable (past, closed, or outside opening hours). Conflicting and
unavailable rows cannot be selected.

Each selected occurrence becomes an ordinary pending `SessionRequest` linked to the series, so the
normal conflict check, review queue, calendar, reports, and exclusion constraint keep working.
Officers can approve or reject all pending occurrences, cancel one occurrence or the selected
occurrence and all later ones, and reschedule future pending occurrences. Cancelling or rescheduling
never changes past occurrences.

The generation rules are:

- **Weekly** steps seven days from the first matching weekday.
- **Every two weeks** steps fourteen days.
- **Monthly** repeats on the original day of the month, clamped to the month's last day
  (for example, January 31 → February 28 → March 31).

## Reports, analytics, and exports

Officers and admins can reach the reporting suite from `/reports/`:

- **Reports** (`/reports/`) — filtered counts, status split, per-unit and per-place breakdowns, and
  monthly charts.
- **Utilization** (`/reports/utilization/`) — a place × weekday × hour heatmap of approved booked
  minutes. The denominator is the place's opening hours across the selected range, minus closure
  days, so percentages stay honest and never exceed 100%. A table fallback is available for each
  grid.
- **Audit log** (`/reports/audit/`) — paginated `RequestStatusHistory` events filtered by actor, new
  status, date range, and request ID or title.
- **Exports** — `/reports/requests.csv` and `/reports/requests.xlsx` reuse the same register filters
  as the reports page.

Board accounts receive `403` for every reporting, analytics, audit, and export endpoint.

## Notifications, browser push, and email

Outgoing email is disabled globally with Django's dummy email backend. The application does not
send request, decision, cancellation, reminder, digest, or password-reset email. Notifications are
delivered in two ways:

1. **In-app** — always written to the notification center at `/notifications/` and the bell menu.
2. **Browser Web Push** — optional per device, delivered by a service worker even when the site or
   tab is closed.

Status changes notify the requester and every active officer/admin, de-duplicated so a user who is
both requester and reviewer receives one notification:

- new request — officers and admins
- approval or rejection — requester, officers, and admins
- cancellation — requester, officers, and admins
- deletion of an approved session — requester, officers, and admins
- session reminders — requester
- officer digest — officers and admins

Bulk approval/rejection and series actions send **one summary notification per recipient**, not one
per occurrence.

Approved sessions receive a 24-hour and a 2-hour reminder. Each reminder is stamped on the request
and de-duplicated by a notification unique constraint, so repeated scheduler runs are harmless.
The officer digest includes the pending queue and approved sessions in the next 14 days; it can be
set to off, daily, or weekly (weekly sends on Monday) at `/settings/notifications/`.

### Enable browser notifications with VAPID

Web Push needs a VAPID key pair. The public key is sent to the browser; the private key signs push
messages and must stay secret.

1. Generate a pair:

   ```bash
   uv run python manage.py generate_vapid_keys
   ```

   The command prints base64url values. Add them to `.env` locally and to the deployment
   environment in production:

   ```dotenv
   VAPID_PUBLIC_KEY=<generated-public-key>
   VAPID_PRIVATE_KEY=<generated-private-key>
   VAPID_SUBJECT=mailto:sessions@your-domain.com
   ```

2. Restart the local server or redeploy. The application serves `/sw.js`, exposes the public key to
   authenticated pages, and enables the **Enable browser notifications** button.

3. Sign in, open `/settings/notifications/`, and click **Enable browser notifications**. Accept the
   browser permission prompt. Repeat on each browser or device you want to use.

4. On iPhone and iPad, add the site to the Home Screen first, then enable notifications from the
   installed app. Push is supported on HTTPS origins only; `http://localhost` and
   `http://127.0.0.1` are allowed while `DJANGO_DEBUG=True`.

Subscriptions are stored per device in `PushSubscription`. A `404` or `410` response from the push
service removes the stale subscription automatically. If you rotate the VAPID keys, existing
subscriptions stop working; users must disable and re-enable browser notifications on each device.
Leaving both keys empty disables Web Push while keeping in-app notifications.

Run the reminder and digest jobs manually with:

```bash
uv run python manage.py send_session_reminders
uv run python manage.py send_officer_digest
```

Both are also exposed as scheduler endpoints protected by `CRON_SECRET`:

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/cron/reminders/` | `POST` | Send any 24h/2h reminder that is now due |
| `/cron/digest/` | `GET` or `POST` | Send the officer digest |

The request must include `Authorization: Bearer <CRON_SECRET>`. Both endpoints return JSON counts
and respond with `401` when the secret is missing or wrong. The handlers reconcile instead of
assuming exact timing, so a late or duplicated run does not duplicate a reminder.

### Scheduler setup

The Vercel plan is Hobby, which allows at most one cron run per day. The project splits the triggers:

- **Vercel Cron** runs `/cron/digest/` daily at `0 7 * * *` (see `vercel.json`).
- **GitHub Actions** runs `.github/workflows/reminders.yml` every 15 minutes and calls
  `/cron/reminders/`, covering the 2-hour reminder and any missed 24-hour send.

Add these repository secrets in GitHub (**Settings → Secrets and variables → Actions**):

| Secret | Value |
| --- | --- |
| `CRON_SECRET` | The same value configured in Vercel |
| `APP_URL` | The production URL, for example `https://your-app.vercel.app` |

Keep `CRON_SECRET` in sync when rotating it: update Vercel, redeploy, update the GitHub secret, then
confirm the next invocation returns `2xx`. Vercel automatically sends the header on its own cron
invocations; the GitHub workflow sends it explicitly.

## Deployment on Vercel

Vercel does not support SQLite for production. Use an external PostgreSQL database such as
Supabase, Neon, or another managed provider.

1. Push the repository and import it into Vercel.
2. Add the environment variables below.
3. Run migrations separately from the Vercel build, for example in CI or from a one-off job:

   ```bash
   DATABASE_URL="<direct-postgres-url>" uv run python manage.py migrate
   ```

4. Create the first admin account and seed units using the same direct database URL.

For a complete Supabase walkthrough, including connection strings and linking it to Vercel, see
[Supabase setup](#supabase-setup).

Vercel detects `manage.py` and the Django entrypoint automatically. `pyproject.toml` sets the
WSGI entrypoint explicitly, and `vercel.json` gives that function a 60-second maximum duration.
Vercel runs `collectstatic` automatically when `STATIC_ROOT` is configured, so there is no custom
build command or `build_files.sh`.

### Response speed

- Put the Vercel function in the **same region as the Supabase project**. Cross-region database
  round trips are the largest source of page latency and make slow mobile connections more likely
  to abort a request. This project sets `"regions": ["arn1"]` in `vercel.json` because the current
  Supabase project is in `eu-north-1` (Stockholm). If you move the database, update that value
  (for example `fra1` for `eu-central-1`).
- Use the Supabase **transaction pooler** URL for the Vercel `DATABASE_URL`. Do not use the direct
  `db.<project-ref>.supabase.co` host at runtime. The settings disable prepared statements and
  server-side cursors automatically when the connection uses port `6543`.
- Keep `DJANGO_DEBUG=False` in production. WhiteNoise serves compressed static files, and
  `GZipMiddleware` compresses HTML and JSON responses.
- Dashboard and report status counts are aggregated in one database query, and the request list is
  paginated.

### Required environment variables

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Long random secret |
| `DJANGO_DEBUG` | `False` in production |
| `DJANGO_ALLOWED_HOSTS` | Your Vercel host, for example `.vercel.app,your-domain.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://*.vercel.app,https://your-domain.com` |
| `DATABASE_URL` | PostgreSQL connection string |
| `VAPID_PUBLIC_KEY` | Public key for browser Web Push |
| `VAPID_PRIVATE_KEY` | Private key for browser Web Push; keep secret |
| `VAPID_SUBJECT` | Contact URI, for example `mailto:sessions@your-domain.com` |
| `CRON_SECRET` | Bearer secret shared with the Vercel and GitHub schedulers |
| `DJANGO_SECURE_COOKIES` | `True` on HTTPS |
| `DJANGO_SECURE_SSL_REDIRECT` | `True` on HTTPS |
| `DJANGO_SECURE_HSTS_SECONDS` | `31536000` after the domain is confirmed |

### Database troubleshooting

- Use the **pooled Supabase URL** for the Vercel runtime `DATABASE_URL`. The direct
  `db.<project-ref>.supabase.co:5432` host is for migrations and may be IPv6-only.
- If the app raises `DATABASE_URL must be set ... on Vercel`, add `DATABASE_URL` to the
  Vercel **Production** environment and redeploy. Environment changes do not apply to an
  existing deployment.
- If the app raises `relation "accounts_user" does not exist` or similar, migrations have not
  run against that database. Run `migrate` with the direct or session-pooler URL.
- If the app raises `Network is unreachable` or `Cannot assign requested address` and the log
  shows an IPv6 address for `db.<project-ref>.supabase.co`, Vercel is still using the direct
  URL. Change the Vercel `DATABASE_URL` to the Supavisor pooler URL and redeploy.
- If the app raises `password authentication failed`, check the pooler username
  (`postgres.<project-ref>`) and URL-encode special characters in the password.
- If you connected Vercel Postgres/Neon, Vercel may expose `POSTGRES_URL` or
  `POSTGRES_URL_NON_POOLING` instead of `DATABASE_URL`. Map the pooled URL to
  `DATABASE_URL` manually.
- If you see SQLite errors on Vercel, `DATABASE_URL` is missing or still points at SQLite.
  Vercel does not support SQLite for production.
- If the browser shows `ERR_NETWORK_CHANGED` or “The network connection was interrupted” after a
  form submission, the request may already have committed. This is a browser/network-level abort,
  not a database rollback. Check the request list before submitting again. Keeping the Vercel
  function in the database region and using a stable connection reduces the chance of it happening.

### Supabase setup

Use Supabase's **pooled Supavisor connection** for the Vercel runtime, and the **session pooler**
(or the direct connection if your network supports IPv6) for migrations. Keep Django
authentication; do not enable RLS on Django tables unless the application is designed for it.

#### 1. Create the Supabase project

1. Create a project at <https://supabase.com/dashboard>.
2. Open **Project Settings → Database** and copy the connection details.
3. Keep the database password somewhere safe. If it contains `@`, `#`, `%`, or another URL
   special character, URL-encode it in `DATABASE_URL`.

#### 2. Get both connection strings

You need two URLs:

```dotenv
# Session pooler — use for migrations and seed commands (IPv4-friendly).
SUPABASE_SESSION_URL=postgresql://postgres.<project-ref>:<password>@<pooler-host>:5432/postgres?sslmode=require

# Direct connection — alternative if your network supports IPv6.
SUPABASE_DIRECT_URL=postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres?sslmode=require

# Transaction pooler — use as the Vercel runtime DATABASE_URL.
SUPABASE_POOLER_URL=postgresql://postgres.<project-ref>:<password>@<pooler-host>:6543/postgres?sslmode=require
```

The pooler username is `postgres.<project-ref>`, not plain `postgres`.

If the direct connection fails with `Network is unreachable`, the project is IPv6-only on that
network. Use the session pooler on port `5432` for migrations.

#### 3. Initialize the database

Run these commands from the repository with the **session pooler** URL (or the direct URL if
your network supports IPv6):

```bash
export SUPABASE_SESSION_URL='postgresql://postgres.<project-ref>:<password>@<pooler-host>:5432/postgres?sslmode=require'

DATABASE_URL="$SUPABASE_SESSION_URL" uv run python manage.py migrate
DATABASE_URL="$SUPABASE_SESSION_URL" uv run python manage.py seed_units
DATABASE_URL="$SUPABASE_SESSION_URL" uv run python manage.py seed_board_accounts --password '<strong-password>'
DATABASE_URL="$SUPABASE_SESSION_URL" uv run python manage.py createsuperuser
```

This creates the tables, the CS/CIS chapter and its 11 boards, the Beni-Suef Student Branch and
its five branch boards, the WIE Event Management board, the 17 board accounts, and the first
admin account.

The migration that creates the exclusion constraint runs:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
```

If your database role cannot create extensions, enable `btree_gist` from the Supabase dashboard
before running migrations. On SQLite the constraint migration is skipped; the service-layer
conflict check still applies. If you are applying the migration to an existing database, resolve
any overlapping approved rows first, because the exclusion constraint will reject them.

#### 4. Link Supabase to Vercel

In Vercel, open **Project → Settings → Environment Variables** and add the pooled URL for both
**Production** and **Preview**:

```dotenv
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@<pooler-host>:6543/postgres?sslmode=require
```

Add the remaining production variables:

```dotenv
DJANGO_SECRET_KEY=<long-random-secret>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=.vercel.app,your-domain.com
CSRF_TRUSTED_ORIGINS=https://*.vercel.app,https://your-domain.com
VAPID_PUBLIC_KEY=<generated-public-key>
VAPID_PRIVATE_KEY=<generated-private-key>
VAPID_SUBJECT=mailto:sessions@your-domain.com
CRON_SECRET=<long-random-secret>
DJANGO_SECURE_COOKIES=True
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SECURE_HSTS_SECONDS=31536000
```

Then redeploy. Environment variable changes do not apply to an existing deployment.

If you use the Vercel Supabase integration or Vercel Postgres/Neon, Vercel may create
`POSTGRES_URL`, `POSTGRES_URL_NON_POOLING`, or `SUPABASE_*` variables instead of `DATABASE_URL`.
Map the **pooled** URL to `DATABASE_URL` manually.

#### 5. Verify the link

After redeploying:

1. Open `/login/` and sign in with the admin account.
2. Open `/admin/` and confirm the chapter, boards, and board accounts exist.
3. Create at least one place and one closure date if needed.
4. Sign in as a board account and confirm it can submit a request but cannot open `/review/` or
   `/reports/`.

If the deployment fails, check the Vercel function logs for `django.db.utils.*` and see
[Database troubleshooting](#database-troubleshooting).

## Dependency files

`pyproject.toml` and `uv.lock` are the source of truth. `requirements.txt` is a generated
production-only export for hosts that expect a requirements file:

```bash
uv export --no-dev --format requirements-txt --no-hashes --no-emit-project -o requirements.txt
```

## Project layout

```text
accounts/        Custom user model, account types, admin
units/           Unit model and chapter/board seed command
places/          Rooms and closure dates
bookings/        Requests, recurring series, status history, services, forms, views
notifications/   In-app notifications, preferences, reminders, digest, Web Push
config/          Django settings, URLs, ASGI/WSGI entrypoints
templates/       Shared and booking templates
static/css/      CS/CIS chapter theme
tests/           Service and view tests
```

## Not included

- Public attendee registration
- QR tickets or check-in
- Payments
- Capacity validation
- Background workers or long-running Celery processes
