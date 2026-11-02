import json
import runpy
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import paths


class MigrationPaths(unittest.TestCase):
    def test_official_scripts_and_local_config(self):
        folder = ROOT / 'src/P1爬虫与数据清洗/微信公众号与景区官方爬虫与数据清洗'
        for name in ('crawler.py', 'clean.py', 'rebuild_from_archive.py'):
            variables = runpy.run_path(str(folder / name))  # 不执行main或请求
            self.assertEqual(Path(variables['ROOT']), ROOT)
            if name != 'clean.py':
                config_path = Path(variables.get('SOURCES', variables.get('CONFIG')))
                self.assertEqual(config_path, folder / 'config/sources.json')
                config = json.loads(config_path.read_text(encoding='utf-8'))
                for area in config['scenic_areas']:
                    for manual in area.get('manual_notes', []):
                        self.assertTrue((ROOT / manual['file']).is_file())
            else:
                self.assertNotEqual(Path(variables['OUT_DIR']), paths.P1)
                self.assertTrue(Path(variables['RAW']).is_file())
                text, stats = variables['clean_text']('9:00')
                self.assertEqual(text, '09:00')
                self.assertEqual(stats['noise_chars_removed'], 0)
                text, _ = variables['clean_text']('上午９：００，东门→湖边！')
                self.assertEqual(text, '上午09:00,东门→湖边!')
                for record in variables['load_raw']():
                    if record.get('raw_html_path'):
                        self.assertTrue((ROOT / record['raw_html_path']).is_file())

    def test_archive_boundary_and_portable_mapping(self):
        parent = ROOT / '.test_tmp'
        parent.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=parent)
        target = Path(temp.name).resolve()
        try:
            archive = target / 'good.zip'
            cache = target / 'cache'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('原始归档说明.json', json.dumps({'source_root': 'D:\\old\\output'}))
                z.writestr('keyword/note.json', '{}')
            paths.prepare_archive(archive, cache)
            self.assertEqual(paths.archive_relocator(cache)('D:\\old\\output\\keyword\\note.json').replace('\\', '/'),
                             str(cache / 'keyword/note.json').replace('\\', '/'))
            bad = target / 'bad.zip'
            with zipfile.ZipFile(bad, 'w') as z:
                z.writestr('../escape.json', '{}')
            with self.assertRaises(ValueError):
                paths.prepare_archive(bad, target / 'bad-cache')
            self.assertFalse((target / 'escape.json').exists())
        finally:
            if not target.is_relative_to(parent.resolve()):
                raise ValueError('拒绝清理测试目录外路径')
            temp.cleanup()


if __name__ == '__main__':
    unittest.main()
