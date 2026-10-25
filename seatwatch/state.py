"""Durable observation ledger and retryable notification outbox."""
from pathlib import Path
import json
import uuid
from .session import atomic_json


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
            'conditions': {}, 'outbox': []}

    def record(self, condition, observation):
        if observation.condition_key != condition.key:
            raise ValueError('조회 조건과 결과의 조건이 다릅니다.')
        row = self.data['conditions'].setdefault(condition.key, {'known_seats': []})
        row.update(status=observation.status, detail=observation.detail,
                   last_attempt=observation.checked_at)
        if observation.status == 'OK':
            current = set(observation.seats)
            appeared = current - set(row['known_seats'])
            row.update(known_seats=sorted(current), last_success=observation.checked_at)
            if appeared:
                self.data['outbox'].append({
                    'id': str(uuid.uuid4()), 'condition_key': condition.key,
                    'text': '대한항공 보너스 좌석 발견\n' + condition.label + '\n' +
                            '\n'.join(sorted(appeared)) + '\n확인 시각: ' + observation.checked_at +
                            '\n예약 전 대한항공에서 다시 확인해 주세요.',
                    'attempts': 0, 'created_at': observation.checked_at,
                })
        atomic_json(self.path, self.data)

    def pending(self):
        return list(self.data['outbox'])

    def delivered(self, message_id):
        self.data['outbox'] = [x for x in self.data['outbox'] if x['id'] != message_id]
        atomic_json(self.path, self.data)

    def failed(self, message_id):
        for item in self.data['outbox']:
            if item['id'] == message_id:
                item['attempts'] += 1
        atomic_json(self.path, self.data)
