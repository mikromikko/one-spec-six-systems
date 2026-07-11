"""Authentication: login/logout, session handling and access decorators."""
import functools
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request, session
from werkzeug.security import check_password_hash

from .db import get_db

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def now_iso():
    """Current UTC time as an ISO-8601 string (used for all timestamps)."""
    return datetime.now(timezone.utc).isoformat()


def authenticate(email, password):
    """Return the user row for valid credentials, otherwise None."""
    email = (email or "").strip().lower()
    user = get_db().execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    if user is None or not check_password_hash(user["password_hash"], password or ""):
        return None
    return user


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        g.user = get_db().execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


@bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or request.form
    user = authenticate(data.get("email"), data.get("password"))
    if user is None:
        return jsonify({"error": "invalid credentials"}), 401
    session.clear()
    session["user_id"] = user["id"]
    return jsonify({"id": user["id"], "email": user["email"], "role": user["role"]})


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.route("/me")
def me():
    if g.user is None:
        return jsonify({"error": "not authenticated"}), 401
    return jsonify(
        {"id": g.user["id"], "email": g.user["email"], "role": g.user["role"]}
    )


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return jsonify({"error": "authentication required"}), 401
        return view(**kwargs)

    return wrapped


def role_required(role):
    def decorator(view):
        @functools.wraps(view)
        @login_required
        def wrapped(**kwargs):
            if g.user["role"] != role:
                return jsonify({"error": "forbidden for this role"}), 403
            return view(**kwargs)

        return wrapped

    return decorator
