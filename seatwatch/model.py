from dataclasses import dataclass
from datetime import date, timedelta
import re


@dataclass(frozen=True)
class Condition:
    origin: str
    destination: str
    departure: str
    cabin: str = '프레스티지석'
    adults: int = 1

    def __post_init__(self):
        for code in (self.origin, self.destination):
            if not re.fullmatch(r'[A-Z]{3}', code):
                raise ValueError('공항은 영문 대문자 3자리로 입력해 주세요.')
        if self.origin == self.destination:
            raise ValueError('출발지와 도착지는 달라야 합니다.')
        date.fromisoformat(self.departure)
        if self.cabin not in ('일반석', '프리미엄석', '프레스티지석', '일등석'):
            raise ValueError('지원하지 않는 좌석 등급입니다.')
        if not 1 <= self.adults <= 9:
            raise ValueError('성인 인원은 1~9명으로 입력해 주세요.')

    @property
    def key(self):
        return '|'.join(map(str, (self.origin, self.destination, self.departure, self.cabin, self.adults)))

    @property
    def label(self):
        return f'{self.origin} → {self.destination} / {self.departure} / {self.cabin} / 성인 {self.adults}명'


def expand_dates(origin, destination, start, end, cabin, adults):
    first = date.fromisoformat(start)
    last = date.fromisoformat(end or start)
    if last < first:
        raise ValueError('종료일은 시작일보다 빠를 수 없습니다.')
    if (last - first).days >= 31:
        raise ValueError('초기 버전은 한 조건에 최대 31일을 지원합니다.')
    return [Condition(origin.upper().strip(), destination.upper().strip(),
                      (first + timedelta(days=i)).isoformat(), cabin, int(adults))
            for i in range((last - first).days + 1)]


@dataclass(frozen=True)
class Observation:
    condition_key: str
    status: str  # OK, LOGIN_REQUIRED, BLOCKED, NETWORK_ERROR, UI_CHANGED
    seats: tuple = ()  # Confirmed bookable flight identifiers, never waiting list.
    checked_at: str = ''
    detail: str = ''

    def __post_init__(self):
        if self.status not in ('OK', 'LOGIN_REQUIRED', 'BLOCKED', 'NETWORK_ERROR', 'UI_CHANGED'):
            raise ValueError('알 수 없는 조회 상태')
        if self.status != 'OK' and self.seats:
            raise ValueError('실패한 조회에는 좌석 결과를 포함할 수 없습니다.')
