# FaciliTrack (CFUMRAS): Test Plan and Results

This document reports how the system was tested against the testing plan in Section 14.2 of the
enterprise architecture study, and the results. Figures were produced on November 16, 2026 on a
Windows 11 development laptop (Python 3.12, Django 5.2, SQLite).

## 1. Summary against the testing plan (Section 14.2)

| # | Planned test (Section 14.2) | Status | Evidence |
|---|---|---|---|
| 28 | Unit and component testing | **Done**: 194 automated tests, 93% statement coverage | Section 2 |
| 29 | Integration testing with identity, scheduling and facilities systems | **Partly done.** The interfaces are simulated: schedule and usage CSV imports stand in for the registrar and sensor systems, and local accounts stand in for SSO. Live integration needs institutional systems and is deferred to the pilot. | `scheduling/tests.py`, `usage/tests.py` (import tests) |
| 30 | Data-quality testing (identifiers, capacity, status, schedules, usage) | **Done** | Unique facility codes, capacity and time validation, all-or-nothing imports, duplicate-usage detection, data-coverage risk rule |
| 31 | Security testing (authentication, authorization, sessions, input validation, logging) | **Done** | Section 3 (OWASP Top 10) |
| 32 | Performance testing with representative workloads | **Done**: all 15 key pages under 3 s at 150 facilities | Section 4 |
| 33 | User acceptance testing | **Ready to run**: scripts and questionnaire prepared | [UAT.md](UAT.md) |
| 34 | Operational testing (backup, restore, monitoring, incidents) | **Pending**: part of deployment (Step 12) | System status page provides monitoring |

## 2. Automated tests and coverage

Run with `python manage.py test` (all tests, about 20 s) or with coverage:

```bash
pip install -r requirements-dev.txt
coverage run manage.py test
coverage report        # summary
coverage html          # browsable report in htmlcov/index.html
```

| Module | Tests | Statement coverage | What is tested |
|---|---:|---:|---|
| accounts | 4 | 100% | Roles, login, role checks |
| audit | 7 | 96% | Create, update and delete logging, login and failed-login logging, no passwords in the log |
| facilities | 16 | 97% | Registry rules (unique codes, floors, hours), role permissions, maintenance logging |
| scheduling | 36 | 88% | Conflict detection (overlaps, maintenance, hours, capacity), CSV import, reservation workflow, room finder |
| usage | 23 | 87% | QR check-in, attendance sheet, manual entry, CSV import, scheduled-vs-actual comparison |
| analytics | 26 | 92% | Interval maths, utilization engine (maintenance, overlaps, operating days), aggregation, definitions |
| risks | 26 | 96% | Each of the six risk rules, scoring bands, register sync (create, update, close, reopen), workflow permissions |
| notifications | 13 | 94% | Alert triggers, de-duplication, email, safe links |
| reports | 9 | 98% | All 4 reports in all 3 formats, identical numbers across formats, export auditing |
| console | 17 | 96% | Temporary passwords, forced password change, lockout protection, threshold validation |
| core | 17 | 76%* | Role-aware dashboard, security tests, benchmark |
| **Total** | **194** | **93%** | |

\* `core` contains the demo-data generator, whose branches for already-existing data are not all exercised.

Engine tests use hand-calculated expected values. For example, a room open 07:00–21:00 with 7 hours
of recorded use must report exactly 50.00%, and two overlapping records of 08:00–10:00 and 09:00–11:00
must count as 3 hours, not 4.

## 3. Security review (OWASP Top 10, 2021)

| OWASP risk | Controls in FaciliTrack | Evidence |
|---|---|---|
| **A01 Broken access control** | Every page requires login. Role-based access is checked on the server for every view, not only by hiding buttons. Users can only see their own reservations and notifications. Deactivated users cannot sign in. | `core/test_security.py`: an automatic sweep of **every route** (over 60) confirms none answers without login; a role matrix confirms the Viewer role gets 403 on 31 restricted pages; ownership tests in `scheduling/tests.py` and `notifications/tests.py` |
| **A02 Cryptographic failures** | Passwords are hashed with PBKDF2 (Django default). In production: HTTPS redirect, secure cookies and HSTS. No secrets in source code: `SECRET_KEY` comes from `.env`, and production refuses to start with the development key. | `python manage.py check --deploy` passes (see note 1) |
| **A03 Injection** | All database access uses the Django ORM (parameterized queries). There is no raw SQL. Templates auto-escape output. Uploads are limited to CSV files of at most 2 MB and 5,000 rows and are parsed as data. | `InjectionTests`: script tags in data are escaped; SQL-like search text is harmless |
| **A04 Insecure design** | Conflict checks run again at approval time. Imports are all-or-nothing. Facilities and users are deactivated, not deleted. The last administrator cannot be removed. Risk scores carry a decision-support disclaimer. | Workflow tests in `scheduling`, `console` and `risks` |
| **A05 Security misconfiguration** | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, HttpOnly cookies, an 8-hour session limit, and `DEBUG` off in production. A System status page flags debug mode, the default key, pending migrations and a single administrator. | `CsrfAndHeaders` tests; `/console/` health checks |
| **A06 Vulnerable components** | Dependencies are pinned in `requirements.txt` (Django 5.2 LTS line). | Re-check with `pip list --outdated` before deployment |
| **A07 Identification and authentication failures** | Login lockout after 5 failures per username and IP (20 per username) for 15 minutes. Django password validators apply. Admin-issued passwords are temporary and must be changed at first login. Every login and failed login is logged. | `LoginThrottleTests`, `console/tests.py`, `audit/tests.py` |
| **A08 Software and data integrity failures** | CSRF protection on every form. Metric definitions are versioned and never edited. Exported reports state their parameters and definition version. | `test_post_without_csrf_token_is_rejected` |
| **A09 Security logging and monitoring failures** | The audit log records who changed what (old and new values), logins, failed logins, password resets, threshold changes (with a reason) and report exports. Audit records cannot be edited or deleted in the admin. | `audit/tests.py`, `reports/tests.py::test_exports_are_audited` |
| **A10 Server-side request forgery** | The system makes no outbound requests based on user input. Notification links are followed only if they point inside the site. | `test_external_link_not_followed`, `test_external_next_url_is_ignored` |

**Notes and residual risks**

1. `check --deploy` reports two remaining warnings: `SECURE_HSTS_INCLUDE_SUBDOMAINS` and
   `SECURE_HSTS_PRELOAD`. They are left off deliberately: a university does not control every
   subdomain, so turning them on could break other campus sites. ICT should decide.
2. No Content-Security-Policy header yet, because Bootstrap and Chart.js load from a CDN and the
   charts use inline scripts. Adding a CSP is recommended for production.
3. Login throttling uses Django's cache. The default in-memory cache is per process, so with
   several server processes, configure a shared cache (database or Redis) for the limits to hold.
4. QR check-in requires login, so it proves *a user* checked in, not that they were physically in
   the room. It is suitable for usage monitoring, not for attendance enforcement.

## 4. Performance testing (KPI 14.1: common views ≤ 3 s)

**Method.** `python manage.py benchmark` generates a campus-sized dataset inside a database
transaction, times each page server-side (median of 3 runs), counts database queries, and then
rolls everything back. The dataset had **150 facilities, 2,228 weekly class slots and 25,423 usage
records** (90 days), about 13 times the demo data. The times exclude network transfer and browser
rendering.

| Page | Server time | DB queries | Result |
|---|---:|---:|---|
| Home dashboard (facilities staff) | 204 ms | 20 | Pass |
| Home dashboard (planner) | 39 ms | 14 | Pass |
| Home dashboard (viewer) | 9 ms | 7 | Pass |
| Facility list | 14 ms | 8 | Pass |
| Facility detail | 18 ms | 9 | Pass |
| Facility weekly calendar | 12 ms | 8 | Pass |
| Find a room (all 150 facilities) | 638 ms | 491 | Pass |
| Utilization dashboard, 28 days | 654 ms | 18 | Pass |
| Utilization dashboard, 90 days, by building | 1,325 ms | 18 | Pass |
| Risk register | 55 ms | 12 | Pass |
| Scheduled vs actual, 28 days | 1,200 ms | 10 | Pass |
| Attendance sheet (today) | 517 ms | 8 | Pass |
| Utilization report preview | 73 ms | 12 | Pass |
| Utilization report PDF | 559 ms | 10 | Pass |
| Risk register Excel | 131 ms | 4 | Pass |

**Nightly jobs** (no interactive target): recalculating 90 days of utilization for 150 facilities
took 18.7 s, and risk detection took 13.6 s. The default nightly run covers 7 days.

**Improvements made because of this test**

| Problem found | Fix | Before → after |
|---|---|---|
| Scheduled vs actual rendered all 9,520 session rows | Campus-wide view lists only sessions needing attention (capped at 300); one-facility view shows all | 3,462 ms (**fail**) → 1,200 ms |
| Each usage record loaded its full facility row | Load only the needed columns; reuse facility objects | 23,861 objects → about 11,000 |
| Dashboard and attendance sheet queried once per session (N+1) | One query for the whole day | 344 → 20 queries; 402 → 8 queries |

**Known limit:** *Find a room* runs a full conflict check for every facility (about 3 queries per
room). It is fast enough at 150 rooms; above about 600 rooms it should be optimized with batched
queries.

## 5. Status of the KPIs in Section 14.1

| KPI | Target | Status now |
|---|---|---|
| Facility master-data completeness | ≥ 98% | Enforced: required fields cannot be empty |
| Facility identifier uniqueness | 100% | Enforced by a database constraint and case-insensitive validation |
| Dashboard response time | ≤ 3 s | **Met** in testing (slowest 1.3 s server time) |
| Monthly availability | ≥ 99.5% | To be measured during the pilot |
| Schedule integration success | ≥ 99% | Import validation and reporting in place; measure during the pilot |
| Report reproducibility | 100% | Every report states its parameters and definition version; formats are tested to match |
| Critical data changes audited | 100% | Automatic for all 14 tracked models, plus logins, exports and threshold changes |
| User adoption | ≥ 80% of pilot users | To be measured during the pilot (see UAT) |
