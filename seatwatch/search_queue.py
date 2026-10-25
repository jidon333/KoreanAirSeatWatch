"""Persistent round-robin conditions; one global cooldown, including manual searches."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from .model import Condition
from .session import atomic_json


class SearchQueue:
    def __init__(self, path, initial, clock=time.time):
        self.path, self.clock = Path(path), clock
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        else:
            self.data = {'conditions': [asdict(initial)], 'interval_minutes': 5,
                         'next_key': initial.key, 'not_before': 0, 'results': {}}
        self.conditions = [Condition(**x) for x in self.data['conditions']]
        self.validate_interval(self.data['interval_minutes'])
        self.save()

    @staticmethod
    def validate_interval(value):
        if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 60:
            raise ValueError('전체 조회 간격은 5~60분으로 입력해 주세요.')
        return value

    @property
    def interval(self):
        return self.data['interval_minutes'] * 60

    @property
    def next_condition(self):
        return next((c for c in self.conditions if c.key == self.data['next_key']),
                    self.conditions[0] if self.conditions else None)

    @property
    def not_before(self):
        return self.data.get('not_before', 0)

    def save(self):
        self.data['conditions'] = [asdict(c) for c in self.conditions]
        atomic_json(self.path, self.data)

    def set_interval(self, minutes):
        self.validate_interval(minutes)
        self.data['interval_minutes'] = minutes
        # Increasing an interval also extends a cooldown already in progress.
        self.data['not_before'] = max(self.not_before, self.data.get('last_finished', 0) + self.interval)
        self.save()

    def put(self, condition, replace_key=None):
        if any(c.key == condition.key and c.key != replace_key for c in self.conditions):
            raise ValueError('이미 등록된 조건입니다.')
        if replace_key:
            index = next(i for i, c in enumerate(self.conditions) if c.key == replace_key)
            self.conditions[index] = condition
            if self.data['next_key'] == replace_key:
                self.data['next_key'] = condition.key
        else:
            self.conditions.append(condition)
        if self.next_condition:
            self.data['next_key'] = self.next_condition.key
        self.save()

    def remove(self, key):
        index = next(i for i, c in enumerate(self.conditions) if c.key == key)
        following = self.conditions[(index + 1) % len(self.conditions)]
        self.conditions.pop(index)
        if self.data['next_key'] == key:
            self.data['next_key'] = following.key if self.conditions else None
        self.save()

    def started(self):
        # A crash during a query must not permit an immediate restart burst.
        self.data['not_before'] = self.clock() + self.interval
        self.save()

    def finished(self, condition, result, advance=False):
        self.data['last_finished'] = self.clock()
        self.data['not_before'] = self.clock() + self.interval
        entry = self.data['results'].setdefault(condition.key, {})
        entry['state'] = result.get('state', 'UNVERIFIED')
        entry['checked_at'] = result.get('checked_at') or datetime.now(timezone.utc).isoformat()
        entry['message'] = result.get('message', '')
        if result.get('state') == 'OK':
            entry['success'] = result
            if advance and condition in self.conditions:
                index = self.conditions.index(condition)
                self.data['next_key'] = self.conditions[(index + 1) % len(self.conditions)].key
        # Failed conditions retain the turn for bounded retries; auth/block pauses all.
        self.save()

    def result(self, condition):
        return self.data['results'].get(condition.key, {})
