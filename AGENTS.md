# AGENTS.md

## Repository state

- Django 5.2 LTS application for CS/CIS Beni-Suef Chapter offline session booking.
- Production database target: PostgreSQL (Vercel-compatible managed provider).
- Local/test database: SQLite through `DATABASE_URL` fallback.
- Package manager: `uv`; `pyproject.toml` and `uv.lock` are authoritative.
- `requirements.txt` is generated for hosts that expect it.
- Tests live in `tests/`, not in the per-app scaffold `tests.py` files.

## Commands

```bash
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py seed_units
uv run python manage.py seed_board_accounts --password "<strong-password>"
uv run python manage.py generate_vapid_keys
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Quality checks:

```bash
uv run pytest
uv run pytest tests/test_services.py::test_pending_does_not_block_but_approved_does
uv run ruff check .
```

If `.env` points at PostgreSQL, override `DATABASE_URL` with SQLite for tests so Django does not
create a test database on the shared server:

```bash
DATABASE_URL=sqlite:////tmp/session-registration-test.sqlite3 uv run pytest
```

Regenerate the production requirements export after dependency changes:

```bash
uv export --no-dev --format requirements-txt --no-hashes --no-emit-project -o requirements.txt
```

## Architecture

- `accounts`: custom `User` (`email` login, `full_name`, `unit`, `account_type`).
  Account types are `OFFICER` and `BOARD_ACCOUNT`; admin is `is_superuser`/`is_staff`.
- `units`: `Unit` with kinds `branch`, `chapter`, `board`; the seed creates
  `CS/CIS Beni-Suef Chapter` with 11 boards, `Beni-Suef Student Branch` with
  five branch boards, and `WIE Beni-Suef Affinity Group` with Event Management.
- `places`: predefined rooms plus closure date ranges.
- `bookings`: `SessionRequest` (UUID), `BookingSeries`, `RequestStatusHistory`, service layer
  (`services.py` for requests, `series.py` for recurring series), reporting/analytics
  (`reports.py`), forms, views.
- `notifications`: in-app notifications, per-user digest preferences, reminder and digest
  scheduling, and browser Web Push (`push.py`, `PushSubscription`); push sends are deferred with
  `transaction.on_commit()`. Outgoing email is disabled with Django's dummy backend.
- `templates/notifications/`: notification center and preferences.
- `templates/`: Django templates using Bootstrap 5, HTMX, Alpine.js, FullCalendar.
- `static/css/theme.css`: CS/CIS chapter palette and shared UI styles.

## Invariants

- One board account per board or branch committee is enforced by a partial unique
  constraint on `User.unit` when `account_type == "BOARD_ACCOUNT"`.
- Pending requests do not block slots. Only approved requests block slots.
- Approval checks conflicts globally across all units. Do not scope the conflict check to the
  requesting unit or reviewer unit.
- `reviewer_unit` is a reporting snapshot (board parent or entity unit), not a visibility filter.
- Board accounts see only their own unit's requests and cannot approve/reject.
- Chapter officers and admins see all requests and can approve/reject any request, including
  their own.
- Rejected and cancelled requests are terminal.
- Cancelling an approved request requires a reason.
- Start times align to 30-minute slots; durations are 30–240 minutes in 30-minute increments.
- All state changes must go through `bookings/services.py`; do not mutate
  `SessionRequest.status` directly in views. The Django admin keeps request status fields
  read-only and disables adding requests. Hard deletion stays disabled; superusers may
  soft-delete with a reason, restore from the admin, and purge old rows with
  `manage.py purge_deleted_sessions --older-than <days> --commit`.
- Reports, utilization analytics, the status-history audit viewer, and CSV/XLSX export are
  available to officers and admins only; board accounts receive 403.
- Utilization percentages are computed from approved sessions split into 30-minute slots. The
  denominator is the place's open minutes for each weekday/hour in the range, minus `PlaceClosure`
  days, and booked minutes are capped so the percentage never exceeds 100.
- In-app notifications are always written. Email delivery is disabled; a missing or failing push
  subscription must not suppress the in-app row.
- Approval, rejection, and cancellation notify the requester plus active officers/admins,
  deduplicated. Bulk and series actions send one summary notification per recipient.
- Browser push uses VAPID (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`) and the
  `/sw.js` service worker. Stale `404`/`410` subscriptions are deleted. Push is skipped when the
  keys are unset.
- Password reset is admin-only; password-reset email routes and templates are intentionally absent.
- Reminder sends are idempotent: `reminder_24h_sent_at` / `reminder_2h_sent_at` on the request and
  a unique `(user, request, kind)` constraint on reminder notifications guard repeated cron runs.
- Only approved, non-deleted, future sessions receive reminders. The digest includes pending
  requests plus approved sessions in the next 14 days; weekly digests send on Monday.
- Cron endpoints require `Authorization: Bearer <CRON_SECRET>` and return 401 when it is unset or
  wrong. Do not make the endpoints depend on a specific scheduler.
- Approval uses a transaction and row locks. PostgreSQL also has an exclusion constraint on
  approved overlapping rows using `btree_gist`; SQLite skips that migration.
- Recurring series materialize each selected occurrence as an ordinary `SessionRequest` linked to
  a `BookingSeries` via `series` / `series_index`. Pending occurrences still do not block slots and
  approval stays per occurrence through `bulk_decide`.
- Series generation caps at `MAX_SERIES_OCCURRENCES = 52`. Weekly/biweekly step from `weekday`;
  monthly anchors to the original day of month, clamped to month end (Jan 31 → Feb 28 → Mar 31).
- Creating a series rejects any selected occurrence that is conflicting, closed, or past. Cancel
  and reschedule only touch occurrences at or after the anchor and never past occurrences.
- Times are handled in `Africa/Cairo`; the UI displays local time.

## Deployment notes

- Vercel detects `manage.py`; `pyproject.toml` explicitly selects the WSGI entrypoint.
- `vercel.json` sets the WSGI function `maxDuration` to 60 seconds.
- Vercel runs `collectstatic` automatically because `STATIC_ROOT` is configured.
- Migrations must run as a separate deploy/CI step with a direct PostgreSQL URL.
- Supabase runtime should use the pooled Supavisor URL; migrations should use the direct URL.
- The Vercel plan is Hobby, so `vercel.json` has exactly one daily cron for `/cron/digest/`.
  Do not add a sub-daily Vercel cron; it fails the deployment. The 2-hour reminder runs from
  `.github/workflows/reminders.yml` every 15 minutes against `/cron/reminders/`.
- `CRON_SECRET` must match between Vercel and the GitHub Actions repository secret. Vercel sends
  the header automatically; the workflow sends it explicitly.
- Browser Web Push requires HTTPS and the `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and
  `VAPID_SUBJECT` environment variables. Generate the keys with
  `manage.py generate_vapid_keys`; leave them unset to disable push without affecting in-app rows.
- Do not add long-running workers or rely on in-memory state across function invocations.

## Working here

- Keep changes consistent with the service-layer pattern and existing template style.
- Add tests for service-layer rules and for any new view/permission path.
- Do not add public attendee registration, QR tickets, check-in, payments, or capacity validation;
  these are intentionally out of scope.
