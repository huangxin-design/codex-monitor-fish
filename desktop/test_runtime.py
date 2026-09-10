"""Desktop tests use synthetic Codex records and temporary preferences only."""
from contextlib import closing
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

from desktop import runtime


def synthetic_home(directory):
    directory.mkdir(parents=True)
    db = directory / 'state_5.sqlite'
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, rollout_path TEXT, '
                     'model TEXT, created_at_ms INTEGER, cwd TEXT, thread_source TEXT, archived INTEGER)')
        conn.execute('CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT)')
        rows = [
            ('root', 'Design', 'root.jsonl', 'test-model', 4, 'C:/Projects/Alpha', 'user', 0),
            ('archive', 'Old', 'archive.jsonl', 'test-model', 3, 'c:\\projects\\alpha', 'user', 1),
            ('child', 'Child', 'child.jsonl', 'test-model', 2, 'C:/Other', 'user', 0),
            ('other', 'Other', 'other.jsonl', 'test-model', 1, '/projects/beta', 'user', 0),
            ('agent', 'Agent', 'agent.jsonl', 'test-model', 5, '/projects/agent', 'agent', 0),
            ('bad', 'Missing path', 'bad.jsonl', 'test-model', 6, '', 'user', 0),
        ]
        conn.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)', rows)
        conn.execute('INSERT INTO thread_spawn_edges VALUES (?,?)', ('root', 'child'))
        conn.commit()
    for ident, _, file, *_ in rows:
        amount = 100 if ident == 'root' else 50
        record = {'type': 'token_usage_record', 'timestamp': '2026-01-01T00:00:00Z',
                  'payload': {'thread_id': ident, 'response_id': 'response-' + ident,
                              'usage': {'input_tokens': amount, 'cached_input_tokens': 25,
                                        'output_tokens': 20, 'reasoning_output_tokens': 5,
                                        'total_tokens': amount + 20}}}
        (directory / file).write_text((json.dumps(record) + '\n') * 2, encoding='utf-8')
    return directory


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.home = synthetic_home(self.base / 'synthetic-codex')
        self.data = self.base / 'preferences'
        self.state = runtime.DesktopState(self.data, self.home)

    def tearDown(self):
        self.temporary.cleanup()

    def test_discovery_groups_normalized_paths_and_includes_archived_roots(self):
        projects = runtime.discover_projects(self.home)
        self.assertEqual(projects, [
            {'directory': 'C:/Projects/Alpha', 'name': 'Alpha', 'task_count': 2},
            {'directory': '/projects/beta', 'name': 'beta', 'task_count': 1},
        ])

    def test_discovery_opens_readonly_database_and_closes_connection(self):
        database = self.home / 'state_5.sqlite'
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        connect = sqlite3.connect
        connections = []

        def capture(database_uri, **kwargs):
            self.assertTrue(database_uri.endswith('?mode=ro'))
            self.assertTrue(kwargs['uri'])
            conn = connect(database_uri, **kwargs)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE threads SET name='must not write'")
            connections.append(conn)
            return conn

        with patch.object(runtime.sqlite3, 'connect', side_effect=capture):
            runtime.discover_projects(self.home)
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute('SELECT 1')

    def test_empty_home_still_provides_setup_and_does_not_create_database(self):
        missing = self.base / 'missing'
        info = self.state.setup_info(missing)
        self.assertFalse(info['configured'])
        self.assertEqual(info['projects'], [])
        self.assertIn('error', info)
        self.assertTrue(info['csrf_token'])
        self.assertFalse(missing.exists())

    def test_incompatible_schema_gives_actionable_error(self):
        other = self.base / 'old-codex'
        other.mkdir()
        with closing(sqlite3.connect(other / 'state_5.sqlite')) as conn:
            conn.execute('CREATE TABLE threads (id TEXT)')
        self.assertIn('暂不受支持', self.state.setup_info(other)['error'])

    def test_selection_validates_source_persists_and_reuses_accounting(self):
        result = self.state.configure({'project_directory': 'c:\\PROJECTS\\alpha'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['project_directory'], 'C:/Projects/Alpha')
        saved = json.loads((self.data / 'settings.json').read_text(encoding='utf-8'))
        self.assertNotIn('csrf_token', saved)
        self.assertNotIn('tasks', saved)
        restored = runtime.DesktopState(self.data)
        self.assertEqual(restored.home, self.home.resolve())
        self.assertTrue(restored.setup_info()['configured'])
        snapshot = restored.snapshot()
        self.assertEqual(len(snapshot['tasks']), 2)
        self.assertEqual(snapshot['totals']['total_tokens'], 260)
        self.assertEqual(sum(item['child_count'] for item in snapshot['tasks']), 1)
        self.assertFalse((self.home / 'settings.json').exists())

    def test_unknown_project_is_rejected_without_saving(self):
        with self.assertRaises(runtime.accounting.MonitorError):
            self.state.configure({'project_directory': 'C:/Projects/Alpha-other'})
        self.assertFalse(self.state.settings_path.exists())
        self.assertIsNone(self.state.monitor)

    def test_network_device_and_invalid_homes_are_rejected_before_filesystem_access(self):
        unsafe = ['\\\\attacker.invalid\\share', '//attacker.invalid/share',
                  '\\\\?\\C:\\Codex', '\\\\.\\pipe\\codex', '\\??\\C:\\Codex',
                  '\\Device\\HarddiskVolume1\\Codex', 'C:/Codex\0', '', 12, [], {}, 'relative/path']
        with patch.object(runtime.Path, 'resolve') as resolve, patch.object(runtime.Path, 'stat') as stat, \
                patch.object(runtime.sqlite3, 'connect') as connect:
            for home in unsafe:
                with self.subTest(home=home), self.assertRaises(runtime.accounting.MonitorError):
                    runtime.resolve_home(home)
            resolve.assert_not_called()
            stat.assert_not_called()
            connect.assert_not_called()

    def test_local_absolute_windows_and_posix_homes_are_accepted(self):
        with patch.object(runtime.Path, 'resolve', return_value=self.home) as resolve:
            for home in ('C:/Users/Friend/.codex', '/home/friend/.codex'):
                self.assertEqual(runtime.resolve_home(home), self.home)
            self.assertEqual(resolve.call_count, 2)

    def test_corrupt_preferences_are_recoverable(self):
        self.state.settings_path.write_text('not-json', encoding='utf-8')
        restored = runtime.DesktopState(self.data, self.home)
        self.assertFalse(restored.setup_info()['configured'])
        self.assertIn('重新选择', restored.setup_info()['error'])
        restored.configure({'project_directory': '/projects/beta'})
        self.assertNotIn('error', restored.setup_info())

    def test_atomic_save_failure_keeps_existing_selection(self):
        self.state.configure({'project_directory': '/projects/beta'})
        with patch.object(runtime.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.state.configure({'project_directory': 'C:/Projects/Alpha'})
        self.assertEqual(self.state.config['project_directory'], '/projects/beta')
        self.assertEqual(json.loads(self.state.settings_path.read_text(encoding='utf-8'))['project_directory'],
                         '/projects/beta')
        self.assertFalse((self.data / 'settings.json.tmp').exists())


class HttpTests(unittest.TestCase):
    def setUp(self):
        DesktopTests.setUp(self)
        self.scan_gates = []
        self.server = runtime.create_server(self.state, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        for gate in self.scan_gates:
            gate.set()
        deadline = time.monotonic() + 3
        while self.state.scan_running and time.monotonic() < deadline:
            time.sleep(0.01)
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)
        DesktopTests.tearDown(self)

    def request(self, method, route, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.request(method, route, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def post(self, payload, headers=None, route='/api/setup'):
        base = {'Content-Type': 'application/json', 'Origin': self.origin,
                'X-Monitor-Token': self.state.csrf_token}
        if headers:
            base.update(headers)
        return self.request('POST', route, json.dumps(payload).encode(), base)

    def poll_usage(self, expected=200):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, _, body = self.request('GET', '/api/usage')
            data = json.loads(body)
            if status == expected and not data.get('refreshing'):
                return data
            time.sleep(0.01)
        self.fail(f'usage did not reach {expected}: {status} {data}')

    def blocked_scan(self, result=None, error=None, updates=(), finish_updates=()):
        entered, release = threading.Event(), threading.Event()
        self.scan_gates.append(release)

        def scan(progress=None):
            for update in updates:
                progress(update)
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test scan wait timed out')
            for update in finish_updates:
                progress(update)
            if error:
                raise error
            return result

        return Mock(side_effect=scan), entered, release

    def progress_update(self, snapshot, done=1, percent=50):
        partial = json.loads(json.dumps(snapshot))
        partial['tasks'] = partial['tasks'][:done]
        partial['totals'] = runtime.accounting.add(*(task['usage'] for task in partial['tasks']))
        partial['daily'] = [{'date': '2026-01-01',
                             'total_tokens': partial['totals']['total_tokens']}] if done else []
        return {'progress': {'tasks_total': len(snapshot['tasks']), 'tasks_done': done,
                             'logs_total': 3, 'logs_done': done, 'current_task': 'Design',
                             'log_bytes_done': 40, 'log_bytes_total': 100, 'percent': percent},
                'partial_snapshot': partial if done else None}

    def assert_scan_error(self, response, message):
        self.assertEqual(response['error'], message)
        self.assertEqual(response['project_directory'], self.state.config['project_directory'])
        self.assertEqual(response['source_home'], str(self.home.resolve()))
        self.assertIsNone(response['progress'])
        self.assertIsNone(response['partial_snapshot'])
        self.assertNotIn('totals', response)

    def test_setup_api_returns_token_and_usage_requires_selection(self):
        status, _, body = self.request('GET', '/api/setup')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['csrf_token'], self.state.csrf_token)
        self.assertEqual(self.request('GET', '/api/usage')[0], 503)
        self.assertEqual(self.post({'project_directory': 'C:/Projects/Alpha'})[0], 200)
        status, _, body = self.request('GET', '/api/usage')
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)['status'], 'loading')
        self.assertEqual(self.poll_usage()['totals']['total_tokens'], 260)
        status, headers, body = self.request('GET', '/api/export.csv')
        self.assertEqual(status, 200)
        self.assertIn('text/csv', headers['Content-Type'])
        self.assertIn(b'190', body)

    def test_slow_initial_scan_is_nonblocking_and_polling_is_single_flight(self):
        self.state.configure({'project_directory': 'C:/Projects/Alpha'})
        result = self.state.snapshot()
        scan, entered, release = self.blocked_scan(result=result)
        self.state.monitor.snapshot = scan
        start = time.monotonic()
        status, _, body = self.request('GET', '/api/usage')
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body), {'status': 'loading', 'message': '正在读取历史记录，请稍候…',
                                           'project_name': 'Alpha', 'project_directory': 'C:/Projects/Alpha',
                                           'source_home': str(self.home.resolve()), 'progress': None,
                                           'partial_snapshot': None})
        self.assertTrue(entered.wait(1))
        for _ in range(4):
            self.assertEqual(self.request('GET', '/api/usage?refresh=1')[0], 202)
        self.assertEqual(scan.call_count, 1)
        start = time.monotonic()
        self.assertEqual(self.request('GET', '/api/health')[0], 200)
        self.assertEqual(self.request('GET', '/api/setup')[0], 200)
        self.assertEqual(self.request('GET', '/api/export.csv')[0], 503)
        self.assertLess(time.monotonic() - start, 1)
        release.set()
        self.assertEqual(self.poll_usage()['totals']['total_tokens'], 260)
        self.assertEqual(scan.call_count, 1)

    def test_cached_refresh_retains_timestamp_and_default_polling_is_throttled(self):
        self.state.configure({'project_directory': 'C:/Projects/Alpha'})
        old = self.state.snapshot()
        first = Mock(return_value=old)
        self.state.monitor.snapshot = first
        self.assertEqual(self.request('GET', '/api/usage')[0], 202)
        self.assertEqual(self.poll_usage()['generated_at'], old['generated_at'])
        for _ in range(3):
            self.assertFalse(json.loads(self.request('GET', '/api/usage')[2])['refreshing'])
        self.assertEqual(first.call_count, 1)
        updated = {**old, 'generated_at': '2026-02-02T00:00:00+00:00'}
        update = self.progress_update(updated, done=1, percent=25)
        scan, entered, release = self.blocked_scan(result=updated, updates=[update])
        self.state.monitor.snapshot = scan
        status, _, body = self.request('GET', '/api/usage?refresh=1')
        self.assertEqual(status, 200)
        cached = json.loads(body)
        self.assertTrue(cached['refreshing'])
        self.assertEqual(cached['generated_at'], old['generated_at'])
        self.assertTrue(entered.wait(1))
        cached = json.loads(self.request('GET', '/api/usage')[2])
        self.assertTrue(cached['refreshing'])
        self.assertEqual(cached['progress'], update['progress'])
        self.assertEqual(cached['totals'], old['totals'])
        self.assertEqual(cached['tasks'], old['tasks'])
        self.assertEqual(cached['daily'], old['daily'])
        self.assertNotIn('partial_snapshot', cached)
        status, _, csv = self.request('GET', '/api/export.csv')
        self.assertEqual(status, 200)
        self.assertEqual(csv, runtime.accounting.csv_bytes(old))
        self.assertEqual(scan.call_count, 1)
        release.set()
        self.assertEqual(self.poll_usage()['generated_at'], updated['generated_at'])
        # Once the configured interval passes, polling starts exactly one more scan.
        with self.state.lock:
            self.state.scan_completed_at -= 11
        self.assertEqual(self.request('GET', '/api/usage')[0], 200)
        self.poll_usage()
        self.assertEqual(scan.call_count, 2)

    def test_initial_progress_is_serializable_and_partial_snapshots_are_detached(self):
        self.state.configure({'project_directory': 'C:/Projects/Alpha'})
        result = self.state.snapshot()
        update = self.progress_update(result)
        expected = json.loads(json.dumps(update))
        scan, entered, release = self.blocked_scan(result=result, updates=[update])
        self.state.monitor.snapshot = scan
        self.assertEqual(self.request('GET', '/api/usage')[0], 202)
        self.assertTrue(entered.wait(1))
        status, _, body = self.request('GET', '/api/usage')
        self.assertEqual(status, 202)
        loading = json.loads(body)
        self.assertEqual(loading['progress'], expected['progress'])
        self.assertEqual(loading['partial_snapshot'], expected['partial_snapshot'])
        self.assertEqual(len(loading['partial_snapshot']['tasks']), 1)
        self.assertNotIn('totals', loading)
        self.assertEqual(self.request('GET', '/api/export.csv')[0], 503)
        # Neither a scanner mutation nor a caller mutation can rewrite published state.
        update['progress']['tasks_done'] = 999
        update['partial_snapshot']['tasks'][0]['title'] = 'mutated scanner'
        _, direct = self.state.usage_response()
        direct['progress']['logs_done'] = 999
        direct['partial_snapshot']['tasks'][0]['title'] = 'mutated caller'
        unchanged = json.loads(self.request('GET', '/api/usage')[2])
        self.assertEqual(unchanged['progress'], expected['progress'])
        self.assertEqual(unchanged['partial_snapshot'], expected['partial_snapshot'])
        # Progress reflects additional work only when a new callback arrives.
        later = self.progress_update(result, done=1, percent=75)
        scan.call_args.kwargs['progress'](later)
        self.assertEqual(json.loads(self.request('GET', '/api/usage')[2])['progress']['percent'], 75)
        release.set()
        finished = self.poll_usage()
        self.assertEqual(finished['tasks'], result['tasks'])
        self.assertEqual(finished['totals'], result['totals'])
        self.assertNotIn('partial_snapshot', finished)
        self.assertIsNone(self.state.scan_partial)

    def test_invalid_progress_and_failed_scan_clear_partial_data(self):
        self.state.configure({'project_directory': 'C:/Projects/Alpha'})
        result = self.state.snapshot()
        for invalid in (b'not-json', float('nan')):
            with self.subTest(invalid=invalid):
                update = self.progress_update(result)
                broken = self.progress_update(result)
                broken['partial_snapshot']['tasks'][0]['model'] = invalid
                scan, entered, release = self.blocked_scan(
                    result=result, updates=[update], finish_updates=[broken])
                self.state.monitor.snapshot = scan
                self.assertEqual(self.request('GET', '/api/usage?refresh=1')[0], 202)
                self.assertTrue(entered.wait(1))
                self.assertIsNotNone(json.loads(self.request('GET', '/api/usage')[2])['partial_snapshot'])
                release.set()
                self.assert_scan_error(self.poll_usage(503), '暂时无法读取本地数据，请稍后重试。')
                self.assertIsNone(self.state.scan_partial)
                self.assertIsNone(self.state.scan_progress)
                self.assertEqual(self.request('GET', '/api/export.csv')[0], 503)
        scan, entered, release = self.blocked_scan(
            error=runtime.accounting.MonitorError('日志格式不兼容'), updates=[self.progress_update(result)])
        self.state.monitor.snapshot = scan
        self.assertEqual(self.request('GET', '/api/usage?refresh=1')[0], 202)
        self.assertTrue(entered.wait(1))
        release.set()
        self.assert_scan_error(self.poll_usage(503), '日志格式不兼容')

    def test_empty_logs_and_empty_project_finish_at_one_hundred_percent(self):
        self.state.configure({'project_directory': '/projects/beta'})
        (self.home / 'other.jsonl').write_text('', encoding='utf-8')
        self.assertEqual(self.request('GET', '/api/usage')[0], 202)
        finished = self.poll_usage()
        self.assertEqual(finished['totals']['total_tokens'], 0)
        self.assertTrue(finished['incomplete'])
        self.assertTrue(finished['tasks'][0]['own_unavailable'])
        self.assertEqual(self.request('GET', '/api/export.csv')[0], 503)
        self.assertEqual(finished['progress']['percent'], 100)
        self.assertEqual(finished['progress']['tasks_done'], 1)
        self.assertEqual(finished['progress']['logs_done'], finished['progress']['logs_total'])
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as conn:
            conn.execute('DELETE FROM threads WHERE id = ?', ('other',))
            conn.commit()
        self.assertEqual(self.request('GET', '/api/usage?refresh=1')[0], 200)
        empty = self.poll_usage()
        self.assertEqual(empty['tasks'], [])
        self.assertEqual(empty['totals']['total_tokens'], 0)
        self.assertFalse(empty['incomplete'])
        self.assertEqual(self.request('GET', '/api/export.csv')[0], 200)
        self.assertEqual(empty['progress']['percent'], 100)
        self.assertEqual(empty['progress']['tasks_done'], 0)
        self.assertEqual(empty['progress']['tasks_total'], 0)

    def test_legacy_tasks_keep_dashboard_available_with_explicit_incomplete_counts(self):
        legacy = json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count',
                            'info': {'total_token_usage': {'total_tokens': 999999}}}}) + '\n'
        (self.home / 'root.jsonl').write_text(legacy, encoding='utf-8')
        self.state.configure({'project_directory': 'C:/Projects/Alpha'})
        self.assertEqual(self.request('GET', '/api/usage')[0], 202)
        data = self.poll_usage()
        self.assertTrue(data['incomplete'])
        self.assertEqual(data['progress']['percent'], 100)
        self.assertEqual(len(data['tasks']), 2)
        self.assertEqual(data['totals']['total_tokens'], 140)
        root = next(task for task in data['tasks'] if task['id'] == 'root')
        self.assertTrue(root['own_unavailable'])
        self.assertFalse(root['children_unavailable'])
        self.assertEqual(root['children']['total_tokens'], 70)
        self.assertEqual(root['unavailable_logs'], 1)
        status, _, body = self.request('GET', '/api/export.csv')
        self.assertEqual(status, 503)
        self.assertIn('统计不完整', json.loads(body)['error'])
        # A project consisting entirely of legacy logs still renders its task and status.
        (self.home / 'other.jsonl').write_text(legacy, encoding='utf-8')
        self.state.configure({'project_directory': '/projects/beta'})
        self.assertEqual(self.request('GET', '/api/usage')[0], 202)
        unavailable = self.poll_usage()
        self.assertTrue(unavailable['incomplete'])
        self.assertEqual(unavailable['progress']['percent'], 100)
        self.assertEqual(len(unavailable['tasks']), 1)
        self.assertTrue(unavailable['tasks'][0]['own_unavailable'])
        self.assertEqual(unavailable['tasks'][0]['unavailable_logs'], 1)
        self.assertEqual(unavailable['totals']['total_tokens'], 0)
        self.assertEqual(self.request('GET', '/api/export.csv')[0], 503)

    def test_project_switch_discards_old_scan_without_starting_parallel_work(self):
        self.state.configure({'project_directory': 'C:/Projects/Alpha'})
        old = self.state.snapshot()
        update = self.progress_update(old)
        scan, entered, release = self.blocked_scan(result=old, updates=[update])
        self.state.monitor.snapshot = scan
        self.assertEqual(self.request('GET', '/api/usage')[0], 202)
        self.assertTrue(entered.wait(1))
        self.assertEqual(json.loads(self.request('GET', '/api/usage')[2])['progress'], update['progress'])
        start = time.monotonic()
        self.assertEqual(self.post({'project_directory': '/projects/beta'})[0], 200)
        self.assertLess(time.monotonic() - start, 1)
        new_monitor = self.state.monitor
        current_scan = Mock(wraps=new_monitor.snapshot)
        new_monitor.snapshot = current_scan
        # A callback arriving after selection changed must be ignored as well as its final result.
        scan.call_args.kwargs['progress'](update)
        for _ in range(3):
            status, _, body = self.request('GET', '/api/usage')
            self.assertEqual(status, 202)
            self.assertEqual(json.loads(body)['project_name'], 'beta')
            self.assertEqual(json.loads(body)['project_directory'], '/projects/beta')
            self.assertEqual(json.loads(body)['source_home'], str(self.home.resolve()))
            self.assertIsNone(json.loads(body)['progress'])
            self.assertIsNone(json.loads(body)['partial_snapshot'])
            self.assertNotIn('totals', json.loads(body))
        current_scan.assert_not_called()
        release.set()
        result = self.poll_usage()
        self.assertEqual(result['project_directory'], '/projects/beta')
        self.assertEqual(result['totals']['total_tokens'], 70)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(current_scan.call_count, 1)

    def test_scan_error_is_visible_and_manual_retry_recovers(self):
        self.state.configure({'project_directory': '/projects/beta'})
        result = self.state.snapshot()
        scan, entered, release = self.blocked_scan(error=runtime.accounting.MonitorError('旧版日志不兼容'))
        self.state.monitor.snapshot = scan
        self.assertEqual(self.request('GET', '/api/usage')[0], 202)
        self.assertTrue(entered.wait(1))
        release.set()
        self.assert_scan_error(self.poll_usage(503), '旧版日志不兼容')
        for _ in range(3):
            self.assertEqual(self.request('GET', '/api/usage')[0], 503)
        self.assertEqual(scan.call_count, 1)
        self.state.monitor.snapshot = Mock(return_value=result)
        self.assertEqual(self.request('GET', '/api/usage?refresh=1')[0], 202)
        self.assertEqual(self.poll_usage()['totals']['total_tokens'], 70)

    def test_nonserializable_database_model_reports_error_without_disconnect(self):
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as conn:
            conn.execute('UPDATE threads SET model = ? WHERE id = ?',
                         (sqlite3.Binary(b'invalid-model-bytes'), 'root'))
            conn.commit()
        self.state.configure({'project_directory': 'C:/Projects/Alpha'})
        self.assertEqual(self.request('GET', '/api/usage')[0], 202)
        self.assert_scan_error(self.poll_usage(503), '暂时无法读取本地数据，请稍后重试。')
        self.assertEqual(self.request('GET', '/api/usage?refresh=1')[0], 202)
        self.assert_scan_error(self.poll_usage(503), '暂时无法读取本地数据，请稍后重试。')
        self.assertEqual(self.request('GET', '/api/health')[0], 200)
        self.assertEqual(self.request('GET', '/api/setup')[0], 200)

    def test_write_requires_exact_origin_token_json_and_bounded_body(self):
        payload = {'project_directory': '/projects/beta'}
        attempts = [
            ({'Origin': 'https://attacker.example'}, 403),
            ({'Origin': self.origin + '.attacker.example'}, 403),
            ({'Origin': 'null'}, 403), ({'Origin': ''}, 403),
            ({'X-Monitor-Token': ''}, 403), ({'X-Monitor-Token': 'wrong'}, 403),
            ({'Content-Type': 'text/plain'}, 415),
            ({'Transfer-Encoding': 'chunked'}, 415),
            ({'Content-Length': str(runtime.MAX_BODY + 1)}, 413),
        ]
        for headers, expected in attempts:
            with self.subTest(headers=headers):
                self.assertEqual(self.post(payload, headers)[0], expected)
        self.assertFalse(self.state.settings_path.exists())

    def test_discovery_requires_authenticated_post_and_rejects_unsafe_homes(self):
        unsafe = '\\\\attacker.invalid\\share'
        with patch.object(runtime, 'discover_projects') as discover:
            self.assertEqual(self.request('GET', '/api/setup?codex_home=' + quote(unsafe))[0], 400)
            self.assertEqual(self.request('GET', '/api/setup?codex_home=')[0], 400)
            self.assertEqual(self.post({'codex_home': unsafe}, {'Origin': 'https://attacker.invalid'},
                                       route='/api/discover')[0], 403)
            discover.assert_not_called()
        with patch.object(runtime.Path, 'resolve') as resolve, patch.object(runtime.Path, 'stat') as stat:
            for home in (unsafe, '//attacker.invalid/share', '\\\\?\\C:\\Codex', 'C:/Codex\0', [], {}, 4, None):
                with self.subTest(home=home):
                    self.assertEqual(self.post({'codex_home': home}, route='/api/discover')[0], 400)
            resolve.assert_not_called()
            stat.assert_not_called()
        status, _, body = self.post({'codex_home': str(self.home)}, route='/api/discover')
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)['projects']), 2)
        self.assertFalse(self.state.settings_path.exists())

    def test_deep_json_is_rejected_and_body_timeout_returns_408(self):
        headers = {'Content-Type': 'application/json', 'Origin': self.origin,
                   'X-Monitor-Token': self.state.csrf_token}
        nested = b'{"nested":' + b'[' * 1200 + b'0' + b']' * 1200 + b'}'
        self.assertEqual(self.request('POST', '/api/setup', nested, headers)[0], 400)
        handler_type = runtime.make_handler(self.state)
        handler = object.__new__(handler_type)
        handler.headers = {**headers, 'Host': self.origin.removeprefix('http://'), 'Content-Length': '2'}
        handler.server = self.server
        handler.path = '/api/setup'
        handler.rfile = Mock()
        handler.rfile.read.side_effect = TimeoutError
        handler.send_json = Mock()
        handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[0], 408)

    def test_invalid_host_and_arbitrary_paths_are_rejected(self):
        self.assertEqual(self.request('GET', '/api/setup', headers={'Host': 'attacker.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/setup', headers={'Host': '127.0.0.1:1'})[0], 403)
        self.assertEqual(self.request('GET', '/../../settings.json')[0], 404)
        self.assertEqual(self.request('GET', '/assets/../../settings.json')[0], 404)

    def test_setup_routes_and_dashboard_injection(self):
        ui = self.base / 'desktop-assets'
        app = self.base / 'app-assets'
        ui.mkdir()
        app.mkdir()
        (ui / 'setup.html').write_text('<body>SETUP</body>', encoding='utf-8')
        (app / 'index.html').write_text('<body>DASHBOARD</body>', encoding='utf-8')
        with patch.object(runtime, 'DESKTOP', ui), patch.object(runtime, 'APP', app):
            self.assertIn(b'SETUP', self.request('GET', '/')[2])
            self.state.configure({'project_directory': '/projects/beta'})
            body = self.request('GET', '/')[2]
            self.assertIn(b'DASHBOARD', body)
            self.assertIn(b'<script src="/desktop.js"></script>', body)
            self.assertIn(b'SETUP', self.request('GET', '/setup')[2])

    def test_occupied_port_uses_another_local_port(self):
        other = runtime.create_server(self.state, self.server.server_port)
        try:
            self.assertNotEqual(other.server_port, self.server.server_port)
            self.assertEqual(other.server_address[0], '127.0.0.1')
        finally:
            other.server_close()

    def test_health_validation_rejects_unrelated_server(self):
        saved = {'url': self.origin + '/', 'pid': os.getpid(), 'instance': self.state.instance}
        runtime.atomic_json(self.data / 'server.json', saved)
        self.assertEqual(runtime.existing_url(self.data), self.origin + '/')
        saved['instance'] = 'unrelated-process'
        runtime.atomic_json(self.data / 'server.json', saved)
        self.assertIsNone(runtime.existing_url(self.data))

    def test_health_and_metadata_reject_json_non_objects(self):
        for invalid in ([], 1, 'text', None, True, {'url': []}):
            with self.subTest(metadata=invalid), patch.object(runtime, 'build_opener') as opener:
                runtime.atomic_json(self.data / 'server.json', invalid)
                self.assertIsNone(runtime.existing_url(self.data))
                opener.assert_not_called()
        saved = {'url': self.origin + '/', 'pid': os.getpid(), 'instance': self.state.instance}
        runtime.atomic_json(self.data / 'server.json', saved)
        for invalid in ([], 1, 'text', None, True):
            with self.subTest(health=invalid), patch.object(runtime, 'build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value = io.BytesIO(json.dumps(invalid).encode())
                self.assertIsNone(runtime.existing_url(self.data))
        health = json.loads(self.request('GET', '/api/health')[2])
        self.assertEqual(health['version'], '0.2.3')

    def test_quit_replies_before_stopping_own_server(self):
        self.assertEqual(self.post({}, route='/api/quit')[0], 200)
        self.worker.join(timeout=3)
        self.assertFalse(self.worker.is_alive())


class LifecycleTests(unittest.TestCase):
    def test_second_launch_reuses_server_and_quit_removes_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            command = [sys.executable, str(Path(runtime.__file__)), '--data-dir', str(directory / 'preferences'),
                       '--codex-home', str(directory / 'empty-codex'), '--no-browser', '--port', '0']
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            metadata = directory / 'preferences/server.json'
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if metadata.is_file() and runtime.existing_url(metadata.parent):
                        break
                    if child.poll() is not None:
                        self.fail(child.communicate()[1].decode(errors='replace'))
                    time.sleep(0.05)
                self.assertTrue(metadata.is_file())
                saved = json.loads(metadata.read_text(encoding='utf-8'))
                self.assertEqual(saved['version'], '0.2.3')
                repeat = subprocess.run(command, capture_output=True, timeout=8)
                self.assertEqual(repeat.returncode, 0, repeat.stderr.decode(errors='replace'))
                self.assertEqual(json.loads(metadata.read_text(encoding='utf-8')), saved)
                self.assertIsNone(child.poll())
                port = runtime.urlsplit(saved['url']).port
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                conn.request('GET', '/api/setup')
                info = json.loads(conn.getresponse().read())
                self.assertFalse(info['configured'])
                self.assertIn('error', info)
                conn.request('POST', '/api/quit', '{}', headers={
                    'Content-Type': 'application/json', 'Origin': saved['url'].rstrip('/'),
                    'X-Monitor-Token': info['csrf_token']})
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
                self.assertEqual(child.wait(timeout=5), 0)
                self.assertFalse(metadata.exists())
                self.assertFalse((directory / 'empty-codex').exists())
            finally:
                if child.poll() is None:
                    child.terminate()
                child.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
