import tempfile
import unittest

from swarm.state import State


class LeaderboardTests(unittest.TestCase):
    def test_priority_weighting_rejection_failure_and_pending_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            state=State(directory)
            cases=[('high','accept','applied'),('medium','accept','applied'),
                   ('low','accept','applied'),('high','accept','failed'),
                   ('high','reject','not_requested'),('high','accept','pending')]
            for i,(priority,decision,status) in enumerate(cases):
                task=f'I{i}'
                state.data['tasks'][task]=dict(priority=priority)
                state.data['submissions'].append(dict(worker='worker_1',task_id=task))
                state.data['decisions'].append(dict(worker='worker_1',task_id=task,
                    decision=decision,rating=4,application=dict(status=status)))
            row=state.board(['worker_1'])[0]
            self.assertEqual(row['submissions'],6)
            self.assertEqual(row['accepted'],5)
            self.assertEqual(row['rejected'],1)
            self.assertEqual(row['applied'],3)
            self.assertEqual(row['patches_failed_to_apply'],1)
            self.assertEqual([row[p+'_applied'] for p in ['high','medium','low']],[1,1,1])
            self.assertEqual(row['impact_score'],6)
            self.assertEqual(row['average_rating'],4)

    def test_unknown_priority_does_not_invent_impact(self):
        with tempfile.TemporaryDirectory() as directory:
            state=State(directory)
            state.data['decisions']=[dict(worker='worker_1',task_id='legacy',decision='accept',
                rating=5,application=dict(status='applied'))]
            row=state.board(['worker_1'])[0]
            self.assertEqual(row['applied'],1)
            self.assertEqual(row['impact_score'],0)
