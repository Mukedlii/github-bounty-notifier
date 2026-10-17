import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import notifier as n
import worker


def item(**changes):
    issue={'html_url':'https://github.com/example/repo/issues/1', 'title':'Bounty $5000', 'body':'Add a Python API timeout and unit test.', 'state':'open', 'labels':[], 'assignees':[], 'comments':0, 'created_at':'2026-10-17T12:00:00Z', 'updated_at':'2026-10-17T14:00:00Z'}
    issue.update(changes)
    return issue


class BroadWatcherTests(unittest.TestCase):
    def test_rewards_no_cap_and_duplicate_amounts(self):
        self.assertEqual(n.reward('$5,000 / $5,000'),[(5000,'USD')])
        self.assertEqual(n.reward('100 NIS and 0.5 BTC'),[(100,'NIS'),(0.5,'BTC')])
        self.assertEqual(n.reward('$0'),[])

    def test_excludes_ai_ban_hardware_assignee_and_crowds(self):
        for changes in [dict(body='No AI allowed. Python API'), dict(body='RX 7900 GPU Python'), dict(assignees=[{'login':'claimed'}]), dict(comments=31)]:
            self.assertFalse(n.preliminary(item(**changes), 'example/repo'))

    def test_costs_and_aggregators_are_not_jobs(self):
        self.assertFalse(n.preliminary(item(title='Infra costs 100 USD',body='Storage budget'), 'x/y'))
        self.assertFalse(n.preliminary(item(title='Bounty ledger: claims and payouts'), 'x/y'))

    def test_policy_and_linked_pr_exclusions(self):
        gh=Mock(); gh._policy_cache={}; gh.get.side_effect=[item(), [], []]
        self.assertIsNotNone(n.assess(gh,item()))
        gh=Mock(); gh._policy_cache={}; gh.get.side_effect=[item(), [], [{'source':{'issue':{'pull_request':{'url':'https://api.github.com/repos/x/y/pulls/1'}}}}]]
        self.assertIsNone(n.assess(gh,item()))
        gh=Mock(); gh.get.return_value=item(comments=1)
        gh._policy_cache={'example/repo':'We require human code review by the contributor.'}
        self.assertIsNone(n.assess(gh,item()))

    def test_opening_date_is_budapest_and_not_update_date(self):
        text=n.message({'url':item()['html_url'],'title':'Python fix','amount':75,'currency':'USD','created_at':'2026-10-17T12:00:00Z'})
        self.assertIn('2026.10.17. 14:00',text)
        self.assertIn('Issue megnyitva',text)

    def test_closed_test_money_and_bounty_request_not_paid_jobs(self):
        self.assertFalse(n.preliminary(item(state='closed'),'x/y'))
        self.assertFalse(n.preliminary(item(body='testnet ETH funds'),'x/y'))
        self.assertFalse(n.preliminary(item(body='Please fund a bounty'),'x/y'))

    def test_mirror_resolves_closed_original(self):
        gh=Mock();gh.get.side_effect=[item(body='Source URL\nhttps://github.com/real/project/issues/2'),item(state='closed')]
        self.assertIsNone(n.assess(gh,item()))

    def test_pending_batch_never_repeated_after_unknown_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'state.json');state=n.State(None,None,path)
            candidate={'url':item()['html_url'],'title':'Bounty','amount':5,'currency':'USD','flags':[]}
            send=Mock(side_effect=RuntimeError('unknown'))
            with self.assertRaises(RuntimeError): n.deliver_batch(state,[candidate],send)
            self.assertEqual(n.deliver_batch(n.State(None,None,path),[candidate],send),0)
            self.assertEqual(send.call_count,1)

    def test_batch_success_persists_message_id(self):
        with tempfile.TemporaryDirectory() as d:
            state=n.State(None,None,str(Path(d)/'state.json'))
            c={'url':item()['html_url'],'title':'Bounty','amount':1,'currency':'USD','flags':[]}
            self.assertEqual(n.deliver_batch(state,[c],lambda _: {'ok':True,'result':{'message_id':9}}),1)
            self.assertEqual(state.data['notifications'][c['url']]['message_id'],9)

    def test_save_failure_prevents_send(self):
        state=Mock();state.data={'notifications':{}};state.save.side_effect=RuntimeError('failed')
        send=Mock()
        with self.assertRaises(RuntimeError): n.deliver_batch(state,[{'url':'https://github.com/x/y/issues/1'}],send)
        send.assert_not_called()

    def test_dispatch_204_and_unknown_not_retried(self):
        response=Mock();response.status=204
        with patch('worker.urllib.request.urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value=response
            worker.dispatch_successor('FAKE','example/repo')
            self.assertEqual(urlopen.call_count,1)
        with patch('worker.urllib.request.urlopen',side_effect=OSError('unknown')) as urlopen:
            with self.assertRaises(RuntimeError): worker.dispatch_successor('FAKE','example/repo')
            self.assertEqual(urlopen.call_count,1)

    def test_private_repo_worker_refuses_run(self):
        with patch.dict('os.environ',{'GITHUB_TOKEN':'FAKE','GITHUB_REPOSITORY':'example/repo'}), patch('worker.GitHub') as gh:
            gh.return_value.get.return_value={'private':True}
            with self.assertRaises(RuntimeError): worker.run_worker()

    def test_worker_runs_five_minute_slots_and_one_successor(self):
        clock = {'t': 0}
        def sleep(seconds): clock['t'] += seconds
        env={'GITHUB_TOKEN':'FAKE','GITHUB_REPOSITORY':'example/repo','FOLLOW_UP':'true','DRY_RUN':'false','ENABLE_SCHEDULE':'true'}
        with patch.dict('os.environ',env), patch('worker.GitHub') as gh, patch('worker.subprocess.run') as run, patch('worker.time.monotonic',side_effect=lambda:clock['t']), patch('worker.time.sleep',side_effect=sleep), patch('worker.dispatch_successor') as successor:
            gh.return_value.get.return_value={'private':False}
            run.return_value.returncode=0
            worker.run_worker()
            self.assertEqual(run.call_count,3)
            self.assertEqual(clock['t'],900)
            successor.assert_called_once_with('FAKE','example/repo')

    def test_disabled_schedule_does_not_chain(self):
        env={'GITHUB_TOKEN':'FAKE','GITHUB_REPOSITORY':'example/repo','FOLLOW_UP':'true','DRY_RUN':'false','ENABLE_SCHEDULE':'false'}
        with patch.dict('os.environ',env), patch('worker.GitHub') as gh, patch('worker.subprocess.run') as run, patch('worker.dispatch_successor') as successor:
            gh.return_value.get.return_value={'private':False}
            run.return_value.returncode=0
            worker.run_worker()
            self.assertEqual(run.call_count,1)
            successor.assert_not_called()

    def test_telegram_preflight(self):
        with patch('notifier.request_json',side_effect=[{'ok':True,'result':{'username':'Github_ertesito_bot'}},{'ok':True,'result':{'id':123,'type':'private'}}]):
            n.verify_telegram('FAKE','123')

if __name__=='__main__': unittest.main()
