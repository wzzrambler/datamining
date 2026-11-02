"""只读检查P1正式数据；--deep还会核对本地原始证据与归档。"""
import argparse
import csv
import json
import importlib.util
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import paths

_spec = importlib.util.spec_from_file_location('p1_verify_rules', paths.ROOT / 'src/P1爬虫与数据清洗/小红书爬虫与数据清洗/数据清洗/src/rules.py')
_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rules)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def verify(deep=False):
    folder = paths.P1
    docs = load(folder / 'documents.json')
    baseline = folder / '辅助资料/legacy_documents.json'
    assert baseline.is_file(), f'缺少原17篇历史基线：{baseline}'
    legacy = load(baseline)
    assert len(legacy) == 17, '历史基线应包含原17篇文档'
    assert len({d['doc_id'] for d in legacy}) == 17, '历史基线文档编号重复'
    expected = set(legacy[0])
    ids = set()
    para_count = 0
    for d in docs:
        assert set(d) == expected, f"字段不一致：{d.get('doc_id')}"
        assert d['doc_id'] not in ids, '文档编号重复'
        ids.add(d['doc_id'])
        assert all(v is not None for v in d.values()), f"存在null：{d['doc_id']}"
        assert d['clean_char_len'] == len(d['content'])
        assert d['paragraph_count'] == len(d['paragraphs'])
        assert d['content'] == '\n'.join(p['text'] for p in d['paragraphs'])
        assert d['text_type'] in ('official_route', 'travel_note')
        for index, p in enumerate(d['paragraphs'], 1):
            assert p['i'] == index and d['content'][p['start']:p['end']] == p['text']
        para_count += len(d['paragraphs'])
    by_id = {d['doc_id']: d for d in docs}
    for old in _rules.normalize_legacy_documents(legacy):
        new = by_id[old['doc_id']]
        # 与清洗生产配置使用同一份已提交基线；只允许格式、统计与证据路径适配。
        assert {k:v for k,v in old.items() if k != 'raw_html_path'} == {k:v for k,v in new.items() if k != 'raw_html_path'}, f"历史基线内容不一致：{old['doc_id']}"
    with (folder / 'paragraphs.csv').open(encoding='utf-8-sig', newline='') as f:
        assert sum(1 for _ in csv.DictReader(f)) == para_count
    origins = load(folder / '辅助资料/paragraph_sources.json')
    by_id = {d['doc_id']: d for d in docs}
    for origin in origins:
        assert 1 <= origin['para_id'] <= len(by_id[origin['doc_id']]['paragraphs'])
    for d in docs:
        if d['raw_html_path']:
            assert not Path(d['raw_html_path']).is_absolute(), '正式证据路径应相对仓库根目录'
    if deep:
        cache = paths.prepare_archive()
        for d in docs:
            if d['raw_html_path']:
                assert paths.evidence_path(d['raw_html_path']).is_file(), f"证据缺失：{d['doc_id']}"
        for p in load(folder / '辅助资料/ocr_results.json'):
            assert paths.evidence_path(p['source']).is_file()
        print(f'本地原始归档与证据存在性校验通过；解压缓存：{cache.relative_to(paths.ROOT)}')
    print(json.dumps({'documents':len(docs), 'paragraphs':para_count,
                      'by_type':dict(Counter(d['text_type'] for d in docs)),
                      'missing_titles':[d['doc_id'] for d in docs if not d['title']]}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deep', action='store_true')
    verify(parser.parse_args().deep)
