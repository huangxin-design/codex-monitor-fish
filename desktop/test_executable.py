"""Exercise the distributed EXE from an isolated folder with no Python on PATH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def create_fixture(home, project):
    home.mkdir(parents=True)
    project.mkdir(parents=True)
    conn = sqlite3.connect(home / 'state_5.sqlite')
    try:
        conn.execute('CREATE TABLE threads (id TEXT, name TEXT, rollout_path TEXT, model TEXT, created_at_ms INTEGER, cwd TEXT, thread_source TEXT, archived INTEGER)')
        conn.execute('CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT)')
        for ident, total, source, directory in (
            ('demo', 110, 'user', project), ('child', 220, 'subagent', project),
            ('other', 990, 'user', project.parent / '另一个项目'),
        ):
            log = home / (ident + '.jsonl')
            usage = {'input_tokens': total - 10, 'cached_input_tokens': 50,
                     'output_tokens': 10, 'reasoning_output_tokens': 3, 'total_tokens': total}
            item = {'type': 'token_usage_record', 'timestamp': '2026-09-01T08:00:00Z',
                    'payload': {'thread_id': ident, 'response_id': ident + '-request', 'usage': usage}}
            log.write_text(json.dumps(item) + '\n', encoding='utf-8')
            conn.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)',
                         (ident, ident, str(log), 'test-model', 0, str(directory), source, 0))
        conn.execute('INSERT INTO thread_spawn_edges VALUES (?,?)', ('demo', 'child'))
        conn.commit()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='fish-app-test-') as temporary:
        root = Path(temporary)
        exe = root / 'CodexMonitorFish.exe'
        shutil.copyfile(args.exe, exe)
        home, project, data_dir = root / 'records', root / '示例 project', root / 'settings'
        create_fixture(home, project)
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in home.iterdir()}
        env = dict(os.environ, PATH='')
        env.pop('PYTHONHOME', None)
        env.pop('PYTHONPATH', None)
        kwargs = {'cwd': root, 'env': env, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        command = [str(exe), '--no-browser', '--data-dir', str(data_dir), '--codex-home', str(home)]
        process = subprocess.Popen(command, **kwargs)
        endpoint, token = None, None

        def request(route, data=None):
            body = json.dumps(data).encode() if data is not None else None
            headers = {'Origin': endpoint, 'Content-Type': 'application/json', 'X-Monitor-Token': token or ''}
            with opener.open(urllib.request.Request(endpoint + route, data=body, headers=headers), timeout=15) as response:
                return response.read(), response.headers

        def ready_snapshot():
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                payload = json.loads(request('/api/usage')[0])
                if 'totals' in payload:
                    return payload
                assert payload.get('status') == 'loading', payload
                time.sleep(.05)
            raise AssertionError('Background scan did not produce a snapshot.')

        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                state_file = data_dir / 'server.json'
                if state_file.is_file():
                    state = json.loads(state_file.read_text(encoding='utf-8'))
                    endpoint = state.get('url') or state.get('endpoint')
                    if endpoint:
                        endpoint = endpoint.rstrip('/')
                        try:
                            info = json.loads(request('/api/setup')[0])
                            token = info['csrf_token']
                            break
                        except (OSError, ValueError, KeyError):
                            pass
                if process.poll() is not None:
                    raise AssertionError('Executable exited before opening local server.')
                time.sleep(.2)
            else:
                raise AssertionError('Executable did not open a local web server.')
            assert not info['configured']
            assert len(info['projects']) == 2
            assert '选择要监控的项目' in request('/')[0].decode()
            request('/api/setup', {'project_directory': str(project)})
            snapshot = ready_snapshot()
            assert snapshot['totals']['total_tokens'] == 330
            html = request('/')[0].decode()
            assert 'Codex 监控小鱼' in html and '/desktop.js' in html
            assert request('/assets/logo.jpg')[1].get_content_type() == 'image/jpeg'
            assert '切换项目' in request('/desktop.js')[0].decode()
            with subprocess.Popen(command, **kwargs) as second:
                assert second.wait(timeout=20) == 0
            assert ready_snapshot()['totals']['total_tokens'] == 330
            after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in home.iterdir()}
            assert before == after, 'Source records were changed.'
            request('/api/quit', {})
            assert process.wait(timeout=20) == 0
            # A fresh process must restore the saved choice without another setup.
            process = subprocess.Popen([str(exe), '--no-browser', '--data-dir', str(data_dir)], **kwargs)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state_file = data_dir / 'server.json'
                if state_file.is_file():
                    try:
                        endpoint = json.loads(state_file.read_text(encoding='utf-8'))['url'].rstrip('/')
                        restored = json.loads(request('/api/setup')[0])
                        token = restored['csrf_token']
                        assert restored['configured']
                        assert restored['project_directory'] == str(project)
                        break
                    except (OSError, ValueError, KeyError):
                        pass
                time.sleep(.2)
            else:
                raise AssertionError('Restart did not restore the saved project.')
            assert ready_snapshot()['totals']['total_tokens'] == 330
            request('/api/quit', {})
            assert process.wait(timeout=20) == 0
            print('EXE smoke PASS: no Python PATH; project selection; total330; assets; single instance; readonly source; restart restores selection; graceful quit.')
        finally:
            if process.poll() is None:
                if endpoint and token:
                    try:
                        request('/api/quit', {})
                        process.wait(timeout=10)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == '__main__':
    main()
