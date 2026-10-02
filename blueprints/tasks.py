from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort

from database import get_db
from auth import login_required, admin_required

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")

VALID_PRIORITIES = ("low", "medium", "high", "urgent")
VALID_STATUSES = ("todo", "in_progress", "completed", "cancelled")


@tasks_bp.route("")
@tasks_bp.route("/")
@login_required
def index():
    db = get_db()
    is_admin = session.get("role") == "admin"

    if is_admin:
        tasks = db.execute(
            """SELECT t.*, u.username AS assignee_name, creator.username AS creator_name
               FROM tasks t
               LEFT JOIN users u ON u.id = t.assigned_to
               LEFT JOIN users creator ON creator.id = t.created_by
               ORDER BY CASE t.status WHEN 'todo' THEN 1 WHEN 'in_progress' THEN 2 ELSE 3 END, t.id DESC"""
        ).fetchall()
        students = db.execute(
            "SELECT id, username, email FROM users WHERE role = 'student' ORDER BY username ASC"
        ).fetchall()
    else:
        tasks = db.execute(
            """SELECT t.*, creator.username AS creator_name
               FROM tasks t
               LEFT JOIN users creator ON creator.id = t.created_by
               WHERE t.assigned_to = ?
               ORDER BY CASE t.status WHEN 'todo' THEN 1 WHEN 'in_progress' THEN 2 ELSE 3 END, t.id DESC""",
            (session["user_id"],),
        ).fetchall()
        students = []

    db.close()
    return render_template("tasks.html", tasks=tasks, students=students, is_admin=is_admin)


@tasks_bp.route("/create", methods=["POST"])
@admin_required
def create_task():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    priority = request.form.get("priority", "medium").strip().lower()
    assigned_to_raw = request.form.get("assigned_to", "").strip()
    due_date = request.form.get("due_date", "").strip() or None

    if not title:
        flash("Task title is required.", "error")
        return redirect(url_for("tasks.index"))

    if priority not in VALID_PRIORITIES:
        priority = "medium"

    assigned_to = int(assigned_to_raw) if assigned_to_raw.isdigit() else None

    db = get_db()
    db.execute(
        """INSERT INTO tasks (title, description, assigned_to, created_by, priority, due_date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (title, description or None, assigned_to, session["user_id"], priority, due_date),
    )
    db.commit()
    db.close()

    flash(f"Task '{title}' created successfully.", "success")
    return redirect(url_for("tasks.index"))


@tasks_bp.route("/<int:task_id>/status", methods=["POST"])
@login_required
def update_task_status(task_id):
    new_status = request.form.get("status", "").strip().lower()
    if new_status not in VALID_STATUSES:
        flash("Invalid status specified.", "error")
        return redirect(url_for("tasks.index"))

    db = get_db()
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        db.close()
        flash("Task not found.", "error")
        return redirect(url_for("tasks.index"))

    # Permission check: admin or assigned student
    is_admin = session.get("role") == "admin"
    if not is_admin and task["assigned_to"] != session.get("user_id"):
        db.close()
        abort(403)

    db.execute(
        "UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (new_status, task_id),
    )
    db.commit()
    db.close()

    flash(f"Task status updated to '{new_status}'.", "success")
    return redirect(url_for("tasks.index"))
