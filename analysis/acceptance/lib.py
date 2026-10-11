"""Shared helpers for the recovered acceptance checks; stdlib-only."""
import argparse
import datetime
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
PDF = HERE / "test-invoice.pdf"
OUTPUT = None


def loopback_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError("Only http://127.0.0.1 targets are permitted")
    return url.rstrip("/")


def configure(run_id, default_base):
    global OUTPUT
    parser = argparse.ArgumentParser(description="Acceptance checks against a disposable local service")
    parser.add_argument("--base", default=default_base)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Fresh receipt directory, separate from the July results")
    args = parser.parse_args()
    base = loopback_url(args.base)
    OUTPUT = args.output_dir.resolve()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if (OUTPUT / f"results-{run_id}.md").exists():
        parser.error("Receipt already exists; use a fresh output directory")
    agent, run = run_id.split("-")
    return base, HERE.parents[1] / "runs" / agent / run


def curl(method, url, token=None, form=None, jsonbody=None, cookiejar=None, headers=None):
    """Returns (status_code:int, body: dict|str). Never follows redirects or proxies."""
    loopback_url(url)
    cmd = ["curl", "--noproxy", "*", "--proto", "=http", "--max-time", "20",
           "-s", "-w", "\n%{http_code}", "-X", method, url]
    if token:
        cmd += ["-H", f"Authorization: Bearer {token}"]
    for h in headers or []:
        cmd += ["-H", h]
    if cookiejar:
        cmd += ["-b", str(cookiejar), "-c", str(cookiejar)]
    if jsonbody is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(jsonbody)]
    if form:
        for k, v in form.items():
            cmd += ["-F", f"{k}={v}"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    body, _, status = out.rpartition("\n")
    try:
        parsed = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError:
        parsed = body
    return int(status or 0), parsed


def save_unit_output(run_id, result):
    if OUTPUT is None:
        raise RuntimeError("configure() must set an explicit receipt directory")
    with (OUTPUT / f"{run_id}-unit.log").open("x") as output:
        output.write(result.stdout + result.stderr)


def wait_up(url, tries=40):
    for _ in range(tries):
        try:
            s, _ = curl("GET", url)
            if s > 0:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


class Report:
    def __init__(self, run_id):
        self.run_id = run_id
        self.rows = []

    def rec(self, no, name, score, note=""):
        assert score in ("pass", "partial", "fail")
        self.rows.append((no, name, score, note))
        print(f"  [{score.upper():7}] {no} {name} — {note}")

    def save(self):
        if OUTPUT is None:
            raise RuntimeError("configure() must set an explicit receipt directory")
        p = OUTPUT / f"results-{self.run_id}.md"
        n = {"pass": 0, "partial": 0, "fail": 0}
        executed = datetime.datetime.now(datetime.timezone.utc).isoformat()
        lines = [f"# Acceptance rerun — {self.run_id}", "",
                 f"Executed as scripted HTTP checks at {executed}.",
                 "Startup/runtime deviations are documented in this directory's receipt.json; July results are unchanged.", "",
                 "| # | Check | Score | Notes |", "|---|---|---|---|"]
        for no, name, score, note in sorted(self.rows):
            n[score] += 1
            lines.append(f"| {no} | {name} | {score} | {note} |")
        lines += ["", f"**Total: {n['pass']} pass / {n['partial']} partial / {n['fail']} fail**", ""]
        with p.open("x") as output:
            output.write("\n".join(lines))
        print(f"saved {p} — {n['pass']}P/{n['partial']}p/{n['fail']}F")
