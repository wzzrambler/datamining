# -*- coding: utf-8 -*-
"""给跑完的 P5 批次补一份 input_manifest.json。

发布包（只有 raw_observations.json、没有 checkpoints 的那种）需要靠这张清单
重建图谱。批次是自己刚跑出来的，清单得从检查点倒推：哪些包跑过、各自是什么类型。
补包产生的分片包不在语料包里，按它父包的类型记（父包是路线就是 route_fragment）。

    python -X utf8 tools/P5/write_release_manifest.py --run-id full-final-20261103
    python -X utf8 tools/P5/write_release_manifest.py --run-id ... --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P5条件建议抽取'))
from p5_agent.budget import TokenBudget          # noqa: E402
from p5_agent.common import Settings, read_json, write_json   # noqa: E402
from p5_agent.contracts import Contracts          # noqa: E402
from p5_agent.packages import Corpus              # noqa: E402
from p5_agent.release_manifest import build_manifest   # noqa: E402

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    run_dir = ROOT / 'processed/P5条件建议抽取/runs' / args.run_id
    if not run_dir.is_dir():
        raise SystemExit('批次不存在：%s' % run_dir)
    settings = Settings.load()
    corpus, contracts = Corpus(), Contracts()
    packages = corpus.build(TokenBudget(settings), contracts.api_tools())
    manifest = build_manifest(run_dir, corpus, packages)
    print(json.dumps({'run_id': args.run_id, 'effective': len(manifest['effective_packages']),
                      'incomplete': manifest['incomplete_packages'],
                      'by_kind': {k: sum(1 for p in manifest['effective_packages'] if p['package_kind'] == k)
                                  for k in {p['package_kind'] for p in manifest['effective_packages']}}},
                     ensure_ascii=False, indent=1))
    if manifest['incomplete_packages']:
        raise SystemExit('还有包没跑完，清单不完整，拒绝写入')
    if not args.apply:
        print('\n（未写入；确认后用 --apply 落盘）')
        return
    write_json(run_dir / 'input_manifest.json', manifest)
    print('已写入：', run_dir / 'input_manifest.json')


if __name__ == '__main__':
    main()
