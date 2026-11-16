# FaciliTrack

**Campus Facility Usage Monitoring with Risk Analytics System (CFUMRAS)**

Django + Bootstrap 5 + SQLite (development) / PostgreSQL (deployment).

## Setup (each team member, first time)

```bash
git clone <repo-url> FaciliTrack
cd FaciliTrack
py -3.12 -m venv .venv
.venv\Scripts\activate          # Windows (PowerShell / cmd)
# source .venv/Scripts/activate # Git Bash
pip install -r requirements.txt
copy .env.example .env          # then set a SECRET_KEY in .env
python manage.py migrate
python manage.py seed_demo      # demo users, buildings, facilities, schedules
python manage.py runserver
```

Open http://127.0.0.1:8000. Every demo user's password is `Demo@12345`.

| Username   | Role                              |
|------------|-----------------------------------|
| admin      | System Administrator (superuser)  |
| facilities | Facilities Management             |
| registrar  | Registrar / Scheduler             |
| planner    | Planning / Institutional Research |
| depthead   | Department Head                   |
| viewer     | Viewer (Faculty / Staff)          |

## Utilization calculation (nightly job)

Utilization is stored as one row per facility per day and recalculated by:

```bash
python manage.py calculate_utilization            # last 7 days (picks up late entries)
python manage.py calculate_utilization --days 120 # longer window
```

Then run risk detection, which reads those metrics:

```bash
python manage.py detect_risks
```

Run both nightly, utilization first. On Windows, create a Task Scheduler task with two actions (adjust the path):

```
Program:   D:\FaciliTrack\.venv\Scripts\python.exe
Arguments: manage.py calculate_utilization
Start in:  D:\FaciliTrack

Program:   D:\FaciliTrack\.venv\Scripts\python.exe
Arguments: manage.py detect_risks
Start in:  D:\FaciliTrack
```

On Linux: `0 1 * * * cd /srv/facilitrack && .venv/bin/python manage.py calculate_utilization && .venv/bin/python manage.py detect_risks`.
The dashboard shows "Data as of …" and flags data older than 26 hours as stale.
Planners and admins can also press **Recalculate** on the dashboard.

To regenerate all demo schedules, usage and metrics: `python manage.py seed_demo --reset` (DEBUG only).

## Administration console

**Admin → System status** (`/console/`) shows health checks, data counts and configuration, and can run
the nightly jobs on demand. From there admins manage **Users** and **Departments**; admins and planners
manage **Thresholds** (risk and utilization settings, each change recorded with a reason) and
**Metric definitions**.

- New users and password resets get a **temporary password**, shown once. The user must change it at first login.
- Users are deactivated, never deleted, so their history stays intact.
- The last active administrator can't be demoted or deactivated, and admins can't deactivate themselves.
- Only a superuser can edit a superuser account. Django's raw `/admin/` remains available to staff accounts.

## Alerts and email

Alerts appear under the bell icon. They are raised for new reservation requests (to approvers),
approval, rejection and cancellation (to the requester), new or escalated **High** risks (to the owner
and admins), risk ownership changes, assigned actions, and overdue actions (one reminder per day,
sent when `detect_risks` runs).

To also send them by email, set `NOTIFY_BY_EMAIL=True` in `.env`. Without SMTP settings, emails are
printed in the `runserver` terminal, which is enough for a demo. For real email, fill in the
`EMAIL_*` settings shown in `.env.example`.

## Reports

`/reports/` has four standard reports (utilization, scheduled vs actual, risk register and facility
inventory), each downloadable as **PDF, Excel or CSV**. Every report states its parameters, metric
definition version and generation time, and every export is recorded in the audit log.

## Testing QR check-in with a phone

QR codes point to the address you opened the site with, so `127.0.0.1` codes won't work on a phone.
With the phone on the same Wi-Fi as your computer:

1. Find your computer's IP address (`ipconfig`, look for IPv4, e.g. `192.168.1.10`).
2. In `.env`, set `ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.10`.
3. Run `python manage.py runserver 0.0.0.0:8000`.
4. Open `http://192.168.1.10:8000/usage/qr/` on the computer and scan a code with the phone.

Windows Firewall may ask to allow Python on private networks. Allow it.

## Run tests

```bash
python manage.py test                 # 194 tests, about 20 seconds

pip install -r requirements-dev.txt   # once, for coverage
coverage run manage.py test && coverage report
coverage html                         # browsable report in htmlcov/index.html

python manage.py benchmark            # performance test on 150 generated rooms (rolled back afterwards)
```

Results, the OWASP Top 10 review and performance figures are in [docs/TESTING.md](docs/TESTING.md).
The user acceptance testing kit (scripts per role and questionnaire) is in [docs/UAT.md](docs/UAT.md).

## Project structure

| App          | Responsibility (paper section)                                  |
|--------------|-----------------------------------------------------------------|
| `config`     | Django settings and root URLs                                    |
| `core`       | Dashboard, system settings (risk thresholds), seed command       |
| `accounts`   | Users, roles (RBAC), departments, login                          |
| `facilities` | Buildings, facility types, facility registry, maintenance (WP2)  |
| `scheduling` | Activities, class schedules, reservations, conflicts (WP3)       |
| `usage`      | Actual usage records: manual, QR, import (WP4)                   |
| `analytics`  | Metric definitions and utilization metrics (WP5, Section 7.3)    |
| `risks`      | Risk register, scoring (likelihood x impact), mitigation (WP5)   |
| `audit`      | Automatic audit log of data changes and logins (R9)              |

## Role-based access in views

```python
from accounts.models import User
from accounts.permissions import RoleRequiredMixin, role_required

@role_required(User.Role.ADMIN, User.Role.FACILITIES)
def edit_facility(request, pk): ...

class FacilityCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = [User.Role.ADMIN, User.Role.FACILITIES]
```

## Audit log

Changes to the models listed in `audit/signals.py` (`TRACKED_MODELS`) are logged
automatically with the user, timestamp, IP address and old/new values. Add new models
to that list when you create them. Admins can view the log at `/audit/`.

## Build progress

- [x] Step 1: Project setup
- [x] Step 2: Database models (all entities in Section 8.1)
- [x] Step 3: Authentication, roles, audit log
- [x] Step 4: Facility registry pages
- [x] Step 5: Schedules, reservations, conflict detection
- [x] Step 6: Usage monitoring
- [x] Step 7: Utilization analytics
- [x] Step 8: Risk analytics engine
- [x] Step 9: Dashboards, alerts, reports
- [x] Step 10: Administration console
- [x] Step 11: Testing ([docs/TESTING.md](docs/TESTING.md), [docs/UAT.md](docs/UAT.md))
- [ ] Step 12: Deployment and pilot
