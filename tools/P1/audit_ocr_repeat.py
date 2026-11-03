"""核对 P1 跨来源去重的效果：统计截图文字里有多少行其实是正文的复读。

只读 processed/P1爬虫数据清洗/辅助资料/raw_and_clean_parts.json，不改任何产物。
原始爬虫包没随仓库分发时，这份明细是唯一还能复算的记录。

    python tools/P1/audit_ocr_repeat.py
    python tools/P1/audit_ocr_repeat.py --out outputs/P1/校验/ocr_repeat.json
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P1爬虫与数据清洗/小红书爬虫与数据清洗/数据清洗'))

from src.rules import grams, repeats_body, squash

PARTS = ROOT / 'processed/P1爬虫数据清洗/辅助资料/raw_and_clean_parts.json'


def audit(parts_path=PARTS):
    detail = json.loads(parts_path.read_text(encoding='utf-8'))
    rows = []
    total = Counter()
    for doc in detail:
        body = squash(''.join(p['clean_text'] for p in doc['parts'] if p['kind'] == 'body'))
        if not body:
            continue
        body_grams = grams(body)
        repeat = keep = 0
        for part in doc['parts']:
            if part['kind'] != 'image':
                continue
            for line in part['clean_text'].split('\n'):
                line = squash(line)
                if not line:
                    continue
                if repeats_body(line, body, body_grams):
                    repeat += 1
                else:
                    keep += 1
        if repeat or keep:
            rows.append({'doc_id': doc['doc_id'], 'repeat_lines': repeat, 'kept_lines': keep})
            total.update(repeat=repeat, keep=keep)
    rows.sort(key=lambda r: (-r['repeat_lines'], r['doc_id']))
    share = total['repeat'] / (total['repeat'] + total['keep']) if total['keep'] + total['repeat'] else 0
    return {'documents': len(rows), 'repeat_lines': total['repeat'], 'kept_lines': total['keep'],
            'repeat_share': round(share, 4), 'per_document': rows,
            'note': '仅统计可判定的整行复读；三字以内的断行残片保留，交给下游清理。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parts', type=Path, default=PARTS)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    report = audit(args.parts)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('已写入 %s' % args.out)
    print('涉及文档 %d，复读 %d 行 / 保留 %d 行（%.1f%%）'
          % (report['documents'], report['repeat_lines'], report['kept_lines'], 100 * report['repeat_share']))
    for row in report['per_document'][:5]:
        if row['repeat_lines']:
            print('  %-16s 复读 %3d 行' % (row['doc_id'], row['repeat_lines']))


if __name__ == '__main__':
    main()
