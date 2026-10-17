"""Broad advertised-money GitHub bounty discovery; Python standard library only."""
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
from zoneinfo import ZoneInfo

API = 'https://api.github.com'
STATE_PATH = 'state/notifications.json'
CHAT_ID_SHA256 = '2d0a470453be09af1c50fb6cb36dfc4eb167ced3f3f63edeb11e99ce23a0fa39'
QUERIES = [
    'is:issue is:open label:bounty',
    'is:issue is:open in:title bounty',
    'is:issue is:open in:body "/bounty"',
    'repo:Expensify/App is:issue is:open label:HelpWanted',
    'is:issue is:open in:title "USD"',
]
AMOUNT = re.compile(r'(?:([$€£])\s*(\d+(?:[,.]\d+)*)|\b(\d+(?:[,.]\d+)*)\s*(USD|EUR|GBP|NIS|ILS|USDC|USDT|BTC|sats?)\b)', re.I)
FAKE_MONEY = re.compile(r'\b(testnet (?:eth|tokens?|funds)|test money|zero[- ]bounty|no (?:cash|monetary) reward)\b', re.I)
PR_LINK = re.compile(r'https://github\.com/[\w.-]+/[\w.-]+/pull/\d+', re.I)
SOURCE_LINK = re.compile(r'(?:Source URL|Original (?:issue|URL)|原始链接)[^\n]*\n+(https://github\.com/[\w.-]+/[\w.-]+/issues/\d+)', re.I)


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
    values = []
    for match in AMOUNT.finditer(text):
        raw = match[2] or match[3]
        if ',' in raw and '.' in raw:
            raw = raw.replace(',', '') if raw.rfind('.') > raw.rfind(',') else raw.replace('.', '').replace(',', '.')
        elif ',' in raw:
            raw = raw.replace(',', '') if len(raw.rsplit(',', 1)[1]) == 3 else raw.replace(',', '.')
        try:
            value = float(raw)
        except ValueError:
            continue
        currency = {'$': 'USD', '€': 'EUR', '£': 'GBP'}.get(match[1], (match[4] or '').upper())
        if value > 0 and (value, currency) not in values:
            values.append((value, currency))
    return values


AI_BLOCK = re.compile(r'no\s+ai\b|(?:do\s+not|don.t)\s+accept\s+ai|ai[- ](?:generated|assisted).{0,60}(?:not\s+allowed|prohibited|forbidden|not\s+accepted)|(?:human|manual).{0,35}(?:written|technical\s+review|code\s+review)|review.{0,30}(?:yourself|human)|own.{0,20}review', re.I | re.S)
UNSUITABLE = re.compile(r'RX\s*7900|RTX\s*2080|GPU\s+(?:kernel|hardware)|physical\s+(?:device|hardware)|real\s+(?:iphone|android\s+device)|FPGA|CUDA|consensus\s+(?:liveness|protocol)|(?:security|smart\s+contract)\s+audit', re.I)
SCOPE = re.compile(r'\b(?:python|javascript|typescript|bash|shell|script|csv|xlsx|excel|docx|pdf|api|webhook|telegram|discord|bot|responsive|css|html|react|next\.js|frontend|landing\s+page|timeout|unit\s+test|regression\s+test|ci|workflow|wasm|changelog|documentation)\b', re.I)
AGGREGATOR = re.compile(r'\[radar\]|bounty\s+(?:alert|ledger|eligibility)|paid\s+work\s+lead|bounty\s+discovery|\d+\s+open\s+bounties|revenue\s+command|(?:fund|post).{0,30}(?:first|real).{0,15}(?:task|bounty)', re.I)


def preliminary(issue, repo):
    text = issue.get('title', '') + '\n' + (issue.get('body') or '')
    labels = ' '.join(x.get('name', '') if isinstance(x, dict) else str(x) for x in issue.get('labels', []))
    if issue.get('state') != 'open' or issue.get('pull_request') or FAKE_MONEY.search(text):
        return []
    if re.search(r'(?:bounty requested|please (?:add|fund).{0,30}bounty|would like a bounty attached|proposed bounty|testnet bounty)', text, re.I):
        return []
    # A cost budget or token price is not a reward for solving this issue.
    if not re.search(r'bounty|reward|will pay|helpwanted|\[\s*[$€£]', text + labels, re.I):
        return []
    if AGGREGATOR.search(issue.get('title', '')) or AI_BLOCK.search(text) or UNSUITABLE.search(text):
        return []
    if issue.get('assignees') or issue.get('comments', 0) > 30 or not SCOPE.search(text):
        return []
    return reward(text)


def repository_policy(gh, repo):
    cache = getattr(gh, '_policy_cache', None)
    if cache is None:
        cache = gh._policy_cache = {}
    if repo not in cache:
        root = gh.get(f'/repos/{repo}/contents/')
        files = [x for x in root if x.get('type') == 'file' and re.match(r'^(?:readme|contributing|agents)(?:\.|$)', x.get('name', ''), re.I)]
        github_dir = next((x for x in root if x.get('type') == 'dir' and x.get('name') == '.github'), None)
        if github_dir:
            files += [x for x in gh.get(f'/repos/{repo}/contents/.github') if x.get('type') == 'file' and re.match(r'^(?:contributing|agents)(?:\.|$)', x.get('name', ''), re.I)]
        parts = []
        for entry in files[:8]:
            blob = gh.get(f'/repos/{repo}/contents/{entry["path"]}')
            if blob.get('encoding') != 'base64' or blob.get('size', 0) > 200000:
                raise RuntimeError('Repository policy could not be fully checked')
            parts.append(base64.b64decode(blob['content']).decode('utf-8'))
        cache[repo] = '\n'.join(parts)
    return cache[repo]


def assess(gh, item):
    # Search responses are discovery only. Re-fetch the canonical live issue.
    parts = urllib.parse.urlparse(item['html_url']).path.strip('/').split('/')
    repo, number = '/'.join(parts[:2]), int(parts[3])
    if not preliminary(item, repo):
        return None
    issue = gh.get(f'/repos/{repo}/issues/{number}')
    mirror = SOURCE_LINK.search(issue.get('body') or '')
    if mirror:
        parts = urllib.parse.urlparse(mirror[1]).path.strip('/').split('/')
        repo, number = '/'.join(parts[:2]), int(parts[3])
        issue = gh.get(f'/repos/{repo}/issues/{number}')
    amounts = preliminary(issue, repo)
    if not amounts:
        return None
    policy = repository_policy(gh, repo)
    if AI_BLOCK.search(policy):
        return None
    flags = []
    text = issue.get('title', '') + '\n' + (issue.get('body') or '')
    flags.append('AI-munkamód és kifizetés karbantartói megerősítése szükséges')
    recent = []
    total = issue.get('comments', 0)
    if total:
        page = max(1, (total + 99) // 100)
        recent = gh.get(f'/repos/{repo}/issues/{number}/comments?per_page=100&page={page}')
    discussion = '\n'.join(c.get('body') or '' for c in recent)
    # Avoid duplicating submitted work, claims, or completed solutions.
    if re.search(r'(?:^|\n)\s*/claim\b|bounty paid|payment sent|already fixed|proposal (?:accepted|selected)|(?:fix|resolve[ds]?|close[ds]?)\s+(?:in\s+)?PR\s*#\d+|PR\s*#\d+', discussion, re.I):
        return None
    if AI_BLOCK.search(discussion):
        return None
    timeline = gh.get(f'/repos/{repo}/issues/{number}/timeline?per_page=100')
    if len(timeline) >= 100 or any(e.get('source', {}).get('issue', {}).get('pull_request') for e in timeline):
        return None
    prs = list(dict.fromkeys(PR_LINK.findall(text + '\n' + discussion)))
    if prs:
        return None
    elif total:
        flags.append(f'{total} hozzászólás; versenyhelyzet ellenőrizendő')
    if re.search(r'bounty paid|payment sent|already fixed|proposal (?:accepted|selected)', discussion, re.I):
        flags.append('Korábbi teljesítés/kifizetés is szerepel a beszélgetésben')
    return {'url': issue['html_url'], 'title': issue['title'], 'rewards': amounts,
            'amount': amounts[0][0], 'currency': amounts[0][1], 'checked_at': now(),
            'created_at': issue.get('created_at'), 'updated_at': issue['updated_at'], 'comments': total, 'flags': flags,
            'kind': 'potential_ai_task', 'pull_requests': [], 'assessment': 'Szabályalapú előszűrés; megoldhatóság nem garantált'}


def message(candidate):
    title = ''.join(c for c in candidate['title'] if c.isprintable())[:130]
    amounts = candidate.get('rewards') or [(candidate['amount'], candidate['currency'])]
    money = ' / '.join(f'{v:g} {currency}' for v, currency in amounts[:4])
    flags = '; '.join(candidate.get('flags', []))
    created = candidate.get('created_at')
    opened = dt.datetime.fromisoformat(created.replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Budapest')).strftime('%Y.%m.%d. %H:%M') if created else 'nem ismert'
    return f"💰 {money} — {title}\n📅 Issue megnyitva: {opened} (Budapest)\n{candidate['url']}\nAI-val lehetséges feladat, részletes ellenőrzés szükséges.\n{flags}".strip()


def deliver_batch(state, candidates, send):
    candidates = [c for c in candidates if c['url'] not in state.data['notifications']]
    if not candidates:
        return 0
    timestamp = now()
    for candidate in candidates:
        state.data['notifications'][candidate['url']] = {'status': 'pending', 'created_at': timestamp, 'candidate': candidate}
    state.save()
    text = 'Új, AI-val lehetséges pénzdíjas feladatok:\n\n' + '\n\n'.join(message(c) for c in candidates)
    text += '\n\nKiírt díj, nem garantált kifizetés. A nehézséget, AI-engedélyt, jogosultságot és fizetési módot munkakezdés előtt külön ellenőrizzük.'
    if len(text) > 4000:
        raise RuntimeError('Notification batch too long; kept pending without sending')
    response = send(text)
    if response.get('ok') is not True or not isinstance(response.get('result', {}).get('message_id'), int):
        raise RuntimeError('Telegram send not confirmed; kept pending, no blind retry')
    for candidate in candidates:
        state.data['notifications'][candidate['url']].update(status='sent', message_id=response['result']['message_id'], sent_at=now())
    state.save()
    return len(candidates)


def verify_telegram(token, chat_id):
    base = 'https://api.telegram.org/bot' + token + '/'
    me = request_json(base + 'getMe')
    if me.get('ok') is not True or me.get('result', {}).get('username', '').lower() != 'github_ertesito_bot':
        raise RuntimeError('Telegram bot identity not verified')
    chat = request_json(base + 'getChat', data={'chat_id': chat_id})
    result = chat.get('result', {})
    if chat.get('ok') is not True or str(result.get('id')) != chat_id or result.get('type') != 'private':
        raise RuntimeError('Telegram private recipient not verified')


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
    if not args.dry_run:
        verify_telegram(telegram, chat_id)
    gh = GitHub(token)
    state = State(gh, repo, args.local_state) if not args.dry_run else None
    seen, canonical, candidates, failures = set(), set(), [], 0
    for query in QUERIES:
        result = gh.get('/search/issues?' + urllib.parse.urlencode({'q': query, 'sort': 'updated', 'order': 'desc', 'per_page': 50}))
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
            if candidate and candidate['url'] not in canonical and (not state or candidate['url'] not in state.data['notifications']):
                canonical.add(candidate['url'])
                candidates.append(candidate)
    report = {'checked_at': now(), 'discovered': len(seen), 'candidates': candidates, 'candidate_check_errors': failures, 'dry_run': args.dry_run}
    Path('run-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    sent = 0
    if not args.dry_run:
        for offset in range(0, len(candidates), 5):
            sent += deliver_batch(state, candidates[offset:offset + 5], lambda text: request_json(
                'https://api.telegram.org/bot' + telegram + '/sendMessage', data={
                    'chat_id': chat_id, 'text': text, 'link_preview_options': {'is_disabled': True}}))
            time.sleep(1)  # Respect Telegram per-chat delivery rate.
    if not args.dry_run:
        state.data['last_scan_at'] = now()
        state.data['last_scan'] = {'discovered': len(seen), 'matches': len(candidates), 'sent': sent, 'check_errors': failures}
        if not failures:
            state.data['last_success_at'] = now()
        state.save()
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
