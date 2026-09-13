"""Exercise only the distributed Skill in isolated folders and real local processes."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request
import zipfile

from desktop.test_runtime import synthetic_home
from tooling import build_release


class SkillStartTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fish-skill-test-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.home = synthetic_home(self.base / 'synthetic-records')
        self.data = self.base / 'preferences'
        package = build_release.build(output=self.base / 'release')
        # This directory contains only the release contents, never the source repo.
        self.install = self.base / 'friend skill 中文'
        with zipfile.ZipFile(package) as bundle:
            bundle.extractall(self.install)
        self.skill = self.install / 'codex-token-monitor'
        self.script = self.skill / 'scripts/start_monitor.py'
        self.package_before = self.fingerprints(self.install)
        self.records_before = self.fingerprints(self.home)
        self.env = dict(os.environ, PATH='', PYTHONIOENCODING='utf-8', CODEX_HOME=str(self.home))
        for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE'):
            self.env.pop(name, None)
        self.opener = build_opener(ProxyHandler({}))
        self.servers = []
        self.addCleanup(self.stop_owned_servers)

    @staticmethod
    def fingerprints(directory):
        return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.rglob('*') if path.is_file()}

    def invoke(self, *extra, data=None, home=None, expect_success=True):
        command = [sys.executable, str(self.script), '--data-dir', str(data or self.data),
                   '--codex-home', str(home or self.home), '--port', '0', '--no-browser', *extra]
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        result = subprocess.run(command, cwd=self.base, env=self.env, capture_output=True,
                                encoding='utf-8', timeout=25, **kwargs)
        if not expect_success:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            return result
        if result.returncode:
            log = (data or self.data) / 'monitor.log'
            diagnostic = log.read_text(encoding='utf-8', errors='replace')[-8000:] if log.is_file() else '(missing)'
            self.fail(f'{result.stderr}\nSynthetic monitor.log:\n{diagnostic}')
        payload = json.loads(result.stdout)
        self.assertFalse(payload['browser_opened'])
        endpoint = payload['url'].removesuffix('setup').rstrip('/')
        _, health = self.request(endpoint, '/api/health')
        self.assertEqual(health['app'], 'codex-monitor-fish-desktop')
        self.assertEqual(health['data_dir'], str((data or self.data).resolve()))
        self.assertEqual(health['pid'], payload['pid'])
        self.assertEqual(health['version'], payload['version'])
        if not any(item[1]['instance'] == health['instance'] for item in self.servers):
            self.servers.append((endpoint, health))
        return payload, endpoint

    def request(self, endpoint, route, body=None, token=''):
        headers = {'Origin': endpoint, 'Content-Type': 'application/json', 'X-Monitor-Token': token}
        request = Request(endpoint + route, headers=headers,
                          data=json.dumps(body).encode('utf-8') if body is not None else None)
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            value = response.read()
            if response.headers.get_content_type() == 'application/json':
                value = json.loads(value)
            return response.status, value

    def stop_owned_servers(self):
        for endpoint, expected in reversed(self.servers):
            try:
                _, actual = self.request(endpoint, '/api/health')
            except OSError:
                continue
            # Never send quit unless the live identity is exactly the process this test started.
            self.assertEqual(actual, expected)
            process = None
            if os.name == 'nt':
                import ctypes
                from ctypes import wintypes
                kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
                kernel32.OpenProcess.restype = wintypes.HANDLE
                kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
                kernel32.WaitForSingleObject.restype = wintypes.DWORD
                kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
                kernel32.CloseHandle.restype = wintypes.BOOL
                process = kernel32.OpenProcess(0x00100000, False, expected['pid'])  # SYNCHRONIZE only.
                self.assertTrue(process, f'Cannot wait for owned server: {ctypes.get_last_error()}')
            try:
                _, setup = self.request(endpoint, '/api/setup')
                status, _ = self.request(endpoint, '/api/quit', {}, setup['csrf_token'])
                self.assertEqual(status, 200)
                deadline = time.monotonic() + 5
                saved = Path(expected['data_dir']) / 'server.json'
                while saved.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertFalse(saved.exists(), 'Owned test server did not shut down.')
                if process:
                    self.assertEqual(kernel32.WaitForSingleObject(process, 5000), 0,
                                     'Owned test server did not exit and release its files.')
            finally:
                if process:
                    kernel32.CloseHandle(process)
        self.servers.clear()

    def select(self, endpoint, project):
        _, setup = self.request(endpoint, '/api/setup')
        status, payload = self.request(endpoint, '/api/setup', {'project_directory': project},
                                       setup['csrf_token'])
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload['ok'])

    def completed_usage(self, endpoint):
        status, payload = self.request(endpoint, '/api/usage')
        self.assertEqual(status, 202, payload)
        self.assertEqual(payload['status'], 'loading')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, payload = self.request(endpoint, '/api/usage')
            if status == 200:
                return payload
            self.assertEqual(status, 202, payload)
            time.sleep(.02)
        self.fail('Synthetic background scan never completed.')

    def test_isolated_zip_select_switch_progress_reuse_and_choose_project(self):
        first, endpoint = self.invoke()
        self.assertFalse(first['reused'])
        _, setup = self.request(endpoint, '/api/setup')
        self.assertFalse(setup['configured'])
        self.assertEqual(len(setup['projects']), 2)
        _, html = self.request(endpoint, '/')
        self.assertIn('选择要监控的项目', html.decode('utf-8'))
        for asset in ('/setup.js', '/desktop.js', '/assets/logo.jpg', '/assets/avatar.svg'):
            self.assertEqual(self.request(endpoint, asset)[0], 200)
        self.select(endpoint, 'C:/Projects/Alpha')
        alpha = self.completed_usage(endpoint)
        self.assertEqual(alpha['totals']['total_tokens'], 260)
        second, reused = self.invoke('--choose-project')
        self.assertTrue(second['reused'])
        self.assertEqual(second['pid'], first['pid'])
        self.assertEqual(endpoint, reused)
        self.assertEqual(second['url'], endpoint + '/setup')
        self.assertIn('选择要监控的项目', self.request(endpoint, '/setup')[1].decode('utf-8'))
        self.assertEqual(self.request(endpoint, '/api/setup')[1]['project_directory'], 'C:/Projects/Alpha')
        self.select(endpoint, '/projects/beta')
        beta = self.completed_usage(endpoint)
        self.assertEqual(beta['project_directory'], '/projects/beta')
        self.assertEqual(beta['totals']['total_tokens'], 70)
        self.assertNotIn('Design', json.dumps(beta))
        _, html = self.request(endpoint, '/')
        self.assertIn('/desktop.js', html.decode('utf-8'))
        self.assertEqual(self.fingerprints(self.home), self.records_before)
        self.assertEqual(self.fingerprints(self.install), self.package_before)

    def test_restart_restores_selection_without_scanning_before_page_opens(self):
        _, endpoint = self.invoke()
        self.select(endpoint, '/projects/beta')
        self.stop_owned_servers()
        again, endpoint = self.invoke()
        self.assertFalse(again['reused'])
        _, setup = self.request(endpoint, '/api/setup')
        self.assertTrue(setup['configured'])
        self.assertEqual(setup['project_directory'], '/projects/beta')
        self.assertEqual(self.completed_usage(endpoint)['totals']['total_tokens'], 70)

    def test_occupied_port_is_not_reused_or_closed(self):
        with socket.socket() as unrelated:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                unrelated.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            unrelated.bind(('127.0.0.1', 0))
            unrelated.listen(1)
            port = unrelated.getsockname()[1]
            _, endpoint = self.invoke('--port', str(port))
            self.assertNotEqual(endpoint, f'http://127.0.0.1:{port}')
            with socket.create_connection(('127.0.0.1', port), timeout=2):
                accepted, _ = unrelated.accept()
                accepted.close()

    def test_missing_records_still_opens_selector_without_creating_source(self):
        missing = self.base / 'not-yet-created-codex'
        _, endpoint = self.invoke(home=missing)
        status, setup = self.request(endpoint, '/api/setup')
        self.assertEqual(status, 200)
        self.assertFalse(setup['configured'])
        self.assertEqual(setup['projects'], [])
        self.assertIn('error', setup)
        self.assertIn('选择要监控的项目', self.request(endpoint, '/')[1].decode('utf-8'))
        self.assertFalse(missing.exists())

    def test_data_directory_cannot_write_into_skill_or_codex_records(self):
        for destination in (self.skill, self.skill / 'new-data', self.home, self.home / 'new-data'):
            with self.subTest(destination=destination):
                result = self.invoke(data=destination, expect_success=False)
                self.assertIn('运行数据目录不能', result.stderr)
        self.assertEqual(self.fingerprints(self.home), self.records_before)
        self.assertEqual(self.fingerprints(self.install), self.package_before)

    def test_reuse_with_changed_source_fails_without_overwriting_selected_project(self):
        first, endpoint = self.invoke()
        self.select(endpoint, '/projects/beta')
        settings = (self.data / 'settings.json').read_bytes()
        other = synthetic_home(self.base / 'other-records')
        result = self.invoke(home=other, expect_success=False)
        self.assertIn('另一数据目录', result.stderr)
        self.assertEqual((self.data / 'settings.json').read_bytes(), settings)
        self.assertEqual(self.request(endpoint, '/api/health')[1]['pid'], first['pid'])
        self.assertEqual(self.request(endpoint, '/api/setup')[1]['codex_home'], str(self.home))

    def test_invalid_port_fails_before_runtime_directory_is_created(self):
        result = self.invoke('--port', '65536', expect_success=False)
        self.assertIn('端口', result.stderr)
        self.assertFalse(self.data.exists())


if __name__ == '__main__':
    unittest.main()
