import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from seatwatch.booking import parse_flights
from seatwatch.diagnostics import public_fields
from seatwatch.app import App
from seatwatch.monitor import Monitor
from seatwatch.model import Condition


class BookingTests(unittest.TestCase):
    def test_sold_out_and_waitlist_never_count_as_available(self):
        text='''- radio "항공편명 KE037 프레스티지석 62,500 마일 5 석"
- radio "항공편명 KE039 프레스티지석 매진" [disabled]
- radio "항공편명 KE041 프레스티지석 62,500 마일 대기 예약"
- radio "항공편명 KE037 일반석 35,000 마일"'''
        rows=parse_flights(text,'프레스티지석')
        self.assertEqual([x['available'] for x in rows],[True,False,False])
        self.assertEqual(rows[0]['seats'],5)
        self.assertEqual(rows[0]['miles'],62500)
        self.assertIsNone(rows[1]['seats'])

    def test_unknown_banner_is_not_empty_success(self):
        self.assertEqual(parse_flights('검색하신 탑승일의 주변 일자까지 만석이거나 운항편이 없습니다.', '프레스티지석'),[])

    def test_diagnostics_omit_identity_and_credentials_recursively(self):
        clean=public_fields({'segmentList':[{'departureAirport':'ICN','arrivalAirport':'ORD','departureDate':'20261028'}],
            'token':'secret','headers':{'Authorization':'secret','Cookie':'secret'},
            'travellerList':[{'name':'private','departureDate':'private'}],
            'account':{'origin':'private'},'opaque':'private'})
        self.assertNotIn('secret',json.dumps(clean))
        self.assertNotIn('private',json.dumps(clean))
        self.assertEqual(clean['segmentList'][0]['arrivalAirport'],'ORD')


class AppStateTests(unittest.TestCase):
    def test_auth_pause_unlocks_ui_and_preserves_condition_for_resume(self):
        with tempfile.TemporaryDirectory() as folder,patch('seatwatch.app.ROOT',Path(folder)):
            root=tk.Tk()
            root.withdraw()
            monitor=Monitor(Path(folder)/'ledger.json',config=lambda:{'enabled':False})
            app=App(root,monitor=monitor)
            try:
                app.current=Condition('ICN','ORD','2026-11-11','일반석')
                monitor.start()
                app.monitoring=True
                app.busy=True
                app.messages.put(('query_condition',{'state':'LOGIN_REQUIRED'},None))
                app.poll()
                self.assertTrue(monitor.paused)
                self.assertFalse(app.monitoring)
                self.assertEqual(app.monitor_button.cget('text'),'자동 확인 재개')
                self.assertEqual(app.current.destination,'ORD')
                self.assertEqual(str(app.browser_button.cget('state')),'normal')
            finally:
                app.close()

    def test_transient_failure_keeps_monitor_active_without_clearing_table(self):
        with tempfile.TemporaryDirectory() as folder,patch('seatwatch.app.ROOT',Path(folder)):
            root=tk.Tk()
            root.withdraw()
            monitor=Monitor(Path(folder)/'ledger.json',config=lambda:{'enabled':False})
            app=App(root,monitor=monitor)
            try:
                app.current=Condition('ICN','ORD','2026-11-11','일반석')
                row=app.table.insert('','end',values=('confirmed previous result',))
                monitor.start()
                app.monitoring=True
                app.busy=True
                app.messages.put(('query_condition',{'state':'NETWORK_ERROR'},None))
                app.poll()
                self.assertTrue(app.monitoring)
                self.assertFalse(app.busy)
                self.assertIn('5분',app.status.get())
                self.assertTrue(app.table.exists(row))
                self.assertEqual(str(app.monitor_button.cget('state')),'normal')
            finally:
                app.close()

    def test_error_stops_monitoring_and_preserves_last_confirmed_rows(self):
        with tempfile.TemporaryDirectory() as folder, patch('seatwatch.app.ROOT', Path(folder)):
            root=tk.Tk()
            root.withdraw()
            app=App(root)
            try:
                result={'state':'OK','condition':{'origin':'ICN','destination':'ORD','departure':'2026-10-28','cabin':'프레스티지석','adults':1},
                    'checked_at':'2026-10-25T12:00:00+00:00','rows':[{'flight':'KE037','cabin':'프레스티지석','miles':62500,'seats':5,'available':True,'detail':'5 석'}]}
                app.render(result)
                original=app.table.item(app.table.get_children()[0])['values']
                app.monitoring=True
                app.busy=True
                app.messages.put(('query_condition',{'state':'BLOCKED','message':'접근 거부'},None))
                app.poll()
                self.assertFalse(app.monitoring)
                self.assertFalse(app.busy)
                self.assertEqual(app.table.item(app.table.get_children()[0])['values'],original)
                self.assertIn('접근 거부',app.status.get())
            finally:
                app.close()

    def test_invalid_condition_does_not_start_worker(self):
        with tempfile.TemporaryDirectory() as folder, patch('seatwatch.app.ROOT',Path(folder)):
            root=tk.Tk()
            root.withdraw()
            calls=[]
            app=App(root,executor=lambda *args: calls.append(args))
            try:
                app.fields['destination'].set('ICN')
                app.search()
                self.assertEqual(calls,[])
                self.assertFalse(app.busy)
                self.assertIn('달라야',app.status.get())
            finally:
                app.close()
