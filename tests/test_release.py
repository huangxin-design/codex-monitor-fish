import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'tooling' / 'build_release.py'
spec = importlib.util.spec_from_file_location('build_release', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in module.SKILL_FILES:
            path = self.root / 'skills' / module.SKILL_NAME / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('synthetic fixture: ' + name).encode('utf-8'))
        (self.root / 'docs').mkdir()
        (self.root / 'docs' / '使用说明.md').write_text('Synthetic instructions.', encoding='utf-8')
        (self.root / 'docs' / 'images').mkdir()
        for name in module.DOC_IMAGES:
            (self.root / 'docs' / 'images' / name).write_bytes(
                b'\x89PNG\r\n\x1a\nsynthetic\r\nimage\x00' + name.encode('ascii'))
        (self.root / 'LICENSE').write_text('Synthetic license.', encoding='utf-8')
        (self.root / 'BRANDING.md').write_text('Synthetic branding terms.', encoding='utf-8')

    def test_identical_sources_produce_identical_archives_and_checksums(self):
        first = module.build(self.root, self.root / 'first')
        second = module.build(self.root, self.root / 'second')
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first.with_suffix('.zip.sha256').read_bytes(), second.with_suffix('.zip.sha256').read_bytes())
        module.verify_archive(first, self.root)

    def test_runtime_data_and_unlisted_files_are_never_included(self):
        app = self.root / 'skills' / module.SKILL_NAME / 'assets' / 'app'
        for name in ('config.json', 'usage-snapshot.json', 'usage-summary.csv', 'account-usage.json', 'monitor.log',
                     '.env', '__pycache__/monitor.pyc', 'assets/private-avatar.jpg'):
            private = app / name
            private.parent.mkdir(parents=True, exist_ok=True)
            private.write_text('PRIVATE SENTINEL', encoding='utf-8')
        (self.root / 'docs' / 'images' / 'mobile-v0.3.1.png').write_bytes(b'PRIVATE SENTINEL')
        archive = module.build(self.root)
        with zipfile.ZipFile(archive) as bundle:
            self.assertIn('使用说明.md', bundle.namelist())
            self.assertIn('LICENSE', bundle.namelist())
            self.assertIn('BRANDING.md', bundle.namelist())
            self.assertEqual(len(bundle.namelist()), len(module.SKILL_FILES) + len(module.DOC_IMAGES) + 3)
            self.assertFalse(any(b'PRIVATE SENTINEL' in bundle.read(name) for name in bundle.namelist()))

    def test_offline_guide_images_keep_binary_bytes_and_relative_paths(self):
        archive = module.build(self.root)
        with zipfile.ZipFile(archive) as bundle:
            for name in module.DOC_IMAGES:
                original = (self.root / 'docs' / 'images' / name).read_bytes()
                archived = bundle.read('images/' + name)
                self.assertEqual(archived, original)
                self.assertTrue(archived.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertNotIn('images/mobile-v0.3.1.png', bundle.namelist())

    def test_native_line_endings_do_not_change_release_bytes(self):
        command = self.root / 'skills' / module.SKILL_NAME / 'assets/app/start.cmd'
        guide = self.root / 'docs' / '使用说明.md'
        command.write_bytes(b'@echo off\npython launch.py\n')
        guide.write_bytes(b'Instructions\nSecond line\n')
        first = module.build(self.root, self.root / 'lf')
        command.write_bytes(command.read_bytes().replace(b'\n', b'\r\n'))
        guide.write_bytes(guide.read_bytes().replace(b'\n', b'\r\n'))
        second = module.build(self.root, self.root / 'crlf')
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_missing_required_file_fails_before_output_is_created(self):
        (self.root / 'skills' / module.SKILL_NAME / 'SKILL.md').unlink()
        with self.assertRaisesRegex(ValueError, 'Required release file is missing'):
            module.build(self.root)
        self.assertFalse((self.root / 'dist').exists())

    def test_checksum_tampering_is_detected(self):
        archive = module.build(self.root)
        archive.with_suffix('.zip.sha256').write_text('wrong checksum\n', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            module.verify_archive(archive, self.root)

    def test_unexpected_archive_entry_is_detected(self):
        archive = module.build(self.root)
        with zipfile.ZipFile(archive, 'a') as bundle:
            bundle.writestr('private.json', 'should never ship')
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            module.verify_archive(archive, self.root)

    def test_mac_launcher_has_executable_mode(self):
        archive = module.build(self.root)
        with zipfile.ZipFile(archive) as bundle:
            launcher = bundle.getinfo(module.SKILL_NAME + '/assets/app/start.command')
            self.assertEqual(launcher.external_attr >> 16, 0o100755)
            self.assertEqual(launcher.date_time, module.STAMP)


if __name__ == '__main__':
    unittest.main()
