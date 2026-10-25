from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from seatwatch.monitor import Monitor
from seatwatch.model import Condition


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'monitor.json'
        self.now=1000
        self.cfg={'enabled':True,'chat_id':'123','bot_username':'test_bot','verified_at':1}
        self.send=Mock(return_value=42)
        self.monitor=self.make()
        self.condition=Condition('ICN','ORD','2027-01-01')
        self.monitor.start()
        self.assertTrue(self.monitor.take_due())

    def make(self):
        return Monitor(self.path,clock=lambda:self.now,config=lambda:self.cfg,sender=self.send)

    def result(self,state='OK',flights=('KE037',)):
        return {'state':state,'rows':[{'flight':f,'available':True} for f in flights] if state=='OK' else [],
                'checked_at':'2026-10-25T12:00:00Z'}

    def accept(self,state='OK',flights=('KE037',)):
        return self.monitor.accept(self.condition,self.result(state,flights))

    def test_no_replay_burst_after_sleep_and_no_overlap(self):
        self.accept()
        self.now+=8*3600
        self.assertTrue(self.monitor.take_due())
        self.assertFalse(self.monitor.take_due())
        self.accept()
        self.assertEqual(self.monitor.due_at,self.now+300)

    def test_failed_queries_preserve_seats_and_pause_requires_explicit_resume(self):
        self.accept()
        self.monitor.flush()
        self.accept('LOGIN_REQUIRED')
        self.assertFalse(self.monitor.active)
        self.assertTrue(self.monitor.paused)
        self.assertIsNone(self.monitor.due_at)
        self.monitor.flush()
        self.assertEqual(self.send.call_count,2)
        self.assertIn('로그인',self.send.call_args.args[1])
        self.monitor.start()
        self.accept()
        self.monitor.flush()
        self.assertEqual(self.send.call_count,2)

    def test_restart_keeps_unsent_alert_but_not_automatic_queries(self):
        self.accept()
        self.send.side_effect=RuntimeError('network')
        self.monitor.flush()
        message=self.monitor.ledger.pending()[0]
        restarted=self.make()
        self.assertFalse(restarted.active)
        self.assertTrue(restarted.paused)
        self.assertEqual(restarted.ledger.pending()[0]['id'],message['id'])
        restarted.flush()
        self.assertEqual(self.send.call_count,1)
        self.now+=60
        self.send.side_effect=None
        restarted.flush()
        self.assertEqual(self.send.call_count,2)
        self.assertEqual(restarted.ledger.pending(),[])

    def test_same_seats_deduplicate_and_confirmed_reappearance_alerts(self):
        self.accept()
        self.monitor.flush()
        self.accept()
        self.monitor.flush()
        self.assertEqual(self.send.call_count,1)
        self.accept(flights=())
        self.accept()
        self.monitor.flush()
        self.assertEqual(self.send.call_count,2)

    def test_network_retry_limit_and_server_denial_never_retry(self):
        self.accept('NETWORK_ERROR')
        self.assertEqual(self.monitor.due_at,self.now+300)
        self.accept('NETWORK_ERROR')
        self.assertEqual(self.monitor.due_at,self.now+900)
        self.accept('NETWORK_ERROR')
        self.assertFalse(self.monitor.active)
        self.monitor.start()
        self.accept('BLOCKED')
        self.assertFalse(self.monitor.active)
        self.assertIsNone(self.monitor.due_at)

    def test_stop_during_query_never_rearms_or_notifies(self):
        self.monitor.stop()
        self.accept()
        self.assertFalse(self.monitor.take_due())
        self.assertFalse(self.monitor.ledger.pending())

    def test_repairing_destination_discards_old_outbox(self):
        self.accept()
        self.cfg={**self.cfg,'chat_id':'456','verified_at':2}
        self.monitor.flush()
        self.send.assert_not_called()
        self.assertFalse(self.monitor.ledger.pending())

    def test_no_connection_means_no_old_notifications_later(self):
        self.cfg={'enabled':False}
        self.accept()
        self.assertFalse(self.monitor.ledger.pending())
        self.monitor.flush()
        self.send.assert_not_called()

    def test_same_auth_incident_alerts_once_until_recovery(self):
        self.accept('LOGIN_REQUIRED')
        self.monitor.flush()
        self.monitor.start()
        self.accept('LOGIN_REQUIRED')
        self.monitor.flush()
        self.assertEqual(self.send.call_count,1)

    def test_same_flight_in_different_conditions_alerts_independently_and_removal_cancels(self):
        self.accept()
        other=Condition('ICN','ORD','2027-01-02','일반석')
        self.monitor.accept(other,self.result())
        self.assertEqual(len(self.monitor.ledger.pending()),2)
        self.monitor.discard_pending(self.condition.key)
        self.monitor.flush()
        self.assertEqual(self.send.call_count,1)
        self.assertIn('2027-01-02',self.send.call_args.args[1])
