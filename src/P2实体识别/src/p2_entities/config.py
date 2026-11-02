"""正式配置与版本哈希；不依赖旧实现或草案。"""
import hashlib
import json
from pathlib import Path

PHASE = Path(__file__).resolve().parents[2]
ROOT = PHASE.parents[1]


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(value))

def load_config(folder=None):
    folder = Path(folder or PHASE / 'config')
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    check = {k: v for k, v in manifest.items() if k != 'config_hash'}
    if digest(encode(check)) != manifest['config_hash']:
        raise ValueError('配置manifest哈希不符')
    values = {}
    for name, sha in manifest['file_sha256'].items():
        if Path(name).name != name:
            raise ValueError('配置文件名越界')
        raw = (folder / name).read_bytes()
        if digest(raw) != sha:
            raise ValueError(f'冻结配置被更改：{name}')
        values[Path(name).stem] = json.loads(raw.decode('utf-8'))
    return manifest, values
