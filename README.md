# Hospital Management API

## Setup
```
pip install -r requirements.txt
cp .env.example .env
```

**Important — schema changed:** Levels 11-18 added new tables/columns
(roles, appointments, audit fields). If you have an old `hospital.db` from
Levels 1-10, delete it before starting the server, since there is no Alembic
migration yet and `create_all` only adds missing tables, never alters
existing ones:
```
rm -f hospital.db
uvicorn main:app --reload
```

## Getting a working session
1. `POST /api/v1/auth/register` with `{"username": "admin", "password": "...", "role": "admin"}`.
2. `POST /api/v1/auth/login` (or the Swagger **Authorize** button) with the same credentials.
3. Create a doctor as admin, then register a second user with
   `{"role": "doctor", "doctor_id": <id>}` to test doctor-only views.

## Tests
```
pytest
```
Runs with coverage (`pytest-cov`) via `pytest.ini`; fails if total coverage
drops below 70%. `tests/test_api.py` covers Levels 1-10, `tests/test_advanced.py`
covers Levels 11-18.

## What's implemented (Levels 11-18)
- **11 - Roles:** `admin` / `doctor` on the `User` model, carried in the JWT.
  Admin has full access. A doctor login is linked to one `Doctor` row
  (`doctor_id` at registration) and can only view/edit their own patients
  and appointments; doctor/patient deletes are admin-only. Unauthorized
  actions return 403; missing/invalid tokens return 401.
- **12 - Appointments:** new `Appointment` model + full CRUD, plus
  `GET /doctors/{id}/appointments` and `GET /patients/{id}/appointments`.
  Doctor/patient existence and active-doctor checks reuse Level 2's logic.
  Overlap detection treats each appointment as a 30-minute slot and blocks
  a second booking for the same doctor in that window (cancelled
  appointments don't block).
- **13 - Integrity:** DB-level unique constraint on doctor email (was
  already enforced at the app layer too), FK constraints with
  `ondelete="RESTRICT"` (and SQLite's FK pragma turned on, since SQLite
  ignores FKs by default), and a single `commit()` helper that turns any
  `IntegrityError` into a `{"error": {...}}` JSON message instead of a raw
  DB traceback.
- **14 - Performance:** `selectinload` on appointment listings to avoid
  N+1 queries when doctor/patient are serialized, a composite index on
  `(doctor_id, appointment_date)` for the overlap check and doctor listing,
  pagination that counts and slices in SQL rather than in Python, and an
  `X-Process-Time-Ms` response header plus a log line on every request.
- **15 - Audit:** `created_at`/`updated_at` (auto-managed) and
  `created_by`/`updated_by` (the calling user's username) on Doctor,
  Patient and Appointment.
- **16 - Hardening:** every error path — business rules, HTTP/auth errors,
  Pydantic validation errors, and any unhandled exception — returns the
  same `{"error": {"code", "message"}}` shape. A basic in-memory sliding
  window rate limiter (100 requests/60s per IP by default, configurable
  via `.env`) returns 429 when exceeded.
- **17 - Testing:** `tests/test_advanced.py` adds role-restriction tests,
  appointment validation/overlap tests, integrity and audit tests, and a
  uniform-error-shape test. Coverage is enforced via `pytest.ini`.
- **18 - Docs:** request/response examples on every schema (see
  `schemas.py`'s `json_schema_extra` and `Field(examples=...)`), tag
  descriptions grouping the Swagger UI, and docstrings on the auth routes.
