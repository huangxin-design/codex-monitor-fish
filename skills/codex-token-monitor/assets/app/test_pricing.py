import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from monitor import Monitor, parse_records
from pricing import PRICING, add_costs, response_cost, zero_cost


def context(model):
    return {'type': 'turn_context', 'payload': {'model': model}}


def record(ident, thread='task', input_tokens=100_000, **extra):
    return {'timestamp': '2026-09-07T17:00:00Z', 'type': 'token_usage_record',
            'payload': {'thread_id': thread, 'response_id': ident, 'usage': {
                'input_tokens': input_tokens, 'cached_input_tokens': 40_000,
                'output_tokens': 10_000, 'reasoning_output_tokens': 9_000,
                'total_tokens': input_tokens + 10_000}, **extra}}


def write_log(path, records, tail=b''):
    path.write_bytes(b''.join(json.dumps(r).encode() + b'\n' for r in records) + tail)


class PricingTests(unittest.TestCase):
    def parse(self, records, tail=b'', cutoff=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'task.jsonl'
            write_log(path, records, tail)
            return parse_records(path, 'task', cutoff)

    def test_cache_and_reasoning_are_subsets_not_extra_charges(self):
        cost = response_cost(record('a')['payload']['usage'])
        self.assertAlmostEqual(cost['usd'], .00275)
        self.assertEqual(cost['priced_responses'], 1)
        self.assertFalse(cost['incomplete'])

    def test_subscription_allocation_matches_user_examples(self):
        for tokens, expected in ((0, 0), (100_000_000, 2.5), (8_000_000_000, 200)):
            with self.subTest(tokens=tokens):
                data = self.parse([record('a', usage={
                    'input_tokens': tokens, 'cached_input_tokens': 0,
                    'output_tokens': 0, 'reasoning_output_tokens': 0, 'total_tokens': tokens})])
                self.assertEqual(data['cost']['usd'], expected)
                self.assertFalse(data['cost']['incomplete'])
        self.assertEqual(PRICING['method'], 'subscription_allocation')
        self.assertEqual(PRICING['period_tokens'], PRICING['weekly_tokens'] * PRICING['weeks'])

    def test_model_switches_deduplication_and_inherited_records(self):
        data = self.parse([context('gpt-6-astra'), record('a'), record('a'),
                           record('inherited', thread='parent'),
                           context('gpt-5.6-luna'), record('b'), record('a')])
        self.assertAlmostEqual(data['cost']['usd'], .0055)
        self.assertEqual(data['cost']['priced_responses'], 2)
        self.assertEqual(data['usage']['total_tokens'], 220_000)

    def test_record_model_wins_over_context_without_changing_next_record(self):
        data = self.parse([context('gpt-6-astra'),
                           record('a', model='gpt-5.6-luna'), record('b')])
        self.assertAlmostEqual(data['cost']['usd'], .0055)
        self.assertEqual(data['models'], ['gpt-5.6-luna', 'gpt-6-astra'])

    def test_missing_or_unknown_models_use_the_same_allocation(self):
        for model in (None, 'unpublished-model', 'gpt-6-astra-future', ['invalid']):
            with self.subTest(model=model):
                data = self.parse([record('a', model=model)])
                self.assertEqual(data['cost'], {**zero_cost(), 'usd': .00275, 'priced_responses': 1})
                self.assertEqual(data['usage']['total_tokens'], 110_000)
                self.assertFalse(data.get('unavailable', False))

    def test_context_without_model_still_allocates_all_tokens(self):
        data = self.parse([context('gpt-6-astra'), record('a'),
                           {'type': 'turn_context', 'payload': {}}, record('b')])
        self.assertAlmostEqual(data['cost']['usd'], .0055)
        self.assertEqual(data['cost']['priced_responses'], 2)
        self.assertEqual(data['cost']['unpriced_responses'], 0)
        self.assertFalse(data['cost']['incomplete'])

    def test_long_context_has_no_surcharge(self):
        short = response_cost(record('a', input_tokens=272_000)['payload']['usage'])
        long = response_cost(record('b', input_tokens=272_001)['payload']['usage'])
        self.assertAlmostEqual(short['usd'], .00705)
        self.assertAlmostEqual(long['usd'], .007050025)
        self.assertAlmostEqual(long['usd'] - short['usd'], .000000025, places=12)

    def test_partial_and_invalid_records_preserve_known_subset(self):
        broken = record('bad')
        broken['payload']['usage']['total_tokens'] = 1
        for records, tail in (([broken], b''), ([], b'partial'), ([], b'broken\n')):
            data = self.parse([context('gpt-6-astra'), record('a'), *records], tail)
            self.assertAlmostEqual(data['cost']['usd'], .00275)
            self.assertTrue(data['cost']['incomplete'])
            self.assertEqual(data['cost']['priced_responses'], 1)

    def test_unknown_cost_and_empty_cutoff_are_distinct(self):
        unknown = self.parse([])['cost']
        empty = self.parse([context('gpt-6-astra'), record('a')],
                           cutoff='2026-09-07T16:00:00Z')['cost']
        self.assertTrue(unknown['incomplete'])
        self.assertEqual(empty, zero_cost())
        self.assertEqual(add_costs(empty, unknown), unknown)

    def test_task_and_child_costs_aggregate_once_and_mark_missing_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            with closing(sqlite3.connect(home / 'state_5.sqlite')) as conn:
                conn.execute('CREATE TABLE threads (id TEXT, name TEXT, rollout_path TEXT, model TEXT, '
                             'created_at_ms INTEGER, cwd TEXT, thread_source TEXT, archived INTEGER)')
                conn.execute('CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT)')
                for ident, source, model in (('root', 'user', 'gpt-6-astra'),
                                              ('child', 'subagent', 'gpt-5.6-luna'),
                                              ('unknown', 'subagent', 'unknown-model')):
                    path = home / (ident + '.jsonl')
                    write_log(path, [context(model), record(ident, thread=ident)])
                    conn.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)',
                                 (ident, ident, str(path), 'gpt-6-astra', 0, '/demo', source, 0))
                conn.executemany('INSERT INTO thread_spawn_edges VALUES (?,?)',
                                 [('root', 'child'), ('root', 'child'), ('root', 'unknown'), ('child', 'missing')])
                conn.commit()
            monitor = Monitor({'codex_home': str(home), 'project_directory': '/demo'})
            data = monitor.snapshot()
            task = data['tasks'][0]
            self.assertAlmostEqual(task['self_cost']['usd'], .00275)
            self.assertAlmostEqual(task['children_cost']['usd'], .0055)
            self.assertFalse(task['self_cost']['incomplete'])
            self.assertTrue(task['children_cost']['incomplete'])
            self.assertEqual(task['total_cost']['priced_responses'], 3)
            self.assertEqual(task['total_cost']['unpriced_responses'], 0)
            self.assertEqual(data['cost_totals']['total'], task['total_cost'])
            self.assertEqual(data['totals']['total_tokens'], 330_000)
            self.assertTrue(data['incomplete'])
            (home / 'root.jsonl').unlink()
            missing = monitor.snapshot()['tasks'][0]
            self.assertEqual(missing['self_cost'], zero_cost(True))
            self.assertAlmostEqual(missing['total_cost']['usd'], .0055)


if __name__ == '__main__':
    unittest.main()
