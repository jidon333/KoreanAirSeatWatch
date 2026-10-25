"""Launch installed Chrome normally and attach using its loopback debug port."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
from playwright.sync_api import sync_playwright
from .session import BrowserSession, ROOT, PUBLIC_URL, login_handoff
from .browser_ui import dismiss_necessary_cookie_notice


class DebugBrowserSession(BrowserSession):
    def __init__(self, profile=None, port=9335):
        super().__init__()
        self.profile = Path(profile) if profile else ROOT / 'data' / 'browser-profile'
        self.port = port
        self.browser = None
        self.process = None

    def open(self):
        if self.context:
            return self.page
        # Never attach to an unknown service or a user's ordinary Chrome profile.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', self.port))
        candidates = [Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
                      Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe']
        executable = next((p for p in candidates if p.is_file()), None)
        if not executable:
            raise RuntimeError('설치된 Chrome을 찾지 못했습니다.')
        self.profile.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen([str(executable),
            '--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={self.port}',
            f'--user-data-dir={self.profile}', '--no-first-run', '--no-default-browser-check',
            'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        endpoint = f'http://127.0.0.1:{self.port}'
        until = time.monotonic() + 25
        while True:
            try:
                with urllib.request.urlopen(endpoint + '/json/version', timeout=1) as response:
                    info = json.load(response)
                if info.get('webSocketDebuggerUrl'):
                    break
            except (OSError, ValueError):
                pass
            if time.monotonic() >= until:
                raise TimeoutError('전용 Chrome 디버깅 연결을 열지 못했습니다.')
            time.sleep(0.25)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(endpoint, no_defaults=True, timeout=15000)
        self.context = self.browser.contexts[0]
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(15000)
        from .window import restore_session_window
        restore_session_window(self)
        # Chrome can restore the profile's previous minimized window bounds.
        cdp = self.context.new_cdp_session(self.page)
        try:
            window = cdp.send('Browser.getWindowForTarget')
            if window['bounds']['windowState'] == 'minimized':
                cdp.send('Browser.setWindowBounds', {'windowId':window['windowId'], 'bounds':{'windowState':'normal'}})
        finally:
            cdp.detach()
        self.page.bring_to_front()
        self.page.goto(PUBLIC_URL, wait_until='domcontentloaded', timeout=60000)
        dismiss_necessary_cookie_notice(self.page)
        return self.page

    def close(self):
        if self.browser and self.browser.is_connected():
            try:
                self.browser.new_browser_cdp_session().send('Browser.close')
            except Exception:
                pass
            self.browser.close()
        if self.pw:
            self.pw.stop()
        self.context = self.pw = self.page = self.browser = None
        if self.process:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass  # Do not terminate a browser that may now have user work.


if __name__ == '__main__':
    from .single_instance import SingleInstance
    with SingleInstance('KoreanAirSeatWatch_Worker'):
        login_handoff(DebugBrowserSession())
