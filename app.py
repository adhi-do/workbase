import os
from flask import Flask, render_template, jsonify
from flask_wtf import CSRFProtect
from dotenv import load_dotenv

from database import init_db
from blueprints.auth import auth_bp
from blueprints.attendance import attendance_bp
from blueprints.admin import admin_bp
from blueprints.tickets import tickets_bp
from blueprints.tasks import tasks_bp
from blueprints.resources import resources_bp

load_dotenv()


def create_app(test_config=None):
    app = Flask(__name__)

    # Default security and session configuration
    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        secret_key = os.urandom(32).hex()

    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
    )

    if test_config:
        app.config.update(test_config)

    # Initialize CSRF Protection
    csrf = CSRFProtect()
    csrf.init_app(app)

    # Ensure database schema is ready
    if not app.config.get("TESTING"):
        init_db()

    # Register modular blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(resources_bp)

    # Security Headers
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # Clean error handlers
    @app.errorhandler(403)
    def forbidden_error(e):
        return render_template("base.html", error_title="403 Forbidden", error_message="You do not have permission to access this resource."), 403

    @app.errorhandler(404)
    def not_found_error(e):
        return render_template("base.html", error_title="404 Not Found", error_message="The requested page could not be found."), 404

    @app.errorhandler(500)
    def internal_error(e):
        return render_template("base.html", error_title="500 Server Error", error_message="An internal server error occurred."), 500

    return app


app = create_app()

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)
