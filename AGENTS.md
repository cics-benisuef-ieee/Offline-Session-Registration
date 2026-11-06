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
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Quality checks:

```bash
uv run pytest
uv run pytest tests/test_services.py::test_pending_does_not_block_but_approved_does
uv run ruff check .
```

Regenerate the production requirements export after dependency changes:

```bash
uv export --no-dev --format requirements-txt --no-hashes --no-emit-project -o requirements.txt
```

## Architecture

- `accounts`: custom `User` (`email` login, `full_name`, `unit`, `account_type`).
  Account types are `OFFICER` and `BOARD_ACCOUNT`; admin is `is_superuser`/`is_staff`.
- `units`: `Unit` with kinds `branch`, `chapter`, `board`; the seed creates
  `CS/CIS Beni-Suef Chapter` with 11 boards and `Beni-Suef Student Branch`
  with five branch boards.
- `places`: predefined rooms plus closure date ranges.
- `bookings`: `SessionRequest` (UUID), `RequestStatusHistory`, service layer, forms, views.
- `notifications`: email helpers; sends are deferred with `transaction.on_commit()`.
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
  read-only and disables adding/deleting requests.
- Reports and CSV export are available to officers and admins only; board accounts receive 403.
- Approval uses a transaction and row locks. PostgreSQL also has an exclusion constraint on
  approved overlapping rows using `btree_gist`; SQLite skips that migration.
- Times are handled in `Africa/Cairo`; the UI displays local time.

## Deployment notes

- Vercel detects `manage.py`; `pyproject.toml` explicitly selects the WSGI entrypoint.
- `vercel.json` sets the WSGI function `maxDuration` to 60 seconds.
- Vercel runs `collectstatic` automatically because `STATIC_ROOT` is configured.
- Migrations must run as a separate deploy/CI step with a direct PostgreSQL URL.
- Supabase runtime should use the pooled Supavisor URL; migrations should use the direct URL.
- Do not add long-running workers or rely on in-memory state across function invocations.

## Working here

- Keep changes consistent with the service-layer pattern and existing template style.
- Add tests for service-layer rules and for any new view/permission path.
- Do not add public attendee registration, QR tickets, check-in, payments, or capacity validation;
  these are intentionally out of scope.
