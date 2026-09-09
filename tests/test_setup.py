import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/codex-token-monitor/scripts/setup_monitor.py'
spec = importlib.util.spec_from_file_location('setup_monitor', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / '示例 project'
        self.home = self.base / 'user-data'
        self.project.mkdir()
        self.home.mkdir()

    def test_new_setup_resolves_own_home_and_preserves_brand(self):
        import json
        output = self.project / 'monitor'
        with patch.dict(os.environ, {'CODEX_HOME': str(self.home)}):
            result = module.setup(self.project, output, name='朋友的项目', port=18768)
        config = json.loads((output / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['codex_home'], str(self.home.resolve()))
        self.assertEqual(config['project_name'], '朋友的项目')
        self.assertEqual(config['tasks'], [])
        self.assertEqual(config['exclude_task_ids'], [])
        self.assertEqual(result['url'], 'http://127.0.0.1:18768/')
        self.assertTrue((output / 'assets/logo.jpg').is_file())
        self.assertTrue((output / 'assets/avatar.svg').is_file())
        self.assertFalse((output / 'test_monitor.py').exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_existing_output_is_not_overwritten(self):
        output = self.project / 'monitor'
        output.mkdir()
        (output / 'index.html').write_text('user work')
        with self.assertRaises(ValueError):
            module.setup(self.project, output, self.home)
        self.assertEqual((output / 'index.html').read_text(), 'user work')

    def test_data_home_and_skill_cannot_be_output(self):
        for output in (self.home / 'monitor', module.SKILL / 'output'):
            with self.assertRaises(ValueError):
                module.setup(self.project, output, self.home)
            self.assertFalse(output.exists())

    def test_avatar_does_not_replace_brand(self):
        import json
        output = self.project / 'monitor'
        picture = self.base / 'my-photo.jpg'
        picture.write_bytes((module.SKILL / 'assets/app/assets/logo.jpg').read_bytes())
        module.setup(self.project, output, self.home, avatar=picture)
        config = json.loads((output / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['avatar_file'], 'assets/avatar.jpg')
        self.assertEqual((output / 'assets/avatar.jpg').read_bytes(), picture.read_bytes())
        self.assertTrue((output / 'assets/logo.jpg').exists())


if __name__ == '__main__':
    unittest.main()
