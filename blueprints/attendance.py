import sqlite3
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify

from database import get_db, get_student_attendance_stat
from auth import login_required
from geo import haversine_distance, validate_coordinates, find_nearest_campus

attendance_bp = Blueprint("attendance", __name__)

MAX_ACCURACY_BONUS_METERS = 50
MAX_TRUSTED_ACCURACY_METERS = 300


@attendance_bp.route("/")
def index():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin.admin_dashboard"))
        return redirect(url_for("attendance.student_dashboard"))
    return redirect(url_for("auth.login"))


@attendance_bp.route("/dashboard")
@login_required
def student_dashboard():
    if session.get("role") == "admin":
        return redirect(url_for("admin.admin_dashboard"))

    db = get_db()
    campuses = db.execute("SELECT * FROM campus WHERE is_active = 1 ORDER BY id DESC").fetchall()
    
    # Primary or default campus for UI guidance
    default_campus = campuses[0] if campuses else None

    history = db.execute(
        """SELECT a.*, c.name AS campus_name
           FROM attendance a
           LEFT JOIN campus c ON c.id = a.campus_id
           WHERE a.user_id = ?
           ORDER BY a.id DESC LIMIT 15""",
        (session["user_id"],),
    ).fetchall()

    marked_today = db.execute(
        "SELECT 1 FROM attendance WHERE user_id = ? AND marked_date = date('now')",
        (session["user_id"],),
    ).fetchone() is not None

    stats = get_student_attendance_stat(db, session["user_id"])

    # Recent tickets filed by this student
    tickets = db.execute(
        """SELECT t.*, c.name AS campus_name
           FROM issue_tickets t
           LEFT JOIN campus c ON c.id = t.campus_id
           WHERE t.user_id = ?
           ORDER BY t.id DESC LIMIT 5""",
        (session["user_id"],),
    ).fetchall()

    # Tasks assigned to this student
    tasks = db.execute(
        """SELECT * FROM tasks
           WHERE assigned_to = ?
           ORDER BY CASE status WHEN 'todo' THEN 1 WHEN 'in_progress' THEN 2 ELSE 3 END, id DESC
           LIMIT 5""",
        (session["user_id"],),
    ).fetchall()

    db.close()

    return render_template(
        "student_dashboard.html",
        campuses=campuses,
        default_campus=default_campus,
        history=history,
        marked_today=marked_today,
        stats=stats,
        tickets=tickets,
        tasks=tasks,
    )


@attendance_bp.route("/mark-attendance", methods=["POST"])
@login_required
def mark_attendance():
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid GPS coordinates received."}), 400

    if not validate_coordinates(lat, lon):
        return jsonify({"status": "error", "message": "GPS coordinates out of valid planetary bounds."}), 400

    try:
        accuracy = float(data.get("accuracy", 0))
    except (TypeError, ValueError):
        accuracy = 0
    accuracy = max(accuracy, 0)

    db = get_db()
    campuses = db.execute("SELECT * FROM campus WHERE is_active = 1").fetchall()
    if not campuses:
        db.close()
        return jsonify({
            "status": "error",
            "message": "No active campus location has been set by the administrator yet.",
        }), 400

    # Ensure single daily check-in
    already = db.execute(
        "SELECT 1 FROM attendance WHERE user_id = ? AND marked_date = date('now')",
        (session["user_id"],),
    ).fetchone()
    if already:
        db.close()
        return jsonify({
            "status": "error",
            "message": "You have already marked attendance today.",
        }), 409

    # Find the nearest active campus pin
    nearest_campus, min_distance = find_nearest_campus(lat, lon, campuses)
    accuracy_bonus = min(accuracy, MAX_ACCURACY_BONUS_METERS)
    effective_radius = nearest_campus["radius_meters"] + accuracy_bonus

    # Low accuracy safety check
    if accuracy > MAX_TRUSTED_ACCURACY_METERS and min_distance > nearest_campus["radius_meters"]:
        db.close()
        return jsonify({
            "status": "error",
            "message": (
                f"Your device's location accuracy is too low (±{int(accuracy)}m) to verify reliably. "
                "Move outdoors, enable high-precision GPS on your device, and try again."
            ),
            "nearest_campus": nearest_campus["name"],
            "distance": round(min_distance, 1),
            "can_ticket": True,
        }), 403

    # Geofence boundary verification
    if min_distance > effective_radius:
        db.close()
        return jsonify({
            "status": "error",
            "message": (
                f"You are {int(min_distance)}m away from '{nearest_campus['name']}'. "
                f"You must be within {int(nearest_campus['radius_meters'])}m to mark attendance."
            ),
            "nearest_campus": nearest_campus["name"],
            "nearest_campus_id": nearest_campus["id"],
            "distance": round(min_distance, 1),
            "allowed_radius": nearest_campus["radius_meters"],
            "can_ticket": True,
        }), 403

    try:
        db.execute(
            """INSERT INTO attendance (user_id, campus_id, latitude, longitude, distance_meters, method)
               VALUES (?, ?, ?, ?, ?, 'gps')""",
            (session["user_id"], nearest_campus["id"], lat, lon, min_distance),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.close()
        return jsonify({"status": "error", "message": "You have already marked attendance today."}), 409

    db.close()
    return jsonify({
        "status": "success",
        "message": f"Attendance marked successfully! Verified at '{nearest_campus['name']}' ({int(min_distance)}m away).",
        "distance": round(min_distance, 1),
        "campus": nearest_campus["name"],
    })
