"""Acceptance checks for copilot/run3 — stdlib wsgiref :8000, JSON+base64 PDF, /approvals/pending, GET /invoices/{id}/pdf."""
import base64
import datetime
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import curl, Report, PDF, configure, save_unit_output

BASE, RUN = configure("copilot-run3", "http://127.0.0.1:8000")
DUE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
PDF64 = base64.b64encode(PDF.read_bytes()).decode()
r = Report("copilot-run3")


def mkuser(email, role):
    return curl("POST", f"{BASE}/users", jsonbody={"email": email, "password": "secret123", "role": role})


def login(email):
    s, b = curl("POST", f"{BASE}/auth/login", jsonbody={"email": email, "password": "secret123"})
    return b.get("token") if isinstance(b, dict) else None


def mk_invoice(token, amount, name="Valmet Steel", email="laskutus@valmetsteel.example"):
    s, b = curl("POST", f"{BASE}/invoices", token=token, jsonbody={
        "amount_euros": str(amount), "counterparty_name": name,
        "counterparty_contact_email": email, "due_date": DUE,
        "pdf_filename": "test-invoice.pdf", "pdf_base64": PDF64})
    iid = (b.get("id") or (b.get("invoice") or {}).get("id")) if isinstance(b, dict) else None
    return s, b, iid


def getinv(token, iid):
    _, det = curl("GET", f"{BASE}/invoices/{iid}", token=token)
    return det.get("invoice", det) if isinstance(det, dict) else {}


def audit(token, iid):
    _, a = curl("GET", f"{BASE}/audit-log?invoice_id={iid}", token=token)
    return a if isinstance(a, list) else a.get("entries", a.get("audit_log", [])) if isinstance(a, dict) else []


mkuser("s1@nordvik.example", "submitter")
mkuser("a1@nordvik.example", "approver")
mkuser("a2@nordvik.example", "approver")
s1, a1, a2 = login("s1@nordvik.example"), login("a1@nordvik.example"), login("a2@nordvik.example")

# 1 sign-in + roles
if all([s1, a1, a2]):
    st, b, probe = mk_invoice(s1, 100)
    st2, _ = curl("POST", f"{BASE}/invoices/{probe}/approve", token=s1)
    r.rec(1, "Sign-in + roles", "pass" if st2 in (401, 403) else "fail",
          f"user creation + 3 logins ok; submitter approve attempt -> {st2}")
else:
    r.rec(1, "Sign-in + roles", "fail", f"logins: s1={bool(s1)} a1={bool(a1)} a2={bool(a2)}")

# 2 invoice creation + PDF download endpoint
st, b, inv2 = mk_invoice(s1, 9000)
if st in (200, 201) and inv2:
    d = getinv(s1, inv2)
    have = d.get("counterparty_name") == "Valmet Steel"
    pdfst, pdfbody = curl("GET", f"{BASE}/invoices/{inv2}/pdf", token=s1)
    pdfok = pdfst == 200 and str(pdfbody).startswith("%PDF")
    r.rec(2, "Invoice creation", "pass" if have and pdfok else "partial",
          f"created id={inv2}; fields ok={have}; pdf download -> {pdfst} ok={pdfok}")
else:
    r.rec(2, "Invoice creation", "fail", f"create -> {st} {str(b)[:120]}")

# 3 lifecycle
curl("POST", f"{BASE}/invoices/{inv2}/reject", token=a1)
rejected = getinv(s1, inv2).get("state") == "rejected"
st, b, inv3 = mk_invoice(s1, 500)
curl("POST", f"{BASE}/invoices/{inv3}/approve", token=a1)
approved = getinv(s1, inv3).get("state") == "approved"
r.rec(3, "Lifecycle", "pass" if rejected and approved else "fail",
      f"reject->{rejected}, approve->{approved}")

# 4 no self-approval
st, b, _ = mk_invoice(a1, 100, name="SelfTest")
r.rec(4, "No self-approval", "pass" if st in (401, 403) else "fail",
      f"approver submit attempt -> {st} (roles exclusive at submission)")

# 5 threshold
st, b, inv5 = mk_invoice(s1, 12000)
curl("POST", f"{BASE}/invoices/{inv5}/approve", token=a1)
still = getinv(s1, inv5).get("state") == "submitted"
curl("POST", f"{BASE}/invoices/{inv5}/approve", token=a2)
dual = getinv(s1, inv5).get("state") == "approved"
st, b, inv5b = mk_invoice(s1, 10000)
curl("POST", f"{BASE}/invoices/{inv5b}/approve", token=a1)
boundary = getinv(s1, inv5b).get("state") == "approved"
r.rec(5, "Two-approver threshold", "pass" if still and dual and boundary else "fail",
      f"12k after 1 approval still submitted={still}; after 2nd approved={dual}; exactly 10k one-approval={boundary}")

# 6 append-only audit
entries = audit(a1, inv5)
haswho = bool(entries) and all(any(k in e for k in ("actor_user_id", "actor_id", "actor", "actor_email")) and any(k in e for k in ("occurred_at", "created_at", "at")) for e in entries)
st_put, _ = curl("PUT", f"{BASE}/audit-log/1", token=a1)
st_del, _ = curl("DELETE", f"{BASE}/audit-log/1", token=a1)
nomod = st_put in (403, 404, 405, 501) and st_del in (403, 404, 405, 501)
r.rec(6, "Append-only audit log", "pass" if haswho and nomod else ("partial" if entries else "fail"),
      f"{len(entries)} entries, actor+timestamp={haswho}; PUT->{st_put} DELETE->{st_del}")

# 7 PII read logging + erasure (dedicated contact-email endpoint)
curl("GET", f"{BASE}/invoices/{inv3}/contact-email", token=a1)
entries = audit(a1, inv3)
readlogged = any("read" in str(e.get("action", "")).lower() for e in entries)
curl("DELETE", f"{BASE}/invoices/{inv3}/contact-email", token=s1)
d = getinv(s1, inv3)
survives = d.get("state") == "approved"
st3, b3 = curl("GET", f"{BASE}/invoices/{inv3}/contact-email", token=a1)
gone = st3 != 200 or not (isinstance(b3, dict) and (b3.get("email") or b3.get("counterparty_contact_email")))
r.rec(7, "PII erasure + access logging", "pass" if readlogged and survives and gone else "partial",
      f"read audited={readlogged}; erased={gone} (post-erase GET -> {st3}); invoice survives={survives}")

# 8 pending list + filters
mk_invoice(s1, 3000, name="Filter Test Oy")
_, pend = curl("GET", f"{BASE}/approvals/pending?counterparty_name=filter", token=a1)
plist = pend if isinstance(pend, list) else pend.get("invoices", []) if isinstance(pend, dict) else []
onlymatch = bool(plist) and all("filter" in (p.get("counterparty_name") or "").lower() for p in plist)
_, pend2 = curl("GET", f"{BASE}/approvals/pending?min_amount_euros=999999", token=a1)
plist2 = pend2 if isinstance(pend2, list) else pend2.get("invoices", []) if isinstance(pend2, dict) else []
r.rec(8, "Pending list + filters", "pass" if onlymatch and len(plist2) == 0 else "partial",
      f"name filter ok={onlymatch} ({len(plist)} rows); min_amount_euros=999999 -> {len(plist2)} rows")

# 9 notification stub
maillog = RUN / "notifications.log"
mail = maillog.read_text() if maillog.exists() else ""
ok = "s1@nordvik.example" in mail
r.rec(9, "Notification stub", "pass" if ok else "fail",
      f"notifications.log exists={maillog.exists()}, addressed to submitter={ok}")

# 10 README + tests
t = subprocess.run(["python3", "-m", "unittest", "-v"], cwd=RUN, capture_output=True, text=True, timeout=300)
save_unit_output("copilot-run3", t)
tail = (t.stdout + t.stderr).strip().splitlines()[-2:]
r.rec(10, "README + tests", "pass" if t.returncode == 0 else "fail",
      f"Unit tests (startup deviations are in the rerun receipt); unittest rc={t.returncode}: {' | '.join(tail)[:160]}")

r.save()
