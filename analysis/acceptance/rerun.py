"""Rerun existing outputs in a network-disabled disposable Linux container."""
import datetime
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

SOURCE = Path(__file__).resolve().parents[2]
DEST = Path('/work/experiment')
OUT = Path('/receipts')
RUN_IDS = [f'{agent}-run{n}' for agent in ('claude', 'copilot') for n in (1, 2, 3)]


def verify(root):
    manifest = json.loads((root / 'analysis/acceptance/provenance.json').read_text())
    for name, expected in manifest['published_inputs'].items():
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f'Published input changed: {name}')
    return len(manifest['published_inputs'])


def main():
    if os.environ.get('ACCEPTANCE_ISOLATED') != '1' or set(os.listdir('/sys/class/net')) != {'lo'}:
        raise RuntimeError('Use the documented Docker invocation with --network none')
    if any(OUT.iterdir()):
        raise RuntimeError('Receipt directory must be empty')
    verified = verify(SOURCE)
    shutil.copytree(SOURCE, DEST)
    env = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
           'HOME': '/tmp/acceptance-home', 'LANG': 'C.UTF-8', 'HOST': '127.0.0.1',
           'NODE_OPTIONS': f'--require={DEST}/analysis/acceptance/bind-loopback.cjs'}
    Path(env['HOME']).mkdir()
    receipt = {
        'executed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'snapshot': 'b3394ba8286680a75b6031181430d93edb061d98',
        'published_input_files_verified': verified,
        'adapter_bundle_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted((SOURCE / 'analysis/acceptance').iterdir())
                                  if p.is_file() and p.suffix in ('.py', '.cjs', '.pdf', '.lock')},
        'runtime': {'architecture': platform.machine(), 'kernel': platform.release()}, 'runs': [],
        'deviations': [
            'Fresh container data and synthetic fixtures; no generation or changes to the six outputs.',
            'Node 24 / Python 3.12 instead of the original July host; exact versions and installed Python dependencies recorded.',
            'npm ci --ignore-scripts uses each published lockfile; pip dependencies recorded separately.',
            'Original Node service startup is preloaded with a fixed-port loopback wrapper; unit tests keep native ephemeral listeners in the loopback-only network. FastAPI runs without reload.',
            'Adapters use repository-relative paths, 127.0.0.1 targets and fresh UTC-dated, exclusive-write receipts.',
            'Check 10 records unit-test success; this rerun does not assert that original README instructions worked verbatim.',
            'Acceptance scoring logic is preserved, including its known limited security coverage.'
        ]}
    for label, command in [('node', ['node', '--version']), ('python', ['python3', '--version']),
                           ('npm', ['npm', '--version']), ('curl', ['curl', '--version'])]:
        receipt['runtime'][label] = subprocess.check_output(command, env=env, text=True).splitlines()[0]
    freeze = subprocess.check_output([str(DEST / 'runs/copilot/run1/.venv/bin/pip'), 'freeze'], env=env, text=True)
    (OUT / 'python-dependencies.txt').write_text(freeze)
    for run_id in RUN_IDS:
        agent, run = run_id.split('-')
        cwd = DEST / 'runs' / agent / run
        log = OUT / f'{run_id}-server.log'
        item = {'run_id': run_id, 'commands': [], 'adapter_exit_code': None, 'counts': None}
        if agent == 'claude':
            if run != 'run3':
                seed = ['npm', 'run', 'seed']; item['commands'].append(seed)
                result = subprocess.run(seed, cwd=cwd, env=env, text=True, capture_output=True, timeout=60)
                (OUT / f'{run_id}-seed.log').write_text(result.stdout + result.stderr)
                if result.returncode:
                    raise RuntimeError(f'Seeding failed: {run_id}; inspect saved log')
            command = ['npm', 'start']; port = 3000
        elif run == 'run1':
            command = ['.venv/bin/python', '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000']; port = 8000
        else:
            command = ['python3', 'app.py']; port = 8000
        item['commands'].append(command)
        with log.open('w') as output:
            server = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                ready = False
                for _ in range(100):
                    if server.poll() is not None:
                        break
                    try:
                        urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=1).close(); ready = True
                    except urllib.error.HTTPError:
                        ready = True
                    except (urllib.error.URLError, TimeoutError):
                        pass
                    if ready:
                        break
                    time.sleep(0.2)
                item['service_ready'] = ready
                if ready:
                    command = ['python3', str(DEST / f'analysis/acceptance/check-{run_id}.py'), '--output-dir', str(OUT)]
                    item['commands'].append(command)
                    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, timeout=360)
                    text = result.stdout + result.stderr
                    (OUT / f'{run_id}-checks.log').write_text(text)
                    item['adapter_exit_code'] = result.returncode
                    match = re.search(r'(\d+)P/(\d+)p/(\d+)F', text)
                    if match:
                        item['counts'] = dict(zip(('pass', 'partial', 'fail'), map(int, match.groups())))
                else:
                    item['error'] = 'Service did not become ready within 20 seconds'
            except subprocess.TimeoutExpired:
                item['error'] = 'Adapter exceeded the 360-second bound'
            finally:
                import signal
                try:
                    os.killpg(server.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL); server.wait()
        receipt['runs'].append(item)
        (OUT / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(run_id, item['counts'], item.get('error', ''), flush=True)
    receipt['published_input_files_verified_after'] = verify(DEST)
    receipt['completed_at_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (OUT / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    if any(item['adapter_exit_code'] != 0 or item['counts'] is None for item in receipt['runs']):
        raise SystemExit('Incomplete rerun; inspect receipts. No failed case is converted to a pass.')


if __name__ == '__main__':
    main()
