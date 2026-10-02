# How This Project Works — A Full Walkthrough

This document teaches you what every file does and why it's written the way it
is. Nothing here is needed to *run* the app (see README.md for that) — this is
purely for learning. Read it alongside the actual files open in another window.

---

## 1. The big picture — how a request flows through the app

1. A browser sends a request (e.g. "GET /login") to **app.py**, which is the
   single entry point of the whole website. Flask receives it and looks at the
   URL and method to decide which Python function ("view") should handle it.
2. That view function talks to **database.py** to read/write SQLite data,
   **auth.py** to check passwords or session state, and **geo.py** to do the
   distance math.
3. The view returns either a rendered HTML page (from `templates/`) or, for
   the attendance check, a small JSON response that JavaScript in the browser
   reads.
4. The browser displays the page; if there's JavaScript (like the "Mark My
   Presence" button), it runs in the user's browser and can call back into the
   server via `fetch()`.

So the request lifecycle is: **browser → app.py (routing) → database.py /
auth.py / geo.py (logic) → templates/ (HTML) → browser**.

---

## 2. database.py — where data lives

We use SQLite because it's a single file (`attendance.db`), needs no separate
server, and is perfectly fine for a college/office-sized user base.

- `SCHEMA` is a multi-statement SQL string defining three tables:
  - **users**: one row per person. `password_hash` and `salt` store the
    password securely (explained in section 3) — the real password is never
    stored anywhere. `role` is either `'admin'` or `'student'`, and the
    `CHECK` constraint stops anything else from ever being inserted, even by
    a bug elsewhere in the code.
  - **campus**: each row is one saved location an admin has set (name,
    latitude, longitude, allowed radius). We keep history instead of
    overwriting, so `created_by` and `created_at` give you an audit trail.
  - **attendance**: one row per check-in. The `UNIQUE(user_id, marked_date)`
    constraint is doing real work — it makes it *physically impossible* at
    the database level for the same student to have two rows on the same
    date, even if the Python code above it had a bug. This is "defense in
    depth": don't rely on only one layer to enforce a rule.
- `get_db()` opens a new connection each time it's called and sets
  `row_factory = sqlite3.Row` so query results behave like dictionaries
  (`row['username']`) instead of plain tuples — much easier to read in
  templates. `PRAGMA foreign_keys = ON` makes SQLite actually enforce the
  `FOREIGN KEY` relationships (SQLite ignores them by default).
- `init_db()` runs the schema. `CREATE TABLE IF NOT EXISTS` means this is safe
  to call every time the app starts — it does nothing if the tables already
  exist, and creates them on the very first run.

---

## 3. auth.py — passwords and access control

### Why not store passwords directly?
If the database were ever leaked, plain-text passwords would hand over every
account immediately. So we transform each password into a **hash** — a
one-way fingerprint that's cheap to check but computationally impractical to
reverse.

### Why PBKDF2 instead of a single `hashlib.sha256(password)` call?
A single, fast hash is *bad* for passwords, because attackers can try billions
of guesses per second against a leaked hash. PBKDF2 fixes this by:
- **Salting**: `secrets.token_hex(16)` generates a random value that's mixed
  into the hash and stored alongside it. Without a salt, two users with the
  same password would have identical hashes, and attackers could pre-compute
  ("rainbow table") common password hashes once and reuse them against every
  account. With a per-user salt, that shortcut is gone.
- **Stretching**: `hashlib.pbkdf2_hmac(..., 260_000)` repeats the hashing
  function 260,000 times. That's intentionally slow (a few milliseconds) —
  irrelevant for a real login, but it multiplies an attacker's guessing cost
  by 260,000×. This iteration count matches current OWASP guidance for
  PBKDF2-SHA256.
- `hash_password()` returns `(hash_hex, salt_hex)`, both stored as text
  columns in `users`.
- `verify_password()` recomputes the hash from the *submitted* password using
  the *stored* salt, then compares it to the stored hash using
  `hmac.compare_digest()` — a constant-time comparison. A normal `==`
  comparison exits as soon as it finds a mismatched character, which leaks
  (via tiny timing differences) how many characters were correct. That's a
  real attack class called a timing attack; `compare_digest` always takes the
  same time regardless of where the mismatch is.

### Access-control decorators
- `login_required`: wraps a view function so that, before it runs, Flask
  checks `session['user_id']` exists. If not, the user is redirected to
  `/login`. This is Python's decorator pattern — `@login_required` above a
  view is equivalent to writing that check at the top of every protected
  view by hand, but without repeating the code.
- `admin_required`: does the same, plus checks `session['role'] == 'admin'`.
  If a logged-in *student* tries to visit an admin-only URL, they get a
  **403 Forbidden**, not a redirect — an explicit "you don't have permission"
  rather than silently sending them somewhere else.

---

## 4. geo.py — deciding "are you actually here?"

`haversine_distance()` implements the **haversine formula**, the standard way
to compute the distance between two latitude/longitude points on a sphere
(the Earth). Straight-line ("Euclidean") distance doesn't work on a globe
because degrees of longitude represent different real-world distances
depending on how far you are from the equator — haversine accounts for that
curvature. The function converts degrees to radians, applies the formula, and
multiplies by Earth's radius (in meters) to return a distance in meters.

This is deliberately kept as a pure function with no database or Flask code
in it, so you could copy this one function into a totally different project
and it would still work — and it's easy to test in isolation.

---

## 5. app.py — the entry point, route by route

### Setup block
```python
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
```
The secret key is what Flask uses to cryptographically **sign** session
cookies, so a user can't edit their own cookie to change `role` from
`student` to `admin` — any tampering invalidates the signature and Flask
rejects the cookie. If you don't set `SECRET_KEY` in `.env`, a random one is
generated every restart — fine for quick testing, but it means everyone gets
logged out whenever the server restarts, which is why the README tells you
to set a real one.

```python
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
)
```
- `HTTPONLY` stops any JavaScript (including injected malicious JavaScript
  from an XSS bug) from reading the session cookie via `document.cookie`.
- `SAMESITE="Lax"` stops the cookie from being sent along on cross-site
  requests initiated by other websites, which is the core defense against
  **CSRF** (a malicious site making your browser submit a form to this app
  without you meaning to).
- `SECURE` (only in production) tells the browser to only ever send the
  cookie over HTTPS, never plain HTTP, so it can't be sniffed on the network.

```python
csrf = CSRFProtect(app)
```
This adds a second, explicit layer of CSRF protection on top of
`SameSite=Lax`: every `POST` form must include a hidden `csrf_token` field
(you'll see `{{ csrf_token() }}` in every template's `<form>`), and every
JSON `fetch()` call must send it in the `X-CSRFToken` header (see
`student_dashboard.html`). If the token is missing or wrong, Flask-WTF
rejects the request before your view code even runs.

```python
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
```
Regular expressions used to reject obviously-malformed input early, before it
ever reaches the database.

### `/` — index
Just a traffic router: sends logged-in admins to `/admin`, logged-in students
to `/dashboard`, and everyone else to `/login`.

### `/register` (GET shows the form, POST creates the account)
1. Reads and trims the submitted fields.
2. Validates username format, email format, and a minimum password length —
   all server-side, because client-side HTML validation (`required`,
   `pattern`) can always be bypassed by anyone who disables JavaScript or
   crafts a raw HTTP request.
3. Hashes the password (section 3) and inserts a new row with
   `role = 'student'` hard-coded — there is no form field for role at all, so
   there's no way to submit `role=admin` from this form even by tampering
   with the request.
4. `try/except sqlite3.IntegrityError` catches the case where the `UNIQUE`
   constraint on `username`/`email` fails, and turns it into a friendly flash
   message instead of crashing.

### `/login` (GET shows the form, POST checks credentials)
Looks up the user by username, then calls `verify_password()`. Notice the
error message is the same ("Invalid username or password") whether the
username doesn't exist *or* the password is wrong — this stops an attacker
from using the login form to discover which usernames are registered
("username enumeration").

On success: `session.clear()` first (wipes any leftover session data from a
previous login), then stores `user_id`, `username`, and `role`. Flask signs
and encrypts this into a cookie automatically.

### `/logout`
`session.clear()` deletes all session data server-side-equivalent (Flask
sessions live in the signed cookie, so clearing it means the cookie no longer
carries any identity) and redirects to `/login`.

### `/admin` (admin_required)
- **POST**: validates that latitude is between -90 and 90, longitude between
  -180 and 180, and radius between 5 and 5000 meters, all using a `try/except
  ValueError` around explicit `float()` conversion and range checks — this
  stops garbage or wildly wrong values (like a radius of -50 or a latitude of
  9000) from ever reaching the database. Then it **inserts a new row**
  (doesn't edit the old one), so you keep a history of every location ever
  set.
- **GET**: loads every saved campus row (newest first), how many students
  checked in today, and the 20 most recent check-ins — using a SQL `JOIN`
  between `attendance` and `users` so the template can show usernames instead
  of raw numeric IDs.

### `/dashboard` (login_required) — student's own page
Fetches the single most recent campus row (`ORDER BY id DESC LIMIT 1` — that
is "whatever the admin saved last"), this student's last 10 check-ins, and
whether they've already checked in today. The template uses `marked_today` to
decide whether to show the "Mark My Presence" button at all.

### `/mark-attendance` (login_required) — the actual security-critical route
This is the one route that JavaScript calls with `fetch()` instead of a normal
form submit, and it's worth reading closely because it's where "the student
must actually be near campus" is enforced:

1. Parses `lat`/`lon` out of the JSON body, and immediately rejects anything
   that isn't a valid float or is outside real-world coordinate ranges.
   **This never trusts the browser's JavaScript to have done this
   correctly** — the client-side code is just a convenience; the server
   re-validates everything, because a browser's DevTools console can call
   `fetch('/mark-attendance', {body: '{"lat": 999, "lon": 999}'})` directly,
   bypassing your HTML/JS entirely.
2. Loads the current campus location. If none has been set, refuses with a
   clear message rather than crashing on `None`.
3. Checks whether this user already has a row for today's date and refuses
   with `409 Conflict` if so (this check plus the database's `UNIQUE`
   constraint from section 2 form the two "defense in depth" layers
   mentioned earlier — the check gives a friendly message, the constraint
   guarantees correctness even under a race condition where two requests
   arrive at nearly the same instant).
4. Calls `haversine_distance()` to compute how far the submitted coordinates
   are from the saved campus coordinates, **on the server**, using the
   **server's stored campus location** — a student's browser never gets to
   claim "I am 0 meters away," it only ever gets to report raw GPS
   coordinates, and the server does the trustworthy comparison.
5. Real GPS is never perfectly exact — even standing still, a phone's
   `position.coords.accuracy` (the browser's own estimate of its margin of
   error, in meters) is rarely 0. So the server adds a small, **capped**
   buffer to the allowed radius: `accuracy_bonus = min(accuracy, 50)`. The
   cap matters — without it, a modified client could just claim
   `"accuracy": 999999` in the JSON body and get accepted from anywhere; the
   server only ever trusts up to 50m of forgiveness, never an arbitrary
   claim. Separately, if the reported accuracy is worse than 300m *and* the
   student is still outside the radius, the server returns a specific
   "your accuracy is too low" message instead of a flat rejection — this is
   almost always what's happening when someone is standing at the exact
   right spot but still gets rejected: their device (often a laptop without
   GPS hardware) is using WiFi/IP-based location, which can be off by
   hundreds of meters to several kilometers.
6. If `distance` is still beyond `effective_radius` (the saved radius plus
   the capped accuracy bonus), returns `403 Forbidden` with the actual
   distance in the message, so the student understands why it failed rather
   than getting a generic error.
7. Otherwise inserts the attendance row and returns `200` with a success
   message. The `try/except sqlite3.IntegrityError` here is the second layer
   defending against the "double-submit" race condition described in step 3.

### Why `X-CSRFToken` header on the fetch() call
Flask-WTF's `CSRFProtect` checks *either* a `csrf_token` form field *or* the
`X-CSRFToken` header. Since `/mark-attendance` receives JSON (not a form
post), `student_dashboard.html` reads the token Jinja already rendered into
the page (`{{ csrf_token() }}`) into a JavaScript variable and attaches it as
a header on the `fetch()` call.

### `init_db()` at module level
`init_db()` is called right after `csrf = CSRFProtect(app)`, at the top level
of the file — not just inside `if __name__ == "__main__":`. That's
deliberate: `python app.py` executes `__main__`, but a production server
like gunicorn instead *imports* `app.py` and looks for the `app` object
inside it (`gunicorn app:app`) — it never runs the `__main__` block at all.
Putting `init_db()` at import time means the tables get created (or confirmed
to already exist, since it's `CREATE TABLE IF NOT EXISTS`) no matter which
way the app is started.

### `if __name__ == "__main__":`
This block only runs when you execute `python app.py` directly (not when the
file is imported, e.g. by `create_admin.py` or by gunicorn). It starts
Flask's built-in development server, which is fine for local testing but not
for real traffic. `debug_mode` is tied to `FLASK_ENV` so that in production,
Flask never shows the interactive debugger or stack traces to visitors
(which would otherwise leak source code and, historically, has been a direct
route to remote code execution on mis-configured servers).

### Procfile
`Procfile` contains one line: `web: gunicorn app:app`. This is a convention
several hosting platforms (Render, Railway, Heroku) read automatically to
know how to start your app in production — `app:app` means "import the
`app` variable from `app.py`," which is the actual `Flask(__name__)` object
gunicorn needs to serve requests.

---

## 6. create_admin.py — why admin creation isn't a web form

If there were a public `/register-admin` page, anyone could just navigate to
it and grant themselves admin rights — a form field can always be tampered
with, no matter how well you hide the button in the UI. Instead, becoming an
admin requires **running a script on the machine hosting the app**, which
only someone with legitimate server access can do. It reuses the exact same
`hash_password()` function as normal registration, so admin passwords get the
same protection.

---

## 7. templates/ — how the pages are put together

- **base.html** is the shared shell: HTML `<head>`, the top navbar, and a
  block that renders any "flash messages" (the little colored banners like
  "Invalid username or password"). Every other template starts with
  `{% extends "base.html" %}` and only fills in the parts that differ,
  avoiding copy-pasted HTML.
- Every `<form method="POST">` includes
  `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` —
  this is what `CSRFProtect` checks on submission (section 5).
- Jinja2 (Flask's template language) **auto-escapes** every `{{ variable }}`
  by default, converting characters like `<` and `>` into safe HTML entities.
  This is what stops a student from registering with a username like
  `<script>alert(1)</script>` and having that script actually execute when
  an admin later views the attendance list — it gets displayed as harmless
  text instead. This is why the templates never use Jinja's `| safe` filter
  anywhere — that filter would turn auto-escaping off.
- **student_dashboard.html**'s inline `<script>` is the browser-side half of
  the flow described in the `/mark-attendance` section above: it calls
  `navigator.geolocation.getCurrentPosition()` (a browser API that asks the
  user for permission, then reports their device's real GPS/network-based
  coordinates), sends those coordinates to the server, and displays whatever
  message the server sends back.

---

## 8. Design decisions worth knowing about

- **No `flask-cors`**: your original `index.html` file talked to a Flask
  server on a different origin, which needed CORS. This rebuilt app serves
  its HTML *and* its API from the same Flask app, so the browser considers
  every request "same-origin" and CORS isn't needed at all — which is also
  more secure, since CORS exists specifically to relax a browser security
  boundary, and the fewer boundaries you relax, the smaller your attack
  surface.
- **SQLite with parameterized queries**: every single SQL statement in this
  project uses `?` placeholders with values passed separately (e.g.
  `db.execute("... WHERE username = ?", (username,))`), never Python string
  formatting/concatenation to build SQL. This is what prevents **SQL
  injection** — the database driver keeps user input strictly as data, never
  as executable SQL syntax, no matter what characters a user types in.
- **Geolocation requires HTTPS in real deployments**: browsers block the
  Geolocation API entirely on plain HTTP except on `localhost`, specifically
  to stop attackers on the network from tricking a site into leaking a
  user's location over an unencrypted connection. This is a browser-enforced
  rule, not something this app's code controls — it's why the README calls
  out that you need real HTTPS once you deploy this beyond your own machine.

---

## 9. Realistic next steps if you keep building this

These aren't implemented, so you understand exactly what's still missing:
- **Rate limiting** login attempts (e.g. with `flask-limiter`) to slow down
  password-guessing attacks.
- **Email verification** on registration, so students can't register with an
  email address they don't own.
- **HTTPS termination** (e.g. via nginx + Let's Encrypt, or a host that
  provides it) — required before any real deployment.
- **A production WSGI server** (gunicorn, waitress) in front of the app
  instead of `python app.py`, which is only meant for local development.
