"""Reuse a healthy local worker; never start a second Chrome for a live session."""
import json
import subprocess
import sys
import time
from .session import ROOT
from .control import command
from .lifecycle import BrowserClosed, read_status, worker_alive, worker_connected, port_open


def healthy():
    return worker_connected(read_status())


def ensure_worker():
    from .single_instance import SingleInstance
    with SingleInstance('KoreanAirSeatWatch_Startup'):
        _ensure_worker()


def _ensure_worker():
    if healthy():
        return
    # A worker may be finishing browser shutdown or starting it. Do not race it.
    deadline = time.monotonic()+70
    while worker_alive(read_status()):
        if healthy():
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('브라우저 준비가 지연됩니다. 기존 작업이 끝나기 전에는 중복 실행하지 않습니다.')
        time.sleep(.25)
    if port_open():
        raise RuntimeError('전용 브라우저 연결 포트가 사용 중입니다. 확인되지 않은 브라우저에 연결하지 않습니다.')
    request = ROOT/'work'/'command.json'
    if request.exists():
        # No live worker and no debug endpoint: preserve the orphan for diagnosis.
        target = ROOT/'work'/'cancelled-commands'
        target.mkdir(parents=True, exist_ok=True)
        request.replace(target/(str(time.time_ns())+'.json'))
    sentinel = ROOT/'work'/'close-login'
    if sentinel.exists():
        sentinel.unlink()
    log = open(ROOT/'work'/'worker.log', 'a', encoding='utf-8')
    try:
        process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'seatwatch.debug_browser'],
            cwd=ROOT, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        log.close()
    deadline = time.monotonic()+70
    while time.monotonic()<deadline:
        if healthy():
            return
        if process.poll() is not None:
            raise RuntimeError('전용 브라우저를 시작하지 못했습니다. work/worker.log를 확인해 주세요.')
        time.sleep(.5)
    raise RuntimeError('브라우저 준비가 지연됩니다. 열린 대한항공 창을 확인해 주세요.')


def execute(step, payload=None):
    try:
        ensure_worker()
        if step == 'query_condition' and read_status().get('state') == 'LOGIN_REQUIRED':
            command('focus_browser', timeout=15)
            return {'state':'LOGIN_REQUIRED', 'message':'대한항공 창에서 로그인한 뒤 지금 조회를 눌러 주세요.'}
        response = command(step, timeout=180, payload=payload)
    except BrowserClosed as exc:
        return {'state':'CLOSED', 'message':str(exc)}
    if 'error_type' in response:
        # Detailed Playwright errors may contain private DOM content; keep UI concise.
        raise RuntimeError('대한항공 화면 처리 실패 ('+response['error_type']+'). 열린 창을 확인한 뒤 다시 조회해 주세요.')
    return response['result']
