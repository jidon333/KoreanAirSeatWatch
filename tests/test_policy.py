import unittest
from seatwatch.policy import classify_response


class AccessFailureContract(unittest.TestCase):
    def test_403_is_not_auth_retry_or_empty_seats(self):
        failure = classify_response(403, 'text/html', '<h1>Access Denied</h1>')
        self.assertEqual(failure.state, 'BLOCKED')
        self.assertFalse(failure.may_retry)

    def test_html_denial_with_ok_status_is_still_blocked(self):
        self.assertEqual(classify_response(200, 'text/html', 'Access Denied').state, 'BLOCKED')

    def test_auth_and_rate_limit_are_separate(self):
        self.assertEqual(classify_response(401).state, 'LOGIN_REQUIRED')
        self.assertEqual(classify_response(429).state, 'BLOCKED')

    def test_server_fault_allows_bounded_retry(self):
        self.assertTrue(classify_response(503).may_retry)
        self.assertIsNone(classify_response(200, 'application/json'))


if __name__ == '__main__':
    unittest.main()
