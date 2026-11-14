"""Isolated tests; never touch the user's saved calibration or live sensors."""
import copy
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import movement_learning as learning


class CumulativeCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = learning.DB_FILE
        learning.DB_FILE = Path(self.tmp.name) / 'trials.sqlite3'
        learning.RECENT.clear()
        self.legacy = {'created_at': time.time(), 'nodes': {
            n: {'median': 5, 'threshold': 10, 'sample_count': 40} for n in ('1', '2')}}

    def tearDown(self):
        learning.DB_FILE = self.old_db
        self.tmp.cleanup()

    def payload(self, label, values=(5, 5), purpose='learn', suffix='a'):
        revision = learning.get_model(self.legacy)['revision']
        start = time.time() - 21
        return {'id': f'test-trial-{suffix}', 'label': label, 'purpose': purpose, 'revision': revision,
                'samples': [{'values': {'1': values[0], '2': values[1]},
                             'timestamps': {'1': start + i, '2': start + i}} for i in range(20)]}

    def test_additive_persistence_idempotency_and_checks_do_not_train(self):
        quiet = self.payload('still')
        learning.add_trial(quiet, self.legacy)
        learning.add_trial(quiet, self.legacy)
        result = learning.add_trial(self.payload('walk_node1', (15, 5), suffix='b'), self.legacy)
        self.assertEqual(result['total_trials'], 2)
        self.assertEqual(result['counts']['still'], 1)
        self.assertEqual(result['counts']['walk_node1'], 1)
        model = copy.deepcopy(result['model'])
        result = learning.add_trial(self.payload('still', purpose='check', suffix='c'), self.legacy)
        self.assertEqual(model, result['model'])
        self.assertEqual(result['checks']['still']['trials'], 1)
        self.assertEqual(result['counts']['still'], 1)
        self.assertEqual(learning.get_status(self.legacy)['total_trials'], 3)

    def test_bad_trial_can_be_excluded_without_erasing_history(self):
        learning.add_trial(self.payload('still'), self.legacy)
        walking = self.payload('walk_room', (15, 15), suffix='b')
        learning.add_trial(walking, self.legacy)
        result = learning.exclude_trial(walking['id'], self.legacy)
        self.assertEqual(result['total_trials'], 2)
        self.assertEqual(result['counts']['walk_room'], 0)
        self.assertEqual(result['model']['nodes'], self.legacy['nodes'])
        self.assertTrue(next(t for t in result['history'] if t['id'] == walking['id'])['excluded'])

    def test_duplicate_frames_and_wrong_revision_rejected(self):
        trial = self.payload('still')
        trial['samples'][1]['timestamps'] = dict(trial['samples'][0]['timestamps'])
        with self.assertRaises(ValueError):
            learning.add_trial(trial, self.legacy)
        trial = self.payload('still')
        trial['revision'] = 999
        with self.assertRaises(ValueError):
            learning.add_trial(trial, self.legacy)
        self.assertEqual(learning.get_status(self.legacy)['total_trials'], 0)

    def test_two_paths_learn_and_isolated_spike_does_not_trigger(self):
        quiet = self.payload('still')
        quiet['samples'][8]['values'] = {'1': 100, '2': 100}
        learning.add_trial(quiet, self.legacy)
        learning.add_trial(self.payload('walk_node1', (15, 5), suffix='b'), self.legacy)
        result = learning.add_trial(self.payload('walk_node2', (5, 15), suffix='c'), self.legacy)
        self.assertEqual(result['model']['training']['false_trigger_rate'], 0)
        self.assertEqual(result['model']['training']['walk_response_rate'], 1)
        self.assertFalse(any(learning.responses(quiet['samples'], result['model']['nodes'])))

    def test_confirmation_deduplicates_polls_and_resets_after_loss(self):
        self.assertFalse(learning.confirmed('1', {'ts': 100, 'change': 20}, 10, 0))
        self.assertFalse(learning.confirmed('1', {'ts': 100, 'change': 20}, 10, 0))
        self.assertTrue(learning.confirmed('1', {'ts': 101, 'change': 20}, 10, 0))
        self.assertFalse(learning.confirmed('1', None, 10, 0))
        self.assertFalse(learning.confirmed('1', {'ts': 102, 'change': 20}, 10, 0))
        self.assertFalse(learning.confirmed('1', {'ts': 103, 'change': 20}, 10, 1))


if __name__ == '__main__':
    unittest.main()
