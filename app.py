import os
import re
import sqlite3

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_wtf import CSRFProtect
from dotenv import load_dotenv

from database import get_db, init_db
from auth import hash_password, verify_password, login_required, admin_required
from geo import haversine_distance

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
)

csrf = CSRFProtect(app)

# Runs on import, so this works whether you start the app with
# `python app.py` (dev) or with gunicorn/another WSGI server (production).
# CREATE TABLE IF NOT EXISTS makes this safe to call every time.
init_db()

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.route("/")
def index():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("student_dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not USERNAME_RE.match(username):
            flash("Username must be 3-30 characters: letters, numbers, underscore only.", "error")
            return render_template("register.html")
        if not EMAIL_RE.match(email):
            flash("Please enter a valid email address.", "error")
            return render_template("register.html")
        if len(password) < 8:
            flash("Password must be at least 8 characters long.", "error")
            return render_template("register.html")

        password_hash, salt = hash_password(password)

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, email, password_hash, salt, role) VALUES (?, ?, ?, ?, 'student')",
                (username, email, password_hash, salt),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("That username or email is already registered.", "error")
            return render_template("register.html")
        finally:
            db.close()

        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        db.close()

        if user is None or not verify_password(password, user["salt"], user["password_hash"]):
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]

        if user["role"] == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("student_dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_dashboard():
    db = get_db()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        lat = request.form.get("latitude", "")
        lon = request.form.get("longitude", "")
        radius = request.form.get("radius", "")

        try:
            lat_f = float(lat)
            lon_f = float(lon)
            radius_f = float(radius)
            if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
                raise ValueError
            if not (5 <= radius_f <= 5000):
                raise ValueError
        except ValueError:
            flash("Please provide a valid latitude, longitude and radius (5-5000 meters).", "error")
            db.close()
            return redirect(url_for("admin_dashboard"))

        if not name:
            name = "Main Campus"

        db.execute(
            "INSERT INTO campus (name, latitude, longitude, radius_meters, created_by) VALUES (?, ?, ?, ?, ?)",
            (name, lat_f, lon_f, radius_f, session["user_id"]),
        )
        db.commit()
        flash("Campus location saved.", "success")
        db.close()
        return redirect(url_for("admin_dashboard"))

    campuses = db.execute("SELECT * FROM campus ORDER BY id DESC").fetchall()
    today_count = db.execute(
        "SELECT COUNT(*) AS c FROM attendance WHERE marked_date = date('now')"
    ).fetchone()["c"]
    recent = db.execute(
        """SELECT attendance.*, users.username FROM attendance
           JOIN users ON users.id = attendance.user_id
           ORDER BY attendance.id DESC LIMIT 20"""
    ).fetchall()
    db.close()

    return render_template(
        "admin_dashboard.html", campuses=campuses, today_count=today_count, recent=recent
    )


@app.route("/dashboard")
@login_required
def student_dashboard():
    db = get_db()
    campus = db.execute("SELECT * FROM campus ORDER BY id DESC LIMIT 1").fetchone()
    history = db.execute(
        "SELECT * FROM attendance WHERE user_id = ? ORDER BY id DESC LIMIT 10",
        (session["user_id"],),
    ).fetchall()
    marked_today = db.execute(
        "SELECT 1 FROM attendance WHERE user_id = ? AND marked_date = date('now')",
        (session["user_id"],),
    ).fetchone() is not None
    db.close()

    return render_template(
        "student_dashboard.html", campus=campus, history=history, marked_today=marked_today
    )


@app.route("/mark-attendance", methods=["POST"])
@login_required
def mark_attendance():
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid coordinates received."}), 400

    try:
        accuracy = float(data.get("accuracy", 0))
    except (TypeError, ValueError):
        accuracy = 0
    accuracy = max(accuracy, 0)

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify({"status": "error", "message": "Coordinates out of range."}), 400

    # A real GPS fix always carries some jitter, even standing still, so we
    # forgive a small amount of it. The forgiveness is capped so someone
    # can't fake a huge "accuracy" value in the request to mark attendance
    # from anywhere — the browser reports accuracy, but we never trust it
    # past this ceiling.
    MAX_ACCURACY_BONUS_METERS = 50
    MAX_TRUSTED_ACCURACY_METERS = 300
    accuracy_bonus = min(accuracy, MAX_ACCURACY_BONUS_METERS)

    db = get_db()
    campus = db.execute("SELECT * FROM campus ORDER BY id DESC LIMIT 1").fetchone()
    if campus is None:
        db.close()
        return jsonify({"status": "error", "message": "No campus location has been set by the admin yet."}), 400

    already = db.execute(
        "SELECT 1 FROM attendance WHERE user_id = ? AND marked_date = date('now')",
        (session["user_id"],),
    ).fetchone()
    if already:
        db.close()
        return jsonify({"status": "error", "message": "You have already marked attendance today."}), 409

    distance = haversine_distance(lat, lon, campus["latitude"], campus["longitude"])
    effective_radius = campus["radius_meters"] + accuracy_bonus

    if accuracy > MAX_TRUSTED_ACCURACY_METERS and distance > campus["radius_meters"]:
        db.close()
        return jsonify({
            "status": "error",
            "message": (
                f"Your device's location accuracy is too low (±{int(accuracy)}m) to verify "
                "reliably. Move outdoors, make sure GPS/location services (not just WiFi) "
                "is enabled, and try again."
            ),
        }), 403

    if distance > effective_radius:
        db.close()
        return jsonify({
            "status": "error",
            "message": f"You are {int(distance)}m away from campus. You must be within {int(campus['radius_meters'])}m to mark attendance.",
        }), 403

    try:
        db.execute(
            "INSERT INTO attendance (user_id, campus_id, latitude, longitude, distance_meters) VALUES (?, ?, ?, ?, ?)",
            (session["user_id"], campus["id"], lat, lon, distance),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.close()
        return jsonify({"status": "error", "message": "You have already marked attendance today."}), 409

    db.close()
    return jsonify({
        "status": "success",
        "message": f"Attendance marked! You were {int(distance)}m from campus.",
    })


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_ENV") != "production"
    app.run(port=5000, debug=debug_mode)
