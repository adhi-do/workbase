# Workbase — Unified Digital Operations Platform

**Workbase** is a unified digital operations platform built to eliminate proxy attendance via secure, server-side GPS verification while laying an expandable foundation for enterprise resource and task management.

Built purely with **Python (Flask)**, **SQLite**, and **standard semantic HTML5 / Modern CSS / Vanilla JavaScript** — strictly free of React, Tailwind, or Java.

---

## MVP Core Capabilities

1. **Anti-Proxy Geofenced Attendance Verification**
   - Students check in using device GPS coordinates captured via HTML5 Geolocation API.
   - Coordinates are calculated server-side using the Haversine great-circle distance against active campus boundary pins.
   - GPS accuracy ceiling prevents spoofing via wide accuracy tolerances (forgiving up to 50m of natural jitter, but rejecting low-precision spoofed signals).
   - Multi-campus pin support automatically checks in students against the nearest active perimeter.
   - Strict daily uniqueness constraint (`UNIQUE(user_id, marked_date)`) prevents duplicate check-ins.

2. **Executive Administrative Console**
   - Manage campus boundary pins (name, latitude, longitude, permitted radius bounds 5m–5,000m, toggle active/inactive, or safe deletion).
   - Real-time audit log of all check-ins with exact timestamp, student identity, calculated distance, and verification mode.
   - **Compliance & Defaulter Auditing (< 70%):**
     - Automatically aggregates institutional attendance rates.
     - Live interactive filtering to isolate defaulters falling below the mandatory 70% threshold.
     - One-click CSV exports: `workbase_defaulters_below_70.csv` and `workbase_attendance_full_log.csv`.

3. **Built-in Error Reporting & Dispute Ticketing**
   - Dedicated dispute workflow for indoor signal attenuation, concrete barrier interference, or browser sensor drift.
   - Captures issue categories (`gps_failure`, `inaccurate_location`, `device_error`, `other`), physical room location, and optional coordinate evidence.
   - Administrators audit tickets and can **Approve & Credit Attendance** retroactively to the student's record under the `ticket_approval` ledger.

4. **Extensible Foundation: Task Allocation & Resource Management**
   - **Task Module:** Assign operational workflows and duty delegations with priority ratings (`low`, `medium`, `high`, `urgent`), deadlines, and lifecycle transitions (`todo` → `in_progress` → `completed`).
   - **Resource Module:** Register, track, book, and return institutional facilities, laboratories, and physical hardware.

---

## Directory Architecture

```text
workbase/
├── app.py                  # Application factory, security headers, error handlers, and blueprint registration
├── auth.py                 # Core cryptographic hashing (PBKDF2-HMAC-SHA256) & RBAC decorators
├── database.py             # SQLite schema, connection management, migrations, and analytics queries
├── geo.py                  # Haversine distance calculations and WGS84 coordinate boundary validation
├── create_admin.py         # Secure CLI utility for provisioning admin accounts (interactive or flag-based)
├── blueprints/             # Modular routing ready to scale
│   ├── __init__.py
│   ├── auth.py             # Authentication endpoints (/login, /register, /logout)
│   ├── attendance.py       # Core attendance check-in & student portal (/dashboard, /mark-attendance)
│   ├── admin.py            # Administrative controls, campus pins, and <70% defaulter filtering (/admin)
│   ├── tickets.py          # Built-in issue ticketing & dispute resolution (/tickets, /admin/tickets)
│   ├── tasks.py            # Operational task allocation (/tasks)
│   └── resources.py        # Institutional resource management (/resources)
├── static/
│   └── style.css           # Modern, responsive CSS design (zero Tailwind, zero frameworks)
├── templates/              # Clean semantic HTML templates
│   ├── base.html           # Unified navigation, flash messages, role-based controls
│   ├── login.html          # Authentication form
│   ├── register.html       # Student self-registration form
│   ├── student_dashboard.html # Attendance action, 70% progress meter, history, tasks
│   ├── admin_dashboard.html   # Campus pins, defaulter filtering, real-time logs
│   ├── tickets.html        # Student dispute ticketing
│   ├── admin_tickets.html  # Admin dispute auditing and attendance crediting
│   ├── tasks.html          # Task allocation and status management
│   └── resources.html      # Asset catalog and reservation system
├── tests/
│   └── test_workbase.py    # Comprehensive automated test suite (21 unit & integration tests)
├── .env.example            # Environment configuration template
├── Procfile                # WSGI deployment configuration (gunicorn app:app)
└── requirements.txt        # Production dependencies
```

---

## Quickstart Guide

### 1. Environment Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate    # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Generate and set a random 64-character secret key:
```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))" >> .env
echo "FLASK_ENV=development" >> .env
```

### 3. Create Administrator Account

Administrators can **only** be created from the CLI to eliminate browser privilege escalation:

**Interactive prompt:**
```bash
python3 create_admin.py
```

**Or automated flag-based setup:**
```bash
python3 create_admin.py --username admin --email admin@workbase.io --password MySecurePassword123
```

### 4. Run Automated Test Suite

Verify all 21 unit and integration tests across auth, GPS verification, RBAC, defaulter calculations, and ticketing:

```bash
python3 -m unittest discover -s tests
```

### 5. Launch the Application

```bash
python3 app.py
```

Access the application in your browser at **http://127.0.0.1:5000**.

---

## Production Security Notes

* **HTTPS Enforcement:** The HTML5 Geolocation API requires a secure HTTPS origin in production (modern browsers restrict GPS on plain HTTP outside `localhost`).
* **Session Cookies:** Set `FLASK_ENV=production` in production. This activates `SESSION_COOKIE_SECURE=True` (HTTPS-only) alongside `HTTPOnly` and `SameSite=Lax`.
* **CSRF Protection:** Every state-changing form and AJAX request is cryptographically verified via Flask-WTF tokens.
* **Tamper-Resistant GPS:** Client clocks and spoofed accuracy numbers are mitigated server-side: accuracy tolerance is capped at 50m, coordinate boundaries are validated, and distances are computed server-side via Haversine geometry.
