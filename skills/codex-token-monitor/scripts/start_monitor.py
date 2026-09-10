"""Start the bundled monitor in the background and return its verified local URL."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import webbrowser

SKILL = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(SKILL / 'assets/desktop'))
import runtime


def default_data_dir():
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    return base / 'CodexMonitorFishSkill'


def start(data_dir=None, codex_home=None, port=18776, no_browser=False, choose_project=False):
    directory = Path(data_dir or default_data_dir()).expanduser().resolve()
    home = runtime.resolve_home(codex_home)
    if directory == SKILL or SKILL in directory.parents or directory == home or home in directory.parents:
        raise ValueError('运行数据目录不能位于 Skill 包或 Codex 记录目录内。')
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('本地端口设置无效。')
    directory.mkdir(parents=True, exist_ok=True)
    url = runtime.existing_url(directory)
    reused = bool(url)
    if not url:
        command = [sys.executable, '-B', str(SKILL / 'assets/desktop/runtime.py'),
                   '--data-dir', str(directory), '--port', str(port), '--no-browser']
        if codex_home is not None:
            command.extend(['--codex-home', str(home)])
        options = {'cwd': str(directory), 'stdin': subprocess.DEVNULL}
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options['start_new_session'] = True
        with (directory / 'monitor.log').open('ab') as log:
            process = subprocess.Popen(command, stdout=log, stderr=log, **options)
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                url = runtime.existing_url(directory)
                if url:
                    break
                if process.poll() not in (None, 0):
                    raise RuntimeError('监控服务未能启动，请检查运行目录中的 monitor.log。')
                time.sleep(0.1)
            if not url:
                raise RuntimeError('监控服务启动超时，请检查运行目录中的 monitor.log。')
        except Exception:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            raise
    saved = json.loads((directory / 'server.json').read_text(encoding='utf-8'))
    if saved.get('version') != runtime.VERSION:
        raise RuntimeError('旧版监控器仍在运行，请从它的网页退出监控后再次启动 Skill。')
    # Changing the data source is a visible project-selection action, not a launch side effect.
    if reused and codex_home is not None:
        opener = runtime.build_opener(runtime.ProxyHandler({}), runtime.NoRedirects())
        with opener.open(url + 'api/setup', timeout=4) as response:
            setup = json.load(response)
        if runtime.resolve_home(setup['codex_home']) != home:
            raise ValueError('正在运行的监控器使用另一数据目录，请在项目选择页更改目录后选择项目。')
    target = url + 'setup' if choose_project else url
    opened = bool(webbrowser.open(target)) if not no_browser else False
    return {'url': target, 'data_dir': str(directory), 'reused': reused,
            'pid': saved['pid'], 'version': runtime.VERSION, 'browser_opened': opened}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--codex-home', type=Path)
    parser.add_argument('--port', type=int, default=18776)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--choose-project', action='store_true')
    args = parser.parse_args()
    try:
        result = start(args.data_dir, args.codex_home, args.port, args.no_browser, args.choose_project)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
