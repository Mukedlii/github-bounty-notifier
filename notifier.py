"""Conservative rule-based GitHub bounty discovery; Python standard library only."""
import argparse
import base64
import hashlib
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.github.com'
STATE_PATH = 'state/notifications.json'
CHAT_ID_SHA256 = '2d0a470453be09af1c50fb6cb36dfc4eb167ced3f3f63edeb11e99ce23a0fa39'
QUERIES = [
    'is:issue is:open no:assignee label:bounty sort:updated-desc',
    'is:issue is:open no:assignee "bounty" "$" sort:created-desc',
    'is:issue is:open no:assignee "USD" "paid" sort:created-desc',
]
# Previously reviewed, allocated, overcrowded, fake-money or unsuitable sources.
EXCLUDED_REPOS = {
    'limitlessprojects/go-hybrid', 'oll4com/spaceapp', 'expensify/app',
    'claude-builders-bounty/claude-builders-bounty', 'tine1117/oss-hunter-livefire',
    'warpspeedopen-source/warpspeed-bounties', 'causify-ai/helpers',
    'ikalus1988/misakanet', 'odiseo159-beep/mergit',
    'securebananalabs/bug-bounty', 'clankernation/openagents',
    'iii123iii/crystal-pdf', 'parabola-org/parabola',
}
AMOUNT = re.compile(r'(?:\$\s*(\d+(?:[.,]\d{1,2})?)|\b(\d+(?:[.,]\d{1,2})?)\s*(USD|EUR|GBP)\b|([€£])\s*(\d+(?:[.,]\d{1,2})?))', re.I)
SKILLS = re.compile(r'\b(csv|xlsx|excel|spreadsheet|python|javascript|typescript|api|telegram|docx|pdf|responsive|css|html|unit tests?|regression tests?|parser|parsing|data conversion|validation)\b', re.I)
COMPLEX = re.compile(r'\b(kernel|compiler|cryptograph\w*|solidity|smart contract|blockchain|webgl|gpu|kubernetes|medical|penetration|exploit|security vulnerability)\b', re.I)
RESTRICTED = re.compile(r'\b(no ai|ai[- ]generated.{0,35}(?:not allowed|prohibited|forbidden)|human.{0,25}(?:review required|must review)|us citizens? only|us[- ]only|must (?:reside|be located) in|on[- ]site|in[- ]person|testnet|test money|zero[- ]bounty|unfunded|amount tbd|deposit required)\b', re.I)
ALLOCATED = re.compile(r'\b(bounty (?:paid|claimed)|assigned to|proposal (?:accepted|selected)|already fixed|payment sent|no longer available|bounties (?:are )?(?:paused|discontinued))\b', re.I)
AI_ALLOWED = re.compile(r'\b(ai[- ]assisted (?:work|contributions?|development) (?:is |are )?(?:allowed|welcome|accepted)|ai (?:use |tools? )?(?:is |are )?(?:allowed|welcome|accepted)|ai[- ]generated (?:code|contributions?) (?:is |are )?(?:allowed|accepted))\b', re.I)
PAYMENT = re.compile(r'\b(paypal|wise|bank transfer|upwork|algora|opire|polar\.sh)\b', re.I)
DEADLINE = re.compile(r'\b(deadline|expires?|by \d{4}-\d{2}-\d{2})\b', re.I)
PR_LINK = re.compile(r'https://github\.com/[\w.-]+/[\w.-]+/pull/\d+', re.I)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def request_json(url, token=None, data=None, method=None):
    headers = {'User-Agent': 'github-bounty-notifier', 'Accept': 'application/vnd.github+json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, headers=headers,
        data=json.dumps(data).encode() if data is not None else None, method=method)
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            github_reason = ''
            if url.startswith(API + '/'):
                try:
                    api_message = json.loads(exc.read(4096)).get('message', '').lower()
                except (ValueError, OSError):
                    api_message = ''
                for needle, label in (
                    ('secondary rate limit', 'secondary rate limit'),
                    ('api rate limit exceeded', 'API rate limit exceeded'),
                    ('resource not accessible by integration', 'resource not accessible by integration'),
                    ('requires authentication', 'authentication required'),
                    ('bad credentials', 'bad credentials'),
                ):
                    if needle in api_message:
                        github_reason = label
                        break
            # Respect an explicit GitHub rate-limit reset. Retry only read-only
            # GitHub GETs, never Telegram sends or state writes.
            rate_limited = exc.code in (403, 429) and (
                exc.headers.get('X-RateLimit-Remaining') == '0' or exc.headers.get('Retry-After') or 'rate limit' in github_reason)
            if rate_limited and data is None and method in (None, 'GET') and url.startswith(API + '/'):
                try:
                    delay = int(exc.headers.get('Retry-After') or (60 if github_reason == 'secondary rate limit' else max(1, int(exc.headers.get('X-RateLimit-Reset', '0')) - int(time.time()) + 1)))
                except ValueError:
                    delay = 61
                if attempt == 0 and 0 < delay <= 60:
                    print('GitHub read rate limit: waiting for the server reset')
                    time.sleep(delay)
                    continue
                raise RuntimeError('GitHub API rate limit; scan incomplete, next scheduled run may retry') from None
            # Never include URLs, request headers, tokens or response bodies.
            raise RuntimeError(f'HTTP {exc.code}; {github_reason or "request failed"}; no automatic delivery retry') from None
        except (OSError, ValueError):
            raise RuntimeError('Network/JSON failure; request outcome may be unknown') from None



class GitHub:
    def __init__(self, token):
        self.token = token

    def get(self, path):
        return request_json(API + path, self.token)

    def pages(self, path, maximum=3):
        results = []
        for page in range(1, maximum + 1):
            batch = self.get(path + ('&' if '?' in path else '?') + f'per_page=100&page={page}')
            results.extend(batch)
            if len(batch) < 100:
                return results
        raise RuntimeError('Incomplete pagination; candidate cannot be verified')


class State:
    """CAS writes before delivery: an uncertain send is never blindly repeated."""
    def __init__(self, gh, repo, local=None):
        self.gh, self.repo, self.local, self.sha = gh, repo, local, None
        self.data = {'version': 1, 'notifications': {}}
        if local:
            if Path(local).exists():
                self.data = json.loads(Path(local).read_text())
        elif repo:
            # Repository starts with an explicit empty state file. A missing or
            # inaccessible file is a hard error, never silently reset deduplication.
            blob = gh.get(f'/repos/{repo}/contents/{STATE_PATH}')
            self.sha = blob['sha']
            self.data = json.loads(base64.b64decode(blob['content']))
        if self.data.get('version') != 1 or not isinstance(self.data.get('notifications'), dict):
            raise RuntimeError('Invalid state: refusing to reset notification history')

    def save(self):
        encoded = json.dumps(self.data, ensure_ascii=False, indent=2) + '\n'
        if self.local:
            path = Path(self.local)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(encoded)
            temporary.replace(path)
        else:
            result = request_json(API + f'/repos/{self.repo}/contents/{STATE_PATH}', self.gh.token, {
                'message': 'Record notification delivery state [skip ci]',
                'sha': self.sha, 'content': base64.b64encode(encoded.encode()).decode(),
            }, method='PUT')
            self.sha = result['content']['sha']


def reward(text):
    matches = list(AMOUNT.finditer(text))
    # Ambiguous multiple prices / ranges require manual review.
    if len(matches) != 1:
        return None
    m = matches[0]
    value = float((m[1] or m[2] or m[5]).replace(',', '.'))
    currency = m[3].upper() if m[3] else {'€': 'EUR', '£': 'GBP'}.get(m[4], 'USD')
    return (value, currency) if 10 <= value <= 750 else None


def preliminary(issue, repo):
    text = issue.get('title', '') + '\n' + (issue.get('body') or '')
    if repo.lower() in EXCLUDED_REPOS or issue.get('state') != 'open' or issue.get('assignees') or issue.get('pull_request'):
        return None
    # Only original poster's explicit offered reward counts, never applicant quotes.
    # Exclude requests asking someone else to fund a bounty.
    if not re.search(r'\b(bounty|reward|will pay|paid task)\b', text, re.I):
        return None
    if re.search(r'\b(seeking|requesting|suggest|please (?:add|fund))\b.{0,35}\bbounty\b', text, re.I):
        return None
    amount = reward(text)
    if not amount or not SKILLS.search(text) or COMPLEX.search(text) or RESTRICTED.search(text) or ALLOCATED.search(text) or DEADLINE.search(text):
        return None
    if not 80 <= len(text) <= 14000 or issue.get('comments', 0) > 12:
        return None
    # Explicit acceptance criteria/scope makes a candidate materially stronger.
    if not re.search(r'\b(acceptance|expected|reproduce|requirements?|tests? pass|deliverables?|steps to)\b', text, re.I):
        return None
    return amount


def assess(gh, item):
    parts = urllib.parse.urlparse(item['html_url']).path.strip('/').split('/')
    repo, number = '/'.join(parts[:2]), int(parts[3])
    if repo.lower() in EXCLUDED_REPOS:
        return None
    issue = gh.get(f'/repos/{repo}/issues/{number}')
    amount = preliminary(issue, repo)
    if not amount:
        return None
    metadata = gh.get(f'/repos/{repo}')
    if metadata.get('archived') or metadata.get('disabled') or metadata.get('fork'):
        return None
    # Reject disposable/spam repositories; this is a heuristic, not funding proof.
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(metadata['created_at'].replace('Z', '+00:00'))
    if age.days < 30 or metadata.get('stargazers_count', 0) < 3:
        return None
    comments = gh.pages(f'/repos/{repo}/issues/{number}/comments')
    owner_logins = {metadata['owner']['login'].lower(), issue['user']['login'].lower()}
    authoritative = [c for c in comments if c.get('author_association') in ('OWNER', 'MEMBER') or c['user']['login'].lower() in owner_logins]
    original = issue['title'] + '\n' + (issue.get('body') or '')
    terms = original + '\n' + '\n'.join(c.get('body') or '' for c in authoritative)
    all_comments = '\n'.join(c.get('body') or '' for c in comments)
    if RESTRICTED.search(terms) or ALLOCATED.search(terms) or PR_LINK.search(all_comments) or PR_LINK.search(original):
        return None
    # Timeline includes linked/cross-referenced PRs, including closed solutions.
    timeline = gh.pages(f'/repos/{repo}/issues/{number}/timeline')
    if any(e.get('source', {}).get('issue', {}).get('pull_request') or e.get('event') in ('connected', 'closed', 'assigned') for e in timeline):
        return None
    prs = gh.get('/search/issues?' + urllib.parse.urlencode({'q': f'repo:{repo} is:pr {number}', 'per_page': 20}))
    if prs.get('incomplete_results') or prs.get('total_count', 0):
        return None
    # Read contributor rules without downloading or executing repository code.
    rules = []
    for path in ('CONTRIBUTING.md', '.github/CONTRIBUTING.md', 'BOUNTY.md', 'BOUNTIES.md'):
        try:
            entry = gh.get(f'/repos/{repo}/contents/{path}')
        except RuntimeError as exc:
            if str(exc).startswith('HTTP 404;'):
                continue
            raise
        if entry.get('encoding') == 'base64':
            rules.append(base64.b64decode(entry['content']).decode('utf-8', 'replace'))
    terms += '\n' + '\n'.join(rules)
    if RESTRICTED.search(terms) or ALLOCATED.search(terms):
        return None
    # Avoid turning unknown AI policy/payment into a supposed eligible assignment.
    # Both must be explicit for automated Telegram delivery. Other cases go to
    # the local run report for manual assistant review, never a "verified job".
    if not AI_ALLOWED.search(terms) or not PAYMENT.search(terms):
        return None
    # Last-moment state refresh before considering a notification.
    fresh = gh.get(f'/repos/{repo}/issues/{number}')
    if fresh.get('state') != 'open' or fresh.get('assignees') or fresh.get('updated_at') != issue.get('updated_at'):
        return None
    return {'url': issue['html_url'], 'title': issue['title'], 'amount': amount[0], 'currency': amount[1],
            'checked_at': now(), 'comments': len(comments), 'updated_at': issue['updated_at'],
            'kind': 'rule_filtered_candidate', 'difficulty': 'Kis/közepesnek előszűrt; kódszintű becslés szükséges'}


def message(candidate):
    # Plain text only; never echo arbitrary issue body or instructions.
    title = ''.join(c for c in candidate['title'] if c.isprintable())[:160]
    return (f"🔎 Új GitHub-bounty jelölt: {candidate['amount']:g} {candidate['currency']}\n"
        f"{title}\n{candidate['url']}\n\n"
        "Automata szabályokkal előszűrt jelölt, nem elfogadott megbízás. "
        "Nyitott, nincs kiosztva; az ellenőrzött GitHub-adatokban nem talált kapcsolódó PR-t. "
        "A leírásban van releváns technológia, összeg, AI-engedély és fizetési csatorna.\n"
        "Nehézség: kis/közepesnek előszűrt, kódszinten még ellenőrizendő. "
        "A finanszírozás, magyarországi jogosultság, kifizetési határidő és a teljesíthetőség nincs igazolva.\n"
        "Következő lépés: küldd ide az issue-linket; ellenőrizzük a kódot és a karbantartónál az ár/fizetés/AI és foglalás feltételeit. "
        "Ne kezdj fizetetlen munkába pusztán ettől az értesítéstől.")


def deliver(state, candidate, send):
    key = candidate['url']
    if key in state.data['notifications']:
        return False
    entry = {'status': 'pending', 'created_at': now(), 'candidate': candidate}
    state.data['notifications'][key] = entry
    state.save()  # If this fails, nothing is sent.
    response = send(message(candidate))
    if response.get('ok') is not True or not isinstance(response.get('result', {}).get('message_id'), int):
        raise RuntimeError('Telegram send not confirmed; kept pending, no blind retry')
    entry.update(status='sent', message_id=response['result']['message_id'], sent_at=now())
    state.save()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--local-state')
    args = parser.parse_args()
    token = os.environ.get('GITHUB_TOKEN')
    telegram = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    repo = os.environ.get('GITHUB_REPOSITORY')
    if not args.dry_run and (not token or not telegram or not repo or hashlib.sha256(chat_id.encode()).hexdigest() != CHAT_ID_SHA256):
        raise RuntimeError('Missing runtime secrets or repository; sending disabled')
    gh = GitHub(token)
    state = State(gh, repo, args.local_state) if not args.dry_run else None
    seen, candidates, failures = set(), [], 0
    for query in QUERIES:
        result = gh.get('/search/issues?' + urllib.parse.urlencode({'q': query, 'per_page': 20}))
        if result.get('incomplete_results'):
            raise RuntimeError('Incomplete GitHub search; refusing to report a complete scan')
        for item in result.get('items', []):
            url = item['html_url']
            if url in seen or (state and url in state.data['notifications']):
                continue
            seen.add(url)
            try:
                candidate = assess(gh, item)
            except RuntimeError:
                failures += 1
                continue
            if candidate:
                candidates.append(candidate)
    report = {'checked_at': now(), 'discovered': len(seen), 'candidates': candidates, 'candidate_check_errors': failures, 'dry_run': args.dry_run}
    Path('run-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    sent = 0
    if not args.dry_run:
        for candidate in candidates[:3]:
            sent += deliver(state, candidate, lambda text: request_json(
                'https://api.telegram.org/bot' + telegram + '/sendMessage', data={
                    'chat_id': chat_id, 'text': text, 'link_preview_options': {'is_disabled': True}}))
    print(json.dumps({'discovered': len(seen), 'matching_candidates': len(candidates), 'sent': sent, 'candidate_check_errors': failures, 'dry_run': args.dry_run}))
    if failures:
        raise RuntimeError('Some candidates could not be verified; see error count')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Deliberately never dump tracebacks or URLs containing credentials.
        print('Notifier failed: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
