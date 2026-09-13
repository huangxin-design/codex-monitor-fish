"""Build and verify a deterministic skill ZIP using only explicitly allowed files."""
import argparse
import hashlib
import io
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = 'codex-token-monitor'
ARCHIVE_NAME = SKILL_NAME + '-skill.zip'
STAMP = (1980, 1, 1, 0, 0, 0)
SKILL_FILES = (
    'SKILL.md',
    'agents/openai.yaml',
    'references/accounting.md',
    'scripts/setup_monitor.py',
    'scripts/start_monitor.py',
    'scripts/save_account_snapshot.py',
    'assets/desktop/runtime.py',
    'assets/desktop/setup.html',
    'assets/desktop/setup.js',
    'assets/desktop/desktop.js',
    'assets/app/monitor.py',
    'assets/app/pricing.py',
    'assets/app/test_pricing.py',
    'assets/app/account_usage.py',
    'assets/app/test_account_usage.py',
    'assets/app/test_monitor.py',
    'assets/app/launch.py',
    'assets/app/index.html',
    'assets/app/start.cmd',
    'assets/app/start.command',
    'assets/app/打开监控器.cmd',
    'assets/app/assets/logo.jpg',
    'assets/app/assets/avatar.svg',
    'assets/app/assets/insights.js',
    'assets/app/assets/insights.css',
)
DOC_IMAGES = ('dashboard-v0.3.1.png', 'cost-reset-v0.3.1.png')


def source_files(root=ROOT):
    root = Path(root).resolve()
    files = {SKILL_NAME + '/' + name: root / 'skills' / SKILL_NAME / name
             for name in SKILL_FILES}
    files['使用说明.md'] = root / 'docs' / '使用说明.md'
    for name in DOC_IMAGES:
        files['images/' + name] = root / 'docs' / 'images' / name
    files['LICENSE'] = root / 'LICENSE'
    files['BRANDING.md'] = root / 'BRANDING.md'
    for path in files.values():
        if not path.is_file():
            raise ValueError('Required release file is missing: ' + str(path.relative_to(root)))
        if any(part.is_symlink() for part in (path, *path.parents) if part != root and root in part.parents):
            raise ValueError('Release sources must not be symbolic links: ' + str(path.relative_to(root)))
        if root not in path.resolve().parents:
            raise ValueError('Release source leaves the repository: ' + str(path.relative_to(root)))
    sources = {}
    for name in sorted(files):
        body = files[name].read_bytes()
        if not name.endswith(('.jpg', '.png')):
            # Git checkouts may use different native EOLs; release text is canonical.
            body = body.replace(b'\r\n', b'\n')
            if name.endswith('.cmd'):
                body = body.replace(b'\n', b'\r\n')
        sources[name] = body
    return sources


def file_mode(name):
    return 0o100755 if name.endswith('/start.command') else 0o100644


def verify_archive(archive, root=ROOT):
    archive = Path(archive)
    expected = source_files(root)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.namelist() != list(expected):
            raise ValueError('Archive contains missing, duplicate, unexpected, or unsorted files.')
        if bundle.testzip() is not None:
            raise ValueError('Archive CRC integrity check failed.')
        for item in bundle.infolist():
            if bundle.read(item.filename) != expected[item.filename]:
                raise ValueError('Archive content differs from source: ' + item.filename)
            if (item.date_time != STAMP or item.create_system != 3
                    or item.external_attr >> 16 != file_mode(item.filename)
                    or item.compress_type != zipfile.ZIP_STORED):
                raise ValueError('Archive metadata is not deterministic: ' + item.filename)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(archive.suffix + '.sha256')
    if checksum.read_text(encoding='ascii') != digest + '  ' + archive.name + '\n':
        raise ValueError('SHA256 checksum does not match the archive.')
    return digest


def build(root=ROOT, output=None):
    sources = source_files(root)
    content = io.BytesIO()
    # Stored entries keep identical bytes across Python/zlib versions and platforms.
    with zipfile.ZipFile(content, 'w') as bundle:
        for name, body in sources.items():
            info = zipfile.ZipInfo(name, STAMP)
            info.create_system = 3
            info.external_attr = file_mode(name) << 16
            bundle.writestr(info, body, compress_type=zipfile.ZIP_STORED)
    output = Path(output) if output is not None else Path(root) / 'dist'
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ARCHIVE_NAME
    with tempfile.NamedTemporaryFile(dir=output, suffix='.tmp', delete=False) as temporary:
        temporary.write(content.getvalue())
        temporary_path = Path(temporary.name)
    try:
        temporary_path.replace(archive)
    finally:
        temporary_path.unlink(missing_ok=True)
    digest = hashlib.sha256(content.getvalue()).hexdigest()
    archive.with_suffix('.zip.sha256').write_bytes((digest + '  ' + archive.name + '\n').encode('ascii'))
    verify_archive(archive, root)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify the existing ZIP and checksum without rebuilding')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist', help='Output directory (default: repository dist/)')
    args = parser.parse_args()
    try:
        archive = args.output / ARCHIVE_NAME if args.check else build(output=args.output)
        digest = verify_archive(archive)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, str(exc) + '\n')
    print(str(archive))
    print('SHA256: ' + digest)


if __name__ == '__main__':
    main()
