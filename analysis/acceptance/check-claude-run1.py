"""Acceptance checks for claude/run1 — Express, bare paths, opaque bearer tokens, seeded users."""
import datetime
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import curl, wait_up, Report, PDF, configure, save_unit_output

BASE, RUN = configure("claude-run1", "http://127.0.0.1:3000")
DUE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
r = Report("claude-run1")


def login(email):
    s, b = curl("POST", f"{BASE}/login", jsonbody={"email": email, "password": "password123"})
    return b.get("token") if isinstance(b, dict) else None


def mk_invoice(token, amount, name="Valmet Steel", email="laskutus@valmetsteel.example"):
    s, b = curl("POST", f"{BASE}/invoices", token=token, form={
        "amount": str(amount), "counterpartyName": name,
        "counterpartyEmail": email, "dueDate": DUE, "pdf": f"@{PDF};type=application/pdf"})
    return s, b


s1, a1, a2 = login("submitter@nordvik.example"), login("approver1@nordvik.example"), login("approver2@nordvik.example")

# 1 sign-in + roles
if all([s1, a1, a2]):
    st, b = mk_invoice(s1, 100)
    inv_probe = b.get("id") if st in (200, 201) else None
    st2, _ = curl("POST", f"{BASE}/invoices/{inv_probe}/approve", token=s1)
    r.rec(1, "Sign-in + roles", "pass" if st2 in (401, 403) else "fail",
          f"3 logins ok; submitter approve attempt -> {st2}")
else:
    r.rec(1, "Sign-in + roles", "fail", f"logins: s1={bool(s1)} a1={bool(a1)} a2={bool(a2)}")

# 2 invoice creation
st, b = mk_invoice(s1, 9000)
inv2 = b.get("id") if isinstance(b, dict) else None
if st in (200, 201) and inv2:
    st2, det = curl("GET", f"{BASE}/invoices/{inv2}", token=s1)
    have = isinstance(det, dict) and det.get("counterpartyName") == "Valmet Steel"
    # attachment retrievable? no download endpoint in API; check field presence
    pdffield = any(k for k in (det or {}) if "pdf" in k.lower())
    r.rec(2, "Invoice creation", "pass" if have and pdffield else "partial",
          f"created id={inv2}; fields ok={have}; pdf field present={pdffield} (no download endpoint — stored path only)")
else:
    r.rec(2, "Invoice creation", "fail", f"create -> {st} {b}")

# 3 lifecycle: reject inv2; approve a fresh one
st, _ = curl("POST", f"{BASE}/invoices/{inv2}/reject", token=a1)
_, det = curl("GET", f"{BASE}/invoices/{inv2}", token=s1)
rejected = isinstance(det, dict) and det.get("state") == "rejected"
st, b = mk_invoice(s1, 500)
inv3 = b.get("id")
curl("POST", f"{BASE}/invoices/{inv3}/approve", token=a1)
_, det = curl("GET", f"{BASE}/invoices/{inv3}", token=s1)
approved = isinstance(det, dict) and det.get("state") == "approved"
r.rec(3, "Lifecycle", "pass" if rejected and approved else "fail",
      f"reject->{rejected}, approve->{approved}")

# 4 no self-approval (approvers cannot submit in this run)
st, b = curl("POST", f"{BASE}/invoices", token=a1, form={
    "amount": "100", "counterpartyName": "X", "counterpartyEmail": "x@x.example",
    "dueDate": DUE, "pdf": f"@{PDF};type=application/pdf"})
r.rec(4, "No self-approval", "pass" if st in (401, 403) else "fail",
      f"approver submit attempt -> {st} (roles exclusive; self-approval structurally impossible + 403 guard in code)")

# 5 threshold
st, b = mk_invoice(s1, 12000)
inv5 = b.get("id")
curl("POST", f"{BASE}/invoices/{inv5}/approve", token=a1)
_, det = curl("GET", f"{BASE}/invoices/{inv5}", token=s1)
still = det.get("state") == "submitted"
curl("POST", f"{BASE}/invoices/{inv5}/approve", token=a2)
_, det = curl("GET", f"{BASE}/invoices/{inv5}", token=s1)
dual = det.get("state") == "approved"
st, b = mk_invoice(s1, 10000)
inv5b = b.get("id")
curl("POST", f"{BASE}/invoices/{inv5b}/approve", token=a1)
_, det = curl("GET", f"{BASE}/invoices/{inv5b}", token=s1)
boundary = det.get("state") == "approved"
r.rec(5, "Two-approver threshold", "pass" if still and dual and boundary else "fail",
      f"12k after 1 approval still submitted={still}; after 2nd approved={dual}; exactly 10k one-approval={boundary}")

# 6 append-only audit
_, audit = curl("GET", f"{BASE}/invoices/{inv5}/audit", token=a1)
entries = audit if isinstance(audit, list) else audit.get("entries", []) if isinstance(audit, dict) else []
haswho = bool(entries) and all(("actor_id" in e or "actorId" in e or "actor" in e) and ("created_at" in e or "createdAt" in e or "at" in e) for e in entries)
st_put, _ = curl("PUT", f"{BASE}/invoices/{inv5}/audit", token=a1)
st_del, _ = curl("DELETE", f"{BASE}/invoices/{inv5}/audit/1", token=a1)
nomod = st_put in (404, 405, 403) and st_del in (404, 405, 403)
r.rec(6, "Append-only audit log", "pass" if haswho and nomod else ("partial" if entries else "fail"),
      f"{len(entries)} entries, actor+timestamp={haswho}; PUT->{st_put} DELETE->{st_del} (no modification API)")

# 7 PII read logging + erasure
st, b = curl("GET", f"{BASE}/invoices/{inv3}/counterparty-email", token=a1)
_, audit = curl("GET", f"{BASE}/invoices/{inv3}/audit", token=a1)
entries = audit if isinstance(audit, list) else []
readlogged = any("read" in str(e.get("action", "")) for e in entries)
st, _ = curl("DELETE", f"{BASE}/invoices/{inv3}/counterparty-email", token=s1)
st2, det = curl("GET", f"{BASE}/invoices/{inv3}", token=s1)
survives = st2 == 200 and det.get("state") == "approved"
st3, b3 = curl("GET", f"{BASE}/invoices/{inv3}/counterparty-email", token=a1)
gone = st3 != 200 or not (isinstance(b3, dict) and b3.get("email"))
r.rec(7, "PII erasure + access logging", "pass" if readlogged and survives and gone else "partial",
      f"read audited={readlogged}; erased={gone}; invoice survives={survives}")

# 8 pending list + filters
st, b = mk_invoice(s1, 3000, name="Filter Test Oy")
_, pend = curl("GET", f"{BASE}/invoices/pending?counterparty=Filter", token=a1)
plist = pend if isinstance(pend, list) else pend.get("invoices", []) if isinstance(pend, dict) else []
onlymatch = bool(plist) and all("Filter" in p.get("counterpartyName", p.get("counterparty_name", "")) for p in plist)
_, pend2 = curl("GET", f"{BASE}/invoices/pending?minAmount=999999", token=a1)
plist2 = pend2 if isinstance(pend2, list) else pend2.get("invoices", []) if isinstance(pend2, dict) else []
r.rec(8, "Pending list + filters", "pass" if onlymatch and len(plist2) == 0 else "partial",
      f"name filter ok={onlymatch}; minAmount=999999 -> {len(plist2)} rows")

# 9 notification stub
maillog = RUN / "data/emails.log"
mail = maillog.read_text() if maillog.exists() else ""
r.rec(9, "Notification stub", "pass" if "submitter@nordvik.example" in mail else "fail",
      f"emails.log exists={maillog.exists()}, addressed to submitter={'submitter@nordvik.example' in mail}")

# 10 README + tests
t = subprocess.run(["npm", "test"], cwd=RUN, capture_output=True, text=True, timeout=300)
save_unit_output("claude-run1", t)
passed = t.returncode == 0
tail = (t.stdout + t.stderr).strip().splitlines()[-3:]
r.rec(10, "README + tests", "pass" if passed else "fail",
      f"Unit tests (startup deviations are in the rerun receipt); npm test rc={t.returncode}: {' | '.join(tail)[:160]}")

r.save()
