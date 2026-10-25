import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from playwright.sync_api import Error
from seatwatch import booking, diagnostics


class FailureDetectionTests(unittest.TestCase):
    def response(self,status,content_type='application/json',body=''):
        request=SimpleNamespace(method='POST',post_data_json={})
        return SimpleNamespace(url='https://www.koreanair.com/api/ap/booking/avail/awardAvailability',
            status=status,request=request,headers={'content-type':content_type},json=lambda:{},text=lambda:body)

    def test_auth_server_error_and_html_denial_are_classified_in_live_trace(self):
        for status,kind,content,body in [(401,'LOGIN_REQUIRED','application/json',''),
                (503,'NETWORK_ERROR','application/json',''),(200,'BLOCKED','text/html','Access Denied')]:
            session=SimpleNamespace(page=Mock())
            trace=diagnostics.Trace(session,{})
            with patch('seatwatch.collector.latch_access_denial') as latch:
                trace.response(self.response(status,content,body))
            self.assertEqual(session.query_failure.state,kind)
            self.assertEqual(latch.called,kind=='BLOCKED')

    def test_network_exception_is_not_sold_out_and_does_not_replace_last_success(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(booking,'ROOT',Path(folder)):
            root=Path(folder)
            (root/'data').mkdir()
            success=root/'data'/'last-success.json'
            success.write_text('previous-result',encoding='utf-8')
            session=SimpleNamespace(page=Mock())
            session.page.is_closed.return_value=False
            session.page.locator.return_value.is_visible.return_value=False
            with patch.object(booking,'_query',side_effect=Error('net::ERR_INTERNET_DISCONNECTED')):
                result=booking.query(session,{'origin':'ICN'})
            self.assertEqual(result['state'],'NETWORK_ERROR')
            self.assertEqual(result['rows'],[])
            self.assertEqual(success.read_text(encoding='utf-8'),'previous-result')

    def test_failed_asset_does_not_mark_booking_network_failure(self):
        session=SimpleNamespace(page=Mock())
        trace=diagnostics.Trace(session,{})
        trace.request_failed(SimpleNamespace(url='https://www.koreanair.com/logo.png'))
        self.assertIsNone(session.query_failure)
        trace.request_failed(SimpleNamespace(url='https://www.koreanair.com/api/ap/booking/avail/awardAvailability'))
        self.assertEqual(session.query_failure.state,'NETWORK_ERROR')
