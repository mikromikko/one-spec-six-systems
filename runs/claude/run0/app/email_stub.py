"""Email notification stub.

Requirement 9: on every invoice state change the service notifies the submitter.
No real SMTP is used; each message is appended as one JSON line to a log file so
it can be inspected (and asserted on in tests).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

DEFAULT_OUTBOX = os.environ.get("EMAIL_OUTBOX", "./emails/outbox.log")


def send_email(to: str, subject: str, body: str, outbox: str | None = None) -> dict:
    path = outbox or os.environ.get("EMAIL_OUTBOX", DEFAULT_OUTBOX)
    message = {
        "to": to,
        "subject": subject,
        "body": body,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(message) + "\n")
    return message
