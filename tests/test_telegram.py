import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from seatwatch.telegram import send_message


class TelegramContract(unittest.TestCase):
    def test_server_acceptance_required(self):
        payload = io.BytesIO(b'{"ok":true,"result":{"message_id":42}}')
        with patch('urllib.request.urlopen', return_value=payload):
            self.assertEqual(send_message('123', 'test', token='123:secret'), 42)

    def test_api_error_does_not_expose_token(self):
        secret_url = 'https://api.telegram.org/bot123:secret/sendMessage'
        with patch('urllib.request.urlopen', side_effect=HTTPError(secret_url, 401, 'bad', {}, None)):
            with self.assertRaises(RuntimeError) as caught:
                send_message('123', 'test', token='123:secret')
        self.assertNotIn('secret', str(caught.exception))
        self.assertIn('401', str(caught.exception))

    def test_network_failure_does_not_expose_url(self):
        with patch('urllib.request.urlopen', side_effect=URLError('secret url')):
            with self.assertRaises(RuntimeError) as caught:
                send_message('123', 'test', token='123:secret')
        self.assertNotIn('secret', str(caught.exception))

    def test_http_ok_without_message_id_is_not_delivery(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"ok":true,"result":{}}')):
            with self.assertRaises(RuntimeError):
                send_message('123', 'test', token='123:secret')


if __name__ == '__main__':
    unittest.main()
