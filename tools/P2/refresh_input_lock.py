# -*- coding: utf-8 -*-
"""P1 语料更新后，重新锁定 P2 的输入指纹。

P2 的配置是冻结的：manifest 里记着 P1 产物的 sha256，对不上就拒绝运行，
防止有人拿旧词表去跑新语料。P1 剔除截图复读行之后 documents.json 和
paragraph_sources.json 都变了，这里把两个指纹换成新值并重算 config_hash。

    python -X utf8 tools/P2/refresh_input_lock.py --check   只看差异，不落盘
    python -X utf8 tools/P2/refresh_input_lock.py --apply   写入

config_version 保持 v2：词表、规则、决策都没有改动，变的只是输入版本。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P2 = ROOT / 'src/P2实体识别/config'
DOCS = ROOT / 'processed/P1爬虫数据清洗/documents.json'
SOURCES = ROOT / 'processed/P1爬虫数据清洗/辅助资料/paragraph_sources.json'


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    manifest = json.loads((P2 / 'manifest.json').read_text(encoding='utf-8'))
    acceptance = json.loads((P2 / 'acceptance.json').read_text(encoding='utf-8'))
    new_input, new_origin = sha(DOCS), sha(SOURCES)

    before = {'input_sha256': manifest['input_sha256'],
              'origin_sha256': acceptance['origin_sha256'],
              'config_hash': manifest['config_hash']}
    changed = []

    if manifest['input_sha256'] != new_input:
        manifest['input_sha256'] = new_input
        changed.append('input_sha256')
    if acceptance['origin_sha256'] != new_origin:
        acceptance['origin_sha256'] = new_origin
        changed.append('origin_sha256')
    if not changed:
        print('输入指纹已是最新，无需改动')
        return 0

    check = {k: v for k, v in manifest.items() if k != 'config_hash'}
    manifest['config_hash'] = digest(encode(check))

    after = {'input_sha256': manifest['input_sha256'],
             'origin_sha256': acceptance['origin_sha256'],
             'config_hash': manifest['config_hash']}
    print(json.dumps({'changed': changed, 'before': before, 'after': after},
                     ensure_ascii=False, indent=1))
    if not args.apply:
        print('\n（未写入；确认后用 --apply 落盘）')
        return 0

    (P2 / 'manifest.json').write_bytes(encode(manifest))
    (P2 / 'acceptance.json').write_bytes(encode(acceptance))
    print('\n已写入。config_hash 变动会让 mention_id 全部重算，下游需一并更新。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
