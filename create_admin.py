"""
Run this once from the command line to create an admin account:

    python create_admin.py

Admin accounts are NOT created through the website's /register form.
This keeps privilege escalation impossible from the browser.
"""
import getpass
import re
import sqlite3

from database import get_db, init_db
from auth import hash_password

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def main():
    init_db()

    username = input("Admin username: ").strip()
    while not USERNAME_RE.match(username):
        print("Username must be 3-30 characters: letters, numbers, underscore only.")
        username = input("Admin username: ").strip()

    email = input("Admin email: ").strip().lower()
    while not EMAIL_RE.match(email):
        print("Please enter a valid email address.")
        email = input("Admin email: ").strip().lower()

    password = getpass.getpass("Admin password (min 8 chars): ")
    while len(password) < 8:
        print("Password must be at least 8 characters.")
        password = getpass.getpass("Admin password (min 8 chars): ")

    password_hash, salt = hash_password(password)

    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (username, email, password_hash, salt, role) VALUES (?, ?, ?, ?, 'admin')",
            (username, email, password_hash, salt),
        )
        db.commit()
        print(f"Admin account '{username}' created successfully.")
    except sqlite3.IntegrityError:
        print("That username or email is already taken.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
