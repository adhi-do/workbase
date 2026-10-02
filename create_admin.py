"""
Workbase Admin Creation CLI

Run interactively:
    python create_admin.py

Or with arguments for automated setup:
    python create_admin.py --username admin --email admin@workbase.io --password MySecurePassword123

Admin accounts are NOT created through the website's /register form.
This prevents privilege escalation from the browser.
"""
import argparse
import getpass
import re
import sqlite3
import sys

from database import get_db, init_db
from auth import hash_password

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def create_admin_user(username: str, email: str, password: str, db_path=None) -> bool:
    if not USERNAME_RE.match(username):
        print("Error: Username must be 3-30 characters: letters, numbers, underscore only.")
        return False
    if not EMAIL_RE.match(email):
        print("Error: Please provide a valid email address.")
        return False
    if len(password) < 8:
        print("Error: Password must be at least 8 characters long.")
        return False

    init_db(db_path)
    password_hash, salt = hash_password(password)

    db = get_db(db_path)
    try:
        db.execute(
            "INSERT INTO users (username, email, password_hash, salt, role) VALUES (?, ?, ?, ?, 'admin')",
            (username, email, password_hash, salt),
        )
        db.commit()
        print(f"Admin account '{username}' created successfully.")
        return True
    except sqlite3.IntegrityError:
        print(f"Error: Username '{username}' or email '{email}' is already taken.")
        return False
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Create a Workbase admin user account.")
    parser.add_argument("--username", help="Admin username (3-30 chars, alphanumeric and underscore)")
    parser.add_argument("--email", help="Admin email address")
    parser.add_argument("--password", help="Admin password (min 8 chars)")

    args = parser.parse_args()

    if args.username and args.email and args.password:
        success = create_admin_user(args.username.strip(), args.email.strip().lower(), args.password)
        if not success:
            sys.exit(1)
        return

    # Interactive mode
    print("=== Workbase Admin Account Setup ===")
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

    create_admin_user(username, email, password)


if __name__ == "__main__":
    main()
