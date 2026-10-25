"""Durable notifications and bounded scheduling, independent of the desktop clock ticks."""
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
import uuid
from .model import Observation
from .session import atomic_json
from .state import Ledger
from . import telegram


class Monitor:
    INTERVAL = 15 * 60

    def __init__(self, path, clock=time.time, config=telegram.config, sender=None):
        self.ledger = Ledger(Path(path))
        self.clock, self.config, self.sender = clock, config, sender
        self.lock = threading.RLock()
        self.active = False
        self.paused = False
        self.failures = 0
        self.due_at = None
        self.delivery_status = ''
        # A restarted app waits for the person to resume; pending deliveries survive.
        previous = self.ledger.data.get('monitor', {})
        self.paused = previous.get('active', False) or previous.get('paused', False)

    def save(self):
        self.ledger.data['monitor'] = {'active': self.active, 'paused': self.paused,
            'failures': self.failures, 'due_at': self.due_at}
        atomic_json(self.ledger.path, self.ledger.data)

    def start(self):
        with self.lock:
            self.active, self.paused, self.failures = True, False, 0
            self.due_at = self.clock()
            self.save()

    def stop(self, paused=False):
        with self.lock:
            self.active, self.paused, self.due_at = False, paused, None
            self.save()

    def take_due(self):
        with self.lock:
            if self.active and self.due_at is not None and self.clock() >= self.due_at:
                self.due_at = None
                self.save()
                return True
            return False

    def binding(self):
        cfg = self.config()
        binding = [cfg.get('bot_username'), cfg.get('chat_id'), cfg.get('verified_at')] if cfg.get('enabled') else None
        if self.ledger.data.get('binding') != binding:
            # Never send an old account's queue to a newly paired destination.
            self.ledger.data['outbox'] = []
            self.ledger.data['binding'] = binding
            self.save()
        return cfg if binding else None

    def incident(self, condition, state, message, checked):
        key = condition.key + '|' + state
        if self.ledger.data.get('incident') != key:
            self.ledger.data['incident'] = key
            self.ledger.data['outbox'].append({'id':str(uuid.uuid4()), 'kind':'health',
                'condition_key':condition.key, 'text':'대한항공 자동 확인 안내\n'+condition.label+'\n'+message,
                'attempts':0, 'created_at':checked})

    def accept(self, condition, result):
        """Called only for an opted-in monitoring query. Never manufacture success."""
        with self.lock:
            if not self.active:
                return ''
            cfg = self.binding()
            state = result.get('state', 'UNVERIFIED')
            normalized = {'CLOSED':'NETWORK_ERROR', 'UNVERIFIED':'UI_CHANGED'}.get(state, state)
            if normalized not in ('OK','LOGIN_REQUIRED','BLOCKED','NETWORK_ERROR','UI_CHANGED'):
                normalized = 'UI_CHANGED'
            checked = result.get('checked_at') or datetime.now(timezone.utc).isoformat()
            history = self.ledger.data.setdefault('runs', [])
            history.append({'at':checked, 'state':normalized, 'condition_key':condition.key})
            self.ledger.data['runs'] = history[-120:]
            seats = tuple(sorted({r['flight'] for r in result.get('rows',[]) if r['available']})) if state == 'OK' else ()
            self.ledger.record(condition, Observation(condition.key, normalized, seats, checked, result.get('message','')))
            if state == 'OK':
                self.failures = 0
                self.ledger.data['incident'] = None
                # Drop stale seat notifications when a later successful query disproves them.
                self.ledger.data['outbox'] = [x for x in self.ledger.pending()
                    if x['condition_key'] != condition.key or (x.get('kind') != 'health'
                        and bool(seats) and set(x.get('seat_ids',seats)).issubset(seats))]
                self.due_at = self.clock() + self.INTERVAL
                message = '15분 뒤 다시 확인합니다.'
            elif normalized in ('LOGIN_REQUIRED','BLOCKED'):
                self.stop(paused=True)
                message = ('로그인이 필요합니다. 대한항공 창에서 로그인한 뒤 자동 확인 재개를 눌러 주세요.'
                           if normalized == 'LOGIN_REQUIRED' else '접근이 거부되어 자동 확인을 중단했습니다. 사이트 상태를 확인해 주세요.')
                self.incident(condition, normalized, message, checked)
            else:
                self.failures += 1
                delays = (300, 900) if normalized == 'NETWORK_ERROR' else (900,)
                if self.failures <= len(delays):
                    delay = delays[self.failures-1]
                    self.due_at = self.clock() + delay
                    message = f'조회 실패 · {delay//60}분 뒤 재시도합니다 ({self.failures}/{len(delays)}).'
                else:
                    self.stop(paused=True)
                    message = '조회 실패가 반복되어 자동 확인을 멈췄습니다. 대한항공 창을 확인한 뒤 재개해 주세요.'
                    self.incident(condition, 'REPEATED_FAILURE', message, checked)
            if not cfg:
                self.ledger.data['outbox'] = []
            self.save()
            return message

    def flush(self):
        """One bounded batch. Failures keep their original ID and use backoff."""
        with self.lock:
            cfg = self.binding()
            if not cfg:
                self.delivery_status = '텔레그램 미연결 · 이 창과 알림음으로만 알려드립니다.'
                return
            binding = self.ledger.data['binding'][:]
            pending = [dict(x) for x in self.ledger.pending() if x.get('retry_at',0) <= self.clock()][:3]
        for item in pending:
            with self.lock:
                cfg = self.binding()
                if not cfg or binding != self.ledger.data['binding']:
                    return
                if not any(x['id']==item['id'] for x in self.ledger.pending()):
                    continue
            try:
                message_id = (self.sender(cfg['chat_id'], item['text']) if self.sender
                              else telegram.send_configured(cfg,item['text']))
            except Exception:
                with self.lock:
                    self.ledger.failed(item['id'])
                    for current in self.ledger.data['outbox']:
                        if current['id'] == item['id']:
                            current['retry_at'] = self.clock() + min(1800, 60 * 2**min(current['attempts']-1,5))
                    self.delivery_status = '텔레그램 전송 실패 · 자동 재시도 대기 중입니다.'
                    self.save()
                break
            else:
                with self.lock:
                    self.ledger.delivered(item['id'])
                    self.ledger.data['last_delivery'] = {'at': self.clock(), 'message_id': message_id}
                    self.delivery_status = '텔레그램이 알림을 수락했습니다.'
                    self.save()
        with self.lock:
            if not pending and not self.ledger.pending():
                self.delivery_status = '텔레그램 연결됨 · 새 좌석과 재로그인 필요 시 알려드립니다.'
