import unittest
from playwright.sync_api import sync_playwright
from seatwatch.browser_ui import dismiss_necessary_cookie_notice, submit_browser_filled_login, require_nearby_dates


class FirstVisitBrowserContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='chrome', headless=True, chromium_sandbox=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)

    def test_consent_hiding_heading_is_handled_first(self):
        self.page.set_content('''<main aria-hidden="true"><h1>보너스 좌석 조회</h1></main>
        <aside><button onclick="document.querySelector('main').removeAttribute('aria-hidden');this.parentElement.remove()">필수 쿠키만 허용</button>
        <button onclick="throw Error('Should not allow optional cookies')">모든 쿠키 허용</button></aside>''')
        dismiss_necessary_cookie_notice(self.page)
        self.assertTrue(self.page.get_by_role('heading',name='보너스 좌석 조회').is_visible())
        self.assertEqual(self.page.locator('aside').count(),0)

    def test_returning_visit_has_no_consent_click(self):
        self.page.set_content('<h1>보너스 좌석 조회</h1>')
        dismiss_necessary_cookie_notice(self.page)
        self.assertEqual(self.page.get_by_role('heading').count(),1)

    def test_login_submission_rejects_non_official_origin(self):
        self.page.set_content('<div id="login-layer"><button>로그인</button></div>')
        with self.assertRaises(ValueError):
            submit_browser_filled_login(self.page)

    def test_nearby_dates_stays_checked_when_called_again(self):
        self.page.set_content('''<input type="checkbox" id="nearby"><label for="nearby">가까운 날짜 함께 조회</label>''')
        require_nearby_dates(self.page)
        require_nearby_dates(self.page)
        self.assertTrue(self.page.get_by_role('checkbox', name='가까운 날짜 함께 조회').is_checked())


if __name__ == '__main__':
    unittest.main()
