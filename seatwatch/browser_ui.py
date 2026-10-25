"""Small, observed browser transitions shared by the app and regression tests."""
from urllib.parse import urlsplit
import json


def require_nearby_dates(page):
    """Always enable nearby-date search; never toggle it off on repeated use."""
    checkbox = page.get_by_role('checkbox', name='가까운 날짜 함께 조회', exact=True)
    checkbox.wait_for(state='visible')
    if not checkbox.is_checked():
        control_id = checkbox.get_attribute('id')
        if control_id:
            page.locator('label[for=' + json.dumps(control_id) + ']').click()
        else:
            checkbox.check()
    if not checkbox.is_checked():
        raise RuntimeError('가까운 날짜 함께 조회가 선택되지 않아 검색을 중단합니다.')


def dismiss_necessary_cookie_notice(page):
    consent = page.get_by_role('button', name='필수 쿠키만 허용', exact=True)
    heading = page.get_by_role('heading', name='보너스 좌석 조회', exact=True)
    consent.or_(heading).first.wait_for(timeout=45000)
    if consent.is_visible():
        consent.click()
    heading.wait_for(timeout=45000)


def submit_browser_filled_login(page):
    # Do not inspect, export or persist password values. Browser autofill owns them.
    if urlsplit(page.url).hostname != 'www.koreanair.com':
        raise ValueError('대한항공 공식 도메인에서만 로그인할 수 있습니다.')
    layer = page.locator('#login-layer')
    if not layer.is_visible():
        return False
    layer.get_by_role('button', name='로그인', exact=True).click()
    layer.wait_for(state='hidden', timeout=20000)
    return True
