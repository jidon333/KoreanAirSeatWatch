from datetime import date
import unittest
from playwright.sync_api import sync_playwright
from seatwatch.booking import choose_date


class CalendarTransitionTest(unittest.TestCase):
    def test_month_id_wins_over_stale_accessibility_month_text(self):
        # Reproduces a transition where visible labels still describe Oct/Nov
        # while the actual calendar nodes already represent Nov/Dec.
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page()
                page.set_content('''<button id="departure" onclick="document.querySelector('[role=dialog]').hidden=false">출발일 12월 11일 (금)</button>
                <section role="dialog" aria-label="출발일" hidden>
                  <div>2026년 10월</div><div id="month202611"><div role="application">
                    <button onclick="choose(11)">11일, 수요일</button></div></div>
                  <div>2026년 11월</div><div id="month202612"><div role="application">
                    <button onclick="choose(12)">11일, 금요일</button></div></div>
                </section><script>function choose(month){
                  document.getElementById('departure').textContent='출발일 '+month+'월 11일 (수)';
                  document.querySelector('[role=dialog]').hidden=true;
                }</script>''')
                choose_date(page,date(2026,11,11))
                self.assertEqual(page.locator('#departure').inner_text(),'출발일 11월 11일 (수)')
            finally:
                browser.close()
