import io
import os
import sqlite3
import tempfile
import unittest

from app import create_app
from database import get_db, init_db, get_system_attendance_summary, get_student_attendance_stat
from auth import hash_password
from geo import haversine_distance, validate_coordinates, find_nearest_campus


class WorkbaseTestCase(unittest.TestCase):
    def setUp(self):
        # Create a temporary file-based SQLite database for testing
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        init_db(self.db_path)

        # Create test Flask app with CSRF disabled for direct programmatic testing
        self.app = create_app({
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-secret-key-123",
        })
        self.client = self.app.test_client()

        # Monkey-patch DB_PATH in database module during test
        import database
        self.orig_db_path = database.DB_PATH
        database.DB_PATH = self.db_path

        # Seed an admin user
        self.admin_id = self._create_user("admin_user", "admin@workbase.io", "AdminPass123", role="admin")

        # Seed student users
        self.student1_id = self._create_user("alice_student", "alice@workbase.io", "StudentPass123", role="student")
        self.student2_id = self._create_user("bob_student", "bob@workbase.io", "StudentPass123", role="student")

        # Seed campus pins (Main Campus: Lat 12.9716, Lon 77.5946, Radius 150m)
        self.campus1_id = self._create_campus("Main Campus", 12.9716, 77.5946, 150.0)
        # Science Block (1.5 km away: Lat 12.9850, Lon 77.5946, Radius 100m)
        self.campus2_id = self._create_campus("Science Block", 12.9850, 77.5946, 100.0)

    def tearDown(self):
        import database
        database.DB_PATH = self.orig_db_path
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_user(self, username, email, password, role="student"):
        pwd_hash, salt = hash_password(password)
        db = get_db(self.db_path)
        cur = db.execute(
            "INSERT INTO users (username, email, password_hash, salt, role) VALUES (?, ?, ?, ?, ?)",
            (username, email, pwd_hash, salt, role),
        )
        db.commit()
        user_id = cur.lastrowid
        db.close()
        return user_id

    def _create_campus(self, name, lat, lon, radius, created_by=1):
        db = get_db(self.db_path)
        cur = db.execute(
            "INSERT INTO campus (name, latitude, longitude, radius_meters, is_active, created_by) VALUES (?, ?, ?, ?, 1, ?)",
            (name, lat, lon, radius, created_by),
        )
        db.commit()
        campus_id = cur.lastrowid
        db.close()
        return campus_id

    def login(self, username, password):
        return self.client.post("/login", data={"username": username, "password": password}, follow_redirects=True)

    def logout(self):
        return self.client.get("/logout", follow_redirects=True)

    # =========================================================================
    # 1. Authentication Tests
    # =========================================================================
    def test_registration_success(self):
        res = self.client.post("/register", data={
            "username": "charlie_new",
            "email": "charlie@workbase.io",
            "password": "ValidPassword123"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Account created successfully", res.data)

        # Ensure created account has role 'student' (no privilege escalation)
        db = get_db(self.db_path)
        user = db.execute("SELECT role FROM users WHERE username = 'charlie_new'").fetchone()
        db.close()
        self.assertEqual(user["role"], "student")

    def test_registration_validation_rules(self):
        # Short password
        res = self.client.post("/register", data={
            "username": "short_pwd_user",
            "email": "short@workbase.io",
            "password": "short"
        }, follow_redirects=True)
        self.assertIn(b"Password must be at least 8 characters long", res.data)

        # Invalid username characters
        res = self.client.post("/register", data={
            "username": "bad user!",
            "email": "bad@workbase.io",
            "password": "ValidPassword123"
        }, follow_redirects=True)
        self.assertIn(b"Username must be 3-30 characters", res.data)

        # Duplicate username
        res = self.client.post("/register", data={
            "username": "alice_student",
            "email": "alice_new@workbase.io",
            "password": "ValidPassword123"
        }, follow_redirects=True)
        self.assertIn(b"That username or email is already registered", res.data)

    def test_login_and_logout(self):
        # Correct credentials
        res = self.login("alice_student", "StudentPass123")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Student Operations Portal", res.data)

        # Logout
        res = self.logout()
        self.assertIn(b"You have been logged out", res.data)

        # Incorrect password
        res = self.login("alice_student", "WrongPassword")
        self.assertIn(b"Invalid username or password", res.data)

    # =========================================================================
    # 2. Access Control & RBAC
    # =========================================================================
    def test_unauthenticated_redirections(self):
        # Accessing dashboard without login redirects to /login
        res = self.client.get("/dashboard")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.headers["Location"])

        # Accessing admin console without login redirects to /login
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.headers["Location"])

    def test_student_forbidden_from_admin_console(self):
        self.login("alice_student", "StudentPass123")
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 403)
        self.assertIn(b"403 Forbidden", res.data)

    def test_admin_access_allowed(self):
        self.login("admin_user", "AdminPass123")
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Executive Operations Console", res.data)

    # =========================================================================
    # 3. GPS Attendance Verification & Anti-Proxy Bounds
    # =========================================================================
    def test_attendance_inside_radius_succeeds(self):
        self.login("alice_student", "StudentPass123")
        # Exact Main Campus coordinates: (12.9716, 77.5946)
        res = self.client.post("/mark-attendance", json={
            "lat": 12.9716,
            "lon": 77.5946,
            "accuracy": 10
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("Main Campus", data["campus"])

    def test_attendance_outside_radius_rejected(self):
        self.login("bob_student", "StudentPass123")
        # 10 km away from any campus
        res = self.client.post("/mark-attendance", json={
            "lat": 13.0800,
            "lon": 77.6500,
            "accuracy": 15
        })
        self.assertEqual(res.status_code, 403)
        data = res.get_json()
        self.assertEqual(data["status"], "error")
        self.assertTrue(data.get("can_ticket"))
        self.assertIn("away from", data["message"])

    def test_duplicate_attendance_same_day_blocked(self):
        self.login("alice_student", "StudentPass123")
        # First check-in
        res1 = self.client.post("/mark-attendance", json={
            "lat": 12.9716,
            "lon": 77.5946,
            "accuracy": 10
        })
        self.assertEqual(res1.status_code, 200)

        # Second check-in on the same calendar date
        res2 = self.client.post("/mark-attendance", json={
            "lat": 12.9716,
            "lon": 77.5946,
            "accuracy": 10
        })
        self.assertEqual(res2.status_code, 409)
        self.assertIn("already marked attendance today", res2.get_json()["message"])

    def test_nearest_campus_selection_with_multiple_pins(self):
        self.login("bob_student", "StudentPass123")
        # Stand at Science Block: (12.9850, 77.5946)
        res = self.client.post("/mark-attendance", json={
            "lat": 12.9850,
            "lon": 77.5946,
            "accuracy": 5
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["campus"], "Science Block")

    def test_untrusted_broad_accuracy_rejected(self):
        self.login("bob_student", "StudentPass123")
        # Student is outside radius (e.g. 200m away, radius is 100m) and reports huge accuracy (±400m)
        res = self.client.post("/mark-attendance", json={
            "lat": 12.9870,
            "lon": 77.5946,
            "accuracy": 400
        })
        self.assertEqual(res.status_code, 403)
        self.assertIn("accuracy is too low", res.get_json()["message"])

    def test_invalid_coordinates_handled(self):
        self.login("bob_student", "StudentPass123")
        res = self.client.post("/mark-attendance", json={
            "lat": 150.0,  # Invalid latitude (>90)
            "lon": 77.5946,
            "accuracy": 10
        })
        self.assertEqual(res.status_code, 400)

    # =========================================================================
    # 4. Defaulters (< 70%) & Attendance Compliance Analytics
    # =========================================================================
    def test_defaulter_calculation_and_filtering(self):
        db = get_db(self.db_path)
        # Create 10 distinct days of institutional attendance
        dates = [f"2026-09-{day:02d}" for day in range(1, 11)]

        charlie_id = self._create_user("charlie_student", "charlie@workbase.io", "StudentPass123", role="student")

        # Charlie attended all 10 days = 100.0% (Establishes 10 system days)
        for d in dates:
            db.execute(
                "INSERT INTO attendance (user_id, campus_id, latitude, longitude, distance_meters, marked_date, method) VALUES (?, ?, ?, ?, ?, ?, 'gps')",
                (charlie_id, self.campus1_id, 12.9716, 77.5946, 10.0, d)
            )

        # Alice attended 8 out of 10 days = 80.0% (Compliant)
        for d in dates[:8]:
            db.execute(
                "INSERT INTO attendance (user_id, campus_id, latitude, longitude, distance_meters, marked_date, method) VALUES (?, ?, ?, ?, ?, ?, 'gps')",
                (self.student1_id, self.campus1_id, 12.9716, 77.5946, 12.0, d)
            )

        # Bob attended 4 out of 10 days = 40.0% (Defaulter < 70%)
        for d in dates[:4]:
            db.execute(
                "INSERT INTO attendance (user_id, campus_id, latitude, longitude, distance_meters, marked_date, method) VALUES (?, ?, ?, ?, ?, ?, 'gps')",
                (self.student2_id, self.campus1_id, 12.9716, 77.5946, 15.0, d)
            )
        db.commit()

        summary = get_system_attendance_summary(db)
        db.close()

        self.assertEqual(summary["total_system_days"], 10)
        self.assertEqual(summary["defaulters_count"], 1)
        self.assertEqual(summary["good_standing_count"], 2)

        # Check Alice
        alice_stat = next(s for s in summary["students"] if s["username"] == "alice_student")
        self.assertEqual(alice_stat["attended_days"], 8)
        self.assertEqual(alice_stat["percentage"], 80.0)
        self.assertFalse(alice_stat["is_defaulter"])

        # Check Bob
        bob_stat = next(s for s in summary["students"] if s["username"] == "bob_student")
        self.assertEqual(bob_stat["attended_days"], 4)
        self.assertEqual(bob_stat["percentage"], 40.0)
        self.assertTrue(bob_stat["is_defaulter"])

    def test_export_defaulters_csv(self):
        # Seed defaulter record
        db = get_db(self.db_path)
        db.execute(
            "INSERT INTO attendance (user_id, campus_id, latitude, longitude, distance_meters, marked_date, method) VALUES (?, ?, ?, ?, ?, '2026-09-01', 'gps')",
            (self.student1_id, self.campus1_id, 12.9716, 77.5946, 10.0)
        )
        db.commit()
        db.close()

        self.login("admin_user", "AdminPass123")
        res = self.client.get("/admin/export-defaulters")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "text/csv")
        self.assertIn("attachment; filename=workbase_defaulters_below_70.csv", res.headers["Content-Disposition"])
        # bob_student attended 0 of 1 day -> 0% -> in CSV
        self.assertIn(b"bob_student", res.data)

    # =========================================================================
    # 5. Issue Ticketing & Error Reporting
    # =========================================================================
    def test_issue_ticketing_and_admin_approval_credits_attendance(self):
        # Alice files a dispute ticket for GPS failure in Room 302
        self.login("alice_student", "StudentPass123")
        res = self.client.post("/tickets", data={
            "issue_type": "gps_failure",
            "campus_id": str(self.campus1_id),
            "description": "Heavy indoor attenuation inside basement lab 101. Could not acquire fix.",
            "reported_lat": "12.9716",
            "reported_lon": "77.5946",
            "reported_accuracy": "80"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Your issue ticket has been submitted", res.data)

        # Verify ticket in database
        db = get_db(self.db_path)
        ticket = db.execute("SELECT * FROM issue_tickets WHERE user_id = ?", (self.student1_id,)).fetchone()
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["status"], "pending")
        ticket_id = ticket["id"]
        ticket_date = ticket["ticket_date"]
        db.close()

        # Admin audits and approves ticket
        self.logout()
        self.login("admin_user", "AdminPass123")
        res = self.client.post(f"/admin/tickets/{ticket_id}/action", data={
            "action": "approve",
            "admin_notes": "Verified presence via faculty confirmation."
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Check that attendance was credited with method 'ticket_approval'
        db = get_db(self.db_path)
        att = db.execute(
            "SELECT * FROM attendance WHERE user_id = ? AND marked_date = ?",
            (self.student1_id, ticket_date)
        ).fetchone()
        self.assertIsNotNone(att)
        self.assertEqual(att["method"], "ticket_approval")

        # Check ticket status updated to approved
        updated_ticket = db.execute("SELECT * FROM issue_tickets WHERE id = ?", (ticket_id,)).fetchone()
        self.assertEqual(updated_ticket["status"], "approved")
        self.assertEqual(updated_ticket["admin_notes"], "Verified presence via faculty confirmation.")
        db.close()

    # =========================================================================
    # 6. Extensible Foundation: Tasks and Resources
    # =========================================================================
    def test_task_management_workflow(self):
        # Admin creates task assigned to Alice
        self.login("admin_user", "AdminPass123")
        res = self.client.post("/tasks/create", data={
            "title": "Inventory Physics Lab Hardware",
            "description": "Audit sensors in Room 402.",
            "assigned_to": str(self.student1_id),
            "priority": "high",
            "due_date": "2026-10-15"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Inventory Physics Lab Hardware", res.data)

        db = get_db(self.db_path)
        task = db.execute("SELECT * FROM tasks WHERE assigned_to = ?", (self.student1_id,)).fetchone()
        self.assertIsNotNone(task)
        task_id = task["id"]
        self.assertEqual(task["status"], "todo")
        db.close()

        # Alice logs in and updates status to 'in_progress' and 'completed'
        self.logout()
        self.login("alice_student", "StudentPass123")
        res = self.client.post(f"/tasks/{task_id}/status", data={"status": "in_progress"}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        db = get_db(self.db_path)
        updated_task = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        self.assertEqual(updated_task["status"], "in_progress")
        db.close()

    def test_resource_tracking_workflow(self):
        # Admin adds resource
        self.login("admin_user", "AdminPass123")
        res = self.client.post("/resources/create", data={
            "name": "Lecture Theater 101",
            "category": "room",
            "campus_id": str(self.campus1_id),
            "notes": "120 seats with projection system"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        db = get_db(self.db_path)
        res_item = db.execute("SELECT * FROM resources WHERE name = 'Lecture Theater 101'").fetchone()
        self.assertIsNotNone(res_item)
        resource_id = res_item["id"]
        db.close()

        # Alice allocates resource
        self.logout()
        self.login("alice_student", "StudentPass123")
        res = self.client.post("/resources/allocate", data={
            "resource_id": str(resource_id),
            "start_time": "2026-10-05T09:00",
            "end_time": "2026-10-05T11:00",
            "purpose": "Academic debate practice"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Resource status should now be 'in_use'
        db = get_db(self.db_path)
        alloc = db.execute("SELECT * FROM resource_allocations WHERE resource_id = ?", (resource_id,)).fetchone()
        self.assertIsNotNone(alloc)
        self.assertEqual(alloc["status"], "active")
        alloc_id = alloc["id"]

        r_check = db.execute("SELECT status FROM resources WHERE id = ?", (resource_id,)).fetchone()
        self.assertEqual(r_check["status"], "in_use")
        db.close()

        # Alice returns resource
        res = self.client.post(f"/resources/allocations/{alloc_id}/return", follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        db = get_db(self.db_path)
        r_check2 = db.execute("SELECT status FROM resources WHERE id = ?", (resource_id,)).fetchone()
        self.assertEqual(r_check2["status"], "available")
        db.close()

    def test_create_admin_helper(self):
        from create_admin import create_admin_user
        success = create_admin_user("super_ops_admin", "superops@workbase.io", "SuperSecurePassword123", db_path=self.db_path)
        self.assertTrue(success)

        db = get_db(self.db_path)
        user = db.execute("SELECT * FROM users WHERE username = 'super_ops_admin'").fetchone()
        db.close()
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "admin")

    def test_campus_pin_toggle_and_soft_delete(self):
        self.login("admin_user", "AdminPass123")

        # Toggle Science Block from active to inactive
        res = self.client.post(f"/admin/campus/{self.campus2_id}/toggle", follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        db = get_db(self.db_path)
        c = db.execute("SELECT is_active FROM campus WHERE id = ?", (self.campus2_id,)).fetchone()
        self.assertEqual(c["is_active"], 0)

        # Deleting a campus that has no attendance records hard-deletes it
        res_del = self.client.post(f"/admin/campus/{self.campus2_id}/delete", follow_redirects=True)
        self.assertEqual(res_del.status_code, 200)
        c_del = db.execute("SELECT * FROM campus WHERE id = ?", (self.campus2_id,)).fetchone()
        self.assertIsNone(c_del)

        # Now link attendance to Main Campus
        db.execute(
            "INSERT INTO attendance (user_id, campus_id, latitude, longitude, distance_meters, marked_date, method) VALUES (?, ?, ?, ?, ?, '2026-09-20', 'gps')",
            (self.student1_id, self.campus1_id, 12.9716, 77.5946, 5.0)
        )
        db.commit()

        # Deleting a campus with attendance records soft-deletes (is_active = 0) to maintain audit logs
        res_del2 = self.client.post(f"/admin/campus/{self.campus1_id}/delete", follow_redirects=True)
        self.assertEqual(res_del2.status_code, 200)
        c_soft = db.execute("SELECT is_active FROM campus WHERE id = ?", (self.campus1_id,)).fetchone()
        self.assertIsNotNone(c_soft)
        self.assertEqual(c_soft["is_active"], 0)
        db.close()

    def test_task_authorization_prevent_unauthorized_student_edit(self):
        # Admin creates task assigned to Alice
        self.login("admin_user", "AdminPass123")
        self.client.post("/tasks/create", data={
            "title": "Private Alice Duty",
            "assigned_to": str(self.student1_id),
            "priority": "low"
        }, follow_redirects=True)

        db = get_db(self.db_path)
        task = db.execute("SELECT id FROM tasks WHERE title = 'Private Alice Duty'").fetchone()
        task_id = task["id"]
        db.close()

        # Bob attempts to update Alice's task status -> 403 Forbidden
        self.logout()
        self.login("bob_student", "StudentPass123")
        res = self.client.post(f"/tasks/{task_id}/status", data={"status": "completed"})
        self.assertEqual(res.status_code, 403)

    def test_resource_conflict_prevention(self):
        # Alice allocates resource
        self.login("alice_student", "StudentPass123")
        res_asset = self._create_campus_asset("Microscope Unit A", "equipment")
        self.client.post("/resources/allocate", data={
            "resource_id": str(res_asset),
            "start_time": "2026-10-06T10:00",
            "end_time": "2026-10-06T12:00",
            "purpose": "Cell observation"
        }, follow_redirects=True)

        # Bob tries to allocate the same resource while it is already in use
        self.logout()
        self.login("bob_student", "StudentPass123")
        res2 = self.client.post("/resources/allocate", data={
            "resource_id": str(res_asset),
            "start_time": "2026-10-06T13:00",
            "end_time": "2026-10-06T14:00",
            "purpose": "Conflicting booking"
        }, follow_redirects=True)
        self.assertIn(b"currently in_use", res2.data)

    def _create_campus_asset(self, name, category):
        db = get_db(self.db_path)
        cur = db.execute(
            "INSERT INTO resources (name, category, campus_id, status) VALUES (?, ?, ?, 'available')",
            (name, category, self.campus1_id)
        )
        db.commit()
        asset_id = cur.lastrowid
        db.close()
        return asset_id


if __name__ == "__main__":
    unittest.main()

