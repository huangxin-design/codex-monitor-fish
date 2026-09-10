"""One-click local desktop launcher for Codex 监控小鱼 (standard library only)."""
import argparse
from contextlib import closing
from copy import deepcopy
import ctypes
import json
import ntpath
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler
import webbrowser


ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
APP = ROOT / 'app'
DESKTOP = ROOT / 'desktop'
sys.path.insert(0, str(APP))
import monitor as accounting
APP_ID = 'codex-monitor-fish-desktop'
VERSION = '0.2.3'
MAX_BODY = 16 * 1024


def default_data_dir():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'CodexMonitorFish'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'CodexMonitorFish'


def resolve_home(value=None):
    if value is None:
        value = os.environ.get('CODEX_HOME') or Path.home() / '.codex'
    if not isinstance(value, (str, os.PathLike)):
        raise accounting.MonitorError('数据目录必须是本机的完整路径。')
    value = os.fspath(value)
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise accounting.MonitorError('数据目录必须是有效的本机完整路径。')
    # Reject network/device namespaces before expanduser, resolve, stat or SQLite.
    normalized = value.replace('\\', '/')
    if normalized.startswith('//') or normalized.lower().startswith(('/??/', '/device/')):
        raise accounting.MonitorError('仅支持本机数据目录，不支持网络共享或设备路径。')
    path = Path(value).expanduser()
    if str(path).replace('\\', '/').startswith('//'):
        raise accounting.MonitorError('仅支持本机数据目录，不支持网络共享或设备路径。')
    if not (ntpath.isabs(str(path)) or path.is_absolute()):
        raise accounting.MonitorError('请填写本机数据目录的完整路径。')
    return path.resolve()


def discover_projects(home):
    """Only read validated Codex tables; historical and archived roots remain visible."""
    database = resolve_home(home) / 'state_5.sqlite'
    if not database.is_file():
        raise accounting.MonitorError('这里还没有找到 Codex 使用记录。请先在本机 Codex 中使用一个项目，或选择实际的数据目录。')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=3)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        required = {
            'threads': {'id', 'name', 'rollout_path', 'model', 'created_at_ms', 'cwd', 'thread_source', 'archived'},
            'thread_spawn_edges': {'parent_thread_id', 'child_thread_id'},
        }
        for table, fields in required.items():
            actual = {row['name'] for row in conn.execute('PRAGMA table_info(' + table + ')')}
            if not fields.issubset(actual):
                raise accounting.MonitorError('当前 Codex 的记录格式暂不受支持，请更新监控器后再试。')
        rows = conn.execute(
            'SELECT cwd FROM threads WHERE thread_source = ? '
            'AND id NOT IN (SELECT child_thread_id FROM thread_spawn_edges WHERE child_thread_id IS NOT NULL) '
            'ORDER BY created_at_ms DESC', ('user',)).fetchall()
    projects = {}
    for row in rows:
        directory = row['cwd']
        if not isinstance(directory, str) or not (ntpath.isabs(directory) or Path(directory).is_absolute()):
            continue
        key = accounting.normalized_directory(directory)
        if key not in projects:
            projects[key] = {'directory': directory,
                             'name': ntpath.basename(directory.rstrip('/\\')) or directory,
                             'task_count': 0}
        projects[key]['task_count'] += 1
    return list(projects.values())


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class DesktopState:
    def __init__(self, data_dir, codex_home=None):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / 'settings.json'
        self.lock = threading.RLock()
        self.csrf_token = secrets.token_urlsafe(32)
        self.instance = secrets.token_urlsafe(24)
        self.monitor = None
        self.config = {}
        self.startup_error = None
        self.scan_running = False
        self.scan_completed_at = None
        self.scan_result = None
        self.scan_error = None
        self.scan_progress = None
        self.scan_partial = None
        try:
            if self.settings_path.is_file():
                settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
                if not isinstance(settings, dict):
                    raise ValueError('invalid settings')
                self.config = {
                    'codex_home': str(resolve_home(codex_home or settings.get('codex_home'))),
                    'project_directory': settings.get('project_directory'),
                    'project_name': settings.get('project_name'),
                    'refresh_seconds': 10,
                }
                self.monitor = accounting.Monitor(self.config)
        except (OSError, ValueError, TypeError, RecursionError):
            self.startup_error = '上次的设置无法读取，请重新选择项目。'
            self.config = {}
        self.home = resolve_home(codex_home or self.config.get('codex_home'))

    def setup_info(self, home=None):
        with self.lock:
            source = resolve_home(self.home if home is None else home)
            result = {
                'configured': self.monitor is not None,
                'project_directory': self.config.get('project_directory'),
                'codex_home': str(source), 'projects': [], 'csrf_token': self.csrf_token,
            }
            try:
                result['projects'] = discover_projects(source)
                if self.startup_error:
                    result['error'] = self.startup_error
            except (OSError, sqlite3.Error, ValueError) as exc:
                result['error'] = str(exc) if isinstance(exc, accounting.MonitorError) else '暂时无法读取这个数据目录，请检查路径后重试。'
            return result

    def configure(self, payload):
        directory = payload.get('project_directory')
        source = payload.get('codex_home')
        if not isinstance(directory, str) or not directory.strip():
            raise accounting.MonitorError('请选择一个要监控的项目。')
        if source is not None and (not isinstance(source, str) or not source.strip()):
            raise accounting.MonitorError('数据目录必须是有效的完整路径。')
        with self.lock:
            home = resolve_home(source or self.home)
            project = next((item for item in discover_projects(home)
                            if accounting.normalized_directory(item['directory']) ==
                            accounting.normalized_directory(directory)), None)
            if project is None:
                raise accounting.MonitorError('没有在这个数据目录中找到所选项目，请重新选择。')
            config = {'codex_home': str(home), 'project_directory': project['directory'],
                      'project_name': project['name'], 'refresh_seconds': 10}
            monitor = accounting.Monitor(config)
            atomic_json(self.settings_path, config)
            self.config, self.monitor, self.home = config, monitor, home
            self.startup_error = None
            self.scan_result = self.scan_error = self.scan_completed_at = None
            self.scan_progress = self.scan_partial = None
            return {'ok': True, 'project_directory': config['project_directory']}

    def snapshot(self):
        with self.lock:
            monitor = self.monitor
        if monitor is None:
            raise accounting.MonitorError('请先选择要监控的项目。')
        return monitor.snapshot()

    def _scan_usage(self, monitor):
        def progress(payload):
            # Validate before publishing and detach lists from the scanner's next update.
            update = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
            current, partial = update['progress'], update['partial_snapshot']
            with self.lock:
                if self.monitor is monitor:
                    self.scan_progress, self.scan_partial = current, partial

        result, error = None, None
        try:
            result = monitor.snapshot(progress=progress)
            result = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
        except Exception as exc:
            result = None
            error = str(exc) if isinstance(exc, accounting.MonitorError) else '暂时无法读取本地数据，请稍后重试。'
        with self.lock:
            # A completed scan for a previous selection must never reach the new project.
            if self.monitor is monitor:
                self.scan_result, self.scan_error = result, error
                self.scan_partial = None
                if error is not None:
                    self.scan_progress = None
                self.scan_completed_at = time.monotonic()
            self.scan_running = False

    def usage_response(self, refresh=False):
        """Return promptly while one daemon scans; polling never queues duplicate work."""
        with self.lock:
            if self.monitor is None:
                raise accounting.MonitorError('请先选择要监控的项目。')
            identity = {'project_name': self.config.get('project_name') or '当前项目',
                        'project_directory': self.config.get('project_directory'),
                        'source_home': str(self.home)}
            due = (self.scan_completed_at is None or refresh or
                   time.monotonic() - self.scan_completed_at >= self.config.get('refresh_seconds', 10))
            if not self.scan_running and due:
                self.scan_running = True
                self.scan_error = None
                self.scan_progress = self.scan_partial = None
                threading.Thread(target=self._scan_usage, args=(self.monitor,), daemon=True).start()
            if self.scan_error is not None:
                return 503, {**identity, 'error': self.scan_error,
                             'progress': None, 'partial_snapshot': None}
            if self.scan_result is None:
                return 202, {'status': 'loading', 'message': '正在读取历史记录，请稍候…',
                             **identity, 'progress': deepcopy(self.scan_progress),
                             'partial_snapshot': deepcopy(self.scan_partial)}
            return 200, {**deepcopy(self.scan_result), 'refreshing': self.scan_running,
                         'progress': deepcopy(self.scan_progress)}


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send_data(self, status, body, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, body):
            self.send_data(status, json.dumps(body, ensure_ascii=False).encode('utf-8'),
                           'application/json; charset=utf-8')

        def local_host(self):
            expected = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in expected:
                self.send_json(403, {'error': '仅允许从本机页面访问。'})
                return False
            return True

        def do_GET(self):
            if not self.local_host():
                return
            route = urlsplit(self.path)
            try:
                if route.path == '/api/health':
                    self.send_json(200, {'app': APP_ID, 'data_dir': str(state.data_dir),
                                         'pid': os.getpid(), 'instance': state.instance, 'version': VERSION})
                elif route.path == '/api/setup':
                    if 'codex_home' in parse_qs(route.query, keep_blank_values=True):
                        self.send_json(400, {'error': '请在监控器页面中更改数据目录。'})
                    else:
                        self.send_json(200, state.setup_info())
                elif route.path in ('/', '/index.html', '/setup'):
                    if route.path == '/setup' or state.monitor is None:
                        body = (DESKTOP / 'setup.html').read_bytes()
                    else:
                        html = (APP / 'index.html').read_text(encoding='utf-8')
                        body = html.replace('</body>', '<script src="/desktop.js"></script></body>').encode('utf-8')
                    self.send_data(200, body, 'text/html; charset=utf-8')
                elif route.path in ('/setup.js', '/desktop.js'):
                    self.send_data(200, (DESKTOP / route.path[1:]).read_bytes(), 'text/javascript; charset=utf-8')
                elif route.path == '/assets/avatar.svg':
                    self.send_data(200, (APP / 'assets/avatar.svg').read_bytes(), 'image/svg+xml')
                elif route.path == '/assets/logo.jpg':
                    self.send_data(200, (APP / 'assets/logo.jpg').read_bytes(), 'image/jpeg')
                elif route.path == '/api/usage':
                    refresh = parse_qs(route.query).get('refresh') == ['1']
                    status, body = state.usage_response(refresh)
                    self.send_json(status, body)
                elif route.path == '/api/export.csv':
                    status, body = state.usage_response()
                    if status == 200:
                        self.send_data(200, accounting.csv_bytes(body), 'text/csv; charset=utf-8')
                    else:
                        self.send_json(503, body if status == 503 else {'error': '历史记录仍在读取，请稍后再导出。'})
                elif route.path == '/favicon.ico':
                    self.send_data(204, b'', 'image/x-icon')
                else:
                    self.send_json(404, {'error': '页面不存在。'})
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.send_json(503, {'error': str(exc) if isinstance(exc, accounting.MonitorError)
                                    else '暂时无法读取本地数据，请稍后重试。'})

        def do_POST(self):
            if not self.local_host():
                return
            if (self.headers.get('Origin') != 'http://' + self.headers.get('Host', '') or
                    not secrets.compare_digest(self.headers.get('X-Monitor-Token', '').encode('utf-8'),
                                               state.csrf_token.encode('utf-8'))):
                self.send_json(403, {'error': '请从监控器页面发起操作，刷新页面后重试。'})
                return
            if (self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json'
                    or self.headers.get('Transfer-Encoding')):
                self.send_json(415, {'error': '请使用 JSON 格式。'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if not 0 < length <= MAX_BODY:
                self.send_json(413, {'error': '请求内容为空或过长。'})
                return
            try:
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('invalid payload')
            except (ValueError, UnicodeError, RecursionError):
                self.send_json(400, {'error': '请求内容无效。'})
                return
            except TimeoutError:
                self.send_json(408, {'error': '读取请求超时，请重试。'})
                return
            except OSError:
                self.close_connection = True
                return
            route = urlsplit(self.path).path
            try:
                if route == '/api/setup':
                    self.send_json(200, state.configure(payload))
                elif route == '/api/discover':
                    home = payload.get('codex_home')
                    if not isinstance(home, str) or not home.strip():
                        raise accounting.MonitorError('请填写本机数据目录的完整路径。')
                    self.send_json(200, state.setup_info(home))
                elif route == '/api/quit':
                    self.send_json(200, {'ok': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self.send_json(404, {'error': '操作不存在。'})
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.send_json(400, {'error': str(exc) if isinstance(exc, accounting.MonitorError)
                                    else '无法保存设置，请检查本地目录权限。'})

        def log_message(self, *_):
            pass
    return Handler


class InstanceLock:
    """OS releases the lock even after a crash; metadata alone never proves liveness."""
    def __init__(self, path):
        self.stream = Path(path).open('a+b')
        self.owned = False

    def acquire(self):
        if self.owned:
            return True
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.owned = True
            return True
        except OSError:
            return False

    def close(self):
        if self.owned:
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def existing_url(data_dir):
    try:
        saved = json.loads((Path(data_dir) / 'server.json').read_text(encoding='utf-8'))
        if not isinstance(saved, dict):
            return None
        url = saved['url']
        if not isinstance(url, str):
            return None
        endpoint = urlsplit(url)
        if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1'
                or not endpoint.port or endpoint.path not in ('', '/')
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            return None
        opener = build_opener(ProxyHandler({}), NoRedirects())
        with opener.open(url.rstrip('/') + '/api/health', timeout=0.7) as response:
            active = json.load(response)
        if not isinstance(active, dict):
            return None
        expected = {'app': APP_ID, 'data_dir': str(Path(data_dir).resolve()),
                    'pid': saved['pid'], 'instance': saved['instance']}
        if all(active.get(key) == value for key, value in expected.items()):
            return url
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        pass
    return None


class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        # Windows SO_REUSEADDR can bind an occupied port and split its traffic.
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def create_server(state, port=18776):
    if type(port) is not int or not 0 <= port <= 65535:
        raise accounting.MonitorError('本地端口设置无效。')
    try:
        server = LocalHTTPServer(('127.0.0.1', port), make_handler(state))
    except OSError:
        if not port:
            raise
        server = LocalHTTPServer(('127.0.0.1', 0), make_handler(state))
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    parser.add_argument('--codex-home', type=Path)
    parser.add_argument('--port', type=int, default=18776)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    directory = args.data_dir.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(directory / 'runtime.lock')
    server = None
    state = None
    try:
        for _ in range(30):
            if lock.acquire():
                break
            url = existing_url(directory)
            if url:
                if not args.no_browser:
                    webbrowser.open(url)
                return 0
            time.sleep(0.1)
        else:
            raise accounting.MonitorError('监控器正在启动，请稍等片刻后重新打开。')
        state = DesktopState(directory, args.codex_home)
        server = create_server(state, args.port)
        url = f'http://127.0.0.1:{server.server_port}/'
        atomic_json(directory / 'server.json', {'url': url, 'pid': os.getpid(),
                                               'instance': state.instance, 'version': VERSION})
        if not args.no_browser:
            threading.Timer(0.25, webbrowser.open, args=(url,)).start()
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
            (directory / 'server.json').unlink(missing_ok=True)
        lock.close()
    return 0


def show_fatal_error(exc):
    message = 'Codex 监控小鱼未能启动。\n\n' + str(exc)
    if os.name == 'nt':
        ctypes.windll.user32.MessageBoxW(None, message, 'Codex 监控小鱼', 0x10)
    elif sys.stderr:
        print(message, file=sys.stderr)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, sqlite3.Error, ValueError) as exc:
        show_fatal_error(exc)
        raise SystemExit(1) from None
