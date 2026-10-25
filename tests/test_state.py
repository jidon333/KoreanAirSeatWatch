import tempfile
import unittest
from pathlib import Path
from seatwatch.model import Condition, Observation, expand_dates
from seatwatch.state import Ledger


class LedgerContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.ledger = Ledger(self.path)
        self.condition = Condition('ICN', 'ATL', '2027-03-10')

    def record(self, status='OK', seats=()):
        self.ledger.record(self.condition, Observation(self.condition.key, status, seats, '2026-10-25T12:00:00Z'))

    def test_failure_is_not_sold_out_or_reappearance(self):
        self.record(seats=('KE035',))
        message = self.ledger.pending()[0]
        self.ledger.delivered(message['id'])
        self.record('LOGIN_REQUIRED')
        self.assertEqual(self.ledger.data['conditions'][self.condition.key]['known_seats'], ['KE035'])
        self.record(seats=('KE035',))
        self.assertEqual(self.ledger.pending(), [])

    def test_real_disappearance_and_reappearance_alerts_again(self):
        self.record(seats=('KE035',))
        self.ledger.delivered(self.ledger.pending()[0]['id'])
        self.record()
        self.record(seats=('KE035',))
        self.assertEqual(len(self.ledger.pending()), 1)

    def test_failed_delivery_survives_restart_without_duplicate(self):
        self.record(seats=('KE035',))
        mid = self.ledger.pending()[0]['id']
        self.ledger.failed(mid)
        self.ledger = Ledger(self.path)
        self.record(seats=('KE035',))
        self.assertEqual(len(self.ledger.pending()), 1)
        self.assertEqual(self.ledger.pending()[0]['attempts'], 1)

    def test_wrong_condition_does_not_mutate_ledger(self):
        with self.assertRaises(ValueError):
            self.ledger.record(self.condition, Observation('wrong', 'OK', ('KE035',)))
        self.assertFalse(self.path.exists())

    def test_unknown_result_cannot_be_success(self):
        with self.assertRaises(ValueError):
            Observation(self.condition.key, 'UNKNOWN')
        with self.assertRaises(ValueError):
            Observation(self.condition.key, 'UI_CHANGED', ('KE035',))

    def test_date_range_boundaries(self):
        self.assertEqual(len(expand_dates('ICN','ATL','2027-01-30','2027-02-02','일반석',1)),4)
        with self.assertRaises(ValueError):
            expand_dates('ICN','ATL','2027-02-02','2027-01-30','일반석',1)


if __name__ == '__main__':
    unittest.main()
