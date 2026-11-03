"""P2正式运行入口，无历史版本切换。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from p2_entities.pipeline import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--figures', type=Path)
    parser.add_argument('--no-plots', action='store_true')
    parser.add_argument('--accept', action='store_true',
                        help='把本次 entities.json 的哈希写回验收清单（发布指纹）')
    args = parser.parse_args()
    checked, stats = run(args.output, args.figures, not args.no_plots)
    if args.accept:
        root = Path(__file__).resolve().parents[2]
        entities = (args.output or root / 'processed/P2实体识别') / 'entities.json'
        digest = hashlib.sha256(entities.read_bytes()).hexdigest()
        path = Path(__file__).resolve().parent / 'config/acceptance.json'
        acceptance = json.loads(path.read_text(encoding='utf-8'))
        if acceptance.get('entities_sha256') != digest:
            acceptance['entities_sha256'] = digest
            path.write_text(json.dumps(acceptance, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print('验收指纹已更新：%s' % digest[:12])
        else:
            print('验收指纹未变化：%s' % digest[:12])
    print(json.dumps({'validation': checked, 'summary': stats}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
