"""Start this local monitor with the current Python interpreter (no packages needed)."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from monitor import BASE, Monitor, MonitorError, load_config


def health(port):
    try:
        request = urllib.request.Request('http://127.0.0.1:' + str(port) + '/api/health')
        # A system proxy must never receive requests about this local app.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1) as response:
            return json.loads(response.read(8192))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def is_our_server(data):
    return (isinstance(data, dict) and data.get('app') == 'codex-token-monitor'
            and isinstance(data.get('directory'), str)
            and os.path.normcase(str(Path(data['directory']).resolve())) ==
                os.path.normcase(str(BASE.resolve())))


def ensure_server(port):
    current = health(port)
    if is_our_server(current):
        return False
    probe = socket.socket()
    try:
        probe.bind(('127.0.0.1', port))
    except OSError:
        raise MonitorError('端口 ' + str(port) + ' 已被其他应用占用。请使用 --port 18767 或修改 config.json 的 port；'
                           '不会关闭其他程序。') from None
    finally:
        probe.close()
    options = {'cwd': str(BASE), 'stdin': subprocess.DEVNULL}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    else:
        options['start_new_session'] = True
    with (BASE / 'monitor.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, str(BASE / 'monitor.py'), '--port', str(port)],
                                   stdout=log, stderr=log, **options)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if is_our_server(health(port)):
            return True
        if process.poll() is not None:
            break
        time.sleep(0.2)
    raise MonitorError('监控未能启动。请查看本应用目录的 monitor.log；已有其他程序不会被关闭。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, help='Use another port without changing config.json')
    parser.add_argument('--no-browser', action='store_true', help='Only print the local page URL')
    args = parser.parse_args()
    config = load_config()
    Monitor(config)  # Validate required scope before starting a background process.
    port = args.port if args.port is not None else config.get('port', 18766)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise MonitorError('端口必须是 1024 到 65535 之间的整数。')
    started = ensure_server(port)
    url = 'http://127.0.0.1:' + str(port) + '/'
    print(('已启动：' if started else '已在运行：') + url)
    if not args.no_browser:
        webbrowser.open(url)


if __name__ == '__main__':
    try:
        main()
    except (MonitorError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
