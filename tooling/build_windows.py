"""Build the Windows single-file app with its own Python runtime."""
import hashlib
import importlib.metadata
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build the Windows executable on Windows.')
    app = ROOT / 'skills/codex-token-monitor/assets/app'
    desktop = ROOT / 'skills/codex-token-monitor/assets/desktop'
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
            '--windowed', '--noupx', '--name', 'CodexMonitorFish-Windows',
            '--icon', str(ROOT / 'desktop/app.ico'),
            '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build/windows'),
            '--specpath', str(ROOT / 'build'), '--paths', str(app)]
    for source, target in (
        (app / 'index.html', 'app'),
        (app / 'assets/avatar.svg', 'app/assets'),
        (app / 'assets/logo.jpg', 'app/assets'),
        (app / 'assets/insights.js', 'app/assets'),
        (app / 'assets/insights.css', 'app/assets'),
        (desktop / 'setup.html', 'desktop'),
        (desktop / 'setup.js', 'desktop'),
        (desktop / 'desktop.js', 'desktop'),
        (ROOT / 'LICENSE', '.'),
        (ROOT / 'BRANDING.md', '.'),
    ):
        if not source.is_file():
            raise SystemExit('Missing build resource: ' + str(source.relative_to(ROOT)))
        args.extend(['--add-data', str(source) + ':' + target])
    # Carry the redistributed interpreter and bootloader notices with the EXE.
    notices = ROOT / 'build/notices'
    notices.mkdir(parents=True, exist_ok=True)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise SystemExit('Python distribution LICENSE.txt was not found.')
    (notices / 'Python-LICENSE.txt').write_bytes(python_license.read_bytes())
    pyinstaller = importlib.metadata.distribution('pyinstaller')
    bootloader_license = next(pyinstaller.locate_file(item) for item in pyinstaller.files
                             if str(item).endswith('licenses/COPYING.txt'))
    (notices / 'PyInstaller-COPYING.txt').write_bytes(bootloader_license.read_bytes())
    args.extend(['--add-data', str(notices) + ':notices'])
    args.append(str(desktop / 'runtime.py'))
    subprocess.run(args, cwd=ROOT, check=True)
    executable = ROOT / 'dist/CodexMonitorFish-Windows.exe'
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    executable.with_suffix('.exe.sha256').write_bytes((digest + '  ' + executable.name + '\n').encode('ascii'))
    print(str(executable))
    print('SHA256: ' + digest)


if __name__ == '__main__':
    main()
