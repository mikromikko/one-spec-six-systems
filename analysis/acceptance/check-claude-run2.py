"""Acceptance checks for claude/run2 — Express, /api/* prefix, JWT bearer, amountEuros field, PDF download endpoint."""
import datetime
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import curl, Report, PDF, configure, save_unit_output

BASE, RUN = configure("claude-run2", "http://127.0.0.1:3000/api")
DUE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
r = Report("claude-run2")


def login(email, password):
    s, b = curl("POST", f"{BASE}/auth/login", jsonbody={"email": email, "password": password})
    return b.get("token") if isinstance(b, dict) else None


def mk_invoice(token, amount, name="Valmet Steel", email="laskutus@valmetsteel.example"):
    s, b = curl("POST", f"{BASE}/invoices", token=token, form={
        "amountEuros": str(amount), "counterpartyName": name,
        "counterpartyEmail": email, "dueDate": DUE, "pdf": f"@{PDF};type=application/pdf"})
    iid = b.get("id") or (b.get("invoice") or {}).get("id") if isinstance(b, dict) else None
    return s, b, iid


def state(token, iid):
    _, det = curl("GET", f"{BASE}/invoices/{iid}", token=token)
    if isinstance(det, dict):
        return det.get("state") or (det.get("invoice") or {}).get("state")
    return None


s1 = login("submitter@nordvik.example", "submitter-pass")
a1 = login("approver1@nordvik.example", "approver-pass")
a2 = login("approver2@nordvik.example", "approver-pass")

# 1 sign-in + roles
if all([s1, a1, a2]):
    st, b, probe = mk_invoice(s1, 100)
    st2, _ = curl("POST", f"{BASE}/invoices/{probe}/approve", token=s1)
    r.rec(1, "Sign-in + roles", "pass" if st2 in (401, 403) else "fail",
          f"3 logins ok; submitter approve attempt -> {st2}")
else:
    r.rec(1, "Sign-in + roles", "fail", f"logins: s1={bool(s1)} a1={bool(a1)} a2={bool(a2)}")

# 2 invoice creation (+ PDF retrievable via download endpoint)
st, b, inv2 = mk_invoice(s1, 9000)
if st in (200, 201) and inv2:
    _, det = curl("GET", f"{BASE}/invoices/{inv2}", token=s1)
    d = det.get("invoice", det) if isinstance(det, dict) else {}
    have = d.get("counterpartyName") == "Valmet Steel"
    pdfst, pdfbody = curl("GET", f"{BASE}/invoices/{inv2}/pdf", token=s1)
    pdfok = pdfst == 200 and str(pdfbody).startswith("%PDF")
    r.rec(2, "Invoice creation", "pass" if have and pdfok else "partial",
          f"created id={inv2}; fields ok={have}; pdf download={pdfok} (status {pdfst})")
else:
    r.rec(2, "Invoice creation", "fail", f"create -> {st} {str(b)[:120]}")

# 3 lifecycle
curl("POST", f"{BASE}/invoices/{inv2}/reject", token=a1)
rejected = state(s1, inv2) == "rejected"
st, b, inv3 = mk_invoice(s1, 500)
curl("POST", f"{BASE}/invoices/{inv3}/approve", token=a1)
approved = state(s1, inv3) == "approved"
r.rec(3, "Lifecycle", "pass" if rejected and approved else "fail",
      f"reject->{rejected}, approve->{approved}")

# 4 no self-approval (approvers cannot submit in this run per extraction)
st, b, _ = mk_invoice(a1, 100, name="X")
r.rec(4, "No self-approval", "pass" if st in (401, 403) else "fail",
      f"approver submit attempt -> {st} (roles exclusive; 403 guard on approve for submitter_id match)")

# 5 threshold
st, b, inv5 = mk_invoice(s1, 12000)
curl("POST", f"{BASE}/invoices/{inv5}/approve", token=a1)
still = state(s1, inv5) == "submitted"
curl("POST", f"{BASE}/invoices/{inv5}/approve", token=a2)
dual = state(s1, inv5) == "approved"
st, b, inv5b = mk_invoice(s1, 10000)
curl("POST", f"{BASE}/invoices/{inv5b}/approve", token=a1)
boundary = state(s1, inv5b) == "approved"
r.rec(5, "Two-approver threshold", "pass" if still and dual and boundary else "fail",
      f"12k after 1 approval still submitted={still}; after 2nd approved={dual}; exactly 10k one-approval={boundary}")

# 6 append-only audit
_, audit = curl("GET", f"{BASE}/invoices/{inv5}/audit", token=a1)
entries = audit if isinstance(audit, list) else audit.get("entries", audit.get("audit", [])) if isinstance(audit, dict) else []
haswho = bool(entries) and all(any(k in e for k in ("actor_id", "actorId", "actor")) and any(k in e for k in ("created_at", "createdAt", "at")) for e in entries)
st_put, _ = curl("PUT", f"{BASE}/invoices/{inv5}/audit", token=a1)
st_del, _ = curl("DELETE", f"{BASE}/invoices/{inv5}/audit/1", token=a1)
nomod = st_put in (403, 404, 405) and st_del in (403, 404, 405)
r.rec(6, "Append-only audit log", "pass" if haswho and nomod else ("partial" if entries else "fail"),
      f"{len(entries)} entries, actor+timestamp={haswho}; PUT->{st_put} DELETE->{st_del}")

# 7 PII read logging + erasure
curl("GET", f"{BASE}/invoices/{inv3}/contact-email", token=a1)
_, audit = curl("GET", f"{BASE}/invoices/{inv3}/audit", token=a1)
entries = audit if isinstance(audit, list) else audit.get("entries", audit.get("audit", [])) if isinstance(audit, dict) else []
readlogged = any("read" in str(e.get("action", "")) for e in entries)
curl("DELETE", f"{BASE}/invoices/{inv3}/contact-email", token=s1)
survives = state(s1, inv3) == "approved"
st3, b3 = curl("GET", f"{BASE}/invoices/{inv3}/contact-email", token=a1)
gone = st3 != 200 or not (isinstance(b3, dict) and (b3.get("email") or b3.get("contactEmail") or b3.get("counterpartyEmail")))
r.rec(7, "PII erasure + access logging", "pass" if readlogged and survives and gone else "partial",
      f"read audited={readlogged}; erased={gone} (post-erase GET -> {st3}); invoice survives={survives}")

# 8 pending list + filters (GET /api/invoices as approver)
mk_invoice(s1, 3000, name="Filter Test Oy")
_, pend = curl("GET", f"{BASE}/invoices?counterpartyName=Filter", token=a1)
plist = pend if isinstance(pend, list) else pend.get("invoices", []) if isinstance(pend, dict) else []
onlymatch = bool(plist) and all("Filter" in (p.get("counterpartyName") or "") for p in plist)
_, pend2 = curl("GET", f"{BASE}/invoices?minAmount=999999", token=a1)
plist2 = pend2 if isinstance(pend2, list) else pend2.get("invoices", []) if isinstance(pend2, dict) else []
r.rec(8, "Pending list + filters", "pass" if onlymatch and len(plist2) == 0 else "partial",
      f"name filter ok={onlymatch} ({len(plist)} rows); minAmount=999999 -> {len(plist2)} rows")

# 9 notification stub
outbox = RUN / "data/outbox.log"
mail = outbox.read_text() if outbox.exists() else ""
ok = "submitter@nordvik.example" in mail
r.rec(9, "Notification stub", "pass" if ok else "fail",
      f"outbox.log exists={outbox.exists()}, addressed to submitter={ok}")

# 10 README + tests
t = subprocess.run(["npm", "test"], cwd=RUN, capture_output=True, text=True, timeout=300)
save_unit_output("claude-run2", t)
tail = (t.stdout + t.stderr).strip().splitlines()[-3:]
r.rec(10, "README + tests", "pass" if t.returncode == 0 else "fail",
      f"Unit tests (startup deviations are in the rerun receipt); npm test rc={t.returncode}: {' | '.join(tail)[:160]}")

r.save()
