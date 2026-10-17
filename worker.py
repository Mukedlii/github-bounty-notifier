"""Bounded worker: five-minute scans and one explicit successor dispatch."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from notifier import API, GitHub


def dispatch_successor(token, repo):
    payload = {'ref': 'main', 'inputs': {'dry_run': False, 'follow_up': True}}
    request = urllib.request.Request(API + f'/repos/{repo}/actions/workflows/watch.yml/dispatches',
        data=json.dumps(payload).encode(), method='POST', headers={
            'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
            'Content-Type': 'application/json', 'User-Agent': 'github-bounty-notifier'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 204:
                raise RuntimeError('Successor dispatch not confirmed')
    except Exception:
        raise RuntimeError('Successor dispatch not confirmed; no blind retry. Hourly watchdog remains configured.') from None
    print('SUCCESSOR_DISPATCH_CONFIRMED', flush=True)


def run_worker(cycles=3, interval=300):
    token, repo = os.environ['GITHUB_TOKEN'], os.environ['GITHUB_REPOSITORY']
    if GitHub(token).get(f'/repos/{repo}').get('private') is not False:
        raise RuntimeError('Continuous worker requires a public repository and standard free runner')
    follow = os.environ.get('FOLLOW_UP', '').lower() == 'true'
    dry = os.environ.get('DRY_RUN', '').lower() == 'true'
    enabled = os.environ.get('ENABLE_SCHEDULE', '').lower() == 'true'
    bootstrap = os.environ.get('BOOTSTRAP', '').lower() == 'true'
    cycles = cycles if follow and not dry and enabled and not bootstrap else 1
    failures = 0
    for index in range(cycles):
        started = time.monotonic()
        command = [sys.executable, '-u', 'notifier.py'] + (['--dry-run'] if dry else [])
        try:
            exit_code = subprocess.run(command, timeout=240).returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
        failures = failures + 1 if exit_code else 0
        print(json.dumps({'cycle': index + 1, 'exit_code': exit_code}), flush=True)
        if failures >= 3:
            raise RuntimeError('Three consecutive scan failures; worker stopped, watchdog may restart later')
        # Wait until next five-minute slot in short interruptible slices.
        if follow and not dry and enabled and not bootstrap:
            deadline = started + interval
            while time.monotonic() < deadline:
                time.sleep(max(0, min(30, deadline - time.monotonic())))
    if follow and not dry and enabled:
        dispatch_successor(token, repo)
    elif failures:
        raise RuntimeError('Scan failed')


if __name__ == '__main__':
    try:
        run_worker()
    except Exception as exc:
        print('Worker stopped: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), file=sys.stderr, flush=True)
        sys.exit(1)
