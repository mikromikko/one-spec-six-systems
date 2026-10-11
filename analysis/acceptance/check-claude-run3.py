"""Acceptance checks for claude/run3 — Express, session cookies, snake_case fields, /api/invoices/pending."""
import datetime
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import curl, Report, PDF, configure, save_unit_output

BASE, RUN = configure("claude-run3", "http://127.0.0.1:3000/api")
DUE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
r = Report("claude-run3")

jars = {}


def jar(who):
    if who not in jars:
        jars[who] = tempfile.NamedTemporaryFile(suffix=f"-{who}.jar", delete=False).name
    return jars[who]


def login(who, email):
    s, b = curl("POST", f"{BASE}/auth/login", jsonbody={"email": email, "password": "password123"}, cookiejar=jar(who))
    return s == 200


def mk_invoice(who, amount, name="Valmet Steel", email="laskutus@valmetsteel.example"):
    s, b = curl("POST", f"{BASE}/invoices", cookiejar=jar(who), form={
        "amount": str(amount), "counterparty_name": name,
        "counterparty_email": email, "due_date": DUE, "pdf": f"@{PDF};type=application/pdf"})
    iid = (b.get("id") or (b.get("invoice") or {}).get("id")) if isinstance(b, dict) else None
    return s, b, iid


def state(who, iid):
    _, det = curl("GET", f"{BASE}/invoices/{iid}", cookiejar=jar(who))
    if isinstance(det, dict):
        return det.get("state") or (det.get("invoice") or {}).get("state")
    return None


ok_s1 = login("s1", "submitter1@nordvik.example")
ok_a1 = login("a1", "approver1@nordvik.example")
ok_a2 = login("a2", "approver2@nordvik.example")

# 1 sign-in + roles
if ok_s1 and ok_a1 and ok_a2:
    st, b, probe = mk_invoice("s1", 100)
    st2, _ = curl("POST", f"{BASE}/invoices/{probe}/approve", cookiejar=jar("s1"))
    r.rec(1, "Sign-in + roles", "pass" if st2 in (401, 403) else "fail",
          f"3 logins ok (cookie sessions); submitter approve attempt -> {st2}")
else:
    r.rec(1, "Sign-in + roles", "fail", f"logins: s1={ok_s1} a1={ok_a1} a2={ok_a2}")

# 2 invoice creation
st, b, inv2 = mk_invoice("s1", 9000)
if st in (200, 201) and inv2:
    _, det = curl("GET", f"{BASE}/invoices/{inv2}", cookiejar=jar("s1"))
    d = det.get("invoice", det) if isinstance(det, dict) else {}
    have = d.get("counterparty_name") == "Valmet Steel"
    pdfst, pdfbody = curl("GET", f"{BASE}/invoices/{inv2}/pdf", cookiejar=jar("s1"))
    pdfok = pdfst == 200 and str(pdfbody).startswith("%PDF")
    score = "pass" if have and pdfok else "partial"
    r.rec(2, "Invoice creation", score,
          f"created id={inv2}; fields ok={have}; pdf download -> {pdfst} ok={pdfok}")
else:
    r.rec(2, "Invoice creation", "fail", f"create -> {st} {str(b)[:120]}")

# 3 lifecycle
curl("POST", f"{BASE}/invoices/{inv2}/reject", cookiejar=jar("a1"))
rejected = state("s1", inv2) == "rejected"
st, b, inv3 = mk_invoice("s1", 500)
curl("POST", f"{BASE}/invoices/{inv3}/approve", cookiejar=jar("a1"))
approved = state("s1", inv3) == "approved"
r.rec(3, "Lifecycle", "pass" if rejected and approved else "fail",
      f"reject->{rejected}, approve->{approved}")

# 4 no self-approval — code has requireRole('submitter') on POST; verify empirically
st, b, selfinv = mk_invoice("a1", 100, name="SelfTest")
if st in (401, 403):
    r.rec(4, "No self-approval", "pass", f"approver submit attempt -> {st} (roles exclusive at submission)")
elif selfinv:
    st2, _ = curl("POST", f"{BASE}/invoices/{selfinv}/approve", cookiejar=jar("a1"))
    st3, _ = curl("POST", f"{BASE}/invoices/{selfinv}/approve", cookiejar=jar("a2"))
    r.rec(4, "No self-approval", "pass" if st2 in (401, 403) and st3 in (200, 201) else "fail",
          f"approver CAN submit (-> {st}); own approve -> {st2}, other approver -> {st3}")
else:
    r.rec(4, "No self-approval", "fail", f"unexpected: {st} {str(b)[:100]}")

# 5 threshold
st, b, inv5 = mk_invoice("s1", 12000)
curl("POST", f"{BASE}/invoices/{inv5}/approve", cookiejar=jar("a1"))
still = state("s1", inv5) == "submitted"
curl("POST", f"{BASE}/invoices/{inv5}/approve", cookiejar=jar("a2"))
dual = state("s1", inv5) == "approved"
st, b, inv5b = mk_invoice("s1", 10000)
curl("POST", f"{BASE}/invoices/{inv5b}/approve", cookiejar=jar("a1"))
boundary = state("s1", inv5b) == "approved"
r.rec(5, "Two-approver threshold", "pass" if still and dual and boundary else "fail",
      f"12k after 1 approval still submitted={still}; after 2nd approved={dual}; exactly 10k one-approval={boundary}")

# 6 append-only audit
_, audit = curl("GET", f"{BASE}/invoices/{inv5}/audit", cookiejar=jar("a1"))
entries = audit if isinstance(audit, list) else audit.get("entries", audit.get("audit", [])) if isinstance(audit, dict) else []
haswho = bool(entries) and all(any(k in e for k in ("actor_id", "actorId", "actor")) and any(k in e for k in ("created_at", "createdAt", "at")) for e in entries)
st_put, _ = curl("PUT", f"{BASE}/invoices/{inv5}/audit", cookiejar=jar("a1"))
st_del, _ = curl("DELETE", f"{BASE}/invoices/{inv5}/audit/1", cookiejar=jar("a1"))
nomod = st_put in (403, 404, 405) and st_del in (403, 404, 405)
r.rec(6, "Append-only audit log", "pass" if haswho and nomod else ("partial" if entries else "fail"),
      f"{len(entries)} entries, actor+timestamp={haswho}; PUT->{st_put} DELETE->{st_del}")

# 7 PII read logging + erasure
curl("GET", f"{BASE}/invoices/{inv3}/counterparty-email", cookiejar=jar("a1"))
_, audit = curl("GET", f"{BASE}/invoices/{inv3}/audit", cookiejar=jar("a1"))
entries = audit if isinstance(audit, list) else audit.get("entries", audit.get("audit", [])) if isinstance(audit, dict) else []
readlogged = any("read" in str(e.get("action", "")) for e in entries)
curl("DELETE", f"{BASE}/invoices/{inv3}/counterparty-email", cookiejar=jar("s1"))
survives = state("s1", inv3) == "approved"
st3, b3 = curl("GET", f"{BASE}/invoices/{inv3}/counterparty-email", cookiejar=jar("a1"))
gone = st3 != 200 or not (isinstance(b3, dict) and (b3.get("email") or b3.get("counterparty_email")))
r.rec(7, "PII erasure + access logging", "pass" if readlogged and survives and gone else "partial",
      f"read audited={readlogged}; erased={gone} (post-erase GET -> {st3}); invoice survives={survives}")

# 8 pending list + filters
mk_invoice("s1", 3000, name="Filter Test Oy")
_, pend = curl("GET", f"{BASE}/invoices/pending?counterparty=Filter", cookiejar=jar("a1"))
plist = pend if isinstance(pend, list) else pend.get("invoices", []) if isinstance(pend, dict) else []
onlymatch = bool(plist) and all("Filter" in (p.get("counterparty_name") or "") for p in plist)
_, pend2 = curl("GET", f"{BASE}/invoices/pending?min_amount=999999", cookiejar=jar("a1"))
plist2 = pend2 if isinstance(pend2, list) else pend2.get("invoices", []) if isinstance(pend2, dict) else []
r.rec(8, "Pending list + filters", "pass" if onlymatch and len(plist2) == 0 else "partial",
      f"name filter ok={onlymatch} ({len(plist)} rows); min_amount=999999 -> {len(plist2)} rows")

# 9 notification stub (.eml files in ./emails + outbox.log)
emaildir = RUN / "emails"
files = list(emaildir.glob("*")) if emaildir.exists() else []
content = "".join(f.read_text(errors="ignore") for f in files if f.is_file())
ok = "submitter1@nordvik.example" in content
r.rec(9, "Notification stub", "pass" if ok else "fail",
      f"emails dir has {len(files)} files; addressed to submitter={ok}")

# 10 README + tests
t = subprocess.run(["npm", "test"], cwd=RUN, capture_output=True, text=True, timeout=300)
save_unit_output("claude-run3", t)
tail = (t.stdout + t.stderr).strip().splitlines()[-3:]
r.rec(10, "README + tests", "pass" if t.returncode == 0 else "fail",
      f"Unit tests (startup deviations are in the rerun receipt); npm test rc={t.returncode}: {' | '.join(tail)[:160]}")

r.save()
