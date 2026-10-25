import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from seatwatch import telegram


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.patch=patch.object(telegram,'ROOT',self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        keys=patch.object(telegram,'keyring')
        keys.start()
        self.addCleanup(keys.stop)
        self.pair={'username':'example_bot','code':'unique-test-code','created_at':int(time.time())}

    def update(self,code=None,chat_type='private',chat_id=123,sender=123):
        return {'message':{'chat':{'id':chat_id,'type':chat_type},'from':{'id':sender},
            'date':int(time.time()),'text':'/start '+(code or self.pair['code'])}}

    def test_only_current_explicit_private_chat_is_bound(self):
        updates=[self.update(code='wrong'),self.update(chat_type='group'),self.update(sender=456),self.update()]
        with patch.object(telegram,'call',return_value=updates),patch.object(telegram,'send_message',return_value=5) as send:
            telegram.finish_pairing(self.pair)
        self.assertEqual(send.call_args.args[0],'123')
        self.assertTrue(telegram.config()['enabled'])
        self.assertEqual(telegram.config()['test_message_id'],5)

    def test_no_match_or_multiple_matches_never_send(self):
        for updates in ([self.update(code='wrong')],[self.update(),self.update(chat_id=456,sender=456)]):
            with patch.object(telegram,'call',return_value=updates),patch.object(telegram,'send_message') as send:
                with self.assertRaises(ValueError):
                    telegram.finish_pairing(self.pair)
                send.assert_not_called()
        self.assertFalse(telegram.config()['enabled'])

    def test_expired_pair_never_contacts_server(self):
        self.pair['created_at']-=601
        with patch.object(telegram,'call') as call:
            with self.assertRaises(ValueError):
                telegram.finish_pairing(self.pair)
            call.assert_not_called()

    def test_failed_test_delivery_never_enables_notifications(self):
        with patch.object(telegram,'call',return_value=[self.update()]),patch.object(telegram,'send_message',side_effect=RuntimeError('network')):
            with self.assertRaises(RuntimeError):
                telegram.finish_pairing(self.pair)
        self.assertFalse(telegram.config()['enabled'])

    def test_token_goes_only_to_credential_store_not_json(self):
        token='123:test-token'
        with patch.object(telegram,'call',return_value={'is_bot':True,'username':'example_bot'}),patch.object(telegram,'save_token') as save:
            pair=telegram.begin_pairing(token)
            save.assert_called_once_with(token)
        self.assertNotIn(token,str(pair))
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(token,path.read_text(encoding='utf-8'))
