import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from seatwatch import collector
from seatwatch.session import BrowserSession


class BlockGateTests(unittest.TestCase):
    def test_denial_persists_and_prevents_query_after_worker_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(collector, 'ROOT', root):
                collector.latch_access_denial(SimpleNamespace())
                evidence = json.loads((root / 'data/access-block.json').read_text(encoding='utf-8'))
                self.assertEqual(evidence['response']['status'], 403)
                fresh_worker = SimpleNamespace(page=object())
                result = collector.run(fresh_worker, {'step': 'retry_search_diagnose'})
                self.assertEqual(result['state'], 'BLOCKED')
            with patch('seatwatch.session.ROOT', root):
                self.assertEqual(BrowserSession().status()['state'], 'BLOCKED')

    def test_blocked_worker_can_still_be_inspected_without_query(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(collector, 'ROOT', Path(directory)):
            worker = SimpleNamespace(page=object(), access_blocked=True, describe=lambda: {'safe': True})
            self.assertEqual(collector.run(worker, {'step': 'inspect'}), {'safe': True})
