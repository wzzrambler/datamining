"""唯一正式实体识别流程：全量读取、精确位置、质量检查和结果导出。"""
from collections import Counter, defaultdict
import json
from pathlib import Path
import platform

from . import __version__
from .config import ROOT, PHASE, digest, load_config, write_json, matches_sha256
from .extractor import Extractor, normalized
from .candidates import collect_candidates


def validate_input(documents):
    seen = set()
    for doc in documents:
        if doc['doc_id'] in seen:
            raise ValueError('重复文档ID：' + doc['doc_id'])
        seen.add(doc['doc_id'])
        if doc['scenic_code'] not in ('GZCL', 'ZHCL', 'JZG'):
            raise ValueError('未知景区')
        if doc['content'] != '\n'.join(p['text'] for p in doc['paragraphs']):
            raise ValueError('段落与正文不一致')
        for i, para in enumerate(doc['paragraphs'], 1):
            if para['i'] != i or doc['content'][para['start']:para['end']] != para['text']:
                raise ValueError(f'{doc["doc_id"]}段落偏移错误')

def validate_entities(result, documents):
    if [d['doc_id'] for d in result['documents']] != [d['doc_id'] for d in documents]:
        raise ValueError('实体输出未按原顺序覆盖全部文档')
    identifiers, total = set(), 0
    for output, doc in zip(result['documents'], documents):
        spans = set()
        if output['scenic_code'] != doc['scenic_code']:
            raise ValueError('文档景区不一致')
        for item in output['mentions']:
            start, end = item['start'], item['end']
            if not 0 <= start < end <= len(doc['content']) or doc['content'][start:end] != item['text']:
                raise ValueError('实体原文切片错误')
            if not 1 <= item['para_id'] <= len(doc['paragraphs']):
                raise ValueError('实体段号错误')
            para = doc['paragraphs'][item['para_id'] - 1]
            if not para['start'] <= start < end <= para['end']:
                raise ValueError('实体越过段落边界')
            if item['type'] not in ('POI', 'TRANSPORT', 'TIME'):
                raise ValueError('实体类型非法')
            if item['poi_role'] not in (('attraction', 'support') if item['type'] == 'POI' else ('',)):
                raise ValueError('POI角色非法')
            key = (start, end, item['type'])
            if key in spans or item['mention_id'] in identifiers:
                raise ValueError('重复实体提及')
            spans.add(key)
            identifiers.add(item['mention_id'])
            for other in output['mentions']:
                if other is item or other['type'] == 'TIME' or item['type'] == 'TIME':
                    continue
                if start < other['end'] and other['start'] < end:
                    raise ValueError('POI/交通提及发生重叠')
            if not item['origin'] or any(x['doc_id'] != doc['doc_id'] or x['para_id'] != item['para_id'] for x in item['origin']):
                raise ValueError('实体来源关联错误')
            if 'time_context' in item:
                context = item['time_context']
                if (doc['content'][context['start']:context['end']] != context['expression'] or
                        not para['start'] <= context['start'] <= start < end <= context['end'] <= para['end'] or
                        context['prefix'] + item['text'] + context['suffix'] != context['expression']):
                    raise ValueError('时间核心值与提示词上下文不一致')
            total += 1
    return {'status': 'passed', 'documents': len(documents), 'mentions': total,
            'checks': ['全部文档覆盖', '全文切片', '段落边界', '类型和POI角色', '重复与重叠', '来源关联'],
            'limitation': '通过只说明结构一致，不代表人工识别准确率'}

def frequencies(result):
    counts, docsets, forms = Counter(), defaultdict(set), defaultdict(Counter)
    for doc in result['documents']:
        for item in doc['mentions']:
            name = normalized(item['canonical_name'] or item['text'])
            key = (doc['scenic_code'], item['type'], item['poi_role'], name)
            counts[key] += 1
            docsets[key].add(doc['doc_id'])
            forms[key][item['text']] += 1
    return [{'scenic_code': k[0], 'type': k[1], 'poi_role': k[2], 'name': k[3],
             'mention_count': n, 'document_count': len(docsets[k]),
             'surface_forms': dict(sorted(forms[k].items()))}
            for k, n in sorted(counts.items(), key=lambda kv: (kv[0][0], kv[0][1], -kv[1], kv[0][3]))]

def summary(result, diagnostics):
    rows = []
    for code in ('GZCL', 'ZHCL', 'JZG'):
        docs = [d for d in result['documents'] if d['scenic_code'] == code]
        items = [m for d in docs for m in d['mentions']]
        rows.append({'scenic_code': code, 'documents': len(docs),
                     'counts': dict(Counter(m['type'] for m in items)),
                     'poi_roles': dict(Counter(m['poi_role'] for m in items if m['type'] == 'POI')),
                     'no_mentions': [d['doc_id'] for d in docs if not d['mentions']]})
    return {'scenic_summaries': rows, 'diagnostic_occurrences': len(diagnostics),
            'diagnostic_terms': dict(Counter(d['term'] for d in diagnostics)),
            'origin_counts': dict(Counter(o['kind'] for d in result['documents'] for m in d['mentions'] for o in m['origin'])),
            'note': '提及次数不是独立地点数；同一提及可有多条来源。尚无人工P/R/F1。'}

def run(output=None, figures=None, draw=True):
    manifest, config = load_config()
    source = ROOT / 'processed/P1爬虫数据清洗/documents.json'
    origin_path = ROOT / 'processed/P1爬虫数据清洗/辅助资料/paragraph_sources.json'
    raw, origin_raw = source.read_bytes(), origin_path.read_bytes()
    if not matches_sha256(source, manifest['input_sha256']):
        raise ValueError('P1输入版本已变，请先重新确认词表及正式配置')
    acceptance = json.loads((PHASE / 'config/acceptance.json').read_text(encoding='utf-8'))
    if not matches_sha256(origin_path, acceptance['origin_sha256']):
        raise ValueError('P1段落来源版本已变，请先重新确认正式配置')
    docs = json.loads(raw.decode('utf-8-sig'))
    origins = json.loads(origin_raw.decode('utf-8-sig'))
    validate_input(docs)
    known = {(d['doc_id'], p['i']) for d in docs for p in d['paragraphs']}
    supplied = {(o['doc_id'], o['para_id']) for o in origins}
    required = {(d['doc_id'], p['i']) for d in docs if d['source_platform'] == '小红书' for p in d['paragraphs']}
    if supplied - known or required - supplied:
        raise ValueError('段落来源缺失或指向不存在的段落')
    extractor = Extractor(manifest, config, origins)
    outputs, diagnostics = [], []
    for doc in docs:
        extracted, notes = extractor.extract(doc)
        outputs.append(extracted)
        diagnostics.extend(notes)
    # Preserve the approved release fingerprints when only checkout EOL differs.
    result = {'schema_version': 'p2-entities-1', 'input_sha256': manifest['input_sha256'],
              'origin_sha256': acceptance['origin_sha256'], 'config_version': manifest['config_version'],
              'config_hash': manifest['config_hash'], 'documents': outputs}
    checked = validate_entities(result, docs)
    freq, stats = frequencies(result), summary(result, diagnostics)
    output = Path(output or ROOT / 'processed/P2实体识别').resolve()
    figures = Path(figures or ROOT / 'outputs/P2/图表').resolve()
    allowed = (ROOT / 'processed/P2实体识别', ROOT / 'outputs/P2', ROOT / '.test_tmp')
    for path in (output, figures):
        if not any(path.is_relative_to(p.resolve()) for p in allowed):
            raise ValueError('输出路径须在P2结果、图表或.test_tmp目录内')
    output.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    for name, data in [('entities.json', result), ('frequencies.json', freq), ('summary.json', stats), ('validation.json', checked)]:
        write_json(output / name, data)
    write_json(output / '辅助资料/diagnostics.json', diagnostics)
    for name, value in collect_candidates(result, docs, origins, config['candidate_specs']).items():
        write_json(output / '辅助资料' / (name + '.json'), value)
    cases = json.loads((PHASE / 'config/quality_cases.json').read_text(encoding='utf-8'))
    if cases['input_sha256'] != result['input_sha256']:
        raise ValueError('验收用例与输入版本不一致')
    by_id = {d['doc_id']: d for d in result['documents']}
    outcomes = []
    for case in cases['cases']:
        matched = any(m['start'] == case['start'] and m['end'] == case['end'] and m['type'] == case['type'] and
                      (not case['subtype'] or m['subtype'] == case['subtype']) and
                      (not case['poi_role'] or m['poi_role'] == case['poi_role']) for m in by_id[case['doc_id']]['mentions'])
        outcomes.append({'case_id': case['case_id'], 'passed': matched == case['expected_present']})
    quality = {'kind': 'targeted_checks_not_accuracy', 'total': len(outcomes),
               'passed': sum(r['passed'] for r in outcomes), 'outcomes': outcomes}
    write_json(output / 'quality_check.json', quality)
    if quality['passed'] != quality['total']:
        raise ValueError('正式定点验收用例未全部通过')
    metadata = {}
    if draw:
        from .plots import plot_frequencies
        metadata = plot_frequencies(freq, figures)
    record = {'implementation_version': __version__, 'python': platform.python_version(),
              'implementation_sha256': {p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob('*.py'))},
              'input_sha256': digest(raw), 'origin_sha256': digest(origin_raw), 'config_hash': manifest['config_hash'],
              'config_version': manifest['config_version'], 'offline': True, 'validation': checked, 'quality': quality,
              'plots': metadata, 'outputs': {str(p.relative_to(output)).replace('\\', '/'): digest(p.read_bytes())
                                           for p in sorted(output.rglob('*.json')) if p.name != 'run_manifest.json'}}
    write_json(output / 'run_manifest.json', record)
    return checked, stats
