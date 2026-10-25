"""Allowlisted booking diagnostics. No headers, cookies or identity values."""
from datetime import datetime, timezone
from urllib.parse import urlsplit
import re
import time
from .session import ROOT, atomic_json
from .policy import classify_response


def public_fields(value, depth=0):
    allowed = {'departureDate', 'arrivalDate', 'departureAirport', 'arrivalAirport',
               'origin', 'destination', 'cabinClass', 'cabin', 'currency',
               'adultCount', 'childCount', 'infantCount', 'emptyFare', 'emptyCode',
               'peakSeason', 'fareFamily', 'status', 'available', 'seatCount'}
    if depth > 8:
        return None
    if isinstance(value, list):
        return [public_fields(item, depth+1) for item in value[:40]]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if re.search(r'pass|token|cookie|auth|member|travell?er|account|contact|email|phone|name', key, re.I):
                continue
            if key in allowed and isinstance(item, (str, int, bool, float, type(None))):
                result[key] = item[:100] if isinstance(item, str) else item
            elif isinstance(item, (dict, list)):
                child = public_fields(item, depth+1)
                if child:
                    result[key] = child
        return result
    return None


class Trace:
    def __init__(self, session, condition):
        self.session, self.page = session, session.page
        self.data = {'at': datetime.now(timezone.utc).isoformat(), 'condition': condition,
                     'responses': [], 'page_error_count': 0}
        self.started = time.monotonic()
        session.query_failure = None

    def response(self, response):
        parsed = urlsplit(response.url)
        if parsed.hostname != 'www.koreanair.com' or not parsed.path.startswith('/api/ap/booking/avail/'):
            return
        row = {'path': parsed.path, 'status': response.status, 'method': response.request.method,
               'elapsed_ms': round((time.monotonic()-self.started)*1000),
               'content_type': response.headers.get('content-type', '').split(';')[0]}
        try:
            row['request'] = public_fields(response.request.post_data_json)
        except Exception:
            row['request'] = None
        if response.status == 200 and 'json' in row['content_type']:
            try:
                row['response'] = public_fields(response.json())
            except Exception:
                row['parse_failed'] = True
        body = ''
        if 'text/html' in row['content_type']:
            try:
                body = response.text()[:8192]
            except Exception:
                pass
        failure = classify_response(response.status, row['content_type'], body)
        if failure and (not self.session.query_failure or failure.state == 'BLOCKED'):
            self.session.query_failure = failure
        if failure and failure.state == 'BLOCKED':
            from .collector import latch_access_denial
            latch_access_denial(self.session, parsed.path, status=response.status)
        self.data['responses'].append(row)

    def request_failed(self, request):
        parsed = urlsplit(request.url)
        if parsed.hostname == 'www.koreanair.com' and parsed.path.startswith('/api/ap/booking/avail/'):
            from .policy import Failure
            if not self.session.query_failure:
                self.session.query_failure = Failure('NETWORK_ERROR','대한항공 연결에 실패했습니다.',True)

    def error(self, error):
        self.data['page_error_count'] += 1

    def __enter__(self):
        self.page.on('response', self.response)
        self.page.on('pageerror', self.error)
        self.page.on('requestfailed', self.request_failed)
        return self

    def __exit__(self, *exc):
        self.page.remove_listener('response', self.response)
        self.page.remove_listener('pageerror', self.error)
        self.page.remove_listener('requestfailed', self.request_failed)
        self.data['exception_type'] = exc[0].__name__ if exc[0] else None
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        atomic_json(ROOT / 'work' / 'diagnostics' / (stamp + '.json'), self.data)
