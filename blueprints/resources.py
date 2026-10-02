from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort

from database import get_db
from auth import login_required, admin_required

resources_bp = Blueprint("resources", __name__, url_prefix="/resources")

VALID_CATEGORIES = ("room", "lab", "equipment", "facility", "vehicle", "other")
VALID_STATUSES = ("available", "in_use", "maintenance", "reserved")


@resources_bp.route("")
@resources_bp.route("/")
@login_required
def index():
    db = get_db()
    is_admin = session.get("role") == "admin"

    resources = db.execute(
        """SELECT r.*, c.name AS campus_name
           FROM resources r
           LEFT JOIN campus c ON c.id = r.campus_id
           ORDER BY r.id DESC"""
    ).fetchall()

    campuses = db.execute("SELECT * FROM campus WHERE is_active = 1 ORDER BY name ASC").fetchall()

    allocations = db.execute(
        """SELECT a.*, r.name AS resource_name, r.category, u.username
           FROM resource_allocations a
           JOIN resources r ON r.id = a.resource_id
           JOIN users u ON u.id = a.user_id
           ORDER BY CASE a.status WHEN 'active' THEN 1 ELSE 2 END, a.id DESC
           LIMIT 30"""
    ).fetchall()

    db.close()
    return render_template(
        "resources.html",
        resources=resources,
        campuses=campuses,
        allocations=allocations,
        is_admin=is_admin,
        categories=VALID_CATEGORIES,
    )


@resources_bp.route("/create", methods=["POST"])
@admin_required
def create_resource():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "room").strip().lower()
    campus_id_raw = request.form.get("campus_id", "").strip()
    notes = request.form.get("notes", "").strip()

    if not name:
        flash("Resource name is required.", "error")
        return redirect(url_for("resources.index"))

    if category not in VALID_CATEGORIES:
        category = "other"

    campus_id = int(campus_id_raw) if campus_id_raw.isdigit() else None

    db = get_db()
    db.execute(
        """INSERT INTO resources (name, category, campus_id, status, notes)
           VALUES (?, ?, ?, 'available', ?)""",
        (name, category, campus_id, notes or None),
    )
    db.commit()
    db.close()

    flash(f"Resource '{name}' registered successfully.", "success")
    return redirect(url_for("resources.index"))


@resources_bp.route("/allocate", methods=["POST"])
@login_required
def allocate_resource():
    resource_id_raw = request.form.get("resource_id", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    purpose = request.form.get("purpose", "").strip()

    if not (resource_id_raw.isdigit() and start_time and end_time and purpose):
        flash("Please complete all required fields for resource booking.", "error")
        return redirect(url_for("resources.index"))

    resource_id = int(resource_id_raw)

    db = get_db()
    resource = db.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
    if not resource:
        db.close()
        flash("Resource not found.", "error")
        return redirect(url_for("resources.index"))

    if resource["status"] != "available":
        db.close()
        flash(f"Resource '{resource['name']}' is currently {resource['status']}.", "error")
        return redirect(url_for("resources.index"))

    db.execute(
        """INSERT INTO resource_allocations (resource_id, user_id, start_time, end_time, purpose, status)
           VALUES (?, ?, ?, ?, ?, 'active')""",
        (resource_id, session["user_id"], start_time, end_time, purpose),
    )
    db.execute("UPDATE resources SET status = 'in_use' WHERE id = ?", (resource_id,))
    db.commit()
    db.close()

    flash(f"Resource '{resource['name']}' booked successfully.", "success")
    return redirect(url_for("resources.index"))


@resources_bp.route("/allocations/<int:allocation_id>/return", methods=["POST"])
@login_required
def return_resource(allocation_id):
    db = get_db()
    allocation = db.execute(
        "SELECT * FROM resource_allocations WHERE id = ?", (allocation_id,)
    ).fetchone()

    if not allocation:
        db.close()
        flash("Allocation not found.", "error")
        return redirect(url_for("resources.index"))

    is_admin = session.get("role") == "admin"
    if not is_admin and allocation["user_id"] != session.get("user_id"):
        db.close()
        abort(403)

    db.execute("UPDATE resource_allocations SET status = 'returned' WHERE id = ?", (allocation_id,))
    db.execute("UPDATE resources SET status = 'available' WHERE id = ?", (allocation["resource_id"],))
    db.commit()
    db.close()

    flash("Resource returned successfully.", "success")
    return redirect(url_for("resources.index"))
