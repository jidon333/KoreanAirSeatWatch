"""Telegram transport. Tokens are never included in returned error text."""
import json
import urllib.request
import urllib.error
import re
import keyring
import secrets
import time
from .session import ROOT, atomic_json

SERVICE = 'KoreanAirSeatWatch'


def config():
    try:
        return json.loads((ROOT/'data'/'telegram.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'enabled':False}


def call(method, payload=None, token=None):
    token = token or keyring.get_password(SERVICE, 'telegram-token')
    if not token:
        raise ValueError('텔레그램 봇 토큰을 먼저 설정해 주세요.')
    request=urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}',
        data=json.dumps(payload or {}).encode('utf-8'),
        headers={'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=15) as response:
            result=json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'텔레그램이 요청을 거부했습니다 (HTTP {exc.code}).') from None
    except (urllib.error.URLError,TimeoutError,OSError,ValueError):
        raise RuntimeError('텔레그램 연결 또는 응답 확인에 실패했습니다.') from None
    if not isinstance(result,dict) or not result.get('ok'):
        raise RuntimeError('텔레그램이 요청을 수락하지 않았습니다.')
    return result.get('result')


def begin_pairing(token):
    token=token.strip()
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+',token):
        raise ValueError('봇 토큰 형식을 확인해 주세요.')
    bot=call('getMe',token=token)
    if not isinstance(bot,dict) or not bot.get('is_bot') or not bot.get('username'):
        raise RuntimeError('봇 정보를 확인하지 못했습니다.')
    save_token(token)
    credential_key='telegram-bot:'+bot['username']
    keyring.set_password(SERVICE,credential_key,token)
    # New credentials invalidate the previous destination until paired again.
    atomic_json(ROOT/'data'/'telegram.json',{'enabled':False})
    code='seatwatch_'+secrets.token_urlsafe(12)
    return {'username':bot['username'],'code':code,'created_at':int(time.time()),'credential_key':credential_key}


def finish_pairing(pair):
    if time.time()-pair['created_at']>600:
        raise ValueError('연결 코드가 만료되었습니다. 토큰 확인부터 다시 진행해 주세요.')
    token=keyring.get_password(SERVICE,pair.get('credential_key','telegram-token'))
    updates=call('getUpdates',{'limit':100,'timeout':0,'allowed_updates':['message']},token=token)
    matches=set()
    for update in updates or []:
        message=update.get('message',{})
        chat=message.get('chat',{})
        if (message.get('text')=='/start '+pair['code'] and chat.get('type')=='private'
                and message.get('from',{}).get('id')==chat.get('id')
                and message.get('date',0)>=pair['created_at']-5):
            matches.add(str(chat['id']))
    if len(matches)!=1:
        raise ValueError('연결 메시지를 아직 확인하지 못했습니다. 봇 대화에서 시작을 누른 뒤 다시 확인해 주세요.')
    chat_id=matches.pop()
    message_id=send_message(chat_id,'대한항공 좌석 알림 연결 확인\n이 대화로 좌석 발견과 로그인 필요 알림을 보내겠습니다.',token=token)
    atomic_json(ROOT/'data'/'telegram.json',{'enabled':True,'chat_id':chat_id,'bot_username':pair['username'],
        'verified_at':int(time.time()),'test_message_id':message_id,'credential_key':pair.get('credential_key','telegram-token')})
    return {'username':pair['username'],'message_id':message_id}


def save_token(token):
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token.strip()):
        raise ValueError('텔레그램 봇 토큰 형식을 확인해 주세요.')
    keyring.set_password(SERVICE, 'telegram-token', token.strip())


def send_message(chat_id, text, token=None):
    token = token or keyring.get_password(SERVICE, 'telegram-token')
    if not token or not re.fullmatch(r'-?\d+', str(chat_id)):
        raise ValueError('봇 토큰과 숫자 채팅 ID를 설정해 주세요.')
    result=call('sendMessage',{'chat_id':str(chat_id),'text':text},token=token)
    if not isinstance(result,dict) or not result.get('message_id'):
        raise RuntimeError('텔레그램의 전송 수락을 확인하지 못했습니다.')
    return result['message_id']


def send_configured(cfg,text):
    # Use the credential associated with this destination, even if another
    # settings window starts pairing a different bot while a delivery is pending.
    current=config()
    if not current.get('enabled') or any(current.get(k)!=cfg.get(k) for k in ('chat_id','bot_username','verified_at')):
        raise RuntimeError('텔레그램 연결 설정이 변경되어 전송을 보류했습니다.')
    token=keyring.get_password(SERVICE,cfg.get('credential_key','telegram-token'))
    if not token:
        raise ValueError('연결한 봇의 토큰이 없습니다. 텔레그램 연결을 다시 진행해 주세요.')
    return send_message(cfg['chat_id'],text,token=token)
