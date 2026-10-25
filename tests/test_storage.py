import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from seatwatch.session import atomic_json


class StorageTests(unittest.TestCase):
    def test_transient_windows_reader_lock_retries_without_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'state.json'
            atomic_json(target,{'before':True})
            replace=Path.replace
            attempts=[]
            def locked_then_ready(path,destination):
                attempts.append(1)
                if len(attempts)<3:
                    self.assertEqual(json.loads(target.read_text()),{'before':True})
                    raise PermissionError('Windows reader lock')
                return replace(path,destination)
            with patch.object(Path,'replace',locked_then_ready):
                atomic_json(target,{'after':True})
            self.assertEqual(json.loads(target.read_text()),{'after':True})
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    def test_permanent_lock_preserves_previous_state_and_fails_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'state.json'
            atomic_json(target,{'before':True})
            with patch.object(Path,'replace',side_effect=PermissionError('locked')) as replace:
                with self.assertRaises(PermissionError):
                    atomic_json(target,{'after':True})
                self.assertEqual(replace.call_count,6)
            self.assertEqual(json.loads(target.read_text()),{'before':True})
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])
