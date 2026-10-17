import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import notifier as n


class NotifierTests(unittest.TestCase):
    def issue(self, **updates):
        value = {'state': 'open', 'assignees': [], 'comments': 0, 'title': 'CSV parser bounty $50',
            'body': 'Fix CSV validation in Python. Acceptance requirements: add regression tests for quoted values and ensure tests pass.'}
        value.update(updates)
        return value

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
