# -*- coding: utf-8 -*-
"""报告口径表：正文里的每个数字都从这里取。

手抄数字是报告最容易出错的地方——旧稿里第 1 章写 100222 字，第 6 章还在用
清理前的 7117 个节点。这里统一从 processed/ 下的产物现读，改产物就等于改报告，
不用再回头找哪一段没跟着改。
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P = ROOT / 'processed'


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _p1():
    docs = load(P / 'P1爬虫数据清洗/documents.json')
    chars = [d['clean_char_len'] for d in docs]
    raw = [d['raw_char_len'] for d in docs]
    platform = Counter(d['source_platform'] for d in docs)
    scene = Counter(d['scenic_code'] for d in docs)
    typ = Counter(d['text_type'] for d in docs)
    total = lambda k: sum(d[k] for d in docs)
    aux = P / 'P1爬虫数据清洗/辅助资料'
    ocr = load(aux / 'ocr_results.json') if (aux / 'ocr_results.json').exists() else []
    return {
        'docs': len(docs),
        'chars': sum(chars),
        'raw_chars': sum(raw),
        'paragraphs': total('paragraph_count'),
        'sentences': total('sentence_count'),
        'min_chars': min(chars),
        'max_chars': max(chars),
        'median_chars': int(statistics.median(chars)),
        'valid': sum(d['is_valid'] for d in docs),
        'by_platform': dict(platform),
        'by_scene': dict(scene),
        'by_type': dict(typ),
        'dup_line_removed': total('dup_line_removed'),
        'noise_char_removed': total('noise_char_removed'),
        'noise_line_removed': total('noise_line_removed'),
        'emoji_removed': total('emoji_removed'),
        'url_removed': total('url_removed'),
        'html_tag_removed': total('html_tag_removed'),
        'invisible_removed': total('invisible_removed'),
        'email_removed': total('email_removed'),
        'tel_removed': total('tel_removed'),
        'wxid_removed': total('wxid_removed'),
        'ocr_images': len(ocr) if isinstance(ocr, list) else len(ocr.get('results', [])),
        'scene_chars': {code: sum(d['clean_char_len'] for d in docs if d['scenic_code'] == code)
                        for code in ('GZCL', 'ZHCL', 'JZG')},
        'scene_docs': dict(Counter(d['scenic_code'] for d in docs)),
        'scene_paragraphs': {code: sum(d['paragraph_count'] for d in docs if d['scenic_code'] == code)
                             for code in ('GZCL', 'ZHCL', 'JZG')},
        'flags': dict(Counter(f for d in docs for f in str(d['quality_flags']).split('；') if f)),
    }


def _p2():
    s = load(P / 'P2实体识别/summary.json')
    ent = load(P / 'P2实体识别/entities.json')
    mentions = [m for d in ent['documents'] for m in d['mentions']]
    kinds = Counter(m['type'] for m in mentions)
    uniq = {m['entity_id'] for m in mentions if m.get('entity_id')}
    q = load(P / 'P2实体识别/quality_check.json')
    return {
        'mentions': len(mentions),
        'by_type': dict(kinds),
        'unique_entities': len(uniq),
        'by_scene': s['scenic_summaries'],
        'diagnostics': s['diagnostic_occurrences'],
        'origin': s.get('origin_counts', {}),
        'poi_scope': dict(Counter(m['scope'] for m in mentions if m['type'] == 'POI')),
        'poi_roles': dict(Counter(m['poi_role'] for m in mentions if m['type'] == 'POI')),
        'diagnostic_terms': s.get('diagnostic_terms', {}),
        'check_passed': q['passed'],
        'check_total': q['total'],
        'docs': len(ent['documents']),
    }


def _p3():
    s = load(P / 'P3路线层级挖掘/summary.json')
    routes = load(P / 'P3路线层级挖掘/routes.json')['routes']
    per_scene = Counter(r['scenic_code'] for r in routes)
    steps_per_route = [len(r['steps']) for r in routes]
    return {
        **s,
        'by_scene': dict(per_scene),
        'steps_mean': round(sum(steps_per_route) / len(steps_per_route), 1),
        'steps_max': max(steps_per_route),
        'steps_min': min(steps_per_route),
        'route_ids': [r['route_id'] for r in routes],
    }


def _p4():
    s = load(P / 'P4共指消解/summary.json')
    return dict(s)


def _p4_eval():
    """人工核验的数字，单列一份，别和自动消解的全量统计混在一起。"""
    path = ROOT / 'outputs/P4/评估/evaluation.json'
    if not path.exists():
        return {}
    d = load(path)
    out = {'status': d.get('status'), 'mode': d.get('mode'),
           'rows': d['validation']['rows'], 'valid_rows': d['validation']['valid_rows'],
           'errors': d['validation']['errors'],
           'confirmed': d['validation']['confirmed_rows']}
    for name in ('rules', 'baseline'):
        block = d.get(name) or {}
        out[name] = {'metrics': block.get('metrics') or {}, 'counts': block.get('counts') or {},
                     'single_antecedent_by_gold_type': block.get('single_antecedent_by_gold_type') or {}}
    return out


def _p5_run():
    ptr = P / 'P5条件建议抽取/ACTIVE_RUN'
    if ptr.exists():
        return ptr.read_text(encoding='utf-8').strip()
    runs = sorted((P / 'P5条件建议抽取/runs').iterdir(), key=lambda p: p.stat().st_mtime)
    return runs[-1].name if runs else ''


def _p5():
    run = _p5_run()
    base = P / 'P5条件建议抽取/runs' / run
    if not (base / 'summary.json').exists():
        return {}
    s = dict(load(base / 'summary.json'))
    s['run_dir'] = run
    return s


def _p6():
    rel = P / 'P6知识图谱/release.json'
    clean = P / 'P6知识图谱/cleaned-v1/cleaning_summary.json'
    out = dict(load(rel)) if rel.exists() else {}
    if clean.exists():
        out['cleaning'] = load(clean)
    return out


def _p7():
    runs = ROOT / 'outputs/P7/agent_runs.json'
    if not runs.exists():
        return {}
    data = load(runs)
    return {
        'scenarios': len(data),
        'status': dict(Counter(r['status'] for r in data)),
        'runs': data,
    }


FACTS = {'p1': _p1(), 'p2': _p2(), 'p3': _p3(), 'p4': _p4(), 'p4_eval': _p4_eval(),
         'p5': _p5(), 'p6': _p6(), 'p7': _p7()}


if __name__ == '__main__':
    print(json.dumps({k: v for k, v in FACTS.items() if k not in ('p7',)}, ensure_ascii=False, indent=1)[:4000])
