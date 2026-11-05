# CS/CIS Beni-Suef Chapter Session Registration

A Django application for registering and approving offline session places for the
**CS/CIS Beni-Suef Chapter**. It replaces informal room-booking messages with a single request
queue, a global conflict check, and a calendar/reporting view.

The application is intentionally a **booking system only**. It does not include public attendee
registration, QR tickets, check-in, or payments.

## What it does

- The seeded entity is the **CS/CIS Beni-Suef Chapter**, with the 11 chapter boards parented to
  it. The `Unit` model still supports branch, chapter, and board kinds for future use.
- Each board has exactly one board account. Board accounts can submit requests for their own
  board and see only their own board's requests.
- Chapter officers can submit requests for the chapter, see every request, and approve or reject
  any request, including their own.
- Admins can manage units, accounts, places, and closures through Django admin. Chapter officers
  and admins can filter the request register at `/reports/` and download a CSV; board accounts
  cannot export reports.
- Users can request a password reset from the sign-in page; reset links are sent through the
  configured email backend.
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

It creates `CS/CIS Beni-Suef Chapter` and the 11 boards:

Agentic AI, Machine Learning, Data Analysis, Red-Team, Network, Mechanical Design, Embedded
Systems, Front-end, Back-end, Flutter, UI/UX.

On an existing database, legacy branch and affinity-group units from the earlier seed are
deactivated.

Create or refresh the board accounts with:

```bash
uv run python manage.py seed_board_accounts --password "<strong-password>"
```

The command creates one account per active board. Emails are generated from the board name by
lowercasing it and replacing spaces or slashes with hyphens, for example
`ui-ux@cics.benisuef.ieee`. Re-running the command updates the existing accounts.

## Tests and linting

```bash
uv run pytest
uv run pytest tests/test_services.py::test_pending_does_not_block_but_approved_does
uv run ruff check .
```

## Roles and visibility

| Account | Can submit for | Sees | Can approve/reject |
| --- | --- | --- | --- |
| Board account | Its own board | Its own board's requests | No |
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
- Emails are sent after the database transaction commits.

State changes go through `bookings/services.py`:

- `create_session_request`
- `approve_session_request`
- `reject_session_request`
- `cancel_session_request`
- `get_available_slots`

The approval path locks the request and place row, checks for an approved overlap, and then
writes the status change. On PostgreSQL, a `btree_gist` exclusion constraint provides a second
line of defence against overlapping approved rows.

## Email

Local development defaults to Django's console email backend. To use SMTP, set these variables
in `.env` or the deployment environment:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=sessions@example.com
```

The email calls use `transaction.on_commit()` and fail silently, so an email provider outage does
not roll back a booking.

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

### Required environment variables

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Long random secret |
| `DJANGO_DEBUG` | `False` in production |
| `DJANGO_ALLOWED_HOSTS` | Your Vercel host, for example `.vercel.app,your-domain.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://*.vercel.app,https://your-domain.com` |
| `DATABASE_URL` | PostgreSQL connection string |
| `DEFAULT_FROM_EMAIL` | Sender for notifications |
| `EMAIL_BACKEND` | SMTP backend in production |
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

### Supabase setup

Use Supabase's **pooled Supavisor connection** for the Vercel runtime, and the **direct database
connection** for migrations. Keep Django authentication; do not enable RLS on Django tables unless
the application is designed for it.

#### 1. Create the Supabase project

1. Create a project at <https://supabase.com/dashboard>.
2. Open **Project Settings → Database** and copy the connection details.
3. Keep the database password somewhere safe. If it contains `@`, `#`, `%`, or another URL
   special character, URL-encode it in `DATABASE_URL`.

#### 2. Get both connection strings

You need two URLs:

```dotenv
# Direct connection — use for migrations and seed commands.
SUPABASE_DIRECT_URL=postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres?sslmode=require

# Transaction pooler — use as the Vercel runtime DATABASE_URL.
SUPABASE_POOLER_URL=postgresql://postgres.<project-ref>:<password>@<pooler-host>:6543/postgres?sslmode=require
```

The pooler username is `postgres.<project-ref>`, not plain `postgres`.

If the direct connection fails because your network or Vercel only supports IPv4, use the
Supabase **session pooler** on port `5432` for migrations.

#### 3. Initialize the database

Run these commands from the repository with the **direct** URL:

```bash
export SUPABASE_DIRECT_URL='postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres?sslmode=require'

DATABASE_URL="$SUPABASE_DIRECT_URL" uv run python manage.py migrate
DATABASE_URL="$SUPABASE_DIRECT_URL" uv run python manage.py seed_units
DATABASE_URL="$SUPABASE_DIRECT_URL" uv run python manage.py seed_board_accounts --password '<strong-password>'
DATABASE_URL="$SUPABASE_DIRECT_URL" uv run python manage.py createsuperuser
```

This creates the tables, the CS/CIS chapter and 11 boards, the board accounts, and the first admin
account.

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
DEFAULT_FROM_EMAIL=sessions@your-domain.com
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.your-provider.com
EMAIL_PORT=587
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
EMAIL_USE_TLS=True
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
bookings/        Requests, status history, services, forms, views
notifications/   Transactional email helpers
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
