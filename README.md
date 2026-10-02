# Campus Attendance

A small website where an admin sets a campus/office location, and students can mark
their attendance only when their device's location is inside the allowed radius.

## 1. Install

```bash
cd campus-attendance
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configure

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the printed value into `.env` as `SECRET_KEY=...`.

## 3. Create the admin account

Admins are never created through the website — only from the terminal, so no one
can sign up as an admin from the browser.

```bash
python create_admin.py
```

Follow the prompts to set an admin username, email, and password.

## 4. Run the app

```bash
python app.py
```

Visit **http://127.0.0.1:5000**. The database file `attendance.db` is created
automatically on first run.

## 5. Using the site

**As admin:**
1. Log in with the account you created in step 3.
2. On the admin dashboard, click "Use My Current Location" (while standing on
   campus) or type the latitude/longitude manually, set a radius in meters, and
   save. Save a new location any time to update it — the most recent one is
   always what students are checked against.
3. Scroll down to see how many students have checked in today and the recent
   check-in list.

**As a student:**
1. Go to `/register` and create an account.
2. Log in, and on the dashboard click "Mark My Presence".
3. Your browser will ask for location permission — allow it. The server checks
   your coordinates against the campus location and only accepts the check-in
   if you're within the allowed radius. Each student can mark attendance once
   per day.

## Notes

- Browser geolocation requires **HTTPS** in production (it works over plain
  HTTP only on `localhost` for local testing).
- Set `FLASK_ENV=production` in `.env` before deploying so debug mode is off
  and session cookies are marked secure (HTTPS-only).
- Put the app behind a real web server (e.g. gunicorn + nginx) for production
  use — `python app.py` runs Flask's built-in dev server, which isn't meant
  for production traffic.

## 6. Deploying to a hosting platform

The app already includes a `Procfile` (`web: gunicorn app:app`) that most
platforms recognize automatically.

**General steps, on any platform (Render, Railway, PythonAnywhere, Fly.io, etc.):**

1. Push this project to a GitHub repo. `.gitignore` already excludes `.env`
   and `attendance.db` — don't remove those lines.
2. Create a new "Web Service" / "App" on the platform and connect your repo.
3. Set the **build command** to `pip install -r requirements.txt` (most
   platforms detect this automatically from `requirements.txt`).
4. Set the **start command** to `gunicorn app:app` (or let the platform read
   the `Procfile`).
5. In the platform's dashboard, add an **environment variable**:
   - `SECRET_KEY` = the random value you generated with
     `python -c "import secrets; print(secrets.token_hex(32))"`
   - `FLASK_ENV` = `production`

   This is what replaces `.env` in production — the platform injects these
   into the app's environment at runtime; `load_dotenv()` in `app.py` is a
   no-op if there's no `.env` file, so this works without any code changes.
6. Deploy. Visit the platform's HTTPS URL (not localhost) — geolocation
   requires HTTPS, and the platform provides that automatically.
7. Open a **shell/console** on the platform (most give you one — Render and
   Railway both do) and run `python create_admin.py` once to create your
   admin account directly on the deployed server.

**Important: SQLite and ephemeral storage.** Many free-tier hosting plans
wipe the filesystem on every redeploy or restart, which means `attendance.db`
— and every account and attendance record in it — disappears. Before relying
on this for real attendance tracking, do one of:
- Pay for a plan with a **persistent disk/volume** (Render, Railway, and
  Fly.io all offer this) and set `DB_PATH` to a path on that disk, or
- Migrate to a managed database (e.g. Postgres) for production use — this
  app is intentionally small so that swap is straightforward, but it's not
  included here.
