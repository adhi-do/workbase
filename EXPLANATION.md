# Workbase Architecture & Technical Walkthrough

This document provides a comprehensive technical walkthrough of **Workbase** — explaining what every file does, why it is written this way, and how security, scalability, and operational workflows are enforced without external frontend frameworks (zero React, zero Tailwind, zero Java).

---

## 1. System Overview & The Request Lifecycle

Workbase is architected around Python's WSGI standard using Flask with modular blueprints and SQLite.

```text
[ Browser / Phone Client ]
         │
         │  HTTPS / HTTP Requests (Form POSTs, Geolocation JSON)
         ▼
     [ app.py ] ── (Security Headers, CSRF Verification, Error Handlers)
         │
         ├──► [ blueprints/auth.py ]       ──► [ auth.py ] (PBKDF2 Password Hashing & RBAC)
         ├──► [ blueprints/attendance.py ] ──► [ geo.py ]  (Haversine Distance & Bounds)
         ├──► [ blueprints/admin.py ]      ──► [ database.py ] (Defaulter <70% Aggregation)
         ├──► [ blueprints/tickets.py ]    ──► [ database.py ] (Issue Auditing & Attendance Crediting)
         ├──► [ blueprints/tasks.py ]      ──► [ database.py ] (Operational Task Allocation)
         └──► [ blueprints/resources.py ]  ──► [ database.py ] (Asset Ledger & Booking)
```

1. **Client Request:** A browser sends an HTTP/HTTPS request to the application.
2. **Security Gateway (`app.py`):** Flask checks CSRF tokens on state-changing requests, applies defense-in-depth headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`), and routes traffic to the registered blueprint.
3. **Domain Logic:** The blueprint interacts with `database.py` (persistent storage), `auth.py` (cryptographic verification and role checks), or `geo.py` (geofence math).
4. **Rendering:** Server responds with semantic, responsive HTML templates styled via pure modern CSS (`static/style.css`) or structured JSON responses for GPS check-ins.

---

## 2. database.py — Data Models & Schemas

The database layer utilizes SQLite with foreign key enforcement (`PRAGMA foreign_keys = ON`) and dictionary row mapping (`row_factory = sqlite3.Row`).

### Core Tables & Indexes

* **`users`**: Stores credentials and authorization roles (`admin` or `student`). Enforces role boundaries via SQL `CHECK(role IN ('admin', 'student'))`.
* **`campus`**: Stores geofence perimeters (latitude, longitude, radius in meters). Includes `is_active` to support soft-deactivation so historical attendance audit records maintain referential integrity.
* **`attendance`**: Records verified check-ins. Includes `UNIQUE(user_id, marked_date)` to make duplicate check-ins physically impossible at the database engine level. Includes `method` (`gps` or `ticket_approval`).
* **`issue_tickets`**: Tracks student error disputes for failed GPS captures, sensor drift, or indoor signal blockage.
* **`tasks`**: Extensible foundation model for task assignment, priority levels (`low`, `medium`, `high`, `urgent`), due dates, and lifecycle statuses (`todo`, `in_progress`, `completed`).
* **`resources` & `resource_allocations`**: Extensible foundation models for tracking institutional rooms, laboratories, equipment, and managing reservations.
* **Database Indexes**: Added on `(user_id, marked_date)`, `marked_date`, `issue_tickets(user_id)`, `issue_tickets(status)`, and `tasks(assigned_to)` for optimized sub-millisecond query performance as records grow.

---

## 3. auth.py — Cryptographic Protection & RBAC

### Password Security (PBKDF2-HMAC-SHA256)
Passwords are never stored in plain text.
- **Salting:** Each user gets a unique 16-byte random salt generated via `secrets.token_hex(16)`, eliminating precomputed rainbow table attacks.
- **Key Stretching:** `hashlib.pbkdf2_hmac("sha256", ..., 260_000)` executes 260,000 hashing rounds, multiplying brute-force resistance by 260,000× in accordance with OWASP recommendations.
- **Constant-Time Verification:** Hash comparison uses `hmac.compare_digest()` to eliminate timing-attack vulnerabilities.

### Role-Based Access Control (RBAC)
- `@login_required`: Guards authenticated views; redirects guests to `/login`.
- `@admin_required`: Restricts sensitive endpoints to users with `role == 'admin'`; unauthorized attempts immediately return a clean **403 Forbidden** error.
- **Privilege Separation:** The web registration endpoint (`/register`) hardcodes `role = 'student'`. Administrator accounts can only be provisioned via the secure CLI (`create_admin.py`).

---

## 4. geo.py — Haversine Distance & Anti-Proxy Verification

Distance calculations on Earth must account for planetary curvature:

$$\Delta\sigma = 2 \arcsin \left( \sqrt{\sin^2\left(\frac{\Delta\phi}{2}\right) + \cos(\phi_1)\cos(\phi_2)\sin^2\left(\frac{\Delta\lambda}{2}\right)} \right)$$

$$d = R \cdot \Delta\sigma \quad (R = 6,371,000\text{ meters})$$

* **Multi-Campus Support:** When a student checks in, `find_nearest_campus(lat, lon, campuses)` dynamically evaluates all active campus boundary pins and verifies the student against their nearest pin.
* **Accuracy Ceiling:** Mobile devices occasionally return inaccurate GPS estimations (e.g. WiFi IP lookup with ±500m radius). Workbase caps GPS accuracy forgiveness at 50m and rejects fixes exceeding 300m if outside the boundary radius, preventing location spoofing via artificially inflated accuracy figures.

---

## 5. Attendance Compliance & Defaulter (< 70%) Filtering

Workbase includes automated statistical aggregation in `get_system_attendance_summary(db)`:

$$\text{Attendance Rate} = \left( \frac{\text{Student Attended Days}}{\text{Institutional Tracking Days}} \right) \times 100$$

* If a student's rate drops below **70.0%**, the platform marks `is_defaulter = True`.
* **Interactive Admin Filter:** In `admin_dashboard.html`, administrators can toggle between **All Students**, **Defaulters (< 70%)**, and **Compliant Students** instantly via client-side DOM filtering.
* **Data Portability:** Administrators can download `workbase_defaulters_below_70.csv` and `workbase_attendance_full_log.csv` directly from the executive console.

---

## 6. Built-in Error Reporting & Dispute Ticketing

Physical campuses frequently encounter signal attenuation in basements, reinforced lecture halls, or older smartphone models.

* When a student's GPS fix fails or falls outside the radius boundary, the interface offers an immediate prompt: **File Attendance Issue Ticket**.
* Pre-populates the student's campus, sensor coordinates, and accuracy metadata into the ticket.
* Administrators review tickets under `/admin/tickets` (including Google Maps links to inspect reported coordinates).
* Upon administrative approval, the system updates ticket status and records a verified attendance entry with `method = 'ticket_approval'`, restoring the student's attendance compliance.

---

## 7. Extensible Foundation: Tasks & Resources

To serve as a comprehensive institutional operations platform, Workbase integrates two operational expansion modules:

1. **Task Allocation Module (`blueprints/tasks.py`)**:
   - Administrators assign duty delegations to student personnel with priorities and deadlines.
   - Assignees monitor tasks on their dashboard and update execution states (`todo` → `in_progress` → `completed`).
2. **Resource Management Module (`blueprints/resources.py`)**:
   - Catalogs rooms, laboratory benches, audio-visual gear, and facilities.
   - Tracks real-time availability states (`available`, `in_use`, `maintenance`).
   - Allows users to book windows and audit asset checkout logs.
