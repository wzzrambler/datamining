"""Read-only P5 semantic review for records already flagged by human review.

This intentionally does not rewrite generated P5 data, raw LLM observations,
release manifests, or downstream P6 graphs. Changes must go through a separate,
validated correction/rebuild step after human approval.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORMAL = Path('processed/P5条件建议抽取/表格/条件建议映射表.csv')
REVIEW = Path('outputs/P5/校验/分层人工校验表.csv')

# Suggested dispositions, NOT automatic replacements or verified facts.
PROPOSALS = {
    'RULE-28dfc38a510ec3072b77': {
        'suggested_statement_kind': 'conditional_fact',
        'suggested_force': 'descriptive',
        'suggested_evidence_category': 'time_dependent_observation',
        'reason': '“上午去人少”是在陈述一个时间相关的经验观察；原文另有“必刷”等建议，应分开标注。',
    },
    'RULE-3cf6c2a2c60a76ffbeef': {
        'suggested_statement_kind': 'conditional_fact',
        'suggested_force': 'descriptive',
        'suggested_evidence_category': 'personal_itinerary_choice',
        'reason': '“因为午后想去……所以选择……”是一次行程选择，不应直接推广为对所有游客的餐厅建议。',
    },
    'RULE-2b7dd1bc24a1e219d6be': {
        'suggested_statement_kind': 'conditional_fact',
        'suggested_force': 'descriptive',
        'suggested_evidence_category': 'subjective_evaluation',
        'reason': '“室外更好玩”是作者主观评价；若保留为信息，应标记其主观性，不能当作已核实的客观事实。',
    },
}


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f'找不到输入文件：{path}')
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def build_report(root: Path = ROOT) -> dict:
    formal_file, review_file = root / FORMAL, root / REVIEW
    formal_rows = read_rows(formal_file)
    review_rows = read_rows(review_file)
    formal = {x['rule_id']: x for x in formal_rows}
    if len(formal) != len(formal_rows):
        raise ValueError('正式映射表有重复 rule_id，必须先检查数据版本')

    pending = [r for r in review_rows if '存疑' in r.get('check_status', '')
               and '待修订' in r.get('revision_status', '')]
    ids = [r['rule_id'] for r in pending]
    if len(set(ids)) != len(ids):
        raise ValueError('审核表中的待修订 rule_id 有重复')
    missing = set(ids) - formal.keys()
    if missing:
        raise ValueError('审核条目在正式映射表中不存在：' + ', '.join(sorted(missing)))
    unknown = set(ids) - PROPOSALS.keys()
    if unknown:
        raise ValueError('发现尚无审核建议的新问题，请先人工分类：' + ', '.join(sorted(unknown)))

    entries = []
    for row in pending:
        old = formal[row['rule_id']]
        for k in ('scenic_code', 'condition_text', 'advice_text', 'statement_kind', 'force'):
            if row[k] != old[k]:
                raise ValueError(f"{row['rule_id']} 的 {k} 在审核表和正式结果中不一致，请先排查版本")
        prop = PROPOSALS[row['rule_id']]
        entries.append({
            'rule_id': row['rule_id'],
            'scenic_code': row['scenic_code'],
            'doc_ids': row['doc_ids'],
            'source_excerpt': row['source_excerpt'],
            'original_statement_kind': row['statement_kind'],
            'original_force': row['force'],
            'condition_text': row['condition_text'],
            'consequence_text': row['advice_text'],
            **prop,
            'human_decision': 'pending',
            'approved_for_export': False,
        })
    return {
        'report_type': 'p5_semantic_review_proposal',
        'read_only': True,
        'official_data_modified': False,
        'formal_mapping_records': len(formal_rows),
        'pending_review_count': len(entries),
        'warning': '此文件只有审核建议，不是已实施修订；不改变P5正式数据和P6图谱。',
        'items': entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='只读检查 P5 已标记存疑的语义记录')
    parser.add_argument('--output', type=Path, help='可选：保存JSON审核建议（不覆盖正式数据）')
    args = parser.parse_args()
    result = build_report()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output is not None:
        dst = args.output.resolve()
        protected = {(ROOT / FORMAL).resolve(), (ROOT / REVIEW).resolve(),
                     (ROOT / 'processed/P5条件建议抽取/release.json').resolve()}
        if dst in protected or (ROOT / 'processed/P5条件建议抽取').resolve() in dst.parents:
            parser.error('审核建议不能覆盖正式P5数据或发布文件')
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(rendered, encoding='utf-8')
        print(f'已生成只读审核建议：{dst}')
    else:
        print(f"正式映射表：{result['formal_mapping_records']} 条；待修订：{result['pending_review_count']} 条\n")
        for item in result['items']:
            print(f"{item['rule_id']} | {item['original_statement_kind']} → {item['suggested_statement_kind']}")
            print(f"  条件：{item['condition_text']}；内容：{item['consequence_text']}")
            print(f"  建议：{item['reason']}\n")
        print(result['warning'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
