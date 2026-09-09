"""Read-only, local Codex usage monitor. Python standard library only."""
import argparse
import copy
import csv
import io
import json
import ntpath
import os
from pathlib import Path
import posixpath
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = Path(__file__).resolve().parent
FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens',
          'reasoning_output_tokens', 'total_tokens')

class MonitorError(ValueError):
    """An actionable configuration or compatibility error safe to show locally."""


def load_config():
    path = BASE / 'config.json'
    if not path.is_file():
        raise MonitorError('尚未配置项目。请先让 Codex 运行此 Skill 的 scripts/setup_monitor.py。')
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise MonitorError('config.json 必须是配置对象。')
    return config


def parse_time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('时间必须包含时区，例如 2026-01-01T00:00:00Z。')
    return parsed


def zero():
    return dict.fromkeys(FIELDS, 0)


def add(*usages):
    return {key: sum(u[key] for u in usages) for key in FIELDS}


def normalized_directory(value):
    value = str(value or '')
    if not value:
        return ''
    # Windows paths can also appear in a relocated database on macOS/Linux.
    if ntpath.splitdrive(value)[0] or '\\' in value:
        value = value.replace('/', '\\')
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        return ntpath.normcase(ntpath.normpath(value))
    return posixpath.normpath(value)


def _local_path(value):
    if not isinstance(value, (str, Path)) or '\x00' in str(value):
        return None
    value = str(value)
    normalized = value.replace('\\', '/')
    if (normalized.startswith('//?/') and len(normalized) >= 7
            and normalized[4] in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
            and normalized[5:7] == ':/'):
        value = value[4:]
        normalized = normalized[4:]
    if normalized.startswith('//') or normalized.lower().startswith(('/??/', '/device/')):
        return None
    return Path(value)


def select_targets(config, rows, edges, cutoff_ms=None):
    excluded = set(config.get('exclude_task_ids', []))
    child_ids = {child for _, child in edges}
    directory = config.get('project_directory')
    scoped = {ident for ident, row in rows.items()
              if normalized_directory(row.get('cwd')) == normalized_directory(directory)
              and row.get('thread_source') == 'user' and ident not in child_ids}
    targets = {task['id']: task for task in config.get('tasks', []) if task['id'] in scoped}
    if directory:
        for row in sorted(rows.values(), key=lambda r: r.get('created_at_ms') or 0):
            if row['id'] in scoped:
                targets.setdefault(row['id'], {'id': row['id'], 'screenshot_title': ''})
    return [task for ident, task in targets.items()
            if ident not in excluded and not (cutoff_ms is not None
                and (rows.get(ident, {}).get('created_at_ms') or 0) > cutoff_ms)]


def parse_records(path, thread_id, cutoff=None, progress=None):
    """Sum per-response usage, never cumulative counters or inherited history."""
    result = {'usage': zero(), 'response_count': 0, 'last_activity': None,
              'daily': {}, 'warnings': [], 'models': []}
    seen = set()
    model = None
    malformed = 0
    partial_tail = False
    legacy_records = False
    own_records = False
    cutoff_time = parse_time(cutoff) if cutoff else None
    with Path(path).open('rb') as stream:
        if progress is not None:
            size = os.fstat(stream.fileno()).st_size
            consumed = reported = 0
            last_report = time.monotonic()
            progress(0, size)
        for raw in stream:
            if progress is not None:
                consumed += len(raw)
                now = time.monotonic()
                if consumed - reported >= 1024 * 1024 or now - last_report >= 0.1:
                    progress(consumed, max(size, consumed))
                    reported, last_report = consumed, now
            if not raw.endswith(b'\n'):
                partial_tail = True
                continue  # Active writers may not have completed the last record.
            try:
                item = json.loads(raw)
            except (ValueError, UnicodeDecodeError, RecursionError):
                malformed += 1
                continue
            if not isinstance(item, dict) or not isinstance(item.get('payload', {}), dict):
                malformed += 1
                continue
            kind = item.get('type')
            payload = item.get('payload') or {}
            if kind == 'event_msg' and payload.get('type') == 'token_count':
                legacy_records = True
            if kind == 'turn_context':
                candidate = payload.get('model', model)
                if isinstance(candidate, str):
                    model = candidate
                elif candidate is not None:
                    malformed += 1
            if kind != 'token_usage_record' or payload.get('thread_id') != thread_id:
                continue
            own_records = True
            stamp = item.get('timestamp', '')
            try:
                recorded_at = parse_time(stamp) if stamp else None
            except (ValueError, TypeError, AttributeError):
                recorded_at = None
            if recorded_at is None:
                result['warnings'].append('一条用量记录缺少有效时间，未计入每日分布。')
                if cutoff_time:
                    result['unavailable'] = True
                    continue
            elif cutoff_time and recorded_at > cutoff_time:
                continue
            response_id = payload.get('response_id')
            if not isinstance(response_id, str) or not response_id:
                result['warnings'].append('一条用量记录缺少请求编号，未纳入总计。')
                result['unavailable'] = True
                continue
            if response_id in seen:
                continue
            usage = payload.get('usage') or {}
            if (not isinstance(usage, dict)
                    or any(type(usage.get(k)) is not int or usage[k] < 0 for k in FIELDS)
                    or usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens']
                    or usage['cached_input_tokens'] > usage['input_tokens']
                    or usage['reasoning_output_tokens'] > usage['output_tokens']):
                result['warnings'].append('一条用量记录字段不完整，未纳入总计。')
                result['unavailable'] = True
                continue
            seen.add(response_id)
            result['usage'] = add(result['usage'], usage)
            result['response_count'] += 1
            if recorded_at:
                result['last_activity'] = max(result['last_activity'] or '', stamp)
                day = recorded_at.astimezone().date().isoformat()
                result['daily'][day] = result['daily'].get(day, 0) + usage['total_tokens']
            if model and model not in result['models']:
                result['models'].append(model)
        if progress is not None:
            progress(consumed, max(size, consumed))
    if malformed:
        result['warnings'].append(f'{malformed} 行日志损坏，统计可能不完整。')
        result['unavailable'] = True
    if partial_tail:
        result['warnings'].append('日志末行尚未写完，将在下次刷新重新读取。')
        result['unavailable'] = True
    if legacy_records and not own_records:
        raise MonitorError('发现仅含旧版 token_count 累计计数的任务日志，无法可靠计算逐次请求用量。'
                           '累计值可能包含继承的历史，不能作为该任务的独立消耗。')
    if own_records and not result['response_count'] and not cutoff:
        raise MonitorError('任务含逐次用量记录，但所有记录的字段均无效；无法提供可靠的用量总计。')
    if not result['response_count'] and (not cutoff or not own_records):
        result['warnings'].append('尚无可统计的逐次请求记录；显示 0 不代表确认没有消耗。')
        result['unavailable'] = True
    result['warnings'] = list(dict.fromkeys(result['warnings']))
    return result


class Monitor:
    def __init__(self, config):
        self.config = config
        home = _local_path(config.get('codex_home') or os.environ.get('CODEX_HOME') or
                           Path.home() / '.codex')
        home = _local_path(home.expanduser()) if home is not None else None
        self.home = _local_path(home.resolve()) if home is not None else None
        if self.home is None:
            raise MonitorError('Codex 数据目录必须是有效的本机路径。')
        directory = config.get('project_directory')
        if not isinstance(directory, str) or not directory.strip():
            raise MonitorError('必须指定 project_directory，监控仅统计该项目及其子任务。')
        if not (ntpath.isabs(directory) or posixpath.isabs(directory)):
            raise MonitorError('project_directory 必须使用完整目录路径。')
        self.cache = {}
        self.path_cache = {}
        self.lock = threading.Lock()

    def read_usage(self, row, cutoff, progress=None):
        callback_failed = False

        def report(done, total):
            nonlocal callback_failed
            try:
                progress(done, total)
            except Exception:
                callback_failed = True
                raise

        try:
            def within_home(value):
                # Validate before probing a path supplied by the task index.
                candidate = _local_path(value)
                if candidate is None:
                    return None
                if not candidate.is_absolute():
                    candidate = self.home / candidate
                candidate = _local_path(candidate.resolve())
                return candidate if candidate is not None and candidate.is_relative_to(self.home) else None

            source = row.get('rollout_path') or ''
            path = within_home(source)
            if path is None or not path.is_file():
                cached_path = self.path_cache.get(row['id'])
                path = within_home(cached_path[1]) if cached_path and cached_path[0] == source else None
            if path is None or not path.is_file():
                # Relocated homes may retain old paths; only search inside this home.
                path = None
                ident = row['id']
                if isinstance(ident, str) and ident and not any(c in ident for c in '/\\\x00'):
                    for folder in ('sessions', 'archived_sessions'):
                        root = within_home(folder)
                        if root is None:
                            continue
                        for candidate in root.rglob('*.jsonl'):
                            if candidate.name.endswith(ident + '.jsonl'):
                                candidate = within_home(candidate)
                                if candidate is not None and candidate.is_file():
                                    path = candidate
                                    break
                        if path is not None:
                            break
                if path is None:
                    raise OSError('No task log inside the selected Codex home')
            stat = path.stat()
            self.path_cache[row['id']] = (source, path)
            signature = (str(path), stat.st_size, stat.st_mtime_ns, cutoff)
            cached = self.cache.get(row['id'])
            if cached and cached[0] == signature:
                if progress is not None:
                    report(stat.st_size, stat.st_size)
                return cached[1]
            try:
                data = (parse_records(path, row['id'], cutoff, report) if progress is not None
                        else parse_records(path, row['id'], cutoff))
            except MonitorError as exc:
                if callback_failed:
                    raise
                data = {'usage': zero(), 'response_count': 0, 'last_activity': None,
                        'daily': {}, 'models': [], 'unavailable': True,
                        'warnings': ['该任务用量无法确认，未纳入已确认总计：' + str(exc)]}
            self.cache[row['id']] = (signature, data)
            return data
        except MonitorError:
            raise
        except (OSError, ValueError, RuntimeError):
            if callback_failed:
                raise
            return {'usage': zero(), 'response_count': 0, 'last_activity': None,
                    'daily': {}, 'models': [], 'unavailable': True,
                    'warnings': ['无法读取该任务日志，用量无法确认，未纳入已确认总计。']}

    def snapshot(self, cutoff=None, progress=None):
        with self.lock:
            return self._snapshot(cutoff, progress)

    def _snapshot(self, cutoff, progress=None):
        db = self.home / 'state_5.sqlite'
        if not db.is_file():
            raise MonitorError('未找到 Codex 的 state_5.sqlite。请检查 CODEX_HOME 或 config.json 中的 codex_home；'
                               '此监控仅支持提供逐次用量日志的本机 Codex 版本。')
        # mode=ro reads live WAL updates without changing the application database.
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=3)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            required = {'threads': {'id', 'name', 'rollout_path', 'model', 'created_at_ms',
                                    'cwd', 'thread_source', 'archived'},
                        'thread_spawn_edges': {'parent_thread_id', 'child_thread_id'}}
            for table, fields in required.items():
                actual = {r['name'] for r in conn.execute('PRAGMA table_info(' + table + ')')}
                if not fields.issubset(actual):
                    raise MonitorError('Codex 数据库结构暂不受支持：' + table + ' 缺少必要字段。'
                                       '请使用与此 skill 兼容的 Codex 版本。')
            rows = {r['id']: dict(r) for r in conn.execute(
                'SELECT id, name, rollout_path, model, created_at_ms, cwd, thread_source, archived FROM threads')}
            edges = list(conn.execute('SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges'))
        descendants = {}
        cutoff_ms = parse_time(cutoff).timestamp() * 1000 if cutoff else None
        for parent, child in edges:
            if cutoff_ms is not None and (rows.get(child, {}).get('created_at_ms') or 0) > cutoff_ms:
                continue
            descendants.setdefault(parent, set()).add(child)
        targets = select_targets(self.config, rows, edges, cutoff_ms)
        roots = {task['id'] for task in targets}
        excluded = set(self.config.get('exclude_task_ids', []))
        plans = []
        assigned = set()
        for target in targets:
            root = target['id']
            members, pending = {root}, [root]
            while pending:
                for child in descendants.get(pending.pop(), ()):
                    if child not in members and child not in roots and child not in excluded:
                        members.add(child)
                        pending.append(child)
            unique = members - assigned
            assigned.update(members)
            plans.append((target, members, unique))

        tasks, daily = [], {}
        state = {'tasks_total': len(plans), 'tasks_done': 0,
                 'logs_total': len(assigned), 'logs_done': 0, 'current_task': '',
                 'log_bytes_done': 0, 'log_bytes_total': 0, 'percent': None}
        partial = None

        def emit(final=False):
            if progress is None:
                return
            fraction = (min(state['log_bytes_done'] / state['log_bytes_total'], 0.999999)
                        if state['log_bytes_total'] else 0)
            # Each distinct task log has equal weight; bytes refine only the current log.
            state['percent'] = (100 if final else
                                min(99.999999, 100 * (state['logs_done'] + fraction)
                                    / state['logs_total']) if state['logs_total'] else None)
            progress({'progress': dict(state), 'partial_snapshot': partial})

        def file_progress(done, total):
            state['log_bytes_done'], state['log_bytes_total'] = done, total
            emit()

        emit()
        for target, members, unique in plans:
            root = target['id']
            row = rows.get(root, {})
            title = row.get('name') or target.get('screenshot_title') or '未命名任务'
            state['current_task'] = str(title)
            emit()
            own, children = zero(), zero()
            unavailable_logs = 0
            own_unavailable = children_unavailable = False
            task_warnings, models = [], []
            latest, response_count = None, 0
            for ident in sorted(members):
                if ident not in unique:
                    task_warnings.append('发现重复子任务关系，已只计入一个任务。')
                    continue
                row = rows.get(ident)
                if not row:
                    task_warnings.append('任务索引缺失，统计可能不完整。')
                    unavailable_logs += 1
                    if ident == root:
                        own_unavailable = True
                    else:
                        children_unavailable = True
                    state['logs_done'] += 1
                    emit()
                    continue
                data = (self.read_usage(row, cutoff, file_progress) if progress is not None
                        else self.read_usage(row, cutoff))
                if data.get('unavailable'):
                    unavailable_logs += 1
                    if ident == root:
                        own_unavailable = True
                    else:
                        children_unavailable = True
                if ident == root:
                    own = data['usage']
                else:
                    children = add(children, data['usage'])
                response_count += data['response_count']
                task_warnings.extend(data['warnings'])
                models.extend(data['models'])
                if data['last_activity']:
                    latest = max(latest or '', data['last_activity'])
                for day, amount in data['daily'].items():
                    daily[day] = daily.get(day, 0) + amount
                state['logs_done'] += 1
                state['log_bytes_done'] = state['log_bytes_total'] = 0
                emit()
            row = rows.get(root, {})
            tasks.append({
                'id': root, 'title': title,
                'screenshot_title': target.get('screenshot_title', ''),
                'archived': bool(row.get('archived')),
                'own': own, 'children': children, 'usage': add(own, children),
                'unavailable_logs': unavailable_logs, 'own_unavailable': own_unavailable,
                'children_unavailable': children_unavailable,
                'child_count': len(members) - 1, 'response_count': response_count,
                'last_activity': latest,
                'model': ' / '.join(dict.fromkeys(models)) or row.get('model') or '未记录',
                'records_source': '逐次请求记录（按请求编号去重）',
                'warnings': list(dict.fromkeys(task_warnings)),
            })
            state['tasks_done'] += 1
            if progress is not None:
                partial = self._snapshot_data(cutoff, copy.deepcopy(tasks), daily)
                emit()
        result = self._snapshot_data(cutoff, tasks, daily)
        if progress is not None:
            partial = copy.deepcopy(result)
            state['current_task'] = ''
            emit(final=True)
        return result

    def _snapshot_data(self, cutoff, tasks, daily):
        warnings = []
        incomplete = any(t['unavailable_logs'] for t in tasks)
        if incomplete:
            warnings.append('部分任务用量无法确认；总计和每日分布仅包含已确认记录，不能视为完整消耗。')
        if any(t['warnings'] for t in tasks):
            warnings.append('部分记录尚未写入或无法读取，请展开任务查看；总计仅包含已读到的记录。')
        return {
            'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'cutoff': cutoff, 'source_home': str(self.home),
            'refresh_seconds': self.config.get('refresh_seconds', 10),
            'project_directory': self.config.get('project_directory'),
            'project_name': self.config.get('project_name') or Path(self.config['project_directory']).name,
            'tasks': tasks, 'totals': add(*(t['usage'] for t in tasks)),
            'incomplete': incomplete,
            'daily': [{'date': day, 'total_tokens': amount} for day, amount in sorted(daily.items())],
            'warnings': warnings,
            'methodology': [
                f'当前统计 {len(tasks)} 个任务（含已归档任务），汇总各自子任务用量；按配置排除指定任务。',
                '每次刷新自动发现指定项目目录中的新任务；任务归档后仍保留历史消耗。',
                '逐次请求用量按任务编号和请求编号去重，包括日志记录的上下文压缩请求；不累加历史累计快照。',
                '总量 = 输入 + 输出。缓存输入已包含在输入中，推理输出已包含在输出中。',
                '分叉任务只统计分叉后实际请求，继承的历史累计值不再次计费。',
                '日期按本机时区。进行中的请求须写入用量记录后才会显示，刷新不会调用模型。',
                '这是本机日志中可见的 token 处理量，并非人民币账单或账户额度百分比；不额外统计图片、视频等外部工具费用。',
            ],
        }


def csv_bytes(data):
    if data.get('incomplete'):
        raise MonitorError('部分任务用量无法确认，当前统计不完整，暂不能导出 CSV。')
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['当前任务名称', '截图名称', '任务编号', '本体Token', '子任务Token',
                     '合计Token', '输入Token', '其中缓存输入', '输出Token', '其中推理输出', '统计时间'])
    for task in data['tasks']:
        def safe(value):
            value = str(value)
            return "'" + value if (value.lstrip().startswith(('=', '+', '-', '@'))
                                   or value.startswith(('\t', '\r', '\n'))) else value
        writer.writerow([safe(task['title']), safe(task['screenshot_title']), safe(task['id']),
                         task['own']['total_tokens'], task['children']['total_tokens'],
                         task['usage']['total_tokens'], task['usage']['input_tokens'],
                         task['usage']['cached_input_tokens'], task['usage']['output_tokens'],
                         task['usage']['reasoning_output_tokens'], data['generated_at']])
    return stream.getvalue().encode('utf-8-sig')


def avatar_asset(config):
    configured = config.get('avatar_file')
    path = BASE / 'assets' / 'avatar.svg'
    content_type = 'image/svg+xml'
    if configured:
        relative = Path(configured)
        candidate = (BASE / relative).resolve()
        assets = (BASE / 'assets').resolve()
        types = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                 '.png': 'image/png', '.webp': 'image/webp'}
        if relative.is_absolute() or candidate.parent != assets or candidate.suffix.lower() not in types:
            raise MonitorError('avatar_file 必须指向本应用 assets 文件夹内的 JPG、PNG 或 WebP 图片。')
        path, content_type = candidate, types[candidate.suffix.lower()]
    return path, content_type


def make_handler(monitor):
    class Handler(BaseHTTPRequestHandler):
        def send_data(self, status, body, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            host = self.headers.get('Host', '').split(':')[0]
            if host not in ('127.0.0.1', 'localhost'):
                self.send_data(403, b'Local access only', 'text/plain')
                return
            route = self.path.split('?')[0]
            try:
                if route == '/api/health':
                    body = json.dumps({'app': 'codex-token-monitor', 'directory': str(BASE),
                                       'pid': os.getpid()}).encode()
                    self.send_data(200, body, 'application/json')
                elif route in ('/', '/index.html'):
                    self.send_data(200, (BASE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
                elif route == '/assets/avatar.svg':
                    path, content_type = avatar_asset(monitor.config)
                    self.send_data(200, path.read_bytes(), content_type)
                elif route == '/assets/logo.jpg':
                    self.send_data(200, (BASE / 'assets' / 'logo.jpg').read_bytes(), 'image/jpeg')
                elif route == '/api/usage':
                    self.send_data(200, json.dumps(monitor.snapshot(), ensure_ascii=False).encode(),
                                   'application/json; charset=utf-8')
                elif route == '/api/export.csv':
                    self.send_data(200, csv_bytes(monitor.snapshot()), 'text/csv; charset=utf-8')
                elif route == '/favicon.ico':
                    self.send_data(204, b'', 'image/x-icon')
                else:
                    self.send_data(404, b'Not found', 'text/plain')
            except (OSError, sqlite3.Error, ValueError) as exc:
                message = str(exc) if isinstance(exc, MonitorError) else '暂时无法读取本地用量，请检查本机配置与日志。'
                body = json.dumps({'error': message,
                                   'detail': type(exc).__name__}, ensure_ascii=False).encode()
                self.send_data(503, body, 'application/json; charset=utf-8')

        def log_message(self, *_):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', action='store_true')
    parser.add_argument('--cutoff', help='Optional UTC ISO timestamp for a reproducible export')
    parser.add_argument('--port', type=int, help='Override the local server port')
    args = parser.parse_args()
    config = load_config()
    monitor = Monitor(config)
    if args.snapshot:
        data = monitor.snapshot(args.cutoff)
        (BASE / 'usage-snapshot.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        (BASE / 'usage-summary.csv').write_bytes(csv_bytes(data))
        print(json.dumps({'total': data['totals'], 'warnings': data['warnings']}, ensure_ascii=False))
        return
    port = args.port if args.port is not None else config.get('port', 18766)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise MonitorError('端口必须是 1024 到 65535 之间的整数。')
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(monitor))
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    try:
        main()
    except (MonitorError, OSError, sqlite3.Error, ValueError) as exc:
        raise SystemExit(str(exc)) from None
