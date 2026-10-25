"""Telegram transport. Tokens are never included in returned error text."""
import json
import urllib.request
import urllib.error
import re
import keyring

SERVICE = 'KoreanAirSeatWatch'


def save_token(token):
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token.strip()):
        raise ValueError('텔레그램 봇 토큰 형식을 확인해 주세요.')
    keyring.set_password(SERVICE, 'telegram-token', token.strip())


def send_message(chat_id, text, token=None):
    token = token or keyring.get_password(SERVICE, 'telegram-token')
    if not token or not re.fullmatch(r'-?\d+', str(chat_id)):
        raise ValueError('봇 토큰과 숫자 채팅 ID를 설정해 주세요.')
    request = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=json.dumps({'chat_id': str(chat_id), 'text': text}).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not expose request URL, bot token, or raw response to the UI/logs.
        raise RuntimeError(f'텔레그램이 전송을 거부했습니다 (HTTP {exc.code}).') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('텔레그램 연결 실패. 다음 확인 때 재시도합니다.') from None
    if not result.get('ok') or not result.get('result', {}).get('message_id'):
        raise RuntimeError('텔레그램의 전송 수락을 확인하지 못했습니다.')
    return result['result']['message_id']
