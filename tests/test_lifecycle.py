from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
from seatwatch import control, lifecycle, worker_client, collector
from seatwatch.session import atomic_json, valid_job


class LifecycleTests(unittest.TestCase):
    def test_focus_login_window_does_not_wait_for_hidden_main_content(self):
        session=Mock()
        session.context.new_cdp_session.return_value.send.return_value={'bounds':{'windowState':'normal'}}
        session.status.return_value={'state':'LOGIN_REQUIRED'}
        with patch.object(collector,'snapshot',side_effect=AssertionError('Login hides main content')):
            self.assertEqual(collector.run(session,{'step':'focus_browser'})['state'],'LOGIN_REQUIRED')

    def test_recent_status_is_not_proof_a_worker_is_alive(self):
        recent={'state':'SEARCH_UNVERIFIED','observed_at':datetime.now(timezone.utc).isoformat()}
        with patch.object(worker_client,'read_status',return_value=recent):
            self.assertFalse(worker_client.healthy())

    def test_pid_reuse_does_not_match_worker_identity(self):
        with patch.object(lifecycle,'process_stamp',return_value='new-creation-time'):
            self.assertFalse(lifecycle.worker_alive({'pid':42,'process_stamp':'old-creation-time','worker_id':'old'}))

    def test_busy_worker_does_not_expire_just_because_heartbeat_is_old(self):
        row={'state':'BUSY','pid':42,'process_stamp':'same','worker_id':'one','observed_at':'2000-01-01T00:00:00Z'}
        with patch.object(lifecycle,'process_stamp',return_value='same'), patch.object(lifecycle,'port_open',return_value=True):
            self.assertTrue(lifecycle.worker_connected(row))

    def test_close_during_wait_returns_immediately_and_removes_queued_request(self):
        row={'state':'BUSY','worker_id':'one'}
        with tempfile.TemporaryDirectory() as directory, patch.object(control,'ROOT',Path(directory)), \
             patch.object(control,'read_status',return_value=row), \
             patch.object(control,'worker_connected',side_effect=[True,False]):
            started=time.monotonic()
            with self.assertRaises(lifecycle.BrowserClosed):
                control._command('query_condition',timeout=180)
            self.assertLess(time.monotonic()-started,1)
            self.assertFalse((Path(directory)/'work'/'command.json').exists())

    def test_replacement_worker_never_accepts_old_or_expired_jobs(self):
        self.assertFalse(valid_job({'worker_id':'old','expires_at':time.time()+100},'new'))
        self.assertFalse(valid_job({'worker_id':'new','expires_at':time.time()-1},'new'))
        self.assertTrue(valid_job({'worker_id':'new','expires_at':time.time()+100},'new'))

    def test_dead_workers_orphan_is_archived_before_starting_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            atomic_json(root/'work'/'command.json',{'id':'orphan'})
            with patch.object(worker_client,'ROOT',root), \
                 patch.object(worker_client,'healthy',side_effect=[False,True]), \
                 patch.object(worker_client,'worker_alive',return_value=False), \
                 patch.object(worker_client,'port_open',return_value=False), \
                 patch.object(worker_client.subprocess,'Popen',return_value=Mock()) as launch:
                worker_client._ensure_worker()
            launch.assert_called_once()
            self.assertFalse((root/'work'/'command.json').exists())
            archived=list((root/'work'/'cancelled-commands').glob('*.json'))
            self.assertEqual(len(archived),1)
            self.assertEqual(json.loads(archived[0].read_text(encoding='utf-8'))['id'],'orphan')

    def test_unowned_debug_port_is_not_reused_or_replaced(self):
        with patch.object(worker_client,'healthy',return_value=False), \
             patch.object(worker_client,'worker_alive',return_value=False), \
             patch.object(worker_client,'port_open',return_value=True), \
             patch.object(worker_client.subprocess,'Popen') as launch:
            with self.assertRaises(RuntimeError):
                worker_client._ensure_worker()
            launch.assert_not_called()
