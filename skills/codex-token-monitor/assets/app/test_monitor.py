import csv
import io
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
import urllib.request
import urllib.error
import threading
from http.server import ThreadingHTTPServer

from monitor import Monitor, MonitorError, parse_records, add, zero, csv_bytes, normalized_directory, avatar_asset, make_handler
from launch import is_our_server, ensure_server


def record(response_id, total=110, thread='task', **extra):
    return {'timestamp': '2026-09-07T17:00:00Z', 'type': 'token_usage_record', 'payload': {
        'thread_id': thread, 'response_id': response_id,
        'usage': {'input_tokens': total - 10, 'cached_input_tokens': 50,
                  'output_tokens': 10, 'reasoning_output_tokens': 3, 'total_tokens': total},
        'thread_token_usage': {'total_tokens': 999999999}, **extra}}


class UsageTests(unittest.TestCase):
    def parse(self, lines, tail=b'', cutoff=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rollout.jsonl'
            path.write_bytes(b''.join(json.dumps(x).encode() + b'\n' for x in lines) + tail)
            return parse_records(path, 'task', cutoff)

    def test_duplicates_inherited_history_and_compaction(self):
        data = self.parse([record('a'), record('a'), record('parent', thread='parent'),
                           {'type': 'event_msg', 'payload': {'type': 'token_count',
                            'info': {'total_token_usage': {'total_tokens': 999999}}}},
                           record('compact', 210), {'type': 'compacted', 'payload': {}}])
        self.assertEqual(data['usage']['total_tokens'], 320)
        self.assertEqual(data['usage']['input_tokens'] + data['usage']['output_tokens'], 320)
        self.assertEqual(data['usage']['cached_input_tokens'], 100)
        self.assertEqual(data['response_count'], 2)
        local_day = datetime.fromisoformat('2026-09-07T17:00:00+00:00').astimezone().date().isoformat()
        self.assertEqual(data['daily'], {local_day: 320})

    def test_counter_reset_does_not_lose_requests(self):
        data = self.parse([record('before', 110), record('after', 120)])
        self.assertEqual(data['usage']['total_tokens'], 230)

    def test_active_partial_tail_then_completed(self):
        pending = json.dumps(record('b')).encode()
        first = self.parse([record('a')], pending)
        second = self.parse([record('a')], pending + b'\n')
        self.assertEqual(first['usage']['total_tokens'], 110)
        self.assertTrue(first['warnings'])
        self.assertEqual(second['usage']['total_tokens'], 220)

    def test_reproducible_cutoff(self):
        self.assertEqual(self.parse([record('a')], cutoff='2026-09-07T16:59:00Z')['response_count'], 0)

    def test_cutoff_handles_fractional_seconds(self):
        later = record('a')
        later['timestamp'] = '2026-09-07T17:00:00.500Z'
        data = self.parse([later], cutoff='2026-09-07T17:00:00Z')
        self.assertEqual(data['response_count'], 0)
        self.assertFalse(data['warnings'])

    def test_missing_response_id_is_not_silent(self):
        with self.assertRaisesRegex(MonitorError, '所有记录'):
            self.parse([record(None)])

    def test_legacy_cumulative_only_is_not_presented_as_exact_zero(self):
        with self.assertRaisesRegex(MonitorError, '旧版 token_count'):
            self.parse([{'type': 'event_msg', 'payload': {'type': 'token_count',
                         'info': {'total_token_usage': {'total_tokens': 9000}}}}])

    def test_inconsistent_token_arithmetic_is_rejected(self):
        broken = record('broken')
        broken['payload']['usage']['total_tokens'] = 999
        data = self.parse([record('good'), broken])
        self.assertEqual(data['usage']['total_tokens'], 110)
        self.assertTrue(data['warnings'])

    def test_missing_timestamp_preserves_total_with_warning(self):
        item = record('a')
        item['timestamp'] = 'invalid'
        data = self.parse([item])
        self.assertEqual(data['usage']['total_tokens'], 110)
        self.assertEqual(data['daily'], {})
        self.assertTrue(data['warnings'])

    def test_unknown_empty_log_is_explicitly_uncertain(self):
        data = self.parse([{'type': 'future-format', 'payload': {}}])
        self.assertEqual(data['usage']['total_tokens'], 0)
        self.assertIn('不代表确认没有消耗', data['warnings'][0])

    def test_cached_and_reasoning_are_not_added_twice(self):
        usage = self.parse([record('a')])['usage']
        self.assertEqual(add(zero(), usage)['total_tokens'], 110)

    def test_csv_neutralizes_formulas(self):
        usage = zero()
        output = csv_bytes({'generated_at': 'now', 'tasks': [{'title': '=1+1',
            'screenshot_title': '中文', 'id': 'task', 'own': usage, 'children': usage, 'usage': usage}]})
        self.assertTrue(output.startswith(b'\xef\xbb\xbf'))
        self.assertIn("'=1+1", output.decode('utf-8-sig'))

    def test_csv_neutralizes_whitespace_and_all_text_fields(self):
        usage = zero()
        for value in ('\t=1+1', '\r=1+1', '\n=1+1', '  =1+1', '@SUM(1)', '+1', '-1'):
            with self.subTest(value=value):
                data = {'generated_at': 'now', 'tasks': [{'title': value,
                    'screenshot_title': value, 'id': value, 'own': usage, 'children': usage, 'usage': usage}]}
                rows = list(csv.reader(io.StringIO(csv_bytes(data).decode('utf-8-sig'))))
                self.assertEqual(rows[1][:3], ["'" + value] * 3)
                self.assertEqual(rows[1][3:10], ['0'] * 7)

    def test_malformed_model_and_deep_json_preserve_valid_usage(self):
        data = self.parse([{'type': 'turn_context', 'payload': {'model': ['bad']}}, record('valid')],
                          b'[' * 2000 + b'0' + b']' * 2000 + b'\n')
        self.assertEqual(data['usage']['total_tokens'], 110)
        self.assertEqual(data['models'], [])
        self.assertTrue(data['warnings'])


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.home = Path(self.folder.name)
        self.conn = sqlite3.connect(self.home / 'state_5.sqlite')
        self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE threads (id TEXT, name TEXT, rollout_path TEXT, model TEXT, '
                          'created_at_ms INTEGER, cwd TEXT, thread_source TEXT, archived INTEGER)')
        self.conn.execute('CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT)')
        self.config = {'codex_home': str(self.home), 'project_directory': 'D:/work/Demo',
                       'exclude_task_ids': ['monitor'],
                       'tasks': [{'id': 'original', 'screenshot_title': '原任务'}]}
        self.add_task('original')
        self.monitor = Monitor(self.config)

    def add_task(self, ident, cwd='D:/work/Demo', source='user', archived=0,
                 parent=None, total=110, created=0):
        path = self.home / (ident + '.jsonl')
        path.write_text(json.dumps(record(ident, total, thread=ident)) + '\n', encoding='utf-8')
        self.conn.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)',
                          (ident, ident, str(path), 'test-model', created, cwd, source, archived))
        if parent:
            self.conn.execute('INSERT INTO thread_spawn_edges VALUES (?,?)', (parent, ident))
        self.conn.commit()

    def test_new_task_appears_on_next_refresh_without_restart(self):
        first = self.monitor.snapshot()
        self.assertEqual(len(first['tasks']), 1)
        self.add_task('new', cwd='\\\\?\\D:\\work\\Demo\\', total=220)
        second = self.monitor.snapshot()
        self.assertEqual({t['id'] for t in second['tasks']}, {'original', 'new'})
        self.assertEqual(second['totals']['total_tokens'], 330)
        self.assertEqual(next(t for t in second['tasks'] if t['id'] == 'new')['screenshot_title'], '')

    def test_scope_archive_and_children_do_not_duplicate(self):
        self.add_task('archived', archived=1, total=220)
        self.add_task('child', source='subagent', parent='original')
        self.add_task('nested', source='subagent', parent='child')
        self.add_task('guardian', source='guardian_review')
        self.add_task('elsewhere', cwd='D:/work/Demo-backup')
        self.add_task('monitor')
        self.add_task('monitor_child', source='subagent', parent='monitor')
        data = self.monitor.snapshot()
        self.assertEqual({t['id'] for t in data['tasks']}, {'original', 'archived'})
        self.assertEqual(data['totals']['total_tokens'], 550)
        self.assertEqual(next(t for t in data['tasks'] if t['id'] == 'original')['child_count'], 2)
        self.assertTrue(next(t for t in data['tasks'] if t['id'] == 'archived')['archived'])

    def test_historical_cutoff_excludes_later_new_tasks(self):
        self.add_task('future', created=1788849134314)
        data = self.monitor.snapshot('2026-09-08T01:46:00Z')
        self.assertEqual([t['id'] for t in data['tasks']], ['original'])

    def test_excluded_child_prunes_subtree_including_legacy_logs(self):
        self.add_task('excluded', source='subagent', parent='original', total=330)
        self.add_task('grandchild', source='subagent', parent='excluded', total=440)
        self.add_task('sibling', source='subagent', parent='original', total=220)
        self.config['exclude_task_ids'].append('excluded')
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                if legacy:
                    (self.home / 'excluded.jsonl').write_text(json.dumps({
                        'type': 'event_msg', 'payload': {'type': 'token_count',
                            'info': {'total_token_usage': {'total_tokens': 9000}}}}) + '\n',
                        encoding='utf-8')
                data = self.monitor.snapshot()
                self.assertEqual([t['id'] for t in data['tasks']], ['original'])
                task = data['tasks'][0]
                self.assertEqual(task['own']['total_tokens'], 110)
                self.assertEqual(task['children']['total_tokens'], 220)
                self.assertEqual(task['usage']['total_tokens'], 330)
                self.assertEqual(data['totals']['total_tokens'], 330)
                self.assertEqual(task['child_count'], 1)
                self.assertEqual(task['response_count'], 2)
                self.assertEqual(data['warnings'], [])

    def test_explicit_task_cannot_escape_project_scope(self):
        self.add_task('unrelated', cwd='D:/elsewhere')
        self.config['tasks'].append({'id': 'unrelated'})
        self.assertEqual([t['id'] for t in self.monitor.snapshot()['tasks']], ['original'])

    def test_posix_paths_are_case_sensitive_and_exact(self):
        self.config['project_directory'] = '/Users/friend/Demo/'
        self.add_task('posix', cwd='/Users/friend/Demo')
        self.add_task('wrong_case', cwd='/Users/friend/demo')
        self.add_task('nested_directory', cwd='/Users/friend/Demo/nested')
        self.assertEqual([t['id'] for t in self.monitor.snapshot()['tasks']], ['posix'])

    def test_relocated_log_is_found_under_codex_home(self):
        root = self.home / 'sessions' / '2026' / '01'
        root.mkdir(parents=True)
        (self.home / 'original.jsonl').rename(root / 'rollout-original.jsonl')
        self.assertEqual(self.monitor.snapshot()['totals']['total_tokens'], 110)

    @unittest.skipUnless(os.name == 'nt', 'Windows extended local paths')
    def test_extended_local_log_and_home_use_direct_cached_path(self):
        source = '\\\\?\\' + str(self.home / 'original.jsonl')
        self.conn.execute('UPDATE threads SET rollout_path=?', (source,))
        self.conn.commit()
        for home in (str(self.home), '\\\\?\\' + str(self.home)):
            with self.subTest(home=home):
                monitor = Monitor({**self.config, 'codex_home': home})
                with (patch.object(Path, 'rglob', side_effect=AssertionError('unexpected fallback')),
                      patch('monitor.parse_records', wraps=parse_records) as parse):
                    self.assertEqual(monitor.snapshot()['totals']['total_tokens'], 110)
                    self.assertEqual(monitor.snapshot()['totals']['total_tokens'], 110)
                self.assertEqual(parse.call_count, 1)

    def test_relocated_path_cache_avoids_scan_and_rediscovers_moved_log(self):
        sessions = self.home / 'sessions'
        sessions.mkdir()
        relocated = sessions / 'rollout-original.jsonl'
        (self.home / 'original.jsonl').rename(relocated)
        self.assertEqual(self.monitor.snapshot()['totals']['total_tokens'], 110)
        with (patch.object(Path, 'rglob', side_effect=AssertionError('unexpected fallback')),
              patch('monitor.parse_records', wraps=parse_records) as parse):
            self.assertEqual(self.monitor.snapshot()['totals']['total_tokens'], 110)
        parse.assert_not_called()
        archive = self.home / 'archived_sessions'
        archive.mkdir()
        relocated.rename(archive / relocated.name)
        self.assertEqual(self.monitor.snapshot()['totals']['total_tokens'], 110)
        self.assertEqual(self.monitor.path_cache['original'][1], (archive / relocated.name).resolve())

    def test_changed_rollout_source_invalidates_resolved_path_cache(self):
        self.assertEqual(self.monitor.snapshot()['totals']['total_tokens'], 110)
        replacement = self.home / 'replacement.jsonl'
        replacement.write_text(json.dumps(record('replacement', 220, thread='original')) + '\n', encoding='utf-8')
        self.conn.execute('UPDATE threads SET rollout_path=?', (str(replacement),))
        self.conn.commit()
        self.assertEqual(self.monitor.snapshot()['totals']['total_tokens'], 220)

    def test_cached_relocated_path_is_rechecked_for_home_containment(self):
        sessions = self.home / 'sessions'
        sessions.mkdir()
        (self.home / 'original.jsonl').rename(sessions / 'rollout-original.jsonl')
        self.assertEqual(self.monitor.snapshot()['totals']['total_tokens'], 110)
        cached_path = self.monitor.path_cache['original'][1]
        external = self.monitor.home.parent / 'outside.jsonl'
        resolve = Path.resolve

        def moved_outside(path, *args, **kwargs):
            return external if path == cached_path else resolve(path, *args, **kwargs)

        with (patch.object(Path, 'resolve', autospec=True, side_effect=moved_outside),
              patch('monitor.parse_records', wraps=parse_records) as parse):
            data = self.monitor.snapshot()
        self.assertEqual(data['totals']['total_tokens'], 0)
        self.assertTrue(data['warnings'])
        parse.assert_not_called()

    def test_invalid_extended_and_device_paths_are_rejected_before_probe(self):
        forbidden = ('\\\\?\\UNC\\invalid.example\\share\\log.jsonl', '\\\\?\\C:relative.jsonl',
                     '\\\\?\\Volume{invalid}\\log.jsonl', '\\\\?\\GLOBALROOT\\Device\\log.jsonl',
                     '\\\\.\\C:\\log.jsonl', '\\??\\C:\\log.jsonl', '\\Device\\log.jsonl',
                     '\\\\?\\1:\\log.jsonl', '\\\\?\\C:\\bad\x00.jsonl')
        resolve = Path.resolve
        for source in forbidden:
            with self.subTest(source=source):
                with (patch.object(Path, 'resolve', autospec=True, side_effect=resolve) as probe,
                      patch.object(Path, 'rglob', return_value=iter(()))):
                    data = self.monitor.read_usage({'id': 'original', 'rollout_path': source}, None)
                self.assertEqual(data['usage']['total_tokens'], 0)
                self.assertTrue(data['warnings'])
                self.assertEqual([call.args[0] for call in probe.call_args_list],
                                 [self.monitor.home / 'sessions', self.monitor.home / 'archived_sessions'])

    def test_relative_rollout_path_resolves_under_codex_home(self):
        self.conn.execute("UPDATE threads SET rollout_path='original.jsonl' WHERE id='original'")
        self.conn.commit()
        self.assertEqual(self.monitor.snapshot()['totals']['total_tokens'], 110)

    def test_rollout_cannot_escape_home_or_probe_network_path(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / 'outside.jsonl'
            external.write_text(json.dumps(record('outside', 999, thread='original')) + '\n', encoding='utf-8')
            relative = __import__('os').path.relpath(external, self.home)
            paths = [str(external), relative, '\\\\invalid.example\\share\\log.jsonl', '//invalid.example/share/log.jsonl']
            if os.name == 'nt':
                paths.append('\\\\?\\' + str(external))
            for path in paths:
                with self.subTest(path=path):
                    self.conn.execute('UPDATE threads SET rollout_path=?', (path,))
                    self.conn.commit()
                    data = self.monitor.snapshot()
                    self.assertEqual(data['totals']['total_tokens'], 0)
                    self.assertTrue(data['warnings'])

    def test_external_old_path_uses_relocated_log_in_home(self):
        root = self.home / 'sessions'
        root.mkdir()
        (self.home / 'original.jsonl').rename(root / 'rollout-original.jsonl')
        self.conn.execute('UPDATE threads SET rollout_path=?', (str(self.home.parent / 'old-home' / 'original.jsonl'),))
        self.conn.commit()
        self.assertEqual(self.monitor.snapshot()['totals']['total_tokens'], 110)

    def test_symlink_log_outside_home_is_ignored(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / 'outside.jsonl'
            external.write_text(json.dumps(record('outside', 999, thread='original')) + '\n', encoding='utf-8')
            link = self.home / 'linked.jsonl'
            try:
                link.symlink_to(external)
            except OSError:
                self.skipTest('Creating symlinks is unavailable on this host')
            self.conn.execute('UPDATE threads SET rollout_path=?', (str(link),))
            self.conn.commit()
            data = self.monitor.snapshot()
            self.assertEqual(data['totals']['total_tokens'], 0)
            self.assertTrue(data['warnings'])

    def test_database_and_rollout_remain_unchanged(self):
        db = self.home / 'state_5.sqlite'
        log = self.home / 'original.jsonl'
        before = (db.read_bytes(), log.read_bytes())
        self.monitor.snapshot()
        self.assertEqual(before, (db.read_bytes(), log.read_bytes()))

    def test_unsupported_schema_has_actionable_error(self):
        self.conn.execute('DROP TABLE thread_spawn_edges')
        self.conn.commit()
        with self.assertRaisesRegex(MonitorError, '数据库结构暂不受支持'):
            self.monitor.snapshot()

    def test_snapshot_closes_database_on_success_and_schema_error(self):
        connect = sqlite3.connect
        opened = []

        def capture_connection(*args, **kwargs):
            connection = connect(*args, **kwargs)
            self.addCleanup(connection.close)
            opened.append(connection)
            return connection

        for compatible in (True, False):
            with self.subTest(compatible=compatible):
                if not compatible:
                    self.conn.execute('DROP TABLE thread_spawn_edges')
                    self.conn.commit()
                with patch('monitor.sqlite3.connect', side_effect=capture_connection):
                    if compatible:
                        self.monitor.snapshot()
                    else:
                        with self.assertRaises(MonitorError):
                            self.monitor.snapshot()
                with self.assertRaises(sqlite3.ProgrammingError):
                    opened[-1].execute('SELECT 1')

    def test_project_name_is_returned(self):
        self.config['project_name'] = '朋友的项目'
        self.assertEqual(self.monitor.snapshot()['project_name'], '朋友的项目')


class PortableTests(unittest.TestCase):
    def test_default_home_uses_environment(self):
        with tempfile.TemporaryDirectory() as home, patch.dict('os.environ', {'CODEX_HOME': home}):
            self.assertEqual(Monitor({'project_directory': '/work/demo'}).home, Path(home).resolve())

    def test_project_scope_is_required(self):
        with self.assertRaisesRegex(MonitorError, '必须指定'):
            Monitor({})
        with self.assertRaisesRegex(MonitorError, '完整目录'):
            Monitor({'project_directory': 'relative'})

    def test_missing_database_is_explained(self):
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaisesRegex(MonitorError, '未找到 Codex'):
                Monitor({'codex_home': home, 'project_directory': '/work/demo'}).snapshot()

    def test_windows_normalization_and_posix_case(self):
        self.assertEqual(normalized_directory('D:/Work/Demo/'), normalized_directory('\\\\?\\d:\\work\\demo'))
        self.assertEqual(normalized_directory('\\\\server\\share\\Demo'), normalized_directory('\\\\?\\UNC\\SERVER\\share\\demo'))
        self.assertNotEqual(normalized_directory('/Work/Demo'), normalized_directory('/work/demo'))

    def test_avatar_can_only_serve_fixed_local_image(self):
        for value in ('../config.json', 'assets/secret.svg', '/tmp/avatar.png', 'nested/avatar.png'):
            with self.assertRaises(MonitorError):
                avatar_asset({'avatar_file': value})
        self.assertEqual(avatar_asset({'avatar_file': 'assets/friend.png'})[1], 'image/png')

    def test_server_reuse_requires_app_and_directory(self):
        from monitor import BASE
        self.assertTrue(is_our_server({'app': 'codex-token-monitor', 'directory': str(BASE)}))
        self.assertFalse(is_our_server({'app': 'codex-token-monitor', 'directory': str(BASE.parent)}))
        self.assertFalse(is_our_server({'app': 'other', 'directory': str(BASE)}))

    def test_port_conflict_never_stops_other_process(self):
        with patch('launch.health', return_value={'app': 'other'}), \
             patch('launch.socket.socket') as socket_mock, patch('launch.subprocess.Popen') as start:
            socket_mock.return_value.bind.side_effect = OSError('occupied')
            with self.assertRaisesRegex(MonitorError, '不会关闭其他程序'):
                ensure_server(18767)
            start.assert_not_called()

    def test_api_exposes_compatibility_error(self):
        class Unsupported:
            config = {}
            def snapshot(self):
                raise MonitorError('数据库结构暂不受支持：测试')
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(Unsupported()))
        self.addCleanup(server.server_close)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as context:
            opener.open('http://127.0.0.1:' + str(server.server_port) + '/api/usage')
        self.assertEqual(context.exception.code, 503)
        body = json.loads(context.exception.read())
        self.assertIn('数据库结构暂不受支持', body['error'])


if __name__ == '__main__':
    unittest.main()
