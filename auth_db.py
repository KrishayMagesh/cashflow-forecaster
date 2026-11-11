"""Login, sessions, CSRF protection and the SQLite connection (shared by every page)."""
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def configure(app, test_config, extra_schema):
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.environ.get("DATABASE_PATH", os.path.join(app.instance_path, "app.db")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    with app.app_context():
        get_db(app).executescript(USERS_SCHEMA + extra_schema)

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        if session.get("user_id") is not None:
            g.user = get_db(app).execute("SELECT id, username FROM users WHERE id = ?",
                                         (session["user_id"],)).fetchone()
        if request.method == "POST":
            sent, expected = request.form.get("csrf_token", ""), session.get("csrf", "")
            if not expected or not secrets.compare_digest(sent, expected):
                abort(400, "Invalid or missing CSRF token.")

    @app.context_processor
    def inject_csrf():
        def csrf_token():
            if "csrf" not in session:
                session["csrf"] = secrets.token_hex(16)
            return session["csrf"]
        return {"csrf_token": csrf_token}

    @app.template_filter("fmt")
    def fmt(value):
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:,.2f}"
        if isinstance(value, int):
            return f"{value:,}"
        return value

    def start_session(user_id):
        session.clear()
        session["user_id"] = user_id
        session["csrf"] = secrets.token_hex(16)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            error = None
            if not USERNAME_RE.match(username):
                error = "Username must be 3-30 letters, numbers or underscores."
            elif len(password) < 8:
                error = "Password must be at least 8 characters."
            if error is None:
                try:
                    db = get_db(app)
                    cur = db.execute("INSERT INTO users (username, password_hash, created_at) "
                                     "VALUES (?, ?, ?)",
                                     (username, generate_password_hash(password), now()))
                    db.commit()
                    start_session(cur.lastrowid)
                    return redirect(url_for("dashboard"))
                except sqlite3.IntegrityError:
                    error = "That username is already taken."
            flash(error, "error")
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = get_db(app).execute("SELECT * FROM users WHERE username = ?",
                                       (request.form.get("username", "").strip(),)).fetchone()
            if user and check_password_hash(user["password_hash"],
                                            request.form.get("password", "")):
                start_session(user["id"])
                return redirect(url_for("dashboard"))
            flash("Incorrect username or password.", "error")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        return redirect(url_for("dashboard" if g.user else "login"))


def get_db(app):
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped
