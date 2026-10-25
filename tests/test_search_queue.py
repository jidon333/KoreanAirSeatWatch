from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from seatwatch.app import App
from seatwatch.model import Condition
from seatwatch.monitor import Monitor
from seatwatch.search_queue import SearchQueue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'queue.json'
        self.now=1000
        self.a=Condition('ICN','ORD','2027-01-01','일반석')
        self.b=Condition('ICN','ORD','2027-01-02','프레스티지석')
        self.queue=self.load()

    def load(self):
        return SearchQueue(self.path,self.a,clock=lambda:self.now)

    def ok(self,c):
        return {'state':'OK','condition':asdict(c),'checked_at':'2026-10-25T12:00:00Z',
                'rows':[{'flight':'KE037','available':True}]}

    def test_rotation_survives_restart_with_global_gap_and_independent_results(self):
        self.queue.put(self.b)
        self.queue.started()
        self.assertEqual(self.load().not_before,1300)
        self.now+=20
        self.queue.finished(self.a,self.ok(self.a),advance=True)
        restarted=self.load()
        self.assertEqual(restarted.next_condition,self.b)
        self.assertEqual(restarted.not_before,1320)
        self.assertEqual(restarted.result(self.a)['success']['condition'],asdict(self.a))
        self.assertFalse(restarted.result(self.b))
        restarted.finished(self.b,self.ok(self.b),advance=True)
        self.assertEqual(restarted.next_condition,self.a)

    def test_failure_retains_turn_and_last_success_manual_does_not_rotate(self):
        self.queue.put(self.b)
        self.queue.finished(self.a,self.ok(self.a))
        self.queue.finished(self.a,{'state':'NETWORK_ERROR'},advance=True)
        self.assertEqual(self.queue.next_condition,self.a)
        self.assertEqual(self.queue.result(self.a)['state'],'NETWORK_ERROR')
        self.assertTrue(self.queue.result(self.a)['success']['rows'])

    def test_edit_remove_duplicate_and_empty_queue(self):
        self.queue.put(self.b)
        with self.assertRaises(ValueError):
            self.queue.put(self.b)
        self.queue.remove(self.a.key)
        self.assertEqual(self.queue.next_condition,self.b)
        self.queue.put(self.a,replace_key=self.b.key)
        self.assertEqual(self.queue.next_condition,self.a)
        self.queue.remove(self.a.key)
        self.assertIsNone(self.load().next_condition)
        self.queue.put(self.b)
        self.assertEqual(self.queue.next_condition,self.b)

    def test_interval_change_never_shortens_existing_cooldown(self):
        self.queue.finished(self.a,self.ok(self.a))
        self.queue.set_interval(15)
        self.assertEqual(self.queue.not_before,1900)
        self.queue.set_interval(5)
        self.assertEqual(self.queue.not_before,1900)
        for bad in (4,61,True,5.5,'5'):
            with self.assertRaises(ValueError):
                self.queue.set_interval(bad)

    def test_retry_obeys_global_interval_and_resume_waits(self):
        monitor=Monitor(Path(self.tmp.name)/'monitor.json',clock=lambda:self.now,config=lambda:{})
        monitor.interval=1200
        monitor.start(not_before=1300)
        self.assertFalse(monitor.take_due())
        self.now=1300
        self.assertTrue(monitor.take_due())
        monitor.accept(self.a,{'state':'NETWORK_ERROR'})
        self.assertEqual(monitor.due_at,2500)
        monitor.accept(self.a,{'state':'BLOCKED'})
        self.assertFalse(monitor.active)
        self.assertIsNone(monitor.due_at)


class QueueAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher=patch('seatwatch.app.ROOT',Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root=tk.Tk()
        self.root.withdraw()
        monitor=Monitor(Path(self.tmp.name)/'monitor.json',config=lambda:{})
        self.app=App(self.root,monitor=monitor)
        self.addCleanup(self.app.close)
        self.app.dispatch=lambda *args: setattr(self.app,'busy',True)

    def result(self,c):
        return {'state':'OK','condition':asdict(c),'checked_at':'2026-10-25T12:00:00Z',
                'rows':[{'flight':'KE037','cabin':c.cabin,'miles':35000,'seats':None,'available':True,'detail':''}]}

    def test_stop_inflight_saves_correct_result_and_does_not_rearm(self):
        first=self.app.search_queue.next_condition
        self.app.fields['departure'].set((date.today()+timedelta(days=5)).isoformat())
        self.app.add_condition()
        self.app.toggle_monitor()
        self.assertEqual(self.app.current,first)
        self.app.stop_monitor()
        self.app.messages.put(('query_condition',self.result(first),None))
        self.app.poll()
        self.assertFalse(self.app.monitor.active)
        self.assertIsNone(self.app.monitor.due_at)
        self.assertEqual(self.app.search_queue.result(first)['success']['condition'],asdict(first))
        self.assertFalse(self.app.monitor.ledger.pending())
        self.app.toggle_monitor()
        self.assertFalse(self.app.busy)
        self.assertGreater(self.app.monitor.due_at,self.app.monitor.clock())

    def test_selection_changes_do_not_retarget_running_query(self):
        first=self.app.search_queue.next_condition
        self.app.fields['departure'].set((date.today()+timedelta(days=5)).isoformat())
        self.app.add_condition()
        second=self.app.search_queue.conditions[1]
        self.app.toggle_monitor()
        self.app.queue_table.selection_set(second.key)
        self.app.select_condition()
        self.assertEqual(self.app.current,first)
        self.app.messages.put(('query_condition',self.result(first),None))
        self.app.poll()
        self.assertEqual(self.app.search_queue.next_condition,second)
        self.assertFalse(self.app.search_queue.result(second))
        self.assertEqual(self.app.monitor.interval,300)

    def test_manual_query_cannot_bypass_cooldown_and_empty_queue_stays_idle(self):
        self.app.search_queue.started()
        self.app.search()
        self.assertFalse(self.app.busy)
        self.app.queue_table.selection_set(self.app.search_queue.conditions[0].key)
        self.app.remove_condition()
        self.app.toggle_monitor()
        self.assertFalse(self.app.monitoring)
        self.assertIn('먼저 추가',self.app.status.get())

    def test_auth_pause_keeps_queue_turn_and_cooldown_across_app_restart(self):
        first=self.app.search_queue.next_condition
        self.app.fields['departure'].set((date.today()+timedelta(days=5)).isoformat())
        self.app.add_condition()
        self.app.toggle_monitor()
        self.app.messages.put(('query_condition',{'state':'LOGIN_REQUIRED'},None))
        self.app.poll()
        self.assertFalse(self.app.monitoring)
        self.assertTrue(self.app.monitor.paused)
        saved=SearchQueue(self.app.search_queue.path,first)
        self.assertEqual(saved.next_condition,first)
        self.assertEqual(len(saved.conditions),2)
        self.assertEqual(saved.result(first)['state'],'LOGIN_REQUIRED')
        self.assertGreater(saved.not_before,self.app.monitor.clock())
        self.app.toggle_monitor()
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.monitor.due_at,saved.not_before)
