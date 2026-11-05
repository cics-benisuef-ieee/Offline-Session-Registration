# Offline Session Registration

A Django application for registering and approving offline IEEE session places. It replaces
informal room-booking messages with a single request queue, a global conflict check, and a
calendar/reporting view.

The application is intentionally a **booking system only**. It does not include public attendee
registration, QR tickets, check-in, or payments.

## What it does

- Units are modelled as a **branch**, **chapters**, or **boards**. The 11 CS/CIS boards are
  seeded under the CS/CIS chapter.
- Each board has exactly one board account. Board accounts can submit requests for their own
  board and see only their own board's requests.
- Officers of any entity can submit requests for their own entity, see every request, and
  approve or reject any request, including their own.
- Admins can manage units, accounts, places, and closures through Django admin. Officers and
  admins can filter the request register at `/reports/` and download a CSV; board accounts cannot
  export reports.
- Users can request a password reset from the sign-in page; reset links are sent through the
  configured email backend.
- Pending requests do **not** block a place. Only approved requests block a place.
- Approval checks for overlapping approved requests globally, across every unit.
- All times are handled in `Africa/Cairo`; the UI uses the IEEE master brand palette.

## Stack

- Django 5.2 LTS
- PostgreSQL in production; SQLite for local development and tests
- Django templates, Bootstrap 5, HTMX, Alpine.js, FullCalendar
- Open Sans and the IEEE brand colour palette
- `uv` for dependency management
- Vercel for hosting

## Local development

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py seed_units
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open <http://127.0.0.1:8000/> and sign in. Create places and accounts from `/admin/`.

The seed command is idempotent:

```bash
uv run python manage.py seed_units
```

It creates `Branch`, `CS/CIS`, `WIE`, and the 11 boards:

Agentic AI, Machine Learning, Data Analysis, Red-Team, Network, Mechanical Design, Embedded
Systems, Front-end, Back-end, Flutter, UI/UX.

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
| Officer | Its own branch/chapter | All requests | Yes, including own |
| Admin (`is_superuser`) | Any unit | All requests | Yes |

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

### Supabase

Use Supabase's **pooled Supavisor connection** for the Vercel runtime, and the **direct database
connection** for migrations. Keep Django authentication; do not enable RLS on Django tables unless
the application is designed for it.

The migration that creates the exclusion constraint runs:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
```

If your database role cannot create extensions, enable `btree_gist` from the Supabase dashboard
before deploying. On SQLite the constraint migration is skipped; the service-layer conflict check
still applies. If you are applying the migration to an existing database, resolve any overlapping
approved rows first, because the exclusion constraint will reject them.

## Dependency files

`pyproject.toml` and `uv.lock` are the source of truth. `requirements.txt` is a generated
production-only export for hosts that expect a requirements file:

```bash
uv export --no-dev --format requirements-txt --no-hashes --no-emit-project -o requirements.txt
```

## Project layout

```text
accounts/        Custom user model, account types, admin
units/           Branch/chapter/board model and seed command
places/          Rooms and closure dates
bookings/        Requests, status history, services, forms, views
notifications/   Transactional email helpers
config/          Django settings, URLs, ASGI/WSGI entrypoints
templates/       Shared and booking templates
static/css/      IEEE theme
tests/           Service and view tests
```

## Not included

- Public attendee registration
- QR tickets or check-in
- Payments
- Capacity validation
- Background workers or long-running Celery processes
