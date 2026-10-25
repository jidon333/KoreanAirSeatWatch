"""Incrementally verified Korean Air booking adapter.

Only searches and inspects availability. Never selects a flight or books.
"""
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .session import ROOT, atomic_json
import importlib
from . import browser_ui
# This module is hot-reloaded by the development worker, including UI helpers.
require_nearby_dates = importlib.reload(browser_ui).require_nearby_dates


def latch_access_denial(session, path='/api/ap/booking/avail/awardAvailability', status=403):
    session.access_blocked = True
    atomic_json(ROOT / 'data' / 'access-block.json', {
        'state': 'BLOCKED', 'observed_at': datetime.now(timezone.utc).isoformat(),
        'response': {'path': path, 'status': status},
        'retry_allowed': False,
        'note': '좌석 조회 요청 접근 거부. 원인은 미확정. 매진으로 기록하지 않음.',
    })


def snapshot(page):
    return re.sub(r'탑승객 [^"\n]+', '탑승객 [계정 선택값 생략]', page.get_by_role('main').aria_snapshot())


def run(session, job):
    page = session.page
    if job.get('step') == 'browser_environment':
        return page.evaluate('''() => ({webdriver: navigator.webdriver, language: navigator.language,
            cookieEnabled: navigator.cookieEnabled, secureContext: window.isSecureContext})''')
    if job.get('step') == 'last_calendar_shape':
        return getattr(session, 'last_calendar_shape', {'state': 'NOT_CAPTURED'})
    if job.get('step') == 'inspect_search_button':
        button = page.get_by_role('button', name='항공편 검색', exact=True)
        cdp = session.context.new_cdp_session(page)
        try:
            window = cdp.send('Browser.getWindowForTarget')
        finally:
            cdp.detach()
        return {'url': page.url, 'window':window, 'button': button.evaluate('''e=>({disabled:e.disabled, rect:e.getBoundingClientRect().toJSON(),
            visibility:document.visibilityState, text:e.innerText})'''),
            'dialogs': page.get_by_role('dialog').all_text_contents()}
    if job.get('step') == 'focus_browser':
        cdp = session.context.new_cdp_session(page)
        try:
            window = cdp.send('Browser.getWindowForTarget')
            if window['bounds']['windowState'] == 'minimized':
                cdp.send('Browser.setWindowBounds', {'windowId': window['windowId'], 'bounds': {'windowState': 'normal'}})
        finally:
            cdp.detach()
        page.bring_to_front()
        # Login overlays hide <main>; taking its accessibility snapshot can wait
        # for the full locator timeout. Focusing a window needs no page snapshot.
        return session.status()
    if job.get('step') == 'acknowledge_manual_retry':
        if (ROOT / 'data' / 'access-block.json').exists():
            raise RuntimeError('이전 접근 거부 기록을 보존한 뒤 수동 재시험해야 합니다.')
        session.access_blocked = False
        return {'state': 'SEARCH_UNVERIFIED', 'message': '사용자 지시로 조건을 바꾼 수동 비교 시험. 자동 감시 아님.'}
    if job.get('step') == 'latch_block':
        latch_access_denial(session)
        return {'state':'BLOCKED','message':'접근 거부 확인. 자동 재시도 중지.'}
    if (getattr(session,'access_blocked',False) or (ROOT / 'data' / 'access-block.json').exists()) and job.get('step') not in ('booking_snapshot','inspect'):
        return {'state':'BLOCKED','message':'접근 거부 상태입니다. 동일 요청을 반복하지 않습니다.'}
    if job.get('step') == 'submit_saved_login':
        layer = page.locator('#login-layer')
        layer.get_by_role('button', name='로그인', exact=True).click()
        try:
            layer.wait_for(state='hidden', timeout=20000)
        except Exception:
            return {'state': 'LOGIN_REQUIRED', 'message': '로그인 창이 유지됩니다. 입력값은 읽지 않았습니다.',
                    'structure': session.describe()}
        return {'state':'SEARCH_UNVERIFIED', 'structure':session.describe()}
    if job.get('step') == 'inspect':
        return session.describe()
    if job.get('step') == 'booking_snapshot':
        if page.locator('#login-layer').is_visible():
            return {'state':'LOGIN_REQUIRED'}
        return {'snapshot': snapshot(page)}
    if job.get('step') == 'return_search':
        page.goto('https://www.koreanair.com/booking/search', wait_until='domcontentloaded')
        page.get_by_role('heading', name='항공권 예매', exact=True).wait_for()
        return {'url': page.url, 'snapshot': snapshot(page), 'dialogs':page.get_by_role('dialog').all_text_contents()}
    if job.get('step') == 'inspect_date_picker':
        page.get_by_role('button', name=re.compile(r'^출발일 \d+월')).click()
        return {'snapshot': snapshot(page), 'inputs': page.locator('main input').evaluate_all(
            'els=>els.map(e=>({type:e.type,id:e.id,placeholder:e.placeholder,readonly:e.readOnly}))')}
    if job.get('step') == 'query_condition':
        from . import booking
        return importlib.reload(booking).query(session, job['condition'])
    if job.get('step') == 'date_picker_text':
        dialog = page.get_by_role('dialog', name='출발일', exact=True)
        return {'text': dialog.inner_text(), 'snapshot': dialog.aria_snapshot()}
    if job.get('step') == 'origin_dialog':
        page.locator('label[for="rdo-bookingType-ow"]').click()
        page.get_by_role('button', name=re.compile(r'^출발지 SEL')).click()
        return {'snapshot': snapshot(page), 'dialogs': page.get_by_role('dialog').aria_snapshot() if page.get_by_role('dialog').count() == 1 else page.locator('body').aria_snapshot()[-16000:]}
    if job.get('step') == 'origin_search':
        page.get_by_role('combobox', name='출발지 검색', exact=True).fill('ICN')
        page.get_by_role('option', name='서울/인천, 대한민국 ICN', exact=True).click()
        return {'snapshot': snapshot(page)}
    if job.get('step') == 'destination_dialog':
        page.get_by_role('button', name='To 도착지', exact=True).click()
        return {'snapshot': snapshot(page)}
    if job.get('step') == 'destination_search':
        destination = job.get('destination', 'ATL')
        page.get_by_role('combobox', name='도착지 검색', exact=True).fill(destination)
        page.get_by_role('option', name=re.compile(re.escape(destination))).wait_for()
        return {'snapshot':snapshot(page)}
    if job.get('step') == 'date_dialog':
        destination = job.get('destination', 'ATL')
        page.get_by_role('option', name=re.compile(re.escape(destination) + r'$')).click()
        page.get_by_role('button', name='출발일 가는 날', exact=True).click()
        return {'snapshot':snapshot(page)}
    if job.get('step') == 'select_october_date':
        page.get_by_role('application').nth(0).get_by_role('button', name='28일, 수요일', exact=True).click()
        require_nearby_dates(page)
        return {'snapshot': snapshot(page)}
    if job.get('step') == 'calendar_to_flights':
        page.get_by_role('button', name='검색', exact=True).click()
        page.wait_for_timeout(8000)
        return {'url': page.url, 'snapshot': snapshot(page)}
    if job.get('step') == 'march_calendar':
        for _ in range(2):
            page.get_by_role('button', name='다음 2달', exact=True).click()
        return {'snapshot':snapshot(page)}
    if job.get('step') == 'select_date':
        page.get_by_role('application').nth(1).get_by_role('button', name='10일, 수요일', exact=True).click()
        return {'snapshot':snapshot(page)}
    if job.get('step') == 'inspect_calendar':
        return {'snapshot':snapshot(page), 'apps':page.get_by_role('application').count(),
                'datebuttons':page.get_by_role('button', name='10일, 수요일', exact=True).count(),
                'html':page.get_by_role('application').evaluate_all('els => els.map(e => e.outerHTML.slice(0,1000))')}
    if job.get('step') == 'search':
        require_nearby_dates(page)
        observed = []
        def response_seen(response):
            if response.request.resource_type in ('xhr', 'fetch'):
                parsed = urlsplit(response.url)
                if parsed.hostname == 'www.koreanair.com':
                    observed.append({'path':parsed.path,'status':response.status,'method':response.request.method})
                    if parsed.path == '/api/ap/booking/avail/awardAvailability' and response.status == 403:
                        latch_access_denial(session)
        page.on('response',response_seen)
        try:
            before = page.url
            page.get_by_role('button', name='항공편 검색', exact=True).click()
            try:
                page.wait_for_url(lambda url: str(url) != before, timeout=30000)
            except Exception:
                pass
            return {'url':page.url, 'snapshot':snapshot(page), 'responses':observed}
        finally:
            page.remove_listener('response', response_seen)
    if job.get('step') == 'retry_search_diagnose':
        if urlsplit(page.url).path != '/booking/search':
            return {'state': 'UI_CHANGED', 'message': '예매 검색 화면이 바뀌어 비교 조회를 중단합니다.'}
        require_nearby_dates(page)
        observed=[]
        page_errors=[]
        def page_error(error):
            page_errors.append(str(error)[:600])
        def capture(response):
            parsed = urlsplit(response.url)
            path=parsed.path
            if parsed.hostname != 'www.koreanair.com' or '/api/' not in path or response.request.resource_type not in ('xhr','fetch'):
                return
            row={'path':path,'status':response.status,'method':response.request.method}
            if '/api/ap/booking/avail/' in path and response.status >= 400:
                row['content_type']=response.headers.get('content-type','')
                row['error_body']=response.text()[:1600]
                if response.status == 403:
                    latch_access_denial(session, path)
            if '/api/ap/booking/avail/' in path and 'json' in response.headers.get('content-type', ''):
                try:
                    body = response.json()
                    def outline(value, depth=0):
                        if isinstance(value, dict):
                            return {k: outline(v, depth+1) if depth < 7 else type(v).__name__ for k,v in list(value.items())[:35]}
                        if isinstance(value, list):
                            return {'items': len(value), 'first_shape': outline(value[0], depth+1) if value and depth<7 else None}
                        return type(value).__name__
                    row['shape'] = outline(body)
                    if path.endswith('/calendarFareMatrix'):
                        session.last_calendar_shape = row['shape']
                    if isinstance(body, dict):
                        row['messages'] = {k: v for k,v in body.items() if re.fullmatch(r'(error|errorCode|errorMessage|code|message|status|success|msg)',k) and isinstance(v,(str,bool,int))}
                except Exception as exc:
                    row['parse_error'] = type(exc).__name__
            if '/api/ap/booking/' in path or response.status >= 400:
                observed.append(row)
        page.on('response',capture)
        page.on('pageerror',page_error)
        try:
            page.get_by_role('button',name='항공편 검색',exact=True).click()
            page.wait_for_timeout(10000)
            return {'snapshot':snapshot(page),'responses':observed,'page_errors':page_errors}
        finally:
            page.remove_listener('response',capture)
            page.remove_listener('pageerror',page_error)
    raise ValueError('아직 검증되지 않은 조회 단계입니다.')
