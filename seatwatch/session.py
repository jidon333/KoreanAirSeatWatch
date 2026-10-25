"""One dedicated browser profile; no password copying or cookie export."""
from pathlib import Path
import json
from datetime import datetime, timezone
import importlib
import os
import time
import uuid
from playwright.sync_api import sync_playwright, Error as PlaywrightError
from .browser_ui import dismiss_necessary_cookie_notice

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_URL = 'https://www.koreanair.com/booking/book-and-manage/award-seat-availability'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def valid_job(job, worker_id):
    return job.get('worker_id') == worker_id and job.get('expires_at',0) > time.time()


class BrowserSession:
    def __init__(self):
        self.pw = None
        self.context = None
        self.page = None

    def open(self):
        if self.context:
            return self.page
        self.pw = sync_playwright().start()
        self.context = self.pw.chromium.launch_persistent_context(
            str(ROOT / 'data' / 'browser-profile'), channel='chrome', headless=False,
            chromium_sandbox=True, locale='ko-KR', viewport={'width': 1280, 'height': 900},
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(15000)
        self.page.goto(PUBLIC_URL, wait_until='domcontentloaded', timeout=60000)
        # First-visit consent hides the main accessibility tree. Handle before
        # waiting for the public page heading; choose necessary cookies only.
        heading = self.page.get_by_role('heading', name='보너스 좌석 조회', exact=True)
        try:
            dismiss_necessary_cookie_notice(self.page)
        except Exception:
            # This capture is BEFORE authentication, on the public page only.
            target = ROOT / 'work' / 'public-open'
            target.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(target / 'screen.png'))
            (target / 'visible.txt').write_text(self.page.locator('body').inner_text(), encoding='utf-8')
            raise
        return self.page

    def open_login(self):
        page = self.open()
        page.get_by_role('button', name='마일리지 예매', exact=True).click()
        return self.status()

    def status(self):
        if getattr(self, 'access_blocked', False) or (ROOT / 'data' / 'access-block.json').exists():
            return {'state':'BLOCKED', 'message':'좌석 조회 접근 거부 · 감시 중단'}
        if not self.page or self.page.is_closed():
            return {'state': 'CLOSED', 'message': '대한항공 창이 닫혔습니다.'}
        if self.page.locator('#login-layer').is_visible():
            return {'state': 'LOGIN_REQUIRED', 'message': '대한항공 공식 창에서 직접 로그인해 주세요.'}
        # Disappearing login dialog alone is NOT evidence of authenticated search.
        return {'state': 'SEARCH_UNVERIFIED', 'message': '로그인 창 없음 · 실제 검색 확인 필요'}

    def describe(self):
        # Structure-only diagnostics: never include input values or account text.
        result = []
        for frame in self.page.frames:
            result.append({'url': frame.url.split('?')[0], 'controls': frame.locator(
                'button, input, select, h1, h2, [role="tab"]').evaluate_all('''els => els.filter(e => e.getBoundingClientRect().width > 0).map(e => ({
                    tag:e.tagName, type:e.getAttribute('type'), id:e.id,
                    role:e.getAttribute('role'), label:e.getAttribute('aria-label'),
                    text:e.tagName === 'INPUT' ? '' : e.innerText?.slice(0,160)
                }))''')})
        return result

    def close(self):
        if self.context:
            self.context.close()
        if self.pw:
            self.pw.stop()
        self.context = self.pw = self.page = None


def login_handoff(session=None):
    """Keep the exact product browser alive while the user authenticates.

    Only publishes high-level state. Never stores form values, response bodies,
    cookies, passwords or authenticated screenshots in diagnostics.
    """
    session = session or BrowserSession()
    from .lifecycle import process_stamp
    worker_id = str(uuid.uuid4())
    identity = {'worker_id':worker_id, 'pid':os.getpid(), 'process_stamp':process_stamp(os.getpid()),
                'port':getattr(session, 'port', 9335)}
    def publish(state):
        atomic_json(ROOT/'work'/'login-status.json', {**state, **identity,
                    'observed_at':datetime.now(timezone.utc).isoformat()})
    try:
        publish({'state':'STARTING','message':'대한항공 창을 준비하고 있습니다.'})
        session.open_login()
        while session.page and not session.page.is_closed():
            result = session.status()
            publish(result)
            if (ROOT / 'work' / 'close-login').exists():
                break
            command = ROOT / 'work' / 'command.json'
            if command.exists():
                job = json.loads(command.read_text(encoding='utf-8'))
                command.unlink()
                if not valid_job(job, worker_id):
                    atomic_json(ROOT/'work'/'command-result.json', {'id':job['id'], 'result':{
                        'state':'CLOSED','message':'종료된 세션의 이전 요청은 취소했습니다. 다시 조회해 주세요.'}})
                    continue
                publish({'state':'BUSY','message':'조회 중입니다.', 'command_id':job['id']})
                try:
                    if job['action'] == 'describe':
                        output = session.describe()
                    elif job['action'] == 'collect':
                        from . import collector
                        importlib.reload(collector)
                        output = collector.run(session, job)
                    else:
                        output = {'error': 'unknown action'}
                    # Publish readiness before the reply: the client may issue
                    # another action immediately after receiving this result.
                    publish(session.status())
                    atomic_json(ROOT / 'work' / 'command-result.json', {'id':job['id'], 'result':output})
                except Exception as exc:
                    if not session.page or session.page.is_closed():
                        atomic_json(ROOT/'work'/'command-result.json', {'id':job['id'], 'result':{
                            'state':'CLOSED','message':'조회 중 전용 브라우저가 닫혔습니다. 지금 조회를 누르면 다시 엽니다.'}})
                    else:
                        atomic_json(ROOT / 'work' / 'command-result.json', {'id':job['id'], 'error_type':type(exc).__name__, 'error':str(exc)[:800]})
            try:
                session.page.wait_for_timeout(1000)
            except PlaywrightError:
                if not session.page or session.page.is_closed():
                    break
                raise
    finally:
        try:
            session.close()
        finally:
            publish({
                'state': 'CLOSED', 'message': '대한항공 창이 닫혔습니다.',
            })


if __name__ == '__main__':
    login_handoff()
