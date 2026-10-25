"""Bounded local diagnostics for the running development browser worker."""
import json
import sys
import time
import uuid
from .session import ROOT, atomic_json
from .lifecycle import BrowserClosed, read_status, worker_connected


def command(step, timeout=45, payload=None):
    from .single_instance import SingleInstance
    with SingleInstance('KoreanAirSeatWatch_Command'):
        return _command(step, timeout, payload)


def _command(step, timeout=45, payload=None):
    owner = read_status()
    if not worker_connected(owner):
        raise BrowserClosed('전용 브라우저가 닫혔습니다. 지금 조회를 누르면 다시 엽니다.')
    request = ROOT / 'work' / 'command.json'
    if request.exists():
        raise RuntimeError('이전 진단 명령이 아직 대기 중입니다.')
    request_id = str(uuid.uuid4())
    atomic_json(request, {**(payload or {}), 'id': request_id, 'action': 'collect', 'step': step,
                         'worker_id':owner['worker_id'], 'expires_at':time.time()+timeout})
    result_file = ROOT / 'work' / 'command-result.json'
    until = time.monotonic() + timeout
    try:
        while time.monotonic() < until:
            if result_file.exists():
                result = json.loads(result_file.read_text(encoding='utf-8'))
                if result.get('id') == request_id:
                    return result
            current = read_status()
            if current.get('worker_id') != owner['worker_id'] or not worker_connected(current):
                raise BrowserClosed('조회 중 전용 브라우저가 닫혔습니다. 지금 조회를 누르면 다시 엽니다.')
            time.sleep(0.25)
        raise TimeoutError('조회 응답 대기 시간 초과. 자동 확인을 중지했습니다.')
    finally:
        # Cancel only our unclaimed request. A replacement worker must not replay it.
        try:
            if request.exists() and json.loads(request.read_text(encoding='utf-8')).get('id') == request_id:
                request.unlink()
        except FileNotFoundError:
            pass


if __name__ == '__main__':
    print(json.dumps(command(sys.argv[1]), ensure_ascii=False, indent=2))
