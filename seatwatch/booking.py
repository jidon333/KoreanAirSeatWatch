"""Search the official visible form. Never choose fares or advance to payment."""
from dataclasses import asdict
from datetime import date, datetime, timezone
import re
import time
from .model import Condition
from .browser_ui import require_nearby_dates
from .diagnostics import Trace
from .session import ROOT, atomic_json


def focus(session):
    cdp = session.context.new_cdp_session(session.page)
    try:
        window = cdp.send('Browser.getWindowForTarget')
        if window['bounds']['windowState'] == 'minimized':
            cdp.send('Browser.setWindowBounds', {'windowId': window['windowId'], 'bounds': {'windowState': 'normal'}})
    finally:
        cdp.detach()
    session.page.bring_to_front()


def parse_flights(snapshot, cabin):
    rows = []
    for label, disabled in re.findall(r'- radio "([^"\n]+)"([^\n]*)', snapshot):
        match = re.fullmatch(r'항공편명 (.+?) (일반석|프리미엄석|프레스티지석|일등석) (.+)', label)
        if not match or match[2] != cabin:
            continue
        fare = re.search(r'([\d,]+) 마일', match[3])
        seats = re.search(r'(\d+) 석', match[3])
        available = '[disabled]' not in disabled and fare is not None and not re.search('대기|매진', match[3])
        rows.append({'flight': match[1], 'cabin': match[2], 'available': available,
                     'miles': int(fare[1].replace(',', '')) if fare else None,
                     'seats': int(seats[1]) if seats else None,
                     'detail': match[3]})
    return rows


def airport(page, which, code):
    name = '출발지' if which == 'origin' else '도착지'
    blank = 'From' if which == 'origin' else 'To'
    button = page.get_by_role('button', name=re.compile(r'^(?:' + name + r' [A-Z]{3}|'+blank+' '+name+r'$)'))
    if re.search(r'\b'+code+r'\b', button.inner_text()):
        return
    button.click()
    field = page.get_by_role('combobox', name=name+' 검색', exact=True)
    field.fill('')
    field.press_sequentially(code, delay=100)
    page.get_by_role('option', name=re.compile(r' '+re.escape(code)+r'$')).click()


def choose_date(page, target):
    page.get_by_role('button', name=re.compile(r'^출발일 (?:\d+월|가는 날)')).click()
    dialog = page.get_by_role('dialog', name='출발일', exact=True)
    dialog.wait_for()
    wanted = (target.year, target.month)
    for _ in range(8):
        months = [(int(y), int(m)) for y,m in re.findall(r'(\d{4})년\s*(\d{1,2})월', dialog.aria_snapshot())]
        months = list(dict.fromkeys(months))
        if wanted in months:
            page.get_by_role('application').nth(months.index(wanted)).get_by_role(
                'button', name=re.compile(r'^'+str(target.day)+r'일,')).click()
            dialog.wait_for(state='hidden')
            return
        if not months:
            raise RuntimeError('날짜 선택 화면의 월을 확인하지 못했습니다.')
        page.get_by_role('button', name='지난 2달' if wanted < months[0] else '다음 2달', exact=True).click()
    raise RuntimeError('지원 범위 안의 출발일을 찾지 못했습니다.')


def wait_result(session, heading):
    page = session.page
    deadline = time.monotonic()+35
    while time.monotonic() < deadline:
        if getattr(session, 'access_blocked', False):
            return 'BLOCKED'
        if page.locator('#login-layer').is_visible():
            return 'LOGIN_REQUIRED'
        if page.get_by_role('heading', name=heading, exact=True).is_visible():
            return 'READY'
        page.wait_for_timeout(300)
    return 'UNVERIFIED'


def query(session, values):
    condition = Condition(**values)
    target = date.fromisoformat(condition.departure)
    if condition.adults != 1:
        raise ValueError('현재 버전은 로그인한 회원 1명만 조회합니다.')
    if not 0 <= (target - date.today()).days <= 360:
        raise ValueError('출발일은 오늘부터 360일 이내로 입력해 주세요.')
    page = session.page
    result = {'state': 'UNVERIFIED', 'condition': asdict(condition), 'rows': [],
              'checked_at': datetime.now(timezone.utc).isoformat(), 'nearby': True}
    with Trace(session, asdict(condition)) as trace:
        focus(session)
        page.goto('https://www.koreanair.com/booking/search', wait_until='domcontentloaded')
        page.get_by_role('heading', name='항공권 예매', exact=True).wait_for()
        tab = page.get_by_role('tab', name='마일리지 예매', exact=True)
        if tab.get_attribute('aria-selected') != 'true':
            tab.click()
        if page.locator('#login-layer').is_visible():
            result.update(state='LOGIN_REQUIRED', message='대한항공 창에서 로그인한 뒤 다시 조회해 주세요.')
        else:
            page.locator('label[for="rdo-bookingType-ow"]').click()
            airport(page, 'origin', condition.origin)
            airport(page, 'destination', condition.destination)
            choose_date(page, target)
            require_nearby_dates(page)
            page.get_by_role('button', name='항공편 검색', exact=True).click()
            stage = wait_result(session, '날짜 선택')
            if stage == 'READY':
                calendar = page.get_by_role('tabpanel', name=re.compile(r'^가는 날 출발지 '+condition.origin+' 도착지 '+condition.destination))
                calendar.wait_for()
                day = page.get_by_role('button', name=re.compile(r'^0?'+str(target.month)+r'월 0?'+str(target.day)+r'일 '))
                if day.count() == 1 and day.is_enabled():
                    day.click()
                    page.get_by_role('button', name='검색', exact=True).click()
                    stage = wait_result(session, '출발편 선택')
                    if stage == 'READY':
                        from .collector import snapshot
                        current = snapshot(page)
                        exact_route = (page.get_by_role('button', name='출발지 '+condition.origin, exact=True).count()==1 and
                                       page.get_by_role('button', name='도착지 '+condition.destination, exact=True).count()==1)
                        exact_day = page.get_by_role('button', name=re.compile(r'^출발일 0?'+str(target.month)+r'월 0?'+str(target.day)+r'일 ')).count()==1
                        passenger = page.get_by_role('button', name=re.compile(r'^탑승객 ')).aria_snapshot()
                        one_person = bool(re.search(r'(?:성인\s*)?1\s*명', passenger))
                        rows = parse_flights(current, condition.cabin)
                        fresh = any(r['status']==200 and r['path'].endswith('/awardAvailability') for r in trace.data['responses'])
                        if exact_route and exact_day and one_person and fresh and rows:
                            result.update(state='OK', rows=rows, message='대한항공 실제 항공편 조회 완료')
                            (ROOT/'work'/'last-flight-snapshot.txt').write_text(current, encoding='utf-8')
                            page.screenshot(path=str(ROOT/'work'/'last-flight-result.png'), full_page=True)
                        else:
                            result['message'] = '검색 조건 또는 새 항공편 응답을 확인하지 못했습니다. 결과를 확정하지 않습니다.'
                            trace.data['verification'] = {'route':exact_route,'date':exact_day,'one_person':one_person,'fresh':fresh,'rows':len(rows)}
                else:
                    result['message'] = '달력에 선택 가능한 항공편이 없습니다. 사이트 오류 가능성 때문에 매진으로 확정하지 않습니다.'
            if result['state'] != 'OK' and stage != 'READY':
                result.update(state=stage, message={'BLOCKED':'접근이 거부되어 자동 조회를 중단했습니다.',
                    'LOGIN_REQUIRED':'대한항공 창에서 로그인한 뒤 다시 조회해 주세요.'}.get(stage, '대한항공에서 정상 결과 화면을 받지 못했습니다. 매진으로 처리하지 않습니다.'))
        result.setdefault('message', '결과를 확정하지 못했습니다.')
        trace.data['outcome'] = result['state']
        result['responses'] = [{k:r[k] for k in ('path','status','method')} for r in trace.data['responses']]
    result['checked_at'] = datetime.now(timezone.utc).isoformat()
    atomic_json(ROOT/'data'/'last-attempt.json', result)
    if result['state']=='OK':
        atomic_json(ROOT/'data'/'last-success.json', result)
    return result
