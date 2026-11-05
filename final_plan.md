# CS/CIS Beni-Suef Chapter — Offline Session Registration

**Status:** Final  
**Application owner:** CS/CIS Beni-Suef Chapter  
**Stack:** Django 5.2 LTS, PostgreSQL, Django templates, Vercel

## 1. Goal

Build an internal room-booking system for the **CS/CIS Beni-Suef Chapter**. Chapter
officers and board accounts submit requests for a room, date, time, and duration. Officers
review every request and approve or reject any request, including their own. Approved
requests reserve the room and appear on the calendar.

This is a **booking system only**. It does not handle public attendee registration, QR
tickets, check-in, payments, or capacity validation.

The application is not branded as, or operated for, IEEE. All user-facing naming, email
copy, documentation, and styling use the CS/CIS Beni-Suef Chapter identity.

## 2. Scope

- Chapter entity: **CS/CIS Beni-Suef Chapter**.
- Boards: the 11 chapter boards are parented to the chapter:
  Agentic AI, Machine Learning, Data Analysis, Red-Team, Network, Mechanical Design,
  Embedded Systems, Front-end, Back-end, Flutter, and UI/UX.
- Each board has exactly one board account.
- Chapter officers can submit requests for the chapter.
- Board accounts can submit requests for their own board only.
- No public attendee accounts.

## 3. Roles and permissions

| Account | Submit for | Sees | Approve/reject |
| --- | --- | --- | --- |
| Board account | Its own board | Its own board's requests | No |
| Chapter officer | The chapter | All requests | Yes, including own |
| Admin (`is_superuser`) | Chapter or any board | All requests | Yes |

- A treasurer is a chapter officer, not a separate role.
- Board accounts cannot export reports.
- Chapter officers and admins can filter `/reports/` and download CSV exports.

## 4. Unit model and seed data

`Unit` supports `branch`, `chapter`, and `board` kinds for future flexibility. The seed
command creates only the chapter and its boards:

- `CS/CIS Beni-Suef Chapter` (`chapter`)
- The 11 boards above, each parented to the chapter.

Board accounts are created with `seed_board_accounts --password <password>`. Emails use the
`<board-slug>@cics.benisuef.ieee` pattern, for example `ui-ux@cics.benisuef.ieee`.

The seed command is idempotent. On an existing database, legacy branch and affinity-group
units from the old seed are deactivated; the chapter unit keeps the `cs-cis` slug so existing
relationships continue to work.

## 5. Booking rules

- Places are predefined; there is no capacity validation.
- A request includes place, date, start time, duration, requesting unit, title, and purpose.
- Start times align to 30-minute slots.
- Durations are 30–240 minutes in 30-minute increments.
- Requests must start and end on the same date, inside opening hours, and outside closure
  dates.
- Requests cannot start in the past.
- Pending requests do not block a place.
- Only approved requests block a place.
- Approval checks conflicts globally across the chapter and all boards.
- Rejected and cancelled requests are terminal.
- Cancelling an approved request requires a reason.
- All state changes go through `bookings/services.py`.

## 6. Data model

- `Unit`: name, slug, kind, parent, active flag.
- `User`: email login, full name, phone, unit, account type.
- `Place`: name, building, room, opening/closing times, active flag.
- `PlaceClosure`: place and inclusive date range.
- `SessionRequest`: requesting unit, reviewer-unit snapshot, submitter, place, title,
  purpose, start/end, status, reviewer, review timestamp, decision reason.
- `RequestStatusHistory`: from/to status, changed by, reason, timestamp.

Statuses: `pending`, `approved`, `rejected`, `cancelled`.

The reviewer-unit snapshot is for reporting only; it is never used as a visibility filter.

## 7. Concurrency and integrity

- Approval runs in a transaction and locks the request and place rows.
- PostgreSQL adds a `btree_gist` exclusion constraint preventing overlapping approved
  requests for the same place.
- SQLite skips the PostgreSQL-only migration; the service-layer conflict check remains
  authoritative in all environments.
- One board account per board is enforced with a partial unique constraint.

## 8. Views and URLs

- `/` — redirect to dashboard or login
- `/dashboard/` — counts, recent requests, upcoming approved sessions
- `/requests/` — filterable request register
- `/requests/new/` — request form with HTMX availability
- `/requests/<uuid>/` — request detail and status history
- `/requests/<uuid>/cancel/` — cancellation action
- `/review/` — officer review queue
- `/review/<uuid>/` — approve/reject screen
- `/availability/` — available-slot partial and JSON response
- `/calendar/` — approved-session calendar with place filter
- `/reports/` — officer/admin report filters
- `/reports/requests.csv` — scoped CSV export
- `/admin/` — unit, account, place, closure, and request inspection

## 9. UI and branding

- Django templates, Bootstrap 5, HTMX, Alpine.js, FullCalendar.
- Open Sans typography.
- CS/CIS chapter palette: deep teal primary, dark teal, gold accent, green success,
  red danger, and neutral surfaces.
- Responsive navigation, status badges with text, accessible contrast, and clear focus
  states.
- No IEEE names, logos, or brand palette.

## 10. Notifications

- Email is sent with `transaction.on_commit()` and fails silently so provider outages do
  not roll back bookings.
- Chapter officers receive new-request and cancellation emails.
- Requesters receive approval/rejection emails.
- Local development uses Django's console email backend; production configures SMTP.
- Password reset links are sent through the configured email backend.

## 11. Deployment

- Vercel hosts the Django application through the WSGI entrypoint.
- Production uses external PostgreSQL through `DATABASE_URL`.
- Supabase runtime uses the pooled Supavisor URL; migrations use the direct connection URL.
- Migrations run as a separate deploy/CI step, never on cold start.
- Static files are collected with `collectstatic` and served through Vercel/WhiteNoise.
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` include the production domain.
- No long-running workers or in-memory state across function invocations.

## 12. Acceptance criteria

- Chapter officers can submit for the chapter; board accounts can submit for their board.
- Board accounts see only their board's requests and cannot approve/reject or export reports.
- Officers and admins see all requests and can approve/reject any request.
- Pending requests do not block availability; approved requests do.
- Cross-board and cross-entity conflicts are blocked globally.
- Approved sessions appear on the calendar.
- CSV export works for officers and admins.
- Status changes are recorded in audit history.
- The UI uses the CS/CIS palette, Open Sans, and accessible contrast.
- The application deploys to Vercel and serves Django through WSGI.
- Production uses external PostgreSQL; migrations run separately.
- No IEEE branding remains in user-facing UI, emails, or documentation.
