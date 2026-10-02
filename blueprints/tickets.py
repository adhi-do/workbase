import sqlite3
from flask import Blueprint, render_template, request, redirect, url_for, session, flash

from database import get_db
from auth import login_required, admin_required

tickets_bp = Blueprint("tickets", __name__)

VALID_ISSUE_TYPES = {
    "gps_failure": "GPS Failure / Permission Denied",
    "inaccurate_location": "Location Inaccuracy / Sensor Drift",
    "device_error": "Device / Browser Incompatibility",
    "other": "Other Operational Issue",
}


@tickets_bp.route("/tickets", methods=["GET", "POST"])
@login_required
def student_tickets():
    db = get_db()

    if request.method == "POST":
        issue_type = request.form.get("issue_type", "").strip()
        description = request.form.get("description", "").strip()
        campus_id_raw = request.form.get("campus_id", "").strip()
        lat_raw = request.form.get("reported_lat", "").strip()
        lon_raw = request.form.get("reported_lon", "").strip()
        acc_raw = request.form.get("reported_accuracy", "").strip()

        if issue_type not in VALID_ISSUE_TYPES:
            flash("Please select a valid issue type.", "error")
            db.close()
            return redirect(url_for("tickets.student_tickets"))

        if len(description) < 5:
            flash("Please provide a detailed explanation of the issue (at least 5 characters).", "error")
            db.close()
            return redirect(url_for("tickets.student_tickets"))

        campus_id = int(campus_id_raw) if campus_id_raw.isdigit() else None
        lat = float(lat_raw) if lat_raw else None
        lon = float(lon_raw) if lon_raw else None
        accuracy = float(acc_raw) if acc_raw else None

        db.execute(
            """INSERT INTO issue_tickets (user_id, campus_id, issue_type, description,
                                          reported_latitude, reported_longitude, reported_accuracy)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session["user_id"], campus_id, issue_type, description, lat, lon, accuracy),
        )
        db.commit()
        db.close()
        flash("Your issue ticket has been submitted. An administrator will review your case.", "success")
        return redirect(url_for("tickets.student_tickets"))

    campuses = db.execute("SELECT * FROM campus WHERE is_active = 1 ORDER BY name ASC").fetchall()
    tickets = db.execute(
        """SELECT t.*, c.name AS campus_name, resolver.username AS resolver_name
           FROM issue_tickets t
           LEFT JOIN campus c ON c.id = t.campus_id
           LEFT JOIN users resolver ON resolver.id = t.resolved_by
           WHERE t.user_id = ?
           ORDER BY t.id DESC""",
        (session["user_id"],),
    ).fetchall()
    db.close()

    return render_template(
        "tickets.html",
        tickets=tickets,
        campuses=campuses,
        valid_issue_types=VALID_ISSUE_TYPES,
    )


@tickets_bp.route("/admin/tickets")
@admin_required
def admin_tickets():
    status_filter = request.args.get("status", "all").strip().lower()
    db = get_db()

    query = """
        SELECT t.*, u.username, u.email, c.name AS campus_name, resolver.username AS resolver_name
        FROM issue_tickets t
        JOIN users u ON u.id = t.user_id
        LEFT JOIN campus c ON c.id = t.campus_id
        LEFT JOIN users resolver ON resolver.id = t.resolved_by
    """
    params = []

    if status_filter in ("pending", "approved", "rejected", "resolved"):
        query += " WHERE t.status = ?"
        params.append(status_filter)

    query += " ORDER BY CASE t.status WHEN 'pending' THEN 1 ELSE 2 END, t.id DESC"

    tickets = db.execute(query, params).fetchall()

    counts = {
        "all": db.execute("SELECT COUNT(*) AS c FROM issue_tickets").fetchone()["c"],
        "pending": db.execute("SELECT COUNT(*) AS c FROM issue_tickets WHERE status = 'pending'").fetchone()["c"],
        "approved": db.execute("SELECT COUNT(*) AS c FROM issue_tickets WHERE status = 'approved'").fetchone()["c"],
        "rejected": db.execute("SELECT COUNT(*) AS c FROM issue_tickets WHERE status = 'rejected'").fetchone()["c"],
    }

    db.close()

    return render_template(
        "admin_tickets.html",
        tickets=tickets,
        status_filter=status_filter,
        counts=counts,
        valid_issue_types=VALID_ISSUE_TYPES,
    )


@tickets_bp.route("/admin/tickets/<int:ticket_id>/action", methods=["POST"])
@admin_required
def update_ticket_status(ticket_id):
    action = request.form.get("action", "").strip().lower()
    admin_notes = request.form.get("admin_notes", "").strip()

    if action not in ("approve", "reject", "resolve"):
        flash("Invalid ticket action.", "error")
        return redirect(url_for("tickets.admin_tickets"))

    db = get_db()
    ticket = db.execute("SELECT * FROM issue_tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket:
        db.close()
        flash("Ticket not found.", "error")
        return redirect(url_for("tickets.admin_tickets"))

    new_status = {
        "approve": "approved",
        "reject": "rejected",
        "resolve": "resolved",
    }[action]

    if action == "approve":
        # Check if student already marked attendance for this ticket's date
        existing_att = db.execute(
            "SELECT 1 FROM attendance WHERE user_id = ? AND marked_date = ?",
            (ticket["user_id"], ticket["ticket_date"]),
        ).fetchone()

        if not existing_att:
            # Determine campus pin: ticket's campus or default active campus
            campus_id = ticket["campus_id"]
            if not campus_id:
                default_campus = db.execute("SELECT id FROM campus WHERE is_active = 1 LIMIT 1").fetchone()
                campus_id = default_campus["id"] if default_campus else None

            if campus_id:
                db.execute(
                    """INSERT INTO attendance (user_id, campus_id, latitude, longitude,
                                               distance_meters, marked_date, method)
                       VALUES (?, ?, ?, ?, ?, ?, 'ticket_approval')""",
                    (
                        ticket["user_id"],
                        campus_id,
                        ticket["reported_latitude"] or 0.0,
                        ticket["reported_longitude"] or 0.0,
                        0.0,
                        ticket["ticket_date"],
                    ),
                )
                flash(f"Ticket #{ticket_id} approved. Attendance credited to student for {ticket['ticket_date']}.", "success")
            else:
                flash(f"Ticket #{ticket_id} approved without attendance credit (no active campus pin exists).", "info")
        else:
            flash(f"Ticket #{ticket_id} approved. Student already has attendance recorded for {ticket['ticket_date']}.", "info")
    elif action == "reject":
        flash(f"Ticket #{ticket_id} rejected.", "info")
    else:
        flash(f"Ticket #{ticket_id} resolved.", "success")

    db.execute(
        """UPDATE issue_tickets
           SET status = ?, admin_notes = ?, resolved_by = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (new_status, admin_notes or None, session["user_id"], ticket_id),
    )
    db.commit()
    db.close()

    return redirect(url_for("tickets.admin_tickets"))
