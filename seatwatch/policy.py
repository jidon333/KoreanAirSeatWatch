"""Keep authentication errors distinct from server access denial and no seats."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Failure:
    state: str
    message: str
    may_retry: bool


def classify_response(status, content_type='', body=''):
    if status == 403 or 'access denied' in body.lower():
        return Failure('BLOCKED', '대한항공이 좌석 조회 접근을 거부했습니다. 자동 감시를 중단합니다.', False)
    if status == 429:
        return Failure('BLOCKED', '대한항공이 요청 횟수를 제한했습니다. 자동 감시를 중단합니다.', False)
    if status == 401:
        return Failure('LOGIN_REQUIRED', '대한항공 로그인이 만료되었습니다.', False)
    if status >= 500:
        return Failure('NETWORK_ERROR', '대한항공 서버가 조회를 완료하지 못했습니다.', True)
    if status >= 400:
        return Failure('UI_CHANGED', '좌석 검색 요청이 거부되었습니다. 조건과 화면 확인이 필요합니다.', False)
    if 'text/html' in content_type:
        return Failure('UI_CHANGED', '좌석 데이터 대신 다른 화면이 반환되었습니다.', False)
    return None
