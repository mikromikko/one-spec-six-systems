"""CLI commands for database setup and user management."""
import click
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash

from .auth import now_iso
from .db import get_db, init_db

_DEMO_USERS = [
    ("submitter@nordvik.test", "submitter"),
    ("submitter2@nordvik.test", "submitter"),
    ("approver@nordvik.test", "approver"),
    ("approver2@nordvik.test", "approver"),
]


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create the database tables (safe to run repeatedly)."""
    init_db()
    click.echo("Database initialised.")


@click.command("create-user")
@click.argument("email")
@click.argument("role", type=click.Choice(["submitter", "approver"]))
@click.option("--password", prompt=True, hide_input=True,
              confirmation_prompt=True)
@with_appcontext
def create_user_command(email, role, password):
    """Create a user with the given EMAIL and ROLE."""
    db = get_db()
    db.execute(
        "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        (email.strip().lower(), generate_password_hash(password), role, now_iso()),
    )
    db.commit()
    click.echo(f"Created {role}: {email}")


@click.command("seed-demo")
@with_appcontext
def seed_demo_command():
    """Create a set of demo users (password: password123)."""
    db = get_db()
    created = 0
    for email, role in _DEMO_USERS:
        exists = db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            continue
        db.execute(
            "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (email, generate_password_hash("password123"), role, now_iso()),
        )
        created += 1
    db.commit()
    click.echo(f"Seeded {created} demo user(s). Password for all: password123")
    for email, role in _DEMO_USERS:
        click.echo(f"  {role:9s} {email}")


def init_app(app):
    app.cli.add_command(init_db_command)
    app.cli.add_command(create_user_command)
    app.cli.add_command(seed_demo_command)
