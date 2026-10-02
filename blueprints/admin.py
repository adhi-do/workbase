import csv
import io
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, Response

from database import get_db, get_system_attendance_summary
from auth import admin_required
from geo import validate_coordinates

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("", methods=["GET", "POST"])
@admin_bp.route("/", methods=["GET", "POST"])
@admin_required
def admin_dashboard():
    db = get_db()

    if request.method == "POST":
        name = request.form.get("name", "").strip() or "Main Campus"
        lat = request.form.get("latitude", "")
        lon = request.form.get("longitude", "")
        radius = request.form.get("radius", "")

        try:
            lat_f = float(lat)
            lon_f = float(lon)
            radius_f = float(radius)
            if not validate_coordinates(lat_f, lon_f):
                raise ValueError("Coordinates out of bounds.")
            if not (5 <= radius_f <= 5000):
                raise ValueError("Radius must be between 5 and 5,000 meters.")
        except (TypeError, ValueError) as err:
            flash(f"Invalid input: {err or 'Please provide valid latitude, longitude, and radius.'}", "error")
            db.close()
            return redirect(url_for("admin.admin_dashboard"))

        db.execute(
            """INSERT INTO campus (name, latitude, longitude, radius_meters, is_active, created_by)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (name, lat_f, lon_f, radius_f, session["user_id"]),
        )
        db.commit()
        flash(f"Campus pin '{name}' saved successfully.", "success")
        db.close()
        return redirect(url_for("admin.admin_dashboard"))

    # Campuses
    campuses = db.execute("SELECT * FROM campus ORDER BY is_active DESC, id DESC").fetchall()

    # Today's check-ins count
    today_count = db.execute(
        "SELECT COUNT(*) AS c FROM attendance WHERE marked_date = date('now')"
    ).fetchone()["c"]

    # Pending tickets count
    pending_tickets_count = db.execute(
        "SELECT COUNT(*) AS c FROM issue_tickets WHERE status = 'pending'"
    ).fetchone()["c"]

    # Real-time attendance log
    recent_logs = db.execute(
        """SELECT a.*, u.username, u.email, c.name AS campus_name
           FROM attendance a
           JOIN users u ON u.id = a.user_id
           LEFT JOIN campus c ON c.id = a.campus_id
           ORDER BY a.id DESC LIMIT 50"""
    ).fetchall()

    # Defaulter (<70%) and Student Attendance Analysis
    summary = get_system_attendance_summary(db)

    # Recent pending tickets
    pending_tickets = db.execute(
        """SELECT t.*, u.username, u.email, c.name AS campus_name
           FROM issue_tickets t
           JOIN users u ON u.id = t.user_id
           LEFT JOIN campus c ON c.id = t.campus_id
           WHERE t.status = 'pending'
           ORDER BY t.id DESC LIMIT 5"""
    ).fetchall()

    db.close()

    return render_template(
        "admin_dashboard.html",
        campuses=campuses,
        today_count=today_count,
        pending_tickets_count=pending_tickets_count,
        recent_logs=recent_logs,
        summary=summary,
        pending_tickets=pending_tickets,
    )


@admin_bp.route("/campus/<int:campus_id>/delete", methods=["POST"])
@admin_required
def delete_campus(campus_id):
    db = get_db()
    # Check if attendance is linked to this campus
    has_attendance = db.execute(
        "SELECT 1 FROM attendance WHERE campus_id = ? LIMIT 1", (campus_id,)
    ).fetchone()

    if has_attendance:
        # Soft delete / deactivate to maintain referential integrity
        db.execute("UPDATE campus SET is_active = 0 WHERE id = ?", (campus_id,))
        flash("Campus pin deactivated (preserved for past attendance audit logs).", "info")
    else:
        db.execute("DELETE FROM campus WHERE id = ?", (campus_id,))
        flash("Campus pin deleted successfully.", "success")

    db.commit()
    db.close()
    return redirect(url_for("admin.admin_dashboard"))


@admin_bp.route("/campus/<int:campus_id>/toggle", methods=["POST"])
@admin_required
def toggle_campus(campus_id):
    db = get_db()
    campus = db.execute("SELECT is_active FROM campus WHERE id = ?", (campus_id,)).fetchone()
    if campus:
        new_state = 0 if campus["is_active"] == 1 else 1
        db.execute("UPDATE campus SET is_active = ? WHERE id = ?", (new_state, campus_id))
        db.commit()
        status_text = "activated" if new_state == 1 else "deactivated"
        flash(f"Campus pin {status_text}.", "success")
    db.close()
    return redirect(url_for("admin.admin_dashboard"))


@admin_bp.route("/export-defaulters", methods=["GET"])
@admin_required
def export_defaulters():
    db = get_db()
    summary = get_system_attendance_summary(db)
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Student ID", "Username", "Email", "Attended Days",
        "Total Institutional Days", "Attendance Rate (%)", "Status"
    ])

    for s in summary["students"]:
        if s["is_defaulter"]:
            writer.writerow([
                s["id"],
                s["username"],
                s["email"],
                s["attended_days"],
                s["total_days"],
                f"{s['percentage']}%",
                "Below 70% Defaulter",
            ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=workbase_defaulters_below_70.csv"},
    )


@admin_bp.route("/export-attendance", methods=["GET"])
@admin_required
def export_attendance():
    db = get_db()
    records = db.execute(
        """SELECT a.id, u.username, u.email, c.name AS campus_name,
                  a.marked_at, a.marked_date, a.distance_meters, a.method,
                  a.latitude, a.longitude
           FROM attendance a
           JOIN users u ON u.id = a.user_id
           LEFT JOIN campus c ON c.id = a.campus_id
           ORDER BY a.id DESC"""
    ).fetchall()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Record ID", "Username", "Email", "Campus", "Timestamp",
        "Date", "Distance (m)", "Verification Method", "Latitude", "Longitude"
    ])

    for r in records:
        writer.writerow([
            r["id"],
            r["username"],
            r["email"],
            r["campus_name"] or "N/A",
            r["marked_at"],
            r["marked_date"],
            f"{r['distance_meters']:.1f}",
            r["method"],
            r["latitude"],
            r["longitude"],
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=workbase_attendance_full_log.csv"},
    )
