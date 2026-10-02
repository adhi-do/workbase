import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "attendance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'student')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS campus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    radius_meters REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    campus_id INTEGER NOT NULL,
    marked_at TEXT NOT NULL DEFAULT (datetime('now')),
    marked_date TEXT NOT NULL DEFAULT (date('now')),
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    distance_meters REAL NOT NULL,
    method TEXT NOT NULL DEFAULT 'gps' CHECK(method IN ('gps', 'ticket_approval')),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (campus_id) REFERENCES campus(id),
    UNIQUE(user_id, marked_date)
);

CREATE TABLE IF NOT EXISTS issue_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    campus_id INTEGER,
    issue_type TEXT NOT NULL CHECK(issue_type IN ('gps_failure', 'inaccurate_location', 'device_error', 'other')),
    description TEXT NOT NULL,
    reported_latitude REAL,
    reported_longitude REAL,
    reported_accuracy REAL,
    ticket_date TEXT NOT NULL DEFAULT (date('now')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected', 'resolved')),
    admin_notes TEXT,
    resolved_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (campus_id) REFERENCES campus(id),
    FOREIGN KEY (resolved_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    assigned_to INTEGER,
    created_by INTEGER NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('low', 'medium', 'high', 'urgent')),
    status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo', 'in_progress', 'completed', 'cancelled')),
    due_date TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (assigned_to) REFERENCES users(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('room', 'lab', 'equipment', 'facility', 'vehicle', 'other')),
    campus_id INTEGER,
    status TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available', 'in_use', 'maintenance', 'reserved')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (campus_id) REFERENCES campus(id)
);

CREATE TABLE IF NOT EXISTS resource_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'returned', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (resource_id) REFERENCES resources(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_attendance_user_date ON attendance(user_id, marked_date);
CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(marked_date);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON issue_tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON issue_tickets(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_resources_campus ON resources(campus_id);
"""


def get_db(db_path=None):
    target = db_path if db_path is not None else DB_PATH
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None):
    conn = get_db(db_path)
    conn.executescript(SCHEMA)

    # Lightweight migration safety for existing databases
    cursor = conn.cursor()
    try:
        campus_cols = [c[1] for c in cursor.execute("PRAGMA table_info(campus)").fetchall()]
        if "is_active" not in campus_cols:
            cursor.execute("ALTER TABLE campus ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

        att_cols = [c[1] for c in cursor.execute("PRAGMA table_info(attendance)").fetchall()]
        if "method" not in att_cols:
            cursor.execute("ALTER TABLE attendance ADD COLUMN method TEXT NOT NULL DEFAULT 'gps'")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


def get_system_attendance_summary(db):
    """
    Computes attendance stats for all registered students:
    - total institutional active days (distinct dates attendance was recorded in the system, min 1 if any exists)
    - attended days per student
    - attendance percentage
    - is_defaulter (< 70%)
    """
    row = db.execute("SELECT COUNT(DISTINCT marked_date) AS total_days FROM attendance").fetchone()
    total_system_days = row["total_days"] if row and row["total_days"] is not None else 0

    students = db.execute("""
        SELECT u.id, u.username, u.email, u.created_at,
               COUNT(DISTINCT a.marked_date) AS attended_days,
               MAX(a.marked_at) AS last_check_in
        FROM users u
        LEFT JOIN attendance a ON u.id = a.user_id
        WHERE u.role = 'student'
        GROUP BY u.id
        ORDER BY u.username ASC
    """).fetchall()

    student_stats = []
    defaulters_count = 0

    for s in students:
        attended = s["attended_days"]
        if total_system_days == 0:
            percentage = 100.0
            is_defaulter = False
        else:
            percentage = round((attended / total_system_days) * 100, 1)
            is_defaulter = percentage < 70.0

        if is_defaulter:
            defaulters_count += 1

        student_stats.append({
            "id": s["id"],
            "username": s["username"],
            "email": s["email"],
            "created_at": s["created_at"],
            "attended_days": attended,
            "total_days": total_system_days,
            "percentage": percentage,
            "is_defaulter": is_defaulter,
            "last_check_in": s["last_check_in"],
        })

    return {
        "total_students": len(students),
        "total_system_days": total_system_days,
        "defaulters_count": defaulters_count,
        "good_standing_count": len(students) - defaulters_count,
        "students": student_stats,
    }


def get_student_attendance_stat(db, user_id):
    """
    Computes attendance stats for a specific student.
    """
    row = db.execute("SELECT COUNT(DISTINCT marked_date) AS total_days FROM attendance").fetchone()
    total_system_days = row["total_days"] if row and row["total_days"] is not None else 0

    row_attended = db.execute(
        "SELECT COUNT(DISTINCT marked_date) AS attended_days FROM attendance WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    attended = row_attended["attended_days"] if row_attended and row_attended["attended_days"] is not None else 0

    if total_system_days == 0:
        percentage = 100.0
        is_defaulter = False
    else:
        percentage = round((attended / total_system_days) * 100, 1)
        is_defaulter = percentage < 70.0

    return {
        "attended_days": attended,
        "total_days": total_system_days,
        "percentage": percentage,
        "is_defaulter": is_defaulter,
    }
