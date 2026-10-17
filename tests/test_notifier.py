import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import notifier as n
import urllib.error
from email.message import Message


class NotifierTests(unittest.TestCase):
    def issue(self, **updates):
        value = {'state': 'open', 'assignees': [], 'comments': 0, 'title': 'CSV parser bounty $50',
            'body': 'Fix CSV validation in Python. Acceptance requirements: add regression tests for quoted values and ensure tests pass.'}
        value.update(updates)
        return value

    def test_rate_limit_only_retries_read_only_github(self):
        headers = Message()
        headers['X-RateLimit-Remaining'] = '0'
        headers['Retry-After'] = '1'
        error = urllib.error.HTTPError('https://api.github.com/search/issues', 403, 'rate limited', headers, None)
        with patch('notifier.urllib.request.urlopen', side_effect=error) as request, patch('notifier.time.sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'rate limit'):
                n.request_json('https://api.github.com/search/issues')
            self.assertEqual(request.call_count, 2)
            sleep.assert_called_once_with(1)
        with patch('notifier.urllib.request.urlopen', side_effect=error) as request, patch('notifier.time.sleep') as sleep:
            with self.assertRaises(RuntimeError):
                n.request_json('https://api.telegram.org/botFAKE/sendMessage', data={'text': 'test'})
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()

    def test_telegram_identity_and_private_recipient(self):
        me = {'ok': True, 'result': {'username': 'Github_ertesito_bot'}}
        chat = {'ok': True, 'result': {'id': 5080373675, 'type': 'private'}}
        with patch('notifier.request_json', side_effect=[me, chat]):
            n.verify_telegram('FAKE', '5080373675')
        with patch('notifier.request_json', return_value={'ok': False}):
            with self.assertRaises(RuntimeError):
                n.verify_telegram('FAKE', '5080373675')
        wrong_chat = {'ok': True, 'result': {'id': 999, 'type': 'group'}}
        with patch('notifier.request_json', side_effect=[me, wrong_chat]):
            with self.assertRaises(RuntimeError):
                n.verify_telegram('FAKE', '5080373675')

    def test_reward(self):
        self.assertEqual(n.reward('bounty $175'), (175, 'USD'))
        self.assertEqual(n.reward('Reward 45 EUR'), (45, 'EUR'))
        self.assertIsNone(n.reward('zero bounty $0'))
        self.assertIsNone(n.reward('$50 to $75'))
        self.assertIsNone(n.reward('$5000'))

    def test_real_scope_only(self):
        self.assertTrue(n.preliminary(self.issue(), 'example/project'))
        for updates in ({'state': 'closed'}, {'assignees': [{}]}, {'comments': 100},
            {'body': 'Please fund a bounty. Python requirements: parser'},
            {'body': 'Python kernel acceptance tests'}, {'body': 'CSV tests. AI-generated code prohibited'},
            {'body': 'CSV tests. testnet funds only'}, {'body': 'CSV acceptance. US citizens only'}):
            self.assertIsNone(n.preliminary(self.issue(**updates), 'example/project'))
        self.assertIsNone(n.preliminary(self.issue(), 'oll4com/spaceapp'))

    def test_no_amount_from_applicant(self):
        self.assertIsNone(n.preliminary(self.issue(title='CSV parser bounty', body='Python validation requirements: fix quoted values.'), 'example/project'))

    def fake_github(self, *, policy=True, comments=None, timeline=None, pr_count=0, changed=False):
        issue = self.issue(html_url='https://github.com/example/project/issues/1', user={'login': 'sponsor'}, updated_at='2026-10-17T08:00:00Z')
        if policy:
            issue['body'] += ' AI-assisted work is allowed. Payment via PayPal.'
        calls = [0]
        gh = Mock()
        def get(path):
            if path == '/repos/example/project/issues/1':
                calls[0] += 1
                value = dict(issue)
                if changed and calls[0] > 1:
                    value['state'] = 'closed'
                return value
            if path == '/repos/example/project':
                return {'owner': {'login': 'maintainer'}, 'created_at': '2020-01-01T00:00:00Z', 'stargazers_count': 10}
            if path.startswith('/search/issues?'):
                return {'total_count': pr_count, 'incomplete_results': False}
            raise RuntimeError('HTTP 404; not found')
        gh.get.side_effect = get
        gh.pages.side_effect = lambda path: (comments or []) if path.endswith('/comments') else (timeline or [])
        return gh

    def test_assess_requires_ai_and_payment_terms(self):
        item = {'html_url': 'https://github.com/example/project/issues/1'}
        self.assertIsNotNone(n.assess(self.fake_github(), item))
        self.assertIsNone(n.assess(self.fake_github(policy=False), item))
        applicant = {'author_association': 'NONE', 'user': {'login': 'applicant'}, 'body': 'AI is allowed. Payment via Wise.'}
        self.assertIsNone(n.assess(self.fake_github(policy=False, comments=[applicant]), item))

    def test_assess_rejects_competition_and_owner_allocation(self):
        item = {'html_url': 'https://github.com/example/project/issues/1'}
        owner = {'author_association': 'OWNER', 'user': {'login': 'maintainer'}, 'body': 'Proposal accepted, assigned to another contributor.'}
        self.assertIsNone(n.assess(self.fake_github(comments=[owner]), item))
        self.assertIsNone(n.assess(self.fake_github(pr_count=1), item))
        self.assertIsNone(n.assess(self.fake_github(timeline=[{'source': {'issue': {'pull_request': { 'url': 'PR'}}}}]), item))
        self.assertIsNone(n.assess(self.fake_github(changed=True), item))

    def test_uncertain_send_never_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'state.json')
            state = n.State(None, None, path)
            candidate = {'url': 'https://github.com/example/project/issues/1', 'title': 'CSV fix', 'amount': 50, 'currency': 'USD'}
            send = Mock(side_effect=RuntimeError('unknown'))
            with self.assertRaises(RuntimeError):
                n.deliver(state, candidate, send)
            self.assertEqual(json.loads(Path(path).read_text())['notifications'][candidate['url']]['status'], 'pending')
            self.assertFalse(n.deliver(n.State(None, None, path), candidate, send))
            self.assertEqual(send.call_count, 1)

    def test_success_recorded_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'state.json')
            state = n.State(None, None, path)
            candidate = {'url': 'https://github.com/example/project/issues/2', 'title': 'CSV fix', 'amount': 50, 'currency': 'USD'}
            send = Mock(return_value={'ok': True, 'result': {'message_id': 123}})
            self.assertTrue(n.deliver(state, candidate, send))
            self.assertFalse(n.deliver(n.State(None, None, path), candidate, send))
            self.assertEqual(send.call_count, 1)
            self.assertEqual(state.data['notifications'][candidate['url']]['message_id'], 123)

    def test_no_send_when_state_save_fails(self):
        state = n.State(None, None, 'nonexistent-state-for-test.json')
        state.save = Mock(side_effect=RuntimeError('write failed'))
        send = Mock()
        with self.assertRaises(RuntimeError):
            n.deliver(state, {'url': 'https://github.com/example/project/issues/3'}, send)
        send.assert_not_called()

    def test_unconfirmed_response_stays_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            state = n.State(None, None, str(Path(directory) / 'state.json'))
            candidate = {'url': 'https://github.com/example/project/issues/4', 'title': 'Fix', 'amount': 20, 'currency': 'EUR'}
            with self.assertRaises(RuntimeError):
                n.deliver(state, candidate, lambda _: {'ok': False})
            self.assertEqual(state.data['notifications'][candidate['url']]['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
