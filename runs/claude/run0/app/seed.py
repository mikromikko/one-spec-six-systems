"""Seed / user-provisioning CLI.

Usage:
    python -m app.seed create-user EMAIL PASSWORD ROLE   # ROLE = submitter|approver
    python -m app.seed demo                              # create a demo user set

Runs against DATABASE_URL (default sqlite:///./invoices.db).
"""
from __future__ import annotations

import sys

from app.db import SessionLocal, init_db
from app.models import Role
from app import services

DEMO_USERS = [
    ("submitter@nordvik.example", "submitter-pass", Role.submitter),
    ("approver1@nordvik.example", "approver-pass", Role.approver),
    ("approver2@nordvik.example", "approver-pass", Role.approver),
]


def _create(email: str, password: str, role: Role) -> None:
    session = SessionLocal()
    try:
        if services.get_user_by_email(session, email):
            print(f"exists: {email}")
            return
        services.create_user(session, email=email, password=password, role=role)
        session.commit()
        print(f"created: {email} ({role.value})")
    finally:
        session.close()


def main(argv: list[str]) -> int:
    init_db()
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "create-user" and len(argv) == 4:
        _create(argv[1], argv[2], Role(argv[3]))
        return 0
    if cmd == "demo":
        for email, pw, role in DEMO_USERS:
            _create(email, pw, role)
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
