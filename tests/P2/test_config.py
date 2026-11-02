"""冻结配置完整性及文件篡改检查。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P2实体识别/src'))
from p2_entities.config import digest, encode, load_config, write_json


class ConfigTests(unittest.TestCase):
    def test_modified_file_is_rejected(self):
        base = ROOT / '.test_tmp'
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base, prefix='p2_config_') as value:
            folder = Path(value).resolve()
            self.assertTrue(folder.is_relative_to(base.resolve()))
            data = {'lexicon.json': {}}
            manifest = {'file_sha256': {k: digest(encode(v)) for k, v in data.items()}}
            manifest['config_hash'] = digest(encode(manifest))
            write_json(folder / 'manifest.json', manifest)
            write_json(folder / 'lexicon.json', {})
            load_config(folder)
            write_json(folder / 'lexicon.json', {'tampered': True})
            with self.assertRaisesRegex(ValueError, '冻结配置被更改'):
                load_config(folder)

    def test_frozen_config_available_and_context_policies_explicit(self):
        manifest, config = load_config()
        self.assertEqual(manifest['config_version'], 'v2')
        self.assertEqual(set(config['lexicon']), {'GZCL', 'ZHCL', 'JZG'})
        for data in config['lexicon'].values():
            for row in data['entries']:
                self.assertIn(row['guard'], {'named', 'gate', 'forest', 'animal_site', 'dual_mobility',
                                             'shuttle_boat', 'aviation', 'deferred', 'route_label', 'station_exit'})


if __name__ == '__main__':
    unittest.main()
